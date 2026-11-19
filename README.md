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
| **qwhdhub** | `wx.10086.cn` | 全国签到、月签、群签、任务、秒杀 | `app_token` = `JSESSIONID=…; UID=…; ticketID=…` | 约 30 分钟 |
| **BASS** | `wap.fj.10086.cn` | 福建养成游戏、流量银行、消消乐、优惠抽奖 | `Bearer token` = 36 位 UUID | 数小时 |

**已覆盖的活动**

| 活动 | 说明 | 每日收益 |
|---|---|---|
| 全国签到 | qwhdhub 每日/每月/群签、任务、秒杀 | 流量 + 话费券 |
| 养成游戏签到 | BASS 连续签到，6 点开放 | 600g → 900g → 1200g 累进 |
| 每日奖励三档 | 阶段一/二/三，各间隔 60 分钟 | 750 饲料/天 |
| 流量银行 | 浏览类任务 + 点数兑换 | 实测 352MB → 1.5GB |
| 福袋 | 每累计喂食 300 次触发 | 随机 |
| 优惠抽奖 | 转盘，刷任务换次数 | 流量券 / 话费券 |
| 一起消消乐 | 自动探测关卡并上报通关 | 每天约 5 点体力 |

> 日常只需要双击 `签到控制台.exe`，其余内容都是排查和进阶用。

---

## 快速开始

**新电脑三步**

1. 装 Python 3.9+（64 位），安装时勾选 `Add Python to PATH`
2. 在项目目录地址栏输入 `cmd`，执行 `pip install -r requirements.txt`
3. 双击 `签到控制台.exe`（或 `run_gui.bat` 用浏览器打开）

命令行等价物：

```bash
python run_all.py --xxl        # 全部跑一遍（含消消乐）
```

**配置凭证**

```bash
cp config.example.json    configs/3239.json     # qwhdhub 配置
cp config_fj.example.json configs_fj/fj1.json  # BASS 配置
```

填好后先做一次只读检查，确认凭证有效：

```bash
python run_all.py --dry-run
```

看到 `2/2 成功` 就对了。

**获取凭证**（抓包或免抓包方式见下文「Token 获取与刷新」）

控制台「Token 管理 → HAR 导入」可以从一份 HAR 同时更新 qwhdhub 与 BASS 的凭证，
手机号会自动归位到对应账号。

> exe 只是界面外壳，实际任务由系统 Python 执行，必须与 `cmcc_*.py` 放在同一目录。

---

## 图形控制台

### 界面

| 页面 | 内容 |
|---|---|
| **总览** | 累计流量/任务数/Token 可用数；每号一张卡片（双平台数据 + 近 7 天记录柱状图）；一键签到（**默认含消消乐**）、只跑单平台、dry-run |
| **Token 管理** | ① HAR 导入（一份 HAR 同时更新 qwhdhub + BASS）② 优惠专区抽奖 |
| **BASS 任务** | 领奖/骰子/喂养/只读；**每日奖励 & 福袋**面板；**流量银行任务周期**面板 |
| **消消乐** | 关卡/星级/局数/间隔，支持 dry-run 与自动探测关卡 |
| **定时任务** | 查询/注册/注销计划任务 |
| **日志** | 实时彩色日志 |

统计数据写在 `gui_data/`，跑得越久越全。

### 打包 exe

```
双击 build_exe.bat          # 首次 2~5 分钟
dist\签到控制台.exe → 复制到项目根目录
```

打包要求：64 位 Python + `pywebview` + `pyinstaller`。失败排查：

- bat 换行必须是 CRLF（被存成 LF 会解析错乱）
- 依赖源用默认 PyPI，清华源曾缺 pywebview
- 成功标志是日志出现 `Build complete!`，exe 约 33MB

窗口起不来时程序会自动回退浏览器模式，启动过程记录在 `gui_data/startup.log`。

---

## 定时任务

推荐注册两个计划任务（Windows）：

| 任务 | 时间 | 脚本 | 作用 |
|---|---|---|---|
| `MobileSignIn` | 每天 **08:30** | `run_scheduled.bat` | `run_all.py --xxl --xxl-times 5`，一次跑完 qwhdhub + BASS + 消消乐 |
| `MobileDailyReward` | 每天 **06:03 起每小时**，共 17 次 | `run_daily_reward.bat` | `run_all.py --daily-only`，补签到 + 领每日奖励三档 |

**为什么需要第二个任务**

