const $=id=>document.getElementById(id), V=id=>$(id).value.trim();
let logSeq=0, STATE=null, _activePage='dash';
const TITLES={dash:'总览',token:'凭证与账号',bass:'福建活动',xxl:'消消乐',seckill:'秒杀抢券',games:'拓展活动',sched:'定时任务',logs:'日志',settings:'设置'};
const PLAT_CN={qwhdhub:'全国签到',BASS:'福建活动'};

function numVal(id,defVal){
  const el=$(id); if(!el)return defVal;
  const raw=String(el.value||'').trim();
  if(raw==='')return defVal;
  const n=Number(raw);
  return Number.isFinite(n)?n:defVal;
}

function nav(b){
  if(!b)return;
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

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
// 内联事件里的字符串参数：先生成合法 JS 字面量，再整体做 HTML 属性转义。
// 不能只 esc(原始值)：HTML 解析会先把 &#39; 还原成引号，仍可能截断 JS 字符串。
function jsArg(v){return esc(JSON.stringify(v==null?'':String(v)));}

let toastTimer=null;
function toast(m,bad){
  const t=$('toast');if(!t)return;
  t.textContent=m;t.classList.toggle('bad',!!bad);t.style.display='block';
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.style.display='none',bad?6000:2600);
}
// 失败原因不能只靠一闪而过的 toast：留在页面顶部，直到下一次成功或手动关闭
function showErr(m){
  const b=$('errBox');if(!b)return;
  b.innerHTML='<button type="button" class="err-x" onclick="clearErr()">✕ 关闭</button>'+esc(m);
  b.style.display='block';
}
function clearErr(){const b=$('errBox');if(b){b.style.display='none';b.innerHTML='';}}

// 自定义深色确认弹窗（替代浏览器原生 window.confirm）
let _cfmRes=null,_cfmPrevFocus=null;
function closeCfm(ok){
  const m=$('cfmModal');if(!m||!m.classList.contains('on'))return;
  m.classList.remove('on');
  const res=_cfmRes, prev=_cfmPrevFocus;
  _cfmRes=null;_cfmPrevFocus=null;
  if(prev&&typeof prev.focus==='function'&&document.contains(prev)){try{prev.focus();}catch(_){}}
  if(res)res(!!ok);
}
function askConfirm(msg,opts){
  opts=opts||{};
  const m=$('cfmModal');if(!m)return Promise.resolve(false);
  if(_cfmRes)closeCfm(false);
  _cfmPrevFocus=document.activeElement;
  $('cfmTitle').textContent=opts.title||'确认操作';
  $('cfmMsg').textContent=msg;
  const okBtn=$('cfmOk'), cancelBtn=$('cfmCancel');
  okBtn.textContent=opts.okText||'确定';
  okBtn.className='btn '+(opts.danger?'danger-pri':'pri');
  m.classList.add('on');
  try{(opts.danger?cancelBtn:okBtn).focus();}catch(_){}
  return new Promise(resolve=>{
    _cfmRes=resolve;
    okBtn.onclick=()=>closeCfm(true);
    cancelBtn.onclick=()=>closeCfm(false);
  });
}
function cf(msg,fn,opts){askConfirm(msg,opts||{danger:true}).then(ok=>{if(ok)fn();});}

// 弹层：账号编辑 / 计划任务日志（记录与还原焦点）
let _modalPrevFocus=null;
function openModal(title,html){
  _modalPrevFocus=document.activeElement;
  $('mTitle').textContent=title;$('mBody').innerHTML=html;$('modal').classList.add('on');
  const x=$('modal').querySelector('.bh .x');if(x)try{x.focus();}catch(_){}
}
function closeModal(){
  const m=$('modal');if(!m||!m.classList.contains('on'))return;
  m.classList.remove('on');$('mBody').innerHTML='';
  const prev=_modalPrevFocus;_modalPrevFocus=null;
  if(prev&&typeof prev.focus==='function'&&document.contains(prev)){try{prev.focus();}catch(_){}}
}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if($('cfmModal')&&$('cfmModal').classList.contains('on'))closeCfm(false);
    else closeModal();
  }
});
document.addEventListener('click',e=>{
  if(e.target&&e.target.id==='modal')closeModal();
  if(e.target&&e.target.id==='cfmModal')closeCfm(false);
});

const TASK_ACTIONS=new Set([
  'refresh_status','run_all','qwhd_run','bass_run','bass_probe',
  'luck_run','xunbao_run','seckill_run','games_run','xxl_run','xxl_all',
  'sched_query','sched_reg','sched_apply_all','sched_unreg_all','sched_unreg'
]);
async function act(body,after){
  let r;
  try{
    const resp=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    r=await resp.json();
  }catch(e){r={ok:false,err:'连不上控制台服务：'+(e&&e.message||e)};}
  if(!r.ok){const m=r.err||'操作失败';toast(m,true);showErr(m);}
  else{clearErr();if(r.msg)toast(r.msg);}
  if(after)after(r);
  if(r&&r.ok&&TASK_ACTIONS.has(body&&body.action)){
    await Promise.all([pullLogs(),pullState(!0)]);
    kickPoll(true);
  }else{
    await pullState(!0);
  }
  return r;
}

// 有任务在跑时把动作按钮置灰，并明确告诉用户在跑什么（data-nobusy 按钮豁免）
function setBusy(running,label){
  document.querySelectorAll('.content .btn, .topbar .btn').forEach(b=>{
    if(b.hasAttribute('data-nobusy'))return;
    b.disabled=!!running;});
  const txt=running?('运行中 · '+(label||'')):'空闲';
  const p=$('taskPill');if(p){p.textContent=txt;p.classList.toggle('run',!!running);}
  const pt=$('taskPillTop');
  if(pt){pt.textContent=txt+(running?' ▾':'');pt.classList.toggle('run',!!running);}
  const ldt=$('ldTitle');
  if(ldt)ldt.textContent=running?('实时日志 · '+(label||'运行中')):'实时日志（最近 10 行）';
  const s=$('btnStop');if(s)s.disabled=!running;
}

function mask(p){return p.length===11?p.slice(0,3)+'****'+p.slice(7):p;}
// 金额 / 流量格式化：顶部 KPI 与到账台账共用
function fy(n){const v=Math.round((n||0)*100)/100;return Number.isInteger(v)?String(v):v.toFixed(2);}
function awFmt(mb){return mb>=1024?(mb/1024).toFixed(2)+'GB':((mb||0)?Math.round(mb)+'MB':'0MB');}
function tokenBadge(s){
  if(s==='ok')return '<span class="badge ok">凭证有效</span>';
  if(s==='expired')return '<span class="badge bad">凭证已过期</span>';
  if(s==='empty')return '<span class="badge na">凭证未配置</span>';
  if(s==='stale')return '<span class="badge na" title="超过凭证有效期没有新的验证，上次的结论不能再当作现在有效">待复验</span>';
  return '<span class="badge na">凭证未校验</span>';}
function ymd(d){const p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());}
function todayLocal(){return ymd(new Date());}
function last7(map){
  const out=[];
  for(let i=6;i>=0;i--){const d=new Date();d.setDate(d.getDate()-i);const k=ymd(d);
    out.push(map[k]||{date:k,flow_mb:0,tasks:0,feeds:0});}
  return out;
}
function signedBadge(date){
  return date===todayLocal()?'<span class="badge signed">今日已签</span>':'<span class="badge na">今日未签</span>';}
function fmtNum(v){return v==null?'—':v;}

// 实测 appToken 寿命 90~135 分钟；超过这个窗口还没再验过，上次那句「有效」
// 就不能当现在有效展示。BASS 的 bearer 按天走，给一整天余量。
const TOKEN_TTL_MIN={qwhdhub:135,BASS:1560};
function minsAgo(mdhm){
  const m=/^(\d{1,2})-(\d{1,2}) (\d{1,2}):(\d{2})$/.exec(String(mdhm||''));
  if(!m)return null;
  const now=new Date();
  const d=new Date(now.getFullYear(),+m[1]-1,+m[2],+m[3],+m[4]);
  if(d-now>864e5)d.setFullYear(d.getFullYear()-1);
  return (now-d)/60000;}
// 票据有效性以「每 2 分钟探测」为准：stats 里的 token_* 是上次跑任务时记下的，
// 可能已经是几小时前的结论。总览卡片和「凭证与账号」表都走这一个函数，免得两页说法不一。
function tokState(a,s){
  s=s||(STATE.stats||{})[a.key]||{};
  if(a.token_state==='empty')return 'empty';
  const live=a.plat==='qwhdhub'?(STATE.token_life||[]).find(x=>x.key===a.key):null;
  if(live&&live.state==='alive')return 'ok';
  if(live&&live.state==='expired')return 'expired';
  const field=a.plat==='qwhdhub'?'token_qwhd':'token_bass';
  const st=s[field]||'unchecked';
  const ages=[s[field+'_at'],s.last_run].map(minsAgo).filter(v=>v!==null);
  const age=ages.length?Math.min(...ages):null;
  if(st==='ok'&&age!==null&&age>(TOKEN_TTL_MIN[a.plat]||TOKEN_TTL_MIN.qwhdhub))return 'stale';
  return st;}

// ---------------- 分片按需渲染 ----------------
let _builtOnce=false,_sig={},_dirtyPages={};
function sliceSig(k,val){
  const s=JSON.stringify(val===undefined?null:val);
  if(_sig[k]===s)return false;
  _sig[k]=s;
  return true;
}

