(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const form = $("research-form"), state = { ids: [], sensitivityIds: [], sensitivityParameters: [], correlationIds: [], baselineId: null, monteCarloId: null, chart: null, advancedChart: null, researchBusy: false, advancedBusy: false, results: [], selectedExperimentId: null, latestTest: null };
  let strategySchema = [];
  const fmt = value => value == null ? "—" : new Intl.NumberFormat(undefined,{maximumFractionDigits:4}).format(value);
  async function request(path, body) {
    const response = await fetch("/api/research" + path, body ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)} : {});
    const data = await response.json();
    if (!response.ok) {
      const detail=data.detail;
      const message=Array.isArray(detail)?detail.map(item=>`${(item.loc||[]).join(".")}: ${item.msg}`).join("; "):
        typeof detail==="string"?detail:detail?JSON.stringify(detail):"Request failed ("+response.status+")";
      throw new Error(message);
    }
    return data;
  }
  function status(message, error=false) { $("api-status").textContent=message; $("api-status").className=error?"status-error":"status-ok"; }
  function error(message) { $("error").textContent=message; $("error").hidden=!message; }
  function syncAdvancedButtons() {
    const busy=state.researchBusy||state.advancedBusy;
    $("run-sensitivity").disabled=busy||state.sensitivityIds.length<2||!state.sensitivityParameters.length;
    $("run-correlation").disabled=busy||state.correlationIds.length<2;
    $("run-portfolio").disabled=busy||state.correlationIds.length<2;
    $("run-robustness").disabled=busy||!state.baselineId;
    $("run-regime").disabled=busy||!state.baselineId;
    $("run-mc").disabled=busy||!state.monteCarloId;
  }
  function updateMode() {
    const mode=$("mode").value;
    $("space-parameters").hidden=false;
    $("parameter-empty").hidden=strategySchema.length>0;
    $("search-config").hidden=false; $("split-config").hidden=mode!=="oos"; $("walk-config").hidden=mode!=="walk_forward";
    $("run-button").textContent={batch:"Run parameter batch",oos:"Select on train → evaluate OOS",walk_forward:"Run walk-forward"}[mode];
  }
  function valueControl(definition, value, role) {
    const input=document.createElement(definition.kind==="choice"?"select":"input");
    input.dataset.role=role;
    if(definition.kind==="choice"){
      for(const choice of definition.choices||[]){const option=document.createElement("option");option.value=String(choice);option.textContent=String(choice);input.append(option);}
      input.value=String(value ?? definition.choices?.[0] ?? "");
    }else if(definition.kind==="boolean"){
      for(const choice of [true,false]){const option=document.createElement("option");option.value=String(choice);option.textContent=String(choice);input.append(option);}
      input.value=String(value ?? false);
    }else{
      input.type=definition.kind==="integer"||definition.kind==="float"?"number":"text";
      if(definition.kind==="integer"){input.step=String(definition.step||1);if(definition.minimum!=null)input.min=String(definition.minimum);if(definition.maximum!=null)input.max=String(definition.maximum);}
      if(definition.kind==="float"){input.step="any";if(definition.minimum!=null)input.min=String(definition.minimum);if(definition.maximum!=null)input.max=String(definition.maximum);}
      input.value=value ?? definition.default ?? "";
    }
    return input;
  }
  function renderStrategyParameters(strategy, fixedValues={}) {
    strategySchema=Array.isArray(strategy?.parameters)?strategy.parameters:[];
    const batch=$("space-parameters");
    batch.replaceChildren();
    $("parameter-empty").hidden=strategySchema.length>0;
    for(const definition of strategySchema){
      const label=definition.label||definition.name.replaceAll("_"," ");
      const group=document.createElement("fieldset");group.className="strategy-parameter";group.dataset.parameter=definition.name;
      const legend=document.createElement("legend");legend.textContent=label;group.append(legend);
      const fixedLabel=document.createElement("label");fixedLabel.textContent="Fixed value";
      const fixedValue=Object.hasOwn(fixedValues,definition.name)?fixedValues[definition.name]:definition.default;
      fixedLabel.append(valueControl(definition,fixedValue,"value"));group.append(fixedLabel);
      const optimizeLabel=document.createElement("label");optimizeLabel.className="parameter-vary";
      const optimize=document.createElement("input");optimize.type="checkbox";optimize.dataset.role="optimize";
      optimizeLabel.append(optimize,document.createTextNode(" Vary in research"));group.append(optimizeLabel);
      if(definition.kind==="integer"||definition.kind==="float"){
        const range=document.createElement("div");range.className="range parameter-range";range.hidden=true;range.dataset.role="range";
        for(const [key,title,initial] of [["minimum","Minimum",definition.minimum??definition.default],["maximum","Maximum",definition.maximum??definition.default],["step","Step",definition.step??(definition.kind==="integer"?1:0.1)]]){
          const controlLabel=document.createElement("label");controlLabel.textContent=title;
          const control=document.createElement("input");control.type="number";control.step=key==="step"&&definition.kind==="float"?"any":String(definition.step||1);control.dataset.role=key;control.value=String(initial);
          if(key!=="step"&&definition.minimum!=null)control.min=String(definition.minimum);
          if(key!=="step"&&definition.maximum!=null)control.max=String(definition.maximum);
          controlLabel.append(control);range.append(controlLabel);
        }
        group.append(range);
      }else if(definition.kind==="choice"){
        const choices=document.createElement("div");choices.hidden=true;choices.dataset.role="choices";
        for(const choice of definition.choices||[]){const choiceLabel=document.createElement("label");const checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.value=String(choice);checkbox.dataset.role="choice";choiceLabel.append(checkbox,document.createTextNode(` ${choice}`));choices.append(choiceLabel);}
        group.append(choices);
      }else{optimize.disabled=true;}
      optimize.addEventListener("change",()=>{const range=group.querySelector('[data-role="range"]'),choices=group.querySelector('[data-role="choices"]');if(range)range.hidden=!optimize.checked;if(choices)choices.hidden=!optimize.checked;});
      batch.append(group);
    }
    updateMode();
  }
  function readParameterValues(container, searching) {
    const values={};
    for(const definition of strategySchema){
      const group=[...container.querySelectorAll("[data-parameter]")].find(item=>item.dataset.parameter===definition.name);
      if(!group)throw new Error(`Missing controls for strategy parameter ${definition.name}`);
      const owner=group;
      const control=owner?.querySelector('[data-role="value"]');
      const raw={value:control.value};
      if(searching){
        const vary=owner.querySelector('[data-role="optimize"]');raw.optimize=!!vary?.checked;
        if(raw.optimize&&(definition.kind==="integer"||definition.kind==="float"))for(const key of ["minimum","maximum","step"])raw[key]=owner.querySelector(`[data-role="${key}"]`).value;
        if(raw.optimize&&definition.kind==="choice")raw.choices=[...owner.querySelectorAll('[data-role="choice"]:checked')].map(item=>item.value);
      }
      values[definition.name]=raw;
    }
    return values;
  }
  function renderSourceContext(result) {
    const definition=result?.definition;
    const available=!!(definition&&result.status==="completed");
    const previousId=state.latestTest?.definition?.experiment_id;
    state.latestTest=available?result:null;
    $("source-empty").hidden=available;
    $("source-context").hidden=!available;
    $("run-button").disabled=!available;
    if(!available){$("source-context").replaceChildren();renderStrategyParameters(null);return;}
    const config=definition.backtest_config||{},period=definition.period||{};
    const values=[
      ["Strategy",`${definition.strategy_id} · v${definition.strategy_version}`],
      ["Instrument / timeframe",`${definition.symbol} · ${definition.timeframe}`],
      ["Test period",`${period.start||"—"} → ${period.end||"—"}`],
      ["Parameters",JSON.stringify(Object.fromEntries(definition.parameters?.values||[]))],
      ["Execution assumptions",`capital ${config.initial_cash??"—"} · quantity ${config.position_size??"—"} · commission ${config.commission_per_unit??"—"}/${config.commission_rate??"—"} · spread ${config.spread_scale??"—"} · slippage ${config.slippage??"—"}`],
    ];
    const target=$("source-context");target.replaceChildren(...values.flatMap(([label,value])=>{
      const term=document.createElement("dt"),description=document.createElement("dd");term.textContent=label;description.textContent=value;return [term,description];
    }));
    if(previousId!==definition.experiment_id){
      for(const name of ["training_start","training_end","testing_start","testing_end"])
        form.elements[name].value="";
    }
    const strategy=($("api-status")._strategies||[]).find(item=>item.id===definition.strategy_id);
    renderStrategyParameters(strategy,Object.fromEntries(definition.parameters?.values||[]));
    initializeSplit();
  }
  async function refreshLatestTest() {
    const latest=await request("/latest-test");
    renderSourceContext(latest);
    if(latest)render({results:[latest]});
    else render({results:[]});
  }
  async function loadCatalog() {
    try {
      const strategies=await request("/strategies");
      $("api-status")._strategies=Array.isArray(strategies)?strategies:[];
      await refreshLatestTest(); status(state.latestTest?"Latest Test loaded":"Run a Test on the main workspace to begin research");
    } catch (reason) { status(reason.message,true); error(reason.message); }
  }
  function initializeSplit(){const period=state.latestTest?.definition?.period;
    const start=String(period?.start||"").slice(0,16),end=String(period?.end||"").slice(0,16);
    if(!start||!end||form.elements.training_start.value)return;
    const left=Date.parse(start+"Z"),right=Date.parse(end+"Z"),cut=new Date(left+(right-left)*.7);
    const boundary=cut.toISOString().slice(0,16);
    form.elements.training_start.value=start;form.elements.training_end.value=boundary;
    form.elements.testing_start.value=boundary;form.elements.testing_end.value=end;}
  function payload() {
    const fields=new FormData(form), mode=fields.get("mode");
    const definition=state.latestTest?.definition;
    if(!definition)throw new Error("Run a successful Test on the main workspace before starting research.");
    const config=definition.backtest_config||{},analysis=definition.analysis_config||{},period=definition.period||{};
    const body={mode:mode,strategy_id:definition.strategy_id,symbol:definition.symbol,timeframe:definition.timeframe,
      start:period.start,end:period.end,
      initial_cash:config.initial_cash,position_size:config.position_size,commission_per_unit:config.commission_per_unit,
      commission_rate:config.commission_rate,spread_scale:config.spread_scale,slippage:config.slippage,
      risk_free_rate:analysis.risk_free_rate,target_return:analysis.target_return};
    if(analysis.periods_per_year!=null)body.periods_per_year=analysis.periods_per_year;
    const search=ResearchParameters.makePayload(strategySchema,readParameterValues($("space-parameters"),true),true);
    body.parameter_space=search.parameter_space;
    if(!search.parameter_space.some(item=>item.kind!=="fixed")){
      body.search_method="grid";body.count=1;
    }else{
      body.search_method=fields.get("search_method");body.count=Number(fields.get("count"));body.seed=Number(fields.get("seed"));
    }
    if(mode==="oos"||mode==="walk_forward"){body.selection_metric=fields.get("selection_metric");body.maximize=fields.get("maximize")==="true";body.top_n=Number(fields.get("top_n"));}
    if(mode==="oos") for(const key of ["training_start","training_end","testing_start","testing_end"]) body[key]=fields.get(key);
    if(mode==="walk_forward"){body.training_days=Number(fields.get("training_days"));body.testing_days=Number(fields.get("testing_days"));body.step_days=Number(fields.get("step_days"));body.anchored=fields.get("anchored")==="true";}
    return body;
  }
  function flatten(result) {
    if(result.results)return result.results;
    if(result.training&&result.testing)return [...result.training.results,...result.testing.results];
    if(result.walk_forward)return flatten(result.walk_forward);
    if(result.windows)return result.windows.flatMap(window=>[...window.result.training.results,...window.result.testing.results]);
    return [];
  }
  function renderChart(analysis) {
    const points=(analysis?.equity_curve||[]).map(item=>({x:new Date(item.timestamp),y:item.equity}));
    $("chart-empty").hidden=points.length>0;
    if(state.chart)state.chart.destroy();
    state.chart=new Chart($("equity-chart"),{type:"line",data:{datasets:[{label:"Equity",data:points,borderColor:"#58bad1",backgroundColor:"#58bad122",pointRadius:0,borderWidth:1.5,fill:true}]},
      options:{responsive:true,maintainAspectRatio:false,animation:false,parsing:false,plugins:{legend:{display:false}},
        scales:{x:{type:"time",time:{unit:"day"},ticks:{color:"#748798",maxTicksLimit:8},grid:{color:"#24313b"}},
          y:{ticks:{color:"#748798"},grid:{color:"#24313b"}}}}});
  }
  function selectExperiment(id) {
    state.selectedExperimentId=id;
    const row=state.results.find(item=>item.status==="completed"&&item.definition?.experiment_id===id)||null;
    for(const tr of $("records").rows)tr.classList.toggle("selected-result",tr.dataset.experimentId===id);
    const analysis=row?.analysis_result;
    const metrics={"Selected total return":analysis?.total_return==null?"—":`${(analysis.total_return*100).toFixed(2)}%`,
      "Net trade P&L":fmt(analysis?.trades?.net_profit),"Trades":analysis?.trades?.total_trades??"—",
      "Win rate":analysis?.trades?.win_rate==null?"—":`${(analysis.trades.win_rate*100).toFixed(2)}%`,
      "Profit factor":fmt(analysis?.trades?.profit_factor),"Expectancy":fmt(analysis?.trades?.expectancy),
      "Max drawdown":analysis?.drawdown?.max_drawdown_pct==null?"—":`${(analysis.drawdown.max_drawdown_pct*100).toFixed(2)}%`,
      "Period volatility":fmt(analysis?.risk?.period_volatility),"Sharpe":fmt(analysis?.risk?.sharpe_ratio),
      "Sortino":fmt(analysis?.risk?.sortino_ratio),"Calmar":fmt(analysis?.risk?.calmar_ratio)};
    for(const card of $("summary").querySelectorAll(".metric")){const label=card.querySelector("small").textContent;if(label in metrics)card.querySelector("b").textContent=metrics[label];}
    renderChart(row?.analysis_result||null);
  }
  function render(result) {
    if(state.advancedChart){state.advancedChart.destroy();state.advancedChart=null;}
    $("advanced-output").replaceChildren();$("advanced-output").hidden=true;
    const rows=flatten(result); state.results=rows; state.ids=rows.filter(row=>row.status==="completed").map(row=>row.definition.experiment_id);
    state.sensitivityIds=ResearchWorkspaceState.sensitivityGroup(rows)
      .map(row=>row.definition.experiment_id);
    state.sensitivityParameters=ResearchWorkspaceState.sensitivityParameters(
      ResearchWorkspaceState.sensitivityGroup(rows));
    state.correlationIds=ResearchWorkspaceState.alignedCorrelationGroup(rows)
      .map(row=>row.definition.experiment_id);
    $("correlation-hint").textContent=state.correlationIds.length>=2
      ? `${state.correlationIds.length} aligned ${state.correlationIds[0]&&rows.find(row=>row.definition.experiment_id===state.correlationIds[0]).definition.phase.toUpperCase()} return series`
      : "Correlation needs at least two completed results from the same phase and data period.";
    $("sensitivity-hint").textContent=state.sensitivityIds.length>=2
      ? state.sensitivityParameters.length
        ? `${state.sensitivityIds.length} comparable configurations · varying: ${state.sensitivityParameters.join(", ")}`
        : "Selected configurations do not vary any shared parameter."
      : "Sensitivity needs multiple completed parameter configurations in one training or batch period.";
    $("portfolio-hint").textContent=state.correlationIds.length>=2
      ? `${state.correlationIds.length} aligned series will receive equal weights.`
      : "Portfolio curve needs at least two completed aligned results.";
    const correlationRow=rows.find(row=>row.definition.experiment_id===state.correlationIds[0]);
    state.portfolioInitialCapital=correlationRow?.definition.backtest_config?.initial_cash;
    const oosRow=rows.find(row=>row.definition.phase==="oos"&&row.status==="completed"&&row.analysis_result);
    state.baselineId=oosRow?.definition.experiment_id||state.sensitivityIds[0]||state.ids[0]||null;
    state.monteCarloId=ResearchWorkspaceState.monteCarloBaseline(rows,state.baselineId)?.definition.experiment_id||null;
    syncAdvancedButtons();
    $("mc-hint").textContent=state.monteCarloId
      ? "Uses closed trades from the selected completed result."
      : "Trade bootstrap needs at least one completed trade and no open position.";
    const firstCompleted=rows.find(row=>row.status==="completed");
    state.selectedExperimentId=firstCompleted?.definition.experiment_id||null;
    const analysis=firstCompleted?.analysis_result;
    const walk=result.walk_forward||result;
    $("result-title").textContent=result.training?"Training and OOS evaluation":walk.windows?walk.windows.length+" walk-forward windows":"Backtest / batch results";
    const completed=rows.filter(row=>row.status==="completed").length, failed=rows.length-completed, summary=$("summary");
    summary.hidden=false;
    const cards=[["Experiments",rows.length],["Completed",completed],["Failed",failed],
      ["OOS evaluated",rows.filter(row=>row.definition.phase==="oos"&&row.status==="completed").length],
      ["Selection metric",result.selection_rule?.metric||"—"],["Selected",result.selected?.length??"—"],
      ["Selected total return",analysis?.total_return==null?"—":(analysis.total_return*100).toFixed(2)+"%"],
      ["Net trade P&L",fmt(analysis?.trades?.net_profit)],["Trades",analysis?.trades?.total_trades??"—"],
      ["Win rate",analysis?.trades?.win_rate==null?"—":(analysis.trades.win_rate*100).toFixed(2)+"%"],
      ["Profit factor",fmt(analysis?.trades?.profit_factor)],["Expectancy",fmt(analysis?.trades?.expectancy)],
      ["Max drawdown",analysis?.drawdown?.max_drawdown_pct==null?"—":(analysis.drawdown.max_drawdown_pct*100).toFixed(2)+"%"],
      ["Period volatility",fmt(analysis?.risk?.period_volatility)],["Sharpe",fmt(analysis?.risk?.sharpe_ratio)],
      ["Sortino",fmt(analysis?.risk?.sortino_ratio)],["Calmar",fmt(analysis?.risk?.calmar_ratio)],
      ...(result.aggregate?[["WF summed OOS net P&L",fmt(result.aggregate.total_net_profit)],
        ["WF completed windows",result.aggregate.completed_window_count]]:[])];
    summary.replaceChildren(...cards.map(pair=>{const box=document.createElement("div");box.className="metric";const small=document.createElement("small");small.textContent=pair[0];const value=document.createElement("b");value.textContent=pair[1];box.append(small,value);return box;}));
    const tbody=$("records");
    tbody.replaceChildren(...rows.map(row=>{const d=row.definition,a=row.analysis_result,tr=document.createElement("tr");tr.dataset.phase=d.phase;tr.dataset.experimentId=d.experiment_id;tr.tabIndex=row.status==="completed"?0:-1;
      const data=[d.phase.toUpperCase(),row.status,JSON.stringify(Object.fromEntries(d.parameters.values)),fmt(a?.total_return),fmt(a?.trades?.net_profit),fmt(a?.trades?.total_trades),d.experiment_id.slice(0,12),row.failure?(row.failure.exception_type+": "+row.failure.message):"—"];
      for(const value of data){const cell=document.createElement("td");cell.textContent=value;tr.append(cell);}
      if(row.status==="failed")tr.title=(row.failure?.exception_type||"Failure")+": "+(row.failure?.message||"");
      if(row.status==="completed"){
        tr.setAttribute("role","button");
        tr.addEventListener("click",()=>selectExperiment(d.experiment_id));
        tr.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();selectExperiment(d.experiment_id);}});
      }
      return tr;}));
    if(state.selectedExperimentId)selectExperiment(state.selectedExperimentId);else renderChart(null);
    $("advanced-tools").hidden=!state.ids.length;
  }
  form.addEventListener("submit",async event=>{event.preventDefault();if(state.advancedBusy)return;error("");const button=$("run-button");state.researchBusy=true;button.disabled=true;button.textContent="Running…";syncAdvancedButtons();$("clear-results").disabled=true;
    try{render(await request("/run",payload()));status("Research run completed");}
    catch(reason){error(reason.message);status("Research run failed",true);if(state.ids.length)$("result-title").textContent="Previous results · new run failed";}
    finally{state.researchBusy=false;button.disabled=false;$("clear-results").disabled=false;syncAdvancedButtons();updateMode();}});
  $("mode").addEventListener("change",()=>{updateMode();initializeSplit();});
  if(typeof BroadcastChannel!=="undefined"){
    const channel=new BroadcastChannel("market-research-latest-test");
    channel.addEventListener("message",()=>refreshLatestTest().catch(reason=>{status(reason.message,true);error(reason.message);}));
  }
  $("clear-results").addEventListener("click",()=>{state.ids=[];state.sensitivityIds=[];state.sensitivityParameters=[];state.correlationIds=[];state.baselineId=null;state.monteCarloId=null;state.results=[];state.selectedExperimentId=null;
    for(const id of ["run-sensitivity","run-robustness","run-correlation","run-portfolio","run-mc","run-regime"])
      $(id).disabled=true;
    $("sensitivity-hint").textContent="Sensitivity needs multiple completed parameter configurations in one training or batch period.";
    $("mc-hint").textContent="Trade bootstrap needs at least one completed trade and no open position.";
    $("correlation-hint").textContent="Correlation needs at least two completed results from the same phase and data period.";
    $("portfolio-hint").textContent="Portfolio curve needs at least two completed aligned results.";
    if(state.advancedChart){state.advancedChart.destroy();state.advancedChart=null;}
    if(state.chart){state.chart.destroy();state.chart=null;}
    $("records").replaceChildren();$("summary").hidden=true;$("advanced-tools").hidden=true;$("chart-empty").hidden=false;
    renderChart(null);
    $("result-title").textContent="No experiment loaded";$("advanced-output").hidden=true;});
  function renderAdvanced(result){
    const out=$("advanced-output"),view=ResearchPresentation.advancedView(result);
    if(state.advancedChart){state.advancedChart.destroy();state.advancedChart=null;}
    out.replaceChildren();out.hidden=false;
    const heading=document.createElement("div");heading.className="advanced-heading";
    const title=document.createElement("h3");title.textContent=view.title;heading.append(title);
    const summary=document.createElement("div");summary.className="advanced-summary";
    for(const item of view.summary){
      const card=document.createElement("div");card.className="advanced-metric";
      const label=document.createElement("small");label.textContent=item.label;
      const value=document.createElement("b");value.textContent=item.value==null?"—":String(item.value);
      card.append(label,value);summary.append(card);
    }
    out.append(heading,summary);
    if(view.columns.length){
      const wrapper=document.createElement("div");wrapper.className="table-wrap advanced-table";
      const table=document.createElement("table"),thead=document.createElement("thead"),headRow=document.createElement("tr");
      for(const column of view.columns){const cell=document.createElement("th");cell.textContent=column;headRow.append(cell);}
      thead.append(headRow);table.append(thead);
      const tbody=document.createElement("tbody");
      for(const values of view.rows){const row=document.createElement("tr");
        for(const value of values){const cell=document.createElement("td");cell.textContent=value==null?"—":String(value);row.append(cell);}
        tbody.append(row);
      }
      if(!view.rows.length){const row=document.createElement("tr"),cell=document.createElement("td");
        cell.colSpan=view.columns.length;cell.textContent="No defined observations for this analysis.";row.append(cell);tbody.append(row);}
      table.append(tbody);wrapper.append(table);out.append(wrapper);
    }
    if(view.chart?.points?.length){
      const chartBox=document.createElement("div");chartBox.className="chart-box advanced-chart-box";
      const canvas=document.createElement("canvas");canvas.setAttribute("aria-label",view.chart.label);chartBox.append(canvas);out.append(chartBox);
      state.advancedChart=new Chart(canvas,{type:"line",data:{datasets:[{label:view.chart.label,
        data:view.chart.points.map(point=>({x:new Date(point.timestamp),y:point.value})),
        borderColor:"#58bad1",backgroundColor:"#58bad122",pointRadius:0,borderWidth:1.5,fill:true}]},
        options:{responsive:true,maintainAspectRatio:false,animation:false,parsing:false,plugins:{legend:{display:false}},
          scales:{x:{type:"time",ticks:{color:"#748798",maxTicksLimit:8},grid:{color:"#24313b"}},
            y:{ticks:{color:"#748798"},grid:{color:"#24313b"}}}}});
    }
    if(view.note){const note=document.createElement("p");note.className="advanced-note";note.textContent=view.note;out.append(note);}
    const sources=document.createElement("small");sources.className="advanced-sources";
    sources.textContent=`Source experiment IDs: ${(result.input_experiment_ids||[]).join(", ")||"—"}`;out.append(sources);
  }
  function renderAdvancedError(message){
    const out=$("advanced-output");out.replaceChildren();out.hidden=false;
    const notice=document.createElement("p");notice.className="advanced-error";notice.setAttribute("role","alert");
    notice.textContent=message;out.append(notice);
  }
  async function advanced(path,body){if(state.advancedBusy||state.researchBusy)return;state.advancedBusy=true;syncAdvancedButtons();$("clear-results").disabled=true;const out=$("advanced-output");out.hidden=false;out.textContent="Running analysis…";
    try{renderAdvanced(await request(path,body));}
    catch(reason){renderAdvancedError(reason.message);}
    finally{state.advancedBusy=false;$("clear-results").disabled=false;syncAdvancedButtons();}}
  $("run-sensitivity").addEventListener("click",()=>advanced("/advanced/sensitivity",{experiment_ids:state.sensitivityIds,
    metric:$("advanced-metric").value,parameters:state.sensitivityParameters}));
  $("run-robustness").addEventListener("click",()=>advanced("/advanced/robustness",{experiment_id:state.baselineId,
    metric:$("advanced-metric").value,scenarios:[
      {name:"commission plus 0.1 per unit",commission_per_unit_addition:0.1},
      {name:"spread scale doubled",spread_multiplier:2},
      {name:"slippage doubled",slippage_multiplier:2,slippage_addition:0.0001}
    ]}));
  $("run-correlation").addEventListener("click",()=>{
    if(state.researchBusy||state.advancedBusy)return;
    if(state.correlationIds.length<2)return;
    advanced("/advanced/correlation",{experiment_ids:state.correlationIds});
  });
  $("run-portfolio").addEventListener("click",()=>{
    if(state.researchBusy||state.advancedBusy)return;
    const aligned=state.correlationIds;if(aligned.length<2)return;const weights={};
    for(const id of aligned)weights[id]=1/aligned.length;
    advanced("/advanced/portfolio",{experiment_ids:aligned,weights:weights,
      initial_capital:state.portfolioInitialCapital});});
  $("run-mc").addEventListener("click",()=>advanced("/advanced/monte-carlo",{experiment_id:state.monteCarloId,
    simulations:Number($("mc-count").value),seed:Number($("mc-seed").value),method:"bootstrap"}));
  $("run-regime").addEventListener("click",()=>advanced("/advanced/regimes",{experiment_id:state.baselineId,
    lookback:Number($("regime-lookback").value),volatility_threshold:Number($("regime-threshold").value)}));
  updateMode();loadCatalog();
})();
