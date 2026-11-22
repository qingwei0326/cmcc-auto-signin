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
import os
import json
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import (atomic_write_json, child_cmd, collect_notify_settings,
                    is_token_dead, load_awards, load_json, load_policy, notify,
                    notify_once)   # 导入即固定 UTF-8 输出

BASE = Path(__file__).parent
RUNS_FILE = BASE / "gui_data" / "runs.json"

# 这一轮里「凭证过期」的账号。它们不是真的执行失败，只是票过期了，
# 汇总成一条通知就行；每小时一轮的每日奖励任务不该一小时骂一次。
TOKEN_DEAD_ACCOUNTS = []


def note_token_dead(name: str):
    if name and name not in TOKEN_DEAD_ACCOUNTS:
        TOKEN_DEAD_ACCOUNTS.append(name)


def token_dead_notice(accounts) -> tuple:
    """凭证过期的通知文案。一条说清「哪些号 + 怎么做」，不逐号发。"""
    who = "、".join(accounts)
    return ("移动签到：凭证过期",
            "以下账号的登录凭证已过期，签到/抽奖/寻宝都会跳过：\n  " + who +
            "\n\n处理：手机抓包 → App 里打开「签到领流量」和「优惠专区」各停留几秒"
            " → 导出 HAR → 控制台「凭证与账号」导入。\n"
            "（凭证寿命实测约 1 小时，抓完请尽快导入）")


def today_finished(awards=None, runs=None, today=None, results=None, dry_run=False) -> bool:
    """今天是否已经干完活了——用来决定凭证过期要不要打扰用户。

    用户的规则：**做完了就直接发总结，当天 token 过期也别再发通知**。
    所以判据要看真实收益/真实执行，而不是「有没有失败」：

      · 本轮或今天有到账收益（哪怕只是追剧领的一张券）→ 算做完；
      · 有「非只读」的成功执行（签到/抽奖/寻宝/拓展等）→ 也算做完；
      · 只有「刷新状态」「只读检查」「dry-run」「查询/配置」成功 → **不算**，
        这类动作票过期照样 ok，拿它当判据会把「一大早就全军覆没」误判成做完。

    results 是本轮 [(key, name, ok, sec)]：run_all 自己不写 runs.json（那是
    dashboard 在子进程结束后记的），所以刚跑完的成功必须直接传进来才算数。
    awards/runs/today/results 都可注入，便于测试；不传就读真实数据。
    """
    today = today or datetime.date.today().isoformat()
    awards = load_awards() if awards is None else awards
    for r in awards or []:
        if str(r.get("at", "")).startswith(today) and not r.get("ignored"):
            return True

    # 本轮刚跑成功的真实任务（排除只读/查询/配置类 action）
    # dry-run 只读检查即便 ok 也不算——票过期时它照样能过，不能拿来证明干完活。
    if not dry_run:
        for key, _name, ok, _sec in results or []:
            if ok and str(key) not in ("check", "dryrun", "query", "config"):
                return True

    runs = load_json(RUNS_FILE, default=[]) if runs is None else runs
    readonly_marks = ("只读", "dry-run", "查询", "配置", "刷新全部账号状态")
    for r in runs or []:
        if not str(r.get("ts", "")).startswith(today) or not r.get("ok"):
            continue
        label = str(r.get("label", ""))
        if any(m in label for m in readonly_marks):
            continue
        return True
    return False


# 每日自动喂养的「今天已喂过」标记，防止计划任务补跑时重复消耗饲料
FEED_STATE = BASE / "gui_data" / "feed_state.json"
# 三档饲料的领取状态（由 cmcc_fj 写入），用来判断能不能「领齐再一起喂」
DAILY_STATE = BASE / "gui_data" / "daily_state.json"
# 邮箱收 HAR 的触发标记：只有「票确实失效且今天任务没全做完」才去查邮箱
MAIL_NEED = BASE / "gui_data" / "mail_need.json"
TODAY_DONE = BASE / "gui_data" / "today_done.json"


def all_tiers_claimed() -> bool:
    """今天三档饲料（150/250/350）是否都已领到手。

    档位之间要隔 60 分钟，全领完至少要约 3 小时。用户要求先把饲料领完
    再一次性喂，而不是到点就先喂一部分，所以喂养必须等这个条件。
    """
    st = load_json(DAILY_STATE, default={}) or {}
    today = datetime.date.today().isoformat()
    confs = sorted((BASE / "configs_fj").glob("*.json"))
    if not confs:
        return False
    for c in confs:
        d = st.get(c.stem) or {}
        if d.get("tiers_date") != today:
            return False
        tiers = d.get("tiers") or []
        if len(tiers) < 3:
            return False
        if any(str(t.get("status") or "").upper() != "RECEIVED" for t in tiers[:3]):
            return False
    return True


