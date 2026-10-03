import csv
import io
import re
from datetime import datetime
from sqlalchemy.dialects.postgresql import insert
from app.models import Instrument, MarketData

BATCH_SIZE = 5000

def get_symbol(filename: str) -> str:
    return re.split(r"[_\-.]", filename, maxsplit=1)[0].upper()

def import_mt5_csv(file, filename: str, db):
    symbol = get_symbol(filename)
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(symbol=symbol, type="forex")
        db.add(instrument)
        db.flush()

    file.seek(0)
    text_file = io.TextIOWrapper(file, encoding="utf-8-sig", newline="")
    reader = csv.reader(text_file, delimiter="\t")
    headers = [header.strip().upper() for header in next(reader)]
    expected = ["<DATE>", "<TIME>", "<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>", "<TICKVOL>", "<VOL>", "<SPREAD>"]

    if headers != expected:
        raise ValueError(f"Unexpected CSV columns: {headers}")

    rows = []
    imported = 0
    skipped = 0
    first_timestamp = None
    last_timestamp = None

    try:
        for values in reader:
            if not values or len(values) != 9:
                continue

            date_value, time_value, open_value, high_value, low_value, close_value, tick_volume, volume, spread = values
            timestamp = datetime.strptime(f"{date_value} {time_value}", "%Y.%m.%d %H:%M:%S")

            rows.append({
                "instrument_id": instrument.id,
                "timestamp": timestamp,
                "open": float(open_value),
                "high": float(high_value),
                "low": float(low_value),
                "close": float(close_value),
                "tick_volume": int(tick_volume),
                "volume": int(volume),
                "spread": int(spread)
            })

            if first_timestamp is None:
                first_timestamp = timestamp
            last_timestamp = timestamp

            if len(rows) >= BATCH_SIZE:
                statement = insert(MarketData).values(rows)
                statement = statement.on_conflict_do_nothing(
                    constraint="uq_market_data_instrument_timestamp"
                )
                result = db.execute(statement)
                imported += result.rowcount
                skipped += len(rows) - result.rowcount
                rows.clear()

        if rows:
            statement = insert(MarketData).values(rows)
            statement = statement.on_conflict_do_nothing(
                constraint="uq_market_data_instrument_timestamp"
            )
            result = db.execute(statement)
            imported += result.rowcount
            skipped += len(rows) - result.rowcount

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        text_file.detach()

    return {
        "symbol": symbol,
        "rows_imported": imported,
        "rows_skipped": skipped,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp
    }