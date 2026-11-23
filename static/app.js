// ---------------- 页面路由 & 分片按需渲染 ----------------
function nav(b){
  if(!b)return;
  closeAppear();
  document.querySelectorAll('nav button').forEach(x=>{x.classList.remove('on');x.removeAttribute('aria-current');});
  b.classList.add('on');
  b.setAttribute('aria-current','page');
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('on'));
  const p=b.dataset.p;
  _activePage=p;
  $('p-'+p).classList.add('on');
  $('pageTitle').textContent='签到控制台 / '+(TITLES[p]||'');
  flushPage(p);
  try{history.replaceState(null,'','#'+p);}catch(e){}
}
function navTo(p){const b=document.querySelector('nav button[data-p="'+p+'"]');if(b)nav(b);}

let _builtOnce=false,_sig={},_dirtyPages={};
function sliceSig(k,val){
  const s=JSON.stringify(val===undefined?null:val);
  if(_sig[k]===s)return false;
  _sig[k]=s;
  return true;
}

function render(){
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
  renderPolicy();
  renderSettings();
  renderSeckillInfo();
  updSched();
  _builtOnce=true;
  _sig={
    env:JSON.stringify([STATE.env, (STATE.accounts||[]).length, (STATE.raw_accounts||[]).length]),
    badges:JSON.stringify([STATE.token_life, STATE.raw_accounts, STATE.daily, STATE.xunbao, STATE.seckill_sessions, STATE.sched, STATE.sched_read_ok, STATE.task]),
    selects:JSON.stringify((STATE.raw_accounts||[]).map(a=>[a.key,a.file,a.plat,a.phone])),
    dash_todo:JSON.stringify([STATE.raw_accounts, STATE.token_life, STATE.seckill_sessions, STATE.daily, STATE.accounts, STATE.sched, STATE.sched_cn]),
    dash_kpi:JSON.stringify([STATE.stats, STATE.raw_accounts, STATE.token_life, STATE.awards, STATE.accounts]),
    dash_aw:JSON.stringify(STATE.awards),
    dash_eff:JSON.stringify(STATE.eff),
    token_accts:JSON.stringify([STATE.raw_accounts, STATE.stats, STATE.token_life]),
    bass:JSON.stringify([STATE.luck, STATE.daily, STATE.flowbank, STATE.xunbao, STATE.stats]),
    xxl:JSON.stringify(STATE.policy&&STATE.policy.xxl),
    seckill:JSON.stringify([STATE.seckill_sessions, STATE.token_life, STATE.policy&&STATE.policy.seckill]),
    sched:JSON.stringify([STATE.sched, STATE.sched_read_ok]),
    settings:JSON.stringify([STATE.policy, STATE.settings, STATE.mail])
  };
}

