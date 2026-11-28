"""全国签到侧的任务完成数：任务函数计数 → res["tasks"] → bump_daily 入账。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402
import cmcc_extra as X  # noqa: E402
import dashboard  # noqa: E402


class ApplyQwhdTasksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(dashboard, "_data_dir", return_value=Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _acc(self):
        return dashboard.load_stats()["accounts"]["k1"] if "accounts" in dashboard.load_stats() \
            else dashboard.load_stats()["k1"]

    def test_tasks_counted_without_flow_or_sign(self):
        dashboard._apply_qwhd_result("k1", {"plat": "qwhd", "today_signed": False,
                                            "flow_mb": 0, "tasks": 3})
        a = self._acc()
        self.assertEqual(a["history"][-1]["tasks"], 3)
        self.assertEqual(a["history"][-1]["flow_mb"], 0)
        self.assertEqual(a["tasks_done_total"], 3)
        self.assertEqual(dashboard._month_sums(a)["tasks_month"], 3)

    def test_flow_and_tasks_bump_once(self):
        with mock.patch.object(dashboard, "bump_daily") as bd:
            dashboard._apply_qwhd_result("k1", {"today_signed": True, "flow_mb": 100,
                                                "tasks": 2})
        bd.assert_called_once_with("k1", flow_mb=100.0, tasks=2)
        dashboard._apply_qwhd_result("k1", {"today_signed": True, "flow_mb": 100, "tasks": 2})
        a = self._acc()
        self.assertEqual(a["history"][-1]["flow_mb"], 100.0)
        self.assertEqual(a["history"][-1]["tasks"], 2)
        self.assertEqual(a["flow_mb_total"], 100.0)
        self.assertEqual(a["tasks_done_total"], 2)

    def test_flow_not_counted_when_unsigned_and_no_bump_when_empty(self):
        with mock.patch.object(dashboard, "bump_daily") as bd:
            dashboard._apply_qwhd_result("k1", {"today_signed": False, "flow_mb": 50})
            bd.assert_not_called()
            dashboard._apply_qwhd_result("k1", {"today_signed": False, "flow_mb": 50,
                                                "tasks": 1})
        bd.assert_called_once_with("k1", flow_mb=0.0, tasks=1)


def _no_sleep():
    return mock.patch.object(X.time, "sleep", lambda *_: None)


class TaskCounterTest(unittest.TestCase):
    def test_mark_tasks(self):
        def api(s, ref, method, path, body=None):
            if path.endswith("taskList"):
                return {"data": {"tasks": [
                    {"taskId": "1", "status": 0}, {"taskId": "2", "status": 0},
                    {"taskId": "3", "status": 0}, {"taskId": "4", "status": 1}]}}
            if path.endswith("taskInfo"):
                return {"data": {}}
            if path.endswith("finishTask"):
                return {"code": "SUCCESS" if body["taskId"] != "3" else "FAIL",
                        "msg": "第三方"}
            return {"code": "SUCCESS", "data": {"awardNum": 1}}
        with mock.patch.object(X, "api_call", api), _no_sleep():
            c = {}
            X.run_mark_tasks(None, "r", None, True, c)
            self.assertEqual(c.get("done", 0), 0)
            c = {}
            X.run_mark_tasks(None, "r", None, False, c)
            self.assertEqual(c["done"], 2)  # 失败跳过的不计

    def test_diy_tasks(self):
        state = {"done": set()}

        def api(s, ref, method, path, body=None):
            if method == "POST":
                if not path.endswith("/b"):
                    state["done"].add(path.rsplit("/", 1)[1])
                return {"code": "SUCCESS"}
            return {"code": "SUCCESS", "data": [
                {"taskId": t, "taskStage": "DONE" if t in state["done"] else "UNDO"}
                for t in ("a", "b", "c")]}
        with mock.patch.object(X, "api_call", api), _no_sleep():
            c = {}
            X.run_diy_tasks(None, "r", "comp", True, c)
            self.assertEqual(c.get("done", 0), 0)
            c = {}
            X.run_diy_tasks(None, "r", "comp", False, c)
            self.assertEqual(c["done"], 2)  # b 的 finish 未生效，不计

    def test_video_day(self):
        q = {"n": 2}

        def api(s, ref, method, path, body=None):
            return {"code": "SUCCESS", "data": {}}

        def vt(data):
            return [{"name": "v"}] * q["n"]

        def complete(*a, **k):
            q["n"] -= 1
            return True, "完成"
        with mock.patch.object(X, "api_call", api), mock.patch.object(X, "_video_tasks", vt), \
                mock.patch.object(X, "open_cmlive_session", lambda *a: ("cs", "jwt")), \
                mock.patch.object(X, "video_remain", lambda *a: 0), \
                mock.patch.object(X, "_complete_video_task", complete), _no_sleep():
            c = {}
            X.run_video_day(None, "r", True, c)
            self.assertEqual(c.get("done", 0), 0)
            c = {}
            X.run_video_day(None, "r", False, c)
            self.assertEqual(c["done"], 2)

    def test_run_games_sums_diy_and_video(self):
        acts = {"x": {"name": "X", "entry": "e", "sign": None, "task_component": "c",
                      "video_day": True}}

        def diy(s, r, comp, dry, counter=None):
            X._bump(counter, 2)
            return "d"

        def vid(s, r, dry, counter=None):
            X._bump(counter, 1)
            return "v"
        with mock.patch.object(X, "ACTS", acts), \
                mock.patch.object(X, "exchange_session", lambda *a: (None, "r")), \
                mock.patch.object(X, "ActConfig", lambda *a: None), \
                mock.patch.object(X, "run_diy_tasks", diy), \
                mock.patch.object(X, "run_video_day", vid), _no_sleep():
            c = {}
            X.run_games(object(), False, c)
        self.assertEqual(c["done"], 3)


if __name__ == "__main__":
    unittest.main()
