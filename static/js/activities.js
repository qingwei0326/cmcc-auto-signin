import {
  $, V, store, PLAT_CN, html, setHtml, setHidden,
  mask, ymd, todayLocal, tokenAge, sessLeftMin, tokState, tokenBadge,
  fillSelKV, openModal, closeModal, act,
} from './core.js';

// ---------------- 下拉框 & 饲料实时提示 ----------------
export function renderSelects(){
  const STATE=store.state||{};
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
      .map(a=>html`<option value="${a.key}">${a.key} · ${mask(a.phone)}</option>`);
    setHtml(hk,html`<option value="">自动识别（按文件里的号码）</option>${opts}`);
    if(Array.from(hk.options).some(o=>o.value===cur))hk.value=cur;
  }
  updateBassFodder();
}

export function updateBassFodder(){
  const STATE=store.state;
  const bf=$('bFodder');if(!bf||!STATE)return;
  const bs=$('bassRunKey'), k=bs?bs.value:'';
  const a=(STATE.raw_accounts||[]).find(x=>x.plat==='BASS'&&x.key===k);
  const st=(a&&a.stats)||(STATE.stats||{})['BASS:'+k]||(STATE.stats||{})[k]||{};
  bf.textContent=st.fodder!=null?(`当前 ${st.fodder}g`+(st.stage?` · ${st.stage}`:'')):'';
}

// ---------------- 账号管理（新建 / 改手机号 / 单账号推送） ----------------
export function renderAccounts(){
  const STATE=store.state||{};
  const box=$('acctBox'); if(!box)return;
  const accts=STATE.raw_accounts||[];
  if(!accts.length){
    setHtml(box,html`<div class="empty">还没有账号。点下面「新建账号」填手机号，再导入一次抓包文件即可。</div>`);
    return;
  }
  setHtml(box,html`<div class="tablewrap"><table class="fbtab"><thead><tr>
    <th class="col-phone">号码</th><th class="col-plat">平台</th><th>配置文件</th><th class="col-badge">凭证</th><th class="col-badge">失败推送</th><th class="col-act">操作</th>
    </tr></thead><tbody>${accts.map(a=>{
      const notify=(a.has_bark||a.has_sendkey||a.has_pushplus)
        ? html`<span class="badge ok">已配置</span>` : html`<span class="badge na">未配置</span>`;
      const tok=tokenBadge(tokState(a));
      return html`<tr><td>${a.phone?mask(a.phone):'（未填手机号）'}</td>
        <td>${PLAT_CN[a.plat]||a.plat}</td>
        <td><code>${a.file}</code></td><td>${tok}</td><td>${notify}</td>
        <td class="col-act"><button type="button" class="btn sm" data-nobusy data-act="edit-account" data-file="${a.file}">编辑</button></td></tr>`;
    })}</tbody></table></div>`);
}

export function openNewAccount(){
  openModal('新建账号',html`
    <div class="infobox">先建一份空配置，再导入抓包文件，凭证会自动填进去。文件名默认用手机号尾号，方便认。</div>
    <div class="frow"><label for="naPlat">平台</label>
      <select class="inp inp-md" id="naPlat">
        <option value="qwhdhub">全国签到（签到领流量）</option>
        <option value="BASS">福建活动（养成游戏）</option>
      </select></div>
    <div class="frow"><label for="naPhone">手机号</label><input class="inp inp-md" id="naPhone" placeholder="11 位手机号"></div>
    <div class="frow"><label for="naName">文件名</label><input class="inp inp-md" id="naName" placeholder="留空则用尾号，如 3239"></div>
    <div class="frow mt-12"><button type="button" class="btn pri" data-act="save-new-account">创建</button>
      <button type="button" class="btn" data-act="close-modal">取消</button></div>`);
}
export async function saveNewAccount(){
  const r=await act({action:'account_new',plat:V('naPlat'),phone:V('naPhone'),name:V('naName')});
  if(r&&r.ok)closeModal();
}

