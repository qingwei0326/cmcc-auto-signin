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
- **累计签到阶梯奖励（fullmarkTask）**：当累签天数达到 7/14/21 天等阶段门槛时，接口返回待领取的满签奖励，脚本会自动识别并触发领取，领取的奖励（如流量包、话费券）会实时记入到账台账 `gui_data/awards.json`。

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
  ——注意这条结论**只适用于三档列表**，见下节

### 喂养累计（App 里的「昨日投喂奖励」）

```
GET /bass-cultivateGameServer/index/getNextReceiveTask
```

```json
{"id": 5, "recordId": 499520178, "taskName": "喂养累计", "fodder": 950,
 "taskStatus": "COMPLETED", "currentTask": false,
 "limitInfo": {"limit": 1, "limitType": "DAILY"},
 "limitReceiveTime": "2026-11-20 06:00:00", "expireTime": "2026-11-21 00:00:00"}
```

**实测要点**

- 与三档**不是一套**：三档在 `getDailyReceiveTask`、看 `currentTask`；
  这条在 `getNextReceiveTask`、看 `taskStatus=COMPLETED`（可领未领）
- `expireTime` **当晚 24 点作废**，隔日补领无效；`limitReceiveTime` 为次日 06:00 起可领
- 达标条件见 `getIndexInfo` 的 `nextReceiveNeedFeedCount` / `nextReceiveGetFodder`
- **领取**：`GET /bass-cultivateGameServer/index/nextReceive`，查询参数为 `appId` 和
  `timestamp`。App 抓包中返回 `code=0000`、`success=true`、`data=null`。余额随后
  增加 750 饲料，再查 `getNextReceiveTask` 返回 `1503`。
- 福建活动常规运行和每小时领奖任务都会检查并领取待领的昨日投喂奖励。
  `--dry-run` 只查询。领取超时后，下一轮先重新查询状态再决定是否领取。
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
**注意**：`--continue-stage`（直接升阶）与 `--auto-claim`（领奖）在参数与业务逻辑上**严格互斥**，自动策略配置与命令行调用时只能二选一。

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

**领奖口径（2026-11-24 用户实测修正）**：`receiveStagePrize` 每一阶都是**随机开奖**，包括一阶——传 `prizeId` 服务端也按 `weigh` 随机（当天一阶传 flow=2 仍领到 0.5 元话费券）。故脚本领奖一律无参，`prize` / `prize_prefer` 设置当前无效。六阶特殊：抽出来之后可以「换其他档位」，但该换奖动作的接口尚未抓到，暂未实现；`prize_id_for()` 仍保留正确 id 号段供将来接换奖用。

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

**2026-11-21 App 实测**

- 从中国移动 App 手动打开优惠专区转盘，可触发赠送 **1 次**抽奖。抓包中活动页的首次成功响应已显示「中国移动 APP 中参与」已完成、剩余 1 次。「签到领福利」使用已到账的抽奖次数，App 赠送由用户手动触发。
- 「逛移动爱购商城」单独增加抽奖次数。抓包中该任务由未完成变为已完成，剩余次数从 1 增至 2。任务从活动页面的商城入口实际完成。

**2026-11-21 12:04 抓包（首次跑通 `join`）**

- `index` 回显 `surplusLuckCount:2`、`canJoin:true`，说明免费次数会攒着不清零到当天？——实测当天 12:04 仍有 2 次，`run_luck` 用这 2 次真抽，返回「2-3元话费券手气红包」+「谢谢参与」，抽完回读 `surplusLuckCount:0`。
  所以 `join` 链路（`GET luck/join?appId=26&timestamp=…`）已用真号验证，不再只是推断。
- `taskList` 每次刷新顺序/内容会变（同一份抓包里两次 `index` 的 task 列表 id 不同），所以刷任务只能按当次返回的 `id` 走，不能写死。
- 新增两个与本模块无关但值得记的接口：
  - `POST bass-commonActServer/marathon/H5Api/getTaskInfo` body `{appId,timestamp,id:"100106"}` → 「浏览天天大转盘」，`browseTime:1`（马拉松式任务体系）。
  - `POST bass-commonAreaServer/H5Api/evaluate/evaluateInfo` body `{appId,timestamp,pageType:13,evaluatePageTitle}` → 「请您评一评」，文案写着「提交后可随机获得奖励」，提交接口未抓到，暂不实现。

**2026-11-22 加次数任务实测（此前一直被 `--no-tasks` 跳过）**

四个任务逐个 `doTask` 全部回 `0000`，剩余次数 **0 → 2**：

