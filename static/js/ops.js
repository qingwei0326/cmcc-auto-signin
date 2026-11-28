import {
  $, V, store, hooks, numVal, html, joinHtml, setHtml, setHidden,
  colorize, copyText, toast, showErr, clearErr, askConfirm, openModal, act,
} from './core.js';

// ---------------- 定时任务：真实状态表 & 日志弹层 ----------------
export const SCHED=[
  {kind:'morning',   cn:'晨间一站式',  name:'MobileMorning',        bat:'run_morning.bat',        desc:'签到 + 消消乐 + 拓展 + 豆苗 + 领券 + 秒杀（一张票跑完）'},
  {kind:'daily',     cn:'每日奖励',    name:'MobileDailyReward',    bat:'run_daily_reward.bat',   desc:'每小时补签到 + 领三档奖励（间隔 60 分钟）', interval:'每小时'},
  {kind:'mail',      cn:'邮箱收 HAR',  name:'MobileMailWatch',      bat:'run_mail_watch.bat',     desc:'每 10 分钟查收 HAR 邮件并自动导入', interval:'每10分'}
];
export const SCHED_PLAN=[["morning","11:40"],["daily","06:00"],["mail","00:00"]];

export function codeText(c,kind){
  if(c===0)return html`<span class="tone-ok">成功</span>`;
  if(c===267011)return html`<span class="tone-warn">从未运行</span>`;
  if(c===267009)return html`<span class="tone-acc">运行中</span>`;
  if(c===267014)return html`<span class="tone-sub">已中止</span>`;
  if(c===267010)return html`<span class="tone-sub">已禁用</span>`;
  if(c==null)return '—';
  const link=kind?html` <button type="button" class="link-btn" data-nobusy data-act="open-log" data-kind="${kind}">看日志</button>`:'';
  return c===1?html`<span class="tone-bad" title="退出码 1：多为票据失效">失败</span>${link}`
    :html`<span class="tone-bad">异常 ${c}</span>${link}`;
}

export function buildSched(){
  const box=$('schedBox'); if(!box||box.dataset.done)return;
  setHtml(box,html`<div class="tablewrap"><table class="stab"><thead><tr><th>任务</th>
    <th class="col-time">触发时刻</th><th class="col-state">状态</th>
    <th class="col-next">下次运行</th><th class="col-last">上次结果</th><th class="col-ops">操作</th></tr></thead><tbody>${
    SCHED.map(t=>html`<tr>
      <td><div class="nm">${t.cn}${t.interval?html`<span class="sched-tag" title="重复型任务：触发时刻表示起点">${t.interval}</span>`:''}</div><div class="sub">${t.desc}</div>
          <div class="sub" title="Windows 计划任务名与启动脚本">${t.name} · ${t.bat}</div></td>
      <td><input class="inp inp-sm sched-inp" id="st_${t.kind}" placeholder="HH:MM"
                 title="${t.interval?'起点时刻（注册若已过会自动从当前+2分钟开始重复）':'每日定时触发时刻 HH:MM'}"
                 data-input="sched-dirty"></td>
      <td id="sc_${t.kind}">—</td>
      <td id="sn_${t.kind}">—</td>
      <td id="sl_${t.kind}">—</td>
      <td class="col-ops"><button type="button" class="btn pri sm" data-act="reg-sched" data-kind="${t.kind}">注册/更新</button>
          <button type="button" class="btn sm" data-nobusy data-act="open-log" data-kind="${t.kind}">日志</button>
          <button type="button" class="btn sm danger" data-act="unreg-sched" data-name="${t.name}" data-cn="${t.cn}">注销</button></td>
    </tr>`)
  }</tbody></table></div>`);
  box.dataset.done='1';
}

