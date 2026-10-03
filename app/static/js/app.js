const form=document.getElementById("import-form");
const fileInput=document.getElementById("csv-file");
const fileName=document.getElementById("file-name");
const status=document.getElementById("status");
const instrument=document.getElementById("instrument");
const timeframe=document.getElementById("timeframe");
const observations=document.getElementById("observations");
const startDate=document.getElementById("start-date");
const endDate=document.getElementById("end-date");
const lastClose=document.getElementById("last-close");
const spread=document.getElementById("spread");
const meanReturn=document.getElementById("mean-return");
const volatility=document.getElementById("volatility");
const drawdown=document.getElementById("drawdown");
const priceRange=document.getElementById("price-range");
const rangeSlider=document.getElementById("range-slider");
const rangeValue=document.getElementById("range-value");
let marketData=[];
let priceChart=null;
let returnChart=null;
let volumeChart=null;
let chartType="candlestick";
let selectedTimestamp=null;
if(typeof ChartZoom!=="undefined")Chart.register(ChartZoom);
if(typeof CrosshairPlugin!=="undefined")Chart.register(CrosshairPlugin);
if(typeof CandlestickController!=="undefined"&&typeof CandlestickElement!=="undefined")Chart.register(CandlestickController,CandlestickElement);
function setStatus(message,type){
    status.className="mt-4 rounded-xl border px-4 py-3 text-sm";
    if(type==="success")status.classList.add("border-emerald-500/20","bg-emerald-500/5","text-emerald-400");
    if(type==="warning")status.classList.add("border-amber-500/20","bg-amber-500/5","text-amber-400");
    if(type==="error")status.classList.add("border-red-500/20","bg-red-500/5","text-red-400");
    if(type==="info")status.classList.add("border-cyan-500/20","bg-cyan-500/5","text-cyan-400");
    status.textContent=message;
}
function calculateReturns(data){
    return data.slice(1).map((row,index)=>(row.close/data[index].close-1)*100);
}
function calculateMetrics(data){
    const returns=calculateReturns(data);
    if(!returns.length)return{mean:0,standardDeviation:0,maximumDrawdown:0,range:0};
    const mean=returns.reduce((sum,value)=>sum+value,0)/returns.length;
    const variance=returns.length>1?returns.reduce((sum,value)=>sum+(value-mean)**2,0)/(returns.length-1):0;
    const standardDeviation=Math.sqrt(variance);
    let peak=data[0].close;
    let maximumDrawdown=0;
    for(const row of data){
        peak=Math.max(peak,row.close);
        maximumDrawdown=Math.min(maximumDrawdown,(row.close/peak-1)*100);
    }
    const prices=data.map(row=>row.close);
    return{mean,standardDeviation,maximumDrawdown,range:Math.max(...prices)-Math.min(...prices)};
}
function updateMetrics(data){
    if(!data.length)return;
    const metrics=calculateMetrics(data);
    observations.textContent=data.length.toLocaleString();
    startDate.textContent=new Date(data[0].timestamp).toLocaleString();
    endDate.textContent=new Date(data[data.length-1].timestamp).toLocaleString();
    lastClose.textContent=Number(data[data.length-1].close).toFixed(5);
    spread.textContent=data[data.length-1].spread;
    meanReturn.textContent=`${metrics.mean.toFixed(4)}%`;
    volatility.textContent=`${metrics.standardDeviation.toFixed(4)}%`;
    drawdown.textContent=`${metrics.maximumDrawdown.toFixed(2)}%`;
    priceRange.textContent=metrics.range.toFixed(5);
}
function getTimeUnit(){
    if(timeframe.value==="M1"||timeframe.value==="M5"||timeframe.value==="M15")return"hour";
    if(timeframe.value==="H1"||timeframe.value==="H4")return"day";
    return"month";
}
function chartOptions(data){
    const unit=getTimeUnit();
    return{
        responsive:true,
        maintainAspectRatio:false,
        animation:false,
        parsing:false,
        normalized:true,
        interaction:{mode:"index",intersect:false},
        onClick:(event,elements)=>{
            if(!elements.length)return;
            const index=elements[0].index;
            if(!data[index])return;
            selectedTimestamp=new Date(data[index].timestamp).getTime();
        },
        plugins:{
            legend:{display:false},
            tooltip:{
                enabled:true,
                callbacks:{
                    title:items=>items.length?new Date(items[0].parsed.x).toLocaleString():"",
                    label:context=>{
                        const raw=context.raw;
                        if(chartType==="candlestick"&&raw?.o!==undefined)return[`Open: ${raw.o.toFixed(5)}`,`High: ${raw.h.toFixed(5)}`,`Low: ${raw.l.toFixed(5)}`,`Close: ${raw.c.toFixed(5)}`];
                        return`Close: ${Number(context.parsed.y).toFixed(5)}`;
                    }
                }
            },
            zoom:{
                pan:{enabled:true,mode:"x"},
                zoom:{wheel:{enabled:true},pinch:{enabled:true},drag:{enabled:true},mode:"x"}
            },
            crosshair:{
                line:{color:"#94a3b8",width:1},
                sync:{enabled:false},
                zoom:{enabled:false}
            }
        },
        scales:{
            x:{
                type:"time",
                time:{unit:unit,displayFormats:{minute:"HH:mm",hour:"MMM d HH:mm",day:"MMM d",month:"MMM yyyy"}},
                ticks:{color:"#64748b",maxTicksLimit:12},
                grid:{color:"#1e293b"}
            },
            y:{
                ticks:{color:"#64748b"},
                grid:{color:"#1e293b"}
            }
        }
    };
}
function getCenteredData(){
    if(!marketData.length)return[];
    const range=Math.min(Number(rangeSlider.value),marketData.length);
    if(!selectedTimestamp)return marketData.slice(-range);
    let index=0;
    let distance=Infinity;
    for(let i=0;i<marketData.length;i++){
        const timestamp=new Date(marketData[i].timestamp).getTime();
        const currentDistance=Math.abs(timestamp-selectedTimestamp);
        if(currentDistance<distance){
            distance=currentDistance;
            index=i;
        }
    }
    let start=index-Math.floor(range/2);
    start=Math.max(0,Math.min(start,marketData.length-range));
    return marketData.slice(start,start+range);
}
function renderCharts(data){
    if(!data.length)return;
    priceChart?.destroy();
    returnChart?.destroy();
    volumeChart?.destroy();
    const candles=data.map(row=>({x:new Date(row.timestamp).getTime(),o:Number(row.open),h:Number(row.high),l:Number(row.low),c:Number(row.close)}));
    const timestamps=data.map(row=>new Date(row.timestamp).getTime());
    const returns=calculateReturns(data);
    priceChart=new Chart(document.getElementById("price-chart"),{
        type:chartType==="candlestick"?"candlestick":"line",
        data:{datasets:[chartType==="candlestick"?{
            label:"Price",
            data:candles,
            color:{up:"#22c55e",down:"#ef4444",unchanged:"#94a3b8"},
            borderColor:{up:"#22c55e",down:"#ef4444",unchanged:"#94a3b8"},
            backgroundColor:{up:"#22c55e",down:"#ef4444",unchanged:"#94a3b8"}
        }:{
            label:"Close",
            data:data.map(row=>({x:new Date(row.timestamp).getTime(),y:Number(row.close)})),
            borderColor:"#22d3ee",
            borderWidth:1.5,
            pointRadius:0,
            pointHoverRadius:3,
            tension:0
        }]},
        options:chartOptions(data)
    });
    const unit=getTimeUnit();
    returnChart=new Chart(document.getElementById("return-chart"),{
        type:"bar",
        data:{datasets:[{
            label:"Return",
            data:returns.map((value,index)=>({x:timestamps[index+1],y:value})),
            backgroundColor:returns.map(value=>value>=0?"#22c55e":"#ef4444"),
            borderWidth:0
        }]},
        options:{
            responsive:true,
            maintainAspectRatio:false,
            animation:false,
            parsing:false,
            normalized:true,
            plugins:{legend:{display:false}},
            scales:{
                x:{type:"time",time:{unit:unit},ticks:{color:"#64748b",maxTicksLimit:12},grid:{color:"#1e293b"}},
                y:{ticks:{color:"#64748b"},grid:{color:"#1e293b"}}
            }
        }
    });
    volumeChart=new Chart(document.getElementById("volume-chart"),{
        type:"line",
        data:{datasets:[
            {
                label:"Tick Volume",
                data:data.map(row=>({x:new Date(row.timestamp).getTime(),y:Number(row.tick_volume)})),
                borderColor:"#a78bfa",
                borderWidth:1.5,
                pointRadius:0,
                tension:0
            },
            {
                label:"Volume",
                data:data.map(row=>({x:new Date(row.timestamp).getTime(),y:Number(row.volume)})),
                borderColor:"#f59e0b",
                borderWidth:1.5,
                pointRadius:0,
                tension:0
            }
        ]},
        options:{
            responsive:true,
            maintainAspectRatio:false,
            animation:false,
            parsing:false,
            normalized:true,
            plugins:{legend:{labels:{color:"#94a3b8"}}},
            scales:{
                x:{type:"time",time:{unit:unit},ticks:{color:"#64748b",maxTicksLimit:12},grid:{color:"#1e293b"}},
                y:{ticks:{color:"#64748b"},grid:{color:"#1e293b"}}
            }
        }
    });
}
function renderCurrentView(){
    const visibleData=getCenteredData();
    if(!visibleData.length)return;
    updateMetrics(visibleData);
    renderCharts(visibleData);
}
function resetZoom(){
    priceChart?.resetZoom();
}
function setChartType(type){
    chartType=type;
    renderCurrentView();
}
async function loadMarketData(symbol,period,limit=5000){
    setStatus(`Loading ${period} market history...`,"info");
    const params=new URLSearchParams({symbol,timeframe:period,limit:String(limit)});
    if(selectedTimestamp)params.set("center_timestamp",new Date(selectedTimestamp).toISOString());
    const response=await fetch(`/market-data?${params.toString()}`);
    const result=await response.json();
    if(!response.ok)throw new Error(result.detail||"Failed to load market data.");
    marketData=result.data;
    rangeSlider.max=Math.min(5000,Math.max(1,marketData.length));
    if(Number(rangeSlider.value)>Number(rangeSlider.max))rangeSlider.value=rangeSlider.max;
    rangeValue.textContent=Number(rangeSlider.value).toLocaleString();
    if(!marketData.length){
        priceChart?.destroy();
        returnChart?.destroy();
        volumeChart?.destroy();
        priceChart=null;
        returnChart=null;
        volumeChart=null;
        setStatus(`${symbol} has no market data for ${period}.`,"warning");
        return;
    }
    status.classList.add("hidden");
    renderCurrentView();
}
form.addEventListener("submit",async event=>{
    event.preventDefault();
    const file=fileInput.files[0];
    const button=form.querySelector("button[type='submit']");
    if(!file){
        setStatus("Select a CSV dataset first.","warning");
        return;
    }
    button.disabled=true;
    button.textContent="Processing...";
    setStatus("Importing historical dataset...","info");
    try{
        const formData=new FormData();
        formData.append("csv_file",file);
        const response=await fetch("/import-csv",{method:"POST",body:formData});
        const data=await response.json();
        if(!response.ok)throw new Error(data.detail||data.message||"Import failed.");
        setStatus(data.message,data.imported===false?"warning":"success");
        if(data.imported!==false)await loadMarketData(data.symbol,timeframe.value,5000);
    }catch(error){
        setStatus(error.message,"error");
    }finally{
        button.disabled=false;
        button.textContent="Import";
    }
});
fileInput.addEventListener("change",()=>{
    const file=fileInput.files[0];
    fileName.textContent=file?`${file.name} · ${(file.size/1024/1024).toFixed(1)} MB`:"";
});
rangeSlider?.addEventListener("input",()=>{
    rangeValue.textContent=Number(rangeSlider.value).toLocaleString();
    renderCurrentView();
});
instrument.addEventListener("change",()=>{
    selectedTimestamp=null;
    loadMarketData(instrument.value,timeframe.value).catch(error=>setStatus(error.message,"error"));
});
timeframe.addEventListener("change",()=>{
    loadMarketData(instrument.value,timeframe.value,5000).catch(error=>setStatus(error.message,"error"));
});
if(rangeSlider)rangeValue.textContent=Number(rangeSlider.value).toLocaleString();
loadMarketData(instrument.value,timeframe.value,5000).catch(error=>setStatus(error.message,"error"));