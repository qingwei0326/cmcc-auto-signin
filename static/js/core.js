export const store={
  state:null,
  logSeq:0,
  activePage:'dash',
  lastRunning:false,
  pendingSchedRefresh:false,
};

export const hooks={
  pullLogs:async()=>{},
  pullState:async()=>{},
  kickPoll:()=>{},
  navTo:()=>{},
  renderNavBadges:()=>{},
  refreshSched:async()=>{},
};

export function registerHooks(h){Object.assign(hooks,h);}

export const $=id=>document.getElementById(id);
export const V=id=>$(id).value.trim();

export function setHidden(el,hide){
  const node=typeof el==='string'?$(el):el;
  if(node)node.classList.toggle('is-hidden',!!hide);
}

const _TONE_CLS=['tone-ok','tone-warn','tone-bad','tone-inherit'];
export function setTone(el,tone){
  const node=typeof el==='string'?$(el):el;
  if(!node)return;
  node.classList.remove(..._TONE_CLS);
  if(tone)node.classList.add('tone-'+tone);
}

export const TITLES={dash:'总览',token:'凭证与账号',bass:'福建活动',seckill:'秒杀抢券',games:'拓展活动',sched:'定时任务',logs:'日志',settings:'设置'};
export const PLAT_CN={qwhdhub:'全国签到',BASS:'福建活动'};
// 实测 appToken 寿命 90~135 分钟；超过窗口未验证不能再当现时有效展示。BASS 按天走给足余量。
export const TOKEN_TTL_MIN={qwhdhub:135,BASS:1560};

export function numVal(id,defVal){
  const el=$(id); if(!el)return defVal;
  const rawVal=String(el.value||'').trim();
  if(rawVal==='')return defVal;
  const n=Number(rawVal);
  return Number.isFinite(n)?n:defVal;
}

export function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
const _HTML_BRAND=Symbol('cmcc.html');
export class SafeHtml{
  constructor(s){this[_HTML_BRAND]=String(s==null?'':s);}
  toString(){return this[_HTML_BRAND];}
}
export function raw(s){return s instanceof SafeHtml?s:new SafeHtml(s);}
function _renderHtmlVal(v){
  if(v==null||v===false)return '';
  if(v instanceof SafeHtml)return v[_HTML_BRAND];
  if(Array.isArray(v))return v.map(_renderHtmlVal).join('');
  return esc(v);
}
export function html(strings,...vals){
  let out=strings[0];
  for(let i=0;i<vals.length;i++)out+=_renderHtmlVal(vals[i])+strings[i+1];
  return new SafeHtml(out);
}
export function joinHtml(arr,sep){
  if(!arr||!arr.length)return raw('');
  const s=sep==null?'':_renderHtmlVal(sep);
  return raw(arr.map(_renderHtmlVal).join(s));
}
export function setHtml(el,content){
  const node=typeof el==='string'?$(el):el;
  if(!node)return;
  if(content==null||content===''||content===false){node.textContent='';return;}
  /* eslint-disable-next-line no-restricted-syntax */
  node.innerHTML=_renderHtmlVal(content);
}

export function mask(p){return p.length===11?p.slice(0,3)+'****'+p.slice(7):p;}
export function fy(n){const v=Math.round((n||0)*100)/100;return Number.isInteger(v)?String(v):v.toFixed(2);}
export function awFmt(mb){return mb>=1024?(mb/1024).toFixed(2)+'GB':((mb||0)?Math.round(mb)+'MB':'0MB');}
export function ymd(d){const p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());}
export function todayLocal(){return ymd(new Date());}
export function fmtNum(v){return v==null?'—':v;}

export function last7(map){
  const out=[];
  for(let i=6;i>=0;i--){
    const d=new Date();d.setDate(d.getDate()-i);const k=ymd(d);
    out.push(map[k]||{date:k,flow_mb:0,tasks:0,feeds:0});
  }
  return out;
}

export function minsAgo(mdhm){
  const m=/^(\d{1,2})-(\d{1,2}) (\d{1,2}):(\d{2})$/.exec(String(mdhm||''));
  if(!m)return null;
  const now=new Date();
  const d=new Date(now.getFullYear(),+m[1]-1,+m[2],+m[3],+m[4]);
  if(d-now>864e5)d.setFullYear(d.getFullYear()-1);
  return (now-d)/60000;
}

export function tokenAge(x){
  if(!x||!x.issued_at)return null;
  const t=new Date(String(x.issued_at).replace(' ','T'));
  if(isNaN(t.getTime()))return null;
  return Math.max(0,Math.round((Date.now()-t.getTime())/60000));
}

export function sessLeftMin(x){
  if(x&&x.expires_at){
    const t=new Date(String(x.expires_at).replace(' ','T'));
    if(!isNaN(t.getTime()))return Math.round((t.getTime()-Date.now())/60000);
  }
  return x?x.left_min:null;
}

