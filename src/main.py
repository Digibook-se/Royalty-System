#!/usr/bin/env python3
import sys
# Ensure UTF-8 output in terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import os
from dotenv import load_dotenv

# Load environment variables as early as possible
load_dotenv()

import logging
from datetime import datetime
from decimal import Decimal
import unicodedata

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

# Pipeline modules (after env loaded so DATABASE_URL is available)
from elib_client import fetch_invoice_csv, aggregate
from report import make_report
from emailer import send_report
from models import Base, engine, SessionLocal, Author, Royalty


def to_ascii(s: str) -> str:
    """Normalize Unicode strings to ASCII by stripping diacritics."""
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')


def main():
    # Parse excluded authors (quoted semicolon-separated)
    raw_excl = os.getenv("EXCLUDED_AUTHORS", "").strip('"')
    excluded = {to_ascii(name.strip()) for name in raw_excl.split(';') if name.strip()} if raw_excl else set()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )

    # Ensure tables exist
    Base.metadata.create_all(engine)
    db = SessionLocal()

    try:
        # Load authors from DB and normalize names as keys
        authors = db.query(Author).all()
        author_objs = {to_ascii(a.name): a for a in authors}
        logging.info(f"Hämtade {len(author_objs)} författare från databasen")

        # Fetch and aggregate data
        df = fetch_invoice_csv()
        logging.info(f"Hämtade {len(df)} rader från eLib-CSV")
        agg_df = aggregate(df)
        logging.info(f"Konsoliderade till {len(agg_df)} unika royaltyrader")

        # Determine period label
        now = datetime.now()
        quarter = f"Q{(now.month-1)//3 + 1} {now.year}"

        # Test settings
        test_email = os.getenv("TEST_EMAIL")
        test_limit = int(os.getenv("TEST_LIMIT", "0"))
        sent = 0

        # Process each author group
        for author_name, group in agg_df.groupby("Författarnamn"):
            norm_name = to_ascii(author_name)
            # Skip if excluded
            if norm_name in excluded:
                logging.info(f"Hoppar över {author_name} (i EXCLUDED_AUTHORS)")
                continue

            # Look up author object
            author = author_objs.get(norm_name)
            if not author:
                logging.warning(f"Ingen författare i DB för '{author_name}'")
                continue

            # Compute carry & payout
            prev_balance = author.carried_balance or Decimal('0')
            today_share = Decimal(group['AuthorShare'].sum())
            if prev_balance + today_share < Decimal('100'):
                paid_out = Decimal('0')
                new_balance = prev_balance + today_share
            else:
                paid_out = prev_balance + today_share
                new_balance = Decimal('0')

            # Generate PDF report
            recipient = test_email or author.email
            pdf_bytes = make_report(author_name, group, quarter, prev_balance, new_balance)

            # Send email
            try:
                send_report(recipient, pdf_bytes, quarter)
                sent += 1
                logging.info(f"Skickade rapport för {author_name} till {recipient}")
            except Exception:
                logging.exception(f"Fel vid mejlskick för {author_name} till {recipient}")

            # Update carried_balance in DB
            db.execute(
                update(Author)
                .where(Author.id == author.id)
                .values(carried_balance=new_balance)
            )

            # Persist royalty entries
            for _, row in group.iterrows():
                royalty = Royalty(
                    author_id=author.id,
                    isbn=row['ISBN'],
                    title=row['Titel'],
                    net_amount=row['Nettobelopp'],
                    author_share=row['AuthorShare'],
                    publisher_share=row['PublisherShare'],
                    created_at=datetime.utcnow()
                )
                db.add(royalty)

            # Respect test limit
            if test_limit and sent >= test_limit:
                logging.info(f"Nått testgräns på {test_limit} mejl, avbryter.")
                break

        db.commit()
    except (SQLAlchemyError, Exception) as e:
        logging.exception(f"Ett fel uppstod i pipelinen: {to_ascii(str(e))}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
