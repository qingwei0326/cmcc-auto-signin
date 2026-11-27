"""环闽寻宝：到终点不等于本轮结束。

2026-11-22 实测：走到第 29/29 格后地图会**重置回第 1 格**（currentSeq=1、canRoll=true、
骰子余额保留）。旧实现在终点就 break，剩下的骰子当天作废。
这里用假 API 复现那条路径：投到终点 → 开宝箱 → 继续投下一圈。
"""

import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402
import cmcc_xunbao as X  # noqa: E402
import common
import run_all


class FakeApi:
    def __init__(self, script):
        self.script = script          # {path: [resp, resp, ...]}
        self.calls = []

    def call(self, path, data=None):
        self.calls.append(path)
        queue = self.script.get(path)
        if not queue:
            return {"ok": True, "code": "0000", "data": {}}
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def count(self, path):
        return self.calls.count(path)


def _init(seq, dice, can_roll, claim=False):
    return {"ok": True, "code": "0000",
            "data": {"currentSeq": seq, "diceBalance": dice,
                     "canRoll": can_roll, "treasureClaimRequired": claim}}


class TestEndOfMapResets(unittest.TestCase):
    def run_with(self, script, **overrides):
        args = types.SimpleNamespace(dry_run=False, tasks=False, invite=False,
                                     did_xxl=False, did_luck=False, max=20)
        vars(args).update(overrides)
        fake = FakeApi(script)
        with mock.patch.object(X, "Xunbao", lambda tok: fake), \
                mock.patch.object(X, "save_state", lambda *a, **k: None), \
                mock.patch.object(X, "record_award", lambda *a, **k: None), \
                mock.patch.object(X.time, "sleep", lambda *_: None):
            return fake, X.run_one("fj1", "13800001234", "tok", args)

    def test_rolls_again_after_treasure(self):
        """投到终点 → 自动开箱 → 地图重置后把剩下的骰子投掉。"""
        script = {
            "activity/status": [{"ok": True, "code": "0000",
                                 "data": {"activityPeriod": "P", "startTime": "", "endTime": ""}}],
            # 初始在第 28 格、2 个骰子；投完到终点（不可投、待开箱）；开箱后回到第 1 格
            "activity/init": [_init(28, 2, True), _init(29, 1, False, True),
                              _init(1, 1, True), _init(5, 0, True)],
            "dice/roll": [
                {"ok": True, "code": "0000",
                 "data": {"dicePoint": 5, "fromSeq": 28, "finalSeq": 29,
                          "reachedEnd": True, "moves": []}},
                {"ok": True, "code": "0000",
                 "data": {"dicePoint": 4, "fromSeq": 1, "finalSeq": 5, "moves": []}},
            ],
            "treasure/open": [{"ok": True, "code": "0000",
                               "data": {"prizeName": "10AI豆兑换券"}}],
            "task/list": [{"ok": True, "code": "0000", "data": {"tasks": []}}],
            "prize/list": [{"ok": True, "code": "0000", "data": {"records": [], "total": 0}}],
        }
        fake, res = self.run_with(script)
        self.assertEqual(res["rolls"], 2, "终点后剩下的骰子没继续投")
        self.assertEqual(fake.count("treasure/open"), 1, "宝箱没开或重复开")
        self.assertIn("10AI豆兑换券", res["wins"])
        self.assertEqual(res["seq"], 5)
        self.assertEqual(res["dice"], 0)

    def test_no_infinite_loop_when_server_does_not_reset(self):
        """开箱后服务端仍停在终点：只能开一次，不能死循环。"""
        script = {
            "activity/status": [{"ok": True, "code": "0000",
                                 "data": {"activityPeriod": "P", "startTime": "", "endTime": ""}}],
            "activity/init": [_init(29, 0, False, True), _init(29, 0, False, True)],
            "treasure/open": [{"ok": True, "code": "0000",
                               "data": {"prizeName": "1GB流量日包"}}],
            "task/list": [{"ok": True, "code": "0000", "data": {"tasks": []}}],
            "prize/list": [{"ok": True, "code": "0000", "data": {"records": [], "total": 0}}],
        }
        fake, res = self.run_with(script)
        self.assertEqual(fake.count("treasure/open"), 1)
        self.assertEqual(res["rolls"], 0)

    def test_stops_when_no_dice(self):
        """骰子为 0 时一次都不投。"""
        script = {
            "activity/status": [{"ok": True, "code": "0000",
                                 "data": {"activityPeriod": "P", "startTime": "", "endTime": ""}}],
            "activity/init": [_init(12, 0, True)],
            "task/list": [{"ok": True, "code": "0000", "data": {"tasks": []}}],
            "prize/list": [{"ok": True, "code": "0000", "data": {"records": [], "total": 0}}],
        }
        fake, res = self.run_with(script)
        self.assertEqual(fake.count("dice/roll"), 0)
        self.assertEqual(res["rolls"], 0)

    def test_unauthorized_response_aborts_at_each_stage(self):
        dead = {"ok": False, "code": 401, "data": {"currentSeq": 1, "diceBalance": 0}}
        todo = {"ok": True, "data": {"tasks": [{"taskCode": "T", "taskName": "任务"}]}}
        prize = {"ok": True, "data": {"records": [{"status": 0, "recordNo": "r"}]}}
        cases = [
            ({"assist/create": [dead]}, {"invite": True}, "assist/create"),
            *[({path: [dead]}, {}, path) for path in (
                "activity/status", "activity/init", "task/list", "prize/list")],
            ({"task/list": [todo], "task/report": [dead]}, {"tasks": True}, "task/report"),
            ({"task/list": [todo], "activity/init": [_init(1, 0, False), dead]},
             {"tasks": True}, "activity/init"),
            ({"prize/list": [prize, dead]}, {}, "prize/list"),
            ({"prize/list": [prize], "prize/claim": [dead]}, {}, "prize/claim"),
            ({"activity/init": [_init(28, 0, False, True)], "treasure/open": [dead]},
             {}, "treasure/open"),
            ({"activity/init": [_init(1, 1, True)], "dice/roll": [dead]}, {}, "dice/roll"),
            ({"activity/init": [_init(1, 1, True), dead]}, {}, "activity/init"),
        ]
        for script, overrides, path in cases:
            with self.subTest(script=script), redirect_stdout(io.StringIO()) as out:
                fake, res = self.run_with(script, **overrides)
                self.assertEqual(res, {"tag": "fj1", "skip": True})
                self.assertEqual(fake.calls[-1], path)
                self.assertIn("token 失效（401）", out.getvalue())
                self.assertTrue(common.is_token_dead(out.getvalue()))

    def test_failed_refresh_does_not_turn_into_zero_dice(self):
        failed = {"ok": False, "code": "-1", "msg": "请求超时"}
        with redirect_stdout(io.StringIO()) as out:
            _, res = self.run_with({"activity/init": [_init(1, 1, True), failed]})
        self.assertEqual(res, {"tag": "fj1", "skip": True})
        self.assertIn("activity/init 失败", out.getvalue())

    def test_list_failures_other_than_401_do_not_block_rolls(self):
        """任务 / 奖品列表偶发失败（非 401）不能挡住投骰子。"""
        failed = {"ok": False, "code": "-1", "msg": "请求超时"}
        script = {
            "activity/init": [_init(1, 1, True), _init(2, 0, False)],
            "task/list": [failed],
            "prize/list": [failed],
        }
        with redirect_stdout(io.StringIO()) as out:
            fake, res = self.run_with(script)
        self.assertNotEqual(res.get("skip"), True, out.getvalue())
        self.assertEqual(fake.count("dice/roll"), 1)
        self.assertIn("task/list 失败", out.getvalue())
        self.assertIn("prize/list 失败", out.getvalue())

    def test_main_returns_one_for_expired_task_list(self):
        api = FakeApi({"task/list": [{"ok": False, "code": "401"}]})
        with mock.patch.object(X, "collect_targets", return_value=[("fj1", "13800001234", "t")]), \
                mock.patch.object(X, "Xunbao", return_value=api), \
                mock.patch.object(sys, "argv", ["cmcc_xunbao.py", "--dry-run"]), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(X.main(), 1)
        self.assertIn("✗ fj1", out.getvalue())


