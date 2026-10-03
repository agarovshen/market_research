# Research Engine

The Research Engine coordinates the existing `BacktestRunner` and
`AnalysisEngine`; it does not implement execution, accounting, or metrics.
`ParameterSpace` supplies deterministic grid or seeded random `ParameterSet`s.
Each result records strategy identity/version, parameters and their specification,
the half-open data period, a market-data fingerprint, backtest/analysis settings,
seed/search metadata, software versions, status, and complete Stage 1/2 results.

```python
from datetime import datetime

from app.analysis import AnalysisSettings
from app.backtest import BacktestRunConfig, BacktestRunner, MarketDataRepository
from app.database import SessionLocal
from app.research import (
    IntegerRange, ParameterSpace, ResearchEngine, SelectionRule,
    DateRange, TrainTestSplit,
)
from app.research.storage import ResearchResultRepository

# Supply a versioned StrategyFactory with validate(parameters) and create(parameters).
space = ParameterSpace((IntegerRange("lookback", 5, 30, step=5),))
with SessionLocal() as session:
    results = ResearchEngine(
        BacktestRunner(MarketDataRepository(session)),
        result_store=ResearchResultRepository(session),
    ).run_batch(
        space.grid(), strategy_factory=my_strategy_factory,
        config=BacktestRunConfig(
            symbol="EURUSD", timeframe="M15",
            start=datetime(2022, 1, 1), end=datetime(2023, 1, 1),
            initial_cash=100_000,
        ),
        parameter_space=space, search_method="grid",
        analysis_settings=AnalysisSettings(periods_per_year=252),
    )
    session.commit()  # repository flushes in one batch; caller owns transaction
```

## Integrity and workflow

- Research periods are half-open `[start, end)`. Train and test may touch at a
  boundary but cannot overlap. `MarketDataRepository` retains its existing
  inclusive `end`; research uses the additive `end_exclusive` query so a bar at
  the split boundary cannot leak into training. Higher-timeframe partial bars
  labeled before a period start are excluded.
- `ParameterSpace.grid()` yields a lazy Cartesian product in specification
  order. `ParameterSpace.random(count, seed=...)` uses a private seeded RNG and
  yields unique configurations or raises if it cannot satisfy the requested
  count. Pass both the seed and parameter space into the engine so experiment
  metadata is sufficient to reproduce generation.
- Each phase loads its market data once, fingerprints it, and shares the
  immutable bars across candidate runs. Strategy-specific constraints live in
  the versioned factory's `validate` method. Invalid candidates and strategy
  failures become explicit failed results; repository/data-load errors still
  propagate to the caller.
- `run_out_of_sample` first evaluates and analyzes all candidates on training
  data. The explicit `SelectionRule` reads only training `AnalysisResult`
  metrics. Only selected parameter sets are then run on test data; no test
  result is accepted by the selection API. `run_walk_forward` repeats this
  sequence using explicit anchored or rolling training windows and disjoint
  OOS windows.
- Results retain the total candidate count, candidate-set fingerprint,
  selection rule/rank, source training experiment ID, and walk-forward index.
  Deterministic IDs hash canonical definition content (including strategy
  version, configs, parameter set, data fingerprint, phase, and software
  version), not timestamps or random UUIDs. Persistence uses the existing
  SQLAlchemy database and a tagged, versioned JSON payload; it never pickles
  Python objects. Apply the new Alembic migration before persisting to
  PostgreSQL.

The number of configurations tried and the selection process are retained for
later multiple-testing analysis. Selecting the strongest training metric among
many candidates creates selection bias. A clean OOS result is useful evidence,
not proof of future validity; Stage 4 statistical validation is still required.

The current runner is sequential by design. It isolates per-candidate strategy
and backtest failures, but a data access error or persistence transaction error
fails the workflow. The caller should commit or roll back the supplied database
session after a research operation.

## Advanced research and workspace

