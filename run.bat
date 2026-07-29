@echo off
setlocal
cd /d "%~dp0"
title ClearPost Doc Automation

REM Own port - AI Agent and others use 8000; do not share it.
set "PORT=8001"
set "HOST=127.0.0.1"
set "URL=http://%HOST%:%PORT%"

if not exist ".venv\Scripts\uvicorn.exe" (
    echo Creating virtual environment and installing dependencies...
    python -m venv .venv
    call ".venv\Scripts\pip.exe" install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo.
echo ============================================
echo   ClearPost Doc Automation
echo   %URL%
echo   Port %PORT% (Research Agent stays on 8000)
echo   Close this window to stop the server.
echo ============================================
echo.

REM Open THIS app only - never hardcode :8000
start "" cmd /c "timeout /t 2 /nobreak >nul & start %URL%"
".venv\Scripts\uvicorn.exe" app.main:app --reload --host %HOST% --port %PORT%
if errorlevel 1 (
    echo.
    echo Server exited with an error. Is port %PORT% already in use?
)
pause
