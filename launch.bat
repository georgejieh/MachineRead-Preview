@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   MachineRead — Free Website Audit Tool
echo ========================================
echo.

:: --- Check Python ----------------------------------------------------------
echo [1/5] Checking Python 3.11+ ...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not on PATH.
    echo Install Python 3.11 or later from https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2 delims=." %%a in ('python -c "import sys; print(sys.version_info.minor)"') do set PY_MINOR=%%a
if %PY_MINOR% lss 11 (
    echo ERROR: Python 3.11+ is required. Found Python 3.%PY_MINOR%.
    pause
    exit /b 1
)
echo    Found Python 3.%PY_MINOR%

:: --- Check Node.js ---------------------------------------------------------
echo [2/5] Checking Node.js 18+ ...
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Node.js is not installed or not on PATH.
    echo Install Node.js 18 or later from https://nodejs.org/
    pause
    exit /b 1
)
for /f "tokens=1 delims=v." %%a in ('node -v') do set NODE_MAJOR=%%a
for /f %%a in ('node -e "console.log(process.version.match(/^v?(\d+)/)[1])"') do set NODE_MAJOR=%%a
if %NODE_MAJOR% lss 18 (
    echo ERROR: Node.js 18+ is required. Found Node.js v%NODE_MAJOR%.
    pause
    exit /b 1
)
echo    Found Node.js v%NODE_MAJOR%

:: --- Backend setup ---------------------------------------------------------
echo [3/5] Setting up backend ...
cd /d "%~dp0"

if not exist backend\.venv (
    echo    Creating virtual environment ...
    python -m venv backend\.venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo    Installing backend dependencies ...
call backend\.venv\Scripts\activate.bat
pip install -r backend\requirements.txt -q
if %errorlevel% neq 0 (
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
    if %errorlevel% neq 0 (
        echo ERROR: Failed to install frontend dependencies.
        cd ..
        pause
        exit /b 1
    )
)
echo    Frontend ready.
cd ..

:: --- Launch ----------------------------------------------------------------
echo [5/5] Starting MachineRead ...
echo.
echo    Backend:  http://localhost:8000
echo    Frontend: http://localhost:3000
echo.
echo    Press Ctrl+C to stop both servers.
echo.
echo ========================================

start "MachineRead Backend" cmd /c "set PYTHONPATH= && cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

:: Small delay so the backend starts first
timeout /t 3 /nobreak >nul

start "MachineRead Frontend" cmd /c "cd /d %~dp0frontend && npm run dev -- -p 3000"

:: Open browser
start http://localhost:3000

echo MachineRead is running. Close the server windows or press Ctrl+C here to stop.
pause >nul
