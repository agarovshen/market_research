"""PostgreSQL-only coverage for the chart's recent timeframe bucket bound.

The fixture uses temporary tables on one connection and always rolls back;
it does not create or modify persistent project tables or market data.
"""

import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.backtest.data import MarketDataRepository
from app.main import get_market_data


SOURCE = (
    (datetime(2024, 1, 1, 3, 59), 10, 11, 9, 10),
    (datetime(2024, 1, 1, 4, 0), 20, 21, 19, 20),
    (datetime(2024, 1, 1, 7, 59), 21, 25, 18, 24),
    (datetime(2024, 1, 1, 8, 0), 30, 32, 29, 31),
    (datetime(2024, 1, 1, 11, 59), 31, 35, 28, 29),
    (datetime(2024, 1, 1, 12, 0), 40, 42, 39, 41),
    (datetime(2024, 1, 1, 15, 59), 41, 45, 38, 44),
    (datetime(2024, 1, 1, 16, 0), 50, 52, 49, 51),
    (datetime(2024, 1, 1, 19, 59), 51, 55, 48, 54),
    (datetime(2024, 1, 1, 20, 0), 60, 62, 59, 61),
    (datetime(2024, 1, 1, 23, 59), 61, 65, 58, 64),
    (datetime(2024, 1, 2, 0, 0), 70, 72, 69, 71),
    (datetime(2024, 1, 2, 3, 59), 71, 75, 68, 74),
    (datetime(2024, 1, 2, 4, 0), 80, 82, 79, 81),
    (datetime(2024, 1, 2, 7, 59), 81, 85, 78, 84),
    (datetime(2024, 1, 2, 8, 0), 90, 92, 89, 91),
    (datetime(2024, 1, 2, 11, 59), 91, 95, 88, 94),
    (datetime(2024, 1, 2, 12, 0), 100, 102, 99, 101),
    (datetime(2024, 1, 2, 15, 59), 101, 105, 98, 104),
    (datetime(2024, 1, 2, 16, 0), 110, 112, 109, 111),
    (datetime(2024, 1, 2, 19, 59), 111, 115, 108, 114),
    (datetime(2024, 1, 2, 20, 0), 120, 122, 119, 121),
    (datetime(2024, 1, 2, 23, 59), 121, 125, 118, 124),
    (datetime(2024, 1, 3, 0, 0), 130, 132, 129, 131),
    (datetime(2024, 1, 3, 3, 59), 131, 135, 128, 134),
    (datetime(2024, 1, 3, 4, 0), 140, 142, 139, 141),
    (datetime(2024, 1, 3, 7, 59), 141, 145, 138, 144),
)


@pytest.fixture
def postgres_market_data(monkeypatch):
    database_url = os.environ.get("DATABASE_URL")
    if not database_url or make_url(database_url).get_backend_name() != "postgresql":
        pytest.skip("PostgreSQL DATABASE_URL is not configured")

    engine = create_engine(database_url)
    try:
        connection = engine.connect()
    except OperationalError:
        engine.dispose()
        pytest.skip("Configured PostgreSQL instance is unavailable")

    transaction = connection.begin()
    try:
        connection.execute(text("""
            CREATE TEMP TABLE instruments (
                id integer PRIMARY KEY,
                symbol varchar(20) UNIQUE NOT NULL,
                type varchar(20) NOT NULL
            ) ON COMMIT DROP
        """))
        connection.execute(text("""
            CREATE TEMP TABLE market_data (
                id integer PRIMARY KEY,
                instrument_id integer NOT NULL,
                timestamp timestamp without time zone NOT NULL,
                open double precision NOT NULL,
                high double precision NOT NULL,
                low double precision NOT NULL,
                close double precision NOT NULL,
                tick_volume integer NOT NULL,
                volume bigint NOT NULL,
                spread integer NOT NULL,
                UNIQUE (instrument_id, timestamp)
            ) ON COMMIT DROP
        """))
        connection.execute(text("INSERT INTO instruments VALUES (1, 'AUDIT', 'forex')"))
        connection.execute(text("""
            INSERT INTO market_data
                (id, instrument_id, timestamp, open, high, low, close,
                 tick_volume, volume, spread)
            VALUES (:id, 1, :timestamp, :open, :high, :low, :close,
                    :tick_volume, :volume, :spread)
        """), [
            {"id": index, "timestamp": timestamp, "open": open_, "high": high,
             "low": low, "close": close, "tick_volume": index,
             "volume": index * 10, "spread": index}
            for index, (timestamp, open_, high, low, close) in enumerate(SOURCE, 1)
        ])
        sessions = sessionmaker(bind=connection, autoflush=False, autocommit=False)
        monkeypatch.setattr("app.main.SessionLocal", sessions)
        yield sessions
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        engine.dispose()


def _payload(bars):
    return [{"timestamp": bar.timestamp.isoformat(), "open": bar.open,
             "high": bar.high, "low": bar.low, "close": bar.close,
             "tick_volume": bar.tick_volume, "volume": bar.volume,
             "spread": bar.spread} for bar in bars]


def test_postgres_recent_h4_and_d1_bars_match_full_canonical_aggregation(postgres_market_data):
    with postgres_market_data() as session:
        repository = MarketDataRepository(session)
        all_h4 = repository.load("AUDIT", timeframe="H4")
        all_d1 = repository.load("AUDIT", timeframe="D1")

    chart_h4 = get_market_data("AUDIT", timeframe="H4", limit=4)["data"]
    chart_d1 = get_market_data("AUDIT", timeframe="D1", limit=2)["data"]
    assert chart_h4 == _payload(all_h4[-4:])
    assert chart_d1 == _payload(all_d1[-2:])

    # The first returned H4 bucket contains source rows at 16:00 and 19:59.
    # The limit query must bound at the bucket start so its open/high/low/close
    # and volume include both rows, not only the last M1 row in that bucket.
    assert chart_h4[0] == {
        "timestamp": "2024-01-02T16:00:00", "open": 110.0, "high": 115.0,
        "low": 108.0, "close": 114.0, "tick_volume": 41,
        "volume": 410, "spread": 21,
    }
    assert [item["timestamp"] for item in chart_h4] == [
        "2024-01-02T16:00:00", "2024-01-02T20:00:00",
        "2024-01-03T00:00:00", "2024-01-03T04:00:00",
    ]
    assert chart_d1 == [
        {"timestamp": "2024-01-02T00:00:00", "open": 70.0, "high": 125.0,
         "low": 68.0, "close": 124.0, "tick_volume": 210,
         "volume": 2100, "spread": 23},
        {"timestamp": "2024-01-03T00:00:00", "open": 130.0, "high": 145.0,
         "low": 128.0, "close": 144.0, "tick_volume": 102,
         "volume": 1020, "spread": 27},
    ]


def test_postgres_centered_h4_request_uses_canonical_repository_bars(postgres_market_data):
    center = datetime(2024, 1, 2, 20)
    source_window = timedelta(minutes=240 * 2)
    start = center - source_window
    end = center + source_window
    with postgres_market_data() as session:
        canonical = MarketDataRepository(session).load(
            "AUDIT", start=start, end=end, timeframe="H4")

    chart = get_market_data("AUDIT", timeframe="H4", limit=3,
                            center_timestamp=center)["data"]
    assert chart == _payload(canonical[2:5])
    assert [item["timestamp"] for item in chart] == [
        "2024-01-02T20:00:00", "2024-01-03T00:00:00", "2024-01-03T04:00:00",
    ]