function renderEnv(){
  const env=STATE.env||{};
  $('envBox').innerHTML=env.ok===false
    ? `<div class="envbar"><b>任务暂时跑不了</b>\n${esc(env.msg)}</div>` : '';
  $('hsub').textContent=`${(STATE.accounts||[]).length} 个号码 · ${(STATE.raw_accounts||[]).length} 份配置`;
}

function renderSelects(){
  const accts=STATE.raw_accounts||[];
  const bassItems=accts.filter(a=>a.plat==='BASS').map(a=>({v:a.file,t:`${a.key} · ${mask(a.phone)}`}));
  fillSelKV('xxlFile',bassItems);
  fillSelKV('bassRunKey',accts.filter(a=>a.plat==='BASS').map(a=>({v:a.key,t:`${a.key} · ${mask(a.phone)}`})));
  const qItems=accts.filter(a=>a.plat==='qwhdhub').map(a=>({v:a.file,t:`${a.key} · ${mask(a.phone)}`}));
  fillSelKV('skFile',qItems);
  fillSelKV('gmFile',qItems);
  const hk=$('harKey');
  if(hk){
    const cur=hk.value, seen=new Set();
    const opts=accts.filter(a=>{if(!a.phone||seen.has(a.phone))return false;seen.add(a.phone);return true;})
      .map(a=>`<option value="${esc(a.key)}">${esc(a.key)} · ${esc(mask(a.phone))}</option>`).join('');
    hk.innerHTML='<option value="">自动识别（按文件里的号码）</option>'+opts;
    if(Array.from(hk.options).some(o=>o.value===cur))hk.value=cur;
  }
  updateBassFodder();
}

function updateBassFodder(){
  const bf=$('bFodder');if(!bf||!STATE)return;
  const bs=$('bassRunKey'), k=bs?bs.value:'';
  const st=(STATE.stats||{})[k]||{};
  bf.textContent=st.fodder!=null?(`当前 ${st.fodder}g`+(st.stage?` · ${st.stage}`:'')):'';
}

function renderKpiAndCards(){
  const S=STATE.stats||{}, accts=STATE.raw_accounts||[], aw=STATE.awards||{};
  let okTok=0,flowM=0;
  const days={};
  accts.forEach(a=>{const s=S[a.key]||{};
    flowM+=s.flow_mb_month||0;
    if(tokState(a,s)==='ok')okTok++;
    (s.history||[]).forEach(h=>{days[h.date]=Math.max(days[h.date]||0,h.flow_mb||0);});});
  // 并入到账台账按日流量（含转盘/寻宝/领奖），取较大值避免重复叠加也不漏抽奖
  Object.values(aw.by_day||{}).forEach(map=>{
    Object.keys(map||{}).forEach(d=>{days[d]=Math.max(days[d]||0,map[d]||0);});
  });
  const dks=Object.keys(days).sort().slice(-7);
  const spark=(map)=>{const vals=dks.map(d=>map[d]||0);
    if(vals.length<2||!vals.some(v=>v>0))return '';
    const mx=Math.max(...vals),n=vals.length;
    const pts=vals.map((v,i)=>`${(i*100/(n-1)).toFixed(1)},${(25-23*v/mx).toFixed(1)}`).join(' ');
    return `<svg class="spark" viewBox="0 0 100 28" preserveAspectRatio="none"><polyline points="${pts}"/></svg>`;};
  const spF=spark(days);
  const awFlowM=((aw.month)||{}).flow_mb||0;
  const flowMShow=Math.max(flowM,awFlowM);
  const awMonth=(aw.month||{});
  const feeM=awMonth.fee_count||0;
  const feeYuan=awMonth.fee_yuan||0;
  const feeAll=aw.fee_total||0;
  const feeYuanAll=aw.fee_yuan_total||0;
  const feeDay=aw.fee_day||{};
  const fdk=Object.keys(feeDay).sort().slice(-7);
  const spFee=(fdk.length<2||!fdk.some(d=>feeDay[d]>0))?'':
    (()=>{const vals=fdk.map(d=>feeDay[d]||0);const mx=Math.max(...vals),n=vals.length;
     const pts=vals.map((v,i)=>`${(i*100/(n-1)).toFixed(1)},${(25-23*v/mx).toFixed(1)}`).join(' ');
     return `<svg class="spark" viewBox="0 0 100 28" preserveAspectRatio="none"><polyline points="${pts}"/></svg>`;})();
  // 本日已得：单独做成和「本月已得」一样的卡片，放在隔壁一一对应
  const awToday=((STATE.awards||{}).today)||{};
  const dayFlow=awToday.flow_mb||0;
  const dayFee=awToday.fee_yuan||0, dayFeeCnt=awToday.fee_count||0;
  $('sumbar').innerHTML=`
    <div class="kpi"><div class="k">本月已得流量</div><div class="v">${flowMShow>=1024?(flowMShow/1024).toFixed(2)+'<small>GB</small>':flowMShow.toFixed(0)+'<small>MB</small>'}</div><div class="c">签到 / 领奖 / 转盘合计（付费流量包不计入）</div>${spF}</div>
    <div class="kpi"><div class="k">本日已得流量</div><div class="v">${dayFlow>=1024?(dayFlow/1024).toFixed(2)+'<small>GB</small>':dayFlow.toFixed(0)+'<small>MB</small>'}</div><div class="c">今天到手的流量，含签到 / 领奖 / 转盘</div></div>
    <div class="kpi"><div class="k">本月已得话费</div><div class="v">${fy(feeYuan)}<small>元</small></div><div class="c">共 ${feeM} 张无门槛券（话费手气红包不计入）· 历史 ${feeAll} 张 / ¥${fy(feeYuanAll)}</div>${spFee}</div>
    <div class="kpi"><div class="k">本日已得话费</div><div class="v">${fy(dayFee)}<small>元</small></div><div class="c">共 ${dayFeeCnt} 张无门槛券（话费手气红包不计入）</div></div>
    <div class="kpi"><div class="k">凭证可用</div><div class="v">${okTok}<small>/ ${accts.length} 份</small></div><div class="c">一个号码两份（全国 + 福建）</div></div>
    <div class="kpi"><div class="k">账号数</div><div class="v">${STATE.accounts.length}<small>个号码</small></div><div class="c">过期需重新抓包导入</div></div>`;
  // 运行记录统计条（分组渲染账号卡）
  $('cards').innerHTML=(STATE.accounts||[]).map(g=>{
    const plats=Object.entries(g.plats||{}).map(([plat,a])=>{
      const s=a.stats||{};
      const isQ=plat==='qwhdhub';
      const tkState=tokState(a,s);
      const hmap={}; (s.history||[]).forEach(h=>{hmap[h.date]={...h};});
      const tail=(a.phone||'').slice(-4);
      const awP=((aw.by_plat||{})[tail]||{})[plat]||{};
      const awD=((aw.by_day_plat||{})[tail]||{})[plat]||{};
      Object.keys(awD).forEach(d=>{
        const row=hmap[d]||{date:d,flow_mb:0,tasks:0,feeds:0};
        row.flow_mb=Math.max(row.flow_mb||0,awD[d]||0);
        hmap[d]=row;});
      const hist=last7(hmap);
      const hasData=hist.some(h=>(h.flow_mb||0)>0||(h.tasks||0)>0);
      const maxF=Math.max(1,...hist.map(h=>h.flow_mb||0));
      const bars=hist.map(h=>{
        const f=h.flow_mb||0, tk=h.tasks||0;
        const hgt=f>0?Math.max(10,(f/maxF)*100):(tk>0?45:0);
        const parts=[];
        if(f)parts.push(f+'MB');
        if(tk)parts.push(tk+'任务');
        const tip=`${h.date.slice(5)} · ${parts.length?parts.join(' · '):'无记录'}`;
        const cls=(f>0||tk>0)?'bar':'bar off';
        return `<div class="${cls}" tabindex="0" aria-label="${esc(tip)}" style="height:${hgt}%"><span class="tip">${esc(tip)}</span></div>`;
      }).join('');
      const dates=hist.map(h=>`<span>${esc(h.date.slice(8))}</span>`).join('');
      return `<div class="plat">
        <div class="pt">${isQ?'全国签到':'福建活动'} ${tokenBadge(tkState)}
          ${signedBadge(isQ?s.today_signed_qwhd:s.today_signed_bass)}
          <span class="stamp" title="这些数字是上次运行脚本时记下的，不是实时值">数据截至 ${esc(s.last_run||'从未运行')}</span></div>
        <div class="stats">
          ${isQ?`
          <div class="stat"><div class="k">本月已得</div><div class="v">${awFmt(Math.max(s.flow_mb_month||0,awP.flow_mb||0))}<small class="sum">${awP.fee_count?`¥${fy(awP.fee_yuan)} · ${awP.fee_count} 张券`:'签到 / 秒杀'}</small></div></div>
          <div class="stat"><div class="k">连签天数</div><div class="v">${fmtNum(s.sign_days)}</div></div>
          <div class="stat"><div class="k">本月任务</div><div class="v">${s.tasks_month||0}<small class="sum">累计 ${s.tasks_done_total||0}</small></div></div>
          <div class="stat"><div class="k">最近奖品</div><div class="v wrap">${esc(s.last_reward||'—')}</div></div>`
          :`
          <div class="stat"><div class="k">本月已得</div><div class="v">${awFmt(awP.flow_mb||0)}<small class="sum">${awP.fee_count?`¥${fy(awP.fee_yuan)} · ${awP.fee_count} 张券`:'养成 / 转盘 / 寻宝'}</small></div></div>
          <div class="stat"><div class="k">饲料余额</div><div class="v y">${fmtNum(s.fodder)}</div></div>
          <div class="stat"><div class="k">流量可兑</div><div class="v">${esc(s.fb_exchange||'—')}</div></div>
          <div class="stat"><div class="k">阶段进度</div><div class="v wrap">${esc(s.stage||'—')}</div></div>`}
        </div>
        <div class="acts">
          ${isQ?`<button type="button" class="btn sm pri" onclick="act({action:'qwhd_run',key:${jsArg(a.key)}})">签到</button>
            <button type="button" class="btn sm" onclick="act({action:'qwhd_run',key:${jsArg(a.key)},dry:1})">只读检查</button>
            <button type="button" class="btn sm" onclick="act({action:'qwhd_run',key:${jsArg(a.key)},claim:1})">签到+领奖</button>`
           :`<button type="button" class="btn sm pri" onclick="act({action:'bass_run',key:${jsArg(a.key)}})">签到</button>
            <button type="button" class="btn sm" onclick="act({action:'bass_run',key:${jsArg(a.key)},dry:1})">只读检查</button>
            <button type="button" class="btn sm" onclick="act({action:'bass_run',key:${jsArg(a.key)},claim:1,roll:1})">领奖+骰子</button>`}
        </div>
        ${hist.length&&hasData?`<div class="hist"><div class="t">近 7 天记录（流量 / 任务）</div><div class="bars">${bars}</div><div class="bardates">${dates}</div></div>`:`<div class="hist"><div class="t">近 7 天</div><div class="empty">暂无记录</div></div>`}
      </div>`;}).join('');
    return `<div class="acct">
      <div class="acct-head"><span class="phone" title="${esc(g.phone)}">${esc(mask(g.phone))}</span></div>
      <div class="acct-body">${plats||'<div class="plat">未配置任何平台</div>'}</div>
    </div>`;
  }).join('');
}