export function editAccount(file){
  const STATE=store.state||{};
  const a=(STATE.raw_accounts||[]).find(x=>x.file===file)||{};
  openModal('编辑账号 · '+file,html`
    <div class="infobox">手机号用于把抓包文件里的凭证归到正确的号上。推送配好后，任一账号失败会通知到手机。<b>推送框留空 = 不修改</b>；要清除请点「清除推送配置」。</div>
    <div class="frow"><label for="eaPhone">手机号</label><input class="inp inp-md" id="eaPhone" value="${a.phone||''}"></div>
    <div class="frow"><label for="eaBark">Bark 地址</label><input class="inp inp-lg" id="eaBark"
      placeholder="${a.has_bark?'已配置（留空则不修改）':'https://api.day.app/你的key'}"></div>
    <div class="frow"><label for="eaKey">Server酱</label><input class="inp inp-lg" id="eaKey"
      placeholder="${a.has_sendkey?'已配置（留空则不修改）':'SendKey'}"></div>
    <div class="frow"><label for="eaPushplus">PushPlus</label><input class="inp inp-lg" id="eaPushplus"
      placeholder="${a.has_pushplus?'已配置（留空则不修改）':'32 位十六进制令牌'}"></div>
    <div class="frow mt-12"><button type="button" class="btn pri" data-act="save-account" data-file="${file}">保存</button>
      <button type="button" class="btn" data-act="close-modal">取消</button>
      ${(a.has_bark||a.has_sendkey||a.has_pushplus)?html`<button type="button" class="btn danger" data-act="clear-notify" data-file="${file}">清除推送配置</button>`:''}</div>`);
}
export async function saveAccount(file){
  const body={action:'account_save',file:file,phone:V('eaPhone')};
  if(V('eaBark'))body.bark_url=V('eaBark');
  if(V('eaKey'))body.serverchan_sendkey=V('eaKey');
  if(V('eaPushplus'))body.pushplus_token=V('eaPushplus');
  const r=await act(body);
  if(r&&r.ok)closeModal();
}
export async function clearNotify(file){
  const r=await act({action:'account_save',file:file,bark_url:'',serverchan_sendkey:'',pushplus_token:''});
  if(r&&r.ok)closeModal();
}

// ---------------- 福建活动子标签控制 ----------------
const BASS_TABS=['all','daily','manual','play','flowbank','xxl'];
let _bassTab='all';

export function setBassTab(tab){
  _bassTab=BASS_TABS.includes(tab)?tab:'all';
  try{sessionStorage.setItem('cmcc_bass_tab',_bassTab);}catch(_){}
  document.querySelectorAll('#bassTabs .subtab').forEach(b=>{
    const on=b.dataset.bt===_bassTab;
    b.classList.toggle('on',on);
    b.setAttribute('aria-selected',on?'true':'false');
  });
  document.querySelectorAll('#p-bass .panel[data-bass-group]').forEach(p=>{
    const show=_bassTab==='all'||p.dataset.bassGroup===_bassTab;
    p.classList.toggle('bass-hidden',!show);
  });
}

export function initBassTab(){
  _bassTab='all';
  try{
    const savedTab=sessionStorage.getItem('cmcc_bass_tab');
    if(BASS_TABS.includes(savedTab))_bassTab=savedTab;
  }catch(_){}
  setBassTab(_bassTab);
}

