from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import SessionLocal
from app.importer import import_mt5_csv, get_symbol
from app.models import Instrument
from app.schemas import InstrumentCreate, InstrumentResponse

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/import-csv")
def import_csv(csv_file: UploadFile = File(...)):
    db = SessionLocal()
    try:
        symbol = get_symbol(csv_file.filename)
        instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
        if instrument is not None:
            return {
                "filename": csv_file.filename,
                "imported": False,
                "message": f"{symbol} already exists in database. Import stopped."
            }
        result = import_mt5_csv(csv_file.file, csv_file.filename, db)
        return {
            "filename": csv_file.filename,
            "imported": True,
            "message": "CSV imported successfully",
            **result
        }
    finally:
        db.close()

@app.post("/instruments", response_model=InstrumentResponse)
def create_instrument(instrument: InstrumentCreate):
    db = SessionLocal()
    try:
        db_instrument = db.query(Instrument).filter(
            Instrument.symbol == instrument.symbol
        ).first()
        if db_instrument is None:
            db_instrument = Instrument(
                symbol=instrument.symbol,
                type=instrument.type
            )
            db.add(db_instrument)
            db.commit()
            db.refresh(db_instrument)
        return db_instrument
    finally:
        db.close()