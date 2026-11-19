"""
common.py —— 各平台脚本共用的基础设施

集中放置原来在四个脚本里各写一遍的东西：

    · 原子写        —— 先写临时文件再 os.replace，避免定时任务并发写坏 json
    · 通知          —— Bark / Server酱推送，失败不影响主流程
    · 会话与接口调用 —— 统一返回 {ok, code, msg, data}
    · 结果展示      —— 统一的 "✓/!!" 输出

各平台脚本只保留自己的接口表和业务流程。
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

BASE = Path(__file__).parent

# 移动端 UA，部分接口会对桌面 UA 直接返回异常
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148/wkwebview")

sys.stdout.reconfigure(encoding="utf-8")


def setup_utf8():
    """把 stdout / stderr 都固定成 UTF-8。

    Windows 上 stderr 在被管道捕获时默认用 GBK，父进程按 UTF-8 解码就会乱码
    （表现为「ִʧ: appTokenLogin ʧ: FAILED APP¼״̬...」）。
    必须在 logging.basicConfig 之前调用，handler 才会拿到新编码的流。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------------- 基础

def now_ms():
    """13 位毫秒时间戳，BASS 全系列接口都要带。"""
    return str(int(time.time() * 1000))


def atomic_write_text(path, text):
    """原子写文本：先写 .tmp 再 os.replace。

    定时任务（每小时轮询）与一键签到可能同时写同一个 json，
    直接 write_text 在中途被打断就会留下半截文件导致后续解析失败。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path, obj, indent=1):
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent))


def load_json(path, default=None):
    """读 json，文件缺失或损坏都返回默认值而不是抛异常。"""
    try:
        p = Path(path)
        if not p.exists():
            return default if default is not None else {}
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


# ---------------------------------------------------------------- 配置

def load_config(path, required=()):
    """读取平台配置；缺少必需字段时直接退出并说明怎么填。"""
    path = Path(path)
    if not path.exists():
        print(f"缺少配置文件：{path}")
        print(f"可复制 config.example.json 作为模板：{BASE / 'config.example.json'}")
        sys.exit(1)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"配置文件解析失败：{path}\n  {e}")
        sys.exit(1)
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        print(f"{path.name} 缺少字段：{', '.join(missing)}")
        print("请参考 config.example.json 填写。")
        sys.exit(1)
    return cfg


def collect_notify_settings(*cfgs):
    """从若干配置里收集推送设置（Bark / Server酱），环境变量优先。"""
    bark = os.environ.get("CMCC_BARK_URL", "").strip()
    key = os.environ.get("CMCC_SERVERCHAN_SENDKEY", "").strip()
    for cfg in cfgs:
        if not cfg:
            continue
        bark = bark or str(cfg.get("bark_url") or "").strip()
        key = key or str(cfg.get("serverchan_sendkey") or "").strip()
    return bark, key


# ---------------------------------------------------------------- 通知

def notify(title, detail, bark_url="", serverchan_sendkey=""):
    """推送到 Bark 和/或 Server酱。失败只告警，绝不影响主流程。"""
    bark_url = (bark_url or os.environ.get("CMCC_BARK_URL", "")).strip()
    key = (serverchan_sendkey or os.environ.get("CMCC_SERVERCHAN_SENDKEY", "")).strip()
    if not bark_url and not key:
        return False

    sent = False
    s = requests.Session()
    s.trust_env = False
    try:
        if key:
            # sendkey 会拼进 URL path，严格校验防止注入
            if not re.fullmatch(r"[0-9A-Za-z]+", key):
                print(f"  [通知] serverchan_sendkey 含非法字符，已跳过")
            else:
                s.post(f"https://sctapi.ftqq.com/{key}.send",
                       data={"title": title, "desp": detail}, timeout=15)
                sent = True
        if bark_url:
            if not bark_url.lower().startswith(("http://", "https://")):
                print("  [通知] bark_url 协议不合法，已跳过")
            else:
                s.get(bark_url, params={"title": title, "body": detail}, timeout=15)
                sent = True
    except Exception as e:
        print(f"  [通知] 推送失败（不影响本次执行）：{e}")
    return sent


# ---------------------------------------------------------------- 会话与调用

def make_session(token=None, headers=None, referer=None):
    """构造一个直连（不走系统代理）的会话。

    走系统代理容易被抓包工具或公司代理干扰，这几个接口直连更稳。
    """
    s = requests.Session()
    s.trust_env = False
    h = {"User-Agent": UA_MOBILE, "Accept": "*/*",
         "Accept-Language": "zh-CN,zh-Hans;q=0.9"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if referer:
        h["Referer"] = referer
    if headers:
        h.update(headers)
    s.headers.update(h)
    return s


def _norm(resp):
    """把 HTTP 响应规整成 {ok, code, msg, data}。"""
    if resp.status_code == 401:
        return {"ok": False, "code": 401,
                "msg": "token 失效，需重新抓包更新", "data": None}
    if resp.status_code != 200:
        return {"ok": False, "code": resp.status_code,
                "msg": f"HTTP {resp.status_code}", "data": None}
    try:
        j = resp.json()
    except Exception:
        return {"ok": False, "code": -1, "msg": "响应不是合法 JSON", "data": None}
    ok = bool(j.get("success")) or str(j.get("code")) in ("0", "0000", "200")
    return {"ok": ok, "code": j.get("code"), "msg": j.get("msg"), "data": j.get("data")}


def api_get(session, url, params=None, retries=1, timeout=25, retry_codes=("-100",)):
    """GET 调用。命中 retry_codes 时退避重试。"""
    last = None
    for attempt in range(retries + 1):
        try:
            res = _norm(session.get(url, params=params, timeout=timeout))
            if res["ok"] or str(res.get("code")) not in retry_codes or attempt >= retries:
                return res
            last = res
        except Exception as e:
            last = {"ok": False, "code": -1, "msg": f"网络异常: {e}", "data": None}
        if attempt < retries:
            time.sleep(2.5)
    return last


def api_post(session, url, body=None, retries=1, timeout=25, retry_codes=("-100", "1006")):
    """POST 调用（JSON body）。命中 retry_codes 时刷新时间戳并退避重试。"""
    body = dict(body or {})
    last = None
    for attempt in range(retries + 1):
        try:
            res = _norm(session.post(url, json=body, timeout=timeout))
            if res["ok"] or str(res.get("code")) not in retry_codes or attempt >= retries:
                return res
            last = res
        except Exception as e:
            last = {"ok": False, "code": -1, "msg": f"网络异常: {e}", "data": None}
        if attempt < retries:
            time.sleep(3.5)
    return last


# ---------------------------------------------------------------- 输出

def show(label, res, extra=""):
    """统一展示一次接口调用结果，返回是否成功。"""
    if not res:
        print(f"  !! {label:<24} 无响应")
        return False
    if res.get("ok"):
        print(f"  ✓ {label:<24} code={res.get('code')}{extra}")
        return True
    print(f"  !! {label:<24} code={res.get('code')} {res.get('msg') or ''}{extra}")
    return False


def mask_phone(phone):
    """手机号脱敏，显示用。"""
    s = str(phone or "")
    return f"...{s[-4:]}" if len(s) >= 4 else s
