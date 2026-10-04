from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.backtest import (
    BacktestEngine, BacktestSettings, Bar, ExecutionCosts, ExecutionEventType,
    OrderAction, OrderStatus, OrderType, Side, Signal,
)
from app.backtest.trace import first_event_difference
from tests.validation.reference_model import calculate_single_stop_trade


START = datetime(2025, 1, 1)


def bar(index, open_, high, low, close, spread=0.0):
    return Bar(START + timedelta(hours=4 * index), open_, high, low, close,
               spread=spread)


class Scripted:
    def __init__(self, signals):
        self.signals = signals

    def on_bar(self, context):
        return self.signals.get(context.index)


def opened_stop(side=Side.LONG, *, trigger=110, stop=103, qty=1, extra=()):
    bars = [bar(0, 100, 105, 98, 104), bar(1, 104, 109, 103, 108),
            *extra]
    result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
        bars, Scripted({1: Signal(OrderAction.OPEN, side, qty,
                                  order_type=OrderType.STOP,
                                  trigger_price=trigger, stop_loss=stop)}))
    return result, bars


@pytest.mark.parametrize(("side", "trigger", "ohlc", "expected"), [
    (Side.LONG, 110, (109, 113, 106, 112), 110),
    (Side.SHORT, 100, (101, 105, 97, 99), 100),
    (Side.LONG, 110, (115, 118, 114, 117), 115),
    (Side.SHORT, 100, (95, 97, 92, 94), 95),
])
def test_stop_entry_normal_and_gap_prices(side, trigger, ohlc, expected):
    first = bar(0, 100, 105, 98, 104)
    signal = Signal(OrderAction.OPEN, side, order_type=OrderType.STOP,
                    trigger_price=trigger, stop_loss=90 if side is Side.LONG else 120)
    result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
        [first, bar(1, *ohlc)], Scripted({0: signal}))
    order = result.orders[0]
    assert order.order_type is OrderType.STOP
    assert order.status is OrderStatus.FILLED
    assert order.trigger_price == trigger
    assert order.reference_price == expected
    assert order.fill_price == expected
    assert order.filled_at == START + timedelta(hours=4)
    assert result.open_position.entry_price == expected


@pytest.mark.parametrize(("side", "trigger", "ohlc"), [
    (Side.LONG, 110, (105, 109, 101, 108)),
    (Side.SHORT, 100, (105, 108, 101, 103)),
])
def test_stop_not_reached_remains_pending(side, trigger, ohlc):
    result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
        [bar(0, 100, 105, 98, 104), bar(1, *ohlc)],
        Scripted({0: Signal(OrderAction.OPEN, side, order_type=OrderType.STOP,
                            trigger_price=trigger)}))
    assert len(result.orders) == 1
    assert result.orders[0].status is OrderStatus.PENDING
    assert result.orders[0].filled_at is None
    assert result.open_position is None
    assert not result.trades
    assert not any(event.event_type in {
        ExecutionEventType.EXECUTION, ExecutionEventType.POSITION_OPENED,
        ExecutionEventType.TRADE_CREATED,
    } for event in result.execution_trace)


