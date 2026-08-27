@echo off
title MoneyMoney AI Engine
cls
echo ========================================================
echo       💰 MONEY MAKER AI TRADING ENGINE 💰
echo ========================================================
echo.

echo [1/2] Clearing previous processes on Port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
    set LOCAL_IP=%%a
    goto :ip_found
)
:ip_found
set LOCAL_IP=%LOCAL_IP: =%

echo [2/2] Launching AI Trading Engine & Web Dashboard...
echo.
echo --------------------------------------------------------
echo 🌐 PC Local URL:       http://127.0.0.1:8000
if defined LOCAL_IP (
    echo 📱 Mobile / WiFi URL:  http://%LOCAL_IP%:8000
)
echo --------------------------------------------------------
echo.

cd /d "%~dp0"
python main.py

pause