def feed_due(pol: dict) -> bool:
    """今天这一轮要不要自动喂养。

    三个条件：过了策略指定的最早时间、三档饲料今天已全部领到手、今天还没喂过。
    最后一条防的是计划任务「错过补跑」再触发一次，白烧 250×N 饲料。
    """
    try:
        hour = int(pol.get("feed_hour"))
    except Exception:
        return False
    if datetime.datetime.now().hour < hour:
        return False
    if not all_tiers_claimed():
        return False
    st = load_json(FEED_STATE, default={}) or {}
    return st.get("date") != datetime.date.today().isoformat()


def mark_mail_needed(accounts) -> None:
    """票失效时打标记，邮箱监听只在看到这个标记后才去收 HAR。

    账号列表取**并集**：先发现 3239 失效、后一轮才发现 4167 失效时，
    旧实现会把 3239 覆盖掉，提醒里就只剩一个号，另一个号白等一整天。
    """
    prev = (load_json(MAIL_NEED, {}) or {})
    merged = list(dict.fromkeys(list(prev.get("accounts") or []) + list(accounts or [])))
    atomic_write_json(MAIL_NEED, {
        "needed": True,
        "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "accounts": merged,
    }, indent=1)


def mark_today_done() -> None:
    """今天所有任务都跑顺了：邮箱监听就不必再查。"""
    atomic_write_json(TODAY_DONE, {"date": datetime.date.today().isoformat()}, indent=1)


def should_mark_done(platform: str, good: int, total: int) -> bool:
    """这一轮跑完，算不算「今天的活儿已经干完」。

    `--platform none`（只跑消消乐 / 寻宝这类）没碰签到，不能算：
    一旦写了 today_done，mail_watch 会一整天认为「没事干」，
    11:40 发来的 HAR 再也导不进来，中午的签到和秒杀就全空。
    """
    if total <= 0 or good != total:
        return False
    return str(platform or "") != "none"


def mark_fed() -> None:
    FEED_STATE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(FEED_STATE, {
        "date": datetime.date.today().isoformat(),
        "at": datetime.datetime.now().strftime("%H:%M:%S"),
    }, indent=1)

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
                # 跨天：把旧文件改名为「它自己那天的日期」，今天的输出另起新文件。
                # 早先这里只是改了 target 的名字、并没有真的改名，于是今天的全部
                # 输出都被写进「daily_reward.2026-11-20.log」这种昨天日期的文件里
                # （而 daily_reward.log 停在昨天不动），按日期翻日志会看错。
                self._archive_old(today)
                archived = self.path.with_name(f"{self.path.stem}.{mtime_day.isoformat()}.log")
                try:
                    target.replace(archived)
                except OSError:
                    pass
                target = self.path
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
               name: str = "", idx: int = 0, total: int = 0, *,
               config_args=None) -> tuple:
    """跑一个子脚本。返回 (名称, 是否成功, 耗时, 输出文本)。

    输出不在子线程里直接打印，而是回传给主线程按顺序打印，
    否则并发执行时各账号的日志会交错在一起。
    签到 / 消消乐 / 秒杀三条链路共用这一份实现。
    """
    if config_args is None:
        config_args = ["--config", str(conf)]
    cmd = child_cmd(script, *config_args, *args)
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
        # 只要输出里明说「凭证过期」就记下来，不看退出码：
        # 有的脚本打印完照样返回 0，只按退出码判会漏。
        if is_token_dead(out) or is_token_dead(err):
            note_token_dead(name)
        if head:
            buf.append(head)
        if out:
            tail = out[-1500:]
            for line in out.splitlines():
                if "追剧领福利" in line and "抽奖" in line and line not in tail:
                    buf.append(line)
            buf.append(tail)
        if "本周已领，达到每周限领一次" in err:
            buf.append("  本周已领，达到每周限领一次")
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
    """跑消消乐：每个 configs_fj 配置一局。

    关卡每次现读 indexInfo.gameLevel（写死关卡会让次数扣了、关卡不动）；
    策略里打开 tasks 时，先做金币任务并把金币换成体力再玩。
    """
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

    pol = (load_policy().get("xxl") or {})
    gap = int(pol.get("gap") or 15)
    star = int(pol.get("star") or 3)

    def one(conf):
        args = ["--star", str(star), "--times", str(times), "--gap", str(gap)]
        if not dry_run and pol.get("tasks"):
            args.append("--tasks")
        if dry_run:
            args.append("--dry-run")
        return run_script(script, conf, args, 900)

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

    脚本内部要睡到开抢时刻（计划任务 11:55 启动、12:00 出手），
    超时必须覆盖等待、会话重试和开抢后的库存核对。
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


