"""领券中心（cmcc_voucher.py）与 HAR 导入的回归测试。

封包密钥、签名公式、URL 末段这三项都来自抓包实测值，写死在测试里防止回归。
不发真实网络请求。
"""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _guard  # noqa: F401,E402  隔离真实全局配置

import cmcc_voucher as V        # noqa: E402
import har_import as HI         # noqa: E402


# UID / SIGN 来自抓包实测；手机号用测试允许的合成号，URL 末段按同一密钥重算
UID = "mhrzaf3b0bdb4949b443c8559bb0a97d"
PHONE = "13800138000"
SIGN = "078091882a02b3486590c008ceb7e40b"
SEGMENT = "c244Qmdac3M1c1Q3LzEyRFBtZ1dYUT09"


class TestCrypto(unittest.TestCase):
    def test_packet_roundtrip(self):
        payload = {"loginNo": PHONE, "busiType": "jx"}
        self.assertEqual(json.loads(V.decode_packet(V.encode_packet(payload))), payload)

    def test_sign_matches_capture(self):
        self.assertEqual(V.build_sign(UID), SIGN)

    def test_path_segment_is_encrypted_phone(self):
        self.assertEqual(V.encode_packet(PHONE), SEGMENT)

    def test_uid_from_token(self):
        token = f"JSESSIONID=abc; UID={UID}; Comment=SessionServer-unity; ticketID=NingBo"
        self.assertEqual(V.uid_from_token(token), UID)
        self.assertEqual(V.uid_from_token("JSESSIONID=abc"), "")

    def test_msg_id_shape(self):
        decoded = V.decode_packet(V._msg_id())
        self.assertEqual(len(decoded), 25)
        self.assertTrue(decoded[:13].isdigit())


class TestClaimable(unittest.TestCase):
    NOW = datetime(2026, 11, 28, 17, 0, 0)

    def row(self, **kw):
        base = {"batchID": "b1", "cardName": "券", "obtainEffTime": "",
                "obtainExpTime": "", "userObtainNum": "0"}
        base.update(kw)
        return base

    def test_open_and_unclaimed(self):
        rows = [self.row(obtainEffTime="20261128100000",
                         obtainExpTime="20261130235959")]
        self.assertEqual(V.claimable(rows, self.NOW), rows)

    def test_not_yet_open(self):
        rows = [self.row(obtainEffTime="20261128200000")]
        self.assertEqual(V.claimable(rows, self.NOW), [])

    def test_expired(self):
        rows = [self.row(obtainExpTime="20261127100000")]
        self.assertEqual(V.claimable(rows, self.NOW), [])

    def test_already_claimed(self):
        rows = [self.row(userObtainNum="2")]
        self.assertEqual(V.claimable(rows, self.NOW), [])


class TestFlow(unittest.TestCase):
    def cfg(self):
        cfg = mock.Mock()
        cfg.app_token = f"JSESSIONID=abc; UID=expired; ticketID=NingBo"
        cfg.voucher_uid = UID
        cfg.phone = PHONE
        cfg.province_code = "591"
        cfg.city_code = "0596"
        return cfg

    def test_dry_run_does_not_receive(self):
        rows = [{"batchID": "b1", "cardName": "话费券", "cardInfo": "无门槛",
                 "obtainEffTime": "20261128000000", "obtainExpTime": "20261201235959",
                 "userObtainNum": "0"}]
        with mock.patch.object(V, "open_session"), \
             mock.patch.object(V, "coupon_query", return_value=rows), \
             mock.patch.object(V, "receive") as receive:
            lines = V.run(self.cfg(), False)
        receive.assert_not_called()
        self.assertTrue(any("dry-run" in line for line in lines))

    def test_go_claims_and_records(self):
        rows = [{"batchID": "b1", "cardName": "话费券", "cardInfo": "无门槛",
                 "obtainEffTime": "20261128000000", "obtainExpTime": "20261201235959",
                 "userObtainNum": "0"}]
        with mock.patch.object(V, "open_session"), \
             mock.patch.object(V, "coupon_query", return_value=rows), \
             mock.patch.object(V, "receive", return_value={"retCode": "000000", "retMsg": "成功"}), \
             mock.patch.object(V, "record_award") as record:
            lines = V.run(self.cfg(), True)
        record.assert_called_once()
        self.assertTrue(any("[已领]" in line for line in lines))

    def test_coupon_query_parses_encrypted_payload(self):
        detail = {"batchID": "2003300567425880064", "cardName": "1GB流量日包券",
                  "cardInfo": "卡券兑换活动", "obtainEffTime": "20261128000000",
                  "obtainExpTime": "20261130235959", "userObtainNum": "0"}
        payload = {"busiData": [{"batchInfo": [detail]}]}
        resp = {"retCode": "000000",
                "data": {"outParam": V.encode_packet(json.dumps(payload, ensure_ascii=False))}}
        with mock.patch.object(V, "_post", return_value=resp):
            rows = V.coupon_query(mock.Mock(), self.cfg())
        self.assertEqual(rows[0]["batchID"], detail["batchID"])


class TestHarImport(unittest.TestCase):
    def test_extract_voucher_uid_and_phone(self):
        init_data = json.dumps({"UID": UID, "sign": SIGN, "deviceInfo": {}},
                               separators=(",", ":"))
        # initMessage 用单层封包
        data = V._aes_encode(init_data.encode(), double=False)
        inparam = V.encode_packet({"loginNo": PHONE, "busiType": "jx"})
        har = {"log": {"entries": [
            {"startedDateTime": "2026-11-28T09:23:18Z", "request": {
                "url": "https://touch.10086.cn/i/v1/cust/initMessage",
                "postData": {"text": json.dumps({"data": data, "channel": "12"})}}},
            {"startedDateTime": "2026-11-28T09:23:35Z", "request": {
                "url": f"https://touch.10086.cn/i/v1/cust/couponQry/{SEGMENT}",
                "postData": {"text": json.dumps({"inParam": inparam, "msgId": V._msg_id()})}}},
        ]}}
        parsed = HI.parse_har_text(json.dumps(har))
        self.assertEqual(parsed["voucher"], {
            PHONE: {"uid": UID, "ts": "2026-11-28T09:23:35Z"}})


if __name__ == "__main__":
    unittest.main()