export function updSched(){
  const STATE=store.state||{};
  buildSched();
  const list=STATE.sched||[];
  SCHED.forEach(t=>{
    const s=list.find(x=>x.name===t.name);
    const e=$('st_'+t.kind);
    if(e&&!e.dataset.dirty&&document.activeElement!==e&&s&&s.time&&e.value!==s.time)e.value=s.time;
    const c=$('sc_'+t.kind), n=$('sn_'+t.kind), l=$('sl_'+t.kind);
    if(c)setHtml(c,s?(s.state==='Disabled'?html`<span class="tone-warn">已禁用</span>`:html`<span class="tone-ok">已注册</span>`)
      :(STATE.sched_read_ok==null?html`<span class="tone-sub">读取中</span>`:html`<span class="tone-bad">未注册</span>`));
    if(n)n.textContent=s&&s.next?s.next:'—';
    if(l)setHtml(l,s?(s.last?html`<div class="sub">${s.last}</div><div>${codeText(s.code,t.kind)}</div>`:codeText(s.code,t.kind)):'—');
  });
  const box=$('schedBox'); if(!box)return;
  let tip=box.querySelector('.schedtip');
  let tipCls='', tipContent=null;
  if(!list.length){
    if(STATE.sched_read_ok===false){
      tipCls='warnbox schedtip';
      tipContent=html`读不到任务状态（PowerShell 可能被策略拦或超时）。点上方「刷新状态」重试；不影响已注册的任务照常运行。`;
    }else if(STATE.sched_read_ok==null){
      tipCls='infobox schedtip';
      tipContent=html`正在读取计划任务状态…（首次要几秒，读到后自动更新）`;
    }else{
      tipCls='infobox schedtip';
      tipContent=html`还没有注册任何计划任务 —— 点上方「一键配置全部（推荐方案）」一次配好 ${SCHED.length} 个，电脑才会每天自动跑。`;
    }
  }
  if(!tipContent){if(tip)tip.remove();return;}
  if(!tip){tip=document.createElement('div');box.appendChild(tip);}
  tip.className=tipCls;
  setHtml(tip,tipContent);
}

export async function refreshSched(silent){
  const r=await act({action:'sched_refresh'});
  if(r&&r.sched&&store.state){store.state.sched=r.sched;updSched();if(!silent)toast('已刷新任务状态');}
}

export async function applyAllSched(){
  const cn=k=>(SCHED.find(x=>x.kind===k)||{}).cn||k;
  const desc=SCHED_PLAN.map(([k,t])=>`${cn(k)}　${t}`).join('\n');
  if(!await askConfirm(`将按推荐方案重新注册 ${SCHED.length} 个计划任务，会覆盖当前触发时刻：\n\n`+desc+
              '\n\n注意：「晨间一站式」那一趟含消消乐（伪造通关成绩）。\n\n继续吗？',{okText:'全部注册'}))return;
  const r=await act({action:'sched_apply_all',plan:SCHED_PLAN});
  if(r&&r.ok)store.pendingSchedRefresh=true;
}
export async function unregAllSched(){
  const r=await act({action:'sched_unreg_all'});
  if(r&&r.ok)store.pendingSchedRefresh=true;
}
export async function regSched(kind){
  const el=$('st_'+kind), t=el?el.value.trim():'';
  const m=/^(\d{1,2}):(\d{2})$/.exec(t);
  if(!m||+m[1]>23||+m[2]>59){toast('触发时刻格式应为 HH:MM（00:00~23:59）',true);return;}
  el.dataset.dirty='';
  const r=await act({action:'sched_reg',kind:kind,time:t});
  if(r&&r.ok)store.pendingSchedRefresh=true;
}
export async function unregSched(name){
  const r=await act({action:'sched_unreg',name:name});
  if(r&&r.ok)store.pendingSchedRefresh=true;
}

