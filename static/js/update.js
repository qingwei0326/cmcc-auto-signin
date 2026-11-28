import { $, store, hooks, html, setHtml, setHidden, toast } from './core.js';

// ---------------- 版本与更新检测 ----------------
//   · 版本号是锚点，显示在侧边栏底部；点击 = 跳到「设置→关于」，不直接发网络请求
//   · 启动 5 秒后静默检查一次，发现新版本才点亮侧栏徽章，无更新/失败完全无感
//   · 支持「启动时自动检查」开关与「以后不再提醒此版本」
const UPDATE_AUTO_KEY='cmcc_auto_update', UPDATE_SKIP_KEY='cmcc_skip_version';
let _updateInfo=null;

function _autoUpdateOn(){try{return localStorage.getItem(UPDATE_AUTO_KEY)!=='0';}catch(_){return true;}}
function _skippedVersion(){try{return localStorage.getItem(UPDATE_SKIP_KEY)||'';}catch(_){return '';}}

function _badgeNormal(v){
  const b=$('verBadge'), STATE=store.state;if(!b)return;
  b.textContent='v'+(v||(STATE&&STATE.version)||'');
  b.classList.remove('up');b.title='当前版本，点击查看更新';b.dataset.mode='normal';
}
function _badgeUp(latest){
  const b=$('verBadge');if(!b)return;
  b.textContent='↑ v'+latest;b.classList.add('up');
  b.title='发现新版本 v'+latest+'，点击查看';b.dataset.mode='up';
}

export function renderVersion(){
  const STATE=store.state;
  const v=(STATE&&STATE.version)||'';
  const about=$('aboutVer');
  if(about)about.textContent=v?('v'+v):'—';
  const b=$('verBadge');
  if(b&&v){b.hidden=false;if(!b.dataset.mode)_badgeNormal(v);}
}

export function gotoAbout(){
  try{hooks.navTo('settings');}catch(_){}
  setTimeout(()=>{
    const p=$('panelSetAbout');
    if(p&&p.scrollIntoView)p.scrollIntoView({behavior:'smooth',block:'center'});
    if(!_updateInfo)checkUpdate(false);   // 进到关于页顺手静默查一次
  },60);
}

export async function checkUpdate(manual){
  const tip=$('aboutUpdate'), skipBtn=$('btnSkipUpdate');
  if(!manual&&!_autoUpdateOn())return null;
  if(manual&&tip)tip.textContent='正在查询 GitHub…';
  let r;
  try{r=await(await fetch('/api/check_update')).json();}
  catch(_){r={error:'连不上控制台服务'};}
  _updateInfo=r||{};
  if(!r||r.error){
    if(manual&&tip)tip.textContent='检查失败：'+((r&&r.error)||'未知')+'（不影响使用，可手动前往 Releases 下载）';
    if(manual)toast('检查更新失败',true);
    return r;
  }
  const cur=r.current||'', lat=r.latest||'';
  // 自动检查且用户已跳过该版本 → 静默，不点亮徽章
  if(r.has_update&&_skippedVersion()===lat&&!manual){
    _badgeNormal(cur);
    if(skipBtn)setHidden(skipBtn,true);
    return r;
  }
  if(r.has_update){
    _badgeUp(lat);
    if(skipBtn)setHidden(skipBtn,false);
    const safeUrl=/^https?:\/\//i.test(String(r.url||''))?String(r.url):'';
    if(tip)setHtml(tip,html`发现新版本 <b>v${lat}</b>${r.published_at?html`（${r.published_at}）`:''}${
      safeUrl?html` · <a href="${safeUrl}" target="_blank" rel="noopener">前往下载</a>`:''}`);
    if(manual)toast('发现新版本 v'+lat);
  }else{
    _badgeNormal(cur);
    if(skipBtn)setHidden(skipBtn,true);
    if(tip)tip.textContent='已是最新版本（v'+cur+'）';
    if(manual)toast('已是最新版本 v'+cur);
  }
  return r;
}

export function skipUpdate(){
  const lat=(_updateInfo&&_updateInfo.latest)||'';
  if(!lat)return;
  try{localStorage.setItem(UPDATE_SKIP_KEY,lat);}catch(_){}
  _badgeNormal((store.state&&store.state.version)||'');
  const b=$('btnSkipUpdate');if(b)setHidden(b,true);
  const tip=$('aboutUpdate');if(tip)tip.textContent='已跳过 v'+lat+'，该版本不再提示';
  toast('已跳过 v'+lat);
}

export function toggleAutoUpdate(on){
  try{localStorage.setItem(UPDATE_AUTO_KEY,on?'1':'0');}catch(_){}
}

// 启动：回填开关状态 + 延迟 5 秒静默检查（不打扰首屏）
export function initUpdate({autoCheck=true}={}){
  const a=$('aboutAuto');
  if(a)a.checked=_autoUpdateOn();
  if(autoCheck&&_autoUpdateOn())setTimeout(()=>checkUpdate(false),5000);
}