@pytest.mark.parametrize(("side", "trigger", "stop", "entry_bar", "exit_bar", "expected_exit"), [
    (Side.LONG, 110, 103, (109, 113, 106, 112), (105, 110, 101, 107), 103),
    (Side.SHORT, 100, 107, (101, 105, 97, 99), (105, 109, 103, 104), 107),
    (Side.LONG, 110, 103, (109, 113, 106, 112), (99, 105, 98, 100), 99),
    (Side.SHORT, 100, 107, (101, 105, 97, 99), (110, 112, 105, 108), 110),
])
def test_stop_loss_touch_and_gap(side, trigger, stop, entry_bar, exit_bar, expected_exit):
    signal = Signal(OrderAction.OPEN, side, order_type=OrderType.STOP,
                    trigger_price=trigger, stop_loss=stop)
    bars = [bar(0, 100, 105, 98, 104), bar(1, *entry_bar), bar(2, *exit_bar)]
    result = BacktestEngine(BacktestSettings(initial_cash=1_000,
                                             close_at_end=False)).run(
        bars, Scripted({0: signal}))
    trade = result.trades[0]
    assert trade.entry_price == trigger
    assert trade.exit_price == expected_exit
    assert trade.quantity == 1
    direction = 1 if side is Side.LONG else -1
    assert trade.gross_pnl == (expected_exit - trigger) * direction
    assert trade.net_pnl == trade.gross_pnl
    assert result.final_equity == 1_000 + trade.net_pnl
    close = next(event for event in result.execution_trace
                 if event.event_type is ExecutionEventType.POSITION_CLOSED)
    assert close.reason == "stop_loss"


@pytest.mark.parametrize(("side", "trigger", "stop", "entry_bar", "exit_bar"), [
    (Side.LONG, 110, 103, (109, 113, 103, 108), None),
    (Side.SHORT, 100, 107, (101, 107, 97, 102), None),
])
def test_trigger_equality_is_inclusive(side, trigger, stop, entry_bar, exit_bar):
    signal = Signal(OrderAction.OPEN, side, order_type=OrderType.STOP,
                    trigger_price=trigger, stop_loss=stop)
    bars = [bar(0, 100, 105, 98, 104), bar(1, *entry_bar)]
    result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
        bars, Scripted({0: signal}))
    assert result.open_position is not None
    assert result.orders[0].fill_price == trigger


@pytest.mark.parametrize(("side", "stop", "ohlc", "expected"), [
    (Side.LONG, 103, (103, 105, 103, 104), 103),
    (Side.SHORT, 107, (107, 109, 105, 106), 107),
])
def test_stop_loss_equality_is_inclusive(side, stop, ohlc, expected):
    # Market entry is unchanged: signal bar 0 fills at bar 1 open.
    result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
        [bar(0, 100, 105, 98, 104), bar(1, 100, 105, 98, 104), bar(2, *ohlc)],
        Scripted({0: Signal(OrderAction.OPEN, side, stop_loss=stop)}))
    assert result.trades[0].exit_price == expected


def test_pending_stop_does_not_trigger_on_creation_bar_but_can_on_next_bar():
    result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
        [bar(0, 109, 112, 106, 111), bar(1, 109, 113, 106, 112)],
        Scripted({0: Signal(OrderAction.OPEN, Side.LONG,
                            order_type=OrderType.STOP, trigger_price=110)}))
    assert result.orders[0].filled_at == START + timedelta(hours=4)
    assert result.open_position.entry_time == START + timedelta(hours=4)


def test_new_position_stop_is_not_applied_to_ambiguous_entry_bar():
    result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
        [bar(0, 100, 105, 98, 104), bar(1, 109, 113, 101, 108)],
        Scripted({0: Signal(OrderAction.OPEN, Side.LONG,
                            order_type=OrderType.STOP, trigger_price=110,
                            stop_loss=103)}))
    assert result.open_position is not None
    assert result.open_position.stop_loss == 103
    assert not result.trades


@pytest.mark.parametrize(("side", "old", "new"), [
    (Side.LONG, 95, 97), (Side.SHORT, 115, 113), (Side.LONG, 97, 97),
])
def test_stop_can_be_modified_explicitly(side, old, new):
    result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
        [bar(0, 100, 105, 98, 104), bar(1, 100, 105, 98, 104),
         bar(2, 100, 105, 98, 104)],
        Scripted({0: Signal(OrderAction.OPEN, side, stop_loss=old),
                  1: Signal(OrderAction.MODIFY_STOP, stop_loss=new)}))
    assert result.open_position.stop_loss == new
    updated = next(event for event in result.execution_trace
                   if event.event_type is ExecutionEventType.STOP_UPDATED)
    assert dict(updated.details)["previous_stop_loss"] == old
    assert updated.stop_loss == new


