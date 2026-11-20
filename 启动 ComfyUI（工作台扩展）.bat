@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] venv python not found: %PYTHON%
  pause
  exit /b 1
)
"%PYTHON%" scripts\comfyui_launcher.py %*
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" (
  echo.
  echo [ERROR] ComfyUI exited with code %EXITCODE%.
  pause
)
endlocal
exit /b %EXITCODE%
