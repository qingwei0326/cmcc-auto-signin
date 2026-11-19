"""
cmcc_fj.py —— 福建移动 BASS 活动平台自动化

覆盖：养成游戏签到 / 每日奖励 / 阶段奖励、流量银行任务与兑换、红包宝。
全部 GET 请求，无签名，只需 Bearer token + appId + 13 位时间戳。

用法：
    python cmcc_fj.py --dry-run      只查状态，不做任何改动（先跑这个）
    python cmcc_fj.py                签到 + 领每日奖励
    python cmcc_fj.py --claim        额外领取阶段奖励
    python cmcc_fj.py --roll         额外掷骰子（消耗次数）
    python cmcc_fj.py --delay 900    执行前随机等待 0~900 秒
"""

import argparse
import datetime
import json
import random
import sys
import time
from pathlib import Path

import requests

from common import atomic_write_json, load_json

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config_fj.json"

HOST = "https://wap.fj.10086.cn"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148/wkwebview")

EP = {
    # 养成游戏
    "index_info":     "/bass-cultivateGameServer/index/getIndexInfo",
    "sign_info":      "/bass-cultivateGameServer/sign/getSignInfo",
    "do_sign":        "/bass-cultivateGameServer/sign/doSign",
    "daily_list":     "/bass-cultivateGameServer/index/getDailyReceiveTask",
    "daily_receive":  "/bass-cultivateGameServer/index/dailyReceive",
    "stage_info":     "/bass-cultivateGameServer/stage/getUserStageInfo",
    "stage_prizes":   "/bass-cultivateGameServer/stage/getStagePrizes",
    "stage_receive":  "/bass-cultivateGameServer/stage/receiveStagePrize",
    "batch_feed":     "/bass-cultivateGameServer/stage/batchFeed",
    "lucky_bag":      "/bass-cultivateGameServer/luckyBag/nearTrigger",
    "task_list":      "/bass-cultivateGameServer/task/getUserTasks",
    # 红包宝
    "hmxb_init":      "/bass-commonAreaServer/H5Api/hmxb/activity/init",
    "hmxb_balance":   "/bass-commonAreaServer/H5Api/hmxb/dice/balance",
    "hmxb_roll":      "/bass-commonAreaServer/H5Api/hmxb/dice/roll",
    "hmxb_open":      "/bass-commonAreaServer/H5Api/hmxb/treasure/open",
    # 流量银行
    "fb_index":       "/bass-commonActServer/flowBank/index",
    "fb_tasks":       "/bass-commonActServer/flowBank/taskInfo",
    "fb_do_task":     "/bass-commonActServer/flowBank/doTask",
}


def now_ms():
    return str(int(time.time() * 1000))


def load_config(path: Path):
    if not path.exists():
        print(f"缺少配置文件：{path}")
        sys.exit(1)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    tk = cfg.get("bearer_token", "")
    if not tk or "把你的" in tk or tk.lower().startswith("bearer"):
        print(f"{path.name} 的 bearer_token 还没填对。")
        print("从抓包任一 wap.fj.10086.cn 请求的 Authorization 头取 Bearer 后面那串。")
        sys.exit(1)
    return cfg


def new_session(cfg):
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "Authorization": f"Bearer {cfg['bearer_token']}",
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "Referer": cfg.get("referer") or f"{HOST}/bass-cultivateGameServer/",
    })
    return s


def call(s, cfg, key, retries=1, extra=None):
    """GET 调用，返回 {ok, code, msg, data}；对限流做一次重试。"""
    url = HOST + EP[key]
    params = {"appId": cfg.get("app_id", "0"), "timestamp": now_ms()}
    if extra:
        params.update(extra)
    last = None
    for attempt in range(retries + 1):
        try:
            r = s.get(url, params=params, timeout=25)
            if r.status_code == 401:
                return {"ok": False, "code": 401,
                        "msg": "token 失效，需重新抓包更新 bearer_token", "data": None}
            if r.status_code != 200:
                last = {"ok": False, "code": r.status_code, "msg": f"HTTP {r.status_code}", "data": None}
            else:
                j = r.json()
                ok = bool(j.get("success")) or str(j.get("code")) in ("0", "0000", "200")
                res = {"ok": ok, "code": j.get("code"), "msg": j.get("msg"), "data": j.get("data")}
                # -100 = 限流，稍等重试
                if not ok and str(j.get("code")) == "-100" and attempt < retries:
                    time.sleep(2.5)
                    continue
                return res
        except Exception as e:
            last = {"ok": False, "code": -1, "msg": f"网络异常: {e}", "data": None}
        if attempt < retries:
            time.sleep(1.5)
    return last


