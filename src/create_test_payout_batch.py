#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from decimal import Decimal
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from models import SessionLocal, PayoutBatch, PayoutTransaction, Author

def parse_args():
    p = argparse.ArgumentParser(description="Skapa TEST payout-batch med 2 authors och 1 kr vardera.")
    p.add_argument("--run-id", type=int, required=True, help="royalty_runs.id (payout_batches.run_id är NOT NULL)")
    p.add_argument("--period", required=True, help="Ex: TEST-2026-02")
    p.add_argument("--author-id-1", type=int, required=True, help="authors.id för testmottagare 1")
    p.add_argument("--author-id-2", type=int, required=True, help="authors.id för testmottagare 2")
    p.add_argument("--amount", default="1.00", help="Belopp per transaktion (default 1.00)")
    return p.parse_args()

def main():
    args = parse_args()
    db = SessionLocal()
    try:
        amt = Decimal(str(args.amount)).quantize(Decimal("0.01"))

        a1 = db.query(Author).filter(Author.id == args.author_id_1).first()
        a2 = db.query(Author).filter(Author.id == args.author_id_2).first()
        if not a1 or not a2:
            raise SystemExit("❌ Hittar inte author-id-1 eller author-id-2 i authors-tabellen.")

        if not (a1.bank_account and a1.bank_account.strip()):
            raise SystemExit(f"❌ Author {a1.id} saknar bank_account (IBAN).")
        if not (a2.bank_account and a2.bank_account.strip()):
            raise SystemExit(f"❌ Author {a2.id} saknar bank_account (IBAN).")

        batch = PayoutBatch(
            run_id=args.run_id,
            period=args.period,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            total_amount=Decimal("0.00"),
            num_transactions=0,
        )
        db.add(batch)
        db.flush()

        txs = [
            PayoutTransaction(
                batch_id=batch.id,
                author_id=a1.id,
                creditor_name=a1.name,
                creditor_email=a1.email,
                creditor_iban=(a1.bank_account or "").replace(" ", "").upper(),
                amount=amt,
                end_to_end_id=f"TEST-{batch.id}-1",
                status="pending",
                block_reason=None,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
            PayoutTransaction(
                batch_id=batch.id,
                author_id=a2.id,
                creditor_name=a2.name,
                creditor_email=a2.email,
                creditor_iban=(a2.bank_account or "").replace(" ", "").upper(),
                amount=amt,
                end_to_end_id=f"TEST-{batch.id}-2",
                status="pending",
                block_reason=None,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        ]

        for t in txs:
            db.add(t)

        batch.num_transactions = 2
        batch.total_amount = amt * 2

        db.commit()

        print("✅ Test payout-batch skapad")
        print(f"- batch_id: {batch.id}")
        print(f"- run_id: {batch.run_id}")
        print(f"- period: {batch.period}")
        print(f"- total: {batch.total_amount} SEK")

    finally:
        db.close()

if __name__ == "__main__":
    main()