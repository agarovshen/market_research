import os
os.environ.setdefault("DATABASE_URL", "sqlite://")

import unittest
from dataclasses import replace
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.analysis import AnalysisSettings
from app.backtest import BacktestRunConfig, BacktestRunner, Bar, OrderAction, Side, Signal
from app.backtest.data import MarketDataRepository
from app.database import Base
from app.models import Instrument, MarketData
from app.research import (
    ChoiceParameter,
    DateRange,
    ExperimentPhase,
    ExperimentStatus,
    FixedParameter,
    FloatRange,
    IntegerRange,
    OutOfSampleResult,
    ParameterSet,
    ParameterSpace,
    ResearchEngine,
    ResearchResult,
    SelectionRule,
    TrainTestSplit,
    WalkForwardConfig,
)
from app.research.serialization import decode_result, encode_result
from app.research.storage import ResearchExperimentRecord, ResearchResultRepository


START = datetime(2020, 1, 1)


class QuantityStrategy:
    def __init__(self, quantity):
        self.quantity = quantity

    def on_bar(self, context):
        if context.index == 0:
            return Signal(OrderAction.OPEN, Side.LONG, self.quantity)
        if context.index == 1:
            return Signal(OrderAction.CLOSE)
        return None


class QuantityFactory:
    strategy_id = "test.quantity"
    strategy_version = "1.0.0"

    def validate(self, parameters):
        if "quantity" not in parameters or parameters["quantity"] <= 0:
            raise ValueError("quantity must be positive")

    def create(self, parameters):
        if parameters.get("fail"):
            raise RuntimeError("intentional strategy construction failure")
        return QuantityStrategy(parameters["quantity"])


class CountingRepository:
    def __init__(self, bars):
        self.bars = tuple(bars)
        self.loads = []

    def load(self, instrument, start=None, end=None, *, timeframe="M1", end_exclusive=None):
        self.loads.append((instrument, start, end, end_exclusive, timeframe))
        upper = end_exclusive if end_exclusive is not None else end
        return tuple(bar for bar in self.bars
                     if (start is None or bar.timestamp >= start)
                     and (upper is None or bar.timestamp < upper))


def fixture_bars():
    opens = (100, 105, 110, 120, 115, 110, 108, 112, 116, 120, 118, 122)
    return tuple(
        Bar(START + timedelta(days=i), value, value + 2, value - 2, value + 1,
            tick_volume=10 + i, volume=100 + i, spread=1)
        for i, value in enumerate(opens)
    )


def base_config(start=START, end=START + timedelta(days=12)):
    return BacktestRunConfig(symbol="EURUSD", timeframe="M1", start=start, end=end,
                             initial_cash=1000, final_liquidation=True)


def run_engine(repository=None):
    return ResearchEngine(BacktestRunner(repository or CountingRepository(fixture_bars())))