let _modalLogLines=[], _modalLogErrOnly=false;
export function renderModalLogLines(){
  const pre=$('modalLogPre');if(!pre)return;
  const shown=_modalLogErrOnly?_modalLogLines.filter(l=>colorize(l)==='err'):_modalLogLines;
  setHtml(pre,shown.length
    ? joinHtml(shown.map(l=>html`<span class="${colorize(l)}">${l}</span>`),'\n')
    : '（没有符合条件的日志行）');
  const box=pre.parentElement;if(box)box.scrollTop=box.scrollHeight;
}
export function toggleModalErr(){
  _modalLogErrOnly=!_modalLogErrOnly;
  const btn=$('mErrBtn');if(btn)btn.classList.toggle('on',_modalLogErrOnly);
  renderModalLogLines();
}
export function copyModalLog(){copyText(_modalLogLines.join('\n'),'已复制任务日志');}
export async function openLog(kind){
  const STATE=store.state;
  const name=(STATE&&STATE.sched_logs||{})[kind];
  if(!name){toast('不知道这个任务对应哪个日志',true);return;}
  _modalLogErrOnly=false;
  openModal('任务日志 · '+name,html`<div class="infobox">正在读取…</div>`);
  let r;
  try{r=await(await fetch('/api/tasklog?name='+encodeURIComponent(name)+'&tail=300')).json();}
  catch(e){r={err:String(e&&e.message||e)};}
  if(r.err){setHtml('mBody',html`<div class="warnbox">读取失败：${r.err}</div>`);return;}
  if(r.missing){setHtml('mBody',html`<div class="warnbox">还没有这个日志文件 —— 该任务尚未运行过。可以在「定时任务」页点该行「注册/更新」，或等它到点自动跑。</div>`);return;}
  _modalLogLines=r.lines||[];
  setHtml('mBody',html`<div class="frow mb-10">
    <span class="fbhead m-0">显示最后 ${_modalLogLines.length} 行（共 ${Math.round((r.size||0)/1024)} KB）· logs\\${name}</span>
    <div class="modal-log-ops">
      <button type="button" class="btn sm flt" id="mErrBtn" data-nobusy data-act="toggle-modal-err">只看错误</button>
      <button type="button" class="btn sm" data-nobusy data-act="copy-modal-log">复制</button>
      <button type="button" class="btn sm" data-nobusy data-act="open-log" data-kind="${kind}">刷新</button>
    </div></div>
    <div class="log-pre"><pre id="modalLogPre"></pre></div>`);
  renderModalLogLines();
}

// ---------------- 设置与策略表单：Dirty State 同步 ----------------
const _dirtyForms={policy:false,settings:false,mail:false};
const _dirtyBadges={policy:'pDirty',settings:'sDirty',mail:'mDirty'};

export function markFormDirty(form,on){
  _dirtyForms[form]=!!on;
  const b=$(_dirtyBadges[form]);
  if(b)b.classList.toggle('on',!!on);
}

function _isEditingPanel(panelId){
  const p=$(panelId);
  return !!(p&&document.activeElement&&p.contains(document.activeElement));
}

export function initDirtyForms(){
  const groups={
    settings:['sBark','sSendkey','sPushplus'],
    mail:['mEnabled','mUser','mPass','mProvider','mHost','mPort','mKeyword','mSender'],
    policy:['pFeed','pFeedAll','pFeedHour','pFull','pPrize','pLuck','pLuckTasks','pXunbao',
            'pTasks','pFb','pCouponWorld','sTasks','sStar','sTimes','sGap','sAt']
  };
  Object.entries(groups).forEach(([form,ids])=>{
    ids.forEach(id=>{
      const el=$(id); if(!el||el.dataset.dirtyBound)return;
      el.dataset.dirtyBound='1';
      const ev=(el.type==='checkbox'||el.tagName==='SELECT')?'change':'input';
      el.addEventListener(ev,()=>markFormDirty(form,true));
    });
  });
}

