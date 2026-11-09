@echo off
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Could not activate .venv. Create it and install requirements.txt first.
    pause
    exit /b 1
)

echo Starting AgriScan server on http://127.0.0.1:8000 ...
start "" cmd /c "python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"

echo Waiting for AgriScan to initialize...
timeout /t 2 /nobreak >nul

start "" http://127.0.0.1:8000
