// Scriptable — iPhone 一键登录拿 BASS token
// 用法：复制本文件全部内容 → Scriptable 新建脚本 → 粘贴 → 保存
// 运行前：关闭 WiFi，确认使用中国移动蜂窝数据
// 运行结果：token 自动复制到剪贴板，并保存到 Scriptable 文档目录

const UA = "Mozilla/5.0 (Linux; Android 11; M2012K11AC Build/RKQ1.200826.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/104.0.5112.97 Mobile Safari/537.36";
const APPID_ONEKEY = "300011950611";
const APPID_FMCC = 20;
const PAGE_INIT = "https://app.fmcc.com.cn/bass-applet-authServer/auth/pageInit";
const GET_SIGN = "https://app.fmcc.com.cn/bass-applet-authServer/numberAbility/getSign";
const VALIDATE = "https://app.fmcc.com.cn/bass-applet-authServer/numberAbility/validateToken";
const GET_MOBILE_HOST = "www.cmpassport.com";
const GET_MOBILE_PATH = "/NumberAbility/h5/getMobile.htm";
const GET_TOKEN = "https://www.cmpassport.com/NumberAbility/h5/getToken.htm";

// 纯 JS base64（Scriptable 各版本通用，不依赖 Data API）
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
function base64Encode(str) {
  let out = "";
  let i = 0;
  while (i < str.length) {
    const c1 = str.charCodeAt(i++);
    const c2 = i < str.length ? str.charCodeAt(i++) : NaN;
    const c3 = i < str.length ? str.charCodeAt(i++) : NaN;
    const e1 = c1 >> 2;
    const e2 = ((c1 & 3) << 4) | ((isNaN(c2) ? 0 : c2) >> 4);
    const e3 = isNaN(c2) ? 64 : (((c2 & 15) << 2) | ((isNaN(c3) ? 0 : c3) >> 6));
    const e4 = isNaN(c3) ? 64 : (c3 & 63);
    out += B64.charAt(e1) + B64.charAt(e2) +
           (e3 === 64 ? "=" : B64.charAt(e3)) +
           (e4 === 64 ? "=" : B64.charAt(e4));
  }
  return out;
}

// 指纹：APK 里 getFingerprint() 的等价实现，服务端会校验（纯 ASCII，上面的实现够用）
const FINGERPRINT = `platform:Linux aarch64@@userAgent:${UA}@@isAndroidOrIOS:Android`;
const USER_INFORMATION = base64Encode(FINGERPRINT);

const log = [];
function line(text) {
  log.push(text);
  console.log(text);
}
function ok(name, detail = "") {
  line(`✓ ${name}  ${detail}`);
}
function fail(name, detail = "") {
  line(`✗ ${name}  ${detail}`);
}

function uuid32() {
  const hex = "0123456789abcdef";
  let s = "";
  for (let i = 0; i < 32; i++) {
    if (i === 12) s += "4";
    else if (i === 16) s += hex[((Math.random() * 4) | 0) + 8];
    else s += hex[(Math.random() * 16) | 0];
  }
  return s;
}

function pad(n, len) {
  return String(n).padStart(len, "0");
}
function ts17() {
  const d = new Date();
  return `${d.getFullYear()}${pad(d.getMonth() + 1, 2)}${pad(d.getDate(), 2)}` +
         `${pad(d.getHours(), 2)}${pad(d.getMinutes(), 2)}${pad(d.getSeconds(), 2)}` +
         `${pad(d.getMilliseconds(), 3)}`;
}
function mathParam() {
  return (Date.now() / 1000).toFixed(3);
}

function fmccHeaders(token) {
  const h = {
    "User-Agent": UA,
    "Content-Type": "application/json",
    "Origin": "https://app.fmcc.com.cn",
    "Referer": "https://app.fmcc.com.cn/bass-equityProvinceH5/"
  };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}
function cmccHeaders() {
  return {
    "User-Agent": UA,
    "Origin": "https://app.fmcc.com.cn",
    "Referer": "https://app.fmcc.com.cn/"
  };
}

async function postJSON(url, body, headers) {
  const r = new Request(url);
  r.method = "POST";
  r.headers = headers;
  r.body = JSON.stringify(body);
  return await r.loadJSON();
}
async function getJSON(url, headers) {
  const r = new Request(url);
  r.headers = headers;
  return await r.loadJSON();
}

