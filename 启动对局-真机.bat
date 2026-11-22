@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title Aero Hand - RPS with REAL HAND (keyboard input)

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found.
  pause
  exit /b 1
)

echo ============================================================
echo   Aero Hand - Rock / Paper / Scissors  (REAL HAND, keyboard)
echo ------------------------------------------------------------
echo   THE HAND WILL MOVE.  Close the temperature monitor first.
echo   Esc or the STOP-ALL bat = emergency release.
echo   Type 1 2 3 in this window to throw rock / scissors / paper.
echo ============================================================
echo.

rem ---- startup self-check ----
".venv\Scripts\python.exe" scripts\precheck.py
if errorlevel 1 (
  echo.
  echo [STOP] Fix the items above, then run this bat again.
  pause
  exit /b 1
)
echo.

".venv\Scripts\python.exe" scripts\30_rps_game.py --live %*
set RC=%ERRORLEVEL%

echo.
echo Game ended (exit code %RC%).
echo If the hand still looks tense, double-click the STOP-ALL bat.
pause