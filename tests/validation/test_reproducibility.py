from app.analysis import AnalysisEngine
from app.backtest import BacktestEngine, BacktestSettings, OrderAction, Side, Signal
from tests.validation.support import bars


class FixedStrategy:
    def on_bar(self, context):
        if context.index == 0:
            return Signal(OrderAction.OPEN, Side.LONG)
        if context.index == 2:
            return Signal(OrderAction.CLOSE)
        return None


def test_identical_configuration_repeats_trades_orders_equity_and_analysis():
    inputs = bars(10, 11, 14, 13)
    config = BacktestSettings(initial_cash=250, position_size=2)
    first = BacktestEngine(config).run(inputs, FixedStrategy())
    second = BacktestEngine(config).run(inputs, FixedStrategy())
    assert first == second
    assert AnalysisEngine().analyze(first) == AnalysisEngine().analyze(second)
