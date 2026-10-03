(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ResearchWorkspaceState = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function compatibleGroups(rows, includeSettings) {
    const groups = new Map();
    for (const row of rows) {
      if (row.status !== "completed" || !row.analysis_result) continue;
      const definition = row.definition;
      const key = JSON.stringify([
        definition.phase,
        definition.symbol,
        definition.timeframe,
        definition.period,
        definition.dataset_fingerprint,
        ...(includeSettings ? [definition.backtest_config, definition.analysis_config] : []),
      ]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row);
    }

    return [...groups.values()];
  }

  function preferredGroup(groups, phasePriority) {
    const eligible = groups.filter(group => group.length >= 2);
    eligible.sort((left, right) =>
      (phasePriority[left[0].definition.phase] ?? 3) - (phasePriority[right[0].definition.phase] ?? 3) ||
      right.length - left.length ||
      String(left[0].definition.period.start).localeCompare(String(right[0].definition.period.start)));
    return eligible[0] || [];
  }

  function alignedCorrelationGroup(rows) {
    // Correlation/portfolio calculations require the same observations, not
    // identical execution costs or analysis settings.
    return preferredGroup(compatibleGroups(rows, false), { oos: 0, batch: 1, train: 2 });
  }

  function sensitivityGroup(rows) {
    const groups = compatibleGroups(rows, true).filter(group =>
      group.every(row => row.definition.strategy_id === group[0].definition.strategy_id &&
        row.definition.strategy_version === group[0].definition.strategy_version));
    const group = preferredGroup(groups, { batch: 0, train: 1 });
    const unique = new Map();
    for (const row of group) {
      const configured = row.definition.parameters?.values;
      const values = configured ? JSON.stringify(configured) : row.definition.experiment_id;
      if (!unique.has(values)) unique.set(values, row);
    }
    return [...unique.values()];
  }

  function sensitivityParameters(rows) {
    if (rows.length < 2) return [];
    const configurations = rows.map(row => Object.fromEntries(row.definition.parameters?.values || []));
    const names = Object.keys(configurations[0] || {});
    return names.filter(name => configurations.some(config =>
      JSON.stringify(config[name]) !== JSON.stringify(configurations[0][name]))).slice(0, 8);
  }

  function monteCarloBaseline(rows, preferredId) {
    const eligible = rows.filter(row => row.status === "completed" && row.backtest_result &&
      !row.backtest_result.open_position && (row.backtest_result.trades || []).length > 0);
    return eligible.find(row => row.definition.experiment_id === preferredId) || eligible[0] || null;
  }

  return { alignedCorrelationGroup, sensitivityGroup, sensitivityParameters, monteCarloBaseline };
});
