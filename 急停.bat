@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - EMERGENCY STOP
echo ==========================================
echo   EMERGENCY STOP - releasing all servos
echo ==========================================
"C:\AeroHandProj\.venv\Scripts\python.exe" C:\AeroHandProj\scripts\25_estop_now.py
echo.
echo [done] servos released. press any key to close
pause >nul