// ---------------- 福建活动各模块渲染 ----------------
export function renderLuck(){
  const STATE=store.state||{};
  const box=$('luckBox'); if(!box)return;
  const lk=STATE.luck||{};
  const keys=Object.keys(lk);
  if(!keys.length){
    setHtml(box,html`<div class="empty">还没有记录。点「只查状态」看剩余次数，或直接「刷任务并抽奖」。</div>`);
    return;
  }
  setHtml(box,keys.map(k=>{
    const a=lk[k]||{};
    const ps=a.last_prizes||[];
    const rows=ps.map(p=>{
      const isWin=p.name&&p.name.indexOf('谢谢')<0;
      return html`<tr><td>${p.at?p.at.slice(11):''}</td>
        <td class="${isWin?'tone-ok':'tone-sub'}">${isWin?'★ ':''}${p.name||'—'}</td>
        <td class="num">${p.life?p.life+' 天':''}</td></tr>`;
    });
    const summary=(a.last_draws!=null)?`　上次抽 ${a.last_draws} 次，中 ${a.last_wins||0} 次`:'';
    const today=todayLocal();
    const daySum=(a.day===today&&a.day_draws)?html`　<b>本程序今日抽 ${a.day_draws} 次，中 ${a.day_wins||0} 次</b>`:'';
    return html`<div class="mt-14">
      <div class="fbhead"><span>${k} · ${a.phone?mask(a.phone):''}</span><span>剩余次数 <b>${a.surplus!=null?a.surplus:'—'}</b>${daySum}${summary}</span><code>${a.updated||''}</code></div>
      ${ps.length?html`<div class="tablewrap"><table class="fbtab"><thead><tr><th class="col-time-sm">时间</th><th>奖品</th><th class="col-num num">有效期</th></tr></thead><tbody>${rows}</tbody></table></div>`:''}
    </div>`;
  }));
}

export function renderXunbao(){
  const STATE=store.state||{};
  const box=$('xunbaoBox'); if(!box)return;
  const xb=STATE.xunbao||{};
  const keys=Object.keys(xb);
  if(!keys.length){
    setHtml(box,html`<div class="empty">还没有记录。点「只查进度」看走到第几格。</div>`);
    return;
  }
  const today=todayLocal();
  setHtml(box,keys.map(k=>{
    const a=xb[k]||{};
    const seq=a.seq||0, total=a.total||29;
    const pct=total?Math.min(100,Math.round(seq/total*100)):0;
    const daySum=(a.day===today&&a.day_rolls)?html`　<b>本程序今日投 ${a.day_rolls} 次，中 ${a.day_prizes||0} 个</b>`:'';
    const ps=(a.last_prizes||[]);
    const tasks=(a.tasks||[]);
    const taskTxt=tasks.length?`任务：${tasks.filter(t=>!t.done).map(t=>t.name).join('、')||'全完成'}`:'';
    return html`<div class="mt-14">
      <div class="fbhead"><span>${k} · ${a.phone?mask(a.phone):''}</span><span>进度 <b>第 ${seq}/${total} 格</b> · 骰子 <b>${a.dice!=null?a.dice:'—'}</b>${a.treasure_claim_required?html` · <b class="tone-warn">★ 宝箱待开</b>`:''}${daySum}</span><code>${a.updated||''}</code></div>
      <div class="pbar"><div class="pbar-fill ok" style="width:${pct}%"></div></div>
      ${taskTxt?html`<div class="muted">${taskTxt}</div>`:''}
      ${ps.length?html`<div class="tablewrap"><table class="fbtab"><thead><tr><th>最近到手</th></tr></thead><tbody>${ps.map(n=>html`<tr><td>${n}</td></tr>`)}</tbody></table></div>`:''}
    </div>`;
  }));
}

