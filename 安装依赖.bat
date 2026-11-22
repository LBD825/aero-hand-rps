@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

echo ============================================================
echo   Aero Hand Open - install dependencies
echo ============================================================
echo   1) main env   .venv      (Python 3.13) serial + GUI + API
echo   2) vision env .venv312   (Python 3.12) mediapipe camera
echo.
echo   This needs "uv" on PATH  (https://docs.astral.sh/uv/)
echo   If you do not have uv, install it first, then run this again.
echo ============================================================
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] uv not found. Install uv first.
  pause
  exit /b 1
)

echo [1/3] creating .venv (Python 3.13) ...
if not exist ".venv\Scripts\python.exe" (
  uv venv --python 3.13 .venv
)

echo [2/3] installing main deps ...
uv pip install --python ".venv\Scripts\python.exe" -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple

echo [3/3] creating .venv312 (Python 3.12) and vision deps ...
if not exist ".venv312\Scripts\python.exe" (
  uv venv --python 3.12 .venv312
)
uv pip install --python ".venv312\Scripts\python.exe" -r requirements-vision.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple

echo.
echo ============================================================
echo   Done. Try the portable launcher bat   (or the manual-control bat)
echo ============================================================
pause
