import os
from datetime import datetime, timedelta, timezone

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


def test_trade_focus_range_keeps_trade_candles_and_available_context(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        instrument = Instrument(symbol="EURUSD", type="forex")
        session.add(instrument)
        session.flush()
        for index, minute in enumerate(TIMES):
            open_, high, low, close = OHLC[index]
            session.add(MarketData(
                instrument_id=instrument.id,
                timestamp=START + timedelta(minutes=minute),
                open=open_, high=high, low=low, close=close,
                tick_volume=index + 1, volume=(index + 1) * 10,
                spread=index + 2,
            ))
        session.commit()
        canonical = MarketDataRepository(session).load("EURUSD", timeframe="H4")

    monkeypatch.setattr("app.main.SessionLocal", sessions)
    payload = lambda bars: [{"timestamp": bar.timestamp.isoformat(), "open": bar.open,
                             "high": bar.high, "low": bar.low, "close": bar.close,
                             "tick_volume": bar.tick_volume, "volume": bar.volume,
                             "spread": bar.spread} for bar in bars]

    # A trade near the first available candle has no earlier context, but its
    # entry, exit and following available context are still returned.
    first = get_market_data("EURUSD", timeframe="H4", limit=8,
                            focus_start=canonical[0].timestamp,
                            focus_end=canonical[1].timestamp)["data"]
    assert first == payload(canonical[:3])

    # A same-bucket trade remains navigable; the neighboring bars are present.
    same_bucket = get_market_data("EURUSD", timeframe="H4", limit=8,
                                  focus_start=canonical[1].timestamp,
                                  focus_end=canonical[1].timestamp)["data"]
    assert same_bucket == payload(canonical[:3])

    # A trade ending at the last available bar keeps both trade candles even
    # when the bounded context window contains no additional candle.
    last = get_market_data("EURUSD", timeframe="H4", limit=8,
                           focus_start=canonical[-2].timestamp,
                           focus_end=canonical[-1].timestamp)["data"]
    assert last == payload(canonical[-2:])
    engine.dispose()


def test_legacy_utc_center_timestamp_does_not_break_naive_h1_data(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        instrument = Instrument(symbol="EURUSD", type="forex")
        session.add(instrument)
        session.flush()
        for index, minute in enumerate(TIMES):
            open_, high, low, close = OHLC[index]
            session.add(MarketData(
                instrument_id=instrument.id,
                timestamp=START + timedelta(minutes=minute),
                open=open_, high=high, low=low, close=close,
                tick_volume=index + 1, volume=(index + 1) * 10,
                spread=index + 2,
            ))
        session.commit()

    monkeypatch.setattr("app.main.SessionLocal", sessions)
    # The old browser client sent ISO UTC (`Z`) while the repository stores
    # imported wall-clock timestamps without timezone information.
    center = (START + timedelta(minutes=242)).replace(tzinfo=timezone.utc)
    response = get_market_data("EURUSD", timeframe="H1", limit=5,
                               center_timestamp=center)
    assert response["data"]
    assert all("+00:00" not in candle["timestamp"] for candle in response["data"])
    engine.dispose()
