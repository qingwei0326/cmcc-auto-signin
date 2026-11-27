"""
cmcc_xunbao.py —— 福建移动「环闽寻宝记」自动化

活动入口（H5）：
    https://wap.fj.10086.cn/bass-activityH5/pages/nationalDay2026/index

链路（全部 POST JSON，body 只要 appId/timestamp；用户态靠 Bearer）：
    activity/status    → 活动期与状态
    activity/init      → 当前格子 currentSeq、骰子数 diceBalance、能否投 canRoll、是否需要开宝箱
    task/list          → 加骰子任务（好友助力 / 打开消息提醒 / 参与天天大转盘）
    task/report        → 上报任务完成 {taskCode}
    dice/balance       → 骰子余额
    dice/roll          → 投骰子，返回路径 moves（含落点 cellCode / event / prizes）
    treasure/open      → 走到终点后开藏宝阁宝箱
    prize/list         → 我的奖品（status 0=待领取，1=已发放）
    prize/claim        → 领取待领取奖品 {recordNo}
    assist/create      → 生成助力链接；assist/do → 帮别人助力 {inviteToken}
    treasure/poolPreview → 宝箱奖池预览

鉴权（踩过的坑）：
    · 与转盘/养成共用同一套 BASS Bearer，configs_fj/*.json 里那份直接能用；
    · **UA 必须带 App 标记** `leadeon/12.5.7/CMCCIT`，只写 leadeon/12 会被挡成
      「2004 请前往中国移动App参与活动」——本次就是靠这个才跑通的。

用法：
    python cmcc_xunbao.py                 # 所有福建账号：投完现有骰子
    python cmcc_xunbao.py --dry-run       # 只看进度/骰子/任务/奖品，不投
    python cmcc_xunbao.py --key fj2       # 只跑某一个配置
    python cmcc_xunbao.py --tasks         # 顺手上报「跳转类」任务换骰子（不含好友助力）
    python cmcc_xunbao.py --max 6         # 每号本次最多投 6 次
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from common import (api_post, app_base, atomic_write_json, file_lock, load_json,
                    make_session, mask_phone, record_award, setup_utf8, update_json)

setup_utf8()

BASE = app_base()
CFG_DIR = BASE / "configs_fj"
STATE_FILE = BASE / "gui_data" / "xunbao_state.json"
ASSIST_STATE = BASE / "gui_data" / "xunbao_assist_state.json"

API = "https://wap.fj.10086.cn/bass-commonAreaServer/H5Api/hmxb/"
REFERER = "https://wap.fj.10086.cn/bass-activityH5/pages/nationalDay2026/index"

# 关键：服务端按 UA 判断「是不是在中国移动 App 里打开的」。
# 少了 leadeon/x.y.z/CMCCIT 这一段，所有用户态接口都回 2004。
UA_APP = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Mobile/15E148/wkwebview leadeon/12.5.7/CMCCIT")

# 好友助力靠真人点链接，程序替不了；「打开消息提醒」要真去开通知权限。
# 两者都不自动上报，只报「参与天天大转盘 / 参与某活动」这类本程序真的做过的跳转任务。
ASSIST_TASK = "TASK_1501"
SKIP_TASKS = {ASSIST_TASK, "TASK_1502"}

# 加骰子任务里有两类「要真去做别的事」才成立：
#   · 参与一起消消乐活动 → 这一轮跑过消消乐（run_all 传 --did-xxl）
#   · 参与天天大转盘     → 这一轮抽过转盘（run_all 传 --did-luck）
# task/report 只递 taskCode，服务端不校验你到底做没做，所以这层判断只能在本地做，
# 没做过的任务一律不报（白拿骰子既不公平，也容易把这个号标记成异常）。
XXL_TASK = "TASK_1515"
XXL_WORD = "消消乐"
LUCK_WORD = "大转盘"
ASSIST_WORD = "助力"
NOTIFY_WORD = "消息提醒"


def task_reportable(task, did_xxl=False, did_luck=False):
    """这个加骰子任务能不能替用户上报（前提：本轮确实做过对应动作）。"""
    code = str(task.get("taskCode") or "")
    name = str(task.get("taskName") or "")
    if code in SKIP_TASKS or ASSIST_WORD in name or NOTIFY_WORD in name:
        return False
    if code == XXL_TASK or XXL_WORD in name:
        return bool(did_xxl)
    if LUCK_WORD in name:
        return bool(did_luck)
    return True

CELL_NAMES = {
    "START": "起点", "END": "终点", "FUDAI": "福袋", "KOI": "锦鲤",
    "REST": "歇脚", "EVENT": "事件", "MARKET": "集市",
}


# ---------------------------------------------------------------- 账号来源

def collect_targets(key=None):
    """从 configs_fj 读 bearer：返回 [(tag, phone, bearer)]"""
    out = []
    if not CFG_DIR.exists():
        return out
    for f in sorted(CFG_DIR.glob("*.json")):
        if key and f.stem != key:
            continue
        try:
            cfg = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        tok = str(cfg.get("bearer_token") or "").strip()
        if tok:
            out.append((f.stem, str(cfg.get("phone") or ""), tok))
    return out


def mutual_assist(dry_run=False):
    """按号对记录当月助力，按助力者记录当天额度，返回遇到 401 的配置名。"""
    targets = collect_targets()
    expired = set()
    if len(targets) < 2:
        print("多号互助力：至少需要两个福建账号，跳过。")
        return {"tried": 0, "success": 0, "expired": []}
    sessions = [(tag, phone, Xunbao(tok)) for tag, phone, tok in targets]
    day = time.strftime("%Y-%m-%d")
    month = day[:7]

    tried = success = 0
    with file_lock(ASSIST_STATE):
        state = load_json(ASSIST_STATE, default={}) or {}
        changed = False
        print(f"\n环闽寻宝 · 多号互助力（{len(sessions)} 个号）")
        for hi, (helper, _hp, hapi) in enumerate(sessions):
            if helper in expired:
                continue
            quota_key = f"quota:{helper}"
            # 旧状态只有助力者和日期，仍可用于当天的额度判断。
            if state.get(quota_key) == day or state.get(helper) == day:
                print(f"  - {helper} 今天已帮过，跳过")
                continue
            # 轮着挑被助力方，避免每次都帮同一个号
            owners = [s for j, s in enumerate(sessions) if j != hi]
            owners = owners[hi % len(owners):] + owners[:hi % len(owners)]
            for owner, _op, oapi in owners:
                pair_key = "|".join(sorted((helper, owner)))
                if state.get(pair_key) == month:
                    print(f"  - {helper} 与 {owner} {month} 已互点，跳过")
                    continue
                if owner in expired:
                    continue
                tried += 1
                if dry_run:
                    print(f"  [dry-run] {helper} 帮 {owner}")
                    break
                cr = oapi.call("assist/create")
                if str(cr.get("code")) == "401":
                    print(f"  - {owner} token 失效（401），跳过")
                    expired.add(owner)
                    continue
                tk = (cr.get("data") or {}).get("inviteToken") if cr.get("ok") else None
                if not tk:
                    print(f"  - {owner} 生成助力码失败 code={cr.get('code')}，换下一个")
                    continue
                hr = hapi.call("assist/do", {"inviteToken": tk})
                code = str(hr.get("code"))
                msg = str(hr.get("msg") or "")
                if code == "401":
                    print(f"  - {helper} token 失效（401），跳过")
                    expired.add(helper)
                    break
                if hr.get("ok"):
                    print(f"  ✓ {helper} 帮 {owner}（{owner} +1 骰）")
                    state[pair_key] = month
                    state[quota_key] = day
                    changed = True
                    success += 1
                    break
                if code == "-100" and ("当月不可为同一人助力" in msg or "已接受过" in msg):
                    print(f"  - {helper} 与 {owner} 本月已互点，跳过")
                    state[pair_key] = month
                    changed = True
                    continue
                if "次数已用尽" in msg:
                    print(f"  - {helper} 今日助力次数已用尽")
                    state[quota_key] = day
                    changed = True
                    break
                print(f"  ✗ {helper} 帮 {owner}: code={hr.get('code')} {msg[:30]}")
                time.sleep(random.uniform(0.6, 1.1))
        if changed:
            atomic_write_json(ASSIST_STATE, state)
    return {"tried": tried, "success": success, "expired": sorted(expired)}


def save_state(phone, info, rolls=0, prizes=0):
    """按号存进度；rolls/prizes 是**当天累计**，跨天归零。

    和转盘一样，界面要能回答「今天这程序投了几次、中了几个」，
    只存最后一次的数字对不上账。
    """
    key = (phone or "unknown").replace("*", "")[-4:] or "unknown"
    today = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    def mutate(st):
        acc = st.setdefault(key, {})
        acc.update(info)
        if acc.get("day") != today:
            acc["day"] = today
            acc["day_rolls"] = 0
            acc["day_prizes"] = 0
        if rolls or prizes:
            acc["day_rolls"] = int(acc.get("day_rolls") or 0) + int(rolls)
            acc["day_prizes"] = int(acc.get("day_prizes") or 0) + int(prizes)
        acc["updated"] = stamp

    try:
        update_json(STATE_FILE, mutate)
    except Exception:
        pass


# ---------------------------------------------------------------- 单账号流程

class Xunbao:
    def __init__(self, token):
        self.s = make_session(token, headers={
            "Content-Type": "application/json",
            "User-Agent": UA_APP,
            "Origin": "https://wap.fj.10086.cn",
        }, referer=REFERER)

    def call(self, path, data=None):
        body = {"appId": 0, "timestamp": int(time.time() * 1000)}
        if data:
            body.update(data)
        retry_codes = ("1006",) if path == "assist/do" else ("-100", "1006")
        return api_post(self.s, API + path, body, retries=1, retry_codes=retry_codes)


class XunbaoRequestError(Exception):
    """无法继续当前账号的寻宝流程。"""


def run_one(tag, phone_hint, tok, args):
    api = Xunbao(tok)
    print("\n" + "-" * 62)
    print(f"【{tag}】{mask_phone(phone_hint)}")
    try:
        return _run_activity(api, tag, phone_hint, args)
    except XunbaoRequestError as e:
        print(f"  {e}")
        return {"tag": tag, "skip": True}


def _run_activity(api, tag, phone_hint, args):
    def call(path, data=None, *, required=False):
        r = api.call(path) if data is None else api.call(path, data)
        if str(r.get("code")) == "401":
            raise XunbaoRequestError(f"{path}：token 失效（401），请重新抓包更新该号 bearer。")
        if required and not r.get("ok"):
            raise XunbaoRequestError(f"{path} 失败：{r.get('code')} {r.get('msg') or ''}")
        return r

    if args.invite:
        # 好友助力要真人点链接，程序只负责生成链接，剩下交给朋友
        r = call("assist/create")
        tk = (r.get("data") or {}).get("inviteToken") if r.get("ok") else ""
        if tk:
            print(f"  助力链接（发给好友，点「去助力」即可）：\n"
                  f"    {REFERER}?inviteToken={tk}")
        else:
            print(f"  生成助力链接失败：{r.get('code')} {r.get('msg')}")
        return {"tag": tag, "phone": phone_hint, "skip": not bool(tk), "invite": True}

    st = call("activity/status", required=True)
    act = st.get("data") or {}
    print(f"  活动期 {act.get('activityPeriod')}  {act.get('startTime')} ~ {act.get('endTime')}")

    init = call("activity/init", required=True)
    d = init.get("data") or {}
    board = d.get("board") or []
    total = max((c.get("seq") or 0) for c in board) if board else 0
    seq = int(d.get("currentSeq") or 0)
    dice = int(d.get("diceBalance") or 0)
    can_roll = bool(d.get("canRoll"))
    claim_req = bool(d.get("treasureClaimRequired"))
    cell = next((c for c in board if c.get("seq") == seq), None)
    cell_name = CELL_NAMES.get((cell or {}).get("cellCode") or "", (cell or {}).get("cellCode") or "?")

    phone = phone_hint or ""
    print(f"  进度：第 {seq}/{total} 格（{cell_name}）　骰子 {dice} 个　"
          f"{'可投' if can_roll else '不可投'}"
          + ("　★ 宝箱待开启" if claim_req else ""))

    # ---- 任务 ----
    # 任务和奖品列表只是附带信息：401 照样按失效处理，其它失败不挡投骰子
    tl = call("task/list")
    tasks = ((tl.get("data") or {}).get("tasks") or []) if tl.get("ok") else []
    if not tl.get("ok"):
        print(f"  task/list 失败：{tl.get('code')} {tl.get('msg') or ''}（本轮不报任务）")
    if tasks:
        print("  加骰子任务：")
        for t in tasks:
            done = str(t.get("status")) == "1"
            usable = task_reportable(t, getattr(args, "did_xxl", False),
                                     getattr(args, "did_luck", False))
            note = ""
            if not done and not usable:
                note = "　（本轮没做过，不报）"
            print(f"    {'✓' if done else '·'} {t.get('taskName')}  +{t.get('rewardDice')} 个"
                  f"  {t.get('limitType')}×{t.get('limitCount')}{note}")

    # ---- 我的奖品 ----
    pl = call("prize/list", {"page": 1, "size": 20})
    pd = (pl.get("data") or {}) if pl.get("ok") else {}
    recs = pd.get("records") or []
    pending = [r for r in recs if str(r.get("status")) == "0"]
    if pl.get("ok"):
        print(f"  我的奖品：共 {pd.get('total')} 个，待领取 {len(pending)} 个")
    else:
        print(f"  prize/list 失败：{pl.get('code')} {pl.get('msg') or ''}")
    if recs:
        top = recs[0]
        print(f"    最近：{top.get('winTime')} {top.get('prizeName')}")

    if args.dry_run:
        save_state(phone, {
            "phone": phone, "seq": seq, "total": total, "dice": dice,
            "can_roll": can_roll, "treasure_claim_required": claim_req,
            "tasks": [{"code": t.get("taskCode"), "name": t.get("taskName"),
                       "done": str(t.get("status")) == "1", "dice": t.get("rewardDice")}
                      for t in tasks],
            "prizes_total": pd.get("total"),
            "prizes_pending": len(pending),
        })
        return {"tag": tag, "phone": phone, "seq": seq, "total": total, "dice": dice,
                "rolls": 0, "prizes": 0}

    # ---- 上报任务（可选）----
    if args.tasks:
        todo = [t for t in tasks
                if str(t.get("status")) != "1"
                and task_reportable(t, getattr(args, "did_xxl", False),
                                    getattr(args, "did_luck", False))]
        if todo:
            print("  [上报任务]")
        for t in todo:
            # task/report 只递 taskCode，服务端按 activity 配置发骰子；
            # 「好友助力」「打开消息提醒」不在 SKIP_TASKS 之外的名单里，不报。
            r = call("task/report", {"taskCode": t.get("taskCode")})
            ok = r.get("ok")
            print(f"    {'OK ' if ok else '!! '} {t.get('taskName')}  "
                  f"code={r.get('code')} {'' if ok else (r.get('msg') or '')}")
            time.sleep(random.uniform(1.2, 2.0))
        if todo:
            time.sleep(1.2)
            d = call("activity/init", required=True).get("data") or {}
            dice = int(d.get("diceBalance") or 0)
            can_roll = bool(d.get("canRoll"))
            print(f"    上报后骰子 {dice} 个")

    # ---- 领取待领取奖品 ----
    new_prizes = []
    if pending:
        pl2 = call("prize/list", {"page": 1, "size": 20})
        for r in ((pl2.get("data") or {}).get("records") or []):
            if str(r.get("status")) != "0":
                continue
            rc = call("prize/claim", {"recordNo": r.get("recordNo")})
            ok = rc.get("ok")
            print(f"  [领奖] {'OK ' if ok else '!! '}{r.get('prizeName')}  code={rc.get('code')}")
            if ok:
                new_prizes.append(r.get("prizeName"))
                record_award(phone, "环闽寻宝", r.get("prizeName") or "")
            time.sleep(random.uniform(1.0, 1.6))

    # ---- 投骰子 + 开宝箱 ----
    # 走到终点后地图会**重置回第 1 格、开始新一圈**（实测 currentSeq 回到 1、
    # canRoll 恢复 true、骰子还在），所以「到达终点」不等于「本轮结束」：
    # 要先开宝箱，再把剩下的骰子接着投下一圈。旧实现在终点就 break，
    # 余下的骰子当天作废——用户 2026-11-22 指出过这个坑。
    rolls, win_prizes, treasures = 0, [], []
    opened = set()          # 已经开过箱的 (圈内位置)，防止服务端延迟时反复开箱

    def read_state():
        d3 = call("activity/init", required=True).get("data") or {}
        return (int(d3.get("currentSeq") or 0), int(d3.get("diceBalance") or 0),
                bool(d3.get("canRoll")), bool(d3.get("treasureClaimRequired")))

    def open_treasure():
        r = call("treasure/open")
        if not r.get("ok"):
            print(f"  [宝箱] 失败 code={r.get('code')} {r.get('msg')}")
            return ""
        nm = (r.get("data") or {}).get("prizeName") or ""
        print(f"  [宝箱] 开出：{nm or '(未返回奖品名)'}")
        if nm:
            record_award(phone, "环闽寻宝·藏宝阁", nm)
            treasures.append(nm)
        return nm

    guard = 0
    while guard < args.max * 3 + 5:
        guard += 1
        need_open = claim_req or bool(total and seq >= total and not can_roll)
        if need_open:
            spot = (seq, total)
            if spot in opened:
                break            # 开过了但服务端没重置，别死循环
            opened.add(spot)
            open_treasure()
            seq, dice, can_roll, claim_req = read_state()
            print(f"    → 开箱后回到第 {seq}/{total} 格，骰子 {dice} 个")
            time.sleep(random.uniform(1.0, 1.8))
            continue
        if rolls >= args.max or not can_roll or dice <= 0:
            break
        r = call("dice/roll")
        code = str(r.get("code"))
        if not r.get("ok"):
            print(f"    [{rolls + 1}] 投掷失败 code={code} {r.get('msg')}")
            if code == "-1":
                break
            time.sleep(2.0)
            continue
        dd = r.get("data") or {}
        rolls += 1
        moves = dd.get("moves") or []
        got = []
        events = []
        for mv in moves:
            for pz in (mv.get("prizes") or []):
                got.append(pz.get("prizeName") or "")
            ev = mv.get("event") or {}
            if ev.get("eventDesc"):
                events.append(ev.get("eventDesc"))
        line = (f"    [{rolls}] 🎲{dd.get('dicePoint')}  "
                f"{dd.get('fromSeq')}→{dd.get('finalSeq')}")
        cell2 = next((c for c in board if c.get("seq") == dd.get("finalSeq")), None)
        if cell2:
            line += f"（{CELL_NAMES.get(cell2.get('cellCode') or '', cell2.get('cellCode') or '')}）"
        print(line)
        for e in events:
            print(f"        事件：{e}")
        for nm in got:
            print(f"        ★ 中奖：{nm}")
            win_prizes.append(nm)
            record_award(phone, "环闽寻宝", nm)
        if dd.get("reachedEnd"):
            print("        → 到达终点，开宝箱后进入下一圈")

        # 每投一次都重新读状态：事件/宝箱会改变骰子数和可投性
        seq, dice, can_roll, claim_req = read_state()
        time.sleep(random.uniform(1.5, 2.5))

    # 兜底：骰子用完了但正好停在终点（上面循环因 rolls 达上限先退出）
    if (claim_req or (total and seq >= total and not can_roll)) \
            and (seq, total) not in opened:
        open_treasure()
        seq, dice, can_roll, claim_req = read_state()

    all_win = new_prizes + win_prizes + treasures
    save_state(phone, {
        "phone": phone, "seq": seq, "total": total, "dice": dice,
        "can_roll": can_roll, "treasure_claim_required": claim_req,
        "tasks": [{"code": t.get("taskCode"), "name": t.get("taskName"),
                   "done": str(t.get("status")) == "1", "dice": t.get("rewardDice")}
                  for t in tasks],
        "last_prizes": all_win[-10:],
    }, rolls=rolls, prizes=len(all_win))

    print(f"  小计：投 {rolls} 次，中 {len(all_win)} 个"
          + (f"（{'、'.join(all_win)}）" if all_win else ""))
    return {"tag": tag, "phone": phone, "seq": seq, "total": total, "dice": dice,
            "rolls": rolls, "prizes": len(all_win), "wins": all_win}


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="环闽寻宝记")
    ap.add_argument("--key", default=None, help="只跑某个 configs_fj 配置，如 fj2")
    ap.add_argument("--dry-run", action="store_true", help="只查进度，不投骰子")
    ap.add_argument("--tasks", action="store_true",
                    help="上报跳转类任务换骰子（不含好友助力）")
    ap.add_argument("--did-xxl", action="store_true",
                    help="本轮真跑过消消乐，允许上报「参与一起消消乐」任务")
    ap.add_argument("--did-luck", action="store_true",
                    help="本轮真抽过天天大转盘，允许上报「参与天天大转盘」任务")
    ap.add_argument("--max", type=int, default=20, help="每号本次最多投几次，默认 20")
    ap.add_argument("--invite", action="store_true", help="只生成好友助力链接，不投骰子")
    ap.add_argument("--mutual-assist", action="store_true",
                    help="多号互相点好友助力换骰子（同一号对每月一次）")
    args = ap.parse_args()

    if args.mutual_assist:
        result = mutual_assist(dry_run=args.dry_run)
        if args.dry_run:
            print("\n（--dry-run，未真点助力）")
        return 1 if result["expired"] else 0

    targets = collect_targets(args.key)
    if not targets:
        print("configs_fj 下没有可用 bearer_token。请先导入抓包。")
        return 1
    print(f"待处理 {len(targets)} 个账号：" + "、".join(f"{t[0]}({t[1][-4:]})" for t in targets))

    results = []
    for tag, phone, tok in targets:
        try:
            results.append(run_one(tag, phone, tok, args))
        except Exception as e:
            print(f"  [{tag}] 异常：{e}")
            results.append({"tag": tag, "skip": True})

    print("\n" + "=" * 62)
    print(" 汇总")
    print("=" * 62)
    for r in results:
        if r.get("invite"):
            print(f"  {'✓' if not r.get('skip') else '✗'} {r['tag']}  助力链接"
                  f"{'已生成' if not r.get('skip') else '生成失败'}")
        elif r.get("skip"):
            print(f"  ✗ {r['tag']}  跳过/失败")
        else:
            print(f"  ✓ {r['tag']}  {mask_phone(r.get('phone'))}  "
                  f"第 {r.get('seq')}/{r.get('total')} 格  骰子 {r.get('dice')}  "
                  f"本次投 {r.get('rolls')} 中 {r.get('prizes')}")
    if args.dry_run:
        print("\n（--dry-run，未投骰子）")
    return 1 if any(r.get("skip") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
