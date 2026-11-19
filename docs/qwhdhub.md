# qwhdhub（wx.10086.cn）接口

全国签到平台，凭证是 App 原生票据 `app_token`，形如：

```
JSESSIONID=<uuid>; UID=<32位hex>; Comment=SessionServer-unity; Path=/;HTTPOnly; ticketID=NingBo
```

- 有效期约 **30 分钟**，实测 jwt 续期失败，只能重新获取
- `ticketID` 是城市标识（如 `NingBo`、`Luoyang`、`POD9`）
- `province_code` / `city_code` / `carrier_operator` 等字段从登录响应里取

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

| 端点 | 说明 |
|---|---|
| `GET /hlwyxhdhub/api/open/_pub/task/getOneTaskInfo` | 活动任务信息 |
| `POST /hlwyxhdhub/api/open/_pub/task/openFinish` | 完成活动任务 |

入口页：`hlwyxhdhub/act-wedrecharge/index.html?pageId=<活动id>`

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
