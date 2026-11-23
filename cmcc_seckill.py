#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中国移动「签到有礼 · 每周秒杀」自动抢券

活动：签到页面（活动号 1021122301）里的「每周秒杀」，每天 12:00 开抢，
奖品 1 元话费券，限抢 1 次。

接口链路（2026-11-20 抓包实测）：
  1. exchange_session()                    SSO 换活动会话 cookie
  2. POST /markSeckill/sysTime             服务器毫秒时间，用来校准本机时钟
  3. POST /markSeckill/secConfig           秒杀场次（secId / prizeId / 开抢时间 / 库存）
  4. POST /markSeckill/todayMarkStatus     当日签到状态 = 参与资格
  5. POST /markSeckill/redeem              真正抢购，参数 {"secId","prizeId"}

redeem 响应判定：
  code=SUCCESS              抢到
  status=NOT_TIME_IN        未开抢（继续密集重试）
  status=PRIZE_NO_STOCK     券抢完（停手）
  status=ZONE_TODAY_COMPLETE 今日已中过（停手）
  status=PRIZE_LIMIT_*      限次（停手）
  status=WORK_ORDER_RESTRIC_LIMIT  风控（停手）
  status=TRAFFIC_LIMITED    参与人数太多（**瞬时限流，不消耗库存，继续重试**）

抢到后奖品进 App「我的奖品」核销，脚本会推送到手机。

用法：
  python cmcc_seckill.py --dry-run      只查场次/校时/资格，不抢
  python cmcc_seckill.py --once         立刻打一次 redeem 看响应格式
  python cmcc_seckill.py                等下一场开抢自动抢
  python cmcc_seckill.py --at 11:59:50  手动指定开抢时刻（调试）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cmcc_sign import (
    API_MARK,
    Config,
    activity_session_file,
    api_headers,
    do_mark,
    exchange_session,
    notify,
    save_activity_session,
)
from common import app_base, emit_result, record_award

log = logging.getLogger("cmcc_seckill")

TZ_CN = timezone(timedelta(hours=8))

SYSTIME_URL = f"{API_MARK}/markSeckill/sysTime"
SECCONFIG_URL = f"{API_MARK}/markSeckill/secConfig"
TODAY_MARK_URL = f"{API_MARK}/markSeckill/todayMarkStatus"
REDEEM_URL = f"{API_MARK}/markSeckill/redeem"

# 触达即终态的 redeem status：不再浪费请求
STOP_STATUSES = {
    "PRIZE_NO_STOCK": "券已抢完",
    "ZONE_TODAY_COMPLETE": "今日已完成中奖，无需再抢",
    "PRIZE_LIMIT_DAY": "当日中奖次数已用完",
    "PRIZE_LIMIT_MONTH": "当月中奖次数已用完",
    "PRIZE_RESTRIC_LIMIT": "活动期间中奖次数已达上限",
    "WORK_ORDER_RESTRIC_LIMIT": "工单限流（风控）",
    "PARAM_ERROR": "参数异常",
    # 无效的 secId/prizeId 会返回这个（实测 secId=999 → NET_ERROR），
    # 属于配置错误，重试无意义，直接停手
    "NET_ERROR": "场次或奖品参数无效",
}

# 注意：TRAFFIC_LIMITED（"参与人数太多"）**故意不放在 STOP_STATUSES 里**。
# 实测（2026-11-20 12:03 真实开抢）它只是瞬时限流，**不消耗库存**——
# 同一时段库存仍从 0.165 继续掉到 0，是被别人抢走的，不是被这次请求打掉的。
# 所以它应当落进「已开抢」的常规重试分支继续打，而不是当作终态停手。

# 会话 cookie 固定 2 小时硬过期，心跳只用于尽早发现失效
KEEPALIVE_INTERVAL = 480
# 启动时没有会话不直接退出：临近开抢持续重试，给用户重新导入 HAR 的时间
SESSION_RETRY_INTERVAL = 15
SESSION_GRACE_AFTER_FIRE = 60
DEFAULT_SESSION_WAIT = 300