| taskName | taskType | taskUrl | 备注 |
|---|---|---|---|
| 请好友帮忙 | 3 | null | 没有对端账号，只能直接上报；`--skip-assist` 可跳过 |
| 中国移动APP中参与 | 2 | null | 同上 |
| 浏览调研页面 | 5 | 有 | `cycle:1`，先 GET 页面再上报 |
| 逛移动爱购商城 | 5 | 有 | 先 GET 页面再上报 |

- `complete:true` 表示服务端已记完成，再报也**不会**多给次数，必须跳过
- 「请好友帮忙」是例外：`doTask` 回 `0000 成功`，但**次数不涨、`complete` 一直是
  false** —— 它确实要真人点一下才算。重复上报无害但也没用，嫌脏就加 `--skip-assist`

**2026-11-23 16:20 抓包补全「请好友帮忙」真实助力链路**（此前以为无对端账号，实为搞错了接口体系）：

- `GET /bass-preferentialAreaServer/luck/doTask/3/{id}` 返回的 49 位串**不是没用的码**，
  它是助力 `encodeId`，且**有时效**（过期后 `hmxb/assist/detail` 回 `status=2 已过期`）。
- 真正的助力走**微信小程序网关**，不是 preferentialAreaServer：
  `POST https://wap.fj.10086.cn/weixin/wxAppletService/bass/callService`
  Header 带 `Authorization: Bearer <小程序token>` + `Referer: https://servicewechat.com/wx7f4c967c88ffdb82/261/page-frame.html`：
  - `serviceId=25` `{taskType:"3", encodeId:码}` → 查助力信息，回 `helpUser` + `helpText`
  - `serviceId=28` `{taskType:"3", encodeId:码}` → **提交助力**（好友点「立即帮TA」那一下）
    - 成功：`resp_code=0000`
    - 重复：`resp_code=1501 您已接受过TA的邀请`
- 活动就是「天天大转盘 / 双十二天天抽好礼」（configKey `SSETTCHLGZ`/`SSETTCHLZL`），
  H5 与小程序是同一活动联动。规则原文：「**活动周期内，一位用户只能为同一好友助力 1 次，
  最多可为 3 位不同好友助力**」。即日起至 2026-11-30，每号最多帮 3 个不同好友。
- **卡点：小程序 token 无法用现有 H5 bearer 换取**。H5 `configs_fj/*.json` 的 bearer 调
  callService 一律 `BASS-0001 token过期`；小程序 token 来自
  `POST /weixin/wxAppletService/wx/code2session`（微信 code，一次性）或
  `POST /bass-applet-authServer/auth/loginByHangYan`（openId+大网SSO票配套）。
  实测 `gui_data/guandan_auth.json` 的咪咕 cat（ANDTYZsid…）换不来（回 1001）。
  → 要自动化多号助力，需为每个号维护微信小程序 token，抓包时顺手存下或另找签发途径。
- 抓包样本：`incoming_har/luck_1.json`（发起方 doTask/3 生成码）、
  `incoming_har/luck_2.json`（好友方 serviceId=25 查询→28 提交）。
- 任务**一天只算一次**，重跑 `run_luck` 不会重复薅到次数
- `doTask` 偶尔回 `401 未登陆`（同一张 bearer 前两分钟 `index` 还是好的），
  说明它比 `index` 更早失效；遇到就整轮收手，别继续抽
- 之前 `run_all.run_luck` 写死 `--no-tasks`（README 也写着"不替人做加次数任务"），
  等于每天白扔 2 次抽奖机会。现在由策略项 `luck_tasks` 控制，默认开

---

## 一起消消乐 `bass-commonAreaServer`

