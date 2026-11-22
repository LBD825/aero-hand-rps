@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run the install .bat first.
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
echo ============================================================
echo  RPS game  (DRY-RUN by default: nothing is sent to the hand)
echo  Window: click "next round" ; keyboard 1/2/3 = rock/scissors/paper
echo  Esc = emergency stop (release + back to open)
echo ============================================================
".venv\Scripts\python.exe" scripts\30_rps_game.py %*
pause
