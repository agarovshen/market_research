const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const Research = require("../app/static/js/research-data.js");
const Parameters = require("../app/static/js/research-parameters.js");
const TradeHistory = require("../app/static/js/trade-history.js");

const rows = [
    { timestamp: "2024-01-01T00:00:00Z", open: 10, high: 11, low: 9, close: 10, tick_volume: 4, volume: 40, spread: 0.2 },
    { timestamp: "2024-01-01T00:01:00Z", open: 10, high: 12, low: 10, close: 12, tick_volume: 5, volume: 50, spread: 0.2 },
    { timestamp: "2024-01-01T00:02:00Z", open: 12, high: 13, low: 11, close: 11, tick_volume: 6, volume: 60, spread: 0.2 },
    { timestamp: "2024-01-01T00:03:00Z", open: 11, high: 12, low: 10, close: 12, tick_volume: 7, volume: 70, spread: 0.2 },
];

test("timeframe changes retain selected and frozen historical timestamps", () => {
    const state = new Research.ChartState();
    const selected = Research.timestamp(rows[1].timestamp);
    state.select(selected);
    assert.equal(state.setTimeframe("H4"), selected);
    state.toggleFrozen(selected);
    assert.equal(state.setTimeframe("M1"), selected);
    assert.throws(() => state.setTimeframe("M30"), RangeError);
});

test("centered data selection and endpoint params preserve a historical anchor", () => {
    const anchor = Research.timestamp(rows[2].timestamp);
    assert.deepEqual(Research.centeredSlice(rows, 3, anchor), rows.slice(1, 4));
    const params = Research.marketDataParams("EURUSD", "M15", 5000, anchor);
    assert.equal(params.get("center_timestamp"), new Date(anchor).toISOString());
    assert.equal(params.get("timeframe"), "M15");
});

test("selection, freeze toggling, and reset retain the historical point", () => {
    const state = new Research.ChartState();
    const time = Research.timestamp(rows[2].timestamp);
    assert.equal(state.selectCandle(rows, 2), time);
    assert.equal(state.selectCandle(rows, -1), null);
    assert.equal(state.toggleFrozen(), time);
    assert.equal(state.toggleFrozen(), null);
    state.toggleFrozen(time);
    state.visibleCount = 3000;
    state.resetView();
    assert.equal(state.visibleCount, 1000);
    assert.equal(state.anchorTimestamp, time);
});

test("strategy parameter payload supports zero, custom, fixed and selected search parameters", () => {
    assert.deepEqual(Parameters.makePayload([], {}, false), { parameters: {}, parameter_space: [] });
    const schema = [
        { name: "body_ratio", kind: "float", default: 0.6 },
        { name: "breakout_buffer", kind: "integer", default: 2 },
        { name: "entry_style", kind: "choice", choices: ["stop", "market"], default: "stop" },
    ];
    const result = Parameters.makePayload(schema, {
        body_ratio: { value: "0.75", optimize: false },
        breakout_buffer: { value: "2", optimize: true, minimum: "1", maximum: "5", step: "2" },
        entry_style: { value: "stop", optimize: true, choices: ["stop", "market"] },
    }, true);
    assert.deepEqual(result.parameters, { body_ratio: 0.75, breakout_buffer: 2, entry_style: "stop" });
    assert.deepEqual(result.parameter_space, [
        { name: "body_ratio", kind: "fixed", value: 0.75 },
        { name: "breakout_buffer", kind: "integer", minimum: 1, maximum: 5, step: 2 },
        { name: "entry_style", kind: "choice", choices: ["stop", "market"] },
    ]);
    assert.throws(() => Parameters.makePayload([{ name: "mode", kind: "choice", choices: ["a"] }],
        { mode: { value: "bad" } }, false), /permitted choice/);
});

