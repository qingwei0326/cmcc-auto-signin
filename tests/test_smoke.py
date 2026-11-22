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
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402  隔离真实全局配置（见 tests/_guard.py）
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
        self.assertEqual(common.mask_phone("13800001234"), "...1234")
        self.assertEqual(common.mask_phone("12"), "12")
        self.assertEqual(common.mask_phone(""), "")

    def test_notify_settings_prefer_env(self):
        os.environ["CMCC_BARK_URL"] = "https://bark.example/x"
        try:
            ch = common.collect_notify_settings({"bark_url": "https://other/y"})
            self.assertEqual(ch["bark_url"], "https://bark.example/x")
        finally:
            del os.environ["CMCC_BARK_URL"]
        ch = common.collect_notify_settings({"bark_url": "https://other/y"})
        self.assertEqual(ch["bark_url"], "https://other/y")

    def test_notify_without_config_does_nothing(self):
        self.assertFalse(common.notify("t", "b", bark_url="", serverchan_sendkey=""))

    def test_notify_rejects_illegal_sendkey(self):
        # 非法 sendkey 不能被拼进 URL，必须跳过且不抛异常
        self.assertFalse(common.notify("t", "b", serverchan_sendkey="abc/../evil"))


class TestHarImport(unittest.TestCase):
    def _har(self, entries):
        return json.dumps({"log": {"entries": entries}})

    def test_parse_qwhd_token_and_phone(self):
        phone = 13800001234
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
        self._write({"fj1": {"tiers_date": today, "feed_reward_date": today, "tiers": [
            {"name": "阶段一", "status": "RECEIVED"},
            {"name": "阶段二", "status": "RECEIVED"},
            {"name": "阶段三", "status": "RECEIVED"}],
            "sign": {"today": True}}})
        self.assertTrue(self.F.daily_nothing_to_do("fj1"))

    def test_pending_feed_reward_prevents_shortcut(self):
        import datetime
        today = datetime.date.today().isoformat()
        self._write({"fj1": {"tiers_date": today, "feed_reward_date": today,
            "tiers": [{"status": "RECEIVED"}] * 3, "sign": {"today": True},
            "feed_reward": {"fodder": 750}}})
        self.assertFalse(self.F.daily_nothing_to_do("fj1"))

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
            p.write_text(json.dumps({"phone": "13800001234"}), encoding="utf-8")
            self.assertEqual(run_all.label_of(p, "phone"), "...1234")

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


