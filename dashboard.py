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

from common import (atomic_write_json, load_policy, parse_result,
                    save_policy, update_json)

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

# 冻结成 exe 后任务脚本交给系统 Python 跑。缺 Python / 缺 requests 时，
# 早先只在日志里吐「[WinError 2] 系统找不到指定的文件」，用户看不懂也没法自救，
# 所以这里提前检查并给出可照做的说明。源码运行时解释器与 requests 必然可用。
_PREFLIGHT = {"at": 0.0, "ok": True, "msg": ""}


def preflight(force=False):
    """检查任务脚本能否执行，返回 (ok, 人话说明)。结果缓存 5 分钟。"""
    if not getattr(sys, "frozen", False):
        return True, ""
    now = time.time()
    if not force and _PREFLIGHT["at"] and now - _PREFLIGHT["at"] < 300:
        return _PREFLIGHT["ok"], _PREFLIGHT["msg"]
    import shutil
    exe = shutil.which("python") or shutil.which("python3")
    if not exe:
        ok, msg = False, ("找不到系统 Python，签到任务无法执行。\n\n"
                          "请安装 Python 3.9+（64 位），安装时勾选 "
                          "Add Python to PATH，装完重开本程序。\n"
                          "下载：https://www.python.org/downloads/")
    else:
        ok, msg = True, ""
        try:
            p = subprocess.run([exe, "-c", "import requests"], capture_output=True,
                               timeout=60, creationflags=NO_WINDOW)
            if p.returncode != 0:
                ok = False
                msg = (f"已找到 Python（{exe}），但缺少 requests 等依赖。\n\n"
                       f"请在项目目录打开 cmd 执行：\n"
                       f"  \"{exe}\" -m pip install -r requirements.txt")
        except Exception as e:
            ok, msg = False, f"无法验证 Python 环境（{exe}）：{e}"
    _PREFLIGHT.update(at=now, ok=ok, msg=msg)
    return ok, msg


def _busy_err() -> str:
    """单任务槽被占时的说明。要讲清「在跑什么、怎么办」，不能只说「可能正忙」。"""
    label = str(getattr(RUNNER, "label", "") or "")
    return (f"正在运行「{label}」，请等它结束，或点右上角「停止」"
            if label else "有任务正在运行，请等它结束，或点右上角「停止」")


def _start(label, cmd, parser=None, shell=False, need_py=True) -> dict:
    """所有「起一个任务」的动作共用：成功返回 ok，被占/环境不行返回可照做的原因。

    need_py=False 用于只跑 PowerShell 的计划任务操作：没装 Python 也该能注销任务。
    """
    if need_py:
        ok, msg = preflight()
        if not ok:
            return {"ok": False, "err": msg}
    started = RUNNER.start(label, cmd, parser=parser, shell=shell)
    if started:
        return {"ok": started}
    return {"ok": False, "err": _busy_err()}


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
        a["history"] = hist[-45:]
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


def _pythonw() -> str:
    """无窗口的 Python 解释器（pythonw.exe），找不到返回空串。

    计划任务用它 + run_hidden.py 启动 bat，桌面就不会再闪黑窗口。
    冻结态下 sys.executable 是 exe 自己，绝不能拿它当解释器——那会让计划任务
    每 2 分钟弹一次控制台窗口，而 bat 根本没跑。必须去找系统 Python。
    """
    if getattr(sys, "frozen", False):
        import shutil
        for name in ("pythonw", "python"):
            exe = shutil.which(name)
            if not exe:
                continue
            if name == "python":
                cand = Path(exe).with_name("pythonw.exe")
                if cand.exists():
                    return str(cand)
            return exe
        return ""
    cand = Path(sys.executable).with_name("pythonw.exe")
    return str(cand) if cand.exists() else sys.executable


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
            # 只报「有没有配推送」，不把 Bark 地址 / SendKey 本身吐给页面
            out.append({"key": f.stem, "plat": plat, "file": str(f.relative_to(BASE)),
                        "phone": str(cfg.get("phone") or ""), "token_state": state,
                        "has_bark": bool(str(cfg.get("bark_url") or "").strip()),
                        "has_sendkey": bool(str(cfg.get("serverchan_sendkey") or "").strip())})
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


