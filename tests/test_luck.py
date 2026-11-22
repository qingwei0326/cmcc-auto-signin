"""大转盘的「加抽奖次数」任务。

2026-11-22 实测：`index.taskList` 里那 4 个任务（请好友帮忙 / 逛移动爱购商城 /
中国移动APP中参与 / 浏览调研页面）逐个 `doTask` 全回 `0000`，剩余次数 0 → 2。
但 `run_all.run_luck` 一直写死传 `--no-tasks`，等于每天白扔两次抽奖机会。
这里把这套判定钉住，顺带钉住「已完成的任务不要重复报」和「401 立刻收手」。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402
import cmcc_luck as L  # noqa: E402


def _no_sleep():
    """do_tasks 里每个任务之间会歇 1~2 秒，测试里不必真等。"""
    return mock.patch.object(L.time, "sleep", lambda *_: None)


class DoTasksTest(unittest.TestCase):
    def test_reports_every_unfinished_task(self):
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

        self.assertEqual(rep["done"], ["请好友帮忙", "逛移动爱购商城", "浏览调研页面"])
        self.assertEqual(rep["skip"], [])
        self.assertFalse(rep["expired"])
        self.assertEqual(calls, ["doTask/3/a", "doTask/5/b", "doTask/5/c"])
        # 只有带 taskUrl 的才去真打开页面；其余两个没有落地页
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

    def test_skip_assist_leaves_friend_task_alone(self):
        """「请好友帮忙」没有对端账号，只能空报；不想空报时给个开关。"""
        tasks = [{"taskName": "请好友帮忙", "taskType": 3, "id": "a", "complete": False}]
        calls = []

        def get(path):
            calls.append(path)
            return {"code": "0000"}

        with _no_sleep():
            rep = L.do_tasks(get, lambda u: None, tasks, skip_assist=True)

        self.assertEqual(calls, [])
        self.assertEqual(rep["done"], [])
        self.assertIn(("请好友帮忙", "好友助力，已跳过"), rep["skip"])

    def test_401_stops_immediately(self):
        """刷任务途中票失效：后面的任务和抽奖都一定失败，别继续耗。"""
        tasks = [
            {"taskName": "请好友帮忙", "taskType": 3, "id": "a", "complete": False},
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


if __name__ == "__main__":
    unittest.main()
