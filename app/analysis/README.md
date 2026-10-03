# Analysis Engine

`AnalysisEngine` consumes the immutable Stage 1 `BacktestResult` and returns an
immutable `AnalysisResult`. It reads no database, market data, strategy, or UI
state and does not alter Backtest Core accounting. The equity curve remains
timestamped and in input order; equal timestamps are preserved as sequential
observations, while out-of-order or non-finite source data is rejected.

```python
from app.analysis import AnalysisEngine, AnalysisSettings

# State the sampling assumption explicitly before requesting annualized metrics.
analysis = AnalysisEngine(AnalysisSettings(periods_per_year=252)).analyze(backtest_result)
print(analysis.total_return, analysis.drawdown.max_drawdown_pct)
print(analysis.trades.win_rate, analysis.risk.sharpe_ratio)
```

## Metric conventions

- Starting equity is `BacktestResult.initial_cash`; ending equity is its
  `final_equity`. Total return is ending/start minus one when starting equity is
  positive. Total P/L is ending minus starting equity. Per-period returns use
  consecutive equity observations, with the first compared with starting cash.
- Drawdown amount is positive peak minus current equity; drawdown percentage is
  current/peak minus one (zero or negative). The running peak begins at starting
  equity, so initial capital losses are included. Maximum drawdown duration is
  the longest consecutive run of underwater observations. Recovery time is
  recorded for the maximum drawdown if a later observation returns to that
  peak. A peak at initial capital has no timestamp in the Stage 1 curve and is
  represented with `peak_time=None`.
- Trade counts, win/loss classification, averages, largest outcomes, and
  expectancy use completed trades' **net PnL**. Gross profit/loss and profit
  factor use completed trades' `gross_pnl`; gross loss is a positive magnitude.
  Profit factor is undefined (`None`) when gross loss is zero. Average groups,
  win rate, and expectancy are `None` when their denominator/count is absent.
  Trade net profit does not include unrealized PnL from any open position.
- Period volatility is sample standard deviation of simple equity returns and
  requires at least two valid observations. Annualized volatility and Sharpe,
  Sortino, Calmar, and CAGR require `periods_per_year`; no frequency is inferred
  from timestamps. Sharpe uses sample standard deviation and an annual effective
  `risk_free_rate` converted to a period rate. Sortino uses downside RMS against
  the period rate implied by annual effective `target_return`. Both ratios are
  annualized by the square root of periods per year. CAGR assumes equally spaced
  observations and requires positive start/end equity. Calmar divides CAGR by
  the absolute maximum drawdown percentage. Undefined/degenerate ratios are
  `None`, never NaN or infinity.

The result also exposes the original equity points, peak equity at every point,
period and cumulative return points, drawdown points, trade statistics, and
risk statistics as immutable tuples/dataclasses for reproducible batch research.
