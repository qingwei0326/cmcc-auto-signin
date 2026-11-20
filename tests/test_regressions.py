"""回归测试：守住 2026-11-20 代码审查修掉的那批「静默失效」缺陷。

每个用例都对应一个真实 bug —— 把代码改回去就会红。
这些缺陷的共同点是「不报错、但功能没在干它声称的事」，所以只测返回值/命令/文件内容，
不去 grep 源码文本。

运行：
    python -m unittest discover -s tests -v
"""

import ast
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cmcc_sign
import common
import dashboard
import har_import
import probe_token_life as PT
import run_all

ROOT = Path(__file__).resolve().parent.parent

try:
    import cmcc_xiaoxiaole as XXL
    HAS_CRYPTO = True
except SystemExit:          # 模块在缺 pycryptodome 时会 sys.exit(1)
    HAS_CRYPTO = False


def _fake_config_dir(tmp, name):
    d = Path(tmp) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "1234.json").write_text(json.dumps({"phone": "13800001234"}), encoding="utf-8")
    return d


# ======================================================================
# run_all 的参数分发与超时
# ======================================================================

class TestRunAllRouting(unittest.TestCase):
    def _run(self, argv):
        cmds = []

        def fake(cmd, **kw):
            cmds.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            platforms = {k: dict(v) for k, v in run_all.PLATFORMS.items()}
            platforms["qwhd"]["dir"] = _fake_config_dir(tmp, "configs")
            platforms["bass"]["dir"] = _fake_config_dir(tmp, "configs_fj")
            with mock.patch.object(run_all, "PLATFORMS", platforms), \
                 mock.patch.object(run_all.subprocess, "run", fake), \
                 mock.patch.object(sys, "argv", ["run_all.py", *argv]), \
                 mock.patch.object(run_all, "notify"), \
                 redirect_stdout(io.StringIO()):
                run_all.main()
        return cmds

    def test_keepalive_passes_login_only(self):
        """保活必须只登录不签到：extra 曾只透传给 bass，qwhd 的 --login-only 被丢掉。"""
        cmds = self._run(["--keepalive"])
        self.assertTrue(cmds)
        for c in cmds:
            self.assertIn("cmcc_sign.py", c[1])
            self.assertIn("--login-only", c)
            self.assertNotIn("--dry-run", c)

    def test_daily_only_passes_flag_to_bass(self):
        cmds = self._run(["--daily-only"])
        self.assertTrue(cmds)
        for c in cmds:
            self.assertIn("cmcc_fj.py", c[1])
            self.assertIn("--daily-only", c)

    def test_extra_only_goes_to_bass(self):
        """--extra 是给 BASS 的，不能塞给 qwhd（argparse 会直接报错）。

        注意必须写成 --extra=--roll：值以 -- 开头时 argparse 不接受空格分隔形式。
        """
        cmds = self._run(["--extra=--roll"])
        qwhd = [c for c in cmds if "cmcc_sign.py" in c[1]]
        bass = [c for c in cmds if "cmcc_fj.py" in c[1]]
        self.assertTrue(qwhd and bass)
        self.assertTrue(all("--roll" in c for c in bass))
        self.assertTrue(all("--roll" not in c for c in qwhd))

    def test_seckill_timeout_covers_the_wait(self):
        """10:30 启动等到 12:00 约 90 分钟，超时上限必须覆盖，否则开抢前就被 kill。"""
        fixed = datetime(2026, 11, 20, 10, 30, 0)
        fake_dt = types.SimpleNamespace(now=lambda: fixed)
        with mock.patch.object(run_all.datetime, "datetime", fake_dt):
            self.assertGreater(run_all.seckill_timeout("11:59:50"), 5000)
        self.assertEqual(run_all.seckill_timeout(""), 6 * 3600)
        self.assertGreaterEqual(run_all.seckill_timeout("00:00:00"), 900)

    def test_run_script_reports_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "a.json"
            conf.write_text("{}", encoding="utf-8")
            fake = lambda cmd, **kw: types.SimpleNamespace(
                returncode=1, stdout="", stderr="[ERROR] token 过期")
            with mock.patch.object(run_all.subprocess, "run", fake):
                name, ok, sec, text = run_all.run_script(ROOT / "cmcc_sign.py", conf, [], 10)
            self.assertFalse(ok)
            self.assertIn("token 过期", text)

    def test_run_script_reports_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp) / "a.json"
            conf.write_text("{}", encoding="utf-8")

            def fake(cmd, **kw):
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))

            with mock.patch.object(run_all.subprocess, "run", fake):
                name, ok, sec, text = run_all.run_script(ROOT / "cmcc_sign.py", conf, [], 1)
            self.assertFalse(ok)
            self.assertIn("超时", text)


# ======================================================================
# 消消乐退出码
# ======================================================================

