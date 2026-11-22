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
echo  RPS game  --LIVE--  !! the hand WILL move !!
echo  Before running: close the temperature monitor (it locks COM5).
echo  Esc = emergency stop.  Ctrl-C in this window also stops.
echo ============================================================
choice /c YN /m "Type Y to drive the real hand"
if errorlevel 2 exit /b 0
".venv\Scripts\python.exe" scripts\30_rps_game.py --live %*
pause
