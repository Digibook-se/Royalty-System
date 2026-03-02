#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import os
import csv
import logging
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Set

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

from elib_client import fetch_invoice_csv, aggregate
from report import make_report
from emailer import send_report
from models import Base, engine, SessionLocal, Author, Royalty


# ------------------------------------------------------------
# Konstanter / konfig
# ------------------------------------------------------------

OUT_DIR = Path("out")
OUT_DIR.mkdir(exist_ok=True)

ALIAS_FILE = OUT_DIR / "author_aliases.csv"

VAT_RATE = Decimal(os.getenv("VAT_RATE", "0.06") or "0.06")
PAYOUT_THRESHOLD = Decimal(os.getenv("PAYOUT_THRESHOLD", "100") or "100")

# För steg C
CONFIRM_WORD = os.getenv("CONFIRM_WORD", "ja").strip().lower() or "ja"


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def to_ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def norm_key(s: str) -> str:
    """Normaliserad matchnyckel för namn/email."""
    if not s:
        return ""
    return to_ascii(str(s)).strip().lower()


def dec(x) -> Decimal:
    return Decimal(str(x))


def fmt_kr(x: Decimal) -> str:
    """
    Svensk format: 97 140,07 kr
    """
    if x is None:
        x = Decimal("0")
    x = dec(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = f"{x:,.2f}"               # 97,140.07
    s = s.replace(",", "X").replace(".", ",").replace("X", " ")  # 97 140,07
    return f"{s} kr"


def period_from_date(dt: datetime) -> Tuple[str, str]:
    """
    Tar ett datum (från CSV) och returnerar:
      period_key   -> YYYY-QX
      period_label -> QX YYYY
    """
    year = dt.year
    q = (dt.month - 1) // 3 + 1
    return f"{year}-Q{q}", f"Q{q} {year}"


def parse_excluded_authors(raw: str) -> Set[str]:
    """
    EXCLUDED_AUTHORS ska vara en lista separerad med semikolon:
      EXCLUDED_AUTHORS=Ankarberg, Doris; Lind, Carola; ...

    Viktigt: vi splittrar INTE på komma (komma ingår i 'Efternamn, Förnamn').
    """
    if not raw:
        return set()

    raw = raw.strip().strip('"').strip("'")
    # stöd även om någon råkat använda radbrytningar
    raw = raw.replace("\n", ";")

    parts = [p.strip() for p in raw.split(";") if p.strip()]
    return {norm_key(p) for p in parts if p}


def safe_read_text(path: Path) -> str:
    """
    Läs fil robust (excel/cp1252/utf-8-sig/utf-8).
    """
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    # sista utväg
    return path.read_text(encoding="utf-8", errors="replace")


def ensure_alias_file_exists(path: Path) -> None:
    """
    Skapa aliasfil med standardheaders om den saknas.
    """
    if path.exists():
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["elib_author", "email", "comment"])


def read_author_aliases(path: Path) -> Dict[str, str]:
    """
    Läser aliasfil och returnerar dict:
      norm(elib_author) -> email

    Accepterar flera varianter av kolumnnamn:
      - elib_author, email, comment
      - alias_name, email
      - author, email
      - name, email
    """
    if not path.exists():
        ensure_alias_file_exists(path)
        return {}

    txt = safe_read_text(path)
    # pandas gör kolumnhantering enkelt
    from io import StringIO
    try:
        df = pd.read_csv(StringIO(txt))
    except Exception as e:
        logging.warning(f"Kunde inte läsa aliasfil {path}: {e}")
        return {}

    cols = [c.strip() for c in df.columns.tolist()]
    lower = {c.lower(): c for c in cols}

    name_col = None
    for candidate in ("elib_author", "alias_name", "author", "name"):
        if candidate in lower:
            name_col = lower[candidate]
            break

    email_col = None
    for candidate in ("email", "e-post", "epost", "mail"):
        if candidate in lower:
            email_col = lower[candidate]
            break

    if not name_col or not email_col:
        logging.warning(f"Aliasfil saknar kolumner. Hittade kolumner: {cols}")
        return {}

    out: Dict[str, str] = {}
    for _, r in df.iterrows():
        n = str(r.get(name_col, "") or "").strip()
        e = str(r.get(email_col, "") or "").strip()
        if not n:
            continue
        if not e:
            # tom email = fortfarande “känd saknas”, men inte mappbar ännu
            continue
        out[norm_key(n)] = e.strip().lower()

    return out