export function tokState(a,s){
  const STATE=store.state||{};
  s=s||a.stats||(STATE.stats||{})[a.stats_key||(a.plat+':'+a.key)]||(STATE.stats||{})[a.key]||{};
  if(a.token_state==='empty')return 'empty';
  const live=a.plat==='qwhdhub'?(STATE.token_life||[]).find(x=>x.key===a.key):null;
  if(live&&live.state==='alive')return 'ok';
  if(live&&live.state==='expired')return 'expired';
  const field=a.plat==='qwhdhub'?'token_qwhd':'token_bass';
  const st=s[field]||'unchecked';
  const ages=[s[field+'_at'],s.last_run].map(minsAgo).filter(v=>v!==null);
  const age=ages.length?Math.min(...ages):null;
  if(st==='ok'&&age!==null&&age>(TOKEN_TTL_MIN[a.plat]||TOKEN_TTL_MIN.qwhdhub))return 'stale';
  return st;
}

export function tokenBadge(s){
  if(s==='ok')return html`<span class="badge ok">凭证有效</span>`;
  if(s==='expired')return html`<span class="badge bad">凭证已过期</span>`;
  if(s==='empty')return html`<span class="badge na">凭证未配置</span>`;
  if(s==='stale')return html`<span class="badge warn" title="超过凭证有效期没有新的验证，上次结论不可直接当作当前有效">待复验</span>`;
  return html`<span class="badge na">凭证未校验</span>`;
}

export function signedBadge(date){
  return date===todayLocal()?html`<span class="badge signed">今日已签</span>`:html`<span class="badge na">今日未签</span>`;
}

export function fillSelKV(id,items){
  const s=$(id);if(!s)return;const cur=s.value;
  setHtml(s,items.map(it=>html`<option value="${it.v}">${it.t}</option>`));
  if(items.some(it=>it.v===cur))s.value=cur;
}

export function colorize(l){
  if(/✓|OK |成功|已签/.test(l))return 'ok';
  if(/\[INFO\].*回落\s*appTokenLogin/.test(l))return 'dim';
  if(/✗|!!|ERROR|失败|过期|异常/.test(l))return 'err';
  if(/▶|■|——|警告|⚠/.test(l))return 'hl';
  if(String(l).startsWith('$'))return 'dim';
  return '';
}

export function copyText(txt,okMsg){
  if(!txt){toast('没有可复制的内容',true);return;}
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(txt).then(()=>toast(okMsg||'已复制')).catch(()=>fallbackCopy(txt,okMsg));
  }else fallbackCopy(txt,okMsg);
}

function fallbackCopy(txt,okMsg){
  const ta=document.createElement('textarea');
  ta.value=txt;ta.className='sr-copy';
  document.body.appendChild(ta);ta.select();
  try{document.execCommand('copy');toast(okMsg||'已复制');}catch(_){toast('复制失败，请手动选中复制',true);}
  document.body.removeChild(ta);
}

// ---------------- Toast / 顶部错误栏 ----------------
let toastTimer=null;
export function toast(m,bad){
  const t=$('toast');if(!t)return;
  t.textContent=m;t.classList.toggle('bad',!!bad);t.classList.add('on');
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('on'),bad?6000:2600);
  if(toastTimer&&typeof toastTimer.unref==='function')toastTimer.unref();
}

export function showErr(m){
  const b=$('errBox');if(!b)return;
  setHtml(b,html`<button type="button" class="err-x" data-act="clear-err">✕ 关闭</button>${m}`);
  b.classList.add('on');
}
export function clearErr(){const b=$('errBox');if(b){b.classList.remove('on');setHtml(b,'');}}

// ---------------- 自定义确认框 & 详情弹层 ----------------
let _cfmRes=null,_cfmPrevFocus=null;
export function closeCfm(ok){
  const m=$('cfmModal');if(!m||!m.classList.contains('on'))return;
  m.classList.remove('on');
  const res=_cfmRes, prev=_cfmPrevFocus;
  _cfmRes=null;_cfmPrevFocus=null;
  if(prev&&typeof prev.focus==='function'&&document.contains(prev)){try{prev.focus();}catch(_){}}
  if(res)res(!!ok);
}

