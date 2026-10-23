@echo off
setlocal
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0gladia_captions.py" %*
) else (
  echo Dependencies are not installed. Run install.cmd first.
  exit /b 1
)
exit /b %errorlevel%
