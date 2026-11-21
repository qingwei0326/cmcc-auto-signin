"""
har_import.py —— 从 HAR 里同时提取 qwhdhub app_token 与 BASS Bearer token，写回配置

设计要点：
  · qwhdhub 的 token 来自 appTokenLogin 请求体（JSESSIONID=..; UID=..; ticketID=..），
    手机号从 userCheckId（十六进制）反解，反解失败时在请求体里正则兜底。
  · BASS 的 token 来自任意 wap.fj.10086.cn / app.fmcc.com.cn 请求的 Authorization 头，
    形如 Bearer <36位UUID>。
  · BASS 的 HAR 本身不含手机号，归属账号依次按三条线索判定：
      1) 同一份 HAR 里的 qwhdhub 号码；
      2) 调用方显式指定的 key（configs/<key>.json 的 phone）；
      3) 用该 bearer 调一次 luck/index，回显的脱敏号码（184****3239）——
         福建活动页压根不发 appTokenLogin，没有这条线索时每份福建抓包
         都会掉进「认不出属于哪个号码」，所以这是主路径。
  · 只写 token 相关字段，其余配置原样保留。
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from common import atomic_write_json, app_base, find_har_files

FJ_LUCK_INDEX = "https://wap.fj.10086.cn/bass-preferentialAreaServer/luck/index"
BASS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Mobile/15E148/wkwebview")

BASE = app_base()

QWH_MARK = "appTokenLogin"
BASS_HOSTS = ("wap.fj.10086.cn", "app.fmcc.com.cn")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PHONE_RE = re.compile(r"1[3-9]\d{9}")


def _headers(req):
    out = {}
    for h in (req.get("headers") or []):
        name = str(h.get("name") or "").lower()
        if name:
            out[name] = str(h.get("value") or "")
    return out


def _phone_from_user_check(uc):
    try:
        p = str(int(str(uc), 16))
    except Exception:
        return ""
    return p if PHONE_RE.fullmatch(p) else ""


def _issued_local(ts: str) -> str:
    """HAR 的 startedDateTime（UTC）→ 本地时间 ISO 字符串。

    HAR 里的时间形如 2026-11-20T04:09:25.123456Z，是 UTC。
    签发时刻是算票据寿命的起点，必须换算到本地时间，否则会差 8 小时。
    """
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None).isoformat(timespec="seconds")
    except Exception:
        return ""


def parse_har_text(text):
    """解析 HAR 文本，返回 {qwhd:{phone:{token,body,ts}}, bass:{token,url,ts}}"""
    data = json.loads(text)
    entries = ((data.get("log") or {}).get("entries")) or []

    qwhd = {}
    bass = None
    mobiles = set()

    for e in entries:
        req = e.get("request") or {}
        url = str(req.get("url") or "")
        ts = str(e.get("startedDateTime") or "")
        hdr = _headers(req)

        for m in PHONE_RE.finditer(url):
            mobiles.add(m.group(0))

        # ---- qwhdhub ----
        if QWH_MARK in url:
            raw = (req.get("postData") or {}).get("text") or ""
            body = {}
            if raw.strip().startswith("{"):
                try:
                    body = json.loads(raw)
                except Exception:
                    body = {}
            tk = str(body.get("token") or "")
            if "JSESSIONID" in tk.upper():
                phone = _phone_from_user_check(body.get("userCheckId"))
                if not phone:
                    m = PHONE_RE.search(json.dumps(body, ensure_ascii=False))
                    if m:
                        phone = m.group(0)
                if phone and (phone not in qwhd or ts >= qwhd[phone]["ts"]):
                    qwhd[phone] = {"token": tk, "body": body, "ts": ts}
                if phone:
                    mobiles.add(phone)

        # ---- BASS ----
        auth = hdr.get("authorization", "")
        if auth.lower().startswith("bearer ") and any(h in url for h in BASS_HOSTS):
            tok = auth[7:].strip()
            if UUID_RE.fullmatch(tok):
                if bass is None or ts >= bass["ts"]:
                    bass = {"token": tok, "url": url, "ts": ts}

    return {"qwhd": qwhd, "bass": bass, "mobiles": sorted(mobiles)}


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, obj, indent=2)
    # 写完立刻复读校验：曾出现过「报告说导入成功、实际写到 exe 临时解包目录」的情况，
    # 那个目录随进程退出消失，用户看到的却是"导入完成"。这里把问题挡在写入这一刻。
    if not _read_json(path):
        raise RuntimeError(f"写入后复读失败：{path}")


def _assert_writable():
    """确认配置目录落在项目目录里，而不是 exe 的临时解包目录。

    冻结成 onefile exe 后模块的 __file__ 指向 PyInstaller 的 _MEIxxxx 临时目录，
    写在那里的配置在 exe 关闭后就消失了。冻结时必须用 exe 所在目录。
    """
    if not getattr(sys, "frozen", False):
        return
    want = Path(sys.executable).resolve().parent
    got = BASE.resolve()
    if got != want:
        raise RuntimeError(
            f"配置目录指向错误：当前 {got}，应为 exe 所在目录 {want}"
            "（onefile exe 解包到临时目录，配置会随进程退出丢失）"
        )


def _fj_targets():
    """{phone: Path} —— configs_fj 下各配置的号码映射"""
    out = {}
    d = BASE / "configs_fj"
    if not d.exists():
        return out
    for f in sorted(d.glob("*.json")):
        c = _read_json(f)
        ph = str(c.get("phone") or "")
        if PHONE_RE.fullmatch(ph):
            out[ph] = f
    return out


def _masked_phone_of_bearer(tok, timeout=12):
    """用 BASS bearer 调一次只读接口，拿回脱敏号码（如 184****3239）。

    这是判断「这个 Bearer 到底属于哪个号」唯一可靠的依据：
    HAR 里的 Bearer 是 36 位 UUID，本身不含任何号码信息。
    token 失效时接口不报错，只是 userPhone 返回 null，所以拿到空串就当失败。
    """
    try:
        import requests
        s = requests.Session()
        s.trust_env = False
        r = s.get(FJ_LUCK_INDEX,
                  params={"appId": 26, "timestamp": int(time.time() * 1000)},
                  headers={"Authorization": "Bearer " + tok, "User-Agent": BASS_UA},
                  timeout=timeout)
        d = (r.json() or {}).get("data") or {}
        return str(d.get("userPhone") or "")
    except Exception:
        return ""


def apply(text, key=None):
    """解析并写回配置，返回人类可读的报告行列表。"""
    try:
        p = parse_har_text(text)
    except Exception as e:
        return [f"解析失败：{e}"]

    report = []
    try:
        _assert_writable()
    except RuntimeError as e:
        return [f"导入中止：{e}"]

    # ---- qwhdhub 写 configs/<尾号>.json ----
    for phone, a in sorted(p["qwhd"].items()):
        out = BASE / "configs" / f"{phone[-4:]}.json"
        cfg = _read_json(out)
        body = a.get("body") or {}
        cfg.update({
            "app_token": a["token"],
            "phone": phone,
            "province_code": str(body.get("provinceCode") or cfg.get("province_code") or "591"),
            "city_code": str(body.get("cityCode") or cfg.get("city_code") or "0596"),
            "carrier_operator": str(body.get("carrierOperator") or cfg.get("carrier_operator") or "002"),
            "app_version_code": str(body.get("appVersionCode") or cfg.get("app_version_code") or "12.5.7"),
        })
        cfg.setdefault("activity_id", "1021122301")
        cfg.setdefault("channel_id", "P00000109876")
        _write_json(out, cfg)
        report.append(f"qwhdhub {phone} → configs/{out.name}（token {len(a['token'])} 字符）")

        # 记录签发时刻，让寿命探测能算出精确分钟数
        # HAR 的 startedDateTime 是 UTC（如 2026-11-20T04:09:25.123Z），要 +8 小时转本地时间
        issued = _issued_local(a.get("ts"))
        if issued:
            try:
                import probe_token_life as PT
                PT.mark_issued(issued, phone=phone)
                report.append(f"  ↳ 票据签发时刻 {issued}，已写入寿命探测记录")
            except Exception as e:
                report.append(f"  ↳ 记录签发时刻失败：{e}")

    if not p["qwhd"]:
        hint = "（未找到 appTokenLogin 请求）"
        if p["mobiles"]:
            # 只显示前几个，否则 HAR 里几百个号码会刷屏
            shown = "、".join(p["mobiles"][:3])
            more = f" 等 {len(p['mobiles'])} 个" if len(p["mobiles"]) > 3 else ""
            hint += f"，HAR 里出现的号码：{shown}{more}"
        report.append("qwhdhub 无可用 token " + hint)

    # ---- BASS 写 configs_fj ----
    if p["bass"]:
        tok = p["bass"]["token"]
        targets = _fj_targets()
        phones = list(p["qwhd"].keys())
        if not phones and key:
            kf = BASE / "configs" / f"{key}.json"
            ph = str(_read_json(kf).get("phone") or "")
            if PHONE_RE.fullmatch(ph):
                phones = [ph]
        if not phones and len(p["mobiles"]) == 1:
            phones = p["mobiles"]

        hit = next((targets[ph] for ph in phones if ph in targets), None)
        masked = ""
        if hit is None:
            # 线索 3：问接口「我是谁」。福建活动抓包没有 qwhdhub 号码，
            # 没有这一步就永远匹配不上 fj1/fj2。
            masked = _masked_phone_of_bearer(tok)
            if masked:
                tail = masked[-4:]
                hit = next((f for ph, f in targets.items() if ph[-4:] == tail), None)
                if hit:
                    report.append(f"BASS 归属：接口回显 {masked} → {hit.name}")
        if hit is None and phones:
            # 号码已知、但还没有对应配置（全新安装就是这种情况）：直接建一份。
            # 早先这里只报「请在页面上选择账号」，可下拉框本身就是空的，
            # 选什么都没用 —— 首次导入永远走不通。
            ph = phones[0]
            hit = BASE / "configs_fj" / f"{ph[-4:]}.json"
            if not hit.exists():
                _write_json(hit, {
                    "bearer_token": "", "app_id": "0", "prize_prefer": "flow",
                    "referer": "https://wap.fj.10086.cn/bass-cultivateGameServer/",
                    "phone": ph, "note": "", "bark_url": "", "serverchan_sendkey": "",
                })
                report.append(f"BASS 首次导入，已新建 configs_fj/{hit.name}（{ph}）")
        if hit is None:
            report.append(
                f"BASS 拿到 token {tok[:8]}…，但认不出属于哪个号码："
                f"接口没回显号码（token 可能已失效）。请在导入按钮旁选择账号；"
                f"若列表是空的，先到「凭证与账号 → 新建账号」建一份福建活动配置并填上手机号。"
            )
        else:
            cfg = _read_json(hit)
            if cfg.get("bearer_token") == tok:
                report.append(f"BASS {hit.name} 已是该 token，跳过")
            else:
                cfg["bearer_token"] = tok
                if phones and not str(cfg.get("phone") or ""):
                    cfg["phone"] = phones[0]
                _write_json(hit, cfg)
                report.append(f"BASS {hit.name}（{cfg.get('phone')}）→ Bearer {tok[:8]}…")
            # 「写了」不等于「能用」，写进去之后再问一次接口
            if not masked:
                masked = _masked_phone_of_bearer(tok)
            if masked:
                report.append(f"✓ BASS 实测可用，接口显示 {masked}")
            else:
                report.append(f"✗ BASS 实测不可用（token 已过期），请重新抓包")
    else:
        report.append("BASS 无可用 token（未找到 wap.fj.10086.cn 的 Authorization: Bearer）")

    report.extend(_verify(report))
    return report


def _verify(report):
    """导入后实测一次，让「导入成功」不等于「票能用」。

    用户反馈过「界面显示导入了，但配置没变、任务还是失败」的情况，
    根因是 onefile exe 把配置写到了临时解包目录。
    所以这里真登录一次：能登录才算真的导入成功。
    """
    out = []
    touched = []
    for line in report:
        if line.startswith("qwhdhub ") and "→ configs/" in line:
            name = line.split("→ configs/")[1].split("（")[0].strip()
            touched.append(BASE / "configs" / name)

    for path in touched:
        try:
            import cmcc_sign as CS
            cfg = CS.Config(str(path))
            CS.exchange_session(cfg)
            out.append(f"✓ {path.name} 实测登录成功，票据可用")
        except Exception as e:
            msg = str(e).split("——")[0].strip()[:110]
            out.append(f"✗ {path.name} 实测登录失败：{msg}")

    if touched and not out:
        out.append("（未能实测验证，请到总览页刷新状态复核）")
    return out


def scan_dir(directory=None):
    """扫描目录下的 *.har，逐个解析并返回 (报告行, 命中文件数)。

    按修改时间**升序**处理（旧 → 新）：后写的覆盖先写的，所以必须让最新的
    HAR 最后落地。早先是倒序，目录里放两份 HAR 时旧 token 会覆盖新 token。

    directory 为 None 且没指定时，会自动去「项目目录 / 下载 / 微信接收目录」
    找候选 HAR —— 抓包文件从微信直接拖过来时也能用，不必再手动挪位置。
    """
    if directory:
        d = Path(directory)
        files = sorted([f for f in d.glob("*.har")], key=lambda p: p.stat().st_mtime)
        if not files:
            return [f"{d} 下没有 .har 文件，请把抓包文件放到该目录，或用「选择文件」导入。"], 0
    else:
        files = find_har_files()
        if not files:
            return ["没找到 HAR 文件（已查：项目目录 / 下载 / 微信接收目录），"
                    "请用「选择文件」导入。"], 0
        # 升序处理，最新的最后落地 → 覆盖旧 token
        files = list(reversed(files))

    return scan_files(files)


def scan_files(files):
    """按给定顺序导入若干个 HAR（命令行直接指文件时用，不再全目录扫）。"""
    lines = []
    used = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            p = parse_har_text(text)
        except Exception as e:
            lines.append(f"{f.name}：解析失败（{e}）")
            continue
        if not p["qwhd"] and not p["bass"]:
            lines.append(f"{f.name}：未含目标请求，跳过")
            continue
        used += 1
        lines.append(f"—— {f.name} ——")
        lines.extend(apply(text))
    return lines, used


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg:
        p = Path(arg)
        if p.is_file():
            # 用户明确点了某一份，就只导这一份 —— 早先这个参数被忽略，
            # 结果「拖进来一份新 HAR」实际把整个目录的旧 HAR 又跑了一遍。
            res, n = scan_files([p])
        elif p.is_dir():
            res, n = scan_dir(str(p))
        else:
            print(f"路径不存在：{arg}")
            sys.exit(2)
    else:
        res, n = scan_dir()
    print("\n".join(res))
    print(f"\n共处理 {n} 个有效 HAR")