class TestSeckill(unittest.TestCase):
    """秒杀相关的纯逻辑（选场次、状态判定、会话路径隔离）。"""

    def test_pick_zone_prefers_active(self):
        from cmcc_seckill import pick_zone
        zones = [
            {"id": 1, "startTime": "1000", "endTime": "2000"},
            {"id": 2, "startTime": "3000", "endTime": "4000"},
            {"id": 3, "startTime": "5000", "endTime": "6000"},
        ]
        z, active = pick_zone(zones, 1500)
        self.assertEqual(z["id"], 1)
        self.assertTrue(active)
        # 不在任何场次内 → 取下一个未开始的
        z, active = pick_zone(zones, 2500)
        self.assertEqual(z["id"], 2)
        self.assertFalse(active)
        # 全部结束 → None
        z, active = pick_zone(zones, 99999)
        self.assertIsNone(z)

    def test_stop_statuses_cover_terminal_cases(self):
        from cmcc_seckill import STOP_STATUSES
        for s in ("PRIZE_NO_STOCK", "ZONE_TODAY_COMPLETE", "PRIZE_LIMIT_DAY",
                  "PRIZE_LIMIT_MONTH", "PRIZE_RESTRIC_LIMIT",
                  "WORK_ORDER_RESTRIC_LIMIT", "PARAM_ERROR"):
            self.assertIn(s, STOP_STATUSES)
        # 明确不能停的两种
        self.assertNotIn("NOT_TIME_IN", STOP_STATUSES)
        self.assertNotIn("SUCCESS", STOP_STATUSES)

    def test_weekly_limit_requires_explicit_already_claimed_message(self):
        from cmcc_seckill import weekly_limit_reached

        status = "WORK_ORDER_RESTRIC_LIMIT"
        self.assertTrue(weekly_limit_reached({"status": status, "msg": "您已领取奖励，限领一次哦"}))
        self.assertFalse(weekly_limit_reached({"status": status, "msg": "操作过于频繁"}))
        self.assertFalse(weekly_limit_reached({"status": "TRAFFIC_LIMITED"}))


    def test_session_file_is_per_account(self):
        """会话必须按账号隔离，否则另一个号会误用本号的会话抢券、券记错人头。"""
        import cmcc_sign
        cfg_a = cmcc_sign.Config.__new__(cmcc_sign.Config)
        cfg_a.phone = "13800001234"
        cfg_b = cmcc_sign.Config.__new__(cmcc_sign.Config)
        cfg_b.phone = "13900005678"
        fa = cmcc_sign.activity_session_file(cfg_a)
        fb = cmcc_sign.activity_session_file(cfg_b)
        self.assertNotEqual(fa, fb)
        self.assertTrue(fa.name.endswith("1234.json"))
        self.assertTrue(fb.name.endswith("5678.json"))

    def test_server_time_conversion_has_no_double_scaling(self):
        """直接测生产函数：毫秒偏移不能再乘 1000（曾偏了约 1000 秒）。"""
        import cmcc_seckill as SK
        local_ms = 1700000000000.0
        self.assertEqual(SK.srv_now_ms(-1021.0, local_ms), local_ms - 1021.0)
        self.assertEqual(SK.srv_now_ms(1500.0, local_ms), local_ms + 1500.0)
        # 反向换算：服务器毫秒 → 本机时钟秒
        self.assertAlmostEqual(SK.srv_ms_to_local_s(local_ms + 1500.0, 1500.0),
                               local_ms / 1000.0, places=6)

    def test_pre_window_blocks_early_fire(self):
        """距开抢超出提前窗口时必须直接放弃，不能密集重试刷风控。"""
        import time
        import cmcc_seckill as SK
        calls = []
        real = SK.api_post

        def fake(s, ref, url, payload=None):
            calls.append(url)
            return {"code": "FAILED", "status": "NOT_TIME_IN", "msg": "未开抢"}

        SK.api_post = fake
        try:
            zone = {"id": 7, "prize": {"id": 9, "name": "1元话费券"}}
            start = time.time() * 1000 + 3600 * 1000    # 一小时后才开抢
            resp, reason = SK.fire_redeem(
                None, "ref", zone, interval=0.01, max_attempts=3,
                deadline_srv_ms=start + 3600 * 1000, offset_ms=0.0,
                start_srv_ms=start, pre_window_s=60.0)
            self.assertIsNone(resp)
            self.assertIn("超出提前窗口", reason)
            self.assertEqual(len(calls), 1)              # 只打一发就收手
        finally:
            SK.api_post = real
        # NOT_TIME_IN 不能是终态（否则未开抢就停了）；NET_ERROR 是终态
        self.assertNotIn("NOT_TIME_IN", SK.STOP_STATUSES)
        self.assertIn("NET_ERROR", SK.STOP_STATUSES)

    def test_session_life_is_two_hours(self):
        """活动会话实测 2 小时硬过期，这是秒杀能否赶上 12:00 的硬约束。"""
        import cmcc_sign
        self.assertEqual(cmcc_sign.SESSION_LIFE_HOURS, 2.0)

    def test_session_expired_calculation(self):
        """会话文件里的 expires_at 决定是否过期。"""
        import datetime
        import json
        import tempfile
        from pathlib import Path
        import cmcc_sign

        cfg = cmcc_sign.Config.__new__(cmcc_sign.Config)
        cfg.phone = "13800138000"
        with tempfile.TemporaryDirectory() as d:
            real = cmcc_sign.activity_session_file
            cmcc_sign.activity_session_file = lambda c: Path(d) / "s.json"
            try:
                now = datetime.datetime.now()
                # 刚建立的会话 → 未过期
                Path(d, "s.json").write_text(json.dumps({
                    "at": now.isoformat(timespec="seconds"),
                    "expires_at": (now + datetime.timedelta(hours=2)).isoformat(timespec="seconds"),
                }), encoding="utf-8")
                self.assertFalse(cmcc_sign.session_expired(cfg))
                # 3 小时前建立的会话 → 已过期
                old = now - datetime.timedelta(hours=3)
                Path(d, "s.json").write_text(json.dumps({
                    "at": old.isoformat(timespec="seconds"),
                    "expires_at": (old + datetime.timedelta(hours=2)).isoformat(timespec="seconds"),
                }), encoding="utf-8")
                self.assertTrue(cmcc_sign.session_expired(cfg))
            finally:
                cmcc_sign.activity_session_file = real

    def test_goto_login_counts_as_dead(self):
        """会话失效时服务端返回 GOTO_LOGIN（不是 401），探活必须认这个码。"""
        import cmcc_seckill as SK

        class FakeSession:
            def __init__(self, code):
                self.code = code

            def post(self, *a, **k):
                return types.SimpleNamespace(json=lambda: {"code": self.code})

        for code, alive in (("GOTO_LOGIN", False), ("401", False), ("SUCCESS", True)):
            self.assertIs(SK.session_alive(FakeSession(code), "ref"), alive, code)

    def test_section_table_has_seckill(self):
        import dashboard
        bundle = dashboard.load_frontend_bundle()
        self.assertIn("cmcc_seckill.py", bundle)
        self.assertIn("seckill_run", bundle)
        self.assertIn("runSeckill", bundle)


