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
        c = C.classify_award("1元话费券")
        self.assertEqual(c["kind"], "fee")
        self.assertFalse(c["need_recharge"])
        self.assertEqual(c["yuan"], 1.0)

    def test_fee_amount_parsed(self):
        """「本月已得话费」要显示金额，面额得从奖品名里抠出来。"""
        self.assertEqual(C.parse_fee_yuan("1元话费券"), 1.0)
        self.assertEqual(C.parse_fee_yuan("0.5元话费券"), 0.5)
        self.assertEqual(C.parse_fee_yuan("2-3元话费券手气红包"), 3.0)   # 区间取上限
        self.assertEqual(C.parse_fee_yuan("充值9.9折券"), 0.0)
        self.assertEqual(C.parse_fee_yuan("1GB流量日包"), 0.0)

    def test_recharge_only_coupons_are_flagged(self):
        for name in ("充值9.9折券", "满30减5话费券", "首充10元券", "2-3元话费券手气红包"):
            with self.subTest(name=name):
                self.assertTrue(C.classify_award(name)["need_recharge"],
                                f"{name} 有门槛，应被标记")

    def test_fee_redpacket_is_ignored_completely(self):
        """话费型手气红包：不算到手可用，也不显示（用户 2026-11-21 明确要求）。"""
        c = C.classify_award("2-3元话费券手气红包")
        self.assertTrue(c["ignored"], "话费券手气红包应被标记为直接忽略")
        self.assertEqual(c["kind"], "fee")

    def test_flow_redpacket_still_counts(self):
        """流量型手气红包是无门槛到账的，不能跟着话费红包一起丢掉。"""
        for name in ("1GB流量手气红包", "1GB流量日包手气红包"):
            with self.subTest(name=name):
                c = C.classify_award(name)
                self.assertEqual(c["kind"], "flow")
                self.assertFalse(c["need_recharge"], "流量红包没有门槛，应算到手")
                self.assertFalse(c["ignored"], "流量红包要正常显示")
                self.assertEqual(c["size_mb"], 1024.0)

    def test_stale_ledger_rows_are_reclassified_on_read(self):
        """历史台账按旧规则记的「手气红包」读出来也要按新规则处理。"""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "awards.json"
            p.write_text(json.dumps({"records": [
                {"at": "2026-11-21 12:13:58", "account": "13900005678",
                 "source": "天天大转盘", "name": "2-3元话费券手气红包",
                 "kind": "fee", "size_mb": 0.0, "need_recharge": False, "url": ""},
                {"at": "2026-11-21 12:20:00", "account": "13900005678",
                 "source": "天天大转盘", "name": "1GB流量手气红包",
                 "kind": "flow", "size_mb": 1024.0, "need_recharge": True, "url": ""},
            ]}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(C, "AWARDS_FILE", p):
                recs = C.load_awards()
        fee_row = [r for r in recs if "话费券" in r["name"]][0]
        flow_row = [r for r in recs if "1GB" in r["name"]][0]
        self.assertTrue(fee_row["ignored"], "旧的话费红包读出来应被忽略")
        self.assertFalse(flow_row["need_recharge"], "旧的流量红包读出来要改回「无门槛」")

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

    def test_view_hides_threshold_coupons_but_counts_them(self):
        C.record_award("13800001234", "天天大转盘", "1GB流量日包")
        C.record_award("13800001234", "天天大转盘", "充值9.9折券")
        C.record_award("13900005678", "养成领奖", "2-3元话费券手气红包")
        C.record_award("13900005678", "每周秒杀", "1元话费券")
        v = D._awards_view()
        names = [r["name"] for r in v["recent"]]
        self.assertIn("1GB流量日包", names)
        self.assertIn("1元话费券", names)
        self.assertNotIn("充值9.9折券", names, "充值类券不该出现在台账列表里")
        self.assertNotIn("2-3元话费券手气红包", names, "话费手气红包直接不显示")
        self.assertEqual(v["month"]["flow_mb"], 1024.0)
        self.assertEqual(v["month"]["fee_count"], 1, "只算无门槛的 1 元券")
        self.assertEqual(v["month"]["fee_yuan"], 1.0, "金额也要统计（1 元券 = 1 元）")
        self.assertEqual(v["month"]["recharge_count"], 1, "话费手气红包不计入，只剩充值券")
        self.assertEqual(v["month"]["recharge_yuan"], 0.0, "手气红包的面额也不再统计")
        th_names = [x["name"] for x in v["month_threshold"]]
        self.assertNotIn("2-3元话费券手气红包", th_names, "手气红包连「有门槛」明细都不出现")
        self.assertIn("充值9.9折券", th_names, "真正的门槛券仍可在悬停里查到")
        self.assertEqual(v["fee_total"], 1)
        self.assertEqual(v["fee_yuan_total"], 1.0)
        self.assertEqual(v["by_account"]["1234"]["flow_mb"], 1024.0)
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), v["by_day"]["1234"])

    def test_view_splits_by_platform(self):
        """一个号两张卡：福建寻宝中的流量不能同时出现在「全国签到」卡上。"""
        C.record_award("13800001234", "环闽寻宝", "7GB流量券包")
        C.record_award("13800001234", "天天大转盘", "1GB流量日包")
        C.record_award("13800001234", "每周秒杀", "1元话费券")
        v = D._awards_view()
        p = v["by_plat"]["1234"]
        self.assertEqual(p["BASS"]["flow_mb"], 8192.0)
        self.assertEqual(p["BASS"]["fee_count"], 0)
        self.assertEqual(p["qwhdhub"]["flow_mb"], 0.0)
        self.assertEqual(p["qwhdhub"]["fee_count"], 1)
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(v["by_day_plat"]["1234"]["BASS"][today], 8192.0)
        self.assertNotIn("qwhdhub", v["by_day_plat"]["1234"], "全国卡的 7 天图不该画福建的流量")

    def test_platform_mapping(self):
        self.assertEqual(D.award_platform("每日签到"), "qwhdhub")
        self.assertEqual(D.award_platform("每周秒杀"), "qwhdhub")
        self.assertEqual(D.award_platform("环闽寻宝·藏宝阁"), "BASS")
        self.assertEqual(D.award_platform("消消乐"), "BASS")
        self.assertEqual(D.award_platform(""), "BASS")

    def test_view_drops_non_flow_fee_rows(self):
        """打车券、AI豆券这类两栏都是「—」的行不进明细。

        用户要的是「今天到手多少流量、几张话费券」，混进无关券会显得乱，
        而且「面额/折合流量」两列全空，看着像数据没读到。
        """
        C.record_award("13800001234", "环闽寻宝", "1GB流量日包券")
        C.record_award("13800001234", "环闽寻宝", "滴滴打车券包")
        C.record_award("13800001234", "环闽寻宝", "10AI豆兑换券")
        v = D._awards_view()
        names = [r["name"] for r in v["recent"]]
        self.assertIn("1GB流量日包券", names)
        self.assertNotIn("滴滴打车券包", names)
        self.assertNotIn("10AI豆兑换券", names)

    def test_view_drops_paid_bundle(self):
        """「15元购10GB」要花钱买，不能算成到手的 10GB。"""
        C.record_award("13800001234", "环闽寻宝", "15元购10GB流量7天包券")
        v = D._awards_view()
        self.assertEqual([r["name"] for r in v["recent"]], [])
        self.assertEqual(v["month"]["flow_mb"], 0.0)

    def test_flow_redpacket_shows_as_flow(self):
        C.record_award("13900005678", "天天大转盘", "1GB流量手气红包")
        v = D._awards_view()
        self.assertIn("1GB流量手气红包", [r["name"] for r in v["recent"]],
                      "流量手气红包要正常显示")
        self.assertEqual(v["month"]["flow_mb"], 1024.0)


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


