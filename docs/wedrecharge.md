# 周三充值日（hlwyxhdhub · wedrecharge）

福建/全国号码在 `wx.10086.cn` 的「周三充值日」活动：做任务攒抽奖次数 → 抽拼图碎片 →
集齐解锁话费券；另有秒杀场次。

> 数据来源：2026-11-20 抓包（`ProxyPin11-20_04_09_25.har`，3239 号）。
> 标注「**未抓到调用**」的端点是只出现了入口/查询、没出现实际提交请求的。

## 入口

```
GET https://wx.10086.cn/hlwyxhdhub/act-wedrecharge/1024101716     活动页
GET https://wx.10086.cn/hlwyxhdhub/api/wedrecharge/_pub/rec_login  进页握手
```

## 端点一览

| 端点 | 方法 | 作用 |
|---|---|---|
| `/api/wedrecharge/queryActivityInfo` | POST | 活动总览：轮次开关、奖品池、**服务器时间** |
| `/api/wedrecharge/querySignActInfo` | POST | 签到态、当前奖品、任务列表 |
| `/api/wedrecharge/signFinishTask` | POST | 完成「签到打卡」类任务 |
| `/api/wedrecharge/queryFloorInfo` | POST | 楼层信息（fshd / qwad / banners） |
| `/api/wedrecharge/queryYwFloorInfo` | POST | 楼层信息（另一变体） |
| `/api/wedrecharge/_pub/getFloorRuleInfo` | GET | 楼层规则说明 |
| `/api/wedrecharge/_pub/getContactInfo` | GET | 客服信息 |
| `/api/wedrecharge/queryDrawPrizeInfo` | POST | 抽奖态 + **服务器时间** |
| `/api/wedrecharge/queryShareImgInfo` | POST | 分享图信息 |
| `/api/wedrecharge/getPrizeList` | POST | 奖品列表（拼图碎片） |
| `/api/wedrecharge/queryChInfo` | POST | 抽奖页信息 + 服务器时间 |

请求体统一是 `{}`（POST 空 JSON），鉴权走 qwhdhub 的会话
（`x-token` 头 / `QWHD_SESSION_TOKEN` cookie，referer 指向活动页）。

## 关键字段

### queryActivityInfo

```json
{"code": "0", "msg": "success", "data": {
  "currentTime": 1795118765075,
  "qy1": 1, "qy3": 1, "qy4": 1, "qy5": 1,
  "w3": 6, "w1": 6,
  "wuyouPrize": [{"round": "w3", "prizeName": "机票代金券", "name": "满300元可用",
                  "prize": "15", "status": 6},
                 {"round": "w1", "prizeName": "酒店代金券", "name": "满300元可用",
                  "prize": "30", "status": 6}],
  "qyPrize": [{"round": "qy1", "prizeName": "美团", "name": "188券包", "status": 1, "img": "..."}]
}}
```

- `currentTime` 是**服务端毫秒时间戳**，秒杀对点时用它校准本地时钟
- `qy1/qy3/qy4/qy5`、`w1/w3` 是各轮次的状态开关与档位编号
- `status` 6 = 已结束/不可参与，1 = 可参与（**具体语义待更多样本确认**）

### querySignActInfo

```json
{"code": "0", "msg": "success", "data": {
  "isTodaySign": "1",
  "rdStatus": 4,
  "prizeInfo": {"name": "充值折扣券", "shortName": "8折", "subTitle": "充值50元及以下可用",
                "stock": "350310", "sendStatus": null, "winTime": null, "getTime": null},
  "signTimes": "1",
  "lastSignDay": "20261120",
  "isBroken": "0", "isEndMonth": "0",
  "taskList": [{"taskId": "ljdk7", "title": "参与周三充值日活动，手动参与打卡",
                "channel": "app", "order": 7, "...": "..."}]
}}
```

- `isTodaySign`：今日是否已签到
- `rdStatus`：轮次状态（抓到 1 与 4 两种，语义待确认）
- `prizeInfo.stock`：当前奖品库存
- `taskList[].taskId` 形如 `ljdk7`，配合 `signFinishTask` 完成任务

