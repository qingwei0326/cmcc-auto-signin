@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem 签到有礼「每周秒杀」自动抢券：每天 12:00 抢 1 元话费券。
rem 本任务在 11:30 启动，脚本内部等到 12:00 精确时刻再出手（提前 0.4 秒）。
rem --seckill-only：只跑秒杀，不重复签到（资格由秒杀脚本自己补签）。
rem 遍历 configs 下所有 qwhdhub 账号逐个抢；某个号失败不影响其它号。
rem 前提：各号的 app_token 或已保存会话在开抢时仍有效，电脑不能关机。
rem 日志追加到 logs\seckill.log
python run_all.py --seckill-only --seckill-at 11:59:50 >> "logs\seckill.log" 2>&1
exit /b %errorlevel%
