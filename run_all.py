"""
run_all.py —— 多账号批量运行器（两个平台）

  qwhdhub 平台  →  configs/*.json      用 cmcc_sign.py 执行
  BASS 平台     →  configs_fj/*.json   用 cmcc_fj.py  执行

用法：
    python run_all.py                    # 两个平台全部执行
    python run_all.py --dry-run          # 全部只读检查
    python run_all.py --platform qwhd    # 只跑 qwhdhub
    python run_all.py --platform bass    # 只跑 BASS
    python run_all.py --delay 900        # 每个号之间随机等待（会退回串行）
    python run_all.py --extra="--roll"   # 给 BASS 脚本追加参数（值以 -- 开头，必须用 =）
    python run_all.py --log logs/daily.log   # 同时写一份按天轮转的日志
"""

import argparse
import datetime
import json
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import collect_notify_settings, load_json, notify

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).parent

PLATFORMS = {
    "qwhd": {
        "name": "qwhdhub (wx.10086.cn)",
        "dir": BASE / "configs",
        "script": BASE / "cmcc_sign.py",
        "id_field": "phone",
    },
    "bass": {
        "name": "BASS (wap.fj.10086.cn)",
        "dir": BASE / "configs_fj",
        "script": BASE / "cmcc_fj.py",
        "id_field": "phone",
    },
}

MAX_WORKERS = 4


# ---------------------------------------------------------------- 日志

class RotatingLog:
    """按天轮转的文本日志：每天一个文件，只保留最近 N 天。

    定时任务每天追加，长期跑下去单个日志会涨到几十 MB，
    这里在写入新一天之前把旧文件改名归档，并清掉过期的。
    """

    def __init__(self, path, keep_days=7):
        self.path = Path(path) if path else None
        self.keep = keep_days

    @property
    def enabled(self):
        return self.path is not None

    def _archive_old(self, today):
        if not self.path.parent.exists():
            return
        stem = self.path.stem
        for f in self.path.parent.glob(f"{stem}.*.log"):
            try:
                day = f.name[len(stem) + 1:].rsplit(".", 1)[0]
                age = (today - datetime.date.fromisoformat(day)).days
            except Exception:
                continue
            if age >= self.keep:
                try:
                    f.unlink()
                except OSError:
                    pass

    def write(self, text):
        if not self.enabled:
            return
        today = datetime.date.today()
        target = self.path
        if target.exists():
            try:
                mtime_day = datetime.date.fromtimestamp(target.stat().st_mtime)
            except OSError:
                mtime_day = today
            if mtime_day != today:
                self._archive_old(today)
                target = self.path.with_name(f"{self.path.stem}.{mtime_day.isoformat()}.log")
        try:
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass

    def line(self, text):
        self.write(text + "\n")


class Tee:
    """把 stdout 同时写到终端和日志文件（按行缓冲，避免频繁开文件）。"""

    def __init__(self, log):
        self.log = log
        self.term = sys.stdout
        self.buf = ""

    def write(self, s):
        self.term.write(s)
        if self.log.enabled:
            self.buf += s
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                self.log.write(line + "\n")

    def flush(self):
        self.term.flush()
        if self.log.enabled and self.buf:
            self.log.write(self.buf)
            self.buf = ""


# ---------------------------------------------------------------- 辅助

def label_of(path: Path, field: str) -> str:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        v = str(cfg.get(field) or "")
        if len(v) >= 4:
            return f"...{v[-4:]}"
    except Exception:
        pass
    return path.stem