export function askConfirm(msg,opts){
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
export function cf(msg,fn,opts){askConfirm(msg,opts||{danger:true}).then(ok=>{if(ok)fn();});}

let _modalPrevFocus=null;
export function openModal(title,content){
  _modalPrevFocus=document.activeElement;
  $('mTitle').textContent=title;setHtml('mBody',content);$('modal').classList.add('on');
  const x=$('modal').querySelector('.bh .x');if(x)try{x.focus();}catch(_){}
}
export function closeModal(){
  const m=$('modal');if(!m||!m.classList.contains('on'))return;
  m.classList.remove('on');setHtml('mBody','');
  const prev=_modalPrevFocus;_modalPrevFocus=null;
  if(prev&&typeof prev.focus==='function'&&document.contains(prev)){try{prev.focus();}catch(_){}}
}

// ---------------- 外观弹出层：主题切换 + rem 根字号缩放 ----------------
export function closeAppear(){
  const pop=$('appearPop'), btn=$('btnAppear');
  if(pop)pop.classList.remove('on');
  if(btn)btn.setAttribute('aria-expanded','false');
}
export function toggleAppear(ev){
  if(ev)ev.stopPropagation();
  const pop=$('appearPop'), btn=$('btnAppear');
  if(!pop)return;
  const open=!pop.classList.contains('on');
  pop.classList.toggle('on',open);
  if(btn)btn.setAttribute('aria-expanded',open?'true':'false');
}

export function applyTheme(theme){
  document.documentElement.dataset.theme=theme;
  const b=$('btnTheme');
  if(b)b.textContent=theme==='light'?'☾ 切换为深色主题':'☀ 切换为浅色主题';
}
export function toggleTheme(){
  const cur=document.documentElement.dataset.theme==='light'?'light':'dark';
  const nxt=cur==='light'?'dark':'light';
  try{localStorage.setItem('cmcc_theme',nxt);}catch(_){}
  applyTheme(nxt);
  toast('已切换为'+(nxt==='light'?'浅色主题':'深色主题'));
}

let ZOOM=1;
export function applyZoom(){
  document.documentElement.style.fontSize=(16*ZOOM)+'px';
  document.documentElement.style.setProperty('--ui-scale',String(ZOOM));
  const zv=$('zoomVal');if(zv)zv.textContent=Math.round(ZOOM*100)+'%';
}
export function zoomBy(d){
  ZOOM=Math.min(1.6,Math.max(0.8,Math.round((ZOOM+d)*10)/10));
  try{localStorage.setItem('cmcc_zoom',String(ZOOM));}catch(_){}
  applyZoom();toast('字号 '+Math.round(ZOOM*100)+'%');
}
export function zoomReset(){
  ZOOM=1;
  try{localStorage.setItem('cmcc_zoom','1');}catch(_){}
  applyZoom();toast('字号已重置 100%');
}

let _coreUiBound=false;
export function initCoreUi(){
  applyTheme(document.documentElement.dataset.theme==='light'?'light':'dark');
  try{ZOOM=Math.min(1.6,Math.max(0.8,Number(localStorage.getItem('cmcc_zoom'))||1));}catch(_){ZOOM=1;}
  applyZoom();
  if(_coreUiBound)return;
  _coreUiBound=true;
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){
      if($('cfmModal')&&$('cfmModal').classList.contains('on'))closeCfm(false);
      else if($('modal')&&$('modal').classList.contains('on'))closeModal();
      else closeAppear();
    }
  });
  document.addEventListener('click',e=>{
    if(e.target&&e.target.id==='modal')closeModal();
    if(e.target&&e.target.id==='cfmModal')closeCfm(false);
    const pop=$('appearPop');
    if(pop&&pop.classList.contains('on')&&(!e.target||!e.target.closest||!e.target.closest('.popwrap'))){
      closeAppear();
    }
  });
}

// ---------------- 忙态管理 & 统一 API Action ----------------
export const TASK_ACTIONS=new Set([
  'refresh_status','run_all','qwhd_run','bass_run','bass_probe',
  'luck_run','xunbao_run','seckill_run','games_run','voucher_run','xxl_run','xxl_all',
  'sched_query','sched_reg','sched_apply_all','sched_unreg_all','sched_unreg'
]);

export function setBusy(running,label){
  document.querySelectorAll('.content .btn, .topbar .btn').forEach(b=>{
    if(b.hasAttribute('data-nobusy'))return;
    b.disabled=!!running;
  });
  const txt=running?('运行中 · '+(label||'')):'空闲';
  const pt=$('taskPillTop'), ptt=$('taskPillTxt');
  if(ptt)ptt.textContent=txt+(running?' ▾':'');
  else if(pt)pt.textContent=txt+(running?' ▾':'');
  if(pt)pt.classList.toggle('run',!!running);
  const ldt=$('ldTitle');
  if(ldt)ldt.textContent=running?('实时日志 · '+(label||'运行中')):'实时日志（最近 10 行）';
  const s=$('btnStop');if(s)s.disabled=!running;
}

export async function act(body,after){
  let r;
  try{
    const resp=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    r=await resp.json();
  }catch(e){r={ok:false,err:'连不上控制台服务：'+(e&&e.message||e)};}
  if(!r.ok){const m=r.err||'操作失败';toast(m,true);showErr(m);}
  else{clearErr();if(r.msg)toast(r.msg);}
  if(after)after(r);
  if(r&&r.ok&&TASK_ACTIONS.has(body&&body.action)){
    await Promise.all([hooks.pullLogs(),hooks.pullState(true)]);
    hooks.kickPoll(true);
  }else{
    await hooks.pullState(true);
  }
  return r;
}
