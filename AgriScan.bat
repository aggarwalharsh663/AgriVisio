@echo off
setlocal
cd /d "%~dp0"

if exist "AgriScan.exe" (
    start "" "AgriScan.exe"
    exit /b 0
)

if exist "electron\dist\electron.exe" (
    start "" "electron\dist\electron.exe" "%~dp0electron"
    exit /b 0
)

echo [Error] AgriScan executable not found.
pause
