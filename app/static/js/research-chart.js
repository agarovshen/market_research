(function (root) {
    "use strict";

    const Data = root.ResearchData;
    const COLORS = {
        grid: "rgba(108, 128, 150, .13)", text: "#7f91a5",
        green: "#35c98b", red: "#f06467", cyan: "#54b8d4", violet: "#a78bfa",
    };
    const PANEL_IDS = ["price", "equity", "drawdown"];
    const NUM_FMT = new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 });
    const TIME_FMT = new Intl.DateTimeFormat(undefined, {
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit",
        minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "short",
    });

    function lowerBound(items, value, accessor) {
        let low = 0, high = items.length;
        while (low < high) {
            const middle = (low + high) >>> 1;
            if (accessor(items[middle]) < value) low = middle + 1;
            else high = middle;
        }
        return low;
    }

    class TimeViewport {
        constructor(charts, onChange) {
            this.charts = charts;
            this.onChange = onChange;
            this.min = null;
            this.max = null;
            this.fullMin = null;
            this.fullMax = null;
            this.minRange = 1;
            this.pending = false;
        }

        setBounds(min, max) {
            this.fullMin = min;
            this.fullMax = max;
        }

        setMinimumRange(value) { this.minRange = Math.max(1, value); }

        setDomain(min, max, source = null) {
            if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return;
            if (max - min < this.minRange) {
                const center = (min + max) / 2;
                min = center - this.minRange / 2;
                max = center + this.minRange / 2;
            }
            const span = max - min;
            if (this.fullMin !== null && this.fullMax !== null) {
                if (span >= this.fullMax - this.fullMin) [min, max] = [this.fullMin, this.fullMax];
                else {
                    if (min < this.fullMin) { max += this.fullMin - min; min = this.fullMin; }
                    if (max > this.fullMax) { min -= max - this.fullMax; max = this.fullMax; }
                }
            }
            this.min = min;
            this.max = max;
            if (this.pending) return;
            this.pending = true;
            requestAnimationFrame(() => {
                this.pending = false;
                for (const chart of this.charts()) {
                    if (!chart || chart === source) continue;
                    chart.options.scales.x.min = this.min;
                    chart.options.scales.x.max = this.max;
                    chart.update("none");
                }
                this.onChange?.();
            });
        }

        zoom(factor) {
            if (this.min === null || this.max === null) return;
            const center = (this.min + this.max) / 2;
            const half = (this.max - this.min) * factor / 2;
            this.setDomain(center - half, center + half);
        }
    }

    class ResearchChart {
        constructor({ state, rootElement = document }) {
            this.state = state;
            this.root = rootElement;
            this.charts = {};
            this.overlays = {};
            this.backtest = { orders: [], trades: [], equity: [], position: null };
            this.signals = [];
            this.hoverTimestamp = null;
            this.hoverPrice = null;
            this.hoverIndex = -1;
            this.hoverFrame = 0;
            this.priceScaleLock = null;
            this.maxDrawdown = null;
            this.viewport = new TimeViewport(() => PANEL_IDS.map(id => this.charts[id]).filter(Boolean),
                () => this.drawOverlays());
            this.registerChartTypes();
            this.createCharts();
            this.bindChartEvents();
            this.resizeObserver = new ResizeObserver(() => {
                for (const chart of Object.values(this.charts)) chart.resize();
                this.sizeOverlays();
                this.drawOverlays();
            });
            for (const panel of PANEL_IDS) {
                const element = this.root.getElementById(`panel-${panel}`);
                if (element) this.resizeObserver.observe(element);
            }
            this.bindControls();
            root.researchChart = {
                setBacktestResult: result => this.setBacktestResult(result),
                setSignals: signals => this.setSignals(signals),
                clearBacktest: () => this.setBacktestResult(null),
                state: this.state,
            };
        }

        registerChartTypes() {
            if (root.ChartZoom) Chart.register(root.ChartZoom);
            if (root.CandlestickController && root.CandlestickElement) {
                Chart.register(root.CandlestickController, root.CandlestickElement);
            }
        }

        createCharts() {
            this.makeChart("price", "price-chart", "candlestick", true);
            this.makeChart("equity", "equity-chart", "line", false);
            this.makeChart("drawdown", "drawdown-chart", "line", false);
        }

        makeChart(role, canvasId, type, showXAxis) {
            const canvas = this.root.getElementById(canvasId);
            const overlay = this.root.getElementById(`${canvasId}-overlay`);
            this.overlays[role] = overlay;
            const zoomOptions = role === "price" ? {
                pan: { enabled: true, mode: "x" },
                zoom: {
                    mode: "x", wheel: { enabled: true, speed: 0.08 }, pinch: { enabled: true },
                    drag: { enabled: true, threshold: 5, modifierKey: "shift" },
                },
                onPan: ({ chart }) => this.captureViewport(chart),
                onZoom: ({ chart }) => this.captureViewport(chart),
                limits: { x: { min: "original", max: "original", minRange: 60000 } },
            } : { zoom: { pan: { enabled: false }, zoom: { wheel: { enabled: false } } } };
            const chart = new Chart(canvas, {
                type,
                data: { datasets: [] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    parsing: false,
                    normalized: role !== "price",
                    resizeDelay: 40,
                    onHover: (event, active, instance) => this.onChartHover(role, event, instance),
                    onClick: (event, active, instance) => this.onChartClick(role, event, instance),
                    interaction: { mode: "nearest", intersect: false },
                plugins: {
                        legend: { display: false },
                        tooltip: { enabled: false },
                        zoom: zoomOptions,
                    },
                    scales: {
                        x: {
                            type: "time", min: undefined, max: undefined,
                            time: { displayFormats: { minute: "HH:mm", hour: "MMM d HH:mm", day: "MMM d", month: "MMM yyyy" } },
                            ticks: { display: showXAxis, color: COLORS.text, maxTicksLimit: 11, maxRotation: 0, font: { size: 10 } },
                            grid: { color: COLORS.grid, drawTicks: false },
                            border: { display: false },
                        },
                        y: {
                            ticks: { color: COLORS.text, maxTicksLimit: role === "price" ? 8 : 4,
                                callback: value => NUM_FMT.format(value), font: { size: 10 } },
                            grid: { color: COLORS.grid, drawTicks: false },
                            border: { display: false },
                        },
                    },
                },
                plugins: role === "price" ? [{
                    id: "researchTradeLines",
                    afterDatasetsDraw: instance => this.drawTradeLines(instance),
                }] : [],
            });
            chart.$researchRole = role;
            this.charts[role] = chart;
        }

        bindChartEvents() {
            for (const [role, chart] of Object.entries(this.charts)) {
                chart.canvas.addEventListener("dblclick", event => {
                    if (role !== "price" || this.hoverTimestamp === null) return;
                    event.preventDefault();
                    const freeze = this.state.toggleFrozen(this.hoverTimestamp);
                    this.root.getElementById("freeze-indicator").textContent = freeze === null ? "LIVE CROSSHAIR" : "FROZEN POINT";
                    this.root.getElementById("selection-status").textContent = freeze === null
                        ? "Frozen point released" : `Frozen at ${new Date(freeze).toLocaleString()}`;
                    this.updateSelectionDecoration();
                });
            }
        }

        bindControls() {
            for (const button of this.root.querySelectorAll("[data-timeframe]")) {
                button.addEventListener("click", () => this.onTimeframe?.(button.dataset.timeframe));
            }
            this.root.getElementById("chart-type").addEventListener("change", event => {
                this.state.chartType = event.target.value;
                this.updateData();
            });
            this.root.getElementById("zoom-in").addEventListener("click", () => this.viewport.zoom(0.8));
            this.root.getElementById("zoom-out").addEventListener("click", () => this.viewport.zoom(1.25));
            this.root.getElementById("reset-view").addEventListener("click", () => this.onReset?.());
            this.root.getElementById("fit-data").addEventListener("click", () => this.onFit?.());
            this.root.getElementById("toggle-crosshair").addEventListener("change", event => {
                this.state.crosshair = event.target.checked;
                this.drawOverlays();
            });
            this.root.getElementById("toggle-grid").addEventListener("change", event => {
                this.state.grid = event.target.checked;
                for (const chart of Object.values(this.charts)) {
                    chart.options.scales.x.grid.display = this.state.grid;
                    chart.options.scales.y.grid.display = this.state.grid;
                    chart.update("none");
                }
            });
            this.root.getElementById("toggle-auto-scale").addEventListener("change", event => {
                this.state.autoScale = event.target.checked;
                const y = this.charts.price.scales.y;
                this.priceScaleLock = this.state.autoScale ? null : { min: y.min, max: y.max };
                this.updateData();
            });
            this.root.getElementById("toggle-equity").addEventListener("change", event => this.togglePanel("equity", event.target.checked));
            this.root.getElementById("toggle-drawdown").addEventListener("change", event => this.togglePanel("drawdown", event.target.checked));
            this.root.getElementById("close-inspector").addEventListener("click", () => {
                this.root.getElementById("trade-inspector").hidden = true;
            });
            this.root.getElementById("toolbar-menu").addEventListener("click", event => {
                const menu = this.root.querySelector(".toolbar-overflow");
                const expanded = event.currentTarget.getAttribute("aria-expanded") === "true";
                event.currentTarget.setAttribute("aria-expanded", String(!expanded));
                menu.classList.toggle("menu-open", !expanded);
            });
        }

        togglePanel(name, visible) {
            this.state.panels[name] = visible;
            this.root.getElementById(`panel-${name}`).hidden = !visible;
            requestAnimationFrame(() => {
                this.charts[name].resize();
                this.sizeOverlays();
                this.drawOverlays();
            });
        }

        setTimeframeButtons(value) {
            for (const button of this.root.querySelectorAll("[data-timeframe]")) {
                const active = button.dataset.timeframe === value;
                button.classList.toggle("active", active);
                button.setAttribute("aria-pressed", String(active));
            }
        }

        setData(data, { resetDomain = true, anchor = this.state.anchorTimestamp } = {}) {
            this.state.data = data || [];
            if (!this.state.data.length) {
                for (const chart of Object.values(this.charts)) {
                    chart.data.datasets = chart.data.datasets.map(dataset => ({ ...dataset, data: [] }));
                    chart.update("none");
                }
                return;
            }
            const times = this.state.data.map(row => Data.timestamp(row.timestamp));
            const edgePadding = times[0] === times[times.length - 1] ? this.timeStep() / 2 : 0;
            this.viewport.setBounds(times[0] - edgePadding, times[times.length - 1] + edgePadding);
            this.viewport.setMinimumRange(this.timeStep());
            this.charts.price.options.plugins.zoom.limits.x.min = times[0] - edgePadding;
            this.charts.price.options.plugins.zoom.limits.x.max = times[times.length - 1] + edgePadding;
            this.charts.price.options.plugins.zoom.limits.x.minRange = this.timeStep();
            this.hoverIndex = anchor == null ? this.state.data.length - 1 : Data.nearestIndex(this.state.data, anchor);
            this.hoverTimestamp = null;
            this.updateData();
            this.updateOhlcPanel();
            if (resetDomain) this.fitVisibleCount(anchor);
        }

        fitVisibleCount(anchor = null) {
            const data = this.state.data;
            if (!data.length) return;
            const count = Math.max(1, Math.min(this.state.visibleCount, data.length));
            const index = anchor == null ? data.length - 1 : Data.nearestIndex(data, anchor);
            const first = Math.max(0, Math.min(index - Math.floor(count / 2), data.length - count));
            const min = Data.timestamp(data[first].timestamp);
            const max = Data.timestamp(data[first + count - 1].timestamp);
            const step = this.timeStep();
            if (min === max) this.viewport.setDomain(min - step / 2, max + step / 2);
            else this.viewport.setDomain(min, max);
        }

        centerTimestamp() {
            if (this.viewport.min !== null && this.viewport.max !== null) {
                return Math.round((this.viewport.min + this.viewport.max) / 2);
            }
            return this.state.data.length ? Data.timestamp(this.state.data.at(-1).timestamp) : null;
        }

        timeStep() {
            return ({ M1: 60000, M5: 300000, M15: 900000, H1: 3600000, H4: 14400000, D1: 86400000 })[this.state.timeframe];
        }

        updateData() {
            const data = this.state.data;
            const candleData = data.map(row => ({ x: Data.timestamp(row.timestamp), o: +row.open, h: +row.high, l: +row.low, c: +row.close }));
            const closeData = data.map(row => ({ x: Data.timestamp(row.timestamp), y: +row.close }));
            const priceData = this.state.chartType === "candlestick" ? candleData : closeData;
            const base = {
                label: this.state.chartType === "candlestick" ? "OHLC" : "Close",
                type: this.state.chartType,
                data: priceData,
                borderWidth: 1,
                color: { up: COLORS.green, down: COLORS.red, unchanged: "#8494a6" },
                borderColor: { up: COLORS.green, down: COLORS.red, unchanged: "#8494a6" },
                backgroundColor: { up: COLORS.green, down: COLORS.red, unchanged: "#8494a6" },
                pointRadius: 0, pointHoverRadius: 0, tension: 0,
            };
            const priceSets = [base];
            const tradeByFill = new Map();
            for (const trade of this.backtest.trades) {
                tradeByFill.set(`${trade.entryTime}:${trade.side}:open`, trade.id);
                tradeByFill.set(`${trade.exitTime}:${trade.side}:close`, trade.id);
            }
            const orders = this.backtest.orders.map(order => ({
                x: order.timestamp, y: order.price, kind: "order", orderId: order.id,
                tradeId: order.tradeId ?? tradeByFill.get(`${order.timestamp}:${order.side}:${order.action}`) ?? null,
                label: order.label, marketAction: order.marketAction, quantity: order.quantity,
                action: order.action, side: order.side,
                pointStyle: "triangle", rotation: order.marketAction === "BUY" ? 0 : 180,
                radius: 5, backgroundColor: order.marketAction === "BUY" ? COLORS.green : COLORS.red,
                borderColor: "#091018", borderWidth: 1,
            }));
            if (orders.length) priceSets.push({
                type: "scatter", label: "Orders", data: orders,
                pointStyle: context => context.raw.pointStyle,
                rotation: context => context.raw.rotation,
                pointRadius: context => context.raw.radius,
                backgroundColor: context => context.raw.backgroundColor,
                borderColor: context => context.raw.borderColor,
                borderWidth: context => context.raw.borderWidth,
                clip: false,
            });
            if (this.signals.length) priceSets.push({
                type: "scatter", label: "Strategy signals", data: this.signals,
                pointStyle: context => context.raw.pointStyle || "rectRot",
                pointRadius: 5, backgroundColor: context => context.raw.color || COLORS.violet,
                borderColor: "#091018", clip: false,
            });
            this.charts.price.data.datasets = priceSets;
            this.charts.price.options.scales.x.time.unit = this.timeUnit();
            this.charts.price.options.scales.y.min = this.priceScaleLock?.min;
            this.charts.price.options.scales.y.max = this.priceScaleLock?.max;
            this.charts.price.update("none");

            this.updateBacktestCharts();
            this.drawOverlays();
        }

        timeUnit() {
            return ({ M1: "minute", M5: "minute", M15: "minute", H1: "hour", H4: "hour", D1: "day" })[this.state.timeframe];
        }

        updateBacktestCharts() {
            const equity = this.backtest.equity;
            const series = [
                ["equity", "Equity", "equity", "#e5edf5"],
                ["cash", "Cash", "cash", "#54b8d4"],
                ["realizedPnl", "Realized PnL", "realizedPnl", COLORS.green],
                ["unrealizedPnl", "Unrealized PnL", "unrealizedPnl", COLORS.violet],
            ];
            this.charts.equity.data.datasets = series.filter(([, , key]) => equity.some(point => point[key] !== null))
                .map(([label, , key, color]) => ({ label, data: equity.map(point => ({ x: point.x, y: point[key] })),
                    borderColor: color, borderWidth: label === "Equity" ? 1.8 : 1.1,
                    pointRadius: 0, tension: 0, spanGaps: false }));
            this.charts.equity.options.scales.x.time.unit = this.timeUnit();
            this.charts.equity.update("none");

            const drawdownData = equity.filter(point => point.drawdown !== null)
                .map(point => ({ x: point.x, y: point.drawdown }));
            this.maxDrawdown = Data.maximumDrawdown(drawdownData);
            this.charts.drawdown.data.datasets = drawdownData.length ? [{
                label: "Drawdown (%)", data: drawdownData,
                borderColor: COLORS.red, backgroundColor: "rgba(240, 100, 103, .14)",
                borderWidth: 1.4, pointRadius: 0, fill: "origin", tension: 0,
            }] : [];
            this.charts.drawdown.options.scales.x.time.unit = this.timeUnit();
            this.charts.drawdown.options.scales.y.beginAtZero = true;
            this.charts.drawdown.update("none");
            const equitySeries = this.charts.equity.data.datasets.map(dataset => dataset.label).join(" · ");
            this.root.getElementById("equity-caption").textContent = equity.length
                ? `${equity.length.toLocaleString()} points · ${equitySeries}` : "No result loaded";
            this.root.getElementById("drawdown-caption").textContent = drawdownData.length
                ? `Current ${this.formatPercent(this.valueAt(drawdownData, this.hoverTimestamp)?.y)} · Max ${this.formatPercent(this.maxDrawdown?.y)}`
                : "No drawdown data supplied";
        }

        setBacktestResult(result) {
            this.backtest = result ? Data.transformBacktest(result) : { orders: [], trades: [], equity: [], position: null };
            if (result) {
                this.state.panels.equity = true;
                this.root.getElementById("toggle-equity").checked = true;
                this.root.getElementById("panel-equity").hidden = false;
                if (this.backtest.equity.some(point => point.drawdown !== null)) {
                    this.state.panels.drawdown = true;
                    this.root.getElementById("toggle-drawdown").checked = true;
                    this.root.getElementById("panel-drawdown").hidden = false;
                }
            }
            this.updateData();
            this.root.getElementById("trade-inspector").hidden = true;
            this.resizeVisibleCharts();
        }

        setSignals(signals) {
            this.signals = (signals || []).map((signal, index) => ({
                x: Data.timestamp(signal.timestamp ?? signal.time),
                y: Number(signal.price),
                kind: "signal",
                label: signal.label ?? signal.type ?? "Signal",
                direction: signal.direction ?? null,
                pointStyle: signal.pointStyle ?? (signal.direction === "exit" ? "crossRot" : "rectRot"),
                color: signal.color ?? COLORS.violet,
                signalId: signal.id ?? index + 1,
                source: signal,
            })).filter(signal => signal.x !== null && Number.isFinite(signal.y));
            this.updateData();
        }

        resizeVisibleCharts() {
            requestAnimationFrame(() => {
                for (const name of PANEL_IDS) if (!this.root.getElementById(`panel-${name}`).hidden) this.charts[name].resize();
                this.sizeOverlays();
                this.drawOverlays();
            });
        }

        captureViewport(chart) {
            const scale = chart.scales.x;
            this.viewport.setDomain(scale.min, scale.max, chart);
        }

        onChartHover(role, event, chart) {
            if (!event || event.type === "mouseout" || event.type === "touchend") {
                if (role === "price") {
                    this.hoverTimestamp = null;
                    this.hoverPrice = null;
                    this.hoverIndex = -1;
                    this.queueHoverRender();
                }
                return;
            }
            const x = event.x;
            if (!Number.isFinite(x) || x < chart.chartArea.left || x > chart.chartArea.right) return;
            const value = chart.scales.x.getValueForPixel(x);
            if (!Number.isFinite(value)) return;
            const index = Data.nearestIndex(this.state.data, value);
            if (index < 0) return;
            this.hoverTimestamp = Data.timestamp(this.state.data[index].timestamp);
            this.hoverPrice = role === "price" ? chart.scales.y.getValueForPixel(event.y) : null;
            this.hoverIndex = index;
            this.queueHoverRender();
        }

        queueHoverRender() {
            if (this.hoverFrame) return;
            this.hoverFrame = requestAnimationFrame(() => {
                this.hoverFrame = 0;
                this.updateOhlcPanel();
                this.updateResearchReadouts();
                this.drawOverlays();
            });
        }

        updateOhlcPanel() {
            const anchor = this.state.frozenTimestamp ?? this.state.selectedTimestamp;
            const index = this.hoverIndex >= 0 ? this.hoverIndex
                : anchor === null ? this.state.data.length - 1 : Data.nearestIndex(this.state.data, anchor);
            const bar = this.state.data[index];
            if (!bar) return;
            const previous = this.state.data[index - 1];
            const change = previous ? Number(bar.close) - Number(previous.close) : null;
            const changePct = previous && Number(previous.close) !== 0 ? change / Number(previous.close) * 100 : null;
            const values = {
                open: bar.open, high: bar.high, low: bar.low, close: bar.close,
                change, changePct, spread: bar.spread,
            };
            for (const [field, value] of Object.entries(values)) {
                const node = this.root.querySelector(`[data-field="${field}"]`);
                node.textContent = value === null ? "—" : field === "changePct" ? `${value >= 0 ? "+" : ""}${value.toFixed(3)}%`
                    : field === "change" ? `${value >= 0 ? "+" : ""}${NUM_FMT.format(value)}`
                    : NUM_FMT.format(value);
                if (field === "change" || field === "changePct") node.classList.toggle("negative", value < 0);
                if (field === "change" || field === "changePct") node.classList.toggle("positive", value >= 0);
            }
            const pointTime = Data.timestamp(bar.timestamp);
            this.root.getElementById("crosshair-time").textContent = TIME_FMT.format(pointTime);
        }

        updateResearchReadouts() {
            const drawdown = this.valueAt(this.backtest.equity.filter(point => point.drawdown !== null)
                .map(point => ({ x: point.x, y: point.drawdown })), this.hoverTimestamp);
            if (this.maxDrawdown) this.root.getElementById("drawdown-caption").textContent =
                `Current ${this.formatPercent(drawdown?.y)} · Max ${this.formatPercent(this.maxDrawdown.y)}`;
        }

        valueAt(points, time) {
            if (!points.length || time === null) return null;
            return points[Data.nearestIndex(points, time, point => point.x)];
        }

        formatPercent(value) { return value === undefined || value === null ? "—" : `${Number(value).toFixed(2)}%`; }

        onChartClick(role, event, chart) {
            const native = event.native || event;
            if (role !== "price") return;
            const active = chart.getElementsAtEventForMode(native, "nearest", { intersect: true }, false);
            if (active.length) {
                const raw = chart.data.datasets[active[0].datasetIndex]?.data[active[0].index];
                if (raw?.kind === "order") {
                    const trade = this.backtest.trades.find(item => item.id === raw.tradeId);
                    if (trade) this.showTrade(trade);
                    else this.showOrder(raw);
                    return;
                }
                if (raw?.kind === "signal") return;
            }
            const time = chart.scales.x.getValueForPixel(event.x);
            const index = Data.nearestIndex(this.state.data, time);
            if (index < 0) return;
            this.state.selectCandle(this.state.data, index);
            this.root.getElementById("selection-status").textContent = `Selected ${new Date(this.state.selectedTimestamp).toLocaleString()}`;
            this.drawOverlays();
        }

        showTrade(trade) {
            const details = [
                ["Trade ID", trade.id], ["Direction", trade.direction],
                ["Entry timestamp", this.formatDate(trade.entryTime)], ["Entry price", NUM_FMT.format(trade.entryPrice)],
                ["Exit timestamp", this.formatDate(trade.exitTime)], ["Exit price", NUM_FMT.format(trade.exitPrice)],
                ["Quantity", NUM_FMT.format(trade.quantity)], ["Gross PnL", NUM_FMT.format(trade.grossPnl)],
                ["Commission", NUM_FMT.format(trade.commission)], ["Spread cost", NUM_FMT.format(trade.spreadCost)],
                ["Slippage", NUM_FMT.format(trade.slippage)], ["Net PnL", NUM_FMT.format(trade.netPnl)],
                ["Return", this.formatPercent(trade.returnPct)], ["Duration", this.formatDuration(trade.durationMs)],
            ];
            this.root.getElementById("trade-title").textContent = `Trade ${trade.id}`;
            this.renderInspector(details, trade.winning ? "positive" : "negative");
        }

        showOrder(order) {
            this.root.getElementById("trade-title").textContent = order.label;
            this.renderInspector([
                ["Timestamp", this.formatDate(order.x)], ["Market action", order.marketAction],
                ["Execution price", NUM_FMT.format(order.y)], ["Quantity", NUM_FMT.format(order.quantity)],
                ["Order type", order.action.toUpperCase()],
            ], "");
        }

        renderInspector(details, pnlClass) {
            const list = this.root.getElementById("trade-details");
            list.replaceChildren();
            for (const [label, value] of details) {
                const term = document.createElement("dt"); term.textContent = label;
                const description = document.createElement("dd"); description.textContent = value ?? "—";
                if (label === "Net PnL") description.classList.add(pnlClass);
                list.append(term, description);
            }
            this.root.getElementById("trade-inspector").hidden = false;
        }

        formatDate(value) { return value === null || value === undefined ? "—" : new Date(value).toISOString().replace(".000Z", "Z"); }
        formatDuration(value) {
            if (value === null || value === undefined) return "—";
            const mins = Math.floor(value / 60000), days = Math.floor(mins / 1440), hours = Math.floor((mins % 1440) / 60);
            return days ? `${days}d ${hours}h` : hours ? `${hours}h ${mins % 60}m` : `${mins}m`;
        }

        updateSelectionDecoration() { this.drawOverlays(); }

        sizeOverlays() {
            for (const [role, overlay] of Object.entries(this.overlays)) {
                if (!overlay) continue;
                const rect = overlay.getBoundingClientRect();
                const ratio = window.devicePixelRatio || 1;
                const width = Math.round(rect.width * ratio), height = Math.round(rect.height * ratio);
                if (overlay.width !== width || overlay.height !== height) {
                    overlay.width = width; overlay.height = height;
                }
                const context = overlay.getContext("2d");
                context.setTransform(ratio, 0, 0, ratio, 0, 0);
                overlay.$role = role;
            }
        }

        drawOverlays() {
            this.sizeOverlays();
            for (const role of PANEL_IDS) {
                const overlay = this.overlays[role], chart = this.charts[role];
                if (!overlay || !chart || this.root.getElementById(`panel-${role}`).hidden) continue;
                const ctx = overlay.getContext("2d");
                const rect = overlay.getBoundingClientRect();
                ctx.clearRect(0, 0, rect.width, rect.height);
                const area = chart.chartArea;
                if (!area) continue;
                const timestamp = this.hoverTimestamp ?? this.state.frozenTimestamp ?? this.state.selectedTimestamp;
                if (timestamp !== null && (role !== "price" || this.state.crosshair)) {
                    const x = chart.scales.x.getPixelForValue(timestamp);
                    if (x >= area.left && x <= area.right) {
                        ctx.save(); ctx.beginPath(); ctx.moveTo(x, area.top); ctx.lineTo(x, area.bottom);
                        ctx.strokeStyle = this.state.frozenTimestamp === timestamp ? "rgba(239, 246, 255, .78)" : "rgba(167, 190, 210, .55)";
                        ctx.lineWidth = 1; ctx.setLineDash([3, 4]); ctx.stroke(); ctx.restore();
                        this.drawEquityHighlight(role, chart, ctx, x, area);
                    }
                }
                if (role === "price") this.drawPriceOverlay(chart, ctx, area);
                if (role === "drawdown" && this.maxDrawdown) this.drawMaximumDrawdown(chart, ctx, area);
            }
        }

        drawEquityHighlight(role, chart, ctx, x, area) {
            if (role !== "equity" || !this.backtest.equity.length) return;
            const point = this.valueAt(this.backtest.equity.filter(item => item.equity !== null)
                .map(item => ({ x: item.x, y: item.equity })), this.hoverTimestamp ?? this.state.anchorTimestamp);
            if (!point) return;
            const y = chart.scales.y.getPixelForValue(point.y);
            ctx.save(); ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2);
            ctx.fillStyle = "#e5edf5"; ctx.fill(); ctx.restore();
        }

        drawPriceOverlay(chart, ctx, area) {
            const activeTimestamp = this.state.frozenTimestamp ?? this.state.selectedTimestamp;
            if (this.state.crosshair && this.hoverTimestamp !== null && this.hoverPrice !== null) {
                const x = chart.scales.x.getPixelForValue(this.hoverTimestamp);
                const y = chart.scales.y.getPixelForValue(this.hoverPrice);
                if (x >= area.left && x <= area.right && y >= area.top && y <= area.bottom) {
                    ctx.save(); ctx.beginPath(); ctx.moveTo(area.left, y); ctx.lineTo(area.right, y);
                    ctx.strokeStyle = "rgba(167, 190, 210, .42)"; ctx.lineWidth = 1; ctx.setLineDash([3, 4]); ctx.stroke();
                    ctx.fillStyle = "#111b25"; ctx.fillRect(area.right - 72, y - 9, 72, 18);
                    ctx.fillStyle = "#d8e2eb"; ctx.font = "10px ui-monospace, monospace"; ctx.textAlign = "center";
                    ctx.fillText(NUM_FMT.format(this.hoverPrice), area.right - 36, y + 3); ctx.restore();
                }
            }
            const position = this.backtest.position;
            if (position && position.entryTime !== null) {
                const x1 = chart.scales.x.getPixelForValue(position.entryTime);
                const y = chart.scales.y.getPixelForValue(position.entryPrice);
                if (x1 <= area.right && y >= area.top && y <= area.bottom) {
                    ctx.save(); ctx.beginPath(); ctx.moveTo(Math.max(area.left, x1), y); ctx.lineTo(area.right, y);
                    ctx.strokeStyle = position.side === "long" ? COLORS.green : COLORS.red;
                    ctx.lineWidth = 1; ctx.setLineDash([7, 4]); ctx.stroke();
                    const lastTime = this.backtest.equity.at(-1)?.x ?? this.state.data.at(-1)?.timestamp;
                    const durationEnd = this.hoverTimestamp ?? (lastTime === undefined ? null : Data.timestamp(lastTime));
                    const duration = durationEnd === null ? null : Math.max(0, durationEnd - position.entryTime);
                    ctx.setLineDash([]); ctx.fillStyle = position.side === "long" ? COLORS.green : COLORS.red;
                    ctx.font = "10px ui-monospace, monospace";
                    ctx.fillText(`${position.side.toUpperCase()} · ${this.formatDuration(duration)}`, Math.max(area.left + 4, x1 + 5), y - 5);
                    ctx.restore();
                }
            }
            if (activeTimestamp !== null && this.state.data.length) {
                const index = Data.nearestIndex(this.state.data, activeTimestamp);
                const bar = this.state.data[index];
                const x = chart.scales.x.getPixelForValue(Data.timestamp(bar.timestamp));
                const y = chart.scales.y.getPixelForValue(Number(bar.close));
                if (x >= area.left && x <= area.right && y >= area.top && y <= area.bottom) {
                    ctx.save(); ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2); ctx.fillStyle = "#eef4f8"; ctx.fill(); ctx.restore();
                }
            }
        }

        drawTradeLines(chart) {
            if ((!this.backtest.trades.length && !this.backtest.orders.length) || !chart.chartArea) return;
            const { ctx, chartArea: area, scales } = chart;
            const start = scales.x.min, end = scales.x.max;
            ctx.save();
            const trades = this.backtest.trades;
            let tradeIndex = lowerBound(trades, start, trade => trade.exitTime);
            for (; tradeIndex < trades.length && trades[tradeIndex].entryTime <= end; tradeIndex++) {
                const trade = trades[tradeIndex];
                if (trade.exitTime < start || trade.entryTime > end) continue;
                const x1 = scales.x.getPixelForValue(trade.entryTime), x2 = scales.x.getPixelForValue(trade.exitTime);
                const y1 = scales.y.getPixelForValue(trade.entryPrice), y2 = scales.y.getPixelForValue(trade.exitPrice);
                if (x2 < area.left || x1 > area.right) continue;
                ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
                ctx.strokeStyle = trade.winning ? "rgba(53, 201, 139, .72)" : "rgba(240, 100, 103, .72)";
                ctx.lineWidth = 1.35; ctx.stroke();
            }
            const allOrders = this.backtest.orders;
            let orderIndex = lowerBound(allOrders, start, order => order.timestamp);
            const visibleOrders = [];
            for (; orderIndex < allOrders.length && allOrders[orderIndex].timestamp <= end; orderIndex++) {
                visibleOrders.push(allOrders[orderIndex]);
            }
            if (visibleOrders.length && area.width / visibleOrders.length >= 100) {
                ctx.font = "9px ui-monospace, monospace";
                for (const order of visibleOrders) {
                    const x = scales.x.getPixelForValue(order.timestamp);
                    const y = scales.y.getPixelForValue(order.price);
                    ctx.fillStyle = order.marketAction === "BUY" ? COLORS.green : COLORS.red;
                    ctx.fillText(`${order.marketAction} · ${order.label}`, x + 7, y - 6);
                }
            }
            ctx.restore();
        }

        drawMaximumDrawdown(chart, ctx, area) {
            const x = chart.scales.x.getPixelForValue(this.maxDrawdown.x);
            const y = chart.scales.y.getPixelForValue(this.maxDrawdown.y);
            if (x < area.left || x > area.right || y < area.top || y > area.bottom) return;
            ctx.save(); ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fillStyle = COLORS.red; ctx.fill(); ctx.restore();
        }
    }

    root.ResearchChart = ResearchChart;
    root.TimeViewport = TimeViewport;
})(window);
