import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const htmlSrc = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');

const dom = new JSDOM(htmlSrc, { url: 'http://127.0.0.1:8789/' });
dom.window.HTMLElement.prototype.scrollIntoView = () => {};

globalThis.__CMCC_NO_AUTO_BOOT__ = true;
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.localStorage = dom.window.localStorage;
globalThis.sessionStorage = dom.window.sessionStorage;
globalThis.history = dom.window.history;
globalThis.Event = dom.window.Event;
globalThis.CustomEvent = dom.window.CustomEvent;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.Node = dom.window.Node;

const apiCalls = [];
let mockState = null;
let mockActionHandler = null;
let mockLogs = { lines: [], running: false, label: '' };

globalThis.fetch = async (url, init = {}) => {
  const u = String(url);
  if (u.startsWith('/api/state')) {
    return { json: async () => structuredClone(mockState) };
  }
  if (u.startsWith('/api/logs')) {
    return { json: async () => structuredClone(mockLogs) };
  }
  if (u.startsWith('/api/action')) {
    const body = JSON.parse(init.body || '{}');
    apiCalls.push(body);
    const res = mockActionHandler ? await mockActionHandler(body) : { ok: true, msg: 'ok' };
    return { json: async () => res };
  }
  if (u.startsWith('/api/tasklog')) {
    return { json: async () => ({ lines: ['10:00:00 [INFO] ok'], size: 1024 }) };
  }
  if (u.startsWith('/api/check_update')) {
    return { json: async () => ({ current: '1.0', latest: '1.0', has_update: false }) };
  }
  throw new Error('Unexpected fetch: ' + u);
};

const { boot, navTo, pullState, render } = await import('../../static/app.js');
const { store, setBusy, clearErr } = await import('../../static/js/core.js');
const { pullLogs, clearConsole } = await import('../../static/js/logs.js');

function makeSampleState() {
  const today = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const ymd = `${today.getFullYear()}-${p(today.getMonth() + 1)}-${p(today.getDate())}`;
  const recent = Array.from({ length: 10 }, (_, i) => ({
    at: `${ymd} 10:0${i}:00`,
    account: i % 2 === 0 ? '13800001234' : '13900005678',
    source: i % 2 === 0 ? '每日签到' : '转盘抽奖',
    name: i % 2 === 0 ? '100MB流量包' : '1元话费券',
    kind: i % 2 === 0 ? 'flow' : 'fee',
    size_mb: i % 2 === 0 ? 100 : 0,
    yuan: i % 2 === 0 ? 0 : 1,
  }));
  return {
    version: '1.5',
    env: { ok: true },
    task: { running: false, label: '' },
    raw_accounts: [
      { key: '1234', file: '1234.json', plat: 'qwhdhub', phone: '13800001234', token_state: 'filled', has_bark: false },
      { key: 'fj1234', file: 'fj1234.json', plat: 'BASS', phone: '13800001234', token_state: 'filled', has_bark: true },
    ],
    accounts: [
      {
        phone: '13800001234',
        plats: {
          qwhdhub: { key: '1234', file: '1234.json', plat: 'qwhdhub', phone: '13800001234', stats: { flow_mb_month: 500, sign_days: 5, last_run: '11-29 08:00', today_signed_qwhd: ymd } },
          BASS: { key: 'fj1234', file: 'fj1234.json', plat: 'BASS', phone: '13800001234', stats: { fodder: 750, stage: '2/6', last_run: '11-29 08:00', today_signed_bass: ymd } },
        },
      },
    ],
    stats: {},
    token_life: [{ key: '1234', phone: '13800001234', state: 'alive', issued_at: `${ymd} 08:00:00`, conclusion: '有效' }],
    seckill_sessions: [],
    daily: {},
    xunbao: {},
    luck: {},
    xxl: {
      fj1234: {
        phone: '13800001234',
        level: 68,
        stamina: 3,
        today_play: 2,
        reached_limit: false,
        gold: 0,
        exchanged: 1,
        day: ymd,
        updated: `${ymd} 08:12`,
        last_rounds: [
          { at: `${ymd} 08:10:00`, level: 67, star: 3, prize: '100MB流量包', won: true },
          { at: `${ymd} 08:11:00`, level: 68, star: 3, prize: '谢谢参与', won: false },
        ],
      },
    },
    flowbank: {},
    sched: [{ name: 'MobileMorning', kind: 'morning', time: '11:40', state: 'Ready', next: '11:40', last: '11-28 11:40', code: 0 }],
    sched_read_ok: true,
    sched_cn: { morning: '晨间一站式' },
    sched_logs: { morning: 'morning.log' },
    awards: {
      today: { flow_mb: 500, fee_yuan: 5, fee_count: 5 },
      month: { flow_mb: 500, fee_yuan: 5, fee_count: 5 },
      fee_total: 5,
      fee_yuan_total: 5,
      by_day: {},
      by_plat: {},
      by_day_plat: {},
      fee_day: {},
      recent,
      recent_total: 10,
    },
    eff: { total_runs: 3, success_rate: 100, machine_sec: 120, saved_min: 15, today_runs: 1, recent: [{ ts: '11-29 08:00', label: '晨间签到', sec: 12, ok: true }] },
    policy: { bass: { feed: 2, feed_all: false, feed_hour: 12, on_stage_full: 'stop', prize: 'fee' }, xxl: { star: 3, times: 5, gap: 12 }, seckill: { at: '11:59:50' } },
    settings: { bark_url: '', serverchan_sendkey: '', pushplus_token: '' },
    mail: { enabled: false, provider: 'qq', host: 'imap.qq.com', port: 993, user: '', subject_keyword: 'HAR' },
  };
}