class TestTokenLifeProbe(unittest.TestCase):
    """app_token 寿命精确探测器的行为守护。"""

    def test_issued_local_converts_utc_to_local(self):
        """HAR 的 startedDateTime 是 UTC，必须 +8 小时换算成本地时间，
        否则签发时刻会差 8 小时，寿命结论完全错。"""
        self.assertEqual(har_import._issued_local("2026-11-20T04:09:25.123456Z"),
                         "2026-11-20T12:09:25")
        self.assertEqual(har_import._issued_local("2026-11-20T00:00:00Z"),
                         "2026-11-20T08:00:00")

    def test_issued_local_handles_bad_input(self):
        """坏输入必须返回空串而不是抛异常（HAR 可能缺时间字段）。"""
        self.assertEqual(har_import._issued_local(""), "")
        self.assertEqual(har_import._issued_local("garbage"), "")
        self.assertEqual(har_import._issued_local(None), "")

    def test_conclusion_reports_life_range(self):
        """结论里应给出「签发后多少分钟仍有效」与「寿命区间」。"""
        import probe_token_life as PT
        rec = {
            "issued_at": "2026-11-20T08:00:00",
            "samples": [
                {"at": "2026-11-20T08:30:00", "ok": True, "detail": "登录成功"},
                {"at": "2026-11-20T09:05:00", "ok": False, "detail": "已过期"},
            ],
        }
        text = PT._conclude(rec)
        self.assertIn("30", text)          # 签发后 30 分钟仍有效
        self.assertIn("首次失效", text)
        self.assertIn("分钟", text)

    def test_conclusion_before_any_failure(self):
        """只探到成功、还没观测到失效时，结论不能瞎报寿命。"""
        import probe_token_life as PT
        rec = {
            "issued_at": "2026-11-20T08:00:00",
            "samples": [{"at": "2026-11-20T08:10:00", "ok": True, "detail": "登录成功"}],
        }
        text = PT._conclude(rec)
        self.assertIn("尚未观测到失效", text)

    def test_expired_state_stops_further_probing(self):
        """确认失效后要停止探测，避免刷同一条报错、也避免被频控。
        导入新 HAR 时 mark_issued 会重置状态，恢复探测。"""
        import probe_token_life as PT
        self.assertFalse(PT._needs_probe({"state": "expired"}))
        self.assertTrue(PT._needs_probe({"state": "alive"}))
        self.assertTrue(PT._needs_probe({"state": "unknown"}))
        self.assertTrue(PT._needs_probe({}))

    def test_mark_issued_resets_state_and_samples(self):
        """导入新票据 = 新一轮探测：旧样本和 expired 状态都要清掉。"""
        import probe_token_life as PT
        with tempfile.TemporaryDirectory() as d:
            real_dir = PT.OUT_DIR
            real_accounts = PT._accounts
            PT.OUT_DIR = Path(d)
            PT._accounts = lambda: [Path(d) / "1234.json"]
            Path(d, "1234.json").write_text(json.dumps({
                "state": "expired",
                "expired_at": "2026-11-20T09:00:00",
                "samples": [{"at": "x", "ok": False}],
            }), encoding="utf-8")
            try:
                PT.mark_issued("2026-11-20T10:00:00")
                rec = json.loads(Path(d, "1234.json").read_text(encoding="utf-8"))
                self.assertEqual(rec["state"], "unknown")
                self.assertEqual(rec["samples"], [])
                self.assertEqual(rec["issued_at"], "2026-11-20T10:00:00")
                self.assertNotIn("expired_at", rec)
            finally:
                PT.OUT_DIR = real_dir
                PT._accounts = real_accounts

    def test_bat_files_use_crlf(self):
        """Windows 的 bat 必须是 CRLF，LF 会导致 cmd 解析报错。覆盖全部 bat。"""
        root = Path(__file__).resolve().parent.parent
        bats = sorted(root.glob("*.bat"))
        self.assertTrue(bats, "项目根目录下应有 bat 启动脚本")
        for p in bats:
            data = p.read_bytes()
            self.assertIn(b"\r\n", data, f"{p.name} 缺 CRLF")
            # 不应出现"只有 LF 没有 CR"的裸换行
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), f"{p.name} 存在裸 LF")

    def test_bat_comments_are_english(self):
        """cmd 在 GBK 控制台下会把 bat 里的中文 rem 注释当命令执行。"""
        root = Path(__file__).resolve().parent.parent
        for p in sorted(root.glob("*.bat")):
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if s.lower().startswith("rem"):
                    self.assertTrue(s.isascii(),
                                    f"{p.name} 的 rem 注释含非 ASCII 字符：{s}")


