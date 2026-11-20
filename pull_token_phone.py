#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pull_token_phone.py —— 用「中国移动一键登录」直接换 BASS token（在手机上运行）

为什么要在手机上跑：
    getMobile 这一步靠运营商网关识别「当前蜂窝网用的是哪个号」。
    在电脑/ WiFi 上跑，服务端会返回「网关未插入手机号-23IP不在白名单」。
    所以：手机 → 关闭 WiFi → 打开移动数据 → 运行本脚本。

逆向来源（com.example.sqb v4.7, LoginActivity.requestFmccOneKeyToken）：
    1. POST app.fmcc.com.cn/bass-applet-authServer/auth/pageInit
         → data.token（匿名 Bearer）
    2. GET  www.cmpassport.com/NumberAbility/h5/getMobile.htm
         → 运营商网关识别本机号码
    3. POST app.fmcc.com.cn/bass-applet-authServer/numberAbility/getSign
         → data.sign
    4. POST www.cmpassport.com/NumberAbility/h5/getToken.htm
         → body.token（一次性登录票）
    5. POST app.fmcc.com.cn/bass-applet-authServer/numberAbility/validateToken
         → data.token（正式 Bearer）+ data.phone

    这个 token 在 app.fmcc.com.cn 和 wap.fj.10086.cn 通用，即 BASS 全平台可用。

用法（Termux）：
    pkg install python
    pip install requests
    python pull_token_phone.py
    python pull_token_phone.py --save        # 顺便写入 configs_fj/<尾号>.json
    python pull_token_phone.py --debug       # 打印每步原始响应

注意：
    - 仅在本人号码、本人账号上使用。
    - token 是账号凭证，不要发到聊天窗口或网盘。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import uuid
from pathlib import Path

import requests
from common import atomic_write_json, setup_utf8

setup_utf8()   # stdout/stderr 都固定 UTF-8，Termux 上不支持 reconfigure 时自动跳过

APP_ID_ONEKEY = "300011950611"
APP_ID_FMCC = 20
PAGE_INIT_URL = "https://app.fmcc.com.cn/bass-applet-authServer/auth/pageInit"
GET_SIGN_URL = "https://app.fmcc.com.cn/bass-applet-authServer/numberAbility/getSign"
VALIDATE_URL = "https://app.fmcc.com.cn/bass-applet-authServer/numberAbility/validateToken"
GET_MOBILE_URL = "http://www.cmpassport.com/NumberAbility/h5/getMobile.htm"
GET_TOKEN_URL = "https://www.cmpassport.com/NumberAbility/h5/getToken.htm"

UA = ("Mozilla/5.0 (Linux; Android 11; M2012K11AC Build/RKQ1.200826.002; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/104.0.5112.97 "
      "Mobile Safari/537.36")

FINGERPRINT = f"platform:Linux aarch64@@userAgent:{UA}@@isAndroidOrIOS:Android"
USER_INFORMATION = base64.b64encode(FINGERPRINT.encode("utf-8")).decode("ascii")


def u32() -> str:
    return uuid.uuid4().hex


def ts17() -> str:
    return time.strftime("%Y%m%d%H%M%S") + "%03d" % (int(time.time() * 1000) % 1000)


def math_param() -> str:
    """服务端要求的时间戳：Unix 秒，带毫秒小数。"""
    return "%.3f" % (time.time())


