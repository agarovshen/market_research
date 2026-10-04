"""Seeded differential properties against the independent arithmetic model."""

import random
from datetime import datetime, timedelta

import pytest

from app.analysis import AnalysisEngine
from app.backtest import (
    BacktestEngine, BacktestSettings, Bar, ExecutionCosts, OrderAction, Side, Signal,
)
from tests.validation.reference import first_difference
from tests.validation.reference_model import calculate


class OneTrade:
    def __init__(self, side, quantity):
        self.side, self.quantity = side, quantity

    def on_bar(self, context):
        if context.index == 0:
            return Signal(OrderAction.OPEN, self.side, self.quantity)
        if context.index == 2:
            return Signal(OrderAction.CLOSE)
        return None


@pytest.mark.parametrize("seed", (20261004, 17))
def test_seeded_ohlc_paths_match_reference_and_directional_arithmetic(seed):
    rng = random.Random(seed)
    for case in range(40):
        entry = rng.randint(50, 250) / 10
        movement = rng.randint(-30, 30) / 10
        exit_price = entry + movement
        quantity = rng.randint(1, 7)
        # Open prices at bars 1 and 3 are the fills; other OHLC values are
        # valid enclosures and do not affect this event-only strategy.
        opens = (entry - 1, entry, entry + rng.randint(-5, 5) / 10, exit_price, exit_price)
        times = [datetime(2022, 1, 1) + timedelta(hours=case * 5 + i) for i in range(5)]
        rows, bars = [], []
        for timestamp, op in zip(times, opens):
            high, low = op + 1, op - 1
            row = {"time": timestamp, "open": op, "high": high, "low": low,
                   "close": op, "spread": 0.0}
            rows.append(row)
            bars.append(Bar(timestamp, op, high, low, op))

        side = Side.LONG if case % 2 == 0 else Side.SHORT
        side_text = side.value
        events = {0: ("open", side_text, quantity), 2: ("close", None, None)}
        expected = calculate(rows, events, initial_cash=500)
        actual = BacktestEngine(BacktestSettings(initial_cash=500)).run(
            bars, OneTrade(side, quantity))
        analysis = AnalysisEngine().analyze(actual)
        trade, = actual.trades
        expected_trade, = expected["trades"]
        assert first_difference(
            {"trades": [{"side": trade.side.value, "entry_time": trade.entry_time,
                         "entry_price": trade.entry_price, "exit_time": trade.exit_time,
                         "exit_price": trade.exit_price, "quantity": trade.quantity,
                         "gross_pnl": trade.gross_pnl, "commission": 0, "fees": 0,
                         "slippage": 0, "net_pnl": trade.net_pnl}],
             "equity": [{"timestamp": p.timestamp, "value": p.equity} for p in actual.equity_curve],
             "drawdown": [{"timestamp": p.timestamp, "value": p.drawdown}
                          for p in analysis.drawdown_series],
             "statistics": {"final_equity": actual.final_equity}},
            {"trades": [expected_trade], "equity": expected["equity"],
             "drawdown": expected["drawdown"], "statistics": {"final_equity": expected["final_equity"]}},
            absolute_tolerance=1e-10,
        ) is None, f"seed={seed} case={case}"

        signed_movement = movement if side is Side.LONG else -movement
        assert abs(trade.gross_pnl - signed_movement * quantity) < 1e-10
        assert abs(trade.net_pnl - trade.gross_pnl) < 1e-10


def test_quantity_scaling_and_cost_direction_with_fixed_seed_path():
    # Same fills; 4 units must produce four times gross PnL and four times
    # per-unit commission. This checks the engine against independent formulas.
    timestamps = [datetime(2024, 2, 1) + timedelta(minutes=i) for i in range(5)]
    values = (9, 10, 11, 13, 13)
    rows, data = [], []
    for timestamp, price in zip(timestamps, values):
        rows.append({"time": timestamp, "open": price, "high": price,
                     "low": price, "close": price, "spread": 0.0})
        data.append(Bar(timestamp, price, price, price, price))
    one, four = [], []
    for qty in (1, 4):
        # Quantity is explicitly signaled, independently mirrored by the oracle.
        expected = calculate(rows, {0: ("open", "long", qty), 2: ("close", None, None)},
                             initial_cash=100, commission_per_unit=.25)
        class Sized:
            def on_bar(self, context):
                if context.index == 0:
                    return Signal(OrderAction.OPEN, Side.LONG, qty)
                if context.index == 2:
                    return Signal(OrderAction.CLOSE)
                return None
        actual = BacktestEngine(BacktestSettings(
            initial_cash=100, costs=ExecutionCosts(commission_per_unit=.25),
        )).run(data, Sized())
        assert abs(actual.trades[0].gross_pnl - expected["trades"][0]["gross_pnl"]) < 1e-10
        assert abs(actual.trades[0].net_pnl - expected["trades"][0]["net_pnl"]) < 1e-10
        (one if qty == 1 else four).append(actual.trades[0])
    assert four[0].gross_pnl == 4 * one[0].gross_pnl
    assert four[0].net_pnl == 4 * one[0].net_pnl