class TestIsolation(unittest.TestCase):
    def test_awards_file_is_isolated(self):
        """测试绝不能写用户真实台账。

        曾经 `cmcc_extra.run_lottery` 抽中会调 record_award，而 _guard 只隔离了
        全局设置、没隔离台账，每跑一次测试就往真实台账塞 3 条无账号的假记录。
        """
        import _guard  # noqa: F401  触发隔离
        import common
        real = Path(__file__).resolve().parent.parent / "gui_data" / "awards.json"
        self.assertNotEqual(Path(common.AWARDS_FILE).resolve(), real.resolve(),
                            "台账没被隔离，测试会污染真实数据")


class TestMailWatch(unittest.TestCase):
    def test_mark_seen_writes_state(self):
        """mark_seen 曾给 atomic_write_json 多传 default 参数而抛异常，
        导致导入成功后邮件没标记已读、下次重复导入。守住这个 bug。"""
        import tempfile
        import mail_watch as mw
        with tempfile.TemporaryDirectory() as td:
            real = mw.STATE
            mw.STATE = Path(td) / "mail_state.json"
            try:
                mw.mark_seen("12345")
                st = json.loads(mw.STATE.read_text(encoding="utf-8"))
                self.assertIn("12345", st.get("seen", []))
                mw.mark_seen("67890")
                st = json.loads(mw.STATE.read_text(encoding="utf-8"))
                self.assertEqual(set(st["seen"]), {"12345", "67890"})
            finally:
                mw.STATE = real

    def test_run_once_notifies_on_success(self):
        """用户明确要求：导入成功也要推通知（不只失败时）。守住这条链路。"""
        from email.message import EmailMessage
        import mail_watch as mw

        msg = EmailMessage()
        msg["Subject"] = "Har"
        msg.set_content("body")
        msg.add_attachment(b"[]", maintype="application", subtype="octet-stream",
                           filename="ok.har")

        fake = mock.Mock()
        fake.search.return_value = ("OK", [b"900"])
        fake.fetch.return_value = ("OK", [(b"900 (RFC822 {1}", msg.as_bytes())])

        cfg = {"enabled": True, "host": "imap.test", "user": "u@t",
               "password": "p", "sender": "", "subject_keyword": "HAR"}
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(mw, "INBOX_DIR", Path(td)), \
             mock.patch.object(mw, "conf", return_value=cfg), \
             mock.patch.object(mw, "should_check", return_value=True), \
             mock.patch.object(mw, "load_state", return_value={}), \
             mock.patch.object(mw, "connect", return_value=fake), \
             mock.patch.object(mw, "mark_seen"), \
             mock.patch.object(mw, "clear_need"), \
             mock.patch("har_import.apply", return_value=["✓ BASS 实测可用，已更新配置"]), \
             mock.patch.object(mw, "catchup_after_import", return_value="已自动补跑签到：成功"), \
             mock.patch.object(mw, "notify_once", return_value=True) as n:
            mw.run_once(cfg)
            n.assert_called_once()
            # notify_once(key, title, detail)，标题在第二个位置参数
            self.assertIn("HAR 自动导入完成", n.call_args[0][1])

    def test_run_once_catchup_only_on_verified_token(self):
        """只有报告里确实「实测成功」才补跑签到，避免拿废票白跑。"""
        import mail_watch as mw
        self.assertIn("未补跑", mw.catchup_after_import({}, ["BASS 无可用 token"]))
        self.assertIn("未补跑", mw.catchup_after_import({}, []))


if __name__ == "__main__":
    # 入口必须在文件末尾：放在中间会让后面的测试类在直接运行时根本不执行
    unittest.main(verbosity=2)
