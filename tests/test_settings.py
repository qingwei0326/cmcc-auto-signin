# -*- coding: utf-8 -*-
"""设置页 + PushPlus 的测试。

用户提的两件事：
  1. 增设「设置」页，把「设置一次、长期生效」的项集中过去；
  2. 推送再加一条 PushPlus 通道。
这两件都要保证「配一次就长期生效」，且优先级明确。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402  隔离真实全局配置（见 tests/_guard.py）
import common as C
import dashboard as D


class _FakeResp:
    status_code = 200
    text = "ok"


class _FakeSession:
    """记录所有出网请求的假会话。"""

    calls = []

    def __init__(self, *a, **k):
        self.trust_env = True

    def post(self, url, **kw):
        _FakeSession.calls.append(("POST", url, kw))
        return _FakeResp()

    def get(self, url, **kw):
        _FakeSession.calls.append(("GET", url, kw))
        return _FakeResp()


class TestGlobalSettings(unittest.TestCase):
    """全局设置：存一次、长期生效。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        self.p = mock.patch.object(C, "GLOBAL_SETTINGS_FILE", self.path)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_save_then_load(self):
        C.save_global_settings({"pushplus_token": "abc123"})
        self.assertEqual(C.load_global_settings()["pushplus_token"], "abc123")

    def test_save_keeps_other_fields(self):
        C.save_global_settings({"bark_url": "https://bark/x"})
        C.save_global_settings({"pushplus_token": "abc123"})
        st = C.load_global_settings()
        self.assertEqual(st["bark_url"], "https://bark/x")
        self.assertEqual(st["pushplus_token"], "abc123")

    def test_missing_file_returns_empty(self):
        self.assertEqual(C.load_global_settings(), {})

    def test_global_beats_account_config(self):
        """设置页配过一次就够了，不用每个账号再填。"""
        C.save_global_settings({"bark_url": "https://global/x"})
        ch = C.collect_notify_settings({"bark_url": "https://account/y"})
        self.assertEqual(ch["bark_url"], "https://global/x")

    def test_env_beats_global(self):
        C.save_global_settings({"bark_url": "https://global/x"})
        os.environ["CMCC_BARK_URL"] = "https://env/z"
        try:
            ch = C.collect_notify_settings()
            self.assertEqual(ch["bark_url"], "https://env/z")
        finally:
            del os.environ["CMCC_BARK_URL"]

    def test_account_used_when_nothing_else_set(self):
        ch = C.collect_notify_settings({"serverchan_sendkey": "SCT123"})
        self.assertEqual(ch["serverchan_sendkey"], "SCT123")


