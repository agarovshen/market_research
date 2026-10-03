(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ResearchWorkspaceState = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function alignedCorrelationGroup(rows) {
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
        definition.backtest_config,
        definition.analysis_config,
      ]);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row);
    }

    const phasePriority = { oos: 0, batch: 1, train: 2 };
    const eligible = [...groups.values()].filter(group => group.length >= 2);
    eligible.sort((left, right) =>
      (phasePriority[left[0].definition.phase] ?? 3) - (phasePriority[right[0].definition.phase] ?? 3) ||
      right.length - left.length ||
      String(left[0].definition.period.start).localeCompare(String(right[0].definition.period.start)));
    return eligible[0] || [];
  }

  return { alignedCorrelationGroup };
});