class TestPaidCoupon(unittest.TestCase):
    """「15元购10GB」不是免费到手的 10GB。

    环闽寻宝的落点奖里就有这种付费券包（15元购10GB、13元购30GB），
    按老规则会被 parse_size_mb 读成 10GB 算进「今日已得流量」，
    等于把要花钱的东西记成白拿的，用户一对账就会发现数字虚高。
    """

    def test_pay_to_buy_flow_not_counted(self):
        c = C.classify_award("15元购10GB流量7天包券")
        self.assertEqual(c["size_mb"], 0.0)
        self.assertTrue(c["need_recharge"])
        self.assertEqual(c["yuan"], 0.0)       # 15 是售价，不是券面额

    def test_free_flow_pack_still_counted(self):
        c = C.classify_award("7GB流量券包")
        self.assertEqual(c["size_mb"], 7168.0)
        self.assertFalse(c["need_recharge"])

    def test_fee_pack_is_thresholded(self):
        """「888元话费券包」是一包满减券，不能当 888 元可用话费。"""
        c = C.classify_award("888元话费券包")
        self.assertEqual(c["kind"], "fee")
        self.assertTrue(c["need_recharge"])


class TestXunbao(unittest.TestCase):
    """寻宝脚本的两处约定：不代报人工任务、当日次数按天累计。"""

    def setUp(self):
        import cmcc_xunbao
        self.X = cmcc_xunbao
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "xunbao_state.json"
        self.patches = [mock.patch.object(cmcc_xunbao, "STATE_FILE", self.path)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_assist_and_notice_tasks_never_reported(self):
        # 好友助力要真人点链接，打开消息提醒要真去开权限，程序不代做
        self.assertIn("TASK_1501", self.X.SKIP_TASKS)
        self.assertIn("TASK_1502", self.X.SKIP_TASKS)
        self.assertNotIn("TASK_1514", self.X.SKIP_TASKS)   # 参与大转盘是真做过的

    def test_day_counters_accumulate(self):
        self.X.save_state("173****4167", {"seq": 15, "dice": 0}, rolls=2, prizes=1)
        self.X.save_state("173****4167", {"seq": 18, "dice": 0}, rolls=1, prizes=1)
        st = json.loads(self.path.read_text(encoding="utf-8"))["4167"]
        self.assertEqual(st["day_rolls"], 3)
        self.assertEqual(st["day_prizes"], 2)
        self.assertEqual(st["seq"], 18)
