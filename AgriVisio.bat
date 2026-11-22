@echo off
setlocal
cd /d "%~dp0"

echo [1/2] Checking components...
set "ELECTRON_EXE=electron\dist\electron.exe"
if not exist "%ELECTRON_EXE%" (
    if exist "%LOCALAPPDATA%\Programs\AgriVisio\electron\dist\electron.exe" (
        set "ELECTRON_EXE=%LOCALAPPDATA%\Programs\AgriVisio\electron\dist\electron.exe"
    )
)

if not exist "%ELECTRON_EXE%" (
    echo [Error] electron\dist\electron.exe is missing. Please extract the full package or reinstall AgriVisio.
    pause
    exit /b 1
)

set "PYTHON_EXE=backend\python\python.exe"
if not exist "%PYTHON_EXE%" (
    if exist "%LOCALAPPDATA%\Programs\AgriVisio\backend\python\python.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\AgriVisio\backend\python\python.exe"
    )
)

echo [2/2] Launching AgriVisio Desktop...
start "" "%ELECTRON_EXE%" "%~dp0electron\main.cjs"
exit /b 0
