#!/usr/bin/env python3
import os
import csv
import sqlite3
from datetime import datetime

DB_PATH = "royalty.db"
OUT_DIR = "out"

def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = os.path.join(OUT_DIR, f"contacts_missing_iban_{ts}.csv")
    out_txt = os.path.join(OUT_DIR, f"contacts_missing_iban_{ts}.txt")

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()

        # Hämta senaste batchen
        cur.execute("select id, period from payout_batches order by id desc limit 1")
        row = cur.fetchone()
        if not row:
            raise SystemExit("❌ Hittar ingen payout_batch i DB. Kör create_payout_batch.py först.")
        batch_id, period = row

        # Blockerade pga not_iban
        cur.execute("""
            select
              id,
              author_id,
              creditor_name,
              creditor_email,
              amount,
              creditor_iban,
              block_reason
            from payout_transactions
            where batch_id = ?
              and status = 'blocked'
              and block_reason = 'not_iban'
            order by amount desc
        """, (batch_id,))
        rows = cur.fetchall()

        if not rows:
            print(f"✅ Inga not_iban-blockerade hittades i senaste batchen (batch_id={batch_id}).")
            return

        # Skriv CSV
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "batch_id", "period",
                "payout_tx_id", "author_id",
                "name", "email",
                "amount_sek",
                "bank_account_raw",
                "block_reason"
            ])
            for (tx_id, author_id, name, email, amount, bank_raw, reason) in rows:
                w.writerow([
                    batch_id, period,
                    tx_id, author_id,
                    name or "", email or "",
                    f"{float(amount):.2f}",
                    bank_raw or "",
                    reason or ""
                ])

        # Skriv TXT (snabb överblick)
        lines = []
        lines.append(f"Kontakter som saknar IBAN (batch_id={batch_id}, period={period})")
        lines.append("=" * 72)
        lines.append(f"Antal blockerade (not_iban): {len(rows)}")
        lines.append("")
        for (tx_id, author_id, name, email, amount, bank_raw, reason) in rows:
            lines.append(f"- {name or 'OKÄND'} | {email or 'saknar email'} | {float(amount):.2f} SEK | bank_raw='{bank_raw or ''}'")

        with open(out_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        print("✅ Klart! Exporterade kontaktlista.")
        print(f"- CSV: {out_csv}")
        print(f"- TXT: {out_txt}")
        print(f"- Antal: {len(rows)} (not_iban) från batch_id={batch_id}, period={period}")

    finally:
        con.close()

if __name__ == "__main__":
    main()
