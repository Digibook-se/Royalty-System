from __future__ import annotations

import io
import os
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
from openpyxl import Workbook

from models import Author, RoyaltyRun, RoyaltyRunItem, PayoutBatch, PayoutTransaction

# Vi återanvänder er befintliga "motor" från main.py och elib_client.py
from elib_client import fetch_invoice_csv, aggregate
from main import (
    compute_run_plan,
    build_author_lookup,
    load_aliases_prefer_xlsx,
    norm_key,
    period_from_date,
    VAT_RATE,
    PAYOUT_THRESHOLD,
)

OUT_DIR = Path("out")
OUT_DIR.mkdir(exist_ok=True)

def _dec(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")

def load_inputs_to_env(
    elib_csv_path: str,
    biblio_xlsx_path: Optional[str] = None,
    biblio_from: Optional[str] = None,
    biblio_to: Optional[str] = None,
) -> None:
    """
    För enkelhet återanvänder vi er existerande importkod som läser från env-variabler.
    """
    os.environ["LOCAL_CSV"] = str(elib_csv_path)

    if biblio_xlsx_path:
        os.environ["BIBLIO_FILE"] = str(biblio_xlsx_path)
        if biblio_from:
            os.environ["BIBLIO_FROMDATE"] = str(biblio_from)
        if biblio_to:
            os.environ["BIBLIO_TODATE"] = str(biblio_to)
    else:
        # Om du kör utan biblio vill vi inte råka ha kvar gamla env-värden
        os.environ.pop("BIBLIO_FILE", None)
        os.environ.pop("BIBLIO_FROMDATE", None)
        os.environ.pop("BIBLIO_TODATE", None)

    import streamlit as st

def analyze_run(
    db, 
    excluded_authors_raw: str = "",
    alias_xlsx_path: str | None = None,
    alias_csv_path: str | None = None,
):
    import pandas as pd
    import streamlit as st
    st.write("ALIAS_XLSX from env:", os.getenv("ALIAS_XLSX"))
    from datetime import datetime
    from elib_client import fetch_invoice_csv, aggregate
    from main import (
        compute_run_plan,
        build_author_lookup,
        norm_key,
        period_from_date,
    )
    from models import Author

    # 1️⃣ Hämta och aggregera data
    df = fetch_invoice_csv()
    agg_df = aggregate(df)

    # 2️⃣ Bestäm period (utan att kräva ToDate)
    period_dt = datetime.today()
    period_key, period_label = period_from_date(period_dt)

    # 3️⃣ Bygg author lookup från DB
    authors = db.query(Author).all()
    author_lookup = build_author_lookup(authors)

    # 4️⃣ Alias (från upload-parametrar eller från .env)
    root_dir = Path(__file__).resolve().parents[3]  # .../royalty-system

    # Läs från env (kan vara None)
    alias_xlsx_env = os.getenv("ALIAS_XLSX")
    alias_csv_env = os.getenv("ALIAS_CSV")

    # Ta upload-path om den finns, annars env, annars default paths i out/
    xlsx_path = alias_xlsx_path or alias_xlsx_env or "out/author_aliases.xlsx"
    csv_path  = alias_csv_path  or alias_csv_env  or "out/author_aliases.csv"

    # Gör relativa paths relativa till projektroten
    if xlsx_path and not os.path.isabs(xlsx_path):
        xlsx_path = str((root_dir / xlsx_path).resolve())
    if csv_path and not os.path.isabs(csv_path):
        csv_path = str((root_dir / csv_path).resolve())

    # Nu är båda ALLTID strängar (inte None) → loadern kraschar inte
    aliases = load_aliases_prefer_xlsx(
        xlsx_path=xlsx_path,
        csv_path=csv_path,
    )
    alias_email_map = {norm_key(k): v for k, v in aliases.items()}

    # 5️⃣ Exkluderingar
    excluded_authors = [
        name.strip()
        for name in excluded_authors_raw.split(",")
        if name.strip()
    ]
    excluded_keys = {norm_key(x) for x in excluded_authors}

    # 6️⃣ Bygg run plan
    run_plan = compute_run_plan(
        db=db,
        agg_df=agg_df,
        author_lookup=author_lookup,
        alias_email_map=alias_email_map,
        excluded_keys=excluded_keys,
        period_key=period_key,
    )
    run_plan.period_label = period_label

    # 7️⃣ Bygg DataFrame för UI
    rows = []
    for item in run_plan.items:
        rows.append(
            {
                "author_id": item.author.id,
                "author_name": item.author.name,
                "email": item.author.email,
                "iban": item.author.bank_account,
                "prev_balance": float(item.prev_balance),
                "today_share": float(item.today_share),
                "payout_ex_vat": float(item.payout_ex_vat),
                "vat_amount": float(item.vat_amount),
                "payout_inc_vat": float(item.payout_inc_vat),
                "new_balance": float(item.new_balance),
                "will_payout": bool(item.will_payout),
                "vat_rate": float(item.vat_rate),
            }
        )

    items_df = pd.DataFrame(rows)

    summary = {
        "period_key": period_key,
        "period_label": period_label,
        "num_items": len(run_plan.items),
        "num_missing": len(run_plan.missing_names),
        "num_already_run": len(run_plan.already_run_names),
        "num_excluded_in_csv": len(run_plan.excluded_in_csv),
        "num_will_payout": int(items_df["will_payout"].sum()) if not items_df.empty and "will_payout" in items_df.columns else 0,
        "total_payout_inc_vat": float(items_df.loc[items_df["will_payout"] == True, "payout_inc_vat"].sum())
        if not items_df.empty and "will_payout" in items_df.columns and "payout_inc_vat" in items_df.columns
        else 0.0,
        "num_missing_iban": int(items_df["iban"].isna().sum()) if not items_df.empty and "iban" in items_df.columns else 0,
    }

    return {
        "run_plan": run_plan,
        "period_label": period_label,
        "num_items": len(run_plan.items),
        "num_missing": len(run_plan.missing_names),
        "num_already_run": len(run_plan.already_run_names),
        "num_excluded_in_csv": len(run_plan.excluded_in_csv),
        "items_df": items_df,
        "missing_names": run_plan.missing_names,
        "already_run_names": run_plan.already_run_names,
        "summary": summary,
        "excluded_in_csv": run_plan.excluded_in_csv,
    }


def create_payout_batch_for_run(db, run_id: int) -> int:
    """
    Skapar PayoutBatch + PayoutTransaction för alla run_items som will_payout=True.
    Blockar transaktioner som saknar IBAN.
    """
    run = db.query(RoyaltyRun).filter(RoyaltyRun.id == run_id).first()
    if not run:
        raise ValueError(f"Hittar ingen RoyaltyRun med id={run_id}")

    items = db.query(RoyaltyRunItem).filter(
        RoyaltyRunItem.run_id == run_id,
        RoyaltyRunItem.will_payout == True,
    ).all()

    batch = PayoutBatch(
        run_id=run_id,
        period=run.period,
        currency="SEK",
        total_amount=Decimal("0"),
        num_transactions=0,
        status="created",
    )
    db.add(batch)
    db.flush()

    total = Decimal("0")
    n = 0

    for it in items:
        author = db.query(Author).filter(Author.id == it.author_id).first()
        creditor_iban = (author.bank_account or "").replace(" ", "") if author else ""
        status = "pending"
        block_reason = None
        if not creditor_iban:
            status = "blocked"
            block_reason = "Saknar IBAN i databasen"

        amount = _dec(it.payout_inc_vat)
        total += amount
        n += 1

        end_to_end_id = f"RUN{run_id}-A{it.author_id}"
        tx = PayoutTransaction(
            batch_id=batch.id,
            author_id=it.author_id,
            creditor_name=it.author_name,
            creditor_email=it.author_email,
            creditor_iban=creditor_iban or None,
            amount=amount,
            end_to_end_id=end_to_end_id,
            status=status,
            block_reason=block_reason,
        )
        db.add(tx)

    batch.total_amount = total
    batch.num_transactions = n
    db.commit()
    return int(batch.id)

def export_blocked_xlsx_bytes(db, batch_id: int) -> bytes:
    """
    Skapar en enkel Blocked.xlsx (för transaktioner med status=blocked) och returnerar filinnehåll som bytes.
    """
    txs = db.query(PayoutTransaction).filter(
        PayoutTransaction.batch_id == batch_id,
        PayoutTransaction.status == "blocked",
    ).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Blocked"

    ws.append(["author_id", "creditor_name", "creditor_email", "creditor_iban", "amount", "reason"])

    for t in txs:
        ws.append([
            t.author_id,
            t.creditor_name,
            t.creditor_email,
            t.creditor_iban,
            float(t.amount),
            t.block_reason,
        ])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def export_bookkeeping_csv_bytes(db, run_id: int) -> bytes:
    """
    Minimal bokföringsunderlag som CSV (MVP).
    Du kan byta till ert befintliga bokföringsunderlag om ni redan har det i main.
    """
    items = db.query(RoyaltyRunItem).filter(RoyaltyRunItem.run_id == run_id).all()
    rows = []
    for it in items:
        rows.append({
            "run_id": run_id,
            "author_id": it.author_id,
            "author_name": it.author_name,
            "payout_ex_vat": float(it.payout_ex_vat),
            "vat_amount": float(it.vat_amount),
            "payout_inc_vat": float(it.payout_inc_vat),
            "will_payout": bool(it.will_payout),
        })
    df = pd.DataFrame(rows)
    return df.to_csv(index=False).encode("utf-8")

def export_pain001_path(batch_id: int) -> Path:
    """
    Återanvänder er exporter (export_pain001.py) genom att importera funktionen vi behöver.
    Exportern i ert repo skriver till out/ och returnerar filnamn via DB.
    Här anropar vi en intern funktion (build_xml) saknas i scriptet, så vi kör scriptets main-liknande funktion.
    """
    # Vi importerar modul och använder dess generate()-funktion om den finns,
    # annars kör vi den modulens "main-style" genom att kalla en hjälpfunktion.
    import export_pain001_ui as p

    # export_pain001.py har ingen "generate" i dagsläget, men har "build_pain001_xml" i koden?
    # Vi tar en säker väg: kalla p.export_batch_to_file(batch_id) om den finns,
    # annars fall back: återskapa minimal export här.
    if hasattr(p, "export_batch_to_file"):
        return Path(p.export_batch_to_file(batch_id))

    # Fallback: använd deras build_* om det finns.
    if hasattr(p, "build_pain001_xml"):
        xml_str = p.build_pain001_xml(batch_id)
        out_path = OUT_DIR / f"pain001_batch_{batch_id}.xml"
        out_path.write_text(xml_str, encoding="utf-8")
        return out_path

    # Sista fallback: kör deras CLI-funktion via ett minimalt "subprocess"-anrop hade varit möjligt,
    # men i MVP gör vi hellre tydligt fel.
    raise RuntimeError(
        "Hittar ingen export-funktion i export_pain001.py. "
        "Lägg gärna till en funktion 'export_batch_to_file(batch_id) -> str' "
        "så kan UI:t anropa den direkt."
    )
from models import RoyaltyRun, RoyaltyRunItem
from datetime import datetime


def approve_run_plan(db, run_plan):
    """
    Sparar en godkänd run i databasen.
    """
    run = RoyaltyRun(
        period_label=run_plan.period_label,
        created_at=datetime.utcnow(),
        status="APPROVED",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    for item in run_plan.items:
        db_item = RoyaltyRunItem(
            run_id=run.id,
            author_id=item.author.id,
            prev_balance=item.prev_balance,
            today_share=item.today_share,
            payout_ex_vat=item.payout_ex_vat,
            vat_amount=item.vat_amount,
            payout_inc_vat=item.payout_inc_vat,
            new_balance=item.new_balance,
            will_payout=item.will_payout,
        )
        db.add(db_item)

    db.commit()

    return run.id