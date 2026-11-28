# qwhdhub（wx.10086.cn）接口

全国签到平台，凭证是 App 原生票据 `app_token`，形如：

```
JSESSIONID=<uuid>; UID=<32位hex>; Comment=SessionServer-unity; Path=/;HTTPOnly; ticketID=NingBo
```

- 有效期约 **30 分钟**，实测 jwt 续期失败，只能重新获取
- **精确寿命探测**：`probe_token_life.py` 每 2 分钟探一次（计划任务 `MobileTokenLifeProbe`），
  签发时刻取自 HAR 里 `appTokenLogin` 的时间戳，结论精确到分钟。
  看结论：`python probe_token_life.py --report`
- `ticketID` 是城市标识（如 `NingBo`、`Luoyang`、`POD9`）
- `province_code` / `city_code` / `carrier_operator` 等字段从登录响应里取

### app_token 本质是「中国移动 App 自己的会话 cookie」

HAR 里 appTokenLogin 的请求体 token 形如：

```
JSESSIONID=<uuid>; UID=<32位hex>; Comment=SessionServer-unity; Path=/;HTTPOnly; ticketID=ShanDong; Secure
```

这与 App 访问 `client.app.coc.10086.cn` 时的 Cookie **完全同格式**（同 UID、同 ticketID 体系）。
即：qwhdhub 的票据 = 中国移动 App 的主站会话，服务端只认它。

**实测（2026-11-20 07:49）**：把 HAR 里 App 主站会话 cookie 直接喂给 `/qwhdsso/appTokenLogin`
→ 一律 `FAILED APP登录状态已过期`。App 主站会话过期后，qwhdhub 票据同样作废，
两者寿命是**同一条**曲线，不存在「App 会话还活着但 qwhdhub 票据先死」的情况。

## 登录

| 端点 | 说明 |
|---|---|
| `POST /qwhdsso/login` | 账号密码登录 |
| `/qwhdsso` | SSO 相关 |

## 签到类

| 端点 | 说明 |
|---|---|
| `POST /qwhdhub/api/diyVideoDayRedesign/sign/doSign` | 每日签到 |
| `GET /qwhdhub/api/diyVideoDayRedesign/sign/querySignStatus` | 查询签到状态（含累计天数） |
| `GET /qwhdhub/api/diySaturdayGame/getClockInStatus` | 周六游戏打卡状态 |
| `POST /qwhdhub/api/diySaturdayGame/clockIn` | 周六游戏打卡 |

## 任务类

| 端点 | 说明 |
|---|---|
| `GET /qwhdhub/api/mark/task/taskList` | 任务列表 |
| `GET /qwhdhub/api/mark/task/taskInfo` | 任务详情 |
| `POST /qwhdhub/api/mark/task/finishTask` | 完成任务 |
| `POST /qwhdhub/api/mark/task/getTaskAward` | 领取任务奖励 |
| `POST /qwhdhub/api/mark` | 标记（活动参与） |

## 拓展活动（`cmcc_extra.py`）

三个 qwhdhub 侧的拓展活动，代码早写好了但**此前从未被调用**（`run_all` 不传 `--games`、
界面无入口），2026-11-20 13:1x 才真正接上：

| 活动 | 入口 | 消耗窗口 |
|---|---|---|
| 周六游戏中心 | `/qwhdhub/diy-client/1126086939` | 游戏币抽奖 **周六 12:00 起**（余额按 31 天清零） |
| 追剧领福利 | `/qwhdhub/diy-client/1126082530` | 抽奖次数**仅周日**开放 |
| 玩游戏抽话费（周任务） | `/qwhdhub/diy-client/1126090514` | 无抽奖，只有每周一刷新的周任务 |

各自的端点：

| 端点 | 说明 |
|---|---|
| `GET /hlwyxhdhub/api/open/_pub/task/getOneTaskInfo` | 活动任务信息 |
| `POST /hlwyxhdhub/api/open/_pub/task/openFinish` | 完成活动任务 |
| `POST /qwhdhub/diyTask/list/<componentId>` | 代币任务清单（`taskStage=UNDO/DONE`） |
| `POST /qwhdhub/diyTask/finish/<taskId>` | 完成代币任务（服务端不校验真实行为） |
| `POST /qwhdhub/diyLottery/period/remain/<componentId>` | 抽奖余额与剩余次数（只读） |
| `GET /qwhdhub/diyLottery/lotterySafely/<componentId>` | 执行一次抽奖 |

入口页：`hlwyxhdhub/act-wedrecharge/index.html?pageId=<活动id>`

### 怎么跑

```bash
python cmcc_sign.py --config configs/3239.json --games          # 单号
python cmcc_sign.py --config configs/3239.json --games --dry-run # 只查余额
python run_all.py --games-only                                   # 全部账号
```

