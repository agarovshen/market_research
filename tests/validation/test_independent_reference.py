"""Cross-check BacktestEngine with primitive, test-only accounting arithmetic."""

from datetime import datetime, timedelta

from app.analysis import AnalysisEngine
from app.backtest import (
    BacktestEngine, BacktestSettings, Bar, ExecutionCosts, OrderAction, Side, Signal,
)
from tests.validation.reference import first_difference
from tests.validation.reference_model import calculate


# Synthetic historical-style OHLC path, explicitly authored for this fixture;
# it is not represented as vendor or exchange data.
RAW = (
    (100.00, 101.00, 99.50, 100.50, .02),
    (101.00, 103.00, 100.75, 102.00, .02),
    (102.00, 104.00, 101.50, 103.00, .04),
    (103.00, 103.50, 99.00, 100.00, .02),
    (100.00, 101.00, 99.00, 99.50, .02),
    (99.00, 100.00, 97.00, 98.00, .04),
    (98.00, 99.00, 96.50, 97.00, .02),
    (97.00, 98.00, 96.00, 97.50, .02),
)
BASE = datetime(2023, 6, 1, 9, 30)
ROWS = tuple({"time": BASE + timedelta(minutes=15 * index), "open": op,
              "high": high, "low": low, "close": close, "spread": spread}
             for index, (op, high, low, close, spread) in enumerate(RAW))
BARS = tuple(Bar(row["time"], row["open"], row["high"], row["low"], row["close"],
                 spread=row["spread"]) for row in ROWS)

# Rule: emit long(2) after the first completed bar, close after bar 3;
# emit short(1) after bar 5, close after bar 7. Signals fill on next opens.
EVENTS = {0: ("open", "long", 2), 2: ("close", None, None),
          4: ("open", "short", 1), 6: ("close", None, None)}


class ExplicitEventRule:
    """Strategy adapter for the written fixture rule; contains no arithmetic."""
    def on_bar(self, context):
        event = EVENTS.get(context.index)
        if event is None:
            return None
        action, side, quantity = event
        return Signal(OrderAction(action), Side(side) if side else None, quantity)


def production_view(result):
    analysis = AnalysisEngine().analyze(result)
    return {
        "orders": [{"action": item.action.value, "side": item.side.value,
                    "signal_time": item.created_at, "time": item.filled_at,
                    "reference_price": item.reference_price, "price": item.fill_price,
                    "quantity": item.quantity, "commission": item.commission,
                    "fees": item.spread_cost, "slippage": item.slippage_cost}
                   for item in result.orders],
        "trades": [{"side": item.side.value, "entry_time": item.entry_time,
                    "entry_price": item.entry_price, "exit_time": item.exit_time,
                    "exit_price": item.exit_price, "quantity": item.quantity,
                    "commission": item.entry_commission + item.exit_commission,
                    "fees": sum(order.spread_cost for order in result.orders
                                if order.filled_at in (item.entry_time, item.exit_time)),
                    "slippage": sum(order.slippage_cost for order in result.orders
                                    if order.filled_at in (item.entry_time, item.exit_time)),
                    "gross_pnl": item.gross_pnl, "net_pnl": item.net_pnl}
                   for item in result.trades],
        "equity": [{"timestamp": point.timestamp, "value": point.equity}
                   for point in result.equity_curve],
        "drawdown": [{"timestamp": point.timestamp, "value": point.drawdown}
                     for point in analysis.drawdown_series],
        "statistics": {"final_equity": result.final_equity},
    }


def test_production_matches_independent_scripted_reference_end_to_end():
    costs = dict(commission_per_unit=.1, commission_rate=.001,
                 slippage=.01, spread_scale=.5)
    expected = calculate(ROWS, EVENTS, initial_cash=10_000, **costs)
    engine_result = BacktestEngine(BacktestSettings(
        initial_cash=10_000,
        costs=ExecutionCosts(**costs),
    )).run(BARS, ExplicitEventRule())
    actual = production_view(engine_result)

    difference = first_difference(actual, expected, absolute_tolerance=1e-9)
    assert difference is None, str(difference)
    assert len(expected["trades"]) == 2
    assert abs(expected["final_equity"] -
               (10_000 + sum(trade["net_pnl"] for trade in expected["trades"]))) < 1e-10
    # Independently hand-check one fill and gross arithmetic to anchor the oracle.
    assert expected["trades"][0]["entry_price"] == 101.015
    assert expected["trades"][0]["exit_price"] == 102.985
    assert expected["trades"][0]["gross_pnl"] == (102.985 - 101.015) * 2
    frictionless = calculate(ROWS, EVENTS, initial_cash=10_000)
    assert expected["trades"][0]["net_pnl"] < frictionless["trades"][0]["net_pnl"]


def test_first_divergence_report_shows_equal_prefix_and_masks_downstream_fields():
    expected = calculate(ROWS, EVENTS, initial_cash=10_000)
    actual = {key: value for key, value in expected.items()}
    actual["trades"] = [dict(row) for row in expected["trades"]]
    actual["trades"][0]["entry_price"] += .0001
    difference = first_difference(actual, expected)
    report = str(difference)
    assert difference.field == "entry_price"
    assert "side: 'long' == 'long'" in report
    assert "entry_price: DIFFERENT" in report
    assert "actual - expected: +0.0001" in report
    assert "exit_time: not comparable" in report
    assert "expected:" in report and "actual:" in report


def test_open_position_final_equity_reconciles_independently_including_unrealized():
    rows = ROWS[:3]
    bars = BARS[:3]
    events = {0: ("open", "long", 2)}
    expected = calculate(rows, events, initial_cash=10_000, close_at_end=False)
    result = BacktestEngine(BacktestSettings(initial_cash=10_000, close_at_end=False)).run(
        bars, ExplicitEventRule())
    assert result.trades == ()
    assert result.open_position is not None
    # Entry=101.01 after 0.01 half-spread; final liquidation=102.98
    # after 0.02 half-spread. Two units yield (102.98 - 101.01) * 2 = 3.94.
    assert abs(result.unrealized_pnl - 3.94) < 1e-12
    assert abs(expected["unrealized_pnl"] - 3.94) < 1e-12
    assert abs(result.final_equity - expected["final_equity"]) < 1e-10
    assert abs(result.final_equity - (result.final_cash + 2 * 102.98)) < 1e-10
