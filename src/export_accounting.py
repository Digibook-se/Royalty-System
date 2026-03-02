#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv
import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()

# Samma DB-modeller som resten av projektet
from models import SessionLocal, RoyaltyRun, RoyaltyRunItem

OUT_DIR = Path("out")
OUT_DIR.mkdir(exist_ok=True)

# --- Konton (BAS/Handelsbolag – rimliga standardval) ---
ACCOUNT_ROYALTY_EXPENSE = "7010"  # Royaltykostnader
ACCOUNT_INPUT_VAT = "2641"        # Ingående moms
ACCOUNT_BANK = "1930"             # Företagskonto/bank
ACCOUNT_ACCRUED = "2990"          # Upplupna kostnader (royalty som kvarhålls)

DEFAULT_TEXT = "Royalty {period_label}"


def dec(x) -> Decimal:
    if x is None:
        return Decimal("0")
    return Decimal(str(x))


def fmt2(x: Decimal) -> str:
    # för CSV/XLSX: 2 decimaler med punkt
    return f"{x.quantize(Decimal('0.01'))}"


def parse_args():
    p = argparse.ArgumentParser(description="Exportera bokföringsunderlag för royalty-run (1930, 7010, 2641, 2990).")
    p.add_argument("--run-id", type=int, required=True, help="royalty_runs.id")
    p.add_argument("--ver-date", type=str, default=str(date.today()), help="Verifikationsdatum (YYYY-MM-DD). Default idag.")
    p.add_argument("--prefix", type=str, default="", help="Valfri prefix på filnamn, t.ex. 'TEST_'")
    return p.parse_args()


