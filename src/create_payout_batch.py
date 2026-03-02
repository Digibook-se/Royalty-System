#!/usr/bin/env python3
import os
import sys
from decimal import Decimal
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from models import SessionLocal, RoyaltyRun, RoyaltyRunItem, PayoutBatch, PayoutTransaction, Author

DEBTOR_NAME = os.getenv("DEBTOR_NAME", "Ditt Bolag AB")
DEBTOR_IBAN = os.getenv("DEBTOR_IBAN", "SE4180000818363030547990").replace(" ", "")

def dec(x) -> Decimal:
    return Decimal(str(x or "0"))

def is_iban(s: str) -> bool:
    s = (s or "").replace(" ", "").upper()
    # Minimal check: startar med 2 bokstäver + minst 13 tecken (SE-IBAN är 24)
    return len(s) >= 15 and s[:2].isalpha() and s[2:4].isdigit()

def parse_args(argv):
    if "--period" not in argv:
        raise SystemExit("❌ Du måste ange --period, t.ex. --period 2025-Q4")
    period = argv[argv.index("--period") + 1]
    run_id = None
    if "--run-id" in argv:
        run_id = int(argv[argv.index("--run-id") + 1])
    return period, run_id

def main():
    period, run_id = parse_args(sys.argv[1:])

    db = SessionLocal()
    try:
        if run_id is None:
            run = (
                db.query(RoyaltyRun)
                .filter(RoyaltyRun.period == period)
                .order_by(RoyaltyRun.id.desc())
                .first()
            )
            if not run:
                raise SystemExit(
                    f"❌ Hittar ingen royalty_run för period {period}. "
                    "Du måste köra src/main.py (utan dry_run) efter att run_plan-sparandet är på plats."
                )
        else:
            run = db.query(RoyaltyRun).filter(RoyaltyRun.id == run_id).first()
            if not run:
                raise SystemExit(f"❌ Hittar ingen royalty_run med id={run_id}")

        items = db.query(RoyaltyRunItem).filter(RoyaltyRunItem.run_id == run.id).all()
        if not items:
            raise SystemExit(f"❌ royalty_run_items saknas för run_id={run.id}")

        batch = PayoutBatch(
            run_id=run.id,
            period=run.period,
            currency="SEK",
            debtor_name=DEBTOR_NAME,
            debtor_iban=DEBTOR_IBAN,
            status="created",
            created_at=datetime.now(timezone.utc),
        )
        db.add(batch)
        db.flush()  # batch.id

        total = Decimal("0")
        num = 0

        authors = db.query(Author.id, Author.bic).all()
        bic_by_author_id = {a_id: (bic or "").strip().upper() for a_id, bic in authors}

        for it in items:
            amount = dec(it.payout_inc_vat)
            if not it.will_payout or amount <= 0:
                continue

            iban = (it.bank_account_snapshot or "").replace(" ", "").upper()
            e2e = f"ROY-{run.period}-{it.author_id}"

            tx = PayoutTransaction(
                batch_id=batch.id,
                author_id=it.author_id,
                creditor_name=it.author_name,
                creditor_email=it.author_email,
                creditor_iban=iban or None,
                amount=str(amount),
                end_to_end_id=e2e,
                status="pending",
            )

            # Blockera om bankkonto saknas eller inte ser ut som IBAN
            bic = bic_by_author_id.get(it.author_id, "").strip().upper()

            if not iban:
                tx.status = "blocked"
                tx.block_reason = "missing_iban"
            elif not is_iban(iban):
                tx.status = "blocked"
                tx.block_reason = "not_iban"
            elif not bic:
                tx.status = "blocked"
                tx.block_reason = "missing_bic"
            else:
                total += amount
                num += 1

            db.add(tx)

        batch.total_amount = str(total)
        batch.num_transactions = int(num)

        db.commit()

        print("✅ Payout-batch skapad")
        print(f"- batch_id: {batch.id}")
        print(f"- period: {batch.period}")
        print(f"- run_id: {batch.run_id}")
        print(f"- num_transactions (ej blocked): {batch.num_transactions}")
        print(f"- total_amount: {batch.total_amount} {batch.currency}")

        blocked = db.query(PayoutTransaction).filter(
            PayoutTransaction.batch_id == batch.id,
            PayoutTransaction.status == "blocked"
        ).count()
        if blocked:
            print(f"⚠️ Blockerade transaktioner: {blocked} (saknar bankkonto eller ej IBAN/BIC)")

    finally:
        db.close()

if __name__ == "__main__":
    main()
