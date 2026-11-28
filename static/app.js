import {
  $, store, registerHooks, TITLES, html, setHtml, setTone,
  tokenAge, sessLeftMin, toast, clearErr, closeModal, toggleAppear, toggleTheme,
  zoomBy, zoomReset, initCoreUi, setBusy, act, cf,
} from './js/core.js';
import {
  renderEnv, renderNavBadges, renderTodo, renderKpiAndCards,
  renderAwSection, onAwFilter, toggleAwAll, renderEff,
} from './js/dash.js';
import {
  renderSelects, updateBassFodder, renderAccounts, openNewAccount, saveNewAccount,
  editAccount, saveAccount, clearNotify, setBassTab, initBassTab,
  renderLuck, renderXunbao, renderDaily, renderFlowbank, renderSkWindow, renderSeckillInfo, renderXxl,
} from './js/activities.js';
import {
  updSched, refreshSched, applyAllSched, unregAllSched, regSched, unregSched,
  openLog, toggleModalErr, copyModalLog, renderPolicy, feedAllToggle, savePolicy,
  renderSettings, saveSettings, mailProvider, saveMail, gotoSettings, afterHar, initHarDrop,
  importHar, initDirtyForms,
} from './js/ops.js';
import {
  getCap, copyCapBox, runBass, runBassProbe, runLuck, runXunbao, pickStage, initManualBass,
  runGames, runXxl, runXxlAll, xxlQuery, detectLevel, runSeckill, seckillQuery, seckillOnce,
  runAll, voucherRun, refreshStatus,
} from './js/runner.js';
import {
  setLogLevel, renderLogList, scrollLogsBottom, copyLogs, toggleDrawer, clearConsole, pullLogs, initLogs,
} from './js/logs.js';
import { renderVersion, gotoAbout, checkUpdate, skipUpdate, toggleAutoUpdate, initUpdate } from './js/update.js';

// ---------------- 页面路由 & 分片按需渲染 ----------------
export function nav(b){
  if(!b)return;
  document.querySelectorAll('nav button').forEach(x=>{x.classList.remove('on');x.removeAttribute('aria-current');});
  b.classList.add('on');
  b.setAttribute('aria-current','page');
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('on'));
  const p=b.dataset.p;
  store.activePage=p;
  $('p-'+p).classList.add('on');
  $('pageTitle').textContent='签到控制台 / '+(TITLES[p]||'');
  flushPage(p);
  try{history.replaceState(null,'','#'+p);}catch(_){}
}
export function navTo(p){
  if(p==='xxl'){navTo('bass');setBassTab('xxl');return;}
  const b=document.querySelector('nav button[data-p="'+p+'"]');if(b)nav(b);
}

let _builtOnce=false,_sig={},_dirtyPages={};
function sliceSig(k,val){
  const s=JSON.stringify(val===undefined?null:val);
  if(_sig[k]===s)return false;
  _sig[k]=s;
  return true;
}