function renderEff(){
  const e=STATE.eff||{}, mins=e.saved_min||0;
  $('effbar').innerHTML=`
    <div class="cell"><div class="k">累计运行</div><div class="v">${e.total_runs||0}<small> 次</small></div></div>
    <div class="cell"><div class="k">成功率</div><div class="v">${e.success_rate==null?'—':e.success_rate+'<small> %</small>'}</div></div>
    <div class="cell"><div class="k">机器工时</div><div class="v">${e.machine_sec>=3600?(e.machine_sec/3600).toFixed(1)+'<small> 小时</small>':Math.round((e.machine_sec||0)/60)+'<small> 分钟</small>'}</div></div>
    <div class="cell"><div class="k">节省人工（估）</div><div class="v">${mins>=60?(mins/60).toFixed(1)+'<small> 小时</small>':mins+'<small> 分钟</small>'}</div></div>`;
  $('effHint').textContent=`今日已运行 ${e.today_runs||0} 次 · 节省时间按人工每轮约 5 分钟估算`;
  const runs=e.recent||[];
  $('runtab').innerHTML= runs.length
    ? '<tr><th>时间</th><th>任务</th><th class="num">耗时</th><th>结果</th></tr>'+runs.map(r=>`<tr>
        <td style="color:var(--sub)">${esc(r.ts)}</td><td>${esc(r.label)}</td>
        <td class="num">${esc(r.sec>=60?Math.floor(r.sec/60)+'分'+Math.round(r.sec%60)+'秒':r.sec+'秒')}</td>
        <td class="${r.ok?'ok':'bad'}">${r.ok?'成功':'失败'}</td></tr>`).join('')
    : '<tr><td style="color:var(--sub);padding:14px">暂无运行记录 — 跑一次签到或刷新状态后自动记录</td></tr>';
}

function renderAwSection(){
  const aw=STATE.awards||{}, awT=aw.today||{};
  // 本月口径已在顶部 KPI；这里只留今日，避免同一屏重复两遍
  if($('awSum'))$('awSum').innerHTML=`
    <div class="cell"><div class="k">今日已得流量</div><div class="v">${awFmt(awT.flow_mb||0)}</div></div>
    <div class="cell"><div class="k">今日已得话费</div><div class="v">¥${fy(awT.fee_yuan)}<small> · ${awT.fee_count||0} 张</small></div></div>`;
  renderAwFilters();
  renderAwTable();
}

function renderSeckillInfo(){
  const skS=$('skSess');
  if(skS){
    const ss=(STATE.seckill_sessions||[]);
    if(!ss.length){skS.innerHTML='<span style="color:var(--warn)">暂无会话 —— 请先跑一次 qwhdhub 签到</span>';}
    else{
      skS.innerHTML=ss.map(x=>{
        const t=x.at?x.at.replace('T',' ').slice(5,16):'?';
        let tail='<span style="color:var(--sub)">未知</span>';
        const left=sessLeftMin(x);
        if(left!==null&&left!==undefined){
          if(left<=0) tail=`<span class="sk-left" data-exp-at="${esc(x.expires_at||'')}" style="color:var(--bad)">已过期</span>`;
          else if(left<60) tail=`<span class="sk-left" data-exp-at="${esc(x.expires_at||'')}" style="color:var(--warn)">剩 ${left} 分钟</span>`;
          else tail=`<span class="sk-left" data-exp-at="${esc(x.expires_at||'')}" style="color:var(--ok)">剩 ${Math.round(left/60)} 小时</span>`;
        }
        return `<span class="pill" style="margin-right:6px;margin-bottom:4px">${esc(x.phone?mask(x.phone):x.file)} · ${esc(t)} · ${tail}</span>`;
      }).join('');
    }
  }
  const tl=$('tlBox');
  if(tl){
    const ts=(STATE.token_life||[]);
    if(!ts.length){tl.innerHTML='<span style="color:var(--sub)">还没有探测数据。导入 HAR 后由计划任务每 2 分钟自动探测。</span>';}
    else{
      tl.innerHTML=ts.map(x=>{
        let tag;
        if(x.state==='alive') tag='<span style="color:var(--ok)">有效</span>';
        else if(x.state==='expired') tag='<span style="color:var(--bad)">已失效</span>';
        else tag='<span style="color:var(--warn)">未确认</span>';
        const iss=x.issued_at?x.issued_at.replace('T',' ').slice(5,16):'?';
        const age=tokenAge(x);
        const ageTxt=age==null?'':` · 已用 <b class="tl-age" data-age-issued="${esc(x.issued_at||'')}" style="color:${age>=25?'var(--warn)':'inherit'}">${age} 分钟</b>`;
        return `<div class="pill" style="display:block;margin-bottom:6px;padding:8px 10px">
          <b>${esc(x.phone?mask(x.phone):x.key)}</b> · ${tag}${ageTxt}
          <span style="color:var(--sub)">签发 ${esc(iss)} · ${esc(x.conclusion||'')}</span>
        </div>`;
      }).join('');
    }
  }
  renderSkWindow();
}

function sessLeftMin(x){
  if(x&&x.expires_at){
    const t=new Date(String(x.expires_at).replace(' ','T'));
    if(!isNaN(t.getTime()))return Math.round((t.getTime()-Date.now())/60000);
  }
  return x?x.left_min:null;
}

