@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem app_token lifetime probe: one pass per run, state kept in gui_data\token_life.
rem Scheduled every 2 minutes so expiry is pinned down to the minute.
python probe_token_life.py --once >> "logs\token_life.log" 2>&1
exit /b %errorlevel%