def parse_seckill_output(key: str, text: str):
    """秒杀结果解析：只取奖品名，填进「最近奖品」。

    真正的收益统计走 common.record_award（脚本自己写 awards.json），
    这里只负责让账号卡片上的「最近奖品」也显示秒杀券。
    """
    r = parse_result(text)
    if not r or r.get("plat") != "seckill":
        return
    if r.get("last_reward"):
        update_stats(key, last_reward=r["last_reward"])


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
        self._cancel = threading.Event()

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
        self._cancel.clear()
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
                # 点「停止」后剩余步骤不再执行：早先只杀当前子进程，
                # 循环会接着跑下一个账号，看起来就是「停不下来」。
                if self._cancel.is_set():
                    self.push("■ 已停止，剩余步骤不再执行")
                    break
                self._buf = []
                rc = self._run_one(cmd, shell)
                text = "".join(self._buf)
                if parser and text and not self._cancel.is_set():
                    try:
                        fn, key = parser
                        fn(key, text)
                    except Exception as e:
                        self.push(f"[解析统计失败] {e}")
                if self._cancel.is_set():
                    self.push("■ 已停止")
                    break
        finally:
            try:
                append_run(label, time.time() - t0, rc == 0)
            except Exception:
                pass
            self.running = False
            self.push(f"■ 完成（退出码 {rc}，耗时 {time.time()-t0:.1f}s）\n")
            self.label = ""

    def kill(self) -> bool:
        """请求停止：置取消标志 + 杀整棵进程树。

        只 kill 直接子进程是不够的：一键签到跑的是 run_all.py，它自己又用
        subprocess.run 起了 cmcc_*.py —— 父进程被杀后孙子进程照样把请求发完。
        Windows 用 taskkill /T 连子孙一起结束；其它平台退回 kill 本身。
        """
        self._cancel.set()
        proc = self.proc
        if proc is None:
            return False
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=15, creationflags=NO_WINDOW)
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return True


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


def _new_account(plat: str, name, phone) -> dict:
    """在界面里新建一份账号配置。

    原先只能手工复制 config*.example.json，而 BASS 的 HAR 导入又要求目标文件
    已存在 —— 全新安装因此在界面里走不通。这里补上入口。
    """
    if plat not in ("qwhdhub", "BASS"):
        return {"ok": False, "err": "请选择平台（全国签到 / 福建活动）"}
    phone = str(phone or "").strip()
    if phone and not re.fullmatch(r"1[3-9]\d{9}", phone):
        return {"ok": False, "err": "手机号应为 11 位数字（1 开头）"}
    name = str(name or "").strip() or (phone[-4:] if phone else "")
    if not name:
        return {"ok": False, "err": "请填手机号（自动用尾号做文件名），或自定义文件名"}
    if not name.endswith(".json"):
        name += ".json"
    # 文件名会拼进路径，必须挡住 ..\ 与绝对路径
    if name.startswith(".") or not re.fullmatch(r"[A-Za-z0-9_\-.]+\.json", name):
        return {"ok": False, "err": "文件名只能用字母、数字、_ - ."}
    sub = "configs" if plat == "qwhdhub" else "configs_fj"
    f = BASE / sub / name
    if f.exists():
        return {"ok": False, "err": f"已存在同名配置 {sub}/{name}"}
    if plat == "qwhdhub":
        cfg = load_json(BASE / "config.example.json") or {}
        cfg.update({"app_token": "", "phone": phone})
    else:
        cfg = {"bearer_token": "", "app_id": "0", "prize_prefer": "flow",
               "referer": "https://wap.fj.10086.cn/bass-cultivateGameServer/",
               "phone": phone, "note": "", "bark_url": "", "serverchan_sendkey": ""}
    f.parent.mkdir(exist_ok=True)
    atomic_write_json(f, cfg, indent=2)
    return {"ok": True, "msg": f"已新建 {sub}/{name}，再导入 HAR 填入凭证即可"}


def act_refresh_status() -> dict:
    """逐个账号 dry-run，拉取实时状态刷新仪表盘。"""
    if RUNNER.running:
        return {"ok": False, "err": _busy_err()}
    ok, msg = preflight()
    if not ok:
        return {"ok": False, "err": msg}
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
        return {"ok": False,
                "err": "还没有可刷新的账号：请到「凭证与账号」页点「新建账号」，再导入抓包文件"}
    started = RUNNER.start_sequence("刷新全部账号状态（只读检查）", steps)
    return {"ok": started} if started else {"ok": False, "err": _busy_err()}


# 7 个计划任务的唯一定义处：名字、脚本、触发器类型、注册后的提示语。
# 单个注册与「一键配置全部」都从这里取，两处不会跑偏。
SCHED_DEF = {
    "sign":      ("MobileSignIn", "run_scheduled.bat", "daily",
                  "每天 {t} 运行"),
    "daily":     ("MobileDailyReward", "run_daily_reward.bat", "1h17",
                  "{t} 起每小时一次（持续 17 小时）"),
    "keepalive": ("MobileJwtKeepAlive", "run_jwt_keepalive.bat", "90m1d",
                  "{t} 起每 90 分钟一次（全天覆盖）"),
    "sess":      ("MobileSessionRefresh", "run_session_refresh.bat", "daily",
                  "每天 {t} 运行（刷新活动会话，覆盖 12 点开抢）"),
    "seckill":   ("MobileSeckill", "run_seckill.bat", "daily",
                  "每天 {t} 运行（脚本内部等到 12:00 开抢）"),
    "games":     ("MobileGames", "run_games.bat", "daily",
                  "每天 {t} 运行（游戏中心 / 追剧 / 抽话费）"),
    "probe":     ("MobileTokenLifeProbe", "run_token_life.bat", "2m1d",
                  "{t} 起每 2 分钟一次（全天覆盖）"),
}