def test_stop_modification_without_position_is_rejected():
    with pytest.raises(ValueError, match="without an open position"):
        BacktestEngine().run([bar(0, 100, 105, 98, 104)],
                             Scripted({0: Signal(OrderAction.MODIFY_STOP,
                                                stop_loss=95)}))


def test_second_pending_stop_is_rejected():
    signals = {
        0: Signal(OrderAction.OPEN, Side.LONG, order_type=OrderType.STOP,
                  trigger_price=110),
        1: Signal(OrderAction.OPEN, Side.SHORT, order_type=OrderType.STOP,
                  trigger_price=90),
    }
    with pytest.raises(ValueError, match="Only one pending STOP"):
        BacktestEngine(BacktestSettings(close_at_end=False)).run(
            [bar(0, 100, 105, 98, 104), bar(1, 100, 105, 98, 104),
             bar(2, 100, 105, 98, 104)], Scripted(signals))


def test_market_entry_is_rejected_while_stop_entry_is_pending():
    signals = {
        0: Signal(OrderAction.OPEN, Side.LONG, order_type=OrderType.STOP,
                  trigger_price=110),
        1: Signal(OrderAction.OPEN, Side.SHORT),
    }
    with pytest.raises(ValueError, match="market entry while a STOP entry is pending"):
        BacktestEngine(BacktestSettings(close_at_end=False)).run(
            [bar(0, 100, 105, 98, 104), bar(1, 100, 105, 98, 104),
             bar(2, 100, 105, 98, 104)], Scripted(signals))


def test_market_order_still_fills_at_next_bar_open():
    result = BacktestEngine(BacktestSettings(close_at_end=False)).run(
        [bar(0, 100, 105, 98, 104), bar(1, 107, 110, 106, 109)],
        Scripted({0: Signal(OrderAction.OPEN, Side.LONG)}))
    assert result.orders[0].order_type is OrderType.MARKET
    assert result.orders[0].fill_price == 107
    assert result.open_position.entry_time == START + timedelta(hours=4)


