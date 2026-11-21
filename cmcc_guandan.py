"""cmcc_guandan.py —— 掼蛋对局协议：抓包回放、牌谱还原、出牌建议。

协议全部逆向自客户端 `assets/scripts/index.js` 与真实抓包（2026-11-21/22），
细节见 docs/guandan.md。这里只做「读 + 算」，不联网。

用法：
    python cmcc_guandan.py --har xxx.har --playbook      # 还原牌谱
    python cmcc_guandan.py --har xxx.har --advise        # 每手给出 AI 建议对照
    python cmcc_guandan.py --har xxx.har --dump-frames   # 原始事件帧
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import guandan_rules as G

# ------------------------------------------------------------------ 协议常量

MSG_GAME = 75          # 顶层消息号：请求 req:75 / 下行 evt:75
GD_ROOM = 18           # 协议组：房间 / 牌局
GD_MATCH = 19          # 协议组：赛事匹配（日赛走这个）

#: 牌局操作（协议组 18）
OpGd = {
    "Join": 1, "Regain": 2, "Leave": 3, "RoomList": 4, "Ready": 5, "Deal": 6,
    "Phase": 7, "Reset": 8, "Next": 9, "Action": 10, "GameInfo": 11,
    "Waiting": 12, "Result": 13, "WaitLeave": 14, "CashUp": 15, "RoundSet": 16,
    "Currency": 17, "WillBegin": 18, "Chat": 19, "Double": 20, "UpGold": 21,
    "UserInfo": 22, "EmoAnim": 23, "Tuan": 24, "Stand": 25, "Sitdown": 26,
    "ShowReady": 27, "RoomCount": 28, "Trustee": 29, "Dissolve": 30,
    "Continue": 31, "Gong": 32, "LiPai": 33, "Surplus": 34, "SwitchSeat": 35,
    "Dis": 36, "Dissvote": 37, "RoomResult": 38, "SortPai": 39, "Flush": 40,
    "Look": 41,
}

#: 赛事匹配操作（协议组 19）
OpGm = {
    "Join": 1, "Rise": 2, "Leave": 3, "Game": 4, "List": 5, "TopList": 6,
    "Reason": 7, "His": 8, "RoomHis": 9, "Replay": 10, "Rule": 12, "Waiting": 13,
}

#: 牌桌上的动作（OpGd.Action 的第 4 个字段）
eGdAct = {"None": 0, "Fanout": 1, "Double": 2, "Gong": 3, "Huan": 4, "Hint": 5}

#: 局内阶段
eGPhase = {
    "Wait": 0, "Ready": 1, "Double": 2, "Deal": 3, "Tuan": 4, "Gong": 5,
    "Anti": 6, "Huan": 7, "HEnd": 8, "Fanout": 9, "Result": 10, "ShowReady": 11,
    "Dissolve": 12, "Reset": 13, "RoomResult": 14, "End": 20,
}

#: 掼蛋牌型编号（出牌请求第 5 字段）
eGdType = {
    "s_0": 0, "s_1": 1, "s_2": 2, "s_3": 3, "s_32": 4, "s_shun": 5, "s_lian": 6,
    "s_gan": 7, "s_4": 8, "s_5": 9, "s_Flush": 10, "s_6": 11, "s_7": 12,
    "s_4_2": 13, "s_8": 14, "s_9": 15, "s_4_3": 16, "s_T": 17, "s_4_4": 18,
    "s_4_5": 19, "s_k4": 20,
}


# ------------------------------------------------------------------ 帧解析

def _split_photon(text: str):
    """把 `~m~<长度>~m~<载荷>` 拆成载荷列表（一个 text 里可能粘了多帧）。"""
    out = []
    i = 0
    n = len(text)
    while i < n:
        if not text.startswith("~m~", i):
            break
        j = text.find("~m~", i + 3)
        if j < 0:
            break
        try:
            size = int(text[i + 3:j])
        except ValueError:
            break
        start = j + 3
        out.append(text[start:start + size])
        i = start + size
    return out


def _json_payload(chunk: str):
    if "~j~" not in chunk:
        return None
    try:
        return json.loads(chunk.split("~j~", 1)[1])
    except Exception:
        return None


def gv(d, key, default=None):
    """取字段。线上 JSON 对象键是字符串，`vals` 展开后是数字，两种都要兼容。"""
    if not isinstance(d, dict):
        return default
    if key in d:
        return d[key]
    return d.get(str(key), default)


def pairs(seq):
    """协议里 `vals` 是 [键,值,键,值,...] 的扁平数组。"""
    if not isinstance(seq, list):
        return {}
    return {seq[i]: seq[i + 1] for i in range(0, len(seq) - 1, 2)}


def iter_frames(msgs):
    """产出 (序号, 方向, 时间戳, 帧字典)。方向 '>' 上行，'<' 下行。"""
    for i, m in enumerate(msgs):
        payload = m.get("payload") or {}
        text = payload.get("text")
        if not text:
            continue
        flow = ">" if m.get("flow") == 0 else "<"
        ts = m.get("timestamp") or 0
        for chunk in _split_photon(text):
            obj = _json_payload(chunk)
            if isinstance(obj, dict):
                yield i, flow, ts, obj


def load_har_connections(path: Path):
    """返回 [(url, [帧, ...])]。只有 Reqable 这类会存 `_webSocketMessages`。"""
    raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    out = []
    for e in raw.get("log", {}).get("entries", []):
        msgs = e.get("_webSocketMessages")
        if not msgs:
            continue
        url = (e.get("request") or {}).get("url", "")
        out.append((url, msgs))
    return out


# ------------------------------------------------------------------ 牌局还原

class Table(object):
    """一局牌的状态：发牌、出牌流水、我的座位。"""

    def __init__(self):
        self.host = 2                 # 级牌点数
        self.base = 0                 # 底分
        self.deal = []                # 发到的 27 张（原始顺序）
        self.deal_field = None        # 发牌帧里的第 4 个字段（含义未定，不采信）
        self.violations = 0
        self.my_seat = None
        self.hands = {1: [], 2: [], 3: [], 4: []}
        self.events = []              # [{seat, play|None, kind}]
        self.history = []             # 每一墩：{"plays": [...], "winner": seat}
        self.advices = []             # 我每一手的 AI 建议
        self.trick = []
        self.last = None
        self.last_seat = None
        self.passes = 0

    # -- 只读属性
    @property
    def me(self):
        return self.my_seat or 1

    def partner(self, seat):
        return {1: 3, 2: 4, 3: 1, 4: 2}.get(seat, seat)

    def hand(self):
        return self.hands.get(self.me) or self.deal

    # -- 事件
    def on_deal(self, cards, host=None, base=None):
        self.deal = list(cards)
        if host:
            self.host = host
        if base:
            self.base = base
        self.hands[self.me] = list(cards)
        self.events.append({"kind": "deal", "cards": list(cards)})

    def on_play(self, seat, cards, type_id=None, mine=False, positions=None):
        cards = list(cards)
        play = G.classify(cards, self.host, hint=type_id) if cards else None
        if play is None and cards:
            # 服务端认了但本地认不出（新牌型/没见过的组合）：按服务端编号兜底
            play = G.Play(type_id if type_id is not None else G.T_SINGLE, 0, cards)
        # 更新我的手牌
        if mine or seat == self.me:
            self._drop_from_hand(cards, positions)
        if play is None:
            self.passes += 1
            self.events.append({"kind": "pass", "seat": seat})
            self.trick.append({"seat": seat, "play": None})
            if self.passes >= 3:
                self._close_trick()
            return
        if seat == self.last_seat:
            # 同一人连着出牌 = 其余三家都过了，上一墩其实已经结束
            self._close_trick()
        self.passes = 0
        self.last = play
        self.last_seat = seat
        self.events.append({"kind": "play", "seat": seat, "play": play})
        self.trick.append({"seat": seat, "play": play})

    def _close_trick(self):
        if self.trick:
            winner = None
            for item in reversed(self.trick):
                if item["play"]:
                    winner = item["seat"]
                    break
            self.history.append({"plays": list(self.trick), "winner": winner})
        self.trick = []
        self.last = None
        self.last_seat = None
        self.passes = 0

    def _drop_from_hand(self, cards, positions=None):
        hand = self.hands.get(self.me)
        if not hand:
            return
        if positions:
            # 位置下标可能相对当前手牌，先按值校验，不行再按值删
            try:
                picked = [hand[i] for i in positions]
                if sorted(picked) == sorted(cards):
                    for i in sorted(positions, reverse=True):
                        hand.pop(i)
                    return
            except IndexError:
                pass
        for c in cards:
            if c in hand:
                hand.remove(c)


def _scan_plays(msgs):
    """扫出所有下行出牌事件（座位 + 牌 + 服务端牌型编号），供推断级牌用。"""
    out = []
    for idx, flow, ts, obj in iter_frames(msgs):
        if obj.get("evt") != MSG_GAME:
            continue
        kv = pairs(obj.get("vals"))
        inner = kv.get(6)
        if not isinstance(inner, dict):
            continue
        if gv(inner, 2) != GD_ROOM or gv(inner, 3) != OpGd["Action"]:
            continue
        raw = gv(inner, 7)
        seat = gv(inner, 6)
        if not isinstance(raw, str) or seat is None:
            continue
        arr = json.loads(raw)
        n = arr[1] if len(arr) > 1 else 0
        if n:
            out.append((idx, seat, arr[2:2 + n], arr[2 + n] if len(arr) > 2 + n else None))
    return out


def infer_host(msgs, candidates=range(2, 15)):
    """暴力试 2..A 当级牌，取「出牌全部合法」且违规最少的那个。

    服务端下发级牌的字段还没定位，用对局本身的合法性反推最稳。
    """
    plays = _scan_plays(msgs)
    best_host, best_bad = 2, None
    for host in candidates:
        last, last_seat, bad = None, None, 0
        for _, seat, cards, tid in plays:
            p = G.classify(cards, host, hint=tid)
            if p is None:
                bad += 1
                continue
            if seat != last_seat and last is not None and not G.can_over(p, last):
                bad += 1
            last, last_seat = p, seat
        if best_bad is None or bad < best_bad:
            best_host, best_bad = host, bad
    return best_host, best_bad or 0


def replay(msgs, host=None):
    """把一局的所有帧吃成 Table。host 为 None 时自动推断级牌。

    只认服务端回显：我自己发出的帧先挂起，等下行的同一手牌回来才入账。
    这样抓到「客户端自说自话（断线重连时疯狂重发）」的包也不会算错。
    """
    t = Table()
    if host:
        t.host = host
    else:
        t.host, _ = infer_host(msgs)
    pending = None        # (kind, cards) —— 我刚发出、还没被回显的动作
    dropped = 0
    for idx, flow, ts, obj in iter_frames(msgs):
        if "req" in obj:
            kv = pairs(obj.get("vals"))
            if kv.get(1) == GD_MATCH and kv.get(2) == OpGm["Game"] \
                    and kv.get(3) == OpGd["Action"] \
                    and kv.get(4) == eGdAct["Fanout"]:
                cards = kv.get(6) or []
                if pending:
                    dropped += 1
                pending = ("play", list(cards)) if cards else ("pass", [])
                if cards:
                    adv = G.choose_follow(t.hand(), t.last, t.host,
                                          partner_left=len(t.hands[t.partner(t.me)]))
                    t.advices.append({"frame": idx, "cards": list(cards), "suggest": adv})
            continue

        if obj.get("evt") != MSG_GAME:
            continue
        kv = pairs(obj.get("vals"))
        inner = kv.get(6)
        if not isinstance(inner, dict):
            continue
        cat = gv(inner, 2)
        op = gv(inner, 3)
        if cat != GD_ROOM:
            continue

        if op == OpGd["Deal"]:
            raw = gv(inner, 7)
            cards = json.loads(raw) if isinstance(raw, str) else raw
            if not cards:
                continue
            # 第 4 个字段不是级牌（实测打 5 时这里是 2），只记录不采信
            t.deal_field = gv(inner, 4)
            t.on_deal(cards, base=gv(inner, 5))
        elif op == OpGd["Action"]:
            seat = gv(inner, 6)
            raw = gv(inner, 7)
            if not isinstance(raw, str) or seat is None:
                continue
            arr = json.loads(raw)
            n = arr[1] if len(arr) > 1 else 0
            cards = arr[2:2 + n]
            mine = False
            if pending:
                if pending[0] == "play" and sorted(cards) == sorted(pending[1]):
                    t.my_seat = seat
                    mine = True
                    pending = None
                elif pending[0] == "pass" and not cards:
                    t.my_seat = seat
                    mine = True
                    pending = None
            if not cards:
                t.on_play(seat, [])
            else:
                t.on_play(seat, cards, type_id=gv(inner, 8) if False else (arr[2 + n] if len(arr) > 2 + n else None),
                          mine=mine or seat == t.my_seat,
                          positions=None)
    if t.trick:
        t._close_trick()
    t.dropped = dropped
    return t


# ------------------------------------------------------------------ 输出

def _seat_name(t, seat):
    if seat == t.me:
        return f"我(座{seat})"
    if seat == t.partner(t.me):
        return f"队友(座{seat})"
    return f"对手(座{seat})"


def dump_playbook(t: Table):
    print(f"级牌 {G.RANK_NAMES[t.host - 2]}（推断）   底分 {t.base}   我的座位 {t.me}")
    if t.deal:
        srt = sorted(t.deal, key=lambda v: (-G.rank_of(v), G.suit_of(v) if G.suit_of(v) >= 0 else 9))
        print(f"发牌 {len(t.deal)} 张: {G.cards_text(srt)}")
    print()
    n = 0
    for trick in t.history:
        n += 1
        print(f"—— 第 {n} 墩 ——")
        for item in trick["plays"]:
            if item["play"] is None:
                print(f"  {_seat_name(t, item['seat']):<10} 不出")
            else:
                p = item["play"]
                print(f"  {_seat_name(t, item['seat']):<10} {p.name:<5} {G.cards_text(p.cards)}")
        if trick["winner"]:
            print(f"  → 赢墩: {_seat_name(t, trick['winner'])}")
    if t.trick:
        print("—— 未结束的一墩 ——")
        for item in t.trick:
            if item["play"] is None:
                print(f"  {_seat_name(t, item['seat']):<10} 不出")
            else:
                print(f"  {_seat_name(t, item['seat']):<10} {item['play'].name:<5} {G.cards_text(item['play'].cards)}")
    print(f"\n共 {len(t.history)} 墩 / {len(t.events)} 个事件")


def dump_advice(t: Table):
    print("我的每一手：实际 vs AI 建议")
    print("-" * 56)
    same = 0
    for i, a in enumerate(t.advices, 1):
        act = G.classify(a["cards"], t.host) if a.get("cards") else None
        sug = a["suggest"]
        act_txt = "不出" if act is None else f"{act.name} {G.cards_text(act.cards)}"
        sug_txt = "不出" if sug is None else f"{sug.name} {G.cards_text(sug.cards)}"
        mark = "  " if act_txt == sug_txt else "≠ "
        if act_txt == sug_txt:
            same += 1
        print(f"{i:2}. 实际 {act_txt:<28}{mark}建议 {sug_txt}")
    if not t.advices:
        print("这局没有捕捉到我的出牌（可能只是旁观/未轮到我）")
    elif not t.deal:
        print("\n这个抓包没带发牌帧（中途才连上），只能看到我出了什么，给不出建议")
    else:
        print(f"\n一致 {same}/{len(t.advices)}")


def dump_frames(msgs):
    for idx, flow, ts, obj in iter_frames(msgs):
        kv = pairs(obj.get("vals"))
        inner = kv.get(6) if isinstance(kv.get(6), dict) else kv
        print(f"[{idx:5}] {flow} evt={obj.get('evt')} req={obj.get('req')} "
              f"cat={gv(inner, 1) or gv(inner, 2)} op={gv(inner, 3)} "
              f"{json.dumps(inner, ensure_ascii=False)[:180]}")


# ------------------------------------------------------------------ 组帧（为客户端准备）

def _frame(obj) -> str:
    body = "~j~" + json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return f"~m~{len(body)}~m~{body}"


def frame_login(uid, u, cat, ver=30009, channel="1301"):
    """登录游戏服（req:16）。uid/u/cat 从 api/login 的响应里取。"""
    vals = [1, uid, 2, u, 3, 2, 4, cat, 5, ver, 6, 0, 7, "migufun_w", 8, "",
            9, 0, 12, 1, 13, channel, 22, str(uid), 42, "08", 29, 0, 30, "0",
            32, 4, 33, 1, 38, 1, 39, 1, 40, [1, 1, 0, 1, 1]]
    return _frame({"req": 16, "vals": vals})


def frame_list():
    """拉场次列表（OpGm.List）。"""
    return _frame({"req": MSG_GAME,
                   "vals": [1, GD_MATCH, 2, OpGm["List"], 3, 0, 18, [0, 1]]})


def frame_ready(match_id=6):
    """准备/加入匹配。"""
    return _frame({"req": MSG_GAME,
                   "vals": [1, GD_MATCH, 2, OpGm["Rise"], 3, match_id, 18, [0, 1]]})


def frame_join(uid, room, match_id, table, seat=0):
    return _frame({"req": MSG_GAME,
                   "vals": [1, GD_MATCH, 2, OpGm["Join"],
                            3, [uid, room, match_id, table, 0, seat]]})


def frame_play(type_id, cards, positions):
    """出牌。type_id 用 guandan_rules 里 classify 的结果 p.type。"""
    return _frame({"req": MSG_GAME,
                   "vals": [1, GD_MATCH, 2, OpGm["Game"], 3, OpGd["Action"],
                            4, eGdAct["Fanout"], 5, type_id,
                            6, list(cards), 7, list(positions), 18, [1, 5]]})


def frame_pass():
    """不出牌。"""
    return _frame({"req": MSG_GAME,
                   "vals": [1, GD_MATCH, 2, OpGm["Game"], 3, OpGd["Action"],
                            4, eGdAct["Fanout"], 5, 0, 6, [], 7, [], 18, [1, 5]]})


def frame_continue(seat=0, flag=0):
    return _frame({"req": MSG_GAME,
                   "vals": [1, GD_MATCH, 2, OpGm["Game"], 3, OpGd["Continue"],
                            4, seat, 5, flag, 18, [1, 3]]})


# ------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="掼蛋抓包解析 / 牌谱还原")
    ap.add_argument("--har", required=True)
    ap.add_argument("--playbook", action="store_true", help="还原牌谱")
    ap.add_argument("--advise", action="store_true", help="对照 AI 建议")
    ap.add_argument("--dump-frames", action="store_true", help="打印事件帧")
    ap.add_argument("--host", type=int, default=0, help="级牌点数 2..14，缺省自动推断")
    ap.add_argument("--url-filter", default="gd-gs3")
    args = ap.parse_args(argv)

    har = Path(args.har)
    if not har.exists():
        print(f"文件不存在: {har}")
        return 1
    conns = load_har_connections(har)
    if not conns:
        print("这个 HAR 没有 WebSocket 帧（ProxyPin 不存帧，要用 Reqable）。")
        return 1
    target = [(u, m) for u, m in conns if args.url_filter in u] or conns

    for url, msgs in target:
        print(f"=== {url}  {len(msgs)} 帧 ===")
        if args.dump_frames:
            dump_frames(msgs)
            continue
        t = replay(msgs, host=args.host or None)
        if args.advise:
            dump_advice(t)
        else:
            dump_playbook(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
