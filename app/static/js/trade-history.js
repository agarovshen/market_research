(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    root.TradeHistory = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    function readTrades(result) {
        if (!result || typeof result !== "object") return { trades: [], malformedCount: 1 };
        if (result.trades == null) return { trades: [], malformedCount: 0 };
        if (!Array.isArray(result.trades)) return { trades: [], malformedCount: 1 };
        const trades = [];
        let malformedCount = 0;
        result.trades.forEach((trade, index) => {
            try {
                if (!trade || typeof trade !== "object") throw new TypeError();
                const entryTime = Date.parse(trade.entry_time);
                const exitTime = Date.parse(trade.exit_time);
                const entryPrice = Number(trade.entry_price);
                const exitPrice = Number(trade.exit_price);
                const side = String(trade.side || "").toLowerCase();
                if (!Number.isFinite(entryTime) || !Number.isFinite(exitTime) ||
                    !Number.isFinite(entryPrice) || !Number.isFinite(exitPrice) ||
                    !["long", "short"].includes(side)) {
                    throw new TypeError();
                }
                trades.push({
                    id: trade.sequence ?? index + 1,
                    side: side === "long" ? "BUY · LONG" : "SELL · SHORT",
                    entry_time: trade.entry_time,
                    exit_time: trade.exit_time,
                    entry_price: entryPrice,
                    exit_price: exitPrice,
                    quantity: trade.quantity ?? null,
                    gross_pnl: trade.gross_pnl ?? null,
                    net_pnl: trade.net_pnl ?? null,
                    entry_commission: trade.entry_commission ?? null,
                    exit_commission: trade.exit_commission ?? null,
                });
            } catch {
                malformedCount += 1;
            }
        });
        return { trades, malformedCount };
    }

    function tradesFromBacktest(result) {
        return readTrades(result).trades;
    }

    function chartHandoff(row, trade, focusTrade = false) {
        if (!row?.backtest_result || !trade) return null;
        const handoff = {
            symbol: row.definition.symbol,
            timeframe: row.definition.timeframe,
            backtest_result: {
                orders: row.backtest_result.orders || [],
                trades: row.backtest_result.trades || [],
                execution_trace: row.backtest_result.execution_trace || [],
                equity_curve: [],
                open_position: row.backtest_result.open_position || null,
            },
        };
        if (focusTrade) {
            handoff.trade_id = trade.id;
            handoff.focus_range = { start: trade.entry_time, end: trade.exit_time };
        } else {
            handoff.center_timestamp = trade.entry_time;
        }
        return handoff;
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

    return { readTrades, tradesFromBacktest, markersFromTrades, resultForId, chartHandoff };
});