1. 每日奖励三档（150/250/350）之间必须间隔 60 分钟，一次运行只能领一档。
   08:30 那趟只领得到第一档，剩下 250+350=600 饲料就浪费了。
   小时级任务负责轮询：能领就领，领完自动空转（本地记录显示已领满时 1 秒退出）。
2. BASS 签到 **6 点才开放**（`signBeginHour=6`），奖励是**连续签到累进**
   （实测第 1 天 600g → 第 2 天 900g → 第 3 天 1200g），**断一天就重置回 600g**。
   只靠 08:30 那一次的话，电脑没开机这天就白断了，所以 06:00 那轮会顺手把签到也做掉。

起始时间定在 06:03 而不是 06:00，是为了避开整点时的数据刷新边界。

```bat
python cmcc_fj.py --config configs_fj/fj1.json --daily-only   # 单号
python run_all.py --daily-only                                 # 所有 BASS 账号
```

注册方式：

```powershell
# 签到任务
$a = New-ScheduledTaskAction -Execute "<项目目录>\run_scheduled.bat" -WorkingDirectory "<项目目录>"
$t = New-ScheduledTaskTrigger -Daily -At 08:30
Register-ScheduledTask -TaskName "MobileSignIn" -Action $a -Trigger $t -Force

# 每日奖励任务（每小时，持续 17 小时）
$a2 = New-ScheduledTaskAction -Execute "<项目目录>\run_daily_reward.bat" -WorkingDirectory "<项目目录>"
$t2 = New-ScheduledTaskTrigger -Daily -At 06:03
$t2.Repetition = (New-ScheduledTaskTrigger -Once -At 06:03 -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 17)).Repetition
Register-ScheduledTask -TaskName "MobileDailyReward" -Action $a2 -Trigger $t2 -Force
```

控制台「定时任务」页也能直接查状态、改时间、注销。

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

### 福袋

`GET /luckyBag/nearTrigger` → `{nextTriggerCount:300, currentFeedCount, needMoreFeeds}`。
**每累计喂食 300 次**触发一次，控制台有进度条。

### 喂养与阶段

`GET /stage/batchFeed`：一次消耗 250 饲料、算 5 次喂食、阶段进度 +250。属游戏内正常消耗。
阶段将满时会自动停手（避免错过领奖），加 `--auto-claim` 可自动领奖续喂。

### 流量银行

`POST /flowBank/doTask`，body `{appId, timestamp, taskId}`。
`completeStatus`：0=未做（点两次 0→2→1）、2=进行中（点一次）、1=已完成。
有 `1006 操作太快` 频控，脚本内置 3.5~5.5 秒间隔。

任务周期由 `taskCycle` 给出（**1=每月一次，2=每周一次**），服务端不返回完成时间，
脚本自行观测并写入 `gui_data/flowbank_state.json`，控制台推算「下次可做」。

### 消消乐

逆向自 `assets/main/index.js`：`playEnd` 的 `gameLevel`/`score` 是本地 AES-CBC 加密
（key=`8cc70bf2a955c6bf`、iv=`1692bc56e8b783f4`、ZeroPadding、前拼 9 位随机字母）。
`score` 其实是星级（0~3），`gameLevel` 是关卡号，用 `--detect-level` 自动探测真实进度。

三个硬现实：

1. **中奖靠服务端抽奖池**，跟 level/score 无关，实测满星也是「谢谢参与」
2. **每天实际约 5 点体力**（`dayLimit=100` 是假上限），用完返回
   `-100 您的体力不足，无法进行挑战`
3. 体力不足时 `indexInfo` 也返回 `-100`，探测关卡失败不代表 token 坏了

脚本遇「体力」关键词立即停止，不会白跑。

### 优惠专区抽奖

三个接口全是 GET，只需 `appId` + `timestamp`：

```
GET /bass-preferentialAreaServer/luck/index              → 剩余次数 + taskList
GET /bass-preferentialAreaServer/luck/doTask/{type}/{id} → 完成任务（直接 GET 即记完成）
GET /bass-preferentialAreaServer/luck/join               → 抽奖
```

**关键**：`preferentialAreaServer` 与养成游戏共用同一套 bass 鉴权，所以
`configs_fj/*.json` 里的 `bearer_token` 可直接用于抽奖，**不需要抓包**。

---

## Token 获取与刷新

凭证是账号钥匙，只在自己电脑上操作，不要发聊天、不要传网盘。