def append_missing_to_alias_file(path: Path, missing_names: List[str]) -> int:
    """
    Säkerställer att alla saknade eLib-namn finns i aliasfilen
    (så du kan fylla i email manuellt).
    Lägger bara till nya rader som inte redan finns.
    """
    ensure_alias_file_exists(path)

    txt = safe_read_text(path)
    from io import StringIO
    try:
        df = pd.read_csv(StringIO(txt))
    except Exception:
        # om filen är trasig: skriv om till standardformat (men försök inte gissa)
        df = pd.DataFrame(columns=["elib_author", "email", "comment"])

    # normalisera kolumner till standard
    cols = [c.strip() for c in df.columns.tolist()]
    lower = {c.lower(): c for c in cols}

    # hitta name-kolumn om den finns i annan form
    if "elib_author" in lower:
        name_col = lower["elib_author"]
    elif "alias_name" in lower:
        name_col = lower["alias_name"]
        df = df.rename(columns={name_col: "elib_author"})
        name_col = "elib_author"
    elif "author" in lower:
        name_col = lower["author"]
        df = df.rename(columns={name_col: "elib_author"})
        name_col = "elib_author"
    elif "name" in lower:
        name_col = lower["name"]
        df = df.rename(columns={name_col: "elib_author"})
        name_col = "elib_author"
    else:
        df["elib_author"] = ""
        name_col = "elib_author"

    if "email" not in [c.lower() for c in df.columns]:
        df["email"] = ""
    if "comment" not in [c.lower() for c in df.columns]:
        df["comment"] = ""

    existing = {norm_key(x) for x in df["elib_author"].fillna("").astype(str).tolist() if str(x).strip()}
    added = 0

    new_rows = []
    for n in missing_names:
        k = norm_key(n)
        if not k or k in existing:
            continue
        new_rows.append({"elib_author": n, "email": "", "comment": "AUTO: saknas i DB – fyll i email"})
        existing.add(k)
        added += 1

    if added:
        df2 = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        # skriv med BOM för excel
        df2.to_csv(path, index=False, encoding="utf-8-sig")

    return added


def build_author_lookup(authors: List[Author]) -> Dict[str, Author]:
    """
    Bygger match-tabell med många nycklar så vi kan matcha:
      - name exakt (ascii)
      - "Efternamn, Förnamn" om name ser ut som "Förnamn Efternamn"
      - email
    """
    lookup: Dict[str, Author] = {}

    def add(k: str, a: Author):
        k = norm_key(k)
        if not k:
            return
        # övertrampa inte befintlig om redan satt (stabilitet)
        lookup.setdefault(k, a)

    for a in authors:
        if a.name:
            add(a.name, a)

            # försök skapa "Last, First" nyckel om möjligt
            parts = str(a.name).strip().split()
            if len(parts) >= 2:
                first = parts[0]
                last = " ".join(parts[1:])
                add(f"{last}, {first}", a)

        if a.email:
            add(a.email.lower(), a)

    return lookup


# ------------------------------------------------------------
# Körplan (för steg 3 + steg C + execution)
# ------------------------------------------------------------

@dataclass
class RunItem:
    author: Author
    author_name_in_csv: str
    group_df: pd.DataFrame
    prev_balance: Decimal
    today_share: Decimal
    payout_ex_vat: Decimal
    vat_amount: Decimal
    payout_inc_vat: Decimal
    new_balance: Decimal
    will_payout: bool


@dataclass
class RunPlan:
    period_key: str
    period_label: str
    items: List[RunItem]
    missing_names: List[str]
    already_run_names: List[str]
    excluded_in_csv: List[str]


