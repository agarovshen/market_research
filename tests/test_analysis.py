import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from math import sqrt

from app.analysis import AnalysisEngine, AnalysisSettings
from app.backtest import (
    BacktestEngine,
    BacktestResult,
    BacktestSettings,
    Bar,
    OrderAction,
    Side,
    Signal,
)
from app.backtest.models import EquityPoint, Trade


START = datetime(2024, 1, 1)


def make_result(equities=(), trade_pnls=(), *, initial=100.0, final=None):
    points = tuple(
        EquityPoint(START + timedelta(days=i), equity, 0.0, equity)
        for i, equity in enumerate(equities)
    )
    trades = tuple(
        Trade(i + 1, Side.LONG, 1, START, START + timedelta(days=1), 1, 1,
              0, 0, gross, net)
        for i, (gross, net) in enumerate(trade_pnls)
    )
    ending = final if final is not None else (equities[-1] if equities else initial)
    return BacktestResult(initial, ending, ending, ending - initial, 0.0,
                          0.0, 0.0, 0.0, None, (), trades, points)


class AnalysisReturnTests(unittest.TestCase):
    def test_positive_negative_and_zero_total_returns(self):
        analyze = AnalysisEngine().analyze
        self.assertEqual(analyze(make_result((110,))).total_return, 0.1)
        self.assertEqual(analyze(make_result((90,))).total_return, -0.1)
        self.assertEqual(analyze(make_result((100,))).total_return, 0.0)

    def test_return_series_and_equity_timestamp_association(self):
        result = AnalysisEngine().analyze(make_result((105, 110, 99)))
        for actual, expected in zip(
            (point.period_return for point in result.cumulative_returns),
            (0.05, 110 / 105 - 1, 99 / 110 - 1),
        ):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(
            (point.cumulative_return for point in result.cumulative_returns),
            (0.05, 0.1, -0.01),
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual([point.timestamp for point in result.equity_curve],
                         [point.timestamp for point in result.cumulative_returns])
        self.assertEqual(result.starting_equity, 100)
        self.assertEqual(result.ending_equity, 99)
        self.assertEqual(result.total_profit_loss, -1)

    def test_empty_single_and_zero_starting_equity(self):
        empty = AnalysisEngine().analyze(make_result())
        self.assertEqual(empty.cumulative_returns, ())
        self.assertEqual(empty.drawdown_series, ())
        self.assertEqual(empty.total_return, 0)
        self.assertEqual(empty.trades.total_trades, 0)
        self.assertIsNone(empty.trades.win_rate)
        self.assertIsNone(empty.trades.expectancy)

        one = AnalysisEngine().analyze(make_result((100,)))
        self.assertEqual(len(one.equity_analysis), 1)
        zero_base = AnalysisEngine().analyze(make_result((1, 0), initial=0))
        self.assertIsNone(zero_base.total_return)
        self.assertTrue(all(point.cumulative_return is None for point in zero_base.cumulative_returns))

        tiny = AnalysisEngine().analyze(make_result((2e-300,), initial=1e-300))
        self.assertAlmostEqual(tiny.total_return, 1.0)

    def test_duplicate_timestamps_are_preserved_in_input_order(self):
        result = make_result((100, 101))
        duplicate = replace(result, equity_curve=(
            result.equity_curve[0],
            replace(result.equity_curve[1], timestamp=START),
        ))
        analysis = AnalysisEngine().analyze(duplicate)
        self.assertEqual([point.timestamp for point in analysis.equity_analysis], [START, START])
        self.assertEqual([point.equity for point in analysis.equity_analysis], [100, 101])

    def test_chronology_and_nonfinite_input_are_rejected(self):
        result = make_result((100, 101))
        out_of_order = replace(result, equity_curve=tuple(reversed(result.equity_curve)))
        with self.assertRaisesRegex(ValueError, "chronological"):
            AnalysisEngine().analyze(out_of_order)
        invalid = replace(result, final_equity=float("nan"))
        with self.assertRaisesRegex(ValueError, "finite"):
            AnalysisEngine().analyze(invalid)

    def test_returns_and_risk_are_undefined_across_zero_equity(self):
        result = AnalysisEngine(AnalysisSettings(periods_per_year=252)).analyze(
            make_result((0, 100, 110), initial=100)
        )
        self.assertIsNone(result.cumulative_returns[1].period_return)
        self.assertTrue(all(value is None for value in (
            result.risk.period_volatility,
            result.risk.annualized_volatility,
            result.risk.annualized_return,
            result.risk.sharpe_ratio,
            result.risk.sortino_ratio,
            result.risk.calmar_ratio,
        )))

    def test_finite_inputs_that_overflow_analysis_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, "finite numeric range"):
            AnalysisEngine().analyze(make_result((1e308, -1e308), initial=1e308, final=1e308))


