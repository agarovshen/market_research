from pydantic import BaseModel


class InstrumentCreate(BaseModel):
    symbol: str
    type: str