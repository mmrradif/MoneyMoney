@echo off
title Money Maker Executable Builder
cls
echo ========================================================
echo       💰 MONEY MAKER EXE BUILDER (NO CODE SHARE) 💰
echo ========================================================
echo.
echo [1/3] Terminating any active Money_Maker background processes...
taskkill /F /IM "Money_Maker.exe" >nul 2>&1

echo.
echo [2/3] Compiling Python source code into Standalone Executable (.exe)...
echo.
python -m PyInstaller --noconfirm --onedir --console --distpath "dist_build" --collect-submodules encodings --add-data "templates;templates" --name "Money_Maker" main.py

if %ERRORLEVEL% NEQ 0 (
    echo ❌ PyInstaller build failed! Please check python/dependency errors.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [3/3] Syncing files to dist\Money_Maker...
if not exist "dist\Money_Maker" mkdir "dist\Money_Maker"
xcopy /E /Y /I "dist_build\Money_Maker" "dist\Money_Maker" >nul 2>&1
if exist "dist_build" rmdir /S /Q "dist_build" >nul 2>&1

echo.
echo ========================================================
echo ✅ BUILD COMPLETE!
echo Output folder: dist\Money_Maker
echo Executable file: dist\Money_Maker\Money_Maker.exe
echo You can zip ^& share the 'dist\Money_Maker' folder.
echo It contains ONLY .exe and compiled binaries (NO .py source code!)
echo ========================================================
pause
