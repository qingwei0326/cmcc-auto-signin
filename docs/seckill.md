# 签到有礼 · 每周秒杀（markSeckill）

活动：签到页面（活动号 `1021122301`）里的「每周秒杀」，**每天 12:00 开抢**，
奖品是 **1 元话费券**，**限抢 1 次**。抢到的券进 App「我的奖品」核销。

实现：`cmcc_seckill.py`

## 接口链路

| 顺序 | 端点 | 作用 |
|---|---|---|
| 1 | `POST /qwhdhub/api/mark/markSeckill/sysTime` | 服务器毫秒时间，用于校准本机时钟 |
| 2 | `POST /qwhdhub/api/mark/markSeckill/secConfig` | 秒杀场次（secId / prizeId / 开抢时间 / 库存） |
| 3 | `POST /qwhdhub/api/mark/markSeckill/todayMarkStatus` | 当日签到状态 = 参与资格 |
| 4 | `POST /qwhdhub/api/mark/markSeckill/redeem` | 真正抢购，参数 `{"secId","prizeId"}` |

`secConfig` 响应结构：

```json
{"data":{"secKillData":{
  "secKillZones":[
    {"id":5,"startDate":"2026-11-20","endDate":"2026-11-20",
     "startTime":"1795147200000","endTime":"1795150800000",
     "dateType":1,"weekDays":null,
     "prize":{"id":"2021015393","name":"1元话费券","leaveStock":1.0,"status":null,
             "imgSrc":"...","useUrl":""}}],
  "mainTitle":"每周秒杀","subtitle":"限抢1次"}}}
```

- `startTime` / `endTime` 是**毫秒时间戳**，实测 12:00:00 ~ 13:00:00
- `prize.leaveStock` 是剩余库存，**为 0 就别打了**（对着空库存狂打容易触发风控）
- `prize.status == "1"` 表示**该账号本场已中奖**，前端按钮态是「去使用」，不用再抢

## redeem 响应判定

| status | 含义 | 处理 |
|---|---|---|
| `code=SUCCESS` | 抢到 | 停止，推送通知 |
| `NOT_TIME_IN` | 未开抢（打早了） | 继续，**0.05 秒**后重试 |
| `PRIZE_NO_STOCK` | 券抢完 | 停止 |
| `ZONE_TODAY_COMPLETE` | 今日已中过 | 停止（按已抢到处理） |
| `PRIZE_LIMIT_DAY` / `PRIZE_LIMIT_MONTH` | 限次 | 停止 |
| `PRIZE_RESTRIC_LIMIT` | 活动期间次数达顶 | 停止 |
| `WORK_ORDER_RESTRIC_LIMIT` | 工单限流（风控） | 停止 |
| `PARAM_ERROR` | 参数异常 | 停止（秒Id / prizeId 不对） |
| `TRAFFIC_LIMITED` | **参与人数太多**（瞬时限流） | **继续重试**（不消耗库存） |

### `TRAFFIC_LIMITED` 实测（2026-11-20 12:03 真实开抢）

第一次真正打到开抢时段的 redeem，返回的是这个码：

```json
{"code":"FAILED","msg":"参与人数太多","status":"TRAFFIC_LIMITED","data":null,"success":false}
```

它与 `PRIZE_NO_STOCK` 有本质区别：

| | 含义 | 库存 | 该不该再打 |
|---|---|---|---|
| `TRAFFIC_LIMITED` | 瞬时限流，人太多 | **不消耗** | 该继续重试 |
| `PRIZE_NO_STOCK` | 券被抢完 | 已消耗 | 停手 |

因为它不消耗库存，所以**没有**放进 `STOP_STATUSES`——不作为终态，
落在「已开抢」的常规重试分支里继续打是正确的。

12:03 → 12:04 的库存曲线印证了这一点（库存是被别人抢走的，不是被这次请求打掉的）：

```
12:03:19  leaveStock = 0.204825
12:03:30  leaveStock = 0.165300   ← 本次 redeem 返回 TRAFFIC_LIMITED
12:04:02  leaveStock = 0.048275
12:04:31  leaveStock = 0.000000   ← 约 70 秒售罄
```


## 关键发现：会话 cookie 可脱离 app_token 复用（但只有 2 小时寿命）

**这是整个功能能落地的关键，也是它的硬约束。**

`appTokenLogin` 拿到的 `QWHD_SESSION_TOKEN` cookie，**单独注入到一个全新的 Session
就能正常调用全部 markSeckill 接口（含 redeem）**，既不需要 app_token，也不需要重新登录：

```python
s = requests.Session()
s.cookies.set("QWHD_SESSION_TOKEN", "<cookie>", domain="wx.10086.cn", path="/")
r = s.post(f"{API_MARK}/markSeckill/secConfig", json={}, headers=api_headers(referer))
# → code=SUCCESS
```

这意味着：

