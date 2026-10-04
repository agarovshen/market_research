# Backtest validation protocol

The tests exercise the existing `BacktestEngine`, `BacktestRunner`,
`AnalysisEngine`, and `ResearchEngine`. **The engine is validated against the
tested assumptions, independent reference calculations, and fixtures.** This
does not establish correctness for every input or strategy.

## Evidence currently covered

**Validated:**

- Exact known-answer trade arithmetic for long and short winners/losers, flat,
  multiple and no-trade paths, quantities, costs, threshold equality/miss,
  next-bar fills, final liquidation, equity, and drawdown.
- Independent calculations in `reference_model.py`, using primitive rows and
  scripted intent tuples only. It does not import application code and models
  only next-bar-open market intents, configured spread/slippage/commission,
  close marking, and final liquidation. One explicit synthetic
  historical-style OHLC path is compared end to end with production orders,
  trades, costs, equity, drawdown, and final equity. Its prices are authored
  test data, not exchange/vendor history; no real historical dataset is
  bundled in this repository.
- Accounting reconciliation against independently calculated net PnL and
  marked unrealized value, including seeded valid OHLC paths and quantity
  scaling.
- Half-open research boundaries; future-prefix invariance for strategy
  signals, orders, completed trades, fills, PnL, and equity inside the shared
  prefix; training/OOS selection isolation; walk-forward window separation;
  deterministic repeats; and result persistence round trips.
- Selected advanced calculations under explicit small fixtures, including
  seeded Monte Carlo repeatability and drawdown arithmetic.
- Structured first-divergence output. It identifies the first differing trade
  field, shows equal fields before it, gives expected and actual values, and
  marks later fields in that trade not comparable. Numeric absolute/relative
  tolerances are optional.

Data behavior tested against current code:

| Condition | Observed behavior |
| --- | --- |
| Strictly sorted timestamps | Accepted |
| Unsorted or duplicate timestamps | Rejected with `ValueError` |
| Missing time intervals / gaps | Accepted; calendar continuity is not checked |
| Empty input / one row | Accepted |
| NaN/infinite price or invalid OHLC / negative spread | Rejected with `ValueError` |
| Null numeric price | Fails numeric validation with `TypeError`; it is not normalized |
| Consistently aware timestamps | Accepted |
| Mixed naive/aware timestamps | Rejected during ordering comparison (`TypeError`) |
| Insufficient SMA warm-up | Strategy emits no signal before its required history |

The engine supports market signals that fill on the next bar open. Stop/target,
limit, cancellation, rejection, gap-trigger and intrabar ordering semantics do
not exist in this contract. The exact-threshold fixture validates a strategy
rule's equality boundary, not a broker trigger order. Strategy-specific
warm-up rules remain the strategy's responsibility.

## Independence and adding fixtures

`reference_model.py` intentionally does not model the full production design.
It accepts dictionaries of primitive timestamp/OHLC values and event tuples,
then uses explicit arithmetic for fill prices, cash changes, PnL, costs, marks,
and drawdown. Keep it test-only and narrow. Do not import engine, analysis, or
production accounting helpers into it.

For a new known answer, write small explicit OHLC rows and a human-readable
rule. State when the rule emits an intent, the next-bar fill timestamp/price,
direction and quantity, PnL formula, costs, equity observations, and drawdown.
Calculate expected numbers from the written assumptions, never from the
implementation under test. For a differential fixture, give the primitive
reference calculation its own events and compare its output with a projection
of production output. Do not build the expected mapping from production
trade/PnL fields.

Seeded property tests should assert mathematical relationships guaranteed by
the configured model (direction, scaling, costs, equity, and nonnegative
drawdown), not specific random output beyond the fixed seed. If changing the
seed, keep the generated OHLC valid and record the seed in assertion messages.

## External references and MT5

MT5 parity is optional external validation. The core validation suite does not
depend on MT5. No MT5 output is bundled or fabricated, and there is no Wine or
terminal automation.

An optional external export can use a JSON-compatible mapping with ordered
`orders`, `trades`, `equity`, `drawdown`, and `statistics`. Include trade side,
entry/exit timestamps and prices, quantity, commission, fees, slippage, gross
and net PnL, and final statistics. Normalize timezone, symbol precision,
quantity units, cost conventions, and timestamp meaning before comparison.
Preserve raw data and record the source and normalization. Call
`tests.validation.reference.first_difference(actual, expected, ...)` to see
the first divergence; `actual` is the engine projection, and `expected` is the
external/reference result. Tolerances are explicit and should match known
precision limits rather than conceal a mismatch.

## Interpreting mismatches and limits

Investigate the first differing field. A fill-time/price difference can
explain later PnL and equity differences. Check bars and timestamp boundaries,
signal timing, fill convention, quantity, commission/spread/slippage units,
and final-liquidation settings. Only change production after a focused fixture
establishes the intended contract and demonstrates a real defect.

**Not universally proven:** every possible strategy; every order type or
intrabar ambiguity; every broker execution model or data vendor; live broker
behavior; all market regimes; or correctness for all historical data. The
tests cannot establish strategy profitability or future performance.

Run validation twice and the complete Python suite with:

```sh
python -m pytest tests/validation
python -m pytest tests/validation
python -m pytest
```

Node tests are run with `node --test tests/*.test.cjs`; compilation and patch
hygiene with `python -m compileall .` and `git diff --check`.
