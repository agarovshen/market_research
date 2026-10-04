from dataclasses import replace

import pytest

from app.backtest import (
    BacktestEngine, BacktestSettings, ExecutionEventType, OrderAction, Side,
    Signal, first_event_difference,
)
from tests.validation.support import bars


class Scripted:
    def __init__(self, instructions=()):
        self.instructions = dict(instructions)

    def on_bar(self, context):
        return self.instructions.get(context.index)


def known_result():
    # Existing known-answer fixture: signal at bar 0, entry at bar 1 open=110;
    # close signal at bar 2, exit at bar 3 open=125; canonical PnL is +15.
    return BacktestEngine(BacktestSettings(initial_cash=1_000)).run(
        bars(100, 110, 120, 125),
        Scripted({0: Signal(OrderAction.OPEN, Side.LONG),
                  2: Signal(OrderAction.CLOSE)}),
    )


def test_simple_market_lifecycle_has_explicit_ordered_events_and_canonical_values():
    result = known_result()
    events = result.execution_trace
    assert [event.event_type for event in events] == [
        ExecutionEventType.BAR,
        ExecutionEventType.SIGNAL,
        ExecutionEventType.ORDER_CREATED,
        ExecutionEventType.BAR,
        ExecutionEventType.EXECUTION,
        ExecutionEventType.POSITION_OPENED,
        ExecutionEventType.BAR,
        ExecutionEventType.SIGNAL,
        ExecutionEventType.ORDER_CREATED,
        ExecutionEventType.BAR,
        ExecutionEventType.EXECUTION,
        ExecutionEventType.POSITION_CLOSED,
        ExecutionEventType.PNL_CALCULATED,
        ExecutionEventType.TRADE_CREATED,
    ]
    opened = events[5]
    assert (opened.timestamp, opened.side, opened.order_id, opened.trade_id,
            opened.price, opened.quantity) == (
        bars(0, 110)[1].timestamp, Side.LONG, 1, 1, 110, 1)
    assert events[2].timestamp == bars(100)[0].timestamp
    assert events[2].reason == "market_signal_next_bar_open"
    assert events[4].price == 110
    assert events[8].action is OrderAction.CLOSE
    assert events[10].price == 125
    assert events[11].reason == "strategy_close_signal"
    assert dict(events[12].details)["net_pnl"] == 15
    assert result.trades[0].net_pnl == 15
    assert result.execution_trace == tuple(replace(event, sequence=index)
                                           for index, event in enumerate(events, 1))
    assert "strategy_reason=not exposed by Signal" in result.format_execution_trace()
    lifecycle = result.format_trade_lifecycle(1)
    assert "Trade #1 lifecycle" in lifecycle
    assert "entry_price=110" in lifecycle and "exit_price=125" in lifecycle
    assert "net_pnl=15" in lifecycle


def test_pending_market_order_is_created_at_signal_then_executes_next_bar():
    result = known_result()
    created = next(event for event in result.execution_trace
                   if event.event_type is ExecutionEventType.ORDER_CREATED)
    execution = next(event for event in result.execution_trace
                     if event.event_type is ExecutionEventType.EXECUTION)
    assert created.timestamp < execution.timestamp
    assert (created.order_id, execution.order_id) == (1, 1)
    assert (created.trade_id, execution.trade_id) == (1, 1)
    assert (created.quantity, execution.quantity, execution.price) == (1, 1, 110)
    # The engine has no trigger/active/cancelled market-order states; trace does
    # not fabricate ORDER_TRIGGERED or ORDER_CANCELLED events.
    assert not any(event.event_type.value in {"order_triggered", "order_cancelled"}
                   for event in result.execution_trace)


def test_final_liquidation_has_explicit_reason_and_canonical_exit_price():
    result = BacktestEngine().run(
        bars(10, 11, 13), Scripted({0: Signal(OrderAction.OPEN, Side.LONG)}))
    closed = next(event for event in result.execution_trace
                  if event.event_type is ExecutionEventType.POSITION_CLOSED)
    exit_execution = next(event for event in result.execution_trace
                          if event.event_type is ExecutionEventType.EXECUTION
                          and event.action is OrderAction.CLOSE)
    assert closed.reason == "end_of_data_liquidation"
    assert exit_execution.reason == "end_of_data_liquidation"
    assert result.trades[0].exit_price == 13
    assert closed.price == result.trades[0].exit_price


def test_no_trade_trace_contains_bars_only_and_no_lifecycle_events():
    result = BacktestEngine().run(bars(10, 11, 9), Scripted())
    assert [event.event_type for event in result.execution_trace] == [
        ExecutionEventType.BAR, ExecutionEventType.BAR, ExecutionEventType.BAR,
    ]
    assert result.trades == ()
    with pytest.raises(ValueError, match="No completed trade"):
        result.format_trade_lifecycle(1)


def test_multiple_trade_lifecycles_are_correlated_by_trade_and_order_sequence():
    result = BacktestEngine(BacktestSettings(initial_cash=1_000)).run(
        bars(100, 110, 115, 106, 103, 105),
        Scripted({0: Signal(OrderAction.OPEN, Side.LONG),
                  1: Signal(OrderAction.CLOSE),
                  2: Signal(OrderAction.OPEN, Side.LONG),
                  3: Signal(OrderAction.CLOSE)}),
    )
    assert [trade.net_pnl for trade in result.trades] == [5, -3]
    trade_1 = tuple(event for event in result.execution_trace if event.trade_id == 1)
    trade_2 = tuple(event for event in result.execution_trace if event.trade_id == 2)
    assert trade_1 and trade_2
    assert {event.order_id for event in trade_1 if event.order_id is not None} == {1, 2}
    assert {event.order_id for event in trade_2 if event.order_id is not None} == {3, 4}
    assert "gross_pnl=5" in result.format_trade_lifecycle(1)
    assert "gross_pnl=-3" in result.format_trade_lifecycle(2)


def test_trace_reconciles_all_canonical_completed_trade_fields():
    result = known_result()
    for trade in result.trades:
        lifecycle = tuple(event for event in result.execution_trace
                          if event.trade_id == trade.sequence)
        opened = next(event for event in lifecycle
                      if event.event_type is ExecutionEventType.POSITION_OPENED)
        closed = next(event for event in lifecycle
                      if event.event_type is ExecutionEventType.POSITION_CLOSED)
        pnl = next(event for event in lifecycle
                   if event.event_type is ExecutionEventType.PNL_CALCULATED)
        assert (opened.side, opened.timestamp, opened.price, opened.quantity) == (
            trade.side, trade.entry_time, trade.entry_price, trade.quantity)
        assert (closed.side, closed.timestamp, closed.price, closed.quantity) == (
            trade.side, trade.exit_time, trade.exit_price, trade.quantity)
        assert dict(pnl.details)["gross_pnl"] == trade.gross_pnl
        assert dict(pnl.details)["net_pnl"] == trade.net_pnl


def test_trace_is_deterministic_and_reports_first_event_field_difference():
    first, second = known_result(), known_result()
    assert first.execution_trace == second.execution_trace
    mismatch_index = next(index for index, event in enumerate(second.execution_trace)
                          if event.event_type is ExecutionEventType.EXECUTION)
    altered = list(second.execution_trace)
    altered[mismatch_index] = replace(altered[mismatch_index], price=111)
    difference = first_event_difference(first.execution_trace, tuple(altered))
    assert difference.event_number == mismatch_index + 1
    assert difference.field == "price"
    assert difference.expected == 110
    assert difference.actual == 111
    assert "FIRST DIVERGENCE" in str(difference)
    assert "field: price" in str(difference)
