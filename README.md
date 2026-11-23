# 中国移动签到自动化 · cmcc-auto-signin

> 两条活动线 + 一个小游戏 + 一个抽奖，一套工具全跑完。
> 图形控制台 + 多账号并发 + 定时任务 + 失败推送。

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()

---

## 功能一览

| 平台 | 域名 | 管什么 | 凭证 | 寿命 |
|---|---|---|---|---|
| **qwhdhub** | `wx.10086.cn` | 全国签到、月签、群签、任务、秒杀 | `app_token` = `JSESSIONID=…; UID=…; ticketID=…` | 约 30 分钟（分钟级实测） |
| **BASS** | `wap.fj.10086.cn` | 福建养成游戏、流量银行、消消乐、优惠抽奖 | `Bearer token` = 36 位 UUID | **≥20 小时**（实测仍存活） |

**已覆盖的活动**

| 活动 | 说明 | 每日收益 |
|---|---|---|
| 全国签到 | qwhdhub 每日/每月/群签、任务、秒杀 | 流量 + 话费券 |
| 养成游戏签到 | BASS 连续签到，6 点开放 | 600g → 900g → 1200g 累进 |
| 每日奖励三档 | 阶段一/二/三，各间隔 60 分钟 | 750 饲料/天 |
| 流量银行 | 浏览类任务 + 点数兑换 | 实测 352MB → 1.5GB |
| 福袋 | 每累计喂食 300 次触发 | 随机 |
| 优惠抽奖 | 转盘，刷任务换次数 | 流量券 / 话费券 |
| 一起消消乐 | 每次读取真实关卡再上报；自动做金币任务换体力 | 每天约 5 点体力 + 金币兑换 |

> 日常只需要双击 `签到领福利.exe`，其余内容都是排查和进阶用。

---

## 快速开始

> **先说清楚一件事**：双击 `签到领福利.exe` 在界面里点按钮时，任务脚本由 exe 自带的运行时解释执行
> （`--run-script`），**不需要目标机器装 Python**，只需确保 exe 与 `cmcc_*.py`、`static/` 在同一目录。
> 但如果要使用 **Windows 计划任务**（`run_*.bat`）或双击 `run_gui.bat` 在浏览器运行，电脑上仍需安装 Python 3.9+ 与 `requests` 等依赖。

**从源码运行或使用计划任务（三步）**

1. 装 Python 3.9+（64 位），安装时勾选 `Add Python to PATH`
2. 在项目目录地址栏输入 `cmd`，执行 `pip install -r requirements.txt`
3. 双击 `签到领福利.exe`（或双击 `run_gui.bat` 用浏览器打开）

**第一次用，全程点界面就够（不用命令行）**

1. 「凭证与账号」→ **新建账号**：选平台、填手机号（文件名自动用尾号）
2. 手机抓包导出 HAR，微信发到电脑 —— 页面里有**分步抓包引导**（点开「第一次用？」）
3. 「凭证与账号」→ **扫描并导入**：项目 / 下载 / 微信接收目录一起扫，
   报告里会写明「✓ 实测登录成功」；福建活动的配置不存在时会**自动新建**
4. 「定时任务」→ **一键配置全部（推荐方案）**：8 个计划任务一次注册好
5. 回「总览」看**「现在需要你做什么」**：票据快到期、今天还没签、下一个自动任务，都写在那儿

命令行等价物：

```bash
python run_all.py --luck       # 签到领福利，优惠专区刷任务 + 把转盘次数抽完
```

**配置凭证（命令行方式）**

Windows 的 cmd 里没有 `cp`，用 `copy`：

```bat
copy config.example.json    configs\3239.json     :: qwhdhub（全国签到）
copy config_fj.example.json configs_fj\fj1.json   :: BASS（福建活动）
```

填好后先做一次只读检查，确认凭证有效：

```bash
python run_all.py --dry-run
```

看到 `2/2 成功` 就对了。

**获取凭证**（抓包或免抓包方式见下文「Token 获取与刷新」）

控制台「凭证与账号 → 导入抓包文件」可以从一份 HAR 同时更新 qwhdhub 与 BASS 的凭证，
手机号会自动归位到对应账号；手机号、失败推送（Bark / Server酱）也能直接在界面上改。

---

## 图形控制台

### 界面与交互

| 页面 / 组件 | 内容与特性 |
|---|---|
| **顶栏外观** | 支持**深色 / 浅色 / 跟随系统**主题切换；支持 **85% ~ 115% 界面无级缩放**（基于根节点 `rem` 响应式缩放，高分屏不发虚、布局不截断） |
| **总览** | **「现在需要你做什么」待办条**；**到账台账**（今天/本月到手流量、可用话费券，逐条记录时间+来源+兑换链接；要充值才生效的券不列入）；本月流量/任务数/凭证可用数；每号一张卡片（**双平台数据严格物理隔离** + 数据截至时间 + 近 7 天柱状图）；「签到领福利」会签到、领取可领饲料并使用已有转盘次数；也可只跑单平台或只读检查 |
| **凭证与账号** | ① **账号管理**：新建账号、改手机号、配单个账号的失败推送（Bark / Server酱 / PushPlus）② **导入抓包文件**（支持拖拽与智能多目录扫描，一份 HAR 同时更新双平台凭证，含分步抓包引导） |
| **福建活动** | 顶置**分类子标签页**（全部活动 / 养成与每日 / 天天大转盘 / 环闽寻宝记 / 流量银行），记忆上次停留状态；优惠专区抽奖；手动执行（领奖/骰子/喂养/只读）；**每日奖励 & 福袋**面板；**全勤满签大奖**；**流量银行任务周期**面板；**环闽寻宝记**面板（投骰子、开藏宝阁）；执行结果保留在结构化容器中 |
| **消消乐** | 星级/局数/间隔与「金币任务换体力」开关（存在设置页，这里只显示）；关卡自动读取；只读查次数 |
| **秒杀抢券** | 每天 12:00 抢 1 元话费券；各号会话是否备好、**票据已用多少分钟**；过了场次时间会明确提示 |
| **拓展活动** | 周六游戏中心 / 追剧领福利 / 玩游戏抽话费，按各自窗口自动判定，支持只查状态与一键做任务 |
| **定时任务** | 真实状态（已注册/下次/上次结果）、改时间、一键配置全部；**每行可直接点开该任务的日志弹窗** |
| **日志** | 本控制台发起的运行，实时彩色输出，支持仅看错误与快捷过滤 |
| **设置** | **设置一次、长期生效**的项：推送通道（Bark / Server酱 / PushPlus，配一次全局通用，可发测试）、**邮箱收 HAR**（QQ 邮箱 IMAP 授权码；票失效且当天任务没做完时每 10 分钟自动查收并导入）、**自动策略**（喂养轮数与时段、阶段满处理、领奖档位、转盘/寻宝/券来世界联动、消消乐参数、秒杀时刻）。**含表单未保存脏状态提醒（Dirty Badge）**，修改未保存会有红点提示，避免配置误丢失 |

