#!/usr/bin/env bash
# =========================================================================
# UGV On-Board Software — Preflight Checkup
# Usage: bash checkup.sh
# Run BEFORE setup.sh to verify the Raspberry Pi is ready for installation.
# =========================================================================
set -uo pipefail

# ── Color codes ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
WARN=0
FAIL=0

pass() {
    echo -e "  ${GREEN}[OK]${NC}   $1"
    ((PASS++))
}

warn() {
    echo -e "  ${YELLOW}[WARN]${NC} $1"
    ((WARN++))
}

fail() {
    echo -e "  ${RED}[FAIL]${NC} $1"
    ((FAIL++))
}

echo ""
echo "============================================"
echo "  UGV Preflight Checkup"
echo "============================================"
echo ""

# ── 1. Architecture ──
ARCH="$(uname -m)"
if [[ "$ARCH" == "aarch64" || "$ARCH" == "armv7l" ]]; then
    pass "Architecture: $ARCH"
else
    fail "Architecture: $ARCH (expected aarch64 or armv7l)"
fi

# ── 2. Python 3.11+ ──
PYTHON_FOUND=""
for cmd in python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        if [[ -n "$ver" ]]; then
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
                PYTHON_FOUND="$cmd ($ver)"
                break
            fi
        fi
    fi
done

if [[ -n "$PYTHON_FOUND" ]]; then
    pass "Python: $PYTHON_FOUND"
else
    fail "Python 3.11+ not found (tried python3.13, python3.12, python3.11, python3)"
fi

# ── 3. I2C ──
if [[ -e /dev/i2c-1 ]]; then
    pass "I2C: /dev/i2c-1 exists"
else
    fail "I2C: /dev/i2c-1 not found (enable with: sudo raspi-config nonint do_i2c 0)"
fi

# ── 4. GPIO access ──
if [[ -e /dev/gpiomem ]]; then
    if [[ -r /dev/gpiomem ]]; then
        pass "GPIO: /dev/gpiomem accessible"
    else
        fail "GPIO: /dev/gpiomem exists but not readable (check group permissions)"
    fi
elif [[ -e /dev/mem ]]; then
    warn "GPIO: /dev/gpiomem not found, /dev/mem exists (may need root)"
else
    fail "GPIO: neither /dev/gpiomem nor /dev/mem found"
fi

# ── 5. Conflicting services ──
CONFLICTS=""
for svc in mosquitto nodered node-red; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        CONFLICTS="${CONFLICTS} ${svc}"
    fi
done

# Check for python processes that could hold GPIO/I2C (exclude this script's own subshells)
PYTHON_PROCS=$(pgrep -a python 2>/dev/null | grep -v "checkup" | grep -v "pgrep" || true)
if [[ -n "$PYTHON_PROCS" ]]; then
    CONFLICTS="${CONFLICTS} python"
fi

if [[ -z "$CONFLICTS" ]]; then
    pass "No conflicting services detected"
else
    warn "Potentially conflicting processes:${CONFLICTS}"
fi

# ── 6. Port conflicts ──
PORT_ISSUES=""
for port in 1883 1880; do
    if ss -tlnp 2>/dev/null | grep -q ":${port} " ; then
        PORT_ISSUES="${PORT_ISSUES} ${port}"
    fi
done

if [[ -z "$PORT_ISSUES" ]]; then
    pass "No port conflicts (1883, 1880 are free)"
else
    warn "Ports in use:${PORT_ISSUES} (may conflict with local mosquitto or node-red)"
fi

# ── 7. Internet connectivity ──
if ping -c 1 -W 3 google.com &>/dev/null; then
    pass "Internet: reachable (google.com)"
else
    fail "Internet: cannot reach google.com (needed for apt and pip)"
fi

# ── 8. Disk space ──
AVAIL_KB=$(df / --output=avail 2>/dev/null | tail -1 | tr -d ' ')
if [[ -n "$AVAIL_KB" && "$AVAIL_KB" =~ ^[0-9]+$ ]]; then
    AVAIL_MB=$((AVAIL_KB / 1024))
    if [[ "$AVAIL_MB" -ge 500 ]]; then
        pass "Disk space: ${AVAIL_MB} MB free on /"
    else
        fail "Disk space: only ${AVAIL_MB} MB free on / (need at least 500 MB)"
    fi
else
    warn "Disk space: could not determine free space on /"
fi

# ── 9. Camera ──
CAMERA_FOUND=false
if command -v libcamera-hello &>/dev/null; then
    if libcamera-hello --list-cameras 2>&1 | grep -qi "available"; then
        CAMERA_FOUND=true
    fi
fi
if [[ "$CAMERA_FOUND" == false ]]; then
    # Fallback: check /dev/video*
    if ls /dev/video* &>/dev/null; then
        CAMERA_FOUND=true
    fi
fi

if [[ "$CAMERA_FOUND" == true ]]; then
    pass "Camera: device detected"
else
    warn "Camera: no camera detected (video streaming will not work)"
fi

# ── 10. Existing installation ──
if systemctl list-unit-files 2>/dev/null | grep -q "ugv.service"; then
    warn "Existing UGV service found (ugv.service is already installed)"
else
    pass "No existing UGV service installed"
fi

# ── Summary ──
echo ""
echo "============================================"
TOTAL=$((PASS + WARN + FAIL))
echo -e "  ${BOLD}Results:${NC} ${GREEN}${PASS} passed${NC}, ${YELLOW}${WARN} warnings${NC}, ${RED}${FAIL} failures${NC}  (${TOTAL} checks)"
echo "============================================"

if [[ "$FAIL" -gt 0 ]]; then
    echo ""
    echo -e "  ${RED}Failures detected.${NC} Fix the issues above before running setup.sh."
    echo ""
    exit 1
elif [[ "$WARN" -gt 0 ]]; then
    echo ""
    echo -e "  ${YELLOW}Warnings present${NC} but non-blocking. You may proceed with setup.sh."
    echo ""
    exit 0
else
    echo ""
    echo -e "  ${GREEN}All checks passed.${NC} Ready for setup.sh."
    echo ""
    exit 0
fi
