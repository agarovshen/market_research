from dataclasses import replace

from app.backtest import BacktestEngine, OrderAction, Side, Signal

from tests.validation.support import bars


class PrefixOnlyStrategy:
    def __init__(self):
        self.observed = []

    def on_bar(self, context):
        self.observed.append(tuple(item.timestamp for item in context.history))
        if context.index == 0:
            return Signal(OrderAction.OPEN, Side.LONG)
        if context.index == 1:
            return Signal(OrderAction.CLOSE)
        return None


def test_strategy_history_is_only_current_completed_prefix():
    strategy = PrefixOnlyStrategy()
    BacktestEngine().run(bars(10, 11, 12, 13, 14), strategy)
    assert [len(prefix) for prefix in strategy.observed] == [1, 2, 3, 4, 5]
    assert all(len(prefix) == index + 1 for index, prefix in enumerate(strategy.observed))


def test_changing_only_future_bars_cannot_change_earlier_trade_decisions_or_equity():
    original = bars(100, 110, 120, 121, 122)
    adversarial = original[:3] + tuple(
        replace(bar, open=10_000 + index, high=10_000 + index,
                low=10_000 + index, close=10_000 + index)
        for index, bar in enumerate(original[3:], start=3)
    )
    first = BacktestEngine().run(original, PrefixOnlyStrategy())
    second = BacktestEngine().run(adversarial, PrefixOnlyStrategy())

    assert first.orders[:2] == second.orders[:2]
    assert first.trades == second.trades
    assert first.equity_curve[:3] == second.equity_curve[:3]


def test_strategy_callback_cannot_index_unobserved_future_bar():
    strategy = PrefixOnlyStrategy()
    BacktestEngine().run(bars(10, 11, 12), strategy)
    assert all(len(history) <= len(strategy.observed) for history in strategy.observed)
