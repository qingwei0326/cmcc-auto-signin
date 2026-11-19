@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Hourly BASS daily-reward collection (3 tiers, 60 min apart).
rem Log is written and rotated by run_all.py (keep 7 days).
python run_all.py --daily-only --log "logs\daily_reward.log" >nul 2>&1
exit /b %errorlevel%