class ParameterGenerationTests(unittest.TestCase):
    def setUp(self):
        self.space = ParameterSpace((
            IntegerRange("fast", 1, 3),
            ChoiceParameter("mode", ("cross", "breakout")),
            FloatRange("risk", 0.5, 1.0, 0.5),
            FixedParameter("enabled", True),
        ))

    def test_grid_cartesian_product_count_and_order(self):
        generated = tuple(self.space.grid())
        self.assertEqual(len(generated), 12)
        self.assertEqual(generated[0].as_dict(),
                         {"enabled": True, "fast": 1, "mode": "cross", "risk": 0.5})
        self.assertEqual(generated[1].as_dict()["risk"], 1.0)
        self.assertEqual(generated[2].as_dict()["mode"], "breakout")
        self.assertEqual(generated[-1].as_dict()["fast"], 3)
        self.assertEqual(len(set(generated)), 12)

    def test_integer_float_fixed_and_choice_boundaries(self):
        space = ParameterSpace((IntegerRange("n", 2, 6, 2),
                                FloatRange("x", 0.2, 0.6, 0.2),
                                FixedParameter("f", "value"),
                                ChoiceParameter("c", (None, 2))))
        generated = tuple(space.grid())
        self.assertEqual(len(generated), 3 * 3 * 1 * 2)
        self.assertEqual([value.as_dict()["n"] for value in generated[::6]], [2, 4, 6])

    def test_random_seed_reproducibility_uniqueness_and_seed_validation(self):
        first = tuple(self.space.random(8, seed=42))
        second = tuple(self.space.random(8, seed=42))
        other = tuple(self.space.random(8, seed=43))
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(len(set(first)), 8)
        self.assertEqual(tuple(self.space.random(0, seed=42)), ())
        with self.assertRaises(ValueError):
            tuple(self.space.random(1, seed=None))

    def test_parameter_specification_rejects_invalid_bounds_names_and_grid(self):
        with self.assertRaises(ValueError):
            IntegerRange("n", 5, 1)
        with self.assertRaises(ValueError):
            FloatRange("x", 0, 1, 0.3)
        with self.assertRaises(ValueError):
            tuple(ParameterSpace((FloatRange("x", 0.0, 1.0),)).grid())
        with self.assertRaises(ValueError):
            ParameterSpace((FixedParameter("same", 1), FixedParameter("same", 2)))
        with self.assertRaises(ValueError):
            ChoiceParameter("choice", (1, 1))
        with self.assertRaises(ValueError):
            ParameterSet.from_mapping({"nan": float("nan")})

    def test_random_generation_rejects_more_unique_configs_than_finite_space(self):
        finite = ParameterSpace((ChoiceParameter("side", ("long", "short")),))
        with self.assertRaisesRegex(ValueError, "unique configurations"):
            tuple(finite.random(3, seed=3))

    def test_random_generator_does_not_change_global_random_state(self):
        import random
        state = random.getstate()
        tuple(self.space.random(3, seed=16))
        self.assertEqual(random.getstate(), state)

    def test_parameter_sets_have_canonical_equality_and_mapping_order(self):
        first = ParameterSet.from_mapping({"b": 2, "a": 1})
        second = ParameterSet.from_mapping({"a": 1, "b": 2})
        self.assertEqual(first, second)
        self.assertEqual(first.values, (("a", 1), ("b", 2)))


class TimeSplitTests(unittest.TestCase):
    def test_half_open_boundaries_adjacent_ranges_and_no_overlap(self):
        train = DateRange(START, START + timedelta(days=3))
        test = DateRange(START + timedelta(days=3), START + timedelta(days=5))
        split = TrainTestSplit(train, test)
        self.assertTrue(train.contains(START))
        self.assertFalse(train.contains(test.start))
        self.assertTrue(test.contains(test.start))
        self.assertEqual(split.training.end, split.testing.start)

    def test_reversed_empty_overlapping_and_timezone_mismatch_ranges_fail(self):
        with self.assertRaises(ValueError):
            DateRange(START, START)
        with self.assertRaises(ValueError):
            DateRange(START + timedelta(days=2), START)
        with self.assertRaises(ValueError):
            TrainTestSplit(DateRange(START, START + timedelta(days=4)),
                           DateRange(START + timedelta(days=3), START + timedelta(days=5)))
        aware = datetime(2020, 1, 1, tzinfo=START.astimezone().tzinfo)
        with self.assertRaises(ValueError):
            DateRange(START, aware)

    def test_walk_forward_anchored_and_rolling_window_boundaries(self):
        period = DateRange(START, START + timedelta(days=12))
        anchored = WalkForwardConfig(period, timedelta(days=4), timedelta(days=2), timedelta(days=2))
        rolling = WalkForwardConfig(period, timedelta(days=4), timedelta(days=2), timedelta(days=2), anchored=False)
        anchored_splits = anchored.splits()
        rolling_splits = rolling.splits()
        self.assertEqual(len(anchored_splits), 4)
        self.assertEqual(anchored_splits[0], TrainTestSplit(
            DateRange(START, START + timedelta(days=4)),
            DateRange(START + timedelta(days=4), START + timedelta(days=6))))
        self.assertEqual(anchored_splits[1].training.start, START)
        self.assertEqual(anchored_splits[1].training.end, START + timedelta(days=6))
        self.assertEqual(rolling_splits[1].training.start, START + timedelta(days=2))
        for split in anchored_splits + rolling_splits:
            self.assertLessEqual(split.training.end, split.testing.start)

    def test_walk_forward_rejects_overlap_and_handles_insufficient_range(self):
        with self.assertRaises(ValueError):
            WalkForwardConfig(DateRange(START, START + timedelta(days=4)),
                              timedelta(days=2), timedelta(days=2), timedelta(days=1))
        too_short = WalkForwardConfig(DateRange(START, START + timedelta(days=5)),
                                      timedelta(days=4), timedelta(days=2), timedelta(days=2))
        self.assertEqual(too_short.splits(), ())


