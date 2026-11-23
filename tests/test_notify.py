"""通知与子进程启动方式的回归测试。

两件事容易悄悄坏掉，所以单独钉住：

1. **凭证失效只发一条**。每日奖励每小时跑一轮，票过期时两三个号各失败一次，
   早先就是每轮每人推一条，一天能收十几条一模一样的「失败」——用户的原话是
   「凭证失效直接一个通知就好了，搞那么多个通知干嘛，软件肯定是一个人用」。
2. **冻结成 exe 后不依赖系统 Python**。exe 自己带 --run-script 解释执行脚本，
   路径构造集中在 common.child_cmd，源码/打包两条路不能走岔。
"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
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
        env = dict(os.environ)
        env.pop("CMCC_NOTIFY_DRY", None)
        env["CMCC_NOTIFY_BATCH"] = "1"
        with mock.patch.dict(os.environ, env, clear=True), \
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


class TestTodayFinished(unittest.TestCase):
    """「今天做完了」的判据：决定 token 过期要不要发通知。

    用户的规则：做完了就发总结，当天 token 过期也别再发通知。
    判据必须看真实收益/真实执行，不能拿只读刷新蒙混过关。
    """

    def setUp(self):
        import run_all
        self.R = run_all
        self.day = "2026-11-22"

    def test_award_today_means_finished(self):
        awards = [{"at": f"{self.day} 14:25:00", "name": "100MB", "ignored": False}]
        self.assertTrue(self.R.today_finished(awards=awards, runs=[], today=self.day))

    def test_ignored_award_does_not_count(self):
        awards = [{"at": f"{self.day} 10:00:00", "name": "手气红包", "ignored": True}]
        self.assertFalse(self.R.today_finished(awards=awards, runs=[], today=self.day))

    def test_award_from_other_day_does_not_count(self):
        awards = [{"at": "2026-11-21 10:00:00", "name": "1元话费券", "ignored": False}]
        self.assertFalse(self.R.today_finished(awards=awards, runs=[], today=self.day))

    def test_successful_real_run_this_round_counts(self):
        results = [("qwhd", "...3239", True, 12.3)]
        self.assertTrue(self.R.today_finished(
            awards=[], runs=[], today=self.day, results=results))

    def test_failed_run_does_not_count(self):
        results = [("qwhd", "...3239", False, 1.4)]  # 票过期导致的失败
        self.assertFalse(self.R.today_finished(
            awards=[], runs=[], today=self.day, results=results))

    def test_dryrun_success_does_not_count(self):
        """只读检查票过期也能 ok，不能拿它证明今天干完活。"""
        results = [("qwhd", "...3239", True, 1.4)]
        self.assertFalse(self.R.today_finished(
            awards=[], runs=[], today=self.day, results=results, dry_run=True))

    def test_readonly_refresh_in_runs_does_not_count(self):
        """今天 11:55 那条「刷新全部账号状态（只读检查）」ok，但票其实已过期。"""
        runs = [{"ts": f"{self.day} 11:55:50", "label": "刷新全部账号状态（只读检查）",
                 "ok": True}]
        self.assertFalse(self.R.today_finished(awards=[], runs=runs, today=self.day))

    def test_real_run_in_runs_counts(self):
        runs = [{"ts": f"{self.day} 14:22:04", "label": "拓展活动 · 4167", "ok": True}]
        self.assertTrue(self.R.today_finished(awards=[], runs=runs, today=self.day))

    def test_nothing_done(self):
        self.assertFalse(self.R.today_finished(awards=[], runs=[], today=self.day))


class TestLocalNotifyBatchQuiet(unittest.TestCase):
    """cmcc_sign 的本地 notify 在批量模式下必须闭嘴——签到/秒杀/周三都复用它。

    这是「任务做完了还推好几次」的真凶：run_all 结尾会发一条汇总战报，
    但子脚本每个号、每类动作都各自直连 pushplus 推一条，几号叠加就刷屏。
    """

    def setUp(self):
        import cmcc_sign
        self.M = cmcc_sign
        # notify 只用到这三个字段，不必真去读 config.json
        self.cfg = type("Cfg", (), {"serverchan_sendkey": "", "bark_url": "",
                                    "pushplus_token": "a" * 32})()

    def test_batch_mode_suppresses_local_notify(self):
        """批量运行：任何一条（含签到成功）都不许发——会话都不该被创建。"""
        with mock.patch.dict(os.environ, {"CMCC_NOTIFY_BATCH": "1"}), \
                mock.patch.object(self.M.requests, "Session",
                                  side_effect=AssertionError("批量模式不该创建会话")):
            self.assertFalse(self.M.notify(self.cfg, "移动签到通知", "签到成功"))

    def test_all_shared_scripts_use_same_notify(self):
        """静态护栏：秒杀 / 周三活动都从 cmcc_sign 导入同一个 notify。

        以后谁给某个脚本换了独立推送函数，这条会提醒他同步补批量静默。
        """
        for name in ("cmcc_seckill", "cmcc_wed"):
            src = Path(__file__).resolve().parent.parent.joinpath(name + ".py").read_text(
                encoding="utf-8")
            self.assertIn("from cmcc_sign import", src, name)
            self.assertRegex(src, r"\bnotify\b", name)

    def test_standalone_still_pushes(self):
        """单独跑脚本时 CMCC_NOTIFY_BATCH 未设，通知要照常发。"""
        env = dict(os.environ)
        env.pop("CMCC_NOTIFY_BATCH", None)
        env.pop("CMCC_NOTIFY_DRY", None)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.M.requests, "Session") as sess:
            sess.return_value.post.return_value = mock.Mock(status_code=200)
            self.M.notify(self.cfg, "移动签到通知", "签到成功")
            self.assertTrue(sess.return_value.post.called, "单独运行应该发 pushplus")


class TestMailWatchNotifyDedup(unittest.TestCase):
    """HAR 导入/补跑通知要走 notify_once，别每 5 分钟轮询重复推。"""

    def setUp(self):
        import mail_watch
        self.src = Path(mail_watch.__file__).resolve().read_text(encoding="utf-8")

    def test_imports_notify_once(self):
        self.assertIn("notify_once", self.src)

    def test_no_bare_notify_calls(self):
        # 除 import 行和注释外，不应再有裸 notify(...) 调用
        import re
        calls = re.findall(r"(?<!once)\bnotify\(", self.src)
        self.assertEqual(calls, [], f"仍有裸通知调用：{calls}")

    def test_uses_distinct_keys(self):
        self.assertIn('notify_once("har_import_done"', self.src)
        self.assertIn('notify_once("har_catchup_failed"', self.src)


class TestNotifyDecisionStatic(unittest.TestCase):
    """静态护栏：推送规则别被改回「只要有过期就发票过期通知」。"""

    def setUp(self):
        import run_all
        self.src = (Path(run_all.__file__).resolve()).read_text(encoding="utf-8")

    def test_token_dead_suppressed_when_done(self):
        self.assertIn("TOKEN_DEAD_ACCOUNTS and not done", self.src)

    def test_daily_report_sent_when_done(self):
        self.assertIn("if not args.no_notify and done:", self.src)

    def test_done_passes_current_results_and_dryrun(self):
        self.assertIn("today_finished(results=results, dry_run=args.dry_run)", self.src)


class TestNotifyReliabilityAndSSRF(unittest.TestCase):
    """覆盖 P1-#10：响应码校验、单通道异常隔离、SSRF 防护、统一 notify 与并发去重。"""

    def _no_dry_env(self):
        env = dict(os.environ)
        env.pop("CMCC_NOTIFY_DRY", None)
        env.pop("CMCC_NOTIFY_BATCH", None)
        return env

    def test_rejects_business_and_http_errors_and_skips_once_state(self):
        class FakeResp:
            def __init__(self, status_code=200, payload=None):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(C, "NOTIFY_STATE_FILE", Path(td) / "notify_state.json"), \
             mock.patch.dict(os.environ, self._no_dry_env(), clear=True):
            # 1. PushPlus code=900 判失败，且 notify_once 不写入去重时间戳
            with mock.patch.object(C.requests, "Session") as sess:
                sess.return_value.post.return_value = FakeResp(200, {"code": 900, "msg": "bad token"})
                with redirect_stdout(io.StringIO()):
                    self.assertFalse(C.notify_once("k_pp", "标题", "正文", pushplus_token="a" * 32))
                self.assertFalse((Path(td) / "notify_state.json").exists())

            # 2. Bark HTTP 404 判失败
            with mock.patch.object(C.requests, "Session") as sess:
                sess.return_value.get.return_value = FakeResp(404, {"code": 404})
                with redirect_stdout(io.StringIO()):
                    self.assertFalse(C.notify("标题", "正文", bark_url="https://api.day.app/testkey"))

            # 3. Server酱 code=40001 判失败
            with mock.patch.object(C.requests, "Session") as sess:
                sess.return_value.post.return_value = FakeResp(200, {"code": 40001, "message": "bad key"})
                with redirect_stdout(io.StringIO()):
                    self.assertFalse(C.notify("标题", "正文", serverchan_sendkey="SCT123ABC"))

    def test_channel_exception_does_not_block_next_channel(self):
        class OkResp:
            status_code = 200

            def json(self):
                return {"code": 200, "msg": "ok"}

        def fake_post(url, **kw):
            if "ftqq.com" in url:
                raise ConnectionError("serverchan down")
            return OkResp()

        with mock.patch.dict(os.environ, self._no_dry_env(), clear=True), \
             mock.patch.object(C.requests, "Session") as sess, \
             redirect_stdout(io.StringIO()):
            sess.return_value.post.side_effect = fake_post
            ok = C.notify("标题", "正文", serverchan_sendkey="SCT123", pushplus_token="a" * 32)
        self.assertTrue(ok)
        self.assertEqual(sess.return_value.post.call_count, 2)

    def test_bark_ssrf_blocked_in_notify_and_dashboard(self):
        import dashboard
        for bad in ("http://127.0.0.1/key", "http://localhost/key",
                    "http://169.254.169.254/latest/meta-data", "http://192.168.1.10/key"):
            with mock.patch.dict(os.environ, self._no_dry_env(), clear=True), \
                 mock.patch.object(C.requests, "Session") as sess, \
                 redirect_stdout(io.StringIO()):
                self.assertFalse(C.notify("t", "b", bark_url=bad), bad)
                sess.return_value.get.assert_not_called()
            with tempfile.TemporaryDirectory() as td, \
                 mock.patch.object(C, "GLOBAL_SETTINGS_FILE", Path(td) / "settings.json"), \
                 mock.patch.object(dashboard, "save_global_settings", C.save_global_settings):
                res = dashboard.do_action({"action": "settings_save", "push": {"bark_url": bad}})
                self.assertFalse(res["ok"], bad)

    def test_cmcc_sign_notify_respects_dry_and_delegates_to_common(self):
        import cmcc_sign
        cfg = type("Cfg", (), {"serverchan_sendkey": "", "bark_url": "",
                               "pushplus_token": "b" * 32})()
        dry_env = self._no_dry_env()
        dry_env["CMCC_NOTIFY_DRY"] = "1"
        with mock.patch.dict(os.environ, dry_env, clear=True), \
             mock.patch.object(C.requests, "Session") as sess:
            sess.return_value.post.side_effect = AssertionError("dry 模式不应发请求")
            self.assertTrue(cmcc_sign.notify(cfg, "标题", "正文"))

        class BadResp:
            status_code = 200

            def json(self):
                return {"code": 900, "msg": "invalid"}

        with mock.patch.dict(os.environ, self._no_dry_env(), clear=True), \
             mock.patch.object(C.requests, "Session") as sess, \
             redirect_stdout(io.StringIO()):
            sess.return_value.post.return_value = BadResp()
            self.assertFalse(cmcc_sign.notify(cfg, "标题", "正文"))

    def test_notify_once_concurrent_only_sends_once(self):
        with tempfile.TemporaryDirectory() as td:
            nf = Path(td) / "notify_state.json"
            calls = []
            barrier = threading.Barrier(5)

            def fake_notify(*a, **k):
                calls.append(a)
                return True

            with mock.patch.object(C, "NOTIFY_STATE_FILE", nf), \
                 mock.patch.object(C, "notify", fake_notify):
                def worker():
                    barrier.wait()
                    C.notify_once("same_key", "标题", "正文")

                threads = [threading.Thread(target=worker) for _ in range(5)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            self.assertEqual(len(calls), 1)