界面约定：
- **操作反馈**：执行时按钮统一置灰并带 `忙碌` 状态指示，顶栏显示实时运行摘要；
- **结构化输出容器**：脚本运行后直接在各卡片下方展开输出框，带状态徽章（执行中 / 执行完成 / 含异常）、执行耗时与时间戳，并支持一键复制整段日志；
- **容错与终止**：「停止」会安全终止整棵子进程树并跳过剩余未执行步骤。

统计数据写在 `gui_data/`，跑得越久越全。

### 打包 exe

```
双击 build_exe.bat          # 首次 2~5 分钟
dist\签到领福利.exe → 复制到项目根目录
```

打包要求：64 位 Python + `pywebview` + `pyinstaller`。失败排查：

- bat 换行必须是 CRLF（被存成 LF 会解析错乱）
- 依赖源用默认 PyPI，清华源曾缺 pywebview
- `static/` 目录必须完整在位：前端采用纯原生无构建的现代化分层架构（`index.html`、`app.css`、`app.js` 及 `js/core.js`、`js/dash.js`、`js/activities.js`、`js/ops.js`），由 `签到领福利.spec` 的 `datas` 统一打包进 exe
- 打包配置以 **spec 为唯一来源**：`build_exe.bat` 直接构建 spec。若改回用命令行参数构建，
  PyInstaller 会按参数重新生成 spec，把手工维护的 `datas`/图标等配置覆盖掉
- 成功标志是日志末尾出现 `Build complete!`，exe 约 33MB，产物在 `dist\签到领福利.exe`

窗口起不来时程序会自动回退浏览器模式，启动过程记录在 `gui_data/startup.log`。

**桌面版内运行不依赖系统 Python**：在界面里点按钮时，exe 自己带解释器以
`签到领福利.exe --run-script cmcc_sign.py --config ...` 方式执行任务脚本。
源码运行时仍是 `python cmcc_sign.py ...`，两条路径都走 `common.child_cmd`。
（仅 Windows 计划任务通过 `run_*.bat` 调用时，仍需要系统 `pythonw.exe`。）

---

## 定时任务

推荐注册下面八个计划任务（Windows）：

| 任务 | 时间 | 脚本 | 作用 |
|---|---|---|---|
| `MobileSignIn` | 每天 **11:45** | `run_scheduled.bat` | `run_all.py --xxl --xxl-times 5`，一次跑完 qwhdhub + BASS + 消消乐。放在秒杀前 10 分钟，是为了让一张票据同时覆盖 签到→会话刷新→秒杀→拓展 |
| `MobileDailyReward` | 每天 **06:00 起每小时**（长期重复） | `run_daily_reward.bat` | `run_all.py --daily-only`，补签到 + 领每日奖励三档 + 触发自动策略（喂养/转盘/寻宝/券来世界） |
| `MobileJwtKeepAlive` | 每天 **00:00 起每 90 分钟**（长期重复） | `run_jwt_keepalive.bat` | `run_all.py --keepalive`，只续期 qwhdhub 的 jwt，不签到 |
| `MobileSessionRefresh` | 每天 **11:54** | `run_session_refresh.bat` | 只跑 qwhdhub 签到，建立新会话，为秒杀做准备 |
| `MobileSeckill` | 每天 **11:55** | `run_seckill.bat` | `run_all.py --seckill-only --seckill-at 11:59:50`，等到 12:00 抢 1 元话费券；会话未就绪会每 15 秒重试 |
| `MobileGames` | 每天 **12:05** | `run_games.bat` | 拓展活动三个：周六游戏中心 / 追剧领福利 / 玩游戏抽话费 |
| `MobileTokenLifeProbe` | 每天 **00:00 起每 2 分钟**（长期重复） | `run_token_life.bat` | 探测 app_token 精确寿命（分钟级），确认失效后自动停手 |
| `MobileMailWatch` | 每天 **00:00 起每 10 分钟**（长期重复） | `run_mail_watch.bat` | 凭证失效且当日未完成时查收 HAR 邮件并自动导入与补跑 |

> **每天只需要抓一次包**：qwhdhub 的 `app_token` 存活只有 1~2 小时（实测 22:52 导入的票
> 23:58 仍有效、00:21 还能签到，01:05 已失效）。所以签到挪到 11:45，与 11:54 会话刷新、
> 11:55 秒杀、12:05 拓展活动共用同一张票。**11:30~11:40 抓包 → 立刻发到监控邮箱**，
> `MobileMailWatch` 每 10 分钟扫一次并自动导入，导入成功后还会自动补跑一次签到。
> 票据寿命从你在 App 里抓的那一刻算，攒着不发等于白抓。
> BASS（福建）用的是 bearer，按天算，不需要每天抓。

> **为什么任务不弹黑窗口**：所有计划任务都用 `pythonw.exe` + `run_hidden.py` 启动 bat，
> 子进程带 `CREATE_NO_WINDOW`，桌面上不会出现任何控制台窗口。
> （`MobileTokenLifeProbe` 每 2 分钟跑一次，早先一天要闪 720 次。）

**为什么需要第二个任务**

1. 每日奖励三档（150/250/350）之间必须间隔 60 分钟，一次运行只能领一档。
   08:30 那趟只领得到第一档，剩下 250+350=600 饲料就浪费了。
   小时级任务负责轮询：能领就领，领完自动空转（本地记录显示已领满时 1 秒退出）。
2. BASS 签到 **6 点才开放**（`signBeginHour=6`），奖励是**连续签到累进**
   （实测第 1 天 600g → 第 2 天 900g → 第 3 天 1200g），**断一天就重置回 600g**。
   只靠 08:30 那一次的话，电脑没开机这天就白断了，所以 06:00 那轮会顺手把签到也做掉。

