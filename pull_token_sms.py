#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pull_token_sms.py —— 用「中国移动官方短信验证码登录」在电脑上直接换 qwhdhub token

不经过任何第三方，不需要抓包，不需要蜂窝网。
逆向的是**协议**（login.10086.cn 的 H5 登录），不是 App 二进制
——App 那层有梆梆加固，逆不动也没必要，因为它登录后同样要回到这条链换票据。

用法：
    python pull_token_sms.py start 13800138000     # 发短信（换成你的号码）
    python pull_token_sms.py captcha               # 若提示需要图形验证码
    python pull_token_sms.py send 1234             # 带图形验证码重发
    python pull_token_sms.py login 654321          # 提交短信验证码 → 出 app_token

成功后自动写入 configs/<尾号>.json，并打印能否用于 qwhdhub 签到。
"""

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

import requests
from common import atomic_write_json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).parent
STATE = BASE / ".sms_login_state.json"
LOGIN = "https://login.10086.cn"

RSA_PUB_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsgDq4OqxuEisnk2F0EJFmw4xKa5IrcqEYHvqxPs2CHEg2k"
    "olhfWA2SjNuGAHxyDDE5MLtOvzuXjBx/5YJtc9zj2xR/0moesS+Vi/xtG1tkVaTCba+TV+Y5C61iyr3FGqr+KOD4/XE"
    "Cu0Xky1W9ZmmaFADmZi7+6gO9wjgVpU9aLcBcw/loHOeJrCqjp7pA98hRJRY+MML8MK15mnC4ebooOva+mJlstW6t/1l"
    "ghR8WNV8cocxgcHHuXBxgns2MlACQbSdJ8c6Z3RQeRZBzyjfey6JCCfbEKouVrWIUuPphBL3OANfgp0B+QG31bapvePT"
    "fXU48TYK0M5kE+8LgbbWQIDAQAB"
)
_PUB = serialization.load_der_public_key(base64.b64decode(RSA_PUB_B64))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")


def et(text: str) -> str:
    """RSA/PKCS1v15 加密后 Base64 —— 页面用 jsencrypt，同一套。"""
    return base64.b64encode(_PUB.encrypt((text or "").encode(), padding.PKCS1v15())).decode()


def new_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": LOGIN + "/",
        "Origin": LOGIN,
        "X-Requested-With": "XMLHttpRequest",
    })
    return s


def load_state():
    if not STATE.exists():
        sys.exit("还没有会话，先执行：python pull_token_sms.py start <手机号>")
    st = json.loads(STATE.read_text(encoding="utf-8"))
    s = new_session()
    for ck in st.get("cookies", []):
        s.cookies.set(ck["name"], ck["value"], domain=ck.get("domain", "login.10086.cn"),
                      path=ck.get("path", "/"))
    return s, st


def save_state(s, st):
    st["cookies"] = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                     for c in s.cookies]
    atomic_write_json(STATE, st)


def hidden(html, eid):
    for pat in (r'id="%s"[^>]*value="([^"]*)"' % re.escape(eid),
                r'value="([^"]*)"[^>]*id="%s"' % re.escape(eid)):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return ""


def step(name, ok, detail=""):
    print(f"  {'✓' if ok else '✗'} {name:<14} {detail}")


# ------------------------------------------------------------------ 发短信

def do_send(s, st, captcha=""):
    phone = st["phone"]
    r = s.post(LOGIN + "/loadToken.action", data={"userName": et(phone)}, timeout=30)
    try:
        tok = r.json().get("result", "")
    except Exception:
        tok = ""
    if not tok:
        step("loadToken", False, r.text[:160])
        return False
    step("loadToken", True, f"{tok[:12]}…")

    data = {"userName": et(phone), "inputCode": et(captcha), "type": "01",
            "channelID": st.get("channelID", "")}
    r2 = s.post(LOGIN + "/sendRandomCodeAction.action", data=data,
                headers={"Xa-before": tok}, timeout=30)
    save_state(s, st)
    txt = (r2.text or "").strip()
    if txt == "0":
        step("发送短信", True, "验证码已发出")
        return True
    if txt in ("-1", "10", "11") or "验证码" in txt:
        step("发送短信", False, f"需要图形验证码（返回 {txt}）")
        print("\n    执行：python pull_token_sms.py captcha  下载图片，")
        print("    看图后：python pull_token_sms.py send <图片里的字符>")
        return False
    step("发送短信", False, f"返回 {txt[:120]}")
    return False


def cmd_start(phone):
    if not re.fullmatch(r"1\d{10}", phone):
        sys.exit("手机号格式不对")
    s = new_session()
    r = s.get(LOGIN + "/", timeout=30)
    st = {"phone": phone,
          "loginMode": hidden(r.text, "loginMode") or "01",
          "channelID": hidden(r.text, "channelID") or "",
          "backUrl": hidden(r.text, "backUrl") or ""}
    save_state(s, st)
    print(f"目标 {phone}  loginMode={st['loginMode']}  channelID={st['channelID'] or '(空)'}\n")
    do_send(s, st, "")


def cmd_send(captcha):
    s, st = load_state()
    do_send(s, st, captcha)


def cmd_captcha():
    s, _ = load_state()
    r = s.get(LOGIN + f"/loadSendflag.htm?timestamp={int(time.time()*1000)}", timeout=30)
    ct = r.headers.get("Content-Type") or ""
    if "image" in ct and r.content:
        p = BASE / "captcha.png"
        p.write_bytes(r.content)
        step("图形验证码", True, f"已保存 {p.name}")
    else:
        step("图形验证码", False, f"没取到图片（{ct}，{len(r.content)} 字节）")


# ------------------------------------------------------------------ 登录

def build_app_token(cookies: dict) -> str:
    """App 的 token 形如 JSESSIONID=...; UID=...; Comment=...; ticketID=..."""
    want = ["JSESSIONID", "UID", "Comment", "ticketID"]
    parts = []
    for k in want:
        for n, v in cookies.items():
            if n.lower() == k.lower():
                parts.append(f"{k}={v}")
    return "; ".join(parts)


def cmd_login(code):
    s, st = load_state()
    params = {
        "accountType": "01",
        "account": et(st["phone"]),
        "password": et(code),
        "pwdType": "02",
        "inputCode": "",
        "backUrl": st.get("backUrl", ""),
        "rememberMe": "0",
        "channelID": st.get("channelID", ""),
        "loginMode": st.get("loginMode", "01"),
        "protocol": "https:",
        "isNew": "0",
        "timestamp": str(int(time.time() * 1000)),
    }
    r = s.post(LOGIN + "/login.htm", data=params, timeout=30)
    save_state(s, st)
    try:
        j = r.json()
    except Exception:
        step("login.htm", False, r.text[:200])
        return

    print("  服务端返回:", json.dumps(j, ensure_ascii=False)[:300])
    if j.get("result") != "0":
        step("验证码登录", False, j.get("desc") or j.get("msg") or "失败")
        return
    step("验证码登录", True, "登录成功")

    cookies = {c.name: c.value for c in s.cookies}
    print("  cookies:", ", ".join(cookies.keys()))

    app_token = build_app_token(cookies)
    if app_token:
        st["app_token"] = app_token
        save_state(s, st)
        print(f"\n  app_token: {app_token[:60]}…（共 {len(app_token)} 字符）")

    # SSO 断言：服务端若给了 assertAcceptURL，跟着走一步
    sso = j.get("assertAcceptURL")
    if sso:
        print(f"  SSO 断言: {sso[:90]}…")
        try:
            s.get(sso, timeout=30, allow_redirects=True)
        except Exception as e:
            print("  跟随后失败:", e)

    write_config(st, cookies, app_token)


def write_config(st, cookies, app_token):
    phone = st["phone"]
    if not app_token:
        step("写入配置", False, "没拼出 app_token，看上面的 cookies 名字")
        return
    cfg = {
        "app_token": app_token,
        "phone": phone,
        "province_code": "591",
        "city_code": "0596",
        "carrier_operator": "002",
        "app_version_code": "12.5.7",
        "activity_id": "1021122301",
        "channel_id": "P00000109876",
        "fee_session_cookie": "",
        "serverchan_sendkey": "",
        "bark_url": "",
    }
    out = BASE / "configs" / f"{phone[-4:]}.json"
    out.parent.mkdir(exist_ok=True)
    atomic_write_json(out, cfg, indent=2)
    step("写入配置", True, str(out.relative_to(BASE)))
    print("\n验证：python cmcc_sign.py --config configs/%s.json --dry-run" % phone[-4:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start", "send", "captcha", "login"])
    ap.add_argument("arg", nargs="?", default="")
    a = ap.parse_args()
    if a.cmd == "start":
        cmd_start(a.arg)
    elif a.cmd == "send":
        cmd_send(a.arg)
    elif a.cmd == "captcha":
        cmd_captcha()
    elif a.cmd == "login":
        cmd_login(a.arg)


if __name__ == "__main__":
    main()
