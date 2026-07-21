@echo off
setlocal enabledelayedexpansion

REM Update the MachineRead benchmark profile snapshot.
REM
REM This is the explicit, on-demand entry point for refreshing the
REM benchmark peer profiles. It is NOT run by launch.bat; users run
REM it directly when they want fresh benchmark data.
REM
REM Usage:
REM   scripts\update_benchmarks.bat
REM   scripts\update_benchmarks.bat --peers custom.json --out backend\private_data\benchmark_profiles.json
REM   scripts\update_benchmarks.bat --concurrency 4

set SCRIPT_DIR=%~dp0
pushd "%SCRIPT_DIR%.."

if not exist "backend\.venv\Scripts\python.exe" (
    echo ERROR: backend\.venv not found. Run launch.bat first ^(or run
    echo        "python -m venv backend\.venv ^&^& backend\.venv\Scripts\activate.bat
    echo        ^&^& pip install -r backend\requirements.txt"^).
    popd
    exit /b 1
)

set PEERS=%CD%\scripts\benchmark_peers.sample.json
set OUT=%CD%\backend\private_data\benchmark_profiles.json
set CONCURRENCY=2

REM Forward any extra args (e.g. --peers custom.json --concurrency 4)
call "backend\.venv\Scripts\python.exe" "scripts\refresh_benchmarks.py" --peers "%PEERS%" --out "%OUT%" --concurrency %CONCURRENCY% %*
set RC=%ERRORLEVEL%
popd
exit /b %RC%
