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

# ── Tailscale MTU udev rule for WebRTC direct-path acceleration ──
# Tailscale defaults tailscale0's MTU to a conservative 1280 bytes. With
# IPv6 (40) + UDP (8) + RTP (12) + SRTP auth tag (16) = 76 bytes of header
# overhead, that leaves only 1204 bytes for the VP8 payload — dangerously
# close to aiortc's default ~1200-byte RTP packet size. In practice, some
# keyframe packets exceed the threshold and are silently dropped on the
# direct tailnet candidate pair, the RCS browser never assembles a
# complete frame, and the operator UI shows "STREAMING" with frozen video.
#
# Fix: install a udev rule that bumps tailscale0's MTU to 1400 every time
# the interface is created. That gives 1400 - 76 = 1324 bytes of payload
# room, with ~124 bytes of headroom. The outer WireGuard encapsulation
# still fits comfortably inside a standard 1500-byte Ethernet MTU on the
# underlying network.
#
# udev runs the rule as root on interface-add events, so there is no
# runtime sudo, no sudoers rule, and no manual re-apply after reboot or
# tailscale up/down. Idempotent and persistent.
#
# This rule has to be installed on BOTH ends of the tailnet (RCS PC and
# UGV Pi) because MTU is a per-interface property — if either side
# advertises 1280, the effective path MTU is 1280. RCS-Software/setup.sh
# installs the same rule on the operator PC side.

UDEV_RULE_FILE="/etc/udev/rules.d/99-tailscale-mtu.rules"
UDEV_RULE_CONTENT='# Managed by RBPi_UGV/setup.sh — do not edit by hand.
# Bump tailscale0 MTU from Tailscale'"'"'s default 1280 to 1400 so WebRTC
# SRTP packets fit on the direct tailnet candidate pair with headroom
# for IPv6 + UDP + RTP + SRTP headers. Required for Option B
# (tailnet-direct WebRTC acceleration) in the UGV camera path.
ACTION=="add", SUBSYSTEM=="net", KERNEL=="tailscale0", RUN+="/sbin/ip link set dev %k mtu 1400"
'

if command -v udevadm &>/dev/null; then
    if [[ -f "$UDEV_RULE_FILE" ]] && \
       diff -q <(printf '%s' "$UDEV_RULE_CONTENT") "$UDEV_RULE_FILE" >/dev/null 2>&1; then
        echo "[OK] Tailscale MTU udev rule already installed at $UDEV_RULE_FILE"
    else
        echo "[INFO] Installing Tailscale MTU udev rule at $UDEV_RULE_FILE..."
        printf '%s' "$UDEV_RULE_CONTENT" | sudo tee "$UDEV_RULE_FILE" >/dev/null
        sudo udevadm control --reload-rules 2>/dev/null || true
        # If tailscale0 is already up, apply the MTU now
        if ip link show tailscale0 >/dev/null 2>&1; then
            CURRENT_MTU=$(ip -json link show tailscale0 | python3 -c "import sys,json; print(json.load(sys.stdin)[0].get('mtu','?'))" 2>/dev/null || echo "?")
            if [[ "$CURRENT_MTU" != "1400" ]]; then
                echo "[INFO] tailscale0 is already up (current MTU: $CURRENT_MTU) — applying new MTU immediately..."
                sudo ip link set dev tailscale0 mtu 1400 || {
                    echo "[WARN] Could not set MTU on tailscale0 — the udev rule will apply on the next tailscale restart."
                }
            fi
        fi
        echo "[OK] Tailscale MTU udev rule installed."
    fi
else
    echo "[WARN] udevadm not found — skipping Tailscale MTU udev rule install."
    echo "[WARN] You may need to manually run 'sudo ip link set dev tailscale0 mtu 1400'"
    echo "[WARN] after each tailscale up for the camera to work on the direct tailnet path."
fi

# ─────────────────────────────────────────────────────────────────────────
# LOCAL MODE — ROVER SIDE (spec §6.4)
# ─────────────────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────────"
echo "[ LOCAL MODE — ROVER SIDE ]"
echo "─────────────────────────────────────────────────────"

read -rp "[?] Enable Local Mode (LAN-direct transport)? [Y/n]: " answer
answer="${answer:-Y}"
if [[ ! "$answer" =~ ^[Yy]$ ]]; then
    echo "[*] Skipping Local Mode setup."
else
    read -rp "[?] mDNS hostname for this rover [ugv-rover-01]: " mdns_hostname
    mdns_hostname="${mdns_hostname:-ugv-rover-01}"

    echo "[*] Installing apt packages: mosquitto avahi-daemon avahi-utils"
    sudo apt update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt install -y \
        mosquitto avahi-daemon avahi-utils python3-aiohttp

    if [[ -f /etc/mosquitto/certs/server.crt ]]; then
        read -rp "[?] Existing Mosquitto certs found. Regenerate? [y/N]: " r
        regen="${r:-N}"
    else
        regen="y"
    fi
    if [[ "$regen" =~ ^[Yy]$ ]]; then
        echo "[*] Generating self-signed CA + server cert (5-year validity)"
        sudo mkdir -p /etc/mosquitto/certs
        local_ip=$(hostname -I | awk '{print $1}')
        sudo openssl req -x509 -nodes -new -newkey rsa:4096 -days 1825 \
            -subj "/CN=ugv-local-ca" \
            -keyout /etc/mosquitto/certs/ca.key \
            -out /etc/mosquitto/certs/ca.crt
        sudo openssl req -new -nodes -newkey rsa:2048 \
            -subj "/CN=${mdns_hostname}" \
            -keyout /etc/mosquitto/certs/server.key \
            -out /etc/mosquitto/certs/server.csr
        sudo bash -c "cat > /tmp/server-ext.cnf" <<EXTEOF