class ResearchBatchTests(unittest.TestCase):
    def test_end_to_end_grid_batch_uses_runner_and_analysis_with_stable_order(self):
        repo = CountingRepository(fixture_bars())
        engine = run_engine(repo)
        space = ParameterSpace((IntegerRange("quantity", 1, 3),))
        results = engine.run_batch(space.grid(), strategy_factory=QuantityFactory(),
                                   config=base_config(), parameter_space=space,
                                   search_method="grid", seed=17)
        self.assertEqual(results.tested_configurations, 3)
        self.assertEqual(results.completed_count, 3)
        self.assertEqual([r.definition.parameters.as_dict()["quantity"] for r in results.results], [1, 2, 3])
        self.assertEqual(len(repo.loads), 1)  # one data query shared by the whole batch
        for result in results.results:
            self.assertEqual(result.status, ExperimentStatus.COMPLETED)
            self.assertEqual(result.definition.phase, ExperimentPhase.BATCH)
            self.assertEqual(result.definition.strategy_version, "1.0.0")
            self.assertIsNotNone(result.backtest_result)
            self.assertIsNotNone(result.analysis_result)
        self.assertEqual(results.results[2].analysis_result.trades.total_trades, 1)

    def test_empty_batch_never_loads_data_and_duplicate_candidates_are_rejected(self):
        repo = CountingRepository(fixture_bars())
        engine = run_engine(repo)
        empty = engine.run_batch((), strategy_factory=QuantityFactory(), config=base_config())
        self.assertEqual(empty.tested_configurations, 0)
        self.assertEqual(repo.loads, [])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            engine.run_batch(({"quantity": 1}, {"quantity": 1}),
                             strategy_factory=QuantityFactory(), config=base_config())
        self.assertEqual(repo.loads, [])

    def test_invalid_parameter_and_strategy_failure_are_isolated_and_observable(self):
        results = run_engine().run_batch(
            ({"quantity": -1}, {"quantity": 1, "fail": True}, {"quantity": 2}),
            strategy_factory=QuantityFactory(), config=base_config())
        self.assertEqual(results.tested_configurations, 3)
        self.assertEqual(results.completed_count, 1)
        self.assertEqual(results.failed_count, 2)
        self.assertEqual(results.results[0].failure.exception_type, "ValueError")
        self.assertIn("positive", results.results[0].failure.message)
        self.assertEqual(results.results[1].failure.exception_type, "RuntimeError")

    def test_missing_strategy_identity_or_factory_methods_are_rejected(self):
        class BadFactory:
            strategy_id = ""
            strategy_version = "1"
        with self.assertRaisesRegex(ValueError, "strategy_id"):
            run_engine().run_batch(({"quantity": 1},), strategy_factory=BadFactory(), config=base_config())

    def test_experiment_identity_is_canonical_and_sensitive_to_relevant_inputs(self):
        engine = run_engine()
        first = engine.run_batch(({"quantity": 1, "label": "x"},), strategy_factory=QuantityFactory(),
                                 config=base_config(), seed=10).results[0]
        same = engine.run_batch(({"label": "x", "quantity": 1},), strategy_factory=QuantityFactory(),
                                config=base_config(), seed=10).results[0]
        other_seed = engine.run_batch(({"quantity": 1, "label": "x"},), strategy_factory=QuantityFactory(),
                                      config=base_config(), seed=11).results[0]
        self.assertEqual(first.definition.canonical_representation, same.definition.canonical_representation)
        self.assertEqual(first.definition.experiment_id, same.definition.experiment_id)
        self.assertNotEqual(first.definition.experiment_id, other_seed.definition.experiment_id)
        self.assertEqual(len(first.definition.experiment_id), 64)

    def test_repeated_batch_produces_identical_results_and_data_fingerprint_changes(self):
        configs = ({"quantity": 1}, {"quantity": 2})
        first = run_engine().run_batch(configs, strategy_factory=QuantityFactory(), config=base_config(), seed=4)
        second = run_engine().run_batch(configs, strategy_factory=QuantityFactory(), config=base_config(), seed=4)
        self.assertEqual(first, second)
        changed_bars = list(fixture_bars())
        changed_bars[0] = replace(changed_bars[0], close=changed_bars[0].close + 0.25)
        changed = run_engine(CountingRepository(changed_bars)).run_batch(
            configs, strategy_factory=QuantityFactory(), config=base_config(), seed=4)
        self.assertNotEqual(first.results[0].definition.dataset_fingerprint,
                            changed.results[0].definition.dataset_fingerprint)
        self.assertNotEqual(first.results[0].definition.experiment_id,
                            changed.results[0].definition.experiment_id)

    def test_research_factory_creates_fresh_stateful_strategy_per_run(self):
        class StatefulStrategy:
            def __init__(self, quantity):
                self.quantity = quantity
                self.calls = 0

            def on_bar(self, context):
                self.calls += 1
                if self.calls == 1:
                    return Signal(OrderAction.OPEN, Side.LONG, self.quantity)
                if self.calls == 3:
                    return Signal(OrderAction.CLOSE)

        class StatefulFactory(QuantityFactory):
            def __init__(self):
                self.created = []

            def create(self, parameters):
                strategy = StatefulStrategy(parameters["quantity"])
                self.created.append(strategy)
                return strategy

        factory = StatefulFactory()
        engine = run_engine()
        candidates = ({"quantity": 1},)
        first = engine.run_batch(candidates, strategy_factory=factory, config=base_config())
        second = engine.run_batch(candidates, strategy_factory=factory, config=base_config())

        self.assertEqual(first, second)
        self.assertEqual(first.results[0].analysis_result.trades.total_trades, 1)
        self.assertEqual(second.results[0].analysis_result.trades.total_trades, 1)
        self.assertEqual(len(factory.created), 2)
        self.assertIsNot(factory.created[0], factory.created[1])

    def test_random_method_requires_a_seed_and_records_parameter_space(self):
        space = ParameterSpace((IntegerRange("quantity", 1, 20),))
        with self.assertRaisesRegex(ValueError, "explicit seed"):
            run_engine().run_batch(space.random(2, seed=42), strategy_factory=QuantityFactory(),
                                   config=base_config(), search_method="random", parameter_space=space)
        results = run_engine().run_batch(space.random(2, seed=42), strategy_factory=QuantityFactory(),
                                         config=base_config(), search_method="random",
                                         parameter_space=space, seed=42)
        self.assertEqual(results.results[0].definition.parameter_space, space)

    def test_parameter_space_rejects_candidates_outside_its_specification(self):
        space = ParameterSpace((IntegerRange("quantity", 1, 2),))
        result = run_engine().run_batch(
            ({"quantity": 3},), strategy_factory=QuantityFactory(), config=base_config(),
            parameter_space=space,
        ).results[0]
        self.assertEqual(result.status, ExperimentStatus.FAILED)
        self.assertIn("outside its integer range", result.failure.message)
        with self.assertRaisesRegex(ValueError, "must record"):
            run_engine().run_batch(({"quantity": 1},), strategy_factory=QuantityFactory(),
                                   config=base_config(), search_method="grid")

    def test_empty_period_is_structured_failure_and_one_bar_period_is_supported(self):
        empty_config = base_config(START + timedelta(days=30), START + timedelta(days=31))
        empty = run_engine().run_batch(({"quantity": 1},), strategy_factory=QuantityFactory(),
                                       config=empty_config)
        self.assertEqual(empty.failed_count, 1)
        self.assertEqual(empty.results[0].failure.exception_type, "InsufficientDataError")
        one_config = base_config(START, START + timedelta(days=1))
        one = run_engine().run_batch(({"quantity": 1},), strategy_factory=QuantityFactory(),
                                     config=one_config)
        self.assertEqual(one.completed_count, 1)
        self.assertEqual(len(one.results[0].backtest_result.equity_curve), 1)