def parse_fire_at(at: str, now: datetime | None = None) -> float:
    """把 --at 的北京时间转成本地时间戳；日期取今天。"""
    hh, mm, *ss = at.split(":")
    target = (now or datetime.now(TZ_CN)).replace(
        hour=int(hh), minute=int(mm), second=int(ss[0]) if ss else 0,
        microsecond=0)
    return target.timestamp()


def session_deadline_for(at: str, now: float | None = None) -> float:
    """会话重试的截止时刻：--at 出手时刻 + 宽限；未指定 --at 则等 5 分钟。"""
    now = time.time() if now is None else now
    if at:
        return parse_fire_at(at) + SESSION_GRACE_AFTER_FIRE
    return now + DEFAULT_SESSION_WAIT


def reload_config(cfg: Config) -> Config:
    """每次重试前重读配置，等待期间导入的新 HAR 才能被当前进程看到。"""
    path = getattr(cfg, "config_path", "")
    return Config(path) if path else cfg


def obtain_session(cfg: Config):
    """优先复用有效活动会话，否则用当前 app_token 建立并保存。"""
    cached = load_session(cfg)
    if cached:
        s, referer = cached
        if session_alive(s, referer):
            log.info("复用已保存的活动会话成功（无需 app_token）")
            return s, referer
        log.info("已保存会话已失效，尝试用 app_token 重新登录")
    s, referer = exchange_session(cfg)
    save_session(s, referer, cfg)
    return s, referer


def acquire_session_with_retry(cfg: Config, deadline: float,
                               retry_interval: float = SESSION_RETRY_INTERVAL,
                               now_fn=None, sleep_fn=None):
    """在 deadline 前反复尝试会话；每轮重读配置，成功返回最新配置。

    返回 (session, referer, latest_cfg)。超时抛 RuntimeError，由统一入口告警。
    """
    now_fn = now_fn or time.time
    sleep_fn = sleep_fn or time.sleep
    last_error = "未知错误"
    attempt = 0
    while True:
        attempt += 1
        cfg = reload_config(cfg)
        try:
            s, referer = obtain_session(cfg)
            if attempt > 1:
                log.info("第 %d 次尝试后会话就绪", attempt)
            return s, referer, cfg
        except Exception as e:
            last_error = str(e).split("——")[0].strip()
        remain = deadline - now_fn()
        if remain <= 0:
            raise RuntimeError(f"直到开抢仍未取得可用会话；最后错误：{last_error}")
        pause = min(retry_interval, remain)
        log.info("会话未就绪：%s；%.0f 秒后重试（每次都会重读 HAR 导入的配置）",
                 last_error, pause)
        sleep_fn(pause)


def save_session(s, referer: str, cfg: Config = None) -> None:
    """把活动会话 cookie + referer 存下来，之后可脱离 app_token 复用。"""
    save_activity_session(s, referer, cfg)


def record_seckill_prize(resp: dict, cfg: Config, zone: dict) -> str:
    """把抢到的奖品记进「收获」台账。

    1 元话费券是**无门槛、可直接兑换**的（不是「充 X 元才能用」），
    所以按 common.classify_award 的口径它属于 kind=fee / need_recharge=false，
    和「天天大转盘」的话费券一样进控制台的到手收益统计。

    没有这一步的话，秒杀抢到的券在控制台上完全看不见——
    「收获」页只统计 awards.json 里的流水。
    """
    data = resp.get("data") or {}
    name = str(data.get("prizeName") or (zone.get("prize") or {}).get("name") or "").strip()
    if not name:
        return ""
    try:
        record_award(getattr(cfg, "phone", "") or "", "每周秒杀", name)
    except Exception as e:
        log.warning("登记奖品流水失败: %s", e)
    log.info("已记入收获：%s", name)
    return name