export function render(){
  const STATE=store.state;
  if(!STATE)return;
  _dirtyPages={};
  renderEnv();
  renderNavBadges();
  renderSelects();
  renderTodo();
  renderKpiAndCards();
  renderAwSection();
  renderEff();
  renderAccounts();
  renderFlowbank();
  renderDaily();
  renderLuck();
  renderXunbao();
  renderXxl();
  renderPolicy();
  renderSettings();
  renderSeckillInfo();
  updSched();
  _builtOnce=true;
  _sig={
    env:JSON.stringify([STATE.env, (STATE.accounts||[]).length, (STATE.raw_accounts||[]).length]),
    badges:JSON.stringify([STATE.token_life, STATE.raw_accounts, STATE.daily, STATE.xunbao, STATE.xxl, STATE.seckill_sessions, STATE.sched, STATE.sched_read_ok, STATE.task]),
    selects:JSON.stringify((STATE.raw_accounts||[]).map(a=>[a.key,a.file,a.plat,a.phone])),
    dash_todo:JSON.stringify([STATE.raw_accounts, STATE.token_life, STATE.seckill_sessions, STATE.daily, STATE.accounts, STATE.sched, STATE.sched_cn]),
    dash_kpi:JSON.stringify([STATE.stats, STATE.raw_accounts, STATE.token_life, STATE.awards, STATE.accounts]),
    dash_aw:JSON.stringify(STATE.awards),
    dash_eff:JSON.stringify(STATE.eff),
    token_accts:JSON.stringify([STATE.raw_accounts, STATE.stats, STATE.token_life]),
    bass:JSON.stringify([STATE.luck, STATE.daily, STATE.flowbank, STATE.xunbao, STATE.xxl, STATE.stats, STATE.policy&&STATE.policy.xxl]),
    seckill:JSON.stringify([STATE.seckill_sessions, STATE.token_life, STATE.policy&&STATE.policy.seckill]),
    sched:JSON.stringify([STATE.sched, STATE.sched_read_ok]),
    settings:JSON.stringify([STATE.policy, STATE.settings, STATE.mail])
  };
}

export function flushPage(p){
  const STATE=store.state;
  if(!STATE||!_dirtyPages[p])return;
  delete _dirtyPages[p];
  if(p==='dash'){
    if(sliceSig('dash_todo',[STATE.raw_accounts, STATE.token_life, STATE.seckill_sessions, STATE.daily, STATE.accounts, STATE.sched, STATE.sched_cn]))renderTodo();
    if(sliceSig('dash_kpi',[STATE.stats, STATE.raw_accounts, STATE.token_life, STATE.awards, STATE.accounts]))renderKpiAndCards();
    if(sliceSig('dash_aw',STATE.awards))renderAwSection();
    if(sliceSig('dash_eff',STATE.eff))renderEff();
  }else if(p==='token'){
    if(sliceSig('token_accts',[STATE.raw_accounts, STATE.stats, STATE.token_life]))renderAccounts();
  }else if(p==='bass'){
    if(sliceSig('bass',[STATE.luck, STATE.daily, STATE.flowbank, STATE.xunbao, STATE.xxl, STATE.stats, STATE.policy&&STATE.policy.xxl])){
      updateBassFodder();renderFlowbank();renderDaily();renderLuck();renderXunbao();renderXxl();renderPolicy();
    }
  }else if(p==='seckill'){
    if(sliceSig('seckill',[STATE.seckill_sessions, STATE.token_life, STATE.policy&&STATE.policy.seckill])){
      renderPolicy();renderSeckillInfo();
    }
  }else if(p==='sched'){
    if(sliceSig('sched',[STATE.sched, STATE.sched_read_ok]))updSched();
  }else if(p==='settings'){
    if(sliceSig('settings',[STATE.policy, STATE.settings, STATE.mail])){renderPolicy();renderSettings();}
  }
}