def fmcc_headers(token: str | None = None) -> dict:
    h = {
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Origin": "https://app.fmcc.com.cn",
        "Referer": "https://app.fmcc.com.cn/bass-equityProvinceH5/",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def cmcc_headers() -> dict:
    return {
        "User-Agent": UA,
        "Origin": "https://app.fmcc.com.cn",
        "Referer": "https://app.fmcc.com.cn/",
    }


def step(name: str, ok: bool, detail: str = ""):
    print(f"  {'✓' if ok else '✗'} {name:<12} {detail}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="写入 configs_fj/<尾号>.json")
    ap.add_argument("--debug", action="store_true", help="打印每步原始响应")
    args = ap.parse_args()

    s = requests.Session()
    s.trust_env = False
    print("中国移动一键登录 → BASS token\n")

    # ---------- 1. pageInit：拿匿名 Bearer ----------
    try:
        r = s.post(
            PAGE_INIT_URL + "?math=" + math_param(),
            json={"appId": APP_ID_FMCC, "timestamp": int(time.time() * 1000),
                  "scope": 0, "url": "https://app.fmcc.com.cn/bass-equityProvinceH5/"},
            headers=fmcc_headers(), timeout=20,
        ).json()
    except Exception as e:
        step("pageInit", False, f"网络异常 {e}")
        return 1
    if args.debug:
        print("    pageInit raw:", json.dumps(r, ensure_ascii=False)[:300])
    anon = ((r.get("data") or {}).get("token")) or ""
    if not anon:
        step("pageInit", False, f"未返回 token：{r}")
        return 1
    step("pageInit", True, f"匿名 token {anon[:8]}…")

    # ---------- 2. getMobile：运营商识别本机号 ----------
    msg_id, uuid_x = u32(), u32()
    t17 = ts17()
    url = (f"{GET_MOBILE_URL}?version=1.0&appId={APP_ID_ONEKEY}&openType=1"
           f"&expandParams=&msgId={msg_id}&timestamp={t17}&uuid={uuid_x}"
           f"&userInformation={USER_INFORMATION}")
    try:
        gm = s.get(url, headers=cmcc_headers(), timeout=20).json()
    except Exception as e:
        step("getMobile", False, f"网络异常 {e}")
        return 1
    if args.debug:
        print("    getMobile raw:", json.dumps(gm, ensure_ascii=False)[:400])
    code = ((gm.get("header") or {}).get("resultCode")) or ""
    if code not in ("0000",):
        desc = ((gm.get("body") or {}).get("resultDesc")) or ""
        step("getMobile", False, f"{code} {desc}")
        if "IP" in desc or "网关" in desc:
            print("\n  提示：必须用手机蜂窝数据，且关闭 WiFi。")
            print("        如果已关 WiFi 仍报此错，尝试开关一次飞行模式。")
        return 1
    step("getMobile", True, "网关已识别本机号码")

    # ---------- 3. getSign ----------
    busi = APP_ID_ONEKEY + msg_id + t17 + uuid_x + "1.0"
    try:
        gs = s.post(
            GET_SIGN_URL + "?math=" + math_param(),
            json={"appId": APP_ID_FMCC, "timestamp": int(time.time() * 1000),
                  "busiParams": busi, "cccode": u32(), "portalType": 3},
            headers=fmcc_headers(anon), timeout=20,
        ).json()
    except Exception as e:
        step("getSign", False, f"网络异常 {e}")
        return 1
    if args.debug:
        print("    getSign raw:", json.dumps(gs, ensure_ascii=False)[:300])
    sign = gs.get("data") or ""
    if not sign:
        step("getSign", False, f"{gs}")
        return 1
    step("getSign", True, "签名已获取")

    # ---------- 4. getToken（cmpassport 一次性票） ----------
    try:
        gt = s.post(
            GET_TOKEN_URL,
            json={
                "header": {"version": "1.0", "msgId": msg_id,
                           "timestamp": t17, "appId": APP_ID_ONEKEY},
                "body": {"expandParams": "", "sign": sign, "uuid": uuid_x},
            },
            headers=cmcc_headers(), timeout=20,
        ).json()
    except Exception as e:
        step("getToken", False, f"网络异常 {e}")
        return 1
    if args.debug:
        print("    getToken raw:", json.dumps(gt, ensure_ascii=False)[:400])
    tcode = ((gt.get("header") or {}).get("resultCode")) or ""
    one_ticket = ((gt.get("body") or {}).get("token")) or ""
    if not one_ticket:
        step("getToken", False, f"{tcode} {gt}")
        return 1
    step("getToken", True, f"一次性票 {one_ticket[:8]}…")

    # ---------- 5. validateToken：换正式 Bearer ----------
    try:
        vt = s.post(
            VALIDATE_URL + "?math=" + math_param(),
            json={
                "appId": APP_ID_FMCC, "timestamp": int(time.time() * 1000),
                "portalType": 3, "cccode": u32(),
                "busiParams": {"resultCode": tcode, "token": one_ticket,
                               "userInformation": USER_INFORMATION, "message": "success"},
            },
            headers=fmcc_headers(anon), timeout=20,
        ).json()
    except Exception as e:
        step("validate", False, f"网络异常 {e}")
        return 1
    if args.debug:
        print("    validate raw:", json.dumps(vt, ensure_ascii=False)[:400])
    data = vt.get("data") or {}
    token = data.get("token") or ""
    phone = data.get("phone") or ""
    if not token:
        step("validate", False, f"{vt}")
        return 1

    step("validate", True, f"手机号 {phone or '(未返回)'}")

    print("\n" + "=" * 46)
    print(f"手机号 : {phone or '(未返回)'}")
    print(f"token  : {token}")
    print("=" * 46)

    # ---------- 验证 token 是否真能打 BASS ----------
    try:
        v = s.get(
            "https://app.fmcc.com.cn/bass-cultivateGameServer/index/getIndexInfo",
            params={"appId": "0", "timestamp": str(int(time.time() * 1000))},
            headers={"User-Agent": UA, "Authorization": f"Bearer {token}",
                     "Referer": "https://app.fmcc.com.cn/bass-equityProvinceH5/"},
            timeout=20,
        ).json()
        ok = str(v.get("code")) == "0000"
        step("BASS 验证", ok,
             f"饲料 {((v.get('data') or {}).get('fodderSurplus'))}" if ok else f"{v.get('code')} {v.get('msg')}")
    except Exception as e:
        step("BASS 验证", False, str(e))

    if args.save and phone:
        out = Path(__file__).parent / "configs_fj" / f"{phone[-4:]}.json"
        out.parent.mkdir(exist_ok=True)
        atomic_write_json(out, {
            "bearer_token": token,
            "app_id": "0",
            "referer": "https://wap.fj.10086.cn/bass-cultivateGameServer/",
            "note": "一键登录自动获取",
            "phone": phone,
        }, indent=2)
        print(f"\n已写入 {out}")

    print("\n把这个 token 填进 configs_fj/<尾号>.json 的 bearer_token 即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
