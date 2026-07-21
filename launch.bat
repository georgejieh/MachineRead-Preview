@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   MachineRead - Free Website Audit Tool
echo ========================================
echo.

:: --- Locate Python --------------------------------------------------------
echo [1/5] Checking Python 3.11+ ...
where python >nul 2>&1
if %errorlevel% neq 0 (
    call :ensure_on_path "C:\Python311" "C:\Python312" "C:\Program Files\Python311" "C:\Program Files\Python312" "%LOCALAPPDATA%\Programs\Python\Python311" "%LOCALAPPDATA%\Programs\Python\Python312"
    where python >nul 2>&1
    if !errorlevel! neq 0 (
        echo ERROR: Python is not installed or not on PATH.
        echo Install Python 3.11 or later from https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo    Found %%v

:: --- Locate Node ----------------------------------------------------------
echo [2/5] Checking Node.js 18+ ...
where node >nul 2>&1
if %errorlevel% neq 0 (
    call :ensure_on_path "%NVM_SYMLINK%" "%NVM_HOME%" "C:\nvm4w\nodejs" "C:\Program Files\nodejs"
    where node >nul 2>&1
    if !errorlevel! neq 0 (
        echo ERROR: Node.js is not installed or not on PATH.
        echo Install Node.js 18 or later from https://nodejs.org/
        pause
        exit /b 1
    )
)
for /f "tokens=*" %%v in ('node --version 2^>^&1') do echo    Found %%v

:: --- Backend setup --------------------------------------------------------
echo [3/5] Setting up backend ...
cd /d "%~dp0"

if not exist backend\.venv (
    echo    Creating virtual environment ...
    python -m venv backend\.venv
    if !errorlevel! neq 0 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo    Installing backend dependencies ...
call backend\.venv\Scripts\activate.bat
pip install -r backend\requirements.txt -q
if !errorlevel! neq 0 (
    echo ERROR: Failed to install backend dependencies.
    pause
    exit /b 1
)
echo    Backend ready.

:: --- Frontend setup --------------------------------------------------------
echo [4/5] Setting up frontend ...
cd frontend
if not exist node_modules (
    echo    Installing frontend dependencies ...
    call npm install
    if !errorlevel! neq 0 (
        echo ERROR: Failed to install frontend dependencies.
        cd ..
        pause
        exit /b 1
    )
)
echo    Frontend ready.
cd ..

:: --- Refresh benchmark profiles (best-effort) ----------------------------
echo    Refreshing benchmark profiles (best-effort) ...
call backend\.venv\Scripts\python.exe scripts\refresh_benchmarks.py --peers scripts\benchmark_peers.sample.json --out backend\private_data\benchmark_profiles.json >nul 2>&1
if !errorlevel! neq 0 (
    echo    Benchmarks refresh skipped ^(using bundled samples^).
) else (
    echo    Benchmarks refreshed.
)

:: --- Launch ----------------------------------------------------------------
echo [5/5] Starting MachineRead ...
echo.
echo    Backend:  http://localhost:8000
echo    Frontend: http://localhost:3000
echo.
echo    Press Ctrl+C to stop both servers.
echo.
echo ========================================

start "MachineRead Backend" cmd /c "cd /d %~dp0 && backend\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

timeout /t 3 /nobreak >nul

start "MachineRead Frontend" cmd /c "cd /d %~dp0frontend && npm run dev -- -p 3000"

start http://localhost:3000

echo MachineRead is running. Close the server windows or press Ctrl+C here to stop.
pause >nul

:: --- helpers --------------------------------------------------------------
:ensure_on_path
:loop_ensure
if "%~1"=="" goto :eof
if exist "%~1" (
    set "PATH=%PATH%;%~1"
)
shift
goto :loop_ensure
