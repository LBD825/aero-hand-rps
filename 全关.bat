@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - stop all

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run the install-deps bat first.
  pause
  exit /b 1
)

echo ============================================================
echo   Aero Hand: STOP ALL  (release hand + kill components)
echo ============================================================
".venv\Scripts\python.exe" "scripts\stop_all.py" %*
echo.
echo Press any key to close this window.
pause >nul
