@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Refresh qwhdhub activity session for the 12:00 seckill.
rem Activity session cookie expires 2h after creation (server returns GOTO_LOGIN).
rem Runs at 11:54, shortly before the 12:00 seckill.
rem The seckill task starts at 11:55 and reuses this session when available.
python run_all.py --platform qwhd --log "logs\session_refresh.log" >nul 2>&1
exit /b %errorlevel%
