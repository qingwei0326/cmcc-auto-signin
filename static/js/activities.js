// ---------------- 下拉框 & 饲料实时提示 ----------------
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
  const a=(STATE.raw_accounts||[]).find(x=>x.plat==='BASS'&&x.key===k);
  const st=(a&&a.stats)||(STATE.stats||{})['BASS:'+k]||(STATE.stats||{})[k]||{};
  bf.textContent=st.fodder!=null?(`当前 ${st.fodder}g`+(st.stage?` · ${st.stage}`:'')):'';
}

// ---------------- 账号管理（新建 / 改手机号 / 单账号推送） ----------------
function renderAccounts(){
  const box=$('acctBox'); if(!box)return;
  const accts=STATE.raw_accounts||[];
  if(!accts.length){
    box.innerHTML='<div class="empty">还没有账号。点下面「新建账号」填手机号，再导入一次抓包文件即可。</div>';
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
    <div class="infobox">先建一份空配置，再导入抓包文件，凭证会自动填进去。文件名默认用手机号尾号，方便认。</div>
    <div class="frow"><label for="naPlat">平台</label>
      <select class="inp inp-md" id="naPlat">
        <option value="qwhdhub">全国签到（签到领流量）</option>
        <option value="BASS">福建活动（养成游戏）</option>
      </select></div>
    <div class="frow"><label for="naPhone">手机号</label><input class="inp inp-md" id="naPhone" placeholder="11 位手机号"></div>
    <div class="frow"><label for="naName">文件名</label><input class="inp inp-md" id="naName" placeholder="留空则用尾号，如 3239"></div>
    <div class="frow mt-12"><button type="button" class="btn pri" onclick="saveNewAccount()">创建</button>
      <button type="button" class="btn" onclick="closeModal()">取消</button></div>`);
}
async function saveNewAccount(){
  const r=await act({action:'account_new',plat:V('naPlat'),phone:V('naPhone'),name:V('naName')});
  if(r&&r.ok)closeModal();
}

function editAccount(file){
  const a=(STATE.raw_accounts||[]).find(x=>x.file===file)||{};
  openModal('编辑账号 · '+file,`
    <div class="infobox">手机号用于把抓包文件里的凭证归到正确的号上。推送配好后，任一账号失败会通知到手机。<b>推送框留空 = 不修改</b>；要清除请点「清除推送配置」。</div>
    <div class="frow"><label for="eaPhone">手机号</label><input class="inp inp-md" id="eaPhone" value="${esc(a.phone||'')}"></div>
    <div class="frow"><label for="eaBark">Bark 地址</label><input class="inp inp-lg" id="eaBark"
      placeholder="${a.has_bark?'已配置（留空则不修改）':'https://api.day.app/你的key'}"></div>
    <div class="frow"><label for="eaKey">Server酱</label><input class="inp inp-lg" id="eaKey"
      placeholder="${a.has_sendkey?'已配置（留空则不修改）':'SendKey'}"></div>
    <div class="frow"><label for="eaPushplus">PushPlus</label><input class="inp inp-lg" id="eaPushplus"
      placeholder="${a.has_pushplus?'已配置（留空则不修改）':'32 位十六进制令牌'}"></div>
    <div class="frow mt-12"><button type="button" class="btn pri" onclick="saveAccount(${jsArg(file)})">保存</button>
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

// ---------------- 福建活动子标签控制 ----------------
let _bassTab='all';
try{
  const savedTab=sessionStorage.getItem('cmcc_bass_tab');
  if(['all','daily','manual','play','flowbank'].includes(savedTab))_bassTab=savedTab;
}catch(_){}

function setBassTab(tab){
  _bassTab=['all','daily','manual','play','flowbank'].includes(tab)?tab:'all';
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
setBassTab(_bassTab);

// ---------------- 福建活动各模块渲染 ----------------
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
    return `<div class="mt-14">
      <div class="fbhead"><span>${esc(k)} · ${esc(a.phone?mask(a.phone):'')}</span><span>剩余次数 <b>${esc(a.surplus!=null?a.surplus:'—')}</b>${daySum}${summary}</span><code>${esc(a.updated||'')}</code></div>
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
    const taskTxt=tasks.length?`任务：${esc(tasks.filter(t=>!t.done).map(t=>t.name).join('、')||'全完成')}`:'';
    return `<div class="mt-14">
      <div class="fbhead"><span>${esc(k)} · ${esc(a.phone?mask(a.phone):'')}</span><span>进度 <b>第 ${esc(seq)}/${esc(total)} 格</b> · 骰子 <b>${esc(a.dice!=null?a.dice:'—')}</b>${a.treasure_claim_required?' · <b style="color:var(--warn)">★ 宝箱待开</b>':''}${daySum}</span><code>${esc(a.updated||'')}</code></div>
      <div class="pbar"><div class="pbar-fill ok" style="width:${pct}%"></div></div>
      ${taskTxt?`<div class="muted">${taskTxt}</div>`:''}
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
    const frLine=fr?`<div class="warnbox" style="margin:6px 0 8px">⚑ 投喂累计奖励 <b>${esc(fr.fodder)}</b> 饲料可领未领 · ${esc(fr.expire)} 过期</div>`:'';
    const pct=lk.need?Math.min(100,Math.round((lk.current/lk.need)*100)):0;
    const bar=lk.need?`<div class="pbar"><div class="pbar-fill${lk.triggered?' ok':''}" style="width:${pct}%"></div></div>`:'';
    const signLine=sg.cycle!=null?`<div style="font-size:.8125rem;margin-bottom:7px">
        <span style="color:${sg.today?'var(--ok)':(sg.open?'var(--warn)':'var(--sub)')}">${sg.today?'✓ 今日已签':(sg.open?'● 待签':'○ 未到签到时间')}</span>
        　连签第 <b>${esc(sg.cycle)}</b> 天${sg.today_fodder?`　今日 ${esc(sg.today_fodder)}g`:''}${sg.next_fodder?` · 明日 ${esc(sg.next_fodder)}g`:''}${sg.begin_hour?`　<code>${esc(sg.begin_hour)}:00</code> 开放`:''}
      </div>`:'';
    return `<div style="margin-bottom:16px">
      <div class="fbhead"><span>${esc(k)} · 已领 ${got}/${tiers.length} 档</span><span>${nt?`下一档 <b style="color:var(--warn)">${esc(nt)}</b>`:'今日已领完'}</span></div>
      ${frLine}
      ${signLine}
      <div style="font-size:.8125rem">${chips}</div>
      <div class="muted" style="margin-top:8px">福袋 ${esc(lk.current||0)}/${esc(lk.need||0)}${lk.more!=null?`　还差 ${esc(lk.more)} 次喂食`:''}${lk.triggered?'　<b style="color:var(--ok)">已触发！</b>':''}</div>
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
    :(s==='2'?'<span class="badge warn">进行中</span>'
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

// ---------------- 秒杀状态与时间窗口提醒 ----------------
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

function renderSeckillInfo(){
  const skS=$('skSess');
  if(skS){
    const ss=(STATE.seckill_sessions||[]);
    if(!ss.length){skS.innerHTML='<span style="color:var(--warn)">暂无会话 —— 请先跑一次 qwhdhub 签到</span>';}
    else{
      skS.innerHTML=ss.map(x=>{
        const t=x.at?x.at.replace('T',' ').slice(5,16):'?';
        let tail='<span class="muted">未知</span>';
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
    if(!ts.length){tl.innerHTML='<span class="muted">还没有探测数据。导入 HAR 后由计划任务每 2 分钟自动探测。</span>';}
    else{
      tl.innerHTML=ts.map(x=>{
        let tag;
        if(x.state==='alive') tag='<span style="color:var(--ok)">有效</span>';
        else if(x.state==='expired') tag='<span style="color:var(--bad)">已失效</span>';
        else tag='<span style="color:var(--warn)">未确认</span>';
        const iss=x.issued_at?x.issued_at.replace('T',' ').slice(5,16):'?';
        const age=tokenAge(x);
        const ageTxt=age==null?'':` · 已用 <b class="tl-age" data-age-issued="${esc(x.issued_at||'')}" style="color:${age>=25?'var(--warn)':'inherit'}">${age} 分钟</b>`;
        return `<div class="pill" style="display:flex;margin-bottom:6px;padding:8px 10px">
          <b>${esc(x.phone?mask(x.phone):x.key)}</b> · ${tag}${ageTxt}
          <span class="muted">签发 ${esc(iss)} · ${esc(x.conclusion||'')}</span>
        </div>`;
      }).join('');
    }
  }
  renderSkWindow();
}
