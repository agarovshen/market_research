import unittest
from datetime import datetime, timedelta

from app.backtest import BacktestEngine, BacktestSettings, Bar, ExecutionCosts, OrderAction, Side, Signal


def bars(*prices, spread=0):
    start = datetime(2024, 1, 1)
    return tuple(Bar(start + timedelta(minutes=i), p, p + 2, p - 2, p, spread=spread)
                 for i, p in enumerate(prices))


class ScriptedStrategy:
    def __init__(self, instructions):
        self.instructions = instructions

    def on_bar(self, context):
        return self.instructions.get(context.index)


class BacktestTests(unittest.TestCase):
    def run_script(self, price_data, instructions, **settings):
        return BacktestEngine(BacktestSettings(**settings)).run(
            price_data, ScriptedStrategy(instructions))

    def test_next_bar_order_execution_and_long_entry_exit(self):
        result = self.run_script(bars(100, 101, 102, 110), {
            0: Signal(OrderAction.OPEN, Side.LONG),
            2: Signal(OrderAction.CLOSE),
        }, initial_cash=1000)
        self.assertEqual([order.reference_price for order in result.orders], [101, 110])
        self.assertEqual([order.fill_price for order in result.orders], [101, 110])
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].gross_pnl, 9)
        self.assertEqual(result.trades[0].net_pnl, 9)
        self.assertEqual(result.final_cash, 1009)

    def test_short_entry_exit_and_position_accounting(self):
        result = self.run_script(bars(100, 101, 99, 90), {
            0: Signal(OrderAction.OPEN, Side.SHORT, 2),
            2: Signal(OrderAction.CLOSE),
        }, initial_cash=1000)
        self.assertEqual(result.trades[0].gross_pnl, 22)
        self.assertEqual(result.final_cash, 1022)
        self.assertEqual(result.final_equity, 1022)

    def test_commission_spread_and_slippage_costs(self):
        result = self.run_script(bars(100, 101, 102, 110, spread=2), {
            0: Signal(OrderAction.OPEN, Side.LONG),
            2: Signal(OrderAction.CLOSE),
        }, initial_cash=1000, costs=ExecutionCosts(
            commission_per_unit=2, commission_rate=.01, slippage=.5, spread_scale=1))
        self.assertAlmostEqual(result.orders[0].fill_price, 102.5)
        self.assertAlmostEqual(result.orders[1].fill_price, 108.5)
        self.assertAlmostEqual(result.total_commission, 6.11)
        self.assertEqual(result.total_spread_cost, 2)
        self.assertEqual(result.total_slippage_cost, 1)
        self.assertAlmostEqual(result.trades[0].gross_pnl, 6)
        self.assertAlmostEqual(result.trades[0].net_pnl, -0.11)

    def test_unrealized_pnl_and_equity_mark_to_market(self):
        result = self.run_script(bars(100, 101, 105, spread=2), {
            0: Signal(OrderAction.OPEN, Side.LONG),
        }, initial_cash=1000, close_at_end=False)
        self.assertEqual(result.final_cash, 898)
        self.assertEqual(result.unrealized_pnl, 2)
        self.assertEqual(result.final_equity, 1002)
        self.assertEqual(result.equity_curve[-1].equity, 1002)
        self.assertIsNotNone(result.open_position)

    def test_force_close_at_end_and_multiple_trades(self):
        result = self.run_script(bars(10, 11, 12, 13, 14, 15), {
            0: Signal(OrderAction.OPEN, Side.LONG),
            1: Signal(OrderAction.CLOSE),
            2: Signal(OrderAction.OPEN, Side.SHORT),
            3: Signal(OrderAction.CLOSE),
        }, initial_cash=100)
        self.assertEqual(len(result.orders), 4)
        self.assertEqual(len(result.trades), 2)
        self.assertEqual([trade.side for trade in result.trades], [Side.LONG, Side.SHORT])
        self.assertEqual(result.realized_pnl, sum(t.net_pnl for t in result.trades))
        self.assertEqual(result.final_equity, 100 + result.realized_pnl)

    def test_open_position_closes_on_last_close(self):
        result = self.run_script(bars(10, 11, 13), {
            0: Signal(OrderAction.OPEN, Side.LONG),
        }, initial_cash=100)
        self.assertEqual(result.orders[-1].reference_price, 13)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.equity_curve[-1].equity, result.final_equity)

    def test_result_is_deterministic(self):
        inputs = bars(10, 11, 9, 14)
        signals = {0: Signal(OrderAction.OPEN, Side.SHORT), 2: Signal(OrderAction.CLOSE)}
        a = self.run_script(inputs, signals, initial_cash=500)
        b = self.run_script(inputs, signals, initial_cash=500)
        self.assertEqual(a, b)

    def test_rejects_invalid_ordering_and_ohlc(self):
        with self.assertRaises(ValueError):
            BacktestEngine().run(tuple(reversed(bars(1, 2))), ScriptedStrategy({}))
        with self.assertRaises(ValueError):
            BacktestEngine().run((Bar(datetime(2024, 1, 1), 10, 9, 11, 10),),
                                 ScriptedStrategy({}))


if __name__ == "__main__":
    unittest.main()
