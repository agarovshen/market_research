(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    root.ExecutionLog = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    const meaningfulTypes = new Set([
        "signal", "order_created", "order_pending", "order_triggered", "execution",
        "position_opened", "stop_updated", "position_closed", "pnl_calculated", "trade_created",
    ]);

    function readEvents(backtestResult) {
        const raw = backtestResult?.execution_trace;
        if (raw == null) return { events: [], malformedCount: 0 };
        if (!Array.isArray(raw)) return { events: [], malformedCount: 1 };
        const events = [];
        let malformedCount = 0;
        for (const event of raw) {
            if (!event || typeof event !== "object" ||
                !Number.isInteger(event.sequence) || typeof event.timestamp !== "string" ||
                typeof event.event_type !== "string") {
                malformedCount += 1;
                continue;
            }
            events.push(event);
        }
        return { events, malformedCount };
    }

    function displayEvents(backtestResult) {
        const parsed = readEvents(backtestResult);
        const activeBars = new Set(parsed.events
            .filter(event => meaningfulTypes.has(event.event_type.trim().toLowerCase()) &&
                event.bar_index != null)
            .map(event => String(event.bar_index)));
        return {
            ...parsed,
            events: parsed.events.filter(event => {
                if (event.event_type.trim().toLowerCase() !== "bar") return true;
                return event.bar_index != null && activeBars.has(String(event.bar_index));
            }),
        };
    }

    function detailText(event) {
        const parts = [];
        if (event.action != null) parts.push(`action=${event.action}`);
        if (event.order_type != null) parts.push(`order_type=${event.order_type}`);
        if (event.reason != null) parts.push(`reason=${event.reason}`);
        if (Array.isArray(event.details)) {
            for (const pair of event.details) {
                if (Array.isArray(pair) && pair.length === 2 && typeof pair[0] === "string") {
                    parts.push(`${pair[0]}=${pair[1] ?? "—"}`);
                }
            }
        }
        return parts.join(" · ") || "—";
    }

    return { readEvents, displayEvents, detailText };
});
