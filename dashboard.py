#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py —— 中国移动自动签到 · 图形控制台（Web 版）

启动后自动打开浏览器 http://127.0.0.1:8765
仅用 Python 标准库（http.server），无新增依赖。

功能：
  * 总览仪表盘：每个号码一张卡片 —— 累计领流量(MB)、完成任务数、连签天数、
    今日签到状态、饲料余额、流量银行可兑、Token 状态，全部大数字展示 + 7 天历史
  * 一键操作：签到（全部/单号/单平台）、BASS 任务、消消乐、状态刷新（dry-run 拉实时数据）
  * Token 管理：qwhdhub 短信登录向导（图形验证码内嵌显示）、HAR 导入、BASS token 保存
  * 定时任务：查询/注册/注销 Windows 计划任务
  * 实时日志控制台 + 历史统计持久化（gui_data/stats.json）

放置：与 run_all.py 同目录，双击 run_gui.bat 或 python dashboard.py
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from common import atomic_write_json, parse_result, update_json

# PyInstaller 冻结后 __file__ 指向临时解压目录，BASE 改用 exe 所在目录（项目根）
if getattr(sys, "frozen", False):
    BASE = Path(sys.executable).resolve().parent
else:
    BASE = Path(__file__).resolve().parent
DATA = BASE / "gui_data"
DATA.mkdir(exist_ok=True)
STATS_FILE = DATA / "stats.json"
RUNS_FILE = DATA / "runs.json"
# 估算口径：一次人工签到（开App/切号/点任务/领奖励）约 5 分钟，自动化跑一轮即节省该时间
MANUAL_MIN_PER_RUN = 5
PORT = int(os.environ.get("CMCC_DASH_PORT", "8765"))
# 冻结后 sys.executable 是 exe 自身，任务脚本需回退到系统 Python
if getattr(sys, "frozen", False):
    import shutil as _shutil
    PY = _shutil.which("python") or _shutil.which("python3") or "python"
else:
    PY = sys.executable
IS_WIN = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0

TODAY = lambda: datetime.now().strftime("%Y-%m-%d")


# ======================================================================
# 统计存储
# ======================================================================

def load_stats() -> dict:
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


RUNS_LOCK = threading.Lock()


