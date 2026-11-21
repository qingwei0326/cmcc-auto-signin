"""
common.py —— 各平台脚本共用的基础设施

集中放置原来在四个脚本里各写一遍的东西：

    · 原子写        —— 先写临时文件再 os.replace，避免定时任务并发写坏 json
    · 通知          —— Bark / Server酱推送，失败不影响主流程
    · 会话与接口调用 —— 统一返回 {ok, code, msg, data}
    · 结果展示      —— 统一的 "✓/!!" 输出

各平台脚本只保留自己的接口表和业务流程。
"""

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import requests


def app_base() -> Path:
    """项目根目录 —— 冻结成 onefile exe 后也必须指向 exe 所在目录。

    冻结时 PyInstaller 把源码解包到临时目录（_MEIxxxx），`__file__` 指向那里。
    但配置、脚本、日志都在 **exe 旁边** 的项目目录里，
    如果用 `Path(__file__).parent`，写进去的 configs 会在 exe 关闭后随临时目录一起消失。
    所以：冻结时用 sys.executable 的目录，源码运行时用 __file__ 的目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE = app_base()

# 移动端 UA，部分接口会对桌面 UA 直接返回异常
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148/wkwebview")


def setup_utf8():
    """把 stdout / stderr 都固定成 UTF-8。

    Windows 上 stderr 在被管道捕获时默认用 GBK，父进程按 UTF-8 解码就会乱码
    （表现为「ִʧ: appTokenLogin ʧ: FAILED APP¼״̬...」）。
    必须在 logging.basicConfig 之前调用，handler 才会拿到新编码的流。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


setup_utf8()


# ---------------------------------------------------------------- 基础

def now_ms():
    """13 位毫秒时间戳，BASS 全系列接口都要带。"""
    return str(int(time.time() * 1000))


def atomic_write_text(path, text):
    """原子写文本：先写 .tmp 再 os.replace。

    定时任务（每小时轮询）与一键签到可能同时写同一个 json，
    直接 write_text 在中途被打断就会留下半截文件导致后续解析失败。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path, obj, indent=1):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent))


def load_json(path, default=None):
    """读 json，文件缺失或损坏都返回默认值而不是抛异常。"""
    try:
        p = Path(path)
        if not p.exists():
            return default if default is not None else {}
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


@contextmanager
def file_lock(path, timeout=20.0):
    """跨进程互斥锁，保护「读整份 → 改自己的 key → 写回」这段临界区。

    原子写只能防半截文件，防不了丢更新：run_all 默认 4 并发，两个号同时
    写 daily_state.json 时后写的会整份覆盖先写的。计划任务与一键签到分属
    不同进程，threading.Lock 也管不到，所以这里用文件锁。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    fh = open(lock_path, "a+b")
    try:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"0")
            fh.flush()
        deadline = time.time() + timeout
        while True:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(f"等待文件锁超时：{lock_path}")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        fh.close()


def update_json(path, mutate, default=None, indent=1):
    """跨进程安全地读-改-写一份 json；mutate(obj) 就地修改，返回改完的对象。"""
    with file_lock(path):
        obj = load_json(path, default if default is not None else {})
        mutate(obj)
        atomic_write_json(path, obj, indent=indent)
        return obj


def find_har_files(extra_dirs=None):
    """找出候选 HAR 文件，按修改时间倒序（新的在前）。

    搜索范围：
      · 项目目录
      · 用户下载目录
      · 微信接收目录（xwechat_files/<wxid>/temp/RWTemp/<月份>/<hash>/xxx.har）

    微信那一段是实际使用场景：抓包文件从微信里直接拖到控制台，
    服务端能自己定位到它，不用把几十 MB 的内容再传一遍。
    """
    cands = []
    try:
        cands += list(BASE.glob("*.har"))
    except Exception:
        pass
    dl = Path.home() / "Downloads"
    if dl.is_dir():
        try:
            cands += list(dl.glob("*.har"))
        except Exception:
            pass
    for wx_root in (Path("D:/wechat/xwechat_files"), Path("C:/wechat/xwechat_files")):
        if wx_root.is_dir():
            try:
                # 微信接收的文件落在 <wxid>/msg/file/<月份>/xxx.har
                cands += list(wx_root.glob("*/msg/file/*/*.har"))
                # 另有一处落在 temp/RWTemp
                cands += list(wx_root.glob("*/temp/RWTemp/*/*/*.har"))
            except Exception:
                pass
    for d in (extra_dirs or []):
        try:
            p = Path(str(d))
            if p.is_file() and p.suffix.lower() == ".har":
                cands.append(p)
            elif p.is_dir():
                cands += list(p.glob("*.har"))
        except Exception:
            pass

    files = []
    seen = set()
    for p in cands:
        try:
            rp = p.resolve()
        except Exception:
            continue
        if rp in seen or not p.is_file():
            continue
        seen.add(rp)
        files.append(p)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


