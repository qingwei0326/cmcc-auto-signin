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

        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(F, "DAILY_STATE", Path(td) / "daily_state.json"), \
             mock.patch.object(F, "call", fake_call), \
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


class TestStagePrizeTier(unittest.TestCase):
    """阶段奖品：id 号段对齐真实奖品池；领奖全阶随机，抽后无选档。

    2026-11-24 用户口径：每阶领奖都随机开（含一阶），传 prizeId 无效；
    六阶抽出来后可换其他档位（换奖接口未抓到，脚本暂不实现）。
    2026-11-23 只读实测 getStagePrizes：6 阶 × 3 档 = id 1~18，
    四阶 10=4元话费券/11=2GB月包/12=9.5折…六阶 16/17/18。
    prize_id_for 仍保留正确号段，供将来六阶换奖接口用。
    """

    def test_prize_id_matches_real_pool(self):
        self.assertEqual(F.prize_id_for(0, "fee"), 1)
        self.assertEqual(F.prize_id_for(0, "flow"), 2)
        self.assertEqual(F.prize_id_for(1, "flow"), 5)      # 二阶流量 = 5
        self.assertEqual(F.prize_id_for(4, "fee"), 13)      # 五阶话费 = 13
        self.assertEqual(F.prize_id_for(5, "flow"), 17)     # 六阶流量 = 17
        self.assertEqual(F.prize_id_for(5, "discount"), 18)
        self.assertEqual(F.prize_id_for(None, "fee"), 1)

    def test_no_stage_is_choosable(self):
        # 2026-11-24 口径：每阶领奖都随机，已无可传 prizeId 选档的阶段
        self.assertEqual(set(F.CHOOSABLE_STAGES), set())

    def _fake_call(self, sent, stage):
        def fake(s, cfg, key, retries=1, extra=None):
            sent[key] = extra
            return {"ok": True, "code": "0000",
                    "data": {"prizeDto": {"prizeName": "1GB流量月包券"}}}
        return fake

    def test_every_stage_random_sends_no_prize_id(self):
        # 全 6 阶（含一阶、六阶）一律无参随机，传 flow 也不带 prizeId
        for stage in range(6):
            sent = {}
            with mock.patch.object(F, "call", self._fake_call(sent, stage)):
                r, pid, name = F.receive_stage_prize(object(), {}, "flow")
            with self.subTest(stage=stage):
                self.assertIsNone(sent["stage_receive"], "随机阶不该带 prizeId")
                self.assertIsNone(pid)
                self.assertTrue(r["ok"])
                self.assertEqual(name, "1GB流量月包券")


if __name__ == "__main__":
    unittest.main()
