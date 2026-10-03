const test = require("node:test");
const assert = require("node:assert/strict");
const { advancedView } = require("../app/static/js/research-presentation.js");

function result(method, output) {
  return { method, input_experiment_ids: ["experiment-a", "experiment-b"], analysis_id: "analysis-a", output };
}

test("sensitivity and robustness results map to readable parameter/scenario tables", () => {
  const sensitivity = advancedView(result("sensitivity", {
    metric: "total_return", dispersion: 0.25, mean_absolute_neighbor_change: 0.1,
    cells: [{ parameters: [["fast", 5], ["slow", 20]], metric: 0.3 }],
    neighbor_change_by_parameter: [["fast", 0.1]],
  }));
  assert.equal(sensitivity.title, "Parameter sensitivity");
  assert.deepEqual(sensitivity.rows, [["{\"fast\":5,\"slow\":20}", "0.3"]]);

  const robustness = advancedView(result("robustness", { metric: "trades.net_profit", points: [
    { name: "higher cost", status: "completed", metric: 12.5, experiment_id: "scenario-1" },
    { name: "invalid", status: "failed", metric: null, experiment_id: null, failure_message: "bad params" },
  ] }));
  assert.equal(robustness.rows[0][0], "higher cost");
  assert.equal(robustness.rows[1][4], "bad params");
});

test("Monte Carlo and correlation results map to percentile and matrix tables", () => {
  const mc = advancedView(result("monte_carlo", {
    method: "bootstrap", seed: 42, simulations: 100,
    terminal_percentiles: [["p50", 110]], return_percentiles: [["p50", .1]],
    drawdown_percentiles: [["p50", 5]], assumptions: "trade resampling",
  }));
  assert.deepEqual(mc.rows, [["p50", "110", "10.000%", "5"]]);

  const corr = advancedView(result("correlation", {
    labels: ["A", "B"], timestamps: ["t1", "t2"], coefficients: [[1, .75], [.75, 1]],
  }));
  assert.deepEqual(corr.columns, ["Series", "A", "B"]);
  assert.deepEqual(corr.rows, [["A", "1", "0.75"], ["B", "0.75", "1"]]);
});

test("regime, portfolio, and walk-forward outputs expose their actual summary data", () => {
  const regime = advancedView(result("regime", {
    definition: { lookback: 10 }, observations: [{ timestamp: "t1" }], trade_regimes: [[1, "up"]],
    statistics: [{ label: "low_volatility:up", observations: 5, trades: 1, net_pnl: 4, average_trade: 4 }],
  }));
  assert.equal(regime.rows[0][0], "low_volatility:up");

  const portfolio = advancedView(result("weighted_portfolio", {
    initial_capital: 1000, weights: [["A", .6], ["B", .4]],
    points: [{ timestamp: "2024-01-01T00:00:00Z", equity: 1025, period_return: .025 }],
    assumptions: "rebalanced",
  }));
  assert.equal(portfolio.summary.find(item => item.label === "Final equity").value, "1,025");
  assert.equal(portfolio.chart.points[0].value, 1025);

  const wf = advancedView(result("walk_forward_aggregate", {
    candidate_rank: 1, completed_window_count: 1, total_net_profit: 4,
    window_summaries: [{ index: 1, training_period: { start: "a", end: "b" },
      testing_period: { start: "b", end: "c" }, completed_selected_candidates: 1,
      failed_selected_candidates: 0, net_profit: 4 }],
  }));
  assert.equal(wf.rows[0][2], "b → c");
});