async function main() {
  line("中国移动一键登录 → BASS token (iPhone)\n");

  // ① pageInit —— 匿名 Bearer
  let pi;
  try {
    pi = await postJSON(
      `${PAGE_INIT}?math=${mathParam()}`,
      { appId: APPID_FMCC, timestamp: Date.now(), scope: 0,
        url: "https://app.fmcc.com.cn/bass-equityProvinceH5/" },
      fmccHeaders(null)
    );
  } catch (e) {
    fail("pageInit", String(e));
    return;
  }
  const anon = (pi.data && pi.data.token) || "";
  if (!anon) { fail("pageInit", JSON.stringify(pi)); return; }
  ok("pageInit", `匿名 token ${anon.slice(0, 8)}…`);

  const msgId = uuid32();
  const uuidX = uuid32();
  const t17 = ts17();

  // ② getMobile —— 运营商网关识别本机号码（必须蜂窝网）
  const query = `version=1.0&appId=${APPID_ONEKEY}&openType=1&expandParams=` +
                `&msgId=${msgId}&timestamp=${t17}&uuid=${uuidX}` +
                `&userInformation=${encodeURIComponent(USER_INFORMATION)}`;
  // APK 原始用 http://，网关插号只在这个明文入口生效
  // 只发一次：重试会用同一个 UUID，服务端判重复(110041)，反而坏事
  let gm = null;
  let respData = null;
  let ctype = "";
  try {
    const req = new Request(`http://${GET_MOBILE_HOST}${GET_MOBILE_PATH}?${query}`);
    req.headers = cmccHeaders();
    respData = await req.loadData();
    try { ctype = (req.response.headers && req.response.headers["Content-Type"]) || ""; } catch (e) {}
    // 检测 PNG magic: 89 50 4E 47
    const u8 = Array.from(respData).slice(0, 4);
    if (u8[0] === 0x89 && u8[1] === 0x50 && u8[2] === 0x4e && u8[3] === 0x47) {
      line(`getMobile 返回图片 (Content-Type: ${ctype}) —— 疑似图形验证码`);
      try {
        const img = Image.fromData(respData);
        await QuickLook.show(img);
      } catch (e) {
        line(`无法显示图片: ${e}`);
      }
      fail("getMobile", "移动返回图形验证码，一键登录被风控拦截");
      return;
    }
    try { gm = JSON.parse(respData.toString()); } catch (e) { gm = { _raw: respData.toString() }; }
  } catch (e) {
    fail("getMobile", `请求失败: ${e}`);
    return;
  }
  const mcode = (gm.header && gm.header.resultCode) || "";
  // 0000=本次成功；110041=该UUID已注册过（重试/上次跑过），等同于取号完成
  if (mcode !== "0000" && mcode !== "110041") {
    const desc = (gm.body && gm.body.resultDesc) || gm._raw || "";
    fail("getMobile", `${mcode} ${desc}`);
    line("\n  关闭 WiFi、只用移动数据；仍失败就开关一次飞行模式。");
    return;
  }
  ok("getMobile", mcode === "110041" ? "网关已识别(UUID已注册)" : "网关已识别本机号码");

  // ③ getSign
  const busi = APPID_ONEKEY + msgId + t17 + uuidX + "1.0";
  let gs;
  try {
    gs = await postJSON(
      `${GET_SIGN}?math=${mathParam()}`,
      { appId: APPID_FMCC, timestamp: Date.now(), busiParams: busi,
        cccode: uuid32(), portalType: 3 },
      fmccHeaders(anon)
    );
  } catch (e) { fail("getSign", String(e)); return; }
  const sign = gs.data || "";
  if (!sign) { fail("getSign", JSON.stringify(gs)); return; }
  ok("getSign", "签名已获取");

  // ④ getToken —— 一次性登录票
  let gt;
  try {
    gt = await postJSON(GET_TOKEN, {
      header: { version: "1.0", msgId: msgId, timestamp: t17, appId: APPID_ONEKEY },
      body: { expandParams: "", sign: sign, uuid: uuidX }
    }, cmccHeaders());
  } catch (e) { fail("getToken", String(e)); return; }
  const tcode = (gt.header && gt.header.resultCode) || "";
  const oneTicket = (gt.body && gt.body.token) || "";
  if (!oneTicket) { fail("getToken", `${tcode} ${JSON.stringify(gt)}`); return; }
  ok("getToken", `一次性票 ${oneTicket.slice(0, 8)}…`);

  // ⑤ validateToken —— 正式 Bearer + 手机号
  let vt;
  try {
    vt = await postJSON(
      `${VALIDATE}?math=${mathParam()}`,
      { appId: APPID_FMCC, timestamp: Date.now(), portalType: 3, cccode: uuid32(),
        busiParams: { resultCode: tcode, token: oneTicket,
                      userInformation: USER_INFORMATION, message: "success" } },
      fmccHeaders(anon)
    );
  } catch (e) { fail("validate", String(e)); return; }
  const data = vt.data || {};
  const token = data.token || "";
  const phone = data.phone || "";
  if (!token) { fail("validate", JSON.stringify(vt)); return; }
  ok("validate", `手机号 ${phone || "(未返回)"}`);

  // ⑥ 验证 token 能打 BASS 活动接口
  try {
    const v = await getJSON(
      "https://app.fmcc.com.cn/bass-cultivateGameServer/index/getIndexInfo?appId=0&timestamp=" + Date.now(),
      { "User-Agent": UA, "Authorization": `Bearer ${token}`,
        "Referer": "https://app.fmcc.com.cn/bass-equityProvinceH5/" }
    );
    if (String(v.code) === "0000") {
      ok("BASS 验证", `饲料 ${(v.data && v.data.fodderSurplus) || "?"}`);
    } else {
      fail("BASS 验证", `${v.code} ${v.msg}`);
    }
  } catch (e) { fail("BASS 验证", String(e)); }

  // 输出
  line("\n" + "=".repeat(42));
  line(`手机号: ${phone || "(未返回)"}`);
  line(`token : ${token}`);
  line("=".repeat(42));

  let copied = false;
  try { Clipboard.copy(token); copied = true; } catch (e) { /* 忽略 */ }

  // 存一份到 Scriptable 文档目录
  try {
    const fm = FileManager.local();
    const p = fm.joinPath(fm.documentsDirectory(), `bass_${phone.slice(-4) || "token"}.json`);
    fm.writeString(p, JSON.stringify({ phone: phone, token: token }, null, 2));
    line(`已保存到 ${p}`);
  } catch (e) { /* 存盘失败不影响主流程 */ }

  try {
    const alert = new Alert();
    alert.title = "BASS token 已获取";
    alert.message = `${phone}\n\n${token}${copied ? "\n\n已复制到剪贴板。" : ""}`;
    alert.addCancelAction("好");
    await alert.show();
  } catch (e) {
    // Alert 万一不可用，token 已经在上面的日志和剪贴板里
  }
}

await main();