# 推荐方案：一键配置全部时用的时刻。和 README / 文档里的说明保持一致。
SCHED_PLAN = [
    ("sign", "08:30"),
    ("daily", "06:00"),
    ("keepalive", "00:00"),
    ("sess", "11:54"),
    ("seckill", "11:55"),
    ("games", "12:05"),
    ("probe", "00:00"),
]

# 每个计划任务的日志文件名（与各 bat 里写的一致）。
# 界面上「失败（见日志）」要能直接点开，不能让用户自己去文件夹翻。
SCHED_LOG = {
    "sign": "daily.log",
    "daily": "daily_reward.log",
    "keepalive": "keepalive.log",
    "sess": "session_refresh.log",
    "seckill": "seckill.log",
    "games": "games.log",
    "probe": "token_life.log",
}

_NO_PYW_ERR = ("找不到系统 Python（pythonw.exe），无法注册计划任务。\n"
               "请先安装 Python 3.9+ 并勾选 Add Python to PATH，再重开本程序。")

# 计划任务的中文名，提示语里用它而不是 MobileXxx 这种内部代号
SCHED_CN = {
    "sign": "每天签到", "daily": "每日奖励", "keepalive": "会话保活",
    "sess": "会话刷新", "seckill": "秒杀抢券", "games": "拓展活动",
    "probe": "票据寿命探测",
}


def read_task_log(name: str, tail: int = 200):
    """读某个计划任务日志的尾部若干行；文件名不在白名单内一律拒绝。"""
    if name not in SCHED_LOG.values():
        return None
    try:
        tail = max(1, min(1000, int(tail)))
    except Exception:
        tail = 200
    f = BASE / "logs" / name
    if not f.is_file():
        return {"name": name, "lines": [], "missing": True}
    try:
        # 日志可能几十 MB，只 seek 读尾部，别整份进内存
        with open(f, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 256 * 1024))
            text = fh.read().decode("utf-8", errors="replace")
        return {"name": name, "lines": text.splitlines()[-tail:], "size": size}
    except Exception as e:
        return {"name": name, "lines": [], "err": str(e)}


def _sched_ps(kind: str, t: str, pyw: str, launcher: str):
    """生成单个任务的注册脚本，返回 (任务名, PowerShell 片段)。

    触发器只有三种形态：每天定时、每小时若干次、以及 90 分钟 / 2 分钟的重复。
    注意不能用 `$tr.Repetition=` 去改重复——PS 5.1 上该属性不可写，写了等于没设。
    """
    name, bat, trig, note = SCHED_DEF[kind]
    d = str(BASE)
    if trig == "daily":
        trg = f"$tr=New-ScheduledTaskTrigger -Daily -At '{t}';"
    elif trig == "1h17":
        trg = (f"$tr=New-ScheduledTaskTrigger -Once -At '{t}' "
               f"-RepetitionInterval (New-TimeSpan -Hours 1) "
               f"-RepetitionDuration (New-TimeSpan -Hours 17);")
    elif trig == "90m1d":
        trg = (f"$tr=New-ScheduledTaskTrigger -Once -At '{t}' "
               f"-RepetitionInterval (New-TimeSpan -Minutes 90) "
               f"-RepetitionDuration (New-TimeSpan -Days 1);")
    else:  # 2m1d
        trg = (f"$tr=New-ScheduledTaskTrigger -Once -At '{t}' "
               f"-RepetitionInterval (New-TimeSpan -Minutes 2) "
               f"-RepetitionDuration (New-TimeSpan -Days 1);")
    ps = (f"$a=New-ScheduledTaskAction -Execute '{pyw}' "
          f"-Argument '\"{launcher}\" \"{d}\\{bat}\"' -WorkingDirectory '{d}';"
          + trg +
          "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
          "-DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew;"
          f"Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $tr -Settings $s -Force | Out-Null;"
          f"Write-Host '{name} 已注册，" + note.format(t=t) + "'")
    return name, ps


