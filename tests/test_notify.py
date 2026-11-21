"""通知与子进程启动方式的回归测试。

两件事容易悄悄坏掉，所以单独钉住：

1. **凭证失效只发一条**。每日奖励每小时跑一轮，票过期时两三个号各失败一次，
   早先就是每轮每人推一条，一天能收十几条一模一样的「失败」——用户的原话是
   「凭证失效直接一个通知就好了，搞那么多个通知干嘛，软件肯定是一个人用」。
2. **冻结成 exe 后不依赖系统 Python**。exe 自己带 --run-script 解释执行脚本，
   路径构造集中在 common.child_cmd，源码/打包两条路不能走岔。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402  隔离真实全局配置（见 tests/_guard.py）
import common as C          # noqa: E402


class TestTokenDeadDetect(unittest.TestCase):
    def test_recognizes_expired_ticket(self):
        for text in ("token 失效（401），跳过。",
                     "登录态已过期，请重新抓包",
                     "获取会话失败：会话缺失",
                     "WX 未登录，跳过",
                     "HTTP 401"):
            self.assertTrue(C.is_token_dead(text), text)

    def test_plain_failure_is_not_token(self):
        for text in ("体力不足，无法进行挑战", "当前查询人数过多，请稍后再试", ""):
            self.assertFalse(C.is_token_dead(text), text)


class TestNotifyOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "notify_state.json"
        self.patches = [
            mock.patch.object(C, "NOTIFY_STATE_FILE", self.state),
            mock.patch.object(C, "notify", mock.Mock(return_value=True)),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in self.patches:
            p.start()
        os.environ.pop("CMCC_NOTIFY_BATCH", None)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_same_key_only_once(self):
        self.assertTrue(C.notify_once("token_dead", "凭证过期", "a、b"))
        self.assertFalse(C.notify_once("token_dead", "凭证过期", "a、b"))
        self.assertEqual(C.notify.call_count, 1)

    def test_different_keys_both_send(self):
        self.assertTrue(C.notify_once("token_dead", "凭证过期", "a"))
        self.assertTrue(C.notify_once("run_failed", "签到失败", "b"))
        self.assertEqual(C.notify.call_count, 2)

    def test_expired_window_sends_again(self):
        C.notify_once("token_dead", "凭证过期", "a", hours=0)
        self.assertTrue(C.notify_once("token_dead", "凭证过期", "a", hours=0))
        self.assertEqual(C.notify.call_count, 2)

    def test_state_file_written(self):
        C.notify_once("token_dead", "凭证过期", "a")
        st = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertIn("token_dead", st)


class TestBatchSuppression(unittest.TestCase):
    def test_child_script_keeps_quiet_about_token(self):
        """run_all 会统一发一条，子脚本再推就重复了。"""
        with mock.patch.dict(os.environ, {"CMCC_NOTIFY_BATCH": "1"}), \
                mock.patch.object(C.requests, "Session", side_effect=AssertionError("不该发")):
            self.assertFalse(C.notify("移动签到失败", "token 失效（401）", "http://x"))

    def test_other_failures_still_push(self):
        with mock.patch.dict(os.environ, {"CMCC_NOTIFY_BATCH": "1"}), \
                mock.patch.object(C.requests, "Session") as sess:
            sess.return_value.get.return_value = mock.Mock(status_code=200)
            self.assertTrue(C.notify("移动签到失败", "体力不足", "http://x"))


class TestChildCmd(unittest.TestCase):
    def test_source_mode_uses_python(self):
        cmd = C.child_cmd("cmcc_xunbao.py", "--key", "fj1", "--dry-run")
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("cmcc_xunbao.py"))
        self.assertEqual(cmd[2:], ["--key", "fj1", "--dry-run"])

    def test_frozen_mode_reruns_exe(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            cmd = C.child_cmd("cmcc_sign.py", "--config", "x.json")
        self.assertEqual(cmd[1], C.RUN_SCRIPT_FLAG)
        self.assertTrue(cmd[2].endswith("cmcc_sign.py"))
        self.assertEqual(cmd[3:], ["--config", "x.json"])

    def test_exe_dispatches_run_script(self):
        """app_desktop 收到 --run-script 要去跑脚本，而不是开窗口。"""
        import app_desktop
        with mock.patch.object(sys, "argv", ["签到领福利.exe", "--run-script",
                                             str(Path("cmcc_xunbao.py").resolve()), "--dry-run"]), \
                mock.patch("runpy.run_path") as rp, \
                mock.patch("pathlib.Path.is_file", lambda self: True):
            self.assertTrue(app_desktop.dispatch_run_script())
            self.assertTrue(str(rp.call_args[0][0]).endswith("cmcc_xunbao.py"))
            self.assertEqual(rp.call_args[1]["run_name"], "__main__")
            # 脚本自己用 argparse 读 sys.argv，前面的 exe/--run-script 必须摘掉
            self.assertEqual(sys.argv[1:], ["--dry-run"])

    def test_normal_start_is_not_dispatched(self):
        import app_desktop
        with mock.patch.object(sys, "argv", ["签到领福利.exe"]):
            self.assertFalse(app_desktop.dispatch_run_script())


class TestRunAllTokenNotice(unittest.TestCase):
    """两个号同时过期，只应产生一条通知，且点明是哪几个号。"""

    def setUp(self):
        import run_all
        self.R = run_all
        self.R.TOKEN_DEAD_ACCOUNTS.clear()

    def tearDown(self):
        self.R.TOKEN_DEAD_ACCOUNTS.clear()

    def test_accounts_deduped(self):
        self.R.note_token_dead("...3239")
        self.R.note_token_dead("...3239")
        self.R.note_token_dead("...4167")
        self.assertEqual(self.R.TOKEN_DEAD_ACCOUNTS, ["...3239", "...4167"])

    def test_notice_mentions_all_accounts_and_steps(self):
        title, detail = self.R.token_dead_notice(["...3239", "...4167"])
        self.assertIn("凭证", title)
        self.assertIn("...3239", detail)
        self.assertIn("...4167", detail)
        self.assertIn("导出 HAR", detail)      # 要能照着做，不能只说「失败了」


class TestRunAllDetectsTokenDead(unittest.TestCase):
    """run_all 要能从子脚本输出里认出「票过期」，才能合并成一条通知。"""

    def setUp(self):
        import run_all
        self.R = run_all
        self.R.TOKEN_DEAD_ACCOUNTS.clear()

    def tearDown(self):
        self.R.TOKEN_DEAD_ACCOUNTS.clear()

    def test_run_script_marks_token_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake_script.py"
            fake.write_text("print('token 失效（401），跳过。')\n", encoding="utf-8")
            conf = Path(tmp) / "cfg.json"
            conf.write_text('{"phone": "13800001234"}', encoding="utf-8")
            self.R.run_script(fake, conf, [], 60)
        self.assertTrue(self.R.TOKEN_DEAD_ACCOUNTS, "没认出凭证过期")

    def test_normal_failure_not_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake_script.py"
            fake.write_text("import sys;print('体力不足，无法进行挑战');sys.exit(1)\n",
                            encoding="utf-8")
            conf = Path(tmp) / "cfg.json"
            conf.write_text('{"phone": "13800001234"}', encoding="utf-8")
            self.R.run_script(fake, conf, [], 60)
        self.assertEqual(self.R.TOKEN_DEAD_ACCOUNTS, [])
