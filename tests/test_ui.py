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
        self.html = dashboard.load_frontend_bundle()

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
        html = dashboard.load_frontend_bundle()
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
        _, mail = dashboard._sched_ps("mail", "00:00", pyw, launcher)
        self.assertIn("Minutes 10", mail)
        _, daily = dashboard._sched_ps("daily", "06:00", pyw, launcher)
        self.assertIn("Hours 1", daily)
        # 17 小时窗口的旧写法 = 只跑注册当天，第二天静默停摆（11-22 就这样丢了一天）
        self.assertNotIn("Hours 17", daily)
        self.assertIn("Days 3650", daily)

    def test_apply_all_rejects_bad_input(self):
        r = dashboard.do_action({"action": "sched_apply_all", "plan": [["nope", "08:30"]]})
        self.assertFalse(r["ok"])
        r = dashboard.do_action({"action": "sched_apply_all", "plan": [["morning", "8点半"]]})
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
        self.assertEqual(ps.count("Register-ScheduledTask"), len(dashboard.SCHED_DEF),
                         "全部任务要在一个脚本里注册完")
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
                    mock.patch.object(run_all, "BASE", Path(tmp)), \
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
        self.html = dashboard.load_frontend_bundle()

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
        self.assertIn("games:platform==='all'", handler)
        self.assertIn("xxl:platform==='all'", handler)

    def test_benefits_action_passes_lottery_and_dry_run_to_batch(self):
        with mock.patch.object(dashboard, "scan_accounts", return_value=[]), \
                mock.patch.object(dashboard, "_start", return_value={"ok": True}) as start:
            dashboard.do_action({"action": "run_all", "platform": "all", "luck": True,
                                 "games": True, "dry": True, "xxl": True})
            label, command = start.call_args.args
        self.assertIn("签到领福利", label)
        self.assertIn("--luck", command)
        self.assertIn("--games", command)
        self.assertIn("含游戏中心", label)
        self.assertIn("--xxl", command)
        self.assertIn("含消消乐", label)
        self.assertIn("--dry-run", command)

    def test_one_click_games_only_on_all_platforms(self):
        """一键接上拓展活动：全部平台带 --games，单平台不带。

        2026-11-23 前一键命令拼装漏了 games 分支，游戏中心写好了却只能靠
        12:05 定时任务或单独按钮触发。窗口由脚本自己拦，带上不会浪费次数。
        """
        with mock.patch.object(dashboard, "scan_accounts", return_value=[]), \
                mock.patch.object(dashboard, "_start", return_value={"ok": True}) as start:
            dashboard.do_action({"action": "run_all", "platform": "all",
                                 "luck": True, "games": True})
            label, command = start.call_args.args
        self.assertIn("--games", command)
        self.assertIn("含游戏中心", label)

        with mock.patch.object(dashboard, "scan_accounts", return_value=[]), \
                mock.patch.object(dashboard, "_start", return_value={"ok": True}) as start:
            dashboard.do_action({"action": "run_all", "platform": "bass", "games": True})
            _label, command = start.call_args.args
        self.assertNotIn("--games", command)

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

    def test_dynamic_text_is_escaped_before_html_render(self):
        """奖品名、运行记录等接口文本必须按文本渲染，不能拼成 HTML。"""
        for marker in ("function jsArg(", "esc(r.label)", "esc(s.last_reward||'—')",
                       "esc(p.name||'—')", "esc(t.title||('#'+id))", "esc(l.t)"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_dash_hierarchy_puts_accounts_before_long_ledger(self):
        """总览首屏顺序：待办 → 指标 → 账号状态 → 到账台账 → 运行记录。"""
        dash = self.html[self.html.find('<div class="page on" id="p-dash"'):
                         self.html.find('<!-- 凭证与账号 -->')]
        ids = ["todoBox", "sumbar", "cards", "awTab", "runtab"]
        positions = [dash.find('id="%s"' % i) for i in ids]
        self.assertNotIn(-1, positions, "总览缺少区块")
        self.assertEqual(positions, sorted(positions), "总览区块顺序不对：%s" % positions)

    def test_ledger_summary_does_not_repeat_monthly_kpi(self):
        """顶部 KPI 已有「本月流量/话费」，台账汇总只留今日，别同一屏说两遍。"""
        seg = self.html[self.html.find("if($('awSum'))"):self.html.find('renderAwFilters();')]
        self.assertIn("今日已得流量", seg)
        self.assertIn("今日已得话费", seg)
        self.assertNotIn("本月已得流量", seg)
        self.assertNotIn("本月已得话费", seg)

    def test_today_kpi_cards_sit_next_to_monthly(self):
        """本日已得要做成和本月一样的独立卡片，成对挨着放，不是标题小字。"""
        seg = self.html[self.html.find("// 本日已得："):
                        self.html.find("// 运行记录统计条")]
        titles = re.findall(r'<div class="k">([^<]+)</div>', seg)
        self.assertEqual(titles[:4], ["本月已得流量", "本日已得流量",
                                      "本月已得话费", "本日已得话费"])
        # 本日卡片和本月卡片结构一致：都有 .v 数值 + .c 说明
        self.assertIn('<div class="k">本日已得流量</div><div class="v">${dayFlow>=1024?', seg)
        self.assertIn('<div class="k">本日已得话费</div><div class="v">${fy(dayFee)}', seg)
        self.assertIn("const awToday=((STATE.awards||{}).today)||{}", seg)
        # 宽屏 6 列排成一行，窄屏 @media 仍会把每对折成一行
        self.assertIn(".kpis{display:grid;grid-template-columns:repeat(6,1fr)", self.html)
        self.assertNotIn("daytag", self.html)   # 不再用标题小字

    def test_ledger_can_fold_and_filter(self):
        """到账台账默认折叠、可展开、可按号码/来源筛选。"""
        for marker in ("id=\"awHint\"", "id=\"awAcct\"", "id=\"awSource\"",
                       "id=\"awMoreBtn\"", "const AW_PAGE=8", "function onAwFilter",
                       "function toggleAwAll", "function renderAwFilters",
                       "function renderAwTable", "renderAwFilters();", "renderAwTable();"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)


class TestFeedOncePerDay(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        patch_base = mock.patch.object(run_all, "BASE", base)
        patch_base.start()
        self.addCleanup(patch_base.stop)
        self.daily_state = base / "gui_data" / "daily_state.json"
        self.daily_state.parent.mkdir()
        self.feed_state = self.daily_state.with_name("feed_state.json")
        self.confs = [base / "1234.json", base / "5678.json"]
        self.set_tiers()

    def set_tiers(self, statuses=("RECEIVED", "RECEIVED", "RECEIVED")):
        today = datetime.now().date().isoformat()
        self.daily_state.write_text(json.dumps({
            key: {"tiers_date": today, "tiers": [{"status": s} for s in account_statuses]}
            for key, account_statuses in (("1234", statuses),
                                           ("5678", ("RECEIVED", "PENDING", "PENDING")))
        }), encoding="utf-8")

    def test_due_only_after_configured_hour(self):
        hour = datetime.now().hour
        self.assertTrue(run_all.feed_due({"feed_hour": hour}, "1234"))
        if hour < 23:
            self.assertFalse(run_all.feed_due({"feed_hour": hour + 1}, "1234"))

    def test_not_due_until_all_tiers_claimed(self):
        """三档没领齐不能先喂，用户要求领完再一起喂。"""
        self.set_tiers(("RECEIVED", "RECEIVED", "PENDING"))
        self.assertFalse(run_all.feed_due({"feed_hour": 0}, "1234"))
        self.set_tiers()
        self.assertTrue(run_all.feed_due({"feed_hour": 0}, "1234"))

    def test_second_run_same_day_is_skipped(self):
        """计划任务补跑不能重复烧饲料。"""
        self.assertTrue(run_all.feed_due({"feed_hour": 0}, "1234"))
        run_all.mark_fed("1234")
        self.assertFalse(run_all.feed_due({"feed_hour": 0}, "1234"))

    def test_bad_hour_never_due(self):
        self.assertFalse(run_all.feed_due({}, "1234"))

    def test_legacy_marker_covers_every_account_only_on_its_date(self):
        today = datetime.now().date().isoformat()
        with mock.patch.object(run_all, "all_tiers_claimed", return_value=True):
            for date, expected in ((today, False), ("2000-01-01", True)):
                self.feed_state.write_text(json.dumps({"date": date}), encoding="utf-8")
                for conf in self.confs:
                    self.assertEqual(run_all.feed_due({"feed_hour": 0}, conf.stem), expected)

    def test_only_eligible_account_gets_feed_and_only_actual_feed_is_recorded(self):
        args_by_account = {}
        feeds = 0

        def fake_run_one(key, conf, dry_run, args, idx, total):
            args_by_account[conf.stem] = args
            count = feeds if "--feed-all" in args and not dry_run else 0
            return (conf.stem, True, 0, '@@RESULT ' + json.dumps({"feeds": count}))

        def run_round(dry_run=False):
            run_all.run_platform("bass", self.confs, dry_run, ["--daily-only"],
                                 feed_policy={"feed_hour": 0}, feed_args=["--feed-all"])

        with mock.patch.object(run_all, "run_one", side_effect=fake_run_one), \
                redirect_stdout(io.StringIO()):
            run_round()
            self.assertIn("--feed-all", args_by_account["1234"])
            self.assertNotIn("--feed-all", args_by_account["5678"])
            self.assertFalse(self.feed_state.exists(), "退出成功但实际未喂，须留待补跑")
            feeds = 1
            run_round(dry_run=True)
            self.assertIn("--feed-all", args_by_account["1234"])
            self.assertFalse(self.feed_state.exists())
            run_round()
            self.assertIn("--feed-all", args_by_account["1234"])
            self.assertEqual(set(json.loads(self.feed_state.read_text(encoding="utf-8"))["accounts"]),
                             {"1234"})
            run_round()
            self.assertNotIn("--feed-all", args_by_account["1234"])


class TestFrontendOptimization(unittest.TestCase):
    """前端拆分、热重载、HAR 直传与交互优化回归守护。"""

    def _fake_handler(self, path, headers=None, body=b""):
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path = path
        h.headers = dict({"Host": "127.0.0.1:8789"}, **(headers or {}))
        h.rfile = io.BytesIO(body)
        h.wfile = io.BytesIO()
        sent = {}

        def fake_send(code, ctype, data):
            sent["code"] = code
            sent["ctype"] = ctype
            sent["data"] = data

        h._send = fake_send
        h._json = lambda obj, code=200: fake_send(
            code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode()
        )
        return h, sent

    def test_static_path_whitelist_and_hot_reload(self):
        self.assertIsNotNone(dashboard._static_path("index.html"))
        self.assertIsNotNone(dashboard._static_path("app.css"))
        self.assertIsNotNone(dashboard._static_path("app.js"))
        self.assertIsNone(dashboard._static_path("../dashboard.py"))
        self.assertIsNone(dashboard._static_path("secret.txt"))

        with tempfile.TemporaryDirectory() as td:
            st = Path(td) / "static"
            st.mkdir()
            (st / "index.html").write_text("<html>v2</html>", encoding="utf-8")
            (st / "app.css").write_text("body{color:#fff}", encoding="utf-8")
            (st / "app.js").write_text("console.log(2);", encoding="utf-8")
            with mock.patch.object(dashboard, "BASE", Path(td)):
                h1, s1 = self._fake_handler("/")
                h1.do_GET()
                self.assertEqual(s1["code"], 200)
                self.assertEqual(s1["data"], b"<html>v2</html>")

                h2, s2 = self._fake_handler("/static/app.css")
                h2.do_GET()
                self.assertEqual(s2["code"], 200)
                self.assertIn("text/css", s2["ctype"])
                self.assertEqual(s2["data"], b"body{color:#fff}")

                h3, s3 = self._fake_handler("/static/app.js")
                h3.do_GET()
                self.assertEqual(s3["code"], 200)
                self.assertIn("javascript", s3["ctype"])

                h4, s4 = self._fake_handler("/static/../dashboard.py")
                h4.do_GET()
                self.assertEqual(s4["code"], 404)

    def test_har_upload_endpoint_and_guards(self):
        h1, s1 = self._fake_handler("/api/har_upload", {
            "Origin": "https://evil.example",
            "Content-Type": "application/octet-stream",
            "Content-Length": "4",
        }, b"test")
        h1.do_POST()
        self.assertEqual(s1["code"], 403)

        h2, s2 = self._fake_handler("/api/har_upload", {
            "Content-Type": "text/plain",
            "Content-Length": "4",
        }, b"test")
        h2.do_POST()
        self.assertEqual(s2["code"], 415)

        h3, s3 = self._fake_handler("/api/har_upload", {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(dashboard.MAX_BODY + 1),
        })
        h3.do_POST()
        self.assertEqual(s3["code"], 413)

        h4, s4 = self._fake_handler("/api/har_upload", {
            "Content-Type": "application/octet-stream",
            "Content-Length": "0",
        })
        h4.do_POST()
        self.assertEqual(s4["code"], 400)

        captured = {}
        payload = b'{"log":{"entries":[]}}'
        h5, s5 = self._fake_handler("/api/har_upload?key=1234", {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(payload)),
        }, payload)
        with mock.patch.object(dashboard, "_import_har_text",
                               lambda text, key=None: captured.update(text=text, key=key) or {"ok": True, "msg": "ok"}):
            h5.do_POST()
        self.assertEqual(s5["code"], 200)
        self.assertEqual(captured["key"], "1234")
        self.assertEqual(captured["text"], payload.decode())

    def test_policy_allows_zero_star_and_sched_rejects_bad_hour_min(self):
        with tempfile.TemporaryDirectory() as td:
            pf = Path(td) / "policy.json"
            with mock.patch.object(common, "POLICY_FILE", pf), \
                 mock.patch.object(dashboard, "save_policy", common.save_policy):
                r = dashboard.do_action({"action": "policy_save", "xxl": {"star": 0, "times": 5, "gap": 12}})
                self.assertTrue(r["ok"])
                self.assertEqual(r["policy"]["xxl"]["star"], 0)

        with mock.patch.object(dashboard, "_pythonw", return_value="pythonw.exe"):
            for bad in ("25:00", "12:60", "24:01"):
                r = dashboard.do_action({"action": "sched_reg", "kind": "sign", "time": bad})
                self.assertFalse(r["ok"], bad)
            r2 = dashboard.do_action({"action": "sched_apply_all", "plan": [["sign", "24:01"]]})
            self.assertFalse(r2["ok"])

    def test_awards_view_reports_recent_total(self):
        today = datetime.now().date().isoformat()
        recs = [{
            "at": f"{today} 10:0{i}:00", "account": "13800001234", "source": "每日签到",
            "name": "100MB流量包", "kind": "flow", "size_mb": 100.0, "yuan": 0.0,
            "need_recharge": False, "ignored": False, "url": "",
        } for i in range(5)]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "awards.json"
            p.write_text(json.dumps({"records": recs}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(common, "AWARDS_FILE", p):
                v = dashboard._awards_view(limit=2)
        self.assertEqual(len(v["recent"]), 2)
        self.assertEqual(v["recent_total"], 5)
        self.assertEqual(v["recent_limit"], 2)

    def test_manual_bass_options_not_restored_and_prize_scoped(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = dashboard.load_frontend_js()
        for i in ("bAuto", "bCont", "bFeedAll", "bPrize"):
            m = re.search(r'<(?:input|select)[^>]*id="%s"[^>]*>' % i, html)
            self.assertIsNotNone(m, i)
            self.assertIn('autocomplete="off"', m.group(0), i)
        for marker in ("function resetManualBass", "function syncStageUi", "正在执行中"):
            self.assertIn(marker, js)
        # 「领奖档位」紧跟在「领本阶奖品」后，位于「放弃奖品」之前
        self.assertLess(html.find('id="bAuto"'), html.find('id="bPrize"'))
        self.assertLess(html.find('id="bPrize"'), html.find('id="bCont"'))

    def test_continue_stage_skips_stage_full_guard(self):
        src = (ROOT / "cmcc_fj.py").read_text(encoding="utf-8")
        self.assertIn("if not (args.auto_claim or args.continue_stage):", src)

    def test_frontend_optimization_markers(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
        js = dashboard.load_frontend_js()
        bundle = dashboard.load_frontend_bundle()

        self.assertIn('/static/app.css', html)
        self.assertIn('/static/app.js', html)
        self.assertIn("max-width:1200px", css)
        self.assertNotIn("pullState();pullLogs();", js)
        self.assertNotIn("you@gmail.com", bundle)
        self.assertNotIn("连 Gmail", bundle)
        self.assertIsNone(re.search(r"(^|[^.\w])confirm\(", js, re.M), "不应再调用原生 confirm()")

        for marker in ('onchange="updateBassFodder()"', 'id="bPrize"', 'id="harDrop"',
                       'id="logFilter"', 'id="logDrawer"', 'id="cfmModal"',
                       'data-nobusy onclick="gotoSettings()"',
                       'data-nobusy onclick="saveSettings()"',
                       'data-nobusy onclick="saveMail()"',
                       'data-nobusy onclick="savePolicy()"'):
            with self.subTest(html_marker=marker):
                self.assertIn(marker, html)

        for marker in ("function kickPoll", "_stateInFlight", "_logsInFlight",
                       "function askConfirm", "function toggleTheme", "function zoomReset",
                       "/api/har_upload", "function renderNavBadges", "function renderSmart",
                       "function toggleDrawer", "function numVal", "prize:$('bPrize')"):
            with self.subTest(js_marker=marker):
                self.assertIn(marker, js)

    def test_frontend_refactor_modules_and_ux(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
        js = dashboard.load_frontend_js()

        for mod in ("js/core.js", "js/dash.js", "js/activities.js", "js/ops.js", "app.js"):
            self.assertIsNotNone(dashboard._static_path(mod), f"缺少前端模块 {mod}")
            self.assertIn(f"/static/{mod}", html)
        self.assertIsNone(dashboard._static_path("js/../dashboard.py"))

        self.assertRegex(css, r"(?m)^\.empty\{")
        self.assertIn(".subtabs{", css)
        self.assertIn("fontSize=(16*ZOOM)", js)
        self.assertNotIn("style.zoom", js)

        for marker in ('id="bassTabs"', 'id="appearPop"', 'id="sDirty"', 'id="mDirty"', 'id="pDirty"'):
            self.assertIn(marker, html)
        for fn in ("function setBassTab(", "function toggleAppear(",
                   "function markFormDirty(", "function copyCapBox("):
            self.assertIn(fn, js)

        ids = re.findall(r'\bid="([^"]+)"', html)
        dups = [i for i in set(ids) if ids.count(i) > 1]
        self.assertEqual(dups, [], f"index.html 存在重复 id：{dups}")

        handlers = re.findall(r'\bon(?:click|change|input)="([^"]+)"', html)
        called = set()
        for expr in handlers:
            called.update(re.findall(r'(?:^|;)\s*([A-Za-z_$][\w$]*)\s*\(', expr))
        for fn in sorted(called):
            self.assertIn(f"function {fn}(", js, f"HTML 内联事件调用了未定义函数 {fn}")


class TestCheckUpdate(unittest.TestCase):
    """更新检测：版本比较正确，网络失败静默，Release 解析到位。"""

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_ver_tuple_compare(self):
        self.assertGreater(dashboard._ver_tuple("v1.4"), dashboard._ver_tuple("1.3"))
        self.assertFalse(dashboard._ver_tuple("1.3") > dashboard._ver_tuple("v1.3"))
        self.assertEqual(dashboard._ver_tuple("1.10") > dashboard._ver_tuple("1.9"), True)

    def test_check_update_swallows_network_error(self):
        with mock.patch.object(dashboard.urllib.request, "urlopen", side_effect=OSError("boom")):
            r = dashboard.check_update(timeout=0.1)
        self.assertFalse(r["has_update"])
        self.assertIn("error", r)
        self.assertEqual(r["current"], dashboard.VERSION)

    def test_check_update_parses_release(self):
        payload = json.dumps({
            "tag_name": "v1.9", "html_url": "https://example.com/rel",
            "name": "v1.9", "published_at": "2026-12-01T10:00:00Z",
        }).encode()
        with mock.patch.object(dashboard.urllib.request, "urlopen",
                               return_value=self._Resp(payload)):
            r = dashboard.check_update()
        self.assertEqual(r["latest"], "1.9")
        self.assertTrue(r["has_update"])
        self.assertEqual(r["published_at"], "2026-12-01")
        self.assertEqual(r["url"], "https://example.com/rel")

    def test_check_update_same_version_no_update(self):
        payload = json.dumps({"tag_name": "v" + dashboard.VERSION,
                              "html_url": "", "name": ""}).encode()
        with mock.patch.object(dashboard.urllib.request, "urlopen",
                               return_value=self._Resp(payload)):
            r = dashboard.check_update()
        self.assertFalse(r["has_update"])


if __name__ == "__main__":
    unittest.main()
