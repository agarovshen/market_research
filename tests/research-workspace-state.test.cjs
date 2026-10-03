const test = require("node:test");
const assert = require("node:assert/strict");
const { alignedCorrelationGroup, sensitivityGroup, sensitivityParameters, monteCarloBaseline } = require("../app/static/js/research-state.js");

function row(id, phase = "batch", period = "2024-01-01", fingerprint = "same") {
  return {
    status: "completed",
    analysis_result: { equity_curve: [] },
    definition: {
      experiment_id: id,
      phase,
      symbol: "EURUSD",
      timeframe: "H1",
      period: { start: period, end: "2024-02-01" },
      dataset_fingerprint: fingerprint,
      backtest_config: { initial_cash: 10000 },
      analysis_config: { periods_per_year: null },
    },
  };
}

test("no correlation group is returned for one completed experiment", () => {
  assert.deepEqual(alignedCorrelationGroup([row("one")]), []);
});

test("correlation picks multiple results with a common phase and data domain", () => {
  const training = [row("train-1", "train"), row("train-2", "train")];
  const oos = [row("oos-1", "oos"), row("oos-2", "oos")];
  const result = alignedCorrelationGroup([...training, ...oos]);
  assert.deepEqual(result.map(item => item.definition.experiment_id), ["oos-1", "oos-2"]);
});

test("correlation never mixes different periods or data fingerprints", () => {
  const result = alignedCorrelationGroup([
    row("a", "batch", "2024-01-01", "data-a"),
    row("b", "batch", "2024-01-01", "data-b"),
    row("c", "batch", "2024-02-01", "data-a"),
  ]);
  assert.deepEqual(result, []);
});

test("correlation accepts timestamp-aligned results with differing cost and analysis settings", () => {
  const first = row("a"), second = row("b");
  second.definition.backtest_config = { initial_cash: 10000, spread_scale: 2 };
  second.definition.analysis_config = { periods_per_year: 252 };
  assert.deepEqual(alignedCorrelationGroup([first, second]).map(item => item.definition.experiment_id), ["a", "b"]);
});

test("failed and unanalyzed results are excluded from correlation input", () => {
  const incomplete = { ...row("b"), status: "failed" };
  const unanalyzed = { ...row("c"), analysis_result: null };
  assert.deepEqual(alignedCorrelationGroup([row("a"), incomplete, unanalyzed]), []);
});

test("sensitivity selects one compatible candidate phase/window instead of mixing walk-forward windows", () => {
  const firstWindow = [row("train-1", "train", "2024-01-01"), row("train-2", "train", "2024-01-01")];
  const secondWindow = [row("train-3", "train", "2024-02-01"), row("train-4", "train", "2024-02-01")];
  assert.deepEqual(sensitivityGroup([...firstWindow, ...secondWindow]).map(item => item.definition.experiment_id),
    ["train-1", "train-2"]);
});

test("sensitivity deduplicates configurations and discovers varying strategy parameters", () => {
  const candidates = [
    row("a"), row("b"), row("duplicate"),
  ];
  for (const [item, values] of candidates.map((item, index) => [item, [["fast", index === 2 ? 2 : index + 2], ["slow", 10]]])) {
    item.definition.strategy_id = "test.strategy";
    item.definition.strategy_version = "1";
    item.definition.parameters = { values };
  }
  const selected = sensitivityGroup(candidates);
  assert.deepEqual(selected.map(item => item.definition.experiment_id), ["a", "b"]);
  assert.deepEqual(sensitivityParameters(selected), ["fast"]);
});

test("Monte Carlo baseline requires closed trades and prefers the selected eligible result", () => {
  const noTrades = row("no-trades");
  noTrades.backtest_result = { trades: [], open_position: null };
  const open = row("open");
  open.backtest_result = { trades: [{ sequence: 1 }], open_position: { quantity: 1 } };
  const eligible = row("eligible");
  eligible.backtest_result = { trades: [{ sequence: 1 }], open_position: null };
  assert.equal(monteCarloBaseline([noTrades, open, eligible], "open").definition.experiment_id, "eligible");
  assert.equal(monteCarloBaseline([noTrades, open], null), null);
});