class TestMutualAssist(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state.json"
        self.targets = [("a", "13800138000", "a"), ("b", "13800001234", "b")]
        self.apis = {
            tag: FakeApi({"assist/create": [{"ok": True, "data": {"inviteToken": "tk"}}]})
            for tag, _, _ in self.targets
        }

    def run_assist(self, day):
        with mock.patch.object(X, "collect_targets", return_value=self.targets), \
                mock.patch.object(X, "ASSIST_STATE", self.state), \
                mock.patch.object(X, "Xunbao", side_effect=self.apis.__getitem__), \
                mock.patch.object(X.time, "strftime", return_value=day), \
                mock.patch.object(X.time, "sleep"), redirect_stdout(io.StringIO()) as out:
            res = X.mutual_assist()
        return res, out.getvalue()

    def test_monthly_pair_is_shared_by_both_directions(self):
        self.assertEqual(self.run_assist("2026-11-27")[0]["success"], 1)
        self.assertEqual(common.load_json(self.state), {
            "a|b": "2026-11", "quota:a": "2026-11-27",
        })
        res, out = self.run_assist("2026-11-28")
        self.assertEqual(res["tried"], 0)
        self.assertIn("已互点，跳过", out)
        self.assertEqual(sum(api.count("assist/create") for api in self.apis.values()), 1)
        self.assertEqual(self.run_assist("2026-12-01")[0]["success"], 1)

    def test_legacy_day_only_state_still_applies(self):
        self.state.write_text(json.dumps({"a": "2026-11-27", "b": "2026-11-27"}))
        self.assertEqual(self.run_assist("2026-11-27")[0]["tried"], 0)
        self.assertEqual(self.run_assist("2026-11-28")[0]["success"], 1)

    def test_monthly_rejections_are_cached_without_using_daily_quota(self):
        self.targets.append(("c", "13900005678", "c"))
        self.apis["c"] = FakeApi({"assist/create": [{"ok": True, "data": {"inviteToken": "tk"}}]})
        for msg in ("当月不可为同一人助力。", "本月已接受过"):
            with self.subTest(msg=msg):
                self.state.write_text(json.dumps({"quota:b": "2026-11-27", "quota:c": "2026-11-27"}))
                self.apis["a"].script["assist/do"] = [
                    {"ok": False, "code": -100, "msg": msg},
                    {"ok": False, "code": -100, "msg": "您今日助力次数已用尽"},
                ]
                self.assertEqual(self.run_assist("2026-11-27")[0]["tried"], 2)
                state = common.load_json(self.state)
                self.assertEqual(state["a|b"], "2026-11")
                self.assertEqual(state["quota:a"], "2026-11-27")
                self.assertNotIn("a|c", state)
                self.assertEqual(self.run_assist("2026-11-27")[0]["tried"], 0)

    def test_assist_reports_expired_owner_and_helper(self):
        dead = {"ok": False, "code": "401"}
        for endpoint, token in (("assist/create", "b"), ("assist/do", "a")):
            with self.subTest(endpoint=endpoint), mock.patch.dict(
                    self.apis[token].script, {endpoint: [dead]}):
                res, out = self.run_assist("2026-11-27")
                self.assertEqual(res["expired"], [token])
                self.assertIn(f"{token} token 失效（401）", out)

    def test_assist_do_does_not_retry_terminal_rejection(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"code": -100, "msg": "当月不可为同一人助力。"}
        with mock.patch.object(X, "make_session") as make, mock.patch.object(X.time, "sleep"):
            make.return_value.post.return_value = response
            self.assertEqual(str(X.Xunbao("t").call("assist/do")["code"]), "-100")
            make.return_value.post.assert_called_once()

    def test_run_all_notes_in_process_expiry(self):
        directory = self.state.parent
        (directory / "a.json").write_text('{"phone":"13800138000"}', encoding="utf-8")
        with mock.patch.dict(run_all.PLATFORMS, {"bass": {"dir": directory}}), \
                mock.patch.object(X, "mutual_assist", return_value={"expired": ["a"]}), \
                mock.patch.object(run_all, "run_script", return_value=("...8000", True, 0, "")), \
                mock.patch.object(run_all, "TOKEN_DEAD_ACCOUNTS", []), redirect_stdout(io.StringIO()):
            run_all.run_xunbao(dry_run=False, mutual=True)
            self.assertEqual(run_all.TOKEN_DEAD_ACCOUNTS, ["...8000"])


if __name__ == "__main__":
    unittest.main()
