#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from pathlib import Path
from decimal import Decimal

from dotenv import load_dotenv
load_dotenv()

from models import SessionLocal, PayoutBatch, PayoutTransaction, Author

OUT_DIR = Path("out")
OUT_DIR.mkdir(exist_ok=True)
def write_xlsx(path: Path, header, rows, sheet_name: str = "Blocked"):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    wb.save(path)


def dec(x) -> Decimal:
    if x is None:
        return Decimal("0")
    return Decimal(str(x))

def parse_args():
    p = argparse.ArgumentParser(description="Exportera blockerade utbetalningar (t.ex. saknar IBAN) för en payout-batch.")
    p.add_argument("--batch-id", type=int, required=True, help="payout_batches.id")
    p.add_argument("--status", type=str, default="blocked", help="status att exportera (default: blocked)")
    p.add_argument("--out", type=str, default="", help="valfri outputfil, annars auto i out/")
    return p.parse_args()

def main():
    args = parse_args()
    db = SessionLocal()
    try:
        batch = db.query(PayoutBatch).filter(PayoutBatch.id == args.batch_id).first()
        if not batch:
            raise SystemExit(f"❌ Hittar ingen payout-batch med id={args.batch_id}")

        q = (
            db.query(PayoutTransaction, Author)
            .join(Author, Author.id == PayoutTransaction.author_id)
            .filter(PayoutTransaction.batch_id == batch.id)
            .filter(PayoutTransaction.status == args.status)
        )
        rows = q.all()

        if not args.out:
            out_path = OUT_DIR / f"blocked_payouts_{batch.period}_batch{batch.id}.xlsx"
        else:
            out_path = Path(args.out)

        header = [
            "batch_id", "period",
            "author_id", "author_name", "author_email",
            "amount_sek", "currency",
            "status", "block_reason",
            "creditor_iban", "raw_bank_account",
            "vat_registered", "vat_number",
        ]

        out_rows = []
        total = Decimal("0")

        for t, a in rows:
            amt = dec(getattr(t, "amount", None) or 0)
            total += amt

            out_rows.append([
                batch.id,
                batch.period,
                a.id,
                a.name or "",
                a.email or "",
                f"{amt.quantize(Decimal('0.01')):,.2f}".replace(",", "X").replace(".", ",").replace("X", " "),
                getattr(t, "currency", "SEK") or "SEK",
                t.status,
                getattr(t, "block_reason", "") or "",
                getattr(t, "creditor_iban", "") or "",
                # raw bank account från author (kan innehålla lokalt konto)
                getattr(a, "bank_account", "") or "",
                "1" if bool(getattr(a, "vat_registered", False)) else "0",
                getattr(a, "vat_number", "") or "",
            ])

        write_xlsx(out_path, header, out_rows, sheet_name=f"Batch{batch.id}")


        print("✅ Blocked-lista exporterad")
        print(f"- Batch: {batch.id} ({batch.period})")
        print(f"- Status: {args.status}")
        print(f"- Antal rader: {len(out_rows)}")
        print(f"- Total (sum amount): {total} SEK")
        print(f"- Fil: {out_path}")

        # Extra: snabb summering per block_reason
        reasons = {}
        for r in out_rows:
            reason = r[8] or "(none)"
            reasons[reason] = reasons.get(reason, 0) + 1

        if reasons:
            print("\nOrsaker:")
            for k, v in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"- {k}: {v}")

    finally:
        db.close()

if __name__ == "__main__":
    main()
