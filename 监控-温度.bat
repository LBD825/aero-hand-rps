@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - live monitor (Ctrl-C to stop)
".venv\Scripts\python.exe" "scripts\14_monitor.py" --hz 2 --csv "monitor-live.csv"
echo.
echo [monitor stopped] the csv is monitor-live.csv
pause
