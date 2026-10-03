import os
os.environ.setdefault("DATABASE_URL", "sqlite://")

import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.analysis import AnalysisEngine
from app.backtest import (
    BacktestEngine, BacktestRunConfig, BacktestRunner, BacktestSettings, Bar, OrderAction, Side, Signal,
)
from app.database import Base
from app.models import Instrument, MarketData
from app.research import (
    DateRange, ExperimentStatus, ParameterSet, ParameterSpace, ResearchEngine,
    SelectionRule, TrainTestSplit, WalkForwardConfig,
)
from app.research.advanced import (
    AdvancedResearchResult, MonteCarloResult, RegimeDefinition, RobustnessScenario,
    aggregate_walk_forward, analyze_regimes, correlate_equity_returns,
    evaluate_robustness, monte_carlo_trades, parameter_sensitivity,
    weighted_rebalanced_portfolio,
)
from app.research.storage import (
    ResearchAnalysisRepository, ResearchExperimentRecord, ResearchResultRepository,
)
from app.research.service import ResearchApplicationService
from app.strategies import SMACrossoverFactory


START = datetime(2022, 1, 1)


def market_bars():
    prices = (10, 9, 8, 9, 12, 15, 13, 10, 8, 11, 14, 17, 15, 12, 9, 12, 16, 20)
    return tuple(Bar(START + timedelta(days=index), price, price + 1, price - 1, price,
                     tick_volume=10, volume=100, spread=0)
                 for index, price in enumerate(prices))


class MemoryRepository:
    def __init__(self, bars):
        self.bars = bars
        self.loads = []

    def load(self, symbol, start=None, end=None, *, timeframe="M1", end_exclusive=None):
        self.loads.append((start, end_exclusive, end, timeframe))
        upper = end_exclusive if end_exclusive is not None else end
        return tuple(bar for bar in self.bars if (start is None or bar.timestamp >= start)
                     and (upper is None or bar.timestamp < upper))


def backtest(bars=None):
    return BacktestEngine().run(bars or market_bars(), CrossingSignals())


class CrossingSignals:
    def on_bar(self, context):
        if context.index == 1:
            return Signal(OrderAction.OPEN, Side.LONG)
        if context.index == 4:
            return Signal(OrderAction.CLOSE)
        if context.index == 6:
            return Signal(OrderAction.OPEN, Side.SHORT)
        if context.index == 8:
            return Signal(OrderAction.CLOSE)
        return None


