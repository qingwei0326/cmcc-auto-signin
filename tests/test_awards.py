"""到账台账的回归测试。

用户的要求很具体：「别让我看喂了几次，就告诉我今天到手多少流量」，
以及「话费券只显示能直接兑换的，充值才能拿的不要列」。
这几条都落在 classify_award / record_award / _awards_view 上。
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common as C          # noqa: E402
import dashboard as D       # noqa: E402


class TestClassify(unittest.TestCase):
    def test_flow_sizes(self):
        self.assertEqual(C.classify_award("1GB流量日包")["size_mb"], 1024.0)
        self.assertEqual(C.classify_award("10GB月包")["size_mb"], 10240.0)
        self.assertEqual(C.classify_award("1.5GB")["size_mb"], 1536.0)
        self.assertEqual(C.classify_award("500MB")["size_mb"], 500.0)
        self.assertEqual(C.classify_award("1GB流量日包")["kind"], "flow")

    def test_usable_fee_coupon(self):
        c = C.classify_award("2-3元话费券手气红包")
        self.assertEqual(c["kind"], "fee")
        self.assertFalse(c["need_recharge"])

    def test_recharge_only_coupons_are_flagged(self):
        for name in ("充值9.9折券", "满30减5话费券", "首充10元券"):
            with self.subTest(name=name):
                self.assertTrue(C.classify_award(name)["need_recharge"],
                                f"{name} 要充值才能用，应被标记")

    def test_thanks_is_not_an_award(self):
        self.assertEqual(C.classify_award("谢谢参与")["size_mb"], 0.0)


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "awards.json"
        self.patch = mock.patch.object(C, "AWARDS_FILE", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_records_flow_in_mb(self):
        C.record_award("13900005678", "天天大转盘", "1GB流量日包")
        st = json.loads(self.path.read_text(encoding="utf-8"))
        row = st["records"][-1]
        self.assertEqual(row["kind"], "flow")
        self.assertEqual(row["size_mb"], 1024.0)
        self.assertEqual(row["account"], "13900005678")

    def test_same_prize_same_minute_recorded_once(self):
        for _ in range(3):
            C.record_award("13900005678", "天天大转盘", "1GB流量日包")
        st = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(st["records"]), 1)

    def test_thanks_not_recorded(self):
        self.assertIsNone(C.record_award("13900005678", "天天大转盘", "谢谢参与"))
        self.assertFalse(self.path.exists())

    def test_view_hides_recharge_coupons_but_counts_them(self):
        C.record_award("13800001234", "天天大转盘", "1GB流量日包")
        C.record_award("13800001234", "天天大转盘", "充值9.9折券")
        C.record_award("13900005678", "养成领奖", "2-3元话费券手气红包")
        v = D._awards_view()
        names = [r["name"] for r in v["recent"]]
        self.assertIn("1GB流量日包", names)
        self.assertIn("2-3元话费券手气红包", names)
        self.assertNotIn("充值9.9折券", names, "充值类券不该出现在台账列表里")
        self.assertEqual(v["month"]["flow_mb"], 1024.0)
        self.assertEqual(v["month"]["recharge_count"], 1)
        self.assertEqual(v["by_account"]["1234"]["flow_mb"], 1024.0)
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), v["by_day"]["1234"])


if __name__ == "__main__":
    unittest.main()


class TestLuckDayCounter(unittest.TestCase):
    """一天跑几轮时，抽奖次数要按天累加，并和「中了几次」分开。

    用户看到程序跑了 3 次、界面只写「抽 0 次」会以为数字错了，
    所以这里把 3239 那种「跑了两轮、第二轮剩 0 次」的场景钉住。
    """

    def setUp(self):
        import cmcc_luck
        self.L = cmcc_luck
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "luck_state.json"
        self.patches = [mock.patch.object(cmcc_luck, "STATE_FILE", self.path)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_counts_accumulate_same_day(self):
        self.L.save_state("184****3239", {"surplus": 0}, draws=2, wins=1)
        self.L.save_state("184****3239", {"surplus": 0}, draws=1, wins=0)
        st = self._state()["3239"]
        self.assertEqual(st["day_draws"], 3)
        self.assertEqual(st["day_wins"], 1)

    def test_second_run_with_zero_draws_keeps_total(self):
        self.L.save_state("184****3239", {"surplus": 0}, draws=2, wins=1)
        self.L.save_state("184****3239", {"surplus": 0})   # 第三轮没次数可抽
        st = self._state()["3239"]
        self.assertEqual(st["day_draws"], 2)
        self.assertEqual(st["day_wins"], 1)

    def test_counters_reset_next_day(self):
        class D1:
            @staticmethod
            def now():
                return datetime(2026, 11, 20, 12, 0)
        class D2:
            @staticmethod
            def now():
                return datetime(2026, 11, 21, 12, 0)
        with mock.patch.object(self.L, "datetime", D1):
            self.L.save_state("184****3239", {}, draws=2, wins=1)
        with mock.patch.object(self.L, "datetime", D2):
            self.L.save_state("184****3239", {})
        st = self._state()["3239"]
        self.assertEqual(st["day"], "2026-11-21")
        self.assertEqual(st["day_draws"], 0)
