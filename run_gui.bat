@echo off
chcp 65001 >nul
rem Browser mode: run dashboard.py (it serves 127.0.0.1 and opens the browser).
rem Keep the console open with cmd /k so a startup error stays readable.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.9+ 并勾选 Add Python to PATH
    pause
    exit /b 1
)
start "签到控制台" cmd /k python dashboard.py
