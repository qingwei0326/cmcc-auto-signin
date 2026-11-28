#!/usr/bin/env python3
"""cmcc_doumi.py —— 移动爱购「AI 豆苗养成记」自动攒阳光值。

活动页 https://dev.coc.10086.cn/coc3/canvas/rightsmarket-h5-canvas/online/2026dmycj
活动 ID 17692，任务列表 ID 55（由 getSlgCreditsHomePage 返回，不写死）。

流程（全部来自 2026-11-28 抓包实测）：
  1. userLoginV2        用 app_token 当 ticket 换 phone token
  2. getSlgCreditsHomePage  取任务列表 ID + 未领取的阳光值记录
  3. accumulateSign     每日签到 +10 阳光值
  4. getGrowthTaskList  列全部任务，对未完成的浏览/分享任务上报
  5. task/visit / share 上报（付费/真实操作类跳过）
  6. getGrowthBubble    把 unreceivedCreditsVOList 逐条领取成阳光值
  7. queryGrowthValue   报最终阳光值

关键点：任务完成只进「未领取列表」，必须再调 getGrowthBubble 领取才算到手。
channelCode 有两套：活动本身 P00000050508，任务上报 P00000153856。

用法：
    python cmcc_doumi.py --config configs/xxx.json
    python cmcc_doumi.py --config configs/xxx.json --dry-run
    python cmcc_doumi.py --config configs/xxx.json --no-tasks   # 只签到+领取，不做任务
"""

import argparse
import re
import sys
import time
import random

import requests

from common import (BASE, UA_MOBILE, setup_utf8, emit_result, load_json,
                    notify as _common_notify, mask_phone)

setup_utf8()  # 必须在 basicConfig 之前，保证 stderr 用 UTF-8

import logging
log = logging.getLogger("doumi")

# ---- 固定参数（抓包实测，活动页自带） ----
COC = "https://dev.coc.10086.cn"
ACTIVITY_ID = 17692
CH_ACTIVITY = "P00000050508"   # 活动 / 兑换 / 分享 / 签到 channel
CH_TASK = "P00000153856"       # 任务上报 channel
SIGN_TASK_ID = 3115
PAGE_SLUG = "2026dmycj"
REFERER = f"{COC}/coc3/canvas/rightsmarket-h5-canvas/online/{PAGE_SLUG}"

P_HOME = "/coc3/coc3-market-activity/api/activity/slgCredits/getSlgCreditsHomePage"
P_GROWTH = "/coc3/coc3-market-activity/api/activity/slgCredits/queryGrowthValue"
P_BUBBLE = "/coc3/coc3-market-activity/api/activity/slgCredits/getGrowthBubble"
P_RULE = "/coc3/coc3-market-activity/api/activity/slgCredits/getSlgCreditsActivityRule"
P_TASK_LIST = "/coc3/coc3-market-activity/cmpp-manage/task/getGrowthTaskList"
P_SIGN_INFO = "/coc3/coc3-market-activity/cmpp-manage/task/getTaskSignInfo"
P_SIGN = "/coc3/coc3-market-activity/cmpp-manage/task/accumulateSign"
P_VISIT = "/coc3/coc3-market-activity/cmpp-manage/task/visit"
P_SHARE = "/coc3/coc3-market-activity/cmpp-manage/task/share"
P_LOGIN = "/coc3/gr/login/ssoRights/userLoginV2"

# taskType → 处理方式。付费/关注/助力/消费/订购等真实操作类一律跳过。
T_VISIT = {1}          # 浏览类
T_SHARE = {2}          # 分享类
SKIP_NOTE = {3: "订购/办理", 4: "领福袋/福利社", 5: "关注公众号/微博",
             6: "好友助力", 15: "AI豆订购", 16: "消费满额"}

STATE_FILE = BASE / "gui_data" / "doumi_state.json"


# ---------------------------------------------------------------- 会话与调用

def login(app_token: str) -> tuple:
    """用 app_token 当 ticket 换 coc 的 phone token。返回 (token, 尾号)。"""
    s = requests.Session()
    s.trust_env = False   # 不走系统代理，避免抓包工具干扰
    s.headers.update({
        "User-Agent": UA_MOBILE,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=utf-8",
        "Origin": COC,
        "Referer": REFERER,
    })
    body = {
        "ticket": app_token, "ticketType": 11,
        "businessType": "RIGHTS_MARKET",
        "wxoaType": "", "m1": "", "m2": "", "s1": "", "s2": "", "from": "",
        "jsid": app_token,
    }
    r = s.post(COC + P_LOGIN, json=body, timeout=25)
    r.raise_for_status()
    j = r.json()
    d = j.get("data") or {}
    token = d.get("token") or ""
    if j.get("code") not in (0, "0") or not token:
        raise RuntimeError(f"登录失败：{j.get('message') or j.get('msg') or j}")
    s.headers["phone"] = token
    return s, token, d.get("mobileMask", "")


