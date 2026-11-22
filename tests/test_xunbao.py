"""环闽寻宝：到终点不等于本轮结束。

2026-11-22 实测：走到第 29/29 格后地图会**重置回第 1 格**（currentSeq=1、canRoll=true、
骰子余额保留）。旧实现在终点就 break，剩下的骰子当天作废。
这里用假 API 复现那条路径：投到终点 → 开宝箱 → 继续投下一圈。
"""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402
import cmcc_xunbao as X  # noqa: E402


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
    def run_with(self, script):
        args = types.SimpleNamespace(dry_run=False, tasks=False, invite=False,
                                     did_xxl=False, did_luck=False, max=20)
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


if __name__ == "__main__":
    unittest.main()
