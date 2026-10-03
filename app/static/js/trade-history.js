(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    root.TradeHistory = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    function tradesFromBacktest(result) {
        if (!result || typeof result !== "object") throw new TypeError("A BacktestResult is required");
        if (result.trades == null) return [];
        if (!Array.isArray(result.trades)) throw new TypeError("BacktestResult trades must be an array");
        return result.trades.map((trade, index) => {
            if (!trade || typeof trade !== "object") throw new TypeError(`Trade ${index + 1} is malformed`);
            const entryTime = Date.parse(trade.entry_time);
            const exitTime = Date.parse(trade.exit_time);
            const entryPrice = Number(trade.entry_price);
            const exitPrice = Number(trade.exit_price);
            const side = String(trade.side || "").toLowerCase();
            if (!Number.isFinite(entryTime) || !Number.isFinite(exitTime) ||
                !Number.isFinite(entryPrice) || !Number.isFinite(exitPrice) ||
                !["long", "short"].includes(side)) {
                throw new TypeError(`Trade ${index + 1} is missing canonical entry/exit data`);
            }
            return {
                id: trade.sequence ?? index + 1,
                side: side === "long" ? "BUY · LONG" : "SELL · SHORT",
                entry_time: trade.entry_time,
                exit_time: trade.exit_time,
                entry_price: entryPrice,
                exit_price: exitPrice,
                quantity: trade.quantity ?? null,
                gross_pnl: trade.gross_pnl ?? null,
                net_pnl: trade.net_pnl ?? null,
                commission: Number(trade.entry_commission || 0) + Number(trade.exit_commission || 0),
                duration_ms: exitTime - entryTime,
            };
        });
    }

    function markersFromTrades(trades) {
        return trades.flatMap(trade => [
            { trade_id: trade.id, kind: "entry", side: trade.side, timestamp: Date.parse(trade.entry_time), price: trade.entry_price },
            { trade_id: trade.id, kind: "exit", side: trade.side, timestamp: Date.parse(trade.exit_time), price: trade.exit_price },
        ]);
    }

    function resultForId(results, experimentId) {
        return results.find(row => row.status === "completed" &&
            row.definition?.experiment_id === experimentId) || null;
    }

    return { tradesFromBacktest, markersFromTrades, resultForId };
});
