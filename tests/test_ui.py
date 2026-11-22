"""界面显示内容的守护测试。

这些用例对应「页面看着正常、但什么都不说」的问题：
- 定时任务页只有注册/注销按钮，看不出任务到底有没有在跑
- 总览不回答「现在要不要我动手」
- 消消乐 / 拓展活动跑完只在日志里能看到结果

运行：
    python -m unittest discover -s tests -v
"""

import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402  隔离真实全局配置（见 tests/_guard.py）
import common
import dashboard
import run_all

ROOT = Path(__file__).resolve().parent.parent


class TestSchedState(unittest.TestCase):
    """定时任务页要显示系统里的真实状态。"""

    def setUp(self):
        with dashboard._SCHED_LOCK:
            dashboard._SCHED_CACHE["at"] = 0.0
            dashboard._SCHED_CACHE["data"] = []
            dashboard._SCHED_CACHE["ok"] = None
            dashboard._SCHED_CACHE["th"] = None

    @staticmethod
    def _fake(payload, calls):
        def run(*a, **kw):
            calls.append(a)
            return mock.Mock(stdout=payload)
        return run

    def test_parses_time_next_last_code(self):
        calls = []
        payload = json.dumps([{
            "name": "MobileSignIn", "state": "Ready",
            "at": "2026-11-20T08:30:00+08:00",
            "next": "11-21 08:30", "last": "11-20 08:30", "code": 0,
        }]).encode()
        with mock.patch.object(dashboard.subprocess, "run", self._fake(payload, calls)):
            out = dashboard._sched_state(ttl=0)
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["name"], "MobileSignIn")
        # StartBoundary 是 ISO，必须取出 HH:MM 才能回填输入框
        self.assertEqual(row["time"], "08:30")
        self.assertEqual(row["next"], "11-21 08:30")
        self.assertEqual(row["code"], 0)

    def test_result_is_cached(self):
        """前端每 6 秒轮询，不能每次都起一个 PowerShell。"""
        calls = []
        payload = json.dumps([{"name": "MobileSignIn", "state": "Ready",
                               "at": "2026-11-20T08:30:00+08:00",
                               "next": "", "last": "", "code": 267011}]).encode()
        with mock.patch.object(dashboard.subprocess, "run", self._fake(payload, calls)):
            dashboard._sched_state(ttl=90)    # 冷启动交给后台读，不挡首屏
            dashboard._sched_wait()
            dashboard._sched_state(ttl=90)    # 之后命中缓存
            dashboard._sched_state(ttl=90)
        self.assertEqual(len(calls), 1, "重复轮询只该起一次 PowerShell")

    def test_failure_does_not_raise(self):
        """读不到任务状态不能让整个 /api/state 挂掉。"""
        def boom(*a, **kw):
            raise OSError("blocked")
        with mock.patch.object(dashboard.subprocess, "run", boom):
            self.assertEqual(dashboard._sched_state(ttl=0), [])

    def test_state_includes_sched(self):
        source = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('"sched": _sched_state()', source)

    def test_never_run_hides_fake_last_time(self):
        """从未运行的任务，系统给的 LastRunTime 是 1999-11-30 占位值，不能当"刚跑过"显示。"""
        calls = []
        payload = json.dumps([{
            "name": "MobileGames", "state": "Ready",
            "at": "2026-11-20T12:05:00+08:00",
            "next": "11-21 12:05", "last": "11-30 00:00", "code": 267011,
        }]).encode()
        with mock.patch.object(dashboard.subprocess, "run", self._fake(payload, calls)):
            row = dashboard._sched_state(ttl=0)[0]
        self.assertEqual(row["last"], "", "未曾运行就不该显示占位日期")
        self.assertEqual(row["code"], 267011)

    def test_sched_refresh_returns_data(self):
        """注册/注销是异步跑的，前端要靠 sched_refresh 立刻拿到新状态。"""
        calls = []
        payload = json.dumps([{"name": "MobileGames", "state": "Ready",
                               "at": "2026-11-20T12:05:00+08:00",
                               "next": "", "last": "", "code": 0}]).encode()
        with mock.patch.object(dashboard.subprocess, "run", self._fake(payload, calls)):
            r = dashboard.do_action({"action": "sched_refresh"})
        self.assertTrue(r.get("ok"))
        self.assertEqual(r["sched"][0]["time"], "12:05")


