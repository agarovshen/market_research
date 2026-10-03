(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const form = $("research-form"), state = { ids: [], sensitivityIds: [], correlationIds: [], baselineId: null, chart: null, advancedChart: null };
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
  function updateMode() {
    const mode=$("mode").value;
    $("single-parameters").hidden=mode!=="single"; $("space-parameters").hidden=mode==="single";
    $("search-config").hidden=mode==="single"; $("split-config").hidden=mode!=="oos"; $("walk-config").hidden=mode!=="walk_forward";
    $("run-button").textContent={single:"Run single backtest",batch:"Run parameter batch",oos:"Select on train → evaluate OOS",walk_forward:"Run walk-forward"}[mode];
  }
  async function loadCatalog() {
    try {
      const strategies=await request("/strategies");
      $("strategy").replaceChildren(...strategies.map(item=>{const option=document.createElement("option");option.value=item.id;option.textContent=item.id+" · v"+item.version;return option;}));
      if(!strategies.length) throw new Error("No strategies are registered.");
      const response=await fetch("/instruments"); const instruments=await response.json();
      if(!response.ok) throw new Error("Unable to load instruments.");
      $("symbol").replaceChildren(...instruments.map(item=>{const option=document.createElement("option");option.value=item.symbol;option.textContent=item.symbol;return option;}));
      if(!instruments.length) throw new Error("No instruments are available. Import market data first.");
      await loadRange(); status("Research API ready");
    } catch (reason) { status(reason.message,true); error(reason.message); }
  }
  async function loadRange() {
    const data=await request("/market-data/"+encodeURIComponent($("symbol").value));
    $("data-range").textContent=data.count ? data.count.toLocaleString()+" stored rows · "+data.start+" through "+data.end : "No market data stored for this instrument.";
    if(data.count){const start=String(data.start).slice(0,16),end=addMinutes(String(data.end),intervalMinutes($("research-form").elements.timeframe.value));
      if(!form.elements.start.value)form.elements.start.value=start;
      if(!form.elements.end.value)form.elements.end.value=end;
      initializeSplit();}
  }
  function intervalMinutes(timeframe){return {M1:1,M5:5,M15:15,H1:60,H4:240,D1:1440}[timeframe]||1;}
  function addMinutes(value,amount){const parts=value.slice(0,16).split(/[-T:]/).map(Number);
    const date=new Date(Date.UTC(parts[0],parts[1]-1,parts[2],parts[3],parts[4]+amount));
    return date.toISOString().slice(0,16);}
  function initializeSplit(){const start=form.elements.start.value,end=form.elements.end.value;
    if(!start||!end||form.elements.training_start.value)return;
    const left=Date.parse(start+"Z"),right=Date.parse(end+"Z"),cut=new Date(left+(right-left)*.7);
    const boundary=cut.toISOString().slice(0,16);
    form.elements.training_start.value=start;form.elements.training_end.value=boundary;
    form.elements.testing_start.value=boundary;form.elements.testing_end.value=end;}
  function range(name,prefix) { return {name:name,kind:"integer",minimum:Number(form.elements[prefix+"_min"].value),maximum:Number(form.elements[prefix+"_max"].value),step:Number(form.elements[prefix+"_step"].value)}; }
  function payload() {
    const fields=new FormData(form), mode=fields.get("mode");
    const body={mode:mode,strategy_id:fields.get("strategy_id"),symbol:fields.get("symbol"),timeframe:fields.get("timeframe"),start:fields.get("start"),end:fields.get("end"),
      initial_cash:Number(fields.get("initial_cash")),position_size:Number(fields.get("position_size")),commission_per_unit:Number(fields.get("commission_per_unit")),
      commission_rate:Number(fields.get("commission_rate")),spread_scale:Number(fields.get("spread_scale")),slippage:Number(fields.get("slippage"))};
    body.risk_free_rate=Number(fields.get("risk_free_rate"));body.target_return=Number(fields.get("target_return"));
    if(fields.get("periods_per_year"))body.periods_per_year=Number(fields.get("periods_per_year"));
    if(mode==="single"){body.parameters={fast_period:Number(fields.get("fast_period")),slow_period:Number(fields.get("slow_period"))};return body;}
    body.parameter_space=[range("fast_period","fast"),range("slow_period","slow")];
    body.search_method=fields.get("search_method");body.count=Number(fields.get("count"));body.seed=Number(fields.get("seed"));
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
    $("chart-empty").hidden=!!analysis?.equity_curve?.length;
    if(!analysis?.equity_curve?.length)return;
    if(state.chart)state.chart.destroy();
    const points=analysis.equity_curve.map(item=>({x:new Date(item.timestamp),y:item.equity}));
    state.chart=new Chart($("equity-chart"),{type:"line",data:{datasets:[{label:"Equity",data:points,borderColor:"#58bad1",backgroundColor:"#58bad122",pointRadius:0,borderWidth:1.5,fill:true}]},
      options:{responsive:true,maintainAspectRatio:false,animation:false,parsing:false,plugins:{legend:{display:false}},
        scales:{x:{type:"time",time:{unit:"day"},ticks:{color:"#748798",maxTicksLimit:8},grid:{color:"#24313b"}},
          y:{ticks:{color:"#748798"},grid:{color:"#24313b"}}}}});
  }
  function render(result) {
    if(state.advancedChart){state.advancedChart.destroy();state.advancedChart=null;}
    $("advanced-output").replaceChildren();$("advanced-output").hidden=true;
    const rows=flatten(result); state.ids=rows.filter(row=>row.status==="completed").map(row=>row.definition.experiment_id);
    state.sensitivityIds=ResearchWorkspaceState.sensitivityGroup(rows)
      .map(row=>row.definition.experiment_id);
    state.correlationIds=ResearchWorkspaceState.alignedCorrelationGroup(rows)
      .map(row=>row.definition.experiment_id);
    $("run-correlation").disabled=state.correlationIds.length<2;
    $("correlation-hint").textContent=state.correlationIds.length>=2
      ? `${state.correlationIds.length} aligned ${state.correlationIds[0]&&rows.find(row=>row.definition.experiment_id===state.correlationIds[0]).definition.phase.toUpperCase()} return series`
      : "Correlation needs at least two completed results from the same phase and data period.";
    $("run-sensitivity").disabled=state.sensitivityIds.length<2;
    $("sensitivity-hint").textContent=state.sensitivityIds.length>=2
      ? `${state.sensitivityIds.length} comparable ${rows.find(row=>row.definition.experiment_id===state.sensitivityIds[0]).definition.phase.toUpperCase()} configurations`
      : "Sensitivity needs multiple completed parameter configurations in one training or batch period.";
    $("run-portfolio").disabled=state.correlationIds.length<2;
    $("portfolio-hint").textContent=state.correlationIds.length>=2
      ? `${state.correlationIds.length} aligned series will receive equal weights.`
      : "Portfolio curve needs at least two completed aligned results.";
    const oosRow=rows.find(row=>row.definition.phase==="oos"&&row.status==="completed"&&row.analysis_result);
    state.baselineId=oosRow?.definition.experiment_id||state.sensitivityIds[0]||state.ids[0]||null;
    $("run-robustness").disabled=!state.baselineId;
    $("run-mc").disabled=!state.baselineId;
    $("run-regime").disabled=!state.baselineId;
    const analysis=oosRow?.analysis_result||rows.find(row=>row.analysis_result)?.analysis_result;
    const walk=result.walk_forward||result;
    $("result-title").textContent=result.training?"Training and OOS evaluation":walk.windows?walk.windows.length+" walk-forward windows":"Backtest / batch results";
    const completed=rows.filter(row=>row.status==="completed").length, failed=rows.length-completed, summary=$("summary");
    summary.hidden=false;
    const cards=[["Experiments",rows.length],["Completed",completed],["Failed",failed],
      ["OOS evaluated",rows.filter(row=>row.definition.phase==="oos"&&row.status==="completed").length],
      ["Selection metric",result.selection_rule?.metric||"—"],["Selected",result.selected?.length??"—"],
      [oosRow?"OOS total return":"Batch representative return",analysis?.total_return==null?"—":(analysis.total_return*100).toFixed(2)+"%"],
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
    tbody.replaceChildren(...rows.map(row=>{const d=row.definition,a=row.analysis_result,tr=document.createElement("tr");tr.dataset.phase=d.phase;
      const data=[d.phase.toUpperCase(),row.status,JSON.stringify(Object.fromEntries(d.parameters.values)),fmt(a?.total_return),fmt(a?.trades?.net_profit),fmt(a?.trades?.total_trades),d.experiment_id.slice(0,12),row.failure?(row.failure.exception_type+": "+row.failure.message):"—"];
      for(const value of data){const cell=document.createElement("td");cell.textContent=value;tr.append(cell);}
      if(row.status==="failed")tr.title=(row.failure?.exception_type||"Failure")+": "+(row.failure?.message||"");return tr;}));
    renderChart(analysis);$("advanced-tools").hidden=!state.ids.length;
  }
  form.addEventListener("submit",async event=>{event.preventDefault();error("");const button=$("run-button");button.disabled=true;button.textContent="Running…";
    try{render(await request("/run",payload()));status("Research run completed");}
    catch(reason){error(reason.message);status("Research run failed",true);}
    finally{button.disabled=false;updateMode();}});
  $("mode").addEventListener("change",()=>{updateMode();initializeSplit();});
  $("symbol").addEventListener("change",()=>loadRange().catch(reason=>error(reason.message)));
  $("clear-results").addEventListener("click",()=>{state.ids=[];state.sensitivityIds=[];state.correlationIds=[];state.baselineId=null;
    for(const id of ["run-sensitivity","run-robustness","run-correlation","run-portfolio","run-mc","run-regime"])
      $(id).disabled=true;
    $("sensitivity-hint").textContent="Sensitivity needs multiple completed parameter configurations in one training or batch period.";
    $("correlation-hint").textContent="Correlation needs at least two completed results from the same phase and data period.";
    $("portfolio-hint").textContent="Portfolio curve needs at least two completed aligned results.";
    if(state.advancedChart){state.advancedChart.destroy();state.advancedChart=null;}
    if(state.chart){state.chart.destroy();state.chart=null;}
    $("records").replaceChildren();$("summary").hidden=true;$("advanced-tools").hidden=true;$("chart-empty").hidden=false;
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
  async function advanced(path,body,button){const out=$("advanced-output");out.hidden=false;out.textContent="Running analysis…";
    if(button)button.disabled=true;
    try{renderAdvanced(await request(path,body));}
    catch(reason){renderAdvancedError(reason.message);}
    finally{if(button)button.disabled=false;}}
  $("run-sensitivity").addEventListener("click",()=>advanced("/advanced/sensitivity",{experiment_ids:state.sensitivityIds,
    metric:$("advanced-metric").value,parameters:["fast_period","slow_period"]},$("run-sensitivity")));
  $("run-robustness").addEventListener("click",()=>advanced("/advanced/robustness",{experiment_id:state.baselineId,
    metric:$("advanced-metric").value,scenarios:[
      {name:"commission plus 0.1 per unit",commission_per_unit_addition:0.1},
      {name:"spread scale doubled",spread_multiplier:2},
      {name:"slippage doubled",slippage_multiplier:2,slippage_addition:0.0001}
    ]},$("run-robustness")));
  $("run-correlation").addEventListener("click",()=>{
    if(state.correlationIds.length<2)return;
    advanced("/advanced/correlation",{experiment_ids:state.correlationIds},$("run-correlation"));
  });
  $("run-portfolio").addEventListener("click",()=>{
    const aligned=state.correlationIds;if(aligned.length<2)return;const weights={};
    for(const id of aligned)weights[id]=1/aligned.length;
    advanced("/advanced/portfolio",{experiment_ids:aligned,weights:weights,
      initial_capital:Number(form.elements.initial_cash.value)},$("run-portfolio"));});
  $("run-mc").addEventListener("click",()=>advanced("/advanced/monte-carlo",{experiment_id:state.baselineId,
    simulations:Number($("mc-count").value),seed:Number($("mc-seed").value),method:"bootstrap"},$("run-mc")));
  $("run-regime").addEventListener("click",()=>advanced("/advanced/regimes",{experiment_id:state.baselineId,
    lookback:Number($("regime-lookback").value),volatility_threshold:Number($("regime-threshold").value)},$("run-regime")));
  updateMode();loadCatalog();
})();