subjectAltName = DNS:${mdns_hostname}.local,DNS:${mdns_hostname},IP:${local_ip},IP:127.0.0.1
EXTEOF
        sudo openssl x509 -req -in /etc/mosquitto/certs/server.csr \
            -CA /etc/mosquitto/certs/ca.crt -CAkey /etc/mosquitto/certs/ca.key \
            -CAcreateserial -days 1825 \
            -extfile /tmp/server-ext.cnf \
            -out /etc/mosquitto/certs/server.crt
        sudo chown mosquitto:mosquitto /etc/mosquitto/certs/*
        sudo chmod 644 /etc/mosquitto/certs/*.crt
        sudo chmod 600 /etc/mosquitto/certs/*.key
        rm /tmp/server-ext.cnf
        echo "    [OK] certs generated"
    fi

    if [[ -f /etc/mosquitto/passwd ]]; then
        read -rp "[?] Existing passwd file. Regenerate? [y/N]: " r
        regen_pw="${r:-N}"
    else
        regen_pw="y"
    fi
    if [[ "$regen_pw" =~ ^[Yy]$ ]]; then
        read -srp "    Enter password for rcs_operator: " p1; echo
        read -srp "    Enter password for ugv_client: " p2; echo
        sudo bash -c "echo '' > /etc/mosquitto/passwd"
        sudo mosquitto_passwd -b /etc/mosquitto/passwd rcs_operator "$p1"
        sudo mosquitto_passwd -b /etc/mosquitto/passwd ugv_client "$p2"
        sudo chmod 600 /etc/mosquitto/passwd
        sudo chown mosquitto:mosquitto /etc/mosquitto/passwd
        echo "    [OK] passwd written"
    fi

    sudo bash -c "cat > /etc/mosquitto/conf.d/rcs-local.conf" <<EOF
# Generated by RBPi_UGV/setup.sh — Local Mode (spec §6.4)
listener 8883
cafile /etc/mosquitto/certs/ca.crt
certfile /etc/mosquitto/certs/server.crt
keyfile /etc/mosquitto/certs/server.key
tls_version tlsv1.2
require_certificate false
allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl
max_connections 10
message_size_limit 4096
EOF

    sudo bash -c "cat > /etc/mosquitto/acl" <<'EOF'
user rcs_operator
topic write ugv/joystick
topic write ugv/heartbeat
topic write ugv/ping
topic read  ugv/telemetry
topic read  ugv/pong
topic write ugv/camera/cmd
topic read  ugv/camera/offer
topic write ugv/camera/answer
topic read  ugv/camera/ice/ugv
topic write ugv/camera/ice/rcs
topic read  ugv/camera/status

user ugv_client
topic read  ugv/joystick
topic read  ugv/heartbeat
topic read  ugv/ping
topic write ugv/telemetry
topic write ugv/pong
topic read  ugv/camera/cmd
topic write ugv/camera/offer
topic read  ugv/camera/answer
topic write ugv/camera/ice/ugv
topic read  ugv/camera/ice/rcs
topic write ugv/camera/status

pattern read $SYS/#
EOF

    sudo bash -c "cat > /etc/avahi/services/ugv.service" <<EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">${mdns_hostname}</name>
  <service>
    <type>_ugv._tcp</type>
    <port>8883</port>
    <txt-record>spec=local-mode/v1</txt-record>
  </service>
</service-group>
EOF

    sudo systemctl enable --now mosquitto avahi-daemon
    sudo systemctl restart mosquitto avahi-daemon
    sudo systemctl is-active mosquitto >/dev/null && echo "    [OK] mosquitto active"
    sudo systemctl is-active avahi-daemon >/dev/null && echo "    [OK] avahi-daemon active"

    bundle_dir=$(mktemp -d)
    sudo cp /etc/mosquitto/certs/ca.crt "$bundle_dir/"
    sudo chown "$USER:$USER" "$bundle_dir/ca.crt"
    cat > "$bundle_dir/manifest.yaml" <<EOF
version: 1
generated_at: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
generated_by: "${mdns_hostname}"
expires_at: "$(date -u -d '+5 years' +%Y-%m-%dT%H:%M:%SZ)"
mqtt:
  hostname: "${mdns_hostname}.local"
  port: 8883
mjpeg:
  endpoint: "/stream.mjpg"
  port: 8443
  auth_username: "rcs_operator"
mdns:
  service_type: "_ugv._tcp"
EOF
    bundle_path="${HOME}/local-mode-bundle.tar.gz"
    tar -czf "$bundle_path" -C "$(dirname "$bundle_dir")" "$(basename "$bundle_dir")"
    rm -rf "$bundle_dir"

    if grep -q "^local_mode:" "${SCRIPT_DIR}/config/default_config.yaml"; then
        sed -i 's/^  enabled: false$/  enabled: true/' \
            "${SCRIPT_DIR}/config/default_config.yaml"
    fi

    echo ""
    echo "─────────────────────────────────────────────────────"
    echo "SUMMARY"
    echo "─────────────────────────────────────────────────────"
    echo "  Local broker:  mqtts://${local_ip}:8883"
    echo "  MJPEG server:  https://${local_ip}:8443/stream.mjpg"
    echo "  mDNS hostname: ${mdns_hostname}.local"
    echo "  Bundle:        ${bundle_path}"
    echo ""
    echo "  Next step: copy the bundle to your operator PC and run RCS setup.sh"
    echo "─────────────────────────────────────────────────────"
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
