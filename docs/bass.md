# BASS（wap.fj.10086.cn）接口

福建移动活动平台。所有请求都是 **GET/POST + Bearer token + appId + 13 位时间戳**，
无签名、无加密参数（消消乐除外）。

## 鉴权

- 凭证：`Authorization: Bearer <36 位 UUID>`，取自任意 `wap.fj.10086.cn` 请求头
- **有效期实测：至少 20.6 小时仍在存活**（fj1 于 11-19 11:13 抓取，
  11-20 07:46 实测 `code=0000` 依然有效；fj2 于 11-19 23:34 抓取，
  11-20 07:46 仍有效，已存活 8.2 小时）
  → BASS 是本项目里**寿命最长、最不需要频繁抓包**的凭证
- **多个子服务共用同一套 bass 鉴权**：`bass-cultivateGameServer`（养成）、
  `bass-commonActServer`（流量银行）、`bass-preferentialAreaServer`（抽奖）
  用同一个 token 都能调通
- 例外：`luck/index` 的请求头 Bearer 只能调 `luck/index` 本身，调其他接口一律 `401 未登陆`；
  业务接口要用 `loginByHangYan` 返回的那个 token

### 换票接口（实测可用，但依赖已失效的上游 token）

BASS 的 bearer 并不是 App 直接下发的，而是**用 App 的「航眼 token」换来的**：

```
POST /bass-applet-authServer/auth/loginByHangYan
{"appId":26,"timestamp":<13位毫秒>,"openId":"1231","token":"YZsidssolg<40位hex>"}
→ {"code":"0000","data":{"bind":true,"token":"<36位UUID>","phone":"184****3239","userId":"..."}}
```

航眼 token 来自中国移动 App 的 `p.10086.cn/wap/v1/entrance/login`（`authToken` 字段）。

**实测结论（2026-11-20 07:46）**：
- 拿 11-19 抓的航眼 token 去换 → `code=1001`，**失败**（航眼 token 本身已过期）
- 拿**现有的有效 bearer** 调 `auth/pageInit` → `code=0000`，返回同一个 token，`bind:true`
  → 说明 bearer 可以自我续命，只要它还活着就一直有效

**结论**：换票路径本身是通的，但航眼 token 比 bearer 短命，
所以**长期仍以「HAR 导入 bearer」为主**；换票接口目前无法独立于抓包使用。

---

## 养成游戏 `bass-cultivateGameServer`

### 首页

```
GET /bass-cultivateGameServer/index/getIndexInfo
```

```json
{"feedCountToday": 30, "feedCount": 455, "fodderSurplus": 250,
 "nextReceiveGetFodder": 50, "nextReceiveNeedFeedCount": 5,
 "first": false, "openHelp": true}
```

- `fodderSurplus`：当前饲料余额
- `feedCountToday` / `feedCount`：今日 / 累计喂食次数
- `nextReceiveNeedFeedCount` + `nextReceiveGetFodder`：**再喂 N 次、次日可领 M 饲料**（实测 5 次 → 50g）

### 连续签到

```
GET  /bass-cultivateGameServer/sign/getSignInfo
POST /bass-cultivateGameServer/sign/doSign
```

```json
{"todaySign": false, "openSignTime": false, "signBeginHour": 6, "cycleToday": 2,
 "signDateList": [
   {"dateStr": "20261119", "cycleDay": 1, "fodder": 600, "today": false, "sign": true},
   {"dateStr": "20261120", "cycleDay": 2, "fodder": 900, "today": true,  "sign": false},
   {"dateStr": "20261121", "cycleDay": 3, "fodder": 1200,"today": false, "sign": false}
 ]}
```

**实测要点**

- `signBeginHour: 6` —— **签到 6 点才开放**，凌晨调用返回 `1502 未到可签到时间`
- 奖励**连续累进**：600g → 900g → 1200g，**断一天 `cycleDay` 重置回 1**
- `signDateList` 直接给出未来几天的奖励，控制台显示的就是这份数据
- `todaySign` 为 true 表示今日已签

### 每日奖励三档

