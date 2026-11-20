"""界面显示内容的守护测试。

这些用例对应「页面看着正常、但什么都不说」的问题：
- 定时任务页只有注册/注销按钮，看不出任务到底有没有在跑
- 总览不回答「现在要不要我动手」
- 消消乐 / 拓展活动跑完只在日志里能看到结果

运行：
    python -m unittest discover -s tests -v
"""

import json
import re
import unittest
from pathlib import Path
from unittest import mock

import dashboard

ROOT = Path(__file__).resolve().parent.parent


class TestSchedState(unittest.TestCase):
    """定时任务页要显示系统里的真实状态。"""

    def setUp(self):
        with dashboard._SCHED_LOCK:
            dashboard._SCHED_CACHE["at"] = 0.0
            dashboard._SCHED_CACHE["data"] = []

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
            dashboard._sched_state(ttl=90)
            dashboard._sched_state(ttl=90)
        self.assertEqual(len(calls), 1, "第二次应命中缓存")

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


if __name__ == "__main__":
    unittest.main()