export function renderSmart(){
  const STATE=store.state;
  if(!STATE)return;
  if(!_builtOnce){render();return;}
  if(sliceSig('env',[STATE.env, (STATE.accounts||[]).length, (STATE.raw_accounts||[]).length]))renderEnv();
  if(sliceSig('badges',[STATE.token_life, STATE.raw_accounts, STATE.daily, STATE.xunbao, STATE.xxl, STATE.seckill_sessions, STATE.sched, STATE.sched_read_ok, STATE.task]))renderNavBadges();
  if(sliceSig('selects',(STATE.raw_accounts||[]).map(a=>[a.key,a.file,a.plat,a.phone])))renderSelects();

  const checkSlice=(page,key,val,fn)=>{
    const s=JSON.stringify(val===undefined?null:val);
    if(_sig[key]===s)return;
    if(store.activePage===page){_sig[key]=s;fn();}
    else{_dirtyPages[page]=true;}
  };
  checkSlice('dash','dash_todo',[STATE.raw_accounts, STATE.token_life, STATE.seckill_sessions, STATE.daily, STATE.accounts, STATE.sched, STATE.sched_cn],renderTodo);
  checkSlice('dash','dash_kpi',[STATE.stats, STATE.raw_accounts, STATE.token_life, STATE.awards, STATE.accounts],renderKpiAndCards);
  checkSlice('dash','dash_aw',STATE.awards,renderAwSection);
  checkSlice('dash','dash_eff',STATE.eff,renderEff);
  checkSlice('token','token_accts',[STATE.raw_accounts, STATE.stats, STATE.token_life],renderAccounts);
  checkSlice('bass','bass',[STATE.luck, STATE.daily, STATE.flowbank, STATE.xunbao, STATE.xxl, STATE.stats, STATE.policy&&STATE.policy.xxl],()=>{
    updateBassFodder();renderFlowbank();renderDaily();renderLuck();renderXunbao();renderXxl();renderPolicy();
  });
  checkSlice('seckill','seckill',[STATE.seckill_sessions, STATE.token_life, STATE.policy&&STATE.policy.seckill],()=>{
    renderPolicy();renderSeckillInfo();
  });
  checkSlice('sched','sched',[STATE.sched, STATE.sched_read_ok],updSched);
  checkSlice('settings','settings',[STATE.policy, STATE.settings, STATE.mail],()=>{renderPolicy();renderSettings();});
}

// ---------------- 统一轮询调度 + 自愈 ----------------
let _stateFail=0,_stateSig='',_pollTimer=null,_stateInFlight=false;
const FAIL_RELOAD_KEY='cmcc_fail_reloaded';

function schedulePoll(ms){clearTimeout(_pollTimer);_pollTimer=setTimeout(tick,ms);}
export function kickPoll(fast){schedulePoll(fast?160:1200);}

export async function pullState(){
  if(_stateInFlight)return;
  _stateInFlight=true;
  let rawData;
  try{
    try{rawData=await(await fetch('/api/state')).json();}
    catch(e){
      _stateFail++;
      _stateSig='';
      setHtml('envBox',html`<div class="envbar">连不上控制台服务（${e&&e.message||e}）。请确认程序还在运行，再刷新本页。</div>`);
      let reloaded=false;
      try{reloaded=sessionStorage.getItem(FAIL_RELOAD_KEY)==='1';}catch(_){}
      if(_stateFail>=5&&!reloaded){
        try{sessionStorage.setItem(FAIL_RELOAD_KEY,'1');}catch(_){}
        location.reload();
      }
      return;
    }
    _stateFail=0;
    try{sessionStorage.removeItem(FAIL_RELOAD_KEY);}catch(_){}
    const sig=JSON.stringify(rawData);
    store.state=rawData;
    renderVersion();
    if(sig===_stateSig){
      setBusy(rawData.task.running,rawData.task.label);
      return;
    }
    _stateSig=sig;
    renderSmart();
    setBusy(rawData.task.running,rawData.task.label);
  }finally{_stateInFlight=false;}
}

async function tick(){
  const hidden=document.visibilityState==='hidden';
  await Promise.all([pullState(), pullLogs()]);
  const STATE=store.state, cap=getCap();
  const running=!!((STATE&&STATE.task&&STATE.task.running)||store.lastRunning||(cap&&cap.armed));
  schedulePoll(hidden?60000:(running?1000:15000));
}