def do_action(body: dict):
    act = body.get("action", "")
    accts = scan_accounts()

    if act == "refresh_status":
        return act_refresh_status()

    if act == "run_all":
        plat = body.get("platform", "all")
        cmd = py("run_all.py", "--platform", plat)
        if body.get("dry"):
            cmd.append("--dry-run")
        luck = plat == "all" and bool(body.get("luck"))
        xxl = not luck and bool(body.get("xxl"))
        if luck:
            cmd.append("--luck")
        elif xxl:
            cmd.append("--xxl")
            if body.get("xxl_times"):
                cmd += ["--xxl-times", str(body["xxl_times"])]
        if body.get("seckill"):
            cmd.append("--seckill")
            if body.get("seckill_at"):
                cmd += ["--seckill-at", body["seckill_at"]]
        pname = {"all": "全部平台", "qwhd": "全国签到", "bass": "福建活动",
                 "none": "不跑签到"}.get(plat, plat)
        title = "签到领福利" if luck else "一键签到"
        label = "%s（%s%s%s%s）" % (title, pname, "，只读检查" if body.get("dry") else "",
                                  "，含消消乐" if xxl else "",
                                  "，含秒杀" if body.get("seckill") else "")
        return _start(label, cmd)

    if act == "qwhd_run":
        a = next((x for x in accts if x["key"] == body.get("key")), None)
        if not a:
            return {"ok": False, "err": "账号不存在"}
        cmd = py("cmcc_sign.py", "--config", a["file"])
        if body.get("dry"): cmd.append("--dry-run")
        if body.get("claim"): cmd.append("--claim")
        if body.get("tasks"): cmd.append("--tasks")
        if body.get("games"): cmd.append("--games")
        return _start(f"全国签到 · {a['key']}", cmd, parser=(parse_qwhd_output, a["key"]))

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
        if body.get("feed_all"): cmd.append("--feed-all")
        if body.get("auto_claim"): cmd.append("--auto-claim")
        # 阶段满时的另一条路：放弃本阶奖品直接升阶（与 --auto-claim 互斥）
        if body.get("continue_stage"): cmd.append("--continue-stage")
        if body.get("tasks"): cmd.append("--tasks")
        if body.get("fb"): cmd.append("--fb-tasks")
        return _start(f"福建活动 · {a['key']}", cmd, parser=(parse_bass_output, a["key"]))

    if act == "bass_probe":
        a = next((x for x in accts if x["key"] == body.get("key")), None)
        if not a:
            return {"ok": False, "err": "账号不存在"}
        return _start(f"福建活动只读探测 · {a['key']}", py("probe_fj.py", "--config", a["file"]))

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
        return _start("优惠专区抽奖", cmd)

    if act == "xunbao_run":
        cmd = py("cmcc_xunbao.py")
        if body.get("key"):
            cmd += ["--key", str(body["key"])]
        if body.get("dry"):
            cmd.append("--dry-run")
        if body.get("tasks"):
            cmd.append("--tasks")
        if body.get("max"):
            cmd += ["--max", str(body["max"])]
        return _start("环闽寻宝记", cmd)

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
        return _start(f"秒杀抢券 · {f.stem}", cmd,
                      parser=(parse_seckill_output, f.stem))

    if act == "games_run":
        f = safe_conf_path(body.get("file"))
        if f is None or not f.is_file():
            return {"ok": False, "err": "配置文件非法或不存在"}
        cmd = py("cmcc_sign.py", "--config", str(f))
        if body.get("dry"):
            cmd.append("--dry-run")
        else:
            cmd.append("--games")
            if body.get("tasks"):
                cmd.append("--tasks")
        return _start(f"拓展活动 · {f.stem}", cmd)

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
        return _start("消消乐", cmd)

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
        return _new_account("BASS", body.get("name", ""), body.get("phone", ""))

    if act == "account_new":
        return _new_account(body.get("plat", ""), body.get("name", ""), body.get("phone", ""))

    if act == "account_save":
        # 手机号 / 失败推送 / 备注：原先只能手改 JSON，界面上没有入口
        f = safe_conf_path(body.get("file"))
        if f is None or not f.is_file():
            return {"ok": False, "err": "配置文件不存在"}
        cfg = load_json(f) or {}
        phone = str(body.get("phone") or "").strip()
        if phone and not re.fullmatch(r"1[3-9]\d{9}", phone):
            return {"ok": False, "err": "手机号应为 11 位数字（1 开头）"}
        if phone:
            cfg["phone"] = phone
        for field in ("bark_url", "serverchan_sendkey"):
            if field not in body:
                continue
            v = str(body.get(field) or "").strip()
            if v and field == "bark_url" and not v.lower().startswith(("http://", "https://")):
                return {"ok": False, "err": "Bark 地址应以 http:// 或 https:// 开头"}
            if v and field == "serverchan_sendkey" and not re.fullmatch(r"[0-9A-Za-z]+", v):
                return {"ok": False, "err": "SendKey 只能含字母和数字"}
            cfg[field] = v
        if "note" in body:
            cfg["note"] = str(body.get("note") or "")[:200]
        atomic_write_json(f, cfg, indent=2)
        return {"ok": True, "msg": f"已保存 {f.name}"}

    # ---- 定时任务 ----
    if act == "policy_save":
        # 「设置一次、长期生效」：喂养/领奖策略存盘，定时任务每轮读它执行
        b = body.get("bass") or {}
        try:
            feed = int(b.get("feed") or 0)
            hour = int(b.get("feed_hour") if b.get("feed_hour") is not None else 12)
        except Exception:
            return {"ok": False, "err": "喂养轮数/小时必须是数字"}
        if not 0 <= feed <= 50:
            return {"ok": False, "err": "喂养轮数应在 0~50（每轮 250 饲料）"}
        if not 0 <= hour <= 23:
            return {"ok": False, "err": "喂养小时应在 0~23"}
        full = str(b.get("on_stage_full") or "stop")
        if full not in ("stop", "claim", "continue"):
            return {"ok": False, "err": "阶段满处理只能是 stop / claim / continue"}
        prize = str(b.get("prize") or "fee")
        if prize not in ("fee", "flow", "discount"):
            return {"ok": False, "err": "奖品档位只能是 fee / flow / discount"}
        patch = {"bass": {
            "feed": feed, "feed_hour": hour, "on_stage_full": full, "prize": prize,
            "feed_all": bool(b.get("feed_all")),
            "tasks": bool(b.get("tasks")), "fb_tasks": bool(b.get("fb_tasks")),
        }}
        # 转盘免费次数当天清零，默认开；界面勾掉才不自动抽
        patch["luck"] = bool(body.get("luck", True))
        # 寻宝骰子同样当天作废；任务上报只涉及「参与天天大转盘」这类真做过的
        patch["xunbao"] = bool(body.get("xunbao", True))
        patch["xunbao_tasks"] = bool(body.get("xunbao_tasks", True))
        xxl = body.get("xxl")
        if isinstance(xxl, dict):
            try:
                patch["xxl"] = {
                    "level": max(1, int(xxl.get("level") or 49)),
                    "star": min(3, max(0, int(xxl.get("star") or 3))),
                    "times": min(20, max(1, int(xxl.get("times") or 5))),
                    "gap": min(120, max(5, int(xxl.get("gap") or 12))),
                }
            except Exception:
                return {"ok": False, "err": "消消乐参数必须是数字"}
        sk = body.get("seckill")
        if isinstance(sk, dict):
            at = str(sk.get("at") or "").strip()
            if at and not re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", at):
                return {"ok": False, "err": "开抢时刻格式应为 HH:MM[:SS]"}
            patch["seckill"] = {"at": at}
        merged = save_policy(patch)
        return {"ok": True, "policy": merged,
                "msg": "策略已保存，定时任务下一轮就按这个执行"}

    if act == "sched_query":
        ps = ("foreach($n in 'MobileSignIn','MobileDailyReward','MobileJwtKeepAlive','MobileSeckill','MobileSessionRefresh','MobileTokenLifeProbe','MobileGames'){"
              "$t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue;"
              "if($t){$i=Get-ScheduledTaskInfo -TaskName $n;"
              "Write-Host ($n+'  状态='+$t.State+'  下次='+$i.NextRunTime+'  上次结果='+$i.LastTaskResult)}"
              "else{Write-Host ($n+'  未注册')}}")
        return _start("查询计划任务", ["powershell", "-NoProfile", "-Command", ps], need_py=False)
    if act == "sched_reg":
        # 隐藏启动：无窗口 pythonw + run_hidden.py，避免任务跑起来时闪黑窗口
        pyw = _pythonw()
        if not pyw:
            return {"ok": False, "err": _NO_PYW_ERR}
        launcher = str(BASE / "run_hidden.py")
        kind = body.get("kind", "sign")
        t = str(body.get("time") or "08:30").strip()
        # t 会插进单引号 PowerShell 字符串，不校验就能闭合引号注入任意命令
        if not re.fullmatch(r"\d{1,2}:\d{2}", t):
            return {"ok": False, "err": "时间格式应为 HH:MM"}
        if kind not in SCHED_DEF:
            return {"ok": False, "err": "未知任务类型"}
        name, ps = _sched_ps(kind, t, pyw, launcher)
        return _start(f"注册计划任务 {name}", ["powershell", "-NoProfile", "-Command", ps], need_py=False)

    if act == "sched_apply_all":
        # 一次调用注册全部任务：7 个 Register-ScheduledTask 塞进同一个 PowerShell 会话，
        # 比在界面上点七次快，也不用逐个等状态刷新。
        pyw = _pythonw()
        if not pyw:
            return {"ok": False, "err": _NO_PYW_ERR}
        launcher = str(BASE / "run_hidden.py")
        plan = body.get("plan") or [[k, t] for k, t in SCHED_PLAN]
        if not isinstance(plan, list) or not plan:
            return {"ok": False, "err": "方案为空"}
        parts, names = [], []
        for item in plan:
            try:
                kind, t = item[0], str(item[1])
            except Exception:
                return {"ok": False, "err": "方案格式应为 [[类型, 时刻], ...]"}
            if kind not in SCHED_DEF:
                return {"ok": False, "err": f"未知任务类型 {kind}"}
            if not re.fullmatch(r"\d{1,2}:\d{2}", t):
                return {"ok": False, "err": f"{kind} 的时刻格式应为 HH:MM"}
            name, ps = _sched_ps(kind, t, pyw, launcher)
            parts.append(ps)
            names.append(f"{SCHED_CN.get(kind, name)} {t}")
        with _SCHED_LOCK:
            _SCHED_CACHE["at"] = 0.0
        r = _start("一键配置全部计划任务",
                   ["powershell", "-NoProfile", "-Command", "\n".join(parts)], need_py=False)
        if r.get("ok"):
            r["msg"] = f"正在配置 {len(parts)} 个任务：{'、'.join(names)}"
        return r

    if act == "sched_unreg_all":
        names = ",".join(f"'{v[0]}'" for v in SCHED_DEF.values())
        ps = (f"foreach($n in @({names})){{"
              "Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue};"
              "Write-Host '全部计划任务已注销'")
        with _SCHED_LOCK:
            _SCHED_CACHE["at"] = 0.0
        return _start("注销全部计划任务", ["powershell", "-NoProfile", "-Command", ps], need_py=False)

    if act == "sched_refresh":
        # 清缓存后同步重读一次，直接把结果回给前端（注册/注销是异步跑的，
        # 前端等两秒再调这个，就能立刻看到新状态）
        with _SCHED_LOCK:
            _SCHED_CACHE["at"] = 0.0
        return {"ok": True, "sched": _sched_state(ttl=0.0)}

    if act == "sched_unreg":
        name = body.get("name", "")
        if name not in ("MobileSignIn", "MobileDailyReward", "MobileJwtKeepAlive",
                        "MobileSeckill", "MobileSessionRefresh", "MobileTokenLifeProbe",
                        "MobileGames"):
            return {"ok": False, "err": "非法任务名"}
        ps = f"Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false; Write-Host '{name} 已注销'"
        return _start(f"注销计划任务 {name}", ["powershell", "-NoProfile", "-Command", ps], need_py=False)

    if act == "stop":
        if not RUNNER.running:
            return {"ok": True, "msg": "当前没有正在运行的任务"}
        label = RUNNER.label
        RUNNER.kill()
        return {"ok": True, "msg": f"已请求停止「{label}」"}

    return {"ok": False, "err": f"未知动作 {act}"}


