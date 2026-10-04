(() => {
    "use strict";

    const $ = id => document.getElementById(id);
    const form = $("test-form");
    if (!form) return;
    let strategies = [];

    function setStatus(message, isError = false) {
        const target = $("test-api-status");
        target.textContent = message;
        target.dataset.kind = isError ? "error" : "ready";
    }

    async function request(url, options = {}) {
        const response = await fetch(url, options);
        let value;
        try { value = await response.json(); }
        catch { throw new Error("The server returned an invalid response."); }
        if (!response.ok) {
            const detail = value?.detail;
            const message = Array.isArray(detail)
                ? detail.map(item => `${(item.loc || []).join(".")}: ${item.msg}`).join("; ")
                : typeof detail === "string" ? detail : `Request failed (${response.status}).`;
            throw new Error(message);
        }
        return value;
    }

    function renderParameters(strategy) {
        const definitions = Array.isArray(strategy?.parameters) ? strategy.parameters : [];
        const container = $("test-parameters");
        container.replaceChildren();
        $("test-parameter-empty").hidden = definitions.length > 0;
        for (const definition of definitions) {
            const label = document.createElement("label");
            label.textContent = definition.label || definition.name.replaceAll("_", " ");
            let control;
            if (definition.kind === "choice" || definition.kind === "boolean") {
                control = document.createElement("select");
                const choices = definition.kind === "boolean" ? [true, false] : definition.choices || [];
                for (const choice of choices) {
                    const option = document.createElement("option");
                    option.value = String(choice);
                    option.textContent = String(choice);
                    control.append(option);
                }
                control.value = String(definition.default ?? choices[0] ?? "");
            } else {
                control = document.createElement("input");
                control.type = definition.kind === "integer" || definition.kind === "float" ? "number" : "text";
                control.value = definition.default ?? "";
                if (definition.kind === "integer" || definition.kind === "float") {
                    control.step = definition.kind === "integer" ? String(definition.step || 1) : "any";
                    if (definition.minimum != null) control.min = String(definition.minimum);
                    if (definition.maximum != null) control.max = String(definition.maximum);
                    control.required = true;
                }
            }
            control.dataset.parameter = definition.name;
            label.append(control);
            container.append(label);
        }
    }

    function addMinutes(value, amount) {
        const parts = value.slice(0, 16).split(/[-T:]/).map(Number);
        const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2], parts[3], parts[4] + amount));
        return date.toISOString().slice(0, 16);
    }

    function intervalMinutes(value) {
        return { M1: 1, M5: 5, M15: 15, H1: 60, H4: 240, D1: 1440 }[value] || 1;
    }

    async function loadCatalog() {
        try {
            const [catalog, instruments] = await Promise.all([
                request("/api/research/strategies"), request("/instruments"),
            ]);
            strategies = Array.isArray(catalog) ? catalog : [];
            if (!strategies.length) throw new Error("No strategies are registered.");
            $("test-strategy").replaceChildren(...strategies.map(strategy => {
                const option = document.createElement("option");
                option.value = strategy.id;
                option.textContent = `${strategy.id} · v${strategy.version}`;
                return option;
            }));
            $("test-strategy").addEventListener("change", () => renderParameters(
                strategies.find(strategy => strategy.id === $("test-strategy").value)));
            renderParameters(strategies[0]);
            if (Array.isArray(instruments) && instruments.length) {
                const existing = $("instrument").value;
                $("instrument").replaceChildren(...instruments.map(item => {
                    const option = document.createElement("option");
                    option.value = item.symbol;
                    option.textContent = item.symbol;
                    return option;
                }));
                if (instruments.some(item => item.symbol === existing)) $("instrument").value = existing;
            }
            await loadDataRange();
            setStatus("Test configuration ready");
        } catch (error) {
            setStatus(error.message || "Unable to load test configuration.", true);
        }
    }

    async function loadDataRange() {
        const symbol = $("instrument").value;
        if (!symbol) return;
        const catalog = await request(`/api/research/market-data/${encodeURIComponent(symbol)}`);
        $("test-data-range").textContent = catalog.count
            ? `${catalog.count.toLocaleString()} stored source rows · ${catalog.start} through ${catalog.end}`
            : "No market data stored for this instrument.";
        if (catalog.count) {
            const start = String(catalog.start).slice(0, 16);
            const end = addMinutes(String(catalog.end), intervalMinutes($("test-timeframe").value));
            if (!$("test-start").value) $("test-start").value = start;
            if (!$("test-end").value) $("test-end").value = end;
        }
    }

    function payload() {
        const fields = new FormData(form);
        const parameterInputs = Object.fromEntries([...$("test-parameters").querySelectorAll("[data-parameter]")]
            .map(control => [control.dataset.parameter, control.value]));
        const currentStrategy = strategies.find(strategy => strategy.id === fields.get("strategy_id"));
        const parameters = ResearchParameters.makePayload(currentStrategy?.parameters || [],
            Object.fromEntries(Object.entries(parameterInputs).map(([name, value]) => [name, { value }])), false).parameters;
        const body = {
            mode: "single",
            strategy_id: fields.get("strategy_id"),
            symbol: fields.get("symbol"),
            timeframe: fields.get("timeframe"),
            start: fields.get("start"),
            end: fields.get("end"),
            initial_cash: Number(fields.get("initial_cash")),
            position_size: Number(fields.get("position_size")),
            commission_per_unit: Number(fields.get("commission_per_unit")),
            commission_rate: Number(fields.get("commission_rate")),
            spread_scale: Number(fields.get("spread_scale")),
            slippage: Number(fields.get("slippage")),
            risk_free_rate: Number(fields.get("risk_free_rate")),
            target_return: Number(fields.get("target_return")),
            parameters,
        };
        if (fields.get("periods_per_year")) body.periods_per_year = Number(fields.get("periods_per_year"));
        return body;
    }

    function displayValue(value, percent = false) {
        if (value == null || !Number.isFinite(Number(value))) return "—";
        const numeric = Number(value);
        return `${percent ? (numeric * 100).toFixed(2) : new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(numeric)}${percent ? "%" : ""}`;
    }

    function renderResult(row) {
        const analysis = row.analysis_result || {};
        $("test-result").hidden = false;
        $("test-result-title").textContent = `${row.definition.strategy_id} · ${row.definition.symbol} ${row.definition.timeframe} · completed`;
        const metrics = [
            ["Total return", displayValue(analysis.total_return, true)],
            ["Net trade P&L", displayValue(analysis.trades?.net_profit)],
            ["Completed trades", analysis.trades?.total_trades ?? "—"],
            ["Win rate", displayValue(analysis.trades?.win_rate, true)],
            ["Max drawdown", displayValue(analysis.drawdown?.max_drawdown_pct, true)],
        ];
        $("test-result-metrics").replaceChildren(...metrics.map(([title, value]) => {
            const item = document.createElement("div");
            item.className = "test-metric";
            const label = document.createElement("small"); label.textContent = title;
            const output = document.createElement("b"); output.textContent = value;
            item.append(label, output);
            return item;
        }));
        ResearchResultSections.renderTradeHistory(row, {
            onView: (selectedRow, trade) => dispatchChart(TradeHistory.chartHandoff(selectedRow, trade, false)),
            onChart: (selectedRow, trade) => dispatchChart(TradeHistory.chartHandoff(selectedRow, trade, true), true),
        });
        ResearchResultSections.renderExecutionLog(row);
        dispatchChart({
            symbol: row.definition.symbol,
            timeframe: row.definition.timeframe,
            backtest_result: row.backtest_result,
        });
    }

    function dispatchChart(handoff, focusTrade = false) {
        window.dispatchEvent(new CustomEvent(focusTrade ? "research:focus-trade" : "research:backtest-result", { detail: handoff }));
    }

    form.addEventListener("submit", async event => {
        event.preventDefault();
        $("test-error").hidden = true;
        if (!form.reportValidity()) return;
        if (Date.parse($("test-end").value) < Date.parse($("test-start").value)) {
            $("test-error").textContent = "Test end must be at or after test start.";
            $("test-error").hidden = false;
            return;
        }
        const button = $("test-run-button");
        button.disabled = true;
        button.textContent = "Running…";
        setStatus("Running test…");
        try {
            const result = await request("/api/research/run", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload()),
            });
            if (!Array.isArray(result.results)) throw new Error("The server returned an invalid test result.");
            const row = result.results.find(item => item?.status === "completed" && item.backtest_result && item.analysis_result);
            if (!row) {
                const failed = result.results[0]?.failure;
                throw new Error(failed ? `${failed.exception_type}: ${failed.message}` : "The test produced no completed result.");
            }
            renderResult(row);
            setStatus("Test completed");
        } catch (error) {
            $("test-error").textContent = error.message || "Test failed.";
            $("test-error").hidden = false;
            setStatus("Test failed", true);
        } finally {
            button.disabled = false;
            button.textContent = "Run Test";
        }
    });

    $("instrument").addEventListener("change", () => loadDataRange().catch(error => {
        $("test-error").textContent = error.message;
        $("test-error").hidden = false;
    }));
    $("test-timeframe").addEventListener("change", () => {
        const current = $("test-end").value;
        if (current) $("test-end").value = addMinutes(current, intervalMinutes($("test-timeframe").value) - intervalMinutes($("test-timeframe").dataset.previous || "H4"));
        $("test-timeframe").dataset.previous = $("test-timeframe").value;
    });

    ResearchResultSections.renderTradeHistory(null);
    ResearchResultSections.renderExecutionLog(null);
    loadCatalog();
})();
