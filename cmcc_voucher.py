#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中国移动 App「领券中心」每日自动领券。

链路（全部在 touch.10086.cn，封包密钥固定写在 App 的 appsso2 JS 里）：
  1. POST /i/v1/cust/initMessage          用 App 票据里的 UID 换 jsessionid-cmcc 会话
  2. POST /i/v1/cust/couponQry/<phone>    拉取可领券池
  3. POST /i/v1/cust/couponReceive/...    按 batchID 领券

请求/响应的 inParam、msgId、outParam 都是
「双层 base64 → AES-CBC(key=iv=043AOQGK6ykklyZA, Pkcs7)」，
initMessage 的 data 是单层 base64。msgId 为 13 位毫秒时间戳 + 任意 12 位后缀。

UID 优先取配置里的 voucher_uid（HAR 导入写入），否则从 app_token 里抠。
app_token 的 UID 寿命很短（约 30 分钟~11 小时），过期后需重新抓包导入。

用法：
  python cmcc_voucher.py --config configs/4167.json           # 只查询
  python cmcc_voucher.py --config configs/4167.json --go      # 实际领券
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import secrets
import sys
import time
from datetime import datetime

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from cmcc_sign import Config, notify
from common import record_award

log = logging.getLogger("cmcc_voucher")

# 来自 appsso2_bde0386.js：g.filter(i%2==1).join("") = 043AOQGK6ykklyZA
PACKING_KEY = b"043AOQGK6ykklyZA"
SALT = "B3A128FA"
HOST = "touch.10086.cn"
BASE = f"https://{HOST}"

# 服务端校验 App 标记，普通浏览器 UA 会被拒
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148/wkwebview "
    "leadeon/12.5.7/CMCCIT"
)
HDR_JSON = {
    "accept": "*/*",
    "content-type": "application/json; charset=UTF-8",
    "origin": BASE,
    "user-agent": UA,
}
HDR_INIT = {**HDR_JSON, "referer": BASE + "/i/mobile/mycoupons.html"}
HDR_API = {
    **HDR_JSON,
    "accept": "application/json, text/javascript, */*; q=0.01",
    "referer": BASE + "/i/mobile/vouchercent.html",
    "x-requested-with": "XMLHttpRequest",
}

# initMessage 的 deviceInfo，来自抓包；服务端只做基本校验，账号间可通用
DEVICE_INFO = {
    "sb": "apple", "sn": "iPhone11,6", "osType": "ios",
    "packageName": "cn.10086.app", "imei": "A1C862F4CC5F41BCA26D692E5218C1D",
    "loginProvince": "591", "loginCity": "0596",
}


# ---------------------------------------------------------------- 封包

def _aes_encode(raw: bytes, *, double: bool) -> str:
    ct = AES.new(PACKING_KEY, AES.MODE_CBC, PACKING_KEY).encrypt(pad(raw, 16))
    b = base64.b64encode(ct)
    return base64.b64encode(b).decode() if double else b.decode()


def _aes_decode(value: str, *, double: bool) -> str:
    raw = base64.b64decode("".join(value.split()))
    if double:
        raw = base64.b64decode(b"".join(raw.split()))
    return unpad(AES.new(PACKING_KEY, AES.MODE_CBC, PACKING_KEY).decrypt(raw), 16).decode()


def encode_packet(obj) -> str:
    """obj 为 dict 时先序列化为紧凑 JSON；str 时原样加密。"""
    raw = obj if isinstance(obj, str) else json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"))
    return _aes_encode(raw.encode(), double=True)


def decode_packet(value: str) -> str:
    return _aes_decode(value, double=True)


def uid_from_token(token: str) -> str:
    for part in token.split(";"):
        key, _, value = part.strip().partition("=")
        if key.upper() == "UID":
            return value
    return ""


def build_sign(uid: str) -> str:
    """appsso2.js: HMAC-MD5(key="", SALT+UID+SALT)。"""
    return hmac.new(b"", f"{SALT}{uid}{SALT}".encode(), hashlib.md5).hexdigest()


def voucher_uid(cfg: Config) -> str:
    """HAR 导入写入的 UID 优先，否则退回 app_token。"""
    uid = getattr(cfg, "voucher_uid", "") or uid_from_token(cfg.app_token)
    return str(uid).strip()


# ---------------------------------------------------------------- 接口

def _post(session: requests.Session, url: str, payload: dict, headers: dict) -> dict:
    response = session.post(url, json=payload, headers=headers,
                            timeout=25, allow_redirects=False)
    response.raise_for_status()
    return response.json()


def open_session(cfg: Config, uid: str) -> requests.Session:
    """用 UID 换 jsessionid-cmcc，返回带会话 cookie 的 session。"""
    device = dict(DEVICE_INFO)
    device["loginProvince"] = cfg.province_code or device["loginProvince"]
    device["loginCity"] = cfg.city_code or device["loginCity"]
    data = _aes_encode(json.dumps(
        {"UID": uid, "sign": build_sign(uid), "deviceInfo": device},
        ensure_ascii=False, separators=(",", ":")).encode(), double=False)
    session = requests.Session()
    session.trust_env = False
    result = _post(session, f"{BASE}/i/v1/cust/initMessage?{int(time.time() * 1000)}",
                   {"data": data, "channel": "12"}, HDR_INIT)
    if str(result.get("retCode")) != "000000":
        raise RuntimeError(
            f"initMessage 失败: {result.get('retCode')} {result.get('retMsg')}"
            "（凭证可能已过期，请重新抓包导入）")
    if "jsessionid-cmcc" not in session.cookies:
        raise RuntimeError("initMessage 未下发 jsessionid-cmcc")
    return session


