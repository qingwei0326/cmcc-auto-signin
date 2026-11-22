@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Daily sign-in. Log is written and rotated by run_all.py (keep 7 days).
rem Runs at 11:45, ten minutes before the seckill: the qwhd ticket only lives
rem 1-2 hours, so one fresh ticket covers sign-in + session refresh + seckill
rem + games. That is why the whole day needs only ONE HAR per account.
python run_all.py --xxl --xxl-times 5 --log "logs\daily.log" >nul 2>&1
exit /b %errorlevel%
