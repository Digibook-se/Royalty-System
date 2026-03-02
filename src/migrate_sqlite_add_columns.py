import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./royalty.db")

if not DATABASE_URL.startswith("sqlite:///"):
    raise RuntimeError("Denna migrering är bara för SQLite (sqlite:///...)")

db_path = DATABASE_URL.replace("sqlite:///", "", 1)

print("Använder DB:", db_path)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

def col_exists(table: str, col: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    return col in cols

changes = []

# royalties: lägg till period/paid_at/payout_id om de saknas
if not col_exists("royalties", "period"):
    cur.execute("ALTER TABLE royalties ADD COLUMN period TEXT")
    changes.append("royalties.period")

if not col_exists("royalties", "paid_at"):
    cur.execute("ALTER TABLE royalties ADD COLUMN paid_at DATETIME")
    changes.append("royalties.paid_at")

if not col_exists("royalties", "payout_id"):
    cur.execute("ALTER TABLE royalties ADD COLUMN payout_id TEXT")
    changes.append("royalties.payout_id")

conn.commit()
conn.close()

if changes:
    print("Klart! Lade till kolumner:", ", ".join(changes))
else:
    print("Inga ändringar behövdes (kolumner fanns redan).")