boot({ autoPoll: false });

function resetEnv() {
  apiCalls.length = 0;
  mockActionHandler = null;
  mockLogs = { lines: [], running: false, label: '' };
  mockState = makeSampleState();
  store.state = structuredClone(mockState);
  clearErr();
  setBusy(false, '');
  document.getElementById('dashDry').checked = false;
  document.getElementById('cfmModal').classList.remove('on');
  document.getElementById('modal').classList.remove('on');
  navTo('dash');
  render();
}

test('1. pullState renders todo, 6 KPIs, account cards, ledger fold/filter', async () => {
  resetEnv();
  store.state = null;
  await pullState();

  assert.ok(document.querySelector('#todoBox .todo'));
  assert.equal(document.querySelectorAll('#sumbar .kpi').length, 6);
  assert.equal(document.querySelectorAll('#cards .acct').length, 1);

  // 8 data rows + 1 header row = 9
  assert.equal(document.querySelectorAll('#awTab tr').length, 9);
  const moreBtn = document.getElementById('awMoreBtn');
  assert.equal(moreBtn.classList.contains('is-hidden'), false);
  moreBtn.click();
  assert.equal(document.querySelectorAll('#awTab tr').length, 11);
  assert.equal(moreBtn.textContent, '收起');
  moreBtn.click();
  assert.equal(document.querySelectorAll('#awTab tr').length, 9);

  const awAcct = document.getElementById('awAcct');
  awAcct.value = '13800001234';
  awAcct.dispatchEvent(new Event('change', { bubbles: true }));
  assert.equal(document.querySelectorAll('#awTab tr').length, 6); // 5 matching + 1 header
});

test('2. nav buttons and bass subtabs switch active page and panels', () => {
  resetEnv();
  document.querySelector('nav button[data-p="bass"]').click();
  assert.equal(document.getElementById('p-bass').classList.contains('on'), true);
  assert.equal(document.querySelector('nav button[data-p="bass"]').getAttribute('aria-current'), 'page');
  assert.ok(document.getElementById('pageTitle').textContent.endsWith('福建活动'));

  document.querySelector('#bassTabs button[data-bt="manual"]').click();
  const panels = [...document.querySelectorAll('#p-bass .panel[data-bass-group]')];
  const hiddenCount = panels.filter((p) => p.classList.contains('bass-hidden')).length;
  assert.equal(hiddenCount, panels.length - 1);
  document.querySelector('#bassTabs button[data-bt="all"]').click();
  assert.equal(panels.every((p) => !p.classList.contains('bass-hidden')), true);
});

test('3. clicking qwhd-only run sends expected payload', async () => {
  resetEnv();
  document.querySelector('button[data-act="run-all"][data-platform="qwhd"]').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(apiCalls.length, 1);
  assert.deepEqual(apiCalls[0], {
    action: 'run_all',
    platform: 'qwhd',
    dry: false,
    luck: false,
    games: false,
    xxl: false,
    tasks: false,
    doumi: false,
  });
});