class DrawdownTests(unittest.TestCase):
    def test_hand_calculated_peak_trough_recovery_and_duration(self):
        analysis = AnalysisEngine().analyze(make_result((100, 110, 105, 90, 120)))
        self.assertEqual([point.peak_equity for point in analysis.equity_analysis],
                         [100, 110, 110, 110, 120])
        self.assertEqual([point.drawdown for point in analysis.drawdown_series], [0, 0, 5, 20, 0])
        self.assertEqual([point.drawdown_pct for point in analysis.drawdown_series],
                         [0, 0, -5 / 110, -20 / 110, 0])
        summary = analysis.drawdown
        self.assertEqual(summary.max_drawdown, 20)
        self.assertAlmostEqual(summary.max_drawdown_pct, -20 / 110)
        self.assertEqual(summary.peak_time, START + timedelta(days=1))
        self.assertEqual(summary.trough_time, START + timedelta(days=3))
        self.assertEqual(summary.recovery_time, START + timedelta(days=4))
        self.assertEqual(summary.max_duration_periods, 2)

    def test_initial_capital_peak_and_unrecovered_multiple_drawdowns(self):
        analysis = AnalysisEngine().analyze(make_result((95, 98, 90, 99, 97)))
        self.assertEqual(analysis.drawdown.max_drawdown, 10)
        self.assertIsNone(analysis.drawdown.peak_time)  # initial equity has no curve timestamp
        self.assertIsNone(analysis.drawdown.recovery_time)
        self.assertEqual(analysis.drawdown.max_duration_periods, 5)

    def test_recovered_drawdown_episodes_keep_their_own_peak_and_recovery(self):
        analysis = AnalysisEngine().analyze(make_result((100, 110, 100, 110, 100, 110)))
        self.assertEqual(analysis.drawdown.max_drawdown, 10)
        self.assertEqual(analysis.drawdown.peak_time, START + timedelta(days=1))
        self.assertEqual(analysis.drawdown.trough_time, START + timedelta(days=2))
        self.assertEqual(analysis.drawdown.recovery_time, START + timedelta(days=3))
        self.assertEqual(analysis.drawdown.max_duration_periods, 1)

    def test_monotonic_and_flat_equity_have_zero_drawdown(self):
        for values in ((100, 105, 110), (100, 100, 100)):
            with self.subTest(values=values):
                analysis = AnalysisEngine().analyze(make_result(values))
                self.assertEqual(analysis.drawdown.max_drawdown, 0)
                self.assertEqual(analysis.drawdown.max_drawdown_pct, 0)
                self.assertIsNone(analysis.drawdown.trough_time)

    def test_zero_peak_makes_percentage_drawdown_undefined(self):
        analysis = AnalysisEngine().analyze(make_result((0, -1), initial=0))
        self.assertEqual(analysis.drawdown.max_drawdown, 1)
        self.assertIsNone(analysis.drawdown.max_drawdown_pct)


class TradeStatisticTests(unittest.TestCase):
    def test_mixed_trade_statistics_use_net_outcomes_and_gross_profit_factor(self):
        analysis = AnalysisEngine().analyze(make_result(
            (100,), ((12, 10), (-6, -8), (0, 0), (5, 4), (-2, -3))
        )).trades
        self.assertEqual(analysis.total_trades, 5)
        self.assertEqual((analysis.winning_trades, analysis.losing_trades,
                          analysis.breakeven_trades), (2, 2, 1))
        self.assertEqual(analysis.win_rate, 0.4)
        self.assertEqual(analysis.gross_profit, 17)
        self.assertEqual(analysis.gross_loss, 8)
        self.assertEqual(analysis.net_profit, 3)
        self.assertEqual(analysis.average_trade, 0.6)
        self.assertEqual(analysis.average_winning_trade, 7)
        self.assertEqual(analysis.average_losing_trade, -5.5)
        self.assertEqual(analysis.profit_factor, 17 / 8)
        self.assertEqual(analysis.largest_winner, 10)
        self.assertEqual(analysis.largest_loser, -8)
        self.assertEqual(analysis.expectancy, 0.6)

    def test_one_trade_win_loss_breakeven_and_zero_gross_loss(self):
        win = AnalysisEngine().analyze(make_result((100,), ((5, 4),))).trades
        self.assertEqual((win.winning_trades, win.losing_trades, win.breakeven_trades), (1, 0, 0))
        self.assertIsNone(win.profit_factor)  # undefined without any gross losses
        loss = AnalysisEngine().analyze(make_result((100,), ((-5, -5),))).trades
        self.assertEqual(loss.profit_factor, 0)
        tie = AnalysisEngine().analyze(make_result((100,), ((0, 0),))).trades
        self.assertEqual(tie.breakeven_trades, 1)
        self.assertIsNone(tie.average_winning_trade)
        self.assertIsNone(tie.average_losing_trade)

    def test_all_winners_and_all_losers(self):
        winners = AnalysisEngine().analyze(make_result((100,), ((2, 1), (3, 2)))).trades
        losers = AnalysisEngine().analyze(make_result((100,), ((-2, -1), (-3, -2)))).trades
        self.assertIsNone(winners.profit_factor)
        self.assertEqual(winners.gross_loss, 0)
        self.assertEqual(losers.profit_factor, 0)
        self.assertEqual(losers.gross_profit, 0)