def brief_error(text: str) -> str:
    """从子进程 stderr 里提炼一行人话，丢掉 traceback 噪音。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for ln in reversed(lines):
        if "[ERROR]" in ln:
            return ln.split("] ", 1)[-1] if "] " in ln else ln
    for ln in reversed(lines):
        if "Error:" in ln or "失败" in ln or "过期" in ln:
            return ln
    return lines[-1] if lines else ""


def run_script(script: Path, conf: Path, args: list, timeout: int,
               name: str = "", idx: int = 0, total: int = 0) -> tuple:
    """跑一个子脚本。返回 (名称, 是否成功, 耗时, 输出文本)。

    输出不在子线程里直接打印，而是回传给主线程按顺序打印，
    否则并发执行时各账号的日志会交错在一起。
    签到 / 消消乐 / 秒杀三条链路共用这一份实现。
    """
    cmd = [sys.executable, str(script), "--config", str(conf), *args]
    name = name or label_of(conf, "phone")
    head = f"--- [{idx}/{total}] {name}  ({conf.name}) ---" if idx else ""
    buf = []
    t0 = time.time()
    ok = False
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="ignore", timeout=timeout)
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        ok = res.returncode == 0
        if head:
            buf.append(head)
        if out:
            buf.append(out[-1500:])
        if not ok:
            msg = brief_error(err) or brief_error(out)
            if msg:
                buf.append(f"  ↑ 失败原因：{msg}")
        if not out and not err:
            buf.append("(无输出)")
    except subprocess.TimeoutExpired:
        if head:
            buf.append(head)
        buf.append("超时，跳过")
    except Exception as e:
        if head:
            buf.append(head)
        buf.append(f"异常：{e}")
    return name, ok, round(time.time() - t0, 1), "\n".join(buf)


def run_one(platform_key: str, conf: Path, dry_run: bool, extra: list,
            idx: int = 1, total: int = 1) -> tuple:
    """跑一个平台账号。extra 由调用方按平台决定，两个平台都要透传。"""
    p = PLATFORMS[platform_key]
    args = (["--dry-run"] if dry_run else []) + list(extra or [])
    return run_script(p["script"], conf, args, 300,
                      label_of(conf, p["id_field"]), idx, total)


def run_platform(key: str, confs: list, dry_run: bool, extra: list,
                 delay: int = 0) -> list:
    """跑一个平台下的所有账号。

    账号之间互不依赖（各自独立 token 与会话），所以默认并发执行；
    传了 --delay 时退回串行并保留账号间隔（错开请求更像真人操作）。
    """
    results = []
    total = len(confs)

    if delay:
        for i, conf in enumerate(confs, 1):
            name, ok, sec, text = run_one(key, conf, dry_run, extra, i, total)
            print(text)
            results.append((key, name, ok, sec))
            if i < total:
                w = random.randint(0, delay)
                print(f"  等待 {w} 秒 ...")
                time.sleep(w)
        return results

    workers = min(MAX_WORKERS, total)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        # 提交顺序固定，取结果也按顺序，输出不会交错
        futs = [ex.submit(run_one, key, c, dry_run, extra, i, total)
                for i, c in enumerate(confs, 1)]
        for fut in futs:
            name, ok, sec, text = fut.result()
            print(text)
            results.append((key, name, ok, sec))
    return results


def run_xxl(dry_run: bool, times: int) -> list:
    """跑消消乐：每个 configs_fj 配置一局，关卡用 --detect-level 自动探测。"""
    script = BASE / "cmcc_xiaoxiaole.py"
    d = BASE / "configs_fj"
    results = []
    if not script.exists():
        print("缺少 cmcc_xiaoxiaole.py，跳过消消乐")
        return results
    confs = sorted(d.glob("*.json")) if d.exists() else []
    if not confs:
        print("configs_fj 下没有配置，跳过消消乐")
        return results

    print("\n" + "=" * 66)
    print(f" 消消乐   {len(confs)} 个账号")
    print("=" * 66)

    def one(conf):
        cmd = [sys.executable, str(script), "--config", str(conf),
               "--star", "3", "--times", str(times), "--gap", "15"]
        cmd.append("--dry-run" if dry_run else "--detect-level")
        t0 = time.time()
        ok = False
        buf = []
        try:
            res = subprocess.run(cmd, capture_output=True, text=True,
                                 encoding="utf-8", errors="ignore", timeout=900)
            out = (res.stdout or "").strip()
            err = (res.stderr or "").strip()
            if out:
                buf.append(out[-1500:])
            ok = res.returncode == 0
            if not ok:
                msg = brief_error(err) or brief_error(out)
                if msg:
                    buf.append(f"  ↑ 失败原因：{msg}")
        except subprocess.TimeoutExpired:
            buf.append("超时，跳过")
        except Exception as e:
            buf.append(f"异常：{e}")
        return label_of(conf, "phone"), ok, round(time.time() - t0, 1), "\n".join(buf)

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(confs))) as ex:
        futs = [ex.submit(one, c) for c in confs]
        for fut in futs:
            name, ok, sec, text = fut.result()
            if text:
                print(text)
            results.append(("xxl", name, ok, sec))
    return results


# ---------------------------------------------------------------- 主流程

def seckill_timeout(at: str = "") -> int:
    """秒杀子进程的超时上限（秒）。

    脚本内部要一直睡到开抢时刻（计划任务 10:30 启动、12:00 出手，约 90 分钟），
    超时若小于这段等待，子进程会在开抢前被 kill —— 秒杀就永远不会发生。
    所以按 --at 距现在还有多久来算，再留 15 分钟给抢购与重试。
    """
    if not at:
        return 6 * 3600
    try:
        hh, mm, *ss = at.split(":")
        now = datetime.datetime.now()
        target = now.replace(hour=int(hh), minute=int(mm),
                             second=int(ss[0]) if ss else 0, microsecond=0)
        wait = (target - now).total_seconds()
    except Exception:
        return 6 * 3600
    return max(900, int(wait) + 900)


def run_seckill(dry_run: bool, at: str = "") -> list:
    """跑签到有礼「每周秒杀」：每个 configs 配置一个号，等到 12:00 自动抢。"""
    script = BASE / "cmcc_seckill.py"
    d = BASE / "configs"
    results = []
    if not script.exists():
        print("缺少 cmcc_seckill.py，跳过秒杀")
        return results
    confs = sorted(d.glob("*.json")) if d.exists() else []
    if not confs:
        print("configs 下没有配置，跳过秒杀")
        return results

    print("\n" + "=" * 66)
    print(f" 秒杀抢券   {len(confs)} 个账号")
    print("=" * 66)

    timeout = seckill_timeout(at)

    def one(conf):
        args = ["--dry-run"] if dry_run else (["--at", at] if at else [])
        return run_script(script, conf, args, timeout)

    # 各号会话独立，必须并发等到同一开抢时刻；串行会让第二个号晚几十秒出手，
    # 而库存通常只有 1 份，晚到就等于抢不到。
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(confs))) as ex:
        futs = [ex.submit(one, c) for c in confs]
        for fut in futs:
            name, ok, sec, text = fut.result()
            if text:
                print(text)
            results.append(("seckill", name, ok, sec))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--platform", choices=["qwhd", "bass", "all", "none"], default="all")
    ap.add_argument("--delay", type=int, default=0)
    ap.add_argument("--extra", default="",
                    help='传给 BASS 脚本的额外参数，写成 --extra="--roll"')
    ap.add_argument("--xxl", action="store_true", help="签完之后接着跑消消乐")
    ap.add_argument("--xxl-times", type=int, default=5, help="消消乐每个号玩几局，默认 5")
    ap.add_argument("--daily-only", action="store_true",
                    help="只补签到 + 领 BASS 每日奖励三档，用于每小时轮询")
    ap.add_argument("--log", default="", help="同时写一份按天轮转的日志")
    ap.add_argument("--no-notify", action="store_true", help="不推送失败告警")
    ap.add_argument("--keepalive", action="store_true",
                    help="只做 qwhdhub 会话保活（续期 jwt，不签到、不推送），用于定时任务")
    ap.add_argument("--seckill", action="store_true",
                    help="签完之后接着跑签到秒杀（12:00 抢话费券）")
    ap.add_argument("--seckill-only", action="store_true",
                    help="只跑秒杀，不跑签到（用于秒杀专用计划任务）")
    ap.add_argument("--seckill-at", default="", help="秒杀开抢时刻，默认按 secConfig 自动选场次")
    args = ap.parse_args()

    # 额外参数按平台分发：--extra 是给 BASS 的，qwhd 的由模式开关自己决定。
    # 早先两个平台共用一个 extra 字符串、却只透传给 bass，导致 --keepalive
    # 的 --login-only 被丢掉，保活任务变成了每 90 分钟真签到一次。
    extra_map = {"qwhd": [], "bass": args.extra.split() if args.extra else []}

    # 只领每日奖励：只跑 BASS，且把 --daily-only 透传给 cmcc_fj.py
    if args.daily_only:
        args.platform = "bass"
        args.xxl = False
        extra_map["bass"].append("--daily-only")

    # 只跑秒杀：不签到（资格由秒杀脚本自己补签），也不跑别的平台
    if args.seckill_only:
        args.seckill = True
        args.xxl = False
        args.platform = "none"

    # 保活：只登录续 jwt，不签到；失败也不推送（保活失败由每日签到去告警）
    if args.keepalive:
        args.platform = "qwhd"
        args.xxl = False
        args.dry_run = False
        args.no_notify = True
        extra_map["qwhd"].append("--login-only")

    log = RotatingLog(args.log)
    if log.enabled:
        sys.stdout = Tee(log)
    keys = ["qwhd", "bass"] if args.platform == "all" else (
        [] if args.platform == "none" else [args.platform])
    results = []

    for key in keys:
        p = PLATFORMS[key]
        d = p["dir"]
        if not d.exists():
            print(f"[{p['name']}] 目录不存在：{d}")
            continue
        confs = sorted(d.glob("*.json"))
        if not confs:
            print(f"[{p['name']}] 没有配置文件（{d.name}/）")
            continue

        print("\n" + "=" * 66)
        print(f" {p['name']}   {len(confs)} 个账号")
        print("=" * 66)

        results.extend(run_platform(key, confs, args.dry_run,
                                    extra_map[key], args.delay))

    if args.xxl:
        results.extend(run_xxl(args.dry_run, args.xxl_times))

    if args.seckill:
        results.extend(run_seckill(args.dry_run, args.seckill_at))

    if not results:
        print("\n没有任何账号被执行。")
        return 1

    print("\n" + "=" * 66)
    print(" 汇总")
    print("=" * 66)
    for key, name, ok, sec in results:
        print(f"  {'✓' if ok else '✗'} [{key:<4}] {name:<10} {sec:>6.1f}s")
    good = sum(1 for _, _, ok, _ in results if ok)
    print(f"\n  {good}/{len(results)} 成功")

    # 失败才推送，避免每天正常签到都打扰
    if not args.no_notify and good != len(results):
        cfgs = []
        for key in keys:
            for f in sorted(PLATFORMS[key]["dir"].glob("*.json")):
                cfgs.append(load_json(f, {}))
        bark, sc_key = collect_notify_settings(*cfgs)
        bad = [f"{k}:{n}" for k, n, ok, _ in results if not ok]
        notify("中国移动签到失败",
               f"{good}/{len(results)} 成功\n失败账号：{', '.join(bad)}", bark, sc_key)

    return 0 if good == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