test('4. run-all all requires confirm when dry unchecked, sends immediately when dry checked', async () => {
  resetEnv();
  const btn = document.querySelector('#p-dash .ops button[data-act="run-all"][data-platform="all"]');
  btn.click();
  assert.equal(document.getElementById('cfmModal').classList.contains('on'), true);
  assert.equal(apiCalls.length, 0);

  document.getElementById('cfmCancel').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(apiCalls.length, 0);

  btn.click();
  document.getElementById('cfmOk').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(apiCalls.length, 1);
  assert.deepEqual(apiCalls[0], {
    action: 'run_all',
    platform: 'all',
    dry: false,
    luck: true,
    games: true,
    xxl: true,
    tasks: true,
    doumi: true,
  });

  apiCalls.length = 0;
  document.getElementById('dashDry').checked = true;
  btn.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(apiCalls.length, 1);
  assert.equal(apiCalls[0].dry, true);
});

test('5. voucherRun top button and card button send voucher_run payload', async () => {
  resetEnv();
  document.querySelector('#p-dash .ops button[data-act="voucher-run"]').click();
  assert.equal(document.getElementById('cfmModal').classList.contains('on'), true);
  document.getElementById('cfmOk').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(apiCalls[0], { action: 'voucher_run', dry: 0 });

  apiCalls.length = 0;
  document.querySelector('#cards button[data-act="voucher-run"][data-key="1234"]').click();
  document.getElementById('cfmOk').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(apiCalls[0], { action: 'voucher_run', dry: 0, key: '1234' });
});

test('6. new account modal handles validation error and closes on success', async () => {
  resetEnv();
  navTo('token');
  document.querySelector('button[data-act="open-new-account"]').click();
  assert.equal(document.getElementById('modal').classList.contains('on'), true);

  mockActionHandler = async () => ({ ok: false, err: '手机号必须是 11 位数字' });
  document.getElementById('naPhone').value = '123';
  document.querySelector('#modal button[data-act="save-new-account"]').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(document.getElementById('errBox').classList.contains('on'), true);
  assert.equal(document.getElementById('modal').classList.contains('on'), true);

  mockActionHandler = async () => ({ ok: true, msg: '已创建' });
  document.getElementById('naPhone').value = '13800009999';
  document.querySelector('#modal button[data-act="save-new-account"]').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(document.getElementById('modal').classList.contains('on'), false);
  assert.equal(apiCalls.at(-1).action, 'account_new');
  assert.equal(apiCalls.at(-1).phone, '13800009999');
});

test('7. manual bass feedAll and mutual exclusive stage options', () => {
  resetEnv();
  navTo('bass');
  const bFeedAll = document.getElementById('bFeedAll');
  const bFeed = document.getElementById('bFeed');
  bFeed.value = '3';
  bFeedAll.checked = true;
  bFeedAll.dispatchEvent(new Event('change', { bubbles: true }));
  assert.equal(bFeed.disabled, true);
  assert.equal(bFeed.value, '0');

  const bAuto = document.getElementById('bAuto');
  const bCont = document.getElementById('bCont');
  const bPrize = document.getElementById('bPrize');
  assert.equal(bPrize.classList.contains('is-hidden'), true);

  bAuto.checked = true;
  bAuto.dispatchEvent(new Event('change', { bubbles: true }));
  assert.equal(bPrize.classList.contains('is-hidden'), false);
  assert.equal(bCont.checked, false);

  bCont.checked = true;
  bCont.dispatchEvent(new Event('change', { bubbles: true }));
  assert.equal(bAuto.checked, false);
  assert.equal(bPrize.classList.contains('is-hidden'), true);
});

test('8. settings dirty pill activates on edit and clears after save', async () => {
  resetEnv();
  navTo('settings');
  const sBark = document.getElementById('sBark');
  const sDirty = document.getElementById('sDirty');
  sBark.value = 'https://api.day.app/testkey';
  sBark.dispatchEvent(new Event('input', { bubbles: true }));
  assert.equal(sDirty.classList.contains('on'), true);

  document.querySelector('button[data-act="save-settings"]').click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(sDirty.classList.contains('on'), false);
  assert.equal(apiCalls[0].action, 'settings_save');
});