class RiskMetricTests(unittest.TestCase):
    def test_period_and_annualized_risk_metrics_against_hand_calculation(self):
        analysis = AnalysisEngine(AnalysisSettings(periods_per_year=4)).analyze(
            make_result((110, 99, 108.9))
        ).risk
        returns = (0.1, -0.1, 0.1)
        period_vol = sqrt(sum((x - (1 / 30)) ** 2 for x in returns) / 2)
        self.assertAlmostEqual(analysis.period_volatility, period_vol)
        self.assertAlmostEqual(analysis.annualized_volatility, period_vol * 2)
        expected_sharpe = (1 / 30) / period_vol * 2
        self.assertAlmostEqual(analysis.sharpe_ratio, expected_sharpe)
        expected_sortino = (1 / 30) / (0.1 / sqrt(3)) * 2
        self.assertAlmostEqual(analysis.sortino_ratio, expected_sortino)
        annual_return = 1.089 ** (4 / 3) - 1
        self.assertAlmostEqual(analysis.annualized_return, annual_return)
        self.assertAlmostEqual(analysis.calmar_ratio, annual_return / 0.1)

    def test_annualized_metrics_require_frequency_and_handle_degenerate_series(self):
        no_frequency = AnalysisEngine().analyze(make_result((100, 110))).risk
        self.assertAlmostEqual(no_frequency.period_volatility, 0.07071067811865475)
        self.assertIsNone(no_frequency.annualized_volatility)
        self.assertIsNone(no_frequency.sharpe_ratio)

        single = AnalysisEngine(AnalysisSettings(periods_per_year=252)).analyze(
            make_result((110,))
        ).risk
        self.assertIsNone(single.period_volatility)
        self.assertIsNone(single.sharpe_ratio)

        flat = AnalysisEngine(AnalysisSettings(periods_per_year=252)).analyze(
            make_result((100, 100, 100))
        ).risk
        self.assertEqual(flat.period_volatility, 0)
        self.assertEqual(flat.annualized_volatility, 0)
        self.assertIsNone(flat.sharpe_ratio)
        self.assertIsNone(flat.sortino_ratio)
        self.assertIsNone(flat.calmar_ratio)

    def test_zero_downside_deviation_and_nonpositive_final_equity(self):
        rising = AnalysisEngine(AnalysisSettings(periods_per_year=2)).analyze(
            make_result((110, 120, 130))
        ).risk
        self.assertIsNone(rising.sortino_ratio)
        bankrupt = AnalysisEngine(AnalysisSettings(periods_per_year=2)).analyze(
            make_result((80, 0), final=0)
        ).risk
        self.assertIsNone(bankrupt.annualized_return)
        self.assertIsNone(bankrupt.calmar_ratio)

    def test_analysis_settings_validation(self):
        with self.assertRaises(ValueError):
            AnalysisSettings(periods_per_year=0)
        with self.assertRaises(ValueError):
            AnalysisSettings(risk_free_rate=-1)


class BacktestIntegrationTests(unittest.TestCase):
    def test_real_engine_result_flows_into_analysis_without_result_changes(self):
        class Strategy:
            def on_bar(self, context):
                if context.index == 0:
                    return Signal(OrderAction.OPEN, Side.LONG, 1)
                if context.index == 2:
                    return Signal(OrderAction.CLOSE)
                return None

        bars = tuple(
            Bar(START + timedelta(days=i), value, value, value, value)
            for i, value in enumerate((100, 100, 110, 110))
        )
        backtest = BacktestEngine(BacktestSettings(initial_cash=1000)).run(bars, Strategy())
        analysis = AnalysisEngine().analyze(backtest)
        self.assertEqual(len(backtest.trades), 1)
        self.assertEqual(backtest.trades[0].gross_pnl, 10)
        self.assertEqual(analysis.trades.net_profit, 10)
        self.assertEqual(analysis.total_profit_loss, 10)
        self.assertEqual(analysis.ending_equity, backtest.final_equity)
        self.assertEqual(len(analysis.equity_curve), len(bars))
        self.assertEqual(analysis, AnalysisEngine().analyze(backtest))


if __name__ == "__main__":
    unittest.main()