def load_session(cfg: Config = None):
    """从磁盘恢复已保存的会话；不可用返回 None。"""
    path = activity_session_file(cfg) if cfg else (
        app_base() / "gui_data" / "seckill_session.json")
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        ck, ref = d.get("cookie") or {}, d.get("referer") or ""
        if not ck or not ref:
            return None
        import requests as _rq
        from cmcc_sign import USER_AGENT, LegacyTLSAdapter
        s = _rq.Session()
        s.trust_env = False
        s.mount("https://", LegacyTLSAdapter())
        s.headers.update({"user-agent": USER_AGENT, "accept-language": "zh-CN,zh-Hans;q=0.9"})
        for k, v in ck.items():
            s.cookies.set(k, v, domain="wx.10086.cn", path="/")
        return s, ref
    except Exception as e:
        log.warning("读取已保存会话失败: %s", e)
        return None


def session_alive(s, referer: str) -> bool:
    """探活：纯 cookie 会话是否还能用。

    实测服务端在会话满 2 小时后返回 `GOTO_LOGIN`（不是 401），
    所以两种码都算失效。
    """
    try:
        r = s.post(SECCONFIG_URL, json={}, headers=api_headers(referer), timeout=15)
        j = r.json()
        code = str(j.get("code"))
        if code in ("GOTO_LOGIN", "401"):
            log.info("会话已失效（服务端返回 %s）", code)
            return False
        return code == "SUCCESS"
    except Exception:
        return False


def api_post(s, referer: str, url: str, payload: dict | None = None) -> dict:
    r = s.post(url, json=payload if payload is not None else {},
               headers=api_headers(referer), timeout=10)
    r.raise_for_status()
    return r.json()


def server_now_ms(s, referer: str) -> int:
    """服务器当前毫秒时间。响应 {"data":{"sysTime":...,"from":"qwhdhub"}}。"""
    try:
        resp = api_post(s, referer, SYSTIME_URL)
        ms = (resp.get("data") or {}).get("sysTime")
        if isinstance(ms, int):
            return ms
        log.warning("sysTime 响应格式变化: %s", json.dumps(resp, ensure_ascii=False)[:200])
    except Exception as e:
        log.warning("sysTime 请求失败: %s", e)
    from email.utils import parsedate_to_datetime
    try:
        r = s.post(SYSTIME_URL, json={}, headers=api_headers(referer), timeout=10)
        date = r.headers.get("Date")
        if date:
            return int(parsedate_to_datetime(date).timestamp() * 1000)
    except Exception as e:
        log.warning("Date 响应头兜底失败: %s", e)
    raise RuntimeError("无法获取服务器时间（sysTime 与 Date 响应头均不可用）")


def srv_now_ms(offset_ms: float, local_ms: float | None = None) -> float:
    """本机毫秒 + 偏移 = 服务器毫秒。

    offset_ms 本身就是毫秒，**不能再乘 1000**（曾因此偏了约 1000 秒）。
    抽成纯函数是为了能被测试直接覆盖，而不是在测试里重算一遍公式。
    """
    return (time.time() * 1000 if local_ms is None else local_ms) + offset_ms


def srv_ms_to_local_s(srv_ms: float, offset_ms: float) -> float:
    """服务器毫秒 → 本机时钟秒（wait_until 用本机时钟睡）。"""
    return (srv_ms - offset_ms) / 1000.0


def sec_config_zones(s, referer: str) -> tuple:
    """拉秒杀场次。风控时 data 会退化成字符串，这里只判类型不硬解。"""
    resp = api_post(s, referer, SECCONFIG_URL)
    data = resp.get("data")
    skd = data.get("secKillData") if isinstance(data, dict) else None
    zones = skd.get("secKillZones") if isinstance(skd, dict) else None
    return (zones if isinstance(zones, list) else None), resp


def query_stock(s, referer: str, sec_id: str):
    """指定场次当前剩余库存；查询失败一律返回 None（未知）。"""
    try:
        zones, _ = sec_config_zones(s, referer)
    except Exception as e:
        log.warning("库存查询失败: %s", e)
        return None
    for z in zones or []:
        if str(z.get("id")) == str(sec_id):
            try:
                return float((z.get("prize") or {}).get("leaveStock"))
            except (TypeError, ValueError):
                return None
    return None


