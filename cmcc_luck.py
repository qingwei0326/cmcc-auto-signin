"""
cmcc_luck.py —— 福建移动「优惠专区 · 转盘抽奖」自动化

链路（全部 GET，只需 appId/timestamp）：
    index                      → 剩余次数 + taskList + 订阅状态
    doTask/{taskType}/{taskId} → 完成任务（直接 GET 即记完成，不用真浏览）
    join                       → 抽奖，返回 prizeName

鉴权（重要）：
    preferentialAreaServer 和养成游戏共用同一套 bass 鉴权，
    所以 configs_fj/*.json 里的 bearer_token 可以直接拿来抽奖，**无需抓包**。
    HAR 模式仅作为兜底：某个号没配 bearer，或想用刚抓的会话时使用。

token 说明：
    · loginByHangYan 入参的 SSO ticket（YZsidssolg…）一次性，无法复用
    · 它换来的 bearer 寿命较长（实测 18 分钟后仍可用）

加次数任务（taskList → doTask）：
    taskType 3 = 请好友帮忙（没有对端账号，只能直接上报）
    taskType 2 = 中国移动APP中参与
    taskType 5 = 浏览类，带 taskUrl（先真打开一次页面再上报，和券来世界同一套路）
    实测一次全刷能把次数从 0 换到 2；complete=true 的已经在服务端记过，跳过。

用法：
    python cmcc_luck.py                 # 所有 configs_fj 账号：刷任务 + 抽奖
    python cmcc_luck.py --dry-run       # 只看剩余次数和任务，不抽
    python cmcc_luck.py --key fj2       # 只跑某一个配置
    python cmcc_luck.py --no-tasks      # 不刷任务，只抽现有次数
    python cmcc_luck.py --skip-assist   # 刷任务，但跳过「请好友帮忙」
    python cmcc_luck.py --max 5         # 每号最多抽 5 次
    python cmcc_luck.py --har x.har     # 改用 HAR 里的会话（兜底）
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from common import mask_phone, record_award, setup_utf8, update_json

setup_utf8()

BASE = Path(__file__).parent
CFG_DIR = BASE / "configs_fj"
STATE_FILE = BASE / "gui_data" / "luck_state.json"
API = "https://wap.fj.10086.cn/bass-preferentialAreaServer/luck/"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148/wkwebview")
APP_ID = 26


# ---------------------------------------------------------------- HAR 兜底

def find_latest_har(explicit=None):
    """显式路径优先；否则在常见目录里找修改时间最新的 .har"""
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        print(f"指定的 HAR 不存在：{explicit}")
        return None

    cands = []
    cands += list(BASE.glob("*.har"))
    dl = Path.home() / "Downloads"
    if dl.is_dir():
        cands += list(dl.glob("*.har"))
    # 微信接收的抓包文件：xwechat_files/<wxid>/temp/RWTemp/<月份>/<hash>/xxx.har
    wx = Path("D:/wechat/xwechat_files")
    if wx.is_dir():
        cands += list(wx.glob("*/temp/RWTemp/*/*/*.har"))

    cands = [p for p in cands if p.is_file()]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def tokens_from_har(path: Path):
    """loginByHangYan 响应里的 token 优先，其次 preferentialAreaServer 请求头"""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        print(f"HAR 解析失败：{e}")
        return []

    resp_toks, hdr_toks = [], []
    for e in (data.get("log") or {}).get("entries") or []:
        url = str((e.get("request") or {}).get("url") or "")
        if "loginByHangYan" in url:
            txt = ((e.get("response") or {}).get("content") or {}).get("text") or ""
            try:
                tok = (json.loads(txt).get("data") or {}).get("token")
                if tok:
                    resp_toks.append(str(tok))
            except Exception:
                pass
        if "preferentialAreaServer" in url:
            for h in (e.get("request") or {}).get("headers") or []:
                if str(h.get("name", "")).lower() == "authorization":
                    v = str(h.get("value") or "")
                    if v.lower().startswith("bearer "):
                        hdr_toks.append(v[7:].strip())

    out = []
    for t in resp_toks + list(reversed(hdr_toks)):
        if t and t not in out:
            out.append(t)
    return out


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


def save_state(phone, info, draws=0, wins=0):
    """多账号共用一份文件，读-改-写要加跨进程锁，否则并发跑号会互相覆盖。

    draws/wins 是**当天累计**（含「谢谢参与」），跨天自动归零。
    不记这个就没法和手厅里的「已抽次数」对账：一天跑三轮、只中一次时，
    界面上只写「抽 0 次」会让人怀疑数字是错的。
    """
    key = (phone or "unknown").replace("*", "")[-4:] or "unknown"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.now().strftime("%Y-%m-%d")

    def mutate(st):
        acc = st.setdefault(key, {})
        acc.update(info)
        if acc.get("day") != today:
            acc["day"] = today
            acc["day_draws"] = 0
            acc["day_wins"] = 0
        if draws or wins:
            acc["day_draws"] = int(acc.get("day_draws") or 0) + int(draws)
            acc["day_wins"] = int(acc.get("day_wins") or 0) + int(wins)
        acc["updated"] = stamp

    try:
        update_json(STATE_FILE, mutate)
    except Exception:
        pass


# ---------------------------------------------------------------- 加次数任务

TASK_ASSIST = 3          # taskType=3 请好友帮忙（没有对端账号，只能空报）


def do_tasks(get, visit, tasks, skip_assist=False):
    """把「加抽奖次数」的任务做掉。返回 {"done", "skip", "expired"}。

    设计取舍：
    · complete=true 的跳过 —— 服务端已经记过账，再报一次也不会多给次数
    · 带 taskUrl 的先真打开一次页面再上报，尽量走和真人一样的路径
    · 不带 taskUrl 的（请好友帮忙 / APP 中参与）只能直接上报，实测同样 0000
    · 碰到 401 立刻收手 —— 后面抽奖也一定失败，别把次数白耗在重试上
    """
    done, skip, expired = [], [], False
    for t in tasks:
        name = str(t.get("taskName") or "")
        tt, tid = t.get("taskType"), t.get("id")
        if t.get("complete"):
            skip.append((name, "服务端已记完成"))
            continue
        if skip_assist and int(tt or 0) == TASK_ASSIST:
            skip.append((name, "好友助力，已跳过"))
            continue
        url = t.get("taskUrl")
        if isinstance(url, str) and url:
            visit(url)
        r = get(f"doTask/{tt}/{tid}")
        code = str(r.get("code"))
        if code == "0000":
            done.append(name)
        elif code in ("401", "-1"):
            expired = True
            skip.append((name, "token 失效"))
            break
        else:
            skip.append((name, f"code={code} {r.get('msg') or ''}".strip()))
        time.sleep(random.uniform(1.2, 2.0))
    return {"done": done, "skip": skip, "expired": expired}


# ---------------------------------------------------------------- 单账号流程

def run_one(tag, phone_hint, tok, args):
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"Authorization": "Bearer " + tok, "User-Agent": UA})

    def get(path):
        p = {"appId": APP_ID, "timestamp": int(time.time() * 1000)}
        try:
            return s.get(API + path, params=p, timeout=20).json()
        except Exception as e:
            return {"code": "-1", "msg": f"请求异常: {e}"}

    def visit(url):
        """浏览类任务：先真的打开一次目标页。打不开也照常上报，不因此丢掉次数。"""
        u = str(url).replace("{token}", tok)
        try:
            s.get(u, timeout=15)
            time.sleep(random.uniform(0.6, 1.2))
            return True
        except Exception:
            return False

    print("\n" + "-" * 62)
    print(f"【{tag}】{mask_phone(phone_hint)}")

    idx = get("index")
    if str(idx.get("code")) == "401":
        print("  token 失效（401），跳过。请更新该号 bearer，或用 --har 指定新抓的包。")
        return {"tag": tag, "skip": True}
    if str(idx.get("code")) != "0000":
        print(f"  index 失败：{idx.get('code')} {idx.get('msg')}")
        return {"tag": tag, "skip": True}

    d = idx.get("data") or {}
    phone = d.get("userPhone") or phone_hint or ""
    surplus = int(d.get("surplusLuckCount") or 0)
    tasks = d.get("taskList") or []
    print(f"  账号 {mask_phone(phone) or '(未返回)'}   剩余次数 {surplus}")
    if tasks:
        todo = [t for t in tasks if not t.get("complete")]
        done = [t for t in tasks if t.get("complete")]
        if todo:
            print("  可加次数任务：" + "、".join(str(t.get("taskName")) for t in todo))
        if done:
            print("  今天已做过：" + "、".join(str(t.get("taskName")) for t in done))
        if not todo:
            print("  加次数任务今天已经做完了")

    sub = get("qrySubscription").get("data") or {}
    print(f"  订阅状态 subscriptionStatus={sub.get('subscriptionStatus')}  first={sub.get('first')}")

    if args.dry_run:
        save_state(phone, {"phone": phone, "surplus": surplus,
                           "tasks": [t.get("taskName") for t in tasks]})
        return {"tag": tag, "phone": phone, "surplus": surplus, "draws": 0, "wins": 0}

    # ---- 刷任务（加抽奖次数）----
    if tasks and not args.no_tasks:
        print("  [刷任务]")
        rep = do_tasks(get, visit, tasks, skip_assist=args.skip_assist)
        for name in rep["done"]:
            print(f"    OK  {name}")
        for name, why in rep["skip"]:
            print(f"    --  {name}  {why}")
        if rep["expired"]:
            print("  token 在刷任务途中失效，放弃这一轮（新票到手再跑）。")
            save_state(phone, {"phone": phone, "surplus": surplus})
            return {"tag": tag, "phone": phone, "surplus": surplus,
                    "draws": 0, "wins": 0, "skip": True}
        if rep["done"]:
            time.sleep(1.5)
            d2 = get("index").get("data") or {}
            surplus = int(d2.get("surplusLuckCount") or 0)
            print(f"    刷完 {len(rep['done'])} 个任务，剩余次数 {surplus}")

    if surplus <= 0:
        print("  没有可抽次数，结束。")
        save_state(phone, {"phone": phone, "surplus": 0})
        return {"tag": tag, "phone": phone, "surplus": 0, "draws": 0, "wins": 0}

    # ---- 抽奖 ----
    n = min(surplus, args.max)
    print(f"  [抽奖] 计划 {n} 次")
    prizes, wins = [], 0
    failed = False
    for i in range(n):
        r = get("join")
        code = str(r.get("code"))
        dd = r.get("data") or {}
        name = dd.get("prizeName") or ""
        if code == "0000":
            is_win = bool(name) and "谢谢" not in name
            print(f"    [{i + 1}/{n}] {('★ ' if is_win else '')}{name}")
            prizes.append({
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "name": name,
                "type": dd.get("prizeType"),
                "life": dd.get("prizeLife"),
                "url": dd.get("prizeUrl") or "",
            })
            if is_win:
                wins += 1
                # 中奖即记流水：控制台「到账台账」靠它显示今天到手多少流量
                record_award(phone or phone_hint, "天天大转盘", name,
                             url=dd.get("prizeUrl") or "")
        elif code == "1600":
            print(f"    [{i + 1}/{n}] 次数已用完，停止")
            break
        else:
            failed = True
            print(f"    [{i + 1}/{n}] 失败 code={code} {r.get('msg')}")
            if code in ("401", "-1"):
                break
        time.sleep(random.uniform(1.5, 2.5))

    final = get("index").get("data") or {}
    info = {"phone": phone, "surplus": int(final.get("surplusLuckCount") or 0)}
    # 这一轮一次没抽（次数已用完 / 接口回 1600）就别把上次的中奖记录清成空，
    # 否则界面上「最近抽到」会莫名其妙变没。
    if prizes:
        info.update({"last_prizes": prizes, "last_draws": len(prizes), "last_wins": wins})
    save_state(phone, info, draws=len(prizes), wins=wins)
    print(f"  小计：抽 {len(prizes)} 次，中 {wins} 次")
    return {"tag": tag, "phone": phone, "surplus": int(final.get("surplusLuckCount") or 0),
            "draws": len(prizes), "wins": wins, "failed": failed}


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description="优惠专区转盘抽奖")
    ap.add_argument("--key", default=None, help="只跑某个 configs_fj 配置，如 fj2")
    ap.add_argument("--har", default=None, help="用 HAR 里的会话（兜底），不指定则读配置里的 bearer")
    ap.add_argument("--dry-run", action="store_true", help="只查状态，不做任务不抽奖")
    ap.add_argument("--no-tasks", action="store_true", help="不刷任务，只抽现有次数")
    ap.add_argument("--skip-assist", action="store_true",
                    help="刷任务，但跳过「请好友帮忙」（没有对端账号，只能空报）")
    ap.add_argument("--max", type=int, default=20, help="每个号最多抽几次，默认 20")
    args = ap.parse_args()

    targets = []
    if args.har:
        har = find_latest_har(args.har)
        if not har:
            print("没找到 HAR。")
            return 1
        print(f"HAR: {har.name}")
        cands = tokens_from_har(har)
        if not cands:
            print("HAR 里没有 preferentialAreaServer / loginByHangYan 的 token。")
            return 1
        # HAR 里 token 可能多个，用 index 逐个试
        for tok in cands:
            r = requests.Session()
            r.trust_env = False
            try:
                j = r.get(API + "index",
                          params={"appId": APP_ID, "timestamp": int(time.time() * 1000)},
                          headers={"Authorization": "Bearer " + tok, "User-Agent": UA},
                          timeout=12).json()
                if str(j.get("code")) == "0000":
                    targets.append(("har", (j.get("data") or {}).get("userPhone") or "", tok))
                    break
            except Exception:
                pass
            time.sleep(0.8)
        if not targets:
            print("HAR 里的 token 都失效了，请重新打开活动页再导出。")
            return 1
    else:
        targets = collect_targets(args.key)
        if not targets:
            print("configs_fj 下没有可用 bearer_token。可加 --har 指定抓包文件。")
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
        if r.get("skip"):
            print(f"  ✗ {r['tag']}  跳过/失败")
        else:
            mark = "✗" if r.get("failed") else "✓"
            print(f"  {mark} {r['tag']}  {mask_phone(r.get('phone'))}  剩余 {r.get('surplus')} 次"
                  f"  本次抽 {r.get('draws')} 中 {r.get('wins')}")
    if args.dry_run:
        print("\n（--dry-run，未做任务也未抽奖）")
    return 1 if any(r.get("skip") or r.get("failed") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
