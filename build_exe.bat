@echo off
title MoneyMoney Executable Builder
cls
echo ========================================================
echo       💰 MONEY MAKER EXE BUILDER (NO CODE SHARE) 💰
echo ========================================================
echo.
echo [1/3] Terminating any active MoneyMoney background processes...
taskkill /F /IM "MoneyMoney.exe" >nul 2>&1

echo.
echo [2/3] Compiling Python source code into Standalone Executable (.exe)...
echo.
python -m PyInstaller --noconfirm --onedir --console --distpath "dist_build" --collect-submodules encodings --add-data "templates;templates" --name "MoneyMoney" main.py

if %ERRORLEVEL% NEQ 0 (
    echo ❌ PyInstaller build failed! Please check python/dependency errors.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [3/3] Syncing files to dist\MoneyMoney...
if not exist "dist\MoneyMoney" mkdir "dist\MoneyMoney"
xcopy /E /Y /I "dist_build\MoneyMoney" "dist\MoneyMoney" >nul 2>&1
if exist "dist_build" rmdir /S /Q "dist_build" >nul 2>&1

echo.
echo ========================================================
echo ✅ BUILD COMPLETE!
echo Output folder: dist\MoneyMoney
echo Executable file: dist\MoneyMoney\MoneyMoney.exe
echo You can zip ^& share the 'dist\MoneyMoney' folder.
echo It contains ONLY .exe and compiled binaries (NO .py source code!)
echo ========================================================
pause
