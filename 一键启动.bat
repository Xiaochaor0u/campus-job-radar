@echo off
rem One-click launcher. ASCII-only on purpose: cmd reads .bat in the OEM code
rem page and mangles non-ASCII comments. All Chinese output comes from Python.
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"

if exist "%PY%" goto start

echo.
echo First run: preparing the Python environment. This may take a few minutes.
echo Please keep this window open.

where python >nul 2>nul
if errorlevel 1 goto nopython

python -m venv ".venv"
if errorlevel 1 goto fail

"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto fail

:start
echo.
echo Starting the site. Keep this window open; press Ctrl+C to stop.
echo Address: http://127.0.0.1:8010/?view=intelligence
echo.
"%PY%" scripts\quickstart.py

echo.
echo The site has stopped.
pause
exit /b 0

:nopython
echo.
echo [X] Python not found on this computer.
echo     1. Install Python 3.11 or newer from https://www.python.org/downloads/
echo     2. On the first install screen, tick "Add python.exe to PATH"
echo     3. Run this file again.
echo.
pause
exit /b 1

:fail
echo.
echo [X] Setup failed. Check your network, then run this file again.
echo     Manual steps are in README section 3.
echo.
pause
exit /b 1
