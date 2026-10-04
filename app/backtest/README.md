# Backtest Core and Strategy Runner

`BacktestRunner` is the application boundary for a Python strategy run. It loads
historical bars through the existing `MarketDataRepository`, then delegates
execution and accounting to `BacktestEngine` and returns its `BacktestResult`.
It does not create another result type or calculate analysis metrics.

## Result contract

`BacktestResult` is an immutable dataclass with these stable top-level fields:

- Scalars: `initial_cash`, `final_cash`, `final_equity`, `realized_pnl`,
  `unrealized_pnl`, `total_commission`, `total_spread_cost`,
  `total_slippage_cost`, and Forex-mode `balance`, `lots`, `units`,
  `contract_size`, `leverage`, `margin_used`, `free_margin`, and
  `margin_level` (`None` while no margin is reserved).
- `open_position`: a `Position` or `None`.
- `orders`: ordered tuple of `Order` records. Each has a sequence, signal and
  fill timestamps (`created_at`, optional `filled_at`), action, side, type,
  status, quantity, reference/fill prices, commission, spread cost, and
  slippage cost. Pending STOP orders have no fill timestamp or fill price.
- `trades`: ordered tuple of completed `Trade` records, including side,
  quantity, entry/exit timestamps and prices, entry/exit commission, gross PnL,
  and net PnL.
- `equity_curve`: ordered tuple of `EquityPoint` records with timestamp, cash,
  unrealized PnL, equity, margin used, free margin, and optional margin level.
- `execution_trace`: ordered immutable `ExecutionEvent` records observing the
  same engine path. `format_execution_trace()` renders the complete stream;
  `format_trade_lifecycle(sequence)` filters one completed trade and then
  prints its canonical `Trade` fields.

Forex Test setup accepts `lots`; the engine converts lots to base units using
`contract_size`. Leverage determines required margin only and does not enter
the PnL calculation. In Forex sizing mode, balance changes by commissions and
realized PnL, equity is balance plus unrealized PnL, and account snapshots
include used/free margin and nullable margin level. Position `notional` and
initial `margin` use fill price; the account snapshot's `margin_used` marks
open units at the current bar close. Direct legacy callers may
continue to supply `quantity`/`position_size` as internal units when `lots` is
omitted.

MARKET signals use completed-bar context and are eligible for execution at the
next bar's open. A STOP opening signal creates one pending STOP order; it
becomes eligible on the bar after its signal. BUY STOP triggers at
`high >= trigger`, SELL STOP at `low <= trigger`. A normal touch uses the
trigger price as its fill reference; a gap through the level uses the bar open.
The existing spread, slippage, and commission settings are applied by the
normal fill-cost path.

An open position may carry an initial stop loss and receive an explicit
`Signal(OrderAction.MODIFY_STOP, stop_loss=...)`. Long stops trigger at
`low <= stop_loss`, short stops at `high >= stop_loss`; a gap through the stop
uses the bar open, otherwise the stop price is the fill reference. A newly
opened position's stop is not checked on its entry bar. A position open at the
start of a bar is checked on that bar. Stop updates take effect after the
completed signal bar. Stop changes are accepted as supplied; the engine does
not impose a tightening-only rule.

Only one pending STOP entry is supported; a second pending STOP or market
entry is rejected. There is no STOP expiry/cancellation, TP, OCO, or partial
fill support. Configured final liquidation closes any remaining position at
the last available bar close. Spread and slippage are reflected in fill prices
and reported cost totals; commission is charged by the engine.

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

`Signal` has no strategy reason field. The trace reports that strategy
reasoning is not exposed instead of inferring it. STOP order type, pending
state, trigger, execution, initial stop, stop updates, and stop closes are
visible in the trace. There are no TP, cancellation/expiry, rejection, or
persistent position ID events. Trade sequence and order sequence provide
lifecycle correlation.

The registered `breakout.h4_two_candle` strategy is implemented in
`app/strategies/h4_breakout.py`. It expects H4 bars from the caller, emits only
STOP entry intents and explicit stop updates, and reads canonical position and
pending-order snapshots from `StrategyContext`. It does not implement fills
or PnL.

OHLC bars do not reveal the true intrabar path. These deterministic rules make
the tested execution repeatable; they do not reconstruct tick-level execution
or broker behavior. If an entry and its stop are both inside one candle's
range, the stop is deferred until the next bar by policy, not by an inferred
price path.

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

The repository accepts `M1`, `M5`, `M15`, `H1`, `H4`, and `D1`. It is the
shared M1-to-timeframe path for `BacktestRunner` and the chart endpoint. The
chart uses `MarketDataRepository.load(..., timeframe=...)` for OHLC values; its
SQL bounds only limit the source range fetched for the chart window.

Aggregation semantics follow the stored timestamp's calendar fields: M5/M15
start at minute multiples within each hour, H1 at each hour, H4 at hours
00/04/08/12/16/20 within each stored day, and D1 at midnight. Stored/imported
timestamps are naive datetimes; no UTC or exchange-session conversion is
applied, and DST is not separately interpreted. There are no session/day
boundaries beyond the calendar buckets. Missing M1 rows are skipped without
gap filling, so partial buckets—including an incomplete final bucket—are
returned. The database's `(instrument_id, timestamp)` unique constraint
rejects duplicate M1 timestamps. Source rows are ordered by timestamp then
database id; each aggregate bar is labeled with its bucket-start timestamp and
bars are returned in first-seen chronological bucket order. Configured
start/end filters apply to source rows before aggregation. A centered chart
window chooses the nearest available bucket (earlier bucket breaks ties) and
then returns the requested surrounding bars.

## Strategy instance lifecycle

`BacktestEngine.run()` and `BacktestRunner.run()` accept a strategy instance
for one run. A strategy may keep mutable state during that run; neither layer
resets nor clones strategy objects. Callers must create a fresh instance for
each independent run. This avoids copying arbitrary user strategy objects or
silently resetting legitimate within-run state. ResearchEngine follows this
contract by calling the supplied strategy factory once for each candidate in
each phase run.