```
GET  /bass-cultivateGameServer/index/getDailyReceiveTask
POST /bass-cultivateGameServer/index/dailyReceive
```

| 档位 | 饲料 | 间隔 |
|---|---|---|
| 阶段一 | 150 | — |
| 阶段二 | 250 | 60 分钟 |
| 阶段三 | 350 | 60 分钟 |

**实测要点**

- 判断该领哪一档看 `currentTask: true`，**不是** `taskStatus`
- `taskStatus` 只有 `RECEIVED`（已领）和 `UN_DO`（未领），**不存在 `COMPLETED`**
  （早期版本用 `COMPLETED` 判断，导致每次只能领到一档）
- 没到间隔时间返回 `1504 不要着急!还未到可领取时间`
- 服务端直接给 `limitReceiveTime`（该档可领时刻）和 `surplusTime`（剩余秒数），
  不需要自己按 60 分钟推算
- 三档当天 24 点过期（`expireTime`）

### 阶段与喂养

```
GET  /bass-cultivateGameServer/stage/getUserStageInfo
GET  /bass-cultivateGameServer/stage/getStagePrizes
GET  /bass-cultivateGameServer/stage/feed            ← 单次喂养（一次 50 饲料）
GET  /bass-cultivateGameServer/stage/continuousStage  ← ★ 阶段满后"继续喂养"升阶
POST /bass-cultivateGameServer/stage/receiveStagePrize
POST /bass-cultivateGameServer/stage/batchFeed        ← 批量喂养（一次 250 饲料）
```

- `batchFeed` 一次消耗 250 饲料、算 **5 次**喂食、阶段进度 +250
- `feed` 一次消耗 50 饲料、算 **1 次**喂食（App 上点一次就是它）
- **每日喂食上限 50 次**（`index_info.feedCountToday` 到 50 就不再增长），
  即 `batchFeed` 约 10 次 / `feed` 约 50 次
- 阶段：`stageInfo.stageName` + `feedNeedFodderCount`（一阶 5000、二阶 10000…），当前进度 `feedFodders`
- 每阶 3 档奖品：`1=话费券 2=流量券 3=折扣券`，奖品 id = `(阶段-1)*3 + 档位`

#### ★ 阶段满时的 `code=133` 与两条出路（2026-11-20 12:36 抓包实测）

阶段进度打满后，再点喂养会返回：

```json
{"code":"133","msg":"请先选择阶段奖励或继续喂养","data":null}
```

服务端给了**两条路**：

1. **领阶段奖励**：`GET /stage/receiveStagePrize?prizeId=<id>`（当前实现走这条）
2. **继续喂养升阶**：`GET /stage/continuousStage`（**本次新发现**，无参数）

`continuousStage` 实测响应：

```json
{"code":"0000","data":{"recordId":6133490,
  "stageInfo":{"stage":1,"stageName":"二阶","feedNeedFodderCount":10000,"reselect":0},
  "status":0,"feedFodders":0,"prizeDto":null},"success":true}}
```

→ 调用后**直接升到下一阶、进度归零**，等价于放弃本阶奖品换取继续喂养。
这是 `--auto-claim` 之外处理"阶段满"的第二种选择，适合不想要奖品、只想冲高阶的情况。

#### 完整奖品表（6 阶，getStagePrizes 实测）

| 阶段 | 话费券(id) | 流量券(id) | 折扣券(id) |
|---|---|---|---|
| 一阶 | 0.5元 (1) | 500MB月包 (2) | 9.9折 (3) |
| 二阶 | 1元 (4) | 1GB日包 (5) | 9.8折 (6) |
| 三阶 | 2元 (7) | 1GB月包 (8) | 9.7折 (9) |
| 四阶 | 4元 (10) | 2GB月包 (11) | 9.5折 (12) |
| 五阶 | 7元 (13) | 5GB月包 (14) | 9.3折 (15) |
| 六阶 | 10元 (16) | 10GB月包 (17) | 9折 (18) |

每项都带 `stockEnough` / `expire` / `weigh`（权重 1/2/3 对应话费/流量/折扣）。

### 福袋

```
GET /bass-cultivateGameServer/luckyBag/nearTrigger
```

