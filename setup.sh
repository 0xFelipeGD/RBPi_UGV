#!/usr/bin/env bash
# =========================================================================
# UGV On-Board Software — Raspberry Pi Setup
# Usage: bash setup.sh
# =========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

echo ""
echo "============================================"
echo "  UGV On-Board Software Setup"
echo "============================================"
echo ""

# ── Check Raspberry Pi ──
if [[ "$(uname -m)" != "aarch64" && "$(uname -m)" != "armv7l" ]]; then
    echo "[WARN] Not running on ARM architecture. Some hardware drivers may not work."
fi

# ── Check Python 3.11+ ──
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "[ERROR] Python 3.11+ is required."
    exit 1
fi
echo "[OK] Python: $($PYTHON --version)"

# ── System dependencies ──
echo "Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-venv \
    python3-dev \
    python3-smbus \
    i2c-tools \
    python3-picamera2 \
    python3-libcamera \
    python3-numpy \
    2>/dev/null || echo "[WARN] Some packages may need manual install"

# ── Enable interfaces ──
echo "Enabling hardware interfaces..."
sudo raspi-config nonint do_i2c 0 2>/dev/null || echo "[INFO] Enable I2C manually: sudo raspi-config"
sudo raspi-config nonint do_serial_hw 0 2>/dev/null || echo "[INFO] Enable Serial manually: sudo raspi-config"

# Enable 1-Wire for DS18B20 temperature sensors
if ! grep -q "dtoverlay=w1-gpio" /boot/firmware/config.txt 2>/dev/null; then
    echo "dtoverlay=w1-gpio" | sudo tee -a /boot/firmware/config.txt > /dev/null
    echo "[INFO] Added 1-Wire overlay to /boot/firmware/config.txt (reboot needed)"
fi

# ── Virtual environment ──
# IMPORTANT: must be created with --system-site-packages so the venv can import
# python3-picamera2 and python3-libcamera, which are installed via apt above
# (they bundle compiled libcamera bindings and cannot be pip-installed on Pi OS).
# Without this flag, camera/pi_camera_track.py silently falls back to the SMPTE
# test pattern. See BUGS_FIXED.md (BUG-017).
VENV_DIR="$SCRIPT_DIR/venv"
if [[ -d "$VENV_DIR" ]]; then
    # Self-heal: detect old venv created without --system-site-packages
    if ! grep -q "include-system-site-packages = true" "$VENV_DIR/pyvenv.cfg" 2>/dev/null; then
        echo "[WARN] Existing venv was created WITHOUT --system-site-packages."
        echo "[WARN] This causes the camera to silently fall back to a test pattern"
        echo "[WARN] because picamera2 (apt-installed) cannot be imported."
        echo "[WARN] Rebuilding venv at: $VENV_DIR"
        rm -rf "$VENV_DIR"
        $PYTHON -m venv --system-site-packages "$VENV_DIR"
        echo "[OK] Venv rebuilt with --system-site-packages"
    fi
else
    echo "Creating virtual environment (with --system-site-packages for picamera2)..."
    $PYTHON -m venv --system-site-packages "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

# ── Sanity check: picamera2 importable from venv? ──
if ! python -c "from picamera2 import Picamera2" 2>/dev/null; then
    echo "[WARN] picamera2 not importable from venv -- camera will fall back to test pattern."
    echo "[WARN] Run: sudo apt install python3-picamera2 python3-libcamera"
    echo "[WARN] (continuing anyway -- this is OK if no camera is attached)"
fi

# ── Install Python dependencies ──
echo "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q

# ── Create log directory ──
sudo mkdir -p /var/log/ugv
sudo chown "$USER:$USER" /var/log/ugv

# ── Copy config if needed ──
CONFIG_FILE="$SCRIPT_DIR/config/config.yaml"
EXAMPLE_FILE="$SCRIPT_DIR/config/config.yaml.example"
if [[ ! -f "$CONFIG_FILE" && -f "$EXAMPLE_FILE" ]]; then
    cp "$EXAMPLE_FILE" "$CONFIG_FILE"
    echo "[INFO] Created config/config.yaml from example."
    echo "  Edit it: nano $CONFIG_FILE"
fi

# ── Detect install user and paths ──
INSTALL_USER="$(whoami)"
INSTALL_HOME="$(eval echo ~"$INSTALL_USER")"
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Install systemd service ──
echo "Installing systemd service..."

