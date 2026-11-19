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

## 关键发现：会话 cookie 可脱离 app_token 复用

**这是整个功能能落地的关键。**

`appTokenLogin` 拿到的 `QWHD_SESSION_TOKEN` cookie，**单独注入到一个全新的 Session
就能正常调用全部 markSeckill 接口（含 redeem）**，既不需要 app_token，也不需要重新登录：

```python
s = requests.Session()
s.cookies.set("QWHD_SESSION_TOKEN", "<cookie>", domain="wx.10086.cn", path="/")
r = s.post(f"{API_MARK}/markSeckill/secConfig", json={}, headers=api_headers(referer))
# → code=SUCCESS
```

这意味着：

- **早上跑一次签到，会话就备好了**，12 点直接用，不受 app_token 30 分钟寿命限制
- 会话按账号隔离存 `gui_data/seckill_session_<尾号>.json`，不同号不串
- `cmcc_sign.py` 每次签到成功后自动保存会话
- `cmcc_seckill.py` 启动时优先读已保存的会话，探活失败才回落到 app_token 重新登录

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

计划任务 `MobileSeckill` → `run_seckill.bat`：**11:30 启动**，脚本内部等到 12:00 出手。
（选 11:30 而不是 12:00 前几分钟，是因为要给脚本留出校时与心跳的时间。）

## 现状与限制

- 2026-11-20 抓包已确认全部端点与参数，`redeem` 在非时段返回 `NOT_TIME_IN`（预期）
- **真正的开抢时刻尚未实测**（每天只有 12:00 ~ 13:00 一次）
- 库存 `leaveStock` 实测为 `1.0`（1 份），可能很快被抢空
- 资格要求**当日已签到**——脚本会先调 `domark` 补签
- 若某号既无已保存会话、app_token 又失效，脚本会明确提示「请重新抓包」并推送通知
