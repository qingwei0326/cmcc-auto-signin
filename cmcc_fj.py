"""
cmcc_fj.py —— 福建移动 BASS 活动平台自动化

覆盖：养成游戏签到 / 每日奖励 / 阶段奖励、流量银行任务与兑换、红包宝。
全部 GET 请求，无签名，只需 Bearer token + appId + 13 位时间戳。

用法：
    python cmcc_fj.py --dry-run      只查状态，不做任何改动（先跑这个）
    python cmcc_fj.py                签到 + 领每日奖励和昨日投喂奖励
    python cmcc_fj.py --claim        额外领取阶段奖励
    python cmcc_fj.py --roll         额外掷骰子（消耗次数）
    python cmcc_fj.py --delay 900    执行前随机等待 0~900 秒
"""

import argparse
import datetime
import random
import re
import sys
import time
from pathlib import Path

from common import (api_get, api_post, app_base, emit_result, file_lock,
                    load_config as _load_config, load_json, make_session,
                    now_ms, record_award, setup_utf8, show, update_json)

setup_utf8()

BASE = app_base()
CONFIG_PATH = BASE / "config_fj.json"

HOST = "https://wap.fj.10086.cn"

EP = {
    # 养成游戏
    "index_info":     "/bass-cultivateGameServer/index/getIndexInfo",
    "sign_info":      "/bass-cultivateGameServer/sign/getSignInfo",
    "do_sign":        "/bass-cultivateGameServer/sign/doSign",
    "daily_list":     "/bass-cultivateGameServer/index/getDailyReceiveTask",
    "daily_receive":  "/bass-cultivateGameServer/index/dailyReceive",
    "next_receive":   "/bass-cultivateGameServer/index/getNextReceiveTask",
    "feed_reward_claim": "/bass-cultivateGameServer/index/nextReceive",
    # 2026-11-24 18:16 抓包新发现：喂养累计的「轮次任务」（独立于每日三档 nextReceive）。
    # 某轮 taskStatus=COMPLETED 后，doMeet 领掉本轮饲料并解锁下一轮（LOCK→UN_DO）。
    # 新人号才有这条链，老号调用 getMeetTask 会返回 1606「非新人」。
    "meet_task":      "/bass-cultivateGameServer/index/getMeetTask",
    "do_meet":        "/bass-cultivateGameServer/index/doMeet",
    "stage_info":     "/bass-cultivateGameServer/stage/getUserStageInfo",
    "stage_prizes":   "/bass-cultivateGameServer/stage/getStagePrizes",
    "stage_receive":  "/bass-cultivateGameServer/stage/receiveStagePrize",
    "batch_feed":     "/bass-cultivateGameServer/stage/batchFeed",
    # 2026-11-20 12:36 抓包新发现：阶段满时「继续喂养」= 放弃本阶奖品直接升下一阶
    "continuous_stage": "/bass-cultivateGameServer/stage/continuousStage",
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


# 「券来世界」= 养成游戏的 H5 入口（App 首页那个图标打开的就是它）。
# 养成任务 id=24「APP首页访问活动」要求的就是「从 App 首页打开它」，
# 打开后上报 doTask/24 就能拿 500 饲料（DAILY×1）。
COUPON_WORLD_URL = "https://wap.fj.10086.cn/bass-feedPrizeH5/?ch=jituan_02"
COUPON_WORLD_KEY = "feedPrizeH5"          # 任务链接里认这个片段


def task_url(task: dict) -> str:
    """取任务要访问的页面地址：优先 app 直链，其次 h5 跳转型。"""
    urls = task.get("taskUrls") or []
    for u in urls:
        if u.get("type") == "app" and u.get("url"):
            return str(u["url"])
    for u in urls:
        if u.get("url"):
            return str(u["url"])
    return ""


def is_coupon_world(task: dict) -> bool:
    return COUPON_WORLD_KEY in task_url(task) or "券来世界" in str(task.get("taskName") or "")


def find_coupon_world_task(s, cfg):
    """任务列表里找「券来世界」那个待完成任务。

    为什么要先查：每小时的轮询有「今天都做完了就 1 秒退出」的短路，
    券来世界要是还没做，短路会把它一起跳过去。查一次（1 个请求）再决定。
    返回 (任务 or None, 任务列表原始结果)。
    """
    tl = call(s, cfg, "task_list")
    if not (tl.get("ok") and isinstance(tl.get("data"), list)):
        return None, tl
    for t in tl["data"]:
        if str(t.get("taskStatus")) == "UN_DO" and is_coupon_world(t):
            return t, tl
    return None, tl


def visit_page(s, url: str, timeout=15) -> bool:
    """真的把页面打开一次。

    服务端其实不校验有没有访问过（doTask 直接就成功），但既然要做这个任务，
    就按它的本来意思先把页面拉起来：券来世界是 SPA，只取到 index.html 即可。
    """
    if not url:
        return False
    try:
        r = s.get(url, timeout=timeout, headers={"Referer": HOST + "/"})
        return r.status_code < 400
    except Exception:
        return False


def load_config(path: Path):
    """读 BASS 配置；bearer 必须是抓包取来的那串，不能是模板占位符。"""
    cfg = _load_config(path, required=("bearer_token",), template="config_fj.example.json")
    tk = str(cfg.get("bearer_token", ""))
    if "把你的" in tk or tk.lower().startswith("bearer"):
        print(f"{path.name} 的 bearer_token 还没填对。")
        print("从抓包任一 wap.fj.10086.cn 请求的 Authorization 头取 Bearer 后面那串。")
        sys.exit(1)
    return cfg


def new_session(cfg):
    return make_session(token=cfg["bearer_token"],
                        referer=cfg.get("referer") or f"{HOST}/bass-cultivateGameServer/")


def call(s, cfg, key, retries=1, extra=None):
    """GET 调用，返回 {ok, code, msg, data}；-100 限流退避重试。"""
    params = {"appId": cfg.get("app_id", "0"), "timestamp": now_ms()}
    if extra:
        params.update(extra)
    return api_get(s, HOST + EP[key], params=params, retries=retries)


def call_post(s, cfg, path, retries=1, extra=None):
    """POST 调用（task/doTask、flowBank/doTask），body = {appId, timestamp} + extra。

    -100 限流 / 1006 操作太快：退避重试并刷新时间戳（统一在 common.api_post 里做）。
    """
    body = {"appId": int(cfg.get("app_id", "0") or 0), "timestamp": int(now_ms())}
    if extra:
        body.update(extra)
    return api_post(s, HOST + path, body=body, retries=retries)


def pause(s=1.0, jitter=1.2):
    time.sleep(s + random.random() * jitter)


def _note_token(res, r):
    """接口回 401 就把结果里的 token 标成失效，供控制台亮红灯。"""
    if str((r or {}).get("code")) == "401":
        res["token"] = "expired"


def flow_mb_of(text):
    """奖品文案折算成 MB（「1.5GB」→1536）；话费券不计流量，与统计口径一致。"""
    text = str(text or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*GB", text, re.I)
    if m:
        return float(m.group(1)) * 1024
    m = re.search(r"(\d+(?:\.\d+)?)\s*MB", text, re.I)
    return float(m.group(1)) if m else 0.0


def _got_prize(res, name, cfg=None):
    """打印并登记领到的奖品：流量折算 MB，话费券只记名字。"""
    print(f"     领到：{name or '接口未返回奖品名'}")
    res["flow_mb"] += flow_mb_of(name)
    if name:
        res["last_reward"] = name
        # 领到的那一刻就记流水，控制台不用再靠日志文案反推
        record_award((cfg or {}).get("phone") or "", "养成领奖", name)


# 阶段奖励每阶固定 3 档：1=话费券 2=流量券 3=折扣券
PRIZE_KIND = {"fee": 1, "flow": 2, "discount": 3}
# 2026-11-24 用户实测口径修正：每一阶（含一阶）领奖都是随机开奖，
# 传 prizeId 服务端也按权重随机，不支持"领奖时选档"（当天一阶传 flow=2 仍领到话费券）。
# 六阶特殊：抽出来之后可以"换其他档位"，但那是抽后独立动作，换奖接口尚未抓到
# （见 docs/bass.md）。当前领奖一律走无参；prize_id_for/PRIZE_KIND 保留供六阶换奖用。
CHOOSABLE_STAGES = frozenset()   # 兼容旧引用：已无"领奖可选档"的阶段


def prize_id_for(stage, kind):
    """按 阶段号 + 档位 算奖品 id（实测池：一阶 1-3、二阶 4-6 … 六阶 16-18）。

    stage 是 getStagePrizes / getUserStageInfo 里那个 0 基序号：stage=0 是一阶
    （need 5000）、stage=1 二阶（10000）……stage=5 六阶。所以是 stage*3+off，
    不能再减 1——历史实现减了 1，一阶时被 max(stage,1) 兜住看不出来，
    从二阶起每次都在要上一阶的奖品。
    """
    off = PRIZE_KIND.get(kind, 1)
    return max(int(stage or 0), 0) * 3 + off


def _gui_path(name: str) -> Path:
    return BASE / "gui_data" / name


CYCLE_NAME = {1: "每月一次", 2: "每周一次", 0: "不限次"}


def feed_reward_state(s, cfg):
    """返回昨日投喂奖励及是否取得了明确状态。"""
    return _parse_feed_reward(call(s, cfg, "next_receive"))


def _parse_feed_reward(r):
    if not r.get("ok"):
        return None, str(r.get("code")) in ("1503", "1600")
    d = r.get("data")
    if not isinstance(d, dict):
        return None, True
    if str(d.get("taskStatus") or "").upper() != "COMPLETED":
        return None, True
    expire = str(d.get("expireTime") or "")
    if expire:
        try:
            if datetime.datetime.strptime(expire, "%Y-%m-%d %H:%M:%S") <= datetime.datetime.now():
                return None, True
        except ValueError:
            pass
    return {"name": str(d.get("taskName") or "喂养累计"),
            "fodder": d.get("fodder"),
            "record_id": str(d.get("recordId") or ""),
            "expire": expire,
            "from": str(d.get("limitReceiveTime") or "")}, True


def handle_feed_reward(s, cfg, key, res, dry_run):
    """查询并按需领取昨日投喂奖励，同一配置的查询和领取串行执行。"""
    lock_path = _gui_path("daily_state.json").with_name(f"feed_reward.{key}")
    try:
        with file_lock(lock_path):
            reply = call(s, cfg, "next_receive")
            pending, checked = _parse_feed_reward(reply)
            if not checked:
                print(f"  投喂累计奖励：暂时无法确认，保留上次记录"
                      f"（code={reply.get('code')} msg={reply.get('msg') or ''}）")
                return
            if pending is None:
                print("  投喂累计奖励：无待领")
            elif dry_run:
                print(f"  投喂累计奖励：{pending['fodder']} 饲料可领未领，{pending['expire']} 过期")
            else:
                # 成功响应的 data 为空。遇到超时，下一轮先查询状态再决定是否领取。
                reply = call(s, cfg, "feed_reward_claim", retries=0)
                _note_token(res, reply)
                if show("领取昨日投喂奖励", reply):
                    res["feed_reward_received"] = pending["fodder"]
                    print(f"     领到饲料 {pending['fodder']}")
                    pending = None
                elif str(reply.get("code")) == "1503":
                    pending = None
            res["feed_reward"] = pending
            track_daily(key, feed_reward=pending, feed_reward_checked=True)
    except TimeoutError:
        print("  投喂累计奖励：另一次任务正在处理")


def meet_state(s, cfg):
    """查喂养轮次任务，返回 (待领轮次, 是否确认)。

    仅新人号有这条链；老号 getMeetTask 回 1606「非新人」，视为无轮次、已确认。
    待领条件：当前轮且 taskStatus=COMPLETED（喂够 needFeeds 次）且未过 expireTime。
    """
    r = call(s, cfg, "meet_task")
    if not r.get("ok"):
        code = str(r.get("code"))
        # 1606=非新人（这条链不适用）；其余失败（含 401）交由调用方处理
        return None, code == "1606", r
    d = r.get("data")
    if not isinstance(d, dict):
        return None, True, r
    tasks = d.get("taskDtoList") or []
    for t in tasks:
        if str(t.get("taskStatus") or "").upper() != "COMPLETED":
            continue
        if not t.get("currentTask"):
            continue
        expire = str(t.get("expireTime") or "")
        if expire:
            try:
                if datetime.datetime.strptime(expire, "%Y-%m-%d %H:%M:%S") <= datetime.datetime.now():
                    return None, True, r
            except ValueError:
                pass
        return {"name": str(t.get("taskName") or "喂养轮次"),
                "fodder": t.get("fodder"),
                "id": t.get("id"),
                "expire": expire}, True, r
    return None, True, r


def handle_meet(s, cfg, key, res, dry_run):
    """喂养轮次：有 COMPLETED 轮次时 doMeet 领奖并解锁下一轮。

    与投喂累计奖励同属领奖动作，串行加锁避免多进程重复领。
    """
    lock_path = _gui_path("daily_state.json").with_name(f"meet.{key}")
    try:
        with file_lock(lock_path):
            pending, checked, probe = meet_state(s, cfg)
            _note_token(res, probe)
            if not checked:
                print("  喂养轮次：暂时无法确认，跳过")
                return
            if pending is None:
                return  # 无待领轮次（老号/未达标/已过期/已领完），静默
            if dry_run:
                print(f"  喂养轮次：{pending['name']} {pending['fodder']} 饲料待领"
                      f"（{pending['expire']} 过期），未领")
                return
            pause()
            reply = call(s, cfg, "do_meet", retries=0)
            _note_token(res, reply)
            if show(f"喂养轮次领奖({pending['name']})", reply):
                nxt = ((reply.get("data") or {}).get("taskName")) if isinstance(reply.get("data"), dict) else None
                tail = f"，解锁 {nxt}" if nxt and nxt != pending["name"] else ""
                print(f"     领到饲料 {pending['fodder']}{tail}")
                res["meet_received"] = pending["fodder"]
                res["meet_round"] = pending["name"]
    except TimeoutError:
        print("  喂养轮次：另一次任务正在处理")


def track_daily(key, daily_list=None, lucky=None, sign=None,
                feed_reward=None, feed_reward_checked=False):
    """记录每日三档奖励（150/250/350 饲料）、连续签到与福袋进度，供控制台展示。

    每日奖励靠 getDailyReceiveTask：currentTask=True 表示当前该领那档，
    taskStatus 只有 RECEIVED / UN_DO，没有 COMPLETED。
    签到靠 sign/getSignInfo：signBeginHour=6 才开放，signDateList 给连续 N 天的
    累进奖励（实测 600 → 900 → 1200g），断签会重置回第一天。
    福袋靠 luckyBag/nearTrigger：每累计喂食 nextTriggerCount(300) 次触发一次。

    整份文件是多个账号共用的，读-改-写必须加跨进程锁：run_all 默认并发跑号，
    计划任务与一键签到又是不同进程，不加锁会互相整份覆盖。
    """
    now = datetime.datetime.now()

    def mutate(st):
        _apply_daily(st.setdefault(key, {}), daily_list, lucky, sign, now,
                     feed_reward, feed_reward_checked)

    try:
        update_json(_gui_path("daily_state.json"), mutate)
    except Exception:
        pass


def _apply_daily(acc, daily_list, lucky, sign, now,
                 feed_reward=None, feed_reward_checked=False):
    """把一次观测结果合并进某账号的记录（就地修改 acc）。"""
    stamp = now.strftime("%Y-%m-%d %H:%M")

    if feed_reward_checked:
        acc["feed_reward"] = feed_reward
        acc["feed_reward_date"] = now.date().isoformat()

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


def daily_nothing_to_do(key, *, need_feed=False, need_tasks=False, need_fb_tasks=False):
    """签到、三档奖励和昨日投喂奖励都有当天的完成记录时跳过轮询。

    每小时的轮询一天要跑 17 次，而三档通常早上 8 点前就领完了，
    后面十几次全是空转（每次还要调 4 个接口）。这里直接短路，
    只看本地状态文件，不再发任何请求。
    若有喂养（need_feed）或当天未完成的浏览/流量银行任务，不能短路。
    """
    if need_feed:
        return False
    acc = (load_json(_gui_path("daily_state.json"), {}) or {}).get(key) or {}
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    if (acc.get("tiers_date") != today or acc.get("feed_reward_date") != today
            or acc.get("feed_reward")):
        return False
    tiers = acc.get("tiers") or []
    if not tiers or any(str(t.get("status")).upper() != "RECEIVED" for t in tiers):
        return False
    if not (acc.get("sign") or {}).get("today"):
        return False
    if need_tasks and acc.get("tasks_date") != today:
        return False
    if need_fb_tasks and acc.get("fb_tasks_date") != today:
        return False
    return True


def _daily_flag_done(key, field) -> bool:
    """今天是否已成功执行过指定分支（如 tasks_date / fb_tasks_date）。"""
    acc = (load_json(_gui_path("daily_state.json"), {}) or {}).get(key) or {}
    return acc.get(field) == datetime.date.today().isoformat()


def _mark_daily_flag(key, field) -> None:
    """记录当天已完成某分支，避免每小时轮询重复打接口。"""
    today = datetime.date.today().isoformat()
    try:
        update_json(_gui_path("daily_state.json"), lambda st: st.setdefault(key, {}).__setitem__(field, today))
    except Exception as e:
        print(f"  [警告] 每日标记写入失败（{field}）：{e}")


def track_fb_tasks(key, tasks):
    """记录流量银行任务的周期/奖励/上次完成时间，供控制台展示。

    服务端只给 completeStatus，不返回"上次完成时间"，所以靠自己观测：
      · 第一次看到完成（或从「未做」变完成）→ 记 last_done = 现在
      · 看到「未做」→ 说明周期已重置，清空 last_done
    这样跑几次之后就能反推出真实的周期起点。

    与 track_daily 同理：多账号共用一份文件，读-改-写要加跨进程锁。
    """
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    def mutate(st):
        _apply_fb(st.setdefault(key, {}), tasks, stamp)

    try:
        update_json(_gui_path("flowbank_state.json"), mutate)
    except Exception:
        pass


def _apply_fb(acc, tasks, stamp):
    """把流量银行任务的一次观测合并进某账号的记录（就地修改 acc）。"""
    previous = acc.get("tasks") or {}
    rec = {}

    for t in tasks:
        tid = str(t.get("id"))
        old = previous.get(tid) or {}
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

    acc["tasks"] = rec
    acc["updated"] = stamp


def fb_due(cycle, last_done, now=None):
    """周任务到周一、月任务到 1 号才进入新周期。

    服务端只返回「已完成 / 未做」，不给上次完成时间，所以用本地记录的
    last_done 判断。没有记录、格式异常或周期 0 一律视为可做（保守，宁可多试一次）。
    """
    try:
        cyc = int(cycle or 0)
    except (TypeError, ValueError):
        return True
    if cyc not in (1, 2) or not last_done:
        return True
    try:
        d = datetime.datetime.strptime(str(last_done)[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return True
    now = now or datetime.datetime.now()
    if cyc == 2:                      # 每周一次：本周一之后才算新周期
        monday = (now - datetime.timedelta(days=now.weekday())).date()
        return d.date() < monday
    return (d.year, d.month) < (now.year, now.month)   # 每月一次


def fb_due_tasks(tasks, records, now=None, force=False):
    """按周期筛出该做的任务，返回 (todo, skipped)。

    status=2（进行中）不受周期约束——做了一半要收尾，否则会一直挂着。
    force=True 时（--fb-all）忽略周期，全部尝试。
    """
    todo, skipped = [], []
    for t in tasks:
        st = str(t.get("completeStatus"))
        if st not in ("0", "2"):
            continue
        if st == "2" or force:
            todo.append(t)
            continue
        rec = (records or {}).get(str(t.get("id"))) or {}
        if fb_due(t.get("taskCycle"), rec.get("last_done"), now):
            todo.append(t)
        else:
            skipped.append(t)
    return todo, skipped


def _claim_label(pid) -> str:
    """领奖那行日志的标题：全阶随机开奖。"""
    return "领阶段奖励(随机开奖)"


def receive_stage_prize(s, cfg, kind=None):
    """领阶段奖励（全阶随机开奖）；返回 (resp, prizeId, prizeName)。

    2026-11-24 用户实测：每一阶都是随机开奖，传 prizeId 也按权重随机，
    一阶传 flow=2 仍领到话费券 → 领奖一律走无参调用，kind 参数保留仅为兼容旧调用。
    11-19 实测无参即领默认档，成功后 feedFodders 归零、status 回到 0。
    六阶抽后可"换其他档位"，但那是独立的换奖动作且接口未抓到，不在此函数内。
    """
    r = call(s, cfg, "stage_receive")
    name = ""
    if isinstance(r.get("data"), dict):
        name = (r["data"].get("prizeDto") or {}).get("prizeName") or ""
    return r, None, name


def run_feed(s, cfg, args, res, prize_kind, key, track_daily):
    """执行喂养（batchFeed 循环），返回喂成功的次数。

    独立成函数是因为它有两个入口：常规流程（--feed）和每日奖励那一轮
    （--daily-only 时由自动策略决定要不要顺带喂），两处逻辑必须完全一样。
    """
    # ---------- 可选：喂养 ----------
    # 抓包证实：GET /stage/batchFeed，无 body，一次耗 250 饲料 / 推进 250 阶段进度。
    # 这是游戏内正常的资源消耗动作，非伪造行为。
    if args.feed > 0 or args.feed_all:
        target = args.feed
        if args.feed_all:
            # 「喂到用完」：轮数不用人填，按当前饲料算；循环里不足 250 仍会停手
            cur0 = call(s, cfg, "index_info")
            fodder0 = int(((cur0.get("data") or {}).get("fodderSurplus") or 0))
            target = fodder0 // 250
            print(f"  喂到用完：当前饲料 {fodder0} → 本轮喂 {target} 轮（每轮 250）")
            if target <= 0:
                print("  -  饲料不足 250，本轮不喂")
                return 0
        done = 0
        guard = 0
        while done < target and guard < target * 3 + 3:
            guard += 1
            cur = call(s, cfg, "index_info")
            fodder = int(((cur.get("data") or {}).get("fodderSurplus") or 0))
            if fodder < 250:
                print(f"  -  饲料不足 250（当前 {fodder}），停止喂养")
                break
            # 阶段将满保护：这一喂会让进度打满时，未开 --auto-claim 就停手
            if not (args.auto_claim or args.continue_stage):
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
            if show(f"喂养 {done+1}/{target}", r):
                done += 1
                continue
            # 133 = 阶段进度已满，"请先选择阶段奖励或继续喂养"（不消耗次数）。
            # 两条出路（2026-11-20 抓包实测）：
            #   --continue-stage → continuousStage 放弃奖品直接升下一阶
            #   否则            → receiveStagePrize 领本阶奖品
            if str(r.get("code")) == "133":
                if args.continue_stage:
                    pause()
                    cs = call(s, cfg, "continuous_stage")
                    if show("继续喂养升阶(放弃本阶奖品)", cs):
                        nxt = ((cs.get("data") or {}).get("stageInfo") or {}).get("stageName") or "下一阶"
                        print(f"     已升到 {nxt}，继续喂养")
                        continue
                    print("  -  升阶失败，停止喂养")
                    break
                pause()
                rp, pid, pname = receive_stage_prize(s, cfg, prize_kind)
                if show(_claim_label(pid), rp):
                    _got_prize(res, pname, cfg)
                    continue
                # 全阶随机、无参请求，失败重试也是同一个请求，不刷两遍
                print("  -  领阶段奖励失败，停止喂养")
                break
            break
        # 喂完重新查阶段进度
        res["feeds"] = done
        # 余额必须用 index_info 复核：stage_info 不带 fodderSurplus，
        # 只靠它会把喂前的旧余额报给控制台（实测喂掉 250 后仍报 250）
        cur2 = call(s, cfg, "index_info")
        if cur2["ok"] and isinstance(cur2.get("data"), dict):
            fv = cur2["data"].get("fodderSurplus")
            if fv is not None:
                res["fodder"] = int(fv)
        st2 = call(s, cfg, "stage_info")
        if st2["ok"] and isinstance(st2["data"], dict):
            d2 = st2["data"]
            si2 = d2.get("stageInfo") or {}
            res["stage"] = f"{si2.get('stageName')} {d2.get('feedFodders')}/{si2.get('feedNeedFodderCount')}"
            if d2.get("fodderSurplus") is not None:
                res["fodder"] = d2["fodderSurplus"]
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
    return res.get("feeds", 0)


def run_visit_tasks(s, cfg, res) -> bool:
    """完成 UN_DO 的 VISIT/APP_VISIT 任务；无待做或全部成功返回 True。"""
    # 抓包证实：任务完成只需 POST /task/doTask/{id}，body={appId,timestamp}，
    # 无停留时长/页面 token 等校验。等价于跳过真实浏览空报完成，故默认关闭。
    pause()
    tl = call(s, cfg, "task_list")
    if not (tl["ok"] and isinstance(tl["data"], list)):
        show("任务列表", tl)
        _note_token(res, tl)
        return False
    targets = [t for t in tl["data"]
               if str(t.get("taskStatus")) == "UN_DO"
               and t.get("completeType") in ("VISIT", "APP_VISIT")]
    if not targets:
        print("  -  没有 UN_DO 的 VISIT 类任务")
        return True
    all_ok = True
    for t in targets:
        tid = t.get("id")
        pause(1.2, 1.0)
        r = call_post(s, cfg, f"/bass-cultivateGameServer/task/doTask/{tid}")
        if show(f"doTask/{tid} {t.get('taskName')}", r):
            res["tasks"] += 1
        else:
            all_ok = False
        _note_token(res, r)
    return all_ok and res.get("token") == "ok"


def fb_task_summary(key, tasks) -> str:
    """优先显示本轮查到的任务数，查询失败时回退到带日期的记录。"""
    if isinstance(tasks, list):
        pending = sum(str(t.get("completeStatus")) != "1" for t in tasks)
        return f"  未完成任务={pending} 个"

    acc = load_json(_gui_path("flowbank_state.json"), {}).get(key) or {}
    recs = acc.get("tasks")
    recorded = str(acc.get("updated") or "")[:10]
    if not isinstance(recs, dict) or not recorded:
        return "  任务状态未知"
    pending = sum(str(r.get("status")) != "1" for r in recs.values())
    suffix = ""
    if recorded != datetime.date.today().isoformat():
        suffix = f"（记录于 {recorded}，可能已过期）"
    return f"  未完成任务={pending} 个{suffix}"


def run_fb_tasks(s, cfg, args, res, key, *, tasks=None) -> bool:
    """完成流量银行浏览任务，所有尝试均需成功且回查确认完成。"""
    # 抓包证实：POST /flowBank/doTask body={appId,timestamp,taskId}，无签名无停留校验。
    # completeStatus: 0=未做 2=进行中 1=已完成。每个任务要点两次（0→2→1）。
    # 有 1006 频控，需间隔。等价于空报浏览，默认关闭。
    all_ok = True
    if tasks is None:
        pause()
        tl = call(s, cfg, "fb_tasks")
        if tl["ok"] and isinstance(tl["data"], list):
            tasks = tl["data"]
            track_fb_tasks(key, tasks)
        else:
            show("流量银行任务列表", tl)
            _note_token(res, tl)
            all_ok = False
    if tasks is not None:
        # 周任务只在周一之后、月任务只在 1 号之后才尝试；没到周期直接跳过，
        # 少打无意义的请求（这个接口有 1006 频控）。
        recs = (load_json(_gui_path("flowbank_state.json"), {}).get(key) or {}).get("tasks") or {}
        todo, skipped = fb_due_tasks(tasks, recs, force=getattr(args, "fb_all", False))
        if skipped:
            print(f"  -  {len(skipped)} 个周/月任务未到周初/月初，跳过"
                  f"（--fb-all 可强制）")
        # completeStatus: 0=未做（要点两次 0→2→1）  2=进行中（再点一次即完成）  1=已完成
        if not todo:
            print("  -  流量银行没有到周期的待做任务")
        reported = {}
        for t in todo:
            tid, title = t.get("id"), t.get("taskTitle")
            rounds = (1, 2) if str(t.get("completeStatus")) == "0" else (1,)
            for round_no in rounds:
                time.sleep(random.uniform(3.5, 5.5))  # 避开 1006 频控
                r = call_post(s, cfg, EP["fb_do_task"], retries=3,
                              extra={"taskId": tid})
                if r["ok"]:
                    reported[str(tid)] = r
                else:
                    show(f"fb任务 {tid} {title} ({round_no}/{len(rounds)})", r)
                    all_ok = False
                _note_token(res, r)
                if not r["ok"] and str(r.get("code")) == "1006":
                    print("     -  频控未过，跳过该轮")
        if todo:
            # 做完立刻回查一次：completeStatus 落到 1 才会进状态文件。
            # 只在开工前记一次快照的话，控制台会一直显示「可做」
            # （2026-11-23 实测：任务其实做完了，界面全标未做，误判成漏跑）。
            time.sleep(random.uniform(1.2, 2.0))
            tl2 = call(s, cfg, "fb_tasks")
            states = {}
            if tl2["ok"] and isinstance(tl2["data"], list):
                track_fb_tasks(key, tl2["data"])
                states = {str(t.get("id")): str(t.get("completeStatus")) for t in tl2["data"]}
                print(f"     任务状态已刷新：{sum(status == '1' for status in states.values())}"
                      f"/{len(states)} 已完成")
            else:
                _note_token(res, tl2)
            for t in todo:
                tid = str(t.get("id"))
                status = states.get(tid)
                label = f"fb任务 {tid} {t.get('taskTitle')}"
                if status != "1":
                    all_ok = False
                if tid not in reported:
                    continue
                if status == "1":
                    show(label, reported[tid], "，回查已完成")
                    res["tasks"] += 1
                elif status is None:
                    print(f"  -  {label}：上报成功但无法确认任务状态")
                elif status == str(t.get("completeStatus")):
                    print(f"  -  {label}：上报成功但任务状态未变化")
                else:
                    print(f"  -  {label}：上报成功，回查任务仍未完成")
    # 重新查可兑流量
    time.sleep(1)
    fb2 = call(s, cfg, "fb_index")
    if fb2["ok"] and isinstance(fb2["data"], dict):
        d2 = fb2["data"]
        res["fb_exchange"] = f"{d2.get('exchangeFlow')}{d2.get('exchangeFlowUnit')}"
        print(f"     流量银行现可兑 {d2.get('exchangeFlow')}{d2.get('exchangeFlowUnit')}"
              f"（{d2.get('exchangeFlowValue')} 点）")
    return all_ok and res.get("token") == "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只查状态，不做改动")
    ap.add_argument("--claim", action="store_true", help="额外领取阶段奖励")
    ap.add_argument("--roll", action="store_true", help="额外掷骰子（消耗次数）")
    ap.add_argument("--feed", type=int, default=0,
                    help="喂养次数（每次耗 250 饲料、推进 250 阶段进度），0=不喂")
    ap.add_argument("--feed-all", action="store_true",
                    help="喂到饲料不足 250 为止：轮数按当前饲料自动算，不用手填。与 --feed 互斥")
    ap.add_argument("--tasks", action="store_true",
                    help="完成 UN_DO 的 VISIT 类任务（等价于空报浏览，默认关闭）")
    ap.add_argument("--coupon-world", action="store_true",
                    help="券来世界（App 首页访问活动）每天一次 500 饲料：先打开页面再上报")
    ap.add_argument("--fb-tasks", action="store_true",
                    help="完成流量银行的浏览任务（空报浏览，默认关闭）")
    ap.add_argument("--fb-all", action="store_true",
                    help="流量银行任务忽略周期，周/月任务也强制尝试")
    ap.add_argument("--daily-only", action="store_true",
                    help="补签到、领取每日三档和昨日投喂奖励，用于定时轮询")
    ap.add_argument("--delay", type=int, default=0, help="执行前随机等待上限（秒）")
    ap.add_argument("--prize", choices=list(PRIZE_KIND), default=None,
                    help="阶段奖励档位 fee/flow/discount（当前无效：每阶随机开奖，"
                         "传了服务端也按权重随机；六阶抽后可换档但接口未抓到）")
    ap.add_argument("--auto-claim", action="store_true",
                    help="阶段将满时自动领奖并继续喂；不加则停手等人工（便于抓包）")
    ap.add_argument("--continue-stage", action="store_true",
                    help="阶段满时调用 continuousStage 直接升阶（放弃本阶奖品）。"
                         "与 --auto-claim 互斥：领奖走 --auto-claim，升阶走本参数")
    ap.add_argument("--config", default="config_fj.json", help="配置文件路径")
    args = ap.parse_args()

    cfg = load_config(BASE / args.config)
    key = Path(args.config).stem
    prize_kind = args.prize or cfg.get("prize_prefer") or "fee"
    if prize_kind not in PRIZE_KIND:
        print(f"未知奖品档位 {prize_kind}，可选：{'/'.join(PRIZE_KIND)}")
        return 1
    if args.continue_stage and args.auto_claim:
        print("参数冲突：--continue-stage（升阶不领奖）与 --auto-claim（领奖）只能选一个")
        return 1
    if args.feed_all and args.feed > 0:
        print("参数冲突：--feed-all（喂到用完）与 --feed（指定轮数）只能选一个")
        return 1
    if args.delay > 0 and not args.dry_run:
        w = random.randint(0, args.delay)
        print(f"随机等待 {w} 秒 ...")
        time.sleep(w)

    s = new_session(cfg)
    print(f"目标 {HOST}  appId={cfg.get('app_id', '0')}")

    # 结构化结果：控制台据此取数，不再正则解析下面那些中文日志
    res = {"plat": "bass", "key": key, "token": "ok", "dry_run": bool(args.dry_run),
           "fodder": None, "stage": "", "fb_exchange": "", "today_signed": False,
           "flow_mb": 0.0, "tasks": 0, "feeds": 0, "last_reward": ""}

    # 券来世界要先看一眼：它没做完时不能走下面的短路，否则每小时的轮询会跳过它
    cw_task = None
    if args.coupon_world and not args.dry_run:
        cw_task, _ = find_coupon_world_task(s, cfg)
        if cw_task:
            print("  -  券来世界访问任务待完成（500 饲料）")

    # 轮询短路：今天该领的都领完了，就不必再调接口
    want_feed = args.feed > 0 or args.feed_all
    if (args.daily_only and not args.dry_run and not cw_task
            and daily_nothing_to_do(key, need_feed=want_feed,
                                    need_tasks=bool(args.tasks),
                                    need_fb_tasks=bool(args.fb_tasks))):
        print("  今日签到和奖励已处理，本轮无事可做（1 秒退出）。")
        res["today_signed"] = True
        res["idle"] = True
        emit_result(res)
        return 0

    # ---------- 状态总览 ----------
    print("\n[状态]")
    info = call(s, cfg, "index_info")
    if info["ok"] and isinstance(info["data"], dict):
        d = info["data"]
        res["fodder"] = d.get("fodderSurplus")
        print(f"  饲料 {d.get('fodderSurplus')}  今日已喂 {d.get('feedCountToday')} 次")
    else:
        show("首页信息", info)
        _note_token(res, info)

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
        res["today_signed"] = signed
        res["sign_cycle"] = d.get("cycleToday")
    else:
        show("签到状态", si)
        _note_token(res, si)
        print("  状态查询失败，终止以免误操作。")
        emit_result(res)
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
    pause()
    handle_feed_reward(s, cfg, key, res, args.dry_run)
    # 喂养轮次（新人号）：达标轮次 doMeet 领奖+解锁下一轮，紧接投喂奖励之后串行处理
    handle_meet(s, cfg, key, res, args.dry_run)
    if "feed_reward_received" in res or "meet_received" in res:
        info = call(s, cfg, "index_info")
        data = info.get("data")
        res["fodder"] = data.get("fodderSurplus") if info["ok"] and isinstance(data, dict) else None
    track_daily(key, dl["data"] if dl["ok"] and isinstance(dl["data"], list) else None,
                lucky_data, sign_data)

    pause()
    st = call(s, cfg, "stage_info")
    if st["ok"] and isinstance(st["data"], dict):
        d = st["data"]
        si2 = d.get("stageInfo") or {}
        res["stage"] = f"{si2.get('stageName')} {d.get('feedFodders')}/{si2.get('feedNeedFodderCount')}"
        print(f"  阶段：{si2.get('stageName')}  饲料 {d.get('feedFodders')}/{si2.get('feedNeedFodderCount')}")

    pause()
    fb = call(s, cfg, "fb_index")
    fb_tasks = None
    if fb["ok"] and isinstance(fb["data"], dict):
        d = fb["data"]
        res["fb_exchange"] = f"{d.get('exchangeFlow')}{d.get('exchangeFlowUnit')}"
        # hasTaskToDo 在任务全完成时仍可能为 True，直接读取只读任务列表。
        pause()
        tl = call(s, cfg, "fb_tasks")
        if tl["ok"] and isinstance(tl["data"], list):
            fb_tasks = tl["data"]
            track_fb_tasks(key, fb_tasks)
        print(f"  流量银行：可兑 {d.get('exchangeFlow')}{d.get('exchangeFlowUnit')}"
              f"（{d.get('exchangeFlowValue')} 点）{fb_task_summary(key, fb_tasks)}")

    if args.dry_run:
        if args.feed_all and res.get("fodder") is not None:
            print(f"  喂到用完：按当前饲料 {res['fodder']} 计，会喂 {int(res['fodder']) // 250} 轮")
        print("\n（--dry-run，到此为止，未做任何改动）")
        emit_result(res)
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
            res["today_signed"] = True
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

    # ---------- 券来世界（App 首页访问活动）----------
    # 每天一次、500 饲料。做法照它本来的意思来：先把券来世界页面打开一次，
    # 再上报 doTask，让日志能对上「访问过」这件事。
    if args.coupon_world and not args.dry_run:
        pause()
        if not cw_task:
            print("  -  券来世界访问任务今天已完成")
        else:
            tid = cw_task.get("id")
            url = task_url(cw_task)
            if url:
                okv = visit_page(s, url)
                print(f"  -  已打开券来世界（{url}）" if okv
                      else f"  -  券来世界页面打开失败（仍按完成上报）：{url}")
            pause(1.0, 0.8)
            r = call_post(s, cfg, f"/bass-cultivateGameServer/task/doTask/{tid}")
            if show(f"doTask/{tid} {cw_task.get('taskName')}", r):
                res["tasks"] += 1
            _note_token(res, r)

    if args.daily_only:
        # 补签成功后要回写一次，否则面板上仍显示"未签"
        if sign_data is not None:
            track_daily(key, None, None, sign_data)
        # 浏览任务放在喂养前，刚赚到的饲料能在本轮一起喂掉
        if args.tasks and not _daily_flag_done(key, "tasks_date"):
            if run_visit_tasks(s, cfg, res):
                _mark_daily_flag(key, "tasks_date")
        if args.fb_tasks and not _daily_flag_done(key, "fb_tasks_date"):
            if run_fb_tasks(s, cfg, args, res, key, tasks=fb_tasks):
                _mark_daily_flag(key, "fb_tasks_date")
        # run_all 在达到喂养时间且本账号领齐三档时注入喂养参数。
        if want_feed:
            run_feed(s, cfg, args, res, prize_kind, key, track_daily)
        print("\n[daily-only] 结束。")
        emit_result(res)
        return 0

    # ---------- 可选：完成 VISIT 任务 ----------
    if args.tasks:
        if run_visit_tasks(s, cfg, res):
            _mark_daily_flag(key, "tasks_date")

    if want_feed:
        run_feed(s, cfg, args, res, prize_kind, key, track_daily)

    # ---------- 可选：流量银行浏览任务 ----------
    if args.fb_tasks:
        if run_fb_tasks(s, cfg, args, res, key, tasks=fb_tasks):
            _mark_daily_flag(key, "fb_tasks_date")

    # ---------- 可选：阶段奖励 ----------
    # 抓包/实测：GET /stage/receiveStagePrize 不带参数即领"默认档"奖励，
    # 成功后 feedFodders 归零、status 回到 0，可继续喂养。
    if args.claim:
        pause()
        stc = call(s, cfg, "stage_info")
        status = str(((stc.get("data") or {}).get("status")))
        if status == "1":
            r, pid, pname = receive_stage_prize(s, cfg, prize_kind)
            if show(_claim_label(pid), r):
                _got_prize(res, pname, cfg)
        else:
            fodders = ((stc.get("data") or {}).get("feedFodders"))
            print(f"  -  阶段未满（{fodders}），暂无可领奖励")
        pause()
        r2 = call(s, cfg, "stage_prizes")
        if r2["ok"] and isinstance(r2["data"], list):
            print("  奖品池：")
            for item in r2["data"]:
                names = "、".join(f"{x.get('prizeName')}" for x in (item.get("prizeList") or []))
                idx = int(item.get("stage") or 1) - 1
                mark = "随机·抽后可换" if idx == 5 else "随机"
                print(f"     第{item.get('stage')}阶（{mark}）：{names}")

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
    emit_result(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
