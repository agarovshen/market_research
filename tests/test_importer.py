import io
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import UploadFile

from app.importer import BATCH_SIZE, import_mt5_csv
from app.models import Instrument, MarketData


HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
START = datetime(2024, 1, 1)


def candle(timestamp, symbol_id=1):
    return MarketData(instrument_id=symbol_id, timestamp=timestamp, open=1.1, high=1.2,
                      low=1.0, close=1.15, tick_volume=10, volume=20, spread=2)


def csv_bytes(timestamps):
    lines = [HEADER]
    for timestamp in timestamps:
        lines.append(
            f"{timestamp:%Y.%m.%d}\t{timestamp:%H:%M:%S}\t1.1\t1.2\t1.0\t1.15\t10\t20\t2\n"
        )
    return io.BytesIO("".join(lines).encode())


class CSVImporterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Instrument.__table__.create(self.engine)
        MarketData.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.instrument("EURUSD")

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def instrument(self, symbol):
        found = self.session.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if found is not None:
            return found
        item = Instrument(symbol=symbol, type="forex")
        self.session.add(item)
        self.session.commit()
        return item

    def existing_minutes(self):
        return set(self.session.scalars(select(MarketData.timestamp)
                                        .where(MarketData.instrument_id == self.instrument("EURUSD").id)))

    def test_empty_database_imports_all_source_candles(self):
        timestamps = [START + timedelta(minutes=i) for i in range(4)]

        result = import_mt5_csv(csv_bytes(timestamps), "EURUSD_M1.csv", self.session)

        self.assertEqual(result["requested_range"], {"start": timestamps[0], "end": timestamps[-1]})
        self.assertIsNone(result["existing_range_before"])
        self.assertEqual(result["rows_inserted"], 4)
        self.assertEqual(result["rows_skipped_existing"], 0)
        self.assertFalse(result["already_complete"])
        self.assertEqual(self.existing_minutes(), set(timestamps))

    def test_repeated_identical_import_is_successful_noop(self):
        timestamps = [START + timedelta(minutes=i) for i in range(5)]
        import_mt5_csv(csv_bytes(timestamps), "EURUSD.csv", self.session)

        result = import_mt5_csv(csv_bytes(timestamps), "EURUSD.csv", self.session)

        self.assertEqual(result["rows_inserted"], 0)
        self.assertEqual(result["rows_skipped_existing"], 5)
        self.assertTrue(result["already_complete"])
        self.assertEqual(len(self.existing_minutes()), 5)
        self.assertEqual(result["final_range"], {"start": timestamps[0], "end": timestamps[-1]})

    def test_existing_superset_is_not_rewritten(self):
        timestamps = [START + timedelta(minutes=i) for i in range(12)]
        self.session.add_all(candle(timestamp) for timestamp in timestamps)
        self.session.commit()

        result = import_mt5_csv(csv_bytes(timestamps[3:8]), "EURUSD.csv", self.session)

        self.assertEqual(result["rows_inserted"], 0)
        self.assertEqual(result["existing_range_before"], {"start": timestamps[0], "end": timestamps[-1]})
        self.assertEqual(len(self.existing_minutes()), 12)

    def test_left_right_and_two_sided_extensions_insert_only_missing_candles(self):
        scenarios = (
            (set(range(3, 10)), range(0, 7), {0, 1, 2}),
            (set(range(0, 5)), range(2, 10), {5, 6, 7, 8, 9}),
            (set(range(2, 7)), range(0, 9), {0, 1, 7, 8}),
        )
        for stored_indexes, requested_indexes, expected_new in scenarios:
            with self.subTest(stored=stored_indexes, requested=list(requested_indexes)):
                self.session.query(MarketData).delete()
                self.session.commit()
                timestamps = [START + timedelta(minutes=i) for i in range(10)]
                self.session.add_all(candle(timestamps[i]) for i in stored_indexes)
                self.session.commit()

                result = import_mt5_csv(csv_bytes([timestamps[i] for i in requested_indexes]),
                                        "EURUSD.csv", self.session)

                self.assertEqual(result["rows_inserted"], len(expected_new))
                self.assertEqual(self.existing_minutes(), {timestamps[i] for i in stored_indexes | expected_new})

    def test_internal_and_multiple_gaps_import_only_missing_source_timestamps(self):
        timestamps = [START + timedelta(minutes=i) for i in range(12)]
        stored = {0, 1, 5, 8, 9, 11}
        missing = {2, 3, 4, 6, 7, 10}
        self.session.add_all(candle(timestamps[i]) for i in stored)
        self.session.commit()

        result = import_mt5_csv(csv_bytes(timestamps), "EURUSD.csv", self.session)

        self.assertEqual(result["rows_inserted"], len(missing))
        self.assertEqual(self.existing_minutes(), set(timestamps))
        self.assertEqual(result["imported_intervals"], [
            {"start": timestamps[2], "end": timestamps[4], "candles": 3},
            {"start": timestamps[6], "end": timestamps[7], "candles": 2},
            {"start": timestamps[10], "end": timestamps[10], "candles": 1},
        ])

    def test_exact_timestamp_boundary_is_inserted_once(self):
        boundary = START + timedelta(minutes=1)
        self.session.add(candle(boundary))
        self.session.commit()

        result = import_mt5_csv(csv_bytes([START, boundary, boundary + timedelta(minutes=1)]),
                                "EURUSD.csv", self.session)

        self.assertEqual(result["rows_inserted"], 2)
        self.assertEqual(len(self.existing_minutes()), 3)
        self.assertEqual(result["rows_skipped_existing"], 1)

    def test_duplicate_timestamps_inside_csv_are_counted_once(self):
        timestamps = [START, START + timedelta(minutes=1), START]

        result = import_mt5_csv(csv_bytes(timestamps), "EURUSD.csv", self.session)

        self.assertEqual(result["rows_inserted"], 2)
        self.assertEqual(result["rows_skipped_existing"], 0)
        self.assertEqual(result["rows_skipped_duplicate_csv"], 1)
        self.assertEqual(result["invalid_rows"], 0)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(MarketData)), 2)

    def test_large_market_volume_and_integer_boundary_are_preserved(self):
        timestamp = START
        payload = (HEADER +
                   f"{timestamp:%Y.%m.%d}\t{timestamp:%H:%M:%S}\t1.1\t1.2\t1.0\t1.15\t2147483647\t57500000\t2147483647\n")

        result = import_mt5_csv(io.BytesIO(payload.encode()), "EURUSD.csv", self.session)

        row = self.session.scalar(select(MarketData))
        self.assertEqual(result["rows_inserted"], 1)
        self.assertEqual((row.tick_volume, row.volume, row.spread), (2147483647, 57500000, 2147483647))

    def test_numeric_overflow_fraction_negative_and_nan_roll_back(self):
        samples = (
            ("2147483648", "10", "2"),
            ("10", "2147483648", "2"),
            ("10", "20", "2147483648"),
            ("1.5", "10", "2"),
            ("-1", "10", "2"),
            ("1", "10", "NaN"),
        )
        for tick_volume, volume, spread in samples:
            payload = (HEADER +
                       f"{START:%Y.%m.%d}\t{START:%H:%M:%S}\t1.1\t1.2\t1.0\t1.15\t{tick_volume}\t{volume}\t{spread}\n")
            with self.subTest(values=(tick_volume, volume, spread)):
                with self.assertRaises(ValueError):
                    import_mt5_csv(io.BytesIO(payload.encode()), "GBPUSD.csv", self.session)
                self.assertIsNone(self.session.scalar(select(Instrument).where(Instrument.symbol == "GBPUSD")))
                self.assertEqual(self.session.scalar(select(func.count()).select_from(MarketData)), 0)

    def test_invalid_timestamp_and_null_prohibited_fields_fail_atomically(self):
        malformed_rows = (
            "not-a-date\t00:00:00\t1.1\t1.2\t1.0\t1.15\t1\t2\t0",
            f"{START:%Y.%m.%d}\t{START:%H:%M:%S}\t\t1.2\t1.0\t1.15\t1\t2\t0",
        )
        for row in malformed_rows:
            with self.subTest(row=row):
                payload = (HEADER + row + "\n").encode()
                with self.assertRaises(ValueError):
                    import_mt5_csv(io.BytesIO(payload), "GBPUSD.csv", self.session)
                self.assertIsNone(self.session.scalar(select(Instrument).where(Instrument.symbol == "GBPUSD")))

    def test_duplicate_csv_across_batch_boundary_is_idempotent_and_counted(self):
        timestamps = [START + timedelta(minutes=i) for i in range(BATCH_SIZE)]
        timestamps.append(timestamps[0])

        result = import_mt5_csv(csv_bytes(timestamps), "EURUSD.csv", self.session)

        self.assertEqual(result["rows_inserted"], BATCH_SIZE)
        self.assertEqual(result["rows_skipped_duplicate_csv"], 1)
        self.assertEqual(result["rows_skipped_existing"], 0)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(MarketData)), BATCH_SIZE)

    def test_different_symbols_have_independent_timestamp_identity(self):
        timestamp = START
        self.session.add(candle(timestamp))
        self.session.commit()

        result = import_mt5_csv(csv_bytes([timestamp]), "GBPUSD.csv", self.session)

        self.assertEqual(result["rows_inserted"], 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(MarketData)), 2)
        self.assertIsNotNone(self.session.scalar(select(Instrument).where(Instrument.symbol == "GBPUSD")))

    def test_import_api_reports_new_data_and_repeat_noop_truthfully(self):
        from app.main import import_csv

        timestamps = [START + timedelta(minutes=i) for i in range(2)]
        make_session = sessionmaker(bind=self.engine)
        with patch("app.main.SessionLocal", side_effect=make_session):
            first = import_csv(UploadFile(filename="EURUSD.csv", file=csv_bytes(timestamps)))
            second = import_csv(UploadFile(filename="EURUSD.csv", file=csv_bytes(timestamps)))

        self.assertEqual(first["rows_inserted"], 2)
        self.assertIn("Imported 2 candles", first["message"])
        self.assertTrue(second["already_complete"])
        self.assertEqual(second["rows_inserted"], 0)
        self.assertIn("No new data", second["message"])

    def test_database_unique_constraint_rejects_duplicate_identity(self):
        self.session.add(candle(START))
        self.session.commit()
        self.session.add(candle(START))

        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()
        self.assertEqual(self.session.scalar(select(func.count()).select_from(MarketData)), 1)

    def test_empty_or_malformed_csv_fails_without_changing_existing_rows(self):
        self.session.add(candle(START))
        self.session.commit()
        before = self.existing_minutes()

        for payload in (HEADER.encode(), (HEADER + "bad\trow\n").encode()):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    import_mt5_csv(io.BytesIO(payload), "GBPUSD.csv", self.session)
                self.assertEqual(self.existing_minutes(), before)
                self.assertIsNone(self.session.scalar(select(Instrument).where(Instrument.symbol == "GBPUSD")))

    def test_failure_on_later_batch_rolls_back_every_insert(self):
        timestamps = [START + timedelta(minutes=i) for i in range(BATCH_SIZE + 1)]
        underlying = self.session

        class FailSecondMarketInsert:
            def __init__(self):
                self.market_inserts = 0

            def intercept(self, statement):
                if getattr(getattr(statement, "table", None), "name", None) == "market_data":
                    self.market_inserts += 1
                    if self.market_inserts == 2:
                        raise RuntimeError("simulated provider/database failure")

            def execute(self, statement, *args, **kwargs):
                self.intercept(statement)
                return underlying.execute(statement, *args, **kwargs)

            def scalars(self, statement, *args, **kwargs):
                self.intercept(statement)
                return underlying.scalars(statement, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(underlying, name)

        with self.assertRaisesRegex(RuntimeError, "simulated provider/database failure"):
            import_mt5_csv(csv_bytes(timestamps), "EURUSD.csv", FailSecondMarketInsert())

        self.assertEqual(self.session.scalar(select(func.count()).select_from(MarketData)), 0)


if __name__ == "__main__":
    unittest.main()
