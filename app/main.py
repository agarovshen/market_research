from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc
from app.database import SessionLocal
from app.importer import get_symbol, import_mt5_csv
from app.models import Instrument, MarketData
from app.schemas import InstrumentCreate, InstrumentResponse
app=FastAPI()
templates=Jinja2Templates(directory="app/templates")
app.mount("/static",StaticFiles(directory="app/static"),name="static")
@app.get("/",response_class=HTMLResponse)
def index(request:Request):
    return templates.TemplateResponse(request=request,name="index.html")
@app.post("/import-csv")
def import_csv(csv_file:UploadFile=File(...)):
    db=SessionLocal()
    try:
        symbol=get_symbol(csv_file.filename)
        if db.query(Instrument).filter(Instrument.symbol==symbol).first():
            return {"filename":csv_file.filename,"imported":False,"message":f"{symbol} already exists in database. Import stopped."}
        return {"filename":csv_file.filename,"imported":True,"message":"CSV imported successfully",**import_mt5_csv(csv_file.file,csv_file.filename,db)}
    finally:
        db.close()
@app.get("/market-data")
def get_market_data(symbol:str,timeframe:str="M1",limit:int=1000):
    timeframe=timeframe.upper()
    valid_timeframes={"M1":1,"M5":5,"M15":15,"H1":60,"H4":240,"D1":1440}
    if timeframe not in valid_timeframes:
        return {"symbol":symbol.upper(),"timeframe":timeframe,"data":[]}
    limit=max(1,min(limit,5000))
    db=SessionLocal()
    try:
        instrument=db.query(Instrument).filter(Instrument.symbol==symbol.upper()).first()
        if instrument is None:
            return {"symbol":symbol.upper(),"timeframe":timeframe,"data":[]}
        rows=(db.query(MarketData).filter(MarketData.instrument_id==instrument.id).order_by(desc(MarketData.timestamp)).limit(limit if timeframe=="M1" else limit*valid_timeframes[timeframe]).all())
        rows.reverse()
        if timeframe!="M1":
            rows=aggregate_market_data(rows,valid_timeframes[timeframe])
            rows=rows[-limit:]
        return {"symbol":instrument.symbol,"timeframe":timeframe,"data":[{"timestamp":row.timestamp.isoformat(),"open":row.open,"high":row.high,"low":row.low,"close":row.close,"tick_volume":row.tick_volume,"volume":row.volume,"spread":row.spread} for row in rows]}
    finally:
        db.close()
def aggregate_market_data(rows,minutes):
    if not rows:
        return []
    interval=minutes*60
    buckets={}
    for row in rows:
        timestamp=int(row.timestamp.timestamp())
        bucket=(timestamp//interval)*interval
        if bucket not in buckets:
            buckets[bucket]={
                "timestamp":row.timestamp,
                "open":row.open,
                "high":row.high,
                "low":row.low,
                "close":row.close,
                "tick_volume":row.tick_volume or 0,
                "volume":row.volume or 0,
                "spread":row.spread
            }
        else:
            candle=buckets[bucket]
            candle["high"]=max(candle["high"],row.high)
            candle["low"]=min(candle["low"],row.low)
            candle["close"]=row.close
            candle["tick_volume"]+=row.tick_volume or 0
            candle["volume"]+=row.volume or 0
            candle["spread"]=row.spread
    return [MarketDataView(**value) for value in buckets.values()]
class MarketDataView:
    def __init__(self,timestamp,open,high,low,close,tick_volume,volume,spread):
        self.timestamp=timestamp
        self.open=open
        self.high=high
        self.low=low
        self.close=close
        self.tick_volume=tick_volume
        self.volume=volume
        self.spread=spread
@app.post("/instruments",response_model=InstrumentResponse)
def create_instrument(instrument:InstrumentCreate):
    db=SessionLocal()
    try:
        db_instrument=db.query(Instrument).filter(Instrument.symbol==instrument.symbol).first()
        if db_instrument is None:
            db_instrument=Instrument(symbol=instrument.symbol,type=instrument.type)
            db.add(db_instrument)
            db.commit()
            db.refresh(db_instrument)
        return db_instrument
    finally:
        db.close()
@app.get("/instruments",response_model=list[InstrumentResponse])
def get_instruments():
    db=SessionLocal()
    try:
        return db.query(Instrument).all()
    finally:
        db.close()