// ---------------- 集中式事件委托 ----------------
const CLICK_ACTIONS={
  'nav':(el)=>nav(el),
  'nav-to':(el,ev)=>{if(ev&&el.tagName==='A')ev.preventDefault();navTo(el.dataset.p);},
  'nav-new-account':()=>{navTo('token');openNewAccount();},
  'goto-about':()=>gotoAbout(),
  'goto-settings':()=>gotoSettings(),
  'toggle-drawer':(el)=>toggleDrawer(el.dataset.force==='0'?false:undefined),
  'nav-drawer-logs':()=>{navTo('logs');toggleDrawer(false);},
  'toggle-appear':(_el,ev)=>toggleAppear(ev),
  'toggle-theme':()=>toggleTheme(),
  'zoom':(el)=>zoomBy(Number(el.dataset.delta)),
  'zoom-reset':()=>zoomReset(),
  'refresh-status':()=>refreshStatus(),
  'stop-task':()=>act({action:'stop'}),
  'clear-err':()=>clearErr(),
  'close-modal':()=>closeModal(),
  'run-all':(el)=>runAll(el.dataset.platform),
  'voucher-run':(el)=>voucherRun(el.dataset.key||undefined),
  'qwhd-run':(el)=>{
    const body={action:'qwhd_run',key:el.dataset.key};
    if(el.dataset.dry)body.dry=1;
    if(el.dataset.claim)body.claim=1;
    act(body);
  },
  'bass-card-run':(el)=>{
    const body={action:'bass_run',key:el.dataset.key};
    if(el.dataset.dry)body.dry=1;
    if(el.dataset.claim)body.claim=1;
    if(el.dataset.roll)body.roll=1;
    act(body);
  },
  'bass-claim':(el)=>act({action:'bass_run',key:el.dataset.key,claim:1}),
  'toggle-aw-all':()=>toggleAwAll(),
  'open-new-account':()=>openNewAccount(),
  'save-new-account':()=>saveNewAccount(),
  'edit-account':(el)=>editAccount(el.dataset.file),
  'save-account':(el)=>saveAccount(el.dataset.file),
  'clear-notify':(el)=>cf('清除该账号的推送配置？',()=>clearNotify(el.dataset.file)),
  'import-har':()=>importHar(),
  'har-scan':(el)=>act({action:'har_import',...(el.dataset.latest?{latest:1}:{})},afterHar),
  'bass-tab':(el)=>setBassTab(el.dataset.bt),
  'run-luck':(el)=>runLuck(el.dataset.mode),
  'run-bass':()=>runBass(),
  'run-bass-probe':()=>runBassProbe(),
  'run-xunbao':(el)=>runXunbao(el.dataset.mode),
  'run-xxl':()=>runXxl(),
  'run-xxl-all':()=>runXxlAll(),
  'xxl-query':()=>xxlQuery(),
  'detect-level':()=>detectLevel(),
  'run-seckill':()=>runSeckill(),
  'seckill-query':()=>seckillQuery(),
  'seckill-once':()=>seckillOnce(),
  'run-games':(el)=>runGames(el.dataset.mode),
  'apply-all-sched':()=>applyAllSched(),
  'unreg-all-sched':()=>cf('注销全部计划任务？注销后电脑不再自动签到。',unregAllSched),
  'refresh-sched':()=>refreshSched(),
  'reg-sched':(el)=>regSched(el.dataset.kind),
  'unreg-sched':(el)=>cf(`注销「${el.dataset.cn}」？注销后它不再自动运行。`,()=>unregSched(el.dataset.name)),
  'open-log':(el)=>openLog(el.dataset.kind),
  'toggle-modal-err':()=>toggleModalErr(),
  'copy-modal-log':()=>copyModalLog(),
  'save-settings':()=>saveSettings(),
  'save-mail':()=>saveMail(),
  'save-policy':()=>savePolicy(),
  'act-simple':(el)=>act({action:el.dataset.action}),
  'check-update':()=>checkUpdate(true),
  'skip-update':()=>skipUpdate(),
  'copy-logs':()=>copyLogs(),
  'clear-console':()=>clearConsole(),
  'log-level':(el)=>setLogLevel(el.dataset.lvl,el),
  'scroll-logs-bottom':()=>scrollLogsBottom(),
  'copy-cap-box':(el)=>copyCapBox(el),
};