test('9. sched reg validates HH:MM and calls sched_reg when valid', async () => {
  resetEnv();
  navTo('sched');
  const inp = document.getElementById('st_morning');
  const regBtn = document.querySelector('#schedBox button[data-act="reg-sched"][data-kind="morning"]');

  inp.value = '25:00';
  regBtn.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(apiCalls.length, 0);
  assert.ok(document.getElementById('toast').classList.contains('on'));

  inp.value = '11:40';
  regBtn.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(apiCalls.length, 1);
  assert.deepEqual(apiCalls[0], { action: 'sched_reg', kind: 'morning', time: '11:40' });
});

test('10. log filtering and setBusy button disabling', async () => {
  resetEnv();
  clearConsole();
  mockLogs = {
    lines: [
      { seq: 1, t: '08:00:00', line: '✓ 签到成功' },
      { seq: 2, t: '08:00:01', line: '✗ 票据已过期' },
    ],
    running: false,
    label: '',
  };
  await pullLogs();
  assert.equal(document.getElementById('console').children.length, 2);

  document.querySelector('#p-logs button[data-lvl="err"]').click();
  assert.equal(document.getElementById('console').children.length, 1);
  assert.ok(document.getElementById('console').textContent.includes('票据已过期'));

  document.querySelector('#p-logs button[data-lvl="all"]').click();
  const lf = document.getElementById('logFilter');
  lf.value = '签到';
  lf.dispatchEvent(new Event('input', { bubbles: true }));
  assert.equal(document.getElementById('console').children.length, 1);
  lf.value = '';
  lf.dispatchEvent(new Event('input', { bubbles: true }));

  setBusy(true, '测试中');
  const btns = [...document.querySelectorAll('.content .btn:not([data-nobusy])')];
  assert.ok(btns.length > 5);
  assert.equal(btns.every((b) => b.disabled), true);
  assert.equal(document.getElementById('btnStop').disabled, false);
  setBusy(false, '');
  assert.equal(btns.every((b) => !b.disabled), true);
});

test('11. appearance theme toggle and zoom buttons update DOM & CSS scale', () => {
  resetEnv();
  document.getElementById('btnTheme').click();
  assert.equal(document.documentElement.dataset.theme, 'light');
  document.getElementById('btnTheme').click();
  assert.equal(document.documentElement.dataset.theme, 'dark');

  document.querySelector('button[aria-label="放大字号"]').click();
  assert.equal(document.documentElement.style.getPropertyValue('--ui-scale'), '1.1');
  assert.equal(document.getElementById('zoomVal').textContent, '110%');
  document.getElementById('zoomVal').click();
  assert.equal(document.documentElement.style.getPropertyValue('--ui-scale'), '1');
});

test('12. dynamic HTML strings are escaped and cannot inject elements', () => {
  resetEnv();
  const bad = '<img id="xss-probe" src="x" onerror="window.__xss=1">';
  store.state.eff.recent = [{ ts: bad, label: bad, sec: 1, ok: true }];
  render();
  assert.equal(document.getElementById('xss-probe'), null);
  assert.ok(document.getElementById('runtab').textContent.includes('<img id="xss-probe"'));
});

test('13. xxl merged into bass subtab with unified cards and merged badge', () => {
  resetEnv();
  assert.equal(document.querySelector('nav button[data-p="xxl"]'), null);
  assert.equal(document.getElementById('nb-bass').classList.contains('warn'), true);
  assert.equal(document.getElementById('bt-dot-xxl').classList.contains('on'), true);

  document.querySelector('nav button[data-p="bass"]').click();
  document.querySelector('#bassTabs button[data-bt="xxl"]').click();
  const xxlPanel = document.querySelector('#p-bass .panel[data-bass-group="xxl"]');
  assert.equal(xxlPanel.classList.contains('bass-hidden'), false);
  assert.ok(document.getElementById('xxlParams'));
  assert.ok(document.getElementById('xxlBox'));

  const cardText = document.getElementById('xxlCards').textContent;
  assert.ok(cardText.includes('剩余体力'));
  assert.ok(cardText.includes('今日通关'));
  assert.ok(cardText.includes('当前关卡'));
  assert.equal(cardText.includes('剩余金币'), false);
  const det = document.querySelector('#xxlCards details.act-details');
  assert.ok(det);
  assert.equal(det.open, false);
});

test.after(() => {
  dom.window.close();
});