export function renderPolicy(){
  const STATE=store.state;
  const p=(STATE&&STATE.policy)||{}, b=p.bass||{};
  if(!$('pFeed'))return;
  if(!_dirtyForms.policy&&!_isEditingPanel('panelSetPolicy')){
    $('pFeed').value=(b.feed!=null?b.feed:0);
    $('pFeedAll').checked=!!b.feed_all;
    feedAllToggle('p');
    $('pFeedHour').value=(b.feed_hour!=null?b.feed_hour:12);
    $('pFull').value=b.on_stage_full||'stop';
    $('pPrize').value=b.prize||'fee';
    $('pTasks').checked=!!b.tasks;
    $('pFb').checked=!!b.fb_tasks;
    if($('pLuck'))$('pLuck').checked=(p.luck!==false);
    if($('pLuckTasks'))$('pLuckTasks').checked=(p.luck_tasks!==false);
    if($('pXunbao'))$('pXunbao').checked=(p.xunbao!==false);
    if($('pCouponWorld'))$('pCouponWorld').checked=(p.coupon_world!==false);
    const x=p.xxl||{};
    if($('sTasks'))$('sTasks').checked=(x.tasks!==false);
    if($('sStar'))$('sStar').value=(x.star!=null?x.star:3);
    if($('sTimes'))$('sTimes').value=(x.times!=null?x.times:5);
    if($('sGap'))$('sGap').value=(x.gap!=null?x.gap:12);
    const sk=p.seckill||{};
    if($('sAt'))$('sAt').value=sk.at||'';
  }
  const xx=p.xxl||{}, skk=p.seckill||{};
  if($('xxlParams'))$('xxlParams').textContent=
    `关卡自动读取 · 星级 ${xx.star!=null?xx.star:3}`
    +` · ${xx.times!=null?xx.times:5} 局 · 间隔 ${xx.gap!=null?xx.gap:12} 秒`
    +(xx.tasks===false?'':' · 金币任务换体力');
  if($('skAtShow'))$('skAtShow').textContent=
    skk.at?skk.at+'（今天的时刻）':'留空则按服务端场次自动选';
  const f=Number(b.feed)||0;
  const fullCn=({stop:'停手',claim:'领奖续喂',continue:'直接升阶'})[b.on_stage_full]||b.on_stage_full;
  $('pMeta').textContent=b.feed_all
    ? `已保存：每天 ${b.feed_hour} 点喂到饲料用完（轮数自动算）· 阶段满时 ${fullCn}`
    : (f>0
      ? `已保存：每天 ${b.feed_hour} 点喂 ${f} 轮（约 ${f*250} 饲料）· 阶段满时 ${fullCn}`
      : '已保存：不自动喂养（只领三档奖励）');
}

export function feedAllToggle(p){
  const all=$(p+'FeedAll'), num=$(p+'Feed');
  if(!all||!num)return;
  num.disabled=all.checked;
  if(all.checked)num.value=0;
}

export async function savePolicy(){
  const body={action:'policy_save',
    luck:($('pLuck')?$('pLuck').checked:true),
    luck_tasks:($('pLuckTasks')?$('pLuckTasks').checked:true),
    xunbao:($('pXunbao')?$('pXunbao').checked:true),
    coupon_world:($('pCouponWorld')?$('pCouponWorld').checked:true),
    bass:{feed:$('pFeedAll').checked?0:numVal('pFeed',0),
          feed_all:$('pFeedAll').checked,
          feed_hour:numVal('pFeedHour',12),
          on_stage_full:V('pFull'),prize:V('pPrize'),
          tasks:$('pTasks').checked,fb_tasks:$('pFb').checked},
    xxl:{level:49,star:numVal('sStar',3),
         times:numVal('sTimes',5),gap:numVal('sGap',12),
         tasks:($('sTasks')?$('sTasks').checked:true)},
    seckill:{at:($('sAt')?V('sAt'):'')}};
  const r=await act(body);
  if(r&&r.ok&&r.policy){
    if(store.state)store.state.policy=r.policy;
    markFormDirty('policy',false);
    renderPolicy();
  }
}

