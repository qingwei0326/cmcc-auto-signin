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

        a = next(x for x in dashboard.scan_accounts() if x["plat"] == "BASS")
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
        for kind in dashboard.SCHED_DEF:
            name, ps = dashboard._sched_ps(kind, "07:15", pyw, launcher)
            self.assertIn("Register-ScheduledTask", ps)
            self.assertIn(f"-TaskName '{name}'", ps)
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
        self.assertIn("Hours 17", daily)
        _, keep = dashboard._sched_ps("keepalive", "00:00", pyw, launcher)
        self.assertIn("Minutes 90", keep)

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

        def fake_run_script(script, conf, args, timeout, name="", idx=0, total=0):
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


class TestFeedOncePerDay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.real = run_all.FEED_STATE
        run_all.FEED_STATE = Path(self.tmp.name) / "feed_state.json"

    def tearDown(self):
        run_all.FEED_STATE = self.real
        self.tmp.cleanup()

    def test_due_only_in_configured_hour(self):
        hour = datetime.now().hour
        self.assertTrue(run_all.feed_due({"feed_hour": hour}))
        self.assertFalse(run_all.feed_due({"feed_hour": (hour + 3) % 24}))

    def test_second_run_same_day_is_skipped(self):
        """计划任务补跑不能重复烧饲料。"""
        hour = datetime.now().hour
        self.assertTrue(run_all.feed_due({"feed_hour": hour}))
        run_all.mark_fed()
        self.assertFalse(run_all.feed_due({"feed_hour": hour}))

    def test_bad_hour_never_due(self):
        self.assertFalse(run_all.feed_due({}))


if __name__ == "__main__":
    unittest.main()
