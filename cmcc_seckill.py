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

抢到后奖品进 App「我的奖品」核销，脚本会推送到手机。

用法：
  python cmcc_seckill.py --dry-run      只查场次/校时/资格，不抢
  python cmcc_seckill.py --once         立刻打一次 redeem 看响应格式
  python cmcc_seckill.py                等下一场开抢自动抢
  python cmcc_seckill.py --at 11:59:50  手动指定开抢时刻（调试）
"""
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
}

# 会话 cookie 滑动过期，长等时用心跳续住
KEEPALIVE_INTERVAL = 480


def save_session(s, referer: str, cfg: Config = None) -> None:
    """把活动会话 cookie + referer 存下来，之后可脱离 app_token 复用。"""
    save_activity_session(s, referer, cfg)


def load_session(cfg: Config = None):
    """从磁盘恢复已保存的会话；不可用返回 None。"""
    path = activity_session_file(cfg) if cfg else (
        Path(__file__).resolve().parent / "gui_data" / "seckill_session.json")
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
    """探活：纯 cookie 会话是否还能用。"""
    try:
        r = s.post(SECCONFIG_URL, json={}, headers=api_headers(referer), timeout=15)
        return r.json().get("code") == "SUCCESS"
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
                max_attempts: int, deadline_srv_ms: int, offset_ms: float):
    """开抢主循环。被拒后核对 leaveStock，没货立刻停手。"""
    payload = {"secId": str(zone["id"]), "prizeId": str(zone["prize"]["id"])}
    log.info("开始抢购: %s (secId=%s prizeId=%s)",
             zone["prize"]["name"], payload["secId"], payload["prizeId"])

    last_stock_check = time.time()
    for attempt in range(1, max_attempts + 1):
        if (time.time() + offset_ms) * 1000 > deadline_srv_ms:
            return None, "场次窗口已结束"
        try:
            resp = api_post(s, referer, REDEEM_URL, payload)
        except Exception as e:
            log.warning("第%d发请求异常: %s", attempt, e)
            time.sleep(interval)
            continue

        code, status, msg = resp.get("code"), resp.get("status"), resp.get("msg", "")
        log.info("第%d发: code=%s status=%s msg=%s", attempt, code, status, msg)
        if code == "SUCCESS":
            return resp, "SUCCESS"
        if status in STOP_STATUSES:
            return resp, status
        # NOT_TIME_IN（打早了）与其他未知错误：密集重试直到开抢/窗口结束
        now = time.time()
        if now - last_stock_check >= 2.0:
            last_stock_check = now
            stock = query_stock(s, referer, payload["secId"])
            log.info("库存核对: %s", "未知" if stock is None else f"剩 {stock:g}")
            if stock == 0:
                return None, "库存已抢完（leaveStock=0），停手"
        time.sleep(0.05 if status == "NOT_TIME_IN" else interval)

    stock = query_stock(s, referer, payload["secId"])
    if stock == 0:
        return None, "库存已抢完（leaveStock=0）"
    if stock is None:
        return None, f"连续 {max_attempts} 发未中（库存未知）"
    return None, f"连续 {max_attempts} 发未中（剩余库存 {stock:g}）"


def run(cfg: Config, dry_run: bool, once: bool, at: str,
        interval: float, lead: float, max_attempts: int) -> int:
    # ① 优先复用已保存的活动会话（实测：仅 cookie 即可调通接口，不依赖 app_token）
    s = referer = None
    cached = load_session(cfg)
    if cached:
        s, referer = cached
        if session_alive(s, referer):
            log.info("复用已保存的活动会话成功（无需 app_token）")
        else:
            log.info("已保存会话已失效，重新登录")
            s = referer = None

    # ② 没有可用会话 → 用 app_token 重新建立
    if s is None:
        try:
            s, referer = exchange_session(cfg)
        except Exception as e:
            # 会话复用失败且 app_token 也不可用 → 明确提示，别抛栈
            log.error("没有可用会话，且 app_token 已失效：%s", str(e).split("——")[0].strip())
            log.error("请用该账号重新抓一次 HAR 并导入，之后再运行秒杀")
            notify(cfg, "移动秒杀：会话缺失",
                   "既无可复用的活动会话，app_token 也已失效\n"
                   "请用该账号重新抓包导入\n手机尾号 %s" % (cfg.phone[-4:] if cfg.phone else "????"))
            return 1
        save_session(s, referer, cfg)

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
        stock_txt = "未知" if stock is None else (f"剩 {stock:g}" if stock else "已抢完")
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
        notify(cfg, "移动秒杀：今日已抢到", detail)
        return 0

    # 开抢前先看库存：没货就不补签也不打 redeem，白费请求还容易被风控
    if (zone.get("prize") or {}).get("leaveStock") == 0:
        reason = "选定场次库存已为 0（已被抢完），不发起抢购"
        log.warning("秒杀未成功: %s", reason)
        notify(cfg, "移动秒杀未成功",
               f"{reason}\n场次 {st.strftime('%m-%d %H:%M')} {zone['prize']['name']}\n手机尾号 {cfg.phone[-4:]}")
        return 1

    eligible = ensure_eligible(s, referer, cfg)
    log.info("秒杀资格（当日签到）: %s", "已具备" if eligible else "未获取！")

    if dry_run:
        log.info("[dry-run] 到此为止，不执行抢购")
        return 0 if eligible else 1

    if at:
        hh, mm, *ss = at.split(":")
        target = datetime.now(TZ_CN).replace(
            hour=int(hh), minute=int(mm), second=int(ss[0]) if ss else 0, microsecond=0)
        start_srv_ms = int(target.timestamp() * 1000)
        active = False
        log.info("开抢时刻被 --at 覆盖为 %s", target.strftime("%H:%M:%S"))

    if once:
        resp = api_post(s, referer, REDEEM_URL,
                        {"secId": str(zone["id"]), "prizeId": str(zone["prize"]["id"])})
        log.info("单发响应: %s", json.dumps(resp, ensure_ascii=False))
        ok = resp.get("code") == "SUCCESS"
        if ok:
            notify(cfg, "移动秒杀成功", f"{zone['prize']['name']}\n手机尾号 {cfg.phone[-4:]}")
        return 0 if ok else 1

    if not eligible:
        notify(cfg, "移动秒杀资格缺失",
               f"当日未签到且补签失败，无法参与秒杀\n手机尾号 {cfg.phone[-4:]}")
        return 1
    if not active:
        wait_until(start_srv_ms / 1000 - offset_ms / 1000 - lead, s, referer, cfg)

    resp, reason = fire_redeem(s, referer, zone, interval, max_attempts, end_srv_ms, offset_ms)

    if reason == "SUCCESS":
        detail = (f"{zone['prize']['name']}\n场次 {st.strftime('%m-%d %H:%M')}，"
                  f"请去 App「我的奖品」核销\n手机尾号 {cfg.phone[-4:]}")
        log.info("秒杀成功！%s", zone["prize"]["name"])
        notify(cfg, "移动秒杀成功 🎉", detail)
        return 0

    if resp is not None and resp.get("status") == "ZONE_TODAY_COMPLETE":
        detail = (f"{zone['prize']['name']}\n场次 {st.strftime('%m-%d %H:%M')}，"
                  f"今日已完成中奖，无需重复抢购\n手机尾号 {cfg.phone[-4:]}")
        log.info("今日已完成中奖，无需重复抢购")
        notify(cfg, "移动秒杀：今日已抢到", detail)
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
    parser.add_argument("--max-attempts", type=int, default=6, metavar="N", help="最大尝试次数")
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
                   interval=args.interval, lead=args.lead, max_attempts=args.max_attempts)
    except KeyboardInterrupt:
        log.info("手动中断")
        return 1
    except Exception as e:
        log.exception("执行失败: %s", e)
        notify(cfg, "移动秒杀异常", f"{e}\n手机尾号 {cfg.phone[-4:] if cfg.phone else '????'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
