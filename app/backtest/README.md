# Backtest Core and Strategy Runner

`BacktestRunner` is the application boundary for a Python strategy run. It loads
historical bars through the existing `MarketDataRepository`, then delegates
execution and accounting to `BacktestEngine` and returns its `BacktestResult`.
It does not create another result type or calculate analysis metrics.

## Result contract

`BacktestResult` is an immutable dataclass with these stable top-level fields:

- Scalars: `initial_cash`, `final_cash`, `final_equity`, `realized_pnl`,
  `unrealized_pnl`, `total_commission`, `total_spread_cost`,
  `total_slippage_cost`.
- `open_position`: a `Position` or `None`.
- `orders`: ordered tuple of `Order` records. Each has a sequence, signal and
  fill timestamps (`created_at`, `filled_at`), action, side, quantity,
  reference/fill prices, commission, spread cost, and slippage cost.
- `trades`: ordered tuple of completed `Trade` records, including side,
  quantity, entry/exit timestamps and prices, entry/exit commission, gross PnL,
  and net PnL.
- `equity_curve`: ordered tuple of `EquityPoint` records with timestamp, cash,
  unrealized PnL, and equity.

Signals use completed-bar context and are eligible for execution at the next
bar's open. Configured final liquidation closes any remaining position at the
last available bar close. Spread and slippage are reflected in fill prices and
reported cost totals; commission is charged by the engine. These fields are
the accounting source of truth for consumers, including the research chart.

## Run a strategy against PostgreSQL history

Provide the existing application's `DATABASE_URL` environment variable, then
run this from the repository root (the strategy class below is intentionally
small; replace its rule with your research strategy):

```python
from datetime import datetime

from app.backtest import (
    BacktestRunConfig,
    BacktestRunner,
    MarketDataRepository,
    OrderAction,
    Side,
    Signal,
)
from app.database import SessionLocal


class ThresholdStrategy:
    def on_bar(self, context):
        if context.index == 0:
            return Signal(OrderAction.OPEN, Side.LONG, quantity=1)
        if context.index == 10:
            return Signal(OrderAction.CLOSE)
        return None


config = BacktestRunConfig(
    symbol="EURUSD",
    timeframe="M15",
    start=datetime(2024, 1, 1),
    end=datetime(2024, 2, 1),
    initial_cash=100_000,
    position_size=1,
    commission_per_unit=0,
    commission_rate=0,
    spread_scale=1,
    slippage=0,
    final_liquidation=True,
)

with SessionLocal() as session:
    result = BacktestRunner(MarketDataRepository(session)).run(
        strategy=ThresholdStrategy(),
        config=config,
    )

print(result.final_equity, len(result.trades))
```

The repository accepts `M1`, `M5`, `M15`, `H1`, `H4`, and `D1`. Higher
timeframes are calendar-aligned aggregations of the existing market-data rows;
the configured start/end filters apply to source rows before aggregation.