# ======================================================================
# HTTP 服务
# ======================================================================

def _month_sums(s: dict) -> dict:
    """按 history 统计「本月」领取量与任务数。

    界面原来只显示累计——数字只增不减，看着没意义（一天 1GB 和一天 0 都只是「更多」）。
    月度量从 history 现算，不动历史数据结构，老数据也立刻可用；
    history 保留 45 条，保证一整月都算得全。
    """
    prefix = datetime.now().strftime("%Y-%m")
    flow = 0.0
    tasks = 0
    feeds = 0
    for h in (s.get("history") or []):
        if str(h.get("date") or "").startswith(prefix):
            flow += h.get("flow_mb") or 0
            tasks += h.get("tasks") or 0
            feeds += h.get("feeds") or 0
    return {"flow_mb_month": round(flow, 1), "tasks_month": tasks, "feeds_month": feeds}


def build_state():
    st = load_stats()
    accts = scan_accounts()
    env_ok, env_msg = preflight()
    for k, v in st.items():
        if isinstance(v, dict):
            v.update(_month_sums(v))
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
        "xunbao": _load_json_safe(DATA / "xunbao_state.json"),
        "awards": _awards_view(),
        "seckill_sessions": _list_seckill_sessions(),
        "token_life": _list_token_life(),
        "sched": _sched_state(),
        "sched_read_ok": _SCHED_CACHE["ok"],
        "sched_logs": SCHED_LOG,
        "sched_cn": SCHED_CN,
        "env": {"ok": env_ok, "msg": env_msg},
        "policy": load_policy(),
    }


