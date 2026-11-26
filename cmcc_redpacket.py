"""
cmcc_redpacket.py —— 福建移动「天天大转盘 · 手气红包」拆包自动化

机制（来自活动规则原文，非推测）：
    手气红包 = 转赠红包。抽中 ≠ 到账。
    抽中后必须【当日分享给好友】+【好友当日拆包】，用户和好友才各得同等面值奖励。
    自己拆自己发的红包 → serviceId=30 回 1500「不能接受自己的邀请哦~」。
    一个用户在活动周期内只能协助拆红包 1 次；分享/拆包均仅限当日。

接口（两套入口，HAR 实证，别混）：
  APP 侧 bass-preferentialAreaServer/luck/（Referer=h5app.10086.cn，APP token）：
    getUserLuckRecord {appId,timestamp,pageNum,pageSize,queryMonth,queryYear}
        → 我的中奖记录；手气红包条目带 encodeJoinId（分享码）
    ★ encodeJoinId 每次查询都会重新签发、值都不同，必须现查现用，不能缓存。
  小程序侧 weixin/wxAppletService/bass/callService（Referer=servicewechat.com，小程序 token）：
    serviceId 22 = 转盘首页   {appId,timestamp} → userPhone 判归属
    serviceId 26 = 查红包详情 {encodeId}        → helpUser + prizeDto（只读）
    serviceId 30 = 拆红包     {encodeId}        → 实际到手 prizeName（不可逆！）

鉴权（19:32 三度实测确认，别再尝试绕开）：
    两个 token 都是 bass，但【签发渠道不同、各进各的门】：
      APP token       → getUserLuckRecord 通；callService 回 BASS-0001（换任何 referer 都不行）
      小程序 token    → callService 通；getUserLuckRecord 回 401 未登陆
    ★ 19:32 用【活的】APP token（9243，APP 侧 code=0000）实测 callService：
      小程序 referer / APP referer / 无 referer 三种全部 BASS-0001 → 确认拆包这一步
      无法用 APP token 代替，没有绕开的路。luck/index 那种不校验渠道的接口
      曾让人误以为全通用，其实不是。
    所以：拿分享码用【发红包号】的 APP token；拆包必须用【拆包号】的小程序 token。

用法：
    python cmcc_redpacket.py --har x.har             # 从 HAR 提 token+encodeId，dry-run
    python cmcc_redpacket.py --har x.har --go        # 真拆（不可逆，跳过自己发的）
    python cmcc_redpacket.py --token TOK --id EID     # 手工指定
    python cmcc_redpacket.py --har x.har --query-only # 只查详情
    python cmcc_redpacket.py --app-token TOK --mine   # 列我的待分享手气红包+分享码（APP token）
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from common import app_base, mask_phone, record_award, setup_utf8, update_json

setup_utf8()

BASE = app_base()
API = "https://wap.fj.10086.cn/weixin/wxAppletService/bass/callService"
APP_LUCK = "https://wap.fj.10086.cn/bass-preferentialAreaServer/luck/"
STATE_FILE = BASE / "gui_data" / "redpacket_state.json"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 MicroMessenger")
REFERER = "https://servicewechat.com/wx7f4c967c88ffdb82/262/page-frame.html"
APP_REFERER = "https://3998516324156551.h5app.10086.cn/3998516324156551/1.0"
APP_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7_9 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Mobile/15E148")
SID_INFO, SID_OPEN, SID_HOME = 26, 30, 22


# ---------------------------------------------------------------- 会话

def make_session(token):
    s = requests.Session()
    s.headers.update({
        "Authorization": "Bearer " + token.strip(),
        "Content-Type": "application/json;charset=utf-8",
        "User-Agent": UA,
        "Referer": REFERER,
        "Accept-Encoding": "gzip,compress,br,deflate",
    })
    return s


def call(s, sid, params):
    """callService 统一派发；math 是随机数，防止网关缓存串响应。"""
    r = s.post(API, params={"math": random.random()},
               json={"serviceId": sid, "params": params}, timeout=25)
    return r.json()


def my_phone(s):
    o = call(s, SID_HOME, {"appId": 8, "timestamp": int(time.time() * 1000)})
    return str((o.get("data") or {}).get("userPhone") or "")


def make_app_session(token):
    """APP 侧会话：Referer 必须是 h5app.10086.cn，否则 getUserLuckRecord 回 401。"""
    s = requests.Session()
    s.headers.update({
        "Authorization": "Bearer " + token.strip(),
        "Content-Type": "application/json;charset=utf-8",
        "User-Agent": APP_UA,
        "Referer": APP_REFERER,
    })
    return s


def my_redpackets(s, month=None, year=None):
    """列【发红包那个号】的待分享手气红包，返回 [{prize, encodeJoinId, expire, at}]。

    encodeJoinId 每次查询服务端都重新签发，值不同但等效，必须现查现用。
    """
    now = datetime.now()
    body = {"appId": 26, "timestamp": int(time.time() * 1000),
            "pageNum": 1, "pageSize": 20,
            "queryMonth": month or now.month, "queryYear": year or now.year}
    out = []
    for page in range(1, 6):
        body["pageNum"] = page
        r = s.post(APP_LUCK + "getUserLuckRecord", json=body, timeout=25)
        o = r.json()
        if str(o.get("code")) != "0000":
            break
        data = o.get("data") or {}
        for x in data.get("list") or []:
            pd = x.get("prizeDto") or {}
            name = str(pd.get("prizeName") or "")
            if "手气红包" not in name:
                continue
            eid = str(x.get("encodeJoinId") or "")
            if eid:
                out.append({"prize": name, "encodeJoinId": eid,
                            "expire": bool(x.get("expire")), "at": x.get("luckTime")})
        if page >= int(data.get("pageNum") or 1):
            break
    return out


# ---------------------------------------------------------------- HAR 提取

def tokens_by_channel(path):
    """按签发渠道把 HAR 里的 token 分开，返回 (mini_tokens, app_tokens)。

    小程序 token（拆包用）：
      · wx/code2session 响应的 data.token —— 最可靠，就是当前小程序账号
      · 带 servicewechat.com Referer 的请求头 Authorization
    APP token（查分享码用）：
      · loginByHangYan / pageInit 响应的 data.token
      · 带 h5app.10086.cn Referer 的请求头 Authorization
    """
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        print(f"HAR 解析失败：{e}")
        return [], []
    mini, app = [], []

    def add(bucket, tok):
        tok = str(tok or "").strip()
        if tok and tok not in bucket and tok not in mini and tok not in app:
            bucket.append(tok)

    for e in (d.get("log") or {}).get("entries") or []:
        req = e.get("request") or {}
        url = str(req.get("url") or "")
        ref = ""
        for h in req.get("headers") or []:
            if str(h.get("name", "")).lower() == "referer":
                ref = str(h.get("value") or "")
        # 1) 签发接口的响应体
        txt = ((e.get("response") or {}).get("content") or {}).get("text") or ""
        if "code2session" in url or "pageInit" in url or "loginByHangYan" in url:
            try:
                tok = (json.loads(txt).get("data") or {}).get("token")
                if tok:
                    add(mini if "code2session" in url else app, tok)
            except Exception:
                pass
        # 2) 请求头（按 referer 分渠道）
        for h in req.get("headers") or []:
            if str(h.get("name", "")).lower() == "authorization":
                v = str(h.get("value") or "").strip()
                if v.lower().startswith("bearer ") and len(v) > 7:
                    if "servicewechat" in ref:
                        add(mini, v[7:].strip())
                    elif "h5app" in ref:
                        add(app, v[7:].strip())
                    elif not ref:
                        # 没带 referer：无法确定渠道，拆包默认走 mini（脚本的主要用途）
                        add(mini, v[7:].strip())
    return mini, app


def from_har(path):
    """兼容旧调用：返回 (全部 token, 所有 encodeId)，小程序 token 排前面。"""
    mini, app = tokens_by_channel(path)
    toks = mini + [t for t in app if t not in mini]
    ids = []
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return toks, ids
    for e in (d.get("log") or {}).get("entries") or []:
        pd = ((e.get("request") or {}).get("postData") or {}).get("text") or ""
        try:
            o = json.loads(pd)
            eid = (o.get("params") or {}).get("encodeId")
            if o.get("serviceId") in (SID_INFO, SID_OPEN) and eid and eid not in ids:
                ids.append(str(eid))
        except Exception:
            pass
    return toks, ids


# ---------------------------------------------------------------- 已拆状态

def already_opened(eid):
    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return eid in set(st.get("opened") or [])
    except Exception:
        return False


def mark_opened(eid, prize):
    def mutate(st):
        op = st.setdefault("opened", [])
        if eid not in op:
            op.append(eid)
        st["opened"] = op[-200:]
        st["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.setdefault("log", []).append({
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "encodeId": eid, "prize": prize,
        })
        st["log"] = st["log"][-200:]
    update_json(STATE_FILE, mutate, default={"opened": [], "log": []})


# ---------------------------------------------------------------- 主流程

def run(args):
    # --mine：用 APP token 列我抽到的待分享手气红包（分享码现查现生成）
    if getattr(args, "mine", False):
        atok = args.app_token
        if not atok and args.har:
            _, apps = tokens_by_channel(args.har)
            atok = apps[0] if apps else None
        if not atok:
            print("--mine 需要 APP token：传 --app-token 或含它的 --har")
            return 2
        packs = my_redpackets(make_app_session(atok), args.month, args.year)
        print(f"我的手气红包（{args.month or datetime.now().month}月 / {args.year or datetime.now().year}）：")
        print("-" * 62)
        if not packs:
            print("  当月没有手气红包记录")
        for p in packs:
            flag = "已过期" if p["expire"] else "可分享"
            print(f"  {p['at']} | {flag} | {p['prize']}")
            print(f"    分享码 encodeJoinId = {p['encodeJoinId']}")
        print("-" * 62)
        print("提示：分享码每次查询都会变，拆包方要现查现拆；自己拆自己的会被 1500 拒绝。")
        return 0

    ids = []
    if args.har:
        # 拆包只认小程序 token；用渠道分类结果，别再一股脑抓 Authorization
        minis, apps = tokens_by_channel(args.har)
        toks = list(minis)
        ids = from_har(args.har)[1]
        if apps:
            print(f"提示：HAR 里还有 {len(apps)} 个 APP token（只能查分享码，不能拆包）")
    else:
        toks = []
    if args.token:
        toks = [args.token] + [t for t in toks if t != args.token]
    if args.id:
        for x in args.id:
            if x not in ids:
                ids.append(x)
    if not toks:
        print("没有可用 token：传 --har 或 --token")
        return 2
    if not ids:
        print("没有 encodeId：传 --id 或换一个含拆包记录的 HAR")
        return 2

    tok = toks[0]
    if len(toks) > 1:
        print(f"HAR 里有 {len(toks)} 个 token，先用第 1 个（其余可用 --token 指定）")
    s = make_session(tok)
    me = my_phone(s)
    print(f"当前拆包账号：{me or '(未取到)'}   待处理红包 {len(ids)} 个")
    print(f"模式：{'真拆' if args.go else 'DRY-RUN（不产生任何实际领取）'}")
    print("-" * 62)

    opened = 0
    for eid in ids:
        q = call(s, SID_INFO, {"encodeId": eid})
        code = str(q.get("resp_code"))
        if code != "0000":
            print(f"[{eid}] 查详情失败 {code} {q.get('resp_desc')}")
            continue
        d = q.get("data") or {}
        helper = str(d.get("helpUser") or "")
        prize = ((d.get("prizeDto") or {}).get("prizeName") or "")
        print(f"[{eid}] 发红包人 {helper or '?'} | 包内 {prize or '?'}")

        if me and helper == me:
            print("  -> 跳过：这是自己发的包，服务端会回 1500")
            continue
        if already_opened(eid):
            print("  -> 跳过：本机状态记录已拆过")
            continue
        if args.query_only or not args.go:
            print("  -> 可拆（dry-run，未发送拆包请求）" if not args.query_only else "  -> 只读查询")
            continue

        # 真拆：不可逆，一个号活动期内只能帮别人拆 1 次，失败的也别狂试
        r = call(s, SID_OPEN, {"encodeId": eid})
        rc = str(r.get("resp_code"))
        got = ((r.get("data") or {}).get("prizeName") or "")
        if rc == "0000":
            print(f"  -> 拆包成功：{got or prize}")
            record_award(me or "未知", "手气红包拆包", got or prize)
            mark_opened(eid, got or prize)
            opened += 1
            break  # 每号每活动期只能协助拆 1 次，成功一次即停
        desc = str(r.get("resp_desc") or "").replace("<br>", " ")
        print(f"  -> 拆包失败 {rc} {desc}")
        # 1500 自己拆自己 / 1510 本月已拆过 / 1410 已被别人拆 / 1600 次数用尽
        # 这些都是硬停信号，继续试下一个包也是白费名额
        if rc in ("1500", "1501", "1510", "1410", "1600"):
            break

    print("-" * 62)
    print(f"完成：本次真拆 {opened} 个" if args.go else "完成：dry-run，未领取任何奖品")
    return 0


def main():
    ap = argparse.ArgumentParser(description="福建移动手气红包拆包自动化（默认 dry-run）")
    ap.add_argument("--har", help="含 token / encodeId 的 HAR 文件")
    ap.add_argument("--token", help="小程序 Bearer token（拆包方，不带 Bearer 前缀）")
    ap.add_argument("--app-token", help="APP Bearer token（发红包方，用于 --mine 查分享码）")
    ap.add_argument("--id", action="append", default=[], help="红包 encodeId，可重复传")
    ap.add_argument("--mine", action="store_true", help="列我的待分享手气红包+分享码")
    ap.add_argument("--month", type=int, help="查询月份，默认当月")
    ap.add_argument("--year", type=int, help="查询年份，默认当年")
    ap.add_argument("--go", action="store_true", help="真拆；不可逆，默认关闭")
    ap.add_argument("--query-only", action="store_true", help="只查详情，不拆")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
