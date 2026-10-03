(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MarketDataImportLock = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function createImportLock() {
    let active = false;
    return {
      tryStart() {
        if (active) return false;
        active = true;
        return true;
      },
      finish() { active = false; },
    };
  }

  return { createImportLock };
});
