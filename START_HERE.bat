@echo off
title Instagram Auto Reeler
cd /d "%~dp0"
echo.
echo  ==== Instagram Auto Reeler ====
echo.

py -3 --version >nul 2>nul
if errorlevel 1 (
  echo Installing Python...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo Python installed. CLOSE this window and double-click START_HERE.bat again.
  pause
  exit /b
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo Installing ffmpeg...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  echo.
  echo ffmpeg installed. CLOSE this window and double-click START_HERE.bat again.
  pause
  exit /b
)

if not exist .venv\Scripts\python.exe (
  echo Setting up - first run only, takes a few minutes...
  py -3 -m venv .venv
)
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt "instagrapi[video]"
.venv\Scripts\python -m pip install -q --no-deps "moviepy==2.2.1"

set /p NOW="How many Reels should I post RIGHT NOW before the daily schedule starts? (e.g. 1): "
if "%NOW%"=="" set NOW=1
.venv\Scripts\python -m reeler -c config.pc.yaml pc-run --now %NOW%
pause
