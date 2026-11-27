"""大转盘的「加抽奖次数」任务。

2026-11-22 实测：`index.taskList` 里那 4 个任务（请好友帮忙 / 逛移动爱购商城 /
中国移动APP中参与 / 浏览调研页面）逐个 `doTask` 全回 `0000`，剩余次数 0 → 2。
但 `run_all.run_luck` 一直写死传 `--no-tasks`，等于每天白扔两次抽奖机会。
好友助力由好友在小程序完成，自己上报不增加次数。
"""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402
import cmcc_luck as L  # noqa: E402


def _no_sleep():
    """do_tasks 里每个任务之间会歇 1~2 秒，测试里不必真等。"""
    return mock.patch.object(L.time, "sleep", lambda *_: None)


class PhonePickingTest(unittest.TestCase):
    """台账里的号码口径要统一成完整号。

    2026-11-25 实测：`luck/index` 回的 `userPhone` 是 `138****1234`（脱敏），
    configs_fj 里是完整号。早先优先取接口值，导致同一账号在
    awards.json 里出现脱敏号与完整号两条，按号统计会拆开。
    """

    def test_prefers_full_config_phone_over_masked_api(self):
        self.assertEqual(L.pick_phone("13800001234", "138****1234"), "13800001234")

    def test_uses_api_when_config_has_no_phone(self):
        self.assertEqual(L.pick_phone("", "13800001234"), "13800001234")

    def test_all_masked_yields_blank(self):
        self.assertEqual(L.pick_phone("138****1234", "139****5678"), "")

    def test_ignores_blank_candidates(self):
        self.assertEqual(L.pick_phone(None, "  ", "13800001234"), "13800001234")


class DoTasksTest(unittest.TestCase):
    def test_reports_every_unfinished_non_assist_task_by_default(self):
        tasks = [
            {"taskName": "请好友帮忙", "taskType": 3, "id": "a", "complete": False},
            {"taskName": "逛移动爱购商城", "taskType": 5, "id": "b", "complete": False,
             "taskUrl": "https://shop/1"},
            {"taskName": "浏览调研页面", "taskType": 5, "id": "c", "complete": False},
        ]
        calls, visited = [], []

        def get(path):
            calls.append(path)
            return {"code": "0000"}

        with _no_sleep():
            rep = L.do_tasks(get, visited.append, tasks)

        self.assertEqual(rep["done"], ["逛移动爱购商城", "浏览调研页面"])
        self.assertEqual(rep["skip"], [("请好友帮忙", "好友助力，已跳过")])
        self.assertFalse(rep["expired"])
        self.assertEqual(calls, ["doTask/5/b", "doTask/5/c"])
        self.assertEqual(visited, ["https://shop/1"])

    def test_already_complete_is_skipped(self):
        """服务端记过账的再报一次也不会多给次数，白跑还可能被判异常。"""
        tasks = [
            {"taskName": "中国移动APP中参与", "taskType": 2, "id": "x", "complete": True},
            {"taskName": "浏览调研页面", "taskType": 5, "id": "c", "complete": False},
        ]
        calls = []

        def get(path):
            calls.append(path)
            return {"code": "0000"}

        with _no_sleep():
            rep = L.do_tasks(get, lambda u: None, tasks)

        self.assertEqual(calls, ["doTask/5/c"])
        self.assertEqual(rep["done"], ["浏览调研页面"])
        self.assertIn(("中国移动APP中参与", "服务端已记完成"), rep["skip"])

    def test_opt_in_reports_assist_task(self):
        tasks = [{"taskName": "请好友帮忙", "taskType": 3, "id": "a", "complete": False}]
        calls = []

        def get(path):
            calls.append(path)
            return {"code": "0000"}

        with _no_sleep():
            rep = L.do_tasks(get, lambda u: None, tasks, skip_assist=False)

        self.assertEqual(calls, ["doTask/3/a"])
        self.assertEqual(rep["done"], ["请好友帮忙"])

    def test_401_stops_immediately(self):
        """刷任务途中票失效：后面的任务和抽奖都一定失败，别继续耗。"""
        tasks = [
            {"taskName": "中国移动APP中参与", "taskType": 2, "id": "a", "complete": False},
            {"taskName": "浏览调研页面", "taskType": 5, "id": "c", "complete": False},
        ]
        calls = []

        def get(path):
            calls.append(path)
            return {"code": "401", "msg": "未登陆"}

        with _no_sleep():
            rep = L.do_tasks(get, lambda u: None, tasks)

        self.assertTrue(rep["expired"])
        self.assertEqual(len(calls), 1)          # 第二条没再打
        self.assertEqual(rep["done"], [])

    def test_failed_task_is_reported_not_raised(self):
        """单个任务报错要让其它任务继续，别一个 -100 就把整轮打断。"""
        tasks = [
            {"taskName": "甲", "taskType": 5, "id": "a", "complete": False},
            {"taskName": "乙", "taskType": 5, "id": "b", "complete": False},
        ]
        replies = iter([{"code": "-100", "msg": "任务类型有误"}, {"code": "0000"}])

        with _no_sleep():
            rep = L.do_tasks(lambda p: next(replies), lambda u: None, tasks)

        self.assertEqual(rep["done"], ["乙"])
        self.assertEqual(rep["skip"], [("甲", "code=-100 任务类型有误")])
        self.assertFalse(rep["expired"])


class LuckTaskRefreshTest(unittest.TestCase):
    def test_cli_opt_in_and_reports_no_chance_increase(self):
        task = {"taskName": "请好友帮忙", "taskType": 3, "id": "a"}
        for flags in ([], ["--skip-assist"], ["--report-assist"]):
            with self.subTest(flags=flags):
                initial = {"code": "0000", "data": {"surplusLuckCount": 0, "taskList": [task]}}
                refreshed = {"code": "0000", "data": {"surplusLuckCount": 0}}
                replies = [initial, {"code": "0000"}, {"code": "0000"}, refreshed]
                with mock.patch.object(L, "collect_targets", return_value=[
                        ("fj1", "13800001234", "token")]), \
                        mock.patch.object(L.requests, "Session") as session, \
                        mock.patch.object(L, "save_state"), _no_sleep(), \
                        mock.patch.object(sys, "argv", ["cmcc_luck.py", *flags]), \
                        redirect_stdout(io.StringIO()) as out:
                    get = session.return_value.get
                    get.side_effect = [mock.Mock(json=mock.Mock(return_value=r)) for r in replies]
                    self.assertEqual(L.main(), 0)
                paths = [call.args[0].removeprefix(L.API) for call in get.call_args_list]
                if flags == ["--report-assist"]:
                    self.assertEqual(paths, ["index", "qrySubscription", "doTask/3/a", "index"])
                    self.assertIn("上报 1 个任务但抽奖次数未增加：请好友帮忙", out.getvalue())
                else:
                    self.assertEqual(paths, ["index", "qrySubscription"])


if __name__ == "__main__":
    unittest.main()