# Generate service file from template into temp file, then copy with sudo
# (avoids 'sed -i' permission issues on /etc/systemd/system/)
sed \
    -e "s|User=pi|User=$INSTALL_USER|g" \
    -e "s|Group=pi|Group=$INSTALL_USER|g" \
    -e "s|/home/pi/ugv-software|$INSTALL_DIR|g" \
    "$SCRIPT_DIR/ugv.service" > /tmp/ugv.service
sudo cp /tmp/ugv.service /etc/systemd/system/ugv.service
rm /tmp/ugv.service

# Generate monitor service file from template
sed \
    -e "s|User=pi|User=$INSTALL_USER|g" \
    -e "s|Group=pi|Group=$INSTALL_USER|g" \
    -e "s|/home/pi/ugv-software|$INSTALL_DIR|g" \
    "$SCRIPT_DIR/ugv-monitor.service" > /tmp/ugv-monitor.service
sudo cp /tmp/ugv-monitor.service /etc/systemd/system/ugv-monitor.service
rm /tmp/ugv-monitor.service

sudo systemctl daemon-reload
sudo systemctl enable ugv.service
sudo systemctl enable ugv-monitor.service
echo "[OK] systemd services installed and enabled (start on boot)"
echo "  Manual control (main):"
echo "    sudo systemctl start ugv"
echo "    sudo systemctl stop ugv"
echo "    sudo systemctl status ugv"
echo "    journalctl -u ugv -f"
echo "  Manual control (monitor):"
echo "    sudo systemctl start ugv-monitor"
echo "    sudo systemctl stop ugv-monitor"
echo "    sudo systemctl status ugv-monitor"
echo "    journalctl -u ugv-monitor -f"

# ── Tailscale bootstrap ──
echo ""
echo "--------------------------------------------"
echo "  Tailscale bootstrap"
echo "--------------------------------------------"

if command -v tailscale &>/dev/null; then
    echo "[OK] Tailscale already installed: $(tailscale version 2>/dev/null | head -1)"
else
    echo "Installing Tailscale via official script..."
    if curl -fsSL https://tailscale.com/install.sh | sh; then
        echo "[OK] Tailscale installed"
    else
        echo "[WARN] Tailscale install failed (no internet, arch mismatch, etc.)"
        echo "[WARN] You can retry later with: curl -fsSL https://tailscale.com/install.sh | sh"
        echo "[WARN] Continuing setup without Tailscale — UGV will still work over direct MQTT."
    fi
fi

# Only proceed with auth if tailscale binary is now available
if command -v tailscale &>/dev/null; then
    BACKEND_STATE=$(sudo tailscale status --json 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('BackendState','Unknown'))" \
        2>/dev/null || echo "Unknown")

    case "$BACKEND_STATE" in
        Running)
            SELF_IP=$(sudo tailscale status --json 2>/dev/null \
                | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('TailscaleIPs',[''])[0])" \
                2>/dev/null || echo "unknown")
            echo "[OK] Tailscale already authenticated — tailnet IP: $SELF_IP"
            ;;
        NeedsLogin|Stopped|NoState)
            if [[ -n "${TAILSCALE_AUTHKEY:-}" ]]; then
                echo "Authenticating Tailscale with provided auth key..."
                if sudo tailscale up --authkey="$TAILSCALE_AUTHKEY" --hostname="$(hostname)"; then
                    echo "[OK] Tailscale authenticated via TAILSCALE_AUTHKEY"
                else
                    echo "[WARN] tailscale up failed — retry manually with 'sudo tailscale up'"
                fi
            else
                echo ""
                echo "════════════════════════════════════════════════════════════════"
                echo "  TAILSCALE FIRST-TIME LOGIN REQUIRED"
                echo "════════════════════════════════════════════════════════════════"
                echo "The next command will print a URL. Open it in your browser on"
                echo "any device, log in to your Tailscale account, and this Pi will"
                echo "be added to your tailnet. The session persists across reboots."
                echo ""
                read -rp "Press Enter to continue (or Ctrl-C to skip Tailscale setup)..."
                sudo tailscale up --hostname="$(hostname)" || {
                    echo "[WARN] tailscale up failed — you can retry manually with 'sudo tailscale up'"
                }
            fi
            ;;
        *)
            echo "[WARN] Tailscale BackendState is '$BACKEND_STATE' — skipping auth."
            echo "[WARN] Check status manually with: sudo tailscale status"
            ;;
    esac
fi

# ── Done ──
echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "  Next steps:"
echo "    1. Edit config:  nano $SCRIPT_DIR/config/config.yaml"
echo "    2. Test run:      bash $SCRIPT_DIR/run.sh"
echo "    3. Reboot to start automatically: sudo reboot"
echo ""