class TestPageContent(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    def test_every_page_has_title(self):
        """TITLES 少一个键，页面标题就只剩「签到控制台 / 」。"""
        pages = set(re.findall(r'<button data-p="(\w+)"', self.html))
        m = re.search(r"const TITLES=\{(.*?)\};", self.html, re.S)
        self.assertIsNotNone(m)
        titles = set(re.findall(r"(\w+):'", m.group(1)))
        self.assertEqual(pages - titles, set(), "有页面缺中文标题")

    def test_dash_has_todo_block(self):
        """总览要直接回答「现在要做什么」。"""
        self.assertIn('id="todoBox"', self.html)
        self.assertIn("function renderTodo", self.html)
        self.assertIn("renderTodo()", self.html)

    def test_sched_page_shows_real_status(self):
        self.assertIn('id="schedBox"', self.html)
        for fn in ("buildSched", "updSched", "regSched", "refreshSched"):
            self.assertIn("function " + fn + "(", self.html)
        self.assertIn("从未运行", self.html)   # 267011 要说人话

    def test_bass_has_stage_switch(self):
        """阶段喂满时的两条路都要有入口，且互斥。"""
        self.assertIn('id="bAuto"', self.html)
        self.assertIn('id="bCont"', self.html)
        self.assertIn("continue_stage", self.html)
        self.assertIn("function pickStage", self.html)

    def test_xxl_and_games_have_result_box(self):
        """点完按钮不能只在日志里能看到结果。"""
        self.assertIn('id="xxlBox"', self.html)
        self.assertIn('id="gmBox"', self.html)
        self.assertIn("function capEnd", self.html)
        self.assertIn("capPush(l.line)", self.html)

    def test_bass_run_passes_continue_stage(self):
        cmds = []

        class FakeRunner:
            running = False
            label = ""

            def start(self, label, cmd, parser=None, shell=False):
                cmds.append(cmd)
                return True

        # configs_fj/ 带凭证、不进仓库，CI 上没有真实账号，这里给一个假的
        a = {"key": "fj1", "plat": "BASS", "file": "configs_fj/fj1.json", "phone": "13800001234",
                 "token_state": "filled", "has_bark": False, "has_sendkey": False, "has_pushplus": False}
        real_scan = dashboard.scan_accounts
        dashboard.scan_accounts = lambda: [a]
        self.addCleanup(setattr, dashboard, "scan_accounts", real_scan)
        real = dashboard.RUNNER
        dashboard.RUNNER = FakeRunner()
        try:
            dashboard.do_action({"action": "bass_run", "key": a["key"],
                                 "feed": 2, "continue_stage": 1})
        finally:
            dashboard.RUNNER = real
        self.assertTrue(cmds)
        self.assertIn("--continue-stage", cmds[-1])
        self.assertIn("--feed", cmds[-1])


class TestSchedPlan(unittest.TestCase):
    """「一键配置全部」：7 个任务共用一份注册逻辑，一次注册好。"""

    def test_plan_covers_every_task(self):
        self.assertEqual({k for k, _ in dashboard.SCHED_PLAN}, set(dashboard.SCHED_DEF))

    def test_frontend_plan_matches_backend(self):
        """前后端各写一份时刻表，跑偏了就会「点了按钮但时间不对」。"""
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        m = re.search(r"const SCHED_PLAN=\[(.*?)\];", html, re.S)
        self.assertIsNotNone(m)
        pairs = re.findall(r'\["(\w+)","([\d:]+)"\]', m.group(1))
        self.assertEqual([list(x) for x in pairs], [[k, t] for k, t in dashboard.SCHED_PLAN])

    def test_each_kind_builds_valid_script(self):
        pyw, launcher = r"C:\py\pythonw.exe", r"D:\x\run_hidden.py"
        for kind, (name, _bat, trig, _note) in dashboard.SCHED_DEF.items():
            _, ps = dashboard._sched_ps(kind, "07:15", pyw, launcher)
            self.assertIn("Register-ScheduledTask", ps)
            self.assertIn(f"-TaskName '{name}'", ps)
            if trig == "daily":
                # 每天定时的任务必须原样用给定时刻；重复型的起点会被推到未来
                self.assertIn("07:15", ps)
            self.assertIn("run_hidden.py", ps)
            # PS 5.1 上 Repetition 不可写，绝不能出现这行
            self.assertNotIn("$tr.Repetition=", ps)

    def test_repeating_kinds_keep_interval(self):
        pyw, launcher = "p", "l"
        _, probe = dashboard._sched_ps("probe", "00:00", pyw, launcher)
        self.assertIn("Minutes 2", probe)
        _, daily = dashboard._sched_ps("daily", "06:00", pyw, launcher)
        self.assertIn("Hours 1", daily)
        # 17 小时窗口的旧写法 = 只跑注册当天，第二天静默停摆（11-22 就这样丢了一天）
        self.assertNotIn("Hours 17", daily)
        self.assertIn("Days 3650", daily)
        _, keep = dashboard._sched_ps("keepalive", "00:00", pyw, launcher)
        self.assertIn("Minutes 90", keep)
        self.assertIn("Days 3650", keep)

    def test_apply_all_rejects_bad_input(self):
        r = dashboard.do_action({"action": "sched_apply_all", "plan": [["nope", "08:30"]]})
        self.assertFalse(r["ok"])
        r = dashboard.do_action({"action": "sched_apply_all", "plan": [["sign", "8点半"]]})
        self.assertFalse(r["ok"])

    def test_apply_all_registers_everything_in_one_call(self):
        started = {}

        class FakeRunner:
            running = False
            label = ""

            def start(self, label, cmd, parser=None, shell=False):
                started["label"] = label
                started["ps"] = cmd[-1]
                return True

        real = dashboard.RUNNER
        dashboard.RUNNER = FakeRunner()
        try:
            r = dashboard.do_action({"action": "sched_apply_all"})
        finally:
            dashboard.RUNNER = real
        self.assertTrue(r["ok"])
        ps = started["ps"]
        self.assertEqual(ps.count("Register-ScheduledTask"), 7, "7 个任务要在一个脚本里注册完")
        for name, _, _, _ in dashboard.SCHED_DEF.values():
            self.assertIn(f"-TaskName '{name}'", ps)


class TestMonthSums(unittest.TestCase):
    """总览要显示「本月」而不是只增不减的累计。"""

    def test_only_current_month_counted(self):
        from datetime import datetime
        cur = datetime.now().strftime("%Y-%m")
        # 造一条上个月、一条本月（跨年也要正确，所以用去年 12 月做反例）
        prev = "2020-12-31"
        s = {"history": [
            {"date": prev, "flow_mb": 999, "tasks": 9, "feeds": 9},
            {"date": f"{cur}-01", "flow_mb": 100, "tasks": 2, "feeds": 3},
            {"date": f"{cur}-02", "flow_mb": 50.5, "tasks": 1, "feeds": 0},
        ]}
        out = dashboard._month_sums(s)
        self.assertEqual(out["flow_mb_month"], 150.5)
        self.assertEqual(out["tasks_month"], 3)
        self.assertEqual(out["feeds_month"], 3)

    def test_empty_history_is_zero(self):
        self.assertEqual(dashboard._month_sums({})["flow_mb_month"], 0)
        self.assertEqual(dashboard._month_sums({"history": None})["tasks_month"], 0)

    def test_history_keeps_full_month(self):
        """history 保留条数必须够覆盖一整月（31 天都活跃也不能丢月初）。"""
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn("hist[-45:]", src)


class TestPolicyPersistence(unittest.TestCase):
    """「设置一次、长期生效」：策略存盘，定时任务照它执行。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.real = common.POLICY_FILE
        common.POLICY_FILE = self.dir / "policy.json"

    def tearDown(self):
        common.POLICY_FILE = self.real
        self.tmp.cleanup()

    def test_defaults_when_missing(self):
        p = common.load_policy()
        self.assertEqual(p["bass"]["on_stage_full"], "stop")
        self.assertEqual(p["bass"]["feed"], 0, "默认不自动喂养，别偷用饲料")

    def test_save_merges_and_persists(self):
        common.save_policy({"bass": {"feed": 3}})
        p = common.load_policy()
        self.assertEqual(p["bass"]["feed"], 3)
        # 没传的键必须保留默认值，不能被整体覆盖掉
        self.assertEqual(p["bass"]["prize"], "fee")
        self.assertEqual(p["xxl"]["times"], 5)

    def test_broken_file_falls_back(self):
        common.POLICY_FILE.write_text("{ 坏 json", encoding="utf-8")
        self.assertEqual(common.load_policy()["bass"]["feed"], 0)


class TestPolicyAppliesToScheduledRun(unittest.TestCase):
    """每日奖励那一轮必须按策略注入喂养/领奖参数。"""

    def _run(self, policy, argv=("run_all.py", "--daily-only")):
        captured = []

        def fake_run_script(script, conf, args, timeout, name="", idx=0, total=0, **_kw):
            captured.append(list(args))
            return (conf.stem, True, 1.0, "ok")

        with tempfile.TemporaryDirectory() as tmp:
            platforms = {k: dict(v) for k, v in run_all.PLATFORMS.items()}
            for name, sub in (("qwhd", "configs"), ("bass", "configs_fj")):
                d = Path(tmp) / sub
                d.mkdir()
                (d / "1234.json").write_text(json.dumps({"phone": "13800001234"}), encoding="utf-8")
                platforms[name]["dir"] = d
            with mock.patch.object(run_all, "PLATFORMS", platforms), \
                    mock.patch.object(run_all, "load_policy", return_value=policy), \
                    mock.patch.object(run_all, "feed_due", return_value=True), \
                    mock.patch.object(run_all, "mark_fed"), \
                    mock.patch.object(run_all, "run_script", side_effect=fake_run_script), \
                    mock.patch.object(sys, "argv", list(argv)), \
                    mock.patch.object(run_all, "notify"), \
                    redirect_stdout(io.StringIO()):
                run_all.main()
        self.assertTrue(captured, "应该跑了至少一个账号")
        return captured[0]

    def test_feed_injected_with_stage_policy(self):
        args = self._run({"bass": {"feed": 4, "feed_hour": 12, "on_stage_full": "claim",
                                   "prize": "flow"}})
        self.assertIn("--daily-only", args)
        self.assertEqual(args[args.index("--feed") + 1], "4")
        self.assertIn("--auto-claim", args)
        self.assertEqual(args[args.index("--prize") + 1], "flow")

    def test_continue_stage_is_exclusive(self):
        args = self._run({"bass": {"feed": 2, "on_stage_full": "continue", "prize": "fee"}})
        self.assertIn("--continue-stage", args)
        self.assertNotIn("--auto-claim", args)

    def test_feed_zero_means_no_feed_flag(self):
        args = self._run({"bass": {"feed": 0, "on_stage_full": "stop", "prize": "fee"}})
        self.assertNotIn("--feed", args)
        self.assertNotIn("--auto-claim", args)

    def test_fake_browsing_stays_off_by_default(self):
        args = self._run({"bass": {"feed": 0}})
        self.assertNotIn("--tasks", args)
        self.assertNotIn("--fb-tasks", args)

    def test_feed_all_injected_without_number(self):
        """「喂到用完」不传轮数，由脚本按当天饲料算。"""
        args = self._run({"bass": {"feed": 0, "feed_all": True, "feed_hour": 12}})
        self.assertIn("--feed-all", args)
        self.assertNotIn("--feed", args)

    def test_fixed_rounds_still_work(self):
        args = self._run({"bass": {"feed": 3, "feed_all": False, "feed_hour": 12}})
        self.assertIn("--feed", args)
        self.assertEqual(args[args.index("--feed") + 1], "3")
        self.assertNotIn("--feed-all", args)


class TestHumanFacing(unittest.TestCase):
    """界面要面向「人」而不是面向命令行：术语、反馈、忙态、手机布局。"""

    def setUp(self):
        self.html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    def test_no_cli_flags_in_visible_labels(self):
        for flag in ("--claim", "--roll", "--dry-run", "--tasks", "--fb-tasks",
                     "--auto-claim", "--continue-stage", "--level", "dry-run"):
            self.assertNotIn(flag, self.html, "界面不该暴露命令行参数：%s" % flag)

    def test_platform_names_are_chinese(self):
        self.assertIn("PLAT_CN", self.html)
        self.assertIn("凭证与账号", self.html)
        self.assertIn("福建活动", self.html)
        # 脚本名保留在 title 里供排查，但不出现在正文文案中
        self.assertIn('title="cmcc_seckill.py"', self.html)

    def test_benefits_button_routes_lottery_only_for_all_accounts(self):
        self.assertIn("签到领福利", self.html)
        i = self.html.find("function runAll")
        handler = self.html[i:self.html.index("function refreshStatus", i)]
        self.assertIn("luck:platform==='all'", handler)
        self.assertNotIn("xxl", handler)

    def test_benefits_action_passes_lottery_and_dry_run_to_batch(self):
        with mock.patch.object(dashboard, "scan_accounts", return_value=[]), \
                mock.patch.object(dashboard, "_start", return_value={"ok": True}) as start:
            dashboard.do_action({"action": "run_all", "platform": "all", "luck": True,
                                 "dry": True, "xxl": True})
            label, command = start.call_args.args
        self.assertIn("签到领福利", label)
        self.assertIn("--luck", command)
        self.assertIn("--dry-run", command)
        self.assertNotIn("--xxl", command)

    def test_errors_stay_on_page(self):
        self.assertIn('id="errBox"', self.html)
        self.assertIn("function showErr", self.html)
        self.assertIn("function clearErr", self.html)
        self.assertIn('aria-live="polite"', self.html)

    def test_busy_disables_action_buttons(self):
        self.assertIn("function setBusy", self.html)
        self.assertIn("data-nobusy", self.html)
        self.assertIn("b.disabled=!!running", self.html)
        self.assertIn('id="taskPillTop"', self.html)

    def test_sched_tip_is_single_and_honest(self):
        i = self.html.find("function updSched")
        seg = self.html[i:self.html.find("async function refreshSched")]
        self.assertIn("schedtip", seg)
        self.assertIn("sched_read_ok", seg)
        self.assertIn("还没有注册任何计划任务", seg)
        self.assertIn("读取中", seg)   # 没读到之前不能谎报「未注册」
        # 原来每次渲染都 insertAdjacentHTML 一条警告，6 秒一条无限堆叠
        self.assertNotIn("insertAdjacentHTML('afterend'", seg)

    def test_task_log_openable_from_ui(self):
        self.assertIn("function openLog", self.html)
        self.assertIn("/api/tasklog", self.html)
        self.assertIn('id="modal"', self.html)
        self.assertIn("function closeModal", self.html)
        self.assertIn("看日志", self.html)

    def test_account_management_has_ui(self):
        for marker in ("function renderAccounts", "function openNewAccount",
                       "function saveNewAccount", "function editAccount",
                       "function saveAccount", "account_new", "account_save",
                       'id="acctBox"'):
            self.assertIn(marker, self.html)

    def test_capture_guide_present(self):
        self.assertIn("第一次用？", self.html)
        self.assertIn("ProxyPin", self.html)
        self.assertIn("文件传输助手", self.html)

    def test_detect_level_autofills_input(self):
        self.assertIn("function detectLevel", self.html)
        # 关卡改为运行时自动读取，探测按钮只做只读展示，不再回填/手填
        self.assertIn("当前真实关卡", self.html)
        self.assertIn("运行时自动使用", self.html)
        self.assertNotIn("$('sLevel')", self.html)

    def test_cards_show_data_timestamp(self):
        self.assertIn("数据截至", self.html)
        self.assertIn("s.last_run", self.html)

    def test_token_age_shown_and_warned(self):
        self.assertIn("function tokenAge", self.html)
        self.assertIn("接近约 30 分钟的寿命上限", self.html)

    def test_mobile_tables_scroll_in_own_container(self):
        self.assertIn(".tablewrap{overflow-x:auto", self.html)
        self.assertIn(".sidebar nav{display:flex;padding:8px;overflow-x:auto", self.html)
        self.assertIn(".scrollhint{display:block}", self.html)

    def test_log_timestamp_contrast_fixed(self):
        self.assertNotIn("color:#5a678a", self.html)
        self.assertIn(".console .t{color:#8b96b8", self.html)

    def test_bass_and_seckill_have_result_boxes(self):
        self.assertIn('id="bassBox"', self.html)
        self.assertIn('id="skBox"', self.html)
        self.assertIn("function seckillQuery", self.html)
        self.assertIn("function runBassProbe", self.html)

    def test_refresh_status_keeps_user_checkbox(self):
        i = self.html.find("function refreshStatus")
        self.assertNotIn("checked=false", self.html[i:i + 200])

    def test_seckill_warns_when_window_passed(self):
        self.assertIn("function renderSkWindow", self.html)
        self.assertIn('id="skWindow"', self.html)
        self.assertIn("今天的场次", self.html)

    def test_har_import_guards_double_click(self):
        self.assertIn("harBusy", self.html)

    def test_har_import_usable_while_task_running(self):
        """秒杀等待期间要能导入新 HAR（脚本每轮重读配置），导入按钮不能被忙态置灰。"""
        i = self.html.find('onclick="importHar()"')
        self.assertIn("data-nobusy", self.html[max(0, i - 120):i])
        for marker in ("act({action:'har_import'},afterHar)",
                       "act({action:'har_import',latest:1},afterHar)"):
            j = self.html.find(marker)
            self.assertIn("data-nobusy", self.html[max(0, j - 120):j])

    def test_feed_reward_visible(self):
        """昨日投喂奖励漏领要看得见：每日面板 + 总览待办条都要提醒。"""
        self.assertIn("feed_reward", self.html)
        self.assertIn("投喂累计奖励可领未领", self.html)
        self.assertIn("当晚作废", self.html)

    def test_feed_all_switches_present(self):
        """「喂到用完」在自动策略与手动执行两处都要有，且勾上就置灰轮数框。"""
        for marker in ('id="pFeedAll"', 'id="bFeedAll"', "function feedAllToggle",
                       "feed_all:$('pFeedAll').checked", "feed_all:$('bFeedAll').checked?1:0"):
            self.assertIn(marker, self.html)

    def test_font_size_control(self):
        """桌面窗口没有浏览器缩放快捷键，界面要自己给一个且记住选择。"""
        self.assertIn("function zoomBy", self.html)
        self.assertIn("cmcc_zoom", self.html)
        self.assertIn('aria-label="放大字号"', self.html)


class TestFeedOncePerDay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.real_feed = run_all.FEED_STATE
        self.real_base = run_all.BASE
        self.real_daily = run_all.DAILY_STATE
        run_all.FEED_STATE = Path(self.tmp.name) / "feed_state.json"
        run_all.DAILY_STATE = Path(self.tmp.name) / "daily_state.json"
        run_all.BASE = Path(self.tmp.name)
        d = run_all.BASE / "configs_fj"
        d.mkdir()
        (d / "1234.json").write_text("{}", encoding="utf-8")
        self.set_tiers()

    def tearDown(self):
        run_all.FEED_STATE = self.real_feed
        run_all.BASE = self.real_base
        run_all.DAILY_STATE = self.real_daily
        self.tmp.cleanup()

    def set_tiers(self, statuses=("RECEIVED", "RECEIVED", "RECEIVED")):
        today = datetime.now().date().isoformat()
        run_all.DAILY_STATE.write_text(json.dumps({
            "1234": {"tiers_date": today,
                     "tiers": [{"status": s} for s in statuses]},
        }), encoding="utf-8")

    def test_due_only_after_configured_hour(self):
        hour = datetime.now().hour
        self.assertTrue(run_all.feed_due({"feed_hour": hour}))
        if hour < 23:
            self.assertFalse(run_all.feed_due({"feed_hour": hour + 1}))

    def test_not_due_until_all_tiers_claimed(self):
        """三档没领齐不能先喂，用户要求领完再一起喂。"""
        self.set_tiers(("RECEIVED", "RECEIVED", "PENDING"))
        self.assertFalse(run_all.feed_due({"feed_hour": 0}))
        self.set_tiers()
        self.assertTrue(run_all.feed_due({"feed_hour": 0}))

    def test_second_run_same_day_is_skipped(self):
        """计划任务补跑不能重复烧饲料。"""
        self.assertTrue(run_all.feed_due({"feed_hour": 0}))
        run_all.mark_fed()
        self.assertFalse(run_all.feed_due({"feed_hour": 0}))

    def test_bad_hour_never_due(self):
        self.assertFalse(run_all.feed_due({}))


if __name__ == "__main__":
    unittest.main()
