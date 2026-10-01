from fastapi import FastAPI

from app.database import SessionLocal
from app.models import Instrument
from app.schemas import InstrumentCreate, InstrumentResponse

app = FastAPI()


@app.get("/")
def root():
    return {"message": "Market Research API is running"}


@app.post("/instruments", response_model=InstrumentResponse)
def create_instrument(instrument: InstrumentCreate):
    db = SessionLocal()

    try:
        db_instrument = Instrument(
            symbol=instrument.symbol,
            type=instrument.type,
        )

        db.add(db_instrument)
        db.commit()
        db.refresh(db_instrument)

        return db_instrument
    finally:
        db.close()


@app.get("/instruments", response_model=list[InstrumentResponse])
def get_instruments():
    db = SessionLocal()

    try:
        return db.query(Instrument).all()
    finally:
        db.close()