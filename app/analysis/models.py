"""Immutable output types for backtest analysis."""

from dataclasses import dataclass
from datetime import datetime

from app.backtest.models import EquityPoint


@dataclass(frozen=True, slots=True)
class ReturnPoint:
    timestamp: datetime
    period_return: float | None
    cumulative_return: float | None


@dataclass(frozen=True, slots=True)
class EquityAnalysisPoint:
    timestamp: datetime
    equity: float
    peak_equity: float


@dataclass(frozen=True, slots=True)
class DrawdownPoint:
    timestamp: datetime
    equity: float
    peak_equity: float
    drawdown: float
    drawdown_pct: float | None


@dataclass(frozen=True, slots=True)
class DrawdownSummary:
    max_drawdown: float
    max_drawdown_pct: float | None
    peak_time: datetime | None
    trough_time: datetime | None
    recovery_time: datetime | None
    max_duration_periods: int


@dataclass(frozen=True, slots=True)
class TradeStatistics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate: float | None
    gross_profit: float
    gross_loss: float
    net_profit: float
    average_trade: float | None
    average_winning_trade: float | None
    average_losing_trade: float | None
    profit_factor: float | None
    largest_winner: float | None
    largest_loser: float | None
    expectancy: float | None


@dataclass(frozen=True, slots=True)
class RiskStatistics:
    period_volatility: float | None
    annualized_volatility: float | None
    annualized_return: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    starting_equity: float
    ending_equity: float
    total_return: float | None
    total_profit_loss: float
    cumulative_returns: tuple[ReturnPoint, ...]
    equity_curve: tuple[EquityPoint, ...]
    equity_analysis: tuple[EquityAnalysisPoint, ...]
    drawdown_series: tuple[DrawdownPoint, ...]
    drawdown: DrawdownSummary
    trades: TradeStatistics
    risk: RiskStatistics
