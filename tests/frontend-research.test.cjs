const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const Research = require("../app/static/js/research-data.js");
const Parameters = require("../app/static/js/research-parameters.js");
const TradeHistory = require("../app/static/js/trade-history.js");
const ExecutionLog = require("../app/static/js/execution-log.js");

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
    const localAnchor = new Date(anchor);
    const pad = value => String(value).padStart(2, "0");
    assert.equal(params.get("center_timestamp"),
        `${localAnchor.getFullYear()}-${pad(localAnchor.getMonth() + 1)}-${pad(localAnchor.getDate())}` +
        `T${pad(localAnchor.getHours())}:${pad(localAnchor.getMinutes())}:${pad(localAnchor.getSeconds())}.` +
        String(localAnchor.getMilliseconds()).padStart(3, "0"));
    assert.equal(params.get("timeframe"), "M15");
});

test("individual trade handoff focuses its canonical period while general view remains separate", () => {
    const trade = { id: 17, entry_time: rows[1].timestamp, exit_time: rows[3].timestamp };
    const result = { definition: { symbol: "GBPUSD", timeframe: "H4" }, backtest_result: {
        orders: [], trades: [], execution_trace: [],
    } };
    const focus = TradeHistory.chartHandoff(result, trade, true);
    assert.equal(focus.trade_id, 17);
    assert.deepEqual(focus.focus_range, { start: trade.entry_time, end: trade.exit_time });
    assert.equal(focus.timeframe, "H4");
    assert.equal(focus.center_timestamp, undefined);
    const params = Research.marketDataParams("GBPUSD", focus.timeframe, 1000, null, focus.focus_range);
    assert.equal(params.get("focus_start"), trade.entry_time);
    assert.equal(params.get("focus_end"), trade.exit_time);

    const general = TradeHistory.chartHandoff(result, trade, false);
    assert.equal(general.center_timestamp, trade.entry_time);
    assert.equal(general.focus_range, undefined);
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
    assert.match(html, /id="space-parameters"/);
    assert.match(html, /Parameter batch/);
    assert.doesNotMatch(html, /Single backtest/);
    for (const text of ["fast_period", "slow_period", "Fast period", "Slow period"]) {
        assert.equal(html.includes(text), false);
        assert.equal(workspace.includes(text), false);
        assert.equal(service.includes(text), false);
        assert.equal(engine.includes(text), false);
    }
    assert.match(workspace, /renderStrategyParameters\(strategy,Object\.fromEntries\(definition\.parameters/);
    assert.match(workspace, /definition\.strategy_id/);
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
    assert.equal(trades[0].entry_commission, 0.05);
    assert.equal(trades[0].exit_commission, 0.05);
    assert.equal(trades[0].duration_ms, undefined);
    assert.equal(trades[1].side, "SELL · SHORT");
    assert.equal(trades[1].quantity, null);
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
    assert.deepEqual(TradeHistory.readTrades({ trades: [{ side: "long" }] }),
        { trades: [], malformedCount: 1 });
});

test("all trades are retained and malformed trade records are isolated", () => {
    const source = Array.from({ length: 52 }, (_, index) => ({
        sequence: index + 1, side: "long", entry_time: rows[0].timestamp,
        exit_time: rows[1].timestamp, entry_price: 10, exit_price: 12,
    }));
    const complete = TradeHistory.readTrades({ trades: source });
    assert.equal(complete.trades.length, 52);
    assert.equal(complete.malformedCount, 0);
    const mixed = TradeHistory.readTrades({ trades: [source[0], { side: "long" }, source[1]] });
    assert.deepEqual(mixed.trades.map(trade => trade.id), [1, 2]);
    assert.equal(mixed.malformedCount, 1);
});

test("main and research pages keep single-test and research-analysis responsibilities separate", () => {
    const main = fs.readFileSync("app/templates/index.html", "utf8");
    const research = fs.readFileSync("app/templates/research.html", "utf8");
    const runner = fs.readFileSync("app/static/js/backtest-workspace.js", "utf8");
    const workspace = fs.readFileSync("app/static/js/research-workspace.js", "utf8");
    const app = fs.readFileSync("app/static/js/app.js", "utf8");
    assert.match(main, /id="test-form"/);
    for (const field of ["test-strategy", "instrument", "test-timeframe", "test-start", "test-end", "test-parameters"])
        assert.match(main, new RegExp(`id="${field}"`));
    assert.match(main, /id="price-chart"/);
    assert.match(main, /id="trade-history-title">Trade History/);
    assert.match(main, /id="execution-log-title">Order &amp; Strategy Log/);
    assert.match(main, /id="execution-log-empty">No execution events\./);
    assert.match(main, /id="trade-empty">No completed trades\./);
    assert.match(main, /research-result-sections\.js/);
    assert.match(runner, /request\("\/api\/research\/run"/);
    assert.match(runner, /mode: "single"/);
    assert.match(runner, /ResearchResultSections\.renderTradeHistory/);
    assert.match(runner, /ResearchResultSections\.renderExecutionLog/);
    assert.match(runner, /TradeHistory\.chartHandoff/);
    assert.match(app, /research:focus-trade/);
    assert.doesNotMatch(research, /Trade History|Order &amp; Strategy Log|trade-records|execution-log-records/);
    assert.match(research, /Training → OOS selection/);
    assert.match(research, /Latest Test source/);
    assert.match(research, /source-context/);
    for (const duplicated of ['id="strategy"', 'id="symbol"', 'name="timeframe"',
        'name="start"', 'name="end"', 'name="initial_cash"', 'name="position_size"',
        'name="commission_per_unit"', 'name="commission_rate"', 'name="spread_scale"',
        'name="slippage"', 'name="periods_per_year"', 'name="risk_free_rate"', 'name="target_return"'])
        assert.equal(research.includes(duplicated), false, `${duplicated} duplicates single-test setup`);
    assert.match(research, /name="search_method"/);
    assert.match(research, /name="training_start"/);
    assert.match(research, /name="training_days"/);
    assert.match(workspace, /request\("\/latest-test"\)/);
    assert.match(workspace, /BroadcastChannel\("market-research-latest-test"\)/);
    assert.match(runner, /BroadcastChannel\("market-research-latest-test"\)/);
    assert.match(workspace, /strategy_id:definition\.strategy_id/);
    assert.match(workspace, /initial_cash:config\.initial_cash/);
    assert.match(research, /Walk-forward/);
    assert.match(research, /advanced-tools/);
    assert.match(research, /Experiment records/);
});

test("canonical execution event reader retains event order and malformed isolation", () => {
    const raw = [
        { sequence: 1, timestamp: rows[0].timestamp, event_type: "bar", bar_index: 0, details: [["low", 9]] },
        { sequence: 2, timestamp: rows[0].timestamp, event_type: "order_created", order_id: 5,
            order_type: "stop", reason: "stop_entry_pending" },
        { sequence: 3, timestamp: rows[0].timestamp, event_type: "order_pending", order_id: 5,
            reason: "eligible_next_bar" },
        { sequence: 4, timestamp: rows[1].timestamp, event_type: "order_triggered", order_id: 5,
            trigger_price: 12 },
        { sequence: 5, timestamp: rows[1].timestamp, event_type: "execution", order_id: 5, price: 12 },
        { sequence: 6, timestamp: rows[1].timestamp, event_type: "position_opened", trade_id: 7, stop_loss: 9 },
        { sequence: 7, timestamp: rows[2].timestamp, event_type: "stop_updated", trade_id: 7, stop_loss: 10,
            details: [["previous_stop_loss", 9]] },
        { sequence: 8, timestamp: rows[3].timestamp, event_type: "position_closed", trade_id: 7 },
        { sequence: 9, timestamp: rows[3].timestamp, event_type: "pnl_calculated", trade_id: 7 },
        { sequence: 10, timestamp: rows[3].timestamp, event_type: "trade_created", trade_id: 7 },
    ];
    const parsed = ExecutionLog.readEvents({ execution_trace: raw });
    assert.deepEqual(parsed.events.map(event => event.sequence), raw.map(event => event.sequence));
    assert.equal(parsed.events[2].event_type, "order_pending");
    assert.equal(ExecutionLog.detailText(parsed.events[6]), "previous_stop_loss=9");
    const badEvent = ExecutionLog.readEvents({ execution_trace: [raw[0], null, raw[1]] });
    assert.deepEqual(badEvent.events.map(event => event.sequence), [1, 2]);
    assert.equal(badEvent.malformedCount, 1);
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
    assert.equal(result.trades[0].entryCommission, 0.4);
    assert.equal(result.trades[0].exitCommission, 0.4);
    assert.equal(result.trades[0].returnPct, undefined);
    assert.equal(result.trades[0].durationMs, undefined);
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
