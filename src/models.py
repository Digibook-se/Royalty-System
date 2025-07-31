import os
from dotenv import load_dotenv
from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, create_engine
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Ladda .env
load_dotenv()

# Hämta DB-URL
DATABASE_URL = os.getenv("DATABASE_URL")

# Bas-klass för modeller
Base = declarative_base()

# Exempelmodell för Author (du kan utöka med fler kolumner)
class Author(Base):
    __tablename__ = "authors"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=True)
    bank_account = Column(String, nullable=True)

# Modell för Royalty-rader
class Royalty(Base):
    __tablename__ = "royalties"
    id = Column(Integer, primary_key=True, index=True)
    author_id = Column(Integer, nullable=False, index=True)
    isbn = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    net_amount = Column(Numeric, nullable=False)
    author_share = Column(Numeric, nullable=False)
    publisher_share = Column(Numeric, nullable=False)
    created_at = Column(DateTime, nullable=False)

# Skapa engine och session-factory
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine
)