class AdvancedResearchTests(unittest.TestCase):
    def setUp(self):
        self.bars = market_bars()
        self.result = backtest(self.bars)
        self.analysis = AnalysisEngine().analyze(self.result)

    def test_monte_carlo_bootstrap_is_seeded_and_reports_distributions(self):
        first = monte_carlo_trades(self.result, simulations=200, seed=91)
        second = monte_carlo_trades(self.result, simulations=200, seed=91)
        self.assertEqual(first, second)
        self.assertEqual(len(first.terminal_equity), 200)
        self.assertEqual(len(first.terminal_return), 200)
        self.assertEqual(len(first.max_drawdown), 200)
        self.assertEqual([label for label, _ in first.terminal_percentiles],
                         ["p05", "p25", "p50", "p75", "p95"])
        shuffled = monte_carlo_trades(self.result, simulations=10, seed=1, method="shuffle")
        self.assertEqual(len(set(shuffled.terminal_equity)), 1)
        with self.assertRaises(ValueError):
            monte_carlo_trades(self.result, simulations=0, seed=1)

    def test_monte_carlo_rejects_no_closed_trades(self):
        flat = BacktestEngine().run(self.bars, type("NoSignals", (), {"on_bar": lambda *_: None})())
        with self.assertRaisesRegex(ValueError, "at least one"):
            monte_carlo_trades(flat, simulations=10, seed=1)

    def test_monte_carlo_refuses_an_unliquidated_position(self):
        open_result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
            self.bars, type("OpenLong", (), {
                "on_bar": lambda _self, context:
                    Signal(OrderAction.OPEN, Side.LONG) if context.index == 0 else None,
            })())
        self.assertIsNotNone(open_result.open_position)
        with self.assertRaisesRegex(ValueError, "fully closed"):
            monte_carlo_trades(open_result, simulations=10, seed=1)

    def test_parameter_sensitivity_has_stable_parameter_order_and_dispersion(self):
        template = SimpleNamespace(status=ExperimentStatus.COMPLETED, analysis_result=self.analysis)
        rows = []
        for fast, metric_value in ((3, 3.0), (1, 1.0), (2, 2.0)):
            analysis = replace(self.analysis, total_return=metric_value)
            rows.append(SimpleNamespace(status=template.status, analysis_result=analysis,
                definition=SimpleNamespace(parameters=ParameterSet.from_mapping({"fast": fast}))))
        result = parameter_sensitivity(rows, "total_return", ("fast",))
        self.assertEqual([cell.parameters for cell in result.cells],
                         [(("fast", 1),), (("fast", 2),), (("fast", 3),)])
        self.assertAlmostEqual(result.dispersion, 1)
        self.assertEqual(result.mean_absolute_neighbor_change, 1)

    def test_sensitivity_rejects_missing_fields_and_undefined_metric_is_omitted(self):
        bad = SimpleNamespace(status=ExperimentStatus.COMPLETED, analysis_result=self.analysis,
            definition=SimpleNamespace(parameters=ParameterSet.from_mapping({"other": 1})))
        with self.assertRaises(ValueError):
            parameter_sensitivity([bad], "total_return", ("fast",))
        analysis = replace(self.analysis, risk=replace(self.analysis.risk, sharpe_ratio=None))
        item = SimpleNamespace(status=ExperimentStatus.COMPLETED, analysis_result=analysis,
            definition=SimpleNamespace(parameters=ParameterSet.from_mapping({"fast": 2})))
        self.assertEqual(parameter_sensitivity([item], "risk.sharpe_ratio", ("fast",)).cells, ())

    def test_regimes_use_only_current_and_trailing_bars(self):
        definition = RegimeDefinition(lookback=4, volatility_threshold=.01, trend_threshold=.001)
        result = analyze_regimes(self.result, self.bars, definition)
        changed_future = self.bars[:-3] + tuple(
            replace(bar, close=bar.close * 100, high=bar.high * 100, low=bar.low * 100)
            for bar in self.bars[-3:])
        changed = analyze_regimes(self.result, changed_future, definition)
        self.assertEqual(result.observations[:len(self.bars)-3], changed.observations[:len(self.bars)-3])
        self.assertEqual(result.observations[0].label, "insufficient_history")
        self.assertEqual(sum(item.observations for item in result.statistics), len(self.bars))
        self.assertTrue(any(item.trades > 0 for item in result.statistics))
        changed_execution_bar = list(self.bars)
        changed_execution_bar[2] = replace(changed_execution_bar[2], close=1000)
        execution_changed = analyze_regimes(self.result, tuple(changed_execution_bar), definition)
        self.assertEqual(result.trade_regimes[0], execution_changed.trade_regimes[0])
        with self.assertRaises(ValueError):
            RegimeDefinition(1, .1)

    def test_correlation_uses_aligned_series_and_handles_constant_series(self):
        result = correlate_equity_returns({"base": self.analysis, "same": self.analysis})
        self.assertEqual(result.labels, ("base", "same"))
        self.assertEqual(len(result.timestamps), len(self.analysis.equity_curve))
        shifted = replace(self.analysis, equity_curve=self.analysis.equity_curve[1:])
        with self.assertRaisesRegex(ValueError, "identical"):
            correlate_equity_returns({"base": self.analysis, "shifted": shifted})
        constant = replace(self.analysis, equity_curve=tuple(
            replace(point, equity=self.analysis.starting_equity) for point in self.analysis.equity_curve))
        matrix = correlate_equity_returns({"a": constant, "b": constant})
        self.assertEqual(matrix.coefficients, ((None, None), (None, None)))

    def test_correlation_rejects_insufficient_strategy_count(self):
        with self.assertRaises(ValueError):
            correlate_equity_returns({"only": self.analysis})

    def test_weighted_portfolio_has_explicit_rebalanced_return_math(self):
        result = weighted_rebalanced_portfolio(
            {"a": self.analysis, "b": self.analysis},
            {"a": .25, "b": .75}, 10000)
        self.assertEqual(len(result.points), len(self.analysis.equity_curve))
        self.assertEqual(result.weights, (("a", .25), ("b", .75)))
        self.assertTrue(result.assumptions.startswith("Fixed target weights"))
        with self.assertRaises(ValueError):
            weighted_rebalanced_portfolio({"a": self.analysis}, {"a": .7}, 10000)
        shifted = replace(self.analysis, equity_curve=self.analysis.equity_curve[1:])
        with self.assertRaisesRegex(ValueError, "identical"):
            weighted_rebalanced_portfolio({"a": self.analysis, "b": shifted},
                                          {"a": .5, "b": .5}, 10000)

    def test_robustness_keeps_named_scenario_failures_and_baseline_trace(self):
        baseline = SimpleNamespace(status=ExperimentStatus.COMPLETED,
                                   definition=SimpleNamespace(experiment_id="baseline"))
        def runner(scenario):
            if scenario.name == "broken":
                raise RuntimeError("fixture failure")
            definition = SimpleNamespace(experiment_id=scenario.name)
            return SimpleNamespace(status=ExperimentStatus.COMPLETED, definition=definition,
                                   analysis_result=self.analysis)
        output = evaluate_robustness(baseline, (
            RobustnessScenario("higher_cost", commission_multiplier=2),
            RobustnessScenario("broken"),
        ), runner, "total_return")
        self.assertEqual(output.baseline_experiment_id, "baseline")
        self.assertEqual([point.status for point in output.points], ["completed", "failed"])
        self.assertIn("fixture failure", output.points[1].failure_message)