export function renderSettings(){
  const STATE=store.state;
  const st=(STATE&&STATE.settings)||{};
  if(!$('sBark'))return;
  if(!_dirtyForms.settings&&!_isEditingPanel('panelSetPush')){
    $('sBark').value=st.bark_url||'';
    $('sSendkey').value=st.serverchan_sendkey||'';
    $('sPushplus').value=st.pushplus_token||'';
  }
  const on=[];
  if(st.bark_url)on.push('Bark');
  if(st.serverchan_sendkey)on.push('Server酱');
  if(st.pushplus_token)on.push('PushPlus');
  $('sMeta').textContent=on.length
    ? ('已开启：'+on.join(' + '))
    : '还没配推送方式（失败时不会通知手机）';
  renderMail();
}

export async function saveSettings(){
  const push={bark_url:V('sBark'),serverchan_sendkey:V('sSendkey'),pushplus_token:V('sPushplus')};
  const r=await act({action:'settings_save',push:push});
  if(r&&r.ok){
    if(store.state)store.state.settings=push;
    markFormDirty('settings',false);
    renderSettings();
  }
}

const MAIL_PRESETS={gmail:['imap.gmail.com',993],qq:['imap.qq.com',993],'163':['imap.163.com',993],'139':['imap.139.com',993]};
export function mailProvider(){
  const p=MAIL_PRESETS[$('mProvider').value];
  if(p){$('mHost').value=p[0];$('mPort').value=p[1];}
}

export function renderMail(){
  const STATE=store.state;
  const m=(STATE&&STATE.mail)||{};
  if(!$('mUser'))return;
  if(!_dirtyForms.mail&&!_isEditingPanel('panelSetMail')){
    const prov=m.provider||'qq';
    $('mEnabled').checked=!!m.enabled;
    $('mProvider').value=prov;
    $('mUser').value=m.user||'';
    $('mPass').value='';
    $('mHost').value=m.host||(MAIL_PRESETS[prov]||MAIL_PRESETS.qq)[0];
    $('mPort').value=m.port||993;
    $('mSender').value=m.sender||'';
    $('mKeyword').value=m.subject_keyword||'HAR';
  }
  $('mMeta').textContent=(m.enabled?('已启用 · '+(m.user||'未填邮箱')):'未启用')
    +(m.password?' · 授权码已保存':' · 尚未保存授权码');
}

export async function saveMail(){
  const r=await act({action:'mail_save',mail:{
    enabled:$('mEnabled').checked,provider:$('mProvider').value,
    host:V('mHost'),port:Number(V('mPort'))||993,
    user:V('mUser'),password:V('mPass'),sender:V('mSender'),subject_keyword:V('mKeyword')}});
  if(r&&r.ok&&store.state){
    store.state.mail={...(store.state.mail||{}),enabled:$('mEnabled').checked,
      provider:$('mProvider').value,host:V('mHost'),port:Number(V('mPort'))||993,user:V('mUser'),
      sender:V('mSender'),subject_keyword:V('mKeyword'),password:V('mPass')?1:(store.state.mail&&store.state.mail.password)};
    if($('mPass'))$('mPass').value='';
    markFormDirty('mail',false);
    renderMail();
  }
}

export function gotoSettings(){hooks.navTo('settings');}

// ---------------- HAR 拖拽 & XHR 二进制直传进度 ----------------
export function afterHar(r){
  const el=$('harOut'); if(!el)return;
  const rep=(r&&r.report)||[];
  const err=r&&r.err;
  if(!rep.length&&!err){setHidden(el,true);return;}
  setHidden(el,false);
  setHtml(el,html`${err?html`<div class="tone-bad">✗ ${err}</div>`:''}${rep.map(t=>html`<div>${t}</div>`)}`);
}

