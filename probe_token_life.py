#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app_token 寿命精确探测（分钟级）

为什么需要它
------------
早前用 15 分钟间隔采样，得到的只是「签发后 24 分钟仍可用、84 分钟已失效」这种
宽区间，无法回答「到底活多久」。本脚本把间隔压到 1~2 分钟，并且**支持断点续跑**：
每次运行读回上次的状态，只追加新样本，所以可以交给计划任务每 2 分钟跑一次，
进程不用常驻，结论照样精确到分钟。

判���方式
--------
登录成功（appTokenLogin 返回 SUCCESS）→ 票据有效；
失败 → 票据失效。两者之间取中点，再减去签发时刻，得到票据寿命。

用法
----
    python probe_token_life.py                    # 探测全部 qwhdhub 账号
    python probe_token_life.py --interval 60      # 间隔 60 秒
    python probe_token_life.py --once             # 只探一次就退出（给计划任务用）
    python probe_token_life.py --report           # 只打印当前结论，不发请求

结论输出到 gui_data/token_life/<尾号>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import cmcc_sign as CS
from common import RotatingLog, Tee, app_base, atomic_write_json, setup_utf8

# 冻结成 exe 后 __file__ 指向临时解包目录，必须改用 exe 所在目录，
# 否则探测记录会写进临时目录，exe 一关就丢。
BASE = app_base()

# 输出被重定向到日志文件时，Windows 默认用 GBK，中文会变乱码。
# 必须在任何输出之前调用（logging 初始化也要在它之后）。
setup_utf8()

OUT_DIR = BASE / "gui_data" / "token_life"


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(p: Path, obj: dict) -> None:
    atomic_write_json(p, obj, indent=1)


# 只有服务端明确拒绝票据才算「失效」；网络抖动 / SSL / SSO 入口变更都不算
EXPIRED_MARKS = ("APP登录状态已过期", "appTokenLogin 失败", "登录状态已过期")


def _probe(cfg) -> tuple[str, str]:
    """试一次登录。返回 (kind, 详情)，kind ∈ alive / expired / error。

    早先任何异常都记 False，一次网络超时就把状态打成 expired 并永久停探，
    寿命结论直接失真。这里把「服务端说票据废了」和「这次没探成」分开。
    """
    try:
        CS.exchange_session(cfg)
        return "alive", "登录成功"
    except Exception as e:
        msg = str(e).split("——")[0].strip()
        kind = "expired" if any(k in msg for k in EXPIRED_MARKS) else "error"
        return kind, msg[:160]


def _needs_probe(rec: dict) -> bool:
    """是否还需要发请求。

    已经观测到「有效 → 失效」转折的账号，说明这张票据彻底废了，
    再探也只是刷同一条报错，还会增加被频控的风险。
    这种状态直接跳过，等导入新 HAR（mark_issued 重置状态）后再探。
    """
    if rec.get("state") == "expired":
        return False
    return True


def _accounts() -> list[Path]:
    d = BASE / "configs"
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.json"))


def _conclude(rec: dict) -> str:
    """根据已有样本给出寿命结论。"""
    ok = [s for s in rec.get("samples", []) if s.get("ok")]
    # kind=error 是「这次没探成」（网络等），不能当成首次失效时刻
    bad = [s for s in rec.get("samples", [])
           if not s.get("ok") and s.get("kind") != "error"]
    if not ok:
        return "尚无成功样本"
    last_ok = ok[-1]
    first_bad = bad[0] if bad else None

    issued = rec.get("issued_at")
    head = ""
    if issued:
        try:
            t0 = datetime.fromisoformat(issued)
            d = (datetime.fromisoformat(last_ok["at"]) - t0).total_seconds() / 60
            head = f"距签发 {d:.0f} 分钟仍有效"
        except Exception:
            head = ""

    if not first_bad:
        return f"{head}；尚未观测到失效（已探测 {len(ok)} 次）"

    t_ok = datetime.fromisoformat(last_ok["at"])
    t_bad = datetime.fromisoformat(first_bad["at"])
    span = (t_bad - t_ok).total_seconds() / 60
    life_lo = None
    life_hi = None
    if issued:
        try:
            t0 = datetime.fromisoformat(issued)
            life_lo = (t_ok - t0).total_seconds() / 60
            life_hi = (t_bad - t0).total_seconds() / 60
        except Exception:
            pass

    out = f"{head}；{t_bad.strftime('%H:%M:%S')} 首次失效（采样间隔 {span:.0f} 分钟）"
    if life_lo is not None:
        out += f" → 票据寿命 {life_lo:.0f}~{life_hi:.0f} 分钟"
    return out


