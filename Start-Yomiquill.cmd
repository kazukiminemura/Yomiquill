@echo off
setlocal
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0start.py" %*
) else (
    py -3.12-64 "%~dp0start.py" %*
)
if not errorlevel 1 exit /b 0
echo.
echo Yomiquill could not start. Check the message above and run this file again.
echo First run requires 64-bit Python 3.12: https://www.python.org/downloads/windows/
if /i not "%~1"=="--setup-only" pause
exit /b 1
