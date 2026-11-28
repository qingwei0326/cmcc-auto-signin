// ---------------- 环境栏 & 导航 / 福建子标签角标 ----------------
function renderEnv(){
  const env=STATE.env||{};
  $('envBox').innerHTML=env.ok===false
    ? `<div class="envbar"><b>任务暂时跑不了</b>\n${esc(env.msg)}</div>` : '';
  $('hsub').textContent=`${(STATE.accounts||[]).length} 个号码 · ${(STATE.raw_accounts||[]).length} 份配置`;
}

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
  const hasDailyOpen=Object.values(STATE.daily||{}).some(v=>v&&v.sign&&v.sign.open&&!v.sign.today);
  const hasXbChest=Object.values(STATE.xunbao||{}).some(v=>v&&v.treasure_claim_required);
  setNavBadge('bass',(hasFr||hasXbChest)?'warn':'',hasFr?'有投喂累计奖励待领':(hasXbChest?'有寻宝宝箱待开':''));

  const btDaily=$('bt-dot-daily'), btPlay=$('bt-dot-play');
  if(btDaily)btDaily.classList.toggle('on',!!(hasFr||hasDailyOpen));
  if(btPlay)btPlay.classList.toggle('on',!!hasXbChest);

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

// ---------------- 总览：现在需要你做什么（带直达操作） ----------------
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
  const lvCls=items.some(x=>x[0]==='bad')?'lv-bad':(items.some(x=>x[0]==='warn')?'lv-warn':'lv-ok');
  box.innerHTML=`<div class="todo ${lvCls}"><div class="th">现在需要你做什么<span class="todo-cnt">${items.length} 项</span></div>`+
    items.map(([lv,t,actHtml])=>`<div class="it"><span class="dot" style="background:var(--${lv})"></span><span class="txt">${t}</span>${actHtml||''}</div>`).join('')+'</div>';
}

