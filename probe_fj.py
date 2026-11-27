"""
probe_fj.py —— 探测 BASS 平台各接口的返回结构（全部只读，不做任何改动）

用法：
    python probe_fj.py                          # 默认读 configs_fj/fj1.json
    python probe_fj.py --config configs_fj/fj2.json
"""

import argparse
import json
import time

import requests

from common import app_base, setup_utf8

setup_utf8()

BASE = app_base()
HOST = "https://wap.fj.10086.cn"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148/wkwebview")

READONLY = [
    ("养成-首页信息",   "/bass-cultivateGameServer/index/getIndexInfo"),
    ("养成-签到状态",   "/bass-cultivateGameServer/sign/getSignInfo"),
    ("养成-每日可领",   "/bass-cultivateGameServer/index/getDailyReceiveTask"),
    ("养成-下档可领",   "/bass-cultivateGameServer/index/getNextReceiveTask"),
    ("养成-任务列表",   "/bass-cultivateGameServer/task/getUserTasks"),
    ("养成-阶段信息",   "/bass-cultivateGameServer/stage/getUserStageInfo"),
    ("养成-阶段奖励",   "/bass-cultivateGameServer/stage/getStagePrizes"),
    ("红包宝-初始化",   "/bass-commonAreaServer/H5Api/hmxb/activity/init"),
    ("红包宝-骰子余额", "/bass-commonAreaServer/H5Api/hmxb/dice/balance"),
    ("红包宝-任务列表", "/bass-commonAreaServer/H5Api/hmxb/task/list"),
    ("流量银行-首页",   "/bass-commonActServer/flowBank/index"),
    ("流量银行-任务",   "/bass-commonActServer/flowBank/taskInfo"),
]


def brief(o, depth=0):
    """把返回结构压成可读摘要。"""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if isinstance(v, (dict, list)):
                out[k] = brief(v, depth + 1)
            else:
                s = str(v)
                out[k] = s if len(s) <= 40 else s[:37] + "..."
        return out
    if isinstance(o, list):
        return f"list[{len(o)}]" + (f" 首项={brief(o[0], depth+1)}" if o and depth < 3 else "")
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs_fj/fj1.json")
    args = ap.parse_args()

    cfg_path = BASE / args.config
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "Authorization": f"Bearer {cfg['bearer_token']}",
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "Referer": cfg.get("referer") or f"{HOST}/bass-cultivateGameServer/",
    })

    print(f"配置: {args.config}  号码: {cfg.get('phone', '?')}\n")

    for name, path in READONLY:
        url = HOST + path
        params = {"appId": cfg.get("app_id", "0"), "timestamp": str(int(time.time() * 1000))}
        print("=" * 70)
        print(f"{name}  {path}")
        try:
            r = s.get(url, params=params, timeout=20)
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}")
                continue
            j = r.json()
            print(f"  code={j.get('code')}  msg={j.get('msg')}  success={j.get('success')}")
            data = j.get("data")
            if data is not None:
                print(f"  data: {json.dumps(brief(data), ensure_ascii=False)[:1200]}")
        except Exception as e:
            print(f"  异常: {e}")
        time.sleep(0.6)


if __name__ == "__main__":
    main()
