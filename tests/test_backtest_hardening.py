import os
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta
from math import nan

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.backtest import (
    BacktestEngine, BacktestRunConfig, BacktestRunner, BacktestSettings,
    Bar, ExecutionCosts, OrderAction, Side, Signal,
)
from app.backtest.data import MarketDataRepository
from app.backtest.models import BacktestResult
from app.database import Base
from app.models import Instrument, MarketData


START = datetime(2024, 1, 2, 9, 0)


def fixture_bars(spread=0):
    """Three hand-calculable bars plus one bar for a next-bar exit."""
    values = (
        (100, 105, 95, 102),
        (103, 108, 101, 107),
        (106, 110, 104, 105),
        (104, 106, 100, 102),
    )
    return tuple(Bar(START + timedelta(minutes=index), *ohlc, spread=spread)
                 for index, ohlc in enumerate(values))


class ScriptedStrategy:
    def __init__(self, signals=None):
        self.signals = signals or {}
        self.contexts = []

    def on_bar(self, context):
        self.contexts.append(context)
        return self.signals.get(context.index)


class BacktestHardeningTests(unittest.TestCase):
    def run_script(self, signals=None, bars=None, **settings):
        strategy = ScriptedStrategy(signals)
        result = BacktestEngine(BacktestSettings(**settings)).run(
            fixture_bars() if bars is None else bars, strategy)
        return result, strategy

    # Market-data iteration and information boundaries
    def test_empty_data_returns_empty_flat_result(self):
        result, strategy = self.run_script({}, bars=(), initial_cash=250)
        self.assertEqual(result.equity_curve, ())
        self.assertEqual(result.orders, ())
        self.assertEqual(result.trades, ())
        self.assertEqual(result.final_cash, 250)
        self.assertEqual(result.final_equity, 250)
        self.assertEqual(strategy.contexts, [])

    def test_one_bar_is_delivered_once_and_final_open_signal_is_not_filled(self):
        one = fixture_bars()[:1]
        result, strategy = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG)}, bars=one)
        self.assertEqual(len(strategy.contexts), 1)
        self.assertEqual(result.orders, ())
        self.assertEqual(result.trades, ())
        self.assertIsNone(result.open_position)
        self.assertEqual(len(result.equity_curve), 1)

    def test_multiple_bar_iteration_is_chronological_and_deterministic(self):
        result, strategy = self.run_script({})
        self.assertEqual([context.bar.timestamp for context in strategy.contexts],
                         [bar.timestamp for bar in fixture_bars()])
        self.assertEqual([point.timestamp for point in result.equity_curve],
                         [bar.timestamp for bar in fixture_bars()])
        self.assertEqual([context.index for context in strategy.contexts], list(range(4)))

    def test_first_strategy_context_is_exactly_one_completed_bar(self):
        _, strategy = self.run_script({})
        first = strategy.contexts[0]
        self.assertEqual(first.index, 0)
        self.assertEqual(first.bar, fixture_bars()[0])
        self.assertEqual(len(first.history), 1)
        self.assertEqual(first.history[-1], first.bar)

    def test_strategy_history_is_a_completed_bar_prefix_without_future_access(self):
        _, strategy = self.run_script({})
        for index, context in enumerate(strategy.contexts):
            self.assertEqual(len(context.history), index + 1)
            self.assertEqual(context.history[-1], context.bar)
            self.assertEqual(tuple(context.history), fixture_bars()[:index + 1])
            with self.assertRaises(IndexError):
                context.history[index + 1]

    def test_strategy_is_called_once_per_input_bar_in_order(self):
        _, strategy = self.run_script({})
        self.assertEqual([context.index for context in strategy.contexts], [0, 1, 2, 3])

    # Signal -> order and next-bar execution
    def test_open_long_signal_is_buy_order_on_next_open(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG)}, close_at_end=False)
        self.assertEqual(len(result.orders), 1)
        order = result.orders[0]
        self.assertEqual((order.action, order.side), (OrderAction.OPEN, Side.LONG))
        self.assertEqual(order.reference_price, 103)
        self.assertEqual(order.fill_price, 103)
        self.assertEqual(order.created_at, fixture_bars()[0].timestamp)
        self.assertEqual(order.filled_at, fixture_bars()[1].timestamp)

    def test_open_short_signal_is_sell_order_on_next_open(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.SHORT)}, close_at_end=False)
        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0].side, Side.SHORT)
        self.assertEqual(result.orders[0].action, OrderAction.OPEN)
        self.assertEqual(result.orders[0].fill_price, 103)

    def test_close_signal_is_ignored_when_flat(self):
        result, _ = self.run_script({0: Signal(OrderAction.CLOSE), 2: Signal(OrderAction.CLOSE)})
        self.assertEqual(result.orders, ())

    def test_no_signal_creates_no_order(self):
        result, _ = self.run_script({})
        self.assertEqual(result.orders, ())

    def test_signal_waits_until_following_bar_and_never_fills_on_signal_bar(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG)})
        order = result.orders[0]
        self.assertGreater(order.filled_at, order.created_at)
        self.assertEqual(order.filled_at, fixture_bars()[1].timestamp)
        self.assertEqual(order.reference_price, fixture_bars()[1].open)
        self.assertNotEqual(order.reference_price, fixture_bars()[0].open)

    def test_signal_on_final_bar_is_discarded_without_impossible_fill(self):
        result, _ = self.run_script({3: Signal(OrderAction.OPEN, Side.LONG)})
        self.assertEqual(result.orders, ())
        self.assertEqual(result.trades, ())
        self.assertIsNone(result.open_position)

    def test_multiple_signals_are_applied_in_bar_order(self):
        result, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.LONG, 2),
            1: Signal(OrderAction.CLOSE),
            2: Signal(OrderAction.OPEN, Side.SHORT, 3),
            3: Signal(OrderAction.CLOSE),
        })
        self.assertEqual([(o.action, o.side, o.quantity) for o in result.orders], [
            (OrderAction.OPEN, Side.LONG, 2), (OrderAction.CLOSE, Side.LONG, 2),
            (OrderAction.OPEN, Side.SHORT, 3), (OrderAction.CLOSE, Side.SHORT, 3),
        ])
        self.assertEqual([trade.sequence for trade in result.trades], [1, 2])
        self.assertEqual([trade.side for trade in result.trades], [Side.LONG, Side.SHORT])

    def test_repeated_open_signal_does_not_increase_existing_position(self):
        result, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.LONG, 1),
            1: Signal(OrderAction.OPEN, Side.LONG, 5),
        }, close_at_end=False)
        self.assertEqual(len(result.orders), 1)
        self.assertEqual(result.open_position.quantity, 1)

    # Long-side accounting
    def test_long_entry_exit_gross_realized_cash_and_final_equity(self):
        result, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.LONG, 2),
            2: Signal(OrderAction.CLOSE),
        }, initial_cash=1000)
        trade = result.trades[0]
        self.assertEqual((trade.entry_price, trade.exit_price), (103, 104))
        self.assertEqual(trade.gross_pnl, 2)
        self.assertEqual(trade.net_pnl, 2)
        self.assertEqual(result.realized_pnl, 2)
        self.assertEqual(result.final_cash, 1002)
        self.assertEqual(result.final_equity, 1002)
        self.assertIsNone(result.open_position)

    def test_long_equity_and_unrealized_pnl_while_position_is_open(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG, 2)},
                                    bars=fixture_bars()[:3], initial_cash=1000, close_at_end=False)
        after_entry = result.equity_curve[1]
        self.assertEqual(after_entry.cash, 794)  # 1000 - 2 * 103
        self.assertEqual(after_entry.unrealized_pnl, 8)  # 2 * (107 - 103)
        self.assertEqual(after_entry.equity, 1008)  # cash + marked long asset (2 * 107)
        self.assertEqual(result.final_equity, 1004)  # final mark: cash + 2 * 105
        self.assertEqual(result.unrealized_pnl, 4)

    def test_long_quantity_scales_pnl_linearly(self):
        one, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG, 1),
                                  2: Signal(OrderAction.CLOSE)}, initial_cash=1000)
        five, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG, 5),
                                   2: Signal(OrderAction.CLOSE)}, initial_cash=1000)
        self.assertEqual(one.trades[0].gross_pnl, 1)
        self.assertEqual(five.trades[0].gross_pnl, 5)

    def test_repeated_long_open_is_not_a_supported_scale_in(self):
        result, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.LONG, 2),
            1: Signal(OrderAction.OPEN, Side.LONG, 7),
            2: Signal(OrderAction.CLOSE),
        })
        self.assertEqual([order.action for order in result.orders], [OrderAction.OPEN, OrderAction.CLOSE])
        self.assertEqual(result.trades[0].quantity, 2)

    # Short-side accounting and sign checks
    def test_short_entry_exit_loss_cash_and_realized_pnl_signs(self):
        result, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.SHORT, 2),
            2: Signal(OrderAction.CLOSE),
        }, initial_cash=1000)
        trade = result.trades[0]
        self.assertEqual((trade.entry_price, trade.exit_price), (103, 104))
        self.assertEqual(trade.gross_pnl, -2)
        self.assertEqual(trade.net_pnl, -2)
        self.assertEqual(result.realized_pnl, -2)
        self.assertEqual(result.final_cash, 998)
        self.assertEqual(result.final_equity, 998)

    def test_short_equity_and_unrealized_pnl_while_position_is_open(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.SHORT, 2)},
                                    bars=fixture_bars()[:3], initial_cash=1000, close_at_end=False)
        after_entry = result.equity_curve[1]
        self.assertEqual(after_entry.cash, 1206)  # 1000 + 2 * 103 short proceeds
        self.assertEqual(after_entry.unrealized_pnl, -8)  # 2 * (103 - 107)
        self.assertEqual(after_entry.equity, 992)  # cash less 2 * 107 liability
        self.assertEqual(result.final_equity, 996)  # cash less 2 * 105 liability
        self.assertEqual(result.unrealized_pnl, -4)

    def test_short_profit_in_a_falling_market(self):
        falling = (
            Bar(START, 100, 102, 98, 100),
            Bar(START + timedelta(minutes=1), 99, 101, 97, 98),
            Bar(START + timedelta(minutes=2), 95, 98, 94, 96),
            Bar(START + timedelta(minutes=3), 90, 92, 89, 91),
        )
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.SHORT, 3),
                                     2: Signal(OrderAction.CLOSE)}, bars=falling)
        self.assertEqual(result.trades[0].gross_pnl, 27)
        self.assertEqual(result.final_cash, 100_000 + 27)

    # Costs, directions, and no double counting
    def test_commission_only_is_charged_at_both_fills(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG),
                                     2: Signal(OrderAction.CLOSE)}, initial_cash=1000,
                                    costs=ExecutionCosts(commission_per_unit=0.25))
        self.assertEqual(result.total_commission, 0.5)
        self.assertEqual(result.trades[0].gross_pnl, 1)
        self.assertEqual(result.trades[0].net_pnl, 0.5)
        self.assertEqual(result.final_cash, 1000.5)

    def test_notional_commission_is_calculated_from_each_execution_price(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG),
                                     2: Signal(OrderAction.CLOSE)}, initial_cash=1000,
                                    costs=ExecutionCosts(commission_rate=0.01))
        self.assertAlmostEqual(result.total_commission, 1.03 + 1.04)
        self.assertAlmostEqual(result.trades[0].net_pnl, 1 - 2.07)

    def test_spread_only_is_in_fill_prices_and_not_subtracted_twice(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG),
                                     2: Signal(OrderAction.CLOSE)}, bars=fixture_bars(spread=4),
                                    initial_cash=1000)
        self.assertEqual((result.orders[0].fill_price, result.orders[1].fill_price), (105, 102))
        self.assertEqual(result.total_spread_cost, 4)
        self.assertEqual(result.trades[0].gross_pnl, -3)
        self.assertEqual(result.trades[0].net_pnl, -3)
        self.assertEqual(result.final_cash, 997)

    def test_slippage_only_adjusts_both_fills_once(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG),
                                     2: Signal(OrderAction.CLOSE)}, initial_cash=1000,
                                    costs=ExecutionCosts(slippage=0.5))
        self.assertEqual((result.orders[0].fill_price, result.orders[1].fill_price), (103.5, 103.5))
        self.assertEqual(result.total_slippage_cost, 1)
        self.assertEqual(result.trades[0].gross_pnl, 0)
        self.assertEqual(result.final_cash, 1000)

    def test_all_execution_costs_match_hand_calculated_round_trip(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG),
                                     2: Signal(OrderAction.CLOSE)},
                                    bars=fixture_bars(spread=4), initial_cash=1000,
                                    costs=ExecutionCosts(commission_per_unit=0.2,
                                        commission_rate=0.01, spread_scale=1, slippage=0.5))
        self.assertEqual((result.orders[0].fill_price, result.orders[1].fill_price), (105.5, 101.5))
        self.assertAlmostEqual(result.total_commission, 1.255 + 1.215)
        self.assertEqual(result.total_spread_cost, 4)
        self.assertEqual(result.total_slippage_cost, 1)
        self.assertAlmostEqual(result.trades[0].gross_pnl, -4)
        self.assertAlmostEqual(result.trades[0].net_pnl, -6.47)
        self.assertAlmostEqual(result.final_cash, 993.53)

    def test_slippage_direction_for_buy_and_sell_fills(self):
        bars = fixture_bars(spread=0)
        long_result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG),
                                          2: Signal(OrderAction.CLOSE)}, bars=bars,
                                         costs=ExecutionCosts(slippage=0.5))
        short_result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.SHORT),
                                           2: Signal(OrderAction.CLOSE)}, bars=bars,
                                          costs=ExecutionCosts(slippage=0.5))
        self.assertEqual(long_result.orders[0].fill_price, 103.5)  # buy entry pays up
        self.assertEqual(long_result.orders[1].fill_price, 103.5)  # sell exit gives up
        self.assertEqual(short_result.orders[0].fill_price, 102.5)  # sell entry gives up
        self.assertEqual(short_result.orders[1].fill_price, 104.5)  # buy-to-cover pays up

    def test_short_spread_cost_and_short_slippage_signs(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.SHORT),
                                     2: Signal(OrderAction.CLOSE)},
                                    bars=fixture_bars(spread=4), initial_cash=1000,
                                    costs=ExecutionCosts(slippage=0.5))
        self.assertEqual((result.orders[0].fill_price, result.orders[1].fill_price), (100.5, 106.5))
        self.assertEqual(result.total_spread_cost, 4)
        self.assertEqual(result.total_slippage_cost, 1)
        self.assertEqual(result.trades[0].gross_pnl, -6)

    def test_zero_cost_settings_leave_market_fills_unchanged(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG),
                                     2: Signal(OrderAction.CLOSE)}, initial_cash=1000,
                                    costs=ExecutionCosts())
        self.assertEqual([order.fill_price for order in result.orders], [103, 104])
        self.assertEqual((result.total_commission, result.total_spread_cost,
                          result.total_slippage_cost), (0, 0, 0))

    # Quantity semantics and invalid values
    def test_open_quantity_override_is_used_by_order_position_and_trade(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG, 0.125),
                                     2: Signal(OrderAction.CLOSE)}, initial_cash=1000)
        self.assertEqual(result.orders[0].quantity, 0.125)
        self.assertEqual(result.trades[0].quantity, 0.125)
        self.assertEqual(result.trades[0].gross_pnl, 0.125)

    def test_invalid_signal_quantities_are_rejected(self):
        for quantity in (0, -1, nan):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                Signal(OrderAction.OPEN, Side.LONG, quantity)

    def test_default_position_size_and_extreme_quantities_are_respected(self):
        default, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG)},
                                     position_size=3, close_at_end=False)
        small, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG, 1e-9)}, close_at_end=False)
        large, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.SHORT, 1e6)}, close_at_end=False)
        self.assertEqual(default.open_position.quantity, 3)
        self.assertEqual(small.open_position.quantity, 1e-9)
        self.assertEqual(large.open_position.quantity, 1e6)

    # End-of-data behavior
    def test_final_long_liquidation_uses_last_close_without_adding_a_bar(self):
        bars = fixture_bars()[:3]
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG, 2)},
                                    bars=bars, initial_cash=1000)
        self.assertEqual(len(result.equity_curve), len(bars))
        self.assertEqual(len(result.orders), 2)
        self.assertEqual(result.orders[-1].reference_price, bars[-1].close)
        self.assertEqual(result.orders[-1].filled_at, bars[-1].timestamp)
        self.assertEqual(result.trades[0].exit_time, bars[-1].timestamp)
        self.assertIsNone(result.open_position)

    def test_final_short_liquidation_uses_last_close_and_costs(self):
        bars = fixture_bars(spread=2)[:3]
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.SHORT)},
                                    bars=bars, initial_cash=1000,
                                    costs=ExecutionCosts(slippage=0.25))
        trade = result.trades[0]
        self.assertEqual(result.orders[-1].reference_price, bars[-1].close)
        self.assertEqual(result.orders[-1].fill_price, bars[-1].close + 1 + 0.25)
        self.assertEqual(result.orders[-1].filled_at, bars[-1].timestamp)
        self.assertEqual(result.total_spread_cost, 2)
        self.assertEqual(result.total_slippage_cost, 0.5)
        self.assertIsNone(result.open_position)
        self.assertAlmostEqual(result.final_cash, 1000 + trade.net_pnl)

    def test_final_liquidation_can_be_disabled_and_keeps_position_marked(self):
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.SHORT, 2)},
                                    bars=fixture_bars()[:3], initial_cash=1000, close_at_end=False)
        self.assertEqual(len(result.orders), 1)
        self.assertEqual(len(result.trades), 0)
        self.assertIsNotNone(result.open_position)
        self.assertEqual(result.open_position.side, Side.SHORT)
        self.assertEqual(result.final_equity, 996)

    def test_open_position_created_at_last_fill_is_liquidated_same_bar(self):
        bars = fixture_bars()[:2]
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG)},
                                    bars=bars, initial_cash=1000)
        self.assertEqual(len(result.equity_curve), 2)
        self.assertEqual(len(result.orders), 2)
        self.assertEqual(result.orders[0].filled_at, result.orders[1].filled_at)
        self.assertEqual(result.trades[0].entry_time, result.trades[0].exit_time)

    # Multiple independent trades and market edge conditions
    def test_long_flat_long_and_short_flat_short_trade_ordering(self):
        long_long, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.LONG),
            1: Signal(OrderAction.CLOSE),
            2: Signal(OrderAction.OPEN, Side.LONG),
        })
        short_short, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.SHORT),
            1: Signal(OrderAction.CLOSE),
            2: Signal(OrderAction.OPEN, Side.SHORT),
        })
        self.assertEqual([t.side for t in long_long.trades], [Side.LONG, Side.LONG])
        self.assertEqual([t.sequence for t in long_long.trades], [1, 2])
        self.assertEqual([t.side for t in short_short.trades], [Side.SHORT, Side.SHORT])
        self.assertEqual([t.sequence for t in short_short.trades], [1, 2])

    def test_long_to_short_and_short_to_long_are_flat_between_trades(self):
        long_short, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.LONG),
            1: Signal(OrderAction.CLOSE),
            2: Signal(OrderAction.OPEN, Side.SHORT),
            3: Signal(OrderAction.CLOSE),
        })
        short_long, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.SHORT),
            1: Signal(OrderAction.CLOSE),
            2: Signal(OrderAction.OPEN, Side.LONG),
            3: Signal(OrderAction.CLOSE),
        })
        self.assertEqual([trade.side for trade in long_short.trades], [Side.LONG, Side.SHORT])
        self.assertEqual([trade.side for trade in short_long.trades], [Side.SHORT, Side.LONG])
        self.assertEqual([order.action for order in long_short.orders], [
            OrderAction.OPEN, OrderAction.CLOSE, OrderAction.OPEN, OrderAction.CLOSE])

    def test_flat_market_produces_zero_pnl(self):
        flat = tuple(Bar(START + timedelta(minutes=i), 100, 100, 100, 100) for i in range(4))
        result, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG),
                                     2: Signal(OrderAction.CLOSE)}, bars=flat)
        self.assertEqual(result.realized_pnl, 0)
        self.assertEqual(result.final_equity, result.initial_cash)

    def test_monotonic_market_signs_for_long_and_short(self):
        rising = tuple(Bar(START + timedelta(minutes=i), p, p + 1, p - 1, p)
                       for i, p in enumerate((100, 101, 102, 103)))
        falling = tuple(Bar(START + timedelta(minutes=i), p, p + 1, p - 1, p)
                        for i, p in enumerate((103, 102, 101, 100)))
        long, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.LONG),
                                   2: Signal(OrderAction.CLOSE)}, bars=rising)
        short, _ = self.run_script({0: Signal(OrderAction.OPEN, Side.SHORT),
                                    2: Signal(OrderAction.CLOSE)}, bars=falling)
        self.assertGreater(long.realized_pnl, 0)
        self.assertGreater(short.realized_pnl, 0)

    def test_equity_after_multiple_closed_trades_reconciles_to_realized_pnl(self):
        result, _ = self.run_script({
            0: Signal(OrderAction.OPEN, Side.LONG), 1: Signal(OrderAction.CLOSE),
            2: Signal(OrderAction.OPEN, Side.SHORT), 3: Signal(OrderAction.CLOSE),
        }, initial_cash=1000)
        self.assertEqual(result.realized_pnl, sum(trade.net_pnl for trade in result.trades))
        self.assertEqual(result.final_cash, 1000 + result.realized_pnl)
        self.assertEqual(result.final_equity, result.final_cash)

    def test_determinism_includes_serialized_result_fields(self):
        signals = {0: Signal(OrderAction.OPEN, Side.SHORT, 1.5), 2: Signal(OrderAction.CLOSE)}
        first, _ = self.run_script(signals, initial_cash=500,
                                   costs=ExecutionCosts(commission_per_unit=0.1, slippage=0.05))
        second, _ = self.run_script(signals, initial_cash=500,
                                    costs=ExecutionCosts(commission_per_unit=0.1, slippage=0.05))
        self.assertEqual(asdict(first), asdict(second))


class RepositoryAndRunnerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        instrument = Instrument(symbol="EURUSD", type="forex")
        self.session.add(instrument)
        self.session.flush()
        self.session.add_all([
            MarketData(instrument_id=instrument.id, timestamp=START + timedelta(minutes=1),
                       open=100, high=102, low=99, close=101, tick_volume=1, volume=10, spread=2),
            MarketData(instrument_id=instrument.id, timestamp=START + timedelta(minutes=4),
                       open=101, high=105, low=100, close=104, tick_volume=2, volume=20, spread=3),
            MarketData(instrument_id=instrument.id, timestamp=START + timedelta(minutes=5),
                       open=104, high=106, low=103, close=105, tick_volume=3, volume=30, spread=4),
            MarketData(instrument_id=instrument.id, timestamp=START + timedelta(minutes=15),
                       open=105, high=108, low=104, close=107, tick_volume=4, volume=40, spread=5),
        ])
        self.session.commit()
        self.repository = MarketDataRepository(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_repository_orders_bars_and_respects_inclusive_date_boundaries(self):
        bars = self.repository.load("eurusd", start=START + timedelta(minutes=4),
                                    end=START + timedelta(minutes=5))
        self.assertEqual([bar.timestamp for bar in bars], [
            START + timedelta(minutes=4), START + timedelta(minutes=5)])
        self.assertEqual([bar.close for bar in bars], [104, 105])

    def test_repository_supports_instrument_id_and_empty_ranges(self):
        instrument_id = self.session.query(Instrument).filter_by(symbol="EURUSD").one().id
        self.assertEqual(len(self.repository.load(instrument_id)), 4)
        self.assertEqual(self.repository.load("EURUSD", start=START + timedelta(days=1)), ())

    def test_repository_aggregates_m5_ohlcv_spread_and_bucket_time(self):
        bars = self.repository.load("EURUSD", timeframe="M5")
        self.assertEqual([bar.timestamp for bar in bars], [START, START + timedelta(minutes=5), START + timedelta(minutes=15)])
        first = bars[0]
        self.assertEqual((first.open, first.high, first.low, first.close), (100, 105, 99, 104))
        self.assertEqual((first.tick_volume, first.volume, first.spread), (3, 30, 3))

    def test_repository_aggregates_higher_timeframes(self):
        h1 = self.repository.load("EURUSD", timeframe="H1")
        h4 = self.repository.load("EURUSD", timeframe="H4")
        daily = self.repository.load("EURUSD", timeframe="D1")
        self.assertEqual(len(h1), 1)
        self.assertEqual(len(h4), 1)
        self.assertEqual(len(daily), 1)
        for aggregated in (h1[0], h4[0], daily[0]):
            self.assertEqual((aggregated.open, aggregated.high, aggregated.low, aggregated.close),
                             (100, 108, 99, 107))
            self.assertEqual((aggregated.tick_volume, aggregated.volume, aggregated.spread), (10, 100, 5))

    def test_repository_filters_source_rows_before_timeframe_aggregation(self):
        bars = self.repository.load("EURUSD", start=START + timedelta(minutes=4),
                                    end=START + timedelta(minutes=5), timeframe="M5")
        self.assertEqual(len(bars), 2)
        self.assertEqual((bars[0].open, bars[0].close), (101, 104))
        self.assertEqual((bars[1].open, bars[1].close), (104, 105))

    def test_repository_rejects_unsupported_timeframes(self):
        with self.assertRaises(ValueError):
            self.repository.load("EURUSD", timeframe="M30")

    def test_runner_passes_config_to_repository_and_returns_engine_result(self):
        strategy = ScriptedStrategy({0: Signal(OrderAction.OPEN, Side.LONG),
                                     2: Signal(OrderAction.CLOSE)})
        config = BacktestRunConfig(symbol=" eurusd ", timeframe="m15", start=START,
                                   end=START + timedelta(hours=1), initial_cash=1000,
                                   commission_per_unit=0.25, spread_scale=0,
                                   slippage=0.1, final_liquidation=False)
        result = BacktestRunner(self.repository).run(strategy=strategy, config=config)
        self.assertIs(type(result), BacktestResult)
        self.assertIsNotNone(result.open_position)
        self.assertEqual(result.total_commission, 0.25)
        self.assertEqual(result.total_spread_cost, 0)
        self.assertEqual(result.total_slippage_cost, 0.1)

    def test_runner_strategy_completes_full_signal_to_result_lifecycle(self):
        class ThresholdStrategy:
            def on_bar(self, context):
                if context.index == 0:
                    return Signal(OrderAction.OPEN, Side.LONG, 2)
                if context.index == 2:
                    return Signal(OrderAction.CLOSE)
                return None

        class InMemoryRepository:
            def load(self, instrument, start=None, end=None, *, timeframe="M1"):
                self.query = instrument, start, end, timeframe
                return fixture_bars()

        repository = InMemoryRepository()
        config = BacktestRunConfig(symbol="EURUSD", timeframe="M15", initial_cash=1000)
        result = BacktestRunner(repository).run(strategy=ThresholdStrategy(), config=config)
        self.assertEqual(repository.query, ("EURUSD", None, None, "M15"))
        self.assertEqual(len(result.orders), 2)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].gross_pnl, 2)
        self.assertEqual(result.final_equity, 1002)

    def test_runner_config_rejects_invalid_timeframe_range_and_costs(self):
        with self.assertRaises(ValueError):
            BacktestRunConfig(symbol="EURUSD", timeframe="M30")
        with self.assertRaises(ValueError):
            BacktestRunConfig(symbol="EURUSD", start=START + timedelta(days=1), end=START)
        with self.assertRaises(ValueError):
            BacktestRunConfig(symbol="EURUSD", slippage=-1)


if __name__ == "__main__":
    unittest.main()
