"""掼蛋对局协议解析（逆向自 Reqable 抓包，2026-11-21）。

目前只做「读」：把 HAR 里的 WebSocket 帧解析成可读的牌局记录。
协议细节见 docs/guandan.md。

用法：
    python cmcc_guandan.py --har path/to/xxx.har --dump-plays
    python cmcc_guandan.py --har path/to/xxx.har --dump-frames
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent

# 索引按顺子序：A 在最低位，K 最高
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["?", "?", "?", "?"]        # 花色名待 UI 对照，暂用组号


def decode_card(v: int):
    """牌值 → {suit, rank}。非法值返回 None。

    编码：value = 34 + 32*组 + 索引，索引按顺子序 0=A..12=K。
    """
    if not isinstance(v, int) or v < 34:
        return None
    g, i = divmod(v - 34, 32)
    if g > 3 or i > 12:
        return None
    return {"suit": g, "rank": RANKS[i], "raw": v}


def card_text(v: int) -> str:
    c = decode_card(v)
    if not c:
        # 小于 34 的是王：不参与顺子序，单独编码。
        # 实测 17 能压 A(66)，所以是王；大小王具体取值待更多样本确认。
        if isinstance(v, int) and 0 < v < 34:
            return f"王({v})"
        return f"?{v}"
    return f"{SUITS[c['suit']]}{c['rank']}" if c["suit"] < len(SUITS) else f"组{c['suit']}{c['rank']}"


def parse_frame(payload) -> dict | None:
    """把 ~m~len~m~~j~{...} 还原成 dict。"""
    if not isinstance(payload, dict):
        return None
    text = payload.get("text") or ""
    if "~j~" not in text:
        return None
    try:
        return json.loads(text.split("~j~", 1)[1])
    except Exception:
        return None


def load_ws_frames(har_path: Path):
    """返回 [(url, [frame, ...])]。只取含 _webSocketMessages 的条目。"""
    raw = json.loads(har_path.read_text(encoding="utf-8", errors="replace"))
    out = []
    for e in raw.get("log", {}).get("entries", []):
        msgs = e.get("_webSocketMessages")
        if not msgs:
            continue
        url = (e.get("request") or {}).get("url", "")
        out.append((url, msgs))
    return out


def iter_events(msgs):
    """逐个产出 (帧序号, 事件字典)。"""
    for idx, m in enumerate(msgs):
        obj = parse_frame(m.get("payload"))
        if isinstance(obj, dict) and "evt" in obj:
            yield idx, obj


def dump_plays(msgs):
    """打印所有出牌记录。"""
    rows = []
    for idx, obj in iter_events(msgs):
        vals = obj.get("vals") or []
        if len(vals) < 4 or not isinstance(vals[3], dict):
            continue
        d = vals[3]
        if d.get("3") != 10 or not isinstance(d.get("7"), str):
            continue
        try:
            arr = json.loads(d["7"])
        except Exception:
            continue
        n = arr[1] if len(arr) > 1 else 0
        cards = arr[2:2 + n] if isinstance(n, int) else []
        rows.append({"frame": idx, "seat": d.get("6"), "count": n, "cards": cards})

    for r in rows:
        if r["count"] == 0:
            print(f"[{r['frame']:5}] 座位{r['seat']}  过牌")
            continue
        txt = " ".join(card_text(c) for c in r["cards"])
        print(f"[{r['frame']:5}] 座位{r['seat']}  {r['count']}张  {txt}")
    print(f"\n共 {len(rows)} 手（含过牌）")


def dump_frames(msgs):
    """打印所有事件帧的原始结构，便于继续逆向。"""
    for idx, obj in iter_events(msgs):
        vals = obj.get("vals") or []
        biz = vals[1] if len(vals) > 1 else None
        body = vals[3] if len(vals) > 3 else None
        print(f"[{idx:5}] evt={obj.get('evt')} biz={biz} {json.dumps(body, ensure_ascii=False)[:300]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="掼蛋抓包解析")
    ap.add_argument("--har", required=True, help="Reqable 导出的 HAR（必须含 WebSocket 帧）")
    ap.add_argument("--dump-plays", action="store_true", help="打印出牌记录")
    ap.add_argument("--dump-frames", action="store_true", help="打印全部事件帧")
    ap.add_argument("--url-filter", default="gd-gs3", help="只解析 URL 含此串的连接")
    args = ap.parse_args(argv)

    har = Path(args.har)
    if not har.exists():
        print(f"文件不存在: {har}")
        return 1

    conns = load_ws_frames(har)
    if not conns:
        print("这个 HAR 里没有 WebSocket 帧。ProxyPin 导出的 HAR 不存帧，要用 Reqable。")
        return 1

    target = [(u, m) for u, m in conns if args.url_filter in u] or conns
    for url, msgs in target:
        print(f"=== {url}  共 {len(msgs)} 帧 ===")
        if args.dump_plays:
            dump_plays(msgs)
        elif args.dump_frames:
            dump_frames(msgs)
        else:
            evts = sum(1 for _ in iter_events(msgs))
            print(f"  事件帧 {evts} 条；用 --dump-plays 或 --dump-frames 看内容")
    return 0


if __name__ == "__main__":
    sys.exit(main())