### queryDrawPrizeInfo / queryChInfo

```json
{"currentTime": 1795118750729, "rdStatus": 1}
```

同样带 `currentTime`（服务器时间）。

## 实测到的行为

- `signFinishTask` 提交后返回纯文本 `"success"`（不是 JSON）
- `getPrizeList` 返回 `{"sdc": false, "prizeList": []}`——该号当时没有碎片
- 活动时间戳 `1795118765075` 对应 2026-11-20 04:06 本地，与本机时钟一致

## 未抓到的部分

以下环节 HAR 里**只有查询、没有实际提交**，接口名与参数待补：

- **抽奖**（消耗次数、抽碎片）
- **拼图集齐后的领券**
- **秒杀下单**（对应 qwhdhub 的 `markSeckill/*`）

要补齐需要再抓一次包：进活动 → 做任务 → 实际抽一次 → 集齐后领券。

---

## 已实现：`cmcc_wed.py`（2026-11-20 13:0x）

抽奖与领券的接口路径来自公开实现（[yupaiLy/cmcc-auto-checkin](https://github.com/yupaiLy/cmcc-auto-checkin)
的 `cmcc_wed.py`）与本地抓包交叉核对，**尚未用本地有效票据实测**，标记为「待验证」。

### 端点（实现所用，成功码是字符串 `"0"`）

| 端点 | 方法 | 作用 | 状态 |
|---|---|---|---|
| `/api/wedrecharge/queryActivityInfo` | POST | 进页登记 + 服务器时间 | 已抓包 |
| `/api/wedrecharge/queryPictureActInfo` | POST | 剩余次数、当前拼图、碎片 | 公开实现，待验证 |
| `/api/wedrecharge/taskList` | POST | 任务清单（status=2 今日不可做） | 公开实现，待验证 |
| `/api/wedrecharge/finishTask` | POST | 完成任务上报 `{"taskId":""}` | 公开实现，待验证 |
| `/api/wedrecharge/drawPicture` | POST | 抽一次 `{"round":"dp1"}` | 公开实现，待验证 |
| `/api/wedrecharge/receiveNew` | POST | 集齐领券 `{"round":"r6/r5/r4"}` | 公开实现，待验证 |

### 玩法与档位

- 拼图档位 `dp1=95折`(4片) → `dp2=9折`(6片) → `dp3=8折`(9片)，依次解锁不可跳级
- 领取 round 与拼图序号**不同源**：`dp1→r6`、`dp2→r5`、`dp3→r4`
- 每次抽奖消耗 1 次机会，碎片随机获得

### 行为边界（有意为之）

| 任务 | 处理 |
|---|---|
| 查询 / 浏览类任务 | 直接上报 `finishTask`，成功即 +1 次 |
| `yqyh` 邀请助力 | **跳过**（需真实用户） |
| `czhf` 充值满 10 元 | **跳过**（涉及真实消费，不代做） |
| `llym` 签到并浏览 5 秒 | **跳过**（需 App 内真实操作） |
| `dlhd` 每日登录 | 进页自动完成，不重复上报 |

- 当前拼图已集齐但还有剩余次数时**主动停手**，不把机会浪费在已完成的拼图上
- `--dry-run` 只查询：不 finishTask、不 drawPicture、不 receiveNew
  （但建立会话与 `queryActivityInfo` 进页登记仍可能被服务端记作「每日登录」）

### 用法

```bash
python cmcc_wed.py --config configs/3239.json --dry-run   # 只查状态
python cmcc_wed.py --config configs/3239.json              # 做任务 + 抽奖 + 领券
python run_all.py --wed-only --dry-run                      # 批量（所有 configs）
python run_all.py --wed                                     # 附加到日常运行
```

## 相关

- qwhdhub 侧接口见 [qwhdhub.md](qwhdhub.md)
- 同类项目的实现可参考 [yupaiLy/cmcc-auto-checkin](https://github.com/yupaiLy/cmcc-auto-checkin)
  的 `cmcc_wed.py`，但它对 `exchange_session` 有 `act_url` 参数要求，
  本项目尚未支持（见 qwhdhub.md 的说明）
