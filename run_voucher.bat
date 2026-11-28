@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
rem Coupon center: claim free coupons that are already open and not claimed yet.
rem --voucher-only: run only the coupon center, skip sign-in / seckill / games.
rem The UID comes from a freshly imported HAR; re-import right before running,
rem because the App ticket is short-lived.
python run_all.py --voucher-only --log "logs\voucher.log" >nul 2>&1
exit /b %errorlevel%