def call_post(s, cfg, path, retries=1, extra=None):
    """POST 调用（task/doTask 用），body = {appId, timestamp} + extra。

    extra 可加 taskId 等字段。对 1006（操作太快）做退避重试。
    """
    url = HOST + path
    body = {"appId": int(cfg.get("app_id", "0") or 0), "timestamp": int(now_ms())}
    if extra:
        body.update(extra)
    last = None
    for attempt in range(retries + 1):
        try:
            r = s.post(url, json=body, timeout=25)
            if r.status_code == 401:
                return {"ok": False, "code": 401, "msg": "token 失效", "data": None}
            j = r.json()
            ok = bool(j.get("success")) or str(j.get("code")) in ("0", "0000", "200")
            res = {"ok": ok, "code": j.get("code"), "msg": j.get("msg"), "data": j.get("data")}
            # -100 限流 / 1006 操作太快：退避重试
            if not ok and str(j.get("code")) in ("-100", "1006") and attempt < retries:
                time.sleep(3.5)
                body["timestamp"] = int(now_ms())  # 重试时刷新时间戳
                continue
            return res
        except Exception as e:
            last = {"ok": False, "code": -1, "msg": f"网络异常: {e}", "data": None}
        if attempt < retries:
            time.sleep(1.5)
    return last


def pause(s=1.0, jitter=1.2):
    time.sleep(s + random.random() * jitter)


# 阶段奖励每阶固定 3 档：1=话费券 2=流量券 3=折扣券
PRIZE_KIND = {"fee": 1, "flow": 2, "discount": 3}


def prize_id_for(stage, kind):
    """按 阶段号 + 档位 算出奖品 id，例如 1阶流量 = (1-1)*3+2 = 2。"""
    off = PRIZE_KIND.get(kind, 1)
    return (max(int(stage or 1), 1) - 1) * 3 + off


FB_STATE = BASE / "gui_data" / "flowbank_state.json"
DAILY_STATE = BASE / "gui_data" / "daily_state.json"
CYCLE_NAME = {1: "每月一次", 2: "每周一次", 0: "不限次"}


def track_daily(key, daily_list=None, lucky=None, sign=None):
    """记录每日三档奖励（150/250/350 饲料）、连续签到与福袋进度，供控制台展示。

    每日奖励靠 getDailyReceiveTask：currentTask=True 表示当前该领那档，
    taskStatus 只有 RECEIVED / UN_DO，没有 COMPLETED。
    签到靠 sign/getSignInfo：signBeginHour=6 才开放，signDateList 给连续 N 天的
    累进奖励（实测 600 → 900 → 1200g），断签会重置回第一天。
    福袋靠 luckyBag/nearTrigger：每累计喂食 nextTriggerCount(300) 次触发一次。
    """
    try:
        DAILY_STATE.parent.mkdir(parents=True, exist_ok=True)
        st = json.loads(DAILY_STATE.read_text(encoding="utf-8")) if DAILY_STATE.exists() else {}
    except Exception:
        st = {}
    acc = st.setdefault(key, {})
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")

    if daily_list is not None:
        old = {t.get("name"): t for t in (acc.get("tiers") or [])}
        tiers = []
        for t in daily_list:
            li = t.get("limitInfo") or {}
            name = str(t.get("taskName") or "")
            status = str(t.get("taskStatus") or "").upper()
            prev = old.get(name) or {}
            at = prev.get("at")
            if status == "RECEIVED" and not at:
                # 第一次观测到"已领"，用当前时间兜底
                at = stamp
            if status != "RECEIVED":
                at = None
            tiers.append({
                "name": name,
                "fodder": t.get("fodder"),
                "status": status,
                "current": bool(t.get("currentTask")),
                "interval": li.get("intervalMinutes"),
                "at": at,
                # 服务端直接给出"这档什么时候可领"，比自己按 interval 推算准
                "limit_at": (str(t.get("limitReceiveTime") or "")[11:16] or None)
                            if t.get("currentTask") else None,
            })
        acc["tiers"] = tiers
        acc["tiers_date"] = now.strftime("%Y-%m-%d")

    if lucky is not None:
        acc["lucky"] = {
            "current": lucky.get("currentFeedCount"),
            "need": lucky.get("nextTriggerCount"),
            "more": lucky.get("needMoreFeeds"),
            "triggered": bool(lucky.get("isNearTrigger")),
            "at": stamp,
        }

    if sign is not None:
        acc["sign"] = {
            "today": bool(sign.get("todaySign")),
            "open": bool(sign.get("openSignTime")),
            "begin_hour": sign.get("signBeginHour"),
            "cycle": sign.get("cycleToday"),
            "today_fodder": sign.get("today_fodder"),
            "next_fodder": sign.get("next_fodder"),
            "at": stamp,
        }

    acc["updated"] = stamp
    try:
        atomic_write_json(DAILY_STATE, st)
    except Exception:
        pass


