(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    root.ResearchData = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    const TIMEFRAMES = Object.freeze(["M1", "M5", "M15", "H1", "H4", "D1"]);

    class ChartState {
        constructor() {
            this.instrument = null;
            this.timeframe = "M1";
            this.chartType = "candlestick";
            this.visibleCount = 1000;
            this.selectedTimestamp = null;
            this.frozenTimestamp = null;
            this.crosshair = true;
            this.grid = true;
            this.autoScale = true;
            this.panels = { equity: false, drawdown: false };
            this.data = [];
        }

        get anchorTimestamp() { return this.frozenTimestamp ?? this.selectedTimestamp; }

        setTimeframe(value) {
            if (!TIMEFRAMES.includes(value)) throw new RangeError(`Unsupported timeframe: ${value}`);
            this.timeframe = value;
            return this.anchorTimestamp;
        }

        select(timestamp) {
            if (!Number.isFinite(timestamp)) return;
            this.selectedTimestamp = timestamp;
        }

        selectCandle(data, index) {
            if (!Number.isInteger(index) || index < 0 || index >= data.length) return null;
            const selected = timestamp(data[index].timestamp);
            this.select(selected);
            return selected;
        }

        toggleFrozen(timestamp = this.selectedTimestamp) {
            if (!Number.isFinite(timestamp)) return this.frozenTimestamp;
            if (this.frozenTimestamp === timestamp) this.frozenTimestamp = null;
            else this.frozenTimestamp = timestamp;
            this.selectedTimestamp = timestamp;
            return this.frozenTimestamp;
        }

        clearSelection() {
            this.selectedTimestamp = null;
            this.frozenTimestamp = null;
        }

        resetView() {
            this.visibleCount = 1000;
        }
    }

    function timestamp(value) {
        const result = typeof value === "number" ? value : Date.parse(value);
        return Number.isFinite(result) ? result : null;
    }

    function nearestIndex(data, target, accessor = row => timestamp(row.timestamp)) {
        if (!data.length || !Number.isFinite(target)) return -1;
        let low = 0, high = data.length;
        while (low < high) {
            const middle = (low + high) >>> 1;
            if (accessor(data[middle]) < target) low = middle + 1;
            else high = middle;
        }
        if (low === 0) return 0;
        if (low === data.length) return data.length - 1;
        return target - accessor(data[low - 1]) <= accessor(data[low]) - target ? low - 1 : low;
    }

    function centeredSlice(data, count, anchor) {
        if (!data.length) return [];
        const length = Math.max(1, Math.min(data.length, Math.floor(count) || 1));
        const index = anchor == null ? data.length - 1 : nearestIndex(data, anchor);
        let start = Math.max(0, Math.min(index - Math.floor(length / 2), data.length - length));
        return data.slice(start, start + length);
    }

    function marketDataParams(symbol, timeframe, limit, anchor, focusRange = null) {
        const params = new URLSearchParams({ symbol, timeframe, limit: String(limit) });
        if (anchor != null) {
            const date = new Date(anchor);
            const pad = value => String(value).padStart(2, "0");
            const wallTime = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
                `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.` +
                String(date.getMilliseconds()).padStart(3, "0");
            params.set("center_timestamp", wallTime);
        }
        if (focusRange?.start && focusRange?.end) {
            params.set("focus_start", focusRange.start);
            params.set("focus_end", focusRange.end);
        }
        return params;
    }

    function transformBacktest(result) {
        if (!result || typeof result !== "object") throw new TypeError("A BacktestResult object is required");
        const rawTrades = result.trades || [];
        const tradeForFill = new Map();
        for (let index = 0; index < rawTrades.length; index++) {
            const trade = rawTrades[index];
            const side = String(trade.side || "").toLowerCase();
            const id = trade.sequence ?? trade.id ?? index + 1;
            tradeForFill.set(`${timestamp(trade.entry_time)}:${side}:open`, id);
            tradeForFill.set(`${timestamp(trade.exit_time)}:${side}:close`, id);
        }
        const orders = (result.orders || []).map((order, index) => {
            const side = String(order.side || "").toLowerCase();
            const action = String(order.action || order.order_type || "").toLowerCase();
            const entry = action === "open" || action.endsWith("entry");
            const direction = side === "long" ? "LONG" : "SHORT";
            const label = entry ? `${direction} ENTRY` : `${direction} EXIT`;
            return {
                id: order.sequence ?? order.id ?? index + 1,
                tradeId: order.trade_id ?? tradeForFill.get(`${timestamp(order.filled_at ?? order.timestamp)}:${side}:${action}`) ?? null,
                timestamp: timestamp(order.filled_at ?? order.timestamp),
                price: Number(order.fill_price ?? order.price),
                referencePrice: Number(order.reference_price ?? order.price),
                quantity: Number(order.quantity ?? order.qty),
                action, side, label,
                marketAction: (entry === (side === "long")) ? "BUY" : "SELL",
                commission: Number(order.commission || 0),
                spreadCost: Number(order.spread_cost || 0),
                slippageCost: Number(order.slippage_cost || 0),
                source: order,
            };
        }).filter(order => order.timestamp !== null && Number.isFinite(order.price))
            .sort((a, b) => a.timestamp - b.timestamp || a.id - b.id);

        const fillCosts = new Map();
        for (const order of orders) {
            const key = `${order.timestamp}:${order.side}:${order.action}`;
            const cost = fillCosts.get(key) || { spread: 0, slippage: 0 };
            cost.spread += order.spreadCost;
            cost.slippage += order.slippageCost;
            fillCosts.set(key, cost);
        }
        const trades = rawTrades.flatMap((trade, index) => {
            if (!trade || typeof trade !== "object") return [];
            const entryTime = timestamp(trade.entry_time);
            const exitTime = timestamp(trade.exit_time);
            const side = String(trade.side || "").toLowerCase();
            const entryPrice = finiteOrNull(trade.entry_price);
            const exitPrice = finiteOrNull(trade.exit_price);
            if (entryTime === null || exitTime === null || entryPrice === null || exitPrice === null ||
                !["long", "short"].includes(side)) return [];
            const entryCosts = fillCosts.get(`${entryTime}:${side}:open`) || { spread: 0, slippage: 0 };
            const exitCosts = fillCosts.get(`${exitTime}:${side}:close`) || { spread: 0, slippage: 0 };
            const quantity = Number(trade.quantity ?? trade.qty);
            const grossPnl = Number(trade.gross_pnl);
            const netPnl = Number(trade.net_pnl);
            return [{
                id: trade.sequence ?? trade.id ?? index + 1,
                side, direction: side.toUpperCase(), quantity,
                entryTime, exitTime, entryPrice, exitPrice,
                grossPnl, netPnl,
                entryCommission: finiteOrNull(trade.entry_commission),
                exitCommission: finiteOrNull(trade.exit_commission),
                spreadCost: entryCosts.spread + exitCosts.spread,
                slippage: entryCosts.slippage + exitCosts.slippage,
                winning: netPnl >= 0,
                source: trade,
            }];
        }).sort((a, b) => a.entryTime - b.entryTime || a.id - b.id);

        const equity = (result.equity_curve || []).map(point => ({
            x: timestamp(point.timestamp),
            equity: finiteOrNull(point.equity),
            cash: finiteOrNull(point.cash),
            realizedPnl: finiteOrNull(point.realized_pnl ?? point.realized_pnl_to_date),
            unrealizedPnl: finiteOrNull(point.unrealized_pnl),
            drawdown: finiteOrNull(point.drawdown_pct ?? point.drawdown),
        })).filter(point => point.x !== null);
        const open = result.open_position || null;
        const position = open ? {
            side: String(open.side || "").toLowerCase(),
            quantity: Number(open.quantity),
            entryTime: timestamp(open.entry_time),
            entryPrice: Number(open.entry_price),
        } : null;
        return { orders, trades, equity, position, source: result };
    }

    function finiteOrNull(value) {
        if (value === undefined || value === null || value === "") return null;
        const number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function formatNumber(value) {
        const number = finiteOrNull(value);
        if (number === null) return "—";
        if (number === 0) return "0";
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 12 }).format(number);
    }

    function formatPercent(value) {
        const number = finiteOrNull(value);
        if (number === null) return "—";
        if (number === 0) return "0%";
        return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 12 }).format(number * 100)}%`;
    }

    function equityChartPoints(analysis) {
        if (!Array.isArray(analysis?.equity_curve)) return [];
        return analysis.equity_curve.flatMap(point => {
            const x = timestamp(point.timestamp);
            const y = finiteOrNull(point.equity);
            return x === null || y === null ? [] : [{ x, y }];
        });
    }

    function maximumDrawdown(drawdownPoints) {
        const valid = drawdownPoints.filter(point => Number.isFinite(point.y));
        if (!valid.length) return null;
        return valid.reduce((minimum, point) => point.y < minimum.y ? point : minimum, valid[0]);
    }

    return {
        TIMEFRAMES, ChartState, timestamp, nearestIndex, centeredSlice,
        marketDataParams, transformBacktest, maximumDrawdown,
        formatNumber, formatPercent, equityChartPoints,
    };
});
