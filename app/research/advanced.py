"""Explicit, deterministic advanced research calculations over Stage 1/2 outputs."""

from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_left
from math import isclose, isfinite, sqrt
from random import Random
from statistics import fmean, stdev
from typing import Iterable, Mapping

from app.analysis import AnalysisResult
from app.backtest import BacktestResult
from app.research.canonical import sha256_json
from app.research.models import (
    DateRange, ExperimentStatus, ResearchResult, SelectionRule, WalkForwardResult,
)


@dataclass(frozen=True, slots=True)
class SensitivityCell:
    parameters: tuple[tuple[str, object], ...]
    metric: float


@dataclass(frozen=True, slots=True)
class ParameterSensitivity:
    metric: str
    parameter_names: tuple[str, ...]
    cells: tuple[SensitivityCell, ...]
    dispersion: float | None
    mean_absolute_neighbor_change: float | None
    neighbor_change_by_parameter: tuple[tuple[str, float | None], ...]


def _metric(analysis: AnalysisResult, path: str) -> float | None:
    value = analysis
    for component in path.split("."):
        value = getattr(value, component)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"Metric {path!r} is not a finite scalar")
    return float(value)


def parameter_sensitivity(
    results: Iterable[ResearchResult], metric: str, parameter_names: Iterable[str],
) -> ParameterSensitivity:
    """Summarize successful experiments in stable lexicographic parameter order."""
    names = tuple(parameter_names)
    if not names or len(set(names)) != len(names):
        raise ValueError("Sensitivity requires unique parameter names")
    cells = []
    scope = None
    seen_parameters = set()
    for result in results:
        if result.status is not ExperimentStatus.COMPLETED:
            continue
        params = result.definition.parameters.as_dict()
        if any(name not in params for name in names):
            raise ValueError("A result is missing a requested sensitivity parameter")
        definition = result.definition
        if hasattr(definition, "strategy_id"):
            current_scope = (definition.strategy_id, definition.strategy_version, definition.symbol,
                             definition.timeframe, definition.period, definition.dataset_fingerprint,
                             definition.phase, definition.backtest_config, definition.analysis_config)
            if scope is None:
                scope = current_scope
            elif current_scope != scope:
                raise ValueError("Sensitivity inputs must share strategy, data period, phase, and settings")
        parameter_key = tuple((name, params[name]) for name in names)
        if parameter_key in seen_parameters:
            raise ValueError("Sensitivity inputs contain duplicate parameter configurations")
        seen_parameters.add(parameter_key)
        value = _metric(result.analysis_result, metric)
        if value is not None:
            cells.append(SensitivityCell(tuple((name, params[name]) for name in names), value))
    cells.sort(key=lambda cell: tuple((type(value).__name__, value) for _, value in cell.parameters))
    values = [cell.metric for cell in cells]
    dispersion = stdev(values) if len(values) > 1 else (0.0 if values else None)
    neighbor_by_parameter = []
    for name in names:
        grouped = {}
        for cell in cells:
            mapping = dict(cell.parameters)
            other = tuple((key, value) for key, value in cell.parameters if key != name)
            grouped.setdefault(other, []).append((mapping[name], cell.metric))
        changes = []
        for group in grouped.values():
            group.sort(key=lambda pair: (type(pair[0]).__name__, pair[0]))
            changes.extend(abs(right[1] - left[1]) for left, right in zip(group, group[1:]))
        neighbor_by_parameter.append((name, fmean(changes) if changes else None))
    neighbor_values = [value for _, value in neighbor_by_parameter if value is not None]
    return ParameterSensitivity(metric, names, tuple(cells), dispersion,
                                fmean(neighbor_values) if neighbor_values else None,
                                tuple(neighbor_by_parameter))


