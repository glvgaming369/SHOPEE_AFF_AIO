@echo off
chcp 65001 >nul
title Shopee Affiliate Offer Scraper
cd /d "%~dp0"

set PORT=8877
set DBPATH=artifacts\db\shopee.db

echo ============================================
echo   Shopee Affiliate Offer Scraper
echo ============================================
echo.

echo [1/2] Kiem tra Flask ...
python -m pip show flask >nul 2>&1
if errorlevel 1 (
    echo    [X] Chua cai Flask. Chay: python -m pip install -r requirements.txt
    pause
    exit /b 1
)
echo.

echo [2/2] Khoi dong server + mo trinh duyet ^(Ctrl+C de dung^) ...
echo    URL: http://127.0.0.1:%PORT%
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:%PORT%"
python scripts\affiliate_scrape_server.py --port %PORT% --db-path %DBPATH%

echo.
echo Da dong server.
pause
