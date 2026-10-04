from datetime import datetime, timedelta

import pytest

from app.backtest import (
    BacktestEngine, BacktestSettings, Bar, ExecutionEventType, OrderAction,
    OrderType, Side, Signal,
)
from app.strategies import get_strategy_factory
from app.strategies.h4_breakout import H4Breakout
from tests.validation.reference_h4_breakout import run as reference_run


START = datetime(2025, 1, 1)
BUY_SETUP = ((100, 105, 98, 104), (104, 110, 103, 109))
BUY_LIFECYCLE = (
    (100, 105, 98, 104), (104, 110, 103, 109), (109, 113, 106, 112),
    (112, 114, 107, 108), (108, 109, 101, 102),
)
SELL_LIFECYCLE = (
    (100, 102, 95, 96), (96, 97, 90, 91), (91, 94, 87, 89),
    (89, 94, 88, 90), (90, 96, 88, 95),
)


def bars(rows):
    return tuple(Bar(START + timedelta(hours=4 * index), *row)
                 for index, row in enumerate(rows))


def run(rows):
    return BacktestEngine(BacktestSettings(initial_cash=1_000,
                                            close_at_end=False)).run(
        bars(rows), H4Breakout())


def strategy_signals(result):
    return tuple(event for event in result.execution_trace
                 if event.event_type is ExecutionEventType.SIGNAL)


def test_h4_breakout_is_registered_and_has_no_parameters():
    factory = get_strategy_factory("breakout.h4_two_candle")
    assert factory.strategy_version == "1.0.0"
    assert factory.create({}).__class__ is H4Breakout
    with pytest.raises(ValueError, match="has no parameters"):
        factory.validate({"unused": 1})


def test_known_answer_buy_setup_stop_trailing_exit_and_trace():
    result = run(BUY_LIFECYCLE)
    signals = strategy_signals(result)
    buy = next(event for event in signals if event.side is Side.LONG)
    assert buy.bar_index == 1
    assert buy.order_type is OrderType.STOP
    assert buy.trigger_price == 110
    assert buy.stop_loss == 103

    entry_order = result.orders[0]
    assert entry_order.action is OrderAction.OPEN
    assert entry_order.order_type is OrderType.STOP
    assert entry_order.trigger_price == 110
    assert entry_order.stop_loss == 103
    assert entry_order.created_at == bars(BUY_LIFECYCLE)[1].timestamp
    assert entry_order.filled_at == bars(BUY_LIFECYCLE)[2].timestamp
    assert entry_order.fill_price == 110
    assert result.execution_trace[next(i for i, event in enumerate(result.execution_trace)
                                       if event.event_type is ExecutionEventType.POSITION_OPENED)].stop_loss == 103

    update = next(event for event in result.execution_trace
                  if event.event_type is ExecutionEventType.STOP_UPDATED)
    assert update.bar_index == 3
    assert update.stop_loss == 106
    assert dict(update.details)["previous_stop_loss"] == 103

    trade = result.trades[0]
    assert (trade.side, trade.quantity) == (Side.LONG, 1)
    assert (trade.entry_time, trade.entry_price) == (bars(BUY_LIFECYCLE)[2].timestamp, 110)
    assert (trade.exit_time, trade.exit_price) == (bars(BUY_LIFECYCLE)[4].timestamp, 106)
    assert trade.gross_pnl == -4
    assert trade.net_pnl == -4
    assert result.final_equity == 996
    exit_order = result.orders[1]
    assert (exit_order.action, exit_order.order_type, exit_order.trigger_price,
            exit_order.fill_price) == (OrderAction.CLOSE, OrderType.STOP, 106, 106)

    relevant = [event.event_type for event in result.execution_trace
                if event.event_type in {
                    ExecutionEventType.SIGNAL, ExecutionEventType.ORDER_CREATED,
                    ExecutionEventType.ORDER_PENDING, ExecutionEventType.ORDER_TRIGGERED,
                    ExecutionEventType.EXECUTION, ExecutionEventType.POSITION_OPENED,
                    ExecutionEventType.STOP_UPDATED, ExecutionEventType.POSITION_CLOSED,
                    ExecutionEventType.PNL_CALCULATED, ExecutionEventType.TRADE_CREATED,
                }]
    assert relevant[:9] == [
        ExecutionEventType.SIGNAL, ExecutionEventType.ORDER_CREATED,
        ExecutionEventType.ORDER_PENDING, ExecutionEventType.ORDER_TRIGGERED,
        ExecutionEventType.EXECUTION, ExecutionEventType.POSITION_OPENED,
        ExecutionEventType.SIGNAL, ExecutionEventType.STOP_UPDATED,
        ExecutionEventType.ORDER_CREATED,
    ]
    assert relevant[9:] == [ExecutionEventType.ORDER_TRIGGERED,
                            ExecutionEventType.EXECUTION,
                            ExecutionEventType.POSITION_CLOSED,
                            ExecutionEventType.PNL_CALCULATED,
                            ExecutionEventType.TRADE_CREATED,
                            ExecutionEventType.SIGNAL]
    # C4 + C5 independently form a separate SELL setup on the final bar.
    # It is visible as a signal but has no next bar on which to create an order.
    assert signals[-1].side is Side.SHORT and signals[-1].bar_index == 4