```json
{"isNearTrigger": false, "nextTriggerCount": 300,
 "currentFeedCount": 151, "needMoreFeeds": 149}
```

**每累计喂食 300 次触发一次**（不是固定 50/100 的进度条）。

### 任务

```
GET  /bass-cultivateGameServer/task/getUserTasks
POST /bass-cultivateGameServer/task/doTask/{id}
```

`doTask` 直接 POST 即记完成（等价于空报浏览）。

---

## 流量银行 `bass-commonActServer`

```
GET  /bass-commonActServer/flowBank/index        → 可兑流量、点数、hasTaskToDo
GET  /bass-commonActServer/flowBank/taskInfo     → 任务列表
POST /bass-commonActServer/flowBank/doTask       → body {appId, timestamp, taskId}
```

**实测要点**

- `completeStatus`：`0`=未做（要点两次 0→2→1）、`2`=进行中（点一次即完成）、`1`=已完成
  —— 只处理 `0` 会漏掉卡在 `2` 的任务
- `taskCycle`：**1=每月一次，2=每周一次**
- 服务端**不返回上次完成时间**，脚本自行观测并写入 `gui_data/flowbank_state.json`
- **周期门控**（2026-11-20 起）：周任务只在**本周一之后**、月任务只在**本月 1 号之后**
  才会尝试，没到周期直接跳过 —— 少打无意义请求，也躲开 `1006` 频控。
  判断依据是本地记录的 `last_done`；`completeStatus=2`（进行中）不受周期限制，必须先收尾。
  需要强制全部尝试时加 `--fb-all`。
  服务端没有完成时间，所以本地记录是推定值；若实际重置日不是周一/1 号，
  最坏结果是周期初试一次失败、之后每天重试到真正重置为止（不会漏做）。
- `index` 的 `hasTaskToDo` **不可靠**，任务全完成时它仍可能为 `true`；
  判断有没有活干要看 `taskInfo` 里 `completeStatus` 的分布
- 频控码 `1006`（操作太快），调用间隔 3.5~5.5 秒足够

---

## 优惠专区抽奖 `bass-preferentialAreaServer`

```
GET /bass-preferentialAreaServer/luck/index              → 剩余次数 + taskList + 奖品池
GET /bass-preferentialAreaServer/luck/doTask/{type}/{id} → 完成任务换次数（直接 GET 即记完成）
GET /bass-preferentialAreaServer/luck/join               → 抽奖
GET /bass-preferentialAreaServer/luck/qrySubscription     → 订阅状态
```

**实测要点**

- 三个接口**全是 GET**，只需 `appId=26` + `timestamp`，`join` 不需要任何业务参数
- 用 POST 会返回 `Request method 'POST' is not supported`
- `join` 返回 `1600 用户已无抽奖次数` 是真的没次数，不是缺参数
- `doTask` 的 `taskType` 取自 `index` 的 `taskList`（如 3=请好友帮忙、5=浏览调研）
- 任务奖励就是抽奖次数，实测刷 4 个任务能把次数从 0 刷到 1~3
- `qrySubscription` 返回 `subscriptionStatus:false, first:true` 表示未订阅 + 首抽

---

## 一起消消乐 `bass-commonAreaServer`

```
GET  /bass-commonAreaServer/H5Api/eliminate/getDailyPlayLimit → playToken
POST /bass-commonAreaServer/H5Api/eliminate/playEnd           → 上报通关
```

**实测要点**

- `gameLevel` / `score` 是本地 **AES-CBC** 加密：
  key=`8cc70bf2a955c6bf`、iv=`1692bc56e8b783f4`、ZeroPadding、明文前拼 9 位随机字母
- `score` 其实是星级（0~3），`gameLevel` 是关卡号
- 中奖由服务端抽奖池决定，**与 level/score 无关**，实测满星也是「谢谢参与」
- **每天实际约 5 点体力**（`getDailyPlayLimit` 返回的 `dayLimit=100` 是假上限），
  用完返回 `-100 您的体力不足，无法进行挑战快去购买体力后再发起挑战吧!`
