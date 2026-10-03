(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ResearchPresentation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const number = value => value == null ? "—" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
  const percent = value => value == null ? "—" : `${(Number(value) * 100).toFixed(3)}%`;
  const pairs = value => Object.fromEntries(value || []);

  function advancedView(result) {
    const output = result.output || {};
    const base = {
      title: result.method || "Advanced research",
      summary: [
        { label: "Source experiments", value: result.input_experiment_ids?.length ?? 0 },
        { label: "Analysis ID", value: result.analysis_id || "—" },
      ],
      columns: [],
      rows: [],
      note: "",
      chart: null,
    };

    if (result.method === "sensitivity") {
      base.title = "Parameter sensitivity";
      base.summary.push(
        { label: "Metric", value: output.metric },
        { label: "Metric dispersion", value: number(output.dispersion) },
        { label: "Mean absolute neighbor change", value: number(output.mean_absolute_neighbor_change) },
      );
      base.columns = ["Parameters", "Metric"];
      base.rows = (output.cells || []).map(cell => [JSON.stringify(pairs(cell.parameters)), number(cell.metric)]);
      base.note = (output.neighbor_change_by_parameter || [])
        .map(([name, change]) => `${name}: mean absolute adjacent change ${number(change)}`).join(" · ") ||
        "No adjacent parameter comparisons are available.";
    } else if (result.method === "robustness") {
      base.title = "Robustness scenarios";
      base.summary.push({ label: "Metric", value: output.metric },
        { label: "Scenarios", value: output.points?.length ?? 0 });
      base.columns = ["Scenario", "Status", "Metric", "Experiment", "Details"];
      base.rows = (output.points || []).map(point => [point.name, point.status, number(point.metric),
        point.experiment_id || "—", point.failure_message || "—"]);
      base.note = `Baseline experiment: ${output.baseline_experiment_id || "—"}`;
    } else if (result.method === "monte_carlo") {
      base.title = "Trade Monte Carlo distribution";
      base.summary.push({ label: "Method", value: output.method }, { label: "Seed", value: output.seed },
        { label: "Simulations", value: output.simulations });
      base.columns = ["Percentile", "Terminal equity", "Terminal return", "Maximum drawdown"];
      const returns = pairs(output.return_percentiles), terminal = pairs(output.terminal_percentiles);
      const drawdowns = pairs(output.drawdown_percentiles);
      base.rows = Object.keys(terminal).map(label => [label, number(terminal[label]),
        percent(returns[label]), number(drawdowns[label])]);
      base.note = output.assumptions || "";
    } else if (result.method === "regime") {
      base.title = "Market regime results";
      base.summary.push({ label: "Lookback bars", value: output.definition?.lookback },
        { label: "Classified bars", value: output.observations?.length ?? 0 },
        { label: "Trades classified", value: output.trade_regimes?.length ?? 0 });
      base.columns = ["Regime", "Observations", "Trades", "Net P&L", "Average trade"];
      base.rows = (output.statistics || []).map(item => [item.label, item.observations, item.trades,
        number(item.net_pnl), number(item.average_trade)]);
    } else if (result.method === "correlation") {
      base.title = "Strategy return correlation";
      base.summary.push({ label: "Aligned observations", value: output.timestamps?.length ?? 0 },
        { label: "Series", value: output.labels?.length ?? 0 });
      base.columns = ["Series", ...(output.labels || [])];
      base.rows = (output.labels || []).map((label, index) => [label,
        ...(output.coefficients?.[index] || []).map(number)]);
      base.note = "Pearson correlation of aligned per-observation returns. — denotes an undefined coefficient.";
    } else if (result.method === "weighted_portfolio") {
      base.title = "Weighted research curve";
      const points = output.points || [];
      base.summary.push({ label: "Initial capital", value: number(output.initial_capital) },
        { label: "Final equity", value: number(points.length ? points[points.length - 1].equity : output.initial_capital) },
        { label: "Observations", value: points.length });
      base.columns = ["Strategy", "Weight"];
      base.rows = (output.weights || []).map(([name, weight]) => [name, percent(weight)]);
      base.chart = { label: "Portfolio equity", points: points.map(point => ({
        timestamp: point.timestamp, value: point.equity,
      })) };
      base.note = output.assumptions || "";
    } else if (result.method === "walk_forward_aggregate") {
      base.title = "Walk-forward OOS summary";
      base.summary.push({ label: "Selected rank", value: output.candidate_rank },
        { label: "Completed windows", value: output.completed_window_count },
        { label: "Summed net P&L", value: number(output.total_net_profit) });
      base.columns = ["Window", "Training period", "OOS period", "Completed", "Failed", "Net P&L"];
      base.rows = (output.window_summaries || []).map(item => [item.index,
        `${item.training_period.start} → ${item.training_period.end}`,
        `${item.testing_period.start} → ${item.testing_period.end}`,
        item.completed_selected_candidates, item.failed_selected_candidates, number(item.net_profit)]);
      base.note = output.assumptions || "";
    } else {
      throw new Error(`Unsupported advanced result type: ${result.method || "unknown"}`);
    }
    return base;
  }

  return { advancedView };
});
