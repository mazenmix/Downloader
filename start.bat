@echo off
title MX Downloader
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python is not installed or is not in PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo MX Downloader is starting...
echo Open: http://127.0.0.1:8000
echo.
python -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
