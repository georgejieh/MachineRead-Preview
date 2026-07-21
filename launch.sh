#!/usr/bin/env bash
set -euo pipefail

echo "========================================"
echo   MachineRead -- Free Website Audit Tool
echo "========================================"
echo

# --- Check Python -----------------------------------------------------------
echo "[1/5] Checking Python 3.11+ ..."
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not installed or not on PATH."
    echo "Install Python 3.11 or later from https://www.python.org/downloads/"
    exit 1
fi

PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$PY_MINOR" -lt 11 ]; then
    echo "ERROR: Python 3.11+ is required. Found Python 3.$PY_MINOR."
    exit 1
fi
echo "   Found Python 3.$PY_MINOR"

# --- Check Node.js ----------------------------------------------------------
echo "[2/5] Checking Node.js 18+ ..."
if ! command -v node &>/dev/null; then
    echo "ERROR: node is not installed or not on PATH."
    echo "Install Node.js 18 or later from https://nodejs.org/"
    exit 1
fi

NODE_MAJOR=$(node -e "console.log(process.version.match(/^v?(\d+)/)[1])")
if [ "$NODE_MAJOR" -lt 18 ]; then
    echo "ERROR: Node.js 18+ is required. Found Node.js v$NODE_MAJOR."
    exit 1
fi
echo "   Found Node.js v$NODE_MAJOR"

# --- Backend setup ----------------------------------------------------------
echo "[3/5] Setting up backend ..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d "backend/.venv" ]; then
    echo "   Creating virtual environment ..."
    python3 -m venv backend/.venv
fi

echo "   Installing backend dependencies ..."
source backend/.venv/bin/activate
pip install -r backend/requirements.txt -q
echo "   Backend ready."

# --- Frontend setup ---------------------------------------------------------
echo "[4/5] Setting up frontend ..."
cd frontend
if [ ! -d "node_modules" ]; then
    echo "   Installing frontend dependencies ..."
    npm install
fi
echo "   Frontend ready."
cd ..

# --- Launch -----------------------------------------------------------------
echo "[5/5] Starting MachineRead ..."
echo
echo "   Backend:  http://localhost:8000"
echo "   Frontend: http://localhost:3000"
echo
echo "   Press Ctrl+C to stop both servers."
echo
echo "========================================"

# Start backend in background
unset PYTHONPATH
cd "$SCRIPT_DIR/backend"
source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# Small delay so backend starts first
sleep 3

# Start frontend in foreground (Ctrl+C kills it and we clean up backend)
cd "$SCRIPT_DIR/frontend"
npm run dev -- -p 3000 &
FRONTEND_PID=$!

# Open browser
if command -v open &>/dev/null; then
    open http://localhost:3000
elif command -v xdg-open &>/dev/null; then
    xdg-open http://localhost:3000
fi

# Trap Ctrl+C to clean up both servers
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

# Wait for either to exit
wait