class TestPushPlus(unittest.TestCase):
    """PushPlus 通道。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        self.p = mock.patch.object(C, "GLOBAL_SETTINGS_FILE", self.path)
        self.p.start()
        _FakeSession.calls = []
        # 这个类专门验证「真的发出去长什么样」，用的是假会话不会真出网，
        # 所以要关掉测试守卫里的 dry 模式。
        self._dry = os.environ.pop("CMCC_NOTIFY_DRY", None)

    def tearDown(self):
        if self._dry is not None:
            os.environ["CMCC_NOTIFY_DRY"] = self._dry
        self.p.stop()
        self.tmp.cleanup()

    def test_token_format(self):
        self.assertTrue(C.pushplus_token_ok("a" * 32))
        self.assertTrue(C.pushplus_token_ok("0123abcd-0123-abcd-0123-abcd0123abcd"))
        self.assertFalse(C.pushplus_token_ok(""))
        self.assertFalse(C.pushplus_token_ok("short"))
        self.assertFalse(C.pushplus_token_ok("有中文的令牌"))

    def test_notify_posts_to_pushplus(self):
        with mock.patch.object(C.requests, "Session", _FakeSession):
            ok = C.notify("标题", "正文", pushplus_token="a" * 32)
        self.assertTrue(ok)
        post = [c for c in _FakeSession.calls if c[0] == "POST"]
        self.assertTrue(post)
        self.assertEqual(post[0][1], C.PUSHPLUS_URL)
        body = post[0][2]["json"]
        self.assertEqual(body["token"], "a" * 32)
        self.assertEqual(body["title"], "标题")
        self.assertEqual(body["content"], "正文")

    def test_illegal_token_is_skipped(self):
        with mock.patch.object(C.requests, "Session", _FakeSession):
            ok = C.notify("t", "b", pushplus_token="坏 token/../x")
        self.assertFalse(ok)
        self.assertEqual(_FakeSession.calls, [])

    def test_global_token_used_when_no_explicit(self):
        C.save_global_settings({"pushplus_token": "b" * 32})
        with mock.patch.object(C.requests, "Session", _FakeSession):
            ok = C.notify("t", "b")
        self.assertTrue(ok)
        self.assertEqual(_FakeSession.calls[0][1], C.PUSHPLUS_URL)

    def test_notify_once_forwards_pushplus(self):
        seen = {}

        def fake_notify(title, detail, bark="", key="", pp=""):
            seen["pp"] = pp
            return True

        with mock.patch.object(C, "notify", fake_notify):
            C.notify_once("k_test_pp", "t", "b", hours=0, pushplus_token="c" * 32)
        self.assertEqual(seen["pp"], "c" * 32)


class TestSettingsAction(unittest.TestCase):
    """控制台的设置相关动作。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        self.p = mock.patch.object(C, "GLOBAL_SETTINGS_FILE", self.path)
        self.p.start()
        self.p2 = mock.patch.object(D, "load_global_settings", C.load_global_settings)
        self.p2.start()
        self.p3 = mock.patch.object(D, "save_global_settings", C.save_global_settings)
        self.p3.start()

    def tearDown(self):
        self.p.stop()
        self.p2.stop()
        self.p3.stop()
        self.tmp.cleanup()

    def _save(self, push):
        return D.do_action({"action": "settings_save", "push": push})

    def test_save_pushplus(self):
        r = self._save({"pushplus_token": "d" * 32})
        self.assertTrue(r["ok"])
        self.assertEqual(C.load_global_settings()["pushplus_token"], "d" * 32)

    def test_reject_bad_bark(self):
        r = self._save({"bark_url": "ftp://x"})
        self.assertFalse(r["ok"])
        self.assertIn("http", r["err"])

    def test_reject_bad_pushplus(self):
        r = self._save({"pushplus_token": "短"})
        self.assertFalse(r["ok"])
        self.assertIn("PushPlus", r["err"])


class TestSettingsPage(unittest.TestCase):
    """界面上要有「设置」页，并且长期项都搬过去了。"""

    @classmethod
    def setUpClass(cls):
        cls.html = Path(__file__).resolve().parent.parent / "static" / "index.html"
        cls.html = cls.html.read_text(encoding="utf-8")

    def test_sidebar_has_settings(self):
        self.assertIn('data-p="settings"', self.html)
        self.assertIn("settings:'设置'", self.html)

    def test_push_fields_exist(self):
        for i in ("sBark", "sSendkey", "sPushplus"):
            self.assertIn(f'id="{i}"', self.html)

    def test_pushplus_mentioned(self):
        self.assertIn("PushPlus", self.html)

    def test_strategy_moved_to_settings(self):
        """策略面板里的字段在设置页，而不是散在活动页。"""
        self.assertIn('id="pFeed"', self.html)
        self.assertIn('id="pCouponWorld"', self.html)
        # 消消乐 / 秒杀不再各自存一份参数
        self.assertNotIn('id="xLevel"', self.html)
        self.assertNotIn('id="skAt"', self.html)

    def test_settings_functions_exist(self):
        for fn in ("function renderSettings", "function saveSettings", "function gotoSettings"):
            self.assertIn(fn, self.html)

    def test_activity_pages_link_to_settings(self):
        self.assertIn('id="xxlParams"', self.html)
        self.assertIn('id="skAtShow"', self.html)


class TestXxlArgsFromPolicy(unittest.TestCase):
    """消消乐参数改成从「设置」读后是数字，子进程只收字符串。

    以前前端传的是输入框里的字符串，现在从 policy 读出来是 number，
    直接丢给 subprocess 会报「expected str, bytes or os.PathLike object」。
    """

    def test_args_are_strings(self):
        captured = {}

        def fake_start(label, cmd, **kw):
            captured["cmd"] = cmd
            return {"ok": True}

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "configs_fj").mkdir()
            (base / "configs_fj" / "fj_test.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(D, "BASE", base), \
                 mock.patch.object(D, "_start", fake_start):
                r = D.do_action({"action": "xxl_run", "file": "configs_fj/fj_test.json",
                                 "level": 49, "star": 3, "times": 5, "gap": 12})
            self.assertTrue(r["ok"])
            self.assertTrue(all(isinstance(x, str) for x in captured["cmd"]),
                            f"有非字符串参数：{captured['cmd']}")
            self.assertIn("49", captured["cmd"])


if __name__ == "__main__":
    unittest.main()