```
GET  /bass-commonAreaServer/H5Api/eliminate/getDailyPlayLimit → playToken
POST /bass-commonAreaServer/H5Api/eliminate/playEnd           → 上报通关
GET  /bass-commonAreaServer/H5Api/eliminate/indexInfo         → gameLevel / gold / physicalPower / gameRecordList
POST /bass-commonAreaServer/H5Api/eliminate/playStart         → 真实客户端开局会调（脚本未依赖）
GET  /bass-commonAreaServer/H5Api/eliminate/getTaskInfo       → taskInfoList / gold
POST /bass-commonAreaServer/H5Api/eliminate/doTask  {id}      → 金币任务（taskType=2 浏览类）
GET  /bass-commonAreaServer/H5Api/eliminate/getStoreInfo      → propInfoList（id=12 是体力/爱心）
POST /bass-commonAreaServer/H5Api/eliminate/exchangeProp {id} → 200 金币换 1 点体力
POST /bass-commonAreaServer/H5Api/eliminate/useGold    {gold} → 游戏内金币道具/复活
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
- **关卡必须每次现读 `indexInfo.gameLevel`**。早期写死 49/63，账号涨到 51/64 后仍在报旧关，结果次数照扣、关卡好几天不动；改成每次读真实关卡后实测 64→65、65→66→67→68 正常推进。
- `isOld` 的真实算法是「本地关卡 != 上报关卡」，用当前关卡上报时固定 `False`
- 服务端会把「谢谢参与」也塞进 `prizeName`，不能算中奖、不能进台账
- 金币任务：`taskType=2` 浏览/访问类（先 GET 任务页再 `doTask`）、`3` 分享活动（`doTask` 实测直接成功）都能自动做；
  `1` 好友助力直接 `doTask` 会回 `-100 任务类型有误`；但 `GET eliminate/share/2` 生成助力码后，
  另一个已登录号 `GET eliminate/help/{码}` 实测有效（被助力方 +200 金币）。
  同一对号每月只能互点一次，第二次起回「您本月已接受过TA的邀请」，脚本视为正常跳过。
- 体力：`getStoreInfo` 里 `propType=12`（道具爱心）200 金币/点，每日最多 20 次；`exchangeProp` 实测 200 金币 → `physicalPower +1`

**任务面板周期（用户截图 2026-11-21 16:12）**

| 任务 | 面板周期 | 金币 | 脚本处理 |
|---|---|---|---|
| 请好友助力 | 每天5次 | 200 | `share/2` + 另一号 `help/{码}`；**同一对号每月只能成功 1 次**，成功月份存 `gui_data/xxl_assist_state.json`，之后跳过 |
| 分享活动让大家一起来玩 | 每周一次 | 200 | `doTask` 可直接完成 |
| 浏览天天大转盘 | 每周一次 | 200 | 先打开任务页再 `doTask` |
| 访问环闽寻宝记 | 每周一次 | 200 | 同上 |
| 浏览流量银行 | 每周一次 | 200 | 同上 |
| 浏览券来世界 | 每周一次 | 200 | 同上 |

---

## 红包宝 `bass-commonAreaServer`

```
GET /bass-commonAreaServer/H5Api/hmxb/activity/init
GET /bass-commonAreaServer/H5Api/hmxb/dice/balance
GET /bass-commonAreaServer/H5Api/hmxb/dice/roll
GET /bass-commonAreaServer/H5Api/hmxb/treasure/open
```

掷骰子会消耗次数，属游戏内正常消耗。

**加骰子任务（2026-11-22 实测）**

`task/list` 返回的任务里，`task/report` 只递 `taskCode`，服务端**不校验你是否真做过**，
所以「报不报」的判断只能放在本地 `cmcc_xunbao.task_reportable()`：

| taskCode | 任务名 | 奖励 | 上报条件 |
|---|---|---|---|
| TASK_1501 | 好友助力（每日最多3次） | +1 | **永不自动上报**，要真人点链接 |
| TASK_1502 | 打开消息提醒 | +5 | 不报，要真去开通知权限 |
| TASK_1515 | 参与一起消消乐活动 | +1 | 本轮 `--xxl` **真跑成功**才报（`--did-xxl`） |
| — | 参与天天大转盘 | +1 | 本轮抽过转盘才报（`--did-luck`） |

顺序上，`run_all` 把寻宝排在**转盘 / 消消乐之后**：先投骰子再去做任务，
等于把本轮赚到的骰子留到明天，而骰子当天不用就作废。
「只跑福建活动」与「签到领福利」现在都自带 `--xunbao --xunbao-tasks`，
控制台消消乐页另有「全部账号（并行）」按钮，走 `run_all --platform none --xxl --xunbao`。

**到终点不等于本轮结束（2026-11-22 实测）**

走到第 29/29 格后，服务端把地图**重置回第 1 格**：`activity/init` 返回
`currentSeq=1`、`canRoll=true`，`diceBalance` 保留。所以顺序是
「投到终点 → `treasure/open` 开宝箱 → 重读状态 → 剩下的骰子接着投下一圈」。

注意终点那一瞬间 `canRoll` 是 **false**（等开箱），旧实现只看这个快照就 `break`，
剩下的骰子当天白扔。现改为循环里处理开箱：开完重读，`canRoll` 恢复就继续投；
用 `(seq, total)` 记「这一圈已开过箱」，服务端延迟未重置时只开一次、不死循环。



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
