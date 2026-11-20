#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中国移动「周三充值日」拼图任务、抽奖与领券。

链路：hlwyxhdhub 活动会话 → queryPictureActInfo/taskList → finishTask
→ drawPicture → receiveNew。邀请助力、真实充值和需要人工浏览的任务不代做。

用法：
  python cmcc_wed.py --config configs/1234.json --dry-run
  python cmcc_wed.py --config configs/1234.json
  python run_all.py --wed-only --dry-run

说明：queryActivityInfo 是进页登记，dry-run 不做任务、不抽奖、不领券，
但建立会话和进页登记仍可能被服务端记作「每日登录」。
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time

import requests

from cmcc_extra import ActConfig
from cmcc_sign import BASE, Config, api_headers, exchange_session, notify

log = logging.getLogger("cmcc_wed")

WED_ACT_URL = (
    "https://wx.10086.cn/hlwyxhdhub/act-wedrecharge/1024101716"
    "?pageId=1849008675699650560&channelId=P00000093619"
)
API_WED = BASE + "/hlwyxhdhub/api/wedrecharge"

TIER_NAMES = {"dp1": "95折话费券", "dp2": "9折话费券", "dp3": "8折话费券"}
PUZZLE_RECEIVE = {1: "r6", 2: "r5", 3: "r4"}
RECEIVE_LABELS = {"r6": "95折话费券", "r5": "9折话费券", "r4": "8折话费券"}

# 不伪造真实行为；其余查询/浏览类任务先直接上报，失败如实报告。
MANUAL_SKIP = {
    "yqyh": "需真实用户邀请助力",
    "czhf": "需真实充值，涉及消费，不自动化",
    "llym": "需在 App 内参与签到并浏览",
    "dlhd": "进页自动完成",
}


def wed_api(s: requests.Session, referer: str, path: str, payload: dict | None = None):
    """调用周三充值日接口；该 hub 的成功码是字符串 ``"0"``。"""
    r = s.post(f"{API_WED}/{path}", json=payload or {},
               headers=api_headers(referer), timeout=30)
    r.raise_for_status()
    resp = r.json()
    if str(resp.get("code")) != "0":
        raise RuntimeError(f"{path} 失败: {resp.get('code')} {resp.get('msg')}")
    data = resp.get("data")
    return data if data is not None else {}


def puzzle_complete(pieces: dict) -> bool:
    """当前拼图所有碎片都至少持有一份才算集齐。"""
    return bool(pieces) and all(
        str(v).isdigit() and int(v) >= 1 for v in pieces.values()
    )


def puzzle_lines(state: dict) -> list[str]:
    lines = []
    pics = state.get("pictureList") or {}
    for idx in sorted(pics, key=lambda x: int(x) if str(x).isdigit() else 999):
        pieces = pics[idx] or {}
        held = {k: int(v) for k, v in pieces.items()
                if str(v).isdigit() and int(v) >= 1}
        have, total = len(held), len(pieces)
        dup = sum(n - 1 for n in held.values() if n > 1)
        tier = TIER_NAMES.get(f"dp{idx}", f"dp{idx}")
        mark = "，已集齐" if puzzle_complete(pieces) else ""
        if dup:
            mark += f"，重复碎片 {dup} 份"
        lines.append(f"拼图{idx}（{tier}）：{have}/{total}{mark}")
    return lines


def quota_left(task: dict) -> int | None:
    """月度剩余次数；没有配额字段时不由客户端设限。"""
    req = task.get("requireTimes")
    try:
        req = int(req)
    except (TypeError, ValueError):
        return None
    if req <= 0:
        return None
    try:
        done = int(task.get("completedTimes") or 0)
    except (TypeError, ValueError):
        done = 0
    return max(0, req - done)


def task_lines(tasks: list[dict]) -> list[str]:
    lines = []
    for t in sorted(tasks, key=lambda x: x.get("order", 99)):
        tid = str(t.get("taskId") or "")
        title = t.get("title") or tid
        left = quota_left(t)
        add = t.get("addDrawTimes") or 1
        quota = (f"，月度 {t.get('completedTimes') or 0}/{t.get('requireTimes')}"
                 if left is not None else "")
        if t.get("status") == 2 or left == 0:
            lines.append(f"✓ {title}（+{add} 次{quota}，已完成或今日不可做）")
        elif tid in MANUAL_SKIP:
            lines.append(f"· {title}（+{add} 次{quota}，{MANUAL_SKIP[tid]}）")
        else:
            lines.append(f"→ {title}（+{add} 次{quota}，可自动上报）")
    return lines


def finish_tasks(s: requests.Session, referer: str,
                 tasks: list[dict], lines: list[str]) -> None:
    """完成可代做任务；真实充值、邀请和人工浏览任务绝不自动提交。"""
    todo = [t for t in tasks
            if str(t.get("taskId") or "") not in MANUAL_SKIP
            and t.get("status") != 2 and quota_left(t) != 0]
    for t in todo:
        tid = str(t.get("taskId") or "")
        title = t.get("title") or tid
        if not tid:
            continue
        try:
            data = wed_api(s, referer, "finishTask", {"taskId": tid})
            add = data.get("addDrawTimes") if isinstance(data, dict) else None
            lines.append(f"[任务] {title} 完成" + (f"，+{add} 次" if add else ""))
        except Exception as e:
            lines.append(f"[任务] {title} 未自动完成：{e}")
        time.sleep(random.uniform(0.8, 1.5))


