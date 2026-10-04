(() => {
    "use strict";

    const $ = selector => document.querySelector(selector);
    const state = new ResearchData.ChartState();
    const instrument = $("#instrument");
    const visibleCount = $("#visible-count");
    const status = $("#status");
    const importStatus = $("#import-status");
    const fileInput = $("#csv-file");
    let loadSequence = 0;
    let activeRequest = null;
    const importLock = MarketDataImportLock.createImportLock();
    let pendingBacktest = null;
    try {
        const stored = sessionStorage.getItem("marketResearch.selectedBacktest");
        if (stored) {
            pendingBacktest = JSON.parse(stored);
            sessionStorage.removeItem("marketResearch.selectedBacktest");
            if (!ResearchData.TIMEFRAMES.includes(pendingBacktest.timeframe) || !pendingBacktest.backtest_result) pendingBacktest = null;
            else state.timeframe = pendingBacktest.timeframe;
        }
    } catch {
        sessionStorage.removeItem("marketResearch.selectedBacktest");
        pendingBacktest = null;
    }

    const chart = new ResearchChart({ state });
    chart.onTimeframe = timeframe => setTimeframe(timeframe);
    chart.onReset = () => {
        state.resetView();
        visibleCount.value = state.visibleCount;
        chart.fitVisibleCount(state.anchorTimestamp);
        chart.updateData();
    };
    chart.onFit = () => {
        state.visibleCount = Math.max(1, state.data.length);
        visibleCount.value = state.visibleCount;
        chart.fitVisibleCount(null);
        chart.updateData();
    };

    function setStatus(message, kind = "info") {
        status.textContent = message;
        status.dataset.kind = kind;
        status.hidden = !message;
        importStatus.textContent = message || "Ready";
        importStatus.dataset.kind = kind;
    }

    async function fetchMarketData({ keepContext = true, centerTimestamp = null } = {}) {
        const symbol = instrument.value;
        const period = state.timeframe;
        const focusRange = pendingBacktest?.focus_range || null;
        const focusedTradeId = pendingBacktest?.trade_id ?? null;
        const sequence = ++loadSequence;
        activeRequest?.abort();
        activeRequest = new AbortController();
        const anchor = centerTimestamp ? ResearchData.timestamp(centerTimestamp) : keepContext
            ? (state.frozenTimestamp ?? state.selectedTimestamp ?? chart.centerTimestamp())
            : null;
        setStatus(`Loading ${symbol} ${period} history…`, "loading");
        chart.setTimeframeButtons(period);
        $("#workspace-symbol").textContent = symbol;
        $("#ohlc-symbol").textContent = symbol;
        const params = ResearchData.marketDataParams(symbol, period,
            focusRange ? state.visibleCount : 5000, anchor, focusRange);
        try {
            const response = await fetch(`/market-data?${params.toString()}`, { signal: activeRequest.signal });
            const result = await response.json();
            if (!response.ok) throw new Error(result.detail || "Market data could not be loaded.");
            if (sequence !== loadSequence) return;
            state.instrument = symbol;
            state.timeframe = period;
            state.data = result.data || [];
            updateSummary(state.data);
            if (pendingBacktest) {
                chart.setBacktestResult(pendingBacktest.backtest_result);
                chart.focusTrade(focusedTradeId);
                pendingBacktest = null;
            }
            if (!state.data.length) {
                setStatus(`${symbol} has no data for ${period}.`, "warning");
                chart.setData([]);
                return;
            }
            chart.setData(state.data, { resetDomain: true, anchor });
            if (focusRange) chart.fitLoadedRange();
            setStatus(`${state.data.length.toLocaleString()} bars loaded`, "success");
            window.setTimeout(() => { if (sequence === loadSequence) setStatus(""); }, 2500);
        } catch (error) {
            if (error.name === "AbortError") return;
            setStatus(error.message || "Market data request failed.", "error");
        }
    }

    function updateSummary(data) {
        $("#observations").textContent = data.length.toLocaleString();
        if (!data.length) {
            $("#history-range").textContent = "—";
            $("#last-close").textContent = "—";
            $("#spread").textContent = "—";
            return;
        }
        const date = value => new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
        $("#history-range").textContent = `${date(data[0].timestamp)} — ${date(data[data.length - 1].timestamp)}`;
        $("#last-close").textContent = Number(data[data.length - 1].close).toFixed(5);
        $("#spread").textContent = String(data[data.length - 1].spread);
        visibleCount.max = "5000";
        visibleCount.value = state.visibleCount;
    }

    async function setTimeframe(value) {
        if (!ResearchData.TIMEFRAMES.includes(value) || value === state.timeframe) return;
        state.setTimeframe(value);
        await fetchMarketData({ keepContext: true });
    }

    async function loadInstruments() {
        try {
            const response = await fetch("/instruments");
            if (!response.ok) return;
            const records = await response.json();
            if (!Array.isArray(records) || !records.length) return;
            const selected = instrument.value;
            instrument.replaceChildren(...records.map(record => {
                const option = document.createElement("option");
                option.value = record.symbol; option.textContent = record.symbol;
                return option;
            }));
            const requested = pendingBacktest?.symbol || selected;
            instrument.value = records.some(record => record.symbol === requested) ? requested : records[0].symbol;
            state.instrument = instrument.value;
        } catch (_) {
            // Market-data loading remains available when the instrument list request fails.
        }
    }

    instrument.addEventListener("change", () => {
        state.clearSelection();
        fetchMarketData({ keepContext: false });
    });
    visibleCount.addEventListener("change", () => {
        const count = Math.max(50, Math.min(5000, Math.floor(Number(visibleCount.value) || 1000)));
        visibleCount.value = count;
        state.visibleCount = count;
        chart.fitVisibleCount(state.anchorTimestamp ?? chart.centerTimestamp());
    });
    visibleCount.addEventListener("keydown", event => { if (event.key === "Enter") visibleCount.blur(); });

    fileInput.addEventListener("change", () => {
        const file = fileInput.files?.[0];
        $("#file-name").textContent = file ? `${file.name} · ${(file.size / 1048576).toFixed(1)} MB` : "";
    });

    $("#import-form").addEventListener("submit", async event => {
        event.preventDefault();
        const file = fileInput.files?.[0];
        if (!file) { setStatus("Choose a CSV file to import.", "warning"); return; }
        if (!importLock.tryStart()) return;
        const button = $("#import-submit");
        button.disabled = true;
        button.textContent = "Importing…";
        setStatus("Importing historical data…", "loading");
        try {
            const body = new FormData(); body.append("csv_file", file);
            const response = await fetch("/import-csv", { method: "POST", body });
            const responseBody = await response.text();
            let result;
            try {
                result = responseBody ? JSON.parse(responseBody) : {};
            } catch {
                if (!response.ok) throw new Error(responseBody.trim() || `Import failed (HTTP ${response.status}).`);
                throw new Error("The server returned an invalid response for the CSV import.");
            }
            if (!response.ok) throw new Error(result.detail || result.message || "Import failed.");
            const previousSymbol = instrument.value;
            if (![...instrument.options].some(option => option.value === result.symbol)) {
                const option = document.createElement("option"); option.value = result.symbol; option.textContent = result.symbol;
                instrument.add(option);
            }
            instrument.value = result.symbol;
            setStatus(result.message || (result.rows_inserted ? `Imported ${result.rows_inserted} candles.` : "No new data."), "success");
            if (result.rows_inserted > 0 || previousSymbol !== result.symbol) {
                state.clearSelection();
                await fetchMarketData({ keepContext: false });
            }
        } catch (error) {
            setStatus(error.message || "Import failed.", "error");
        } finally {
            importLock.finish();
            button.disabled = false;
            button.textContent = "Import";
        }
    });

    function handleShortcut(event) {
        if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return;
        const target = event.target;
        if (target?.matches("input, select, textarea, [contenteditable='true'], button")) return;
        const key = event.key.toLowerCase();
        const shortcuts = { "1": "M1", "5": "M5", "3": "M15", h: "H1", "4": "H4", d: "D1" };
        if (shortcuts[key]) { event.preventDefault(); setTimeframe(shortcuts[key]); return; }
        if (key === "r") { event.preventDefault(); chart.onReset(); }
        else if (key === "+" || key === "=") { event.preventDefault(); chart.viewport.zoom(0.8); }
        else if (key === "-") { event.preventDefault(); chart.viewport.zoom(1.25); }
        else if (key === "c") {
            const toggle = $("#toggle-crosshair"); toggle.checked = !toggle.checked; toggle.dispatchEvent(new Event("change"));
        }
    }
    document.addEventListener("keydown", handleShortcut);

    window.addEventListener("research:backtest-result", event => chart.setBacktestResult(event.detail));
    window.addEventListener("research:signals", event => chart.setSignals(event.detail));

    (async () => {
        await loadInstruments();
        chart.setTimeframeButtons(state.timeframe);
        await fetchMarketData({ keepContext: false, centerTimestamp: pendingBacktest?.center_timestamp || null });
    })();
})();
