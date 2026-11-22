@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - API server
echo Starting local API server on http://127.0.0.1:8765/
echo Open that URL in a browser to test.  Ctrl-C to stop.
"C:\AeroHandProj\.venv\Scripts\python.exe" C:\AeroHandProj\scripts\26_api.py %*
echo.
pause >nul