拓展活动已并入 `MobileMorning` → `run_morning.bat`：每天 **11:40** 随签到一起跑（`--games`）。
**周六 12:00** 正是游戏币抽奖窗口开启的时刻；其它日子只报余额、不消耗。
控制台「拓展活动」页也有单号手动执行入口。

## 获取凭证

两条路线：

1. **HAR 导入**（通用）：抓包找 `appTokenLogin` 请求，body 里的 `token` 就是 `app_token`；
   手机号在 `userCheckId` 字段（十六进制编码的十进制号码），由 `har_import.py` 自动反解归位。
2. **手机一键登录**（只适用 BASS）：见 [bass.md](bass.md) 的 Termux 方案，
   换出的是 BASS 凭证，**不能用于 qwhdhub**。

> 短信验证码登录（`login.10086.cn` H5 链路）曾实现过，但该页面在 2026-05 改版后
> 加入了设备指纹校验，`sendRandomCodeAction.action` 稳定返回 `6`、
> `loadSendflag.htm` 返回空，已不可用，故移除相关代码。

### App 内切号的坑

`appTokenLogin` 的响应里会下发 `data.jwt` 和 `QWHD_SESSION_TOKEN`，而 **App 内切号会让
另一个号的 `app_token` 立即失效**。多号请分设备登录，或一次只抓一个号。

### act_url 参数

`exchange_session()` 目前不支持自定义 `act_url`（活动页地址）。跑周三充值日这类
**独立活动**时需要它——同类项目的实现（`yupaiLy/cmcc-auto-checkin` 的 `cmcc_wed.py`）
已经要求 `exchange_session(cfg, act_url=...)`，本项目尚未跟进。

### jwt 续期（**已实测证伪，注意**）

`appTokenLogin` 的请求体里 `jwtToken` 与 `token` 同时出现，App 端两个值都是现成的。
曾以为「jwt 是账号级长期凭证、可以免票据续期」，**2026-11-20 用对照实验证伪了**：

| 组合 | 结果 |
|---|---|
| A. `jwtToken` + `token=""` | FAILED　APP登录状态已过期 |
| B. `jwtToken` + 伪造的 token | FAILED　APP登录状态已过期 |
| C. `jwtToken` + 真实 app_token | **SUCCESS** |

服务端真正校验的是 **`token`（App 票据）**，`jwtToken` 单独用通不过。
所以「用缓存 jwt 免票据续期」这条路当前**不成立**，`cmcc_sign.py` 里第②步仅作兜底保留。

实际链路：

```
app_token（抓包所得，约 30 分钟）
  → appTokenLogin(jwtToken=旧jwt, token=app_token)
  → 响应 data.jwt（新 jwt）+ 下发 QWHD_SESSION_TOKEN
  → 后续 API 带 x-token 头 / QWHD_SESSION_TOKEN cookie
```

- `x-token` 头在 HAR 里出现 60+ 次，App 的 qwhdhub 请求基本都带它
- `QWHD_SESSION_TOKEN` 同时也会落在 cookie 里，本项目用 cookie 方式（已验证可用）
- 登录成功后服务端会下发**新的 jwt**（与传入的不同），仍写回 `.cmcc_jwt_cache_<尾号>.json`
- **jwt 续期回落机制**：执行时若日志显示 `[INFO] jwt 续期失败...，回落 appTokenLogin 引导`，属于预期的优雅降级机制——程序优先尝试使用上次缓存的 jwt 快速建立会话，若服务端过期则自动平滑回退至使用 `app_token` 重新调用 `appTokenLogin`；只要后续输出 `活动会话建立成功`，即代表整个会话刷新与鉴权完全成功，无需人工介入。

**净效果：登录依赖 app_token，定时重跑只能延后失效，不能真正续命。**
只能靠提高抓包频率（实测 04:05 抓的票据，04:15 仍可用）来维持。

## 限时秒杀（markSeckill）

| 端点 | 说明 |
|---|---|
| `POST /qwhdhub/api/mark/markSeckill/secConfig` | 秒杀场次配置（本次抓包连接被重置，未取到内容） |
| `POST /qwhdhub/api/mark/markSeckill/todayMarkStatus` | 当天秒杀状态，实测返回 `{"markStatus": "marked"}` |

## 周三充值日

另见 [wedrecharge.md](wedrecharge.md) —— 独立活动，接口自成一套
（`/hlwyxhdhub/api/wedrecharge/*`），并带服务器时间戳可用于秒杀对点。

## 注意事项

- **App 内切号会让另一个号的 `app_token` 失效**，多号请分设备登录。
- 奖品统计只算带 MB/GB 的流量券，话费券单独记。
