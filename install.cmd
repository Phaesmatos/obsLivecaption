@echo off
setlocal
where py >nul 2>nul
if errorlevel 1 (
  python -m venv "%~dp0.venv"
) else (
  py -3 -m venv "%~dp0.venv"
)
if errorlevel 1 exit /b 1
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 exit /b 1
echo.
echo Installed. Try: start.cmd --demo
exit /b 0