def test_independent_reference_agrees_with_strategy_execution_and_trade():
    primitive = [{"time": bar.timestamp, "open": bar.open, "high": bar.high,
                  "low": bar.low, "close": bar.close}
                 for bar in bars(BUY_LIFECYCLE)]
    expected = reference_run(primitive)
    actual = run(BUY_LIFECYCLE)
    assert expected["setups"][0] == {
        "index": 1, "first_normal": True, "second_normal": True,
        "first_direction": True, "second_direction": True, "breakout": True,
        "side": "long", "valid": True, "trigger": 110, "stop_loss": 103,
    }
    assert expected["signals"][0] == {
        "index": 1, "side": "long", "trigger": 110, "stop_loss": 103,
    }
    assert expected["entry"] == {
        "index": 2, "time": primitive[2]["time"], "reference_price": 110,
        "side": "long", "quantity": 1.0, "initial_stop_loss": 103,
        "trigger": 110,
    }
    assert expected["stop_updates"] == [{"index": 3, "old": 103, "new": 106}]
    assert expected["exit"] == {
        "index": 4, "time": primitive[4]["time"], "reference_price": 106,
        "stop_loss": 106, "reason": "stop_loss",
    }
    assert expected["trade"]["gross_pnl"] == -4
    assert expected["trade"]["final_equity"] == 996

    trade = actual.trades[0]
    assert trade.side.value == expected["trade"]["side"]
    assert trade.quantity == expected["trade"]["quantity"]
    assert trade.entry_time == expected["trade"]["entry_time"]
    assert trade.entry_price == expected["trade"]["entry_price"]
    assert trade.exit_time == expected["trade"]["exit_time"]
    assert trade.exit_price == expected["trade"]["exit_price"]
    assert trade.gross_pnl == expected["trade"]["gross_pnl"]
    assert trade.net_pnl == expected["trade"]["net_pnl"]
    assert actual.final_equity == expected["trade"]["final_equity"]

    position_opened = next(event for event in actual.execution_trace
                           if event.event_type is ExecutionEventType.POSITION_OPENED)
    closed = next(event for event in actual.execution_trace
                  if event.event_type is ExecutionEventType.POSITION_CLOSED)
    pnl = next(event for event in actual.execution_trace
               if event.event_type is ExecutionEventType.PNL_CALCULATED)
    assert (position_opened.side.value, position_opened.price,
            position_opened.quantity, position_opened.stop_loss) == (
                expected["entry"]["side"], expected["entry"]["reference_price"], 1, 103)
    assert (closed.price, closed.reason) == (expected["exit"]["reference_price"], "stop_loss")
    assert dict(pnl.details)["net_pnl"] == expected["trade"]["net_pnl"]


