import {
  $, V, store, hooks, html, setHtml, setHidden,
  colorize, copyText, toast, askConfirm, act,
} from './core.js';

// ---------------- 结构化执行结果区捕获机（防抢跑） ----------------
let CAP=null;
export function getCap(){return CAP;}

export function capStart(kind,then){
  CAP={kind:kind,buf:[],then:then||null,armed:false,seenActivity:false,startedAt:Date.now()};
  const box=$(kind+'Box');
  if(box){
    setHtml(box,html`<div class="outbox"><div class="outbox-head"><span class="ob-badge run">执行中</span><span class="h">已发出执行请求 · 正在执行中…（完成后这里显示输出）</span></div></div>`);
    try{box.scrollIntoView({behavior:'smooth',block:'nearest'});}catch(_){}
  }
}
export function capArm(r){
  if(!r||!r.ok){CAP=null;return;}
  if(!CAP)return;
  CAP.armed=true;
  if(CAP.seenActivity&&!store.lastRunning)capEnd();
}
export function capDrop(){CAP=null;}
export function capPush(line){
  if(!CAP)return;
  const s=String(line).replace(/\s+$/,'');
  if(!s.trim()||s.startsWith('▶')||s.startsWith('■')||s.startsWith('$ '))return;
  CAP.seenActivity=true;
  CAP.buf.push(s);if(CAP.buf.length>300)CAP.buf.shift();
}
export function copyCapBox(btn){
  const ob=btn&&btn.closest?btn.closest('.outbox'):null;
  const bd=ob?ob.querySelector('.outbox-body'):null;
  copyText(bd?bd.innerText:'','已复制执行结果');
}
export function capEnd(){
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
      ? buf.map(l=>html`<div class="${colorize(l)}">${l}</div>`)
      : html`<div class="dim">（本次没有输出）</div>`;
    setHtml(box,html`<div class="outbox"><div class="outbox-head"><span class="ob-badge ${badgeCls}">${badgeTxt}</span><span class="h">${head}</span><button type="button" class="btn sm" data-nobusy data-act="copy-cap-box">复制</button></div><div class="outbox-body">${body}</div></div>`);
  }
  if(then)then(buf.slice());
}