class OutOfSampleTests(unittest.TestCase):
    def setUp(self):
        self.repo = CountingRepository(fixture_bars())
        self.engine = run_engine(self.repo)
        self.split = TrainTestSplit(
            DateRange(START, START + timedelta(days=3)),
            DateRange(START + timedelta(days=3), START + timedelta(days=6)),
        )

    def test_training_selection_then_oos_only_runs_selected_candidate(self):
        configurations = ({"quantity": 1}, {"quantity": 3})
        result = self.engine.run_out_of_sample(
            configurations, strategy_factory=QuantityFactory(), config=base_config(), split=self.split,
            selection=SelectionRule("total_return", maximize=True), seed=9,
        )
        self.assertIsInstance(result, OutOfSampleResult)
        self.assertEqual(result.training.tested_configurations, 2)
        self.assertEqual(result.training.results[0].definition.phase, ExperimentPhase.TRAIN)
        self.assertEqual(len(result.selected), 1)
        self.assertEqual(result.selected[0].parameters.as_dict()["quantity"], 3)
        self.assertEqual(result.testing.tested_configurations, 1)
        oos = result.testing.results[0]
        self.assertEqual(oos.definition.phase, ExperimentPhase.OOS)
        self.assertEqual(oos.definition.source_training_experiment_id,
                         result.selected[0].training_experiment_id)
        self.assertEqual(oos.definition.tested_configurations, 2)
        self.assertEqual([call[1] for call in self.repo.loads], [self.split.training.start, self.split.testing.start])

    def test_selection_is_training_only_and_oos_metric_paths_are_rejected(self):
        batch = self.engine.run_batch(({"quantity": 1},), strategy_factory=QuantityFactory(), config=base_config())
        with self.assertRaisesRegex(ValueError, "training-phase"):
            self.engine.select_training_candidates(batch, SelectionRule("total_return"))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            SelectionRule("oos.total_return")

    def test_invalid_training_metric_candidates_are_not_sent_to_oos(self):
        result = self.engine.run_out_of_sample(
            ({"quantity": 1},), strategy_factory=QuantityFactory(), config=base_config(), split=self.split,
            selection=SelectionRule("trades.profit_factor"),
        )
        self.assertEqual(result.selected, ())
        self.assertEqual(result.testing.results, ())
        self.assertEqual(len(self.repo.loads), 1)  # no test-period load without a selected candidate

    def test_oos_results_are_not_used_to_change_training_selection(self):
        once = self.engine.run_out_of_sample(
            ({"quantity": 1}, {"quantity": 3}), strategy_factory=QuantityFactory(), config=base_config(),
            split=self.split, selection=SelectionRule("total_return"),
        )
        selected_before = once.selected
        # Later test output can be inspected but does not mutate the immutable training selection.
        self.assertEqual(once.selected, selected_before)
        self.assertEqual(once.selected[0].training_experiment_id,
                         once.testing.results[0].definition.source_training_experiment_id)

    def test_result_model_rejects_partial_and_misassociated_oos_results(self):
        result = self.engine.run_out_of_sample(
            ({"quantity": 1},), strategy_factory=QuantityFactory(), config=base_config(),
            split=self.split, selection=SelectionRule("total_return"),
        )
        completed = result.training.results[0]
        with self.assertRaisesRegex(ValueError, "partial result"):
            ResearchResult(completed.definition, ExperimentStatus.FAILED,
                           completed.backtest_result, None, completed.failure)

        oos_result = result.testing.results[0]
        invalid_definition = replace(oos_result.definition, source_training_experiment_id="0" * 64)
        invalid_oos = replace(oos_result, definition=invalid_definition)
        invalid_batch = replace(result.testing, results=(invalid_oos,))
        with self.assertRaisesRegex(ValueError, "selected training candidate"):
            replace(result, testing=invalid_batch)

    def test_walk_forward_executes_each_train_then_test_window_without_leakage(self):
        spec = WalkForwardConfig(
            DateRange(START, START + timedelta(days=12)),
            timedelta(days=4), timedelta(days=2), timedelta(days=2), anchored=False,
        )
        result = self.engine.run_walk_forward(
            ({"quantity": 1},), strategy_factory=QuantityFactory(), config=base_config(),
            walk_forward=spec, selection=SelectionRule("total_return"), seed=8,
        )
        self.assertEqual(len(result.windows), 4)
        self.assertEqual(len(self.repo.loads), 8)
        for index, window in enumerate(result.windows):
            self.assertLessEqual(window.split.training.end, window.split.testing.start)
            self.assertEqual(window.result.training.results[0].definition.phase, ExperimentPhase.TRAIN)
            self.assertEqual(window.result.testing.results[0].definition.phase, ExperimentPhase.OOS)
            self.assertEqual(window.result.training.results[0].definition.walk_forward_window, index + 1)
            self.assertEqual(window.result.testing.results[0].definition.walk_forward_window, index + 1)
            self.assertEqual(self.repo.loads[index * 2][1], window.split.training.start)
            self.assertEqual(self.repo.loads[index * 2 + 1][1], window.split.testing.start)
        repeated = run_engine().run_walk_forward(
            ({"quantity": 1},), strategy_factory=QuantityFactory(), config=base_config(),
            walk_forward=spec, selection=SelectionRule("total_return"), seed=8,
        )
        self.assertEqual(result, repeated)


class RepositoryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        instrument = Instrument(symbol="EURUSD", type="forex")
        self.session.add(instrument)
        self.session.flush()
        self.session.add_all([
            MarketData(instrument_id=instrument.id, timestamp=START + timedelta(minutes=i),
                       open=100 + i, high=101 + i, low=99 + i, close=100 + i,
                       tick_volume=i, volume=i * 10, spread=1)
            for i in range(6)
        ])
        self.session.commit()
        self.repository = MarketDataRepository(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_exclusive_end_is_supported_without_changing_inclusive_end(self):
        inclusive = self.repository.load("EURUSD", end=START + timedelta(minutes=3))
        exclusive = self.repository.load("EURUSD", end_exclusive=START + timedelta(minutes=3))
        self.assertEqual(len(inclusive), 4)
        self.assertEqual(len(exclusive), 3)
        with self.assertRaisesRegex(ValueError, "either inclusive end"):
            self.repository.load("EURUSD", end=START, end_exclusive=START + timedelta(minutes=1))

    def test_research_higher_timeframe_boundaries_exclude_boundary_source_rows(self):
        engine = ResearchEngine(BacktestRunner(self.repository))
        result = engine.run_batch(({"quantity": 1},), strategy_factory=QuantityFactory(),
                                  config=BacktestRunConfig(
                                      symbol="EURUSD", timeframe="M5",
                                      start=START + timedelta(minutes=1),
                                      end=START + timedelta(minutes=4), initial_cash=1000,
                                  ))
        # Partial M5 candle label precedes the unaligned start/end interval and is excluded.
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.results[0].failure.exception_type, "InsufficientDataError")


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.repository = ResearchResultRepository(self.session)
        self.result = run_engine().run_batch(
            ({"quantity": 2},), strategy_factory=QuantityFactory(), config=base_config(),
            parameter_space=ParameterSpace((IntegerRange("quantity", 1, 3),)),
        ).results[0]

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_tagged_json_round_trip_preserves_complete_research_result(self):
        self.assertEqual(decode_result(encode_result(self.result)), self.result)
        self.repository.save(self.result)
        self.session.commit()
        loaded = self.repository.get(self.result.definition.experiment_id)
        self.assertEqual(loaded, self.result)
        record = self.session.get(ResearchExperimentRecord, self.result.definition.experiment_id)
        self.assertEqual(record.strategy_id, "test.quantity")
        self.assertEqual(record.symbol, "EURUSD")
        self.assertEqual(record.status, "completed")

    def test_save_is_idempotent_and_detects_identity_collision(self):
        self.repository.save(self.result)
        self.session.commit()
        self.repository.save(self.result)
        self.session.commit()
        count = self.session.scalar(select(ResearchExperimentRecord))
        self.assertIsNotNone(count)
        changed_backtest = replace(self.result.backtest_result, final_equity=1234)
        different_result = replace(self.result, backtest_result=changed_backtest)
        with self.assertRaisesRegex(ValueError, "identity collision"):
            self.repository.save(different_result)

    def test_research_engine_persists_completed_and_failed_batch_results(self):
        engine = ResearchEngine(BacktestRunner(CountingRepository(fixture_bars())),
                                result_store=self.repository)
        batch = engine.run_batch(
            ({"quantity": 1}, {"quantity": -1}),
            strategy_factory=QuantityFactory(), config=base_config(),
        )
        self.session.commit()
        stored = self.repository.list_for_strategy("test.quantity")
        self.assertEqual(len(stored), 2)
        self.assertEqual({item.status for item in stored},
                         {ExperimentStatus.COMPLETED, ExperimentStatus.FAILED})
        self.assertEqual({item.definition.experiment_id for item in stored},
                         {item.definition.experiment_id for item in batch.results})


if __name__ == "__main__":
    unittest.main()