- 会话不依赖 app_token 的 30 分钟寿命，可以留到中午用
- 会话按账号隔离存 `gui_data/seckill_session_<尾号>.json`，不同号不串
- `cmcc_sign.py` 每次签到成功后自动保存会话
- `cmcc_seckill.py` 启动时优先读已保存的会话，探活失败才回落到 app_token 重新登录

### ⚠️ 但会话本身只有 2 小时寿命（实测，两次独立探测一致）

```
探测一（每 10 分钟探活）
  04:22:27  建立 → 存活
  06:12:39  存活
  06:22:39  失效  code=GOTO_LOGIN      ← 正好 120 分钟

探测二（每 5 分钟探活）
  04:26:53  建立 → 存活
  06:22:16  存活
  06:27:17  失效  code=GOTO_LOGIN      ← 正好 120 分钟
```

**请求不能延长它**（两个探测都在持续发请求，仍是准时 2 小时失效）。
失效时服务端返回 `GOTO_LOGIN`（不是 401），本项目的探活函数认这个码。

试过的续命手段，全部无效：

| 手段 | 结果 |
|---|---|
| 持续调用接口 | ❌ 仍准时 2 小时失效 |
| 带 `jwtToken` 请求头 | ❌ `GOTO_LOGIN` |
| 重新访问 referer 里带 SSO token 的活动页 | ❌ 302，cookie 不变 |

### 因此：签到时刻决定能不能赶上 12 点

会话寿命 2 小时 → 建立时刻 + 120 分钟必须覆盖 12:00。
既然如此，就**没必要提前一两个小时启动**：把刷新和开抢都挪到临近 12 点即可，
凭证窗口最短、失败也最容易当场补救。

所以计划任务分两个：

```
MobileSessionRefresh   11:54   run_session_refresh.bat  → 签到并建立新会话
MobileSeckill          11:55   run_seckill.bat          → 脚本内部等到 11:59:50 出手
```

11:54 那次签到**仍然需要有效的 app_token**（票据实测约 30~46 分钟寿命）。
若那时票据已过期，就必须你抓一次包导入 —— 这是目前无法自动化的唯一环节。

### 启动时没有会话不再直接退出（2026-11-20 12:5x 修复）

旧版本在**启动阶段**拿不到会话就 `return 1`，脚本活不到 12:00 —— 12 点那一刻
根本没有调度器在跑。现在改成：

1. 每 15 秒重试一次拿会话，直到 `11:59:50 + 60 秒` 才放弃
2. **每次重试都重读配置文件**（`Config.config_path` → `reload_config()`），
   所以等待期间你导入的新 HAR 会被下一轮自动识别，不需要重启任务
3. 放弃时才推送「会话缺失」通知

实测（真实网络，票据已过期）：连续重试 3 次、每次都重新登录并重读配置，
最后按截止时间如实报错，不再"启动即退出"。

> ⚠️ **会话按账号隔离是必须的**：早期版本用共享文件，导致 4167 复用了 3239 的会话，
> 券会记到 3239 头上。已改为 `seckill_session_<尾号>.json`。

## 时钟校准

服务器时间比本机**快约 1 秒**（实测偏移 -990 ~ -999 ms）。秒杀要对点，
所以用 `sysTime` 返回值算偏移量：

```python
offset_ms = server_now_ms - time.time() * 1000
target_local = start_srv_ms / 1000 - offset_ms / 1000 - lead
```

`lead` 默认 0.4 秒，用于抵消网络延迟（提前出手）。

## 抢购策略

1. 开场后**密集重试**：`NOT_TIME_IN` 时间隔 0.05 秒，其它情况 0.35 秒
2. 每 2 秒核对一次库存（`leaveStock`），**为 0 立刻停手**
3. 默认最多 6 发（`--max-attempts`）
4. 等待期间每 8 分钟发一次心跳（`todayMarkStatus`）续会话，并回写保存

## 用法

```bash
python cmcc_seckill.py --config configs/3239.json --dry-run   # 只查场次/校时/资格
python cmcc_seckill.py --config configs/3239.json --once      # 立即打一次看响应格式
python cmcc_seckill.py --config configs/3239.json             # 等下一场自动抢
python cmcc_seckill.py --config configs/3239.json --at 11:59:50  # 手动指定开抢时刻
```

集成：

```bash
python run_all.py --seckill                    # 一键里带秒杀
python run_all.py --seckill --seckill-at 11:59:50
```

计划任务 `MobileSeckill` → `run_seckill.bat`：**11:55 启动**，脚本内部等到 12:00 出手。
（只提前 5 分钟，因为会话刷新任务 11:54 刚跑过；会话未就绪时会每 15 秒重试到临近开抢。）

## 现状与限制

- 2026-11-20 抓包已确认全部端点与参数，`redeem` 在非时段返回 `NOT_TIME_IN`（预期）
- **2026-11-20 12:03 已完成真实开抢实测**（见上文 `TRAFFIC_LIMITED` 一节）：
  唯一一次 redeem 返回「参与人数太多」，**未中奖**（`prize.status` 非 `"1"`、`leaveStock` 最终归零）