// 侧边栏各 tab 状态角标（异常红点 / 待办黄点 / 运行中蓝点）
function setNavBadge(p,cls,tip){
  const el=$('nb-'+p);if(!el)return;
  el.className='nav-badge'+(cls?' '+cls:'');
  if(tip)el.title=tip; else el.removeAttribute('title');
}
function renderNavBadges(){
  if(!STATE)return;
  const tl=STATE.token_life||[], accts=STATE.raw_accounts||[];
  const hasExp=tl.some(x=>x.state==='expired');
  const hasEmpty=accts.length>0&&accts.every(a=>a.token_state==='empty');
  const hasSoon=tl.some(x=>{if(x.state!=='alive')return false;const a=tokenAge(x);return a!=null&&a>=25;});
  setNavBadge('token',(hasExp||hasEmpty)?'bad':(hasSoon?'warn':''),
    hasExp?'有账号票据已失效':(hasSoon?'有票据接近寿命上限':''));

  const hasFr=Object.values(STATE.daily||{}).some(v=>v&&v.feed_reward);
  const hasXbChest=Object.values(STATE.xunbao||{}).some(v=>v&&v.treasure_claim_required);
  setNavBadge('bass',(hasFr||hasXbChest)?'warn':'',hasFr?'有投喂累计奖励待领':(hasXbChest?'有寻宝宝箱待开':''));

  const ss=STATE.seckill_sessions||[];
  const hasDeadSess=ss.some(x=>{const l=sessLeftMin(x);return l!=null&&l<=0;});
  setNavBadge('seckill',hasDeadSess?'warn':'',hasDeadSess?'秒杀活动会话已过期':'');

  const sch=STATE.sched||[];
  const hasFail=sch.some(x=>x.code!=null&&x.code!==0&&x.code!==267011&&x.code!==267009&&x.code!==267014&&x.code!==267010);
  const noSched=(STATE.sched_read_ok===true&&sch.length===0);
  setNavBadge('sched',hasFail?'bad':(noSched?'warn':''),hasFail?'有计划任务上次执行失败':(noSched?'尚未配置计划任务':''));

  const running=!!((STATE.task&&STATE.task.running)||lastRunning);
  setNavBadge('logs',running?'run':'',running?'正在输出实时日志':'');
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
  // 记录各分片最新签名，使后续轮询可跳过未变板块
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

// 账号列表：新建 / 改手机号 / 配失败推送
function renderAccounts(){
  const box=$('acctBox'); if(!box)return;
  const accts=STATE.raw_accounts||[];
  if(!accts.length){
    box.innerHTML='<div class="hist"><div class="empty">还没有账号。点下面「新建账号」填手机号，再导入一次抓包文件即可。</div></div>';
    return;
  }
  box.innerHTML='<div class="tablewrap"><table class="fbtab"><thead><tr>'+
    '<th>号码</th><th>平台</th><th>配置文件</th><th>凭证</th><th>失败推送</th><th style="width:88px">操作</th>'+
    '</tr></thead><tbody>'+accts.map(a=>{
      const notify=(a.has_bark||a.has_sendkey||a.has_pushplus)
        ? '<span class="badge ok">已配置</span>' : '<span class="badge na">未配置</span>';
      const tok=tokenBadge(tokState(a));
      return `<tr><td>${esc(a.phone?mask(a.phone):'（未填手机号）')}</td>
        <td>${PLAT_CN[a.plat]||esc(a.plat)}</td>
        <td><code>${esc(a.file)}</code></td><td>${tok}</td><td>${notify}</td>
        <td><button type="button" class="btn sm" data-nobusy onclick="editAccount(${jsArg(a.file)})">编辑</button></td></tr>`;
    }).join('')+'</tbody></table></div>';
}
function openNewAccount(){
  openModal('新建账号',`
    <div class="infobox">先建一份空配置，再导入抓包文件，凭证会自动填进去。
      文件名默认用手机号尾号，方便认。</div>
    <div class="frow"><label for="naPlat">平台</label>
      <select class="inp" id="naPlat">
        <option value="qwhdhub">全国签到（签到领流量）</option>
        <option value="BASS">福建活动（养成游戏）</option>
      </select></div>
    <div class="frow"><label for="naPhone">手机号</label><input class="inp" id="naPhone" placeholder="11 位手机号" style="min-width:200px"></div>
    <div class="frow"><label for="naName">文件名</label><input class="inp" id="naName" placeholder="留空则用尾号，如 3239" style="min-width:200px"></div>
    <div class="frow"><button type="button" class="btn pri" onclick="saveNewAccount()">创建</button>
      <button type="button" class="btn" onclick="closeModal()">取消</button></div>`);
}
async function saveNewAccount(){
  const r=await act({action:'account_new',plat:V('naPlat'),phone:V('naPhone'),name:V('naName')});
  if(r&&r.ok)closeModal();
}
function editAccount(file){
  const a=(STATE.raw_accounts||[]).find(x=>x.file===file)||{};
  openModal('编辑账号 · '+file,`
    <div class="infobox">手机号用于把抓包文件里的凭证归到正确的号上。
      推送配好后，任一账号失败会通知到手机。<b>推送框留空 = 不修改</b>；要清除请点「清除推送配置」。</div>
    <div class="frow"><label for="eaPhone">手机号</label><input class="inp" id="eaPhone" value="${esc(a.phone||'')}" style="min-width:200px"></div>
    <div class="frow"><label for="eaBark">Bark 地址</label><input class="inp" id="eaBark" style="min-width:280px"
      placeholder="${a.has_bark?'已配置（留空则不修改）':'https://api.day.app/你的key'}"></div>
    <div class="frow"><label for="eaKey">Server酱</label><input class="inp" id="eaKey" style="min-width:280px"
      placeholder="${a.has_sendkey?'已配置（留空则不修改）':'SendKey'}"></div>
    <div class="frow"><label for="eaPushplus">PushPlus</label><input class="inp" id="eaPushplus" style="min-width:280px"
      placeholder="${a.has_pushplus?'已配置（留空则不修改）':'32 位十六进制令牌'}"></div>
    <div class="frow"><button type="button" class="btn pri" onclick="saveAccount(${jsArg(file)})">保存</button>
      <button type="button" class="btn" onclick="closeModal()">取消</button>
      ${(a.has_bark||a.has_sendkey||a.has_pushplus)?`<button type="button" class="btn danger" onclick="cf('清除该账号的推送配置？',()=>clearNotify(${jsArg(file)}))">清除推送配置</button>`:''}</div>`);
}
async function saveAccount(file){
  const body={action:'account_save',file:file,phone:V('eaPhone')};
  if(V('eaBark'))body.bark_url=V('eaBark');
  if(V('eaKey'))body.serverchan_sendkey=V('eaKey');
  if(V('eaPushplus'))body.pushplus_token=V('eaPushplus');
  const r=await act(body);
  if(r&&r.ok)closeModal();
}
async function clearNotify(file){
  const r=await act({action:'account_save',file:file,bark_url:'',serverchan_sendkey:'',pushplus_token:''});
  if(r&&r.ok)closeModal();
}

// 计划任务日志弹层：支持颜色高亮、刷新、只看错误、一键复制、打开直达底部
let _modalLogKind='', _modalLogLines=[], _modalLogErrOnly=false;
function renderModalLogLines(){
  const pre=$('modalLogPre');if(!pre)return;
  const shown=_modalLogErrOnly?_modalLogLines.filter(l=>colorize(l)==='err'):_modalLogLines;
  pre.innerHTML=shown.length
    ? shown.map(l=>{const c=colorize(l);return `<span class="${c}">${esc(l)}</span>`;}).join('\n')
    : '（没有符合条件的日志行）';
  const box=pre.parentElement;if(box)box.scrollTop=box.scrollHeight;
}
function toggleModalErr(){
  _modalLogErrOnly=!_modalLogErrOnly;
  const btn=$('mErrBtn');if(btn)btn.classList.toggle('on',_modalLogErrOnly);
  renderModalLogLines();
}
function copyText(txt,okMsg){
  if(!txt){toast('没有可复制的内容',true);return;}
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(txt).then(()=>toast(okMsg||'已复制')).catch(()=>fallbackCopy(txt,okMsg));
  }else fallbackCopy(txt,okMsg);
}
function fallbackCopy(txt,okMsg){
  const ta=document.createElement('textarea');
  ta.value=txt;ta.style.position='fixed';ta.style.opacity='0';
  document.body.appendChild(ta);ta.select();
  try{document.execCommand('copy');toast(okMsg||'已复制');}catch(_){toast('复制失败，请手动选中复制',true);}
  document.body.removeChild(ta);
}
function copyModalLog(){copyText(_modalLogLines.join('\n'),'已复制任务日志');}
async function openLog(kind){
  const name=(STATE&&STATE.sched_logs||{})[kind];
  if(!name){toast('不知道这个任务对应哪个日志',true);return;}
  _modalLogKind=kind;_modalLogErrOnly=false;
  openModal('任务日志 · '+name,'<div class="infobox">正在读取…</div>');
  let r;
  try{r=await(await fetch('/api/tasklog?name='+encodeURIComponent(name)+'&tail=300')).json();}
  catch(e){r={err:String(e&&e.message||e)};}
  if(r.err){$('mBody').innerHTML='<div class="warnbox">读取失败：'+esc(r.err)+'</div>';return;}
  if(r.missing){$('mBody').innerHTML='<div class="warnbox">还没有这个日志文件 —— 该任务尚未运行过。'+
    '可以在「定时任务」页点该行「注册/更新」，或等它到点自动跑。</div>';return;}
  _modalLogLines=r.lines||[];
  $('mBody').innerHTML=`<div class="frow" style="margin-bottom:10px">
    <span class="fbhead" style="margin:0">显示最后 ${_modalLogLines.length} 行（共 ${Math.round((r.size||0)/1024)} KB）· logs\\${esc(name)}</span>
    <div style="margin-left:auto;display:flex;gap:6px">
      <button type="button" class="btn sm flt" id="mErrBtn" data-nobusy onclick="toggleModalErr()">只看错误</button>
      <button type="button" class="btn sm" data-nobusy onclick="copyModalLog()">复制</button>
      <button type="button" class="btn sm" data-nobusy onclick="openLog(${jsArg(kind)})">刷新</button>
    </div></div>
    <div class="log-pre"><pre id="modalLogPre"></pre></div>`;
  renderModalLogLines();
}

function renderSkWindow(){
  const el=$('skWindow'); if(!el)return;
  const n=new Date(), mins=n.getHours()*60+n.getMinutes();
  if(mins>=13*60||mins<11*60){
    el.style.display='block';
    el.innerHTML=mins>=13*60
      ? '今天的场次（12:00~13:00）已结束。现在点「等到点自动抢」会<b>立刻出手并被拒</b>，请明天再来，或交给 11:55 的计划任务。'
      : '现在距开抢还早。点「等到点自动抢」会一直占用运行槽等到 12:00，期间别的操作都做不了；建议交给 11:55 的计划任务。';
  }else el.style.display='none';
}