def pick_zone(zones: list, now_ms: int):
    """选出进行中或下一场即将开始的场次，返回 (zone, 是否已开抢)。"""
    for z in zones:
        if int(z["startTime"]) <= now_ms <= int(z["endTime"]):
            return z, True
    upcoming = sorted((z for z in zones if int(z["startTime"]) > now_ms),
                      key=lambda z: int(z["startTime"]))
    return (upcoming[0] if upcoming else None), False


def ensure_eligible(s, referer: str, cfg: Config) -> bool:
    """秒杀资格 = 当日已签到。未签则先补签。"""
    resp = api_post(s, referer, TODAY_MARK_URL)
    data = resp.get("data")
    if isinstance(data, dict) and data.get("markStatus") == "marked":
        return True
    log.info("今日未签到，先补签获取秒杀资格...")
    try:
        result = do_mark(s, referer, datetime.now(TZ_CN).strftime("%Y%m%d"))
        log.info("补签响应: code=%s status=%s msg=%s",
                 result.get("code"), result.get("status"), result.get("msg", ""))
    except Exception as e:
        log.warning("补签请求失败: %s", e)
    resp = api_post(s, referer, TODAY_MARK_URL)
    data = resp.get("data")
    return isinstance(data, dict) and data.get("markStatus") == "marked"


def wait_until(target: float, s, referer: str, cfg: Config = None):
    """睡到 target（本地时钟秒）。超过一个心跳周期就发请求续会话。"""
    while True:
        remain = target - time.time()
        if remain <= 0:
            return
        time.sleep(min(remain, KEEPALIVE_INTERVAL))
        if target - time.time() > 60:
            try:
                api_post(s, referer, TODAY_MARK_URL)
                # 服务端可能滑动 cookie，每次心跳后回写保存
                save_session(s, referer, cfg)
                log.info("会话心跳 ok，距开抢还有 %.1f 分钟", (target - time.time()) / 60)
            except Exception as e:
                log.warning("会话心跳失败: %s", e)


def fire_redeem(s, referer: str, zone: dict, interval: float,
                max_attempts: int, deadline_srv_ms: int, offset_ms: float,
                start_srv_ms: int = 0, pre_window_s: float = 60.0):
    """开抢主循环。

    分两个阶段：
    - **未开抢**（服务器时间还没到 start_srv_ms）：0.05 秒密集重试，
      但只在 start_srv_ms 前 pre_window_s 秒的窗口内重试。
      窗口太早（比如 --at 设成凌晨而场次在中午）会打几千次请求触发风控，
      所以超窗就放弃并如实报告。
    - **已开抢**：受 max_attempts 硬上限约束，避免狂打触发风控。
      被拒后每 2 秒核对一次 leaveStock 只作观测，**不作为停手理由**。

    ⚠️ leaveStock 归零不代表售罄（2026-11-21 12:36 实测：leaveStock=0.0 时
    redeem 仍返回 SUCCESS 并真拿到券）。所以本循环不会因为库存为 0 而放弃，
    只有拿到明确的终态码（SUCCESS / PRIZE_NO_STOCK / ZONE_TODAY_COMPLETE 等）
    或打满 max_attempts 才收手。

    阶段判定用**服务器时间**而非响应码——响应码可能是别的原因导致的失败。
    """
    payload = {"secId": str(zone["id"]), "prizeId": str(zone["prize"]["id"])}
    log.info("开始抢购: %s (secId=%s prizeId=%s)",
             zone["prize"]["name"], payload["secId"], payload["prizeId"])

    last_stock_check = time.time()
    started = False          # 是否已进入开抢阶段
    attempt = 0
    while True:
        now_srv_ms = srv_now_ms(offset_ms)
        if now_srv_ms > deadline_srv_ms:
            return None, "场次窗口已结束"
        if start_srv_ms and now_srv_ms >= start_srv_ms:
            started = True
        attempt += 1
        try:
            resp = api_post(s, referer, REDEEM_URL, payload)
        except Exception as e:
            log.warning("第%d发请求异常: %s", attempt, e)
            time.sleep(interval)
            continue

        code, status, msg = resp.get("code"), resp.get("status"), resp.get("msg", "")
        if started:
            log.info("第%d发: code=%s status=%s msg=%s", attempt, code, status, msg)
        else:
            log.info("第%d发（未开抢）: status=%s msg=%s", attempt, status, msg)
        if code == "SUCCESS":
            return resp, "SUCCESS"
        if status in STOP_STATUSES:
            return resp, status

        # 未开抢 → 继续密集重试，但不得超出提前窗口
        if not started:
            if start_srv_ms and (start_srv_ms - now_srv_ms) / 1000.0 > pre_window_s:
                return None, (f"距开抢还有 {(start_srv_ms - now_srv_ms) / 1000.0:.0f} 秒，"
                              f"超出提前窗口 {pre_window_s:.0f} 秒，未出手")
            time.sleep(0.05)
            continue

        # 已开抢但没抢到 → 受 max_attempts 约束
        if attempt > max_attempts:
            break

        now = time.time()
        if now - last_stock_check >= 2.0:
            last_stock_check = now
            stock = query_stock(s, referer, payload["secId"])
            log.info("库存核对: %s", "未知" if stock is None else f"剩 {stock:g}")
            # ⚠️ leaveStock 归零**不能**作为停手理由：2026-11-21 12:36 实测
            # leaveStock=0.0 时 redeem 仍返回 SUCCESS。留着这个判断只会白丢券。
        time.sleep(interval)

    stock = query_stock(s, referer, payload["secId"])
    if stock is None:
        return None, f"连续 {max_attempts} 发未中（库存未知）"
    return None, f"连续 {max_attempts} 发未中（剩余库存 {stock:g}）"


