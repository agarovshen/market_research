from datetime import datetime, timedelta

import pytest
from fastapi.encoders import jsonable_encoder

from app.analysis import AnalysisEngine
from app.backtest import (
    BacktestEngine, BacktestRunConfig, BacktestRunner, BacktestSettings,
    Bar, OrderAction, Side, Signal,
)
from app.research.api import ResearchRunRequest
from app.research.serialization import decode, encode
from app.strategies.h4_breakout import H4Breakout


START = datetime(2025, 1, 1)


class Script:
    def __init__(self, signals):
        self.signals = signals

    def on_bar(self, context):
        return self.signals.get(context.index)


class StaticRepository:
    def __init__(self, bars):
        self.bars = bars

    def load(self, *args, **kwargs):
        return self.bars


def flat_bars(*prices):
    return tuple(Bar(START + timedelta(hours=i), price, price, price, price)
                 for i, price in enumerate(prices))


def completed_long(leverage=30, initial_cash=1000):
    bars = flat_bars(1.3000, 1.3000, 1.3050, 1.3050)
    signals = {0: Signal(OrderAction.OPEN, Side.LONG),
               1: Signal(OrderAction.CLOSE)}
    return BacktestRunner(StaticRepository(bars)).run(
        strategy=Script(signals),
        config=BacktestRunConfig(symbol="GBPUSD", initial_cash=initial_cash,
                                 lots=0.01, contract_size=100_000,
                                 leverage=leverage),
    )


def test_lots_units_notional_margin_and_pnl_are_distinct_from_leverage():
    baseline = completed_long(30)
    lower_leverage = completed_long(20)
    still_lower_leverage = completed_long(10)

    order = baseline.orders[0]
    assert order.lots == pytest.approx(0.01)
    assert order.units == order.quantity == 1000
    assert order.contract_size == 100_000
    assert order.leverage == 30
    assert order.notional == pytest.approx(1300)
    assert order.margin == pytest.approx(1300 / 30)
    assert baseline.trades[0].gross_pnl == pytest.approx(5)
    assert baseline.trades[0].net_pnl == pytest.approx(5)
    assert baseline.trades[0].quantity == baseline.trades[0].units == 1000
    assert baseline.trades[0].lots == pytest.approx(0.01)
    assert [result.trades[0].net_pnl for result in
            (baseline, lower_leverage, still_lower_leverage)] == pytest.approx([5, 5, 5])
    assert [result.orders[0].margin for result in
            (baseline, lower_leverage, still_lower_leverage)] == pytest.approx([1300/30, 65, 130])
    wire = jsonable_encoder(baseline)
    assert wire["lots"] == pytest.approx(0.01)
    assert wire["orders"][0]["units"] == 1000
    assert wire["orders"][0]["notional"] == pytest.approx(1300)
    assert wire["orders"][0]["margin"] == pytest.approx(1300 / 30)
    assert wire["equity_curve"][1]["balance"] == pytest.approx(1000)
    assert wire["equity_curve"][1]["free_margin"] == pytest.approx(1000 - 1300 / 30)
    restored = decode(encode(baseline))
    assert restored.trades == baseline.trades
    assert restored.equity_curve == baseline.equity_curve
    assert restored.lots == pytest.approx(0.01)
    request = ResearchRunRequest(
        mode="single", strategy_id="breakout.h4_two_candle", symbol="GBPUSD",
        timeframe="H4", start="2025-01-01T00:00:00", end="2025-01-02T00:00:00",
        initial_cash=1000, lots=0.01, contract_size=100000, leverage=30,
    )
    assert (request.lots, request.contract_size, request.leverage) == (0.01, 100000, 30)


def test_short_forex_pnl_is_units_times_price_move_not_leverage():
    bars = flat_bars(1.3050, 1.3050, 1.3000, 1.3000)
    result = BacktestEngine(BacktestSettings(
        initial_cash=1000, lots=0.01, contract_size=100_000, leverage=30,
    )).run(bars, Script({0: Signal(OrderAction.OPEN, Side.SHORT),
                         1: Signal(OrderAction.CLOSE)}))
    assert result.trades[0].gross_pnl == pytest.approx(5)
    assert result.balance == pytest.approx(1005)


def test_forex_balance_equity_and_margin_snapshots_use_account_semantics():
    result = completed_long()
    open_point = result.equity_curve[1]
    assert open_point.balance == pytest.approx(1000)
    assert open_point.cash == pytest.approx(1000)
    assert open_point.equity == pytest.approx(1000)
    assert open_point.margin_used == pytest.approx(1300 / 30)
    assert open_point.free_margin == pytest.approx(1000 - 1300 / 30)
    assert open_point.margin_level == pytest.approx(1000 / (1300 / 30) * 100)
    assert result.balance == pytest.approx(1005)
    assert result.final_cash == pytest.approx(1005)
    assert result.final_equity == pytest.approx(1005)
    assert result.margin_used == 0
    assert result.free_margin == pytest.approx(1005)
    assert result.margin_level is None

    losing = BacktestEngine(BacktestSettings(
        initial_cash=1000, lots=0.01, contract_size=100_000, leverage=30,
    )).run(flat_bars(1.3000, 1.3000, 1.2950, 1.2950),
           Script({0: Signal(OrderAction.OPEN, Side.LONG),
                   1: Signal(OrderAction.CLOSE)}))
    assert losing.balance == pytest.approx(995)
    assert losing.final_equity == pytest.approx(995)
    assert AnalysisEngine().analyze(losing).total_return == pytest.approx(-0.005)


def test_insufficient_free_margin_rejects_open_before_creating_position():
    result_bars = flat_bars(1.3000, 1.3000)
    engine = BacktestEngine(BacktestSettings(
        initial_cash=40, lots=0.01, contract_size=100_000, leverage=30,
    ))
    with pytest.raises(ValueError, match="Insufficient free margin"):
        engine.run(result_bars, Script({0: Signal(OrderAction.OPEN, Side.LONG)}))


def test_h4_breakout_signal_stop_and_sl_lifecycle_survive_forex_units():
    rows = ((100, 105, 98, 104), (104, 110, 103, 109), (109, 113, 106, 112),
            (112, 114, 107, 108), (108, 109, 101, 102))
    bars = tuple(Bar(START + timedelta(hours=4*i), *row) for i, row in enumerate(rows))
    result = BacktestEngine(BacktestSettings(
        initial_cash=100_000, lots=0.01, leverage=30, close_at_end=False,
    )).run(bars, H4Breakout())
    assert [(event.bar_index, event.side, event.trigger_price, event.stop_loss)
            for event in result.execution_trace if event.event_type.value == "signal"
            and event.action is OrderAction.OPEN
            and event.side is not None] == [(1, Side.LONG, 110, 103), (4, Side.SHORT, 101, 109)]
    assert result.orders[0].units == 1000
    assert result.orders[0].trigger_price == 110
    assert [event.stop_loss for event in result.execution_trace
            if event.event_type.value == "stop_updated"] == [106]
    assert result.trades[0].entry_price == 110
    assert result.trades[0].exit_price == 106
    assert result.trades[0].quantity == 1000
    assert result.trades[0].gross_pnl == pytest.approx(-4000)
    assert result.balance == pytest.approx(96_000)
