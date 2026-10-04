from app.analysis import AnalysisEngine
from app.backtest import BacktestEngine, BacktestSettings, ExecutionCosts, OrderAction, Side, Signal

from tests.validation.support import bars


class Orders:
    def __init__(self, signals):
        self.signals = signals

    def on_bar(self, context):
        return self.signals.get(context.index)


def test_market_order_lifecycle_uses_next_bar_open_with_direction_and_quantity():
    result = BacktestEngine(BacktestSettings(initial_cash=1_000)).run(
        bars(100, 105, 108, 101),
        Orders({0: Signal(OrderAction.OPEN, Side.SHORT, 3), 2: Signal(OrderAction.CLOSE)}),
    )
    opening, closing = result.orders
    assert (opening.action, opening.side, opening.quantity, opening.reference_price, opening.fill_price) == (
        OrderAction.OPEN, Side.SHORT, 3, 105, 105
    )
    assert (closing.action, closing.side, closing.quantity, closing.reference_price, closing.fill_price) == (
        OrderAction.CLOSE, Side.SHORT, 3, 101, 101
    )
    assert opening.created_at < opening.filled_at
    assert closing.created_at < closing.filled_at
    trade, = result.trades
    assert (trade.entry_time, trade.exit_time, trade.gross_pnl, trade.net_pnl) == (
        opening.filled_at, closing.filled_at, 12, 12
    )


def test_execution_costs_and_trade_net_pnl_follow_explicit_formula():
    settings = BacktestSettings(initial_cash=1_000, costs=ExecutionCosts(
        commission_per_unit=2, commission_rate=.01, slippage=.5, spread_scale=1,
    ))
    result = BacktestEngine(settings).run(
        bars(100, 101, 102, 110, spread=2),
        Orders({0: Signal(OrderAction.OPEN, Side.LONG), 2: Signal(OrderAction.CLOSE)}),
    )
    # Entry fill 101 + (1 spread + .5 slippage)=102.5; exit fill 110-1-.5=108.5.
    # Gross=-14? For a long these are adverse costs on both fills: 108.5-102.5=6.
    # Commissions are (2+1.025)+(2+1.085)=6.11, net=-0.11.
    entry, exit = result.orders
    assert (entry.fill_price, exit.fill_price) == (102.5, 108.5)
    assert (entry.commission, exit.commission) == (3.025, 3.085)
    assert result.trades[0].gross_pnl == 6
    assert abs(result.trades[0].net_pnl - (6 - 3.025 - 3.085)) < 1e-12
    assert result.total_spread_cost == 2
    assert result.total_slippage_cost == 1
    assert abs(result.final_equity - (1_000 + result.trades[0].net_pnl)) < 1e-12


def test_accounting_invariants_hold_for_closed_trade_path():
    result = BacktestEngine(BacktestSettings(initial_cash=500, costs=ExecutionCosts(
        commission_per_unit=.25, spread_scale=1,
    ))).run(
        bars(20, 21, 24, 19, 18, 22, spread=2),
        Orders({0: Signal(OrderAction.OPEN, Side.LONG, 2),
                2: Signal(OrderAction.CLOSE),
                3: Signal(OrderAction.OPEN, Side.SHORT),
                4: Signal(OrderAction.CLOSE)}),
    )
    analysis = AnalysisEngine().analyze(result)
    assert abs(result.realized_pnl - sum(trade.net_pnl for trade in result.trades)) < 1e-9
    assert abs(result.final_equity - (result.initial_cash + result.realized_pnl)) < 1e-9
    assert abs(analysis.drawdown.max_drawdown - max(
        point.drawdown for point in analysis.drawdown_series
    )) < 1e-9
    assert analysis.drawdown.max_drawdown >= max(point.drawdown for point in analysis.drawdown_series)
    assert all(point.drawdown >= 0 for point in analysis.drawdown_series)
