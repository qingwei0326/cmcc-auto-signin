@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Guandan auto-play (Migu). Requires gui_data\guandan_auth.json, which is
rem written by har_import.py whenever a HAR containing api/login is imported.
rem The "cat" token lives about 3 days, so re-capture once every 2-3 days.
rem --minutes 45 keeps a single run bounded; the game ends by itself after 15 hands.
python guandan_bot.py --minutes 45 >> "logs\guandan_bot_out.log" 2>&1
exit /b %errorlevel%
