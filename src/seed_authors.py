#!/usr/bin/env python3
import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from models import engine, Base, SessionLocal, Author

"""
Script för att massuppdatera Authors-tabellen från en Excel-fil.
Placera din Excel-fil (t.ex. authors.xlsx) i projektroten.
Kör: python src/seed_authors.py authors.xlsx
"""


def seed_from_excel(path: str):
    # Ladda miljövariabler (om DB-URL etc behövs)
    load_dotenv()

    # Läs Excel (första ark)
    df = pd.read_excel(path, engine="openpyxl")
    # Vi förväntar kolumner: Name, Email, BankAccount
    expected = {"name", "email", "bank_account"}
    cols = set(df.columns.str.lower())
    if not expected.issubset(cols):
        raise ValueError(f"Saknade kolumner i Excel: {expected - cols}")

    # Standardisera kolumnnamn
    df = df.rename(columns={
        col: col.lower().strip() for col in df.columns
    })

    # Upserta i DB
    Base.metadata.create_all(engine)
    session = SessionLocal()
    count_new = 0
    count_updated = 0
    try:
        for idx, row in df.iterrows():
            email = row["email"]
            name = row.get("name")
            bank = row.get("bank_account")
            # Hitta existerande
            author = session.query(Author).filter_by(email=email).one_or_none()
            if author is None:
                author = Author(email=email, name=name, bank_account=bank)
                session.add(author)
                count_new += 1
            else:
                author.name = name
                author.bank_account = bank
                count_updated += 1
        session.commit()
    except Exception as e:
        session.rollback()
        raise
    finally:
        session.close()

    print(f"Nya författare skapade: {count_new}")
    print(f"Författare uppdaterade: {count_updated}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python seed_authors.py <path_to_excel>")
        sys.exit(1)
    seed_from_excel(sys.argv[1])
