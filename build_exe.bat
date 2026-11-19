@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 签到控制台 · 桌面版打包

echo ============================================
echo   签到控制台 · Windows 桌面版一键打包
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.9+ 并勾选 Add to PATH
    pause
    exit /b 1
)

echo [1/3] 检查打包依赖 pywebview + pyinstaller ...
python -c "import webview, PyInstaller" >nul 2>nul
if not errorlevel 1 (
    echo   依赖已安装，跳过。
    goto BUILD
)

echo   正在安装（用默认 PyPI 源，清华源可能缺 pywebview）...
python -m pip install --upgrade pywebview pyinstaller
if errorlevel 1 (
    echo   默认源失败，尝试清华源 ...
    python -m pip install --upgrade pywebview pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
)
python -c "import webview, PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试
    pause
    exit /b 1
)

:BUILD
echo.
echo [2/3] 开始打包（首次约 2-5 分钟，请耐心等待）...
set ICONFLAG=
if exist app.ico set ICONFLAG=--icon app.ico
python -m PyInstaller --noconfirm --clean --onefile --windowed %ICONFLAG% --name 签到控制台 --collect-all webview app_desktop.py
if errorlevel 1 (
    echo [错误] 打包失败，请把上方报错信息发给助手
    pause
    exit /b 1
)

echo.
echo [3/3] 打包完成！
echo.
echo   exe 位置: %~dp0dist\签到控制台.exe
echo.
echo   ★ 使用方法：把 dist\签到控制台.exe 复制到项目根目录
echo     （与 cmcc_sign.py、dashboard.py 同一层级），双击运行即可。
echo   ★ 注意：签到任务仍由系统 Python 执行，电脑上需保留 Python
echo     和已安装的 requests 等依赖（你平时能跑脚本就没问题）。
echo.
pause
