@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem AI Dou Miao (China Mobile mall): sign + browse/share tasks + claim sunshine.
rem Shares configs/ with qwhd (app_token used as ticket); no separate config needed.
rem Run once daily; to run together with sign-in, use run_all.py --doumi instead.
python run_all.py --platform doumi --log "logs\doumi.log" >nul 2>&1
exit /b %errorlevel%
