(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.ResearchWorkspaceState = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function compatibleGroups(rows) {
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
    return preferredGroup(compatibleGroups(rows), { oos: 0, batch: 1, train: 2 });
  }

  function sensitivityGroup(rows) {
    const groups = compatibleGroups(rows).filter(group =>
      group.every(row => row.definition.strategy_id === group[0].definition.strategy_id &&
        row.definition.strategy_version === group[0].definition.strategy_version));
    return preferredGroup(groups, { batch: 0, train: 1 });
  }

  return { alignedCorrelationGroup, sensitivityGroup };
});
