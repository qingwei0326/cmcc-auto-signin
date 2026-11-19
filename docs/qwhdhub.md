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

### jwt 续期（重要）

`appTokenLogin` 的请求体里 `jwtToken` **优先于** `token`：

- jwt 是**账号级长期凭证**，服务端校验通过即签发会话，且不受 App 内切换登录影响
- 因此脚本优先用本地缓存的 jwt 免票据续期，`app_token` 只在 jwt 缺失/失效时作引导兜底
- 每次登录响应都会重签 jwt，脚本总是回写缓存（`.cmcc_jwt_cache_<尾号>.json`）

**实测 jwt 存活约 2.5~3.5 小时**。只要在它失效前跑一次脚本，凭证链就能无限延长；
一旦断了（如关机超过数小时），只能重新抓包。

## 注意事项

- **App 内切号会让另一个号的 `app_token` 失效**，多号请分设备登录。
- 奖品统计只算带 MB/GB 的流量券，话费券单独记。
