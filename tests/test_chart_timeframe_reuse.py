import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backtest import BacktestRunConfig, BacktestRunner
from app.backtest.data import MarketDataRepository
from app.database import Base
from app.main import get_market_data
from app.models import Instrument, MarketData


START = datetime(2024, 1, 2, 3, 58)
OHLC = (
    (100, 102, 99, 101),
    (101, 105, 100, 104),
    (104, 106, 103, 105),
    (105, 108, 104, 107),
    (107, 110, 106, 109),
    (109, 112, 108, 111),
    (111, 113, 109, 110),
)
TIMES = (0, 1, 2, 241, 242, 1201, 1202)


class NoSignal:
    def on_bar(self, context):
        return None


def _expected_payload(bars):
    return [{"timestamp": item.timestamp.isoformat(), "open": item.open,
             "high": item.high, "low": item.low, "close": item.close,
             "tick_volume": item.tick_volume, "volume": item.volume,
             "spread": item.spread} for item in bars]


def test_chart_and_backtest_use_the_same_h4_and_d1_repository_bars(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        instrument = Instrument(symbol="EURUSD", type="forex")
        session.add(instrument)
        session.flush()
        source_rows = []
        for index, minute in enumerate(TIMES):
            open_, high, low, close = OHLC[index]
            source_rows.append(MarketData(
                instrument_id=instrument.id,
                timestamp=START + timedelta(minutes=minute),
                open=open_, high=high, low=low, close=close,
                tick_volume=index + 1, volume=(index + 1) * 10,
                spread=index + 2,
            ))
        session.add_all(source_rows)
        session.commit()
        instrument_id = instrument.id

    monkeypatch.setattr("app.main.SessionLocal", sessions)
    with sessions() as session:
        repository = MarketDataRepository(session)
        for timeframe in ("H4", "D1"):
            canonical = repository.load(instrument_id, timeframe=timeframe)
            chart = get_market_data("EURUSD", timeframe=timeframe, limit=100)
            assert chart["data"] == _expected_payload(canonical)
            limited = get_market_data("EURUSD", timeframe=timeframe, limit=1)
            assert limited["data"] == _expected_payload(canonical[-1:])
        centered = get_market_data(
            "EURUSD", timeframe="H4", limit=3,
            center_timestamp=START + timedelta(minutes=2),
        )
        assert [item["timestamp"] for item in centered["data"]] == [
            (START.replace(hour=4, minute=0)).isoformat(),
            (START.replace(hour=8, minute=0)).isoformat(),
        ]

    # BacktestRunner already delegates timeframe loading to this same public
    # repository path, so its consumed bar timestamps must match the H4 chart.
    h4_chart = get_market_data("EURUSD", timeframe="H4", limit=100)
    with sessions() as session:
        result = BacktestRunner(MarketDataRepository(session)).run(
            strategy=NoSignal(),
            config=BacktestRunConfig("EURUSD", timeframe="H4"),
        )
    assert [point.timestamp.isoformat() for point in result.equity_curve] == [
        item["timestamp"] for item in h4_chart["data"]
    ]
    assert result.trades == ()
    engine.dispose()