function renderLuck(){
  const box=$('luckBox'); if(!box)return;
  const lk=STATE.luck||{};
  const keys=Object.keys(lk);
  if(!keys.length){
    box.innerHTML='<div class="empty">还没有记录。点「只查状态」看剩余次数，或直接「刷任务并抽奖」。</div>';
    return;
  }
  box.innerHTML=keys.map(k=>{
    const a=lk[k]||{};
    const ps=a.last_prizes||[];
    const rows=ps.map(p=>{
      const isWin=p.name&&p.name.indexOf('谢谢')<0;
      return `<tr><td>${esc(p.at?p.at.slice(11):'')}</td>
        <td style="color:${isWin?'var(--ok)':'var(--sub)'}">${isWin?'★ ':''}${esc(p.name||'—')}</td>
        <td>${esc(p.life?p.life+' 天':'')}</td></tr>`;
    }).join('');
    const summary=(a.last_draws!=null)?`　上次抽 ${esc(a.last_draws)} 次，中 ${esc(a.last_wins||0)} 次`:'';
    const today=todayLocal();
    const daySum=(a.day===today&&a.day_draws)?`　<b>本程序今日抽 ${esc(a.day_draws)} 次，中 ${esc(a.day_wins||0)} 次</b>`:'';
    return `<div style="margin-top:14px">
      <div class="fbhead">${esc(k)} · ${esc(a.phone?mask(a.phone):'')}　剩余次数 <b>${esc(a.surplus!=null?a.surplus:'—')}</b>${daySum}${summary}　<code>${esc(a.updated||'')}</code></div>
      ${ps.length?`<div class="tablewrap"><table class="fbtab"><thead><tr><th style="width:110px">时间</th><th>奖品</th><th style="width:90px">有效期</th></tr></thead><tbody>${rows}</tbody></table></div>`:''}
    </div>`;
  }).join('');
}

function renderXunbao(){
  const box=$('xunbaoBox'); if(!box)return;
  const xb=STATE.xunbao||{};
  const keys=Object.keys(xb);
  if(!keys.length){
    box.innerHTML='<div class="empty">还没有记录。点「只查进度」看走到第几格。</div>';
    return;
  }
  const today=todayLocal();
  box.innerHTML=keys.map(k=>{
    const a=xb[k]||{};
    const seq=a.seq||0, total=a.total||29;
    const pct=total?Math.min(100,Math.round(seq/total*100)):0;
    const daySum=(a.day===today&&a.day_rolls)?`　<b>本程序今日投 ${esc(a.day_rolls)} 次，中 ${esc(a.day_prizes||0)} 个</b>`:'';
    const ps=(a.last_prizes||[]);
    const tasks=(a.tasks||[]);
    const taskTxt=tasks.length?`　任务：${esc(tasks.filter(t=>!t.done).map(t=>t.name).join('、')||'全完成')}`:'';
    return `<div style="margin-top:14px">
      <div class="fbhead">${esc(k)} · ${esc(a.phone?mask(a.phone):'')}　进度 <b>第 ${esc(seq)}/${esc(total)} 格</b>　骰子 <b>${esc(a.dice!=null?a.dice:'—')}</b>${a.treasure_claim_required?'　★ 宝箱待开':''}${daySum}　<code>${esc(a.updated||'')}</code></div>
      <div style="height:6px;border-radius:3px;background:var(--line);margin:8px 0 4px">
        <div style="height:6px;border-radius:3px;width:${pct}%;background:var(--ok)"></div></div>
      <div style="color:var(--sub);font-size:12px">${taskTxt}</div>
      ${ps.length?`<div class="tablewrap"><table class="fbtab"><thead><tr><th>最近到手</th></tr></thead><tbody>${ps.map(n=>`<tr><td>${esc(n)}</td></tr>`).join('')}</tbody></table></div>`:''}
    </div>`;
  }).join('');
}

function nextDailyTime(tiers){
  const cur=tiers.find(t=>t.current&&t.status!=='RECEIVED');
  if(!cur)return '';
  if(cur.limit_at)return `今天 ${cur.limit_at}`;
  const last=[...tiers].reverse().find(t=>t.status==='RECEIVED'&&t.at);
  if(!last||!cur.interval)return '可领';
  const d=new Date(last.at.replace(' ','T'));
  if(isNaN(d.getTime()))return '可领';
  d.setMinutes(d.getMinutes()+cur.interval);
  const hh=String(d.getHours()).padStart(2,'0'), mm=String(d.getMinutes()).padStart(2,'0');
  return ymd(d)===todayLocal()?`今天 ${hh}:${mm}`:`${ymd(d)} ${hh}:${mm}`;
}

function renderDaily(){
  const box=$('dailyBox'); if(!box)return;
  const dd=STATE.daily||{};
  const keys=Object.keys(dd).filter(k=>dd[k]&&((dd[k].tiers&&dd[k].tiers.length)||dd[k].sign));
  if(!keys.length){
    box.innerHTML='<div class="empty">还没有记录。跑一次 BASS 任务后，这里会显示三档奖励的领取状态和福袋进度。</div>';
    return;
  }
  box.innerHTML=keys.map(k=>{
    const a=dd[k], tiers=a.tiers||[];
    const got=tiers.filter(t=>t.status==='RECEIVED').length;
    const nt=nextDailyTime(tiers);
    const chips=tiers.map(t=>{
      const ok=t.status==='RECEIVED', cur=t.current&&!ok;
      const color=ok?'var(--ok)':(cur?'var(--warn)':'var(--sub)');
      const mark=ok?'✓':(cur?'●':'○');
      return `<span style="display:inline-block;margin-right:16px;color:${color}">${mark} ${esc(t.name)} ${esc(t.fodder)}饲料${t.at?` <code>${esc(t.at.slice(11))}</code>`:''}</span>`;
    }).join('');
    const lk=a.lucky||{};
    const sg=a.sign||{};
    const fr=a.feed_reward;
    const frLine=fr?`<div style="font-size:13px;margin-bottom:7px;color:var(--warn)">⚑ 投喂累计奖励 <b>${esc(fr.fodder)}</b> 饲料可领未领 · ${esc(fr.expire)} 过期</div>`:'';
    const pct=lk.need?Math.min(100,Math.round((lk.current/lk.need)*100)):0;
    const bar=lk.need?`<div style="height:6px;background:var(--line);border-radius:3px;margin-top:7px;max-width:440px">
        <div style="height:6px;width:${pct}%;background:${lk.triggered?'var(--ok)':'var(--acc)'};border-radius:3px"></div></div>`:'';
    const signLine=sg.cycle!=null?`<div style="font-size:12.5px;margin-bottom:7px">
        <span style="color:${sg.today?'var(--ok)':(sg.open?'var(--warn)':'var(--sub)')}">${sg.today?'✓ 今日已签':(sg.open?'● 待签':'○ 未到签到时间')}</span>
        　连签第 <b>${esc(sg.cycle)}</b> 天${sg.today_fodder?`　今日 ${esc(sg.today_fodder)}g`:''}${sg.next_fodder?` · 明日 ${esc(sg.next_fodder)}g`:''}${sg.begin_hour?`　<code>${esc(sg.begin_hour)}:00</code> 开放`:''}
      </div>`:'';
    return `<div style="margin-bottom:16px">
      <div class="fbhead">${esc(k)} · 已领 ${got}/${tiers.length} 档${nt?`　下一档 <b style="color:var(--warn)">${esc(nt)}</b>`:'　今日已领完'}</div>
      ${frLine}
      ${signLine}
      <div style="font-size:12.5px">${chips}</div>
      <div style="font-size:12px;color:var(--sub);margin-top:8px">福袋 ${esc(lk.current||0)}/${esc(lk.need||0)}${lk.more!=null?`　还差 ${esc(lk.more)} 次喂食`:''}${lk.triggered?'　<b style="color:var(--ok)">已触发！</b>':''}</div>
      ${bar}
    </div>`;
  }).join('');
}

function nextRun(cycle,lastDone){
  if(!lastDone)return '—';
  if(cycle===0)return '不限';
  const d=new Date(lastDone.replace(' ','T'));
  if(isNaN(d.getTime()))return '—';
  if(cycle===2){
    const wd=(d.getDay()+6)%7;
    d.setDate(d.getDate()-wd+7);
  } else if(cycle===1){
    d.setMonth(d.getMonth()+1,1);
  } else return '—';
  const k=ymd(d);
  return k<=todayLocal()?'<span style="color:var(--ok)">今天可做</span>':k;
}

function renderFlowbank(){
  const box=$('fbTasks'); if(!box)return;
  const fb=STATE.flowbank||{};
  const keys=Object.keys(fb).filter(k=>fb[k]&&fb[k].tasks&&Object.keys(fb[k].tasks).length);
  if(!keys.length){
    box.innerHTML='<div class="empty">还没有记录。在「手动执行一次」里勾上「流量银行任务」跑一次，这里就会列出每个任务的周期、奖励和下次可做时间。</div>';
    return;
  }
  const badge=s=>s==='1'?'<span class="badge ok">已完成</span>'
    :(s==='2'?'<span class="badge" style="background:rgba(214,168,86,.16);color:var(--warn)">进行中</span>'
    :'<span class="badge signed">可做</span>');
  box.innerHTML=keys.map(k=>{
    const acc=fb[k]||{},tasks=acc.tasks||{};
    const ids=Object.keys(tasks).sort((a,b)=>((tasks[a].cycle||9)-(tasks[b].cycle||9)));
    const rows=ids.map(id=>{
      const t=tasks[id];
      const cyc=t.cycle===1?'每月一次':(t.cycle===2?'每周一次':'不限');
      const flow=t.flow?(`+${esc(t.flow)}${t.unit==='M'?'MB':esc(t.unit)}`):'—';
      return `<tr><td>${esc(t.title||('#'+id))}</td><td>${cyc}</td><td>${flow}</td>
        <td>${badge(t.status)}</td><td><code>${esc(t.last_done||'—')}</code></td><td>${nextRun(t.cycle,t.last_done)}</td></tr>`;
    }).join('');
    return `<div style="margin-bottom:16px">
      <div class="fbhead">${esc(k)} · 最后更新 ${esc(acc.updated||'—')}</div>
      <div class="tablewrap"><table class="fbtab"><thead><tr><th>任务</th><th>周期</th><th>奖励</th><th>状态</th><th>上次完成</th><th>下次可做</th></tr></thead>
      <tbody>${rows}</tbody></table></div></div>`;
  }).join('');
}