export function nextDailyTime(tiers){
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

export function renderDaily(){
  const STATE=store.state||{};
  const box=$('dailyBox'); if(!box)return;
  const dd=STATE.daily||{};
  const keys=Object.keys(dd).filter(k=>dd[k]&&((dd[k].tiers&&dd[k].tiers.length)||dd[k].sign));
  if(!keys.length){
    setHtml(box,html`<div class="empty">还没有记录。跑一次 BASS 任务后，这里会显示三档奖励的领取状态和福袋进度。</div>`);
    return;
  }
  setHtml(box,keys.map(k=>{
    const a=dd[k], tiers=a.tiers||[];
    const got=tiers.filter(t=>t.status==='RECEIVED').length;
    const nt=nextDailyTime(tiers);
    const chips=tiers.map(t=>{
      const ok=t.status==='RECEIVED', cur=t.current&&!ok;
      const tone=ok?'tone-ok':(cur?'tone-warn':'tone-sub');
      const mark=ok?'✓':(cur?'●':'○');
      return html`<span class="daily-chip ${tone}">${mark} ${t.name} ${t.fodder}饲料${t.at?html` <code>${t.at.slice(11)}</code>`:''}</span>`;
    });
    const lk=a.lucky||{};
    const sg=a.sign||{};
    const fr=a.feed_reward;
    const frLine=fr?html`<div class="warnbox fr-warn">⚑ 投喂累计奖励 <b>${fr.fodder}</b> 饲料可领未领 · ${fr.expire} 过期</div>`:'';
    const pct=lk.need?Math.min(100,Math.round((lk.current/lk.need)*100)):0;
    const bar=lk.need?html`<div class="pbar"><div class="pbar-fill${lk.triggered?' ok':''}" style="width:${pct}%"></div></div>`:'';
    const signLine=sg.cycle!=null?html`<div class="daily-sign">
        <span class="${sg.today?'tone-ok':(sg.open?'tone-warn':'tone-sub')}">${sg.today?'✓ 今日已签':(sg.open?'● 待签':'○ 未到签到时间')}</span>
        　连签第 <b>${sg.cycle}</b> 天${sg.today_fodder?`　今日 ${sg.today_fodder}g`:''}${sg.next_fodder?` · 明日 ${sg.next_fodder}g`:''}${sg.begin_hour?html`　<code>${sg.begin_hour}:00</code> 开放`:''}
      </div>`:'';
    return html`<div class="mb-16">
      <div class="fbhead"><span>${k} · 已领 ${got}/${tiers.length} 档</span><span>${nt?html`下一档 <b class="tone-warn">${nt}</b>`:'今日已领完'}</span></div>
      ${frLine}
      ${signLine}
      <div class="daily-chips">${chips}</div>
      <div class="muted mt-8">福袋 ${lk.current||0}/${lk.need||0}${lk.more!=null?`　还差 ${lk.more} 次喂食`:''}${lk.triggered?html`　<b class="tone-ok">已触发！</b>`:''}</div>
      ${bar}
    </div>`;
  }));
}

export function nextRun(cycle,lastDone){
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
  return k<=todayLocal()?html`<span class="tone-ok">今天可做</span>`:k;
}

export function renderFlowbank(){
  const STATE=store.state||{};
  const box=$('fbTasks'); if(!box)return;
  const fb=STATE.flowbank||{};
  const keys=Object.keys(fb).filter(k=>fb[k]&&fb[k].tasks&&Object.keys(fb[k].tasks).length);
  if(!keys.length){
    setHtml(box,html`<div class="empty">还没有记录。在「手动执行一次」里勾上「流量银行任务」跑一次，这里就会列出每个任务的周期、奖励和下次可做时间。</div>`);
    return;
  }
  const badge=s=>s==='1'?html`<span class="badge ok">已完成</span>`
    :(s==='2'?html`<span class="badge warn">进行中</span>`
    :html`<span class="badge signed">可做</span>`);
  setHtml(box,keys.map(k=>{
    const acc=fb[k]||{},tasks=acc.tasks||{};
    const ids=Object.keys(tasks).sort((a,b)=>((tasks[a].cycle||9)-(tasks[b].cycle||9)));
    const rows=ids.map(id=>{
      const t=tasks[id];
      const cyc=t.cycle===1?'每月一次':(t.cycle===2?'每周一次':'不限');
      const flow=t.flow?(`+${t.flow}${t.unit==='M'?'MB':t.unit}`):'—';
      return html`<tr><td>${t.title||('#'+id)}</td><td>${cyc}</td><td>${flow}</td>
        <td>${badge(t.status)}</td><td><code>${t.last_done||'—'}</code></td><td>${nextRun(t.cycle,t.last_done)}</td></tr>`;
    });
    return html`<div class="mb-16">
      <div class="fbhead">${k} · 最后更新 ${acc.updated||'—'}</div>
      <div class="tablewrap"><table class="fbtab"><thead><tr><th>任务</th><th class="col-state">周期</th><th class="col-num">奖励</th><th class="col-state">状态</th><th class="col-ts">上次完成</th><th class="col-time">下次可做</th></tr></thead>
      <tbody>${rows}</tbody></table></div></div>`;
  }));
}

// ---------------- 秒杀状态与时间窗口提醒 ----------------
export function renderSkWindow(){
  const el=$('skWindow'); if(!el)return;
  const n=new Date(), mins=n.getHours()*60+n.getMinutes();
  const show=mins>=13*60||mins<11*60;
  setHidden(el,!show);
  if(show){
    setHtml(el,mins>=13*60
      ? html`今天的场次（12:00~13:00）已结束。现在点「等到点自动抢」会<b>立刻出手并被拒</b>，请明天再来，或交给 11:55 的计划任务。`
      : html`现在距开抢还早。点「等到点自动抢」会一直占用运行槽等到 12:00，期间别的操作都做不了；建议交给 11:55 的计划任务。`);
  }
}

export function renderSeckillInfo(){
  const STATE=store.state||{};
  const skS=$('skSess');
  if(skS){
    const ss=(STATE.seckill_sessions||[]);
    if(!ss.length){setHtml(skS,html`<span class="tone-warn">暂无会话 —— 请先跑一次 qwhdhub 签到</span>`);}
    else{
      setHtml(skS,ss.map(x=>{
        const t=x.at?x.at.replace('T',' ').slice(5,16):'?';
        let tail=html`<span class="muted">未知</span>`;
        const left=sessLeftMin(x);
        if(left!==null&&left!==undefined){
          if(left<=0) tail=html`<span class="sk-left tone-bad" data-exp-at="${x.expires_at||''}">已过期</span>`;
          else if(left<60) tail=html`<span class="sk-left tone-warn" data-exp-at="${x.expires_at||''}">剩 ${left} 分钟</span>`;
          else tail=html`<span class="sk-left tone-ok" data-exp-at="${x.expires_at||''}">剩 ${Math.round(left/60)} 小时</span>`;
        }
        return html`<span class="pill sk-pill">${x.phone?mask(x.phone):x.file} · ${t} · ${tail}</span>`;
      }));
    }
  }
  const tl=$('tlBox');
  if(tl){
    const ts=(STATE.token_life||[]);
    if(!ts.length){setHtml(tl,html`<span class="muted">还没有探测数据。导入 HAR 后由计划任务每 2 分钟自动探测。</span>`);}
    else{
      setHtml(tl,ts.map(x=>{
        let tag;
        if(x.state==='alive') tag=html`<span class="tone-ok">有效</span>`;
        else if(x.state==='expired') tag=html`<span class="tone-bad">已失效</span>`;
        else tag=html`<span class="tone-warn">未确认</span>`;
        const iss=x.issued_at?x.issued_at.replace('T',' ').slice(5,16):'?';
        const age=tokenAge(x);
        const ageTxt=age==null?'':html` · 已用 <b class="tl-age ${age>=25?'tone-warn':'tone-inherit'}" data-age-issued="${x.issued_at||''}">${age} 分钟</b>`;
        return html`<div class="pill tl-pill">
          <b>${x.phone?mask(x.phone):x.key}</b> · ${tag}${ageTxt}
          <span class="muted">签发 ${iss} · ${x.conclusion||''}</span>
        </div>`;
      }));
    }
  }
  renderSkWindow();
}

// ---------------- 消消乐状态卡片 ----------------
export function renderXxl(){
  const STATE=store.state||{};
  const box=$('xxlCards'); if(!box)return;
  const xxlMap=STATE.xxl||{};
  const bassAccts=(STATE.raw_accounts||[]).filter(a=>a.plat==='BASS');
  const seen=new Set(bassAccts.map(a=>a.key));
  const extra=Object.keys(xxlMap).filter(k=>!seen.has(k)).map(k=>({key:k,phone:(xxlMap[k]||{}).phone||''}));
  const list=bassAccts.concat(extra);
  if(!list.length){
    setHtml(box,html`<div class="empty">还没有福建活动账号，请先在「凭证与账号」添加。</div>`);
    return;
  }
  const today=todayLocal();
  setHtml(box,html`<div class="act-cards">${list.map(acc=>{
    const x=xxlMap[acc.key];
    const phone=acc.phone||(x&&x.phone)||'';
    const title=`${acc.key}${phone?' · '+mask(phone):''}`;
    if(!x){
      return html`<article class="act-card is-empty">
        <div class="act-card-head"><span class="acct-name">${title}</span><div class="acct-right"><span class="badge na">未查询</span></div></div>
        <div class="act-card-metrics">
          <div class="act-metric"><div class="k">剩余体力</div><div class="v muted">—</div></div>
          <div class="act-metric"><div class="k">今日通关</div><div class="v muted">—</div></div>
          <div class="act-metric"><div class="k">当前关卡</div><div class="v muted">—</div></div>
        </div>
        <div class="act-details">选中此账号并点「只读查剩余次数」后显示实时体力与关卡。</div>
      </article>`;
    }
    const isToday=x.day===today;
    const stamina=x.stamina;
    const todayPlay=isToday?(x.today_play!=null?x.today_play:0):0;
    const level=x.level;
    let badge=html`<span class="badge na">已查询</span>`;
    if(isToday&&x.reached_limit)badge=html`<span class="badge ok">今日已满</span>`;
    else if(typeof stamina==='number'&&stamina>0)badge=html`<span class="badge warn">可跑 ${stamina} 局</span>`;
    else if(stamina===0)badge=html`<span class="badge na">体力用尽</span>`;

    const rounds=x.last_rounds||[];
    const notes=[];
    if(x.exchanged>0)notes.push(`本次换体力 +${x.exchanged} 点`);
    if(x.gold>0)notes.push(`剩余金币 ${x.gold}`);
    if(!isToday&&x.day)notes.push(`上次记录 ${x.day}`);
    const hasDetails=rounds.length>0||notes.length>0;
    const summaryTxt=rounds.length?`展开明细（最近 ${rounds.length} 局）`:'展开明细';
    return html`<article class="act-card">
      <div class="act-card-head">
        <span class="acct-name">${title}</span>
        <div class="acct-right">${badge}<span>${x.updated||''}</span></div>
      </div>
      <div class="act-card-metrics">
        <div class="act-metric"><div class="k">剩余体力</div><div class="v${typeof stamina==='number'&&stamina>0?' tone-warn':''}">${stamina!=null?html`${stamina}<small> 点</small>`:'—'}</div></div>
        <div class="act-metric"><div class="k">今日通关</div><div class="v${isToday&&x.reached_limit?' tone-ok':''}">${html`${todayPlay}<small> 局</small>`}</div></div>
        <div class="act-metric"><div class="k">当前关卡</div><div class="v">${level!=null?html`第 ${level}<small> 关</small>`:'—'}</div></div>
      </div>
      ${hasDetails?html`<details class="act-details">
        <summary>${summaryTxt}</summary>
        <div class="act-details-body">
          ${rounds.map(r=>html`<div class="act-detail-row"><span>${(r.at||'').slice(11,16)} · 第 ${r.level} 关（${r.star}★）</span><span class="${r.won?'tone-ok':''}">${r.won?'★ ':''}${r.prize||'谢谢参与'}</span></div>`)}
          ${notes.length?html`<div>${notes.join(' · ')}</div>`:''}
        </div>
      </details>`:''}
    </article>`;
  })}</div>`);
}
