@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Extended activities: Saturday game center, drama rewards, weekly phone-charge lottery.
rem --games-only: run only the extended activities, skip BASS / mini-game / seckill.
rem Draw windows are enforced by the script itself (Sat 12:00+, Sun only).
rem A valid app_token is required; import a fresh HAR if the ticket expired.
python run_all.py --games-only --log "logs\games.log" >nul 2>&1
exit /b %errorlevel%