def weekly_limit_reached(response):
    if not response or response.get("status") != "WORK_ORDER_RESTRIC_LIMIT":
        return False
    message = str(response.get("msg") or "")
    return "已领取奖励" in message and "限领一次" in message


def report_weekly_limit():
    log.info("本周已领，达到每周限领一次")
    emit_result({"plat": "seckill", "token": "ok", "success": False,
                 "status": "weekly_limit", "flow_mb": 0.0})
    return 0


def run(cfg: Config, dry_run: bool, once: bool, at: str,
        interval: float, lead: float, max_attempts: int,
        pre_window_s: float = 60.0) -> int:
    # ① 启动时拿不到会话不再退出。计划任务只需提前几分钟启动，
    #    在等待期间导入新 HAR 也会被下一轮重试读到。
    at_target_s = parse_fire_at(at) if at else None
    session_deadline = session_deadline_for(at)
    if dry_run or once:
        session_deadline = time.time()
    try:
        s, referer, cfg = acquire_session_with_retry(cfg, session_deadline)
    except Exception as e:
        log.error("没有可用会话：%s", e)
        log.error("请在开抢前重新抓包导入；脚本运行期间导入也会被自动识别。")
        if not dry_run:
            notify(cfg, "移动秒杀：会话缺失",
                   f"{e}\n请在开抢前重新抓包导入\n"
                   "手机尾号 %s" % (cfg.phone[-4:] if cfg.phone else "????"))
        return 1

    srv_ms = server_now_ms(s, referer)
    offset_ms = srv_ms - time.time() * 1000
    log.info("服务器时间 %s，本机时钟偏移 %+.0f ms",
             datetime.fromtimestamp(srv_ms / 1000, TZ_CN).strftime("%m-%d %H:%M:%S.%f")[:-3],
             offset_ms)

    zones, cfg_resp = sec_config_zones(s, referer)
    if not zones:
        raise RuntimeError("secConfig 未返回场次或响应异常: "
                           + json.dumps(cfg_resp, ensure_ascii=False)[:200])
    log.info("共 %d 个场次:", len(zones))
    for z in zones:
        st = datetime.fromtimestamp(int(z["startTime"]) / 1000, TZ_CN)
        et = datetime.fromtimestamp(int(z["endTime"]) / 1000, TZ_CN)
        prize = z.get("prize") or {}
        stock = prize.get("leaveStock")
        # leaveStock 归零不等于售罄（2026-11-21 实测），所以文案别写「已抢完」
        stock_txt = "未知" if stock is None else f"剩 {stock:g}"
        log.info("  场次%s %s ~ %s  %s (prizeId=%s, 库存%s)",
                 z["id"], st.strftime("%m-%d %H:%M:%S"), et.strftime("%H:%M:%S"),
                 prize.get("name"), prize.get("id"), stock_txt)

    zone, active = pick_zone(zones, srv_ms)
    if zone is None:
        raise RuntimeError("没有可参与的场次（全部已结束）")
    start_srv_ms, end_srv_ms = int(zone["startTime"]), int(zone["endTime"])
    st = datetime.fromtimestamp(start_srv_ms / 1000, TZ_CN)
    log.info("选定场次%s：%s 开抢（%s）", zone["id"], st.strftime("%m-%d %H:%M:%S"),
             "进行中" if active else f"距开始 {(start_srv_ms - srv_ms) / 1000:.0f} 秒")

    # 已抢到优先于库存判断：prize.status=1 表示本账号本场已中奖
    if str((zone.get("prize") or {}).get("status")) == "1":
        detail = (f"{zone['prize']['name']}\n场次 {st.strftime('%m-%d %H:%M')}，"
                  f"今日已抢到，无需重复抢购\n手机尾号 {cfg.phone[-4:]}")
        log.info("本场已抢到（prize.status=1），无需再抢")
        if not dry_run:
            notify(cfg, "移动秒杀：今日已抢到", detail)
        # 已经抢到过了，也要把这张券补记进「收获」台账
        record_seckill_prize({"data": {"prizeName": zone["prize"]["name"]}}, cfg, zone)
        emit_result({"plat": "seckill", "token": "ok", "success": True,
                     "last_reward": zone["prize"]["name"], "flow_mb": 0.0})
        return 0

    # 开抢前先看库存：没货就不补签也不打 redeem，白费请求还容易被风控
    #
    # ⚠️ 实测修正（2026-11-21 12:36）：**leaveStock==0 不等于「券已被抢完」**。
    # 当时 4167 的 leaveStock 已经是 0.0，redeem 依然返回 SUCCESS 并真拿到券。
    # leaveStock 更像是「服务端当轮剩余概率/权重」，会归零但**不代表本号抢不到**。
    # 真正判断「本号已中过」的是 prize.status == "1"（上面已判）。
    # 所以这里绝不能因为 leaveStock 为 0 就放弃——那会让明天直接白丢一张券。
    if (zone.get("prize") or {}).get("leaveStock") == 0:
        log.info("leaveStock 为 0，但这不等于售罄（2026-11-21 实测 leaveStock=0 仍能中奖），继续出手")

    eligible = ensure_eligible(s, referer, cfg)
    log.info("秒杀资格（当日签到）: %s", "已具备" if eligible else "未获取！")

    if dry_run:
        log.info("[dry-run] 到此为止，不执行抢购")
        return 0 if eligible else 1

    # 场次真实开始时间（服务器时钟）——用于判断是否已进入开抢阶段
    zone_start_srv_ms = int(zone["startTime"])
    # 默认在场次开始时出手（服务器时间换算到本机）
    fire_at_local_s = srv_ms_to_local_s(zone_start_srv_ms, offset_ms)

    if at:
        # --at 指定「何时开始出手」，通常给提前量（如 11:59:50）
        fire_at_local_s = at_target_s
        target = datetime.fromtimestamp(at_target_s, TZ_CN)
        active = False
        log.info("开抢出手时刻被 --at 覆盖为 %s（场次本身 %s 开始）",
                 target.strftime("%H:%M:%S"), st.strftime("%H:%M:%S"))

    if once:
        resp = api_post(s, referer, REDEEM_URL,
                        {"secId": str(zone["id"]), "prizeId": str(zone["prize"]["id"])})
        log.info("单发响应: %s", json.dumps(resp, ensure_ascii=False))
        if weekly_limit_reached(resp):
            return report_weekly_limit()
        ok = resp.get("code") == "SUCCESS"
        if ok:
            name = record_seckill_prize(resp, cfg, zone)
            notify(cfg, "移动秒杀成功", f"{name or zone['prize']['name']}\n手机尾号 {cfg.phone[-4:]}")
            emit_result({"plat": "seckill", "token": "ok", "success": True,
                         "last_reward": name or zone["prize"]["name"], "flow_mb": 0.0})
        return 0 if ok else 1

    if not eligible:
        notify(cfg, "移动秒杀资格缺失",
               f"当日未签到且补签失败，无法参与秒杀\n手机尾号 {cfg.phone[-4:]}")
        return 1
    if not active:
        # 等到出手时刻（fire_at_local_s 已是本机时钟，再减 lead 抵消网络延迟）
        wait_until(fire_at_local_s - lead, s, referer, cfg)

    resp, reason = fire_redeem(s, referer, zone, interval, max_attempts,
                               end_srv_ms, offset_ms, zone_start_srv_ms, pre_window_s)

    if reason == "SUCCESS":
        detail = (f"{zone['prize']['name']}\n场次 {st.strftime('%m-%d %H:%M')}，"
                  f"请去 App「我的奖品」核销\n手机尾号 {cfg.phone[-4:]}")
        log.info("秒杀成功！%s", zone["prize"]["name"])
        record_seckill_prize(resp or {}, cfg, zone)
        notify(cfg, "移动秒杀成功 🎉", detail)
        emit_result({"plat": "seckill", "token": "ok", "success": True,
                     "last_reward": zone["prize"]["name"], "flow_mb": 0.0})
        return 0

    if weekly_limit_reached(resp):
        return report_weekly_limit()

    if resp is not None and resp.get("status") == "ZONE_TODAY_COMPLETE":
        detail = (f"{zone['prize']['name']}\n场次 {st.strftime('%m-%d %H:%M')}，"
                  f"今日已完成中奖，无需重复抢购\n手机尾号 {cfg.phone[-4:]}")
        log.info("今日已完成中奖，无需重复抢购")
        notify(cfg, "移动秒杀：今日已抢到", detail)
        # 之前已经抢到过（本次没参与），也该在收获里看到这张券
        record_seckill_prize(resp or {}, cfg, zone)
        emit_result({"plat": "seckill", "token": "ok", "success": True,
                     "last_reward": zone["prize"]["name"], "flow_mb": 0.0})
        return 0

    detail = f"{reason}\n场次 {st.strftime('%m-%d %H:%M')} {zone['prize']['name']}\n手机尾号 {cfg.phone[-4:]}"
    log.warning("秒杀未成功: %s", reason)
    notify(cfg, "移动秒杀未成功", detail)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="中国移动「签到有礼」每周秒杀自动抢券")
    parser.add_argument("--config", default="config.json", help="配置文件路径（与 cmcc_sign.py 共用）")
    parser.add_argument("--dry-run", action="store_true", help="只查场次/校时/资格，不抢")
    parser.add_argument("--once", action="store_true", help="立即打一次 redeem 后退出")
    parser.add_argument("--at", metavar="HH:MM[:SS]", help="手动指定开抢时刻（北京时间）")
    parser.add_argument("--interval", type=float, default=0.35, metavar="SEC", help="重试间隔秒")
    parser.add_argument("--lead", type=float, default=0.4, metavar="SEC", help="提前开火秒数，抵消网络延迟")
    parser.add_argument("--max-attempts", type=int, default=6, metavar="N",
                        help="开抢后最大尝试次数（默认 6）")
    parser.add_argument("--pre-window", type=float, default=60.0, metavar="SEC",
                        help="未开抢阶段的提前窗口秒数，超出就不出手（默认 60）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = Config(args.config)
    if err := cfg.validate():
        log.error("%s\n参考 config.example.json 填写", err)
        return 1

    try:
        return run(cfg, dry_run=args.dry_run, once=args.once, at=args.at,
                   interval=args.interval, lead=args.lead,
                   max_attempts=args.max_attempts, pre_window_s=args.pre_window)
    except KeyboardInterrupt:
        log.info("手动中断")
        return 1
    except Exception as e:
        log.exception("执行失败: %s", e)
        if not args.dry_run:
            notify(cfg, "移动秒杀异常", f"{e}\n手机尾号 {cfg.phone[-4:] if cfg.phone else '????'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
