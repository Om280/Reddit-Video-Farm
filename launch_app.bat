@echo off
rem Double-click to open the Reddit Video Maker desktop app (no console window).
cd /d "%~dp0"
if not exist "venv\Scripts\pythonw.exe" (
    echo venv not found. Run: python -m venv venv ^&^& venv\Scripts\pip install -e .
    pause
    exit /b 1
)
start "" "venv\Scripts\pythonw.exe" gui.py