def _awards_view(limit=40) -> dict:
    """到账台账：只挑「到手可用」的，按天/按号汇总给前端。

    用户的原话是「别让我看喂了几次，就告诉我今天到手多少流量」，
    所以这里的口径是**实际到手的流量 + 无门槛可直接兑换的话费券**。
    有门槛的券（充值/满减/手气红包）只计数、不进列表、不计入张数。
    """
    try:
        import common as C
    except Exception:
        return {"recent": [], "today": {}, "month": {}, "by_account": {}}

    today = TODAY()
    month = today[:7]
    recs = C.load_awards()          # 新的在前
    recent, seen = [], {}
    for r in recs:
        if r.get("ignored"):
            continue                 # 话费型手气红包：用户要求直接不显示
        # 同一秒同一奖品的重复登记（多账号/重复读接口）只留一条
        sig = (r.get("at"), r.get("account"), r.get("name"))
        if sig in seen:
            continue
        seen[sig] = 1
        if r.get("need_recharge"):
            continue                 # 要充值 / 要花钱买的券不上榜
        if r.get("kind") not in ("flow", "fee"):
            # 打车券、AI豆兑换券这类既不折流量也不抵话费，列出来两栏全是「—」，
            # 用户要的是「今天到手多少流量、几张话费券」，所以不进明细。
            continue
        recent.append(r)
        if len(recent) >= limit:
            break

    def agg(prefix):
        flow, fee, recharge, yuan, th_yuan = 0.0, 0, 0, 0.0, 0.0
        for r in recs:
            if not str(r.get("at", "")).startswith(prefix):
                continue
            if r.get("ignored"):
                continue             # 话费型手气红包：不计入任何数字
            amt = float(r.get("yuan") or 0)
            if r.get("need_recharge"):
                recharge += 1
                th_yuan += amt
                continue
            if r.get("kind") == "flow":
                flow += float(r.get("size_mb") or 0)
            elif r.get("kind") == "fee":
                fee += 1
                yuan += amt
        return {"flow_mb": round(flow, 1), "fee_count": fee, "fee_yuan": round(yuan, 2),
                "recharge_count": recharge, "recharge_yuan": round(th_yuan, 2)}

    by = {}
    by_day = {}
    fee_day = {}          # 日期 → 当天拿到的无门槛话费券张数（给「本月已得话费」画迷你趋势）
    for r in recs:
        if (not str(r.get("at", "")).startswith(month)
                or r.get("need_recharge") or r.get("ignored")):
            continue
        tail = str(r.get("account") or "")[-4:] or "?"
        b = by.setdefault(tail, {"flow_mb": 0.0, "fee_count": 0, "fee_yuan": 0.0, "last": ""})
        if r.get("kind") == "flow":
            b["flow_mb"] = round(b["flow_mb"] + float(r.get("size_mb") or 0), 1)
            d = str(r.get("at", ""))[:10]
            dd = by_day.setdefault(tail, {})
            dd[d] = round(dd.get(d, 0) + float(r.get("size_mb") or 0), 1)
        elif r.get("kind") == "fee":
            b["fee_count"] += 1
            b["fee_yuan"] = round(b["fee_yuan"] + float(r.get("yuan") or 0), 2)
            d = str(r.get("at", ""))[:10]
            fee_day[d] = fee_day.get(d, 0) + 1
        if not b["last"]:
            b["last"] = r.get("name") or ""

    usable_fee = [r for r in recs
                  if r.get("kind") == "fee" and not r.get("need_recharge")]
    return {"recent": recent, "today": agg(today), "month": agg(month),
            "by_account": by, "by_day": by_day, "fee_day": fee_day,
            # 历史累计：话费券张数 + 合计面额（只算无门槛可直接兑换的）
            "fee_total": len(usable_fee),
            "fee_yuan_total": round(sum(float(r.get("yuan") or 0) for r in usable_fee), 2),
            # 本月有门槛的券明细（只有名字）：列表里不出现，但要让用户知道「哪张券不算」
            # 话费型手气红包（ignored）连这里也不出现——用户要求直接不显示
            "month_threshold": [{"name": r.get("name") or "", "at": r.get("at") or "",
                                 "yuan": float(r.get("yuan") or 0)}
                                for r in recs
                                if str(r.get("at", "")).startswith(month)
                                and r.get("need_recharge") and not r.get("ignored")]}


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