def test_sell_mirror_uses_low_entry_high_initial_and_prior_high_trail():
    result = run(SELL_LIFECYCLE)
    primitive = [{"time": item.timestamp, "open": item.open, "high": item.high,
                  "low": item.low, "close": item.close}
                 for item in bars(SELL_LIFECYCLE)]
    expected = reference_run(primitive)
    sell = next(event for event in strategy_signals(result)
                if event.side is Side.SHORT)
    assert sell.bar_index == 1
    assert sell.order_type is OrderType.STOP
    assert sell.trigger_price == 90
    assert sell.stop_loss == 97
    assert result.orders[0].fill_price == 90
    assert result.orders[0].stop_loss == 97
    update = next(event for event in result.execution_trace
                  if event.event_type is ExecutionEventType.STOP_UPDATED)
    assert update.stop_loss == 94  # Previous completed candle C3.high.
    trade = result.trades[0]
    assert (trade.side, trade.entry_price, trade.exit_price,
            trade.gross_pnl, trade.net_pnl) == (Side.SHORT, 90, 94, -4, -4)
    assert expected["signals"][0] == {
        "index": 1, "side": "short", "trigger": 90, "stop_loss": 97,
    }
    assert expected["stop_updates"] == [{"index": 3, "old": 97, "new": 94}]
    assert expected["trade"]["exit_price"] == trade.exit_price
    assert expected["trade"]["net_pnl"] == trade.net_pnl


@pytest.mark.parametrize("rows", [
    # First candle not bullish.
    ((104, 105, 98, 100), (100, 110, 99, 109)),
    # Second candle not bullish.
    ((100, 105, 98, 104), (109, 110, 103, 104)),
    # First body ratio below one half.
    ((100, 105, 98, 102), (102, 110, 101, 109)),
    # Second body ratio below one half.
    ((100, 105, 98, 104), (104, 110, 103, 107)),
    # Second close exactly equal to or strictly below first high; both second
    # candles have a normal body so the breakout boundary is isolated.
    ((100, 105, 98, 104), (90, 110, 89, 105)),
    ((100, 105, 98, 104), (90, 110, 89, 104)),
    # Zero range in either candle.
    ((100, 100, 100, 100), (100, 110, 99, 109)),
    ((100, 105, 98, 104), (104, 104, 104, 104)),
])
def test_invalid_buy_setup_conditions_do_not_signal(rows):
    result = run(rows)
    assert not strategy_signals(result)
    assert not result.orders


@pytest.mark.parametrize("rows", [
    # First candle not bearish.
    ((96, 102, 95, 100), (100, 101, 90, 91)),
    # Second candle not bearish.
    ((100, 102, 95, 96), (91, 97, 90, 92)),
    # First body ratio below one half.
    ((100, 102, 95, 98), (98, 99, 90, 91)),
    # Second body ratio below one half.
    ((100, 102, 95, 96), (96, 97, 90, 93)),
    # Second close exactly equal to or strictly above first low; both second
    # candles have a normal body so the breakout boundary is isolated.
    ((100, 102, 95, 96), (101, 103, 91, 95)),
    ((100, 102, 95, 96), (105, 106, 90, 97)),
    # Zero range in either candle.
    ((100, 100, 100, 100), (100, 101, 90, 91)),
    ((100, 102, 95, 96), (95, 95, 95, 95)),
])
def test_invalid_sell_setup_conditions_do_not_signal(rows):
    result = run(rows)
    assert not strategy_signals(result)
    assert not result.orders


