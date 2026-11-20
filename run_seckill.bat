@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Sign-in "weekly flash sale": grab a 1 CNY phone-credit coupon at 12:00.
rem Started at 10:30; the script waits until 12:00 internally (0.4s early).
rem --seckill-only: run the seckill only, no repeated sign-in.
rem All accounts under configs wait and fire concurrently at the same instant.
rem Requires a valid session or app_token at fire time; the PC must be on.
python run_all.py --seckill-only --seckill-at 11:59:50 >> "logs\seckill.log" 2>&1
exit /b %errorlevel%
