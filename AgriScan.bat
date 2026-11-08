@echo off
setlocal
cd /d "%~dp0"

echo [1/4] Unblocking downloaded files from Windows SmartScreen...
powershell -NoProfile -Command "Get-ChildItem -Path '%~dp0' -Recurse -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue"

echo [2/4] Checking components...
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

echo [3/4] Launching AgriScan Desktop...
start "" "electron\dist\electron.exe" "%~dp0electron\main.cjs"
exit /b 0
