@echo off
setlocal
cd /d "%~dp0"

echo [1/2] Checking components...
if not exist "electron\dist\electron.exe" (
    echo [Error] electron\dist\electron.exe is missing. Please extract the full zip package.
    pause
    exit /b 1
)

if not exist "backend\python\python.exe" (
    echo [Error] backend\python\python.exe is missing. Please extract the full zip package.
    pause
    exit /b 1
)

echo [2/2] Launching AgriScan Desktop...
start "" "electron\dist\electron.exe" "%~dp0electron\main.cjs"
exit /b 0
