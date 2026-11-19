@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Daily sign-in. Log is written and rotated by run_all.py (keep 7 days).
python run_all.py --xxl --xxl-times 5 --log "logs\daily.log" >nul 2>&1
exit /b %errorlevel%
