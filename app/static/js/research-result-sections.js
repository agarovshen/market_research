(function (root, factory) {
    const api = factory(root.TradeHistory, root.ExecutionLog);
    if (typeof module === "object" && module.exports) module.exports = api;
    root.ResearchResultSections = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (TradeHistory, ExecutionLog) {
    "use strict";

    const $ = id => document.getElementById(id);
    const format = value => value == null ? "—" : new Intl.NumberFormat(undefined, { maximumFractionDigits: 12 }).format(value);

    function renderTradeHistory(row, { onView = null, onChart = null } = {}) {
        const body = $("trade-records");
        if (!body) return;
        body.replaceChildren();
        const result = row?.backtest_result;
        const parsed = result ? TradeHistory.readTrades(result) : { trades: [], malformedCount: 0 };
        const trades = parsed.trades;
        $("trade-empty").hidden = trades.length > 0;
        const viewButton = $("inspect-trades");
        if (viewButton) {
            viewButton.disabled = !result || trades.length === 0;
            viewButton.onclick = () => {
                if (!trades.length || !onView) return;
                onView(row, trades[0]);
            };
        }
        const context = $("trade-result-context");
        if (context) context.textContent = row
            ? `${row.definition?.phase?.toUpperCase() || "TEST"} · ${row.definition?.symbol || "—"} ${row.definition?.timeframe || "—"}` +
              `${row.definition?.experiment_id ? ` · ${row.definition.experiment_id}` : ""}` +
              (parsed.malformedCount ? ` · ${parsed.malformedCount} malformed trade row(s) omitted` : ` · ${trades.length} completed trades`)
            : "No test result selected.";

        body.replaceChildren(...trades.map(trade => {
            const tr = document.createElement("tr");
            tr.dataset.tradeId = String(trade.id);
            const values = [trade.id, trade.side, new Date(trade.entry_time).toLocaleString(),
                new Date(trade.exit_time).toLocaleString(), format(trade.entry_price), format(trade.exit_price),
                format(trade.quantity), format(trade.gross_pnl), format(trade.net_pnl),
                `${format(trade.entry_commission)} / ${format(trade.exit_commission)}`];
            for (const value of values) {
                const td = document.createElement("td");
                td.textContent = value ?? "—";
                tr.append(td);
            }
            const action = document.createElement("td"), inspect = document.createElement("button");
            inspect.type = "button";
            inspect.textContent = "Chart";
            inspect.setAttribute("aria-label", `Show trade ${trade.id} on price chart`);
            inspect.addEventListener("click", () => onChart?.(row, trade));
            action.append(inspect);
            tr.append(action);
            return tr;
        }));
    }

    function renderExecutionLog(row) {
        const body = $("execution-log-records");
        if (!body) return;
        body.replaceChildren();
        const parsed = ExecutionLog.readEvents(row?.backtest_result);
        const events = parsed.events;
        $("execution-log-empty").hidden = events.length > 0;
        const warning = $("execution-log-warning");
        if (warning) {
            warning.hidden = parsed.malformedCount === 0;
            warning.textContent = parsed.malformedCount
                ? `${parsed.malformedCount} malformed execution event(s) omitted.` : "";
        }
        const context = $("execution-log-context");
        if (context) context.textContent = row
            ? `${row.definition?.strategy_id || "Strategy"} · ${row.definition?.symbol || "—"} ${row.definition?.timeframe || "—"} · ${events.length} canonical event(s)`
            : "No test result selected.";
        const cell = value => {
            const td = document.createElement("td");
            td.textContent = value == null ? "—" : String(value);
            return td;
        };
        body.replaceChildren(...events.map(event => {
            const tr = document.createElement("tr");
            tr.dataset.eventSequence = String(event.sequence);
            tr.append(cell(event.sequence), cell(event.timestamp), cell(event.bar_index),
                cell(event.event_type.toUpperCase()), cell(event.side?.toUpperCase()),
                cell(event.order_id), cell(event.trade_id), cell(event.price),
                cell(event.quantity), cell(event.trigger_price), cell(event.stop_loss),
                cell(ExecutionLog.detailText(event)));
            return tr;
        }));
    }

    return { renderTradeHistory, renderExecutionLog };
});
