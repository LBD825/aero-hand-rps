@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
title Aero Hand - pack RPS for a friend

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run the install-deps bat first.
  pause
  exit /b 1
)

echo ============================================================
echo   Pack the RPS game for a friend
echo ------------------------------------------------------------
echo   A = play the game only  (camera + software hand, about 7.6 MB)
echo   B = A plus REAL HAND    (agent + sim2real + live bats, about 7.7 MB)
echo.
echo   Output goes to:  ??\RPS??-^<date^>-^<A or B^>\
echo   Nothing secret is packed (no venv, logs, tokens).
echo ============================================================
echo.
choice /c AB /m "Choose tier A (game only) or B (with real hand)"
if errorlevel 2 goto tierB
goto tierA

:tierA
echo.
echo [A] packing...
".venv\Scripts\python.exe" scripts\pack_dist.py --tier A
goto done

:tierB
echo.
echo [B] packing...
".venv\Scripts\python.exe" scripts\pack_dist.py --tier B
goto done

:done
echo.
echo Finished. Open ?? folder to see the package.
pause
