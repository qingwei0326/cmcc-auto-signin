"""手气红包拆包脚本的单测（全部用合成数据，不碰真实 token/号码）"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _guard  # noqa: F401,E42  隔离真实全局配置与台账
import cmcc_redpacket as rp


def _har(entries):
    return {"log": {"entries": entries}}


def _entry(url, headers=None, post=None, resp=None):
    return {
        "request": {
            "url": url,
            "headers": headers or [],
            "postData": {"text": json.dumps(post)} if post is not None else {},
        },
        "response": {"content": {"text": json.dumps(resp if resp is not None else {})}},
    }


class TestHarExtraction(unittest.TestCase):
    def test_extract_token_and_encode_ids(self):
        har = _har([
            _entry(rp.API, headers=[{"name": "Authorization", "value": "Bearer TOK1"}],
                   post={"serviceId": 26, "params": {"encodeId": "AAA"}}),
            _entry(rp.API, headers=[{"name": "Authorization", "value": "Bearer TOK1"}],
                   post={"serviceId": 30, "params": {"encodeId": "BBB"}}),
            _entry(rp.API, post={"serviceId": 22, "params": {"appId": 8}}),
            _entry(rp.API, post={"serviceId": 26, "params": {"encodeId": "AAA"}}),
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".har", delete=False, encoding="utf-8") as f:
            json.dump(har, f)
            p = f.name
        try:
            toks, ids = rp.from_har(Path(p))
            self.assertEqual(toks, ["TOK1"])
            self.assertEqual(ids, ["AAA", "BBB"])
        finally:
            Path(p).unlink()

    def test_broken_har_returns_empty(self):
        with tempfile.NamedTemporaryFile("w", suffix=".har", delete=False) as f:
            f.write("not json")
            p = f.name
        try:
            self.assertEqual(rp.from_har(Path(p)), ([], []))
        finally:
            Path(p).unlink()


class TestTokenChannels(unittest.TestCase):
    """HAR 里两类 bass token 必须按签发渠道分开，否则拆包必失败"""

    def test_split_by_referer_and_issuer(self):
        har = _har([
            _entry("https://wap.fj.10086.cn/weixin/wxAppletService/wx/code2session",
                   post={"code": "x"},
                   resp={"data": {"token": "MINI_FROM_CODE2SESSION"}}),
            _entry(rp.API, headers=[{"name": "Referer", "value": rp.REFERER},
                                    {"name": "Authorization", "value": "Bearer MINI_FROM_HEADER"}]),
            _entry("https://app.fmcc.com.cn/bass-applet-authServer/auth/loginByHangYan",
                   post={"token": "sso"},
                   resp={"data": {"token": "APP_FROM_ISSUER"}}),
            _entry(rp.APP_LUCK + "index", headers=[
                {"name": "Referer", "value": rp.APP_REFERER},
                {"name": "Authorization", "value": "Bearer APP_FROM_HEADER"}]),
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".har", delete=False, encoding="utf-8") as f:
            json.dump(har, f)
            p = f.name
        try:
            mini, app = rp.tokens_by_channel(Path(p))
            self.assertEqual(mini, ["MINI_FROM_CODE2SESSION", "MINI_FROM_HEADER"])
            self.assertEqual(app, ["APP_FROM_ISSUER", "APP_FROM_HEADER"])
        finally:
            Path(p).unlink()


class TestMyRedpackets(unittest.TestCase):
    """我的中奖记录 → 手气红包分享码（APP token，分页）"""

    def test_pagination_and_filter(self):
        page1 = {"code": "0000", "data": {"pageNum": 2, "list": [
            {"luckTime": "2026/11/26", "encodeJoinId": "E1", "expire": False,
             "prizeDto": {"prizeName": "1-2GB流量手气红包"}},
            {"luckTime": "2026/11/26", "encodeJoinId": None, "expire": False,
             "prizeDto": {"prizeName": "1GB流量日包"}},
        ]}}
        page2 = {"code": "0000", "data": {"pageNum": 2, "list": [
            {"luckTime": "2026/11/18", "encodeJoinId": "E2", "expire": True,
             "prizeDto": {"prizeName": "2-3元话费券手气红包"}},
        ]}}
        seen = []
        def fake_post(self, url, json, timeout):
            seen.append(dict(json))
            data = page1 if json["pageNum"] == 1 else page2
            return type("R", (), {"json": staticmethod(lambda: data)})()
        post = type("S", (), {"post": fake_post})()
        with patch.object(rp, "APP_LUCK", "http://fake/"):
            out = rp.my_redpackets(post, 11, 2026)
        self.assertEqual([p["encodeJoinId"] for p in out], ["E1", "E2"])
        self.assertEqual([p["expire"] for p in out], [False, True])
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["pageNum"], 1)
        self.assertEqual(seen[1]["pageNum"], 2)

    def test_login_failure_stops(self):
        fail = {"code": "401", "msg": "未登陆"}
        post = type("S", (), {
            "post": lambda self, url, json, timeout: type(
                "R", (), {"json": staticmethod(lambda: fail)})(),
        })()
        with patch.object(rp, "APP_LUCK", "http://fake/"):
            self.assertEqual(rp.my_redpackets(post), [])


class TestOwnPacketSkip(unittest.TestCase):
    """自己发的包必须跳过，服务端会回 1500"""

    def test_skip_own_packet(self):
        FIELDS = {"helpUser": "13800001234",
                  "prizeDto": {"prizeName": "1-2GB流量手气红包"}}
        state = {"responses": [
            {"resp_code": "0000", "data": {"userPhone": "13800001234"}},
            {"resp_code": "0000", "data": FIELDS},
        ]}
        with patch.object(rp, "call", side_effect=lambda s, sid, p: state["responses"].pop(0)), \
             patch.object(rp, "make_session"), \
             patch.object(rp, "already_opened", return_value=False):
            rc = rp.run(type("A", (), {
                "har": None, "token": "TOK", "id": ["OWN"],
                "go": True, "query_only": False,
            })())
        self.assertEqual(rc, 0)

    def test_stops_after_one_success(self):
        """一个号活动期内只能协助拆 1 次，成功即停"""
        FIELDS = {"helpUser": "13900005678",
                  "prizeDto": {"prizeName": "1-2GB流量手气红包"}}
        calls = []
        state = {"responses": [
            {"resp_code": "0000", "data": {"userPhone": "13800001234"}},
            {"resp_code": "0000", "data": FIELDS},
            {"resp_code": "0000", "data": {"prizeName": "1GB流量日包"}},
        ]}
        def fake_call(s, sid, p):
            calls.append(sid)
            return state["responses"].pop(0)
        with patch.object(rp, "call", side_effect=fake_call), \
             patch.object(rp, "make_session"), \
             patch.object(rp, "already_opened", return_value=False), \
             patch.object(rp, "record_award"), \
             patch.object(rp, "mark_opened"):
            rc = rp.run(type("A", (), {
                "har": None, "token": "TOK", "id": ["P1", "P2"],
                "go": True, "query_only": False,
            })())
        self.assertEqual(rc, 0)
        self.assertEqual(calls.count(rp.SID_OPEN), 1)


if __name__ == "__main__":
    unittest.main()
