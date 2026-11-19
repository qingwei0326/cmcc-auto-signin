@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem 日志由 run_all.py 统一写入并按天轮转（保留 7 天）
python run_all.py --xxl --xxl-times 5 --log "logs\daily.log" >nul 2>&1
exit /b %errorlevel%
