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

python3 monitor.py &
MONITOR_PID=$!

cleanup() {
    kill "$MONITOR_PID" 2>/dev/null || true
}
trap cleanup SIGINT SIGTERM EXIT

echo "[INFO] UGV Monitor → http://$(hostname -I | awk '{print $1}'):8080"

python3 main.py "$@"