def write_csv(path: Path, header, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def try_write_xlsx(path: Path, sheet_name: str, header, rows):
    # valfritt – om openpyxl finns
    try:
        from openpyxl import Workbook
    except Exception:
        return False

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return True


def main():
    args = parse_args()

    db = SessionLocal()
    try:
        run = db.query(RoyaltyRun).filter(RoyaltyRun.id == args.run_id).first()
        if not run:
            raise SystemExit(f"❌ Hittar ingen RoyaltyRun med id={args.run_id}")

        items = db.query(RoyaltyRunItem).filter(RoyaltyRunItem.run_id == run.id).all()
        if not items:
            raise SystemExit(f"❌ Inga RoyaltyRunItem hittades för run_id={run.id}")

        period_key = run.period or "UNKNOWN"
        period_label = getattr(run, "period_label", None) or period_key
        ver_text = DEFAULT_TEXT.format(period_label=period_label)
        ver_date = args.ver_date

        # --- Summeringar ---
        # OBS: Vi aggregerar per author_id så alias-rader summeras per författare.
        per_author = defaultdict(lambda: {
            "name": "",
            "email": "",
            "payout_ex_vat": Decimal("0"),
            "vat_amount": Decimal("0"),
            "payout_inc_vat": Decimal("0"),
            "carry": Decimal("0"),
            "will_payout_any": False,
            "vat_registered_any": False,
        })

        total_payout_ex = Decimal("0")
        total_vat = Decimal("0")
        total_payout_inc = Decimal("0")
        total_carry = Decimal("0")

        for it in items:
            aid = it.author_id
            a = per_author[aid]
            a["name"] = it.author_name or a["name"]
            a["email"] = it.author_email or a["email"]

            payout_ex = dec(it.payout_ex_vat)
            vat_amt = dec(it.vat_amount)
            payout_inc = dec(it.payout_inc_vat)

            # carry/logik: vi bokar upplupet endast på de rader som INTE betalas ut
            will_payout = bool(it.will_payout)

            if will_payout:
                total_payout_ex += payout_ex
                total_vat += vat_amt
                total_payout_inc += payout_inc

                a["payout_ex_vat"] += payout_ex
                a["vat_amount"] += vat_amt
                a["payout_inc_vat"] += payout_inc
                a["will_payout_any"] = True
                a["vat_registered_any"] = a["vat_registered_any"] or bool(getattr(it, "vat_registered", False))
            else:
                carry = dec(it.new_balance)
                total_carry += carry
                a["carry"] += carry

        # Royaltykostnad = utbetalt ex moms + carry (upplupet)
        total_expense = (total_payout_ex + total_carry).quantize(Decimal("0.01"))

        total_vat = total_vat.quantize(Decimal("0.01"))
        total_payout_inc = total_payout_inc.quantize(Decimal("0.01"))
        total_carry = total_carry.quantize(Decimal("0.01"))

        # --- Verifikationsrader (1930, 7010, 2641, 2990) ---
        # Debet: 7010 (kostnad), 2641 (ing moms)
        # Kredit: 1930 (utbetalning inkl moms), 2990 (upplupet/carry)
        voucher_rows = []

        # 7010 Debet
        if total_expense != 0:
            voucher_rows.append([ver_date, ver_text, ACCOUNT_ROYALTY_EXPENSE, fmt2(total_expense), "", "Royaltykostnad (utbetalt exkl moms + carry)"])

        # 2641 Debet
        if total_vat != 0:
            voucher_rows.append([ver_date, ver_text, ACCOUNT_INPUT_VAT, fmt2(total_vat), "", "Ingående moms på royalty (momsregistrerade författare)"])

        # 1930 Kredit
        if total_payout_inc != 0:
            voucher_rows.append([ver_date, ver_text, ACCOUNT_BANK, "", fmt2(total_payout_inc), "Utbetalning royalty inkl moms"])

        # 2990 Kredit (carry)
        if total_carry != 0:
            voucher_rows.append([ver_date, ver_text, ACCOUNT_ACCRUED, "", fmt2(total_carry), "Upplupen royalty (carry-over till nästa period)"])

        # Kontroll: Debet = Kredit
        debit_sum = sum((dec(r[3]) for r in voucher_rows if r[3]), Decimal("0"))
        credit_sum = sum((dec(r[4]) for r in voucher_rows if r[4]), Decimal("0"))
        if debit_sum.quantize(Decimal("0.01")) != credit_sum.quantize(Decimal("0.01")):
            raise RuntimeError(f"❌ Verifikationen balanserar inte. Debet={debit_sum} Kredit={credit_sum}")

        # --- Detaljrapport per författare ---
        detail_rows = []
        for aid, a in sorted(per_author.items(), key=lambda kv: (kv[1]["name"] or "", kv[0])):
            detail_rows.append([
                aid,
                a["name"],
                a["email"],
                fmt2(a["payout_ex_vat"].quantize(Decimal("0.01"))),
                fmt2(a["vat_amount"].quantize(Decimal("0.01"))),
                fmt2(a["payout_inc_vat"].quantize(Decimal("0.01"))),
                fmt2(a["carry"].quantize(Decimal("0.01"))),
            ])

        prefix = args.prefix or ""
        base = f"{prefix}accounting_{period_key}_run{run.id}"

        voucher_csv = OUT_DIR / f"{base}_voucher.csv"
        detail_csv = OUT_DIR / f"{base}_detail.csv"

        write_csv(
            voucher_csv,
            header=["date", "text", "account", "debit", "credit", "comment"],
            rows=voucher_rows
        )
        write_csv(
            detail_csv,
            header=["author_id", "name", "email", "payout_ex_vat", "vat_amount", "payout_inc_vat", "carry_over"],
            rows=detail_rows
        )

        # Valfritt XLSX
        voucher_xlsx = OUT_DIR / f"{base}_voucher.xlsx"
        detail_xlsx = OUT_DIR / f"{base}_detail.xlsx"
        xlsx1 = try_write_xlsx(voucher_xlsx, "Voucher", ["date", "text", "account", "debit", "credit", "comment"], voucher_rows)
        xlsx2 = try_write_xlsx(detail_xlsx, "Detail", ["author_id", "name", "email", "payout_ex_vat", "vat_amount", "payout_inc_vat", "carry_over"], detail_rows)

        # --- Print summary ---
        print("✅ Bokföringsunderlag skapat")
        print(f"- Run: {run.id} ({period_label})")
        print(f"- Utbetalt exkl moms: {fmt2(total_payout_ex.quantize(Decimal('0.01')))}")
        print(f"- Moms (ingående):   {fmt2(total_vat)}")
        print(f"- Utbetalt inkl moms:{fmt2(total_payout_inc)}")
        print(f"- Carry-over:        {fmt2(total_carry)}")
        print(f"- Royaltykostnad:    {fmt2(total_expense)}")
        print("")
        print("Filer:")
        print(f"- {voucher_csv}")
        print(f"- {detail_csv}")
        if xlsx1 and xlsx2:
            print(f"- {voucher_xlsx}")
            print(f"- {detail_xlsx}")
        else:
            print("(XLSX hoppades över – installera openpyxl om du vill ha Excel: pip install openpyxl)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