@pytest.mark.parametrize("rows", [
    # Both candles have body/range exactly 0.50; setup is valid.
    ((100, 106, 98, 104), (104, 112, 100, 110)),
    # Second close strictly beyond the first high, with a BUY STOP later hit
    # exactly at its trigger price.
    ((100, 105, 98, 104), (104, 110, 103, 109)),
])
def test_body_ratio_boundary_and_buy_stop_equality(rows):
    third = (109, 110, 107, 109) if rows[1][1] == 110 else (110, 112, 104, 111)
    result = run((*rows, third))
    signal = next(event for event in strategy_signals(result)
                  if event.side is Side.LONG)
    assert signal is not None
    assert result.orders[0].fill_price == signal.trigger_price


def test_sell_close_equal_to_first_low_is_invalid_and_sell_stop_equality_fills():
    equal_close = ((100, 102, 95, 96), (101, 103, 91, 95))
    assert not strategy_signals(run(equal_close))
    valid = (*SELL_LIFECYCLE[:2], (91, 94, 90, 92))
    result = run(valid)
    assert result.orders[0].trigger_price == 90
    assert result.orders[0].fill_price == 90  # C3.low exactly equals SELL STOP.


def test_setup_is_emitted_once_and_pending_order_is_respected():
    rows = (*BUY_SETUP, (107, 109, 106, 108), (108, 109, 107, 108))
    result = run(rows)
    long_signals = [event for event in strategy_signals(result)
                    if event.side is Side.LONG]
    assert len(long_signals) == 1
    assert len(result.orders) == 1
    assert result.orders[0].status.value == "pending"
    pending_context = H4Breakout()
    second = bars(BUY_SETUP)[1]
    from app.backtest.strategy import StrategyContext
    context = StrategyContext(1, bars(BUY_SETUP), second,
                              pending_order=result.orders[0])
    assert pending_context.on_bar(context) is None


def test_no_position_means_no_trailing_stop_update():
    result = run(((100, 102, 99, 101), (101, 103, 100, 102),
                  (102, 104, 101, 103)))
    assert not any(event.event_type is ExecutionEventType.STOP_UPDATED
                   for event in result.execution_trace)


def test_initial_stop_is_not_applied_on_entry_bar_and_trailing_waits_until_next_bar():
    rows = (*BUY_SETUP, (109, 113, 101, 108), (108, 112, 104, 110))
    result = run(rows)
    assert result.trades == ()
    assert result.open_position is not None
    assert result.open_position.stop_loss == 101  # Prior completed entry candle low.
    updates = [event for event in result.execution_trace
               if event.event_type is ExecutionEventType.STOP_UPDATED]
    assert len(updates) == 1
    assert updates[0].bar_index == 3
    # The entry bar itself generated no modification, and its low below 103 did
    # not cause the initial stop to execute on that ambiguous bar.
    assert not any(event.event_type is ExecutionEventType.STOP_UPDATED
                   and event.bar_index == 2 for event in result.execution_trace)


def test_prefix_invariance_of_setup_order_and_earlier_execution():
    prefix = BUY_LIFECYCLE[:3]
    future_one = (*prefix, *BUY_LIFECYCLE[3:])
    future_two = (*prefix, (112, 140, 111, 139), (139, 141, 130, 132))
    first, second = run(future_one), run(future_two)
    prefix_end = bars(prefix)[-1].timestamp
    first_events = tuple(event for event in first.execution_trace
                         if event.timestamp <= prefix_end)
    second_events = tuple(event for event in second.execution_trace
                          if event.timestamp <= prefix_end)
    assert first_events == second_events
    assert first.orders[0] == second.orders[0]
    first_entry = next(event for event in first_events
                       if event.event_type is ExecutionEventType.POSITION_OPENED)
    second_entry = next(event for event in second_events
                        if event.event_type is ExecutionEventType.POSITION_OPENED)
    assert first_entry == second_entry
    assert first_entry.price == 110


def test_strategy_result_is_deterministic():
    one = run(BUY_LIFECYCLE)
    two = run(BUY_LIFECYCLE)
    assert one.execution_trace == two.execution_trace
    assert one.orders == two.orders
    assert one.trades == two.trades
    assert one.equity_curve == two.equity_curve