class Stage4WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.bars = market_bars()
        self.repository = MemoryRepository(self.bars)
        self.engine = ResearchEngine(BacktestRunner(self.repository))
        self.factory = SMACrossoverFactory()

    def candidates(self):
        from app.research import IntegerRange
        return ParameterSpace((IntegerRange("fast_period", 2, 3),
                               IntegerRange("slow_period", 5, 6)))

    def test_registered_sma_strategy_validates_and_executes_next_bar(self):
        strategy = self.factory.create({"fast_period": 2, "slow_period": 3})
        result = BacktestEngine().run(self.bars, strategy)
        first = result.orders[0]
        self.assertEqual(first.created_at, self.bars[4].timestamp)
        self.assertEqual(first.filled_at, self.bars[5].timestamp)
        self.assertEqual(first.reference_price, self.bars[5].open)
        with self.assertRaises(ValueError):
            self.factory.create({"fast_period": 3, "slow_period": 3})

    def test_train_selection_oos_and_walkforward_keep_phases_disjoint_and_reproducible(self):
        space = self.candidates()
        candidates = tuple(space.grid())
        end = START + timedelta(days=len(self.bars))
        split = TrainTestSplit(DateRange(START, START + timedelta(days=9)),
                               DateRange(START + timedelta(days=9), end))
        config = BacktestRunConfig("EURUSD", start=START, end=end, initial_cash=1000)
        one = self.engine.run_out_of_sample(candidates, strategy_factory=self.factory, config=config,
            split=split, selection=SelectionRule("total_return", top_n=2),
            parameter_space=space, search_method="grid")
        two = self.engine.run_out_of_sample(candidates, strategy_factory=self.factory, config=config,
            split=split, selection=SelectionRule("total_return", top_n=2),
            parameter_space=space, search_method="grid")
        self.assertEqual(one, two)
        self.assertTrue(all(row.definition.period.end <= split.testing.start for row in one.training.results))
        self.assertTrue(all(row.definition.period.start >= split.training.end for row in one.testing.results))
        self.assertTrue(all(row.definition.phase == "oos" and row.definition.source_training_experiment_id
                            for row in one.testing.results))
        self.assertEqual(one.tested_configurations, len(candidates))
        sensitivity = parameter_sensitivity(one.training.results, "total_return",
                                            ("fast_period", "slow_period"))
        self.assertEqual(len(sensitivity.cells), len(candidates))
        with self.assertRaisesRegex(ValueError, "share strategy, data period, phase"):
            parameter_sensitivity((one.training.results[0], one.testing.results[0]),
                                  "total_return", ("fast_period", "slow_period"))
        completed_oos = [row for row in one.testing.results if row.backtest_result is not None]
        traded_oos = next(row for row in completed_oos if row.backtest_result.trades)
        simulation = monte_carlo_trades(traded_oos.backtest_result, simulations=40, seed=7)
        self.assertEqual(simulation.seed, 7)
        test_bars = tuple(bar for bar in self.bars if split.testing.contains(bar.timestamp))
        regimes = analyze_regimes(traded_oos.backtest_result, test_bars, RegimeDefinition(3, .01))
        self.assertEqual(len(regimes.observations), len(test_bars))
        correlations = correlate_equity_returns({
            row.definition.experiment_id: row.analysis_result for row in completed_oos
        })
        self.assertEqual(len(correlations.timestamps), len(traded_oos.analysis_result.equity_curve))
        portfolio = weighted_rebalanced_portfolio({
            row.definition.experiment_id: row.analysis_result for row in completed_oos
        }, {row.definition.experiment_id: .5 for row in completed_oos}, 1000)
        self.assertEqual(len(portfolio.points), len(traded_oos.analysis_result.equity_curve))

        def run_cost_scenario(scenario):
            stressed = replace(config, start=split.testing.start, end=split.testing.end,
                               commission_per_unit=scenario.commission_per_unit_addition)
            return self.engine.run_batch((traded_oos.definition.parameters,),
                strategy_factory=self.factory, config=stressed, search_method="controlled_robustness",
                parameter_space=space).results[0]
        robustness = evaluate_robustness(traded_oos,
            (RobustnessScenario("additional commission", commission_per_unit_addition=.1),),
            run_cost_scenario, "trades.net_profit")
        self.assertEqual(robustness.points[0].status, "completed")
        self.assertLess(robustness.points[0].metric, traded_oos.analysis_result.trades.net_profit)
        config_wf = WalkForwardConfig(DateRange(START, end), timedelta(days=5),
                                      timedelta(days=2), timedelta(days=2), anchored=False)
        walk = self.engine.run_walk_forward(candidates, strategy_factory=self.factory, config=config,
            walk_forward=config_wf, selection=SelectionRule("total_return"),
            parameter_space=space, search_method="grid")
        self.assertEqual(len(walk.windows), len(config_wf.splits()))
        for window in walk.windows:
            self.assertLessEqual(window.split.training.end, window.split.testing.start)
            self.assertTrue(all(row.definition.period == window.split.training
                                for row in window.result.training.results))
            self.assertTrue(all(row.definition.period == window.split.testing
                                for row in window.result.testing.results))
        aggregate = aggregate_walk_forward(walk)
        self.assertEqual(len(aggregate.window_summaries), len(walk.windows))
        self.assertEqual(aggregate.completed_window_count,
                         sum(bool(item.return_values) for item in aggregate.window_summaries))
        self.assertAlmostEqual(aggregate.total_net_profit,
                               sum(item.net_profit for item in aggregate.window_summaries))


class ResearchAPIIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Importing the API registers the SQLAlchemy experiment record in metadata.
        from app.main import app
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        with self.sessions() as session:
            instrument = Instrument(symbol="EURUSD", type="fx")
            session.add(instrument)
            session.flush()
            for bar in market_bars():
                session.add(MarketData(instrument_id=instrument.id, timestamp=bar.timestamp,
                    open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                    tick_volume=bar.tick_volume, volume=bar.volume, spread=bar.spread))
            session.commit()
        self.app = app

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_research_application_service_runs_and_persists_stage_one_pipeline(self):
        from app.research.api import router
        routes = {(route.path, method) for route in router.routes
                  for method in getattr(route, "methods", ())}
        self.assertTrue(any(getattr(route, "path", None) == "/research"
                            for route in self.app.routes))
        self.assertIn("/api/research/run", self.app.openapi()["paths"])
        self.assertIn(("/api/research/run", "POST"), routes)
        self.assertIn(("/api/research/advanced/monte-carlo", "POST"), routes)
        self.assertIn(("/api/research/advanced/portfolio", "POST"), routes)
        with self.sessions() as session:
            service = ResearchApplicationService(session)
            self.assertEqual(service.available_strategies()[0]["id"],
                             "moving_average.sma_crossover")
            catalog = service.market_data_catalog("EURUSD")
            self.assertEqual(catalog["count"], len(market_bars()))
            from fastapi.templating import Jinja2Templates
            html = Jinja2Templates(directory="app/templates").get_template(
                "research.html").render(request=None)
            self.assertIn("Single backtest", html)
            self.assertIn("Training → OOS selection", html)
            body = {
                "mode": "single", "strategy_id": "moving_average.sma_crossover",
                "symbol": "EURUSD", "timeframe": "M1",
                "start": START.isoformat(), "end": (START + timedelta(days=len(market_bars()))).isoformat(),
                "parameters": {"fast_period": 2, "slow_period": 5}, "initial_cash": 1000,
                "periods_per_year": 252, "risk_free_rate": 0.02,
            }
            from app.research.api import ResearchRunRequest, run_research
            batch = run_research(ResearchRunRequest(**body), session)
            self.assertEqual(batch["tested_configurations"], 1)
            result = batch["results"][0]
            self.assertEqual(result["status"], "completed")
            self.assertGreater(len(result["backtest_result"]["equity_curve"]), 0)
            self.assertEqual(result["definition"]["analysis_config"]["periods_per_year"], 252)
            self.assertEqual(result["definition"]["analysis_config"]["risk_free_rate"], .02)
            identity = batch["results"][0]["definition"]["experiment_id"]
            stored = ResearchResultRepository(session).get(identity)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.status.value, "completed")
            invalid = dict(body, parameters={"fast_period": 5, "slow_period": 2})
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as rejected:
                run_research(ResearchRunRequest(**invalid), session)
            self.assertEqual(rejected.exception.status_code, 422)
            advanced = AdvancedResearchResult("monte_carlo", (identity,),
                (("seed", 42),), MonteCarloResult("shuffle", 42, 1, (1000,), (0,), (0,),
                (("p50", 1000),), (("p50", 0),), (("p50", 0),), "fixture assumptions"))
            advanced_store = ResearchAnalysisRepository(session)
            advanced_store.save(advanced)
            self.assertEqual(advanced_store.get(advanced.analysis_id), advanced)


if __name__ == "__main__":
    unittest.main()
