@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found.
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
echo ============================================================
echo  Power-on self check (READ ONLY): prints current / temperature
echo  It sends the current position back as target = release, no motion.
echo  Close the temperature monitor window first (it locks COM5).
echo ============================================================
".venv\Scripts\python.exe" scripts\16_safe_init.py --live --read %*
pause