@dataclass(frozen=True, slots=True)
class RobustnessScenario:
    name: str
    commission_multiplier: float = 1.0
    spread_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    commission_per_unit_addition: float = 0.0
    slippage_addition: float = 0.0
    position_size_multiplier: float = 1.0
    parameter_overrides: tuple[tuple[str, object], ...] = ()
    period: DateRange | None = None

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("Robustness scenario requires a name")
        if any(not isfinite(value) or value < 0 for value in
               (self.commission_multiplier, self.spread_multiplier, self.slippage_multiplier,
                self.position_size_multiplier)):
            raise ValueError("Execution assumption multipliers must be finite and nonnegative")
        if any(not isfinite(value) or value < 0 for value in
               (self.commission_per_unit_addition, self.slippage_addition)):
            raise ValueError("Additional costs must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class RobustnessPoint:
    name: str
    metric: float | None
    experiment_id: str | None
    status: str
    failure_message: str | None = None


@dataclass(frozen=True, slots=True)
class RobustnessResult:
    metric: str
    baseline_experiment_id: str
    points: tuple[RobustnessPoint, ...]


def evaluate_robustness(
    baseline: ResearchResult,
    scenarios: Iterable[RobustnessScenario],
    run_scenario,
    metric: str,
) -> RobustnessResult:
    """Run caller-defined controlled scenarios; no assumed robustness threshold."""
    if baseline.status is not ExperimentStatus.COMPLETED:
        raise ValueError("A completed baseline result is required")
    SelectionRule(metric)
    points = []
    for scenario in scenarios:
        try:
            result = run_scenario(scenario)
            if result.status is ExperimentStatus.COMPLETED:
                value = _metric(result.analysis_result, metric)
                points.append(RobustnessPoint(scenario.name, value,
                                              result.definition.experiment_id, "completed"))
            else:
                points.append(RobustnessPoint(scenario.name, None,
                                              result.definition.experiment_id, "failed",
                                              result.failure.message if result.failure else "scenario failed"))
        except Exception as error:
            points.append(RobustnessPoint(scenario.name, None, None, "failed",
                                          f"{type(error).__name__}: {error}"))
    return RobustnessResult(metric, baseline.definition.experiment_id, tuple(points))


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    method: str
    seed: int
    simulations: int
    terminal_equity: tuple[float, ...]
    terminal_return: tuple[float, ...]
    max_drawdown: tuple[float, ...]
    terminal_percentiles: tuple[tuple[str, float], ...]
    return_percentiles: tuple[tuple[str, float], ...]
    drawdown_percentiles: tuple[tuple[str, float], ...]
    assumptions: str


def monte_carlo_trades(
    result: BacktestResult, *, simulations: int, seed: int, method: str = "bootstrap",
) -> MonteCarloResult:
    """Resample net trade PnL with replacement or permute its order.

    Paths start at initial_cash. Drawdown is peak-relative absolute cash loss.
    Bootstrap assumes trades are exchangeable; shuffle preserves trade outcomes
    and therefore terminal equity. Intra-trade paths and costs are not resimulated.
    """
    if isinstance(simulations, bool) or not isinstance(simulations, int) or simulations < 1:
        raise ValueError("simulations must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if method not in {"bootstrap", "shuffle"}:
        raise ValueError("method must be 'bootstrap' or 'shuffle'")
    if result.open_position is not None:
        raise ValueError("Monte Carlo trade resampling requires a fully closed backtest result")
    pnl = tuple(trade.net_pnl for trade in result.trades)
    if not pnl:
        raise ValueError("Monte Carlo requires at least one completed trade")
    if not isfinite(result.initial_cash) or result.initial_cash <= 0 or any(not isfinite(value) for value in pnl):
        raise ValueError("Monte Carlo inputs require positive finite initial cash and finite trade PnL")
    rng = Random(seed)
    terminal, terminal_returns, drawdowns = [], [], []
    for _ in range(simulations):
        path = [rng.choice(pnl) for _ in pnl] if method == "bootstrap" else rng.sample(pnl, len(pnl))
        equity = peak = result.initial_cash
        max_drawdown = 0.0
        for trade_pnl in path:
            equity += trade_pnl
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        terminal.append(equity)
        terminal_returns.append(equity / result.initial_cash - 1)
        drawdowns.append(max_drawdown)
    return MonteCarloResult(method, seed, simulations, tuple(terminal), tuple(terminal_returns),
                            tuple(drawdowns), _percentiles(terminal), _percentiles(terminal_returns),
                            _percentiles(drawdowns),
                            "Completed trade net PnL resampled as independent/exchangeable units; "
                            "no intratrade path, open positions, or new transaction costs are modeled.")


def _percentiles(values: list[float]) -> tuple[tuple[str, float], ...]:
    ordered = sorted(values)
    output = []
    for label, probability in (("p05", .05), ("p25", .25), ("p50", .5), ("p75", .75), ("p95", .95)):
        index = probability * (len(ordered) - 1)
        low = int(index)
        high = min(low + 1, len(ordered) - 1)
        output.append((label, ordered[low] + (ordered[high] - ordered[low]) * (index - low)))
    return tuple(output)


@dataclass(frozen=True, slots=True)
class RegimeDefinition:
    lookback: int
    volatility_threshold: float
    trend_threshold: float = 0.0

    def __post_init__(self):
        if isinstance(self.lookback, bool) or not isinstance(self.lookback, int) or self.lookback < 2:
            raise ValueError("Regime lookback must be an integer >= 2")
        if (not isfinite(self.volatility_threshold) or self.volatility_threshold < 0
                or not isfinite(self.trend_threshold) or self.trend_threshold < 0):
            raise ValueError("Regime thresholds must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class RegimeObservation:
    timestamp: object
    label: str


@dataclass(frozen=True, slots=True)
class RegimeStatistics:
    label: str
    observations: int
    trades: int
    net_pnl: float
    average_trade: float | None


@dataclass(frozen=True, slots=True)
class RegimeResult:
    definition: RegimeDefinition
    observations: tuple[RegimeObservation, ...]
    statistics: tuple[RegimeStatistics, ...]
    trade_regimes: tuple[tuple[int, str], ...]


def analyze_regimes(result: BacktestResult, bars, definition: RegimeDefinition) -> RegimeResult:
    """Classify each bar from trailing returns only, then assign trades by entry bar."""
    data = tuple(bars)
    previous_timestamp = None
    if any(not isfinite(bar.close) for bar in data):
        raise ValueError("Regime bars require finite closes")
    for bar in data:
        if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
            raise ValueError("Regime bars must be strictly chronological")
        previous_timestamp = bar.timestamp
    previous_close = None
    returns = []
    observations = []
    timestamps = []
    for bar in data:
        value = None if previous_close in (None, 0) else bar.close / previous_close - 1
        returns.append(value)
        previous_close = bar.close
        history = [item for item in returns[max(0, len(returns) - definition.lookback):] if item is not None]
        if len(history) < definition.lookback - 1:
            label = "insufficient_history"
        else:
            volatility = stdev(history) if len(history) > 1 else 0.0
            trend = fmean(history)
            regime = "high_volatility" if volatility >= definition.volatility_threshold else "low_volatility"
            if trend > definition.trend_threshold:
                direction = "up"
            elif trend < -definition.trend_threshold:
                direction = "down"
            else:
                direction = "neutral"
            label = f"{regime}:{direction}"
        observations.append(RegimeObservation(bar.timestamp, label))
        timestamps.append(bar.timestamp)
    buckets: dict[str, list[float]] = {}
    trade_regimes = []
    for trade in result.trades:
        # A fill happens at the bar open; only a prior completed bar can define
        # the regime available to the strategy at that instant.
        prior_index = bisect_left(timestamps, trade.entry_time) - 1
        label = observations[prior_index].label if prior_index >= 0 else None
        if label is not None:
            buckets.setdefault(label, []).append(trade.net_pnl)
            trade_regimes.append((trade.sequence, label))
    all_labels = sorted({observation.label for observation in observations})
    stats = tuple(RegimeStatistics(label, sum(item.label == label for item in observations),
                                   len(buckets.get(label, ())), sum(buckets.get(label, ())),
                                   fmean(buckets[label]) if buckets.get(label) else None)
                  for label in all_labels)
    return RegimeResult(definition, tuple(observations), stats, tuple(trade_regimes))


@dataclass(frozen=True, slots=True)
class CorrelationMatrix:
    labels: tuple[str, ...]
    timestamps: tuple[object, ...]
    coefficients: tuple[tuple[float | None, ...], ...]


def correlate_equity_returns(results: Mapping[str, AnalysisResult]) -> CorrelationMatrix:
    """Pearson correlation on per-observation returns with exactly aligned timestamps."""
    if len(results) < 2 or len(set(results)) != len(results):
        raise ValueError("Correlation requires at least two uniquely named result series")
    labels = tuple(sorted(results))
    timestamp_sets = [tuple(point.timestamp for point in results[name].equity_curve) for name in labels]
    if any(timestamps != timestamp_sets[0] for timestamps in timestamp_sets[1:]):
        raise ValueError("Correlation input series must have identical observation timestamps")
    series = {}
    for name in labels:
        points = results[name].equity_curve
        values = {}
        previous = results[name].starting_equity
        for point in points:
            if previous == 0:
                values[point.timestamp] = None
            else:
                values[point.timestamp] = point.equity / previous - 1
            previous = point.equity
        series[name] = values
    timestamps = timestamp_sets[0]
    matrix = []
    for left in labels:
        row = []
        for right in labels:
            pairs = [(series[left][timestamp], series[right][timestamp]) for timestamp in timestamps]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            if len(pairs) < 2:
                row.append(None)
                continue
            a_values, b_values = [item[0] for item in pairs], [item[1] for item in pairs]
            a_mean, b_mean = fmean(a_values), fmean(b_values)
            numerator = sum((a - a_mean) * (b - b_mean) for a, b in pairs)
            denominator = sqrt(sum((a - a_mean) ** 2 for a in a_values) *
                               sum((b - b_mean) ** 2 for b in b_values))
            row.append(numerator / denominator if denominator else None)
        matrix.append(tuple(row))
    return CorrelationMatrix(labels, timestamps, tuple(matrix))


@dataclass(frozen=True, slots=True)
class PortfolioPoint:
    timestamp: object
    equity: float
    period_return: float


@dataclass(frozen=True, slots=True)
class PortfolioResearchResult:
    initial_capital: float
    weights: tuple[tuple[str, float], ...]
    points: tuple[PortfolioPoint, ...]
    assumptions: str


@dataclass(frozen=True, slots=True)
class AdvancedResearchResult:
    """Persistable advanced output, linked to immutable source experiment IDs."""

    method: str
    input_experiment_ids: tuple[str, ...]
    configuration: tuple[tuple[str, object], ...]
    output: (ParameterSensitivity | RobustnessResult | MonteCarloResult |
             RegimeResult | CorrelationMatrix | PortfolioResearchResult | WalkForwardAggregate)

    @property
    def analysis_id(self) -> str:
        return sha256_json(("advanced-research-v1", self))

    def __post_init__(self):
        if not self.method or not self.input_experiment_ids:
            raise ValueError("Advanced research outputs require a method and source experiment IDs")
        if len(set(self.input_experiment_ids)) != len(self.input_experiment_ids):
            raise ValueError("Advanced analysis source experiment IDs must be unique")


def weighted_rebalanced_portfolio(
    results: Mapping[str, AnalysisResult], weights: Mapping[str, float], initial_capital: float,
) -> PortfolioResearchResult:
    """Combine net strategy returns with periodic target-weight rebalancing.

    Uses exact common timestamps, applies supplied fixed weights per observation,
    and assumes cost-free rebalance. It is research math, not portfolio execution.
    """
    if set(results) != set(weights) or not results:
        raise ValueError("Portfolio weights must match the supplied strategies")
    if not isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError("initial_capital must be positive and finite")
    if any(not isfinite(value) or value < 0 for value in weights.values()):
        raise ValueError("Portfolio weights must be finite and nonnegative")
    if not isclose(sum(weights.values()), 1.0, rel_tol=0, abs_tol=1e-10):
        raise ValueError("Portfolio weights must sum to 1")
    timestamp_sets = [tuple(point.timestamp for point in result.equity_curve)
                      for result in results.values()]
    if any(timestamps != timestamp_sets[0] for timestamps in timestamp_sets[1:]):
        raise ValueError("Portfolio input series must have identical observation timestamps")
    timestamps = timestamp_sets[0]
    prior_equity = {name: results[name].starting_equity for name in results}
    point_by_name = {name: {point.timestamp: point.equity for point in value.equity_curve}
                     for name, value in results.items()}
    portfolio_equity = initial_capital
    points = []
    for timestamp in timestamps:
        period_return = 0.0
        for name in results:
            current = point_by_name[name][timestamp]
            start = prior_equity[name]
            if start <= 0:
                raise ValueError("Underlying equity must remain positive for return-weighted portfolio")
            period_return += weights[name] * (current / start - 1)
            prior_equity[name] = current
        portfolio_equity *= 1 + period_return
        points.append(PortfolioPoint(timestamp, portfolio_equity, period_return))
    return PortfolioResearchResult(initial_capital, tuple(sorted(weights.items())), tuple(points),
                                   "Fixed target weights rebalanced at every common equity timestamp; "
                                   "underlying returns are net of their original costs; portfolio rebalance "
                                   "costs, margin, exposure overlap, and capital constraints are not modeled.")


def drawdown_from_equity(equity: Iterable[float]) -> tuple[float, ...]:
    """Utility for advanced simulations; positive absolute peak-to-trough loss."""
    peak = None
    result = []
    for value in equity:
        if not isfinite(value):
            raise ValueError("Equity inputs must be finite")
        peak = value if peak is None else max(peak, value)
        result.append(peak - value)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class WalkForwardWindowAggregate:
    index: int
    training_period: DateRange
    testing_period: DateRange
    completed_selected_candidates: int
    failed_selected_candidates: int
    experiment_ids: tuple[str, ...]
    net_profit: float
    return_values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardAggregate:
    candidate_rank: int
    window_summaries: tuple[WalkForwardWindowAggregate, ...]
    total_net_profit: float
    completed_window_count: int
    assumptions: str = (
        "Uses the selected training rank independently in each OOS window. "
        "Dollar net PnL is summed without carrying or compounding capital across windows."
    )


def aggregate_walk_forward(result: WalkForwardResult, *, candidate_rank: int = 1) -> WalkForwardAggregate:
    """Summarize one explicit training rank while retaining every original window.

    Dollar PnL is summed across independent window runs, without compounding or
    pretending that each window's initial capital is carried into the next.
    """
    if isinstance(candidate_rank, bool) or not isinstance(candidate_rank, int) or candidate_rank < 1:
        raise ValueError("candidate_rank must be a positive integer")
    summaries = []
    total_profit = 0.0
    completed_windows = 0
    for window in result.windows:
        selected = [item for item in window.result.testing.results
                    if item.definition.selected_rank == candidate_rank]
        completed = [item for item in selected if item.status is ExperimentStatus.COMPLETED]
        failed = [item for item in selected if item.status is ExperimentStatus.FAILED]
        profit = sum(item.analysis_result.trades.net_profit for item in completed)
        returns = tuple(item.analysis_result.total_return for item in completed
                        if item.analysis_result.total_return is not None)
        total_profit += profit
        completed_windows += bool(completed)
        summaries.append(WalkForwardWindowAggregate(
            window.index, window.split.training, window.split.testing, len(completed), len(failed),
            tuple(item.definition.experiment_id for item in selected), profit, returns))
    return WalkForwardAggregate(candidate_rank, tuple(summaries),
                                total_profit, completed_windows)