起始时间定在 06:03 而不是 06:00，是为了避开整点时的数据刷新边界。

**为什么需要第三个任务（会话保活）**

`appTokenLogin` 的请求体里 `jwtToken` 与 `token` 同时出现，App 端两个值都是现成的。
曾以为「jwt 是账号级长期凭证、可以免票据续期」，**2026-11-20 用对照实验证伪了**：

| 组合 | 结果 |
|---|---|
| `jwtToken` + `token=""` | FAILED　APP登录状态已过期 |
| `jwtToken` + 伪造的 token | FAILED　APP登录状态已过期 |
| `jwtToken` + 真实 app_token | **SUCCESS** |

服务端真正校验的是 **`token`（App 票据）**，`jwtToken` 单独用通不过。
也就是说**登录终究依赖 app_token（约 30 分钟寿命）**，定时重跑只能延后失效、不能真正续命。

保活任务的作用因此变成：**在票据有效期内尽量多跑，失败就说明该重新抓包了**，
同时把每次尝试的结果写进 `logs/keepalive.log`，方便统计票据的真实寿命。

```bat
python cmcc_fj.py --config configs_fj/fj1.json --daily-only   # 单号
python run_all.py --daily-only                                 # 所有 BASS 账号
python run_all.py --keepalive                                  # 只试登录，不签到
```

注册方式（建议直接在控制台「定时任务」页点「一键配置全部」，命令行等价写法如下）：

```powershell
# 1. 每天定时任务（例：签到 11:45；其余 sess=11:54、seckill=11:55、games=12:05 同理）
$a = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument '"<项目目录>\run_hidden.py" "<项目目录>\run_scheduled.bat"' -WorkingDirectory "<项目目录>"
$t = New-ScheduledTaskTrigger -Daily -At 11:45
Register-ScheduledTask -TaskName "MobileSignIn" -Action $a -Trigger $t -Force

# 2. 长期重复任务（PowerShell 5.1 需在 New-ScheduledTaskTrigger 直接传 Repetition 参数，不能赋值 $tr.Repetition）
$start = (Get-Date).AddMinutes(2).ToString("HH:mm")
$a2 = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument '"<项目目录>\run_hidden.py" "<项目目录>\run_daily_reward.bat"' -WorkingDirectory "<项目目录>"
$t2 = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName "MobileDailyReward" -Action $a2 -Trigger $t2 -Force
```

> 重复型任务（`MobileDailyReward` 每小时、`MobileJwtKeepAlive` 每 90 分钟、`MobileMailWatch` 每 10 分钟、`MobileTokenLifeProbe` 每 2 分钟）均使用未来起点 + `-RepetitionDuration (New-TimeSpan -Days 3650)`，避免过去时刻被推到明天或次日停摆。控制台「定时任务」页可直接查状态、改时间、看日志和注销。

---

## 秒杀抢券（每天 12:00 抢 1 元话费券）

签到页面里的「每周秒杀」，**每天 12:00 ~ 13:00 开抢**，奖品 1 元话费券，每个账号每周限抢 1 次，领取资格每周重置。
实现见 `cmcc_seckill.py`，接口细节见 [docs/seckill.md](docs/seckill.md)。

```bash
python cmcc_seckill.py --config configs/3239.json --dry-run   # 只查场次与资格
python cmcc_seckill.py --config configs/3239.json --once      # 立即打一次看响应
python cmcc_seckill.py --config configs/3239.json             # 等下一场自动抢
```

**关键机制：会话 cookie 可脱离 app_token 复用——但只有 2 小时寿命。**
实测只要注入 `QWHD_SESSION_TOKEN` cookie，不需要 app_token、不需要重新登录，
就能调用全部秒杀接口（含 `redeem`）。所以：

- 每次 qwhdhub 签到成功后自动保存该号的会话到 `gui_data/seckill_session_<尾号>.json`
- 秒杀脚本启动时优先读已保存会话（探活失败才回落到 app_token 重新登录）

⚠️ **但会话是建立后 2 小时硬过期**（两次独立探测一致：04:22:27 建立 → 06:22:39
返回 `GOTO_LOGIN`，准时 120 分钟）。请求不能延长它，带 jwt 也不行，
重新访问 SSO token 链接也不行。所以：

| 签到时刻 | 会话失效 | 覆盖 12:00？ |
|---|---|---|
| 08:30 | 10:30 | ❌ |
| **11:54** | **13:54** | ✅ |

**必须临近 12:00 重新签到一次**（计划任务 `MobileSessionRefresh` 11:54 干这个），
否则 12 点时会话已死。而那次签到**仍需要有效 app_token**（票据约 30~46 分钟寿命），
若已过期就还得抓一次包 —— 这是目前唯一无法自动化的环节。

服务器时间比本机快约 1 秒，脚本用 `markSeckill/sysTime` 校准后**提前 0.4 秒**出手；
被拒后每 2 秒核对一次库存作观测（实测 `leaveStock=0` 仍可能中奖，因此不会因库存显示为 0 而提前放弃），直到拿到明确终态码或达到尝试上限才停手。

多账号是**并发**等到同一时刻一起出手的（串行会让第二个号晚几十秒，库存只有 1 份时等于抢不到）。
`run_all.py` 给秒杀子进程的超时上限按 `--seckill-at` 距现在还有多久动态计算，
足够覆盖 11:55 → 12:00 这段等待；早先固定 900 秒，子进程会在开抢前就被 kill。

**启动时拿不到会话不再直接退出**：脚本每 15 秒重试一次，直到 `11:59:50 + 60 秒` 才放弃，
而且**每轮都重读配置文件** —— 等待期间导入的新 HAR 会被下一轮自动识别，不用重启任务。

> 限制：真正的开抢时刻尚未实测（每天只有 12:00 一次机会）；
> 资格要求当日已签到（脚本会自动补签）；库存通常只有 1 份，抢不抢得到看手速与运气；
> **会话 2 小时硬过期**，所以必须靠 11:54 那次签到建立新会话，
> 而那次签到仍需有效 app_token —— 票据过期时只能重新抓包。

---

## 功能详解

### 养成游戏签到

