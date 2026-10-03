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
