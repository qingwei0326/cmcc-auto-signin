@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Sign-in "weekly flash sale": grab a 1 CNY phone-credit coupon at 12:00.
rem Started at 11:55; retry session setup until just after the firing time.
rem --seckill-only: run the seckill only, no repeated sign-in.
rem All accounts under configs wait and fire concurrently at the same instant.
rem A HAR imported while this script is waiting is picked up on the next retry.
python run_all.py --seckill-only --seckill-at 11:59:50 >> "logs\seckill.log" 2>&1
exit /b %errorlevel%
