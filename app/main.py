from fastapi import FastAPI
from app.models import Instrument

app = FastAPI()


@app.get("/")
def root():
    return {"message": "Market Research API is running"}


@app.get("/instruments")
def get_instruments():
    return [
        Instrument(symbol="EURUSD", type="forex"),
        Instrument(symbol="BTCUSD", type="crypto"),
    ]