def daily_nothing_to_do(key):
    """本地记录显示今天三档奖励已领完、签到也完成 → 本轮无事可做。

    每小时的轮询一天要跑 17 次，而三档通常早上 8 点前就领完了，
    后面十几次全是空转（每次还要调 4 个接口）。这里直接短路，
    只看本地状态文件，不再发任何请求。
    """
    acc = (load_json(DAILY_STATE, {}) or {}).get(key) or {}
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    if acc.get("tiers_date") != today:
        return False
    tiers = acc.get("tiers") or []
    if not tiers or any(str(t.get("status")).upper() != "RECEIVED" for t in tiers):
        return False
    return bool((acc.get("sign") or {}).get("today"))


def track_fb_tasks(key, tasks):
    """记录流量银行任务的周期/奖励/上次完成时间，供控制台展示。

    服务端只给 completeStatus，不返回"上次完成时间"，所以靠自己观测：
      · 第一次看到完成（或从「未做」变完成）→ 记 last_done = 现在
      · 看到「未做」→ 说明周期已重置，清空 last_done
    这样跑几次之后就能反推出真实的周期起点。
    """
    p = FB_STATE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        st = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        st = {}
    acc = st.setdefault(key, {})
    rec = acc.setdefault("tasks", {})
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    for t in tasks:
        tid = str(t.get("id"))
        old = rec.get(tid) or {}
        status = str(t.get("completeStatus"))
        row = {
            "title": t.get("taskTitle") or "",
            "cycle": int(t.get("taskCycle") or 0),
            "flow": str(t.get("taskFlow") or ""),
            "unit": t.get("taskFlowUnit") or "M",
            "status": status,
            "last_seen": stamp,
        }
        if status in ("1", "2"):
            if old.get("status") in (None, "0") or not old.get("last_done"):
                row["last_done"] = stamp
            else:
                row["last_done"] = old.get("last_done")
        else:
            row["last_done"] = None
        rec[tid] = row

    acc["updated"] = stamp
    try:
        atomic_write_json(p, st)
    except Exception:
        pass


def receive_stage_prize(s, cfg, kind):
    """领阶段奖励，带上 prizeId 指定档位；返回 (resp, prizeId, prizeName)。"""
    st = call(s, cfg, "stage_info")
    stage = ((st.get("data") or {}).get("stageInfo") or {}).get("stage") or 1
    pid = prize_id_for(stage, kind)
    r = call(s, cfg, "stage_receive", extra={"prizeId": pid})
    name = ""
    if isinstance(r.get("data"), dict):
        name = (r["data"].get("prizeDto") or {}).get("prizeName") or ""
    return r, pid, name


