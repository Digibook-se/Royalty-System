#!/usr/bin/env python3
import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Author

"""
Massuppdaterar Authors-tabellen från en Excel-fil utan duplicates.
Kör:
  source venv/bin/activate
  pip install openpyxl
  python src/seed_authors_upsert.py authors.xlsx
"""


def seed_from_excel(path: str):
    # Ladda miljövariabler för DB-URL
    load_dotenv()
    DATABASE_URL = os.getenv("DATABASE_URL")

    # Läs Excel och drop duplicates på email
    df = pd.read_excel(path, engine="openpyxl")
    # Standardisera kolumnnamn till lower-case
    df.columns = [col.lower().strip() for col in df.columns]
    # Kontrollera nödvändiga kolumner
    expected = {"name", "email", "bank_account"}
    if not expected.issubset(df.columns):
        missing = expected - set(df.columns)
        raise ValueError(f"Saknade kolumner i Excel: {missing}")

    # Ta bort dubbletter baserat på email (behåll sista)
    df = df.drop_duplicates(subset=["email"], keep="last")

    # Setup databas
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    new_count = 0
    updated_count = 0
    try:
        for _, row in df.iterrows():
            email = str(row["email"]).strip().lower()
            name = str(row["name"]).strip()
            bank = str(row["bank_account"]).strip()

            # Hitta existerande författare
            author = session.query(Author).filter_by(email=email).one_or_none()
            if author is None:
                author = Author(email=email, name=name, bank_account=bank)
                session.add(author)
                new_count += 1
            else:
                # Uppdatera fält
                author.name = name
                author.bank_account = bank
                updated_count += 1
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"Misslyckades med att uppdatera författare: {e}")
        return
    finally:
        session.close()

    print(f"Nya författare skapade: {new_count}")
    print(f"Författare uppdaterade: {updated_count}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python seed_authors_upsert.py <path_to_excel>")
        sys.exit(1)
    seed_from_excel(sys.argv[1])
