@echo off
rem One-click collector. Keep this file ASCII-only: cmd reads .bat in the OEM
rem code page, so non-ASCII comments get garbled and break parsing.
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto run

where python >nul 2>nul
if errorlevel 1 goto nopython

rem A system Python without the project dependencies would fail with a
rem confusing traceback, so check first and point at the setup step instead.
python -c "import sqlalchemy" >nul 2>nul
if errorlevel 1 goto needs_setup

set "PY=python"

:run
"%PY%" scripts\easy_collect.py
echo.
pause
exit /b 0

:needs_setup
echo.
echo [!] The Python environment is not ready yet.
echo     Double-click the START file in this folder once (first-time setup,
echo     takes a few minutes), then run this file again.
echo.
pause
exit /b 1

:nopython
echo.
echo [X] Python not found on this computer.
echo     1. Install Python 3.11 or newer from https://www.python.org/downloads/
echo     2. On the first install screen, tick "Add python.exe to PATH"
echo     3. Double-click the START file in this folder to finish setup.
echo.
pause
exit /b 1
