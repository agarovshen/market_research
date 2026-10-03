from datetime import datetime,timedelta
import logging
from fastapi import FastAPI,File,HTTPException,Request,UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc,text
from sqlalchemy.exc import SQLAlchemyError
from app.database import SessionLocal
from app.importer import get_symbol,import_mt5_csv
from app.models import Instrument,MarketData
from app.schemas import InstrumentCreate,InstrumentResponse
from app.research.api import router as research_router
app=FastAPI()
logger=logging.getLogger(__name__)
app.include_router(research_router)
templates=Jinja2Templates(directory="app/templates")
app.mount("/static",StaticFiles(directory="app/static"),name="static")
@app.get("/",response_class=HTMLResponse)
def index(request:Request):
    return templates.TemplateResponse(request=request,name="index.html")
@app.get("/research",response_class=HTMLResponse)
def research_workspace(request:Request):
    return templates.TemplateResponse(request=request,name="research.html")
@app.post("/import-csv")
def import_csv(csv_file:UploadFile=File(...)):
    db=SessionLocal()
    try:
        symbol=get_symbol(csv_file.filename)
        result=import_mt5_csv(csv_file.file,csv_file.filename,db)
        if result["already_complete"]:
            message="No new data — all candles in the requested file range are already stored."
        else:
            message=f"Imported {result['rows_inserted']:,} candles; skipped {result['rows_skipped_existing']:,} already stored or duplicate candles."
        return {"filename":csv_file.filename,"imported":True,"message":message,**result}
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422,detail=str(error)) from error
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("CSV market-data import failed")
        raise HTTPException(status_code=500,detail="CSV import failed while writing market data.") from error
    finally:
        db.close()