// ---------------- 各活动手动执行入口 ----------------
export async function runBass(){
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
export function runBassProbe(){
  capStart('bass');
  act({action:'bass_probe',key:V('bassRunKey')}).then(capArm);
}
export function runLuck(mode){
  capStart('luckOut');
  const body={action:'luck_run'};
  if(mode==='dry')body.dry=1;
  if(mode==='no_tasks')body.no_tasks=1;
  act(body).then(capArm);
}
export function runXunbao(mode){
  capStart('xunbaoOut');
  const body={action:'xunbao_run'};
  if(mode==='dry')body.dry=1;
  act(body).then(capArm);
}
export function pickStage(which){
  if(which==='auto'&&$('bAuto').checked)$('bCont').checked=false;
  if(which==='cont'&&$('bCont').checked)$('bAuto').checked=false;
  syncStageUi();
}
export function syncStageUi(){
  const a=$('bAuto'), c=$('bCont'), p=$('bPrize'), h=$('bStageHint');
  if(!a||!c)return;
  if(p)setHidden(p,!a.checked);
  if(h)h.textContent=a.checked?'喂满时会领奖后继续喂（一/六阶按所选档位，二~五阶服务端随机开奖）'
    :(c.checked?'喂满时会放弃本阶奖品直接升阶（奖品作废）':'都不勾：快喂满时会停手等你决定');
}
// 手动执行区的勾选只管这一次：页面每次加载都回到默认，不让浏览器偷偷恢复
export function resetManualBass(){
  ['bClaim','bRoll','bDry','bFeedAll','bAuto','bCont','bTasks','bFb'].forEach(id=>{const e=$(id);if(e)e.checked=false;});
  const f=$('bFeed');if(f){f.value=0;f.disabled=false;}
  const p=$('bPrize');if(p)p.value='fee';
  syncStageUi();
}

let _manualBassBound=false;
export function initManualBass(){
  resetManualBass();
  if(_manualBassBound)return;
  _manualBassBound=true;
  window.addEventListener('load',()=>setTimeout(resetManualBass,0));
  window.addEventListener('pageshow',e=>{if(e.persisted)resetManualBass();});
}

export async function runGames(mode){
  capStart('gm');
  const r=await act({action:'games_run',file:V('gmFile'),dry:mode==='dry'?1:0,tasks:mode==='tasks'?1:0},
                    ()=>setTimeout(()=>hooks.pullState(true),3000));
  capArm(r);
}
export async function runXxl(){
  if(!await askConfirm('消消乐属于伪造游戏通关成绩，中奖靠服务端概率，确定要跑吗？',{okText:'开始执行',danger:true}))return;
  const STATE=store.state;
  const x=((STATE&&STATE.policy)||{}).xxl||{};
  capStart('xxl');
  act({action:'xxl_run',file:V('xxlFile'),
       level:(x.level!=null?x.level:49),star:(x.star!=null?x.star:3),
       times:(x.times!=null?x.times:5),gap:(x.gap!=null?x.gap:12)})
    .then(capArm);
}
export async function runXxlAll(){
  if(!await askConfirm('所有福建号并行跑消消乐（每号按策略局数），跑完顺手投寻宝骰子。确定？',{okText:'并行执行',danger:true}))return;
  const STATE=store.state;
  const x=((STATE&&STATE.policy)||{}).xxl||{};
  capStart('xxl');
  act({action:'xxl_all',times:(x.times!=null?x.times:5)}).then(capArm);
}
export function xxlQuery(){capStart('xxl');act({action:'xxl_run',file:V('xxlFile'),dry:1}).then(capArm);}
export function detectLevel(){
  capStart('xxl',lines=>{
    const m=lines.join('\n').match(/当前真实关卡:\s*(\d+)/);
    if(m)toast('当前真实关卡 '+m[1]+'（运行时自动使用，不用手填）');
    else toast('没读到关卡号（可能凭证失效），看下方输出',true);
  });
  act({action:'xxl_run',file:V('xxlFile'),dry:1}).then(capArm);
}
export async function runSeckill(){
  const STATE=store.state;
  const skk=(((STATE&&STATE.policy)||{}).seckill)||{}, at=(skk.at||'').trim(),
        n=new Date(), mins=n.getHours()*60+n.getMinutes();
  if(mins>=13*60&&!await askConfirm('今天的场次（12:00~13:00）已结束，现在出手会被拒。仍要继续吗？',{okText:'仍要继续'}))return;
  capStart('sk');
  act({action:'seckill_run',file:V('skFile'),at:at||''}).then(capArm);
}
export function seckillQuery(){capStart('sk');act({action:'seckill_run',file:V('skFile'),dry:1}).then(capArm);}
export function seckillOnce(){capStart('sk');act({action:'seckill_run',file:V('skFile'),once:1}).then(capArm);}

export async function runAll(platform){
  const dry=$('dashDry').checked;
  if(platform==='all'&&!dry&&!await askConfirm('将执行「签到领福利」（含全部平台签到、签到页AI豆任务、AI豆苗养成记、大转盘、寻宝、拓展活动及消消乐伪造通关成绩），确定继续吗？',{okText:'开始执行',danger:true}))return;
  return act({action:'run_all',platform:platform,
    dry:dry, luck:platform==='all', games:platform==='all',
    xxl:platform==='all', tasks:platform==='all', doumi:platform==='all'});
}
export function refreshStatus(){act({action:'refresh_status'});}
export async function voucherRun(key){
  const dry=!!$('dashDry')?.checked;
  if(!dry&&!await askConfirm('将真实领取领券中心'+(key?'「'+key+'」':'全部账号')+'未领的免费券（需先导入新鲜 HAR），确定继续吗？',{okText:'开始执行',danger:true}))return;
  return act({action:'voucher_run',dry:dry?1:0,...(key?{key}:{})});
}