# ---------------------------------------------------------------- 自动策略

# ---------------------------------------------------------------- 到账流水
#
# 用户只关心「今天到手多少流量、几张能用的券」，不关心喂了几次饲料。
# 所以每个脚本领到东西的那一刻就写一条流水（gui_data/awards.json），
# 控制台只读这份流水，不再靠日志文案反推 —— 定时任务 / 命令行跑的也能记上。

AWARDS_FILE = BASE / "gui_data" / "awards.json"
AWARDS_KEEP = 600       # 只留最近 600 条，文件不会无限长

# 名字里出现这些词 = 有门槛才能用（要充值 / 满额 / 指定场景抵扣），不算「到手可用」。
# 「手气红包」是转盘抽到的话费券，实测**满额才能抵扣**（2026-11-21 用户确认），
# 所以不能当无门槛券计入「本月已得话费」，只单独计数。
THRESHOLD_WORDS = ("充值", "满", "首充", "续费", "开通", "办理", "门槛", "手气红包", "加赠")
RECHARGE_WORDS = THRESHOLD_WORDS      # 旧名，保留兼容已有调用
REDPACKET_WORD = "手气红包"            # 话费型手气红包直接忽略；流量型照常计入

# 「15元购10GB」「13元购30GB」这种是**要先掏钱买**的券包，不是白拿的流量。
# 名字里带「数字+元+购/办」，就按付费处理：不进「已得流量」，只标成有门槛。
PAY_WORDS = ("元购", "元办", "元享", "元升级", "元包", "元/月")


def parse_size_mb(text) -> float:
    """「1GB日包」→1024，「500MB」→500。认不出就 0。"""
    t = str(text or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:GB|G)(?![a-zA-Z])", t, re.I)
    if m:
        return round(float(m.group(1)) * 1024, 1)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:MB|M)(?![a-zA-Z])", t, re.I)
    if m:
        return round(float(m.group(1)), 1)
    return 0.0


def parse_fee_yuan(text) -> float:
    """「1元话费券」→1.0，「0.5元话费券」→0.5，「2-3元话费券手气红包」→3.0。

    区间面额取**上限**（用户看到的是「最高能抵多少」）；认不出就 0。
    """
    t = str(text or "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:[-~至]\s*(\d+(?:\.\d+)?))?\s*元", t)
    if not m:
        return 0.0
    v = m.group(2) or m.group(1)
    try:
        return round(float(v), 2)
    except Exception:
        return 0.0


def classify_award(name) -> dict:
    """把奖品名分成 流量 / 话费券 / 折扣券 / 其他，标出门槛、面额、是否直接忽略。

    规则（用户 2026-11-21 定）：
    - **话费类「手气红包」**：满额才能抵扣，直接**不算也不显示**（`ignored=True`）
    - **流量类「手气红包」**：照常计入到手流量（流量是无门槛到账的），
      所以流量类判门槛时不看「手气红包」这个词
    - 其它带「充值 / 满 / 首充 …」的券：算有门槛，只计数不列明细
    """
    t = str(name or "").strip()
    mb = parse_size_mb(t)
    paid = any(w in t for w in PAY_WORDS)
    if paid:
        # 付费券包（15元购10GB…）：东西还没到手，不能计入「已得流量」
        mb = 0.0
    if mb > 0 and ("流量" in t or "G" in t.upper() or "M" in t.upper() or "包" in t):
        kind = "flow"
    elif "流量" in t:
        kind = "flow"
    elif "话费" in t:
        kind = "fee"
    elif "券" in t or "折" in t:
        kind = "coupon"      # 折扣券/兑换券；need_recharge 会标出哪些要充值
    else:
        kind = "other"
    words = THRESHOLD_WORDS if kind != "flow" else tuple(
        w for w in THRESHOLD_WORDS if w != REDPACKET_WORD)
    if kind == "fee" and "券包" in t:
        # 「888元话费券包」是一包满减券，不是能直接抵 888 元的话费
        words = tuple(words) + ("券包",)
    need_recharge = paid or any(w in t for w in words)
    return {"kind": kind, "size_mb": mb, "need_recharge": need_recharge,
            "yuan": 0.0 if paid else parse_fee_yuan(t),
            # 话费型手气红包：不算「到手可用」，也不进台账明细（流量型照常算）
            # 「加赠券」是充值时额外送的，不是到手可用的话费，和话费手气红包一样不显示
            "ignored": kind == "fee" and (REDPACKET_WORD in t or "加赠" in t)}