def compute_run_plan(
    db,
    agg_df: pd.DataFrame,
    author_lookup: Dict[str, Author],
    alias_email_map: Dict[str, str],
    excluded_keys: Set[str],
    period_key: str,
) -> RunPlan:
    missing: List[str] = []
    already: List[str] = []
    excluded_in_csv: List[str] = []
    items: List[RunItem] = []

    for author_name, group in agg_df.groupby("Författarnamn"):
        author_name = str(author_name).strip()
        k = norm_key(author_name)

        # 1) exkludera helt
        if k and k in excluded_keys:
            excluded_in_csv.append(author_name)
            continue

        # 2) alias via email
        author_obj: Optional[Author] = None
        alias_email = alias_email_map.get(k)
        if alias_email:
            author_obj = author_lookup.get(norm_key(alias_email))

        # 3) fallback match på namn
        if not author_obj:
            author_obj = author_lookup.get(k)

        if not author_obj:
            missing.append(author_name)
            continue

        # 4) skydd mot dubbelkörning
        exists = (
            db.query(Royalty)
            .filter(Royalty.author_id == author_obj.id, Royalty.period == period_key)
            .first()
        )
        if exists:
            already.append(author_name)
            continue

        prev_balance = dec(author_obj.carried_balance or 0)
        today_share = dec(group["AuthorShare"].sum()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        total_available = (prev_balance + today_share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        will_payout = total_available >= PAYOUT_THRESHOLD
        if will_payout:
            payout_ex_vat = total_available
            new_balance = Decimal("0.00")
        else:
            payout_ex_vat = Decimal("0.00")
            new_balance = total_available

        vat_amount = (payout_ex_vat * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        payout_inc_vat = (payout_ex_vat + vat_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        items.append(
            RunItem(
                author=author_obj,
                author_name_in_csv=author_name,
                group_df=group,
                prev_balance=prev_balance,
                today_share=today_share,
                payout_ex_vat=payout_ex_vat,
                vat_amount=vat_amount,
                payout_inc_vat=payout_inc_vat,
                new_balance=new_balance,
                will_payout=will_payout,
            )
        )

    # period_label sätts senare i main
    return RunPlan(
        period_key=period_key,
        period_label="",
        items=items,
        missing_names=missing,
        already_run_names=already,
        excluded_in_csv=excluded_in_csv,
    )


# ------------------------------------------------------------
# Print (steg 2 + 3 + C)
# ------------------------------------------------------------

def print_step_2_and_3_summary(df, agg_df, period_label: str, run_plan: RunPlan, excluded_raw_list: List[str]):
    print("\n" + "=" * 50)
    print("STEG 2 – TOTALSUMMOR & RIMLIGHETSKONTROLL")
    print("=" * 50)

    total_net = dec(agg_df["Nettobelopp"].sum())
    total_author = dec(agg_df["AuthorShare"].sum())
    total_publisher = dec(agg_df["PublisherShare"].sum())

    author_pct = (total_author / total_net * 100) if total_net else Decimal("0")
    publisher_pct = (total_publisher / total_net * 100) if total_net else Decimal("0")

    from_date = df["FromDate"].astype(str).min() if "FromDate" in df.columns else "?"
    to_date = df["ToDate"].astype(str).max() if "ToDate" in df.columns else "?"

    print(f"\nPeriod:        {period_label}")
    print(f"Datumspann:    {from_date} → {to_date}")
    print(f"Antal rader:   {len(df)}")

    print("\nINTÄKTER (FRÅN CSV)")
    print("-" * 34)
    print(f"Totalt netto (alla titlar): {fmt_kr(total_net)}")

    print("\nFÖRDELNING")
    print("-" * 34)
    print(f"Författarroyalty (70 %):   {fmt_kr(total_author)}")
    print(f"Förlagsandel (30 %):       {fmt_kr(total_publisher)}")

    print("\nKontroll:")
    print(f"- Författarandel i %:       {author_pct:6.2f} %")
    print(f"- Förlagsandel i %:         {publisher_pct:6.2f} %")
    print(f"- Avvikelse från 70/30:     {abs(author_pct - 70):6.2f} %")

    print("\n" + "=" * 50)
    print("STEG 3 – FÖRFATTARFÖRDELNING")
    print("=" * 50)

    # CSV-total (rent underlag)
    per_author = agg_df.groupby("Författarnamn")["AuthorShare"].sum().sort_values(ascending=False)

    num_authors_total = int(per_author.count())
    num_positive = int((per_author > 0).sum())
    num_payout = int((per_author >= PAYOUT_THRESHOLD).sum())
    num_carry = int((per_author < PAYOUT_THRESHOLD).sum())

    total_payout = dec(per_author[per_author >= PAYOUT_THRESHOLD].sum())
    total_carry = dec(per_author[per_author < PAYOUT_THRESHOLD].sum())

    median_royalty = dec(per_author.median()) if len(per_author) else Decimal("0")
    mean_royalty = dec(per_author.mean()) if len(per_author) else Decimal("0")

    print("\nCSV-TOTAL (hela underlaget)")
    print("-" * 34)
    print(f"Antal författare totalt:        {num_authors_total}")
    print(f"Antal med royalty (> 0 kr):     {num_positive}")
    print(f"Antal med utbetalning (≥{PAYOUT_THRESHOLD}):   {num_payout}")
    print(f"Antal under gräns (carry-over): {num_carry}")

    print("\nSUMMOR (CSV-total)")
    print("-" * 34)
    print(f"Totalt att betala ut nu:        {fmt_kr(total_payout)}")
    print(f"Totalt carry-over:             {fmt_kr(total_carry)}")

    print("\nSTATISTIK (CSV-total)")
    print("-" * 34)
    print(f"Genomsnittlig royalty:          {fmt_kr(mean_royalty)}")
    print(f"Median royalty:                {fmt_kr(median_royalty)}")

    print("\nTOP 5 – HÖGSTA UTBETALNINGAR (CSV-total)")
    print("-" * 34)
    for i, (name, amount) in enumerate(per_author.head(5).items(), start=1):
        print(f"{i}. {name:<30} {dec(amount):>10,.0f} kr")

    # Efter filter (det som faktiskt kommer köras)
    items = run_plan.items
    num_reports = len(items)
    num_payout_run = sum(1 for it in items if it.will_payout)
    num_carry_run = sum(1 for it in items if not it.will_payout)

    total_payout_ex = sum((it.payout_ex_vat for it in items), Decimal("0.00"))
    total_vat = sum((it.vat_amount for it in items), Decimal("0.00"))
    total_payout_inc = sum((it.payout_inc_vat for it in items), Decimal("0.00"))
    total_carry_next = sum((it.new_balance for it in items if not it.will_payout), Decimal("0.00"))

    print("\nEFTER FILTER (det som faktiskt kommer köras)")
    print("-" * 34)
    print(f"Rapporter som kommer skapas/skickas: {num_reports}")
    print(f"Författare med utbetalning (≥{PAYOUT_THRESHOLD}):   {num_payout_run}")
    print(f"Författare som sparar vidare:        {num_carry_run}")

    print("\nSUMMOR (efter filter)")
    print("-" * 34)
    print(f"Totalt att betala ut (exkl moms):    {fmt_kr(total_payout_ex)}")
    print(f"Moms {int(VAT_RATE*100)}% totalt:                      {fmt_kr(total_vat)}")
    print(f"Totalt att betala ut (inkl moms):    {fmt_kr(total_payout_inc)}")
    print(f"Totalt carry-over till nästa period: {fmt_kr(total_carry_next)}")

    # Avvikelser
    missing = run_plan.missing_names
    already = run_plan.already_run_names
    excluded_in_csv = run_plan.excluded_in_csv

    print("\nAVVIKELSER (varför skillnad mot CSV-total)")
    print("-" * 34)
    print(f"Saknas i DB:                         {len(set(missing))}")
    print(f"Redan körda för perioden:            {len(set(already))}")
    print(f"Exkluderade (EXCLUDED_AUTHORS):      {len(set(excluded_in_csv))}")

    print("\nLISTA – Exkluderade (från .env, urval)")
    print("-" * 34)
    if excluded_raw_list:
        for n in excluded_raw_list[:30]:
            print(f"- {n}")
        if len(excluded_raw_list) > 30:
            print(f"... och {len(excluded_raw_list)-30} till")
    else:
        print("Inga.")

    print("\nLISTA – Exkluderade som matchade i CSV (urval)")
    print("-" * 34)
    if excluded_in_csv:
        for n in sorted(set(excluded_in_csv))[:30]:
            print(f"- {n}")
        if len(set(excluded_in_csv)) > 30:
            print(f"... och {len(set(excluded_in_csv))-30} till")
    else:
        print("Inga.")

    # Listor + filer
    missing_file = OUT_DIR / f"missing_in_db_{run_plan.period_key}.txt"
    already_file = OUT_DIR / f"already_run_{run_plan.period_key}.txt"

    def write_list(path: Path, rows: List[str]):
        path.write_text("\n".join(sorted(set(rows))) + ("\n" if rows else ""), encoding="utf-8")

    write_list(missing_file, missing)
    write_list(already_file, already)

    print("\nLISTA – Saknas i DB (urval)")
    print("-" * 34)
    if missing:
        for n in sorted(set(missing))[:25]:
            print(f"- {n}")
        if len(set(missing)) > 25:
            print(f"... och {len(set(missing))-25} till")
    else:
        print("Inga.")

    print("\nLISTA – Redan körda för perioden (urval)")
    print("-" * 34)
    if already:
        for n in sorted(set(already))[:25]:
            print(f"- {n}")
        if len(set(already)) > 25:
            print(f"... och {len(set(already))-25} till")
    else:
        print("Inga.")

    # se till att saknade hamnar i aliasfilen
    added = append_missing_to_alias_file(ALIAS_FILE, sorted(set(missing)))
    print("\nFILER SPARADE")
    print("-" * 34)
    print(f"- {missing_file}")
    print(f"- {already_file}")
    if added:
        print(f"- {ALIAS_FILE} (lade till {added} rader)")
    else:
        print(f"- {ALIAS_FILE}")

    print("\n(Steg 3 avslutat – ingen data har skrivits)")


def ask_to_continue_or_exit():
    while True:
        answer = input("\nVill du fortsätta till nästa steg? (j/n): ").strip().lower()
        if answer == "j":
            return
        if answer == "n":
            print("\nAvbrutet av användaren. Ingen data har ändrats.\n")
            raise SystemExit(0)
        print("Svara med 'j' eller 'n'.")


def final_confirmation(dry_run: bool, run_plan: RunPlan):
    print("\n" + "=" * 50)
    print("STEG C – SLUTGILTIGT GODKÄNNANDE (POINT OF NO RETURN)")
    print("=" * 50)

    print("\nNÄSTA STEG KOMMER ATT:")
    if dry_run:
        print("- (DRY_RUN=1) Inga mejl skickas och ingen data sparas i DB.")
    else:
        print("- Skicka mejl till författare och spara rader i DB (oåterkalleligt för perioden).")

    items = run_plan.items
    num_payout = sum(1 for it in items if it.will_payout)
    num_carry = sum(1 for it in items if not it.will_payout)
    total_ex = sum((it.payout_ex_vat for it in items), Decimal("0.00"))
    total_vat = sum((it.vat_amount for it in items), Decimal("0.00"))
    total_inc = sum((it.payout_inc_vat for it in items), Decimal("0.00"))
    total_carry = sum((it.new_balance for it in items if not it.will_payout), Decimal("0.00"))

    print("\nUTBETALNINGAR (beräknat efter filter):")
    print(f"- Författare med utbetalning (≥{PAYOUT_THRESHOLD} kr): {num_payout}")
    print(f"- Författare som sparar till nästa period: {num_carry}")
    print(f"- Totalt att betala ut (exkl moms): {fmt_kr(total_ex)}")
    print(f"- Moms {int(VAT_RATE*100)}% totalt: {fmt_kr(total_vat)}")
    print(f"- Totalt att betala ut (inkl moms): {fmt_kr(total_inc)}")
    print(f"- Totalt carry-over till nästa period: {fmt_kr(total_carry)}")

    while True:
        answer = input(f"\nÄr du helt säker på att du vill GENOMFÖRA nästa steg? ({CONFIRM_WORD}/nej): ").strip().lower()
        if answer == CONFIRM_WORD:
            return
        if answer == "nej":
            print("\nAvbrutet av användaren. Ingen data har ändrats.\n")
            raise SystemExit(0)
        print(f"Svara med '{CONFIRM_WORD}' eller 'nej'.")


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )

    dry_run = os.getenv("DRY_RUN", "0") == "1"
    force_rerun = os.getenv("FORCE_RERUN", "0") == "1"  # om du vill återköra period

    if dry_run:
        logging.info("DRY_RUN=1 → inga mejl skickas")

    raw_excluded = os.getenv("EXCLUDED_AUTHORS", "") or ""
    excluded_keys = parse_excluded_authors(raw_excluded)
    excluded_raw_list = [p.strip() for p in raw_excluded.strip().strip('"').strip("'").split(";") if p.strip()]

    logging.info(f"EXCLUDED_AUTHORS raw: {raw_excluded!r}")
    logging.info(f"EXCLUDED_AUTHORS (antal): {len(excluded_keys)}")

    test_email = os.getenv("TEST_EMAIL")
    test_limit = int(os.getenv("TEST_LIMIT", "0") or "0")
    sent = 0

    Base.metadata.create_all(engine)
    db = SessionLocal()

    try:
        # --- Authors / lookup ---
        authors = db.query(Author).all()
        author_lookup = build_author_lookup(authors)
        logging.info(f"Hämtade {len(authors)} författare från databasen (match-nycklar: {len(author_lookup)})")

        # --- Alias ---
        alias_email_map = read_author_aliases(ALIAS_FILE)
        logging.info(f"Läste alias: {len(alias_email_map)} kopplingar från {ALIAS_FILE}")

        # --- CSV ---
        df = fetch_invoice_csv()
        logging.info(f"Hämtade {len(df)} rader från eLib-CSV")

        if "ToDate" not in df.columns:
            raise KeyError("CSV saknar kolumnen 'ToDate' – kan inte avgöra period")

        to_date = (
            df["ToDate"]
            .astype(str)
            .dropna()
            .sort_values()
            .iloc[-1]
        )

        period_dt = datetime.fromisoformat(to_date)
        period_key, period_label = period_from_date(period_dt)
        logging.info(f"Period bestämd från CSV: {period_label}")

        # --- Agg ---
        agg_df = aggregate(df)
        logging.info(f"Konsoliderade till {len(agg_df)} royaltyrader")

        # --- Plan ---
        run_plan = compute_run_plan(
            db=db,
            agg_df=agg_df,
            author_lookup=author_lookup,
            alias_email_map=alias_email_map,
            excluded_keys=excluded_keys,
            period_key=period_key,
        )
        run_plan.period_label = period_label

        # --- STEG 2 + 3 (print + filer) ---
        print_step_2_and_3_summary(df, agg_df, period_label, run_plan, excluded_raw_list)

        ask_to_continue_or_exit()

        # --- STEG C ---
        final_confirmation(dry_run=dry_run, run_plan=run_plan)

        # --- Execute ---
        for item in run_plan.items:
            author = item.author
            group = item.group_df

            # extra skydd: exkludera även här, om något skulle slinka igenom
            if norm_key(item.author_name_in_csv) in excluded_keys:
                logging.info(f"Hoppar över {item.author_name_in_csv} (EXCLUDED_AUTHORS)")
                continue

            # dubbelkörningsskydd (om FORCE_RERUN=0)
            if not force_rerun:
                exists = (
                    db.query(Royalty)
                    .filter(Royalty.author_id == author.id, Royalty.period == period_key)
                    .first()
                )
                if exists:
                    logging.info(f"Redan körd för {item.author_name_in_csv} ({period_label})")
                    continue

            recipient = test_email or author.email
            if not recipient:
                logging.warning(f"Saknar email i DB för {author.name} – hoppar över (kan inte skicka)")
                continue
                
            # Beräkningar (kommer från run_plan)
            prev_balance = float(item.prev_balance or 0)
            period_total = float(item.period_total or 0)
            payout_ex_vat = float(item.payout_ex_vat or 0)
            vat_amount = float(item.vat_amount or 0)
            payout_inc_vat = float(item.payout_inc_vat or 0)
            carry_to_next = float(item.new_balance or 0)

            # Skapa PDF-rapport (report.make_report tar POSITIONELLA argument)
            pdf = make_report(author.name, author_df, period_label, prev_balance, carry_to_next)

        # Skicka rapport (i DRY_RUN skickas till TEST_EMAIL om satt, annars till författarens mail)
        recipient = author.email
        if DRY_RUN:
            recipient = os.environ.get('TEST_EMAIL') or recipient
        email_ok = False
        if recipient:
            try:
                send_report(recipient, pdf, period_label)
                email_ok = True
                sent += 1
            except Exception as e:
                logging.warning(f"Kunde inte skicka rapport till {recipient}: {e}")
        else:
            logging.warning(f"Saknar email för {author.name} – rapport skapas men kan inte skickas")

        # Spara i DB (bara om inte DRY_RUN och om vi faktiskt lyckades skicka till någon)
        if (not DRY_RUN) and email_ok:
            # Uppdatera carry-over
            author.carried_balance = carry_to_next

            # Spara alla rader (per titel) för att markera perioden som körd
            for _, row in group.iterrows():
                db.add(
                    Royalty(
                        author_id=author.id,
                        period=period_key,
                        title=str(row.get('Titel', '')).strip(),
                        isbn=str(row.get('ISBN', '')).strip(),
                        gross=float(row.get('Nettobelopp', 0) or 0),
                        amount=float(row.get('AuthorShare', 0) or 0),
                    )
                )
            db.commit()
            db.refresh(author)

        # Logg för admin
        if payout_ex_vat > 0:
            logging.info(f"{author.name}: utbetalning {payout_inc_vat:.2f} kr (inkl moms), carry {carry_to_next:.2f} kr")
        else:
            logging.info(f"{author.name}: under gräns – carry {carry_to_next:.2f} kr")
            if test_limit and sent >= test_limit:
                logging.info("Testgräns nådd – avbryter")
                break

    except (SQLAlchemyError, Exception) as e:
        logging.exception(f"Ett fel uppstod: {to_ascii(str(e))}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