const CHANGE_ACTIONS={
  'aw-filter':()=>onAwFilter(),
  'bass-fodder':()=>updateBassFodder(),
  'feed-all':(el)=>feedAllToggle(el.dataset.prefix),
  'pick-stage':(el)=>pickStage(el.dataset.which),
  'mail-provider':()=>mailProvider(),
  'auto-update':(el)=>toggleAutoUpdate(el.checked),
};

const INPUT_ACTIONS={
  'log-filter':()=>renderLogList(),
  'sched-dirty':(el)=>{el.dataset.dirty='1';},
};

let _eventsBound=false;
export function bindEvents(){
  if(_eventsBound)return;
  _eventsBound=true;
  document.addEventListener('click',e=>{
    const el=e.target&&e.target.closest?e.target.closest('[data-act]'):null;
    if(!el||el.disabled)return;
    const fn=CLICK_ACTIONS[el.dataset.act];
    if(fn)fn(el,e);
  });
  document.addEventListener('change',e=>{
    const el=e.target&&e.target.closest?e.target.closest('[data-change]'):null;
    if(!el)return;
    const fn=CHANGE_ACTIONS[el.dataset.change];
    if(fn)fn(el,e);
  });
  document.addEventListener('input',e=>{
    const el=e.target&&e.target.closest?e.target.closest('[data-input]'):null;
    if(!el)return;
    const fn=INPUT_ACTIONS[el.dataset.input];
    if(fn)fn(el,e);
  });
}

let _lastActivity=Date.now(), _timersBound=false;
export function boot(opts={}){
  registerHooks({pullLogs,pullState,kickPoll:opts.autoPoll===false?()=>{}:kickPoll,navTo,renderNavBadges,refreshSched});
  initCoreUi();
  initBassTab();
  initDirtyForms();
  initHarDrop();
  initManualBass();
  initLogs();
  bindEvents();

  const h=(typeof location!=='undefined'&&location.hash)?location.hash.slice(1):'';
  if(h)navTo(h);

  if(typeof window!=='undefined'){
    window.__cmcc={
      get state(){return store.state;},
      set state(v){store.state=v;},
      render,renderSmart,setBusy,act,navTo,pullState,pullLogs,
    };
  }

  if(opts.autoPoll!==false&&!_timersBound){
    _timersBound=true;
    ['click','keydown','mousemove','wheel'].forEach(ev=>
      document.addEventListener(ev,()=>{_lastActivity=Date.now();},{passive:true}));
    document.addEventListener('visibilitychange',()=>{
      if(document.visibilityState==='visible')kickPoll(true);
    });
    tick();
    initUpdate({autoCheck:true});
    setInterval(()=>{
      if(!store.state)return;
      document.querySelectorAll('.tl-age[data-age-issued]').forEach(el=>{
        const a=tokenAge({issued_at:el.dataset.ageIssued});
        if(a!=null){el.textContent=a+' 分钟';setTone(el,a>=25?'warn':'inherit');}
      });
      document.querySelectorAll('.sk-left[data-exp-at]').forEach(el=>{
        const l=sessLeftMin({expires_at:el.dataset.expAt});
        if(l==null)return;
        if(l<=0){el.textContent='已过期';setTone(el,'bad');}
        else if(l<60){el.textContent='剩 '+l+' 分钟';setTone(el,'warn');}
        else{el.textContent='剩 '+Math.round(l/60)+' 小时';setTone(el,'ok');}
      });
      renderSkWindow();
    },30000);
    setInterval(()=>{
      const STATE=store.state;
      if(STATE&&STATE.task&&STATE.task.running)return;
      if(Date.now()-_lastActivity<60000)return;
      location.reload();
    },6*60*60*1000);
    window.addEventListener('error',e=>{
      try{toast('页面脚本出错：'+(e.message||'未知'),true);}catch(_){}
    });
  }else if(opts.autoPoll===false){
    initUpdate({autoCheck:false});
  }
}

if(!globalThis.__CMCC_NO_AUTO_BOOT__)boot();
