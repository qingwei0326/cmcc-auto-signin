@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem qwhdhub session keepalive: log in only, no sign-in.
rem Runs every 90 minutes to probe whether the app_token is still usable.
python run_all.py --keepalive --log "logs\keepalive.log" >nul 2>&1
exit /b %errorlevel%