`GET /sign/getSignInfo` 返回 `signBeginHour`（开放小时）与 `signDateList`（未来几天的累进奖励），
`GET /sign/doSign` 领取。断签会让 `cycleDay` 重置回 1，所以脚本用 `signDateList` 的
今日/明日奖励在控制台上提示，签到本身由每小时任务兜底。

### 每日奖励（三档）

`GET /index/getDailyReceiveTask` 返回三档，**共 750 饲料/天**：

| 档位 | 饲料 | 间隔 |
|---|---|---|
| 阶段一 | 150 | — |
| 阶段二 | 250 | 60 分钟 |
| 阶段三 | **350** | 60 分钟 |

判断依据是 `currentTask=true`（`taskStatus` 只有 `RECEIVED`/`UN_DO`，没有 `COMPLETED`）。
每档间隔 60 分钟，没到时间返回 `1504`。接口同时给出 `limitReceiveTime`（这档何时可领）
与 `surplusTime`（剩余秒数），控制台显示的是服务端值而非本地推算。

福建活动常规运行和每小时领奖任务也会领取已开放的「昨日投喂奖励」。这笔奖励独立于每日三档，过期时间以 App 为准。

### 券来世界（`--coupon-world`）

养成任务里的 **id=24「APP首页访问活动」**，落地页就是券来世界
`https://wap.fj.10086.cn/bass-feedPrizeH5/?ch=jituan_02`（养成游戏的 H5 入口，
App 首页那个图标点进去就是它）。每天一次，完成给 **500 饲料**。

脚本的做法按任务本来的意思来：先 `GET` 券来世界页面一次，再
`POST /bass-cultivateGameServer/task/doTask/24`。服务端其实不校验有没有真的
访问过，但这样日志能对上「访问过」这件事，也不算纯空报。

要注意每小时的轮询有「今天都做完了就 1 秒退出」的短路，所以脚本在短路之前
会先查一次任务列表：券来世界还没做就不能短路，否则它会被一起跳过。

### 福袋

`GET /luckyBag/nearTrigger` → `{nextTriggerCount:300, currentFeedCount, needMoreFeeds}`。
**每累计喂食 300 次**触发一次，控制台有进度条。

### 喂养与阶段

`GET /stage/batchFeed`：一次消耗 250 饲料、算 5 次喂食、阶段进度 +250。属游戏内正常消耗。
阶段将满时会自动停手（避免错过领奖），加 `--auto-claim` 可自动领奖续喂。

**自动策略（设一次，长期生效）**：控制台「福建活动 → 自动策略」面板保存一次即可，
存在 `gui_data/policy.json`，由每小时那次 `MobileDailyReward` 读取执行：

| 字段 | 含义 |
|---|---|
| `feed` / `feed_hour` | 每天在指定时间**之后**、且**三档饲料（150/250/350，档间隔 60 分钟）全部领齐**才喂几轮（每轮 250 饲料，0 = 不喂）；不再领一档喂一档，而是**领完一起喂**。**一天只喂一次**，任务补跑不会重复消耗 |
| `feed_all` | `true` = **喂到饲料用完**：轮数按当天饲料自动算（`饲料 // 250`），不用填 `feed`；不足 250 自动停手，默认 `false` |
| `on_stage_full` | `stop` 停手 / `claim` 领本阶奖品后继续喂（对应 `--auto-claim`）/ `continue` 放弃奖品直接升阶（对应 `--continue-stage`，两者严格互斥） |
| `prize` | 领奖档位：`fee` 话费 / `flow` 流量 / `discount` 折扣。**只有一阶和六阶能自己挑**；二~五阶由服务端按 `weigh` 随机开奖（App 上标「随机」），此项对它们无效 |
| `tasks` / `fb_tasks` | 空报浏览类，默认 `false` |
| `luck` | `true` = 每天顺带把「优惠专区 · 天天大转盘」的次数抽完，默认 `true` |
| `luck_tasks` | `true` = 抽之前先做「请好友帮忙 / 逛移动爱购商城 / APP 中参与 / 浏览调研」这 4 个加次数任务（实测 0 → 2 次），默认 `true`；关掉就只花已有次数 |
| `xunbao` / `xunbao_tasks` | `true` = 每天顺带把「环闽寻宝记」的骰子投完、到终点开宝箱；`xunbao_tasks` 控制是否顺手报「参与天天大转盘」换骰子（好友助力永不代报），默认都 `true` |
| `coupon_world` | `true` = 每天做一次「券来世界」访问任务（App 首页访问活动，500 饲料）。脚本会**先把券来世界页面真的打开一次**再上报，默认 `true` |

命令行等价写法：`python run_all.py --daily-only` 会自动读取该策略并注入
`--feed N`（或 `--feed-all`）`--auto-claim --prize flow` 等参数；
单号手动喂到用完：`python cmcc_fj.py --config configs_fj/fj1.json --feed-all`
（与 `--feed N` 互斥；加 `--dry-run` 只报告会喂几轮，不真喂）。
想让某一轮不按策略走，直接调 `cmcc_fj.py` 传显式参数即可。

### 到账台账（`gui_data/awards.json`）

只看「到手多少」，不看喂了几次。脚本**领到东西的那一刻**写一条流水，
所以控制台跑的、命令行跑的、计划任务跑的都会记上，不依赖日志解析：

| 字段 | 说明 |
|---|---|
| `at` / `account` / `source` | 到手时间、号码、来源（天天大转盘 / 养成领奖 / 每日签到） |
| `name` / `kind` | 奖品名；`flow` 流量 / `fee` 话费券 / `coupon` 折扣券 |
| `size_mb` | 流量折算 MB（1GB=1024MB），话费券为 0 |
| `need_recharge` | `true` = 要充值/满减才生效（如「充值9.9折券」），**界面不列入台账**，只单独计数 |
| `url` | 兑换/领取链接（有就显示「去兑换」） |

写入入口：`common.record_award()`；汇总：`dashboard._awards_view()`
（今日 / 本月流量、可用话费券数、按号按月、按天）。同号同奖品同一分钟内重复登记会自动去重。

### 流量银行

`POST /flowBank/doTask`，body `{appId, timestamp, taskId}`。
`completeStatus`：0=未做（点两次 0→2→1）、2=进行中（点一次）、1=已完成。
有 `1006 操作太快` 频控，脚本内置 3.5~5.5 秒间隔。

任务周期由 `taskCycle` 给出（**1=每月一次，2=每周一次**），服务端不返回完成时间，
脚本自行观测并写入 `gui_data/flowbank_state.json`，控制台推算「下次可做」。

