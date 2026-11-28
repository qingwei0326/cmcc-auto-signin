"""用 HAR 中同一次领券中心会话持续查询，记录可用时间区间。"""

import argparse
import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


# 领券中心网页客户端的固定封包参数，与账号票据无关。
PACKING_KEY = b"043AOQGK6ykklyZA"
HOST = "touch.10086.cn"
SESSION_COOKIE = "jsessionid-cmcc"
OMIT_HEADERS = {"host", "connection", "content-length", "transfer-encoding", "accept-encoding"}


def local_time(value):
    return value.astimezone().isoformat(timespec="seconds")


def encode_msg_id(value):
    cipher = AES.new(PACKING_KEY, AES.MODE_CBC, PACKING_KEY)
    return base64.b64encode(base64.b64encode(cipher.encrypt(pad(value.encode(), 16)))).decode()


def decode_packet(value):
    cipher = AES.new(PACKING_KEY, AES.MODE_CBC, PACKING_KEY)
    return unpad(cipher.decrypt(base64.b64decode(base64.b64decode(value))), 16).decode()


def session_cookie(raw):
    for item in raw.split(";"):
        name, _, value = item.strip().partition("=")
        if name.lower() == SESSION_COOKIE:
            return value
    return ""


def observed_at(entry):
    start = datetime.fromisoformat(entry["startedDateTime"].replace("Z", "+00:00"))
    return start + timedelta(milliseconds=max(0, entry.get("time") or 0))


def query_example(entry):
    request, response = entry["request"], entry["response"]
    url = urlsplit(request["url"])
    if (
        request["method"] != "POST" or url.scheme != "https" or url.netloc != HOST
        or not re.fullmatch(r"/i/[^/]+/cust/couponQry/[^/?#]+", url.path)
        or url.query or url.fragment or response["status"] != 200
    ):
        return None
    headers = {item["name"]: item["value"] for item in request["headers"]
               if not item["name"].startswith(":") and item["name"].lower() not in OMIT_HEADERS}
    cookie = session_cookie(next((v for k, v in headers.items() if k.lower() == "cookie"), ""))
    if not cookie:
        return None
    try:
        content = response["content"]
        text = content.get("text") or ""
        payload = json.loads(base64.b64decode(text) if content.get("encoding") == "base64" else text)
        if payload.get("retCode") != "000000" or not payload.get("data", {}).get("outParam"):
            return None
        raw = request["postData"]["text"]
        msg_id = json.loads(raw)["msgId"]
        decoded = decode_packet(msg_id)
        if not re.fullmatch(r"\d{13}.{12}", decoded) or raw.count(msg_id) != 1:
            return None
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return request["url"], headers, raw, msg_id, decoded[13:], cookie


def load_capture(path):
    entries = json.loads(path.read_text(encoding="utf-8-sig"))["log"]["entries"]
    samples = sorted(((observed_at(e), sample) for e in entries if (sample := query_example(e))),
                     key=lambda row: row[0])
    if not samples:
        raise ValueError("no successful query")
    last_success, sample = samples[-1]
    cookie = sample[-1]
    first_success = next(at for at, row in samples if row[-1] == cookie)
    issued = [
        observed_at(e) for e in entries
        if urlsplit(e["request"]["url"]).hostname == HOST
        and any(h["name"].lower() == "set-cookie" and session_cookie(h["value"]) == cookie
                for h in e["response"]["headers"])
        and observed_at(e) <= first_success
    ]
    return sample[:-1], min(issued, default=None), first_success, last_success


def probe(session, sample):
    target, headers, raw, old_id, suffix = sample
    new_id = encode_msg_id(f"{time.time_ns() // 1_000_000}{suffix}")
    try:
        response = session.post(target, headers=headers, data=raw.replace(old_id, new_id, 1).encode(),
                                allow_redirects=False, timeout=(8, 15))
        if response.status_code == 401:
            return "rejected", "HTTP 401"
        if response.status_code != 200:
            return "unknown", f"HTTP {response.status_code}"
        payload = response.json()
        code = str(payload.get("retCode", ""))
        if code == "000000" and isinstance(payload.get("data"), dict) and payload["data"].get("outParam"):
            return "ok", "HTTP 200 / 000000"
        if code == "400010" and payload.get("retMsg") in {"登陆号码非法", "登录号码非法"}:
            return "rejected", "HTTP 200 / 400010 登录号码非法"
        safe_code = code if re.fullmatch(r"\d{1,8}", code) else "未知业务码"
        return "unknown", f"HTTP 200 / {safe_code}"
    except (requests.RequestException, AttributeError, ValueError) as error:
        return "unknown", type(error).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("har", type=Path, help="含成功 couponQry 的 App HAR")
    parser.add_argument("--once", action="store_true", help="只查询一次")
    parser.add_argument("--interval", type=int, default=300, help="查询间隔秒数，默认 300")
    parser.add_argument("--max-minutes", type=int, default=180, help="最长自动观察分钟数，默认 180")
    parser.add_argument("--log", type=Path, help="追加不含凭据的探测记录")
    args = parser.parse_args()
    if args.interval < 60 or args.max_minutes < 1:
        parser.error("间隔至少 60 秒，最长观察时间至少 1 分钟")
    try:
        sample, issued, first_success, last_success = load_capture(args.har)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, "无法读取含有效 couponQry 样本的 HAR。\n")

    def log(message):
        print(message, flush=True)
        if args.log:
            args.log.parent.mkdir(parents=True, exist_ok=True)
            with args.log.open("a", encoding="utf-8") as file:
                print(message, file=file)

    start = issued or first_success
    log(f"计时起点 {local_time(start)}（{'抓包签发时间' if issued else '抓包首次成功，实际签发时间未知'}）")
    log(f"抓包最后成功 {local_time(last_success)}")
    deadline = time.monotonic() + args.max_minutes * 60
    with requests.Session() as session:
        session.trust_env = False
        while True:
            status, detail = probe(session, sample)
            now = datetime.now(timezone.utc)
            log(f"{local_time(now)} {status} {detail}")
            if status == "ok":
                last_success = now
            if status == "rejected":
                log(f"最后成功 {local_time(last_success)}，首次拒绝 {local_time(now)}")
                log(f"从计时起点算起，可用区间边界约为 "
                    f"{(last_success - start).total_seconds() / 60:.1f}～"
                    f"{(now - start).total_seconds() / 60:.1f} 分钟。")
                if not issued:
                    log("未捕获签发，这个区间描述首次观测后的余寿命。")
                return 1
            if args.once or deadline - time.monotonic() < args.interval:
                log(f"尚未观测到登录拒绝，最后成功 {local_time(last_success)}")
                return 2 if status == "unknown" else 0
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
