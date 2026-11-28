import {
  $, V, store, hooks, html, setHtml, setHidden, colorize, copyText, setBusy,
} from './core.js';
import { getCap, capPush, capEnd } from './runner.js';

// ---------------- 日志过滤 / 复制 / 迷你抽屉 ----------------
const getCons=()=>$('console');
let LOG_ITEMS=[], _logLevel='all', _drawerOpen=false;
export function getLogItems(){return LOG_ITEMS;}

export function makeLogNode(l){
  const d=document.createElement('div');
  setHtml(d,html`<span class="t">${l.t}</span><span></span>`);
  d.lastChild.textContent=l.line;
  if(l.cls)d.lastChild.className=l.cls;
  return d;
}
export function matchLogItem(it){
  if(_logLevel==='err'&&it.cls!=='err')return false;
  if(_logLevel==='ok'&&it.cls!=='ok')return false;
  const kw=($('logFilter')?V('logFilter'):'').toLowerCase();
  if(kw&&!(it.line.toLowerCase().includes(kw)||it.t.toLowerCase().includes(kw)))return false;
  return true;
}
function _updateLogCount(shownLen){
  const el=$('logCount'), cons=getCons();if(!el)return;
  if(!LOG_ITEMS.length){el.textContent='';return;}
  const n=(shownLen==null)?(cons?cons.children.length:0):shownLen;
  el.textContent=n===LOG_ITEMS.length?`共 ${LOG_ITEMS.length} 行`:`筛选 ${n} / ${LOG_ITEMS.length} 行`;
}
export function setLogLevel(lvl,btn){
  _logLevel=lvl;
  document.querySelectorAll('#p-logs .flt').forEach(b=>b.classList.toggle('on',b===btn));
  renderLogList();
}
export function renderLogList(){
  const cons=getCons();
  if(!cons)return;
  const matched=LOG_ITEMS.filter(matchLogItem);
  const shown=matched.slice(-1200);
  const frag=document.createDocumentFragment();
  shown.forEach(it=>frag.appendChild(makeLogNode(it)));
  setHtml(cons,'');cons.appendChild(frag);
  cons.scrollTop=cons.scrollHeight;
  updateLogJump();
  _updateLogCount(matched.length);
  setHidden('consoleEmpty',LOG_ITEMS.length>0);
}
export function updateLogJump(){
  const btn=$('logJump'), cons=getCons();if(!btn||!cons)return;
  const away=cons.scrollHeight-cons.scrollTop-cons.clientHeight>=50;
  btn.classList.toggle('on',away&&cons.children.length>0);
}
export function scrollLogsBottom(){const cons=getCons();if(cons){cons.scrollTop=cons.scrollHeight;updateLogJump();}}
export function copyLogs(){
  const txt=LOG_ITEMS.filter(matchLogItem).map(it=>`${it.t} ${it.line}`).join('\n');
  copyText(txt,'已复制控制台日志');
}
export function renderDrawer(){
  const dr=$('logDrawer'), bd=$('ldBody');
  if(!dr||!bd||!_drawerOpen)return;
  const recent=LOG_ITEMS.slice(-10);
  if(!recent.length){setHtml(bd,html`<div>（暂无输出）</div>`);return;}
  const frag=document.createDocumentFragment();
  recent.forEach(it=>frag.appendChild(makeLogNode(it)));
  setHtml(bd,'');bd.appendChild(frag);
  bd.scrollTop=bd.scrollHeight;
}
export function toggleDrawer(force){
  const dr=$('logDrawer');if(!dr)return;
  _drawerOpen=(force===undefined)?!_drawerOpen:!!force;
  dr.classList.toggle('on',_drawerOpen);
  if(_drawerOpen)renderDrawer();
}
export function clearConsole(){
  const cons=getCons();
  LOG_ITEMS=[];
  if(cons)setHtml(cons,'');
  setHidden('consoleEmpty',false);
  _updateLogCount(0);
  updateLogJump();
  renderDrawer();
}

let _logsInFlight=false;
export async function pullLogs(){
  if(_logsInFlight)return;
  _logsInFlight=true;
  const cons=getCons();
  try{
    let r;
    try{r=await(await fetch('/api/logs?since='+store.logSeq)).json();}catch(_){return;}
    if(r.lines&&r.lines.length){
      const atBottom=cons?(cons.scrollHeight-cons.scrollTop-cons.clientHeight<45):true;
      const hasFilter=_logLevel!=='all'||($('logFilter')&&V('logFilter'));
      r.lines.forEach(l=>{
        store.logSeq=l.seq;capPush(l.line);
        const it={t:l.t,line:l.line,cls:colorize(l.line)};
        LOG_ITEMS.push(it);
        if(!hasFilter&&cons&&matchLogItem(it))cons.appendChild(makeLogNode(it));
      });
      if(LOG_ITEMS.length>1500)LOG_ITEMS=LOG_ITEMS.slice(-1500);
      if(hasFilter)renderLogList();
      else if(cons){
        while(cons.children.length>1200)cons.removeChild(cons.firstChild);
        if(atBottom)cons.scrollTop=cons.scrollHeight;
        updateLogJump();
        _updateLogCount(LOG_ITEMS.length);
      }
      setHidden('consoleEmpty',true);
      if(_drawerOpen)renderDrawer();
    }
    const wasRunning=store.lastRunning; store.lastRunning=!!r.running;
    const cap=getCap();
    if(cap&&(r.running||(r.lines&&r.lines.length)))cap.seenActivity=true;
    setBusy(r.running,r.label);
    hooks.renderNavBadges();
    if(!r.running){
      const curCap=getCap();
      if(curCap&&curCap.armed&&(curCap.seenActivity||Date.now()-curCap.startedAt>1500)){
        capEnd();
      }
      if(wasRunning){
        hooks.pullState(true);
        if(store.pendingSchedRefresh){store.pendingSchedRefresh=false;hooks.refreshSched(true);}
      }
    }
  }finally{_logsInFlight=false;}
}

export function initLogs(){
  const cons=getCons();
  if(cons&&!cons.dataset.scrollBound){
    cons.dataset.scrollBound='1';
    cons.addEventListener('scroll',updateLogJump,{passive:true});
  }
}