SCHED_TASKS = ("MobileSignIn", "MobileDailyReward", "MobileJwtKeepAlive",
               "MobileSeckill", "MobileSessionRefresh", "MobileGames",
               "MobileTokenLifeProbe")
_SCHED_KIND_BY_NAME = {v[0]: k for k, v in SCHED_DEF.items()}
_SCHED_CACHE = {"at": 0.0, "data": [], "ok": None, "th": None}
_SCHED_LOCK = threading.Lock()


def _sched_bg_refresh():
    """后台读一次真实状态。冷启动时用它，首屏就不必等 PowerShell。"""
    with _SCHED_LOCK:
        th = _SCHED_CACHE.get("th")
        if th is not None and th.is_alive():
            return
        t = threading.Thread(target=_sched_read_sync, daemon=True)
        _SCHED_CACHE["th"] = t
        t.start()


def _sched_wait(timeout: float = 20.0):
    """等后台那次读取落盘（测试与显式刷新用）。"""
    with _SCHED_LOCK:
        th = _SCHED_CACHE.get("th")
    if th is not None:
        th.join(timeout)


def _sched_state(ttl: float = 90.0) -> list:
    """取计划任务状态；缓存冷且允许异步时，先返回旧值并让后台去读。

    读一次要起 PowerShell（最长 12 秒）。原先它挡在 /api/state 里，
    首屏要等它读完才渲染 —— 打开程序先白屏十几秒。
    """
    now = time.time()
    with _SCHED_LOCK:
        if now - _SCHED_CACHE["at"] < ttl:
            return _SCHED_CACHE["data"]
        cold = _SCHED_CACHE["at"] == 0.0
    if ttl > 0 and cold:
        _sched_bg_refresh()
        with _SCHED_LOCK:
            return _SCHED_CACHE["data"]
    return _sched_read_sync()


