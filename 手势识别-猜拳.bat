@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - RPS gesture (rock / scissors / paper)
echo RPS gesture recognizer (camera). 3 classes only: rock / scissors / paper
echo   q = quit
echo   wants the API server running if you also want the hand to move?  use --api
echo.
".venv312\Scripts\python.exe" "scripts\27b_vision_rps.py" %*
echo.
pause >nul