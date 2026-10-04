# Backtest validation protocol

This suite checks the existing `BacktestEngine`, `BacktestRunner`,
`AnalysisEngine`, and `ResearchEngine` against hand-calculated fixtures,
independent arithmetic, adversarial future data, and the repository's stated
interfaces. Passing means the implementation is **validated against the tested
assumptions and fixtures**. It is not proof of universal correctness, market
realism, strategy profitability, or future performance.

## Coverage and limits

Known-answer tests document next-bar-open entry/exit timing, long/short PnL,
costs, cash/equity, drawdown, flat/loss/win/multiple/no trade paths, and empty
and one-bar cases. Execution currently means the engine's supported market
signals; stops, targets, pending limit/stop orders, cancellation, and rejection
are not implemented or asserted. Data tests record actual behavior: timestamps
must be strictly increasing, gaps are allowed, OHLC/spread must be valid,
finite values are required, and consistently aware timestamps are accepted.
The runner/research tests cover half-open research phases, OOS selection,
walk-forward separation, deterministic repeats, and persistence round trips.
Advanced tests check seed repeatability and limited known arithmetic; they do
not validate statistical assumptions as claims about real markets.

## Adding a known-answer fixture

Write the bars and strategy instructions explicitly. Before asserting any
result, state the timeline and arithmetic in comments: signal timestamp, next
bar fill timestamp and price, side/quantity, gross PnL formula, each cost, net
PnL, cash/equity marks, and drawdown from the listed equity observations.
Never compute an expected result by calling engine or analysis code. Keep a
fixture small enough that each expected value can be checked by hand.

## External reference / MT5 comparison

Export reference data into a structured JSON-compatible mapping with `trades`
(ordered records containing side, entry/exit timestamps and prices, quantity,
commission, fees, slippage, and PnL), plus ordered `equity`, `drawdown`, and
`statistics` sections. Normalize timezone, symbol precision, quantity units,
commission convention, spread convention, and whether timestamps denote bar
open or close before comparison. `tests.validation.reference.first_difference`
reports the first divergent ordered field and numeric delta, with optional
absolute and relative tolerances. Preserve the raw export and record platform,
feed, settings, and normalization choices alongside it. No MT5 output is
bundled or fabricated here; where automated MT5 execution is unavailable, run
the reference export manually and compare the normalized result.

## Interpreting mismatches

Investigate the earliest difference first: a wrong fill time or price can
explain every later PnL/equity mismatch. Check data ordering and bars, interval
boundaries, signal timing, fill convention, cost units, and liquidation policy
before treating a final-statistic difference as an accounting defect. Add a
focused regression fixture and change production only when the expected
contract is established independently.

Known assumptions include a single instrument, one net position, next-bar
open market fills, configured spread/slippage/commission, and current
final-liquidation settings. Indicator warm-up behavior is strategy-owned.
External execution parity, all broker conventions, and all possible strategy
implementations remain unverified.

Run with `python -m pytest tests/validation` and the full suite with
`python -m pytest`.
