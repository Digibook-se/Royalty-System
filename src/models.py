import os
from datetime import datetime
from dotenv import load_dotenv

from sqlalchemy import (
    Column, Integer, Boolean, String, Numeric, DateTime,
    create_engine
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Ladda .env
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./royalty.db")

Base = declarative_base()


class Author(Base):
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=True, index=True)
    bank_account = Column(String, nullable=True)
    vat_registered = Column(Boolean, default=False, nullable=False)
    vat_number = Column(String, nullable=True)
    carried_balance = Column(Numeric, default=0)
    bic = Column(String, nullable=True)


class Royalty(Base):
    __tablename__ = "royalties"

    id = Column(Integer, primary_key=True, index=True)
    author_id = Column(Integer, nullable=False, index=True)

    isbn = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)

    net_amount = Column(Numeric, nullable=False)
    author_share = Column(Numeric, nullable=False)
    publisher_share = Column(Numeric, nullable=False)

    period = Column(String, nullable=True, index=True)     # ex: "2025-Q4"
    paid_at = Column(DateTime, nullable=True)
    payout_id = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ============================================================
# NYTT: Run-plan (sparar exakt underlaget för bankfil)
# ============================================================

class RoyaltyRun(Base):
    __tablename__ = "royalty_runs"

    id = Column(Integer, primary_key=True, index=True)
    period = Column(String, nullable=False, index=True)       # "2025-Q4"
    period_label = Column(String, nullable=True)              # "Q4 2025"
    vat_rate = Column(Numeric, nullable=False)                # t.ex. 0.06
    payout_threshold = Column(Numeric, nullable=False)        # t.ex. 100
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RoyaltyRunItem(Base):
    __tablename__ = "royalty_run_items"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, nullable=False, index=True)
    author_id = Column(Integer, nullable=False, index=True)

    # Snapshot av identitet/bank vid körning
    author_name = Column(String, nullable=True)
    author_email = Column(String, nullable=True)
    bank_account_snapshot = Column(String, nullable=True)

    # Snapshot av momsstatus vid körning
    vat_registered = Column(Boolean, default=False, nullable=False)
    vat_number = Column(String, nullable=True)

    # Plan-beräkningar
    prev_balance = Column(Numeric, nullable=False)
    today_share = Column(Numeric, nullable=False)
    payout_ex_vat = Column(Numeric, nullable=False)
    vat_amount = Column(Numeric, nullable=False)
    payout_inc_vat = Column(Numeric, nullable=False)
    new_balance = Column(Numeric, nullable=False)
    will_payout = Column(Boolean, default=False, nullable=False)


# ============================================================
# NYTT: Payout batch + transaktioner (det som bankfilen bygger på)
# ============================================================

class PayoutBatch(Base):
    __tablename__ = "payout_batches"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, nullable=False, index=True)
    period = Column(String, nullable=False, index=True)

    currency = Column(String, nullable=False, default="SEK")
    debtor_name = Column(String, nullable=True)
    debtor_iban = Column(String, nullable=True)

    total_amount = Column(Numeric, nullable=False, default=0)
    num_transactions = Column(Integer, nullable=False, default=0)

    status = Column(String, nullable=False, default="created")  # created/exported/sent/confirmed
    pain001_filename = Column(String, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PayoutTransaction(Base):
    __tablename__ = "payout_transactions"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, nullable=False, index=True)
    author_id = Column(Integer, nullable=False, index=True)

    creditor_name = Column(String, nullable=True)
    creditor_email = Column(String, nullable=True)
    creditor_iban = Column(String, nullable=True)

    amount = Column(Numeric, nullable=False)
    end_to_end_id = Column(String, nullable=False, index=True)

    status = Column(String, nullable=False, default="pending")  # pending/blocked/exported/paid
    block_reason = Column(String, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

print(">>> Använder databas:", engine.url)