// ---------------- 总览：KPI + 双平台账号卡片 ----------------
function renderKpiAndCards(){
  const S=STATE.stats||{}, accts=STATE.raw_accounts||[], aw=STATE.awards||{};
  let okTok=0,flowM=0;
  const days={};
  accts.forEach(a=>{const s=a.stats||S[a.stats_key||(a.plat+':'+a.key)]||S[a.key]||{};
    flowM+=s.flow_mb_month||0;
    if(tokState(a,s)==='ok')okTok++;
    (s.history||[]).forEach(h=>{days[h.date]=Math.max(days[h.date]||0,h.flow_mb||0);});});
  // 并入到账台账按日流量（含转盘/寻宝/领奖），取较大值避免重复叠加也不漏抽奖
  Object.values(aw.by_day||{}).forEach(map=>{
    Object.keys(map||{}).forEach(d=>{days[d]=Math.max(days[d]||0,map[d]||0);});
  });
  const dks=Object.keys(days).sort().slice(-7);
  const spark=(map,keys,colorVar)=>{const vals=keys.map(d=>map[d]||0);
    if(vals.length<2||!vals.some(v=>v>0))return '';
    const mx=Math.max(...vals),n=vals.length;
    const ptsArr=vals.map((v,i)=>`${(i*100/(n-1)).toFixed(1)},${(24-21*v/mx).toFixed(1)}`);
    const pts=ptsArr.join(' ');
    const area=`0,27 ${pts} 100,27`;
    return `<svg class="spark" viewBox="0 0 100 28" preserveAspectRatio="none"><polygon class="spark-area" fill="var(--${colorVar})" points="${area}"/><polyline points="${pts}"/></svg>`;};
  const spF=spark(days,dks,'acc');
  const awFlowM=((aw.month)||{}).flow_mb||0;
  const flowMShow=Math.max(flowM,awFlowM);
  const awMonth=(aw.month||{});
  const feeM=awMonth.fee_count||0;
  const feeYuan=awMonth.fee_yuan||0;
  const feeAll=aw.fee_total||0;
  const feeYuanAll=aw.fee_yuan_total||0;
  const feeDay=aw.fee_day||{};
  const fdk=Object.keys(feeDay).sort().slice(-7);
  const spFee=spark(feeDay,fdk,'ok');
  // 本日已得：单独做成和「本月已得」一样的卡片，放在隔壁一一对应
  const awToday=((STATE.awards||{}).today)||{};
  const dayFlow=awToday.flow_mb||0;
  const dayFee=awToday.fee_yuan||0, dayFeeCnt=awToday.fee_count||0;
  $('sumbar').innerHTML=`
    <div class="kpi kpi-flow" title="签到 / 领奖 / 转盘合计（付费流量包不计入）"><div class="k">本月已得流量</div><div class="v">${flowMShow>=1024?(flowMShow/1024).toFixed(2)+'<small>GB</small>':flowMShow.toFixed(0)+'<small>MB</small>'}</div><div class="c">签到 / 领奖 / 转盘合计</div>${spF}</div>
    <div class="kpi kpi-flow" title="今天到手的流量，含签到 / 领奖 / 转盘"><div class="k">本日已得流量</div><div class="v">${dayFlow>=1024?(dayFlow/1024).toFixed(2)+'<small>GB</small>':dayFlow.toFixed(0)+'<small>MB</small>'}</div><div class="c">今天到手（含抽奖）</div></div>
    <div class="kpi kpi-fee" title="无门槛话费券合计，话费手气红包不计入"><div class="k">本月已得话费</div><div class="v">${fy(feeYuan)}<small>元</small></div><div class="c">共 ${feeM} 张 · 历史 ${feeAll} 张 / ¥${fy(feeYuanAll)}</div>${spFee}</div>
    <div class="kpi kpi-fee" title="今天无门槛话费券合计，话费手气红包不计入"><div class="k">本日已得话费</div><div class="v">${fy(dayFee)}<small>元</small></div><div class="c">今日共 ${dayFeeCnt} 张无门槛券</div></div>
    <div class="kpi kpi-sys"><div class="k">凭证可用</div><div class="v">${okTok}<small>/ ${accts.length} 份</small></div><div class="c">每号两份（全国 + 福建）</div></div>
    <div class="kpi kpi-sys"><div class="k">账号数</div><div class="v">${STATE.accounts.length}<small>个号码</small></div><div class="c">过期需重新抓包导入</div></div>`;
  // 运行记录统计条（分组渲染账号卡）
  $('cards').innerHTML=(STATE.accounts||[]).map(g=>{
    const platEntries=Object.entries(g.plats||{});
    let qSigned=false, bSigned=false, okCount=0;
    const plats=platEntries.map(([plat,a])=>{
      const s=a.stats||{};
      const isQ=plat==='qwhdhub';
      const tkState=tokState(a,s);
      if(tkState==='ok')okCount++;
      const sDate=isQ?s.today_signed_qwhd:s.today_signed_bass;
      if(sDate===todayLocal()){if(isQ)qSigned=true;else bSigned=true;}
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
          ${signedBadge(sDate)}
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
            <button type="button" class="btn sm" onclick="act({action:'qwhd_run',key:${jsArg(a.key)},claim:1})">签到+领奖</button>
            <button type="button" class="btn sm" onclick="act({action:'voucher_run',key:${jsArg(a.key)}})" title="领券中心：按开放时间领未领的免费券">领券</button>`
           :`<button type="button" class="btn sm pri" onclick="act({action:'bass_run',key:${jsArg(a.key)}})">签到</button>
            <button type="button" class="btn sm" onclick="act({action:'bass_run',key:${jsArg(a.key)},dry:1})">只读检查</button>
            <button type="button" class="btn sm" onclick="act({action:'bass_run',key:${jsArg(a.key)},claim:1,roll:1})">领奖+骰子</button>`}
        </div>
        ${hist.length&&hasData?`<div class="hist"><div class="t">近 7 天记录（流量 / 任务）</div><div class="bars">${bars}</div><div class="bardates">${dates}</div></div>`:`<div class="hist"><div class="t">近 7 天</div><div class="empty">暂无记录</div></div>`}
      </div>`;}).join('');
    const totalP=platEntries.length;
    const headBadges=(totalP?`<span class="badge ${okCount===totalP?'ok':(okCount>0?'warn':'bad')}">${okCount}/${totalP} 凭证就绪</span>`:'')
      +((qSigned||bSigned)?'<span class="badge signed">今日已有签到</span>':'');
    return `<div class="acct">
      <div class="acct-head"><span class="phone" title="${esc(g.phone)}">${esc(mask(g.phone))}</span><div class="acct-meta">${headBadges}</div></div>
      <div class="acct-body">${plats||'<div class="plat"><div class="empty">未配置任何平台</div></div>'}</div>
    </div>`;
  }).join('');
}

// ---------------- 总览：到账台账折叠 / 筛选 ----------------
const AW_PAGE=8;          // 默认只显示最近 8 条，避免台账占满首屏
let awExpanded=false;
function onAwFilter(){awExpanded=false;renderAwTable();}
function toggleAwAll(){awExpanded=!awExpanded;renderAwTable();}

function renderAwSection(){
  const aw=STATE.awards||{}, awT=aw.today||{};
  // 本月口径已在顶部 KPI；这里只留今日，避免同一屏重复两遍
  if($('awSum'))$('awSum').innerHTML=`
    <div class="cell"><div class="k">今日已得流量</div><div class="v">${awFmt(awT.flow_mb||0)}</div></div>
    <div class="cell"><div class="k">今日已得话费</div><div class="v">¥${fy(awT.fee_yuan)}<small> · ${awT.fee_count||0} 张</small></div></div>`;
  renderAwFilters();
  renderAwTable();
}

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
        <td class="muted">${esc((r.at||'').slice(5,16))}</td>
        <td>${esc(r.account?mask(r.account):'—')}</td>
        <td>${esc(r.source||'')}</td>
        <td>${esc(r.name||'')}${r.kind==='fee'?' <span class="tag">话费券</span>':''}</td>
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

// ---------------- 总览：运行记录 & 效率 ----------------
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
        <td class="muted">${esc(r.ts)}</td><td>${esc(r.label)}</td>
        <td class="num">${esc(r.sec>=60?Math.floor(r.sec/60)+'分'+Math.round(r.sec%60)+'秒':r.sec+'秒')}</td>
        <td class="${r.ok?'ok':'bad'}">${r.ok?'成功':'失败'}</td></tr>`).join('')
    : '<tr><td style="color:var(--sub);padding:14px">暂无运行记录 — 跑一次签到或刷新状态后自动记录</td></tr>';
}
