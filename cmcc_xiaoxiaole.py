"""
cmcc_xiaoxiaole.py —— 福建移动 BASS「消消乐」H5 小游戏（半自动）

原理（逆向自 bass-eliminateHappyH5/assets/main/index.js）：
  - getDailyPlayLimit → 每局拿一个一次性 playToken
  - playEnd 提交 {gameLevel, score, isOld, playToken}
    其中 gameLevel / score 是本地 AES-CBC 加密的明文数字：
      key = 8cc70bf2a955c6bf
      iv  = 1692bc56e8b783f4
      padding = ZeroPadding，密文前拼 9 位随机字母
  - score 其实是「星级」starCount，取值 0~3（3 星最高），不是分数！
  - gameLevel 是当前关卡号（本地 store 的 Level，初始 1）。

重要现实（别抱错期待）：
  1. 中奖是服务端抽奖池决定的，跟 score/level 无关。首抽必中已用掉，
     之后是概率抽奖，大概率「谢谢参与」。脚本只如实报告结果，不保证中奖。
  2. 服务端会校验 gameLevel 与账号进度、score 范围（<=3）。乱填会 -100 异常请求。
     所以 --level 要填账号真实到达的关卡号（可从抓包或 App 里看）。
  3. 每天游戏次数有限（用户实测约 5 次/天，接口 dayLimit 显示 100 但非真实可用）。
  4. 有全局频控：短时间多次调用会 -100「当前查询人数过多」。务必慢速。

依赖：pip install requests pycryptodome

用法：
  python cmcc_xiaoxiaole.py --dry-run --config configs_fj/fj2.json   # 只读看次数
  python cmcc_xiaoxiaole.py --config configs_fj/fj2.json --level 63 --times 5
  python cmcc_xiaoxiaole.py --config configs_fj/fj2.json --level 63 --times 5 --gap 15

退出码（run_all.py 靠它判定成败并决定是否推送）：
  0 = 正常收尾，含「体力/次数用尽」这类预期内的停止
  1 = 真失败：配置错误、token 失效、取不到 playToken、连续异常失败
"""

import argparse
import base64
import json
import random
import string
import sys
import time
from pathlib import Path

import requests

try:
    from Crypto.Cipher import AES
except ImportError:
    print("缺少 pycryptodome，请先运行：python -m pip install pycryptodome", file=sys.stderr)
    sys.exit(1)

from common import setup_utf8

setup_utf8()

BASE = Path(__file__).parent
HOST = "https://wap.fj.10086.cn"
API = HOST + "/bass-commonAreaServer/H5Api/eliminate/"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148/wkwebview")
REFERER = "https://wap.fj.10086.cn/bass-eliminateHappyH5/"

# 逆向自 index.js toolsHelper 模块
AES_KEY = b"8cc70bf2a955c6bf"
AES_IV = b"1692bc56e8b783f4"


def aes_encode(plain: str) -> str:
    """等价 JS 的 toolsHelper.aesEncode：AES-CBC/ZeroPadding + 前拼 9 位随机字母。"""
    data = plain.encode("utf-8")
    pad = (16 - len(data) % 16) % 16
    data += b"\x00" * pad
    ct = AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(data)
    prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=9))
    return prefix + base64.b64encode(ct).decode("ascii")


def make_session(token: str) -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "Authorization": "Bearer " + token,
        "User-Agent": UA,
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": HOST,
        "Referer": REFERER,
        "Accept": "*/*",
    })
    return s


def post(s: requests.Session, path: str, body: dict = None, retries: int = 1) -> dict:
    """POST 调用，带 appId/timestamp。

    -100 不只是限流：体力耗尽 / 当日次数用完后，除 getDailyPlayLimit 外的接口
    都会返回「当前查询人数过多，请稍后再试」。这种情况重试毫无意义
    （实测 436 秒全烧在重试上），所以只对网络异常重试，-100 直接返回交给主循环判断。
    """
    body = dict(body or {})
    url = API + path
    j = {}
    for attempt in range(retries + 1):
        body["appId"] = "0"
        body["timestamp"] = str(int(time.time() * 1000))
        try:
            r = s.post(url, json=body, timeout=20)
            j = r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(6 + attempt * 6)
                continue
            return {"code": "-1", "msg": f"请求异常: {e}", "data": None}
        return j
    return j


def show_limit(s: requests.Session) -> dict:
    """打印今日次数并返回整个响应（调用方要看 code 判定 token 是否失效）。"""
    j = post(s, "getDailyPlayLimit")
    d = j.get("data") or {}
    print(f"  今日已玩 {d.get('todayPlayCount')} 次 / dayLimit={d.get('dayLimit')} "
          f"reached={d.get('reachedLimit')}  code={j.get('code')}")
    return j


def prize_name(data) -> str:
    if not isinstance(data, dict):
        return ""
    return data.get("prizeName") or ""


