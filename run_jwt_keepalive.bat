@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem qwhdhub 会话保活：只续期 jwt 不签到。jwt 实测寿命约 2.5~3.5 小时，
rem 每 90 分钟跑一次即可让凭证链无限延长，避免频繁抓包。
python run_all.py --keepalive --log "logs\keepalive.log" >nul 2>&1
exit /b %errorlevel%
