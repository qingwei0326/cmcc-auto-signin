@echo off
rem 启动 Web 图形控制台（自动打开浏览器 http://127.0.0.1:8765）
cd /d "%~dp0"
start "" pythonw dashboard.py
if errorlevel 1 (
  echo 启动失败，请确认已安装 Python 并加入 PATH
  pause
)