def run_wed(dry_run: bool) -> list:
    """周三充值日：查询/完成拼图任务、抽奖并领取已集齐奖励。"""
    script = BASE / "cmcc_wed.py"
    confs = sorted((BASE / "configs").glob("*.json"))
    results = []
    if not script.exists() or not confs:
        print("缺少 cmcc_wed.py 或 configs 下没有配置，跳过周三充值日")
        return results
    print("\n" + "=" * 66)
    print(f" 周三充值日   {len(confs)} 个账号")
    print("=" * 66)

    def one(conf):
        return run_script(script, conf, ["--dry-run"] if dry_run else [], 900)

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(confs))) as ex:
        futs = [ex.submit(one, c) for c in confs]
        for fut in futs:
            name, ok, sec, text = fut.result()
            if text:
                print(text)
            results.append(("wed", name, ok, sec))
    return results


def run_xunbao(dry_run: bool, tasks: bool = False,
               did_xxl: bool = False, did_luck: bool = False) -> list:
    """环闽寻宝记：投掉现有骰子、开宝箱，中奖写进到账台账。

    `did_xxl` / `did_luck` 表示本轮已经玩过消消乐 / 抽过大转盘：
    寻宝的加骰子任务里有这两项，只有真做过才允许上报换骰子。
    """
    confs = sorted(PLATFORMS["bass"]["dir"].glob("*.json"))
    if not confs:
        print("环闽寻宝：没有福建账号，跳过。")
        return []
    script = BASE / "cmcc_xunbao.py"
    if not script.is_file():
        print("环闽寻宝：缺少 cmcc_xunbao.py。")
        return [("xunbao", "环闽寻宝", False, 0)]

    print(f"\n环闽寻宝记 · {len(confs)} 个福建账号")
    args = []
    if tasks:
        # 只报这一轮真做过的任务；好友助力永远不报
        args.append("--tasks")
        if did_xxl:
            args.append("--did-xxl")
        if did_luck:
            args.append("--did-luck")
    if dry_run:
        args.append("--dry-run")
    results = []
    for index, conf in enumerate(confs, 1):
        # 寻宝是顺手多拿一次，挂了不能把整轮签到判成失败
        try:
            name, ok, sec, text = run_script(
                script, conf, args, 600, idx=index, total=len(confs),
                config_args=["--key", conf.stem],
            )
        except Exception as e:
            print(f"  [xunbao] {conf.stem} 异常：{e}")
            results.append(("xunbao", conf.stem, False, 0.0))
            continue
        print(text)
        results.append(("xunbao", name, ok, sec))
    return results


