#!/usr/bin/env bash
# Update the MachineRead benchmark profile snapshot.
#
# This is the explicit, on-demand entry point for refreshing the
# benchmark peer profiles. It is NOT run by launch.sh / launch.bat;
# users run it directly when they want fresh benchmark data.
#
# Usage:
#   ./scripts/update_benchmarks.sh
#   ./scripts/update_benchmarks.sh --peers custom.json --out backend/private_data/benchmark_profiles.json
#   ./scripts/update_benchmarks.sh --concurrency 4
#
# Defaults match the public-tree sample peers and the standard
# private_data output path. Pass --peers and/or --out to override.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure the backend virtualenv exists. The refresh script imports the
# app, which depends on backend/.venv being installed.
if [ ! -d "$REPO_ROOT/backend/.venv" ]; then
    echo "ERROR: backend/.venv not found. Run launch.sh first (or run"
    echo "       'python -m venv backend/.venv && source backend/.venv/bin/activate"
    echo "       && pip install -r backend/requirements.txt')."
    exit 1
fi

# Activate the venv so the python interpreter resolves the app package.
# shellcheck disable=SC1091
source "$REPO_ROOT/backend/.venv/bin/activate"

cd "$REPO_ROOT"

PEERS="${REPO_ROOT}/scripts/benchmark_peers.sample.json"
OUT="${REPO_ROOT}/backend/private_data/benchmark_profiles.json"
CONCURRENCY=2

# Forward any extra args (e.g. --peers /path/to/peers.json --concurrency 4)
python "$REPO_ROOT/scripts/refresh_benchmarks.py" \
    --peers "$PEERS" \
    --out "$OUT" \
    --concurrency "$CONCURRENCY" \
    "$@"
