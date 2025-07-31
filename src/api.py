import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from models import engine, SessionLocal, Base, Author

# Ladda .env tidigt
load_dotenv()

# Se till att tabellerna finns
Base.metadata.create_all(bind=engine)

app = FastAPI()

class EntryPayload(BaseModel):
    # Du kan mappa fältnamn till mer beskrivande attribut
    name: str = Field(..., alias="_0")
    title: str = Field(..., alias="_1")
    email: str = Field(..., alias="_2")
    bank_account: str = Field(..., alias="_3")
    bank_name: str = Field(..., alias="_4")

@app.post("/webhook", status_code=201)
def receive_entry(payload: EntryPayload):
    """
    Tar emot Zapier-webhook och sparar eller uppdaterar Author.
    """
    db = SessionLocal()
    try:
        # Försök hitta existerande författare på e-post
        author = db.query(Author).filter_by(email=payload.email).one_or_none()
        if not author:
            author = Author(
                email=payload.email,
                name=payload.name,
                bank_account=payload.bank_account
            )
            db.add(author)
        else:
            # Uppdatera namn/kontouppgifter om de ändrats
            author.name = payload.name
            author.bank_account = payload.bank_account
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Dublett eller databaskonflikt")
    finally:
        db.close()

    return {"status": "saved", "email": payload.email}