def show(label, r, extra=""):
    flag = "OK " if r["ok"] else "!! "
    print(f"  {flag}{label:<22} code={r['code']}  {r['msg'] or ''}{extra}")
    return r["ok"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只查状态，不做改动")
    ap.add_argument("--claim", action="store_true", help="额外领取阶段奖励")
    ap.add_argument("--roll", action="store_true", help="额外掷骰子（消耗次数）")
    ap.add_argument("--feed", type=int, default=0,
                    help="喂养次数（每次耗 250 饲料、推进 250 阶段进度），0=不喂")
    ap.add_argument("--tasks", action="store_true",
                    help="完成 UN_DO 的 VISIT 类任务（等价于空报浏览，默认关闭）")
    ap.add_argument("--fb-tasks", action="store_true",
                    help="完成流量银行的浏览任务（空报浏览，默认关闭）")
    ap.add_argument("--daily-only", action="store_true",
                    help="只补签到 + 领每日奖励三档（跳过喂养/流量银行），用于定时轮询，10 秒内结束")
    ap.add_argument("--delay", type=int, default=0, help="执行前随机等待上限（秒）")
    ap.add_argument("--prize", choices=list(PRIZE_KIND), default=None,
                    help="阶段奖励档位：fee=话费券 flow=流量券 discount=折扣券"
                         "（不传则读配置 prize_prefer，再不行默认 fee）")
    ap.add_argument("--auto-claim", action="store_true",
                    help="阶段将满时自动领奖并继续喂；不加则停手等人工（便于抓包）")
    ap.add_argument("--config", default="config_fj.json", help="配置文件路径")
    args = ap.parse_args()

    cfg = load_config(BASE / args.config)
    key = Path(args.config).stem
    prize_kind = args.prize or cfg.get("prize_prefer") or "fee"
    if prize_kind not in PRIZE_KIND:
        print(f"未知奖品档位 {prize_kind}，可选：{'/'.join(PRIZE_KIND)}")
        return 1
    if args.delay > 0 and not args.dry_run:
        w = random.randint(0, args.delay)
        print(f"随机等待 {w} 秒 ...")
        time.sleep(w)

    s = new_session(cfg)
    print(f"目标 {HOST}  appId={cfg.get('app_id', '0')}")

    # 轮询短路：今天该领的都领完了，就不必再调接口
    if args.daily_only and not args.dry_run and daily_nothing_to_do(key):
        print("  今日三档奖励已领完、签到也已完成，本轮无事可做（1 秒退出）。")
        return 0

    # ---------- 状态总览 ----------
    print("\n[状态]")
    info = call(s, cfg, "index_info")
    if info["ok"] and isinstance(info["data"], dict):
        d = info["data"]
        print(f"  饲料 {d.get('fodderSurplus')}  今日已喂 {d.get('feedCountToday')} 次")
    else:
        show("首页信息", info)

    pause()
    si = call(s, cfg, "sign_info")
    signed = False
    sign_open = False
    sign_data = None
    if si["ok"] and isinstance(si["data"], dict):
        d = si["data"]
        signed = str(d.get("todaySign")) == "True"
        sign_open = str(d.get("openSignTime")) == "True"
        # signDateList 按日期升序给连续签到的累进奖励（600 → 900 → 1200g）
        today_g = next_g = None
        for it in (d.get("signDateList") or []):
            if it.get("today"):
                today_g = it.get("fodder")
            elif today_g is not None and next_g is None:
                next_g = it.get("fodder")
        sign_data = {**d, "today_fodder": today_g, "next_fodder": next_g}
        extra = ""
        if today_g:
            extra = f"  今日 {today_g}g"
            if next_g:
                extra += f" · 明日 {next_g}g"
        print(f"  签到：今日{'已签' if signed else '未签'}  连签第 {d.get('cycleToday')} 天  "
              f"{'可签' if sign_open else '未到开放时间(%s点)' % (d.get('signBeginHour') or 6)}{extra}")
    else:
        show("签到状态", si)
        print("  状态查询失败，终止以免误操作。")
        return 1

    pause()
    dl = call(s, cfg, "daily_list")
    claimable = 0
    if dl["ok"] and isinstance(dl["data"], list):
        # currentTask=True 才是"该领的那档"；taskStatus 只有 RECEIVED / UN_DO
        for t in dl["data"]:
            if t.get("currentTask") and str(t.get("taskStatus")).upper() != "RECEIVED":
                claimable += 1
        got = sum(1 for t in dl["data"] if str(t.get("taskStatus")).upper() == "RECEIVED")
        kinds = "/".join(str(t.get("fodder")) for t in dl["data"])
        print(f"  每日奖励：{len(dl['data'])} 档（{kinds} 饲料）已领 {got} 档，{claimable} 档待领")
    else:
        print("  每日奖励：查询失败")

    pause()
    lb = call(s, cfg, "lucky_bag")
    lucky_data = None
    if lb["ok"] and isinstance(lb["data"], dict):
        d = lb["data"]
        lucky_data = d
        flag = "  ⚑ 已触发可开！" if d.get("isNearTrigger") else ""
        print(f"  福袋：{d.get('currentFeedCount')}/{d.get('nextTriggerCount')}"
              f"  还差 {d.get('needMoreFeeds')} 次喂食{flag}")
    track_daily(key, dl["data"] if dl["ok"] and isinstance(dl["data"], list) else None,
                lucky_data, sign_data)

    pause()
    st = call(s, cfg, "stage_info")
    if st["ok"] and isinstance(st["data"], dict):
        d = st["data"]
        si2 = d.get("stageInfo") or {}
        print(f"  阶段：{si2.get('stageName')}  饲料 {d.get('feedFodders')}/{si2.get('feedNeedFodderCount')}")

    pause()
    fb = call(s, cfg, "fb_index")
    if fb["ok"] and isinstance(fb["data"], dict):
        d = fb["data"]
        print(f"  流量银行：可兑 {d.get('exchangeFlow')}{d.get('exchangeFlowUnit')}"
              f"（{d.get('exchangeFlowValue')} 点）  有待做任务={d.get('hasTaskToDo')}")

    if args.dry_run:
        print("\n（--dry-run，到此为止，未做任何改动）")
        return 0

    # ---------- 签到 ----------
    # 签到 6 点才开放，连签奖励是累进的（实测 600 → 900 → 1200g），断一天就重置回第一天。
    # 所以每小时轮询（--daily-only）也必须补签，不能只赌 08:30 那一次开机。
    print("\n[执行]")
    if signed:
        print("  -  今日已签到，跳过")
    elif not sign_open:
        print(f"  -  未到签到开放时间（{(sign_data or {}).get('signBeginHour') or 6} 点后自动补签）")
    else:
        pause()
        r = call(s, cfg, "do_sign")
        if show("签到", r):
            if isinstance(r["data"], dict) and r["data"].get("fodder"):
                print(f"     获得饲料 {r['data'].get('fodder')}")
            signed = True
            if sign_data is not None:
                sign_data["todaySign"] = True

    # ---------- 领每日奖励 ----------
    if claimable > 0:
        pause()
        r = call(s, cfg, "daily_receive")
        if show("领每日奖励", r):
            pause()
            dl2 = call(s, cfg, "daily_list")
            if dl2["ok"] and isinstance(dl2["data"], list):
                got2 = sum(1 for t in dl2["data"] if str(t.get("taskStatus")).upper() == "RECEIVED")
                track_daily(key, dl2["data"], None)
                nxt = next((t for t in dl2["data"]
                            if t.get("currentTask") and str(t.get("taskStatus")).upper() != "RECEIVED"), None)
                tip = f"，下一档 {nxt.get('fodder')} 饲料（隔 {((nxt.get('limitInfo') or {}).get('intervalMinutes'))} 分钟）" if nxt else ""
                print(f"     本日已领 {got2}/{len(dl2['data'])} 档{tip}")
        elif str(r.get("code")) == "1504":
            print(f"  -  {r.get('msg') or '还未到可领取时间'}（两档之间需间隔 60 分钟）")
    else:
        print("  -  当前没有待领档位（今日三档已领完，或未开启）")

    if args.daily_only:
        # 补签成功后要回写一次，否则面板上仍显示"未签"
        if sign_data is not None:
            track_daily(key, None, None, sign_data)
        print("\n[daily-only] 结束。")
        return 0

    # ---------- 可选：完成 VISIT 任务 ----------
    # 抓包证实：任务完成只需 POST /task/doTask/{id}，body={appId,timestamp}，
    # 无停留时长/页面 token 等校验。等价于跳过真实浏览空报完成，故默认关闭。
    if args.tasks:
        pause()
        tl = call(s, cfg, "task_list")
        if tl["ok"] and isinstance(tl["data"], list):
            targets = [t for t in tl["data"]
                       if str(t.get("taskStatus")) == "UN_DO"
                       and t.get("completeType") in ("VISIT", "APP_VISIT")]
            if not targets:
                print("  -  没有 UN_DO 的 VISIT 类任务")
            for t in targets:
                tid = t.get("id")
                pause(1.2, 1.0)
                r = call_post(s, cfg, f"/bass-cultivateGameServer/task/doTask/{tid}")
                show(f"doTask/{tid} {t.get('taskName')}", r)
        else:
            show("任务列表", tl)

    # ---------- 可选：喂养 ----------
    # 抓包证实：GET /stage/batchFeed，无 body，一次耗 250 饲料 / 推进 250 阶段进度。
    # 这是游戏内正常的资源消耗动作，非伪造行为。
    if args.feed > 0:
        done = 0
        guard = 0
        while done < args.feed and guard < args.feed * 3:
            guard += 1
            cur = call(s, cfg, "index_info")
            fodder = int(((cur.get("data") or {}).get("fodderSurplus") or 0))
            if fodder < 250:
                print(f"  -  饲料不足 250（当前 {fodder}），停止喂养")
                break
            # 阶段将满保护：这一喂会让进度打满时，未开 --auto-claim 就停手
            if not args.auto_claim:
                stc = call(s, cfg, "stage_info")
                sd = stc.get("data") or {}
                prog = int(sd.get("feedFodders") or 0)
                need = int((sd.get("stageInfo") or {}).get("feedNeedFodderCount") or 5000)
                if prog > 0 and prog + 250 >= need:
                    print(f"  !  再喂一次阶段就满（{prog}/{need}），已停手。")
                    print(f"     去 App 手动喂这一手、顺手抓包看『选奖品』真实接口；")
                    print(f"     若信任当前接口可加 --auto-claim 让脚本自动领奖续喂。")
                    break
            pause(1.2, 1.0)
            r = call(s, cfg, "batch_feed")
            if show(f"喂养 {done+1}/{args.feed}", r):
                done += 1
                continue
            # 133 = 阶段进度已满，必须先领阶段奖励才能继续喂（不消耗次数）
            if str(r.get("code")) == "133":
                pause()
                rp, pid, pname = receive_stage_prize(s, cfg, prize_kind)
                if show(f"领阶段奖励(prizeId={pid})", rp):
                    print(f"     领到：{pname or '接口未返回奖品名'}")
                    continue
                # 带 prizeId 不被服务端接受时，退回默认档，至少不卡住
                print("     -  指定档位失败，退回默认档重试")
                pause()
                rp2, _pid2, pname2 = receive_stage_prize(s, cfg, "fee")
                if show("领阶段奖励(默认档)", rp2):
                    print(f"     领到：{pname2 or '接口未返回奖品名'}")
                    continue
                print("  -  领阶段奖励失败，停止喂养")
                break
            break
        # 喂完重新查阶段进度
        st2 = call(s, cfg, "stage_info")
        if st2["ok"] and isinstance(st2["data"], dict):
            d2 = st2["data"]
            si2 = d2.get("stageInfo") or {}
            print(f"     阶段现为 {si2.get('stageName')}  饲料 {d2.get('feedFodders')}/{si2.get('feedNeedFodderCount')}")
        # 喂养会推进福袋进度，顺手刷新
        if done:
            pause(0.6, 0.4)
            lb2 = call(s, cfg, "lucky_bag")
            if lb2["ok"] and isinstance(lb2["data"], dict):
                d2l = lb2["data"]
                flag = "  ⚑ 已触发可开！" if d2l.get("isNearTrigger") else ""
                print(f"     福袋：{d2l.get('currentFeedCount')}/{d2l.get('nextTriggerCount')}"
                      f"  还差 {d2l.get('needMoreFeeds')} 次喂食{flag}")
                track_daily(key, None, d2l)

    # ---------- 可选：流量银行浏览任务 ----------
    # 抓包证实：POST /flowBank/doTask body={appId,timestamp,taskId}，无签名无停留校验。
    # completeStatus: 0=未做 2=进行中 1=已完成。每个任务要点两次（0→2→1）。
    # 有 1006 频控，需间隔。等价于空报浏览，默认关闭。
    if args.fb_tasks:
        pause()
        tl = call(s, cfg, "fb_tasks")
        if tl["ok"] and isinstance(tl["data"], list):
            track_fb_tasks(Path(args.config).stem, tl["data"])
            # completeStatus: 0=未做（要点两次 0→2→1）  2=进行中（再点一次即完成）  1=已完成
            todo = [t for t in tl["data"] if str(t.get("completeStatus")) in ("0", "2")]
            if not todo:
                print("  -  流量银行没有待做任务")
            for t in todo:
                tid, title = t.get("id"), t.get("taskTitle")
                rounds = (1, 2) if str(t.get("completeStatus")) == "0" else (1,)
                for round_no in rounds:
                    time.sleep(random.uniform(3.5, 5.5))  # 避开 1006 频控
                    r = call_post(s, cfg, EP["fb_do_task"], retries=3,
                                  extra={"taskId": tid})
                    show(f"fb任务 {tid} {title} ({round_no}/{len(rounds)})", r)
                    if not r["ok"] and str(r.get("code")) == "1006":
                        print("     -  频控未过，跳过该轮")
        else:
            show("流量银行任务列表", tl)
        # 重新查可兑流量
        time.sleep(1)
        fb2 = call(s, cfg, "fb_index")
        if fb2["ok"] and isinstance(fb2["data"], dict):
            d2 = fb2["data"]
            print(f"     流量银行现可兑 {d2.get('exchangeFlow')}{d2.get('exchangeFlowUnit')}"
                  f"（{d2.get('exchangeFlowValue')} 点）")

    # ---------- 可选：阶段奖励 ----------
    # 抓包/实测：GET /stage/receiveStagePrize 不带参数即领"默认档"奖励，
    # 成功后 feedFodders 归零、status 回到 0，可继续喂养。
    if args.claim:
        pause()
        stc = call(s, cfg, "stage_info")
        status = str(((stc.get("data") or {}).get("status")))
        if status == "1":
            r, pid, pname = receive_stage_prize(s, cfg, prize_kind)
            if show(f"领阶段奖励(prizeId={pid})", r):
                print(f"     领到：{pname or '接口未返回奖品名'}")
            else:
                pause()
                r2c, _p, pname2 = receive_stage_prize(s, cfg, "fee")
                if show("领阶段奖励(默认档)", r2c):
                    print(f"     领到：{pname2 or '接口未返回奖品名'}")
        else:
            fodders = ((stc.get("data") or {}).get("feedFodders"))
            print(f"  -  阶段未满（{fodders}），暂无可领奖励")
        pause()
        r2 = call(s, cfg, "stage_prizes")
        if r2["ok"] and isinstance(r2["data"], list):
            print("  奖品池：")
            for item in r2["data"]:
                names = "、".join(f"{x.get('prizeName')}" for x in (item.get("prizeList") or []))
                print(f"     第{item.get('stage')}阶：{names}")

    # ---------- 可选：红包宝掷骰子 ----------
    if args.roll:
        pause()
        bal = call(s, cfg, "hmxb_balance")
        if bal["ok"] and isinstance(bal["data"], dict):
            n = int(bal["data"].get("balance") or 0)
            print(f"  -  骰子余额 {n}")
            for i in range(n):
                pause(1.5, 1.5)
                r = call(s, cfg, "hmxb_roll")
                if not show(f"掷骰子 {i+1}/{n}", r):
                    break
                if isinstance(r["data"], dict):
                    print(f"     点数 {r['data'].get('dicePoint')} → 第 {r['data'].get('finalSeq')} 格")
                    if str(r["data"].get("treasureClaimRequired")) == "True":
                        pause(1.2, 0.8)
                        orr = call(s, cfg, "hmxb_open")
                        show("开宝箱", orr)
        else:
            show("骰子余额", bal)

    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
