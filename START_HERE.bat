@echo off
title Auto Poster - Instagram Reels + YouTube Shorts
cd /d "%~dp0"
echo.
echo  ==== Auto Poster: Instagram Reels + YouTube Shorts ====
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
.venv\Scripts\python -m pip install -q -r requirements.txt "instagrapi[video]" playwright
.venv\Scripts\python -m pip install -q --no-deps "moviepy==2.2.1"

rem The program asks its questions itself (videos folder, topic, posts per day, where to post)
rem and remembers the answers. Next time you can keep them or change them.
.venv\Scripts\python -m reeler start
pause