### 消消乐

逆向自 `assets/main/index.js`：`playEnd` 的 `gameLevel`/`score` 是本地 AES-CBC 加密
（key=`8cc70bf2a955c6bf`、iv=`1692bc56e8b783f4`、ZeroPadding、前拼 9 位随机字母）。
`score` 其实是星级（0~3）。**关卡每次都现读 `indexInfo.gameLevel`**，不再写死：
早期写死 49/63，账号涨到 51/64 后继续报旧关，次数照扣、关卡好几天不动；
改成现读后实测 64→65→66→67→68 正常推进。`--level` 只作读不到时的兜底。

金币任务（`--tasks`，设置页可长期打开）：`taskType=2` 浏览/访问类会先打开任务页再
`doTask`，`taskType=3` 分享活动实测可直接 `doTask`，都 +200 金币；
`taskType=1` 好友助力直接上报会失败，但**可以用你自己的两个号互点**：自动生成助力码并互调
`help/{码}`，被助力方 +200 金币；同一对号每月限一次，成功月份记在
`gui_data/xxl_assist_state.json`，本月点过以后直接跳过，不再白跑接口。
面板上写的「每天5次」是要 5 个不同好友，自己两个号做不到。
拿到金币后自动查商店，
把 `propType=12`（道具爱心）按 **200 金币/点**换成体力（每日最多 20 次），实测有效。

三个硬现实：

1. **中奖靠服务端抽奖池**，跟 level/score 无关；服务端会把「谢谢参与」也当
   `prizeName` 回传，脚本会过滤，不记台账
2. **每天基础 5 点体力**（`dayLimit=100` 是假上限），金币可以换额外体力
3. 体力不足时某些接口会返回 `-100`，不代表 token 坏了

脚本遇「体力」关键词立即停止；若一局通关后关卡没推进，也会提前停，避免继续白扣次数。

### 优惠专区抽奖

三个接口全是 GET，只需 `appId` + `timestamp`：

```
GET /bass-preferentialAreaServer/luck/index              → 剩余次数 + taskList
GET /bass-preferentialAreaServer/luck/doTask/{type}/{id} → 完成任务（直接 GET 即记完成）
GET /bass-preferentialAreaServer/luck/join               → 抽奖
```

**关键**：`preferentialAreaServer` 与养成游戏共用同一套 bass 鉴权，所以
`configs_fj/*.json` 里的 `bearer_token` 可直接用于抽奖，**不需要抓包**。

**加次数任务**（`taskList` → `doTask`，2026-11-22 实测）：

| 任务 | taskType | 有无落地页 | 结果 |
|---|---|---|---|
| 请好友帮忙 | 3 | 无 | `0000` |
| 中国移动APP中参与 | 2 | 无 | `0000` |
| 浏览调研页面 | 5 | 有 `taskUrl` | `0000` |
| 逛移动爱购商城 | 5 | 有 `taskUrl` | `0000` |

四个任务一次刷完，剩余次数从 **0 → 2**（3239 当场抽到一张 9.9 折券）。
带 `taskUrl` 的会先真的 GET 一次页面再上报，路径尽量贴近真人；
`complete:true` 的任务直接跳过——服务端已经记过账，再报一次也不会多给。
这些任务一天只算一次，重跑不会重复薅。

### 环闽寻宝记（`cmcc_xunbao.py`）

活动页 `https://wap.fj.10086.cn/bass-activityH5/pages/nationalDay2026/index`，
后端是 `bass-commonAreaServer/H5Api/hmxb/*`，全部 POST JSON，body 只要 `appId` + `timestamp`：

```
activity/status  activity/init   dice/balance   dice/roll
task/list        task/report     treasure/open  treasure/poolPreview
prize/list       prize/claim     assist/create  assist/detail  assist/do
```

玩法是投骰子在一张 29 格的八闽棋盘上走，落点有福袋/锦鲤/歇脚/事件/集市，
走到第 29 格开「藏宝阁」宝箱（奖池头奖 888 元话费券包）。

**两个坑**：

1. **UA 必须带 App 标记** `leadeon/12.5.7/CMCCIT`。只写 `leadeon/12` 时
   `activity/status` 能过，但所有用户态接口都回 `2004 请前往中国移动App参与活动`。
2. 骰子当天不用就作废；来源是任务（好友助力 3 次/天、打开消息提醒一次性 5 个、
   参与天天大转盘 1 次/天）。

程序只做「投掉现有骰子 + 领待领取奖品 + 到终点开宝箱」，
`--tasks` 只上报「参与天天大转盘」这类本程序真的做过的跳转任务；
**好友助力和打开消息提醒一律不代报**（`SKIP_TASKS`），要助力只能
`--invite` 生成链接发给真人。

助力还有一条服务端限制：**同一对号码每月只能互相助力一次**，
重复会回 `status=8「本月已助力过该发起者」`（实测两个号互相帮过之后就再也点不动，
换新邀请链接也没用）。自己两个号互帮解决不了，需要第三个人。

```bash
python cmcc_xunbao.py --dry-run        # 只查进度/骰子/任务/奖品
python cmcc_xunbao.py                  # 投掉现有骰子
python cmcc_xunbao.py --key fj2 --tasks  # 顺手上报大转盘任务换骰子
python cmcc_xunbao.py --key fj1 --invite # 只生成好友助力链接
```

中奖会写进「到账台账」。注意「15元购10GB」这类是**付费券包**，
按 `common.PAY_WORDS` 判成有门槛，不算「已得流量」。

---

## Token 获取与刷新

凭证是账号钥匙，只在自己电脑上操作，不要发聊天、不要传网盘。

### 刷新 qwhdhub

1. 手机抓包，打开中国移动 App → 我的 → 签到领流量
2. 搜 `appTokenLogin`，切到**请求**标签，确认 body 里有 `"token": "JSESSIONID=…"`
3. 导出 HAR 后导入 —— **不用挪文件**，控制台「凭证与账号 → 导入抓包文件」有两个入口：
   - 「导入」：直接选文件（从微信把 .har 拖进文件选择框即可）
   - 「自动查找」：服务端去**项目目录 / 下载目录 / 微信接收目录**扫，
     最新的先处理、最后落地（避免旧票据覆盖新的）；「只导入最新一份」只处理最新的那个