function flushPage(p){
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
    if(sliceSig('bass',[STATE.luck, STATE.daily, STATE.flowbank, STATE.xunbao, STATE.stats])){
      updateBassFodder();renderFlowbank();renderDaily();renderLuck();renderXunbao();
    }
  }else if(p==='xxl'){
    if(sliceSig('xxl',STATE.policy&&STATE.policy.xxl))renderPolicy();
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

function renderSmart(){
  if(!STATE)return;
  if(!_builtOnce){render();return;}
  if(sliceSig('env',[STATE.env, (STATE.accounts||[]).length, (STATE.raw_accounts||[]).length]))renderEnv();
  if(sliceSig('badges',[STATE.token_life, STATE.raw_accounts, STATE.daily, STATE.xunbao, STATE.seckill_sessions, STATE.sched, STATE.sched_read_ok, STATE.task]))renderNavBadges();
  if(sliceSig('selects',(STATE.raw_accounts||[]).map(a=>[a.key,a.file,a.plat,a.phone])))renderSelects();

  const checkSlice=(page,key,val,fn)=>{
    const s=JSON.stringify(val===undefined?null:val);
    if(_sig[key]===s)return;
    if(_activePage===page){_sig[key]=s;fn();}
    else{_dirtyPages[page]=true;}
  };
  checkSlice('dash','dash_todo',[STATE.raw_accounts, STATE.token_life, STATE.seckill_sessions, STATE.daily, STATE.accounts, STATE.sched, STATE.sched_cn],renderTodo);
  checkSlice('dash','dash_kpi',[STATE.stats, STATE.raw_accounts, STATE.token_life, STATE.awards, STATE.accounts],renderKpiAndCards);
  checkSlice('dash','dash_aw',STATE.awards,renderAwSection);
  checkSlice('dash','dash_eff',STATE.eff,renderEff);
  checkSlice('token','token_accts',[STATE.raw_accounts, STATE.stats, STATE.token_life],renderAccounts);
  checkSlice('bass','bass',[STATE.luck, STATE.daily, STATE.flowbank, STATE.xunbao, STATE.stats],()=>{
    updateBassFodder();renderFlowbank();renderDaily();renderLuck();renderXunbao();
  });
  checkSlice('xxl','xxl',STATE.policy&&STATE.policy.xxl,renderPolicy);
  checkSlice('seckill','seckill',[STATE.seckill_sessions, STATE.token_life, STATE.policy&&STATE.policy.seckill],()=>{
    renderPolicy();renderSeckillInfo();
  });
  checkSlice('sched','sched',[STATE.sched, STATE.sched_read_ok],updSched);
  checkSlice('settings','settings',[STATE.policy, STATE.settings, STATE.mail],()=>{renderPolicy();renderSettings();});
}

// ---------------- 结构化执行结果区捕获机（防抢跑） ----------------
let CAP=null;
function capStart(kind,then){
  CAP={kind:kind,buf:[],then:then||null,armed:false,seenActivity:false,startedAt:Date.now()};
  const box=$(kind+'Box');
  if(box){
    box.innerHTML='<div class="outbox"><div class="outbox-head"><span class="ob-badge run">执行中</span><span class="h">已发出执行请求 · 正在执行中…（完成后这里显示输出）</span></div></div>';
    try{box.scrollIntoView({behavior:'smooth',block:'nearest'});}catch(_){}
  }
}
function capArm(r){
  if(!r||!r.ok){CAP=null;return;}
  if(!CAP)return;
  CAP.armed=true;
  if(CAP.seenActivity&&!lastRunning)capEnd();
}
function capDrop(){CAP=null;}
function capPush(line){
  if(!CAP)return;
  const s=String(line).replace(/\s+$/,'');
  if(!s.trim()||s.startsWith('▶')||s.startsWith('■')||s.startsWith('$ '))return;
  CAP.seenActivity=true;
  CAP.buf.push(s);if(CAP.buf.length>300)CAP.buf.shift();
}
function copyCapBox(btn){
  const ob=btn&&btn.closest?btn.closest('.outbox'):null;
  const bd=ob?ob.querySelector('.outbox-body'):null;
  copyText(bd?bd.innerText:'','已复制执行结果');
}
function capEnd(){
  if(!CAP)return;
  const {kind,then,buf}=CAP;
  const box=$(kind+'Box');
  CAP=null;
  if(box){
    const errCnt=buf.filter(l=>colorize(l)==='err').length;
    const badgeCls=errCnt>0?'err':'ok';
    const badgeTxt=errCnt>0?`含 ${errCnt} 条异常`:'执行完成';
    const head=`最近一次执行 · ${new Date().toLocaleTimeString('zh-CN',{hour12:false})}（${buf.length} 行）`;
    const body=buf.length
      ? buf.map(l=>{const c=colorize(l);return `<div class="${c}">${esc(l)}</div>`;}).join('')
      : '<div class="dim">（本次没有输出）</div>';
    box.innerHTML=`<div class="outbox"><div class="outbox-head"><span class="ob-badge ${badgeCls}">${badgeTxt}</span><span class="h">${head}</span><button type="button" class="btn sm" data-nobusy onclick="copyCapBox(this)">复制</button></div><div class="outbox-body">${body}</div></div>`;
  }
  if(then)then(buf.slice());
}

// ---------------- 各活动手动执行入口 ----------------
async function runBass(){
  const warn=[];if($('bTasks').checked)warn.push('养成浏览任务（空报伪造）');if($('bFb').checked)warn.push('流量银行任务（空报伪造）');
  if(warn.length&&!await askConfirm('本次将执行伪造浏览类任务：\n- '+warn.join('\n- ')+'\n\n确定继续吗？',{okText:'确认执行',danger:true}))return;
  capStart('bass');
  act({action:'bass_run',key:V('bassRunKey'),dry:$('bDry').checked?1:0,claim:$('bClaim').checked?1:0,roll:$('bRoll').checked?1:0,
       feed:$('bFeedAll').checked?0:parseInt($('bFeed').value||0),
       feed_all:$('bFeedAll').checked?1:0,
       tasks:$('bTasks').checked?1:0,fb:$('bFb').checked?1:0,
       prize:$('bPrize')?V('bPrize'):'fee',
       auto_claim:$('bAuto').checked?1:0,continue_stage:$('bCont').checked?1:0})
    .then(capArm);
}
function runBassProbe(){
  capStart('bass');
  act({action:'bass_probe',key:V('bassRunKey')}).then(capArm);
}
function pickStage(which){
  if(which==='auto'&&$('bAuto').checked)$('bCont').checked=false;
  if(which==='cont'&&$('bCont').checked)$('bAuto').checked=false;
  syncStageUi();
}
function syncStageUi(){
  const a=$('bAuto'), c=$('bCont'), p=$('bPrize'), h=$('bStageHint');
  if(!a||!c)return;
  if(p)p.style.display=a.checked?'':'none';
  if(h)h.textContent=a.checked?'喂满时会领所选档位的奖品后继续喂'
    :(c.checked?'喂满时会放弃本阶奖品直接升阶（奖品作废）':'都不勾：快喂满时会停手等你决定');
}
// 手动执行区的勾选只管这一次：页面每次加载都回到默认，不让浏览器偷偷恢复
function resetManualBass(){
  ['bClaim','bRoll','bDry','bFeedAll','bAuto','bCont','bTasks','bFb'].forEach(id=>{const e=$(id);if(e)e.checked=false;});
  const f=$('bFeed');if(f){f.value=0;f.disabled=false;}
  const p=$('bPrize');if(p)p.value='fee';
  syncStageUi();
}
resetManualBass();
window.addEventListener('load',()=>setTimeout(resetManualBass,0));
window.addEventListener('pageshow',e=>{if(e.persisted)resetManualBass();});

async function runGames(mode){
  capStart('gm');
  const r=await act({action:'games_run',file:V('gmFile'),dry:mode==='dry'?1:0,tasks:mode==='tasks'?1:0},
                    ()=>setTimeout(pullState,3000));
  capArm(r);
}
async function runXxl(){
  if(!await askConfirm('消消乐属于伪造游戏通关成绩，中奖靠服务端概率，确定要跑吗？',{okText:'开始执行',danger:true}))return;
  const x=((STATE&&STATE.policy)||{}).xxl||{};
  capStart('xxl');
  act({action:'xxl_run',file:V('xxlFile'),
       level:(x.level!=null?x.level:49),star:(x.star!=null?x.star:3),
       times:(x.times!=null?x.times:5),gap:(x.gap!=null?x.gap:12)})
    .then(capArm);
}
async function runXxlAll(){
  if(!await askConfirm('所有福建号并行跑消消乐（每号按策略局数），跑完顺手投寻宝骰子。确定？',{okText:'并行执行',danger:true}))return;
  const x=((STATE&&STATE.policy)||{}).xxl||{};
  capStart('xxl');
  act({action:'xxl_all',times:(x.times!=null?x.times:5)}).then(capArm);
}
function xxlQuery(){capStart('xxl');act({action:'xxl_run',file:V('xxlFile'),dry:1}).then(capArm);}
function detectLevel(){
  capStart('xxl',lines=>{
    const m=lines.join('\n').match(/当前真实关卡:\s*(\d+)/);
    if(m)toast('当前真实关卡 '+m[1]+'（运行时自动使用，不用手填）');
    else toast('没读到关卡号（可能凭证失效），看下方输出',true);
  });
  act({action:'xxl_run',file:V('xxlFile'),dry:1}).then(capArm);
}
async function runSeckill(){
  const skk=(((STATE&&STATE.policy)||{}).seckill)||{}, at=(skk.at||'').trim(),
        n=new Date(), mins=n.getHours()*60+n.getMinutes();
  if(mins>=13*60&&!await askConfirm('今天的场次（12:00~13:00）已结束，现在出手会被拒。仍要继续吗？',{okText:'仍要继续'}))return;
  capStart('sk');
  act({action:'seckill_run',file:V('skFile'),at:at||''}).then(capArm);
}
function seckillQuery(){capStart('sk');act({action:'seckill_run',file:V('skFile'),dry:1}).then(capArm);}
function seckillOnce(){capStart('sk');act({action:'seckill_run',file:V('skFile'),once:1}).then(capArm);}

async function runAll(platform){
  const dry=$('dashDry').checked;
  if(platform==='all'&&!dry&&!await askConfirm('将执行「签到领福利」（含全部平台签到、大转盘、寻宝、拓展活动及消消乐伪造通关成绩），确定继续吗？',{okText:'开始执行',danger:true}))return;
  return act({action:'run_all',platform:platform,
    dry:dry, luck:platform==='all', games:platform==='all',
    xxl:platform==='all'});
}
function refreshStatus(){act({action:'refresh_status'});}

// ---------------- 日志过滤 / 复制 / 迷你抽屉 ----------------
const cons=$('console');
let LOG_ITEMS=[], _logLevel='all', _drawerOpen=false;
function makeLogNode(l){
  const d=document.createElement('div');
  d.innerHTML=`<span class="t">${esc(l.t)}</span><span></span>`;
  d.lastChild.textContent=l.line;
  if(l.cls)d.lastChild.className=l.cls;
  return d;
}
function matchLogItem(it){
  if(_logLevel==='err'&&it.cls!=='err')return false;
  if(_logLevel==='ok'&&it.cls!=='ok')return false;
  const kw=($('logFilter')?V('logFilter'):'').toLowerCase();
  if(kw&&!(it.line.toLowerCase().includes(kw)||it.t.toLowerCase().includes(kw)))return false;
  return true;
}
function _updateLogCount(shownLen){
  const el=$('logCount');if(!el)return;
  if(!LOG_ITEMS.length){el.textContent='';return;}
  const n=(shownLen==null)?cons.children.length:shownLen;
  el.textContent=n===LOG_ITEMS.length?`共 ${LOG_ITEMS.length} 行`:`筛选 ${n} / ${LOG_ITEMS.length} 行`;
}
function setLogLevel(lvl,btn){
  _logLevel=lvl;
  document.querySelectorAll('#p-logs .flt').forEach(b=>b.classList.toggle('on',b===btn));
  renderLogList();
}
function renderLogList(){
  if(!cons)return;
  const matched=LOG_ITEMS.filter(matchLogItem);
  const shown=matched.slice(-1200);
  const frag=document.createDocumentFragment();
  shown.forEach(it=>frag.appendChild(makeLogNode(it)));
  cons.innerHTML='';cons.appendChild(frag);
  cons.scrollTop=cons.scrollHeight;
  updateLogJump();
  _updateLogCount(matched.length);
  if($('consoleEmpty'))$('consoleEmpty').style.display=LOG_ITEMS.length?'none':'';
}
function updateLogJump(){
  const btn=$('logJump');if(!btn||!cons)return;
  const away=cons.scrollHeight-cons.scrollTop-cons.clientHeight>=50;
  btn.classList.toggle('on',away&&cons.children.length>0);
}
if(cons)cons.addEventListener('scroll',updateLogJump,{passive:true});
function scrollLogsBottom(){if(cons){cons.scrollTop=cons.scrollHeight;updateLogJump();}}
function copyLogs(){
  const txt=LOG_ITEMS.filter(matchLogItem).map(it=>`${it.t} ${it.line}`).join('\n');
  copyText(txt,'已复制控制台日志');
}
function renderDrawer(){
  const dr=$('logDrawer'), bd=$('ldBody');
  if(!dr||!bd||!_drawerOpen)return;
  const recent=LOG_ITEMS.slice(-10);
  if(!recent.length){bd.innerHTML='<div>（暂无输出）</div>';return;}
  const frag=document.createDocumentFragment();
  recent.forEach(it=>frag.appendChild(makeLogNode(it)));
  bd.innerHTML='';bd.appendChild(frag);
  bd.scrollTop=bd.scrollHeight;
}
function toggleDrawer(force){
  const dr=$('logDrawer');if(!dr)return;
  _drawerOpen=(force===undefined)?!_drawerOpen:!!force;
  dr.classList.toggle('on',_drawerOpen);
  if(_drawerOpen)renderDrawer();
}

// ---------------- 统一轮询调度 + 轻量计时 + 自愈 ----------------
let _stateFail=0,_stateSig='',_pollTimer=null,_stateInFlight=false,_logsInFlight=false;
const FAIL_RELOAD_KEY='cmcc_fail_reloaded';

function schedulePoll(ms){clearTimeout(_pollTimer);_pollTimer=setTimeout(tick,ms);}
function kickPoll(fast){schedulePoll(fast?160:1200);}

async function pullState(visible){
  if(_stateInFlight)return;
  _stateInFlight=true;
  let raw;
  try{
    try{raw=await(await fetch('/api/state')).json();}
    catch(e){
      _stateFail++;
      _stateSig='';
      $('envBox').innerHTML='<div class="envbar">连不上控制台服务（'+esc(e&&e.message||e)+
        '）。请确认程序还在运行，再刷新本页。</div>';
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
    const sig=JSON.stringify(raw);
    STATE=raw;
    if(sig===_stateSig){
      setBusy(STATE.task.running,STATE.task.label);
      return;
    }
    _stateSig=sig;
    renderSmart();
    setBusy(STATE.task.running,STATE.task.label);
  }finally{_stateInFlight=false;}
}

async function pullLogs(){
  if(_logsInFlight)return;
  _logsInFlight=true;
  try{
    let r;
    try{r=await(await fetch('/api/logs?since='+logSeq)).json();}catch(e){return;}
    if(r.lines&&r.lines.length){
      const atBottom=cons?(cons.scrollHeight-cons.scrollTop-cons.clientHeight<45):true;
      const hasFilter=_logLevel!=='all'||($('logFilter')&&V('logFilter'));
      r.lines.forEach(l=>{
        logSeq=l.seq;capPush(l.line);
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
      if($('consoleEmpty'))$('consoleEmpty').style.display='none';
      if(_drawerOpen)renderDrawer();
    }
    const wasRunning=lastRunning; lastRunning=!!r.running;
    if(CAP&&(r.running||(r.lines&&r.lines.length)))CAP.seenActivity=true;
    setBusy(r.running,r.label);
    renderNavBadges();
    if(!r.running){
      if(CAP&&CAP.armed&&(CAP.seenActivity||Date.now()-CAP.startedAt>1500)){
        capEnd();
      }
      if(wasRunning){
        pullState(!0);
        if(_pendingSchedRefresh){_pendingSchedRefresh=false;refreshSched(true);}
      }
    }
  }finally{_logsInFlight=false;}
}

function clearConsole(){LOG_ITEMS=[];if(cons)cons.innerHTML='';if($('consoleEmpty'))$('consoleEmpty').style.display='';_updateLogCount(0);updateLogJump();renderDrawer();}

(function(){const h=location.hash.slice(1);const b=h&&document.querySelector('nav button[data-p="'+h+'"]');if(b)nav(b);})();

let _lastActivity=Date.now();
['click','keydown','mousemove','wheel'].forEach(ev=>
  document.addEventListener(ev,()=>{_lastActivity=Date.now();},{passive:true}));
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='visible')kickPoll(true);
});
async function tick(){
  const hidden=document.visibilityState==='hidden';
  await Promise.all([pullState(!hidden), pullLogs()]);
  const running=!!((STATE&&STATE.task&&STATE.task.running)||lastRunning||(CAP&&CAP.armed));
  schedulePoll(hidden?60000:(running?1000:15000));
}
tick();

// 每 30 秒原地刷新票据/会话分钟数与秒杀窗口提示，不重绘整页 DOM
setInterval(()=>{
  if(!STATE)return;
  document.querySelectorAll('.tl-age[data-age-issued]').forEach(el=>{
    const a=tokenAge({issued_at:el.dataset.ageIssued});
    if(a!=null){el.textContent=a+' 分钟';el.style.color=a>=25?'var(--warn)':'inherit';}
  });
  document.querySelectorAll('.sk-left[data-exp-at]').forEach(el=>{
    const l=sessLeftMin({expires_at:el.dataset.expAt});
    if(l==null)return;
    if(l<=0){el.textContent='已过期';el.style.color='var(--bad)';}
    else if(l<60){el.textContent='剩 '+l+' 分钟';el.style.color='var(--warn)';}
    else{el.textContent='剩 '+Math.round(l/60)+' 小时';el.style.color='var(--ok)';}
  });
  renderSkWindow();
},30000);

// 页面自愈：开太久（>6 小时）且没任务在跑，重载清一次堆内存
setInterval(()=>{
  if(STATE&&STATE.task&&STATE.task.running)return;
  if(Date.now()-_lastActivity<60000)return;
  location.reload();
},6*60*60*1000);

window.addEventListener('error',e=>{
  try{toast('页面脚本出错：'+(e.message||'未知'),true);}catch(_){}
});