def api(s: requests.Session, path: str, body: dict, *, retries: int = 1) -> dict:
    """统一调用。coc 返回 resultCode=0 成功，data 有时是提示字符串。"""
    last = None
    for i in range(retries + 1):
        try:
            r = s.post(COC + path, json=body, timeout=25)
            r.raise_for_status()
            j = r.json()
        except Exception as e:
            last = {"resultCode": -1, "msg": f"网络异常: {e}", "data": None}
        else:
            rc = j.get("resultCode", j.get("code"))
            if str(rc) == "0":
                return j
            last = j
            msg = str(j.get("msg") or j.get("data") or "")
            # 任务上报的限频提示放在 data 里，退避重试一次
            if "频繁" not in msg or i >= retries:
                return j
        time.sleep(3.0)
    return last or {}


def jsessionid_of(ticket: str) -> str:
    m = re.search(r"JSESSIONID=([^;]+)", ticket or "")
    return m.group(1) if m else ""


def data_of(res: dict):
    return res.get("data") if str(res.get("resultCode", res.get("code"))) == "0" else None


# ---------------------------------------------------------------- 各环节

def do_sign(s, wt_ac_id: str) -> str:
    """签到领 10 阳光值。重复签到服务端会返回提示，不算失败。"""
    api(s, P_SIGN_INFO, {"taskId": SIGN_TASK_ID, "needQyCredits": 1, "needLimit": 0})
    body = {"taskId": SIGN_TASK_ID, "channelCode": CH_ACTIVITY, "needLimit": 0,
            "wtAc": CH_ACTIVITY + "_", "wtAcId": wt_ac_id}
    r = api(s, P_SIGN, body)
    d = data_of(r)
    if isinstance(d, list) and d:
        names = "+".join(str(x.get("awardName", "")) for x in d)
        return f"签到成功 {names}"
    msg = str(r.get("msg") or r.get("data") or r.get("resultCode") or "未知")
    return f"签到：{msg}"


def slug_of(url: str) -> str:
    """从任务跳转 URL 取页面 slug，如 .../online/zzcj3?... → zzcj3。"""
    if not url:
        return ""
    m = re.search(r"/online/([^?/]+)", url)
    return m.group(1) if m else ""


def do_tasks(s, todo_only: bool = True) -> tuple:
    """上报浏览/分享任务。返回 (完成数, 跳过数, 明细行)。"""
    r = api(s, P_TASK_LIST, {"taskListId": 55})
    tasks = data_of(r)
    if not isinstance(tasks, list):
        return 0, 0, [f"任务列表读取失败：{r.get('msg') or r.get('resultCode')}"]
    done = skipped = 0
    lines = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tt = int(t.get("taskType") or 0)
        if t.get("isFinish") == 1:
            continue
        if tt in SKIP_NOTE:
            skipped += 1
            continue
        tid = t.get("taskId")
        if not tid:
            continue
        if tt in T_VISIT:
            # visitUrl 用任务自带的跳转页 slug；没有则用活动页（抓包验证可用）。
            vul = slug_of(str(t.get("unfinishRedirectUrl") or "")) or PAGE_SLUG
            res = api(s, P_VISIT, {"channelCode": CH_TASK, "visitUrl": vul, "taskId": tid})
            act = "浏览"
        elif tt in T_SHARE:
            res = api(s, P_SHARE, {"channelCode": CH_ACTIVITY,
                                   "visitUrl": PAGE_SLUG, "taskId": tid})
            act = "分享"
        else:
            skipped += 1
            continue
        d = res.get("data")
        ok = str(res.get("resultCode")) == "0" and d is None
        # data 是字符串提示（访问过于频繁 / 该url不在配置范围内）视为没成功
        if ok:
            done += 1
            lines.append(f"  ✓ {act} {t.get('taskName', tid)}")
        else:
            lines.append(f"  ✗ {act} {t.get('taskName', tid)}：{d or res.get('msg')}")
        time.sleep(random.uniform(1.2, 2.5))  # 限频：连报会「访问过于频繁」
    return done, skipped, lines


