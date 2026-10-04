from datetime import datetime
from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True)
    type: Mapped[str] = mapped_column(String(20))

class MarketData(Base):
    __tablename__ = "market_data"
    __table_args__ = (
        UniqueConstraint("instrument_id", "timestamp", name="uq_market_data_instrument_timestamp"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    timestamp: Mapped[datetime]
    open: Mapped[float]
    high: Mapped[float]
    low: Mapped[float]
    close: Mapped[float]
    tick_volume: Mapped[int]
    volume: Mapped[int] = mapped_column(BigInteger)
    spread: Mapped[int]