def draw_all(s: requests.Session, referer: str, lines: list[str],
             max_draws: int = 30) -> None:
    """抽完当前可用次数；每抽前重查状态，集齐当前拼图就停手。"""
    total = 0
    for _ in range(max_draws):
        state = wed_api(s, referer, "queryPictureActInfo")
        try:
            times = int(state.get("drawTimes") or 0)
            active = int(state.get("activeIndex") or 1)
        except (TypeError, ValueError):
            lines.append("[抽奖] 停止：活动状态字段格式异常")
            break
        if times <= 0:
            break
        pieces = (state.get("pictureList") or {}).get(str(active)) or {}
        if puzzle_complete(pieces):
            lines.append(f"[抽奖] 拼图{active}已集齐，剩余 {times} 次保留")
            break
        r = s.post(f"{API_WED}/drawPicture", json={"round": f"dp{active}"},
                   headers=api_headers(referer), timeout=30)
        r.raise_for_status()
        resp = r.json()
        if str(resp.get("code")) != "0":
            lines.append(f"[抽奖] 停止：{resp.get('code')} {resp.get('msg')}")
            break
        total += 1
        prize = (resp.get("data") or {}).get("prize") or {}
        log.info("[抽奖] dp%s 第 %d 抽，碎片 %s", active, total,
                 prize.get("prizeId") or prize.get("prizeName") or "?")
        time.sleep(random.uniform(1, 2))
    lines.append(f"[抽奖] 本次共抽 {total} 次")


def receive_ready(s: requests.Session, referer: str,
                  state: dict, lines: list[str]) -> None:
    """领取已集齐拼图；已领过按正常状态处理，不重复报错。"""
    pics = state.get("pictureList") or {}
    for idx_s in sorted(pics, key=lambda x: int(x) if str(x).isdigit() else 999):
        if not str(idx_s).isdigit() or not puzzle_complete(pics[idx_s] or {}):
            continue
        rnd = PUZZLE_RECEIVE.get(int(idx_s))
        if not rnd:
            continue
        label = RECEIVE_LABELS.get(rnd, rnd)
        r = s.post(f"{API_WED}/receiveNew", json={"round": rnd},
                   headers=api_headers(referer), timeout=30)
        r.raise_for_status()
        resp = r.json()
        msg = resp.get("msg") or ""
        if str(resp.get("code")) == "0":
            prizes = (resp.get("data") or {}).get("actPrizes") or []
            names = "、".join(filter(None, (p.get("prizeName") for p in prizes)))
            lines.append(f"[领取] {label} 领取成功" + (f"（{names}）" if names else ""))
        elif "已领取" in msg:
            lines.append(f"[领取] {label} 已领取过")
        else:
            lines.append(f"[领取] {label} 领取失败：{msg or resp.get('code')}")
        time.sleep(random.uniform(1, 2))


def run(cfg: Config, dry_run: bool, do_tasks: bool = True,
        do_draw: bool = True, do_claim: bool = True,
        max_draws: int = 30) -> list[str]:
    # ActConfig 只替换活动入口，账号凭证仍来自原配置。
    s, referer = exchange_session(ActConfig(cfg, WED_ACT_URL))
    wed_api(s, referer, "queryActivityInfo")
    state = wed_api(s, referer, "queryPictureActInfo")
    tasks = wed_api(s, referer, "taskList")
    if isinstance(tasks, dict):
        tasks = tasks.get("taskList") or tasks.get("tasks") or []

    active = int(state.get("activeIndex") or 1)
    tier = TIER_NAMES.get(f"dp{active}", f"dp{active}")
    lines = [f"剩余抽奖次数：{state.get('drawTimes')}，当前拼图：{tier}"]
    lines += puzzle_lines(state)
    lines += task_lines(tasks)

    if not dry_run:
        if do_tasks:
            finish_tasks(s, referer, tasks, lines)
        if do_draw:
            draw_all(s, referer, lines, max_draws=max_draws)

    final = wed_api(s, referer, "queryPictureActInfo")
    lines.append(f"终态：剩余 {final.get('drawTimes')} 次")
    lines += puzzle_lines(final)
    completed = [idx for idx, pieces in (final.get("pictureList") or {}).items()
                 if puzzle_complete(pieces or {})]
    if completed and dry_run:
        lines.append("[领取] 已有拼图集齐；正式运行可自动领取")
    elif completed and do_claim:
        receive_ready(s, referer, final, lines)
    if dry_run:
        lines.append("[dry-run] 未上报任务、未抽奖、未领券")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="中国移动「周三充值日」拼图抽奖与领券")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="只查询；不做任务、不抽奖、不领券")
    ap.add_argument("--no-tasks", action="store_true", help="不做任务")
    ap.add_argument("--no-draw", action="store_true", help="不消耗抽奖次数")
    ap.add_argument("--no-claim", action="store_true", help="不领取已集齐奖励")
    ap.add_argument("--max-draws", type=int, default=30, help="单次最多抽奖次数")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    cfg = Config(args.config)
    if err := cfg.validate():
        log.error("%s", err)
        return 1
    try:
        for line in run(cfg, args.dry_run, not args.no_tasks,
                        not args.no_draw, not args.no_claim, args.max_draws):
            print(line)
        return 0
    except Exception as e:
        msg = str(e)
        if "appTokenLogin" in msg or "票据" in msg or "过期" in msg:
            log.error("登录失败：%s", msg.split("——")[0].strip())
            log.error("app_token 约 30~46 分钟寿命，请重新抓包导入后再运行。")
        else:
            log.exception("周三充值日执行失败: %s", e)
        notify(cfg, "周三充值日执行失败",
               f"{msg.split('——')[0].strip()}\n手机尾号 {cfg.phone[-4:] if cfg.phone else '????'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