@app.get("/market-data")
def get_market_data(symbol:str,timeframe:str="M1",limit:int=1000,center_timestamp:datetime|None=None):
    timeframe=timeframe.upper()
    valid_timeframes={"M1","M5","M15","H1","H4","D1"}
    if timeframe not in valid_timeframes:
        return {"symbol":symbol.upper(),"timeframe":timeframe,"data":[]}
    limit=max(1,min(limit,5000))
    db=SessionLocal()
    try:
        instrument=db.query(Instrument).filter(Instrument.symbol==symbol.upper()).first()
        if instrument is None:
            return {"symbol":symbol.upper(),"timeframe":timeframe,"data":[]}
        half=limit//2
        if timeframe=="M1":
            if center_timestamp:
                before=text("""
                    SELECT timestamp,open,high,low,close,tick_volume,volume,spread
                    FROM market_data
                    WHERE instrument_id=:instrument_id AND timestamp<=:center_timestamp
                    ORDER BY timestamp DESC
                    LIMIT :limit
                """)
                after=text("""
                    SELECT timestamp,open,high,low,close,tick_volume,volume,spread
                    FROM market_data
                    WHERE instrument_id=:instrument_id AND timestamp>:center_timestamp
                    ORDER BY timestamp ASC
                    LIMIT :limit
                """)
                before_rows=list(db.execute(before,{"instrument_id":instrument.id,"center_timestamp":center_timestamp,"limit":half}).mappings().all())
                after_rows=list(db.execute(after,{"instrument_id":instrument.id,"center_timestamp":center_timestamp,"limit":limit-half}).mappings().all())
                rows=list(reversed(before_rows))+after_rows
            else:
                query=text("""
                    SELECT timestamp,open,high,low,close,tick_volume,volume,spread
                    FROM market_data
                    WHERE instrument_id=:instrument_id
                    ORDER BY timestamp DESC
                    LIMIT :limit
                """)
                rows=list(reversed(db.execute(query,{"instrument_id":instrument.id,"limit":limit}).mappings().all()))
            data=[{"timestamp":row["timestamp"].isoformat(),"open":row["open"],"high":row["high"],"low":row["low"],"close":row["close"],"tick_volume":row["tick_volume"],"volume":row["volume"],"spread":row["spread"]} for row in rows]
        else:
            timeframe_minutes={"M5":5,"M15":15,"H1":60,"H4":240,"D1":1440}
            minutes=timeframe_minutes[timeframe]
            bucket_expressions={
                "M5":"date_trunc('hour',timestamp)+floor(extract(minute from timestamp)/5)*interval '5 minutes'",
                "M15":"date_trunc('hour',timestamp)+floor(extract(minute from timestamp)/15)*interval '15 minutes'",
                "H1":"date_trunc('hour',timestamp)",
                "H4":"date_trunc('day',timestamp)+floor(extract(hour from timestamp)/4)*interval '4 hours'",
                "D1":"date_trunc('day',timestamp)"
            }
            bucket=bucket_expressions[timeframe]
            if center_timestamp:
                start_timestamp=center_timestamp-timedelta(minutes=minutes*half*2)
                end_timestamp=center_timestamp+timedelta(minutes=minutes*half*2)
                query=text(f"""
                    WITH candles AS (
                        SELECT
                            {bucket} AS bucket,
                            (array_agg(open ORDER BY timestamp))[1] AS open,
                            MAX(high) AS high,
                            MIN(low) AS low,
                            (array_agg(close ORDER BY timestamp DESC))[1] AS close,
                            SUM(tick_volume) AS tick_volume,
                            SUM(volume) AS volume,
                            (array_agg(spread ORDER BY timestamp DESC))[1] AS spread
                        FROM market_data
                        WHERE instrument_id=:instrument_id
                        AND timestamp>=:start_timestamp
                        AND timestamp<=:end_timestamp
                        GROUP BY bucket
                    ),
                    target AS (
                        SELECT bucket
                        FROM candles
                        ORDER BY abs(extract(epoch FROM (bucket-:center_timestamp)))
                        LIMIT 1
                    ),
                    before_candles AS (
                        SELECT *
                        FROM candles
                        WHERE bucket<=(SELECT bucket FROM target)
                        ORDER BY bucket DESC
                        LIMIT :half
                    ),
                    after_candles AS (
                        SELECT *
                        FROM candles
                        WHERE bucket>(SELECT bucket FROM target)
                        ORDER BY bucket ASC
                        LIMIT :after_limit
                    )
                    SELECT * FROM before_candles
                    UNION ALL
                    SELECT * FROM after_candles
                    ORDER BY bucket ASC
                """)
                rows=db.execute(query,{"instrument_id":instrument.id,"start_timestamp":start_timestamp,"end_timestamp":end_timestamp,"center_timestamp":center_timestamp,"half":half,"after_limit":limit-half}).mappings().all()
            else:
                query=text(f"""
                    WITH candles AS (
                        SELECT
                            {bucket} AS bucket,
                            (array_agg(open ORDER BY timestamp))[1] AS open,
                            MAX(high) AS high,
                            MIN(low) AS low,
                            (array_agg(close ORDER BY timestamp DESC))[1] AS close,
                            SUM(tick_volume) AS tick_volume,
                            SUM(volume) AS volume,
                            (array_agg(spread ORDER BY timestamp DESC))[1] AS spread
                        FROM market_data
                        WHERE instrument_id=:instrument_id
                        GROUP BY bucket
                    )
                    SELECT bucket,open,high,low,close,tick_volume,volume,spread
                    FROM candles
                    ORDER BY bucket DESC
                    LIMIT :limit
                """)
                rows=list(reversed(db.execute(query,{"instrument_id":instrument.id,"limit":limit}).mappings().all()))
            data=[{"timestamp":row["bucket"].isoformat(),"open":row["open"],"high":row["high"],"low":row["low"],"close":row["close"],"tick_volume":row["tick_volume"],"volume":row["volume"],"spread":row["spread"]} for row in rows]
        return {"symbol":instrument.symbol,"timeframe":timeframe,"data":data}
    finally:
        db.close()
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
