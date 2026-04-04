# RBPi_UGV — UGV Embedded Software

## Overview

Embedded control software running on a Raspberry Pi. Receives MQTT commands from the operator, drives motors via GPIO/PCA9685/Serial, reads sensors, and publishes telemetry.

## Architecture

ROS2-inspired node-based design (same pattern as RCS-Software):

- **MqttBridgeNode** — Paho MQTT client: subscribes control, publishes telemetry/pong, bridges camera signaling
- **CameraNode** — WebRTC video streaming: picamera2 capture → aiortc RTCPeerConnection (MQTT signaling)
- **DriveNode** — 50 Hz control loop: mixing → ramp limiting → motor backend
- **SafetyNode** — 10 Hz watchdog: heartbeat monitoring, E-stop GPIO control
- **SensorNode** — 20 Hz polling: battery (ADS1115), temperature (DS18B20), GPS (NMEA)
- **TelemetryNode** — 2 Hz publisher: aggregates sensor + safety data → MQTT
- **MessageBus** — Thread-safe internal pub/sub
- **StateManager** — Central thread-safe state store
- **Launcher** — Node lifecycle (startup order: Safety → MQTT → Camera → Sensor → Telemetry → Drive)

## MQTT Interface

**Always check `../INTERFACE_CONTRACT.md` before modifying any MQTT code.**

| Topic | Direction | QoS | Rate |
|-------|-----------|-----|------|
| `ugv/joystick` | Subscribe | 0 | 50 Hz |
| `ugv/heartbeat` | Subscribe | 0 | 1 Hz |
| `ugv/ping` | Subscribe | 0 | 0.5 Hz |
| `ugv/pong` | Publish | 0 | On-demand (immediate echo) |
| `ugv/telemetry` | Publish | 1 | 2 Hz |
| `ugv/camera/cmd` | Subscribe | 1 | On-demand |
| `ugv/camera/offer` | Publish | 1 | On-demand |
| `ugv/camera/answer` | Subscribe | 1 | On-demand |
| `ugv/camera/ice/ugv` | Publish | 1 | On-demand |
| `ugv/camera/ice/rcs` | Subscribe | 1 | On-demand |
| `ugv/camera/status` | Publish | 1 | On-demand |

## Payload Format

Compact JSON — see `mqtt/serializer.py`:
- Inbound joystick: `{"t", "sa", "ta", "sb", "tb", "sh", "th"}`
- Inbound heartbeat: `{"t"}`
- Inbound ping: `{"t", "seq"}` → immediate pong echo `{"t", "seq", "t_rx", "t_tx"}`
- Outbound telemetry: `{"speed", "bat_v", "bat_pct", "temp_l", "temp_r", "rssi", "lat", "lon", "armed", "hb_age"}`

## Safety / Watchdog

| Parameter | Value | Config key |
|-----------|-------|------------|
| Heartbeat expected interval | 1.0s (from RCS) | — |
| Watchdog check interval | 100ms (10 Hz) | — |
| Heartbeat timeout | 3.0s (min 1.0s) | `safety.heartbeat_timeout` |
| E-stop GPIO | BCM 25, active LOW | `safety.estop_pin` |
| Ramp-down on E-stop | 0.5s | `safety.ramp_down_time` |
| States | DISARMED → ARMED → ESTOP_ACTIVE | — |

## Drive Backends

| Backend | Config key | Interface |
|---------|-----------|-----------|
| GPIO PWM | `gpio_pwm` | BCM 18/19 (PWM), BCM 23/24 (direction) |
| PCA9685 | `pca9685` | I2C 0x40, RC ESC pulse 1000-2000us |
| Serial PLC | `serial_plc` | /dev/ttyUSB0, JSON protocol |

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point |
| `mqtt/mqtt_bridge.py` | MQTT client + pong echo |
| `mqtt/serializer.py` | Payload serialization/deserialization |
| `mqtt/topics.py` | Topic name constants |
| `safety/watchdog_node.py` | Watchdog thread + E-stop GPIO |
| `drive/drive_node.py` | 50 Hz control loop + mixing |
| `drive/mixer.py` | Arcade/tank mixing algorithms |
| `drive/backends/` | Motor output drivers |
| `sensors/sensor_node.py` | Sensor polling coordinator |
| `telemetry/telemetry_node.py` | Telemetry aggregation + publish |
| `camera/camera_node.py` | WebRTC peer connection + signaling lifecycle |
| `camera/pi_camera_track.py` | aiortc MediaStreamTrack: picamera2 capture / test pattern |
| `config/default_config.yaml` | Default configuration |
| `checkup.sh` | Preflight check script (run before setup.sh) |
| `setup.sh` | First-time setup: install deps, create venv, install service |
| `ugv.service` | Systemd unit file (main daemon) |
| `ugv-monitor.service` | Systemd unit file (monitor web UI) |

## Configuration

YAML-based: `config/default_config.yaml` (defaults) + `config/config.yaml` (user overrides, deep-merged).

Key settings: `mqtt.*`, `drive.mode`, `drive.backend`, `safety.heartbeat_timeout`, `sensors.*`, `telemetry.publish_rate_hz`, `camera.enabled`, `camera.resolution`, `camera.framerate`, `camera.stun_servers`

## Running

```bash
./checkup.sh                    # Preflight: verify Pi is ready (run before setup)
./setup.sh                      # First time: install deps, create venv, install service
./run.sh                        # Manual run
sudo systemctl start ugv        # Systemd service
python -m pytest tests/ -v      # Run tests
```

## Credentials

- MQTT user: `ugv_client`
- MQTT client_id: `ugv-onboard`

## Hardware Pins (default)

- GPIO 18/19: Motor PWM (left/right)
- GPIO 23/24: Motor direction (left/right)
- GPIO 25: E-stop relay (active LOW)
- I2C 0x48: ADS1115 battery ADC
- I2C 0x40: PCA9685 servo controller (optional)
- UART /dev/ttyAMA0: GPS (9600 baud)
- 1-Wire: DS18B20 temperature sensors
