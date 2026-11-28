@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem One-shot morning run: sign + xxl + games + doumi + voucher + seckill.
rem Seckill script waits internally until 11:59:50; one fresh HAR covers all.
rem Launch at 11:40; send a fresh HAR to the inbox at 11:30-11:40.
python run_all.py --xxl --xxl-times 5 --games --doumi --voucher --seckill --seckill-at 11:59:50 --log "logs\morning.log" >nul 2>&1
exit /b %errorlevel%