@unittest.skipUnless(HAS_CRYPTO, "需要 pycryptodome")
class TestXxlExitCode(unittest.TestCase):
    def _run(self, limit, ends, times=2):
        calls = {"n": 0}

        def fake_post(s, path, body=None, retries=1):
            if path == "getDailyPlayLimit":
                return limit
            if path == "playEnd":
                r = ends[min(calls["n"], len(ends) - 1)]
                calls["n"] += 1
                return r
            return {"code": "0000", "data": []}

        fake_time = types.SimpleNamespace(sleep=lambda *_: None,
                                          strftime=time.strftime, time=time.time)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "fj.json"
            cfg.write_text(json.dumps({"bearer_token": "x" * 36}), encoding="utf-8")
            argv = ["cmcc_xiaoxiaole.py", "--config", str(cfg),
                    "--times", str(times), "--gap", "0"]
            with mock.patch.object(XXL, "post", fake_post), \
                 mock.patch.object(XXL, "time", fake_time), \
                 mock.patch.object(sys, "argv", argv), \
                 redirect_stdout(io.StringIO()):
                return XXL.main()

    def test_success_is_zero(self):
        limit = {"code": "0000", "data": {"playToken": "t", "reachedLimit": False}}
        ends = [{"code": "0000", "data": {"prizeName": "500MB月包券"}}]
        self.assertEqual(self._run(limit, ends), 0)

    def test_daily_limit_reached_is_zero(self):
        """次数用尽是预期内收尾，不该天天触发失败推送。"""
        limit = {"code": "0000", "data": {"reachedLimit": True}}
        self.assertEqual(self._run(limit, [{"code": "0000"}]), 0)

    def test_stamina_exhausted_is_zero(self):
        limit = {"code": "0000", "data": {"playToken": "t"}}
        ends = [{"code": "-100", "msg": "您的体力不足，无法进行挑战"}]
        self.assertEqual(self._run(limit, ends), 0)

    def test_token_expired_is_one(self):
        self.assertEqual(self._run({"code": "401"}, [{"code": "0000"}]), 1)

    def test_gateway_block_is_one(self):
        limit = {"code": "0000", "data": {"playToken": "t"}}
        ends = [{"code": "-100", "msg": "当前查询人数过多，请稍后再试"}]
        self.assertEqual(self._run(limit, ends, times=2), 1)

    def test_missing_play_token_is_one(self):
        limit = {"code": "0000", "data": {"playToken": ""}}
        self.assertEqual(self._run(limit, [{"code": "0000"}]), 1)

    def test_exit_code_propagates_to_process(self):
        """main() 的返回值必须经 sys.exit 传出去，否则 run_all 永远看到 0。"""
        p = subprocess.run([sys.executable, str(ROOT / "cmcc_xiaoxiaole.py"),
                            "--config", str(ROOT / "no_such_config.json")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        self.assertEqual(p.returncode, 1)


# ======================================================================
# 会话寿命
# ======================================================================

class TestSessionExpiry(unittest.TestCase):
    def test_heartbeat_does_not_extend_expiry(self):
        """同一个 cookie 心跳回写不能刷新 expires_at —— 服务端是 2 小时硬过期。"""
        import requests
        cfg = types.SimpleNamespace(phone="13800001234")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            s = requests.Session()
            s.cookies.set("QWHD_SESSION_TOKEN", "AAA", domain="wx.10086.cn", path="/")
            with mock.patch.object(cmcc_sign, "activity_session_file", lambda c: path):
                cmcc_sign.save_activity_session(s, "ref", cfg)
                first = json.loads(path.read_text(encoding="utf-8"))
                time.sleep(1.1)
                cmcc_sign.save_activity_session(s, "ref", cfg)      # 心跳
                again = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(again["at"], first["at"])
                self.assertEqual(again["expires_at"], first["expires_at"])
                # 换了新 cookie（重新登录）才重新起算
                s.cookies.set("QWHD_SESSION_TOKEN", "BBB", domain="wx.10086.cn", path="/")
                cmcc_sign.save_activity_session(s, "ref", cfg)
                fresh = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotEqual(fresh["at"], first["at"])

    def test_dashboard_computes_left_min(self):
        """datetime.datetime 笔误会让剩余分钟永远是 null（异常被吞掉）。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            now = datetime.now()
            (d / "seckill_session_1234.json").write_text(json.dumps({
                "cookie": {"QWHD_SESSION_TOKEN": "x"}, "referer": "r",
                "at": now.isoformat(timespec="seconds"),
                "expires_at": (now + timedelta(minutes=90)).isoformat(timespec="seconds"),
            }), encoding="utf-8")
            with mock.patch.object(dashboard, "DATA", d):
                out = dashboard._list_seckill_sessions()
            self.assertEqual(len(out), 1)
            self.assertIsNotNone(out[0]["left_min"])
            self.assertTrue(88 <= out[0]["left_min"] <= 90)


# ======================================================================
# 票据寿命探测
# ======================================================================

class TestTokenLifeProbe(unittest.TestCase):
    def test_mark_issued_only_touches_matching_account(self):
        """给一个号导入 HAR 不能清空另一个号的样本、写上本号的签发时刻。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            a, b = d / "1234.json", d / "5678.json"
            for p, ph in ((a, "13800001234"), (b, "13900005678")):
                p.write_text(json.dumps({"phone": ph, "state": "expired",
                                         "samples": [{"at": "x", "ok": False}]}),
                             encoding="utf-8")
            with mock.patch.object(PT, "OUT_DIR", d), \
                 mock.patch.object(PT, "_accounts", lambda: [a, b]):
                PT.mark_issued("2026-11-20T10:00:00", phone="13800001234")
            ra = json.loads(a.read_text(encoding="utf-8"))
            rb = json.loads(b.read_text(encoding="utf-8"))
            self.assertEqual(ra["state"], "unknown")
            self.assertEqual(ra["samples"], [])
            self.assertEqual(ra["issued_at"], "2026-11-20T10:00:00")
            self.assertEqual(rb["state"], "expired")
            self.assertEqual(len(rb["samples"]), 1)
            self.assertNotIn("issued_at", rb)

    def test_network_error_is_not_expiry(self):
        with mock.patch.object(PT.CS, "exchange_session",
                               side_effect=RuntimeError("网络异常: timed out")):
            self.assertEqual(PT._probe(None)[0], "error")
        with mock.patch.object(PT.CS, "exchange_session",
                               side_effect=RuntimeError("登录页未返回 sid，SSO 入口可能已变更")):
            self.assertEqual(PT._probe(None)[0], "error")
        with mock.patch.object(PT.CS, "exchange_session",
                               side_effect=RuntimeError(
                                   "appTokenLogin 失败: FAILED APP登录状态已过期 —— 请重新抓包")):
            self.assertEqual(PT._probe(None)[0], "expired")
        with mock.patch.object(PT.CS, "exchange_session", return_value=None):
            self.assertEqual(PT._probe(None)[0], "alive")

    def test_error_sample_does_not_poison_conclusion(self):
        rec = {"issued_at": "2026-11-20T08:00:00", "samples": [
            {"at": "2026-11-20T08:30:00", "ok": True, "kind": "alive"},
            {"at": "2026-11-20T08:32:00", "ok": False, "kind": "error"},
            {"at": "2026-11-20T09:05:00", "ok": False, "kind": "expired"}]}
        text = PT._conclude(rec)
        self.assertIn("09:05:00", text)
        self.assertNotIn("08:32:00", text)


# ======================================================================
# HAR 导入
# ======================================================================

class TestHarImport(unittest.TestCase):
    def _har(self, token, ts):
        return json.dumps({"log": {"entries": [{
            "startedDateTime": ts,
            "request": {"url": "https://wx.10086.cn/qwhdsso/appTokenLogin.do",
                        "headers": [],
                        "postData": {"text": json.dumps({
                            "token": token,
                            "userCheckId": format(13800001234, "X")})}}}]}})

    def test_newest_har_wins(self):
        """目录里放两份 HAR 时，必须让最新那份最后落地（曾按倒序处理，旧的覆盖新的）。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            old, new = d / "old.har", d / "new.har"
            old.write_text(self._har("JSESSIONID=OLD; UID=1; ticketID=x",
                                     "2026-11-20T01:00:00Z"), encoding="utf-8")
            new.write_text(self._har("JSESSIONID=NEW; UID=1; ticketID=x",
                                     "2026-11-20T02:00:00Z"), encoding="utf-8")
            os.utime(old, (1000000, 1000000))
            os.utime(new, (2000000, 2000000))
            with mock.patch.object(har_import, "BASE", d), \
                 mock.patch.object(PT, "OUT_DIR", d / "life"), \
                 mock.patch.object(PT, "_accounts", lambda: []):
                lines, used = har_import.scan_dir(d)
            self.assertEqual(used, 2)
            cfg = json.loads((d / "configs" / "1234.json").read_text(encoding="utf-8"))
            self.assertTrue(cfg["app_token"].startswith("JSESSIONID=NEW"), cfg["app_token"])

    def test_mark_issued_receives_phone(self):
        seen = {}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(har_import, "BASE", Path(tmp)), \
                 mock.patch.object(PT, "mark_issued",
                                   lambda ts, phone=None: seen.update(ts=ts, phone=phone)):
                har_import.apply(self._har("JSESSIONID=A; UID=1; ticketID=x",
                                           "2026-11-20T04:09:25Z"))
        self.assertEqual(seen["phone"], "13800001234")
        self.assertEqual(seen["ts"], "2026-11-20T12:09:25")   # UTC → 本地 +8


# ======================================================================
# 跨进程状态写入
# ======================================================================

class TestCrossProcessState(unittest.TestCase):
    def test_update_json_does_not_lose_updates(self):
        """4 个进程同时改同一份 json，各自的 key 都要留下（原子写防不了丢更新）。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "state.json"
            code = (
                "import sys,time;sys.path.insert(0,r'%s');import common;"
                "common.update_json(r'%s', lambda st: (time.sleep(0.2), st.update({sys.argv[1]:1})))"
            ) % (ROOT, p)
            procs = [subprocess.Popen([sys.executable, "-c", code, f"k{i}"],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for i in range(4)]
            for pr in procs:
                out, err = pr.communicate(timeout=120)
                self.assertEqual(pr.returncode, 0, err.decode("utf-8", "replace"))
            st = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(sorted(st), ["k0", "k1", "k2", "k3"])


# ======================================================================
# 控制台安全边界
# ======================================================================

class TestDashboardGuards(unittest.TestCase):
    def test_safe_conf_path_rejects_escape(self):
        for bad in ("../config.json", "configs/../../x.json", "C:/Windows/win.ini",
                    "/etc/passwd", "configs/.hidden.json", "configs/sub/a.json",
                    "configs_fj/..\\evil.json", "", None):
            self.assertIsNone(dashboard.safe_conf_path(bad), bad)
        ok = dashboard.safe_conf_path("configs/1234.json")
        self.assertIsNotNone(ok)
        self.assertEqual(ok.parent.name, "configs")

    def test_sched_reg_rejects_injected_time(self):
        """time 会插进单引号 PowerShell 串，不校验就能闭合引号执行任意命令。"""
        r = dashboard.do_action({"action": "sched_reg", "kind": "sign",
                                 "time": "08:30'; Start-Process calc; '"})
        self.assertFalse(r["ok"])

    def test_bass_token_new_rejects_traversal(self):
        r = dashboard.do_action({"action": "bass_token_new", "name": "..\\..\\evil"})
        self.assertFalse(r["ok"])

    def test_run_actions_reject_bad_config_path(self):
        for act in ("seckill_run", "xxl_run"):
            r = dashboard.do_action({"action": act, "file": "../config.json"})
            self.assertFalse(r["ok"], act)

    def test_host_of(self):
        self.assertEqual(dashboard._host_of("127.0.0.1:8765"), "127.0.0.1")
        self.assertEqual(dashboard._host_of("http://localhost:8765"), "localhost")
        self.assertEqual(dashboard._host_of("https://evil.example"), "evil.example")
        self.assertEqual(dashboard._host_of("[::1]:8765"), "[::1]")

    def test_local_only(self):
        def h(headers):
            obj = dashboard.Handler.__new__(dashboard.Handler)
            obj.headers = headers
            return obj
        self.assertTrue(h({"Host": "127.0.0.1:8765"})._local_only())
        self.assertTrue(h({"Host": "localhost:8765",
                           "Origin": "http://localhost:8765"})._local_only())
        self.assertFalse(h({"Host": "evil.example:8765"})._local_only())
        self.assertFalse(h({"Host": "127.0.0.1:8765",
                            "Origin": "https://evil.example"})._local_only())

    def test_http_guards_end_to_end(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def status(req):
            try:
                with urlopen(req, timeout=10) as r:
                    return r.status
            except HTTPError as e:
                return e.code

        try:
            # 会明文吐 app_token 的死端点必须已经不存在
            self.assertEqual(status(base + "/api/bass_cfg?file=configs/1234.json"), 404)
            # 恶意网页的跨站 POST → 403
            self.assertEqual(status(Request(
                base + "/api/action", data=b'{"action":"stop"}',
                headers={"Content-Type": "application/json",
                         "Origin": "https://evil.example"})), 403)
            # text/plain 属「简单请求」不触发预检 → 必须被 415 挡住
            self.assertEqual(status(Request(
                base + "/api/action", data=b'{"action":"stop"}',
                headers={"Content-Type": "text/plain"})), 415)
            # 正常本机请求仍能拿到页面
            self.assertEqual(status(base + "/"), 200)
        finally:
            srv.shutdown()
            srv.server_close()
            th.join(timeout=10)


# ======================================================================
# 结构化结果（取代正则解析日志）
# ======================================================================

class TestStructuredResults(unittest.TestCase):
    def test_emit_parse_roundtrip(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            common.emit_result({"plat": "qwhd", "sign_days": 12})
        self.assertEqual(common.parse_result(buf.getvalue()),
                         {"plat": "qwhd", "sign_days": 12})

    def test_parse_result_takes_last(self):
        self.assertEqual(common.parse_result('@@RESULT {"a":1}\n噪音\n@@RESULT {"a":2}\n'),
                         {"a": 2})
        self.assertIsNone(common.parse_result("没有结果行"))
        self.assertIsNone(common.parse_result("@@RESULT {坏 json"))

    def test_qwhd_parser_prefers_json_over_log_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            sf = Path(tmp) / "stats.json"
            text = ("当前累计签到 999 天，今日已签\n"
                    '@@RESULT {"plat":"qwhd","token":"ok","sign_days":12,'
                    '"today_signed":true,"flow_mb":100,"last_reward":"100MB"}')
            with mock.patch.object(dashboard, "STATS_FILE", sf):
                dashboard.parse_qwhd_output("k", text)
            st = json.loads(sf.read_text(encoding="utf-8"))["k"]
            self.assertEqual(st["sign_days"], 12)        # 不是日志里的 999
            self.assertEqual(st["flow_mb_total"], 100.0)
            self.assertEqual(st["token_qwhd"], "ok")

    def test_bass_parser_prefers_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            sf = Path(tmp) / "stats.json"
            text = ('@@RESULT {"plat":"bass","token":"ok","fodder":750,'
                    '"stage":"第2阶 250/5000","fb_exchange":"1.5GB",'
                    '"today_signed":true,"flow_mb":500,"tasks":3,"feeds":2,'
                    '"last_reward":"500MB月包券"}')
            with mock.patch.object(dashboard, "STATS_FILE", sf):
                dashboard.parse_bass_output("k", text)
            st = json.loads(sf.read_text(encoding="utf-8"))["k"]
            self.assertEqual(st["fodder"], 750)
            self.assertEqual(st["fb_exchange"], "1.5GB")
            self.assertEqual(st["tasks_done_total"], 3)
            self.assertEqual(st["feeds_total"], 2)
            self.assertEqual(st["flow_mb_total"], 500.0)

    def test_bass_idle_does_not_claim_token_ok(self):
        """本地短路那轮根本没发请求，不能据此说 token 有效。"""
        with tempfile.TemporaryDirectory() as tmp:
            sf = Path(tmp) / "stats.json"
            text = '@@RESULT {"plat":"bass","token":"ok","idle":true,"today_signed":true}'
            with mock.patch.object(dashboard, "STATS_FILE", sf):
                dashboard.parse_bass_output("k", text)
            st = json.loads(sf.read_text(encoding="utf-8"))["k"]
            self.assertNotIn("token_bass", st)

    def test_legacy_fallback_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            sf = Path(tmp) / "stats.json"
            with mock.patch.object(dashboard, "STATS_FILE", sf):
                dashboard.parse_qwhd_output("k", "当前累计签到 7 天，今日已签\n活动会话建立成功")
            st = json.loads(sf.read_text(encoding="utf-8"))["k"]
            self.assertEqual(st["sign_days"], "7")
            self.assertEqual(st["token_qwhd"], "ok")

    def test_api_post_refreshes_timestamp_on_retry(self):
        """1006 按时间戳判定，带旧时间戳重试只会继续被拒。"""
        bodies = []

        class FakeSession:
            def post(self, url, json=None, timeout=None):
                bodies.append(dict(json))
                code = "1006" if len(bodies) == 1 else "0"
                return types.SimpleNamespace(
                    status_code=200, json=lambda: {"code": code, "msg": "", "data": {}})

        with mock.patch.object(common.time, "sleep", lambda *_: None):
            res = common.api_post(FakeSession(), "https://x",
                                  body={"appId": 0, "timestamp": 1}, retries=1)
        self.assertTrue(res["ok"])
        self.assertEqual(len(bodies), 2)
        self.assertNotEqual(bodies[0]["timestamp"], bodies[1]["timestamp"])


# ======================================================================
# 端到端：真实 main() → @@RESULT → 控制台解析 → 统计落库
# ======================================================================

class TestEndToEndResult(unittest.TestCase):
    def test_bass_main_emits_result_dashboard_can_use(self):
        import cmcc_fj
        data = {
            "index_info": {"fodderSurplus": 750, "feedCountToday": 3},
            "sign_info": {"todaySign": True, "openSignTime": True, "cycleToday": 5,
                          "signBeginHour": 6,
                          "signDateList": [{"today": True, "fodder": 1200},
                                           {"fodder": 1500}]},
            "daily_list": [{"taskName": "阶段一", "taskStatus": "RECEIVED",
                            "currentTask": False, "fodder": 150,
                            "limitInfo": {"intervalMinutes": 60}}],
            "lucky_bag": {"currentFeedCount": 100, "nextTriggerCount": 300,
                          "needMoreFeeds": 200, "isNearTrigger": False},
            "stage_info": {"stageInfo": {"stageName": "第2阶",
                                         "feedNeedFodderCount": 5000},
                           "feedFodders": 250},
            "fb_index": {"exchangeFlow": 1.5, "exchangeFlowUnit": "GB",
                         "exchangeFlowValue": 1536, "hasTaskToDo": False},
        }

        def fake_call(s, cfg, key, retries=1, extra=None):
            return {"ok": True, "code": "0000", "msg": "", "data": data[key]}

        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            cfg = d / "fj1.json"
            cfg.write_text(json.dumps({"bearer_token": "x" * 36, "app_id": "0",
                                       "phone": "13800001234"}), encoding="utf-8")
            sf = d / "stats.json"
            with mock.patch.object(cmcc_fj, "call", fake_call), \
                 mock.patch.object(cmcc_fj, "new_session", lambda c: object()), \
                 mock.patch.object(cmcc_fj, "pause", lambda *a, **k: None), \
                 mock.patch.object(cmcc_fj, "DAILY_STATE", d / "daily.json"), \
                 mock.patch.object(cmcc_fj, "FB_STATE", d / "fb.json"), \
                 mock.patch.object(dashboard, "STATS_FILE", sf), \
                 mock.patch.object(sys, "argv", ["cmcc_fj.py", "--config", str(cfg), "--dry-run"]), \
                 redirect_stdout(buf):
                rc = cmcc_fj.main()
                self.assertEqual(rc, 0)
                text = buf.getvalue()
                r = common.parse_result(text)
                self.assertIsNotNone(r, "main() 必须输出 @@RESULT 行")
                self.assertEqual(r["plat"], "bass")
                self.assertEqual(r["fodder"], 750)
                # 控制台拿同一份输出解析，统计要真的落库。
                # 必须在 mock 作用域内调用，否则会写到真实的 gui_data/stats.json
                dashboard.parse_bass_output("fj1", text)
                st = json.loads(sf.read_text(encoding="utf-8"))["fj1"]
            self.assertEqual(st["fodder"], 750)
            self.assertEqual(st["fb_exchange"], "1.5GB")
            self.assertEqual(st["today_signed_bass"], dashboard.TODAY())
            self.assertIn("第2阶", st["stage"])
            self.assertEqual(st["token_bass"], "ok")


# ======================================================================
# 前端拆分与打包
# ======================================================================

class TestFrontendSplit(unittest.TestCase):
    def test_html_file_matches_loaded_constant(self):
        f = ROOT / "static" / "index.html"
        self.assertTrue(f.is_file())
        self.assertEqual(dashboard.INDEX_HTML, f.read_text(encoding="utf-8"))
        for marker in ("runSeckill", "seckill_run", "cmcc_seckill.py"):
            self.assertIn(marker, dashboard.INDEX_HTML)

    def test_missing_frontend_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(dashboard, "BASE", Path(tmp)), \
                 mock.patch.object(sys, "frozen", False, create=True):
                with self.assertRaises(SystemExit):
                    dashboard._load_frontend()

    def test_frozen_frontend_resolves_from_meipass(self):
        """exe 运行时页面在 PyInstaller 解包目录里，不在 exe 同级目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            (bundle / "static").mkdir(parents=True)
            (bundle / "static" / "index.html").write_text("<html>frozen</html>", encoding="utf-8")
            with mock.patch.object(dashboard, "BASE", Path(tmp) / "exe_dir"), \
                 mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "_MEIPASS", str(bundle), create=True):
                self.assertEqual(dashboard._load_frontend(), "<html>frozen</html>")

    def test_build_config_bundles_static(self):
        spec = (ROOT / "签到控制台.spec").read_text(encoding="utf-8")
        bat = (ROOT / "build_exe.bat").read_text(encoding="utf-8")
        self.assertIn("('static', 'static')", spec)
        # bat 必须从 spec 构建：用命令行参数会让 PyInstaller 重新生成 spec、覆盖手工配置
        self.assertIn("签到控制台.spec", bat)


# ======================================================================
# fixture 隐私（本仓库是公开的）
# ======================================================================

class TestFixturePrivacy(unittest.TestCase):
    """测试 fixture 只能用合成号码，别把真实手机号写进公开仓库。"""

    SYNTHETIC = {"13800001234", "13900005678", "13800138000"}

    def test_no_real_mobile_numbers_in_fixtures(self):
        for p in sorted((ROOT / "tests").glob("*.py")):
            for n in re.findall(r"\b1[3-9]\d{9}\b", p.read_text(encoding="utf-8")):
                # 失败信息里也要脱敏，别把号码打进 CI 日志
                self.assertIn(n, self.SYNTHETIC,
                              f"{p.name} 出现非合成手机号 {n[:3]}****{n[-4:]}，"
                              f"请换成 TestFixturePrivacy.SYNTHETIC 里的号码")


# ======================================================================
# Python 3.9 兼容（README 承诺的最低版本）
# ======================================================================

class TestPython39Compat(unittest.TestCase):
    def test_pep604_annotations_have_future_import(self):
        for p in sorted(ROOT.glob("*.py")):
            tree = ast.parse(p.read_text(encoding="utf-8"))
            has_future = any(
                isinstance(n, ast.ImportFrom) and n.module == "__future__"
                and any(a.name == "annotations" for a in n.names)
                for n in tree.body)
            uses_union = False
            for n in ast.walk(tree):
                anns = []
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = n.args
                    anns = [x.annotation for x in
                            a.posonlyargs + a.args + a.kwonlyargs if x.annotation]
                    if n.returns:
                        anns.append(n.returns)
                elif isinstance(n, ast.AnnAssign) and n.annotation:
                    anns = [n.annotation]
                for ann in anns:
                    if any(isinstance(s, ast.BinOp) and isinstance(s.op, ast.BitOr)
                           for s in ast.walk(ann)):
                        uses_union = True
            if uses_union:
                self.assertTrue(
                    has_future,
                    f"{p.name} 用了 X|Y 注解但没有 from __future__ import annotations，"
                    f"Python 3.9 导入即 TypeError")

    def test_no_syntax_newer_than_39(self):
        for p in sorted(ROOT.glob("*.py")):
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p), feature_version=(3, 9))


class TestFrozenPathGuards(unittest.TestCase):
    """onefile exe 解包到临时目录，配置不能写到那里。

    真实故障：界面显示「HAR 导入完成」，但 configs/*.json 一个字节没变——
    因为冻结后模块的 __file__ 指向 _MEIxxxx 临时目录，写进去的配置随进程退出消失。
    """

    def test_app_base_uses_exe_dir_when_frozen(self):
        """冻结时 app_base() 必须指向 exe 所在目录，而不是 __file__ 的临时目录。"""
        import common
        import sys
        with tempfile.TemporaryDirectory() as d:
            fake_exe = Path(d) / "签到控制台.exe"
            fake_exe.write_text("x", encoding="utf-8")
            old_frozen = getattr(sys, "frozen", None)
            old_exe = sys.executable
            try:
                sys.frozen = True
                sys.executable = str(fake_exe)
                self.assertEqual(common.app_base().resolve(), Path(d).resolve())
            finally:
                if old_frozen is None:
                    del sys.frozen
                else:
                    sys.frozen = old_frozen
                sys.executable = old_exe

    def test_har_import_base_is_app_base(self):
        """har_import 与 probe_token_life 会被 exe 动态导入，BASE 必须用 app_base()。"""
        import har_import
        import probe_token_life
        import common
        self.assertEqual(har_import.BASE.resolve(), common.app_base().resolve())
        self.assertEqual(probe_token_life.BASE.resolve(), common.app_base().resolve())

    def test_no_module_writes_to_frozen_tempdir(self):
        """凡是会被 exe 动态导入的模块，都不能用 Path(__file__).parent 当数据目录。"""
        import ast
        # 这些模块跑在 exe 里（dashboard 动态 import 它们）
        for name in ("har_import.py", "probe_token_life.py", "common.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in tree.body:
                # 只看模块顶层的 BASE = ... 赋值
                if isinstance(node, ast.Assign):
                    targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                    if "BASE" in targets:
                        seg = ast.get_source_segment(src, node.value) or ""
                        self.assertNotIn(
                            "__file__",
                            seg,
                            f"{name} 的 BASE 不能用 __file__（冻结后指向临时目录），"
                            "应改用 common.app_base()")

    def test_write_json_verifies_readback(self):
        """写完必须复读校验，避免'报告成功但文件没落盘'。"""
        import har_import
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            har_import._write_json(p, {"a": 1})
            self.assertTrue(p.is_file())
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 1})

    def test_max_body_allows_large_har(self):
        """抓包 HAR 常在 20~70MB，64MB 上限会把正常文件直接拒掉（413）。"""
        import dashboard
        self.assertGreaterEqual(
            dashboard.MAX_BODY, 128 * 1024 * 1024,
            "MAX_BODY 太小，大 HAR 上传会返回 413")

    def test_find_har_files_covers_wechat(self):
        """微信接收目录必须被纳入搜索范围（抓包文件常从微信直接拖过来）。"""
        import common
        src = (ROOT / "common.py").read_text(encoding="utf-8")
        self.assertIn("msg/file", src)
        self.assertIn("xwechat_files", src)

    def test_frontend_shows_import_error(self):
        """导入失败原因要留在页面上，不能只靠一闪而过的 toast。"""
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("harOut", html)
        # afterHar 里必须有 err 分支
        i = html.find("function afterHar")
        j = html.find("function importHar")
        seg = html[i:j]
        self.assertIn("err", seg)
    def test_import_writes_into_project_dir_not_temp(self):
        """导入必须写进项目目录 —— 这正是「显示导入了但配置没变」的根因。

        真实故障复现步骤：
          1. exe 冻结，模块 __file__ 指向 _MEIxxxx 临时目录
          2. 导入写进临时目录
          3. exe 关闭，临时目录被清理，configs/*.json 从未变过
        这里直接断言：apply() 之后，项目目录下的 configs 真的变了。
        """
        import har_import
        with tempfile.TemporaryDirectory() as d:
            fake_cfg_dir = Path(d) / "configs"
            fake_cfg_dir.mkdir()
            target = fake_cfg_dir / "1234.json"  # 合成号 13800001234 的尾号
            target.write_text(json.dumps({
                "app_token": "old", "phone": "13800001234",
            }), encoding="utf-8")

            real_base = har_import.BASE
            real_apply = har_import.apply
            try:
                har_import.BASE = Path(d)
                har = {"log": {"entries": [{
                    "startedDateTime": "2026-11-20T11:40:00.000000Z",
                    "request": {
                        "url": "https://wx.10086.cn/qwhdsso/appTokenLogin?sid=X",
                        "method": "POST",
                        "headers": [{"name": "Cookie", "value": "x"}],
                        "postData": {"text": json.dumps({
                            "token": "JSESSIONID=NEWTOKEN; UID=u; ticketID=ShanDong; Secure",
                            "provinceCode": "591", "cityCode": "0596",
                            "carrierOperator": "002", "appVersionCode": "12.5.7",
                            "userCheckId": "3368b4ed2"})},
                    }}]}}
                har_import.apply(json.dumps(har))
                after = json.loads(target.read_text(encoding="utf-8"))
                self.assertIn("NEWTOKEN", after["app_token"],
                              "导入没有写进 configs 目录 —— 又退回了临时目录的老问题")
            finally:
                har_import.BASE = real_base

    def test_verify_reports_login_failure(self):
        """导入后必须实测登录一次，假票不能报「导入成功」。"""
        import har_import
        import inspect
        src = inspect.getsource(har_import)
        self.assertIn("exchange_session", src)
        self.assertIn("实测登录", src)


class TestContinuousStage(unittest.TestCase):
    """2026-11-20 12:36 抓包发现的 continuousStage 升阶接口。"""

    def test_endpoint_registered(self):
        """continuousStage 端点必须注册，否则升阶分支拿不到 URL。"""
        import cmcc_fj
        self.assertIn("continuous_stage", cmcc_fj.EP)
        self.assertTrue(cmcc_fj.EP["continuous_stage"].endswith("/stage/continuousStage"))

    def test_flag_exists_and_is_exclusive(self):
        """--continue-stage 与 --auto-claim 互斥，两个都传必须报错退出。"""
        import cmcc_fj
        src = (ROOT / "cmcc_fj.py").read_text(encoding="utf-8")
        self.assertIn('--continue-stage', src)
        self.assertIn("参数冲突", src)

    def test_133_branch_has_continue_path(self):
        """code=133 时若指定 --continue-stage，要调用升阶而不是领奖。"""
        import inspect
        import cmcc_fj
        src = inspect.getsource(cmcc_fj)
        i = src.find('str(r.get("code")) == "133"')
        self.assertGreater(i, 0, "133 分支不能丢")
        seg = src[i:i+700]
        self.assertIn("continue_stage", seg, "133 分支里要有升阶路径")

    def test_docs_record_prize_table(self):
        """6 阶奖品表要归档，避免下次重新逆向。"""
        doc = (ROOT / "docs" / "bass.md").read_text(encoding="utf-8")
        self.assertIn("continuousStage", doc)
        self.assertIn("10GB月包", doc)
        self.assertIn("code=133", doc)


class FakeHttpResp:
    """模拟 requests.Response：只需要 json() 与 raise_for_status()。"""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class TestWedPuzzle(unittest.TestCase):
    """周三充值日拼图任务、抽奖与领券。"""

    def test_puzzle_complete_needs_every_piece(self):
        from cmcc_wed import puzzle_complete
        self.assertFalse(puzzle_complete({"a": "1", "b": "0"}))
        self.assertTrue(puzzle_complete({"a": "1", "b": "2"}))
        self.assertFalse(puzzle_complete({}))

    def test_quota_left_uses_monthly_quota(self):
        from cmcc_wed import quota_left
        self.assertEqual(quota_left({"requireTimes": 4, "completedTimes": 2}), 2)
        self.assertEqual(quota_left({"requireTimes": 4, "completedTimes": 9}), 0)
        self.assertIsNone(quota_left({}))

    def test_task_lines_marks_manual_tasks(self):
        from cmcc_wed import task_lines
        tasks = [
            {"taskId": "czhf", "title": "充值满10元", "status": 0, "addDrawTimes": 1},
            {"taskId": "ll_x", "title": "浏览页面", "status": 0, "addDrawTimes": 1},
            {"taskId": "done1", "title": "已完成任务", "status": 2, "addDrawTimes": 1},
        ]
        lines = task_lines(tasks)
        self.assertTrue(any("充值满10元" in l and "·" in l for l in lines),
                        "真实充值任务必须标成不代做")
        self.assertTrue(any("浏览页面" in l and "→" in l for l in lines))
        self.assertTrue(any("已完成任务" in l and "✓" in l for l in lines))

    def test_finish_tasks_skips_manual_tasks(self):
        import cmcc_wed as W
        calls = []

        class FS:
            def post(self, url, json=None, headers=None, timeout=None):
                calls.append((url, json))
                return FakeHttpResp({"code": "0", "data": {"addDrawTimes": 1}})

        tasks = [
            {"taskId": "czhf", "title": "充值满10元", "status": 0},
            {"taskId": "ll_x", "title": "浏览页面", "status": 0},
        ]
        lines = []
        with mock.patch.object(W.time, "sleep"):
            W.finish_tasks(FS(), "ref", tasks, lines)
        self.assertEqual(len(calls), 1, "只应上报可代做任务")
        self.assertTrue(calls[0][0].endswith("/finishTask"))
        self.assertEqual(calls[0][1], {"taskId": "ll_x"})

    def test_draw_all_uses_current_puzzle_and_stops_when_empty(self):
        import cmcc_wed as W
        calls = []
        state = {"drawTimes": 1, "activeIndex": 2, "pictureList": {"2": {"a": "0"}}}

        class FS:
            def post(self, url, json=None, headers=None, timeout=None):
                calls.append((url, json))
                if url.endswith("queryPictureActInfo"):
                    return FakeHttpResp({"code": "0", "data": state})
                if url.endswith("drawPicture"):
                    state["drawTimes"] = 0
                    return FakeHttpResp({"code": "0", "data": {"prize": {"prizeId": "p1"}}})
                return FakeHttpResp({"code": "0", "data": {}})

        lines = []
        with mock.patch.object(W.time, "sleep"):
            W.draw_all(FS(), "ref", lines)
        draws = [c for c in calls if c[0].endswith("/drawPicture")]
        self.assertEqual(len(draws), 1)
        self.assertEqual(draws[0][1], {"round": "dp2"},
                         "必须抽当前 activeIndex 对应的档位")

    def test_draw_all_keeps_chances_when_puzzle_complete(self):
        import cmcc_wed as W
        calls = []

        class FS:
            def post(self, url, json=None, headers=None, timeout=None):
                calls.append(url)
                return FakeHttpResp({"code": "0", "data": {
                    "drawTimes": 5, "activeIndex": 1,
                    "pictureList": {"1": {"a": "1", "b": "1"}}}})

        lines = []
        with mock.patch.object(W.time, "sleep"):
            W.draw_all(FS(), "ref", lines)
        self.assertFalse([c for c in calls if c.endswith("/drawPicture")],
                         "拼图已集齐时不该再消耗次数")
        self.assertTrue(any("已集齐" in l for l in lines))

    def test_receive_ready_uses_round_mapping(self):
        import cmcc_wed as W
        calls = []

        class FS:
            def post(self, url, json=None, headers=None, timeout=None):
                calls.append((url, json))
                return FakeHttpResp({"code": "0", "data": {
                    "actPrizes": [{"prizeName": "95折话费券"}]}})

        lines = []
        with mock.patch.object(W.time, "sleep"):
            W.receive_ready(FS(), "ref",
                            {"pictureList": {"1": {"a": "1"}}}, lines)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith("/receiveNew"))
        self.assertEqual(calls[0][1], {"round": "r6"})
        self.assertTrue(any("领取成功" in l for l in lines))

    def test_dry_run_never_writes(self):
        import cmcc_wed as W
        calls = []

        class FS:
            def post(self, url, json=None, headers=None, timeout=None):
                calls.append(url)
                if url.endswith("queryPictureActInfo"):
                    return FakeHttpResp({"code": "0", "data": {
                        "drawTimes": 2, "activeIndex": 1,
                        "pictureList": {"1": {"a": "1"}}}})
                if url.endswith("taskList"):
                    return FakeHttpResp({"code": "0", "data": [
                        {"taskId": "ll_x", "title": "浏览", "status": 0}]})
                return FakeHttpResp({"code": "0", "data": {}})

        cfg = cmcc_sign.Config.__new__(cmcc_sign.Config)
        cfg.phone = "13800001234"
        cfg.app_token = "x"
        with mock.patch.object(W, "exchange_session", return_value=(FS(), "ref")):
            lines = W.run(cfg, dry_run=True)
        writes = [c for c in calls
                  if c.endswith("/finishTask") or c.endswith("/drawPicture")
                  or c.endswith("/receiveNew")]
        self.assertEqual(writes, [], "dry-run 不能做任何写操作")
        self.assertTrue(any("dry-run" in l for l in lines))

    def test_run_all_supports_wed_only(self):
        """周三充值日要能从批量运行器单独触发。"""
        calls = []

        def fake_run_script(script, conf, args, timeout):
            calls.append((script.name, list(args)))
            return conf.stem, True, 1.0, "ok"

        with mock.patch.object(run_all, "run_script", side_effect=fake_run_script), \
                mock.patch.object(sys, "argv", ["run_all.py", "--wed-only", "--dry-run"]):
            run_all.main()
        self.assertTrue(calls, "wed-only 应至少跑一个账号")
        for name, args in calls:
            self.assertEqual(name, "cmcc_wed.py")
            self.assertIn("--dry-run", args)


class TestSeckillSessionRetry(unittest.TestCase):
    """秒杀：启动时没会话不再直接退出，而是重试到开抢前。"""

    def test_parse_fire_at_uses_todays_clock(self):
        import cmcc_seckill as SK
        ts = SK.parse_fire_at("11:59:50", datetime(2026, 11, 20, 0, 0, 0))
        dt = datetime.fromtimestamp(ts)
        self.assertEqual((dt.hour, dt.minute, dt.second), (11, 59, 50))

    def test_session_deadline_covers_grace_after_fire(self):
        import cmcc_seckill as SK
        base = SK.parse_fire_at("11:59:50", datetime(2026, 11, 20, 0, 0, 0))
        self.assertAlmostEqual(SK.session_deadline_for("11:59:50", now=base - 100),
                               base + SK.SESSION_GRACE_AFTER_FIRE, delta=1)
        self.assertAlmostEqual(SK.session_deadline_for("", now=1000.0),
                               1000.0 + SK.DEFAULT_SESSION_WAIT, delta=1)

    def test_retry_keeps_going_until_session_appears(self):
        import cmcc_seckill as SK
        clock = {"t": 1000.0}
        calls = {"n": 0}

        def fake_exchange(cfg):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("appTokenLogin 失败: FAILED —— 票据已过期")
            return ("S", "R")

        cfg = cmcc_sign.Config.__new__(cmcc_sign.Config)
        cfg.phone = "13800001234"
        cfg.app_token = "x"
        cfg.config_path = ""
        with mock.patch.object(SK, "load_session", return_value=None), \
                mock.patch.object(SK, "save_session", lambda *a, **k: None), \
                mock.patch.object(SK, "exchange_session", side_effect=fake_exchange):
            s, ref, latest = SK.acquire_session_with_retry(
                cfg, deadline=1100.0, retry_interval=15.0,
                now_fn=lambda: clock["t"],
                sleep_fn=lambda d: clock.__setitem__("t", clock["t"] + d))
        self.assertEqual(calls["n"], 2, "第一次失败后必须继续重试")
        self.assertEqual((s, ref), ("S", "R"))

    def test_retry_gives_up_after_deadline(self):
        import cmcc_seckill as SK
        clock = {"t": 1000.0}
        calls = {"n": 0}

        def always_fail(cfg):
            calls["n"] += 1
            raise RuntimeError("票据已过期")

        cfg = cmcc_sign.Config.__new__(cmcc_sign.Config)
        cfg.phone = "13800001234"
        cfg.app_token = "x"
        cfg.config_path = ""
        with mock.patch.object(SK, "load_session", return_value=None), \
                mock.patch.object(SK, "exchange_session", side_effect=always_fail):
            with self.assertRaises(RuntimeError) as ctx:
                SK.acquire_session_with_retry(
                    cfg, deadline=1060.0, retry_interval=15.0,
                    now_fn=lambda: clock["t"],
                    sleep_fn=lambda d: clock.__setitem__("t", clock["t"] + d))
        self.assertGreaterEqual(calls["n"], 2, "截止前应尝试多次")
        self.assertIn("票据已过期", str(ctx.exception))

    def test_waiting_process_picks_up_newly_imported_har(self):
        """等待期间导入的新 HAR 必须被下一轮重试读到（内存旧票据不算数）。"""
        import cmcc_seckill as SK
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.json"
            path.write_text(json.dumps(
                {"app_token": "old-token", "phone": "13800001234"}), encoding="utf-8")
            env = dict(os.environ)
            env.pop("CMCC_APP_TOKEN", None)
            env.pop("CMCC_PHONE", None)
            with mock.patch.dict(os.environ, env, clear=True):
                cfg = cmcc_sign.Config(str(path))
                self.assertEqual(cfg.app_token, "old-token")
                path.write_text(json.dumps(
                    {"app_token": "new-token", "phone": "13800001234"}), encoding="utf-8")
                fresh = SK.reload_config(cfg)
                self.assertEqual(fresh.app_token, "new-token",
                                 "重试时必须重读配置文件才能用上新票据")


class TestExtendedActivitiesWiring(unittest.TestCase):
    """拓展活动三个（周六游戏中心 / 追剧 / 玩游戏抽话费）此前写好却没人调用。"""

    def test_games_only_passes_games_to_qwhd(self):
        """--games-only 必须把 --games 透传给 cmcc_sign.py，否则等于没开。"""
        cmds = []
        fake = lambda cmd, **kw: (cmds.append(cmd),
                                   types.SimpleNamespace(returncode=0, stdout="ok", stderr=""))[1]
        with tempfile.TemporaryDirectory() as tmp:
            platforms = {k: dict(v) for k, v in run_all.PLATFORMS.items()}
            platforms["qwhd"]["dir"] = _fake_config_dir(tmp, "configs")
            platforms["bass"]["dir"] = _fake_config_dir(tmp, "configs_fj")
            with mock.patch.object(run_all, "PLATFORMS", platforms), \
                 mock.patch.object(run_all.subprocess, "run", fake), \
                 mock.patch.object(sys, "argv", ["run_all.py", "--games-only"]), \
                 mock.patch.object(run_all, "notify"), \
                 redirect_stdout(io.StringIO()):
                run_all.main()
        self.assertTrue(cmds)
        for c in cmds:
            self.assertIn("cmcc_sign.py", c[1])
            self.assertIn("--games", c)

    def test_games_only_skips_bass(self):
        """拓展活动只在 qwhdhub 侧，不能顺带把 BASS 也跑了。"""
        cmds = []
        fake = lambda cmd, **kw: (cmds.append(cmd),
                                   types.SimpleNamespace(returncode=0, stdout="ok", stderr=""))[1]
        with tempfile.TemporaryDirectory() as tmp:
            platforms = {k: dict(v) for k, v in run_all.PLATFORMS.items()}
            platforms["qwhd"]["dir"] = _fake_config_dir(tmp, "configs")
            platforms["bass"]["dir"] = _fake_config_dir(tmp, "configs_fj")
            with mock.patch.object(run_all, "PLATFORMS", platforms), \
                 mock.patch.object(run_all.subprocess, "run", fake), \
                 mock.patch.object(sys, "argv", ["run_all.py", "--games-only"]), \
                 mock.patch.object(run_all, "notify"), \
                 redirect_stdout(io.StringIO()):
                run_all.main()
        self.assertFalse([c for c in cmds if "cmcc_fj.py" in c[1]],
                         "--games-only 不应触发 BASS")

    def test_lottery_respects_window(self):
        """不在窗口期只报余额，不消耗抽奖次数。"""
        import cmcc_extra as EX
        calls = []

        def fake_api_call(s, referer, method, path, body=None):
            calls.append(path)
            if path.startswith("/qwhdhub/diyLottery/period/remain/"):
                return {"code": "SUCCESS", "data": {"remainGameVal": 100, "remain": 5}}
            return {"code": "SUCCESS", "data": [{"prizeName": "x"}]}

        cfg = {"component": "c", "wallet_key": "remainGameVal", "cost": 10,
               "count_key": "remain", "weekday": 6, "start_hour": 12,
               "label": "游戏币"}
        with mock.patch.object(EX, "api_call", side_effect=fake_api_call), \
                mock.patch.object(EX.time, "sleep"):
            # 周三 10:00 → 未到窗口
            with mock.patch.object(EX, "datetime") as fake_dt:
                fake_dt.now.return_value = datetime(2026, 11, 18, 10, 0, 0)
                out = EX.run_lottery(None, "ref", cfg, dry_run=False)
        self.assertIn("未到窗口", out)
        self.assertFalse([c for c in calls if "lotterySafely" in c],
                         "窗口外不得消耗抽奖次数")

    def test_lottery_draws_inside_window(self):
        """窗口期内余额足够就要真的抽。"""
        import cmcc_extra as EX
        calls = []

        def fake_api_call(s, referer, method, path, body=None):
            calls.append(path)
            if path.startswith("/qwhdhub/diyLottery/period/remain/"):
                return {"code": "SUCCESS", "data": {"remainGameVal": 30, "remain": 5}}
            return {"code": "SUCCESS", "data": [{"prizeName": "话费券"}]}

        cfg = {"component": "c", "wallet_key": "remainGameVal", "cost": 10,
               "count_key": "remain", "weekday": 6, "start_hour": 12,
               "label": "游戏币"}
        with mock.patch.object(EX, "api_call", side_effect=fake_api_call), \
                mock.patch.object(EX.time, "sleep"), \
                mock.patch.object(EX, "datetime") as fake_dt:
            fake_dt.now.return_value = datetime(2026, 11, 21, 13, 0, 0)  # 周六 13:00
            out = EX.run_lottery(None, "ref", cfg, dry_run=False)
        self.assertIn("已抽", out)
        self.assertEqual(len([c for c in calls if "lotterySafely" in c]), 3)

    def test_dashboard_has_games_page_and_action(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="p-games"', html)
        self.assertIn("games_run", html)
        self.assertIn('id="gmFile"', html)
        self.assertIn("MobileGames", html)
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('act == "games_run"', src)
        self.assertIn("MobileGames", src)

    def test_games_bat_is_crlf_and_ascii_comments(self):
        p = ROOT / "run_games.bat"
        self.assertTrue(p.exists(), "缺少 run_games.bat")
        data = p.read_bytes()
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.lower().startswith("rem"):
                self.assertTrue(s.isascii(), f"rem 注释含非 ASCII：{s}")


class TestHiddenTaskLauncher(unittest.TestCase):
    """计划任务不该再闪黑窗口：走 pythonw + run_hidden.py。"""

    def test_launcher_uses_create_no_window(self):
        """启动器必须带 CREATE_NO_WINDOW，否则子进程还是会开控制台。"""
        import run_hidden
        self.assertEqual(run_hidden.CREATE_NO_WINDOW, 0x08000000)
        seen = {}

        class FakeProc:
            returncode = 0

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            seen["flags"] = kw.get("creationflags")
            return FakeProc()

        with mock.patch.object(run_hidden.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(sys, "argv", ["run_hidden.py", r"D:////x////run.bat"]):
            rc = run_hidden.main()
        self.assertEqual(rc, 0)
        self.assertEqual(seen["cmd"][:2], ["cmd.exe", "/c"])
        self.assertEqual(seen["flags"], run_hidden.CREATE_NO_WINDOW)

    def test_dashboard_sched_uses_hidden_launcher(self):
        """控制台注册任务时必须走 pythonw + run_hidden.py，而不是直接跑 bat。"""
        captured = []

        class FakeRunner:
            def start(self, label, cmd, **kw):
                captured.append(cmd[-1])
                return {"ok": True}

            def kill(self):
                return {"ok": True}

        real = dashboard.RUNNER
        dashboard.RUNNER = FakeRunner()
        try:
            dashboard.do_action({"action": "sched_reg", "kind": "seckill", "time": "11:55"})
        finally:
            dashboard.RUNNER = real
        self.assertTrue(captured)
        ps = captured[-1]
        self.assertIn("pythonw.exe", ps)
        self.assertIn("run_hidden.py", ps)
        self.assertNotIn("cmd.exe", ps)

    def test_sched_kind_games_is_allowed(self):
        """games 分支加过但曾漏进白名单，界面注册会直接报「未知任务类型」。"""
        captured = []

        class FakeRunner:
            def start(self, label, cmd, **kw):
                captured.append(cmd[-1])
                return {"ok": True}

            def kill(self):
                return {"ok": True}

        real = dashboard.RUNNER
        dashboard.RUNNER = FakeRunner()
        try:
            r = dashboard.do_action({"action": "sched_reg", "kind": "games", "time": "12:05"})
        finally:
            dashboard.RUNNER = real
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(captured)

    def test_repeating_triggers_do_not_use_unwritable_property(self):
        """PS 5.1 里 $tr.Repetition 不可写，用它等于没设重复（会退化成每天一次）。"""
        captured = []

        class FakeRunner:
            def start(self, label, cmd, **kw):
                captured.append(cmd[-1])
                return {"ok": True}

            def kill(self):
                return {"ok": True}

        real = dashboard.RUNNER
        dashboard.RUNNER = FakeRunner()
        try:
            dashboard.do_action({"action": "sched_reg", "kind": "probe", "time": "00:00"})
        finally:
            dashboard.RUNNER = real
        ps = captured[-1]
        self.assertIn("RepetitionInterval", ps)
        self.assertNotIn("$tr.Repetition=", ps)


class TestFlowbankCycleGate(unittest.TestCase):
    """流量银行周任务只在周初、月任务只在月初才尝试。"""

    def test_weekly_task_due_after_monday(self):
        import cmcc_fj as FJ
        # 本周五完成 → 本周内都不该再试
        fri = datetime(2026, 11, 20, 0, 55)
        self.assertFalse(FJ.fb_due(2, "2026-11-20 00:55", datetime(2026, 11, 20, 19, 0)))
        self.assertFalse(FJ.fb_due(2, "2026-11-20 00:55", datetime(2026, 11, 22, 23, 0)))
        # 下周一 → 新周期，可做
        self.assertTrue(FJ.fb_due(2, "2026-11-20 00:55", datetime(2026, 11, 23, 0, 5)))
        # 周五做、下周一还没到就重复跑，不应误判
        self.assertFalse(FJ.fb_due(2, "2026-11-23 09:00", datetime(2026, 11, 27, 9, 0)))

    def test_monthly_task_due_on_first_day(self):
        import cmcc_fj as FJ
        self.assertFalse(FJ.fb_due(1, "2026-11-20 00:55", datetime(2026, 11, 30, 23, 0)))
        self.assertTrue(FJ.fb_due(1, "2026-11-20 00:55", datetime(2026, 12, 1, 0, 5)))

    def test_unknown_or_missing_record_is_due(self):
        import cmcc_fj as FJ
        self.assertTrue(FJ.fb_due(1, None))                 # 没记录：保守地试
        self.assertTrue(FJ.fb_due(2, "坏格式"))
        self.assertTrue(FJ.fb_due(0, "2026-11-20 00:55"))   # 不限次

    def test_due_tasks_skips_off_cycle_but_finishes_in_progress(self):
        import cmcc_fj as FJ
        recs = {"1": {"last_done": "2026-11-20 08:00"}}
        tasks = [
            {"id": 1, "taskCycle": 2, "completeStatus": "0"},   # 本周已做 → 跳过
            {"id": 2, "taskCycle": 2, "completeStatus": "2"},   # 进行中 → 必须收尾
            {"id": 3, "taskCycle": 1, "completeStatus": "1"},   # 已完成 → 不碰
            {"id": 4, "taskCycle": 1, "completeStatus": "0"},   # 无记录 → 可做
        ]
        todo, skipped = FJ.fb_due_tasks(tasks, recs, now=datetime(2026, 11, 20, 19, 0))
        self.assertEqual([t["id"] for t in todo], [2, 4])
        self.assertEqual([t["id"] for t in skipped], [1])

    def test_force_ignores_cycle(self):
        import cmcc_fj as FJ
        recs = {"1": {"last_done": "2026-11-20 08:00"}}
        tasks = [{"id": 1, "taskCycle": 2, "completeStatus": "0"}]
        todo, skipped = FJ.fb_due_tasks(tasks, recs, now=datetime(2026, 11, 20, 19, 0), force=True)
        self.assertEqual(len(todo), 1)
        self.assertEqual(skipped, [])

    def test_cli_has_fb_all(self):
        src = (ROOT / "cmcc_fj.py").read_text(encoding="utf-8")
        self.assertIn('"--fb-all"', src)
        self.assertIn("fb_due_tasks(", src)


# ======================================================================
# 可用性审查（2026-11-20）：修掉「人用不了」的那一批
# ======================================================================

class TestUsabilityFixes(unittest.TestCase):
    """每条对应审查里发现的一个真实阻断/困惑点。"""

    def setUp(self):
        with dashboard._SCHED_LOCK:
            dashboard._SCHED_CACHE.update(at=0.0, data=[], ok=None, th=None)

    # ---- 入口与环境 ----

    def test_dashboard_has_runnable_entry(self):
        """原先模块只有定义没有入口：python dashboard.py 秒退，run_gui.bat 是死路径。"""
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', src)
        self.assertTrue(callable(dashboard.main))
        self.assertTrue(callable(dashboard.make_server))

    def test_main_serves_and_opens_browser(self):
        started = {}

        class FakeSrv:
            server_address = ("127.0.0.1", 8765)

            def serve_forever(self):
                started["served"] = True

        with mock.patch.object(dashboard, "make_server", lambda: (FakeSrv(), "")), \
                mock.patch.object(dashboard.webbrowser, "open", lambda u: started.update(url=u)), \
                mock.patch.object(dashboard, "preflight", lambda force=False: (True, "")), \
                redirect_stdout(io.StringIO()):
            rc = dashboard.main()
        self.assertEqual(rc, 0)
        self.assertTrue(started.get("served"))
        self.assertIn("8765", started.get("url", ""))

    def test_run_gui_bat_checks_python_and_runs_entry(self):
        data = (ROOT / "run_gui.bat").read_bytes()
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        text = data.decode("utf-8")
        self.assertIn("where python", text)
        self.assertIn("dashboard.py", text)

    def test_preflight_reports_missing_python(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(dashboard, "_PREFLIGHT", {"at": 0.0, "ok": True, "msg": ""}), \
                mock.patch("shutil.which", lambda n: None):
            ok, msg = dashboard.preflight(force=True)
        self.assertFalse(ok)
        self.assertIn("Python", msg)
        self.assertIn("PATH", msg)

    def test_preflight_reports_missing_requests(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(dashboard, "_PREFLIGHT", {"at": 0.0, "ok": True, "msg": ""}), \
                mock.patch("shutil.which", lambda n: r"C:\py\python.exe"), \
                mock.patch.object(dashboard.subprocess, "run", lambda *a, **k: mock.Mock(returncode=1)):
            ok, msg = dashboard.preflight(force=True)
        self.assertFalse(ok)
        self.assertIn("requests", msg)
        self.assertIn("pip install", msg)

    def test_preflight_skipped_when_running_from_source(self):
        """源码运行时解释器与 requests 必然可用，不该白起子进程。"""
        with mock.patch.object(dashboard.subprocess, "run", side_effect=AssertionError("不该调用")):
            self.assertEqual(dashboard.preflight(force=True), (True, ""))

    def test_frozen_pythonw_never_points_at_exe(self):
        """冻结态拿 exe 当解释器，会让计划任务每 2 分钟弹一次控制台窗口而 bat 根本没跑。"""
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"D:\app\签到控制台.exe"), \
                mock.patch("shutil.which", lambda n: r"C:\py\python.exe" if n == "python" else None):
            pyw = dashboard._pythonw()
        self.assertNotIn("签到控制台.exe", pyw)
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch("shutil.which", lambda n: None):
            self.assertEqual(dashboard._pythonw(), "")
        with mock.patch.object(dashboard, "_pythonw", lambda: ""):
            r = dashboard.do_action({"action": "sched_reg", "kind": "sign", "time": "08:30"})
        self.assertFalse(r["ok"])
        self.assertIn("pythonw", r["err"])

    # ---- 忙 / 停止 / 错误文案 ----

    def test_busy_action_names_the_running_task(self):
        class BusyRunner:
            running = True
            label = "一键签到（全部平台）"

            def start(self, *a, **k):
                return False

        real = dashboard.RUNNER
        dashboard.RUNNER = BusyRunner()
        try:
            r = dashboard.do_action({"action": "run_all", "platform": "all"})
        finally:
            dashboard.RUNNER = real
        self.assertFalse(r["ok"])
        self.assertIn("一键签到", r["err"])
        self.assertIn("停止", r["err"])

    def test_stop_when_idle_says_so(self):
        r = dashboard.do_action({"action": "stop"})
        self.assertTrue(r["ok"])
        self.assertIn("没有正在运行", r.get("msg", ""))

    def test_kill_terminates_whole_tree_on_windows(self):
        """只杀直接子进程时，run_all 起的孙子进程照样把请求发完。"""
        r = dashboard.TaskRunner()
        r.proc = mock.Mock(pid=999)
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return mock.Mock(returncode=0)

        with mock.patch.object(dashboard, "IS_WIN", True), \
                mock.patch.object(dashboard.subprocess, "run", fake_run):
            self.assertTrue(r.kill())
        self.assertEqual(seen["cmd"][:3], ["taskkill", "/PID", "999"])
        self.assertIn("/T", seen["cmd"])
        self.assertTrue(r._cancel.is_set())

    def test_cancelled_sequence_skips_remaining_steps(self):
        """「刷新状态」是顺序多步：杀掉当前一步后不能接着跑下一个账号。"""
        r = dashboard.TaskRunner()
        r._cancel.set()
        seen = []
        with mock.patch.object(r, "_run_one", lambda cmd, shell: seen.append(cmd) or 0), \
                mock.patch.object(dashboard, "append_run", lambda *a, **k: None):
            r._run_steps([(["a"], None), (["b"], None)], shell=False)
        self.assertEqual(seen, [])
        self.assertFalse(r.running)
        self.assertTrue(any("已停止" in l["line"] for l in r.logs))

    def test_refresh_status_without_accounts_explains_next_step(self):
        with mock.patch.object(dashboard, "scan_accounts", lambda: []):
            r = dashboard.do_action({"action": "refresh_status"})
        self.assertFalse(r["ok"])
        self.assertIn("新建账号", r["err"])

    # ---- 首屏不被 PowerShell 卡住 ----

    def test_cold_sched_read_does_not_block_first_paint(self):
        calls = []

        def slow_run(*a, **k):
            calls.append(1)
            time.sleep(0.3)
            return mock.Mock(stdout=b"[]")

        with mock.patch.object(dashboard.subprocess, "run", slow_run):
            t0 = time.time()
            out = dashboard._sched_state(ttl=90)
            elapsed = time.time() - t0
            dashboard._sched_wait()
        self.assertLess(elapsed, 0.2, "冷启动读取不能挡首屏")
        self.assertEqual(out, [])
        self.assertEqual(len(calls), 1)

    def test_sched_read_failure_is_distinguishable_from_none_registered(self):
        with mock.patch.object(dashboard.subprocess, "run", side_effect=OSError("blocked")):
            dashboard._sched_state(ttl=0)
        self.assertIs(dashboard._SCHED_CACHE["ok"], False)

    # ---- 计划任务日志 / 账号管理 ----

    def test_tasklog_whitelist_and_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            (logs / "daily.log").write_text("\n".join("行%d" % i for i in range(50)), encoding="utf-8")
            with mock.patch.object(dashboard, "BASE", Path(tmp)):
                self.assertIsNone(dashboard.read_task_log("../config.json"))
                self.assertIsNone(dashboard.read_task_log("not-a-task.log"))
                self.assertTrue(dashboard.read_task_log("keepalive.log")["missing"])
                r = dashboard.read_task_log("daily.log", tail=5)
        self.assertEqual(len(r["lines"]), 5)
        self.assertEqual(r["lines"][-1], "行49")

    def test_tasklog_and_favicon_routes(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]

        def status(path):
            try:
                with urlopen(base + path, timeout=10) as r:
                    return r.status
            except HTTPError as e:
                return e.code

        try:
            self.assertEqual(status("/api/tasklog?name=../config.json"), 404)
            self.assertIn(status("/favicon.ico"), (200, 204))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_account_new_and_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(dashboard, "BASE", Path(tmp)):
                r = dashboard.do_action({"action": "account_new", "plat": "BASS", "phone": "13800001234"})
                self.assertTrue(r["ok"], r)
                f = Path(tmp) / "configs_fj" / "1234.json"
                self.assertTrue(f.is_file())
                self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["phone"], "13800001234")
                self.assertFalse(dashboard.do_action(
                    {"action": "account_new", "plat": "BASS", "phone": "123"})["ok"])
                self.assertFalse(dashboard.do_action(
                    {"action": "account_new", "plat": "BASS", "name": "..\\evil"})["ok"])
                self.assertFalse(dashboard.do_action(
                    {"action": "account_save", "file": "../config.json", "phone": "13800001234"})["ok"])
                r2 = dashboard.do_action({"action": "account_save", "file": "configs_fj/1234.json",
                                          "bark_url": "https://api.day.app/x",
                                          "serverchan_sendkey": "ABC123"})
                self.assertTrue(r2["ok"], r2)
                cfg = json.loads(f.read_text(encoding="utf-8"))
                self.assertEqual(cfg["bark_url"], "https://api.day.app/x")
                self.assertFalse(dashboard.do_action(
                    {"action": "account_save", "file": "configs_fj/1234.json",
                     "serverchan_sendkey": "a;b"})["ok"])
                self.assertFalse(dashboard.do_action(
                    {"action": "account_save", "file": "configs_fj/1234.json",
                     "bark_url": "javascript:alert(1)"})["ok"])

    def test_scan_accounts_reports_notify_without_leaking_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "configs_fj"
            d.mkdir()
            (d / "1234.json").write_text(json.dumps({
                "phone": "13800001234", "bearer_token": "x",
                "bark_url": "https://api.day.app/secret", "serverchan_sendkey": "SECRET"}),
                encoding="utf-8")
            with mock.patch.object(dashboard, "BASE", Path(tmp)):
                a = dashboard.scan_accounts()[0]
        self.assertTrue(a["has_bark"])
        self.assertTrue(a["has_sendkey"])
        self.assertNotIn("secret", json.dumps(a))
        self.assertNotIn("SECRET", json.dumps(a))

    def test_har_import_creates_missing_bass_config(self):
        """全新安装：号码已知但 configs_fj 为空时必须自动建配置，而不是让人手工复制模板。"""
        har = {"log": {"entries": [
            {"startedDateTime": "2026-11-20T04:00:00Z",
             "request": {"url": "https://wx.10086.cn/qwhdsso/appTokenLogin", "headers": [],
                         "postData": {"text": json.dumps({
                             "token": "JSESSIONID=A; UID=1; ticketID=x",
                             "userCheckId": format(13800001234, "X")})}}},
            {"startedDateTime": "2026-11-20T04:00:01Z",
             "request": {"url": "https://wap.fj.10086.cn/bass-cultivateGameServer/sign/getSignInfo",
                         "headers": [{"name": "Authorization",
                                      "value": "Bearer 12345678-1234-1234-1234-123456789abc"}]}}]}}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(har_import, "BASE", Path(tmp)), \
                    mock.patch.object(har_import, "_verify", lambda rep: []), \
                    mock.patch.object(PT, "mark_issued", lambda *a, **k: None):
                rep = har_import.apply(json.dumps(har))
            f = Path(tmp) / "configs_fj" / "1234.json"
            self.assertTrue(f.is_file(), rep)
            cfg = json.loads(f.read_text(encoding="utf-8"))
            self.assertEqual(cfg["phone"], "13800001234")
            self.assertTrue(cfg["bearer_token"].startswith("12345678"))
            self.assertTrue(any("已新建" in l for l in rep), rep)

    def test_har_import_unknown_owner_gives_actionable_hint(self):
        har = {"log": {"entries": [
            {"startedDateTime": "2026-11-20T04:00:01Z",
             "request": {"url": "https://wap.fj.10086.cn/bass-cultivateGameServer/sign/getSignInfo",
                         "headers": [{"name": "Authorization",
                                      "value": "Bearer 12345678-1234-1234-1234-123456789abc"}]}}]}}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(har_import, "BASE", Path(tmp)), \
                    mock.patch.object(har_import, "_verify", lambda rep: []):
                rep = har_import.apply(json.dumps(har))
        line = next(l for l in rep if l.startswith("BASS 拿到 token"))
        self.assertIn("新建账号", line)
        self.assertNotIn("请在页面上选择账号后再导入", line)


# ======================================================================
# 喂到饲料用完：不用每次手填轮数
# ======================================================================

class TestFeedAll(unittest.TestCase):
    def _call(self, fodder, calls):
        def fake_call(s, cfg, ep, **kw):
            calls.append(ep)
            if ep == "index_info":
                return {"ok": True, "data": {"fodderSurplus": fodder}}
            if ep == "stage_info":
                return {"ok": True, "data": {"feedFodders": 0, "fodderSurplus": fodder,
                                             "stageInfo": {"feedNeedFodderCount": 5000,
                                                           "stageName": "一阶"}}}
            return {"ok": True, "code": "0", "data": {}}
        return fake_call

    def _run_feed(self, fodder):
        import cmcc_fj as FJ
        calls = []
        args = types.SimpleNamespace(feed=0, feed_all=True,
                                     auto_claim=True, continue_stage=False)
        res = {"feeds": 0}
        with mock.patch.object(FJ, "call", self._call(fodder, calls)), \
                mock.patch.object(FJ, "pause", lambda *a, **k: None), \
                redirect_stdout(io.StringIO()):
            n = FJ.run_feed(None, {}, args, res, "fee", "k", lambda *a, **k: None)
        return n, calls

    def test_rounds_computed_from_fodder(self):
        n, calls = self._run_feed(1000)
        self.assertEqual(n, 4, "1000 饲料应喂 4 轮（每轮 250）")
        self.assertEqual(calls.count("batch_feed"), 4)

    def test_leftover_below_250_not_fed(self):
        n, calls = self._run_feed(1249)
        self.assertEqual(n, 4, "余 249 不够一轮，不该再喂")
        self.assertEqual(calls.count("batch_feed"), 4)

    def test_no_fodder_means_no_feed(self):
        n, calls = self._run_feed(200)
        self.assertEqual(n, 0)
        self.assertNotIn("batch_feed", calls)

    def test_fodder_refreshed_after_feed(self):
        """喂完的余额要复核：stage_info 不带 fodderSurplus，留旧值会误导控制台。"""
        import cmcc_fj as FJ
        state = {"fodder": 500}

        def fake_call(s, cfg, ep, **kw):
            if ep == "index_info":
                return {"ok": True, "data": {"fodderSurplus": state["fodder"]}}
            if ep == "stage_info":
                return {"ok": True, "data": {"feedFodders": 0,
                                             "stageInfo": {"feedNeedFodderCount": 5000,
                                                           "stageName": "一阶"}}}
            if ep == "batch_feed":
                state["fodder"] -= 250
                return {"ok": True, "code": "0000", "data": {}}
            return {"ok": True, "code": "0", "data": {}}

        args = types.SimpleNamespace(feed=0, feed_all=True,
                                     auto_claim=True, continue_stage=False)
        res = {"feeds": 0, "fodder": 500}
        with mock.patch.object(FJ, "call", fake_call), \
                mock.patch.object(FJ, "pause", lambda *a, **k: None), \
                redirect_stdout(io.StringIO()):
            FJ.run_feed(None, {}, args, res, "fee", "k", lambda *a, **k: None)
        self.assertEqual(res["feeds"], 2)
        self.assertEqual(res["fodder"], 0, "喂完余额必须复核，不能留喂前的旧值")

    def test_feed_all_conflicts_with_feed(self):
        import cmcc_fj as FJ
        with mock.patch.object(sys, "argv", ["cmcc_fj.py", "--feed-all", "--feed", "3"]), \
                mock.patch.object(FJ, "load_config", lambda p: {}), \
                redirect_stdout(io.StringIO()):
            rc = FJ.main()
        self.assertEqual(rc, 1)

    def test_policy_save_accepts_feed_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = common.POLICY_FILE
            common.POLICY_FILE = Path(tmp) / "p.json"
            try:
                r = dashboard.do_action({"action": "policy_save",
                                         "bass": {"feed": 0, "feed_all": True}})
                self.assertTrue(r["ok"], r)
                self.assertTrue(r["policy"]["bass"]["feed_all"])
                self.assertEqual(common.load_policy()["bass"]["feed_all"], True)
            finally:
                common.POLICY_FILE = real

    def test_feed_all_defaults_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = common.POLICY_FILE
            common.POLICY_FILE = Path(tmp) / "p.json"
            try:
                self.assertIs(common.load_policy()["bass"]["feed_all"], False)
            finally:
                common.POLICY_FILE = real

    def test_bass_run_passes_feed_all(self):
        cmds = []

        class FakeRunner:
            running = False
            label = ""

            def start(self, label, cmd, parser=None, shell=False):
                cmds.append(cmd)
                return True

        a = next(x for x in dashboard.scan_accounts() if x["plat"] == "BASS")
        real = dashboard.RUNNER
        dashboard.RUNNER = FakeRunner()
        try:
            dashboard.do_action({"action": "bass_run", "key": a["key"], "feed_all": 1})
        finally:
            dashboard.RUNNER = real
        self.assertIn("--feed-all", cmds[-1])
        self.assertNotIn("--feed", cmds[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
