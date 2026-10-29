@echo off
setlocal
cd /d "%~dp0"
title AgriScan Desktop App
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app_desktop.py
) else (
    python app_desktop.py
)
