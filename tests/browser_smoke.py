# -*- coding: utf-8 -*-
"""浏览器冒烟测试（手动跑，不在 unittest discover 里）。

    python tests/browser_smoke.py

需要 playwright 与一个可用浏览器（优先本机 Edge，无需下载 chromium）：
    pip install playwright

它在真实浏览器里打开控制台，抓 **JS 运行时错误**、首屏耗时、手机端横向溢出、
忙态置灰、弹层与回填等静态断言查不出来的问题。
**不触发任何真实签到请求**：只点只读/校验类交互，忙态用 setBusy 模拟。

截图落在 .qwen/tmp/shot_*.png。退出码非 0 表示有检查项失败。
"""
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CMCC_DASH_PORT", "8799")
import dashboard  # noqa: E402

OUT = ROOT / ".qwen" / "tmp"
OUT.mkdir(parents=True, exist_ok=True)
fails = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else " FAIL ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


srv, err = dashboard.make_server()
assert srv is not None, err
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d" % srv.server_address[1]

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    try:
        browser = p.chromium.launch(headless=True, channel="msedge")
    except Exception as e:
        print("msedge 不可用（%s），改用自带 chromium" % str(e)[:80])
        browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 920})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    page.on("console", lambda m: errors.append("console." + m.type + ": " + m.text)
            if m.type == "error" else None)

    t0 = time.time()
    page.goto(URL, wait_until="load")
    page.wait_for_selector("#todoBox .todo", timeout=20000)
    paint = time.time() - t0
    check("首屏 3 秒内渲染", paint < 3.0, "%.2fs" % paint)
    page.wait_for_selector("#schedBox .stab", state="attached", timeout=20000)
    page.wait_for_timeout(600)
    check("无 JS 运行时错误（首屏）", not errors, errors[:3])

    has_acct = page.evaluate("()=>(STATE.raw_accounts||[]).length>0")
    check("待办条有内容", page.eval_on_selector("#todoBox", "e=>e.innerText.trim().length") > 10)
    check("KPI 四张", page.eval_on_selector_all("#sumbar .kpi", "e=>e.length") == 4)
    check("定时任务表已建", page.eval_on_selector_all("#schedBox .stab tbody tr", "e=>e.length") == 7)
    if has_acct:
        check("账号卡渲染", page.eval_on_selector_all("#cards .acct", "e=>e.length") >= 1)
        check("卡片带数据时间", "数据截至" in page.inner_text("#cards"))
        check("账号表已建", page.eval_on_selector_all("#acctBox tbody tr", "e=>e.length") >= 1)
    else:
        check("空状态给出下一步", "新建账号" in page.inner_text("#todoBox"))
    # 到账台账：用户只看「到手多少流量」，这里必须真渲染出数字而不是空白
    check("到账台账汇总只留今日两项", page.eval_on_selector_all("#awSum .cell", "e=>e.length") == 2)
    check("顶部 KPI 保留本月口径",
          "本月已得流量" in page.inner_text("#sumbar") and "本月已得话费" in page.inner_text("#sumbar"))
    check("台账不再重复本月口径",
          "本月已得流量" not in page.inner_text("#awSum") and "本月已得话费" not in page.inner_text("#awSum"))
    check("台账有今日口径", "今日已得流量" in page.inner_text("#awSum"))
    check("台账未出现充值类券", "充值9.9折" not in page.inner_text("#awTab"))
    # 信息层级：账号状态排在到账台账前面
    order = page.evaluate("""()=>['cards','awTab','runtab'].map(id=>{
        const e=document.getElementById(id);return e?e.getBoundingClientRect().top:-1;})""")
    check("总览区块顺序正确", order[0] <= order[1] < order[2], order)
    # 台账折叠 / 筛选：只改前端状态，不触发任何任务
    total = page.evaluate("()=>((STATE.awards||{}).recent||[]).length")
    shown = page.eval_on_selector_all("#awTab tr", "e=>e.length")
    check("台账默认折叠为 8 条", shown <= 9, "rows=%d total=%d" % (shown - 1, total))
    btn_shown = page.eval_on_selector("#awMoreBtn", "e=>getComputedStyle(e).display!=='none'")
    check("展开按钮按数据量显隐", btn_shown == (total > 8), "total=%d" % total)
    if total > 8:
        page.evaluate("()=>{awExpanded=true;renderAwTable();}")
        check("展开后显示全部", page.eval_on_selector_all("#awTab tr", "e=>e.length") == total + 1)
        check("按钮变为收起", "收起" in page.inner_text("#awMoreBtn"))
        page.evaluate("()=>{awExpanded=false;renderAwTable();}")
        check("收起后恢复 8 条", page.eval_on_selector_all("#awTab tr", "e=>e.length") == 9)
    acct_opts = page.eval_on_selector_all("#awAcct option", "es=>es.map(e=>e.value)")
    src_opts = page.eval_on_selector_all("#awSource option", "es=>es.map(e=>e.value)")
    check("号码/来源筛选含全部项", acct_opts[:1] == [""] and src_opts[:1] == [""],
          (acct_opts[:3], src_opts[:3]))
    if total > 0:
        picked = page.evaluate("""()=>{const r=((STATE.awards||{}).recent||[])[0];
            const a=r&&r.account;if(!a)return '';
            const s=document.getElementById('awAcct');s.value=a;onAwFilter();
            return a;}""")
        if picked:
            expect = page.evaluate("a=>((STATE.awards||{}).recent||[]).filter(r=>r.account===a).length", picked)
            got = page.eval_on_selector_all("#awTab tr", "e=>e.length")
            check("按号码筛选生效", min(got - 1, 8) == min(expect, 8), "got=%d expect=%d" % (got - 1, expect))
            page.evaluate("()=>{document.getElementById('awAcct').value='';onAwFilter();}")
    page.screenshot(path=str(OUT / "shot_dash.png"), full_page=True)

    for key, title in [("token", "凭证与账号"), ("bass", "福建活动"), ("xxl", "消消乐"),
                       ("seckill", "秒杀抢券"), ("games", "拓展活动"),
                       ("sched", "定时任务"), ("logs", "日志")]:
        page.click('nav button[data-p="%s"]' % key)
        page.wait_for_timeout(250)
        got = page.inner_text("#pageTitle")
        check("页面标题 " + key, got.endswith(title), got)
    check("切页无 JS 错误", not errors, errors[:3])

    # 定时任务页：提示条不堆叠（连渲染 5 次）
    page.click('nav button[data-p="sched"]')
    page.evaluate("()=>{for(let i=0;i<5;i++)render();}")
    n = page.eval_on_selector_all("#schedBox .schedtip", "e=>e.length")
    check("提示条最多一条", n <= 1, "count=%d" % n)
    page.screenshot(path=str(OUT / "shot_sched.png"), full_page=True)

    # 日志弹层（只读本地文件）
    page.click('#schedBox button[onclick="openLog(\'sign\')"]')
    page.wait_for_timeout(700)
    check("日志弹层打开", page.eval_on_selector("#modal", "e=>e.classList.contains('on')"))
    check("日志有内容或明确说明", len(page.inner_text("#mBody")) > 10)
    page.screenshot(path=str(OUT / "shot_log.png"))
    page.click('#modal .bh button')
    page.wait_for_timeout(200)
    check("弹层可关闭", not page.eval_on_selector("#modal", "e=>e.classList.contains('on')"))

    # 新建账号：非法手机号必须被挡住，且不落盘
    page.click('nav button[data-p="token"]')
    page.click('button[onclick="openNewAccount()"]')
    page.wait_for_timeout(200)
    page.fill("#naPhone", "123")
    page.click('button[onclick="saveNewAccount()"]')
    page.wait_for_timeout(600)
    check("非法手机号被拒", "11 位" in page.inner_text("#errBox"), page.inner_text("#errBox")[:60])
    check("错误条可见（不只 toast）",
          page.eval_on_selector("#errBox", "e=>getComputedStyle(e).display") != "none")
    page.click('#modal .bh button')

    if has_acct:
        page.click('#acctBox tbody tr:first-child button')
        page.wait_for_timeout(250)
        check("编辑弹层打开", page.eval_on_selector("#modal", "e=>e.classList.contains('on')"))
        check("手机号输入框存在", page.eval_on_selector_all("#eaPhone", "e=>e.length") == 1)
        page.screenshot(path=str(OUT / "shot_edit.png"))
        page.click('#modal .bh button')

    # 空闲时停止按钮置灰；直接调接口也要有明确反馈
    check("停止按钮空闲时禁用", page.eval_on_selector("#btnStop", "e=>e.disabled"))
    page.evaluate("()=>act({action:'stop'})")
    page.wait_for_timeout(600)
    check("空闲停止有反馈", "没有正在运行" in page.inner_text("#toast"), page.inner_text("#toast"))

    # 忙态：按钮置灰 + 顶栏 pill（不真的跑任务）
    page.evaluate("()=>setBusy(true,'测试任务')")
    dis = page.eval_on_selector_all('.content .btn:not([data-nobusy])', "es=>es.filter(e=>e.disabled).length")
    tot = page.eval_on_selector_all('.content .btn:not([data-nobusy])', "es=>es.length")
    check("忙时动作按钮全部置灰", dis == tot and tot > 5, "%d/%d" % (dis, tot))
    check("顶栏显示运行中", "运行中" in page.inner_text("#taskPillTop"))
    check("忙时停止可点", not page.eval_on_selector("#btnStop", "e=>e.disabled"))
    page.evaluate("()=>setBusy(false,'')")
    check("空闲后恢复可点",
          page.eval_on_selector_all('.content .btn:not([data-nobusy])', "es=>es.every(e=>!e.disabled)"))

    # 探测关卡回填
    page.click('nav button[data-p="xxl"]')
    page.evaluate("""()=>{capStart('xxl',lines=>{const m=lines.join('\\n').match(/自动探测当前关卡:\\s*(\\d+)/);
        if(m){document.getElementById('xLevel').value=m[1];}});
        capPush('  自动探测当前关卡: 63（共 5 条通关记录）');capEnd();}""")
    check("探测结果回填关卡号", page.input_value("#xLevel") == "63", page.input_value("#xLevel"))
    check("消消乐默认不勾", not page.eval_on_selector("#dashXxl", "e=>e.checked"))

    # 喂到用完：勾上就置灰轮数框，不用手填
    page.click('nav button[data-p="bass"]')
    page.wait_for_timeout(200)
    page.check("#bFeedAll")
    check("喂到用完置灰轮数框", page.eval_on_selector("#bFeed", "e=>e.disabled"))
    page.uncheck("#bFeedAll")
    check("取消后轮数框恢复", not page.eval_on_selector("#bFeed", "e=>e.disabled"))

    page.click('nav button[data-p="seckill"]')
    page.wait_for_timeout(200)
    check("秒杀结果区存在", page.eval_on_selector_all("#skBox", "e=>e.length") == 1)
    page.screenshot(path=str(OUT / "shot_seckill.png"), full_page=True)

    # 字号缩放
    page.click('button[aria-label="放大字号"]')
    page.wait_for_timeout(200)
    z = page.evaluate("()=>document.documentElement.style.zoom")
    check("字号可放大", str(z) == "1.1", z)
    page.click('button[aria-label="缩小字号"]')
    page.wait_for_timeout(200)
    check("字号可缩回", str(page.evaluate("()=>document.documentElement.style.zoom")) == "1")

    # 动态内容转义：伪造一份带 HTML 的 STATE，只重绘，不触发任何任务。
    # 注入的 img 若被当成元素插入，onerror 会置 window.__xss。
    page.evaluate("""() => {
        window.__origState = JSON.parse(JSON.stringify(STATE));
        const bad = '<img id="xss-probe" src="x" onerror="window.__xss=1">';
        const S = JSON.parse(JSON.stringify(STATE));
        const k = (S.raw_accounts && S.raw_accounts[0] && S.raw_accounts[0].key) || 'probe';
        S.eff = S.eff || {};
        S.eff.recent = [{ts: bad, label: bad, sec: 1, ok: true}];
        S.stats = S.stats || {};
        S.stats[k] = Object.assign({}, S.stats[k] || {},
            {last_reward: bad, fb_exchange: bad, stage: bad});
        S.luck = {probe: {last_prizes: [{at: '2026-11-22 15:00:00', name: bad, life: bad}]}};
        S.flowbank = {probe: {updated: bad, tasks: {1: {title: bad, cycle: 0, flow: 1,
            unit: 'MB', status: '0', last_done: bad}}}};
        S.daily = {probe: {tiers: [{name: bad, fodder: bad, at: '2026-11-22 15:00:00',
            status: 'PENDING', current: true, limit_at: bad}],
            feed_reward: {fodder: bad, expire: bad}}};
        S.seckill_sessions = [{phone: '', file: bad, at: bad, left_min: 1}];
        S.token_life = [{key: 'probe', phone: '', issued_at: bad,
            conclusion: bad, state: 'alive'}];
        S.raw_accounts = [{key: 'probe', file: bad, plat: 'qwhdhub',
            phone: '13800001234', token_state: 'filled'}];
        S.accounts = [{phone: '13800001234', plats: {qwhdhub: {
            key: 'probe', file: 'configs/probe.json', plat: 'qwhdhub',
            phone: '13800001234', stats: S.stats[k]}}}];
        STATE = S;
        render();
    }""")
    check("动态文本不会注入 HTML",
          page.evaluate("()=>!document.getElementById('xss-probe') && !window.__xss"))
    check("转义后原文仍完整可见",
          '<img id="xss-probe"' in page.inner_text("#runtab"),
          page.inner_text("#runtab")[:100])
    check("编辑按钮的内联参数未被截断",
          page.evaluate("()=>[...document.querySelectorAll('#acctBox button')].every("
                        "b=>{try{new Function(b.getAttribute('onclick'));return true}"
                        "catch(e){return false}})"))
    page.evaluate("()=>{STATE=window.__origState;render();}")

    # 手机端
    m = ctx.new_page()
    merr = []
    m.on("pageerror", lambda e: merr.append(str(e)))
    m.set_viewport_size({"width": 390, "height": 844})
    m.goto(URL, wait_until="load")
    m.wait_for_selector("#todoBox .todo", timeout=20000)
    m.wait_for_timeout(600)
    ow = m.evaluate("()=>({sw:document.documentElement.scrollWidth,iw:window.innerWidth})")
    check("手机端无整页横向溢出", ow["sw"] <= ow["iw"] + 1, ow)
    check("手机端能看到运行状态",
          m.eval_on_selector("#taskPillTop", "e=>getComputedStyle(e).display") != "none")
    check("手机导航可横向滑动", m.eval_on_selector(".sidebar nav", "e=>e.scrollWidth>e.clientWidth"))
    m.screenshot(path=str(OUT / "shot_mobile.png"), full_page=True)
    m.click('nav button[data-p="sched"]')
    m.wait_for_selector("#schedBox .tablewrap", timeout=20000)
    m.wait_for_timeout(400)
    ow2 = m.evaluate("()=>({sw:document.documentElement.scrollWidth,iw:window.innerWidth})")
    check("手机端定时任务页不溢出", ow2["sw"] <= ow2["iw"] + 1, ow2)
    check("表格在自己的容器里滚动",
          m.eval_on_selector("#schedBox .tablewrap", "e=>e.scrollWidth>e.clientWidth"))
    m.screenshot(path=str(OUT / "shot_mobile_sched.png"), full_page=True)
    check("手机端无 JS 错误", not merr, merr[:3])

    check("全程无 console 错误", not errors, errors[:5])
    browser.close()

srv.shutdown()
srv.server_close()
print("\n==== %s ====" % ("全部通过" if not fails else "失败 %d 项: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
