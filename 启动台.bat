@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - launcher
echo Starting Aero Hand launcher...
".venv\Scripts\python.exe" "scripts\00_launcher.py"
if errorlevel 1 pause