test("generic chart has no indicator, volume or per-bar return controls or panels", () => {
    const html = fs.readFileSync("app/templates/index.html", "utf8");
    const chart = fs.readFileSync("app/static/js/research-chart.js", "utf8");
    const styles = fs.readFileSync("app/static/css/input.css", "utf8");
    for (const obsolete of ["toggle-sma", "toggle-ema", "volume-chart", "volume-caption", "return-chart", "PER-BAR RETURN", "Tick volume"])
        assert.equal(html.includes(obsolete) || chart.includes(obsolete), false, `${obsolete} is still in the generic chart`);
    assert.match(html, /id="price-chart"/);
    assert.match(chart, /this\.makeChart\("price"/);
    assert.match(chart, /crosshair/);
    assert.match(styles, /@media/);
    assert.deepEqual(new Research.ChartState().panels, { equity: false, drawdown: false });
});

test("research form consumes API strategy schemas without SMA-specific parameter branches", () => {
    const html = fs.readFileSync("app/templates/research.html", "utf8");
    const workspace = fs.readFileSync("app/static/js/research-workspace.js", "utf8");
    const service = fs.readFileSync("app/research/service.py", "utf8");
    const engine = fs.readFileSync("app/research/engine.py", "utf8");
    assert.match(html, /id="single-parameters"/);
    assert.match(html, /id="space-parameters"/);
    for (const text of ["fast_period", "slow_period", "Fast period", "Slow period"]) {
        assert.equal(html.includes(text), false);
        assert.equal(workspace.includes(text), false);
        assert.equal(service.includes(text), false);
        assert.equal(engine.includes(text), false);
    }
    assert.match(workspace, /renderStrategyParameters\(strategies\[0\]\)/);
    assert.match(workspace, /renderStrategyParameters\(strategies\.find/);
});

test("trade history preserves canonical fields and exact chart marker coordinates", () => {
    const backtest = { trades: [
        { sequence: 7, side: "long", quantity: 2, entry_time: rows[0].timestamp, exit_time: rows[0].timestamp,
            entry_price: 10.25, exit_price: 10.5, gross_pnl: 0.5, net_pnl: 0.4,
            entry_commission: 0.05, exit_commission: 0.05 },
        { sequence: 8, side: "short", entry_time: rows[2].timestamp, exit_time: rows[3].timestamp,
            entry_price: 11, exit_price: 12, gross_pnl: -1, net_pnl: -1 },
    ] };
    const trades = TradeHistory.tradesFromBacktest(backtest);
    assert.equal(trades[0].id, 7);
    assert.equal(trades[0].side, "BUY · LONG");
    assert.equal(trades[0].entry_time, rows[0].timestamp);
    assert.equal(trades[0].exit_time, rows[0].timestamp);
    assert.equal(trades[0].entry_price, 10.25);
    assert.equal(trades[0].exit_price, 10.5);
    assert.equal(trades[0].net_pnl, 0.4);
    assert.equal(trades[1].side, "SELL · SHORT");
    assert.equal(trades[1].quantity, null);
    assert.equal(trades[1].commission, 0);
    assert.deepEqual(TradeHistory.markersFromTrades(trades), [
        { trade_id: 7, kind: "entry", side: "BUY · LONG", timestamp: Date.parse(rows[0].timestamp), price: 10.25 },
        { trade_id: 7, kind: "exit", side: "BUY · LONG", timestamp: Date.parse(rows[0].timestamp), price: 10.5 },
        { trade_id: 8, kind: "entry", side: "SELL · SHORT", timestamp: Date.parse(rows[2].timestamp), price: 11 },
        { trade_id: 8, kind: "exit", side: "SELL · SHORT", timestamp: Date.parse(rows[3].timestamp), price: 12 },
    ]);
});

test("empty and malformed BacktestResult trade data is explicit and never stale", () => {
    assert.deepEqual(TradeHistory.tradesFromBacktest({ trades: [] }), []);
    assert.deepEqual(TradeHistory.tradesFromBacktest({ trades: null }), []);
    assert.throws(() => TradeHistory.tradesFromBacktest({ trades: [{ side: "long" }] }), /canonical entry\/exit/);
});

test("selected research result is phase-specific and switching to an empty result clears trades", () => {
    const selected = {
        definition: { experiment_id: "train-a", phase: "train" }, status: "completed",
        backtest_result: { trades: [{ sequence: 1, side: "long", entry_time: rows[0].timestamp,
            exit_time: rows[1].timestamp, entry_price: 10, exit_price: 12, net_pnl: 2 }] },
    };
    const unrelated = {
        definition: { experiment_id: "oos-a", phase: "oos" }, status: "completed",
        backtest_result: { trades: [] },
    };
    assert.equal(TradeHistory.resultForId([selected, unrelated], "train-a"), selected);
    assert.deepEqual(TradeHistory.tradesFromBacktest(TradeHistory.resultForId([selected, unrelated], "oos-a").backtest_result), []);
    assert.equal(TradeHistory.resultForId([{ ...selected, status: "failed" }], "train-a"), null);
});

test("BacktestResult transformation maps orders, trade details, and equity fields", () => {
    const result = Research.transformBacktest({
        orders: [
            { sequence: 1, action: "open", side: "long", quantity: 2, filled_at: rows[0].timestamp,
                fill_price: 10.1, reference_price: 10, commission: 0.4, spread_cost: 0.2, slippage_cost: 0.1 },
            { sequence: 2, action: "close", side: "long", quantity: 2, filled_at: rows[2].timestamp,
                fill_price: 11.9, reference_price: 12, commission: 0.4, spread_cost: 0.2, slippage_cost: 0.1 },
        ],
        trades: [{ sequence: 1, side: "long", quantity: 2, entry_time: rows[0].timestamp,
            exit_time: rows[2].timestamp, entry_price: 10.1, exit_price: 11.9,
            entry_commission: 0.4, exit_commission: 0.4, gross_pnl: 3.6, net_pnl: 2.8 }],
        equity_curve: [
            { timestamp: rows[0].timestamp, cash: 979.4, equity: 999.4, unrealized_pnl: 0, drawdown_pct: 0 },
            { timestamp: rows[2].timestamp, cash: 1002.8, equity: 1002.8, unrealized_pnl: 0, drawdown_pct: -0.02 },
        ],
        open_position: null,
    });
    assert.equal(result.orders[0].label, "LONG ENTRY");
    assert.equal(result.orders[0].marketAction, "BUY");
    assert.equal(result.orders[1].label, "LONG EXIT");
    assert.equal(result.orders[1].marketAction, "SELL");
    assert.equal(result.trades[0].returnPct, 2.8 / 20.2 * 100);
    assert.equal(result.trades[0].commission, 0.8);
    assert.equal(result.trades[0].spreadCost, 0.4);
    assert.equal(result.trades[0].slippage, 0.2);
    assert.equal(result.equity[1].equity, 1002.8);
    assert.equal(result.equity[1].drawdown, -0.02);
    assert.deepEqual(Research.maximumDrawdown(result.equity.map(point => ({ x: point.x, y: point.drawdown }))),
        { x: result.equity[1].x, y: -0.02 });
});

test("drawdown stays absent when the result does not supply drawdown values", () => {
    const result = Research.transformBacktest({
        equity_curve: [{ timestamp: rows[0].timestamp, cash: 100, equity: 100, unrealized_pnl: 0 }],
    });
    assert.equal(result.equity[0].drawdown, null);
    assert.equal(Research.maximumDrawdown([]), null);
});
