@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
title Aero Hand - RPS with CAMERA (LIVE hand)

if not exist ".venv312\Scripts\python.exe" (
  echo [ERROR] .venv312 not found - it is the mediapipe environment.
  echo         Run the install-deps bat first.
  pause
  exit /b 1
)

echo ============================================================
echo   Aero Hand - Rock / Paper / Scissors  (CAMERA + LIVE HAND)
echo ------------------------------------------------------------
echo   BEFORE YOU START:
echo     1) close the temperature monitor  (it locks COM5)
echo     2) nothing blocking the hand
echo     3) press Esc, or double-click the STOP-ALL bat, to release
echo.
echo   HOW TO PLAY:
echo     click NEXT ROUND  -^> 3 2 1 shoot  (camera listens while counting)
echo     show rock / scissors / paper and hold about 0.2 s
echo     the hand answers, then returns to open by itself
echo.
echo   One bout = 10 rounds.
echo ============================================================
echo.

rem ---- startup self-check: refuse to start if one is already running ----
".venv312\Scripts\python.exe" scripts\precheck.py
if errorlevel 1 (
  echo.
  echo [STOP] Fix the items above, then run this bat again.
  pause
  exit /b 1
)
echo.

".venv312\Scripts\python.exe" scripts\30_rps_game.py --cam --live --persona feiyu --rounds-per-bout 10 %*
set RC=%ERRORLEVEL%

echo.
echo Game window closed (exit code %RC%).
echo If the hand still looks tense, double-click the STOP-ALL bat.
pause