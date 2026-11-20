@echo off
chcp 65001 >nul
rem Launch the web dashboard (opens http://127.0.0.1:8765 in the browser).
cd /d "%~dp0"
start "" pythonw dashboard.py
if errorlevel 1 (
  echo 启动失败，请确认已安装 Python 并加入 PATH
  pause
)