- **2026-11-21 12:02 真实开抢**：`--once` **一发即中**，`code=SUCCESS`，
  奖品 1 元话费券（prizeId=2021015394，pcardId=6789370278996696，status=UNUSED），
  服务端 `secConfig.prize.status` 由 null 变为 `"1"`。**中奖已实测确认** ✅
- 库存 `leaveStock` 实测为 `1.0`（1 份），**实测约 70 秒售罄**（12:03:19 → 12:04:31）
- 资格要求**当日已签到**——脚本会先调 `domark` 补签
- 若某号既无已保存会话、app_token 又失效，脚本会明确提示「请重新抓包」并推送通知
- `markSeckill` 下**没有奖品记录接口**（2026-11-20 实测 8 个候选端点，
  2026-11-21 扩测至约 120 个路径 / 多个 10086 子域，全部「签到有礼转发异常」或「插件不存在」），
  中奖核销只能到 App「我的奖品」人工看；程序化判断中没中奖的唯一依据是
  `secConfig` 里的 `prize.status == "1"`

### ⚠️ 场次 id 与 prizeId 每天递增，绝不能硬编码

实测三天：

| 日期 | secId | prizeId | 场次窗口 |
|---|---|---|---|
| 2026-11-20 | 5 | 2021015393 | 12:00:00 ~ 13:00:00 |
| 2026-11-21 | **6** | **2021015394** | 12:00:00 ~ 13:00:00 |
| 2026-11-22 | **7** | **2021017302** | 12:00:00 ~ 13:00:00 |

`cmcc_seckill.py` 一律从 `secConfig` 动态选场次（`pick_zone()`），**不要**把
`secId=5 / prizeId=2021015393` 写进任何调用方——写死会在第二天变成 `PARAM_ERROR`
或打在错误场次上。

### ⚠️ 计划任务当前不会真正执行脚本（2026-11-21 实测）

7 个 `Mobile*` 计划任务已注册的 Action 是：

```
签到控制台.exe "D:\...\run_hidden.py" "D:\...\run_seckill.bat"
```

但 **`签到控制台.exe` 是 GUI 外壳（`app_desktop.py`），不解析 argv**——
给它传 `run_hidden.py` 它照样只开 GUI 窗口，**不会**执行 bat。

证据：

- `gui_data/startup.log` 今天 08:30 / 11:54 / 11:55 / 12:05 各有一次
  「启动 + 服务已监听 http://127.0.0.1:8765」→ 是 GUI 被拉起来了
- 而 `logs/session_refresh.log`、`logs/seckill.log`、`logs/games.log`
  **今天零写入**（mtime 仍停在 11-20）→ bat 一次都没跑

后果：**12:00 那一刻没有任何调度器在跑秒杀**，券是靠手动 `--once` 打出去的。

`dashboard.py` 的 `_sched_ps()` 现在生成的是 `-Execute '{pythonw}'`（无窗口 pythonw），
已注册的 7 个任务还是旧的 exe 版本 → 需要在控制台点一次
**「一键配置全部计划任务」**重新注册。

### ✅ dry-run 顺带就会刷新会话（省事）

`cmcc_sign.py --dry-run` 也会走 `exchange_session()` → `save_activity_session()`，
所以在控制台点一次「刷新全部账号状态（只读）」就等于建立了一份 2 小时有效的活动会话，
不必单独跑会话刷新任务。
（2026-11-21 的会话就是 11:51:15 一次 dry-run 刷新顺带写下的。）

### ✅ 已修复的调度缺陷（2026-11-20 13:0x）

两个计划任务的**预期设计**与**实际行为**曾经不符：

| 任务 | 触发 | 设计意图 | 当时实际 |
|---|---|---|---|
| `MobileSessionRefresh` | 10:15 | 签到并建会话，覆盖到 12:15 | ❌ **两个号全部失败**（app_token 过期，0/2） |
| `MobileSeckill` | 10:30 | 启动后内部等到 11:59:50 出手 | ❌ **在等到 11:59:50 之前就退出**（return 1） |

**根因**：`cmcc_seckill.py` 在启动阶段就要先拿到可用会话，拿不到就直接 `return 1`，
脚本根本活不到内部等待的那一刻 —— **12:00 那一刻没有任何调度器在跑**。

**现在的行为**：

| 项 | 现状 |
|---|---|
| 会话拿不到 | 每 15 秒重试，直到 `11:59:50 + 60 秒` 才放弃；不再启动即退出 |
| 等待期间导入 HAR | 每轮重试都重读配置，新票据自动生效，不用重启任务 |
| `MobileSessionRefresh` | 10:15 → **11:54**（只提前 6 分钟，凭证窗口最短） |
| `MobileSeckill` | 10:30 → **11:55**（提前 5 分钟即可） |
| `run_all.seckill_timeout()` | 按 `--at` 距现在的时间动态计算，11:55 启动也够覆盖等待与抢购 |