def claim_credits(s, unreceived: list) -> tuple:
    """把未领取记录逐条领成阳光值。返回 (领取条数, 明细行)。"""
    got = 0
    lines = []
    for rec in unreceived or []:
        if not isinstance(rec, dict):
            continue
        rid = rec.get("recordId")
        if not rid:
            continue
        body = {"activityId": ACTIVITY_ID, "creditsRecordId": rid,
                "referer": PAGE_SLUG, "channelNo": CH_ACTIVITY}
        r = api(s, P_BUBBLE, body)
        if str(r.get("resultCode")) == "0":
            got += 1
            lines.append(f"  ✓ 领 {rec.get('showName', '')} +{rec.get('showNum', '')} 阳光值")
        else:
            lines.append(f"  ✗ 领 {rec.get('recordId')}：{r.get('msg') or r.get('data')}")
        time.sleep(random.uniform(0.6, 1.2))
    return got, lines


# ---------------------------------------------------------------- 主流程

def run(cfg: dict, *, dry_run: bool = False, do_task: bool = True) -> dict:
    app_token = str(cfg.get("app_token") or "")
    if not app_token:
        raise RuntimeError("配置缺少 app_token")
    wt_ac_id = jsessionid_of(app_token)

    s, token, mask = login(app_token)
    grow = lambda: data_of(api(s, P_GROWTH, {"activityId": ACTIVITY_ID})) or {}
    home = lambda: data_of(api(s, P_HOME, {"activityId": ACTIVITY_ID})) or {}

    before = grow().get("growthValue", 0)
    h = home()
    unreceived = h.get("unreceivedCreditsVOList") or []
    lines = [f"手机 {mask or mask_phone(cfg.get('phone'))}  当前阳光值 {before}，待领 {len(unreceived)} 条"]

    if dry_run:
        r = api(s, P_TASK_LIST, {"taskListId": h.get("slgTaskListId") or 55})
        tasks = data_of(r) or []
        todo = [t for t in tasks if isinstance(t, dict) and t.get("isFinish") != 1
                and int(t.get("taskType") or 0) not in SKIP_NOTE]
        lines.append(f"[只读] 可做任务 {len(todo)} 个：" +
                     "、".join(str(t.get("taskName")) for t in todo[:8]))
        print("\n".join(lines))
        return {"plat": "doumi", "dry_run": True, "growth": before,
                "unreceived": len(unreceived), "todo": len(todo)}

    lines.append(do_sign(s, wt_ac_id))

    if do_task:
        done, skipped, task_lines = do_tasks(s)
        lines.append(f"任务：完成 {done}，跳过 {skipped}（付费/真实操作类）")
        lines.extend(task_lines)
        unreceived = home().get("unreceivedCreditsVOList") or []

    got, claim_lines = claim_credits(s, unreceived)
    lines.append(f"领取阳光值：{got} 条")
    lines.extend(claim_lines)

    after = grow().get("growthValue", 0)
    lines.append(f"阳光值：{before} → {after}（+{max(0, after - before)}）")

    summary = "\n".join(lines)
    print(summary)
    # 批量运行时由 run_all 统一发汇总战报，子脚本静默（与 cmcc_sign 一致）
    import os
    if not os.environ.get("CMCC_NOTIFY_BATCH"):
        _common_notify("AI豆苗养成记", summary,
                       bark_url=str(cfg.get("bark_url") or ""),
                       serverchan_sendkey=str(cfg.get("serverchan_sendkey") or ""))
    return {"plat": "doumi", "token": "ok", "growth": after,
            "growth_before": before, "signed": True, "tasks_done": done if do_task else 0,
            "credits_claimed": got}


def main() -> int:
    ap = argparse.ArgumentParser(description="移动爱购 AI豆苗养成记自动攒阳光值")
    ap.add_argument("--config", default="config.json", help="配置文件（复用 configs/*.json）")
    ap.add_argument("--dry-run", action="store_true", help="只查状态")
    ap.add_argument("--no-tasks", action="store_true", help="只签到+领取，不做任务")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    try:
        cfg = load_json(args.config, {})
        if not cfg:
            print(f"配置文件读取失败：{args.config}")
            return 1
        res = run(cfg, dry_run=args.dry_run, do_task=not args.no_tasks)
        emit_result(res)
        return 0
    except Exception as e:
        if args.debug:
            log.exception("执行失败")
        else:
            log.error("执行失败：%s", e)
        emit_result({"plat": "doumi", "token": "error", "error": str(e)[:200]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