def main() -> int:
    ap = argparse.ArgumentParser(description="福建移动消消乐（半自动，慢速少量）")
    ap.add_argument("--config", default="configs_fj/fj2.json", help="账号配置 json")
    ap.add_argument("--dry-run", action="store_true", help="只读看次数，不玩游戏")
    ap.add_argument("--detect-level", action="store_true",
                    help="从 indexInfo.gameRecordList 自动读取账号当前最高关卡（推荐先跑这个）")
    ap.add_argument("--level", type=int, default=None,
                    help="当前关卡号（必须接近账号真实进度，默认 63=抓包样本）")
    ap.add_argument("--star", type=int, default=3, choices=[0, 1, 2, 3],
                    help="星级 score，0~3，默认 3（满星）")
    ap.add_argument("--times", type=int, default=5, help="本次玩几局，默认 5")
    ap.add_argument("--gap", type=int, default=12, help="每局间隔秒数，默认 12（防频控）")
    args = ap.parse_args()

    cfg_path = BASE / args.config
    if not cfg_path.exists():
        print(f"找不到配置：{cfg_path}")
        return 1
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"配置解析失败：{cfg_path}\n  {e}")
        return 1
    token = cfg.get("bearer_token", "")
    if not token:
        print("配置里 bearer_token 为空")
        return 1

    s = make_session(token)
    print(f"账号配置: {args.config}")
    print("=" * 56)

    lim0 = show_limit(s)
    if args.dry_run and not args.detect_level:
        print("\n--dry-run：仅查看，未玩游戏。")
        return 0 if str(lim0.get("code")) == "0000" else 1

    # 自动探测当前关卡：indexInfo.gameRecordList 里最高 gameLevel 即账号进度
    detected = None
    if args.detect_level:
        time.sleep(2)
        idx = post(s, "indexInfo")
        gr = (idx.get("data") or {}).get("gameRecordList") or []
        if gr:
            detected = max(int(g.get("gameLevel", 0)) for g in gr)
            print(f"  自动探测当前关卡: {detected}（共 {len(gr)} 条通关记录）")
        else:
            code = str(idx.get("code"))
            print(f"  探测关卡失败: code={code} msg={idx.get('msg')!r}")
            if code == "-100":
                print("  提示：-100 多为体力耗尽或今日次数用完（也可能短时限流），先试 1 局确认")
        if args.dry_run:
            print("\n--dry-run：仅查看，未玩游戏。")
            return 0 if str(lim0.get("code")) == "0000" else 1

    level = detected if detected is not None else (args.level if args.level is not None else 63)
    print(f"\n参数: level={level}  star={args.star}  times={args.times}  gap={args.gap}s")
    print("提示: 中奖由服务端抽奖池决定，脚本不保证中奖；首抽大奖通常已用掉。\n")

    got = 0
    played = 0
    fail_streak = 0
    hard_fail = False   # 真失败（token/网关/参数），退出码 1
    soft_stop = False   # 预期内收尾（体力或次数用尽），退出码 0
    for i in range(args.times):
        # 每局重新取一次性 token
        lim = post(s, "getDailyPlayLimit")
        ld = lim.get("data") or {}
        lcode = str(lim.get("code"))
        if lcode == "401":
            print(f"[{i + 1}] token 失效（401），停止。请更新该号 bearer_token。")
            hard_fail = True
            break
        if ld.get("reachedLimit"):
            print(f"[{i + 1}] 今日次数已用完，停止。")
            soft_stop = True
            break
        if lcode != "0000":
            print(f"[{i + 1}] 取 playToken 失败：code={lcode} {lim.get('msg')}，停止。")
            hard_fail = True
            break
        play_token = ld.get("playToken", "")
        if not play_token:
            print(f"[{i + 1}] 未拿到 playToken，停止。")
            hard_fail = True
            break

        body = {
            "gameLevel": aes_encode(str(level)),
            "score": aes_encode(str(args.star)),
            "isOld": False,
            "playToken": play_token,
        }
        r = post(s, "playEnd", body)
        code = str(r.get("code", ""))
        pn = prize_name(r.get("data"))
        played += 1
        if code == "0000":
            fail_streak = 0
            if pn:
                got += 1
                print(f"[{i + 1}] ✓ 完成  level={level} star={args.star}  →  中奖：{pn}")
            else:
                print(f"[{i + 1}] ✓ 完成  level={level} star={args.star}  →  未中奖（谢谢参与）")
        else:
            fail_streak += 1
            msg = str(r.get("msg") or "")
            print(f"[{i + 1}] ✗ code={code} msg={msg!r}")
            # 服务端对体力不足有明确文案，命中就直接收手，别再试探
            if "体力" in msg or "次数" in msg:
                print(f"    体力/次数不足，直接停止（今日已玩 {ld.get('todayPlayCount')} 次）。")
                soft_stop = True
                break
            if code == "401":
                print("    token 失效（401），停止。请更新该号 bearer_token。")
                hard_fail = True
                break
            # 其它 -100：连续两次拿不到结果也停，继续跑只是白烧次数
            if fail_streak >= 2:
                print(f"    连续 {fail_streak} 局失败（{code}）——大概率次数用完或网关在拦，"
                      f"提前停止，剩余局数不再消耗。")
                hard_fail = True
                break

        if i < args.times - 1:
            time.sleep(args.gap + random.uniform(-1, 2))

    print("\n" + "=" * 56)
    print(f"本次实际提交 {played} 局（目标 {args.times}），中奖 {got} 次。")
    # 收尾查奖品（需带 queryMonth，格式 YYYY-MM）
    month = time.strftime("%Y-%m")
    pr = post(s, "getMyPrizeList", {"queryMonth": month})
    plist = pr.get("data") or []
    if isinstance(plist, list) and plist:
        print("当前奖品：")
        for x in plist:
            print(f"  - {x.get('prizeName')}  status={x.get('status')} 到期={x.get('expireDate')}")
    else:
        print(f"奖品查询：code={pr.get('code')} msg={pr.get('msg')!r}")

    if hard_fail:
        return 1
    if played == 0 and not soft_stop:
        print("一局都没提交成功，且不属于体力/次数用尽 —— 记为失败。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
