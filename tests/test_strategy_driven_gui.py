import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.backtest import BacktestRunConfig, BacktestRunner, Bar, OrderAction, Side, Signal
from app.research import FixedParameter, ParameterSet, ParameterSpace, ResearchEngine
from app.research.service import ResearchApplicationService
from app.strategies.sma_crossover import SMACrossoverFactory


START = datetime(2024, 1, 1)
BARS = tuple(Bar(START + timedelta(hours=i), price, price + 1, price - 1, price)
             for i, price in enumerate((10.0, 12.0, 11.0, 13.0)))


class MemoryRepository:
    def load(self, symbol, start=None, end=None, *, timeframe="M1", end_exclusive=None):
        upper = end_exclusive if end_exclusive is not None else end
        return tuple(bar for bar in BARS if (start is None or bar.timestamp >= start)
                     and (upper is None or bar.timestamp < upper))


class PriceActionStrategy:
    def __init__(self, threshold=0.0):
        self.threshold = threshold

    def on_bar(self, context):
        if context.index == 0 and context.bar.close >= 10 + self.threshold:
            return Signal(OrderAction.OPEN, Side.LONG)
        if context.index == 1:
            return Signal(OrderAction.CLOSE)
        return None


class NoParameterFactory:
    strategy_id = "fixture.price_action"
    strategy_version = "1"
    parameter_schema = ()

    def validate(self, parameters):
        if parameters:
            raise ValueError("This strategy has no parameters")

    def create(self, parameters):
        self.validate(parameters)
        return PriceActionStrategy()


class CustomParameterFactory:
    strategy_id = "fixture.breakout"
    strategy_version = "2"
    parameter_schema = (
        {"name": "body_ratio", "kind": "float", "default": 0.5, "minimum": 0.1, "maximum": 1.0},
        {"name": "breakout_buffer", "kind": "integer", "default": 0, "minimum": 0, "maximum": 5},
    )

    def validate(self, parameters):
        if set(parameters) != {"body_ratio", "breakout_buffer"}:
            raise ValueError("Expected this strategy's own parameter contract")

    def create(self, parameters):
        self.validate(parameters)
        return PriceActionStrategy(parameters["breakout_buffer"])


class StrategyDrivenTests(unittest.TestCase):
    def test_strategy_catalog_returns_factory_owned_schema_without_gui_assumptions(self):
        with patch("app.research.service.STRATEGY_FACTORIES", {
            NoParameterFactory.strategy_id: NoParameterFactory(),
            CustomParameterFactory.strategy_id: CustomParameterFactory(),
            SMACrossoverFactory.strategy_id: SMACrossoverFactory(),
        }):
            catalog = ResearchApplicationService(None).available_strategies()
        by_id = {strategy["id"]: strategy["parameters"] for strategy in catalog}
        self.assertEqual(by_id[NoParameterFactory.strategy_id], [])
        self.assertEqual([item["name"] for item in by_id[CustomParameterFactory.strategy_id]],
                         ["body_ratio", "breakout_buffer"])
        self.assertEqual([item["name"] for item in by_id[SMACrossoverFactory.strategy_id]],
                         ["fast_period", "slow_period"])

    def test_research_runner_accepts_a_strategy_with_no_indicators_or_parameters(self):
        engine = ResearchEngine(BacktestRunner(MemoryRepository()))
        result = engine.run_batch((ParameterSet(()),), strategy_factory=NoParameterFactory(),
            config=BacktestRunConfig("EURUSD", timeframe="H4", start=START,
                                     end=START + timedelta(hours=len(BARS)), initial_cash=1000))
        self.assertEqual(result.completed_count, 1)
        completed = result.results[0]
        self.assertEqual(completed.definition.parameters.as_dict(), {})
        self.assertEqual(len(completed.backtest_result.trades), 1)
        self.assertEqual(completed.backtest_result.trades[0].entry_time, BARS[1].timestamp)

    def test_research_runner_uses_arbitrary_factory_parameters_verbatim(self):
        space = ParameterSpace((FixedParameter("body_ratio", 0.75),
                                FixedParameter("breakout_buffer", 0)))
        engine = ResearchEngine(BacktestRunner(MemoryRepository()))
        result = engine.run_batch(space.grid(), strategy_factory=CustomParameterFactory(),
            config=BacktestRunConfig("EURUSD", start=START,
                                     end=START + timedelta(hours=len(BARS)), initial_cash=1000),
            parameter_space=space, search_method="grid")
        self.assertEqual(result.completed_count, 1)
        self.assertEqual(result.results[0].definition.parameters.as_dict(),
                         {"body_ratio": 0.75, "breakout_buffer": 0})
        self.assertIsNotNone(result.results[0].analysis_result)


if __name__ == "__main__":
    unittest.main()