let harBusy=false,_droppedHarFile=null;
function setHarProgress(pct,msg){
  const p=$('harProg'), bar=$('harProgBar'), info=$('harFileInfo');
  if(p)p.classList.toggle('on',pct!=null);
  if(bar&&pct!=null)bar.style.width=Math.max(0,Math.min(100,pct))+'%';
  if(info&&msg)info.textContent=msg;
}
function _setHarBtnBusy(busy){
  const btn=$('btnHarImport');
  if(!btn)return;
  btn.disabled=!!busy;
  btn.textContent=busy?'导入中…':'导入所选文件';
}
function pickHarFile(f){
  if(!f)return;
  _droppedHarFile=f;
  const info=$('harFileInfo');
  if(info)info.textContent=`已选择：${f.name}（${(f.size/1048576).toFixed(2)} MB）— 点「导入所选文件」开始`;
}
export function initHarDrop(){
  const dz=$('harDrop'), inp=$('harFile');
  if(!dz||!inp||dz.dataset.dropBound)return;
  dz.dataset.dropBound='1';
  dz.addEventListener('click',e=>{if(e.target!==inp)inp.click();});
  dz.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();inp.click();}});
  inp.addEventListener('change',()=>{if(inp.files&&inp.files[0])pickHarFile(inp.files[0]);});
  ['dragenter','dragover'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.add('over');}));
  ['dragleave','drop'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.remove('over');}));
  dz.addEventListener('drop',e=>{
    const f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];
    if(!f)return;
    try{
      const dt=new DataTransfer();dt.items.add(f);inp.files=dt.files;
    }catch(_){}
    pickHarFile(f);
  });
}

export function importHar(){
  const inp=$('harFile');
  const f=(inp&&inp.files&&inp.files[0])||_droppedHarFile;
  if(!f){toast('先选择一个抓包文件（.har）',true);return;}
  if(harBusy){toast('上一份还在导入，等它出报告再点',true);return;}
  harBusy=true;
  _setHarBtnBusy(true);
  const mb=(f.size/1048576).toFixed(1);
  const el=$('harOut');
  if(el){setHidden(el,false);setHtml(el,html`<div class="muted">正在上传 ${f.name}（${mb}MB）…导入后会实测登录一次，请稍候</div>`);}
  setHarProgress(0,`准备上传 ${f.name}（${mb} MB）…`);

  const xhr=new XMLHttpRequest();
  const key=V('harKey');
  xhr.open('POST','/api/har_upload'+(key?('?key='+encodeURIComponent(key)):''),true);
  xhr.setRequestHeader('Content-Type','application/octet-stream');
  xhr.upload.onprogress=e=>{
    if(!e.lengthComputable)return;
    const pct=Math.round((e.loaded/e.total)*100);
    setHarProgress(pct,pct>=100?'上传完成，服务端正在解析并实测登录…':`上传中 ${pct}%（${(e.loaded/1048576).toFixed(1)} / ${mb} MB）`);
  };
  xhr.onload=()=>{
    harBusy=false;
    _setHarBtnBusy(false);
    setHarProgress(null,`已处理：${f.name}（${mb} MB）`);
    let r=null;
    try{r=JSON.parse(xhr.responseText||'{}');}
    catch(e){r={ok:false,err:'解析响应失败（HTTP '+xhr.status+'）'};}
    if(!r.ok){const m=r.err||'HAR 导入失败';toast(m,true);showErr(m);}
    else{clearErr();if(r.msg)toast(r.msg);}
    afterHar(r);
    hooks.pullLogs();hooks.pullState(true);
  };
  xhr.onerror=()=>{
    harBusy=false;
    _setHarBtnBusy(false);
    setHarProgress(null,'上传失败，请确认控制台服务仍在运行');
    const r={ok:false,err:'上传失败：网络断开或文件读取受限'};
    toast(r.err,true);showErr(r.err);afterHar(r);
  };
  xhr.onabort=()=>{harBusy=false;_setHarBtnBusy(false);setHarProgress(null,'上传已取消');};
  xhr.send(f);
}