def load_runs() -> list:
    try:
        return json.loads(RUNS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def append_run(label: str, sec: float, ok: bool):
    def mutate(runs):
        runs.append({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "label": label, "sec": round(sec, 1), "ok": ok})
        del runs[:-500]
    with RUNS_LOCK:
        update_json(RUNS_FILE, mutate, default=[], indent=0)


def efficiency() -> dict:
    runs = load_runs()
    total = len(runs)
    ok = sum(1 for r in runs if r.get("ok"))
    sec = sum(float(r.get("sec") or 0) for r in runs)
    today = TODAY()
    today_runs = [r for r in runs if str(r.get("ts", "")).startswith(today)]
    return {
        "total_runs": total,
        "ok_runs": ok,
        "success_rate": round(ok * 100.0 / total, 1) if total else None,
        "machine_sec": round(sec),
        "saved_min": ok * MANUAL_MIN_PER_RUN,
        "today_runs": len(today_runs),
        "recent": runs[-12:][::-1],
    }


STATS_LOCK = threading.Lock()


# stats.json 里每个账号的字段口径：
#   flow_mb_total / tasks_done_total / feeds_total —— 累计量
#   sign_days / today_signed_qwhd / today_signed_bass —— 签到状态
#   fodder / stage / fb_exchange —— BASS 实时状态
#   token_qwhd / token_bass —— ok / expired / empty
#   last_reward / last_run / history[{date, flow_mb, tasks, feeds}]


def update_stats(key: str, **kw):
    """更新某账号的统计字段。

    跨进程加锁：计划任务与一键签到是不同进程，threading.Lock 管不到，
    不加锁会整份互相覆盖（原子写只防半截文件，不防丢更新）。
    """
    with STATS_LOCK:
        update_json(STATS_FILE, lambda st: st.setdefault(key, {}).update(kw))


def bump_daily(key: str, flow_mb: float = 0, tasks: int = 0, feeds: int = 0):
    """把当日领取量记入 history（同日累加）。"""
    def mutate(st):
        a = st.setdefault(key, {"history": []})
        hist = a.setdefault("history", [])
        today = TODAY()
        row = next((h for h in hist if h.get("date") == today), None)
        if row is None:
            row = {"date": today, "flow_mb": 0.0, "tasks": 0, "feeds": 0}
            hist.append(row)
        row["flow_mb"] = round(row.get("flow_mb", 0) + flow_mb, 1)
        row["tasks"] = row.get("tasks", 0) + tasks
        row["feeds"] = row.get("feeds", 0) + feeds
        a["history"] = hist[-30:]
        if flow_mb:
            a["flow_mb_total"] = round(a.get("flow_mb_total", 0) + flow_mb, 1)
        if tasks:
            a["tasks_done_total"] = a.get("tasks_done_total", 0) + tasks
        if feeds:
            a["feeds_total"] = a.get("feeds_total", 0) + feeds
    with STATS_LOCK:
        update_json(STATS_FILE, mutate)


# ======================================================================
# 配置扫描
# ======================================================================

def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def scan_accounts():
    """返回 [{key, plat, file, phone, token_state}]"""
    out = []
    for plat, d, field in (("qwhdhub", BASE / "configs", "app_token"),
                           ("BASS", BASE / "configs_fj", "bearer_token")):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            cfg = load_json(f) or {}
            tok = str(cfg.get(field) or "")
            state = "empty" if (not tok or "<" in tok) else "filled"
            out.append({"key": f.stem, "plat": plat, "file": str(f.relative_to(BASE)),
                        "phone": str(cfg.get("phone") or ""), "token_state": state})
    return out


# ======================================================================
# 输出解析 —— 把脚本 stdout 变成统计数字
# ======================================================================

def _apply_qwhd_result(key: str, r: dict):
    """用脚本输出的结构化结果更新统计（首选路径，不依赖日志文案）。"""
    upd = {"last_run": datetime.now().strftime("%m-%d %H:%M")}
    if r.get("sign_days") is not None:
        upd["sign_days"] = r["sign_days"]
    if r.get("today_signed"):
        upd["today_signed_qwhd"] = TODAY()
    if r.get("last_reward"):
        upd["last_reward"] = r["last_reward"]
    # token=error 是网络/入口异常，不能据此判凭证失效
    if r.get("token") in ("ok", "expired"):
        upd["token_qwhd"] = r["token"]
    update_stats(key, **upd)
    flow = float(r.get("flow_mb") or 0)
    if flow > 0 and r.get("today_signed"):
        bump_daily(key, flow_mb=flow)


def _apply_bass_result(key: str, r: dict):
    """用脚本输出的结构化结果更新统计（首选路径，不依赖日志文案）。"""
    upd = {"last_run": datetime.now().strftime("%m-%d %H:%M")}
    if r.get("fodder") is not None:
        upd["fodder"] = r["fodder"]
    if r.get("stage"):
        upd["stage"] = r["stage"]
    if r.get("fb_exchange"):
        upd["fb_exchange"] = r["fb_exchange"]
    if r.get("today_signed"):
        upd["today_signed_bass"] = TODAY()
    if r.get("last_reward"):
        upd["last_reward"] = r["last_reward"]
    # idle=本地短路，本轮根本没发请求，不能据此说 token 有效
    if r.get("token") in ("ok", "expired") and not r.get("idle"):
        upd["token_bass"] = r["token"]
    update_stats(key, **upd)
    flow = float(r.get("flow_mb") or 0)
    tasks = int(r.get("tasks") or 0)
    feeds = int(r.get("feeds") or 0)
    if flow or tasks or feeds:
        bump_daily(key, flow_mb=flow, tasks=tasks, feeds=feeds)


def parse_qwhd_output(key: str, text: str):
    r = parse_result(text)
    if r and r.get("plat") == "qwhd":
        _apply_qwhd_result(key, r)
        return
    _parse_qwhd_legacy(key, text)


def _parse_qwhd_legacy(key: str, text: str):
    """兜底：旧版脚本没有 @@RESULT 行时，仍按日志文案解析。"""
    upd = {}
    m = re.search(r"当前累计签到 (\S+) 天，今日(已签|未签)", text)
    if m:
        upd["sign_days"] = m.group(1)
        if m.group(2) == "已签":
            upd["today_signed_qwhd"] = TODAY()
    m = re.search(r"签到成功！累计 (\S+) 天", text)
    if m:
        upd["sign_days"] = m.group(1)
        upd["today_signed_qwhd"] = TODAY()
    if "今日已签到" in text:
        upd["today_signed_qwhd"] = TODAY()
    # 奖品 → 流量 MB
    flow = 0.0
    for m in re.finditer(r"获得奖品[:：]\s*(.+)", text):
        line = m.group(1)
        upd["last_reward"] = line.strip()
        mm = re.search(r"(\d+(?:\.\d+)?)\s*(MB|M\b)", line, re.I)
        if mm:
            flow += float(mm.group(1))
        mm = re.search(r"(\d+(?:\.\d+)?)\s*GB", line, re.I)
        if mm:
            flow += float(mm.group(1)) * 1024
    # token 失效判断：
    # 「jwt 续期失败(APP登录状态已过期)」是常态提示，紧接着会回落 appTokenLogin
    # 并「活动会话建立成功」，此时 token 依然可用，不能算失效。
    session_ok = "活动会话建立成功" in text
    if "appTokenLogin 失败" in text and not session_ok:
        upd["token_qwhd"] = "expired"
    elif session_ok or upd.get("sign_days") is not None:
        upd["token_qwhd"] = "ok"
    if "领奖" in text:
        upd["last_run"] = datetime.now().strftime("%m-%d %H:%M")
    if upd:
        update_stats(key, **upd)
    if flow > 0 and upd.get("today_signed_qwhd") == TODAY():
        bump_daily(key, flow_mb=flow)


def parse_bass_output(key: str, text: str):
    r = parse_result(text)
    if r and r.get("plat") == "bass":
        _apply_bass_result(key, r)
        return
    _parse_bass_legacy(key, text)


def _parse_bass_legacy(key: str, text: str):
    """兜底：旧版脚本没有 @@RESULT 行时，仍按日志文案解析。"""
    upd = {}
    m = re.search(r"饲料\s*(\d+)\s+今日已喂\s*(\d+) 次", text)
    if m:
        upd["fodder"] = int(m.group(1))
    m = re.search(r"签到：今日(已签|未签)\s*循环第 (\S+) 天", text)
    if m:
        if m.group(1) == "已签":
            upd["today_signed_bass"] = TODAY()
    m = re.search(r"阶段：(\S+)\s+饲料 (\d+)/(\d+)", text)
    if m:
        upd["stage"] = f"{m.group(1)} {m.group(2)}/{m.group(3)}"
        upd.setdefault("fodder", None)
    m = re.search(r"流量银行：可兑 (\d+(?:\.\d+)?)(MB|GB)", text)
    if m:
        upd["fb_exchange"] = m.group(1) + m.group(2)
    m = re.search(r"流量银行现可兑 (\d+(?:\.\d+)?)(MB|GB)", text)
    if m:
        upd["fb_exchange"] = m.group(1) + m.group(2)
    if "token 失效" in text:
        upd["token_bass"] = "expired"
    elif upd:
        upd["token_bass"] = "ok"
    # 阶段奖励奖品：cmcc_fj.py 输出「领到：0.5元话费券」/「领到：500MB月包券」
    flow = 0.0
    for m in re.finditer(r"领到：\s*(.+)", text):
        line = m.group(1).strip()
        if not line or "未返回" in line:
            continue
        upd["last_reward"] = line
        mm = re.search(r"(\d+(?:\.\d+)?)\s*MB", line, re.I)
        if mm:
            flow += float(mm.group(1))
        mm = re.search(r"(\d+(?:\.\d+)?)\s*GB", line, re.I)
        if mm:
            flow += float(mm.group(1)) * 1024
    # 完成任务计数（doTask / fb任务 成功行；common.show 用 ✓，旧版用 OK）
    tasks = len(re.findall(r"(?:OK|✓)\s+(?:doTask|fb任务)", text))
    # 喂养次数（「✓ 喂养 1/11」这类行）
    feeds = len(re.findall(r"(?:OK|✓)\s+喂养", text))
    upd["last_run"] = datetime.now().strftime("%m-%d %H:%M")
    if upd:
        update_stats(key, **upd)
    if tasks or flow or feeds:
        bump_daily(key, flow_mb=flow, tasks=tasks, feeds=feeds)


# ======================================================================
# 后台任务运行器（全局单任务，日志缓冲 + 完成回调解析）
# ======================================================================

class TaskRunner:
    def __init__(self):
        self.logs = deque(maxlen=4000)
        self.seq = 0
        self.running = False
        self.label = ""
        self.proc = None
        self._parser = None   # (fn, key) 完成后解析输出
        self._buf = []

    def push(self, line: str):
        self.seq += 1
        self.logs.append({"seq": self.seq, "t": datetime.now().strftime("%H:%M:%S"), "line": line.rstrip("\n")})

    def start(self, label, cmd, parser=None, shell=False):
        return self.start_sequence(label, [(cmd, parser)], shell=shell)

    def start_sequence(self, label, steps, shell=False):
        """顺序跑多条命令，共用一个运行槽与日志缓冲。

        steps = [(cmd, parser), ...]，parser 为 (fn, key) 或 None。
        「刷新全部账号状态」就是逐号 dry-run，不必再另起一套线程去掏内部字段。
        """
        if self.running:
            return False
        self.running = True
        self.label = label
        self.push(f"▶ {label}")
        threading.Thread(target=self._run_steps, args=(steps, shell), daemon=True).start()
        return True

    def _run_one(self, cmd, shell) -> int:
        """跑一条命令并把输出推进日志；返回退出码。"""
        self.push("$ " + (" ".join(map(str, cmd)) if isinstance(cmd, list) else str(cmd)))
        try:
            self.proc = subprocess.Popen(cmd, cwd=str(BASE), shell=shell,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         creationflags=NO_WINDOW)
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                self._buf.append(line)
                self.push(line)
            self.proc.wait()
            return self.proc.returncode
        except Exception as e:
            self.push(f"[异常] {e}")
            return -1
        finally:
            self.proc = None

    def _run_steps(self, steps, shell):
        rc = -1
        t0 = time.time()
        label = self.label
        try:
            for cmd, parser in steps:
                self._buf = []
                rc = self._run_one(cmd, shell)
                text = "".join(self._buf)
                if parser and text:
                    try:
                        fn, key = parser
                        fn(key, text)
                    except Exception as e:
                        self.push(f"[解析统计失败] {e}")
        finally:
            try:
                append_run(label, time.time() - t0, rc == 0)
            except Exception:
                pass
            self.running = False
            self.push(f"■ 完成（退出码 {rc}，耗时 {time.time()-t0:.1f}s）\n")
            self.label = ""

    def kill(self):
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass


RUNNER = TaskRunner()


def py(*args):
    return [PY, *[str(a) for a in args]]


# ======================================================================
# 动作
# ======================================================================

CONF_DIRS = ("configs", "configs_fj")


def safe_conf_path(rel):
    """把前端传来的配置路径限制在 configs/ 与 configs_fj/ 内，非法返回 None。

    早先直接 BASE / rel：传绝对路径会整个覆盖 BASE，传 ..\\.. 能读写项目外任意
    文件，而 /api/bass_cfg 只脱敏 bearer_token，app_token 会明文吐出去。
    """
    rel = str(rel or "").replace("\\", "/").strip()
    parts = rel.split("/")
    if len(parts) != 2 or parts[0] not in CONF_DIRS:
        return None
    name = parts[1]
    if (not name.endswith(".json") or name.startswith(".")
            or not re.fullmatch(r"[A-Za-z0-9_\-.]+", name)):
        return None
    f = (BASE / parts[0] / name).resolve()
    root = (BASE / parts[0]).resolve()
    return f if root in f.parents else None


def safe_project_path(p):
    """限制在项目目录内的已存在文件（HAR 等），非法返回 None。"""
    try:
        f = Path(str(p or "")).resolve()
    except Exception:
        return None
    root = BASE.resolve()
    if f == root or root not in f.parents:
        return None
    return f if f.is_file() else None


def find_account(pred):
    for a in scan_accounts():
        if pred(a):
            return a
    return None


def act_refresh_status():
    """逐个账号 dry-run，拉取实时状态刷新仪表盘。"""
    if RUNNER.running:
        return False
    steps = []
    for a in scan_accounts():
        is_qwhd = a["plat"] == "qwhdhub"
        field = "token_qwhd" if is_qwhd else "token_bass"
        if a["token_state"] == "empty":
            RUNNER.push(f"⚠ {a['key']}（{a['plat']}）token 未配置，跳过")
            update_stats(a["key"], **{field: "empty"})
            continue
        script = "cmcc_sign.py" if is_qwhd else "cmcc_fj.py"
        parser = (parse_qwhd_output if is_qwhd else parse_bass_output, a["key"])
        steps.append((py(script, "--config", a["file"], "--dry-run"), parser))
    if not steps:
        RUNNER.push("没有可刷新的账号（token 都为空）")
        return False
    return RUNNER.start_sequence("刷新全部账号状态（dry-run 只读）", steps)


def do_action(body: dict):
    act = body.get("action", "")
    accts = scan_accounts()

    if act == "refresh_status":
        return {"ok": act_refresh_status()}

    if act == "run_all":
        plat = body.get("platform", "all")
        cmd = py("run_all.py", "--platform", plat)
        if body.get("dry"):
            cmd.append("--dry-run")
        if body.get("xxl"):
            cmd.append("--xxl")
            if body.get("xxl_times"):
                cmd += ["--xxl-times", str(body["xxl_times"])]
        if body.get("seckill"):
            cmd.append("--seckill")
            if body.get("seckill_at"):
                cmd += ["--seckill-at", body["seckill_at"]]
        label = "一键签到（%s%s%s%s）" % (plat, "，dry-run" if body.get("dry") else "",
                                     "，含消消乐" if body.get("xxl") else "",
                                     "，含秒杀" if body.get("seckill") else "")
        return {"ok": RUNNER.start(label, cmd)}

    if act == "qwhd_run":
        a = next((x for x in accts if x["key"] == body.get("key")), None)
        if not a:
            return {"ok": False, "err": "账号不存在"}
        cmd = py("cmcc_sign.py", "--config", a["file"])
        if body.get("dry"): cmd.append("--dry-run")
        if body.get("claim"): cmd.append("--claim")
        if body.get("tasks"): cmd.append("--tasks")
        if body.get("games"): cmd.append("--games")
        return {"ok": RUNNER.start(f"qwhdhub 签到 · {a['key']}", cmd, parser=(parse_qwhd_output, a["key"]))}

    if act == "bass_run":
        a = next((x for x in accts if x["key"] == body.get("key")), None)
        if not a:
            return {"ok": False, "err": "账号不存在"}
        cmd = py("cmcc_fj.py", "--config", a["file"])
        if body.get("dry"): cmd.append("--dry-run")
        if body.get("claim"): cmd.append("--claim")
        if body.get("roll"): cmd.append("--roll")
        feed = int(body.get("feed") or 0)
        if feed: cmd += ["--feed", str(feed)]
        if body.get("prize"): cmd += ["--prize", str(body["prize"])]
        if body.get("auto_claim"): cmd.append("--auto-claim")
        if body.get("tasks"): cmd.append("--tasks")
        if body.get("fb"): cmd.append("--fb-tasks")
        return {"ok": RUNNER.start(f"BASS 任务 · {a['key']}", cmd, parser=(parse_bass_output, a["key"]))}

    if act == "bass_probe":
        a = next((x for x in accts if x["key"] == body.get("key")), None)
        if not a:
            return {"ok": False, "err": "账号不存在"}
        return {"ok": RUNNER.start(f"BASS 探测 · {a['key']}", py("probe_fj.py", "--config", a["file"]))}

    if act == "luck_run":
        cmd = py("cmcc_luck.py")
        if body.get("har"):
            hf = safe_project_path(body["har"])
            if hf is None:
                return {"ok": False, "err": "HAR 路径必须是项目内的文件"}
            cmd += ["--har", str(hf)]
        if body.get("dry"):
            cmd.append("--dry-run")
        if body.get("no_tasks"):
            cmd.append("--no-tasks")
        if body.get("max"):
            cmd += ["--max", str(body["max"])]
        return {"ok": RUNNER.start("优惠专区抽奖", cmd)}

    if act == "seckill_run":
        f = safe_conf_path(body.get("file"))
        if f is None or not f.is_file():
            return {"ok": False, "err": "配置文件非法或不存在"}
        at = str(body.get("at") or "").strip()
        if at and not re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", at):
            return {"ok": False, "err": "开抢时刻格式应为 HH:MM[:SS]"}
        cmd = py("cmcc_seckill.py", "--config", str(f))
        if body.get("dry"):
            cmd.append("--dry-run")
        elif body.get("once"):
            cmd.append("--once")
        elif at:
            cmd += ["--at", at]
        return {"ok": RUNNER.start(f"签到秒杀 · {f.name}", cmd)}

    if act == "xxl_run":
        f = safe_conf_path(body.get("file"))
        if f is None or not f.is_file():
            return {"ok": False, "err": "配置文件非法或不存在"}
        cmd = py("cmcc_xiaoxiaole.py", "--config", str(f))
        if body.get("dry"):
            cmd.append("--dry-run")
        elif body.get("detect"):
            cmd.append("--detect-level")
        else:
            cmd += ["--level", body.get("level", 49), "--star", body.get("star", 3),
                    "--times", body.get("times", 5), "--gap", body.get("gap", 12)]
        return {"ok": RUNNER.start("消消乐", cmd)}

    # ---- token 管理 ----
    if act == "har_import":
        try:
            import har_import as HI
        except Exception as e:
            return {"ok": False, "err": f"har_import 模块不可用：{e}"}
        har = body.get("har")
        try:
            if har:
                rep = HI.apply(har, key=(body.get("key") or None))
                used = 1
            else:
                d = body.get("dir") or None
                if d is not None:
                    dp = Path(str(d)).resolve()
                    root = BASE.resolve()
                    if dp != root and root not in dp.parents:
                        return {"ok": False, "err": "扫描目录必须在项目内"}
                if body.get("latest"):
                    # 只处理最新的一份，避免旧 HAR 里的过期票据覆盖新的
                    from common import find_har_files
                    cands = find_har_files()
                    if not cands:
                        return {"ok": False, "err": "没找到 HAR 文件（已查：项目目录 / 下载 / 微信接收目录）"}
                    newest = cands[0]
                    text = newest.read_text(encoding="utf-8", errors="ignore")
                    rep = [f"—— {newest.name}（{newest.stat().st_size // 1048576}MB）——"] + HI.apply(text, key=(body.get("key") or None))
                    used = 1
                else:
                    rep, used = HI.scan_dir(d)
        except Exception as e:
            return {"ok": False, "err": f"导入失败：{e}"}
        for line in rep:
            RUNNER.push("  " + line)
        RUNNER.push("■ HAR 导入结束")
        if not used:
            return {"ok": False, "err": "没有可用 token", "report": rep}
        return {"ok": True, "report": rep, "msg": f"HAR 导入完成（{used} 份）"}

    if act == "bass_token_save":
        f = safe_conf_path(body.get("file"))
        if f is None or f.parent.name != "configs_fj" or not f.is_file():
            return {"ok": False, "err": "配置文件不存在或不在 configs_fj 下"}
        cfg = load_json(f) or {}
        tok = str(body.get("token", "")).strip()
        if tok.startswith("Bearer "):
            tok = tok[7:].strip()
        cfg["bearer_token"] = tok
        if body.get("phone"):
            cfg["phone"] = str(body["phone"]).strip()
        atomic_write_json(f, cfg, indent=2)
        update_stats(f.stem, token_bass="filled" if tok else "empty")
        return {"ok": True, "msg": f"已保存 {f.name}（token {len(tok)} 字符）"}

    if act == "bass_token_new":
        name = str(body.get("name", "")).strip()
        if not name:
            return {"ok": False, "err": "文件名为空"}
        if not name.endswith(".json"):
            name += ".json"
        # 文件名会拼进路径，必须挡住 ..\ 与绝对路径
        if name.startswith(".") or not re.fullmatch(r"[A-Za-z0-9_\-.]+\.json", name):
            return {"ok": False, "err": "文件名只能用字母、数字、_ - ."}
        f = BASE / "configs_fj" / name
        f.parent.mkdir(exist_ok=True)
        if f.exists():
            return {"ok": False, "err": "已存在同名配置"}
        atomic_write_json(f, {"bearer_token": "", "app_id": "0",
                              "referer": "https://wap.fj.10086.cn/bass-cultivateGameServer/",
                              "phone": "", "note": ""}, indent=2)
        return {"ok": True, "msg": f"已新建 configs_fj/{name}"}

    # ---- 定时任务 ----
    if act == "sched_query":
        ps = ("foreach($n in 'MobileSignIn','MobileDailyReward','MobileJwtKeepAlive','MobileSeckill','MobileSessionRefresh','MobileTokenLifeProbe'){"
              "$t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue;"
              "if($t){$i=Get-ScheduledTaskInfo -TaskName $n;"
              "Write-Host ($n+'  状态='+$t.State+'  下次='+$i.NextRunTime+'  上次结果='+$i.LastTaskResult)}"
              "else{Write-Host ($n+'  未注册')}}")
        return {"ok": RUNNER.start("查询计划任务", ["powershell", "-NoProfile", "-Command", ps])}
    if act == "sched_reg":
        kind = body.get("kind", "sign")
        t = str(body.get("time") or "08:30").strip()
        # t 会插进单引号 PowerShell 字符串，不校验就能闭合引号注入任意命令
        if not re.fullmatch(r"\d{1,2}:\d{2}", t):
            return {"ok": False, "err": "时间格式应为 HH:MM"}
        if kind not in ("sign", "daily", "keepalive", "sess", "seckill", "probe"):
            return {"ok": False, "err": "未知任务类型"}
        if kind == "daily":
            # 每小时轮询领每日奖励三档：06:00 起每 1 小时一次，持续 17 小时
            name, bat = "MobileDailyReward", "run_daily_reward.bat"
            d = str(BASE)
            ps = (f"$a=New-ScheduledTaskAction -Execute '{d}\\{bat}' -WorkingDirectory '{d}';"
                  f"$tr=New-ScheduledTaskTrigger -Daily -At '{t}';"
                  f"$r=(New-ScheduledTaskTrigger -Once -At '{t}' -RepetitionInterval (New-TimeSpan -Hours 1) "
                  f"-RepetitionDuration (New-TimeSpan -Hours 17)).Repetition;"
                  f"$tr.Repetition=$r;"
                  f"Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $tr -Force | Out-Null;"
                  f"Write-Host '{name} 已注册，{t} 起每小时一次（持续 17 小时）'")
        elif kind == "keepalive":
            # qwhdhub 会话保活：每 90 分钟只试登录一次（--login-only，不签到）。
            # jwt 单独用服务端不认，续不了命；失败即代表 app_token 过期、该重新抓包
            name, bat = "MobileJwtKeepAlive", "run_jwt_keepalive.bat"
            d = str(BASE)
            ps = (f"$a=New-ScheduledTaskAction -Execute '{d}\\{bat}' -WorkingDirectory '{d}';"
                  f"$tr=New-ScheduledTaskTrigger -Daily -At '{t}';"
                  f"$r=(New-ScheduledTaskTrigger -Once -At '{t}' -RepetitionInterval (New-TimeSpan -Minutes 90) "
                  f"-RepetitionDuration (New-TimeSpan -Days 1)).Repetition;"
                  f"$tr.Repetition=$r;"
                  f"Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $tr -Force | Out-Null;"
                  f"Write-Host '{name} 已注册，{t} 起每 90 分钟一次（全天覆盖）'")
        elif kind == "sess":
            # 会话刷新：活动会话寿命 2 小时，10:15 建立的新会话覆盖 12:00 开抢
            name, bat = "MobileSessionRefresh", "run_session_refresh.bat"
            d = str(BASE)
            ps = (f"$a=New-ScheduledTaskAction -Execute '{d}\\{bat}' -WorkingDirectory '{d}';"
                  f"$tr=New-ScheduledTaskTrigger -Daily -At '{t}';"
                  f"Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $tr -Force | Out-Null;"
                  f"Write-Host '{name} 已注册，每天 {t} 运行（刷新活动会话，覆盖 12 点开抢）'")
        elif kind == "seckill":
            # 秒杀抢券：10:30 启动，脚本内部等到 12:00 再出手。
            # 靠的是 10:15 那次签到建立的活动会话（2 小时寿命），不是 app_token
            name, bat = "MobileSeckill", "run_seckill.bat"
            d = str(BASE)
            ps = (f"$a=New-ScheduledTaskAction -Execute '{d}\\{bat}' -WorkingDirectory '{d}';"
                  f"$tr=New-ScheduledTaskTrigger -Daily -At '{t}';"
                  f"Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $tr -Force | Out-Null;"
                  f"Write-Host '{name} 已注册，每天 {t} 运行（脚本内部等到 12:00 开抢）'")
        elif kind == "probe":
            # 票据寿命探测：每 2 分钟探一次，签发时刻取自 HAR，结论精确到分钟
            name, bat = "MobileTokenLifeProbe", "run_token_life.bat"
            d = str(BASE)
            ps = (f"$a=New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c \"{d}\\{bat}\"' -WorkingDirectory '{d}';"
                  f"$tr=New-ScheduledTaskTrigger -Daily -At '{t}';"
                  f"$r=(New-ScheduledTaskTrigger -Once -At '{t}' -RepetitionInterval (New-TimeSpan -Minutes 2) "
                  f"-RepetitionDuration (New-TimeSpan -Days 1)).Repetition;"
                  f"$tr.Repetition=$r;"
                  f"Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $tr -Force | Out-Null;"
                  f"Write-Host '{name} 已注册，{t} 起每 2 分钟一次（全天覆盖）'")
        else:
            name, bat = "MobileSignIn", "run_scheduled.bat"
            d = str(BASE)
            ps = (f"$a=New-ScheduledTaskAction -Execute '{d}\\{bat}' -WorkingDirectory '{d}';"
                  f"$tr=New-ScheduledTaskTrigger -Daily -At '{t}';"
                  f"Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $tr -Force | Out-Null;"
                  f"Write-Host '{name} 已注册，每天 {t} 运行'")
        return {"ok": RUNNER.start(f"注册计划任务 {name}", ["powershell", "-NoProfile", "-Command", ps])}
    if act == "sched_unreg":
        name = body.get("name", "")
        if name not in ("MobileSignIn", "MobileDailyReward", "MobileJwtKeepAlive",
                        "MobileSeckill", "MobileSessionRefresh", "MobileTokenLifeProbe"):
            return {"ok": False, "err": "非法任务名"}
        ps = f"Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false; Write-Host '{name} 已注销'"
        return {"ok": RUNNER.start(f"注销计划任务 {name}", ["powershell", "-NoProfile", "-Command", ps])}

    if act == "stop":
        RUNNER.kill()
        return {"ok": True}

    return {"ok": False, "err": f"未知动作 {act}"}


# ======================================================================
# HTTP 服务
# ======================================================================

def build_state():
    st = load_stats()
    accts = scan_accounts()
    # 按手机号分组
    phones = {}
    for a in accts:
        p = a["phone"] or a["key"]
        g = phones.setdefault(p, {"phone": p, "plats": {}})
        s = st.get(a["key"], {})
        g["plats"][a["plat"]] = {**a, "stats": s}
    return {
        "accounts": list(phones.values()),
        "raw_accounts": accts,
        "stats": st,
        "eff": efficiency(),
        "task": {"running": RUNNER.running, "label": RUNNER.label},
        "bass_files": [str(f.relative_to(BASE)) for f in sorted((BASE / "configs_fj").glob("*.json"))] if (BASE / "configs_fj").exists() else [],
        "flowbank": _load_json_safe(DATA / "flowbank_state.json"),
        "daily": _load_json_safe(DATA / "daily_state.json"),
        "luck": _load_json_safe(DATA / "luck_state.json"),
        "seckill_sessions": _list_seckill_sessions(),
        "token_life": _list_token_life(),
    }


def _list_token_life() -> list:
    """列出各账号的 app_token 寿命探测结论。

    探测由计划任务 MobileTokenLifeProbe 每 2 分钟跑一次，状态落在
    gui_data/token_life/<尾号>.json。这里只读出来展示，不发任何请求。
    """
    out = []
    d = DATA / "token_life"
    if not d.exists():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            o = load_json(f) or {}
        except Exception:
            continue
        s = o.get("samples") or []
        out.append({
            "key": f.stem,
            "phone": o.get("phone") or "",
            "state": o.get("state") or "unknown",
            "issued_at": o.get("issued_at") or "",
            "updated": o.get("updated") or "",
            "conclusion": o.get("conclusion") or "",
            "last_ok": bool(s and s[-1].get("ok")),
            "samples": len(s),
        })
    return out


def _list_seckill_sessions() -> list:
    """列出已保存的活动会话（按账号），供控制台展示秒杀就绪状态。

    会话实测寿命 2 小时，所以同时算出剩余时间——这决定能不能赶上 12 点开抢。
    """
    out = []
    for f in sorted(DATA.glob("seckill_session_*.json")):
        try:
            o = load_json(f) or {}
            at = o.get("at") or ""
            exp = o.get("expires_at") or ""
            left_min = None
            if exp:
                try:
                    # 本模块是 from datetime import datetime，写成 datetime.datetime
                    # 会抛 AttributeError 并被下面吞掉，剩余分钟就永远是 null
                    left_min = round((datetime.fromisoformat(exp)
                                      - datetime.now()).total_seconds() / 60)
                except Exception:
                    pass
            out.append({
                "file": f.name,
                "phone": o.get("phone") or "",
                "at": at,
                "expires_at": exp,
                "left_min": left_min,
                "keys": list((o.get("cookie") or {}).keys()),
            })
        except Exception:
            pass
    return out


def _load_json_safe(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
# HAR 走浏览器上传时会被 JSON 包裹（体积再涨一点），实测抓包文件常在 20~70MB。
# 64MB 上限会把正常 HAR 直接拒掉（413），所以放宽到 256MB。
MAX_BODY = 256 * 1024 * 1024


def _host_of(value) -> str:
    """从 Host / Origin 头里取出主机名（去掉协议、端口、路径）。"""
    v = str(value or "").strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):
        return v.split("]")[0] + "]"
    return v.rsplit(":", 1)[0] if ":" in v else v


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _local_only(self) -> bool:
        """只接受本机来源，挡住 DNS rebinding 与恶意网页的跨站请求。

        服务虽然只监听 127.0.0.1，但浏览器里任何网页都能向它发请求；
        POST 用 text/plain 属「简单请求」不触发预检，所以必须自己校验来源。
        """
        if _host_of(self.headers.get("Host")) not in LOCAL_HOSTS:
            return False
        origin = self.headers.get("Origin")
        return not origin or _host_of(origin) in LOCAL_HOSTS

    def _send(self, code, ctype, data: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode())

    def do_GET(self):
        if not self._local_only():
            self._json({"err": "forbidden"}, 403)
            return
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode())
        elif u.path == "/api/state":
            self._json(build_state())
        elif u.path == "/api/logs":
            try:
                since = int((q.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            lines = [l for l in RUNNER.logs if l["seq"] > since]
            self._json({"lines": lines, "running": RUNNER.running, "label": RUNNER.label})
        else:
            self._json({"err": "not found"}, 404)

    def do_POST(self):
        if not self._local_only():
            self._json({"err": "forbidden"}, 403)
            return
        # 要求 application/json：跨站「简单请求」发不出这个类型，会被预检挡住
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._json({"err": "需要 application/json"}, 415)
            return
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > MAX_BODY:
            self._json({"err": "请求体过大"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if u.path == "/api/action":
            try:
                self._json(do_action(body))
            except Exception as e:
                self._json({"ok": False, "err": str(e)}, 500)
        else:
            self._json({"err": "not found"}, 404)


# ======================================================================
# 前端页面（单文件，深色仪表盘风格）
# ======================================================================

def _load_frontend() -> str:
    """读取前端页面（单独放 static/index.html，便于格式化与 lint）。

    冻结成 exe 后页面在 PyInstaller 的解包目录里（_MEIPASS），源码运行时在项目根目录。
    两处都找不到就直接报错退出，而不是启动一个空白控制台。
    """
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", str(BASE))) / "static" / "index.html")
    candidates.append(BASE / "static" / "index.html")
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise SystemExit("缺少 static/index.html：请把它与程序放在同一目录"
                     "（打包时由 --add-data \"static;static\" 带上）")


INDEX_HTML = _load_frontend()