def _sched_read_sync() -> list:
    """读取 Windows 计划任务的**真实**状态：是否注册 / 实际触发时间 / 下次 / 上次结果。

    页面上原来只有「注册/注销」按钮，看不出任务到底有没有在跑；输入框里还是写死的
    默认时刻（与真实注册时间可能不一致，点一下就静默改掉）。这里把系统里的真相读出来。

    每次读取都要起一个 PowerShell，所以由 _sched_state 负责缓存与异步；
    读取失败（被策略拦 / 超时）时返回上一次的结果，不抛给页面。
    """
    now = time.time()
    names = ",".join(f"'{n}'" for n in SCHED_TASKS)
    ps = ("$o=@();foreach($n in @(" + names + ")){"
          "$t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue;"
          "if($t){$i=Get-ScheduledTaskInfo -TaskName $n;"
          "$nx='';if($i.NextRunTime){$nx=$i.NextRunTime.ToString('MM-dd HH:mm')};"
          "$ls='';if($i.LastRunTime){$ls=$i.LastRunTime.ToString('MM-dd HH:mm')};"
          "$o+=[PSCustomObject]@{name=$n;state=[string]$t.State;"
          "at=[string]$t.Triggers[0].StartBoundary;"
          "next=$nx;last=$ls;"
          "code=[int]$i.LastTaskResult}}};"
          "ConvertTo-Json @($o) -Compress")
    data = None
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=12, creationflags=NO_WINDOW)
        raw = (p.stdout or b"").decode("utf-8", errors="replace").strip()
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list):
                data = None
    except Exception:
        data = None
    if data is None:
        with _SCHED_LOCK:
            # 读失败与「一个都没注册」要分得开，否则界面会把新用户误报成被策略拦
            _SCHED_CACHE["ok"] = False
            return _SCHED_CACHE["data"]

    out = []
    for d in data:
        at = str(d.get("at") or "")
        code = d.get("code")
        # 267011 = 从未运行；此时系统给的 LastRunTime 是 1999-11-30 这类占位值，
        # 直接显示会让人以为「刚跑过一次」，所以清掉。
        last = "" if code == 267011 else str(d.get("last") or "")
        kind = _SCHED_KIND_BY_NAME.get(d.get("name") or "", "")
        out.append({
            "name": d.get("name") or "",
            "kind": kind,
            "log": SCHED_LOG.get(kind, ""),
            "state": d.get("state") or "",
            # StartBoundary 形如 2026-11-20T06:03:00+08:00，取 HH:MM 回填输入框
            "time": at[11:16] if len(at) >= 16 and at[10] == "T" else "",
            "next": str(d.get("next") or ""),
            "last": last,
            "code": code,
        })
    with _SCHED_LOCK:
        _SCHED_CACHE["at"] = now
        _SCHED_CACHE["data"] = out
        _SCHED_CACHE["ok"] = True
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
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionError, OSError):
            # 页面每秒轮询，关页/切页时浏览器会掐断连接。
            # 不接住就会往 stderr 打一整段 traceback，看着像服务崩了。
            self.close_connection = True

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
        elif u.path == "/favicon.ico":
            ico = BASE / "app.ico"
            if ico.is_file():
                self._send(200, "image/x-icon", ico.read_bytes())
            else:
                self._send(204, "image/x-icon", b"")
        elif u.path == "/api/state":
            self._json(build_state())
        elif u.path == "/api/logs":
            try:
                since = int((q.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            lines = [l for l in RUNNER.logs if l["seq"] > since]
            self._json({"lines": lines, "running": RUNNER.running, "label": RUNNER.label})
        elif u.path == "/api/tasklog":
            data = read_task_log((q.get("name") or [""])[0], (q.get("tail") or ["200"])[0])
            if data is None:
                self._json({"err": "未知日志名"}, 404)
            else:
                self._json(data)
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

    顺序上**先看 exe 旁边的 static/**：改完界面不用重新打包就能生效，
    只有把 exe 单独拷到别处（旁边没有 static/）时才回退到打包进去的那份。
    早先是 _MEIPASS 优先，结果「改了 index.html、exe 里还是老界面」。
    """
    candidates = [BASE / "static" / "index.html"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", str(BASE))) / "static" / "index.html")
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise SystemExit("缺少 static/index.html：请把它与程序放在同一目录"
                     "（打包时由 spec 的 datas 带上）")


INDEX_HTML = _load_frontend()


# ======================================================================
# 入口
# ======================================================================

class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 的 SO_REUSEADDR 允许绑到**已在监听**的端口：两个实例会抢同一个端口，
    # 请求随机落到旧实例上（表现为「改了代码但界面没变」）。这里关掉它，
    # 端口被占就老实顺延；POSIX 保留 reuse，避免重启时被 TIME_WAIT 卡住。
    allow_reuse_address = os.name != "nt"


def make_server():
    """绑定本地端口（被占就顺延 10 个），返回 (server, 错误说明)。"""
    for port in range(PORT, PORT + 11):
        try:
            return ConsoleServer(("127.0.0.1", port), Handler), ""
        except OSError:
            continue
    return None, f"端口 {PORT}~{PORT + 10} 均被占用，请关闭占用程序后重试。"


def alert(msg: str):
    """无窗口环境下的错误提示：Windows 弹原生对话框，其它平台打印。"""
    print(msg)
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "签到控制台", 0x10)
        except Exception:
            pass


def main() -> int:
    """浏览器模式入口：python dashboard.py / run_gui.bat。

    这个入口原先根本不存在——模块只有定义没有启动代码，
    run_gui.bat 双击后进程秒退，既不监听也不开浏览器，还不报错。
    """
    ok, msg = preflight(force=True)
    if not ok:
        alert(msg)          # 界面照常起，让用户看得到说明再去装 Python
    srv, err = make_server()
    if srv is None:
        alert(err)
        return 1
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    print(f"签到控制台已启动：{url}\n关闭本窗口即退出。")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
