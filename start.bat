@echo off
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Could not activate .venv. Create it and install requirements.txt first.
    pause
    exit /b 1
)
start "" http://127.0.0.1:8000
python -m uvicorn backend.main:app --reload
