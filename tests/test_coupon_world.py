"""「券来世界」访问任务的回归测试。

任务 id=24「APP首页访问活动」：从 App 首页打开券来世界（bass-feedPrizeH5），
每天一次，完成给 500 饲料。脚本的做法是**先把页面真的打开一次，再上报 doTask**，
而不是干巴巴调接口——服务端其实不校验，但日志要能对上「访问过」这件事。
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402  隔离真实全局配置（见 tests/_guard.py）
import cmcc_fj as F          # noqa: E402
import common as C          # noqa: E402


TASK_ID_24 = {
    "id": 24,
    "taskName": "APP首页访问活动",
    "completeType": "APP_VISIT",
    "taskStatus": "UN_DO",
    "fodder": 500,
    "taskUrls": [
        {"type": "h5", "url": "https://dx.10086.cn/A/8RwMJA"},
        {"type": "app", "url": "https://wap.fj.10086.cn/bass-feedPrizeH5/?ch=jituan_02"},
    ],
}

TASK_FLOWBANK = {
    "id": 30,
    "taskName": "浏览流量银行",
    "completeType": "VISIT",
    "taskStatus": "UN_DO",
    "fodder": 50,
    "taskUrls": [{"type": "app", "url": "https://wap.fj.10086.cn/bass-flowBankH5/"}],
}


class TestCouponWorldDetect(unittest.TestCase):
    def test_pick_app_url_first(self):
        self.assertEqual(F.task_url(TASK_ID_24),
                         "https://wap.fj.10086.cn/bass-feedPrizeH5/?ch=jituan_02")

    def test_recognize_coupon_world(self):
        self.assertTrue(F.is_coupon_world(TASK_ID_24))

    def test_other_visit_task_not_matched(self):
        self.assertFalse(F.is_coupon_world(TASK_FLOWBANK))

    def test_recognize_by_name(self):
        self.assertTrue(F.is_coupon_world(
            {"id": 99, "taskName": "浏览券来世界", "taskUrls": []}))


class TestCouponWorldFlow(unittest.TestCase):
    """未完成时要先访问页面再上报；已完成就跳过，不重复上报。"""

    def _run(self, tasks):
        cfg = {"bearer_token": "x" * 36, "app_id": "0", "phone": "13800001234"}
        visited = []
        reported = []

        class FakeS:
            def get(self, url, **kw):
                visited.append(url)
                return mock.Mock(status_code=200)

        def fake_call(s, cfg, key, retries=1, extra=None):
            if key == "task_list":
                return {"ok": True, "code": "0000", "msg": "成功", "data": tasks}
            return {"ok": True, "code": "0000", "msg": "成功", "data": {}}

        def fake_post(s, cfg, path, retries=1, extra=None):
            reported.append(path)
            return {"ok": True, "code": "0000", "msg": "成功", "data": None}

        with mock.patch.object(F, "call", fake_call), \
             mock.patch.object(F, "call_post", fake_post), \
             mock.patch.object(F, "pause", lambda *a, **k: None), \
             mock.patch.object(F, "new_session", lambda cfg: FakeS()), \
             mock.patch.object(F, "load_config", lambda p: cfg), \
             mock.patch.object(F, "emit_result", lambda *a, **k: None), \
             mock.patch.object(sys, "argv", ["cmcc_fj.py", "--coupon-world"]), \
             redirect_stdout(io.StringIO()):
            F.main()
        return visited, reported

    def test_visits_page_before_report(self):
        visited, reported = self._run([TASK_ID_24])
        self.assertTrue(any("feedPrizeH5" in u for u in visited), "应先打开券来世界页面")
        self.assertIn("/bass-cultivateGameServer/task/doTask/24", reported)

    def test_skips_when_already_done(self):
        done = dict(TASK_ID_24, taskStatus="RECEIVED")
        visited, reported = self._run([done])
        self.assertEqual(visited, [], "已完成就不该再打开页面")
        self.assertEqual(reported, [])


class TestPolicy(unittest.TestCase):
    def test_default_on(self):
        self.assertTrue(C.load_policy().get("coupon_world", True))

    def test_run_all_injects_flag(self):
        import run_all
        cmds = []
        platforms = {k: dict(v) for k, v in run_all.PLATFORMS.items()}

        def fake(cmd, **kw):
            cmds.append(cmd)
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            for name, sub in (("configs", "configs"), ("configs_fj", "configs_fj")):
                d = Path(tmp) / sub
                d.mkdir()
                (d / "a.json").write_text('{"phone": "13800001234"}', encoding="utf-8")
            platforms["qwhd"]["dir"] = Path(tmp) / "configs"
            platforms["bass"]["dir"] = Path(tmp) / "configs_fj"
            with mock.patch.object(run_all, "PLATFORMS", platforms), \
                 mock.patch.object(run_all.subprocess, "run", fake), \
                 mock.patch.object(sys, "argv", ["run_all.py", "--daily-only"]), \
                 mock.patch.object(run_all, "notify"), \
                 redirect_stdout(io.StringIO()):
                run_all.main()
        bass = [c for c in cmds if "cmcc_fj.py" in " ".join(c)]
        self.assertTrue(bass)
        for c in bass:
            self.assertIn("--coupon-world", c, "每日那一轮要带上券来世界访问")