> 导入后控制台会**实测登录一次**，报告里会写明「✓ 实测登录成功」或失败原因，
> 所以「显示导入了」不等于「票能用」，看报告里的验证行。

**抓不到 `appTokenLogin` 也不用退出重登。** App 已是登录态时不会再发这条请求，
但每个 `wx.10086.cn` 请求的**请求头 `x-token`** 里带着同一张票
（`JSESSIONID=…; UID=…; ticketID=…`），导入程序会自动用它兜底。
归属靠 `/qwhdhub/api/mark/user/info` 响应里的脱敏号码（如 `173****4167`）
按「前 3 位 + 后 4 位」匹配已有账号，所以连 appTokenLogin 都没有也能导入。

> **App 内切号会让另一个号的 app_token 失效**，两个号尽量分设备登录。

**关于抓包频率**：`app_token` 只有约 30 分钟寿命，且**只靠它才能登录**——
`jwtToken` 单独用服务端不认（详见上面「会话保活」那节的对照实验）。
所以 qwhdhub 需要定期重新抓包。保活任务会在票据有效期内不断尝试，
失败即代表该抓包了，结果记在 `logs/keepalive.log`。

### 刷新 BASS

抓包找任意 `wap.fj.10086.cn` 请求的 `Authorization: Bearer <36位UUID>`，
用 HAR 导入会自动按手机号归到对应 `configs_fj/fjN.json`。

**免抓包路线**（手机蜂窝网，非 WiFi）：

```bash
pkg install python && pip install requests
python pull_token_phone.py --save     # Termux
```

原理 `pageInit → getMobile → getSign → getToken → validateToken`，走「本机号码一键登录」。
`getMobile` 必须蜂窝网，PC 会返回 `网关未插入手机号`。iPhone 用 `pull_token_iphone.js`。

> 这条链路换出的是 BASS 平台凭证，**不能用于 qwhdhub**（两者鉴权体系不同）。

---

## 目录结构

```
.
├── 签到领福利.exe          桌面版入口（用 build_exe.bat 构建，不入库）
├── run_gui.bat             无 exe 时用浏览器打开控制台（跑 python dashboard.py）
├── run_scheduled.bat       定时签到用
├── run_daily_reward.bat    定时领每日奖励用
├── run_jwt_keepalive.bat   定时续期 qwhdhub 的 jwt（会话保活）
├── run_session_refresh.bat 定时刷新活动会话（11:54，供 12:00 秒杀复用）
├── run_seckill.bat         定时秒杀抢券（11:55 启动，等到 12:00）
├── run_games.bat           定时拓展活动（12:05）
├── run_token_life.bat      定时探测 app_token 寿命（每 2 分钟）
├── run_mail_watch.bat      定时查收邮箱 HAR 附件并自动导入（每 10 分钟）
├── run_hidden.py           无窗口启动器（计划任务用它跑 bat，不再闪黑窗口）
├── common.py               共用基础设施：原子写、文件锁、通知、会话、接口调用、日志轮转
├── disk_imports.py         冻结环境下优先从磁盘加载项目 .py 模块
├── run_all.py              批量调度（多账号并发、失败推送、日志轮转）
├── cmcc_sign.py            qwhdhub 签到（全国）
├── cmcc_extra.py           qwhdhub 拓展活动模块
├── cmcc_fj.py              BASS 签到 + 喂养 + 每日奖励 + 流量银行 + 福袋
├── cmcc_xiaoxiaole.py      消消乐
├── cmcc_luck.py            优惠专区转盘抽奖（加次数任务 + 抽完次数）
├── cmcc_xunbao.py          环闽寻宝记（投骰子、开宝箱、奖品领取）
├── cmcc_seckill.py         签到有礼每周秒杀（12:00 抢话费券）
├── cmcc_wed.py             周三充值日（拼图任务、抽奖、集齐领券）
├── har_import.py           从 HAR 提取 qwhdhub + BASS 凭证并写配置
├── mail_watch.py           邮箱收 HAR 附件并自动导入与补跑
├── probe_token_life.py     app_token 寿命精确探测（分钟级）
├── dashboard.py            图形控制台后端（HTTP API + 任务调度）
├── static/                 控制台前端静态资源（打包时由 spec 的 datas 整体带上）
│   ├── index.html          主页面（深色/浅色、外观设置弹层、子标签页、表单脏状态提示）
│   ├── app.css             样式与主题变量（rem 缩放、骨架屏、状态徽章、响应式输入网格）
│   ├── app.js              全局路由、分片脏渲染、结构化命令输出、日志流式监听
│   └── js/                 模块化业务脚本
│       ├── core.js         基础工具、DOM 辅助、弹层/气泡、缩放与深浅色主题
│       ├── dash.js         总览面板、双平台数据独立卡片、到账台账与效率看板
│       ├── activities.js   活动子标签页切换、账号弹窗、大转盘/寻宝/流量银行面板
│       └── ops.js          计划任务列表与弹窗日志、配置脏状态追踪、HAR 导入
├── app_desktop.py          桌面版入口（pywebview 原生窗口 + --run-script 调度）
├── pull_token_phone.py     手机一键登录换 BASS token（免抓包）
├── pull_token_iphone.js    同上，iPhone Scriptable 版
├── probe_fj.py             只读探测 BASS 接口
├── config.example.json     qwhdhub 配置模板
├── config_fj.example.json  BASS 配置模板
├── tests/                  单元与回归测试（全部用假响应，不发真实请求）
├── docs/                   接口文档（qwhdhub / 秒杀 / 周三充值日 / BASS）
├── configs/                真实 qwhdhub 配置（不入库）
├── configs_fj/             真实 BASS 配置（不入库）
├── gui_data/               运行状态与统计（不入库）
└── logs/                   运行日志（不入库）
```

### 常用参数

```bash
python run_all.py --xxl                    # 全部 + 消消乐
python run_all.py --platform bass --dry-run
python run_all.py --log logs/daily.log     # 同时写一份按天轮转的日志
python cmcc_fj.py --config configs_fj/fj1.json --claim       # 领阶段奖励
python cmcc_fj.py --config configs_fj/fj1.json --feed 3      # 喂养 3 次（每次 250 饲料）
python cmcc_fj.py --config configs_fj/fj1.json --fb-tasks    # 流量银行任务（按周期门控）
python cmcc_fj.py --config configs_fj/fj1.json --fb-tasks --fb-all  # 忽略周期强制尝试
python cmcc_luck.py --key fj2 --dry-run                      # 抽奖：只看次数
python cmcc_luck.py --key fj2 --skip-assist                  # 刷任务但跳过「请好友帮忙」
python cmcc_xiaoxiaole.py --config configs_fj/fj1.json --detect-level --times 5
python probe_token_life.py --report                    # 看 app_token 寿命结论
python probe_token_life.py --once                      # 手动探一轮
```

