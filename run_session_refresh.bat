@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Refresh qwhdhub activity session for the 12:00 seckill.
rem Activity session cookie expires 2h after creation (server returns GOTO_LOGIN).
rem Signing in at 10:15 creates a session valid until 12:15, covering the seckill.
rem Only runs qwhdhub signin; does not touch BASS or the game.
python run_all.py --platform qwhd --log "logs\session_refresh.log" >nul 2>&1
exit /b %errorlevel%
