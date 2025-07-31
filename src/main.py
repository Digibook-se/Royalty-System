#!/usr/bin/env python3
import sys
# Ensure UTF-8 output in terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import os
import logging
from datetime import datetime
import unicodedata

from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError

# Pipeline modules
from elib_client import fetch_invoice_csv, aggregate
from report import make_report
from emailer import send_report
from models import Base, engine, SessionLocal, Author, Royalty


def to_ascii(s: str) -> str:
    """Normalize Unicode strings to ASCII by stripping diacritics."""
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')


def main():
    # Load environment variables
    load_dotenv()

    # Configure logging to STDERR with UTF-8 encoding
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8"
    )

    # Create tables if they don't exist
    Base.metadata.create_all(engine)

    # Open DB session
    db = SessionLocal()
    try:
        # Read authors from DB
        authors = {u.name: u for u in db.query(Author).all()}
        logging.info(f"Hämtade {len(authors)} författare från databasen via webhook")

        # Fetch and aggregate eLib data
        df = fetch_invoice_csv()
        logging.info(f"Hämtade {len(df)} rader från eLib-CSV")
        agg_df = aggregate(df)
        logging.info(f"Konsoliderade till {len(agg_df)} unika royaltyrader")

        # Determine quarter for email subject
        now = datetime.now()
        quarter = f"Q{(now.month-1)//3 + 1} {now.year}"

        # Test settings: redirect to TEST_EMAIL and limit by TEST_LIMIT
        test_email = os.getenv("TEST_EMAIL")
        test_limit = int(os.getenv("TEST_LIMIT", "0"))
        sent = 0

        # Loop per author
        for author_name, group in agg_df.groupby("Författarnamn"):
            name_ascii = to_ascii(author_name)
            author_obj = authors.get(author_name)
            if not author_obj:
                logging.warning(f"Ingen författare i databasen för '{name_ascii}'")
                continue

            recipient = test_email or author_obj.email

            # Generate PDF report
            pdf_bytes = make_report(author_name, group)

            # Send email
            try:
                send_report(recipient, pdf_bytes, quarter)
                sent += 1
                logging.info(f"Skickade rapport för {name_ascii} till {recipient}")
            except Exception as ex:
                err = to_ascii(str(ex))
                logging.error(f"Fel vid mejlskick för {name_ascii} till {recipient}: {err}")
                continue

            # Stop after test limit
            if test_limit and sent >= test_limit:
                logging.info(f"Nått testgräns på {test_limit} mejl, avbryter.")
                break

            # Save royalty entries in DB
            for _, row in group.iterrows():
                royalty = Royalty(
                    author_id=author_obj.id,
                    isbn=row["ISBN"],
                    title=row["Titel"],
                    net_amount=row["Nettobelopp"],
                    author_share=row["AuthorShare"],
                    publisher_share=row["PublisherShare"],
                    created_at=datetime.utcnow()
                )
                db.add(royalty)
        db.commit()

    except (SQLAlchemyError, Exception) as e:
        err_msg = to_ascii(str(e))
        logging.error(f"Ett fel uppstod i pipelinen: {err_msg}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
