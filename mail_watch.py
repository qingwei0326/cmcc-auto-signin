#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mail_watch.py —— 直接从邮箱收 HAR 附件并自动导入（不经过智能体）

用法：
    python mail_watch.py --once        # 查一次收件箱（给计划任务每 5 分钟调用）
    python mail_watch.py --test        # 只测邮箱连通，不导入

配置在 gui_data/mail_settings.json（已 gitignore，不上传）：
{
  "enabled": true,
  "host": "imap.gmail.com",
  "port": 993,
  "user": "你的邮箱@gmail.com",
  "password": "16 位应用专用密码",      # 不是 Gmail 登录密码
  "sender": "只允许这个发件人",          # 留空=不限制（不建议）
  "subject_keyword": "HAR"              # 标题含这个词才处理，留空=不筛
}

安全：
  · 只从白名单发件人取未读邮件；
  · 附件只保存 .har，写到 incoming_har/，不执行；
  · 处理过的 UID 记在 gui_data/mail_state.json，重复跑不会重复导入；
  · 导入结果用现成的 PushPlus/Bark/Server酱 推送。
"""

import argparse
import datetime
import email
import imaplib
import re
import ssl
import sys
import time
from email import policy
from pathlib import Path

from common import app_base, atomic_write_json, load_json, notify, setup_utf8, update_json

setup_utf8()

BASE = app_base()
DATA = BASE / "gui_data"
SETTINGS = DATA / "mail_settings.json"
STATE = DATA / "mail_state.json"
MAIL_NEED = DATA / "mail_need.json"
TODAY_DONE = DATA / "today_done.json"
INBOX_DIR = BASE / "incoming_har"

PROVIDERS = {
    "gmail": {"host": "imap.gmail.com", "port": 993,
              "hint": "Gmail 要在 Google 账号里生成 16 位应用专用密码，不是登录密码；电脑需能访问 imap.gmail.com。"},
    "qq": {"host": "imap.qq.com", "port": 993,
           "hint": "QQ 邮箱要在「设置 → 账户 → POP3/IMAP」开启 IMAP 并生成授权码，填授权码不是 QQ 密码。"},
    "163": {"host": "imap.163.com", "port": 993,
            "hint": "163 邮箱要在「设置 → POP3/SMTP/IMAP」开启 IMAP 并拿授权码。"},
    "139": {"host": "imap.139.com", "port": 993,
            "hint": "139 邮箱要在网页版「设置 → 账号与安全 → 邮箱协议设置」开启 IMAP 并获取授权码；授权码有效期 90 天，过期要重取。"},
}

DEFAULTS = {
    "enabled": False,
    "provider": "gmail",
    "host": "imap.gmail.com",
    "port": 993,
    "user": "",
    "password": "",
    "sender": "",
    "subject_keyword": "HAR",
}


def conf():
    s = dict(DEFAULTS)
    s.update(load_json(SETTINGS, default={}) or {})
    p = PROVIDERS.get(str(s.get("provider") or "gmail").lower())
    if p and not str(s.get("host") or "").strip():
        s["host"] = p["host"]
        s["port"] = p["port"]
    return s


def load_state() -> dict:
    return load_json(STATE, default={}) or {}


def should_check() -> bool:
    """只在「凭证确实失效 + 今天任务没全做完」时查邮箱。

    这两个标记由 run_all.py 每次运行时写：票失效写 mail_need，
    全部跑顺写 today_done。这样没过期、或今天已经做完，就完全不连 Gmail。
    """
    done = load_json(TODAY_DONE, default={}) or {}
    if done.get("date") == datetime.date.today().isoformat():
        print("今天的任务已全部完成，不查邮箱")
        return False
    need = load_json(MAIL_NEED, default={}) or {}
    if not need.get("needed"):
        print("没有凭证失效标记，不查邮箱")
        return False
    return True


def clear_need():
    atomic_write_json(MAIL_NEED, {
        "needed": False,
        "cleared_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=1)


def mark_seen(uid: str):
    def mutate(st):
        seen = set(st.get("seen") or [])
        seen.add(str(uid))
        st["seen"] = sorted(seen)[-500:]
        st["last_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    update_json(STATE, mutate, default={})


def connect(cfg):
    host = str(cfg.get("host") or "imap.gmail.com").strip()
    port = int(cfg.get("port") or 993)
    try:
        imap = imaplib.IMAP4_SSL(host, port, timeout=30)
    except ssl.SSLError:
        # 139 邮箱的 IMAP 用老式加密套件，OpenSSL 3 默认 SECLEVEL=2 会直接
        # 拒绝握手（SSLV3_ALERT_HANDSHAKE_FAILURE）。降到 SECLEVEL=0 重试，
        # 只对第一次握手失败的服务器生效，QQ/163 正常路径不受影响。
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        except ssl.SSLError:
            pass
        imap = imaplib.IMAP4_SSL(host, port, timeout=30, ssl_context=ctx)
    imap.login(str(cfg.get("user") or "").strip(), str(cfg.get("password") or ""))
    imap.select("INBOX")
    return imap


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "mail.har"))[-120:]
    return name if name.lower().endswith(".har") else name + ".har"


def extract_har(msg):
    """从邮件里找第一个 .har 附件，返回 (文件名, 文本)。"""
    if not msg.is_multipart():
        return None
    for part in msg.walk():
        if part.is_multipart():
            continue
        fn = str(part.get_filename() or "")
        if not fn.lower().endswith(".har"):
            continue
        raw = part.get_payload(decode=True)
        if not raw:
            continue
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return fn, raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return fn, raw.decode("utf-8", errors="replace")
    return None


def test_connection(cfg) -> int:
    try:
        imap = connect(cfg)
        typ, data = imap.search(None, "UNSEEN")
        n = len(data[0].split()) if typ == "OK" and data and data[0] else 0
        imap.logout()
        print(f"✓ 邮箱连通：{cfg.get('user')}（收件箱未读 {n} 封）")
        return 0
    except Exception as e:
        print(f"✗ 邮箱连接/登录失败：{e}")
        provider = PROVIDERS.get(str(cfg.get("provider") or "gmail").lower())
        print("  " + (provider["hint"] if provider else "检查 host/端口与授权码。"))
        return 1


def run_once(cfg) -> int:
    cfg = conf()
    if not cfg.get("enabled"):
        print("邮箱收件未启用（gui_data/mail_settings.json 里 enabled=false）")
        return 0
    missing = [k for k in ("host", "user", "password") if not str(cfg.get(k) or "").strip()]
    if missing:
        print("邮箱配置不全，缺：" + "、".join(missing))
        return 0

    if not should_check():
        return 0

    st = load_state()
    seen = set(st.get("seen") or [])
    try:
        imap = connect(cfg)
    except Exception as e:
        print(f"邮箱连接失败：{e}")
        return 1

    criteria = ["UNSEEN"]
    sender = str(cfg.get("sender") or "").strip()
    if sender:
        criteria.append(f'FROM "{sender}"')
    typ, data = imap.search(None, *criteria)
    if typ != "OK" or not data or not data[0]:
        print("没有待处理的新邮件")
        imap.logout()
        return 0

    uids = [u for u in data[0].split() if u.decode() not in seen]
    if not uids:
        print("新邮件都已处理过")
        imap.logout()
        return 0

    import har_import as HI

    INBOX_DIR.mkdir(exist_ok=True)
    ok = 0
    for uid in uids:
        uid_s = uid.decode()
        typ, msg_data = imap.fetch(uid, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)
        subject = str(msg.get("subject") or "")
        kw = str(cfg.get("subject_keyword") or "").strip()
        if kw and kw.lower() not in subject.lower():
            continue
        found = extract_har(msg)
        if not found:
            continue
        fn, text = found
        saved = INBOX_DIR / f"{int(time.time())}_{uid_s}_{safe_name(fn)}"
        saved.write_text(text, encoding="utf-8")
        print(f"收到 HAR：{fn}（{len(text)} 字符）→ {saved.name}")
        report = HI.apply(text)
        for line in report:
            print("  " + line)
        notify("HAR 自动导入完成", "\n".join(report))
        mark_seen(uid_s)
        imap.store(uid, "+FLAGS", "\\Seen")
        ok += 1
        if ok:
            clear_need()

    imap.logout()
    print(f"本轮处理 {ok} 份 HAR")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="邮箱收 HAR 自动导入")
    ap.add_argument("--once", action="store_true", help="查一次收件箱（默认）")
    ap.add_argument("--test", action="store_true", help="只测连通，不导入")
    args = ap.parse_args()
    cfg = conf()
    if args.test:
        return test_connection(cfg)
    return run_once(cfg)


if __name__ == "__main__":
    sys.exit(main())