def run_once() -> int:
    """探测所有账号一次，追加样本并更新结论。返回 0（至少一个账号有效）。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")
    alive = 0
    total = 0
    skipped = 0

    for cfg_path in _accounts():
        total += 1
        key = cfg_path.stem
        out = OUT_DIR / f"{key}.json"
        rec = _load(out)
        rec.setdefault("samples", [])
        rec.setdefault("phone", "")

        if not _needs_probe(rec):
            skipped += 1
            print(f"[{now}] ...{key}  已确认失效，跳过（等新 HAR 导入后重测）", flush=True)
            continue

        try:
            cfg = CS.Config(cfg_path)
            rec["phone"] = cfg.phone
            kind, detail = _probe(cfg)
        except Exception as e:
            kind, detail = "error", f"配置读取失败: {str(e)[:120]}"

        ok = kind == "alive"
        rec["samples"].append({"at": now, "ok": ok, "kind": kind, "detail": detail})
        # 只保留最近 500 个样本，避免文件无限增长
        rec["samples"] = rec["samples"][-500:]
        rec["updated"] = now

        # 状态机：unknown → alive（探到过成功）→ expired（服务端明确拒绝票据）
        # error 样本不改状态：网络抖动不该让探测永久停手
        if kind == "alive":
            rec["state"] = "alive"
        elif kind == "expired":
            tail = rec["samples"][-3:]
            if rec.get("state") == "alive" or (
                    len(tail) == 3 and all(s.get("kind") == "expired" for s in tail)):
                rec["state"] = "expired"
                rec["expired_at"] = now
            else:
                rec.setdefault("state", "unknown")
        else:
            rec.setdefault("state", "unknown")

        rec["conclusion"] = _conclude(rec)
        _save(out, rec)

        flag = {"alive": "有效", "expired": "失效", "error": "未判定"}[kind]
        print(f"[{now}] ...{key}  app_token {flag}  {detail}", flush=True)
        if ok:
            alive += 1

    print(f"\n存活 {alive}/{total} 个账号" + (f"，跳过 {skipped} 个已确认失效" if skipped else ""))
    return 0 if alive else 1


def report() -> int:
    """打印所有账号的当前结论，不发请求。"""
    if not OUT_DIR.exists():
        print("还没有探测数据")
        return 1
    for p in sorted(OUT_DIR.glob("*.json")):
        rec = _load(p)
        print(f"--- ...{p.stem}（{rec.get('phone', '?')}）")
        print(f"    {rec.get('conclusion', '无结论')}")
        if rec.get("issued_at"):
            print(f"    签发时刻 {rec['issued_at']}")
        s = rec.get("samples", [])
        if s:
            print(f"    最后一次探测 {s[-1]['at']} → {'有效' if s[-1]['ok'] else '失效'}")
    return 0


def _phone_of(cfg_path: Path, rec: dict) -> str:
    """账号手机号：优先探测记录里的，其次配置文件里的（记录可能还没探过）。"""
    ph = str(rec.get("phone") or "")
    if ph:
        return ph
    return str(_load(BASE / "configs" / f"{cfg_path.stem}.json").get("phone") or "")


def mark_issued(issued_at: str | None = None, phone: str | None = None) -> int:
    """把 app_token 签发时刻写入探测记录。

    签发时刻来自 HAR 里 appTokenLogin 请求的时间戳（最接近真实签发点）。
    没有 HAR 时退化为「导入时刻」，这会让寿命算短一点。

    **必须按账号定位**：早先无条件遍历全部 configs 写同一个 stamp，给 3239
    导入 HAR 会清空 4167 的样本、并写上 3239 的签发时间，4167 的寿命结论
    随之失真。传 phone 时只动那一个号；不传则保留旧行为（全部账号）。
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = issued_at or datetime.now().isoformat(timespec="seconds")
    hit = 0
    for cfg_path in _accounts():
        out = OUT_DIR / f"{cfg_path.stem}.json"
        rec = _load(out)
        if phone is not None and _phone_of(cfg_path, rec) != str(phone):
            continue
        rec["issued_at"] = stamp
        # 新票据 = 新的一轮探测：清掉旧样本与旧状态
        rec["samples"] = []
        rec["state"] = "unknown"
        rec.pop("expired_at", None)
        rec["conclusion"] = _conclude(rec)
        _save(out, rec)
        hit += 1
        print(f"...{cfg_path.stem} 签发时刻标记为 {stamp}")
    if phone is not None and not hit:
        print(f"⚠ 未找到手机号 {phone} 对应的账号，签发时刻未写入")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="app_token 寿命精确探测")
    ap.add_argument("--interval", type=int, default=120, help="常驻模式的探测间隔秒数")
    ap.add_argument("--once", action="store_true", help="只探一次就退出")
    ap.add_argument("--report", action="store_true", help="只打印结论，不发请求")
    ap.add_argument("--mark-issued", metavar="ISO时间", nargs="?", const="",
                    help="标记 app_token 签发时刻（不传则用当前时间）")
    ap.add_argument("--log", default="", help="同时写一份按天轮转的日志")
    args = ap.parse_args()

    log = RotatingLog(args.log)
    old_stdout = sys.stdout
    if log.enabled:
        sys.stdout = Tee(log)
    try:
        if args.report:
            return report()
        if args.mark_issued is not None:
            return mark_issued(args.mark_issued or None)

        if args.once:
            return run_once()

        # 常驻模式：适合手动跑着观察
        try:
            while True:
                run_once()
                print(f"--- 等待 {args.interval} 秒后下一轮 ---", flush=True)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n探测结束")
            return 0
    finally:
        if log.enabled:
            sys.stdout.flush()
            sys.stdout = old_stdout


if __name__ == "__main__":
    sys.exit(main())
