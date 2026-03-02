from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv

# Ladda .env från projektroten och från royalty_ui (om den finns)
ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"      # .../royalty-system/.env
UI_ENV = Path(__file__).resolve().parents[1] / ".env"        # .../royalty_ui/.env

load_dotenv(ROOT_ENV, override=False)
load_dotenv(UI_ENV, override=True)



from contextlib import contextmanager
from typing import Iterator

from models import Base, engine, SessionLocal

def init_db() -> None:
    """Skapar tabeller om de inte finns."""
    Base.metadata.create_all(bind=engine)

@contextmanager
def get_db() -> Iterator:
    """Context manager för SQLAlchemy-session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
