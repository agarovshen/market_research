from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import SessionLocal
from app.models import Instrument
from app.schemas import InstrumentCreate, InstrumentResponse

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/import-csv")
async def import_csv(csv_file: UploadFile = File(...)):
    content = await csv_file.read()
    print(csv_file.filename)
    print(len(content))
    return {"filename": csv_file.filename, "size": len(content), "message": "CSV received successfully"}

@app.post("/instruments", response_model=InstrumentResponse)
def create_instrument(instrument: InstrumentCreate):
    db = SessionLocal()
    try:
        db_instrument = Instrument(symbol=instrument.symbol, type=instrument.type)
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