def _msg_id() -> str:
    # 后 12 位服务端不校验，随机生成即可
    return encode_packet(f"{int(time.time() * 1000)}{secrets.token_hex(6)}")


def _path_segment(phone: str) -> str:
    """URL 末段就是手机号用同一密钥封包后的结果。"""
    return encode_packet(phone)


def coupon_query(session: requests.Session, cfg: Config) -> list[dict]:
    """拉可领券池，返回扁平化的批次信息。"""
    inparam = encode_packet({
        "busiType": "jx", "areaCode": cfg.province_code, "channelId": "11",
        "loginNo": cfg.phone, "loginType": "0", "isQuality": "0",
        "obtainBusi": "lqzx", "recommendChannel": "11",
    })
    result = _post(session, f"{BASE}/i/v1/cust/couponQry/{_path_segment(cfg.phone)}",
                   {"inParam": inparam, "msgId": _msg_id()}, HDR_API)
    if str(result.get("retCode")) != "000000":
        raise RuntimeError(f"couponQry 失败: {result.get('retCode')} {result.get('retMsg')}")
    out = (result.get("data") or {}).get("outParam")
    if not out:
        raise RuntimeError("couponQry 未返回 outParam")
    payload = json.loads(decode_packet(out))
    rows = []
    for group in payload.get("busiData") or []:
        for item in group.get("batchInfo") or []:
            rows.append({
                "batchID": item.get("batchID") or "",
                "cardName": item.get("cardName") or "",
                "cardInfo": item.get("cardInfo") or "",
                "couponValue": item.get("couponValue") or "",
                "unitName": item.get("unitName") or "",
                "obtainEffTime": item.get("obtainEffTime") or "",
                "obtainExpTime": item.get("obtainExpTime") or "",
                "userObtainNum": item.get("userObtainNum") or "0",
            })
    return rows


def claimable(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """筛出当前已开放、未过期、且没领过的券。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d%H%M%S")
    out = []
    for row in rows:
        if not row.get("batchID"):
            continue
        if row["obtainEffTime"] and row["obtainEffTime"] > stamp:
            continue
        if row["obtainExpTime"] and row["obtainExpTime"] < stamp:
            continue
        try:
            if int(row["userObtainNum"] or 0) > 0:
                continue
        except (TypeError, ValueError):
            pass
        out.append(row)
    return out


def receive(session: requests.Session, cfg: Config, batch: dict) -> dict:
    inparam = encode_packet({
        "loginNo": cfg.phone, "loginType": "0", "channelId": "11",
        "oprType": "2", "batchID": batch["batchID"],
        "areaCode": "100", "payCode": "11",
    })
    return _post(session, f"{BASE}/i/v1/cust/couponReceive/{_path_segment(cfg.phone)}",
                 {"inParam": inparam, "msgId": _msg_id()}, HDR_API)


# ---------------------------------------------------------------- 主流程

def run(cfg: Config, go: bool) -> list[str]:
    uid = voucher_uid(cfg)
    if not uid:
        raise RuntimeError("找不到领券中心 UID：请重新抓包导入，或检查 app_token/voucher_uid")
    session = open_session(cfg, uid)
    rows = coupon_query(session, cfg)
    ready = claimable(rows)
    lines = [f"券池 {len(rows)} 张，未领且已开放 {len(ready)} 张"]
    ready_ids = {row["batchID"] for row in ready}
    for row in rows:
        state = "待领" if row["batchID"] in ready_ids else "跳过"
        lines.append(
            f"  [{state}] {row['cardName']} {row['cardInfo']}"
            f"（开放 {row['obtainEffTime']} 截止 {row['obtainExpTime']}）")
    if not go:
        lines.append("[dry-run] 未领券，加 --go 实际领取")
        return lines

    got = 0
    for row in ready:
        try:
            result = receive(session, cfg, row)
        except Exception as exc:
            lines.append(f"  [失败] {row['cardName']}: {type(exc).__name__}")
            continue
        code = str(result.get("retCode"))
        message = result.get("retMsg") or ""
        if code == "000000":
            got += 1
            lines.append(f"  [已领] {row['cardName']}")
            record_award(cfg.phone[-4:], "领券中心", row["cardName"] or "优惠券",
                         kind="coupon")
        elif code == "060209":
            lines.append(f"  [跳过] {row['cardName']}: 已领取")
        else:
            lines.append(f"  [失败] {row['cardName']}: {code} {message}")
    lines.append(f"本次领取成功 {got} 张")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="中国移动 App 领券中心自动领券")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--go", action="store_true", help="实际领券；默认只查询")
    parser.add_argument("--dry-run", action="store_true", help="仅查询（与默认相同）")
    parser.add_argument("--no-notify", action="store_true", help="失败时不推送")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    cfg = Config(args.config)
    if error := cfg.validate():
        log.error("%s", error)
        return 1
    try:
        for line in run(cfg, args.go and not args.dry_run):
            print(line)
        return 0
    except Exception as exc:
        message = str(exc)
        log.error("领券失败: %s", message)
        if not args.no_notify:
            notify(cfg, "领券中心执行失败",
                   f"{message}\n手机尾号 {cfg.phone[-4:] if cfg.phone else '????'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
