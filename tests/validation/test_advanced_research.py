from datetime import datetime, timedelta

import pytest

from app.analysis import AnalysisEngine
from app.backtest import BacktestEngine, BacktestSettings, Bar, OrderAction, Side, Signal
from app.research import (
    RegimeDefinition, analyze_regimes, correlate_equity_returns, monte_carlo_trades,
)
from app.research.advanced import drawdown_from_equity, weighted_rebalanced_portfolio
from tests.validation.support import bars


class Script:
    def on_bar(self, context):
        if context.index in (0, 2, 4):
            return Signal(OrderAction.OPEN, Side.LONG)
        if context.index in (1, 3, 5):
            return Signal(OrderAction.CLOSE)


def closed_result(prices):
    return BacktestEngine(BacktestSettings(initial_cash=100)).run(
        bars(*prices), Script())


def test_monte_carlo_seed_and_known_single_winner_and_loser():
    winner = closed_result((10, 10, 12))
    a = monte_carlo_trades(winner, simulations=8, seed=7)
    b = monte_carlo_trades(winner, simulations=8, seed=7)
    assert a == b
    assert set(a.terminal_equity) == {102.0}

    loser = closed_result((10, 10, 8))
    assert set(monte_carlo_trades(loser, simulations=3, seed=1).terminal_equity) == {98.0}
    mixed = closed_result((10, 10, 12, 11, 8, 9, 10))
    assert monte_carlo_trades(mixed, simulations=20, seed=1) != monte_carlo_trades(
        mixed, simulations=20, seed=2)


def test_monte_carlo_rejects_empty_closed_trade_sample():
    no_trade = BacktestEngine().run(bars(1, 2), ScriptNoop())
    with pytest.raises(ValueError, match="at least one completed trade"):
        monte_carlo_trades(no_trade, simulations=1, seed=0)


class ScriptNoop:
    def on_bar(self, context):
        return None


def test_simple_drawdown_known_path_and_regimes_are_deterministic():
    assert drawdown_from_equity((100, 110, 105, 90, 115)) == (0, 0, 5, 20, 0)
    data = bars(100, 102, 101, 105, 104)
    result = BacktestEngine().run(data, ScriptNoop())
    definition = RegimeDefinition(lookback=2, volatility_threshold=0.0)
    first = analyze_regimes(result, data, definition)
    assert first == analyze_regimes(result, data, definition)


def test_correlation_constant_and_known_relationships():
    with pytest.raises(ValueError, match="at least two"):
        correlate_equity_returns({})


class BuyOnFirstBar:
    def on_bar(self, context):
        if context.index == 0:
            return Signal(OrderAction.OPEN, Side.LONG)


def _analysis_with_final_close(close):
    start = datetime(2024, 1, 1)
    data = (
        Bar(start, 1, 1, 1, 1),
        Bar(start + timedelta(days=1), 1, max(1, close), min(1, close), close),
    )
    backtest = BacktestEngine(BacktestSettings(initial_cash=1)).run(data, BuyOnFirstBar())
    return AnalysisEngine().analyze(backtest)


def test_weighted_portfolio_is_independent_of_equivalent_mapping_order():
    # One large return and two near-total losses expose order-dependent sums.
    results = {
        "a": _analysis_with_final_close(1_000_000_000_001.0),
        "b": _analysis_with_final_close(0.01),
        "c": _analysis_with_final_close(0.01),
    }
    weights = {name: 1 / 3 for name in results}
    first = weighted_rebalanced_portfolio(results, weights, 100)

    reordered_names = ("c", "b", "a")
    reordered_results = {name: results[name] for name in reordered_names}
    reordered_weights = {name: weights[name] for name in reordered_names}
    second = weighted_rebalanced_portfolio(reordered_results, reordered_weights, 100)

    assert first == second
    assert tuple(point.period_return.hex() for point in first.points) == tuple(
        point.period_return.hex() for point in second.points
    )
    assert tuple(point.equity.hex() for point in first.points) == tuple(
        point.equity.hex() for point in second.points
    )
