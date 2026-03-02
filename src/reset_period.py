#!/usr/bin/env python3
import sys
import os
from dotenv import load_dotenv
from sqlalchemy import delete
from sqlalchemy.orm import Session

load_dotenv()

from models import engine, Royalty, Author


PERIOD = "2025-Q3"


def main():
    print("=" * 60)
    print(f"RESET AV ROYALTY-DATA FÖR PERIOD: {PERIOD}")
    print("=" * 60)

    apply = os.getenv("APPLY", "0") == "1"

    with Session(engine) as db:
        # Hur många royaltyrader?
        royalty_rows = (
            db.query(Royalty)
            .filter(Royalty.period == PERIOD)
            .count()
        )

        # Hur många unika författare?
        author_ids = (
            db.query(Royalty.author_id)
            .filter(Royalty.period == PERIOD)
            .distinct()
            .all()
        )
        num_authors = len(author_ids)

        print(f"\nHITTAT I DB:")
        print(f"- Royalty-rader:        {royalty_rows}")
        print(f"- Berörda författare:   {num_authors}")

        if royalty_rows == 0:
            print("\nIngenting att radera. Avslutar.")
            return

        if not apply:
            print("\n⚠️  PREVIEW-LÄGE")
            print("Inget har raderats.")
            print("\nFör att VERKSTÄLLA, kör:")
            print("  APPLY=1 python3 src/reset_period.py")
            return

        # Säkerhetsfråga
        answer = input(
            f"\n⚠️  Detta kommer RADERA ALL royalty-data för {PERIOD}.\n"
            "Skriv EXACT 'YES' för att fortsätta: "
        ).strip()

        if answer != "YES":
            print("\nAvbrutet. Ingen data har ändrats.")
            return

        # Radera
        result = db.execute(
            delete(Royalty).where(Royalty.period == PERIOD)
        )
        db.commit()

        print("\n✅ KLART")
        print(f"- Raderade royalty-rader: {result.rowcount}")
        print(f"- Period {PERIOD} är nu återställd för test.")


if __name__ == "__main__":
    main()
