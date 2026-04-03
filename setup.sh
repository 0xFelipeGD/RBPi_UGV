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
VENV_DIR="$SCRIPT_DIR/venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

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
