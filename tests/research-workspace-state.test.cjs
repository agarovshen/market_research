const test = require("node:test");
const assert = require("node:assert/strict");
const { alignedCorrelationGroup, sensitivityGroup } = require("../app/static/js/research-state.js");

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