def record_award(account, source, name, url="", size_mb=None, kind=None,
                 allow_dup=False):
    """记一条到账流水。脚本领到东西就调它，失败也不能影响主流程。

    allow_dup：同一次运行里**确实可能连中多张相同奖品**（比如抽奖 3 张一样的券），
    此时要跳过「同分钟去重」，否则 3 张只会记 1 张，战报少算。
    """
    t = str(name or "").strip()
    if not t or "谢谢" in t:
        return None
    cls = classify_award(t)
    row = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # 账号为空说明调用方没传上下文（曾经是测试污染），显式标「未知」，
        # 免得台账里出现一行没有归属的奇怪记录
        "account": str(account or "").strip() or "未知",
        "source": str(source or ""),
        "name": t,
        "kind": kind or cls["kind"],
        "size_mb": float(size_mb if size_mb is not None else cls["size_mb"]),
        "yuan": cls["yuan"],          # 面额（元）：话费券用来算「本月已得话费」
        "need_recharge": cls["need_recharge"],
        "ignored": cls["ignored"],    # 话费型手气红包：记账但不显示、不计入
        "url": str(url or ""),
    }

    def mutate(st):
        rec = st.setdefault("records", [])
        # 同一账号同一来源同一奖品，60 秒内重复登记说明是重复读接口，忽略。
        # 但抽奖这类「一次真的中多张相同」的场景传 allow_dup=True 跳过。
        if rec and not allow_dup:
            last = rec[-1]
            if (last.get("account") == row["account"] and last.get("source") == row["source"]
                    and last.get("name") == row["name"] and last.get("at", "")[:16] == row["at"][:16]):
                return
        rec.append(row)
        st["records"] = rec[-AWARDS_KEEP:]

    try:
        update_json(AWARDS_FILE, mutate, default={"records": []})
        return row
    except Exception:
        return None


def load_awards():
    """读流水（新的在前）。

    读的时候按**当前**规则重判一遍：历史数据是按旧规则写下的
    （比如「手气红包」早先被当成无门槛券），这里自动纠正，不必手工改台账。
    """
    try:
        st = load_json(AWARDS_FILE, default=None) or {}
    except Exception:
        st = {}
    recs = st.get("records") if isinstance(st, dict) else st
    out = list(reversed(recs or []))
    for r in out:
        if not isinstance(r, dict):
            continue
        cls = classify_award(r.get("name"))
        r["need_recharge"] = cls["need_recharge"]      # 以当前规则为准（流量红包要改回 False）
        r["ignored"] = cls["ignored"]
        if not r.get("yuan") and cls["yuan"]:
            r["yuan"] = cls["yuan"]                    # 老流水没存面额，读的时候补上
    return out


def awards_summary(month_prefix=None, usable_only=True):
    """按月汇总：流量合计（MB）、可直兑话费券、有门槛的券（只计数不展示明细）。"""
    month_prefix = month_prefix or datetime.now().strftime("%Y-%m")
    flow = 0.0
    fee = 0
    recharge = 0
    items = []
    for r in load_awards():
        if not str(r.get("at", "")).startswith(month_prefix):
            continue
        if r.get("ignored"):          # 话费型手气红包：不算、不显示
            continue
        kind = r.get("kind")
        if kind == "flow":
            flow += float(r.get("size_mb") or 0)
        elif kind == "fee":
            if r.get("need_recharge"):
                recharge += 1
                if usable_only:
                    continue
            fee += 1
        if usable_only and r.get("need_recharge"):
            continue
        items.append(r)
    return {"flow_mb": round(flow, 1), "fee_count": fee,
            "recharge_count": recharge, "items": items}