### app_token 到底能活多久

`MobileTokenLifeProbe` 计划任务每 2 分钟探一次登录，签发时刻取自 HAR 里
`appTokenLogin` 的时间戳，**结论精确到分钟**：

```bash
python probe_token_life.py --report
```

输出形如：

```
--- ...3239（184****3239）
    距签发 30 分钟仍有效；09:05:00 首次失效（采样间隔 2 分钟） → 票据寿命 30~53 分钟
```

导入新 HAR 时会自动重置探测状态、重新开始计时；
确认失效后脚本会**停止继续探测**（避免刷同一条报错、也避免被频控）。

---

## 数据与统计

| 文件 | 内容 |
|---|---|
| `gui_data/stats.json` | 累计流量/任务数、近 7 天记录、token 状态 |
| `gui_data/daily_state.json` | 每日三档奖励、连续签到状态、福袋进度 |
| `gui_data/flowbank_state.json` | 流量银行任务周期与上次完成时间 |
| `gui_data/luck_state.json` | 抽奖次数与中奖记录 |
| `gui_data/runs.json` | 运行次数、成功率、机器工时 |
| `gui_data/token_life/<尾号>.json` | app_token 寿命探测样本与结论 |
| `logs/daily.log` | 定时任务日志（按天轮转，保留 7 天） |

统计口径：流量只统计**带 MB/GB 的奖品**（话费券不算）；喂养单列 `feeds`。

### 接口文档

各活动的端点、字段含义与踩过的坑都整理在 [`docs/`](docs/)：

- [docs/qwhdhub.md](docs/qwhdhub.md) —— 全国签到平台的端点与凭证获取
- [docs/bass.md](docs/bass.md) —— 福建站的养成游戏、流量银行、抽奖、消消乐
- [docs/wedrecharge.md](docs/wedrecharge.md) —— 周三充值日（独立活动，含服务器时间戳）

想适配别的省份或加新活动，从这几份文档入手会快很多。

### 设置页

侧边栏最后一个「设置」页，放的都是**设置一次、长期生效**的东西，
不用每次手动勾：

- **推送设置**：Bark / Server酱 / PushPlus，配一次全局通用（见下面「失败推送」）。
- **邮箱收 HAR**：填邮箱地址 + IMAP 授权码 + 允许发件人，每 10 分钟查一次。
  只有「凭证确实失效且当天任务没做完」时才连邮箱；收到 `.har` 附件自动导入并推送结果。
  **导入且实测成功后会自动补跑一次当天签到**——否则会出现「票修好了、任务还是没做」。
  补跑只在报告里确实「实测成功」时才触发，废票不会白跑。
  配置存 `gui_data/mail_settings.json`（不进 git）。支持 QQ / 139 / 163 邮箱授权码；
  139 授权码有效期 90 天，过期要重取。**Gmail 官方已转向 OAuth2，密码方式不可用，不推荐**。
- **自动策略**：
  - 每天自动喂养的轮数与时段、喂到饲料用完、阶段喂满时怎么处理、领奖档位；
  - 天天大转盘 / 环闽寻宝 / 券来世界要不要跟着每日奖励自动跑；
  - 伪造类开关（养成浏览任务、流量银行任务、券来世界访问）；
  - 消消乐的星级 / 局数 / 间隔（关卡每次自动读真实值，不用填），秒杀抢券的开抢时刻。

策略存盘在 `gui_data/policy.json`，由「每日奖励」计划任务每轮读它执行，
改完不用重启任何东西。消消乐页和秒杀抢券页不再各自存一份参数，
只显示设置页里保存的值，点「去设置里改」即可。

### 今日战报

每天跑完且**没出错**时，会自动推一条「今日战报」：当天到手的流量/券按账号分组，
带每号小计和总计（流量 GB、话费元）。同一天只推一条（20 小时内不重复），
所以每小时轮询也不会刷屏。

口径与「今日已得流量/本月已得流量」完全一致：

- **话费型手气红包、话费加赠券** —— 不算也不列明细（要充值才能用）；
- **付费券包**（如「15元购10GB」）—— 东西还没到手，不计入到手流量；
- **有门槛的券** —— 只计数，明细不列。

正文只显示账号**尾号**（推送会落到手机锁屏上，不暴露完整手机号）。

想随时看：控制台「设置 → 推送设置 → 推送今日战报」。

### 失败推送

通知只推**一条**。理由很实在：每日奖励是每小时跑一轮，凭证一过期，
两三个号各失败一次、每轮各推一条，一天能收到十几条一模一样的「失败」。

现在的规则：

- **凭证过期**（401 / 登录态失效 / 会话缺失）→ 合并成一条，写清是哪几个号、
  以及「抓包 → 导入」怎么做；同一天最多一条（12 小时内不重复）。
- 其它失败 → 一条汇总（成功数 + 失败账号），20 小时内不重复。
- 子脚本自己发的失败通知，在批量运行时会被抑制（`CMCC_NOTIFY_BATCH=1`），
  避免和汇总通知重复。
- 去重状态记在 `gui_data/notify_state.json`，多进程/并发调用均通过文件互斥锁（`file_lock`）安全原子写入，防止高频并发下重复多次弹窗推送。
- **安全与通道校验**：所有自定义推送 URL 强制防 SSRF 校验（拦截内网与私有 IP 回环）；严格检查各通道的 HTTP 状态码与业务 code（如 PushPlus 返回非 200 判定发送失败并留存详细日志）。



支持的通道有三个，可以同时填、同时收：

| 通道 | 填什么 | 去哪拿 |
|---|---|---|
| Bark | 推送地址，如 `https://api.day.app/你的key` | Bark App |
| Server酱 | SendKey（只含字母数字） | sct.ftqq.com |
| PushPlus | 32 位十六进制令牌 | pushplus.plus「我的令牌」 |

