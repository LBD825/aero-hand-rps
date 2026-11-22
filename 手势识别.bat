@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - vision gesture control
echo Starting webcam gesture control...
echo   space = toggle auto execute    r = reload poses    q = quit
echo   NOTE: the API server must be running first (run the API bat)
echo.
".venv312\Scripts\python.exe" "scripts\27_vision_gesture.py"
echo.
pause >nul