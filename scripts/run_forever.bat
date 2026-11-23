@echo off
REM Keeps running and posts a Reel at every slot in config.yaml. Leave this window open.
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat 2>nul
python -m reeler run
pause
