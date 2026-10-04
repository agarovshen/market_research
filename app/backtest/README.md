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
- `execution_trace`: ordered immutable `ExecutionEvent` records observing the
  same engine path. `format_execution_trace()` renders the complete stream;
  `format_trade_lifecycle(sequence)` filters one completed trade and then
  prints its canonical `Trade` fields.

Signals use completed-bar context and are eligible for execution at the next
bar's open. Configured final liquidation closes any remaining position at the
last available bar close. Spread and slippage are reflected in fill prices and
reported cost totals; commission is charged by the engine. These fields are
the accounting source of truth for consumers, including the research chart.

## Inspect the execution trace

The trace is emitted by `BacktestEngine.run()` and is available on results
returned directly by the engine or through `BacktestRunner`:

```python
result = BacktestRunner(repository).run(strategy=my_strategy, config=config)
print(result.format_execution_trace())
print(result.format_trade_lifecycle(1))
```

Events have monotonically increasing sequence numbers. A typical completed
market trade records a `BAR`, `SIGNAL`, and `ORDER_CREATED`; on the next bar it
records `EXECUTION` and `POSITION_OPENED`; later it records the close execution,
`POSITION_CLOSED`, `PNL_CALCULATED`, and `TRADE_CREATED`. Fill prices, costs,
timestamps, quantity, and PnL come from values already produced by the
canonical engine path. The trace does not recalculate PnL. For close-at-end,
the close order and execution are marked `end_of_data_liquidation`.

`Signal` currently carries only action, optional side, and optional quantity;
it has no strategy reason field. The trace reports that strategy reasoning is
not exposed instead of inferring it. The engine currently has no stop/target
management, pending stop/limit order triggers, cancellation/rejection states,
or persistent position IDs. Accordingly, there are no SL/TP update or
`ORDER_TRIGGERED`/`ORDER_CANCELLED` events. Market order intent is associated
with the signal bar and its actual fill on the next bar; there is no separate
active-order state. Trade sequence and order sequence provide lifecycle
correlation.

For trace diagnostics, `first_event_difference(expected, actual)` reports the
first event number and differing field. This compares event streams only; the
canonical `BacktestResult.trades` remains authoritative.

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
