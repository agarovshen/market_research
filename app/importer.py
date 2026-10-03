"""Additive, conflict-safe import of tab-delimited OHLCV CSV files."""

import csv
import io
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import Instrument, MarketData

# Nine bound columns per candle, under SQLAlchemy's PostgreSQL safe limit.
BATCH_SIZE = 3000
CSV_HEADERS = ("<DATE>", "<TIME>", "<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>",
               "<TICKVOL>", "<VOL>", "<SPREAD>")


def get_symbol(filename: str) -> str:
    return re.split(r"[_\-.]", filename, maxsplit=1)[0].upper()


def _insert(db, model):
    """Select the dialect's native conflict-safe insert used by the app/tests."""
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        return sqlite_insert(model)
    if dialect == "postgresql":
        return postgresql_insert(model)
    raise ValueError(f"CSV import is unsupported for database dialect {dialect!r}")


def _get_or_create_instrument(db, symbol: str) -> Instrument:
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if instrument is not None:
        return instrument

    identity = db.scalar(
        _insert(db, Instrument)
        .values(symbol=symbol, type="forex")
        .on_conflict_do_nothing(index_elements=[Instrument.symbol])
        .returning(Instrument.id)
    )
    if identity is not None:
        return db.get(Instrument, identity)

    # A concurrent request created this symbol after our first SELECT.
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if instrument is None:
        raise RuntimeError(f"Instrument {symbol} disappeared during import")
    return instrument


def import_mt5_csv(file, filename: str, db):
    """Merge valid CSV candles into market_data without replacing stored rows.

    The file's first/last candle timestamps define the requested source range.
    ``imported_intervals`` describes runs of source candles that were actually
    inserted; it does not infer unlisted market sessions or calendar gaps.
    """
    symbol = get_symbol(filename)
    text_file = None
    try:
        file.seek(0)
        text_file = io.TextIOWrapper(file, encoding="utf-8-sig", newline="")
        reader = csv.reader(text_file, delimiter="\t")
        try:
            headers = tuple(header.strip().upper() for header in next(reader))
        except StopIteration as error:
            raise ValueError("CSV file is empty") from error
        if headers != CSV_HEADERS:
            raise ValueError(f"Unexpected CSV columns: {list(headers)}")

        instrument = _get_or_create_instrument(db, symbol)
        existing_before = db.execute(
            select(func.min(MarketData.timestamp), func.max(MarketData.timestamp))
            .where(MarketData.instrument_id == instrument.id)
        ).one()
        existing_range_before = _range(existing_before[0], existing_before[1])

        batch = []
        batch_timestamps = set()
        inserted = 0
        source_rows = 0
        first_timestamp = None
        last_timestamp = None
        imported_intervals = []
        active_import_interval = None

        def close_import_interval():
            nonlocal active_import_interval
            if active_import_interval is not None:
                start, end, count = active_import_interval
                imported_intervals.append(_range(start, end, count))
                active_import_interval = None

        def flush_batch():
            nonlocal inserted, active_import_interval
            if not batch:
                return
            timestamps = [row["timestamp"] for row in batch]
            present = set(db.scalars(
                select(MarketData.timestamp).where(
                    MarketData.instrument_id == instrument.id,
                    MarketData.timestamp.in_(timestamps),
                )
            ))
            inserted_in_batch = set()
            candidates = [row for row in batch if row["timestamp"] not in present]
            if candidates:
                statement = _insert(db, MarketData).values(candidates)
                if db.get_bind().dialect.name == "postgresql":
                    statement = statement.on_conflict_do_nothing(
                        constraint="uq_market_data_instrument_timestamp")
                else:
                    statement = statement.on_conflict_do_nothing(
                        index_elements=["instrument_id", "timestamp"])
                inserted_in_batch = set(db.scalars(statement.returning(MarketData.timestamp)))
                inserted += len(inserted_in_batch)

            for row in batch:
                timestamp = row["timestamp"]
                if timestamp in inserted_in_batch:
                    if active_import_interval is None:
                        active_import_interval = (timestamp, timestamp, 1)
                    else:
                        start, end, count = active_import_interval
                        active_import_interval = (min(start, timestamp), max(end, timestamp), count + 1)
                else:
                    close_import_interval()
            batch.clear()
            batch_timestamps.clear()

        for line_number, values in enumerate(reader, start=2):
            if not values or all(not value.strip() for value in values):
                continue
            if len(values) != len(CSV_HEADERS):
                raise ValueError(f"Malformed CSV row at line {line_number}: expected 9 columns")
            date_value, time_value, open_value, high_value, low_value, close_value, tick_volume, volume, spread = values
            try:
                timestamp = datetime.strptime(f"{date_value} {time_value}", "%Y.%m.%d %H:%M:%S")
                row = {
                    "instrument_id": instrument.id,
                    "timestamp": timestamp,
                    "open": float(open_value),
                    "high": float(high_value),
                    "low": float(low_value),
                    "close": float(close_value),
                    "tick_volume": int(tick_volume),
                    "volume": int(volume),
                    "spread": int(spread),
                }
            except (TypeError, ValueError) as error:
                raise ValueError(f"Malformed market data at CSV line {line_number}: {error}") from error

            source_rows += 1
            first_timestamp = timestamp if first_timestamp is None else min(first_timestamp, timestamp)
            last_timestamp = timestamp if last_timestamp is None else max(last_timestamp, timestamp)
            if timestamp in batch_timestamps:
                continue
            batch.append(row)
            batch_timestamps.add(timestamp)
            if len(batch) >= BATCH_SIZE:
                flush_batch()

        flush_batch()
        close_import_interval()
        if source_rows == 0:
            raise ValueError("CSV contains no market-data rows")

        db.commit()
        final_range_row = db.execute(
            select(func.min(MarketData.timestamp), func.max(MarketData.timestamp))
            .where(MarketData.instrument_id == instrument.id)
        ).one()
        rows_skipped = source_rows - inserted
        return {
            "symbol": symbol,
            "requested_range": _range(first_timestamp, last_timestamp),
            "existing_range_before": existing_range_before,
            "imported_intervals": imported_intervals,
            "source_rows": source_rows,
            "rows_inserted": inserted,
            "rows_skipped_existing": rows_skipped,
            "already_complete": inserted == 0,
            "final_range": _range(final_range_row[0], final_range_row[1]),
        }
    except csv.Error as error:
        db.rollback()
        raise ValueError(f"Malformed CSV: {error}") from error
    except Exception:
        db.rollback()
        raise
    finally:
        if text_file is not None:
            text_file.detach()


def _range(start, end, candles=None):
    if start is None or end is None:
        return None
    result = {"start": start, "end": end}
    if candles is not None:
        result["candles"] = candles
    return result
