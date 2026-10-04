"""Manually calculated outcomes for the documented next-bar-open engine."""

from app.analysis import AnalysisEngine
from app.backtest import BacktestEngine, BacktestSettings, OrderAction, Side, Signal

from tests.validation.support import bars


class Scripted:
    def __init__(self, instructions):
        self.instructions = instructions

    def on_bar(self, context):
        return self.instructions.get(context.index)


def run(prices, instructions, *, initial_cash=1_000.0, **settings):
    return BacktestEngine(BacktestSettings(initial_cash=initial_cash, **settings)).run(
        bars(*prices), Scripted(instructions)
    )


def test_long_exact_entry_exit_profit_and_accounting():
    # Signal on bar 0 fills at bar 1 open=110; close signal on bar 2 fills
    # at bar 3 open=125. One unit => gross/net +15, final equity 1015.
    result = run((100, 110, 120, 125), {
        0: Signal(OrderAction.OPEN, Side.LONG),
        2: Signal(OrderAction.CLOSE),
    })
    assert [(order.action, order.created_at, order.filled_at, order.fill_price)
            for order in result.orders] == [
        (OrderAction.OPEN, bars(1)[0].timestamp, bars(1, 2)[1].timestamp, 110),
        (OrderAction.CLOSE, bars(1, 2, 3)[2].timestamp, bars(1, 2, 3, 4)[3].timestamp, 125),
    ]
    trade, = result.trades
    assert (trade.side, trade.quantity, trade.entry_price, trade.exit_price) == (Side.LONG, 1, 110, 125)
    assert (trade.gross_pnl, trade.net_pnl, result.final_equity) == (15, 15, 1_015)
    assert result.realized_pnl == sum((125 - 110) * 1 for _ in (0,))


def test_short_loss_flat_multiple_and_no_trade_known_answers():
    # Short enters at 110 and exits at 125: (110-125)*2 = -30.
    loss = run((100, 110, 120, 125), {
        0: Signal(OrderAction.OPEN, Side.SHORT, 2),
        2: Signal(OrderAction.CLOSE),
    })
    assert loss.trades[0].gross_pnl == -30
    assert loss.trades[0].net_pnl == -30
    assert loss.final_equity == 970

    # Entry and exit both fill at 110: zero gross/net PnL.
    flat = run((100, 110, 115, 110), {
        0: Signal(OrderAction.OPEN, Side.LONG),
        2: Signal(OrderAction.CLOSE),
    })
    assert flat.trades[0].net_pnl == 0
    assert flat.final_equity == 1_000

    # Two manually enumerated trades: +10 then -4, total +6.
    multiple = run((100, 110, 115, 106, 103, 105), {
        0: Signal(OrderAction.OPEN, Side.LONG),
        1: Signal(OrderAction.CLOSE),
        2: Signal(OrderAction.OPEN, Side.LONG),
        3: Signal(OrderAction.CLOSE),
    })
    assert [trade.net_pnl for trade in multiple.trades] == [5, -3]
    assert multiple.final_equity == 1_002

    no_trades = run((100, 90, 80), {})
    assert no_trades.trades == ()
    assert no_trades.final_equity == 1_000


def test_empty_and_one_bar_edges_and_open_position_transition():
    engine = BacktestEngine(BacktestSettings(initial_cash=100))
    empty = engine.run((), Scripted({}))
    assert empty.equity_curve == ()
    assert empty.final_equity == 100

    one = engine.run(bars(50), Scripted({0: Signal(OrderAction.OPEN, Side.LONG)}))
    # A signal generated on the only bar has no following open to fill against.
    assert one.orders == ()
    assert one.trades == ()
    assert one.final_equity == 100

    open_position = BacktestEngine(BacktestSettings(initial_cash=100, close_at_end=False)).run(
        bars(50, 55, 60), Scripted({0: Signal(OrderAction.OPEN, Side.LONG)})
    )
    assert open_position.open_position.entry_time == bars(0, 55)[1].timestamp
    assert open_position.open_position.entry_price == 55
    assert open_position.final_equity == 105


def test_drawdown_matches_independent_equity_arithmetic():
    # Entry=110, interim mark=120 gives equity 1010; exit at 90 loses 20.
    # Equity observations: 1000, 1000, 1010, 980; peak=1010, max DD=30.
    result = run((100, 110, 120, 90), {
        0: Signal(OrderAction.OPEN, Side.LONG),
        2: Signal(OrderAction.CLOSE),
    })
    analysis = AnalysisEngine().analyze(result)
    assert [point.equity for point in result.equity_curve] == [1_000, 1_000, 1_010, 980]
    assert [point.drawdown for point in analysis.drawdown_series] == [0, 0, 0, 30]
    assert analysis.drawdown.max_drawdown == 30
    # Percentage drawdown uses signed return-from-peak convention.
    assert analysis.drawdown.max_drawdown_pct == -30 / 1_010
    assert all(point.peak_equity >= point.equity for point in analysis.equity_analysis)
