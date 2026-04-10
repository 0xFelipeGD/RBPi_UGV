#!/usr/bin/env bash
# UGV On-Board Software launcher
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "[ERROR] Virtual environment not found. Run setup.sh first."
    exit 1
fi

source "$VENV_DIR/bin/activate"
cd "$SCRIPT_DIR"

MONITOR_PID=""
if [ "${MONITOR_ENABLED:-1}" = "1" ]; then
    python3 monitor.py &
    MONITOR_PID=$!
    echo "[INFO] UGV Monitor → http://$(hostname -I | awk '{print $1}'):8080"
else
    echo "[INFO] UGV Monitor disabled (MONITOR_ENABLED=0)"
fi

cleanup() {
    if [ -n "$MONITOR_PID" ]; then
        kill "$MONITOR_PID" 2>/dev/null || true
    fi
}
trap cleanup SIGINT SIGTERM EXIT

python3 main.py "$@"
