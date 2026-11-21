@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Check inbox for HAR attachments every 10 minutes and import them.
python mail_watch.py --once >>"logs\mail_watch.log" 2>&1
exit /b %errorlevel%
