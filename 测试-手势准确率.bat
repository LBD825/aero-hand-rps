@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - gesture accuracy test

if not exist ".venv312\Scripts\python.exe" (
  echo [ERROR] .venv312 not found. Run the install-deps bat first.
  pause
  exit /b 1
)

echo ============================================================
echo   Gesture accuracy test  (12 gestures, 3 s each, about 40 s)
echo ------------------------------------------------------------
echo   A window will pop up with a big prompt: do that gesture.
echo   Keep your hand back-of-hand up, fingers pointing up.
echo   Press q in the window to quit early.
echo.
echo   The result is also saved under  ??\?????-*.md
echo ============================================================
echo.

".venv312\Scripts\python.exe" scripts\33_gesture_accuracy.py %*
echo.
pause