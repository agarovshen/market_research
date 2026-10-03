"""Deterministic performance analysis over immutable BacktestResult values."""

from dataclasses import dataclass, fields, is_dataclass
from math import isfinite, sqrt
from statistics import fmean, stdev

from app.analysis.models import (
    AnalysisResult,
    DrawdownPoint,
    DrawdownSummary,
    EquityAnalysisPoint,
    ReturnPoint,
    RiskStatistics,
    TradeStatistics,
)
from app.backtest.models import BacktestResult


@dataclass(frozen=True, slots=True)
class AnalysisSettings:
    """Risk assumptions; rates are annual effective rates expressed as decimals."""

    periods_per_year: float | None = None
    risk_free_rate: float = 0.0
    target_return: float = 0.0

    def __post_init__(self) -> None:
        if self.periods_per_year is not None and (
            not isfinite(self.periods_per_year) or self.periods_per_year <= 0
        ):
            raise ValueError("periods_per_year must be finite and positive")
        if not isfinite(self.risk_free_rate) or self.risk_free_rate <= -1:
            raise ValueError("risk_free_rate must be finite and greater than -1")
        if not isfinite(self.target_return) or self.target_return <= -1:
            raise ValueError("target_return must be finite and greater than -1")


class AnalysisEngine:
    """Calculate return, drawdown, trade, and optional annualized risk statistics."""

    def __init__(self, settings: AnalysisSettings = AnalysisSettings()):
        self.settings = settings

    def analyze(self, result: BacktestResult) -> AnalysisResult:
        """Analyze one BacktestResult without altering it or requiring market data."""
        try:
            return self._analyze(result)
        except OverflowError as error:
            raise ValueError("Analysis calculation exceeded the finite numeric range") from error

    def _analyze(self, result: BacktestResult) -> AnalysisResult:
        self._validate(result)
        start = result.initial_cash
        ending = result.final_equity

        returns: list[ReturnPoint] = []
        equity_points: list[EquityAnalysisPoint] = []
        drawdowns: list[DrawdownPoint] = []
        peak = start
        previous = start
        for point in result.equity_curve:
            value = point.equity
            period_return = self._ratio(value - previous, previous)
            cumulative_return = self._ratio(value - start, start)
            returns.append(ReturnPoint(point.timestamp, period_return, cumulative_return))
            previous = value

            if value >= peak:
                peak = value
            drawdown = peak - value
            drawdown_pct = self._ratio(value - peak, peak)
            equity_points.append(EquityAnalysisPoint(point.timestamp, value, peak))
            drawdowns.append(DrawdownPoint(point.timestamp, value, peak, drawdown, drawdown_pct))

        dd_summary = self._drawdown_summary(drawdowns)
        trades = self._trade_statistics(result)
        period_returns = tuple(point.period_return for point in returns if point.period_return is not None)
        risk = self._risk_statistics(
            period_returns,
            start,
            ending,
            dd_summary.max_drawdown_pct,
            len(period_returns) == len(returns),
        )
        analysis = AnalysisResult(
            starting_equity=start,
            ending_equity=ending,
            total_return=self._ratio(ending - start, start),
            total_profit_loss=ending - start,
            cumulative_returns=tuple(returns),
            equity_curve=result.equity_curve,
            equity_analysis=tuple(equity_points),
            drawdown_series=tuple(drawdowns),
            drawdown=dd_summary,
            trades=trades,
            risk=risk,
        )
        self._validate_analysis(analysis)
        return analysis

    @staticmethod
    def _ratio(numerator: float, denominator: float) -> float | None:
        return numerator / denominator if denominator > 0 else None

    @staticmethod
    def _drawdown_summary(points: list[DrawdownPoint]) -> DrawdownSummary:
        if not points:
            return DrawdownSummary(0.0, 0.0, None, None, None, 0)

        max_amount = max(point.drawdown for point in points)
        trough_index = next(i for i, point in enumerate(points) if point.drawdown == max_amount)
        trough = points[trough_index]
        valid_percentages = [point.drawdown_pct for point in points if point.drawdown_pct is not None]
        max_pct = min(valid_percentages) if valid_percentages else None
        if max_amount == 0 and max_pct is not None:
            max_pct = 0.0

        # Longest run below a running peak. The count is in observed equity periods,
        # so it does not presume bars are equally spaced in calendar time.
        current_underwater = longest_underwater = 0
        for point in points:
            if point.drawdown > 0:
                current_underwater += 1
                longest_underwater = max(longest_underwater, current_underwater)
            else:
                current_underwater = 0

        recovery_time = None
        if max_amount > 0:
            recovery_time = next(
                (point.timestamp for point in points[trough_index + 1:]
                 if point.peak_equity >= trough.peak_equity and point.drawdown == 0),
                None,
            )
        # A running peak may be initial capital, which has no timestamp in the input.
        max_peak_time = next(
            (point.timestamp for point in reversed(points[:trough_index + 1])
             if point.drawdown == 0 and point.equity == trough.peak_equity),
            None,
        )
        return DrawdownSummary(
            max_drawdown=max_amount,
            max_drawdown_pct=max_pct,
            peak_time=max_peak_time,
            trough_time=trough.timestamp if max_amount else None,
            recovery_time=recovery_time,
            max_duration_periods=longest_underwater,
        )

    @staticmethod
    def _trade_statistics(result: BacktestResult) -> TradeStatistics:
        trades = result.trades
        net = [trade.net_pnl for trade in trades]
        gross = [trade.gross_pnl for trade in trades]
        winners = [value for value in net if value > 0]
        losers = [value for value in net if value < 0]
        gross_profit = sum(value for value in gross if value > 0)
        gross_loss = -sum(value for value in gross if value < 0)
        count = len(trades)
        return TradeStatistics(
            total_trades=count,
            winning_trades=len(winners),
            losing_trades=len(losers),
            breakeven_trades=count - len(winners) - len(losers),
            win_rate=len(winners) / count if count else None,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            net_profit=sum(net),
            average_trade=fmean(net) if count else None,
            average_winning_trade=fmean(winners) if winners else None,
            average_losing_trade=fmean(losers) if losers else None,
            profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else None,
            largest_winner=max(winners) if winners else None,
            largest_loser=min(losers) if losers else None,
            expectancy=fmean(net) if count else None,
        )

    def _risk_statistics(
        self,
        returns: tuple[float, ...],
        starting_equity: float,
        ending_equity: float,
        max_drawdown_pct: float | None,
        returns_complete: bool,
    ) -> RiskStatistics:
        if not returns_complete:
            return RiskStatistics(None, None, None, None, None, None)
        if not all(isfinite(value) for value in returns):
            raise ValueError("Analysis calculation exceeded the finite numeric range")
        period_volatility = stdev(returns) if len(returns) >= 2 else None
        periods = self.settings.periods_per_year
        if periods is None:
            return RiskStatistics(period_volatility, None, None, None, None, None)

        annualized_volatility = period_volatility * sqrt(periods) if period_volatility is not None else None
        annualized_return = None
        if starting_equity > 0 and ending_equity > 0 and returns:
            annualized_return = (ending_equity / starting_equity) ** (periods / len(returns)) - 1

        rf_period = (1 + self.settings.risk_free_rate) ** (1 / periods) - 1
        target_period = (1 + self.settings.target_return) ** (1 / periods) - 1
        sharpe = sortino = None
        if len(returns) >= 2:
            excess = [value - rf_period for value in returns]
            excess_volatility = stdev(excess)
            if excess_volatility > 0:
                sharpe = fmean(excess) / excess_volatility * sqrt(periods)
            downside_squares = [min(value - target_period, 0.0) ** 2 for value in returns]
            downside_deviation = sqrt(fmean(downside_squares))
            if downside_deviation > 0:
                sortino = fmean(value - target_period for value in returns) / downside_deviation * sqrt(periods)

        calmar = None
        if annualized_return is not None and max_drawdown_pct is not None and max_drawdown_pct < 0:
            calmar = annualized_return / abs(max_drawdown_pct)
        return RiskStatistics(
            period_volatility,
            annualized_volatility,
            annualized_return,
            sharpe,
            sortino,
            calmar,
        )

    @staticmethod
    def _validate(result: BacktestResult) -> None:
        values = (
            result.initial_cash, result.final_cash, result.final_equity,
            result.realized_pnl, result.unrealized_pnl,
            result.total_commission, result.total_spread_cost, result.total_slippage_cost,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("BacktestResult accounting values must be finite")
        previous = None
        for point in result.equity_curve:
            if not isfinite(point.equity) or not isfinite(point.cash) or not isfinite(point.unrealized_pnl):
                raise ValueError("Equity points must contain finite values")
            if previous is not None:
                try:
                    out_of_order = point.timestamp < previous
                except TypeError as error:
                    raise ValueError("Equity timestamps must use compatible timezone awareness") from error
                if out_of_order:
                    raise ValueError("Equity points must be in chronological order")
            previous = point.timestamp
        for trade in result.trades:
            if not all(isfinite(value) for value in (trade.gross_pnl, trade.net_pnl)):
                raise ValueError("Trade PnL values must be finite")
            if trade.exit_time < trade.entry_time:
                raise ValueError("Trade exit_time cannot precede entry_time")

    @staticmethod
    def _validate_analysis(analysis: AnalysisResult) -> None:
        """Catch overflow from finite but extreme inputs instead of returning inf/NaN."""
        pending = [analysis]
        while pending:
            value = pending.pop()
            if is_dataclass(value):
                pending.extend(getattr(value, field.name) for field in fields(value))
            elif isinstance(value, tuple):
                pending.extend(value)
            elif isinstance(value, float) and not isfinite(value):
                raise ValueError("Analysis calculation exceeded the finite numeric range")