**推荐在控制台的「设置」页统一配一次**（`gui_data/settings.json`），
所有账号、所有脚本都用它，不用给每个账号单独填一遍。
优先级：**环境变量 > 设置页 > 单账号配置**。

- 环境变量：`CMCC_BARK_URL` / `CMCC_SERVERCHAN_SENDKEY` / `CMCC_PUSHPLUS_TOKEN`
- 单账号配置：`configs/*.json` 或 `configs_fj/*.json` 里的 `bark_url` /
  `serverchan_sendkey` / `pushplus_token`（在「凭证与账号 → 编辑」里填）

全部成功时不推送；失败时按上面的规则推一条。
「设置」页还有个「发一条测试」按钮，配完点一下就知道通不通。

---

## 安全与性质说明

- `configs/`、`configs_fj/` 里是账号凭证，**不要外传**；控制台只监听 `127.0.0.1`
- **不要用 GitHub Actions 跑**（凭证会传到第三方）
- **空报浏览**（`--tasks` / `--fb-tasks` / 抽奖 `doTask`）：跳过真实浏览直接上报完成，
  属伪造行为记录，默认不勾选，界面上有 ⚠ 提示
- **消消乐**：伪造游戏通关成绩，性质更重，与签到主流程独立
- 竞品提醒：曾逆向的「省钱宝」外挂申请 `READ_SMS`（可读银行验证码）且走明文 HTTP，**不要安装**
- 本项目仅供学习交流，使用前请自行确认是否符合运营商服务条款

---

## 已知限制

- qwhdhub 的 `app_token` 约 30 分钟寿命，**只靠它才能登录**（`jwtToken` 单独用服务端不认，已实测）；
  所以 qwhdhub 仍需定期抓包，保活任务只能延后失效、不能续命
- **app_token 实质是中国移动 App 的主站会话 cookie**（HAR 里格式与 App Cookie 完全一致）：
  App 会话一过期，qwhdhub 票据同时作废，两者寿命是同一条曲线
- **BASS 是本项目里最省心的凭证**：bearer 实测已存活 20.6 小时仍有效，
  换票接口（`auth/loginByHangYan`）存在但依赖更短命的航眼 token，
  长期仍以 HAR 导入为主
- **打包成 exe 后，所有数据路径必须指向 exe 所在目录**。
  onefile 打包会把源码解包到临时目录（`_MEIxxxx`），
  那里写出来的配置会随进程退出消失 —— 曾导致「界面显示导入成功、配置却没变」。
  现在统一走 `common.app_base()`，并有回归测试守着
- 消消乐每天约 5 点体力，中奖概率低，别指望薅到东西
- BASS 签到在凌晨会返回 `1502 未到可签到时间`，属正常
- 每日奖励三档之间必须间隔 60 分钟，靠 `MobileDailyReward` 每小时轮询领取；不装这个任务就只领得到第一档
- 自动喂养会等**三档全领齐**（约需 3 小时）再一次性喂，而不是领一档喂一档；
  其他任务（含券来世界的 500 饲料）在喂养前已完成，不依赖饲料
- 福建以外省份的 BASS 活动未适配（`wap.fj.10086.cn` 为福建站）
- **拓展活动三个已启用**（`MobileGames` 任务 + 控制台「拓展活动」页）：
  代码早写好了，但此前 `run_all` 从不传 `--games`、界面也没入口，等于从没跑过；
  现在 12:05 每天跑一次 —— 周六游戏中心的游戏币全周可抽（10 币/次，31 天周期清零），
  追剧的抽奖只在**周日**开放（其它日子只打卡/攒次数，不消耗）；全民掼蛋挑战赛任务与抽奖均自动完成。
  抽奖遇到「未中奖」会**继续抽完剩余次数**（毕竟照样扣币），不会中途停下；
  同一次中多张相同奖品也逐张记账，不会被去重吞掉
- **周三充值日的抽奖与领券已实现**（`cmcc_wed.py`），但端点来自公开实现而非本地抓包，
  尚未用有效票据实测；任务类只做查询/浏览类，**邀请助力与真实充值一律不代做**
- **秒杀的真正开抢时刻尚未实测**：每天只有 12:00 ~ 13:00 一次机会，
  库存通常只有 1 份，抢不抢得到看运气
- qwhdhub 签到**必须**有有效 app_token；秒杀虽可用已保存的会话 cookie 兜底，
  但会话本身也是靠签到时建立的，app_token 全断后仍需重新抓包

---

## 开发与测试

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v      # 408 个用例，覆盖全部业务、安全与界面
python tests/browser_smoke.py                # 可选：真实浏览器冒烟（需 playwright）
```

测试全部用临时目录与假响应，**不读 `configs/`、不发任何真实请求**，可以放心跑。
另有一个只跑测试的 GitHub Actions（`.github/workflows/tests.yml`，Windows + Linux × Python 3.9/3.12）——
上面「不要用 GitHub Actions 跑」指的是签到任务本身，不是测试。

`tests/test_regressions.py` 里每条用例都对应一个修过的真实缺陷：保活参数被丢、
消消乐退出码恒 0、会话剩余分钟恒 null、HAR 签发时刻跨账号污染、并发写状态丢更新、
控制台路径穿越与跨站请求、**控制台没有可运行入口**、**冻结态计划任务拿 exe 当解释器**、
**「停止」停不掉顺序任务与孙子进程**、**首屏被 PowerShell 卡住**、
**全新安装无法在界面里建账号**等。把代码改回去就会红。

`tests/test_ui.py` 守界面本身：每页有中文标题、待办条在、真实任务状态在、
**不暴露命令行参数**、**消消乐默认不勾**、**错误留在页面上**、**忙时按钮置灰**、
**提示条不堆叠**、**手机表格在自己的容器里滚动**、日志时间戳对比度达标、
**前端无构建 JS/CSS 静态分层模块完整性**、**全局空状态展示规范**、**rem 响应式缩放与外观弹层契约**。

三个文件都能单独跑（`python tests/test_smoke.py`），入口在文件末尾，
不会漏掉后半部分的测试类。

---

## 相关项目

- [yupaiLy/cmcc-auto-checkin](https://github.com/yupaiLy/cmcc-auto-checkin) —— 同样是中国移动签到
  自动化的开源项目，按省份分目录组织，本项目的 qwhdhub 部分思路与其相近，在此致谢

## License

MIT