def test_stop_trace_trade_and_independent_accounting_reconcile():
    rows = [
        {"time": START, "open": 100, "high": 105, "low": 98, "close": 104, "spread": 0.2},
        {"time": START + timedelta(hours=4), "open": 104, "high": 110, "low": 103, "close": 109, "spread": 0.2},
        {"time": START + timedelta(hours=8), "open": 109, "high": 113, "low": 106, "close": 112, "spread": 0.2},
        {"time": START + timedelta(hours=12), "open": 112, "high": 114, "low": 107, "close": 108, "spread": 0.2},
        {"time": START + timedelta(hours=16), "open": 108, "high": 109, "low": 101, "close": 102, "spread": 0.2},
    ]
    bars = [Bar(row["time"], row["open"], row["high"], row["low"],
                row["close"], spread=row["spread"]) for row in rows]
    # Modify the position's stop after entry; execution is eligible from next bar.
    strategy = Scripted({
        1: Signal(OrderAction.OPEN, Side.LONG, order_type=OrderType.STOP,
                  trigger_price=110, stop_loss=103),
        3: Signal(OrderAction.MODIFY_STOP, stop_loss=106),
    })
    result = BacktestEngine(BacktestSettings(
        initial_cash=1_000, close_at_end=False,
        costs=ExecutionCosts(commission_per_unit=0.25, slippage=0.1),
    )).run(bars, strategy)
    expected = calculate_single_stop_trade(
        rows, signal_index=1, side="long", trigger=110, stop_loss=106,
        initial_cash=1_000, commission_per_unit=0.25, slippage=0.1)
    trade = result.trades[0]
    assert (trade.entry_time, trade.entry_price) == (expected["entry"]["time"], expected["entry"]["price"])
    assert (trade.exit_time, trade.exit_price) == (expected["exit"]["time"], expected["exit"]["price"])
    assert trade.quantity == expected["trade"]["quantity"]
    assert trade.gross_pnl == pytest.approx(expected["trade"]["gross_pnl"])
    assert trade.net_pnl == pytest.approx(expected["trade"]["net_pnl"])
    assert result.total_commission == pytest.approx(
        expected["entry"]["commission"] + expected["exit"]["commission"])
    assert result.total_spread_cost == pytest.approx(
        expected["entry"]["spread_cost"] + expected["exit"]["spread_cost"])
    assert result.total_slippage_cost == pytest.approx(
        expected["entry"]["slippage_cost"] + expected["exit"]["slippage_cost"])
    assert result.final_equity == pytest.approx(expected["final_equity"])
    assert result.equity_curve[-1].equity == pytest.approx(expected["final_equity"])
    assert [point.equity for point in result.equity_curve] == pytest.approx(expected["equity"])
    peak = None
    actual_drawdown = []
    for point in result.equity_curve:
        peak = point.equity if peak is None else max(peak, point.equity)
        actual_drawdown.append(peak - point.equity)
    assert actual_drawdown == pytest.approx(expected["drawdown"])
    lifecycle_types = [event.event_type for event in result.execution_trace]
    assert lifecycle_types.index(ExecutionEventType.ORDER_PENDING) < lifecycle_types.index(ExecutionEventType.ORDER_TRIGGERED)
    assert lifecycle_types.index(ExecutionEventType.STOP_UPDATED) < lifecycle_types.index(ExecutionEventType.POSITION_CLOSED)
    assert lifecycle_types[-1] is ExecutionEventType.TRADE_CREATED
    trace_entry_execution = next(index for index, event in enumerate(result.execution_trace)
                                 if event.event_type is ExecutionEventType.EXECUTION)
    changed = list(result.execution_trace)
    changed[trace_entry_execution] = replace(
        changed[trace_entry_execution], price=changed[trace_entry_execution].price + 1)
    divergence = first_event_difference(tuple(changed), result.execution_trace)
    assert divergence.event_number == trace_entry_execution + 1
    assert divergence.field == "price"
    assert "FIRST DIVERGENCE" in str(divergence)
    assert "trigger_price=110" in result.format_execution_trace()


def test_stop_trace_round_trip_preserves_new_enum_and_fields():
    from app.research.serialization import decode, encode

    result, _ = opened_stop(extra=(bar(2, 115, 118, 114, 117),))
    assert decode(encode(result.execution_trace)) == result.execution_trace


def test_random_stop_trigger_properties_are_deterministic():
    import random

    rng = random.Random(44017)
    for case in range(40):
        side = Side.LONG if rng.randrange(2) == 0 else Side.SHORT
        trigger = rng.uniform(80, 120)
        hit = bool(rng.randrange(2))
        if side is Side.LONG:
            open_ = trigger - rng.uniform(0.1, 2)
            high = trigger + rng.uniform(0, 3) if hit else max(open_, trigger - rng.uniform(0.01, 2))
            low = open_ - 1
            close = open_
            reference = trigger if hit else None
        else:
            open_ = trigger + rng.uniform(0.1, 2)
            low = trigger - rng.uniform(0, 3) if hit else min(open_, trigger + rng.uniform(0.01, 2))
            high = open_ + 1
            close = open_
            reference = trigger if hit else None
        outcome = BacktestEngine(BacktestSettings(close_at_end=False)).run(
            [bar(0, 100, 105, 98, 104), bar(1, open_, high, low, close)],
            Scripted({0: Signal(OrderAction.OPEN, side, order_type=OrderType.STOP,
                                trigger_price=trigger)}))
        assert (outcome.orders[0].status is OrderStatus.FILLED) is hit, f"seed=44017 case={case}"
        if hit:
            assert outcome.orders[0].reference_price == pytest.approx(reference), f"seed=44017 case={case}"