def daily_report(day_prefix=None):
    """生成「今日战报」：当天到手的东西按账号分组，带流量/话费合计。

    口径与 awards_summary 一致：
      · ignored 的（话费手气红包、话费加赠券）不算数也不列明细；
      · 有门槛的券（需充值）只计数，不算进「到手流量/话费」。
    返回 (标题, 正文)。
    """
    day = day_prefix or datetime.now().strftime("%Y-%m-%d")
    rows = [r for r in load_awards() if str(r.get("at", "")).startswith(day)]

    by_acc = {}
    for r in rows:
        if r.get("ignored"):
            continue
        acc = str(r.get("account") or "").strip() or "未知账号"
        # 只显示尾号：推送会落到手机锁屏上，不暴露完整手机号
        # 同一个号可能一会儿记全号一会儿记尾号，统一按尾号归并
        tail = re.sub(r"\D", "", acc)[-4:] or acc
        by_acc.setdefault(tail, []).append(r)

    if not by_acc:
        return f"{day} 今日战报", "今天还没有到手的奖品。"

    total_flow = 0.0
    total_fee = 0
    lines = []
    for acc in sorted(by_acc):
        items = by_acc[acc]
        flow = sum(float(r.get("size_mb") or 0) for r in items
                   if r.get("kind") == "flow" and not r.get("need_recharge"))
        fee_yuan = sum(float(r.get("yuan") or 0) for r in items
                       if r.get("kind") == "fee" and not r.get("need_recharge"))
        fee_cnt = sum(1 for r in items
                      if r.get("kind") == "fee" and not r.get("need_recharge"))
        coupon_cnt = sum(1 for r in items if r.get("kind") == "coupon")
        other_cnt = sum(1 for r in items if r.get("kind") == "other")
        total_flow += flow
        total_fee += fee_yuan

        lines.append(f"【{acc}】")
        for r in items:
            if r.get("need_recharge"):
                continue      # 有门槛的券不算到手，明细不列
            src = r.get("source") or "其它"
            lines.append(f"  · {r.get('name')}（{src}）")
        bits = []
        if flow:
            bits.append(f"流量 {flow / 1024:.2f} GB" if flow >= 1024 else f"流量 {flow:.0f} MB")
        if fee_cnt:
            bits.append(f"话费券 {fee_cnt} 张（{fee_yuan:.0f} 元）")
        if coupon_cnt:
            bits.append(f"其它券 {coupon_cnt} 张")
        if other_cnt:
            bits.append(f"其它 {other_cnt} 项")
        lines.append("  小计：" + ("、".join(bits) if bits else "无"))
        lines.append("")

    foot = []
    if total_flow:
        foot.append(f"合计流量 {total_flow / 1024:.2f} GB" if total_flow >= 1024
                    else f"合计流量 {total_flow:.0f} MB")
    if total_fee:
        foot.append(f"合计话费 {total_fee:.0f} 元")
    foot.append(f"共 {sum(len(v) for v in by_acc.values())} 项")
    lines.append("—— " + "，".join(foot))
    return f"{day} 今日战报", "\n".join(lines)


# ---------------------------------------------------------------- 自动策略

POLICY_FILE = BASE / "gui_data" / "policy.json"