The Stage 4 domain lives in app.research.advanced; app.research.api is the
FastAPI boundary and app.research.service adapts UI requests to the Stage 3
engine. Open /research for the vanilla JavaScript research workspace. It
offers a single run, a grid or seeded random batch, explicit train/OOS
selection, and anchored/rolling walk-forward. The current versioned strategy
registry contains a simple long-only SMA crossover so the workflow is runnable;
it is an example strategy, not a performance recommendation.

Advanced analyses accept stored Stage 1/2 results and preserve input experiment
IDs and configuration in research_analyses. The new table is added by the
same Stage 4 Alembic migration as experiments. Advanced result IDs hash the
canonical method, inputs, assumptions, and output. Use
ResearchAnalysisRepository.get(analysis_id) to retrieve an output.

Implemented calculations and their conventions:

* Sensitivity reports deterministic parameter cells, sample standard
  deviation, and mean absolute metric change between adjacent values while
  holding other parameters fixed. Inputs must share strategy/version, data,
  phase, and settings. It reports measurements and does not label stable or
  unstable regions.
* Robustness reruns the existing Research Engine under explicit parameter,
  commission, spread, slippage, position-size, and contained date-window
  perturbations. It adds no trading economics. Each scenario remains a saved
  experiment with a reported failure or metric; no pass threshold is implied.
* Monte Carlo resamples closed-trade net PnL with replacement (bootstrap)
  or permutes the observed trade order (shuffle). It reports terminal equity,
  terminal return, and absolute peak-to-trough drawdown distributions using
  linearly interpolated p05/p25/p50/p75/p95 percentiles. Trades are assumed
  exchangeable for bootstrap. Inputs with an open position are rejected. Neither
  method models intra-trade paths or new costs.
* Regimes use trailing close-to-close simple returns. Volatility is sample
  standard deviation over lookback - 1 returns; direction is the mean of
  those returns compared with explicit thresholds. A trade is assigned the
  regime known at the last completed bar strictly before its entry fill, so its
  execution bar close cannot leak into that trade's classification. Regime
  re-analysis checks that current stored bars still match the experiment's
  dataset fingerprint.
* Correlation is Pearson correlation of per-equity-observation simple returns.
  Series must have identical timestamps; fewer than two observations or a
  constant series yields None. Timeframes are not resampled implicitly.
* Annualized volatility/return and Sharpe/Sortino use the caller-supplied
  AnalysisSettings annualization periods, annual effective risk-free rate,
  and target return. If periods/year is omitted, annualized ratios remain
  undefined and are unavailable for selection.
* The weighted research curve applies nonnegative fixed target weights that
  sum to one and rebalances at every shared observation. It combines strategy
  returns net of their existing costs. Portfolio rebalance costs, margin,
  exposure overlap, and capital constraints are not modeled; this is not a
  portfolio execution engine.
* Walk-forward aggregation chooses an explicit selected training rank (rank
  one by default), retains every original window, and sums independent window
  net PnL without compounding or implying capital carryover.

The current Backtest Core supports one instrument and one net position, fixed
quantity, and its existing commission/spread/slippage configuration. The
Research GUI can vary fixed position size and Stage 4 can perturb it, but
per-trade risk budgeting, stop loss/take profit, max exposure, leverage, and
portfolio capital constraints need Stage 1 accounting support and are not
simulated here. No frontend calculation duplicates accounting or analysis.

Historical results are not proof of future performance. OOS is evidence, not
proof. Large parameter searches increase multiple-testing and selection-bias
concerns. Robustness results do not guarantee future robustness. Monte Carlo
results depend on the simulation assumptions above. Strategy code itself is
trusted Python; a factory must respect completed-bar history and must be
versioned whenever its behavior changes.

The API limits UI batches to 10,000 candidates and Monte Carlo to 100,000
simulations per request. Research remains sequential and complete outputs are
retained in memory during a run. Large production searches should be submitted
in bounded batches. The browser-level API integration test uses the application
service and SQLAlchemy directly because this environment does not include the
optional httpx2 package required by its installed Starlette TestClient.
