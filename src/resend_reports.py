#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
from pathlib import Path
from decimal import Decimal

from dotenv import load_dotenv
load_dotenv()

# Viktigt: importera från samma nivå som övriga scripts körs på (src/)
from models import SessionLocal, Author, RoyaltyRun, RoyaltyRunItem
from report import make_report
from emailer import send_report

def parse_args(argv):
    run_id = None
    dry_run = False
    test_email = None
    limit = 0

    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--run-id":
            run_id = int(argv[i+1]); i += 2; continue
        if a == "--dry-run":
            dry_run = True; i += 1; continue
        if a == "--test-email":
            test_email = argv[i+1]; i += 2; continue
        if a == "--limit":
            limit = int(argv[i+1]); i += 2; continue
        print(f"Okänd flagga: {a}")
        sys.exit(1)

    if run_id is None:
        print("❌ Du måste ange --run-id, ex: python src/resend_reports.py --run-id 12")
        sys.exit(1)

    return run_id, dry_run, test_email, limit


def dec(x) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_id, dry_run, test_email, limit = parse_args(sys.argv[1:])

    db = SessionLocal()
    try:
        run = db.query(RoyaltyRun).filter(RoyaltyRun.id == run_id).first()
        if not run:
            raise SystemExit(f"❌ Hittar ingen RoyaltyRun med id={run_id}")

        items = (
            db.query(RoyaltyRunItem)
            .filter(RoyaltyRunItem.run_id == run_id)
            .order_by(RoyaltyRunItem.id.asc())
            .all()
        )
        if not items:
            raise SystemExit(f"❌ Inga royalty_run_items hittades för run_id={run_id}")

        logging.info(f"Run {run_id} – period={run.period_label or run.period} – items={len(items)}")
        if dry_run:
            logging.info("dry-run: inga mejl skickas (skapar bara PDF i minnet).")

        sent = 0
        failed = 0

        for it in items:
            # Hämta Author för PDF-data per ISBN/titel från royalties-tabellen?
            # Vi bygger rapporten från it + (valfritt) royalties för perioden.
            author = db.query(Author).filter(Author.id == it.author_id).first()
            if not author:
                logging.warning(f"Hoppar över author_id={it.author_id} (saknas i authors)")
                failed += 1
                continue

            recipient = test_email or it.author_email or author.email
            if not recipient:
                logging.warning(f"Saknar email för {author.name} (author_id={author.id}) – hoppar över")
                failed += 1
                continue

            # Bygg en minimal dataframe-liknande struktur för report.make_report
            # Din make_report verkar förvänta sig en dataframe med rader per titel.
            # Vi kan läsa ut alla Royalty-rader för perioden och författaren via modellerna,
            # men för att inte gissa fel här använder vi snapshots i RunItem + tom lista,
            # och låter make_report hantera totals (om den kräver detaljer, säg till så kopplar vi på Royalties).
            #
            # Säkert val: bygg report från DB-royalties om de finns:
            from models import Royalty
            rows = (
                db.query(Royalty)
                .filter(Royalty.author_id == author.id, Royalty.period == run.period)
                .all()
            )

            # Om inga royalty-rader finns (t.ex. om mailen failade innan commit i execute-loopen),
            # kan vi fortfarande skicka en “sammanfattningsrapport” – men då behöver report.py stöd.
            # Här gör vi: om 0 rader -> hoppa och logga, så du ser vilka som saknar underlag.
            if not rows:
                logging.warning(f"Inga royalty-rader i DB för {author.name} ({run.period}) – kan inte skapa PDF")
                failed += 1
                continue

            # Skapa dataframe-liknande via pandas (report.py använder oftast pandas)
            import pandas as pd
            df = pd.DataFrame([{
                "Titel": r.title,
                "ISBN": r.isbn,
                "Nettobelopp": float(r.net_amount or 0),
                "AuthorShare": float(r.author_share or 0),
                "PublisherShare": float(r.publisher_share or 0),
                "Författarnamn": author.name,
            } for r in rows])

            prev_balance = float(dec(it.prev_balance))
            period_share = float(dec(it.today_share))
            payout_ex_vat = float(dec(it.payout_ex_vat))
            carry_to_next = float(dec(it.new_balance))

            vat_rate = dec(getattr(it, "vat_rate", None) or 0)  # om finns i tabellen
            # Om vat_rate saknas i schema: räkna från vat_amount/payout_ex_vat
            if vat_rate == 0 and dec(it.payout_ex_vat) != 0:
                try:
                    vat_rate = (dec(it.vat_amount) / dec(it.payout_ex_vat))
                except Exception:
                    vat_rate = Decimal("0")

            pdf_bytes = make_report(
                author.name,
                df,
                run.period_label or run.period,
                prev_balance,
                period_share,
                payout_ex_vat,
                carry_to_next,
                vat_rate=vat_rate,
            )

            if dry_run:
                logging.info(f"[dry-run] Skulle skicka till {recipient}: {author.name}")
                sent += 1
            else:
                try:
                    send_report(recipient, pdf_bytes, run.period_label or run.period)
                    logging.info(f"✅ Skickade till {recipient}: {author.name}")
                    sent += 1
                except Exception as e:
                    logging.warning(f"❌ Kunde inte skicka till {recipient}: {e}")
                    failed += 1

            if limit and sent >= limit:
                logging.info("Limit nådd – avbryter")
                break

        logging.info(f"KLART – sent={sent}, failed={failed}")

    finally:
        db.close()


if __name__ == "__main__":
    main()