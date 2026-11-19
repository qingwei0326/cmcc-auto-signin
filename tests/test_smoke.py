"""
冒烟测试：覆盖最容易在重构中被改坏的纯函数。

运行：
    python -m unittest discover -s tests -v
    python tests/test_smoke.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common
import har_import
import run_all


class TestCommon(unittest.TestCase):
    def test_atomic_write_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "a.json"
            common.atomic_write_json(p, {"你好": 1, "x": [1, 2]})
            self.assertEqual(common.load_json(p), {"你好": 1, "x": [1, 2]})
            # 不应留下临时文件
            self.assertFalse(list(p.parent.glob("*.tmp")))

    def test_atomic_write_overwrites_and_keeps_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            common.atomic_write_json(p, {"v": 1})
            common.atomic_write_json(p, {"v": 2})
            self.assertEqual(common.load_json(p), {"v": 2})

    def test_load_json_missing_returns_default(self):
        self.assertEqual(common.load_json(Path("/no/such/file.json"), {}), {})
        self.assertEqual(common.load_json(Path("/no/such/file.json"), None), {})

    def test_load_json_broken_returns_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(common.load_json(p, {"fallback": 1}), {"fallback": 1})

    def test_mask_phone(self):
        self.assertEqual(common.mask_phone("18405963239"), "...3239")
        self.assertEqual(common.mask_phone("12"), "12")
        self.assertEqual(common.mask_phone(""), "")

    def test_notify_settings_prefer_env(self):
        os.environ["CMCC_BARK_URL"] = "https://bark.example/x"
        try:
            bark, key = common.collect_notify_settings({"bark_url": "https://other/y"})
            self.assertEqual(bark, "https://bark.example/x")
        finally:
            del os.environ["CMCC_BARK_URL"]
        bark, key = common.collect_notify_settings({"bark_url": "https://other/y"})
        self.assertEqual(bark, "https://other/y")

    def test_notify_without_config_does_nothing(self):
        self.assertFalse(common.notify("t", "b", bark_url="", serverchan_sendkey=""))

    def test_notify_rejects_illegal_sendkey(self):
        # 非法 sendkey 不能被拼进 URL，必须跳过且不抛异常
        self.assertFalse(common.notify("t", "b", serverchan_sendkey="abc/../evil"))


class TestHarImport(unittest.TestCase):
    def _har(self, entries):
        return json.dumps({"log": {"entries": entries}})

    def test_parse_qwhd_token_and_phone(self):
        phone = 18405963239
        text = self._har([
            {"startedDateTime": "2026-11-19T23:00:00",
             "request": {"url": "https://wx.10086.cn/abc/appTokenLogin.do",
                         "headers": [],
                         "postData": {"text": json.dumps({
                             "token": "JSESSIONID=AAA; UID=BBB; ticketID=CCC",
                             "userCheckId": format(phone, "X")})}}},
        ])
        r = har_import.parse_har_text(text)
        self.assertIn(str(phone), r["qwhd"])
        self.assertTrue(r["qwhd"][str(phone)]["token"].startswith("JSESSIONID=AAA"))

    def test_parse_bass_bearer_takes_latest(self):
        text = self._har([
            {"startedDateTime": "2026-11-19T23:00:05",
             "request": {"url": "https://wap.fj.10086.cn/a", "headers": [
                 {"name": "Authorization", "value": "Bearer 11111111-2222-3333-4444-555555555555"}]}},
            {"startedDateTime": "2026-11-19T23:00:09",
             "request": {"url": "https://wap.fj.10086.cn/b", "headers": [
                 {"name": "Authorization", "value": "Bearer 99999999-2222-3333-4444-555555555555"}]}},
        ])
        r = har_import.parse_har_text(text)
        self.assertTrue(r["bass"]["token"].startswith("99999999"))

    def test_parse_empty_har(self):
        r = har_import.parse_har_text(json.dumps({"log": {"entries": []}}))
        self.assertEqual(r["qwhd"], {})
        self.assertFalse(r["bass"])


class TestDailyShortcut(unittest.TestCase):
    """每日奖励空转短路的判定逻辑。"""

    def setUp(self):
        import cmcc_fj
        self.F = cmcc_fj
        self.tmp = Path(tempfile.mkdtemp()) / "daily_state.json"
        self._orig = cmcc_fj.DAILY_STATE
        cmcc_fj.DAILY_STATE = self.tmp

    def tearDown(self):
        self.F.DAILY_STATE = self._orig

    def _write(self, obj):
        self.tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def test_empty_state_not_done(self):
        self._write({})
        self.assertFalse(self.F.daily_nothing_to_do("fj1"))

    def test_all_received_and_signed(self):
        import datetime
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        self._write({"fj1": {"tiers_date": today, "tiers": [
            {"name": "阶段一", "status": "RECEIVED"},
            {"name": "阶段二", "status": "RECEIVED"},
            {"name": "阶段三", "status": "RECEIVED"}],
            "sign": {"today": True}}})
        self.assertTrue(self.F.daily_nothing_to_do("fj1"))

    def test_pending_tier_not_done(self):
        import datetime
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        self._write({"fj1": {"tiers_date": today, "tiers": [
            {"name": "阶段一", "status": "RECEIVED"},
            {"name": "阶段二", "status": "RECEIVED"},
            {"name": "阶段三", "status": "UN_DO"}],
            "sign": {"today": True}}})
        self.assertFalse(self.F.daily_nothing_to_do("fj1"))

    def test_stale_date_not_done(self):
        self._write({"fj1": {"tiers_date": "2000-01-01",
                             "tiers": [{"name": "a", "status": "RECEIVED"}],
                             "sign": {"today": True}}})
        self.assertFalse(self.F.daily_nothing_to_do("fj1"))


class TestRunAll(unittest.TestCase):
    def test_brief_error_prefers_error_line(self):
        text = "noise\nTraceback...\n[ERROR] token 过期\nmore"
        self.assertIn("token 过期", run_all.brief_error(text))

    def test_brief_error_empty(self):
        self.assertEqual(run_all.brief_error(""), "")

    def test_label_of_masks_phone(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            p.write_text(json.dumps({"phone": "18405963239"}), encoding="utf-8")
            self.assertEqual(run_all.label_of(p, "phone"), "...3239")

    def test_label_of_missing_file(self):
        self.assertEqual(run_all.label_of(Path("/no/such.json"), "phone"), "such")

    def test_rotating_log_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            log = run_all.RotatingLog(Path(d) / "daily.log")
            log.line("第一行")
            log.line("第二行")
            out = Path(d) / "daily.log"
            self.assertTrue(out.exists())
            self.assertIn("第一行", out.read_text(encoding="utf-8"))

    def test_rotating_log_disabled(self):
        log = run_all.RotatingLog("")
        log.line("不会写任何文件")
        self.assertFalse(log.enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