def run_luck(dry_run: bool, tasks: bool = True) -> list:
    """用福建账号抽奖。

    tasks=True 时先刷「加抽奖次数」的任务（4 个任务实测能换 2 次），
    再把次数抽完；False 就是老行为，只花已有次数。
    """
    confs = sorted(PLATFORMS["bass"]["dir"].glob("*.json"))
    if not confs:
        print("优惠专区抽奖：没有福建账号，跳过。")
        return []
    script = BASE / "cmcc_luck.py"
    if not script.is_file():
        print("优惠专区抽奖：缺少 cmcc_luck.py。")
        return [("luck", "优惠专区", False, 0)]

    print(f"\n优惠专区抽奖 · {len(confs)} 个福建账号")
    args = [] if tasks else ["--no-tasks"]
    if dry_run:
        args.append("--dry-run")
    results = []
    for index, conf in enumerate(confs, 1):
        # 转盘是「顺手多拿一次」，它挂掉不能把整轮签到判成失败
        try:
            name, ok, sec, text = run_script(
                script, conf, args, 600, idx=index, total=len(confs),
                config_args=["--key", conf.stem],
            )
        except Exception as e:
            print(f"  [luck] {conf.stem} 异常：{e}")
            results.append(("luck", conf.stem, False, 0.0))
            continue
        print(text)
        results.append(("luck", name, ok, sec))
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
    ap.add_argument("--luck", action="store_true",
                    help="优惠专区：先做加次数任务，再把转盘次数抽完")
    ap.add_argument("--xunbao", action="store_true", help="环闽寻宝记：投掉现有骰子")
    ap.add_argument("--xunbao-tasks", action="store_true",
                    help="寻宝顺手上报「参与天天大转盘」任务换骰子")
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
    ap.add_argument("--wed", action="store_true",
                    help="额外跑周三充值日拼图任务、抽奖与领券")
    ap.add_argument("--wed-only", action="store_true",
                    help="只跑周三充值日，不跑其它平台")
    ap.add_argument("--games", action="store_true",
                    help="顺带跑拓展活动：周六游戏中心 / 追剧领福利 / 玩游戏抽话费")
    ap.add_argument("--tasks", action="store_true",
                    help="顺带跑签到页 AI豆任务")
    ap.add_argument("--games-only", action="store_true",
                    help="只跑拓展活动三个，不跑其它平台")
    args = ap.parse_args()

    # 让子脚本别自己推「凭证过期」——本轮结束由这里合并成一条
    os.environ["CMCC_NOTIFY_BATCH"] = "1"

    # 额外参数按平台分发：--extra 是给 BASS 的，qwhd 的由模式开关自己决定。
    # 早先两个平台共用一个 extra 字符串、却只透传给 bass，导致 --keepalive
    # 的 --login-only 被丢掉，保活任务变成了每 90 分钟真签到一次。
    extra_map = {"qwhd": [], "bass": args.extra.split() if args.extra else []}
    feed_injected = False   # 这一轮是否按策略注入了 --feed（成功后才记「今天已喂」）
    # 策略读一次就够：bass 段落是喂养/任务这类子项，luck / xunbao 这些是顶层开关。
    # 早先全从 policy["bass"] 里取，顶层开关永远是 None → 一律走默认 True，
    # 结果「设置」里取消勾选不生效。
    policy = load_policy()
    luck_tasks = bool(policy.get("luck_tasks", True))

    # 只领每日奖励：只跑 BASS，且把 --daily-only 透传给 cmcc_fj.py
    if args.daily_only:
        args.platform = "bass"
        args.xxl = False
        extra_map["bass"].append("--daily-only")
        # 自动策略（控制台「自动策略」面板保存一次即可）：这一轮顺带喂养、阶段满了怎么办、
        # 领哪一档奖品。不读策略就等于回到「只领三档、不喂」的老行为。
        pol = (policy.get("bass") or {})
        feed = int(pol.get("feed") or 0)
        feed_all = bool(pol.get("feed_all"))
        if (feed > 0 or feed_all) and feed_due(pol):
            # 喂到用完时轮数由脚本按当前饲料算，这里不传数字
            extra_map["bass"] += ["--feed-all"] if feed_all else ["--feed", str(feed)]
            feed_injected = True
        full = str(pol.get("on_stage_full") or "stop")
        if full == "claim":
            extra_map["bass"].append("--auto-claim")
        elif full == "continue":
            extra_map["bass"].append("--continue-stage")
        prize = str(pol.get("prize") or "fee")
        if prize in ("fee", "flow", "discount"):
            extra_map["bass"] += ["--prize", prize]
        if pol.get("tasks"):
            extra_map["bass"].append("--tasks")
        # 天天大转盘的免费次数当天不用就清零，跟着每日奖励一起抽完。
        # 加次数任务（4 个）由 luck_tasks 控制，默认顺手做掉——实测能从 0 换到 2 次。
        args.luck = args.luck or bool(policy.get("luck", True))
        # 券来世界（App 首页访问活动）：每天一次 500 饲料，先打开页面再上报
        if policy.get("coupon_world", True):
            extra_map["bass"].append("--coupon-world")
        # 寻宝的骰子当天不用就作废，跟着每日奖励一起投掉
        args.xunbao = args.xunbao or bool(policy.get("xunbao", True))
        args.xunbao_tasks = args.xunbao_tasks or bool(policy.get("xunbao_tasks", True))
        if pol.get("fb_tasks"):
            extra_map["bass"].append("--fb-tasks")

    # 只跑秒杀：不签到（资格由秒杀脚本自己补签），也不跑别的平台
    if args.seckill_only:
        args.seckill = True
        args.xxl = False
        args.platform = "none"

    # 只跑周三充值日：拼图抽奖与领券独立于普通签到和每日秒杀
    if args.wed_only:
        args.wed = True
        args.xxl = False
        args.seckill = False
        args.platform = "none"

    # 保活：只登录续 jwt，不签到；失败也不推送（保活失败由每日签到去告警）
    if args.keepalive:
        args.platform = "qwhd"
        args.xxl = False
        args.dry_run = False
        args.no_notify = True
        extra_map["qwhd"].append("--login-only")

    # 拓展活动（周六游戏中心 / 追剧 / 玩游戏抽话费）需要透传 --games，
    # 否则这些代码写好了却永远没人调用。
    if args.games:
        extra_map["qwhd"].append("--games")
    if args.tasks:
        extra_map["qwhd"].append("--tasks")

    # 只跑拓展活动：qwhdhub 侧三个活动，不碰 BASS / 消消乐 / 秒杀
    if args.games_only:
        args.platform = "qwhd"
        args.xxl = False
        args.seckill = False
        args.wed = False
        if "--games" not in extra_map["qwhd"]:
            extra_map["qwhd"].append("--games")

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

    # 喂养成功才记「今天已喂」，失败（多半是票据过期）留到下一次补跑
    if feed_injected and not args.dry_run and any(
            k == "bass" and ok for k, _n, ok, _s in results):
        mark_fed()

    if args.luck:
        results.extend(run_luck(args.dry_run, tasks=luck_tasks))

    if args.xxl:
        results.extend(run_xxl(args.dry_run, args.xxl_times))

    # 寻宝放在转盘 / 消消乐之后：它的加骰子任务里就有「参与天天大转盘」「参与一起消消乐」，
    # 先投骰子再去做任务，等于把本轮赚到的骰子留到明天（骰子当天不用就作废）。
    # 只有这两件事**真的跑成功**才允许上报对应任务，失败时宁可少拿骰子也不空报。
    if args.xunbao:
        did_xxl = any(k == "xxl" and ok for k, _n, ok, _s in results)
        did_luck = any(k == "luck" and ok for k, _n, ok, _s in results)
        results.extend(run_xunbao(args.dry_run, tasks=args.xunbao_tasks,
                                  did_xxl=did_xxl, did_luck=did_luck))

    if args.seckill:
        results.extend(run_seckill(args.dry_run, args.seckill_at))

    if args.wed:
        results.extend(run_wed(args.dry_run))

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

    # 给邮箱监听用的触发标记：只有「票失效了、今天还没全做完」才值得去收 HAR
    if TOKEN_DEAD_ACCOUNTS:
        mark_mail_needed(TOKEN_DEAD_ACCOUNTS)
    elif should_mark_done(args.platform, good, len(results)):
        mark_today_done()

    # 推送通道配置：战报和失败提醒都用它
    cfgs = []
    for key in keys:
        for f in sorted(PLATFORMS[key]["dir"].glob("*.json")):
            cfgs.append(load_json(f, {}))
    ch = collect_notify_settings(*cfgs)

    # 今天是否真的干完活了：本轮成功 / 当天到账 / 当天有非只读的成功执行。
    # 本轮 results 直接传进去——run_all 自己不写 runs.json，不能等 dashboard 记。
    done = today_finished(results=results, dry_run=args.dry_run)

    # 做完就推「今日战报」：当天到手的东西按账号分组。
    # 不再要求「全部成功且无过期」——只要今天真有收获就发总结。
    # notify_once 按天去重，每小时轮询也只会推一条。
    if not args.no_notify and done:
        try:
            from common import daily_report
            title, detail = daily_report()
            notify_once("daily_report", title, detail, ch["bark_url"],
                        ch["serverchan_sendkey"], hours=20,
                        pushplus_token=ch["pushplus_token"])
        except Exception as e:
            print(f"  [战报] 生成失败：{e}")

    # 失败才推送，避免每天正常签到都打扰；同类事当天只推一条
    if not args.no_notify and (good != len(results) or TOKEN_DEAD_ACCOUNTS):

        if TOKEN_DEAD_ACCOUNTS and not done:
            # 票过期且今天啥也没干成 → 提醒重抓。
            # 今天已经做完（有到账/有成功执行）就不再打扰：用户明确说过
            # 「做完了当天 token 过期也别发通知」。
            title, detail = token_dead_notice(TOKEN_DEAD_ACCOUNTS)
            notify_once("token_dead", title, detail, ch["bark_url"],
                        ch["serverchan_sendkey"], hours=12,
                        pushplus_token=ch["pushplus_token"])
        bad = [f"{k}:{n}" for k, n, ok, _ in results if not ok and n not in TOKEN_DEAD_ACCOUNTS]
        if bad:
            notify_once("run_failed", "中国移动签到失败",
                        f"{good}/{len(results)} 成功\n失败账号：{', '.join(bad)}",
                        ch["bark_url"], ch["serverchan_sendkey"], hours=20,
                        pushplus_token=ch["pushplus_token"])

    return 0 if good == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
