"""
har_import.py —— 从 HAR 里同时提取 qwhdhub app_token 与 BASS Bearer token，写回配置

设计要点：
  · qwhdhub 的 token 来自 appTokenLogin 请求体（JSESSIONID=..; UID=..; ticketID=..），
    手机号从 userCheckId（十六进制）反解，反解失败时在请求体里正则兜底。
  · BASS 的 token 来自任意 wap.fj.10086.cn / app.fmcc.com.cn 请求的 Authorization 头，
    形如 Bearer <36位UUID>。
  · 全国票据以新票实测登录，福建按候选逐一验证并核对归属，再写配置。
  · 只写 token 相关字段，其余配置原样保留。
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from common import (atomic_write_json, app_base, find_har_files, notify,
                    seed_platform_stats, stats_key, update_json)

FJ_LUCK_INDEX = "https://wap.fj.10086.cn/bass-preferentialAreaServer/luck/index"
BASS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Mobile/15E148/wkwebview")

BASE = app_base()

QWH_MARK = "appTokenLogin"
BASS_HOSTS = ("wap.fj.10086.cn", "app.fmcc.com.cn")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PHONE_RE = re.compile(r"1[3-9]\d{9}")
# 脱敏号码，如 173****4167
MASKED_RE = re.compile(r"1[3-9]\d\*\*\*\*\d{4}")
# 没写进任何凭证时对源 HAR 的统一说明
KEPT_HINT = "保留文件以便重试"


def _headers(req):
    out = {}
    for h in (req.get("headers") or []):
        name = str(h.get("name") or "").lower()
        if name:
            out[name] = str(h.get("value") or "")
    return out


def _match_existing_phone(placeholder):
    """173XXXX4167 → 在 configs/ 里找前3位和后4位都对得上的完整号码。"""
    t = str(placeholder or "")
    if "XXXX" not in t:
        return ""
    pre, tail = t.split("XXXX", 1)
    for f in sorted((BASE / "configs").glob("*.json")):
        ph = str(_read_json(f).get("phone") or "")
        if PHONE_RE.fullmatch(ph) and ph.startswith(pre) and ph.endswith(tail):
            return ph
    return ""


def _unmask_to_phone(masked):
    """173****4167 → 173XXXX4167（中间四位用 X 占位）。

    服务端只给脱敏号码，没法还原完整手机号；这里保留尾号用于匹配已有配置，
    完整号码靠 app_token 实测登录后由接口回显。
    """
    t = str(masked or "")
    if not t:
        return ""
    digits = re.sub(r"\D", "", t)
    if len(digits) < 7:
        return ""
    return digits[:3] + "XXXX" + digits[-4:]


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
    bass_candidates = {}
    mobiles = set()
    # x-token 兜底：APP 已登录时不再发 appTokenLogin，但每个 wx.10086.cn 请求
    # 都带 x-token 头，里面就是同一张 App 票据（JSESSIONID=..; UID=..; ticketID=..）
    xtoken_fallback = {}
    # 脱敏号码线索：/qwhdhub/api/mark/user/info 的 nickName 形如 173****4167
    masked_hint = ""

    for e in entries:
        req = e.get("request") or {}
        url = str(req.get("url") or "")
        ts = str(e.get("startedDateTime") or "")
        hdr = _headers(req)
        origin = urlsplit(url)
        national = origin.scheme == "https" and origin.hostname == "wx.10086.cn"

        for m in PHONE_RE.finditer(url):
            mobiles.add(m.group(0))

        # ---- qwhdhub ----
        if national and QWH_MARK in origin.path:
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

        # ---- qwhd 响应里的脱敏号码（归属判定用） ----
        if national and "qwhdhub" in origin.path:
            rtxt = str(((e.get("response") or {}).get("content") or {}).get("text") or "")
            if rtxt:
                m = MASKED_RE.search(rtxt)
                if m and (not masked_hint or origin.path.endswith("/mark/user/info")):
                    masked_hint = m.group(0)

        # ---- x-token 兜底 ----
        xt = hdr.get("x-token") or ""
        if "JSESSIONID" in xt.upper() and national:
            if xtoken_fallback.get("token") is None or ts >= xtoken_fallback["ts"]:
                xtoken_fallback = {"token": xt, "url": url, "ts": ts}

        # ---- 响应里的 ticketID 时刻 = 票据签发时间 ----

        # ---- BASS ----
        auth = hdr.get("authorization", "")
        if (origin.scheme == "https" and origin.hostname in BASS_HOSTS
                and auth.lower().startswith("bearer ")):
            tok = auth[7:].strip()
            if UUID_RE.fullmatch(tok):
                if ts >= bass_candidates.get(tok, {}).get("ts", ""):
                    bass_candidates[tok] = {"token": tok, "url": url, "ts": ts}

    # appTokenLogin 没抓到时，用 x-token 兜底。归属靠脱敏号码，
    # 脱敏号码也读不到就先归到一个占位号，让调用方用接口反查。
    if not qwhd and xtoken_fallback.get("token"):
        phone = _unmask_to_phone(masked_hint)
        qwhd[phone or "?"] = {"token": xtoken_fallback["token"],
                              "body": {}, "ts": xtoken_fallback["ts"],
                              "masked": masked_hint,
                              "from_xtoken": True}
        if phone:
            mobiles.add(phone)

    candidates = sorted(reversed(list(bass_candidates.values())), key=lambda row: row["ts"], reverse=True)
    return {"qwhd": qwhd, "bass": next(iter(candidates), None),
            "bass_candidates": candidates, "mobiles": sorted(mobiles),
            "masked": masked_hint}


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
                  timeout=timeout, allow_redirects=False)
        d = (r.json() or {}).get("data") or {}
        return str(d.get("userPhone") or "")
    except Exception:
        return ""


def _verify_national(candidate):
    import cmcc_sign as CS

    cfg = CS.Config(None)
    for field in ("phone", "app_token", "province_code", "city_code", "carrier_operator",
                  "app_version_code", "activity_id", "channel_id"):
        setattr(cfg, field, candidate[field])
    session, referer = CS.exchange_session(cfg, use_jwt_cache=False)
    with session:
        CS.query_markstatus(session, referer)


def _verify_bass(cfg):
    import cmcc_fj as FJ

    with FJ.new_session(cfg) as session:
        # 用只读的 index_info 验票，不用 sign_info：后者依赖签到前置任务，
        # 新号/当天未开签到会回 1600「前置任务未完成」，把有效票误判成失效。
        # index_info 只要 bearer 合法就成功；失效票回 401（见 api_get/_norm）。
        if not FJ.call(session, cfg, "index_info", retries=0).get("ok"):
            raise RuntimeError("BASS 验票失败")


def _write_verified(path, cfg, field, patch):
    """保留其它字段，避免等待验票时覆盖另一进程新写入的凭证。"""
    def merge(current):
        if current.get(field) != cfg.get(field) or current.get("phone") != cfg.get("phone"):
            raise RuntimeError("配置已变化，请重试")
        current.update(patch)
    update_json(path, merge, indent=2)


def _mark_verified(key, field, report):
    stamp = datetime.now().strftime("%m-%d %H:%M")
    plat = "qwhdhub" if field == "token_qwhd" else "BASS"
    skey = stats_key(plat, key)
    try:
        def merge(stats):
            seed_platform_stats(stats, skey).update({field: "ok", field + "_at": stamp})
        update_json(BASE / "gui_data" / "stats.json", merge)
    except (OSError, ValueError, TypeError, AttributeError):
        report.append(f"✗ {key}：新票已保存，状态标记更新失败")


def _same_phone(phone, observed):
    if PHONE_RE.fullmatch(observed):
        return phone == observed
    return bool(MASKED_RE.fullmatch(observed)
                and phone[:3] == observed[:3] and phone[-4:] == observed[-4:])


def _import_national(parsed, report):
    for phone, entry in sorted(parsed["qwhd"].items()):
        if "XXXX" in phone:
            phone = _match_existing_phone(phone)
        if not PHONE_RE.fullmatch(phone):
            report.append("✗ 全国签到：无法确认账号，请抓取包含账号信息的活动页")
            continue
        path = BASE / "configs" / f"{phone[-4:]}.json"
        old = _read_json(path)
        if old.get("phone") and old["phone"] != phone:
            report.append(f"✗ 全国 {path.stem}：尾号对应多份配置，未更新")
            continue
        body = entry.get("body") or {}
        candidate = {
            "app_token": entry["token"], "phone": phone,
            "province_code": str(body.get("provinceCode") or old.get("province_code") or "591"),
            "city_code": str(body.get("cityCode") or old.get("city_code") or "0596"),
            "carrier_operator": str(body.get("carrierOperator") or old.get("carrier_operator") or "002"),
            "app_version_code": str(body.get("appVersionCode") or old.get("app_version_code") or "12.5.7"),
            "activity_id": old.get("activity_id") or "1021122301",
            "channel_id": old.get("channel_id") or "P00000109876",
        }
        try:
            _verify_national(candidate)
            _write_verified(path, old, "app_token", candidate)
        except Exception as e:
            reason = "App 票据已过期" if "过期" in str(e) else type(e).__name__
            report.append(f"✗ 全国 {path.stem}：实测登录或保存失败（{reason}），保留原配置")
            continue
        report.append(f"✓ 全国 {path.stem}：实测登录成功，已更新配置")
        issued = _issued_local(entry.get("ts"))
        if old.get("app_token") != candidate["app_token"]:
            try:
                import probe_token_life as PT
                PT.mark_issued(issued or None, phone=phone)
            except Exception:
                report.append(f"✗ 全国 {path.stem}：新票已导入，寿命探测记录更新失败")
        _mark_verified(path.stem, "token_qwhd", report)


def _known_phones():
    """所有已知完整号码：福建配置 + 全国配置 + HAR 里的全国票据。

    BASS bearer 回查只给脱敏号（如 178****9243），必须拿完整号去匹配归属。
    新号往往只有全国 configs/ 有完整号、configs_fj/ 还没建，所以全国配置
    也必须纳入，否则认不出账号，无法自动新建福建配置。
    """
    out = set(_fj_targets())
    for directory in ("configs", "configs_fj"):
        for f in sorted((BASE / directory).glob("*.json")):
            phone = str(_read_json(f).get("phone") or "")
            if PHONE_RE.fullmatch(phone):
                out.add(phone)
    return out


def _import_bass(parsed, key, report):
    targets = _fj_targets()
    known = _known_phones()
    known.update(phone for phone in parsed["qwhd"] if PHONE_RE.fullmatch(phone))
    if key and re.fullmatch(r"[\w-]+", str(key)):
        for directory in ("configs", "configs_fj"):
            phone = str(_read_json(BASE / directory / f"{key}.json").get("phone") or "")
            if PHONE_RE.fullmatch(phone):
                known.add(phone)
    if len(parsed["mobiles"]) == 1:
        known.add(parsed["mobiles"][0])

    imported = set()
    failed = set()
    unknown = 0
    for entry in parsed["bass_candidates"]:
        token = entry["token"]
        observed = _masked_phone_of_bearer(token)
        owners = {phone for phone in known if _same_phone(phone, observed)}
        if not owners and PHONE_RE.fullmatch(observed):
            owners.add(observed)
        if len(owners) != 1:
            unknown += 1
            continue
        phone = owners.pop()
        if phone in imported:
            continue
        path = targets.get(phone) or BASE / "configs_fj" / f"{phone[-4:]}.json"
        old = _read_json(path)
        if old.get("phone") and old["phone"] != phone:
            failed.add(phone)
            continue
        patch = {"bearer_token": token, "phone": phone}
        if not old:
            patch.update(app_id="0", prize_prefer="flow",
                         referer="https://wap.fj.10086.cn/bass-cultivateGameServer/")
        try:
            _verify_bass({**old, **patch})
            _write_verified(path, old, "bearer_token", patch)
        except Exception:
            failed.add(phone)
            continue
        imported.add(phone)
        action = "已更新" if old else "已新建"
        report.append(f"✓ BASS {path.stem}：实测可用，{action}配置")
        _mark_verified(path.stem, "token_bass", report)

    for phone in sorted(failed - imported):
        report.append(f"✗ 福建尾号 {phone[-4:]}：验证或保存失败，保留原配置")
    if unknown and not imported:
        report.append("✗ BASS：凭证失效或无法核实账号。首次导入时请先新建账号或同时抓全国签到页")
    elif unknown:
        report.append(f"BASS 已跳过 {unknown} 份未能核实的候选凭证")


def report_outcome(report):
    """返回（有导入成功项，有失败项）。缺少另一平台的可选凭证不算失败。"""
    return (any(line.startswith("✓ ") for line in report),
            any(line.startswith("✗ ") for line in report))


def notify_import_problem(report):
    """每次导入汇总推送，避免把 HAR 内容、路径或票据放进通知。"""
    ok, failed = report_outcome(report)
    if ok and not failed:
        return False
    title = "HAR 部分导入失败" if ok else "HAR 导入失败"
    safe_lines = [line for line in report if line.startswith("✗ ")]
    return notify(title, "\n".join(safe_lines) or "没有找到可导入的凭证。")


def _finish_with_source(report, source_path, success):
    """导入结束后处理源 HAR：写进了凭证、或里面一个凭证都没有，就删；
    找到了凭证却没写进去（验证失败 / 写配置失败）、解析失败，才保留以便重试。

    success 只认「至少一份凭证真正写进 configs/ 或 configs_fj/」——
    由 report_outcome() 的 ✓ 行判定；状态标记失败等 ✗ 不影响这个结论。
    删除失败只写报告、不抛异常：不加 ✗ 前缀，避免把已成功的导入误报成失败。
    """
    if not source_path:
        return report
    if not success:
        report.append(KEPT_HINT)
        return report
    source = Path(source_path)
    try:
        source.unlink()
    except OSError:
        report.append(f"抓包文件 {source.name} 删除失败（可能被占用），请手动删除")
    else:
        report.append(f"已删除抓包文件 {source.name}")
    return report


def apply(text, key=None, source_path=None):
    """解析并验证候选票据，再更新对应配置。

    source_path 不为空时：至少一份凭证真正写入就删除该 HAR 源文件；
    解析失败 / 没抓到可用 token / 写配置失败则保留文件，并在报告里说明。
    """
    try:
        p = parse_har_text(text)
    except (ValueError, AttributeError, TypeError):
        return _finish_with_source(["✗ HAR 解析失败，请重新导出"], source_path, False)
    try:
        _assert_writable()
    except (RuntimeError, OSError):
        return _finish_with_source(["✗ 项目配置目录不可写"], source_path, False)

    report = []
    _import_national(p, report)
    _import_bass(p, key, report)
    if not p["qwhd"]:
        report.append("qwhdhub 无可用 token（未抓到 App 登录票据）")
    if not p["bass_candidates"]:
        report.append("BASS 无可用 token（未抓到福建 Bearer）")
    # 一个凭证都没有的 HAR 留着也没用，还带着手机号等隐私，直接删
    no_creds = not p["qwhd"] and not p["bass_candidates"]
    return _finish_with_source(report, source_path,
                               report_outcome(report)[0] or no_creds)


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
        except Exception:
            lines.append("✗ HAR 解析失败，请重新导出")
            lines.append(KEPT_HINT)
            continue
        if not p["qwhd"] and not p["bass"]:
            lines.append(f"{f.name}：未含目标请求，跳过")
            _finish_with_source(lines, f, True)
            continue
        used += 1
        lines.append(f"—— {f.name} ——")
        lines.extend(apply(text, source_path=f))
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
    print(f"\n共处理 {n} 个 HAR")
    notify_import_problem(res)
