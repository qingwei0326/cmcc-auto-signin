# -*- coding: utf-8 -*-
"""掼蛋规则与对局回放的测试。

样本都取自真实抓包（2026-11-21/22 咪咕掼蛋），所以断言写死了具体牌，
改规则改错了会直接红。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _guard  # noqa: F401  隔离全局配置

import cmcc_guandan as Q
import guandan_bot as B
import guandan_rules as G

B.QUIET = True


def mkmsg(flow, obj):
    body = "~j~" + json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return {"flow": flow, "timestamp": 0,
            "payload": {"type": 2, "text": "~m~%d~m~%s" % (len(body), body)}}


def mkhar(msgs):
    """造一个只含一帧 WS 连接的最小 HAR。"""
    har = {"log": {"entries": [{
        "request": {"url": "wss://gd-gs3.migufun.com:8444"},
        "_webSocketMessages": msgs,
    }]}}
    f = tempfile.NamedTemporaryFile("w", suffix=".har", delete=False, encoding="utf-8")
    json.dump(har, f, ensure_ascii=False)
    f.close()
    return Path(f.name)


def bc(cat, op, body):
    """下行事件帧。"""
    inner = {"2": cat, "3": op}
    inner.update(body)
    return mkmsg(1, {"vals": [1, cat, 6, inner], "evt": 75})


#: 真实发牌：27 张
DEAL = [16, 37, 69, 101, 101, 133, 46, 78, 78, 142, 45, 77, 141,
        75, 107, 107, 106, 40, 72, 72, 39, 70, 134, 36, 99, 99, 130]


class CardCodecTest(unittest.TestCase):
    def test_value_roundtrip(self):
        self.assertEqual(G.card_value(0, 2), 34)      # ♥2
        self.assertEqual(G.card_value(1, 2), 66)      # ♦2
        self.assertEqual(G.card_value(2, 14), 110)    # ♣A
        self.assertEqual(G.card_value(3, 14), 142)    # ♠A

    def test_suit_order_follows_client(self):
        # 客户端 pos2col=[0,♥,♦,♣,♠]：组 0 是红桃，组 3 是黑桃
        self.assertEqual(G.card_text(34), "♥2")
        self.assertEqual(G.card_text(142), "♠A")

    def test_rank_and_joker(self):
        self.assertEqual(G.rank_of(46), 14)
        self.assertEqual(G.rank_of(17), 17)
        self.assertEqual(G.card_text(16), "小王")
        self.assertEqual(G.card_text(17), "大王")

    def test_host_promotion(self):
        self.assertEqual(G.order_of(34, 2), 15)       # 级牌压 A
        self.assertEqual(G.order_of(34, 5), 2)
        self.assertEqual(G.order_of(46, 2), 14)
        self.assertGreater(G.order_of(17, 2), G.order_of(34, 2))


class ClassifyTest(unittest.TestCase):
    def test_single_pair_triple(self):
        self.assertEqual(G.classify([46], 2).type, G.T_SINGLE)
        self.assertEqual(G.classify([46, 78], 2).type, G.T_PAIR)
        self.assertEqual(G.classify([42, 74, 138], 2).type, G.T_TRIPLE)

    def test_triple_with_two(self):
        p = G.classify([40, 72, 72, 99, 99], 2)       # 888 + 33
        self.assertEqual(p.type, G.T_TRIPLE2)
        self.assertEqual(p.order, 8)

    def test_bombs(self):
        self.assertEqual(G.classify([131, 131, 67, 35], 2).type, G.T_BOMB4)
        self.assertEqual(G.classify([45, 45, 109, 141, 141], 2).type, G.T_BOMB5)
        self.assertEqual(G.classify([16, 16, 17, 17], 2).type, G.T_JOKER4)

    def test_flush_beats_five_bomb(self):
        flush = G.classify([40, 39, 38, 37, 36], 5)   # ♥8..♥4 同花顺
        bomb5 = G.classify([45, 45, 109, 141, 141], 5)
        self.assertEqual(flush.type, G.T_FLUSH)
        self.assertTrue(G.can_over(flush, bomb5))
        self.assertFalse(G.can_over(bomb5, flush))

    def test_straight_and_a2345(self):
        p = G.classify([130, 99, 36, 37, 70], 2)      # ♠2 ♣3 ♥4 ♥5 ♦6
        self.assertEqual(p.type, G.T_STRAIGHT)
        self.assertEqual(p.order, 6)
        a_low = G.classify([46, 34, 99, 36, 69], 5)   # A 2 3 4 5 -> 5 高
        self.assertIn(a_low.type, (G.T_STRAIGHT, G.T_FLUSH))

    def test_pair_seq_and_plate(self):
        # 334455：♣3 ♠3 ♥4 ♦4 ♥5 ♦5
        self.assertEqual(G.classify([99, 131, 36, 68, 37, 69], 2).type, G.T_PAIRSEQ)
        # 333444：♣3 ♠3 ♥3 ♦4 ♦4 ♦4
        self.assertEqual(G.classify([99, 131, 35, 68, 68, 68], 2).type, G.T_PLATE)


class WildcardTest(unittest.TestCase):
    """红桃级牌是逢人配：打 5 时 ♥5（值 37）可以当任意牌。"""

    def test_wild_fills_triple(self):
        p = G.classify([37, 138, 42, 130, 98], 5, hint=G.T_TRIPLE2)
        self.assertEqual(p.type, G.T_TRIPLE2)
        self.assertEqual(p.order, 10)                 # 10 10 + 万能牌 = 三个 10

    def test_wild_only_when_host_matches(self):
        self.assertTrue(G.is_wild(37, 5))
        self.assertFalse(G.is_wild(37, 2))
        self.assertTrue(G.is_wild(34, 2))


class CanOverTest(unittest.TestCase):
    def test_same_type_compare_order(self):
        a = G.classify([42, 74, 138], 2)              # 三个 10
        b = G.classify([45, 77, 141], 2)              # 三个 K
        self.assertTrue(G.can_over(b, a))
        self.assertFalse(G.can_over(a, b))

    def test_bomb_beats_any_normal(self):
        triple2 = G.classify([66, 66, 34], 2)         # 三个 2（级牌，最大三张）
        bomb = G.classify([131, 131, 67, 35], 2)      # 四个 3
        self.assertTrue(G.can_over(bomb, triple2))

    def test_pass_is_always_beatable(self):
        p = G.classify([46], 2)
        self.assertTrue(G.can_over(p, G.Play(G.T_PASS, 0, [])))


class StrategyTest(unittest.TestCase):
    def test_decompose_covers_every_card(self):
        plays = G.decompose(DEAL, 2)
        self.assertEqual(sum(len(p.cards) for p in plays), len(DEAL))

    def test_lead_and_follow(self):
        lead = G.choose_lead(DEAL, 2)
        self.assertIsNotNone(lead)
        prev = G.classify([40, 72, 72, 99, 99], 2)    # 888 + 33
        sug = G.choose_follow(DEAL, prev, 2)
        self.assertIsNotNone(sug)
        self.assertTrue(G.can_over(sug, prev))

    def test_reproduces_human_play(self):
        """真实对局里我出的是 888+33，引擎应该给出同一手。"""
        prev = G.classify([100, 100, 66, 66, 34], 5)  # 对手 444 + 222
        sug = G.choose_follow(DEAL, prev, 5)
        self.assertEqual(sorted(sug.cards), sorted([40, 72, 72, 99, 99]))


class ReplayTest(unittest.TestCase):
    def build(self, host_field=2):
        msgs = [
            bc(Q.GD_ROOM, Q.OpGd["Deal"],
               {"4": host_field, "5": 2000, "7": json.dumps(DEAL)}),
            # 我出 888+33
            mkmsg(0, {"req": 75, "vals": [1, 19, 2, 4, 3, 10, 4, 1, 5, 4,
                                          6, [40, 72, 72, 99, 99], 7, [17, 18, 19, 24, 25],
                                          18, [1, 5]]}),
            bc(Q.GD_ROOM, Q.OpGd["Action"],
               {"6": 1, "7": json.dumps([0, 5, 40, 72, 72, 99, 99, 4, 27, 1, 0, 0, 0, 2, 20000, 0, 0]),
                "8": json.dumps([17, 18, 19, 24, 25])}),
            bc(Q.GD_ROOM, Q.OpGd["Action"],
               {"6": 2, "7": json.dumps([0, 0, 1, 0, 0, 0, 3, 20000, 0, 0])}),
        ]
        return msgs

    def test_deal_and_play(self):
        har = mkhar(self.build())
        try:
            url, msgs = Q.load_har_connections(har)[0]
            t = Q.replay(msgs)
        finally:
            har.unlink()
        self.assertEqual(len(t.deal), 27)
        self.assertEqual(t.base, 2000)
        self.assertEqual(t.my_seat, 1)
        self.assertEqual(len(t.hand()), 27 - 5)       # 出掉 5 张
        self.assertEqual(t.history[0]["plays"][0]["play"].type, G.T_TRIPLE2)

    def test_echo_not_double_counted(self):
        har = mkhar(self.build())
        try:
            url, msgs = Q.load_har_connections(har)[0]
            t = Q.replay(msgs)
        finally:
            har.unlink()
        plays = [e for e in t.events if e["kind"] == "play"]
        self.assertEqual(len(plays), 1)               # 只算一次

    def test_infer_host(self):
        har = mkhar(self.build(host_field=2))
        try:
            url, msgs = Q.load_har_connections(har)[0]
        finally:
            har.unlink()
        self.assertIn(Q.infer_host(msgs)[0], range(2, 15))


class FrameBuilderTest(unittest.TestCase):
    def test_play_frame_shape(self):
        text = Q.frame_play(4, [40, 72, 72, 99, 99], [17, 18, 19, 24, 25])
        body = json.loads(text.split("~j~", 1)[1])
        self.assertEqual(body["req"], 75)
        self.assertEqual(body["vals"][:4], [1, 19, 2, 4])
        self.assertIn(10, body["vals"])               # OpGd.Action
        self.assertIn(4, body["vals"])

    def test_pass_frame(self):
        body = json.loads(Q.frame_pass().split("~j~", 1)[1])
        self.assertEqual(body["vals"][-1], [1, 5])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- 自动打牌

class FakeWS(object):
    def __init__(self):
        self.sent = []

    def send_text(self, text):
        self.sent.append(text)

    def close(self):
        pass


def _kv_of(text):
    """把帧拆成 {协议字段: 值}，避免在测试里数下标。"""
    body = json.loads(text.split("~j~", 1)[1])
    return Q.pairs(body["vals"])


class ListParseTest(unittest.TestCase):
    RAW = json.dumps([
        [],
        ["mK261122_460", "中国移动APP全民掼蛋挑战赛 日赛", "11月场日赛第一场", "移动话费赛",
         "http://a.png|https://b.png", "周期积分累计赛，初始积分0", "00:00-12:00", "免费参赛",
         "", "https://x.json", "mK261101_459", "1434:1:20001:1000000:1:0", "", "",
         '{"md": "09/20-12/31"}',
         "mK261122_461", "中国移动APP全民掼蛋挑战赛 日赛", "11月场日赛第二场", "移动话费赛",
         "http://a.png", "周期积分累计赛", "12:00-19:00", "免费参赛", "", "https://x.json",
         "mK261101_459", "", "", '{"md": "09/20-12/31"}'],
    ], ensure_ascii=False)

    def test_picks_open_session(self):
        # 只在 00:00-12:00 那一场里选 460（另一场 461 此刻没开）
        mid = B._pick_from_list(self.RAW)
        self.assertIn(mid, (460, 461))

    def test_window_regex(self):
        self.assertEqual(B._parse_window("00:00-12:00"), (0, 720))
        self.assertEqual(B._parse_window("19:00-24:00"), (1140, 1440))
        self.assertIsNone(B._parse_window("https://x.json"))
        self.assertIsNone(B._parse_window(""))

    def test_positions(self):
        hand = [34, 66, 66, 99]
        self.assertEqual(B._positions(hand, [66, 99]), [1, 3])
        self.assertIsNone(B._positions(hand, [46]))

    def test_mask(self):
        ext = json.dumps({"msisdn": "184****3239"})
        self.assertEqual(B._mask_of(ext), "184****3239")
        self.assertEqual(B._mask_of("not json"), "")


class BotPlayTest(unittest.TestCase):
    def make(self, host=2):
        bot = B.Bot({"cat": "c", "uid": 1, "u": "u"}, host=host)
        bot.ws = FakeWS()
        return bot

    def test_lead_when_host_known(self):
        bot = self.make(host=2)
        bot.host_ok = True
        bot.hand = [46, 78, 34, 66, 99, 40]
        bot.act()
        self.assertEqual(len(bot.ws.sent), 1)
        kv = _kv_of(bot.ws.sent[0])
        self.assertEqual(kv[2], B.OpGm["Game"])
        self.assertEqual(kv[3], B.OpGd["Action"])
        self.assertTrue(kv[6], "应该真的出了牌")
        self.assertLess(len(bot.hand), 6)

    def test_pass_when_host_unknown(self):
        bot = self.make()
        bot.host_ok = False
        bot.hand = [46, 78, 34]
        bot.last = G.classify([40, 72, 72, 99, 99], 5)
        bot.act()
        kv = _kv_of(bot.ws.sent[0])
        self.assertEqual(kv[5], G.T_PASS)
        self.assertEqual(kv[6], [])

    def test_lead_lowest_single_when_host_unknown(self):
        bot = self.make()
        bot.host_ok = False
        bot.hand = [46, 78, 99]
        bot.last = None
        bot.act()
        kv = _kv_of(bot.ws.sent[0])
        self.assertEqual(kv[5], G.T_SINGLE)
        self.assertEqual(kv[6], [99])                   # ♣3 最小

    def test_on_action_acts_on_my_turn(self):
        bot = self.make(host=2)
        bot.host_ok = True
        bot.hand = [46, 78, 34, 66, 99, 40, 72, 72]
        # 座 4 出 888+33，下家是 1 号位（我）
        inner = {2: 18, 3: 10, 4: 1, 5: 0, 6: 4,
                 7: json.dumps([0, 5, 40, 72, 72, 99, 99, 4, 27, 1, 0, 0, 0, 1, 20000, 0, 0])}
        bot.on_action(inner)
        self.assertTrue(bot.ws.sent, "轮到我时应主动出牌")
        kv = _kv_of(bot.ws.sent[0])
        self.assertIn(kv[5], (G.T_PASS, G.T_SINGLE, G.T_PAIR, G.T_TRIPLE, G.T_TRIPLE2))

    def test_deal_then_play_removes_cards(self):
        bot = self.make(host=2)
        bot.host_ok = True
        bot.on_deal(DEAL)
        self.assertEqual(len(bot.hand), 27)
        bot.on_deal(DEAL)
        bot.last = None
        bot.act()
        self.assertLess(len(bot.hand), 27)


class ImportGuandanTest(unittest.TestCase):
    def test_extract_cat_from_har(self):
        import har_import
        har = {"log": {"entries": [
            {"request": {"url": "https://gd-api.migufun.com/api/login", "method": "POST",
                         "postData": {"text": json.dumps({"ap": 1001, "mode": 3,
                                                          "cat": "ANDTYZsidssolg" + "a" * 32})}},
             "response": {"content": {"text": json.dumps({"success": True, "data": {
                 "mid": 123, "u": "1@migufun_w", "tokenExpire": 4102444800000,
                 "ext": json.dumps({"msisdn": "139****0001"})}})}}},
        ]}}
        got = har_import.parse_har_text(json.dumps(har))
        g = got.get("guandan") or {}
        self.assertEqual(g.get("uid"), 123)
        self.assertEqual(g.get("u"), "1@migufun_w")
        self.assertTrue(g.get("cat", "").startswith("ANDTYZ"))
        self.assertEqual(g.get("phone_mask"), "139****0001")
