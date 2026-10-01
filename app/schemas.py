from pydantic import BaseModel, ConfigDict


class InstrumentCreate(BaseModel):
    symbol: str
    type: str


class InstrumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    type: str