- 体力不足时 `indexInfo` 等接口也返回 `-100`，**探测关卡失败不代表 token 失效**
- 每次上报前要重新取一次 `playToken`

---

## 红包宝 `bass-commonAreaServer`

```
GET /bass-commonAreaServer/H5Api/hmxb/activity/init
GET /bass-commonAreaServer/H5Api/hmxb/dice/balance
GET /bass-commonAreaServer/H5Api/hmxb/dice/roll
GET /bass-commonAreaServer/H5Api/hmxb/treasure/open
```

掷骰子会消耗次数，属游戏内正常消耗。

### 补充接口（2026-11-20 抓包新增）

| 端点 | 说明 |
|---|---|
| `POST /bass-commonAreaServer/H5Api/hmxb/prize/list` | 奖品列表 |
| `POST /bass-commonAreaServer/H5Api/hmxb/activity/status` | 活动状态 |
| `POST /bass-commonAreaServer/H5Api/hmxb/marquee/list` | 跑马灯（滚动公告） |

### 其他子服务

| 端点 | 说明 |
|---|---|
| `POST /bass-unifyDocsConfigServer/client/getDocsConfig` | 统一文档配置（configKey 见下表） |
| `POST /bass-scatteredAdsServer/H5Api/scatteredAds/advertise/space/detail/list/get` | 广告位详情（key=`QLSJZMDQLS` 本次返回"key 不存在"） |
| `GET /bass-activityH5/pages/nationalDay2026/index` | **国庆活动页**（2026 新活动，未实现） |
| `GET /bass-equityServer/H5/user/getUserMemberInfo` | 会员信息：`opened=false` 表示未开会员 |
| `GET /bass-commonAreaServer/marathon/H5Api/getTaskInfo` | 任务详情，body `{"appId":26,"timestamp":ms,"id":"100106"}`（浏览天天大转盘） |

### 12:36 抓包新发现的其他接口（未实现，价值低）

| 端点 | 实测结果 | 价值 |
|---|---|---|
| `GET /stage/feed` | `code=133 请先选择阶段奖励或继续喂养` | 单次喂养（50 饲料），已由 batchFeed 覆盖 |
| `GET /stage/continuousStage` | `code=0000` 升到二阶、进度归零 | **有用**，见上文"阶段满两条路" |
| `GET /returnReward/getReturnRewardInfo` | `showPopup=false, days=[]` | 回流奖励，本号无数据 |
| `GET /interaction/getInteractionInfo` | 返回饥饿/OTHER/满足三类宠物台词 | 纯文案，无价值 |
| `GET /index/getMeetTask` | `code=1606 非新人` | 新人任务，本号不适用 |
| `GET /luck/qrySubscription` | `subscriptionStatus=false` | 抽奖订阅状态 |

#### unifyDocsConfig 的 configKey（本次抓到）

本次 18 次调用全是这几个 key：

- `TTZPHD01` = 大转盘配置（含 cover / originUrl 等，configContent 是 JSON 字符串）
- `TTZPHD02` = 不存在
- `QLSJHDGZ` = 券来世界活动规则（活动至 2026-11-30）
- `QLSJHDFX` = 活动分享文案
- `QLSJHDZL` = 活动助力文案
- `SSETTCHLGZ` / `SSETTCHLHB` / `SSETTCHLH5` / `SSETTCHLZL` / `SSETTCHLLX` = 双十二天天抽好礼（规则/红包/H5分享/助力/拉新）
- `ZGYDKHFWXY` = 中国移动客户服务协议
- `H5YSZC` = H5 隐私政策入口文案
- `TTZPPZZLFX` = 天天转盘膨胀助力分享

> 活动规则里写明：券来世界 / 双十二活动都到 **2026-11-30** 结束。
> 这些是运营配置，不是可自动化的领奖接口，归档备查。

---

## 手机一键登录（免抓包）

Termux 跑 `pull_token_phone.py --save`，链路：

```
pageInit → getMobile → getSign → getToken → validateToken
```

- `getMobile` **必须蜂窝网**，PC 会返回 `网关未插入手机号`
- iPhone 可用 `pull_token_iphone.js`（Scriptable）