# 「设置一次、长期生效」的默认值。键名与控制台、run_all 共用，改这里就全变了。
POLICY_DEFAULT = {
    "luck": True,                # 每天把「优惠专区·天天大转盘」的免费次数抽完
    "coupon_world": True,        # 每天做一次「券来世界」访问任务（500 饲料）
    "xunbao": True,              # 每天把「环闽寻宝记」的骰子投完（当天不用就作废）
    "xunbao_tasks": True,        # 顺手报「参与天天大转盘」换骰子（好友助力不报）
    "bass": {
        "feed": 0,               # 每天自动喂几轮（每轮 250 饲料，0 = 不喂）
        "feed_all": False,       # True = 喂到饲料不足 250 为止，轮数自动算，不用填 feed
        "feed_hour": 12,         # 只在几点那一轮喂（默认 12 点，与每日奖励任务同一次运行）
        "on_stage_full": "stop",  # stop 停手 / claim 领奖续喂 / continue 放弃奖品升阶
        "prize": "fee",          # 领奖档位 fee 话费 / flow 流量 / discount 折扣
        "tasks": False,          # 养成浏览任务（空报，默认关）
        "fb_tasks": False,       # 流量银行任务（空报，默认关）
    },
    "xxl": {"level": 49, "star": 3, "times": 5, "gap": 12},
    "seckill": {"at": "11:59:50"},
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_policy() -> dict:
    """读取自动策略；缺字段用默认值补齐，文件坏了也不影响运行。"""
    try:
        data = load_json(POLICY_FILE, default=None) or {}
    except Exception:
        data = {}
    return _deep_merge(POLICY_DEFAULT, data if isinstance(data, dict) else {})


def save_policy(patch: dict) -> dict:
    """把 patch 合并进现有策略并落盘，返回合并后的完整策略。"""
    merged = _deep_merge(load_policy(), patch or {})
    merged["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(POLICY_FILE, merged, indent=2)
    return merged


# ---------------------------------------------------------------- 配置

def load_config(path, required=(), template="config.example.json"):
    """读取平台配置；缺少必需字段时直接退出并说明怎么填。"""
    path = Path(path)
    if not path.exists():
        print(f"缺少配置文件：{path}")
        print(f"可复制 {template} 作为模板：{BASE / template}")
        sys.exit(1)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"配置文件解析失败：{path}\n  {e}")
        sys.exit(1)
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        print(f"{path.name} 缺少字段：{', '.join(missing)}")
        print(f"请参考 {template} 填写。")
        sys.exit(1)
    return cfg


# ---------------------------------------------------------------- 子脚本启动
#
# 冻结成 exe 后不再要求用户装 Python：由 exe 自己带 --run-script 解释执行脚本。
# 源码运行时还是 python xxx.py。两条路径都从这里出，免得各写一份又走岔。

RUN_SCRIPT_FLAG = "--run-script"


def script_path(name) -> Path:
    """脚本位置：优先 exe/项目同目录，其次打包内的解包目录。"""
    name = str(name)
    cands = [BASE / name]
    mp = getattr(sys, "_MEIPASS", "")
    if mp:
        cands.append(Path(mp) / name)
    for c in cands:
        if c.is_file():
            return c
    return cands[0]


def child_cmd(name, *args) -> list:
    """构造「跑某个子脚本」的命令行。"""
    p = script_path(name)
    tail = [str(a) for a in args]
    if getattr(sys, "frozen", False):
        return [sys.executable, RUN_SCRIPT_FLAG, str(p), *tail]
    return [sys.executable, str(p), *tail]


# ---------------------------------------------------------------- 通知

# 凭证失效的典型文案。run_all 用它判断「这一轮是不是只是票过期了」，
# 好把各账号的失败合并成一条通知。
TOKEN_DEAD = re.compile(
    r"(401|token\s*(失效|过期)|票据.*(失效|过期)|登录态.*(失效|过期)"
    r"|请重新抓包|会话缺失|未登录|not\s*login|未登录态)", re.I)

NOTIFY_STATE_FILE = BASE / "gui_data" / "notify_state.json"

# 设置页保存的全局配置：推送方式这类「设置一次、长期生效」的项。
# 放在 gui_data 下，和 policy.json 一样是本地文件，不进 git。
GLOBAL_SETTINGS_FILE = BASE / "gui_data" / "settings.json"

PUSHPLUS_URL = "https://www.pushplus.plus/send"


def load_global_settings() -> dict:
    """读设置页的全局配置。文件缺失或损坏都返回空字典。"""
    st = load_json(GLOBAL_SETTINGS_FILE, {}) or {}
    return st if isinstance(st, dict) else {}


def save_global_settings(patch: dict) -> dict:
    """写全局配置（只改传入的字段，其余保留）。"""
    cur = load_global_settings()
    for k, v in (patch or {}).items():
        cur[k] = v
    atomic_write_json(GLOBAL_SETTINGS_FILE, cur, indent=1)
    return cur


def pushplus_token_ok(token) -> bool:
    """PushPlus token 是一串 32 位十六进制，带不带连字符都行。

    它要进请求体（不是 URL path），但仍做基本格式校验，
    免得把「还没配」这类空值或手滑粘的整段文字发出去。
    """
    return bool(re.fullmatch(r"[0-9A-Za-z_-]{16,80}", str(token or "").strip()))


def is_token_dead(text) -> bool:
    """这段输出是不是「凭证过期」，而不是真的执行出错。"""
    return bool(TOKEN_DEAD.search(str(text or "")))


def notify_once(key, title, detail, bark_url="", serverchan_sendkey="",
                hours=20, pushplus_token="") -> bool:
    """同一类事 hours 小时内只推一次。

    每日奖励是每小时跑一轮，凭证过期时一小时失败一次；每轮都推的话
    一天能收到十几条一模一样的「失败」，等于没通知。按 key 去重，
    票过期一天提醒一次就够，重抓之后自然会好。
    """
    now = datetime.now()
    last = str((load_json(NOTIFY_STATE_FILE, {}) or {}).get(key) or "")
    if last:
        try:
            spent = (now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
            if spent < hours * 3600:
                return False
        except Exception:
            pass
    if not notify(title, detail, bark_url, serverchan_sendkey, pushplus_token):
        return False

    def mutate(st):
        st[key] = now.strftime("%Y-%m-%d %H:%M:%S")

    try:
        update_json(NOTIFY_STATE_FILE, mutate, default={})
    except Exception:
        pass
    return True


def collect_notify_settings(*cfgs) -> dict:
    """收集推送配置，返回各通道的最终取值。

    优先级：环境变量 > 设置页的全局配置 > 单账号配置。
    设置页就是「设置一次、永久生效」的地方——配一次全局 PushPlus，
    所有账号、所有脚本都能收到，不用每个账号再填一遍。
    返回 dict 而不是元组，是为了以后再加通道不用改所有调用点。
    """
    out = {"bark_url": "", "serverchan_sendkey": "", "pushplus_token": ""}
    for cfg in cfgs:
        if not cfg:
            continue
        for k in out:
            if not out[k]:
                out[k] = str(cfg.get(k) or "").strip()

    gs = load_global_settings()
    for k in out:
        v = str(gs.get(k) or "").strip()
        if v:
            out[k] = v
    for k, env_name in (("bark_url", "CMCC_BARK_URL"),
                        ("serverchan_sendkey", "CMCC_SERVERCHAN_SENDKEY"),
                        ("pushplus_token", "CMCC_PUSHPLUS_TOKEN")):
        v = os.environ.get(env_name, "").strip()
        if v:
            out[k] = v
    return out


# ---------------------------------------------------------------- 通知

def notify(title, detail, bark_url="", serverchan_sendkey="", pushplus_token=""):
    """推送到 Bark / Server酱 / PushPlus。失败只告警，绝不影响主流程。

    批量运行（run_all）会设 CMCC_NOTIFY_BATCH=1：此时子脚本遇到「凭证过期」
    不各自推送，由 run_all 收齐账号后合并成一条，避免两三个号各发一条。
    """
    if os.environ.get("CMCC_NOTIFY_BATCH") and is_token_dead(f"{title}\n{detail}"):
        return False

    gs = load_global_settings()
    bark_url = (bark_url or os.environ.get("CMCC_BARK_URL", "")
                or gs.get("bark_url") or "").strip()
    key = (serverchan_sendkey or os.environ.get("CMCC_SERVERCHAN_SENDKEY", "")
           or gs.get("serverchan_sendkey") or "").strip()
    pp = (pushplus_token or os.environ.get("CMCC_PUSHPLUS_TOKEN", "")
          or gs.get("pushplus_token") or "").strip()
    if not bark_url and not key and not pp:
        return False

    sent = False
    dry = os.environ.get("CMCC_NOTIFY_DRY")   # 测试期间：只走流程，不真发
    s = requests.Session()
    s.trust_env = False
    try:
        if key:
            # sendkey 会拼进 URL path，严格校验防止注入
            if not re.fullmatch(r"[0-9A-Za-z]+", key):
                print(f"  [通知] serverchan_sendkey 含非法字符，已跳过")
            elif dry:
                sent = True
            else:
                s.post(f"https://sctapi.ftqq.com/{key}.send",
                       data={"title": title, "desp": detail}, timeout=15)
                sent = True
        if pp:
            if not pushplus_token_ok(pp):
                print("  [通知] pushplus_token 格式不对，已跳过")
            elif dry:
                sent = True
            else:
                s.post(PUSHPLUS_URL,
                       json={"token": pp, "title": title, "content": detail,
                             "template": "html"},
                       timeout=15)
                sent = True
        if bark_url:
            if not bark_url.lower().startswith(("http://", "https://")):
                print("  [通知] bark_url 协议不合法，已跳过")
            elif dry:
                sent = True
            else:
                s.get(bark_url, params={"title": title, "body": detail}, timeout=15)
                sent = True
    except Exception as e:
        print(f"  [通知] 推送失败（不影响本次执行）：{e}")
    return sent


# ---------------------------------------------------------------- 会话与调用

def make_session(token=None, headers=None, referer=None):
    """构造一个直连（不走系统代理）的会话。

    走系统代理容易被抓包工具或公司代理干扰，这几个接口直连更稳。
    """
    s = requests.Session()
    s.trust_env = False
    h = {"User-Agent": UA_MOBILE, "Accept": "*/*",
         "Accept-Language": "zh-CN,zh-Hans;q=0.9"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if referer:
        h["Referer"] = referer
    if headers:
        h.update(headers)
    s.headers.update(h)
    return s


def _norm(resp):
    """把 HTTP 响应规整成 {ok, code, msg, data}。"""
    if resp.status_code == 401:
        return {"ok": False, "code": 401,
                "msg": "token 失效，需重新抓包更新", "data": None}
    if resp.status_code != 200:
        return {"ok": False, "code": resp.status_code,
                "msg": f"HTTP {resp.status_code}", "data": None}
    try:
        j = resp.json()
    except Exception:
        return {"ok": False, "code": -1, "msg": "响应不是合法 JSON", "data": None}
    ok = bool(j.get("success")) or str(j.get("code")) in ("0", "0000", "200")
    return {"ok": ok, "code": j.get("code"), "msg": j.get("msg"), "data": j.get("data")}


def _refresh_ts(payload):
    """重试前刷新 timestamp（保持原来的 str/int 类型）。

    BASS 的 1006「操作太快」按时间戳判定，带着旧时间戳重试只会继续被拒。
    """
    if isinstance(payload, dict) and "timestamp" in payload:
        old = payload["timestamp"]
        payload["timestamp"] = now_ms() if isinstance(old, str) else int(now_ms())


def api_get(session, url, params=None, retries=1, timeout=25, retry_codes=("-100",)):
    """GET 调用。命中 retry_codes 时刷新时间戳并退避重试。"""
    params = dict(params or {})
    last = None
    for attempt in range(retries + 1):
        try:
            res = _norm(session.get(url, params=params, timeout=timeout))
            if res["ok"] or str(res.get("code")) not in retry_codes or attempt >= retries:
                return res
            last = res
        except Exception as e:
            last = {"ok": False, "code": -1, "msg": f"网络异常: {e}", "data": None}
        if attempt < retries:
            time.sleep(2.5)
            _refresh_ts(params)
    return last


def api_post(session, url, body=None, retries=1, timeout=25, retry_codes=("-100", "1006")):
    """POST 调用（JSON body）。命中 retry_codes 时刷新时间戳并退避重试。"""
    body = dict(body or {})
    last = None
    for attempt in range(retries + 1):
        try:
            res = _norm(session.post(url, json=body, timeout=timeout))
            if res["ok"] or str(res.get("code")) not in retry_codes or attempt >= retries:
                return res
            last = res
        except Exception as e:
            last = {"ok": False, "code": -1, "msg": f"网络异常: {e}", "data": None}
        if attempt < retries:
            time.sleep(3.5)
            _refresh_ts(body)
    return last


# ---------------------------------------------------------------- 输出

def show(label, res, extra=""):
    """统一展示一次接口调用结果，返回是否成功。"""
    if not res:
        print(f"  !! {label:<24} 无响应")
        return False
    if res.get("ok"):
        print(f"  ✓ {label:<24} code={res.get('code')}{extra}")
        return True
    print(f"  !! {label:<24} code={res.get('code')} {res.get('msg') or ''}{extra}")
    return False


def mask_phone(phone):
    """手机号脱敏，显示用。"""
    s = str(phone or "")
    return f"...{s[-4:]}" if len(s) >= 4 else s


# ---------------------------------------------------------------- 结构化结果

RESULT_MARK = "@@RESULT "


def emit_result(payload):
    """在 stdout 末尾打一行机器可读结果，供控制台取数。

    控制台原先靠正则匹配中文日志（「当前累计签到 N 天」）取统计，改一句文案
    就会静默失准。这里把数字直接结构化输出，日志文案随便改都不影响统计。
    """
    print(RESULT_MARK + json.dumps(payload, ensure_ascii=False))


def parse_result(text):
    """取输出里最后一条 @@RESULT；没有或解析失败返回 None。"""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith(RESULT_MARK):
            try:
                obj = json.loads(line[len(RESULT_MARK):])
            except Exception:
                return None
            return obj if isinstance(obj, dict) else None
    return None
