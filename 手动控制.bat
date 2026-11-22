@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - manual control (GUI)
echo Opening manual control window...
"C:\AeroHandProj\.venv\Scripts\python.exe" C:\AeroHandProj\scripts\24_gui_manual.py
if errorlevel 1 pause