### 刷新 qwhdhub

1. 手机抓包，打开中国移动 App → 我的 → 签到领流量
2. 搜 `appTokenLogin`，切到**请求**标签，确认 body 里有 `"token": "JSESSIONID=…"`
3. 导出 HAR 放到项目目录 → 控制台「Token 管理 → HAR 导入」，或命令行 `python har_import.py`（自动扫目录下所有 .har）

> **App 内切号会让另一个号的 app_token 失效**，两个号尽量分设备登录。

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

### qwhdhub 短信登录（电脑端）

```bash
python pull_token_sms.py start 13800138000
python pull_token_sms.py login <验证码>
```

走 `login.10086.cn` 官方链路，不经第三方。需要图形验证码时先 `captcha` 再 `send <字符>`。

---

## 目录结构

```
.
├── 签到控制台.exe          桌面版入口（用 build_exe.bat 构建，不入库）
├── run_gui.bat             无 exe 时用浏览器打开控制台
├── run_scheduled.bat       定时签到用
├── run_daily_reward.bat    定时领每日奖励用
├── common.py               共用基础设施：原子写、通知、会话、接口调用
├── run_all.py              批量调度（多账号并发、失败推送、日志轮转）
├── cmcc_sign.py            qwhdhub 签到（全国）
├── cmcc_extra.py           qwhdhub 拓展活动模块
├── cmcc_fj.py              BASS 签到 + 喂养 + 每日奖励 + 流量银行 + 福袋
├── cmcc_xiaoxiaole.py      消消乐
├── cmcc_luck.py            优惠专区转盘抽奖
├── har_import.py           从 HAR 提取 qwhdhub + BASS 凭证并写配置
├── dashboard.py            图形控制台后端 + 内嵌页面
├── app_desktop.py          桌面版入口（pywebview 原生窗口）
├── pull_token_phone.py     手机一键登录换 BASS token（免抓包）
├── pull_token_iphone.js    同上，iPhone Scriptable 版
├── pull_token_sms.py       电脑端短信登录换 qwhdhub token
├── probe_fj.py             只读探测 BASS 接口
├── config.example.json     qwhdhub 配置模板
├── config_fj.example.json  BASS 配置模板
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
python cmcc_fj.py --config configs_fj/fj1.json --fb-tasks    # 流量银行任务
python cmcc_luck.py --key fj2 --dry-run                      # 抽奖：只看次数
python cmcc_xiaoxiaole.py --config configs_fj/fj1.json --detect-level --times 5
```

---

## 数据与统计

| 文件 | 内容 |
|---|---|
| `gui_data/stats.json` | 累计流量/任务数、近 7 天记录、token 状态 |
| `gui_data/daily_state.json` | 每日三档奖励、连续签到状态、福袋进度 |
| `gui_data/flowbank_state.json` | 流量银行任务周期与上次完成时间 |
| `gui_data/luck_state.json` | 抽奖次数与中奖记录 |
| `gui_data/runs.json` | 运行次数、成功率、机器工时 |
| `logs/daily.log` | 定时任务日志（按天轮转，保留 7 天） |

统计口径：流量只统计**带 MB/GB 的奖品**（话费券不算）；喂养单列 `feeds`。

### 失败推送

在 `configs/*.json` 或 `configs_fj/*.json` 里填 `bark_url`（Bark 推送地址）或
`serverchan_sendkey`（Server酱 SendKey），任一账号失败时就会推一条通知。
也可以用环境变量 `CMCC_BARK_URL` / `CMCC_SERVERCHAN_SENDKEY` 统一配置。
全部成功时不推送，避免打扰。

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

- qwhdhub 的 `app_token` 约 30 分钟寿命，jwt 续期实测失败，只能重新获取
- 消消乐每天约 5 点体力，中奖概率低，别指望薅到东西
- BASS 签到在凌晨会返回 `1502 未到可签到时间`，属正常
- 每日奖励三档之间必须间隔 60 分钟，靠 `MobileDailyReward` 每小时轮询领取；不装这个任务就只领得到第一档
- 福建以外省份的 BASS 活动未适配（`wap.fj.10086.cn` 为福建站）

---

## 相关项目

- [yupaiLy/cmcc-auto-checkin](https://github.com/yupaiLy/cmcc-auto-checkin) —— 同样是中国移动签到
  自动化的开源项目，按省份分目录组织，本项目的 qwhdhub 部分思路与其相近，在此致谢

## License

MIT
