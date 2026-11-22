@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set VISION_POSE_SECS=0
title Aero Hand - gesture follow (INSTANT)

if not exist ".venv312\Scripts\python.exe" (
  echo [ERROR] .venv312 not found. Run the install-deps bat first.
  pause
  exit /b 1
)

echo ============================================================
echo   Gesture FOLLOW  --INSTANT--  (VISION_POSE_SECS=0)
echo ------------------------------------------------------------
echo   The hand jumps to the pose with no ramp.
echo   Big jumps (thumb abduction spans up to 51000 units) will spike
echo   current to 400+ for a moment. Three spikes in a row = estop
echo   and the API process exits by design.
echo.
echo   1) close the temperature monitor first (it locks COM5)
echo   2) the API must be running (double-click the API bat first)
echo   3) in the camera window press SPACE to enable follow
echo   4) SPACE again = stop;  the stop-all bat = full shutdown
echo ============================================================
echo.

rem ---- precheck: COM5 free / camera / model ----
".venv312\Scripts\python.exe" scripts\precheck.py
if errorlevel 1 (
  echo.
  echo [STOP] Fix the items above, then run this bat again.
  pause
  exit /b 1
)
echo.

".venv312\Scripts\python.exe" scripts\27_vision_gesture.py %*
pause