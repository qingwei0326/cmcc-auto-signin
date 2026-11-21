"""guandan_bot.py —— 咪咕掼蛋自动打牌。

登录链：`api/login`（要 cat 令牌）→ `wss://gd-ls1` 换游戏服地址 →
`wss://gd-gs3` 登录 → 拉场次 → 进桌 → 挨个出牌。

**凭证从哪来**：`cat` 是咪咕 SDK 在「中国移动」App 内用 SIM 卡免密签发的，
PC 端算不出来。所以流程是：用户每隔 2~3 天在 App 里进一次掼蛋，顺手抓个包
发邮箱，`har_import` 自动把 `cat` 抠出来写进 `gui_data/guandan_auth.json`，
剩余天数由本脚本全自动打。

用法：
    python guandan_bot.py --probe          # 只登录+进桌，一张牌都不出（先验流程）
    python guandan_bot.py                  # 自动打，打到局末/断线
    python guandan_bot.py --minutes 45     # 最多打 45 分钟
    python guandan_bot.py --host 5         # 手动指定级牌（默认自动推断）
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import guandan_rules as G
import guandan_ws as WS
from cmcc_guandan import (GD_MATCH, GD_ROOM, MSG_GAME, OpGd, OpGm,
                          frame_join, frame_list, frame_lock, frame_pass,
                          frame_play, frame_query, frame_sign, gv, pairs)

BASE = Path(__file__).parent
AUTH_FILE = BASE / "gui_data" / "guandan_auth.json"
LOG_FILE = Path(os.environ.get("CMCC_GUANDAN_LOG") or (BASE / "logs" / "guandan_bot.log"))

QUIET = False           # 测试时置 True，别刷屏也别写日志

LOGIN_URL = "https://gd-api.migufun.com/api/login"
LS_URL = "wss://gd-ls1.migufun.com"
GS_PORT = 8444
EMID = 601              # 赛事组 id，写死在客户端里

# 移动 App 渠道参数（抓包里就是这样，换一个也不影响登录）
CHANNEL = "40257751680"
TOUCHPOINT = "BSZT2026000001"
APP_VER = "1.2.0.9"
VC = 30009


# ------------------------------------------------------------------ 日志

def _log(msg):
    if QUIET:
        return
    line = time.strftime("%H:%M:%S ") + str(msg)
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------ 凭证

def load_auth():
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not data.get("cat"):
        return None
    return data


def save_auth(data):
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(AUTH_FILE)


def auth_left_days(auth):
    exp = (auth or {}).get("expire") or 0
    if not exp:
        return None
    return (exp / 1000.0 - time.time()) / 86400.0


# ------------------------------------------------------------------ 会话

class Bot(object):
    def __init__(self, auth, probe=False, host=0, minutes=0, login_only=False):
        self.auth = auth
        self.probe = probe
        self.login_only = login_only
        self.fixed_host = host
        self.deadline = time.time() + minutes * 60 if minutes else 0
        self.uid = int(auth.get("uid") or 0)
        self.u = auth.get("u") or ""
        self.cat = auth.get("cat") or ""
        self.mask = auth.get("phone_mask") or ""

        self.ws = None
        self.seat = int(auth.get("seat") or 1)
        self.hand = []
        self.last = None
        self.last_seat = None
        self.passes = 0
        self.host = host or 2
        self.host_ok = bool(host)
        self.samples = []
        self.dealt = False
        self.acted_at = 0.0
        self.match_id = 0
        self.room_field = {}

    # -------------------------------------------------------------- 登录
    def _ext(self):
        return json.dumps({
            "appType": 1, "appVersion": APP_VER, "appChannel": "migufun_w",
            "deviceId": self.auth.get("device") or "00-00-00-00",
            "channel": CHANNEL, "touchpoint": TOUCHPOINT,
            "mc": "GD_ZYZT", "sc": "GD_BSZT_0001",
            "province": "591", "city": "0596",
        }, ensure_ascii=False, separators=(",", ":"))

    def http_login(self):
        """用 cat 换 uid/u。顺带刷新 tokenExpire。"""
        import requests
        body = {"ap": 1001, "cc": "migufun_w", "ccu": "", "mode": 3,
                "cat": self.cat, "cuid": "",
                "ext": self._ext(), "v": APP_VER, "vc": VC}
        r = requests.post(LOGIN_URL, json=body, timeout=15)
        try:
            data = r.json()
        except Exception:
            raise RuntimeError(f"登录返回不是 JSON：{r.text[:200]}")
        info = data.get("data") or {}
        if not data.get("success") or not info.get("mid"):
            code = info.get("code")
            msg = info.get("msg") or data.get("msg") or ""
            if code == 132:
                raise RuntimeError(
                    "cat 已失效（第三方验证失败）。实测抓包里那三张 cat 在 30 分钟后"
                    "全部返回 132，和伪造值同一个错误 —— 说明 cat 要么一次性、要么只有"
                    "几分钟寿命。请重新抓包后立刻跑本脚本，别隔夜。")
            raise RuntimeError(f"登录失败：{msg or code} {str(data)[:200]}")
        self.uid = int(info["mid"])
        self.u = info.get("u") or self.u
        self.cat = info.get("cat") or self.cat
        self.mask = (info.get("ext") and _mask_of(info["ext"])) or self.mask
        self.auth.update({"uid": self.uid, "u": self.u, "cat": self.cat,
                          "expire": info.get("tokenExpire") or 0,
                          "phone_mask": self.mask})
        save_auth(self.auth)
        _log(f"登录成功 uid={self.uid} 号码={self.mask or '?'} "
             f"剩余 {auth_left_days(self.auth):.1f} 天")
        return info

    def ls_login(self):
        """登录服换游戏服地址。"""
        ws = WS.WebSocket(LS_URL, timeout=15)
        ws.connect()
        vals = [1, self.uid, 2, self.cat, 3, "9000", 4, "1", 5, 0, 7, 1,
                8, "web", 9, "migufun_w", 10, VC, 11, "1301", 12, 1, 13, 1]
        ws.send_text(_frame({"req": 1, "vals": vals}))
        addr = None
        for _ in range(6):
            msg = ws.recv_text(timeout=8)
            if msg is None:
                break
            obj = _parse(msg)
            if not obj:
                continue
            if obj.get("res") == 1:
                kv = pairs(obj.get("vals"))
                host = ""
                for v in kv.values():
                    if isinstance(v, str) and "migufun" in v and ":" in v:
                        host = v.split(":")[0]
                        break
                addr = host or "gd-gs3.migufun.com"
                _log(f"游戏服地址：{addr}")
                break
        ws.close()
        if not addr:
            raise RuntimeError("登录服没给游戏服地址")
        return addr

    def gs_connect(self, host):
        ws = WS.WebSocket(f"wss://{host}:{GS_PORT}", timeout=20)
        ws.connect()
        # 第一条：随机 uuid，服务端原样回显
        sid = "%08x-%04x-%04x-%04x-%012x" % (
            random.getrandbits(32), random.getrandbits(16), random.getrandbits(16),
            random.getrandbits(16), random.getrandbits(48))
        ws.send_text(f"~m~{len(sid)}~m~{sid}")
        echo = ws.recv_text(timeout=8)
        if echo and sid in echo:
            _log("WebSocket 握手 OK")
        vals = [1, self.uid, 2, self.u, 3, 2, 4, self.cat, 5, VC, 6, 0,
                7, "migufun_w", 8, "", 9, 0, 12, 1, 13, "1301",
                22, str(self.uid), 23, self.mask or "", 42, "08", 29, 0,
                30, _hex32(), 32, 4, 33, 1, 38, 1, 39, 1,
                40, [1, 1, 0, 1, 1],
                41, f"{host}:9091", 34, self._ext()]
        ws.send_text(_frame({"req": 16, "vals": vals}))
        self.ws = ws
        # 等 res:14 / res:16
        for _ in range(8):
            msg = ws.recv_text(timeout=8)
            if msg is None:
                break
            obj = _parse(msg)
            if not obj:
                continue
            if obj.get("res") in (14, 16):
                _log(f"游戏服登录 OK (res={obj.get('res')})")
                if obj.get("res") == 14:
                    continue
                return
        _log("游戏服登录没等到确认，继续尝试")

    # -------------------------------------------------------------- 收发
    def _heartbeat(self):
        self.ws.send_text(_frame({"irq": 1, "vals": [1, int(time.time() * 1000)]}))

    # -------------------------------------------------------------- 场次
    def pick_match(self):
        """拉场次列表，挑当前时段在开的掼蛋日赛。"""
        self.ws.send_text(frame_list())
        t0 = time.time()
        while time.time() - t0 < 12:
            msg = self.ws.recv_text(timeout=3)
            if msg is None:
                continue
            obj = _parse(msg)
            if not obj:
                continue
            kv = pairs(obj.get("vals") or [])
            inner = kv.get(6)
            if not isinstance(inner, dict):
                continue
            if gv(inner, 2) == GD_MATCH and gv(inner, 3) == OpGm["List"]:
                raw = gv(inner, 6) or gv(inner, 5)
                mid = _pick_from_list(raw)
                if mid:
                    _log(f"选中场次 {mid}")
                    return mid
                _log("场次列表里没找到正在开的掼蛋日赛")
                return 0
        return 0

    def join(self, match_id):
        # 顺序照抄客户端：查询状态 -> 锁房 -> 进桌
        for text, tag in ((frame_query(), "查询状态"),
                          (frame_lock(0), "锁房"),
                          (frame_join(self.uid, EMID, GD_MATCH, match_id), "进桌")):
            self.ws.send_text(text)
            _log(f"→ {tag}")
            time.sleep(0.4)
        self.match_id = match_id

    # -------------------------------------------------------------- 牌局
    def _infer_host(self):
        if self.fixed_host:
            return
        if len(self.samples) < 2:
            return
        best, best_bad = None, None
        for h in range(2, 15):
            last, last_seat, bad = None, None, 0
            for seat, cards, tid in self.samples[-12:]:
                p = G.classify(cards, h, hint=tid)
                if p is None:
                    bad += 1
                    continue
                if seat != last_seat and last is not None and not G.can_over(p, last):
                    bad += 1
                last, last_seat = p, seat
            if best_bad is None or bad < best_bad:
                best, best_bad = h, bad
        if best and best_bad == 0 and best != self.host:
            _log(f"级牌推断为 {G.RANK_NAMES[best - 2]}（原来按 {G.RANK_NAMES[self.host - 2]}）")
            self.host = best
            self.host_ok = True
        elif best_bad == 0:
            self.host_ok = True

    def on_deal(self, cards):
        self.hand = list(cards)
        self.last = None
        self.last_seat = None
        self.passes = 0
        self.dealt = True
        srt = sorted(cards, key=lambda v: (-G.rank_of(v), _suit_key(v)))
        _log(f"发牌 {len(cards)} 张：{G.cards_text(srt)}")

    def on_action(self, inner):
        seat = gv(inner, 6)
        raw = gv(inner, 7)
        if not isinstance(raw, str):
            # 无 7 的是「该 X 号位出牌」的倒计时通知
            if gv(inner, 5) == 2 and gv(inner, 6) == self.seat:
                self.act()
            return
        arr = json.loads(raw)
        n = arr[1] if len(arr) > 1 else 0
        cards = arr[2:2 + n]
        nxt = arr[-4] if len(arr) >= 4 else 0
        if cards:
            tid = arr[2 + n] if len(arr) > 2 + n else None
            play = G.classify(cards, self.host, hint=tid)
            self.samples.append((seat, list(cards), tid))
            self._infer_host()
            if play:
                play = G.classify(cards, self.host, hint=tid) or play
            who = "我" if seat == self.seat else f"座{seat}"
            _log(f"  {who} 出 {G.TYPE_NAMES.get(tid, '?')} {G.cards_text(cards)}")
            self.last = play
            self.last_seat = seat
            self.passes = 0
            if seat == self.seat:
                self._drop(cards)
        else:
            self.passes += 1
            if self.passes >= 3:
                self.last = None
                self.last_seat = None
                self.passes = 0
        if nxt == self.seat:
            self.act()

    def _drop(self, cards):
        for c in cards:
            if c in self.hand:
                self.hand.remove(c)

    def act(self):
        if self.probe or not self.hand:
            return
        if time.time() - self.acted_at < 0.5:      # 同一次轮次别重复出手
            return
        self.acted_at = time.time()

        if self.last is None:
            if self.host_ok:
                pick = G.choose_lead(self.hand, self.host)
            else:
                # 级牌未知时只出最小单张 —— 首出任何牌都合法，不会被打回
                low = min(self.hand, key=lambda v: (G.rank_of(v), _suit_key(v)))
                pick = G.Play(G.T_SINGLE, G.rank_of(low), [low])
        else:
            partner = (self.last_seat == self._partner())
            if self.host_ok:
                pick = G.choose_follow(self.hand, self.last, self.host, partner=partner)
            else:
                pick = None          # 级牌没确定之前一律不跟，宁可不出
        if pick is None:
            self.ws.send_text(frame_pass())
            _log("  → 不出")
            return
        pos = _positions(self.hand, pick.cards)
        if pos is None:
            self.ws.send_text(frame_pass())
            _log("  → 不出（手牌对不上，保守过牌）")
            return
        self.ws.send_text(frame_play(pick.type, list(pick.cards), pos))
        _log(f"  → {pick.name} {G.cards_text(pick.cards)}")
        self._drop(list(pick.cards))
        self.last = pick
        self.last_seat = self.seat
        self.passes = 0

    def _partner(self):
        return {1: 3, 2: 4, 3: 1, 4: 2}.get(self.seat, 0)

    # -------------------------------------------------------------- 主循环
    def run(self):
        host = self.ls_login()
        self.gs_connect(host)
        mid = self.pick_match()
        if not mid:
            _log("没拿到场次，退出")
            return 2
        if self.login_only:
            _log("--login-only：登录链验证完毕，不进场")
            self.ws.close()
            return 0
        self.join(mid)

        last_hb = 0
        idle_since = time.time()
        while True:
            if self.deadline and time.time() > self.deadline:
                _log("到时间了，收工")
                break
            if time.time() - last_hb > 3:
                try:
                    self._heartbeat()
                except WS.WSError:
                    raise
                last_hb = time.time()
            try:
                msg = self.ws.recv_text(timeout=1.0)
            except WS.WSError as exc:
                _log(f"连接断了：{exc}")
                break
            if msg is None:
                if self.probe and time.time() - idle_since > 90:
                    _log("probe：90 秒没动静，收工")
                    break
                continue
            idle_since = time.time()
            obj = _parse(msg)
            if not obj:
                continue
            self.dispatch(obj)
        if self.ws:
            self.ws.close()
        return 0

    def dispatch(self, obj):
        if "res" in obj:
            kv = pairs(obj.get("vals") or [])
            if obj.get("err"):
                _log(f"!! 请求失败 res={obj.get('res')} err={obj.get('err')} "
                     f"msg={obj.get('msg')} {str(kv)[:160]}")
            return
        if obj.get("evt") != MSG_GAME:
            return
        kv = pairs(obj.get("vals") or [])
        inner = kv.get(6)
        if not isinstance(inner, dict):
            return
        cat = gv(inner, 2)
        op = gv(inner, 3)
        if isinstance(op, list):
            return
        if cat == GD_ROOM:
            if op == OpGd["Deal"]:
                raw = gv(inner, 7)
                cards = json.loads(raw) if isinstance(raw, str) else raw
                if cards:
                    self.on_deal(cards)
            elif op == OpGd["Action"]:
                self.on_action(inner)
            elif op == OpGd["Result"]:
                _log(f"本局结算：{json.dumps(inner, ensure_ascii=False)[:200]}")
                self.dealt = False
            elif op == OpGd["Phase"]:
                _log(f"阶段 -> {gv(inner, 4)}")
            elif op == OpGd["Join"]:
                self.room_field = inner
                _log("进桌成功，等发牌…")
        elif cat == GD_MATCH and op in (13, 5):
            pass        # 房间信息/场次列表，已单独处理


# ------------------------------------------------------------------ 工具

def _frame(obj):
    body = "~j~" + json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return f"~m~{len(body)}~m~{body}"


def _parse(text):
    if "~j~" not in text:
        return None
    try:
        return json.loads(text.split("~j~", 1)[1])
    except Exception:
        return None


def _suit_key(v):
    s = G.suit_of(v)
    return 9 if s < 0 else s


def _hex32():
    return "".join(random.choice("0123456789abcdef") for _ in range(32))


def _mask_of(ext_text):
    try:
        ext = json.loads(ext_text)
    except Exception:
        return ""
    m = ext.get("msisdn") or ""
    return m if "*" in m else ""


def _positions(hand, cards):
    """把要出的牌映射成手牌下标；对不上返回 None。"""
    pool = list(hand)
    out = []
    for c in cards:
        try:
            i = pool.index(c)
        except ValueError:
            return None
        out.append(i)
        pool[i] = None
        # 保持与真实下标一致：不能 pop，只能标记
    return out


def _pick_from_list(raw):
    """从场次列表里挑一个当前时段在开的掼蛋日赛，返回场次 id。"""
    if not raw:
        return 0
    try:
        blob = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return 0
    names = blob[1] if isinstance(blob, list) and len(blob) > 1 else []
    if not isinstance(names, list):
        return 0
    # 每 14 个字段一条记录，第 0 个是 mK<日期>_<场次id>
    recs = []
    cur = []
    for i, item in enumerate(names):
        nxt = names[i + 1] if i + 1 < len(names) else ""
        starts = (isinstance(item, str) and item.startswith("mK")
                  and "_" in item and isinstance(nxt, str)
                  and ("赛" in nxt or "掼蛋" in nxt))
        if starts:
            if cur:
                recs.append(cur)
            cur = [item]
        elif cur:
            cur.append(item)
    if cur:
        recs.append(cur)

    now = time.localtime()
    minutes = now.tm_hour * 60 + now.tm_min
    fallback = 0
    for r in recs:
        code = r[0]
        try:
            mid = int(code.rsplit("_", 1)[1])
        except Exception:
            continue
        title = " ".join(x for x in r[1:4] if isinstance(x, str))
        window = next((x for x in r if _WINDOW_RE.match(x or "")), "")
        if "掼蛋" not in title:
            continue
        fallback = fallback or mid
        span = _parse_window(window)
        if span and span[0] <= minutes < span[1]:
            return mid
    return fallback


_WINDOW_RE = __import__("re").compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def _parse_window(text):
    m = _WINDOW_RE.match(text or "")
    if not m:
        return None
    a = int(m.group(1)) * 60 + int(m.group(2))
    b = int(m.group(3)) * 60 + int(m.group(4))
    return (a, b if b > a else 24 * 60)


# ------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="咪咕掼蛋自动打牌")
    ap.add_argument("--probe", action="store_true", help="只登录进桌，不出牌")
    ap.add_argument("--login-only", action="store_true",
                    help="只验证登录链（不会占用比赛次数）")
    ap.add_argument("--minutes", type=int, default=0, help="最多打多少分钟，0=不限")
    ap.add_argument("--host", type=int, default=0, help="手动指定级牌 2..14")
    ap.add_argument("--seat", type=int, default=0, help="手动指定座位 1..4")
    args = ap.parse_args(argv)

    auth = load_auth()
    if not auth:
        print(f"没有掼蛋凭证。请先抓一次包（含 gd-api.migufun.com/api/login）"
              f"发到邮箱，让 har_import 写 {AUTH_FILE.name}")
        return 2

    left = auth_left_days(auth)
    if left is not None and left <= 0:
        print(f"凭证已过期 {abs(left):.1f} 天，请重新抓包")
        return 2
    if left is not None:
        _log(f"凭证剩余 {left:.1f} 天")

    bot = Bot(auth, probe=args.probe, host=args.host, minutes=args.minutes,
              login_only=args.login_only)
    if args.seat:
        bot.seat = args.seat
    try:
        bot.http_login()
        return bot.run()
    except KeyboardInterrupt:
        _log("手动中断")
        return 0
    except Exception as exc:
        _log(f"出错：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
