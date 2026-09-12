@echo off
chcp 65001 >nul
title Shopee Affiliate Offer Scraper
cd /d "%~dp0"

set PORT=8877
set DBPATH=artifacts\db\shopee.db
rem So process rieng xu ly dang video (moi process 1 port, tan dung nhieu loi CPU that su - xem
rem --video-workers trong scripts\affiliate_scrape_server.py). 2026-09-12: DE TRONG = tool TU
rem DONG tinh bang 80 phan tram so loi CPU logic cua may nay (theo yeu cau tool tu dat so
rem process thong minh theo 80% so loi cua may server). CHI dien so vao day neu muon TU TAY
rem ghi de (vd muon it hon de danh CPU cho viec khac tren cung may).
set VIDEO_WORKERS=
set VIDEO_WORKERS_ARG=
if not "%VIDEO_WORKERS%"=="" set VIDEO_WORKERS_ARG=--video-workers %VIDEO_WORKERS%

echo ============================================
echo   Shopee Affiliate Offer Scraper
echo ============================================
echo.

echo [1/3] Kiem tra Flask ...
python -m pip show flask >nul 2>&1
if errorlevel 1 (
    echo    [X] Chua cai Flask. Chay: python -m pip install -r requirements.txt
    pause
    exit /b 1
)
echo.

echo [2/3] Kiem tra server co dang chay san khong ...
rem Neu mo THEM 1 server trong khi ban da co san 1 cai dang chay, Windows KHONG bao loi
rem "port da dung" (2 tien trinh cung bind duoc 1 port) - request se bi ngau nhien roi vao
rem 1 trong 2 tien trinh, gay hien tuong bam nut tren dashboard "khong phan hoi" ngau nhien.
netstat -ano | findstr /C:":%PORT% " | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
    echo.
    echo    [!] Server co ve DA DANG CHAY tren port %PORT% roi ^(tu lan mo truoc^).
    echo    [!] Mo THEM 1 server nua se lam 2 tien trinh tranh nhau 1 port, gay loi
    echo        "bam nut khong phan hoi" ngau nhien tren dashboard.
    echo.
    choice /C CM /N /M "   [C] Chi mo trinh duyet toi server dang chay san ^(khuyen nghi^), [M] Van mo them: "
    if errorlevel 2 (
        echo    Ban chon mo THEM - tiep tuc...
    ) else (
        start http://127.0.0.1:%PORT%
        echo    Da mo trinh duyet toi server dang chay san. Dong cua so nay binh thuong.
        pause
        exit /b 0
    )
)
echo.

echo [3/3] Khoi dong server + mo trinh duyet ^(Ctrl+C de dung^) ...
echo    URL: http://127.0.0.1:%PORT%
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:%PORT%"

:run
python scripts\affiliate_scrape_server.py --port %PORT% --db-path %DBPATH% %VIDEO_WORKERS_ARG%
if %errorlevel%==42 (
    echo.
    echo [Cap nhat] Da tai code moi - khoi dong lai server...
    goto run
)

echo.
echo Da dong server.
pause
