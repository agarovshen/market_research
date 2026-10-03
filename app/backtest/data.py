from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.models import Bar
from app.models import Instrument, MarketData


class MarketDataRepository:
    """Read existing market_data rows; owns no schema or database connection."""

    def __init__(self, session: Session):
        self.session = session

    TIMEFRAMES = {"M1", "M5", "M15", "H1", "H4", "D1"}

    def load(self, instrument: int | str, start: datetime | None = None,
             end: datetime | None = None, *, timeframe: str = "M1") -> tuple[Bar, ...]:
        timeframe = timeframe.upper()
        if timeframe not in self.TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        if isinstance(instrument, str):
            record = self.session.scalar(select(Instrument).where(Instrument.symbol == instrument.upper()))
            if record is None:
                raise ValueError(f"Unknown instrument: {instrument}")
            instrument_id = record.id
        else:
            instrument_id = instrument
        query = select(MarketData).where(MarketData.instrument_id == instrument_id)
        if start is not None:
            query = query.where(MarketData.timestamp >= start)
        if end is not None:
            query = query.where(MarketData.timestamp <= end)
        rows = self.session.scalars(query.order_by(MarketData.timestamp, MarketData.id)).all()
        bars = tuple(Bar(
            timestamp=row.timestamp, open=row.open, high=row.high, low=row.low,
            close=row.close, tick_volume=row.tick_volume, volume=row.volume,
            spread=row.spread,
        ) for row in rows)
        return bars if timeframe == "M1" else self._aggregate(bars, timeframe)

    @classmethod
    def _aggregate(cls, bars: tuple[Bar, ...], timeframe: str) -> tuple[Bar, ...]:
        """Aggregate chronological source rows into calendar-aligned OHLCV bars."""
        groups: dict[datetime, list[Bar]] = {}
        for bar in bars:
            timestamp = bar.timestamp
            if timeframe in {"M5", "M15"}:
                minutes = 5 if timeframe == "M5" else 15
                bucket = timestamp.replace(minute=timestamp.minute // minutes * minutes,
                                           second=0, microsecond=0)
            elif timeframe == "H1":
                bucket = timestamp.replace(minute=0, second=0, microsecond=0)
            elif timeframe == "H4":
                bucket = timestamp.replace(hour=timestamp.hour // 4 * 4,
                                           minute=0, second=0, microsecond=0)
            else:  # D1
                bucket = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            groups.setdefault(bucket, []).append(bar)

        return tuple(Bar(
            timestamp=timestamp,
            open=group[0].open,
            high=max(bar.high for bar in group),
            low=min(bar.low for bar in group),
            close=group[-1].close,
            tick_volume=sum(bar.tick_volume for bar in group),
            volume=sum(bar.volume for bar in group),
            spread=group[-1].spread,
        ) for timestamp, group in groups.items())