// ---------------- 自动策略：存一次，长期生效 ----------------
let PREFS_ONCE=false;
function renderPolicy(){
  const p=(STATE&&STATE.policy)||{}, b=p.bass||{};
  if(!$('pFeed'))return;
  if(!PREFS_ONCE){
    PREFS_ONCE=true;
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
function feedAllToggle(p){
  const all=$(p+'FeedAll'), num=$(p+'Feed');
  if(!all||!num)return;
  num.disabled=all.checked;
  if(all.checked)num.value=0;
}
async function savePolicy(){
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
  if(r&&r.ok&&r.policy){STATE.policy=r.policy;PREFS_ONCE=true;renderPolicy();}
}

// ---------------- 设置页：推送设置 ----------------
let SET_ONCE=false;
function renderSettings(){
  const st=(STATE&&STATE.settings)||{};
  if(!$('sBark'))return;
  if(!SET_ONCE){
    SET_ONCE=true;
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
async function saveSettings(){
  const push={bark_url:V('sBark'),serverchan_sendkey:V('sSendkey'),pushplus_token:V('sPushplus')};
  const r=await act({action:'settings_save',push:push});
  if(r&&r.ok){
    if(STATE)STATE.settings=push;
    SET_ONCE=true;renderSettings();
  }
}

// ---------------- 设置页：邮箱收 HAR ----------------
let MAIL_ONCE=false;
const MAIL_PRESETS={gmail:['imap.gmail.com',993],qq:['imap.qq.com',993],'163':['imap.163.com',993],'139':['imap.139.com',993]};
function mailProvider(){
  const p=MAIL_PRESETS[$('mProvider').value];
  if(p){$('mHost').value=p[0];$('mPort').value=p[1];}
}
function renderMail(){
  const m=(STATE&&STATE.mail)||{};
  if(!$('mUser'))return;
  if(!MAIL_ONCE){
    MAIL_ONCE=true;
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
async function saveMail(){
  const r=await act({action:'mail_save',mail:{
    enabled:$('mEnabled').checked,provider:$('mProvider').value,
    host:V('mHost'),port:Number(V('mPort'))||993,
    user:V('mUser'),password:V('mPass'),sender:V('mSender'),subject_keyword:V('mKeyword')}});
  if(r&&r.ok&&STATE){STATE.mail={...(STATE.mail||{}),enabled:$('mEnabled').checked,
    provider:$('mProvider').value,host:V('mHost'),port:Number(V('mPort'))||993,user:V('mUser'),
    sender:V('mSender'),subject_keyword:V('mKeyword'),password:V('mPass')?1:STATE.mail.password};
    MAIL_ONCE=false;renderMail();}
}
function gotoSettings(){
  const b=document.querySelector('nav button[data-p="settings"]');
  if(b)nav(b);
}
function fillSelKV(id,items){
  const s=$(id);if(!s)return;const cur=s.value;
  s.innerHTML=items.map(it=>`<option value="${esc(it.v)}">${esc(it.t)}</option>`).join('');
  if(items.some(it=>it.v===cur))s.value=cur;
}

// ---------------- 总览：到账台账折叠 / 筛选 ----------------
const AW_PAGE=8;          // 默认只显示最近 8 条，避免台账占满首屏
let awExpanded=false;
function onAwFilter(){awExpanded=false;renderAwTable();}
function toggleAwAll(){awExpanded=!awExpanded;renderAwTable();}
function renderAwFilters(){
  const rows=((STATE.awards||{}).recent)||[];
  const phones=[...new Set(rows.map(r=>r.account).filter(Boolean))];
  const sources=[...new Set(rows.map(r=>r.source).filter(Boolean))];
  fillSelKV('awAcct',[{v:'',t:'全部号码'}].concat(phones.map(a=>({v:a,t:mask(a)}))));
  fillSelKV('awSource',[{v:'',t:'全部来源'}].concat(sources.map(s=>({v:s,t:s}))));
}
function renderAwTable(){
  const tb=$('awTab');if(!tb)return;
  const ac=$('awAcct'), sc=$('awSource');
  const af=ac?ac.value:'', sf=sc?sc.value:'';
  const aw=STATE.awards||{};
  const rawRows=(aw.recent)||[];
  const allTotal=rawRows.length;
  let rows=rawRows.slice();
  if(af)rows=rows.filter(r=>(r.account||'')===af);
  if(sf)rows=rows.filter(r=>(r.source||'')===sf);
  const total=rows.length;
  const shown=awExpanded?rows:rows.slice(0,AW_PAGE);
  tb.innerHTML=shown.length
    ? '<tr><th>到手时间</th><th>号码</th><th>来源</th><th>到手内容</th><th class="num">面额</th><th class="num">折合流量</th></tr>'+
      shown.map(r=>`<tr>
        <td style="color:var(--sub)">${esc((r.at||'').slice(5,16))}</td>
        <td>${esc(r.account?mask(r.account):'—')}</td>
        <td>${esc(r.source||'')}</td>
        <td style="min-width:170px">${esc(r.name||'')}${r.kind==='fee'?' <span class="tag">话费券</span>':''}</td>
        <td class="num">${(r.yuan||0)>0?'¥'+fy(r.yuan):'—'}</td>
        <td class="num">${(r.size_mb||0)>0?awFmt(r.size_mb):'—'}</td></tr>`).join('')
    : `<tr><td colspan="6" style="color:var(--sub);padding:14px">${allTotal?'当前筛选没有记录（可重置上方下拉框查看全部）':'还没有到账记录 —— 领到流量或券会自动出现在这里'}</td></tr>`;
  const btn=$('awMoreBtn');
  if(btn){btn.textContent=awExpanded?'收起':'展开全部';btn.style.display=total>AW_PAGE?'':'none';}
  const hint=$('awHint');
  if(hint){
    const recTotal=aw.recent_total||allTotal;
    const capNote=(recTotal>allTotal)?`（共 ${recTotal} 条，仅展示最近 ${allTotal} 条）`:'';
    hint.textContent=(total>AW_PAGE?`最近 ${shown.length} / ${total} 条`:`共 ${total} 条`)+capNote;
  }
}

// ---------------- 总览：现在需要你做什么（带直达操作） ----------------
function tokenAge(x){
  if(!x||!x.issued_at)return null;
  const t=new Date(String(x.issued_at).replace(' ','T'));
  if(isNaN(t.getTime()))return null;
  return Math.max(0,Math.round((Date.now()-t.getTime())/60000));
}
function renderTodo(){
  const box=$('todoBox'); if(!box)return;
  const items=[], nm=x=>esc(mask(x.phone||'')||x.key);
  if(!(STATE.raw_accounts||[]).length)
    items.push(['bad','<b>还没有账号</b> → 去「凭证与账号」点「新建账号」填手机号，再导入一次抓包文件',
      '<button type="button" class="btn sm pri todo-act" data-nobusy onclick="navTo(\'token\');openNewAccount()">去新建账号</button>']);
  const tl=STATE.token_life||[];
  const exp=tl.filter(x=>x.state==='expired');
  const alive=tl.filter(x=>x.state==='alive');
  if(exp.length)items.push(['bad',`<b>${exp.length} 个号的票据已失效</b>（${exp.map(nm).join('、')}）→ 去「凭证与账号」导入刚抓的文件。导入后 11:54 的会话刷新会自动接上，不用重启任务`,
    '<button type="button" class="btn sm pri todo-act" data-nobusy onclick="navTo(\'token\')">去导入 HAR</button>']);
  if(alive.length){
    items.push(['ok',`${alive.length} 个号票据有效（${alive.map(nm).join('、')}），暂时不用抓包`,'']);
    const soon=alive.filter(x=>{const a=tokenAge(x);return a!=null&&a>=25;});
    if(soon.length)items.push(['warn',`票据已用 ${soon.map(x=>tokenAge(x)+' 分钟').join('、')}，接近约 30 分钟的寿命上限 —— 要赶 12:00 秒杀的话现在就该重新抓包`,
      '<button type="button" class="btn sm todo-act" data-nobusy onclick="navTo(\'token\')">去重抓导入</button>']);
  }
  if(!tl.length&&(STATE.raw_accounts||[]).length)items.push(['warn','还没有票据寿命数据 —— 导入一次抓包文件后，探测任务会自动开始计时',
    '<button type="button" class="btn sm todo-act" data-nobusy onclick="navTo(\'token\')">去导入 HAR</button>']);
  const ss=STATE.seckill_sessions||[];
  const dead=ss.filter(x=>{const l=sessLeftMin(x);return l!=null&&l<=0;});
  if(dead.length)items.push(['warn',`秒杀会话已过期（${dead.map(x=>esc(mask(x.phone||'')||x.file)).join('、')}）—— 11:54 的刷新任务会重建，前提是那一刻票据还活着`,
    '<button type="button" class="btn sm todo-act" data-nobusy onclick="navTo(\'seckill\')">查看秒杀页</button>']);
  const frs=Object.entries(STATE.daily||{}).filter(([k,v])=>v&&v.feed_reward);
  if(frs.length){
    const frBtn=frs.length===1
      ? `<button type="button" class="btn sm pri todo-act" onclick="act({action:'bass_run',key:${jsArg(frs[0][0])},claim:1})">立即领取</button>`
      : '<button type="button" class="btn sm pri todo-act" data-nobusy onclick="navTo(\'bass\')">去福建活动领取</button>';
    items.push(['warn',`<b>投喂累计奖励可领未领</b>：${frs.map(([k,v])=>`${esc(k)} ${esc(v.feed_reward.fodder)} 饲料（${esc(v.feed_reward.expire)} 过期）`).join('、')} —— 当晚作废，可运行福建活动领取`,frBtn]);
  }
  let noSign=[];
  (STATE.accounts||[]).forEach(g=>Object.entries(g.plats||{}).forEach(([plat,a])=>{
    const st=a.stats||{};
    const signed=plat==='qwhdhub'?st.today_signed_qwhd:st.today_signed_bass;
    if(signed!==todayLocal())noSign.push(esc(mask(g.phone))+'（'+esc(PLAT_CN[plat]||plat)+'）');
  }));
  if(noSign.length)items.push(['sub',`今日还没签到：${noSign.join('、')} —— 11:45 的任务会自动补，也可在下方卡片手动点`,
    '<button type="button" class="btn sm pri todo-act" onclick="runAll(\'all\')">立即签到领福利</button>']);
  const nx=(STATE.sched||[]).filter(x=>x.next&&x.name!=='MobileTokenLifeProbe'&&x.name!=='MobileMailWatch')
    .sort((a,b)=>(a.next<b.next?-1:1))[0];
  if(nx)items.push(['acc',`下一个自动任务：<b>${esc((STATE.sched_cn||{})[nx.kind]||nx.name)}</b> ${esc(nx.next)}`,
    '<button type="button" class="btn sm todo-act" data-nobusy onclick="navTo(\'sched\')">查看任务</button>']);
  box.innerHTML='<div class="todo"><div class="th">现在需要你做什么</div>'+
    items.map(([lv,t,actHtml])=>`<div class="it"><span class="dot" style="background:var(--${lv})"></span><span class="txt">${t}</span>${actHtml||''}</div>`).join('')+'</div>';
}

// ---------------- 定时任务：真实状态表 ----------------
const SCHED=[
  {kind:'sign',      cn:'每天签到',    name:'MobileSignIn',         bat:'run_scheduled.bat',      desc:'全国签到 + 福建活动 + 消消乐'},
  {kind:'daily',     cn:'每日奖励',    name:'MobileDailyReward',    bat:'run_daily_reward.bat',   desc:'每小时补签到 + 领三档奖励', interval:'每小时'},
  {kind:'keepalive', cn:'会话保活',    name:'MobileJwtKeepAlive',   bat:'run_jwt_keepalive.bat',  desc:'每 90 分钟只登录，不签到', interval:'每90分'},
  {kind:'sess',      cn:'会话刷新',    name:'MobileSessionRefresh', bat:'run_session_refresh.bat',desc:'刷新活动会话，供秒杀复用'},
  {kind:'seckill',   cn:'秒杀抢券',    name:'MobileSeckill',        bat:'run_seckill.bat',        desc:'启动后内部等到 12:00 开抢'},
  {kind:'games',     cn:'拓展活动',    name:'MobileGames',          bat:'run_games.bat',          desc:'拓展活动三合一'},
  {kind:'probe',     cn:'票据寿命探测',name:'MobileTokenLifeProbe', bat:'run_token_life.bat',     desc:'每 2 分钟探测票据寿命', interval:'每2分'},
  {kind:'mail',      cn:'邮箱收 HAR',  name:'MobileMailWatch',      bat:'run_mail_watch.bat',     desc:'每 10 分钟查收 HAR 邮件并自动导入', interval:'每10分'}
];
function codeText(c,kind){
  if(c===0)return '<span style="color:var(--ok)">成功</span>';
  if(c===267011)return '<span style="color:var(--warn)">从未运行</span>';
  if(c===267009)return '<span style="color:var(--acc)">运行中</span>';
  if(c===267014)return '<span style="color:var(--sub)">已中止</span>';
  if(c===267010)return '<span style="color:var(--sub)">已禁用</span>';
  if(c==null)return '—';
  const link=kind?` <a href="javascript:void(0)" onclick="openLog('${kind}')">看日志</a>`:'';
  return c===1?`<span style="color:var(--bad)" title="退出码 1：多为票据失效">失败</span>${link}`
    :`<span style="color:var(--bad)">异常 ${c}</span>${link}`;
}
function buildSched(){
  const box=$('schedBox'); if(!box||box.dataset.done)return;
  box.innerHTML='<div class="tablewrap"><table class="stab"><thead><tr><th>任务</th>'+
    '<th style="width:100px">触发时刻</th><th style="width:76px">状态</th>'+
    '<th style="width:112px">下次运行</th><th style="width:172px">上次结果</th><th style="width:236px">操作</th></tr></thead><tbody>'+
    SCHED.map(t=>`<tr>
      <td><div class="nm">${t.cn}${t.interval?`<span class="sched-tag" title="重复型任务：触发时刻表示起点">${t.interval}</span>`:''}</div><div class="sub">${t.desc}</div>
          <div class="sub" title="Windows 计划任务名与启动脚本">${t.name} · ${t.bat}</div></td>
      <td><input class="inp" id="st_${t.kind}" placeholder="HH:MM" style="min-width:84px"
                 title="${t.interval?'起点时刻（注册若已过会自动从当前+2分钟开始重复）':'每日定时触发时刻 HH:MM'}"
                 oninput="this.dataset.dirty='1'"></td>
      <td id="sc_${t.kind}">—</td>
      <td id="sn_${t.kind}">—</td>
      <td id="sl_${t.kind}">—</td>
      <td><button type="button" class="btn pri sm" onclick="regSched('${t.kind}')">注册/更新</button>
          <button type="button" class="btn sm" data-nobusy onclick="openLog('${t.kind}')">日志</button>
          <button type="button" class="btn sm danger" onclick="cf('注销「${t.cn}」？注销后它不再自动运行。',()=>unregSched('${t.name}'))">注销</button></td>
    </tr>`).join('')+'</tbody></table></div>';
  box.dataset.done='1';
}
function updSched(){
  buildSched();
  const list=STATE.sched||[];
  SCHED.forEach(t=>{
    const s=list.find(x=>x.name===t.name);
    const e=$('st_'+t.kind);
    if(e&&!e.dataset.dirty&&document.activeElement!==e&&s&&s.time&&e.value!==s.time)e.value=s.time;
    const c=$('sc_'+t.kind), n=$('sn_'+t.kind), l=$('sl_'+t.kind);
    if(c)c.innerHTML=s?(s.state==='Disabled'?'<span style="color:var(--warn)">已禁用</span>':'<span style="color:var(--ok)">已注册</span>')
      :(STATE.sched_read_ok==null?'<span style="color:var(--sub)">读取中</span>':'<span style="color:var(--bad)">未注册</span>');
    if(n)n.textContent=s&&s.next?s.next:'—';
    if(l)l.innerHTML=s?(s.last?`<div class="sub">${esc(s.last)}</div><div>${codeText(s.code,t.kind)}</div>`:codeText(s.code,t.kind)):'—';
  });
  const box=$('schedBox'); if(!box)return;
  let tip=box.querySelector('.schedtip');
  let html='';
  if(!list.length){
    if(STATE.sched_read_ok===false)
      html='<div class="warnbox schedtip">读不到任务状态（PowerShell 可能被策略拦或超时）。点上方「刷新状态」重试；不影响已注册的任务照常运行。</div>';
    else if(STATE.sched_read_ok==null)
      html='<div class="infobox schedtip">正在读取计划任务状态…（首次要几秒，读到后自动更新）</div>';
    else
      html='<div class="infobox schedtip">还没有注册任何计划任务 —— 点上方「一键配置全部（推荐方案）」一次配好 8 个，电脑才会每天自动跑。</div>';
  }
  if(!html){if(tip)tip.remove();return;}
  if(tip)tip.outerHTML=html;
  else box.insertAdjacentHTML('beforeend',html);
}
async function refreshSched(silent){
  const r=await act({action:'sched_refresh'});
  if(r&&r.sched&&STATE){STATE.sched=r.sched;updSched();if(!silent)toast('已刷新任务状态');}
}

const SCHED_PLAN=[["sign","11:45"],["daily","06:00"],["keepalive","00:00"],
                  ["sess","11:54"],["seckill","11:55"],["games","12:05"],["probe","00:00"],["mail","00:00"]];
let _pendingSchedRefresh=false;
async function applyAllSched(){
  const cn=k=>(SCHED.find(x=>x.kind===k)||{}).cn||k;
  const desc=SCHED_PLAN.map(([k,t])=>`${cn(k)}　${t}`).join('\n');
  if(!await askConfirm('将按推荐方案重新注册 8 个计划任务，会覆盖当前触发时刻：\n\n'+desc+
              '\n\n注意：「每天签到」那一趟含消消乐（伪造通关成绩）。\n\n继续吗？',{okText:'全部注册'}))return;
  const r=await act({action:'sched_apply_all',plan:SCHED_PLAN});
  if(r&&r.ok)_pendingSchedRefresh=true;
}
async function unregAllSched(){
  const r=await act({action:'sched_unreg_all'});
  if(r&&r.ok)_pendingSchedRefresh=true;
}
async function regSched(kind){
  const el=$('st_'+kind), t=el?el.value.trim():'';
  const m=/^(\d{1,2}):(\d{2})$/.exec(t);
  if(!m||+m[1]>23||+m[2]>59){toast('触发时刻格式应为 HH:MM（00:00~23:59）',true);return;}
  el.dataset.dirty='';
  const r=await act({action:'sched_reg',kind:kind,time:t});
  if(r&&r.ok)_pendingSchedRefresh=true;
}
async function unregSched(name){
  const r=await act({action:'sched_unreg',name:name});
  if(r&&r.ok)_pendingSchedRefresh=true;
}

// ---------------- 统一轮询调度 ----------------
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

// ---------------- 执行结果区捕获机（防抢跑） ----------------
let CAP=null;
function capStart(kind,then){
  CAP={kind:kind,buf:[],then:then||null,armed:false,seenActivity:false,startedAt:Date.now()};
  const box=$(kind+'Box');
  if(box){
    box.innerHTML='<div class="outbox"><div class="h">已发出执行请求 · 正在执行中…（完成后这里显示输出）</div></div>';
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
function capEnd(){
  if(!CAP)return;
  const {kind,then,buf}=CAP;
  const box=$(kind+'Box');
  CAP=null;
  if(box){
    const head=`最近一次执行 · ${new Date().toLocaleTimeString('zh-CN',{hour12:false})}`;
    const body=buf.length?esc(buf.join('\n')):'（本次没有输出）';
    box.innerHTML=`<div class="outbox"><div class="h">${head}</div>${body}</div>`;
  }
  if(then)then(buf.slice());
}

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
    .then(capArm);}
function runBassProbe(){
  capStart('bass');
  act({action:'bass_probe',key:V('bassRunKey')}).then(capArm);}
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
// 手动执行区的勾选只管这一次：页面每次加载都回到默认，
// 不让浏览器把上一次的勾选偷偷恢复回来。
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
    .then(capArm);}
async function runXxlAll(){
  if(!await askConfirm('所有福建号并行跑消消乐（每号按策略局数），跑完顺手投寻宝骰子。确定？',{okText:'并行执行',danger:true}))return;
  const x=((STATE&&STATE.policy)||{}).xxl||{};
  capStart('xxl');
  act({action:'xxl_all',times:(x.times!=null?x.times:5)}).then(capArm);}
function xxlQuery(){capStart('xxl');act({action:'xxl_run',file:V('xxlFile'),dry:1}).then(capArm);}
function detectLevel(){
  capStart('xxl',lines=>{
    const m=lines.join('\n').match(/当前真实关卡:\s*(\d+)/);
    if(m)toast('当前真实关卡 '+m[1]+'（运行时自动使用，不用手填）');
    else toast('没读到关卡号（可能凭证失效），看下方输出',true);
  });
  act({action:'xxl_run',file:V('xxlFile'),dry:1}).then(capArm);}
async function runSeckill(){
  const skk=(((STATE&&STATE.policy)||{}).seckill)||{}, at=(skk.at||'').trim(),
        n=new Date(), mins=n.getHours()*60+n.getMinutes();
  if(mins>=13*60&&!await askConfirm('今天的场次（12:00~13:00）已结束，现在出手会被拒。仍要继续吗？',{okText:'仍要继续'}))return;
  capStart('sk');
  act({action:'seckill_run',file:V('skFile'),at:at||''}).then(capArm);}
function seckillQuery(){capStart('sk');act({action:'seckill_run',file:V('skFile'),dry:1}).then(capArm);}
function seckillOnce(){capStart('sk');act({action:'seckill_run',file:V('skFile'),once:1}).then(capArm);}

// ---------------- HAR 拖拽 & XHR 二进制直传进度 ----------------
function afterHar(r){
  const el=$('harOut'); if(!el)return;
  const rep=(r&&r.report)||[];
  const err=r&&r.err;
  if(!rep.length&&!err){el.style.display='none';return;}
  el.style.display='block';
  let html='';
  if(err) html+=`<div style="color:var(--bad)">✗ ${esc(err)}</div>`;
  html+=rep.map(t=>`<div>${esc(t)}</div>`).join('');
  el.innerHTML=html;
}
let harBusy=false,_droppedHarFile=null;
function setHarProgress(pct,msg){
  const p=$('harProg'), bar=$('harProgBar'), info=$('harFileInfo');
  if(p)p.classList.toggle('on',pct!=null);
  if(bar&&pct!=null)bar.style.width=Math.max(0,Math.min(100,pct))+'%';
  if(info&&msg)info.textContent=msg;
}
function pickHarFile(f){
  if(!f)return;
  _droppedHarFile=f;
  const info=$('harFileInfo');
  if(info)info.textContent=`已选择：${f.name}（${(f.size/1048576).toFixed(2)} MB）— 点「导入所选文件」开始`;
}
(function initHarDrop(){
  const dz=$('harDrop'), inp=$('harFile');
  if(!dz||!inp)return;
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
})();
function importHar(){
  const inp=$('harFile');
  const f=(inp&&inp.files&&inp.files[0])||_droppedHarFile;
  if(!f){toast('先选择一个抓包文件（.har）',true);return;}
  if(harBusy){toast('上一份还在导入，等它出报告再点',true);return;}
  harBusy=true;
  const mb=(f.size/1048576).toFixed(1);
  const el=$('harOut');
  if(el){el.style.display='block';el.innerHTML=`<div style="color:var(--sub)">正在上传 ${esc(f.name)}（${mb}MB）…导入后会实测登录一次，请稍候</div>`;}
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
    setHarProgress(null,`已处理：${f.name}（${mb} MB）`);
    let r=null;
    try{r=JSON.parse(xhr.responseText||'{}');}
    catch(e){r={ok:false,err:'解析响应失败（HTTP '+xhr.status+'）'};}
    if(!r.ok){const m=r.err||'HAR 导入失败';toast(m,true);showErr(m);}
    else{clearErr();if(r.msg)toast(r.msg);}
    afterHar(r);
    pullLogs();pullState(!0);
  };
  xhr.onerror=()=>{
    harBusy=false;
    setHarProgress(null,'上传失败，请确认控制台服务仍在运行');
    const r={ok:false,err:'上传失败：网络断开或文件读取受限'};
    toast(r.err,true);showErr(r.err);afterHar(r);
  };
  xhr.onabort=()=>{harBusy=false;setHarProgress(null,'上传已取消');};
  xhr.send(f);
}

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
function colorize(l){
  if(/✓|OK |成功|已签/.test(l))return 'ok';
  if(/✗|!!|ERROR|失败|过期|异常/.test(l))return 'err';
  if(/▶|■|——|警告|⚠/.test(l))return 'hl';
  if(String(l).startsWith('$'))return 'dim';
  return '';}
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
function setLogLevel(lvl,btn){
  _logLevel=lvl;
  document.querySelectorAll('#p-logs .flt').forEach(b=>b.classList.toggle('on',b===btn));
  renderLogList();
}
function renderLogList(){
  if(!cons)return;
  const frag=document.createDocumentFragment();
  LOG_ITEMS.filter(matchLogItem).slice(-1200).forEach(it=>frag.appendChild(makeLogNode(it)));
  cons.innerHTML='';cons.appendChild(frag);
  cons.scrollTop=cons.scrollHeight;
  updateLogJump();
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

let lastRunning=false;
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
function clearConsole(){LOG_ITEMS=[];if(cons)cons.innerHTML='';if($('consoleEmpty'))$('consoleEmpty').style.display='';updateLogJump();renderDrawer();}

// ---------------- 主题切换 + 字号缩放 ----------------
function applyTheme(theme){
  document.documentElement.dataset.theme=theme;
  const b=$('btnTheme');
  if(b)b.textContent=theme==='light'?'☾':'☀';
}
function toggleTheme(){
  const cur=document.documentElement.dataset.theme==='light'?'light':'dark';
  const nxt=cur==='light'?'dark':'light';
  try{localStorage.setItem('cmcc_theme',nxt);}catch(_){}
  applyTheme(nxt);
  toast('已切换为'+(nxt==='light'?'浅色主题':'深色主题'));
}
applyTheme(document.documentElement.dataset.theme==='light'?'light':'dark');

let ZOOM=1;
try{ZOOM=Number(localStorage.getItem('cmcc_zoom'))||1;}catch(e){}
function applyZoom(){
  document.documentElement.style.zoom=ZOOM;
  const zv=$('zoomVal');if(zv)zv.textContent=Math.round(ZOOM*100)+'%';
}
function zoomBy(d){
  ZOOM=Math.min(1.6,Math.max(0.8,Math.round((ZOOM+d)*10)/10));
  try{localStorage.setItem('cmcc_zoom',String(ZOOM));}catch(e){}
  applyZoom();toast('字号 '+Math.round(ZOOM*100)+'%');
}
function zoomReset(){
  ZOOM=1;
  try{localStorage.setItem('cmcc_zoom','1');}catch(e){}
  applyZoom();toast('字号已重置 100%');
}
applyZoom();

(function(){const h=location.hash.slice(1);const b=h&&document.querySelector('nav button[data-p="'+h+'"]');if(b)nav(b);})();

// ---------------- 统一轮询 + 轻量分钟计时 + 自愈 ----------------
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
