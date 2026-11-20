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

from common import atomic_write_json

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


def save_stats(st: dict):
    atomic_write_json(STATS_FILE, st)


RUNS_LOCK = threading.Lock()


def load_runs() -> list:
    try:
        return json.loads(RUNS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def append_run(label: str, sec: float, ok: bool):
    with RUNS_LOCK:
        runs = load_runs()
        runs.append({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "label": label, "sec": round(sec, 1), "ok": ok})
        atomic_write_json(RUNS_FILE, runs[-500:], indent=0)


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


def acct_stats(key: str) -> dict:
    with STATS_LOCK:
        st = load_stats()
        a = st.setdefault(key, {
            "flow_mb_total": 0.0,     # 累计领取流量 MB（qwhd 奖品解析）
            "tasks_done_total": 0,    # 累计完成任务数（doTask/fb任务 OK 计数）
            "sign_days": None,        # qwhd 累计签到天数
            "today_signed_qwhd": "",  # qwhd 今日已签日期
            "today_signed_bass": "",  # BASS 今日已签日期
            "fodder": None,           # BASS 饲料
            "stage": "",              # BASS 阶段名
            "fb_exchange": "",        # 流量银行可兑（如 352MB / 1.5GB）
            "token_qwhd": "",         # ok / expired / empty
            "token_bass": "",
            "last_reward": "",
            "last_run": "",
            "history": [],            # [{date, flow_mb, tasks}]
        })
        return a


def update_stats(key: str, **kw):
    with STATS_LOCK:
        st = load_stats()
        a = st.setdefault(key, {})
        a.update(kw)
        save_stats(st)


def bump_daily(key: str, flow_mb: float = 0, tasks: int = 0, feeds: int = 0):
    """把当日领取量记入 history（同日累加）。"""
    with STATS_LOCK:
        st = load_stats()
        a = st.setdefault(key, {"history": []})
        hist = a.setdefault("history", [])
        today = TODAY()
        row = next((h for h in hist if h.get("date") == today), None)
        if row is None:
            row = {"date": today, "flow_mb": 0.0, "tasks": 0, "feeds": 0}
            hist.append(row)
        row["flow_mb"] = round(row["flow_mb"] + flow_mb, 1)
        row["tasks"] += tasks
        row["feeds"] = row.get("feeds", 0) + feeds
        a["history"] = hist[-30:]
        if flow_mb:
            a["flow_mb_total"] = round(a.get("flow_mb_total", 0) + flow_mb, 1)
        if tasks:
            a["tasks_done_total"] = a.get("tasks_done_total", 0) + tasks
        if feeds:
            a["feeds_total"] = a.get("feeds_total", 0) + feeds
        save_stats(st)


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

def parse_qwhd_output(key: str, text: str):
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
    # 完成任务计数（doTask / fb任务 OK 行）
    tasks = len(re.findall(r"OK\s+(?:doTask|fb任务)", text))
    # 喂养次数（「OK 喂养 1/11」这类行）
    feeds = len(re.findall(r"OK\s+喂养", text))
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
        if self.running:
            return False
        self.running = True
        self.label = label
        self._parser = parser
        self._buf = []
        self.push(f"▶ {label}")
        self.push("$ " + (" ".join(map(str, cmd)) if isinstance(cmd, list) else str(cmd)))
        threading.Thread(target=self._run, args=(cmd, shell), daemon=True).start()
        return True

    def _run(self, cmd, shell):
        rc = -1
        t0 = time.time()
        label = self.label
        try:
            self.proc = subprocess.Popen(cmd, cwd=str(BASE), shell=shell,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         creationflags=NO_WINDOW)
            for raw in iter(self.proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                self._buf.append(line)
                self.push(line)
            self.proc.wait()
            rc = self.proc.returncode
        except Exception as e:
            self.push(f"[异常] {e}")
        finally:
            text = "".join(self._buf)
            if self._parser and text:
                try:
                    fn, key = self._parser
                    fn(key, text)
                except Exception as e:
                    self.push(f"[解析统计失败] {e}")
            try:
                append_run(label, time.time() - t0, rc == 0)
            except Exception:
                pass
            self.proc = None
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

def find_account(pred):
    for a in scan_accounts():
        if pred(a):
            return a
    return None


def act_refresh_status():
    """逐个账号 dry-run，拉取实时状态刷新仪表盘。"""
    if RUNNER.running:
        return False
    accts = scan_accounts()
    def run_all_dry():
        for a in accts:
            if RUNNER._stop_seq:
                break
        # 顺序执行在单独线程里
    def worker():
        for a in accts:
            if a["plat"] == "qwhdhub":
                if a["token_state"] == "empty":
                    RUNNER.push(f"⚠ {a['key']}（{a['plat']}）token 未配置，跳过")
                    update_stats(a["key"], token_qwhd="empty")
                    continue
                cmd = py("cmcc_sign.py", "--config", a["file"], "--dry-run")
                parser = (parse_qwhd_output, a["key"])
            else:
                if a["token_state"] == "empty":
                    RUNNER.push(f"⚠ {a['key']}（{a['plat']}）token 未配置，跳过")
                    update_stats(a["key"], token_bass="empty")
                    continue
                cmd = py("cmcc_fj.py", "--config", a["file"], "--dry-run")
                parser = (parse_bass_output, a["key"])
            RUNNER.push(f"\n—— 刷新 {a['key']}（{a['plat']}）——")
            RUNNER._parser = parser
            RUNNER._buf = []
            try:
                p = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
                RUNNER.proc = p
                for raw in iter(p.stdout.readline, b""):
                    line = raw.decode("utf-8", errors="replace")
                    RUNNER._buf.append(line)
                    RUNNER.push(line)
                p.wait()
                text = "".join(RUNNER._buf)
                fn, key = parser
                fn(key, text)
            except Exception as e:
                RUNNER.push(f"[异常] {e}")
        RUNNER.push("\n■ 状态刷新完成")
    # 借用 runner 的 running 标志与日志，但自管线程
    RUNNER.running = True
    RUNNER.label = "刷新状态"
    RUNNER.push("▶ 刷新全部账号状态（dry-run 只读）")
    def wrap():
        t0 = time.time()
        try:
            worker()
        finally:
            try:
                append_run("刷新全部账号状态", time.time() - t0, True)
            except Exception:
                pass
            RUNNER.running = False
            RUNNER.proc = None
            RUNNER.label = ""
    threading.Thread(target=wrap, daemon=True).start()
    return True


RUNNER._stop_seq = False  # 兼容占位


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
            cmd += ["--har", str(body["har"])]
        if body.get("dry"):
            cmd.append("--dry-run")
        if body.get("no_tasks"):
            cmd.append("--no-tasks")
        if body.get("max"):
            cmd += ["--max", str(body["max"])]
        return {"ok": RUNNER.start("优惠专区抽奖", cmd)}

    if act == "seckill_run":
        cfg = body.get("file") or "configs/3239.json"
        cmd = py("cmcc_seckill.py", "--config", cfg)
        if body.get("dry"):
            cmd.append("--dry-run")
        elif body.get("once"):
            cmd.append("--once")
        elif body.get("at"):
            cmd += ["--at", body["at"]]
        return {"ok": RUNNER.start(f"签到秒杀 · {cfg}", cmd)}

    if act == "xxl_run":
        cmd = py("cmcc_xiaoxiaole.py", "--config", body.get("file", "configs_fj/fj1.json"))
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
                rep, used = HI.scan_dir(body.get("dir") or None)
        except Exception as e:
            return {"ok": False, "err": f"导入失败：{e}"}
        for line in rep:
            RUNNER.push("  " + line)
        RUNNER.push("■ HAR 导入结束")
        if not used:
            return {"ok": False, "err": "没有可用 token", "report": rep}
        return {"ok": True, "report": rep, "msg": f"HAR 导入完成（{used} 份）"}

    if act == "bass_token_save":
        rel = body.get("file")
        f = BASE / rel
        if not f.exists() or "configs_fj" not in rel:
            return {"ok": False, "err": "配置文件不存在"}
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
        t = body.get("time", "08:30")
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
            # qwhdhub 会话保活：每 90 分钟续期一次 jwt（实测寿命 2.5~3.5 小时）
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
            # 秒杀抢券：11:30 启动，脚本内部等到 12:00 再出手（app_token 那时仍有效）
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
                    d = datetime.datetime.fromisoformat(exp) - datetime.datetime.now()
                    left_min = round(d.total_seconds() / 60)
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

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
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode())
        elif u.path == "/api/state":
            self._json(build_state())
        elif u.path == "/api/logs":
            since = int((q.get("since") or ["0"])[0])
            lines = [l for l in RUNNER.logs if l["seq"] > since]
            self._json({"lines": lines, "running": RUNNER.running, "label": RUNNER.label})
        elif u.path == "/api/bass_cfg":
            rel = (q.get("file") or [""])[0]
            f = BASE / rel if rel else BASE / "__none__"
            cfg = load_json(f) if f.is_file() else None
            if cfg:
                tok = cfg.get("bearer_token", "")
                cfg = {**cfg, "bearer_token": (tok[:4] + "…" + tok[-4:]) if len(tok) > 12 else ("(空)" if not tok else tok)}
            self._json(cfg or {"err": "not found"})
        else:
            self._json({"err": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
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

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>签到控制台</title>
<style>
:root{
  --bg:#0f1115; --side:#0b0d10; --card:#16181d; --card2:#1c1f26; --line:#24272f;
  --txt:#e4e6eb; --sub:#8a8f9b; --acc:#5b8dd9; --ok:#4caf7d; --warn:#d3a244; --bad:#d46a6a;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:"Microsoft YaHei","PingFang SC","Segoe UI",sans-serif;min-height:100vh}
a{color:var(--acc)}
/* ===== 外壳：侧边栏 + 主区 ===== */
.shell{display:flex;min-height:100vh}
.sidebar{width:212px;flex-shrink:0;background:var(--side);border-right:1px solid var(--line);display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.brand{padding:18px 20px 15px;border-bottom:1px solid var(--line)}
.brand b{font-size:14.5px;font-weight:600;display:block}
.brand small{display:block;font-size:11px;color:var(--sub);margin-top:4px}
nav{padding:12px 10px;flex:1}
nav button{display:flex;align-items:center;gap:9px;width:100%;text-align:left;background:none;border:none;color:var(--sub);font-size:13.5px;padding:9px 12px;border-radius:6px;cursor:pointer;margin-bottom:2px;border-left:2px solid transparent;transition:.12s}
nav button svg{width:15px;height:15px;flex-shrink:0;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;opacity:.85}
nav button:hover{color:var(--txt);background:var(--card)}
nav button.on{color:var(--txt);background:var(--card);border-left-color:var(--acc);font-weight:500}
.side-foot{padding:14px 16px;border-top:1px solid var(--line);font-size:11.5px;color:var(--sub);line-height:1.7}
.main{flex:1;min-width:0;display:flex;flex-direction:column}
.topbar{display:flex;align-items:center;gap:12px;padding:13px 28px;border-bottom:1px solid var(--line);background:var(--bg);position:sticky;top:0;z-index:10}
.topbar .title{font-size:15px;font-weight:600}
.topbar .actions{margin-left:auto;display:flex;gap:8px;align-items:center}
.content{padding:24px 28px 40px;max-width:1240px;width:100%}
.page{display:none}.page.on{display:block}
/* ===== 页头 ===== */
.phead{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:20px;flex-wrap:wrap}
.phead h2{font-size:18px;font-weight:600}
.phead .desc{font-size:12.5px;color:var(--sub);margin-top:5px}
.phead .ops{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
/* ===== 按钮 ===== */
.btn{background:var(--card2);border:1px solid var(--line);color:var(--txt);padding:7px 15px;border-radius:6px;font-size:13px;cursor:pointer;transition:.15s}
.btn:hover{border-color:var(--acc);color:var(--acc)}
.btn.pri{background:var(--acc);border:1px solid var(--acc);color:#fff;font-weight:500}
.btn.pri:hover{opacity:.85;color:#fff}
.btn.sm{padding:5px 11px;font-size:12px;border-radius:5px}
.btn.danger:hover{border-color:var(--bad);color:var(--bad)}
.pill{font-size:12px;padding:4px 12px;border-radius:99px;border:1px solid var(--line);color:var(--sub);display:inline-block}
.pill.run{color:#fff;background:var(--acc);border-color:var(--acc)}
/* ===== KPI ===== */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.kpi .k{font-size:12px;color:var(--sub);margin-bottom:8px}
.kpi .v{font-size:25px;font-weight:600;letter-spacing:-.3px}
.kpi .v small{font-size:12.5px;color:var(--sub);font-weight:400;margin-left:2px}
.kpi .c{font-size:11px;color:var(--sub);margin-top:7px}
.kpi .spark{display:block;width:100%;height:26px;margin-top:9px}
.kpi .spark polyline{fill:none;stroke:var(--acc);stroke-width:1.6;vector-effect:non-scaling-stroke;opacity:.7}
.kpi .v,.strip .v,.stat .v{font-variant-numeric:tabular-nums}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-thumb{background:#2a2e37;border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:#363b46}
::-webkit-scrollbar-track{background:transparent}
/* ===== 账号卡 ===== */
.acct{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:16px;overflow:hidden}
.acct-head{display:flex;align-items:center;gap:10px;padding:13px 20px;border-bottom:1px solid var(--line)}
.acct-head .phone{font-size:15px;font-weight:600}
.acct-head .raw{font-size:12px;color:var(--sub)}
.acct-body{display:grid;grid-template-columns:1fr 1fr}
.acct-body .plat{padding:16px 20px;border-right:1px solid var(--line)}
.acct-body .plat:last-child{border-right:none}
.acct-body .plat:only-child{grid-column:1/-1}
.plat .pt{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;margin-bottom:10px;flex-wrap:wrap}
.badge{font-size:11px;padding:2px 9px;border-radius:99px;font-weight:400}
.badge.ok{background:rgba(76,175,125,.14);color:var(--ok)}
.badge.bad{background:rgba(212,106,106,.14);color:var(--bad)}
.badge.na{background:#23262e;color:var(--sub)}
.badge.signed{background:rgba(91,141,217,.15);color:var(--acc)}
.stats{display:flex;flex-direction:column;margin-top:2px}
.stat{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:7px 0;border-bottom:1px solid var(--line)}
.stat:last-child{border-bottom:none;padding-bottom:0}
.stat .k{font-size:12.5px;color:var(--sub);white-space:nowrap}
.stat .v{font-size:14px;font-weight:600;text-align:right}
.stat .v small{font-size:11px;color:var(--sub);font-weight:400}
.stat .v.wrap{font-size:12px;line-height:1.5;max-width:62%;text-align:right;font-weight:500}
.stat .v.y{color:var(--warn)}
.acts{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.hist{margin-top:12px;border-top:1px dashed var(--line);padding-top:10px}
.hist .t{font-size:11.5px;color:var(--sub);margin-bottom:7px}
.bars{display:flex;gap:5px;align-items:flex-end;height:44px;border-bottom:1px solid var(--line);justify-content:space-around}
.bar{flex:1;max-width:34px;background:var(--acc);border-radius:2px 2px 0 0;min-height:0;position:relative;opacity:.55}
.bar.off{background:var(--line);opacity:.35;min-height:2px}
.bar:hover{opacity:.85}
.bar .tip{display:none;position:absolute;bottom:110%;left:50%;transform:translateX(-50%);background:#000;color:#fff;font-size:11px;padding:3px 8px;border-radius:5px;white-space:nowrap;z-index:5}
.bar:hover .tip{display:block}
.bardates{display:flex;gap:5px;margin-top:4px}
.bardates span{flex:1;text-align:center;font-size:9.5px;color:var(--sub)}
.hist .empty{font-size:12px;color:var(--sub);padding:20px 0;text-align:center;border:1px dashed var(--line);border-radius:6px}
/* ===== 面板 / 统计条 / 表格 ===== */
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;margin-bottom:16px}
.panel h3{font-size:14px;font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.panel h3 .hint{font-size:12px;color:var(--sub);font-weight:400}
.strip{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:16px}
.strip .cell{padding:13px 18px;border-right:1px solid var(--line);background:var(--card2)}
.strip .cell:last-child{border-right:none}
.strip .k{font-size:11.5px;color:var(--sub);margin-bottom:5px}
.strip .v{font-size:19px;font-weight:600}
.strip .v small{font-size:12px;color:var(--sub);font-weight:400}
table.runtab{width:100%;max-width:880px;border-collapse:collapse;font-size:12.5px;table-layout:fixed}
.runtab th:nth-child(1){width:150px}
.runtab th:nth-child(3){width:90px}
.runtab th:nth-child(4){width:70px}
.runtab td:nth-child(2){overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.runtab th{color:var(--sub);font-weight:400;text-align:left;padding:7px 12px;border-bottom:1px solid var(--line);font-size:12px}
.runtab th.num{text-align:right}
.runtab td{padding:8px 12px;border-bottom:1px solid var(--line)}
.runtab tr:last-child td{border-bottom:none}
.runtab td.num{text-align:right;font-variant-numeric:tabular-nums}
/* 流量银行任务周期表 */
table.fbtab{width:100%;max-width:960px;border-collapse:collapse;font-size:12.5px;table-layout:fixed}
.fbtab th{color:var(--sub);font-weight:400;text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);font-size:12px}
.fbtab td{padding:8px 10px;border-bottom:1px solid var(--line)}
.fbtab tr:last-child td{border-bottom:none}
.fbtab code{font-size:11.5px;color:var(--sub)}
.fbhead{font-size:12.5px;color:var(--sub);margin:0 0 6px}
.runtab tr:hover td{background:var(--card2)}
.runtab .ok{color:var(--ok)}.runtab .bad{color:var(--bad)}
/* ===== 表单 ===== */
.frow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.frow label{font-size:13px;color:var(--sub);min-width:88px}
.inp{background:var(--card2);border:1px solid var(--line);color:var(--txt);padding:8px 12px;border-radius:6px;font-size:13px;min-width:180px}
.inp:focus{outline:none;border-color:var(--acc)}
select.inp{cursor:pointer}
.chk{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--txt);cursor:pointer;background:var(--card2);border:1px solid var(--line);padding:7px 12px;border-radius:6px}
.chk input{accent-color:var(--acc)}
.warnbox{background:rgba(211,162,68,.08);border:1px solid rgba(211,162,68,.3);color:var(--warn);font-size:12.5px;padding:10px 14px;border-radius:8px;margin:10px 0;line-height:1.7}
.infobox{background:rgba(91,141,217,.07);border:1px solid rgba(91,141,217,.25);color:#a8c3ff;font-size:12.5px;padding:10px 14px;border-radius:8px;margin:10px 0;line-height:1.8}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
/* ===== 日志 ===== */
.console{background:#0a0c10;border:1px solid var(--line);border-radius:8px;padding:14px 16px;height:420px;overflow-y:auto;font-family:Consolas,"Courier New",monospace;font-size:12.5px;line-height:1.65}
.console div{white-space:pre-wrap;word-break:break-all;color:#c9d4ee}
.console .t{color:#5a678a;margin-right:8px}
.console .ok{color:var(--ok)}.console .err{color:var(--bad)}.console .hl{color:var(--warn)}.console .dim{color:#5a678a}
.toast{position:fixed;top:64px;right:28px;background:var(--card2);border:1px solid var(--line);color:var(--txt);padding:11px 18px;border-radius:8px;font-size:13px;z-index:99;box-shadow:0 4px 16px rgba(0,0,0,.4);display:none}
@media(max-width:1100px){.kpis{grid-template-columns:repeat(2,1fr)}.strip{grid-template-columns:repeat(2,1fr)}.strip .cell:nth-child(2){border-right:none}.acct-body{grid-template-columns:1fr}.acct-body .plat{border-right:none;border-bottom:1px solid var(--line)}.acct-body .plat:last-child{border-bottom:none}}
@media(max-width:860px){.sidebar{width:168px}}
@media(max-width:640px){.shell{flex-direction:column}.sidebar{width:100%;height:auto;position:static;flex-direction:row;align-items:center}.sidebar nav{display:flex;padding:8px}.sidebar .brand,.side-foot{display:none}.grid2{grid-template-columns:1fr}.kpis{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand"><b>签到控制台</b><small>中国移动自动签到</small></div>
    <nav>
      <button class="on" data-p="dash" onclick="nav(this)"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>总览</button>
      <button data-p="token" onclick="nav(this)"><svg viewBox="0 0 24 24"><circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3 19 4m-3.5 0H19v3.5"/></svg>Token 管理</button>
      <button data-p="bass" onclick="nav(this)"><svg viewBox="0 0 24 24"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z"/></svg>BASS 任务</button>
      <button data-p="xxl" onclick="nav(this)"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>消消乐</button>
      <button data-p="seckill" onclick="nav(this)"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>秒杀抢券</button>
      <button data-p="sched" onclick="nav(this)"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>定时任务</button>
      <button data-p="logs" onclick="nav(this)"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3m5 0h5"/></svg>日志</button>
    </nav>
    <div class="side-foot">
      <span class="pill" id="taskPill">空闲</span><br>
      <span id="hsub">加载中…</span>
    </div>
  </aside>
  <div class="main">
    <div class="topbar">
      <div class="title" id="pageTitle">签到控制台 / 总览</div>
      <div class="actions">
        <button class="btn sm" onclick="refreshStatus()">刷新状态</button>
        <button class="btn sm danger" onclick="act({action:'stop'})">停止</button>
      </div>
    </div>
    <div class="content">

      <!-- 总览 -->
      <div class="page on" id="p-dash">
        <div class="phead">
          <div><h2>总览</h2><div class="desc">每个号码的签到状态、累计收益与近 7 天记录</div></div>
          <div class="ops">
            <button class="btn pri" onclick="runAll('all')">一键签到</button>
            <button class="btn" onclick="runAll('qwhd')">只跑 qwhdhub</button>
            <button class="btn" onclick="runAll('bass')">只跑 BASS</button>
            <label class="chk"><input type="checkbox" id="dashDry">dry-run</label>
            <label class="chk"><input type="checkbox" id="dashXxl" checked>含消消乐</label>
          </div>
        </div>
        <div class="kpis" id="sumbar"></div>
        <div id="cards"></div>
        <div class="panel">
          <h3>运行记录 <span class="hint" id="effHint"></span></h3>
          <div class="strip" id="effbar"></div>
          <table class="runtab" id="runtab"></table>
        </div>
      </div>

      <!-- Token 管理 -->
      <div class="page" id="p-token">
        <div class="phead"><div><h2>Token 管理</h2><div class="desc">从 HAR 一次性导入 qwhdhub 与 BASS 的登录凭证</div></div></div>
        <div class="panel">
          <h3>HAR 导入 <span class="hint">一份 HAR 同时更新 qwhdhub 与 BASS</span></h3>
          <div class="infobox">手机抓包（Stream / ProxyPin）导出含 <b>appTokenLogin</b> 或 <b>wap.fj.10086.cn</b> 请求的 HAR。
            qwhdhub token 按 HAR 里的手机号自动归位；BASS token 跟随同一号码写进 configs_fj。</div>
          <div class="frow"><label>HAR 文件</label><input type="file" id="harFile" accept=".har,.json" style="min-width:220px">
            <select class="inp" id="harKey" style="min-width:160px"></select>
            <button class="btn pri sm" onclick="importHar()">导入</button></div>
          <div class="frow"><label>目录扫描</label>
            <button class="btn sm" onclick="act({action:'har_import'},afterHar)">扫描项目目录 *.har 并导入</button></div>
          <div class="infobox" id="harOut" style="display:none"></div>
          <div class="infobox">导入后回「总览」点右上角「刷新状态」即可复核 token 是否生效。</div>
        </div>
        <div class="panel">
          <h3>优惠专区抽奖 <span class="hint">自动从最新 HAR 取 token，刷任务 + 抽奖</span></h3>
          <div class="infobox">先打开一次「优惠专区抽奖」页面，让抓包工具记录，然后在下面点执行——
            脚本会自动在<b>项目目录 / 下载 / 微信接收目录</b>里找最新的 HAR 并提取 token。
            抓到的 bearer 能撑数小时，不必每次都抓。token 失效会提示重新打开活动页。</div>
          <div class="frow">
            <button class="btn pri" onclick="act({action:'luck_run'},()=>setTimeout(pullState,3000))">刷任务并抽奖</button>
            <button class="btn" onclick="act({action:'luck_run',dry:1},()=>setTimeout(pullState,3000))">只查状态</button>
            <button class="btn" onclick="act({action:'luck_run',no_tasks:1},()=>setTimeout(pullState,2500))">只抽不刷任务</button>
          </div>
          <div id="luckBox"></div>
        </div>
      </div>

      <!-- BASS 任务 -->
      <div class="page" id="p-bass">
        <div class="phead"><div><h2>BASS 任务</h2><div class="desc">cmcc_fj.py 参数化执行：签到、领奖、喂养、探测</div></div></div>
        <div class="panel">
          <h3>BASS 附加动作</h3>
          <div class="frow"><label>账号</label><select class="inp" id="bassRunKey"></select></div>
          <div class="frow"><label>选项</label>
            <label class="chk"><input type="checkbox" id="bClaim">--claim 领阶段奖励</label>
            <label class="chk"><input type="checkbox" id="bRoll">--roll 掷骰子+开箱</label>
            <label class="chk"><input type="checkbox" id="bDry">--dry-run 只读</label>
          </div>
          <div class="frow"><label>喂养次数</label><input class="inp" id="bFeed" type="number" min="0" max="50" value="0" style="min-width:70px">
            <span style="font-size:12px;color:var(--sub)">每次耗 250 饲料（游戏内正常消耗）</span></div>
          <div class="frow"><label style="color:var(--warn)">伪造类</label>
            <label class="chk"><input type="checkbox" id="bTasks">--tasks 养成浏览任务（空报）</label>
            <label class="chk"><input type="checkbox" id="bFb">--fb-tasks 流量银行任务（空报）</label>
          </div>
          <div class="warnbox">「空报」= 跳过真实浏览直接上报完成，属伪造行为记录。勾选后点执行会再弹一次确认。</div>
          <div class="frow">
            <button class="btn pri" onclick="runBass()">执行</button>
            <button class="btn" onclick="act({action:'bass_probe',key:V('bassRunKey')})">probe_fj 只读探测</button>
          </div>
        </div>
        <div class="panel">
          <h3>每日奖励 &amp; 福袋 <span class="hint">三档 150/250/350 饲料，每档间隔 60 分钟，每天 6 点重置</span></h3>
          <div class="infobox">「阶段一/二/三」是喂养奖励，<b>currentTask=true</b> 那档才是当前该领的；
            两档之间要隔 60 分钟，没到时间会返回 <code>1504</code>。福袋则是<b>每累计喂食 300 次</b>触发一次。
            时间是脚本<b>观测到</b>该状态的时刻，首次记录可能略晚于真实领取时间。<br>
            三档已交给计划任务 <b>MobileDailyReward</b> 每小时自动领取（「定时任务」页可查），不用手动点。</div>
          <div id="dailyBox"></div>
        </div>
        <div class="panel">
          <h3>流量银行任务周期 <span class="hint">每周一次 / 每月一次，记录上次完成时间并推算下次可做</span></h3>
          <div class="infobox">服务端只返回「已完成 / 未做」，不提供完成时间。这里记录脚本每次观测到的状态变化，
            跑满一个周期后就能反推真实重置点。<b>下次可做为估算值</b>，实际以服务端重置为准。</div>
          <div id="fbTasks"></div>
        </div>
      </div>

      <!-- 消消乐 -->
      <div class="page" id="p-xxl">
        <div class="phead"><div><h2>消消乐</h2><div class="desc">cmcc_xiaoxiaole.py · 伪造通关成绩换抽奖，中奖靠概率</div></div></div>
        <div class="panel">
          <h3>消消乐执行</h3>
          <div class="frow"><label>账号</label><select class="inp" id="xxlFile"></select></div>
          <div class="frow"><label>关卡 --level</label><input class="inp" id="xLevel" type="number" value="49" style="min-width:80px">
            <label style="min-width:auto">星级</label><input class="inp" id="xStar" type="number" min="0" max="3" value="3" style="min-width:60px">
            <label style="min-width:auto">局数</label><input class="inp" id="xTimes" type="number" min="1" max="20" value="5" style="min-width:60px">
            <label style="min-width:auto">间隔秒</label><input class="inp" id="xGap" type="number" min="5" max="120" value="12" style="min-width:70px"></div>
          <div class="warnbox">关卡号必须接近账号真实进度（乱填会 -100 异常请求）；每天约 5 次可用，有全局频控；性质比空报浏览更重，请自行决定使用。</div>
          <div class="frow">
            <button class="btn pri" onclick="runXxl()">开始执行</button>
            <button class="btn" onclick="act({action:'xxl_run',file:V('xxlFile'),dry:1})">dry-run 查剩余次数</button>
            <button class="btn" onclick="act({action:'xxl_run',file:V('xxlFile'),detect:1})">探测当前关卡</button>
          </div>
        </div>
      </div>

      <!-- 秒杀抢券 -->
      <div class="page" id="p-seckill">
        <div class="phead"><div><h2>秒杀抢券</h2><div class="desc">cmcc_seckill.py · 签到有礼「每周秒杀」每天 12:00 抢 1 元话费券</div></div></div>
        <div class="panel">
          <h3>执行秒杀</h3>
          <div class="frow"><label>账号</label><select class="inp" id="skFile"></select></div>
          <div class="frow"><label>开抢时刻</label><input class="inp" id="skAt" value="11:59:50" style="min-width:100px">
            <span style="font-size:12px;color:var(--sub)">留空则由脚本按 secConfig 自动选场次</span></div>
          <div class="frow">
            <button class="btn pri" onclick="runSeckill()">等到点自动抢</button>
            <button class="btn" onclick="act({action:'seckill_run',file:V('skFile'),dry:1})">dry-run 查场次</button>
            <button class="btn" onclick="act({action:'seckill_run',file:V('skFile'),once:1})">立即打一次（验证）</button>
          </div>
          <div class="infobox">流程：校服务器时间 → 拉场次 → 查当日签到资格 → 等到点 → 密集重试 <code>redeem</code>。
            开抢时会<b>提前 0.4 秒</b>出手以抵消网络延迟，被拒后每 2 秒核对一次库存，没货立刻停手。
            抢到的券进 App「我的奖品」核销，脚本会推送到手机。</div>
          <div class="warnbox"><b>真正的前提是「早上那次签到跑过」</b>——它会自动备好活动会话，
            12 点就能直接用。如果某号从没签到过、且 app_token 也已过期，
            脚本会提示「请重新抓包」并推送通知。</div>
          <div class="frow"><label>会话就绪</label><span style="font-size:13px" id="skSess">加载中…</span></div>
          <div class="frow"><label>票据寿命</label><span style="font-size:13px" id="tlBox">加载中…</span></div>
          <div class="infobox">票据寿命由计划任务 <code>MobileTokenLifeProbe</code> 每 2 分钟探测一次
            （每账号只发一次登录请求，确认失效后自动停手）。
            签发时刻取自 HAR 里 <code>appTokenLogin</code> 的时间戳，
            所以结论能精确到分钟，而不是「大概半小时」。</div>
          <div class="infobox">每次 qwhdhub 签到成功后，会自动保存该账号的活动会话 cookie
            （<code>gui_data/seckill_session_&lt;尾号&gt;.json</code>）。实测<b>仅凭该 cookie 就能调用秒杀接口，
            完全不需要 app_token</b>。</div>
          <div class="warnbox"><b>会话只能活 2 小时</b>（实测：04:22 建立 → 06:22 服务端返回
            <code>GOTO_LOGIN</code> 准时失效）。所以要赶上 12:00 开抢，
            必须在 <b>10:00 之后</b>再签到一次；早上 08:30 那次签到的会话 10:30 就过期了。
            计划任务 <b>MobileSessionRefresh</b>（10:15）就是干这个的——
            它建立的新会话能撑到 12:15，覆盖开抢时刻。<br>
            若那时 app_token 也过期了（票据约 30~40 分钟），仍需你抓一次包导入。</div>
        </div>
      </div>

      <!-- 定时任务 -->
      <div class="page" id="p-sched">
        <div class="phead"><div><h2>定时任务</h2><div class="desc">Windows 计划任务注册 / 注销（PowerShell Register-ScheduledTask）</div></div></div>
        <div class="panel">
          <h3>Windows 计划任务</h3>
          <div class="frow"><label>签到任务</label><span style="font-size:13px">MobileSignIn → run_scheduled.bat，每天</span>
            <input class="inp" id="tSign" value="08:30" style="min-width:80px">
            <button class="btn pri sm" onclick="act({action:'sched_reg',kind:'sign',time:V('tSign')})">注册/更新</button>
            <button class="btn sm danger" onclick="cf('注销 MobileSignIn？',()=>act({action:'sched_unreg',name:'MobileSignIn'}))">注销</button></div>
          <div class="frow"><label>每日奖励任务</label><span style="font-size:13px">MobileDailyReward → run_daily_reward.bat，每小时</span>
            <input class="inp" id="tDaily" value="06:00" style="min-width:80px">
            <button class="btn pri sm" onclick="act({action:'sched_reg',kind:'daily',time:V('tDaily')})">注册/更新</button>
            <button class="btn sm danger" onclick="cf('注销 MobileDailyReward？',()=>act({action:'sched_unreg',name:'MobileDailyReward'}))">注销</button></div>
          <div class="frow"><label>会话保活任务</label><span style="font-size:13px">MobileJwtKeepAlive → run_jwt_keepalive.bat，每 90 分钟</span>
            <input class="inp" id="tKeep" value="00:00" style="min-width:80px">
            <button class="btn pri sm" onclick="act({action:'sched_reg',kind:'keepalive',time:V('tKeep')})">注册/更新</button>
            <button class="btn sm danger" onclick="cf('注销 MobileJwtKeepAlive？',()=>act({action:'sched_unreg',name:'MobileJwtKeepAlive'}))">注销</button></div>
          <div class="frow"><label>会话刷新任务</label><span style="font-size:13px">MobileSessionRefresh → run_session_refresh.bat，每天</span>
            <input class="inp" id="tSess" value="10:15" style="min-width:80px">
            <button class="btn pri sm" onclick="act({action:'sched_reg',kind:'sess',time:V('tSess')})">注册/更新</button>
            <button class="btn sm danger" onclick="cf('注销 MobileSessionRefresh？',()=>act({action:'sched_unreg',name:'MobileSessionRefresh'}))">注销</button></div>
          <div class="frow"><label>秒杀抢券任务</label><span style="font-size:13px">MobileSeckill → run_seckill.bat，脚本内等到 12:00</span>
            <input class="inp" id="tSeckill" value="10:30" style="min-width:80px">
            <button class="btn pri sm" onclick="act({action:'sched_reg',kind:'seckill',time:V('tSeckill')})">注册/更新</button>
            <button class="btn sm danger" onclick="cf('注销 MobileSeckill？',()=>act({action:'sched_unreg',name:'MobileSeckill'}))">注销</button></div>
          <div class="frow"><label>票据寿命探测</label><span style="font-size:13px">MobileTokenLifeProbe → run_token_life.bat，每 2 分钟</span>
            <input class="inp" id="tProbe" value="00:00" style="min-width:80px">
            <button class="btn pri sm" onclick="act({action:'sched_reg',kind:'probe',time:V('tProbe')})">注册/更新</button>
            <button class="btn sm danger" onclick="cf('注销 MobileTokenLifeProbe？',()=>act({action:'sched_unreg',name:'MobileTokenLifeProbe'}))">注销</button></div>
          <div class="infobox">每日奖励三档（150/250/350）之间必须间隔 60 分钟，一次跑只能领一档。
            这个任务从设定时间起每小时检查一次、共 17 次，能领就领，领完自动空转。<br>
            <b>会话保活</b>：qwhdhub 的 jwt 实测只能活 2.5~3.5 小时，每 90 分钟续一次就能让凭证链一直不断，
            省去反复抓包。电脑连续关机超过几小时链还是会断，那时才需要重新抓包。<br>
            <b>票据寿命探测</b>：每 2 分钟探一次 app_token 登录，签发时刻取自 HAR，
            所以「票据到底活多久」能精确到分钟；确认失效后自动停手不再空转。</div>
          <div class="frow"><button class="btn" onclick="act({action:'sched_query'})">查询当前状态</button>
            <span style="font-size:12px;color:var(--sub)">日志：logs\daily.log / logs\xiaoxiaole.log</span></div>
        </div>
      </div>

      <!-- 日志 -->
      <div class="page" id="p-logs">
        <div class="phead"><div><h2>日志</h2><div class="desc">所有操作共用，自动跟随滚动</div></div>
          <div class="ops"><button class="btn sm" onclick="clearConsole()">清屏</button></div></div>
        <div class="panel" style="padding:14px">
          <div class="console" id="console"></div>
        </div>
      </div>

    </div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
const $=id=>document.getElementById(id), V=id=>$(id).value.trim();
let logSeq=0, STATE=null;
const TITLES={dash:'总览',token:'Token 管理',bass:'BASS 任务',xxl:'消消乐',seckill:'秒杀抢券',sched:'定时任务',logs:'日志'};
function nav(b){document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('on'));$('p-'+b.dataset.p).classList.add('on');
  $('pageTitle').textContent='签到控制台 / '+(TITLES[b.dataset.p]||'');
  try{history.replaceState(null,'','#'+b.dataset.p);}catch(e){}}
function toast(m){const t=$('toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',2600);}
function cf(msg,fn){if(confirm(msg))fn();}
async function act(body,after){
  const r=await(await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(!r.ok)toast((r.err||'失败：可能正忙')); else if(r.msg)toast(r.msg);
  if(after)after(); pullState(); return r;
}
function mask(p){return p.length===11?p.slice(0,3)+'****'+p.slice(7):p;}
function tokenBadge(s){
  if(s==='ok')return '<span class="badge ok">Token 有效</span>';
  if(s==='expired')return '<span class="badge bad">Token 已过期</span>';
  if(s==='empty')return '<span class="badge na">Token 未配置</span>';
  return '<span class="badge na">Token 未校验</span>';}
function ymd(d){const p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());}
function todayLocal(){return ymd(new Date());}
function last7(map){
  const out=[];
  for(let i=6;i>=0;i--){const d=new Date();d.setDate(d.getDate()-i);const k=ymd(d);
    out.push(map[k]||{date:k,flow_mb:0,tasks:0,feeds:0});}
  return out;
}
function signedBadge(date){
  return date===todayLocal()?'<span class="badge signed">今日已签</span>':'<span class="badge na">今日未签</span>';}
function fmtNum(v){return v==null?'—':v;}

function render(){
  if(!STATE)return;
  const S=STATE.stats, accts=STATE.raw_accounts;
  // KPI
  let flow=0,tasks=0,okTok=0;
  const days={},dayT={};
  accts.forEach(a=>{const s=S[a.key]||{};flow+=s.flow_mb_total||0;tasks+=s.tasks_done_total||0;
    const tk=a.plat==='qwhdhub'?s.token_qwhd:s.token_bass; if(tk==='ok')okTok++;
    (s.history||[]).forEach(h=>{days[h.date]=(days[h.date]||0)+(h.flow_mb||0);dayT[h.date]=(dayT[h.date]||0)+(h.tasks||0);});});
  const dks=Object.keys(days).sort().slice(-7);
  const spark=(map)=>{const vals=dks.map(d=>map[d]||0);
    if(vals.length<2||!vals.some(v=>v>0))return '';
    const mx=Math.max(...vals),n=vals.length;
    const pts=vals.map((v,i)=>`${(i*100/(n-1)).toFixed(1)},${(25-23*v/mx).toFixed(1)}`).join(' ');
    return `<svg class="spark" viewBox="0 0 100 28" preserveAspectRatio="none"><polyline points="${pts}"/></svg>`;};
  const spF=spark(days),spT=spark(dayT);
  $('sumbar').innerHTML=`
    <div class="kpi"><div class="k">累计领取流量</div><div class="v">${flow>=1024?(flow/1024).toFixed(2)+'<small>GB</small>':flow.toFixed(0)+'<small>MB</small>'}</div>${spF||'<div class="c">全部账号历史合计</div>'}</div>
    <div class="kpi"><div class="k">累计完成任务</div><div class="v">${tasks}<small>个</small></div>${spT||'<div class="c">doTask / fb 任务</div>'}</div>
    <div class="kpi"><div class="k">Token 可用</div><div class="v">${okTok}<small>/ ${accts.length}</small></div><div class="c">过期需重新获取</div></div>
    <div class="kpi"><div class="k">账号数</div><div class="v">${STATE.accounts.length}<small>个号码</small></div><div class="c">qwhdhub + BASS 配置</div></div>`;
  // 运行记录统计条
  const e=STATE.eff||{};
  const mins=e.saved_min||0;
  $('effbar').innerHTML=`
    <div class="cell"><div class="k">累计运行</div><div class="v">${e.total_runs||0}<small> 次</small></div></div>
    <div class="cell"><div class="k">成功率</div><div class="v">${e.success_rate==null?'—':e.success_rate+'<small> %</small>'}</div></div>
    <div class="cell"><div class="k">机器工时</div><div class="v">${e.machine_sec>=3600?(e.machine_sec/3600).toFixed(1)+'<small> 小时</small>':Math.round((e.machine_sec||0)/60)+'<small> 分钟</small>'}</div></div>
    <div class="cell"><div class="k">节省人工（估）</div><div class="v">${mins>=60?(mins/60).toFixed(1)+'<small> 小时</small>':mins+'<small> 分钟</small>'}</div></div>`;
  $('effHint').textContent=`今日已运行 ${e.today_runs||0} 次 · 节省时间按人工每轮约 5 分钟估算`;
  const runs=e.recent||[];
  $('runtab').innerHTML= runs.length
    ? '<tr><th>时间</th><th>任务</th><th class="num">耗时</th><th>结果</th></tr>'+runs.map(r=>`<tr>
        <td style="color:var(--sub)">${r.ts}</td><td>${r.label}</td>
        <td class="num">${r.sec>=60?Math.floor(r.sec/60)+'分'+Math.round(r.sec%60)+'秒':r.sec+'秒'}</td>
        <td class="${r.ok?'ok':'bad'}">${r.ok?'成功':'失败'}</td></tr>`).join('')
    : '<tr><td style="color:var(--sub);padding:14px">暂无运行记录 — 跑一次签到或刷新状态后自动记录</td></tr>';
  // 账号卡
  $('cards').innerHTML=STATE.accounts.map(g=>{
    const plats=Object.entries(g.plats).map(([plat,a])=>{
      const s=a.stats||{};
      const isQ=plat==='qwhdhub';
      const tkState=isQ?(s.token_qwhd||(a.token_state==='filled'?'unchecked':'empty')):(s.token_bass||(a.token_state==='filled'?'unchecked':'empty'));
      const hmap={}; (s.history||[]).forEach(h=>{hmap[h.date]=h;});
      const hist=last7(hmap);
      const hasData=hist.some(h=>(h.flow_mb||0)>0||(h.tasks||0)>0||(h.feeds||0)>0);
      const maxF=Math.max(1,...hist.map(h=>h.flow_mb||0));
      const bars=hist.map(h=>{
        const f=h.flow_mb||0, fd=h.feeds||0, tk=h.tasks||0;
        const hgt=f>0?Math.max(10,(f/maxF)*100):(fd>0?45:0);
        const parts=[];
        if(f)parts.push(f+'MB');
        if(tk)parts.push(tk+'任务');
        if(fd)parts.push(fd+'次喂养');
        const tip=`${h.date.slice(5)} · ${parts.length?parts.join(' · '):'无记录'}`;
        const cls=(f>0||fd>0||tk>0)?'bar':'bar off';
        return `<div class="${cls}" style="height:${hgt}%"><span class="tip">${tip}</span></div>`;
      }).join('');
      const dates=hist.map(h=>`<span>${h.date.slice(8)}</span>`).join('');
      return `<div class="plat">
        <div class="pt">${isQ?'qwhdhub · 全国签到':'BASS · 福建活动'} ${tokenBadge(tkState)}
          ${signedBadge(isQ?s.today_signed_qwhd:s.today_signed_bass)}</div>
        <div class="stats">
          ${isQ?`
          <div class="stat"><div class="k">累计领取</div><div class="v">${(s.flow_mb_total||0)>=1024?((s.flow_mb_total/1024).toFixed(2)+'<small>GB</small>'):((s.flow_mb_total||0).toFixed(0)+'<small>MB</small>')}</div></div>
          <div class="stat"><div class="k">连签天数</div><div class="v">${fmtNum(s.sign_days)}</div></div>
          <div class="stat"><div class="k">完成任务</div><div class="v">${s.tasks_done_total||0}</div></div>
          <div class="stat"><div class="k">最近奖品</div><div class="v wrap">${s.last_reward||'—'}</div></div>`
          :`
          <div class="stat"><div class="k">喂养次数</div><div class="v">${s.feeds_total||0}<small>次</small></div></div>
          <div class="stat"><div class="k">饲料余额</div><div class="v y">${fmtNum(s.fodder)}</div></div>
          <div class="stat"><div class="k">流量可兑</div><div class="v">${s.fb_exchange||'—'}</div></div>
          <div class="stat"><div class="k">阶段进度</div><div class="v wrap">${s.stage||'—'}</div></div>`}
        </div>
        <div class="acts">
          ${isQ?`<button class="btn sm pri" onclick="act({action:'qwhd_run',key:'${a.key}'})">签到</button>
            <button class="btn sm" onclick="act({action:'qwhd_run',key:'${a.key}',dry:1})">只读检查</button>
            <button class="btn sm" onclick="act({action:'qwhd_run',key:'${a.key}',claim:1})">签到+领奖</button>`
           :`<button class="btn sm pri" onclick="act({action:'bass_run',key:'${a.key}'})">签到</button>
            <button class="btn sm" onclick="act({action:'bass_run',key:'${a.key}',dry:1})">只读检查</button>
            <button class="btn sm" onclick="act({action:'bass_run',key:'${a.key}',claim:1,roll:1})">领奖+骰子</button>`}
        </div>
        ${hist.length&&hasData?`<div class="hist"><div class="t">近 7 天记录（流量 / 任务 / 喂养）</div><div class="bars">${bars}</div><div class="bardates">${dates}</div></div>`:`<div class="hist"><div class="t">近 7 天</div><div class="empty">暂无记录</div></div>`}
      </div>`;}).join('');
    return `<div class="acct">
      <div class="acct-head"><span class="phone">${mask(g.phone)}</span><span class="raw">${g.phone}</span></div>
      <div class="acct-body">${plats||'<div class="plat">未配置任何平台</div>'}</div>
    </div>`;
  }).join('');
  // 下拉框
  const bk=STATE.bass_files;
  fillSel('bassFile',bk); fillSel('xxlFile',bk);
  fillSel('bassRunKey',accts.filter(a=>a.plat==='BASS').map(a=>a.key));
  // 秒杀只作用于 qwhdhub（签到有礼活动）
  fillSel('skFile',accts.filter(a=>a.plat==='qwhdhub').map(a=>a.file));
  // 秒杀会话就绪状态
  const skS=$('skSess');
  if(skS){
    const ss=(STATE.seckill_sessions||[]);
    if(!ss.length){skS.innerHTML='<span style="color:var(--warn)">暂无会话 —— 请先跑一次 qwhdhub 签到</span>';}
    else{
      skS.innerHTML=ss.map(x=>{
        const t=x.at?x.at.replace('T',' ').slice(5,16):'?';
        let tail='<span style="color:var(--sub)">未知</span>';
        if(x.left_min!==null&&x.left_min!==undefined){
          if(x.left_min<=0) tail='<span style="color:var(--bad)">已过期</span>';
          else if(x.left_min<60) tail=`<span style="color:var(--warn)">剩 ${x.left_min} 分钟</span>`;
          else tail=`<span style="color:var(--ok)">剩 ${Math.round(x.left_min/60)} 小时</span>`;
        }
        return `<span class="pill" style="margin-right:6px">${x.phone?mask(x.phone):x.file} · ${t} · ${tail}</span>`;
      }).join('');
    }
  }
  // app_token 寿命探测结论
  const tl=$('tlBox');
  if(tl){
    const ts=(STATE.token_life||[]);
    if(!ts.length){tl.innerHTML='<span style="color:var(--sub)">还没有探测数据。导入 HAR 后由计划任务每 2 分钟自动探测。</span>';}
    else{
      tl.innerHTML=ts.map(x=>{
        let tag;
        if(x.state==='alive') tag='<span style="color:var(--ok)">有效</span>';
        else if(x.state==='expired') tag='<span style="color:var(--bad)">已失效</span>';
        else tag='<span style="color:var(--warn)">未确认</span>';
        const iss=x.issued_at?x.issued_at.replace('T',' ').slice(5,16):'?';
        return `<div class="pill" style="display:block;margin-bottom:6px;padding:8px 10px">
          <b>${x.phone?mask(x.phone):x.key}</b> · ${tag}
          <span style="color:var(--sub)">签发 ${iss} · ${x.conclusion||''}</span>
        </div>`;
      }).join('');
    }
  }
  // HAR 导入的账号兜底下拉（BASS token 无法自证归属时用）
  const hk=$('harKey');  if(hk&&!hk.dataset.done){
    const seen=new Set();
    const opts=accts.filter(a=>{if(!a.phone||seen.has(a.phone))return false;seen.add(a.phone);return true;})
      .map(a=>`<option value="${a.key}">${a.key} · ${mask(a.phone)}</option>`).join('');
    hk.innerHTML='<option value="">自动识别（按 HAR 号码）</option>'+opts;
    hk.dataset.done='1';
  }
  $('hsub').textContent=`${STATE.accounts.length} 个号码 · ${accts.length} 份配置`;
  renderFlowbank();
  renderDaily();
  renderLuck();
}
function renderLuck(){
  const box=$('luckBox'); if(!box)return;
  const lk=STATE.luck||{};
  const keys=Object.keys(lk);
  if(!keys.length){
    box.innerHTML='<div class="empty">还没有记录。点「只查状态」看剩余次数，或直接「刷任务并抽奖」。</div>';
    return;
  }
  box.innerHTML=keys.map(k=>{
    const a=lk[k]||{};
    const ps=a.last_prizes||[];
    const rows=ps.map(p=>{
      const isWin=p.name&&p.name.indexOf('谢谢')<0;
      return `<tr><td>${p.at?p.at.slice(11):''}</td>
        <td style="color:${isWin?'var(--ok)':'var(--sub)'}">${isWin?'★ ':''}${p.name||'—'}</td>
        <td>${p.life?p.life+' 天':''}</td></tr>`;
    }).join('');
    const summary=(a.last_draws!=null)?`　上次抽 ${a.last_draws} 次，中 ${a.last_wins||0} 次`:'';
    return `<div style="margin-top:14px">
      <div class="fbhead">${k} · ${a.phone||''}　剩余次数 <b>${a.surplus!=null?a.surplus:'—'}</b>${summary}　<code>${a.updated||''}</code></div>
      ${ps.length?`<table class="fbtab"><thead><tr><th style="width:110px">时间</th><th>奖品</th><th style="width:90px">有效期</th></tr></thead><tbody>${rows}</tbody></table>`:''}
    </div>`;
  }).join('');
}
// 每日奖励：下一档可领时间 = 上一个已领档时间 + 该档间隔
function nextDailyTime(tiers){
  const cur=tiers.find(t=>t.current&&t.status!=='RECEIVED');
  if(!cur)return '';
  // 优先用服务端给的 limitReceiveTime（精确），拿不到再退回"上次领取 + interval"推算
  if(cur.limit_at)return `今天 ${cur.limit_at}`;
  const last=[...tiers].reverse().find(t=>t.status==='RECEIVED'&&t.at);
  if(!last||!cur.interval)return '可领';
  const d=new Date(last.at.replace(' ','T'));
  if(isNaN(d.getTime()))return '可领';
  d.setMinutes(d.getMinutes()+cur.interval);
  const hh=String(d.getHours()).padStart(2,'0'), mm=String(d.getMinutes()).padStart(2,'0');
  return ymd(d)===todayLocal()?`今天 ${hh}:${mm}`:`${ymd(d)} ${hh}:${mm}`;
}
function renderDaily(){
  const box=$('dailyBox'); if(!box)return;
  const dd=STATE.daily||{};
  const keys=Object.keys(dd).filter(k=>dd[k]&&((dd[k].tiers&&dd[k].tiers.length)||dd[k].sign));
  if(!keys.length){
    box.innerHTML='<div class="empty">还没有记录。跑一次 BASS 任务后，这里会显示三档奖励的领取状态和福袋进度。</div>';
    return;
  }
  box.innerHTML=keys.map(k=>{
    const a=dd[k], tiers=a.tiers||[];
    const got=tiers.filter(t=>t.status==='RECEIVED').length;
    const nt=nextDailyTime(tiers);
    const chips=tiers.map(t=>{
      const ok=t.status==='RECEIVED', cur=t.current&&!ok;
      const color=ok?'var(--ok)':(cur?'var(--warn)':'var(--sub)');
      const mark=ok?'✓':(cur?'●':'○');
      return `<span style="display:inline-block;margin-right:16px;color:${color}">${mark} ${t.name} ${t.fodder}饲料${t.at?` <code>${t.at.slice(11)}</code>`:''}</span>`;
    }).join('');
    const lk=a.lucky||{};
    const sg=a.sign||{};
    const pct=lk.need?Math.min(100,Math.round((lk.current/lk.need)*100)):0;
    const bar=lk.need?`<div style="height:6px;background:var(--line);border-radius:3px;margin-top:7px;max-width:440px">
        <div style="height:6px;width:${pct}%;background:${lk.triggered?'var(--ok)':'var(--acc)'};border-radius:3px"></div></div>`:'';
    const signLine=sg.cycle!=null?`<div style="font-size:12.5px;margin-bottom:7px">
        <span style="color:${sg.today?'var(--ok)':(sg.open?'var(--warn)':'var(--sub)')}">${sg.today?'✓ 今日已签':(sg.open?'● 待签':'○ 未到签到时间')}</span>
        　连签第 <b>${sg.cycle}</b> 天${sg.today_fodder?`　今日 ${sg.today_fodder}g`:''}${sg.next_fodder?` · 明日 ${sg.next_fodder}g`:''}${sg.begin_hour?`　<code>${sg.begin_hour}:00</code> 开放`:''}
      </div>`:'';
    return `<div style="margin-bottom:16px">
      <div class="fbhead">${k} · 已领 ${got}/${tiers.length} 档${nt?`　下一档 <b style="color:var(--warn)">${nt}</b>`:'　今日已领完'}</div>
      ${signLine}
      <div style="font-size:12.5px">${chips}</div>
      <div style="font-size:12px;color:var(--sub);margin-top:8px">福袋 ${lk.current||0}/${lk.need||0}${lk.more!=null?`　还差 ${lk.more} 次喂食`:''}${lk.triggered?'　<b style="color:var(--ok)">已触发！</b>':''}</div>
      ${bar}
    </div>`;
  }).join('');
}
// 周期推算：每周 +7 天，每月 +1 个月（仅估算，实际以服务端重置为准）
function nextRun(cycle,lastDone){
  if(!lastDone)return '—';
  if(cycle===0)return '不限';
  const d=new Date(lastDone.replace(' ','T'));
  if(isNaN(d.getTime()))return '—';
  if(cycle===2)d.setDate(d.getDate()+7);
  else if(cycle===1)d.setMonth(d.getMonth()+1);
  else return '—';
  const now=new Date();
  const k=ymd(d);
  return k<=todayLocal()?'<span style="color:var(--ok)">今天可做</span>':k;
}
function renderFlowbank(){
  const box=$('fbTasks'); if(!box)return;
  const fb=STATE.flowbank||{};
  const keys=Object.keys(fb).filter(k=>fb[k]&&fb[k].tasks&&Object.keys(fb[k].tasks).length);
  if(!keys.length){
    box.innerHTML='<div class="empty">还没有记录。在「执行」里勾上 --fb-tasks 跑一次，这里就会列出每个任务的周期、奖励和下次可做时间。</div>';
    return;
  }
  const badge=s=>s==='1'?'<span class="badge signed">已完成</span>'
    :(s==='2'?'<span class="badge" style="color:var(--warn)">进行中</span>'
    :'<span class="badge bad">可做</span>');
  box.innerHTML=keys.map(k=>{
    const acc=fb[k]||{},tasks=acc.tasks||{};
    const ids=Object.keys(tasks).sort((a,b)=>((tasks[a].cycle||9)-(tasks[b].cycle||9)));
    const rows=ids.map(id=>{
      const t=tasks[id];
      const cyc=t.cycle===1?'每月一次':(t.cycle===2?'每周一次':'不限');
      const flow=t.flow?(`+${t.flow}${t.unit==='M'?'MB':t.unit}`):'—';
      return `<tr><td>${t.title||('#'+id)}</td><td>${cyc}</td><td>${flow}</td>
        <td>${badge(t.status)}</td><td><code>${t.last_done||'—'}</code></td><td>${nextRun(t.cycle,t.last_done)}</td></tr>`;
    }).join('');
    return `<div style="margin-bottom:16px">
      <div class="fbhead">${k} · 最后更新 ${acc.updated||'—'}</div>
      <table class="fbtab"><thead><tr><th>任务</th><th>周期</th><th>奖励</th><th>状态</th><th>上次完成</th><th>下次可做</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }).join('');
}
function fillSel(id,vals){const s=$(id);if(!s)return;const cur=s.value;s.innerHTML=vals.map(v=>`<option>${v}</option>`).join('');if(vals.includes(cur))s.value=cur;}
async function pullState(){STATE=await(await fetch('/api/state')).json();render();
  const p=$('taskPill');
  if(STATE.task.running){p.textContent='运行中 · '+STATE.task.label;p.classList.add('run');}else{p.textContent='空闲';p.classList.remove('run');}}
function runBass(){
  const warn=[];if($('bTasks').checked)warn.push('养成浏览任务（空报伪造）');if($('bFb').checked)warn.push('流量银行任务（空报伪造）');
  if(warn.length&&!confirm('本次将执行伪造浏览类任务：\n- '+warn.join('\n- ')+'\n\n确定继续吗？'))return;
  act({action:'bass_run',key:V('bassRunKey'),dry:$('bDry').checked?1:0,claim:$('bClaim').checked?1:0,roll:$('bRoll').checked?1:0,
       feed:parseInt($('bFeed').value||0),tasks:$('bTasks').checked?1:0,fb:$('bFb').checked?1:0});}
function runXxl(){if(!confirm('消消乐属于伪造游戏通关成绩，中奖靠服务端概率，确定要跑吗？'))return;
  act({action:'xxl_run',file:V('xxlFile'),level:V('xLevel'),star:V('xStar'),times:V('xTimes'),gap:V('xGap')});}
function runSeckill(){
  const at=V('skAt').trim();
  act({action:'seckill_run',file:V('skFile'),at:at||''});}
function afterHar(r){
  const el=$('harOut'); if(!el)return;
  const rep=(r&&r.report)||[];
  if(!rep.length){el.style.display='none';return;}
  el.style.display='block';
  el.innerHTML=rep.map(t=>`<div>${t.replace(/[<>]/g,'')}</div>`).join('');
}
function importHar(){
  const f=$('harFile').files[0];
  if(!f){toast('先选择一个 HAR 文件');return;}
  const fr=new FileReader();
  fr.onload=async()=>{
    const r=await act({action:'har_import',har:fr.result,key:V('harKey')},()=>setTimeout(pullState,1500));
    afterHar(r);
  };
  fr.onerror=()=>toast('读取文件失败');
  fr.readAsText(f);
}
function runAll(platform){
  // 消消乐只跟随「一键签到」；只跑单个平台时不带，避免误消耗游戏次数
  const xxl=(platform==='all'&&$('dashXxl')&&$('dashXxl').checked)?1:0;
  return act({action:'run_all',platform:platform,
    dry:($('dashDry')&&$('dashDry').checked)?1:0, xxl:xxl});}
function refreshStatus(){
  if($('dashDry'))$('dashDry').checked=false;
  act({action:'refresh_status'});}
// 日志
const cons=$('console');
function colorize(l){
  let c='';
  if(/✓|OK |成功|已签/.test(l))c='ok';
  else if(/✗|!!|ERROR|失败|过期|异常/.test(l))c='err';
  else if(/▶|■|——|警告|⚠/.test(l))c='hl';
  else if(/^\$/.test(l))c='dim';
  return c?` class="${c}"`:'';}
async function pullLogs(){
  const r=await(await fetch('/api/logs?since='+logSeq)).json();
  if(r.lines.length){
    const atBottom=cons.scrollHeight-cons.scrollTop-cons.clientHeight<40;
    r.lines.forEach(l=>{logSeq=l.seq;
      const d=document.createElement('div');
      d.innerHTML=`<span class="t">${l.t}</span><span${colorize(l.line)}></span>`;
      d.lastChild.textContent=l.line;
      if(/✓|OK |成功|已签/.test(l.line))d.lastChild.className='ok';
      else if(/✗|!!|ERROR|失败|过期|异常/.test(l.line))d.lastChild.className='err';
      else if(/▶|■|——|⚠/.test(l.line))d.lastChild.className='hl';
      else if(l.line.startsWith('$'))d.lastChild.className='dim';
      cons.appendChild(d);});
    while(cons.children.length>2500)cons.removeChild(cons.firstChild);
    if(atBottom)cons.scrollTop=cons.scrollHeight;}
  const p=$('taskPill');
  if(r.running){p.textContent='运行中 · '+(r.label||'');p.classList.add('run');}
  else if(p.classList.contains('run')){p.textContent='空闲';p.classList.remove('run');pullState();}}
function clearConsole(){cons.innerHTML='';}
// 签到总览 dry-run 联动
document.addEventListener('change',e=>{if(e.target.id==='dashDry')window._dashDry=e.target.checked;});
const _act=act; act=async(b,a)=>{if(b.action==='run_all'&&$('dashDry').checked)b.dry=1;return _act(b,a);};
(function(){const h=location.hash.slice(1);const b=h&&document.querySelector('nav button[data-p="'+h+'"]');if(b)nav(b);})();
pullState();pullLogs();setInterval(pullLogs,1200);setInterval(pullState,6000);
</script>
</body>
</html>
"""


# ======================================================================

def verify_tokens_async():
    """启动时后台校验 token 真实有效性。

    qwhdhub 的 api 不能直接用 app_token 打（会 SSL 握手失败，必须先走 qwhdsso 换 session），
    BASS 可以直接打 getIndexInfo。所以统一复用现有脚本的 --dry-run 输出判定：
      qwhdhub → cmcc_sign.py --config X --dry-run
      BASS    → cmcc_fj.py   --config X --dry-run
    结果写进 stats 的 token_qwhd / token_bass，界面灯据此显示有效/过期/未校验。
    """
    def judge(plat, text):
        if plat == "qwhdhub":
            if ("appTokenLogin 失败" in text) or ("过期" in text) or ("重新登录" in text):
                return "expired"
            if ("累计签到" in text) or ("今日已签" in text) or ("签到成功" in text):
                return "ok"
        else:
            if ("登录状态已过期" in text) or ("token 失效" in text) or ("401" in text):
                return "expired"
            if ("饲料" in text) or ("流量银行" in text) or ("今日已签" in text):
                return "ok"
        return None
    def run_one(a):
        if a["token_state"] == "empty":
            return "empty"
        script = "cmcc_sign.py" if a["plat"] == "qwhdhub" else "cmcc_fj.py"
        try:
            p = subprocess.run([PY, script, "--config", a["file"], "--dry-run"],
                               cwd=str(BASE), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=90,
                               creationflags=NO_WINDOW)
            return judge(a["plat"], (p.stdout or "") + (p.stderr or ""))
        except Exception:
            return None
    def run():
        for a in scan_accounts():
            st = run_one(a)
            if st:
                key = "token_qwhd" if a["plat"] == "qwhdhub" else "token_bass"
                update_stats(a["key"], **{key: st})
            time.sleep(0.4)
    threading.Thread(target=run, daemon=True).start()


def main():
    verify_tokens_async()
    srv = None
    for port in range(PORT, PORT + 11):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        print(f"端口 {PORT}~{PORT+10} 均被占用，请关闭占用程序后重试")
        sys.exit(1)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    print(f"控制台已启动：{url}  （Ctrl+C 退出）")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
