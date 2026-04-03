# UGV On-Board Software — Phase 3

Raspberry Pi headless daemon for a teleoperated UGV (Unmanned Ground Vehicle). Receives MQTT commands from the operator's ground station, drives motors, reads sensors, and publishes telemetry.

## Architecture

```
[Operator PC — Phase 1]
  |  MQTT: ugv/joystick (30-50 Hz), ugv/heartbeat (1 Hz), ugv/ping (0.5 Hz)
  v
[VPS — Mosquitto Broker — Phase 2]
  |
  v
[Raspberry Pi — THIS SOFTWARE]
  |-- MqttBridgeNode   : MQTT client, receives commands, sends telemetry
  |-- DriveNode         : Joystick -> motor mixing -> hardware output
  |-- SafetyNode        : Heartbeat watchdog, E-stop relay, fault detection
  |-- SensorNode        : Battery ADC, temperature probes, GPS serial
  |-- TelemetryNode     : Packages sensor data, publishes at 2 Hz
  |-- StateManager      : Thread-safe central state store
  |-- MessageBus        : Internal pub/sub (ROS2-inspired)
  |-- Launcher          : Node lifecycle manager
```

## Requirements

- Raspberry Pi 4B or 5
- Raspberry Pi OS Bookworm (64-bit)
- Python 3.11+
- Network connection to MQTT broker (Phase 2)

## Quick Start

```bash
# 1. Clone the repo on your Raspberry Pi
git clone <repo-url> ~/ugv-software
cd ~/ugv-software

# 2. Run the setup wizard (installs everything)
bash setup.sh

# 3. Edit your configuration
nano config/config.yaml
# Set: mqtt.host, mqtt.username, mqtt.password

# 4. Test run
bash run.sh

# 5. Reboot to start automatically
sudo reboot
```

## Configuration

All settings are in `config/config.yaml`. Only override what you need — defaults are in `config/default_config.yaml`.

### Key Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `mqtt.host` | `localhost` | MQTT broker address |
| `mqtt.port` | `8883` | MQTT broker port (TLS) |
| `mqtt.username` | `""` | MQTT auth username |
| `mqtt.password` | `""` | MQTT auth password |
| `mqtt.tls.enabled` | `true` | Use TLS encryption |
| `drive.mode` | `"arcade"` | Drive mode: `"arcade"` or `"tank"` |
| `drive.backend` | `"gpio_pwm"` | Motor backend: `"gpio_pwm"`, `"pca9685"`, `"serial_plc"` |
| `safety.heartbeat_timeout` | `3.0` | Seconds before E-stop (min: 1.0) |
| `safety.estop_pin` | `25` | BCM pin for E-stop relay |
| `sensors.battery.enabled` | `true` | Enable battery ADC |
| `sensors.temperature.enabled` | `true` | Enable temperature probes |
| `sensors.gps.enabled` | `false` | Enable GPS module |

## Hardware Wiring

### GPIO Pin Assignments (Default)

| BCM Pin | Function | Direction | Notes |
|---------|----------|-----------|-------|
| 18 | Left motor PWM | Output | Hardware PWM (PWM0) |
| 19 | Right motor PWM | Output | Hardware PWM (PWM1) |
| 23 | Left motor DIR | Output | HIGH = forward, LOW = reverse |
| 24 | Right motor DIR | Output | HIGH = forward, LOW = reverse |
| 25 | E-stop relay | Output | **Active LOW** (LOW = E-stop engaged) |

### I2C Devices

| Address | Device | Purpose |
|---------|--------|---------|
| 0x48 | ADS1115 | Battery voltage ADC |
| 0x40 | PCA9685 | Servo/ESC PWM driver (optional) |

### Serial Ports

| Port | Device | Purpose |
|------|--------|---------|
| `/dev/ttyAMA0` | GPS module | NMEA sentences |
| `/dev/ttyUSB0` | PLC | Motor control serial (optional) |

## Safety

**WARNING: This software controls real motors. Incorrect wiring or configuration can cause injury or equipment damage.**

- The E-stop relay uses **active LOW** convention: GPIO LOW = relay energized = motors CANNOT run
- This is **fail-safe**: if the Pi loses power or crashes, GPIO goes LOW and the E-stop engages
- The heartbeat watchdog **cannot be disabled** — minimum timeout is 1.0 second
- On startup, motors are locked until the first heartbeat is received from the operator
- On shutdown, the E-stop always engages
- If the operator connection is lost, motors ramp to zero within the configured timeout

## Service Management

```bash
# Start/stop/restart
sudo systemctl start ugv
sudo systemctl stop ugv
sudo systemctl restart ugv

# Check status
sudo systemctl status ugv

# View live logs
journalctl -u ugv -f

# Disable auto-start
sudo systemctl disable ugv
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `setup.sh` fails on Python | Install Python 3.11+: `sudo apt install python3.11` |
| MQTT won't connect | Check `config.yaml`: host, port, username, password, TLS settings |
| No motor output | Verify `drive.backend` matches your hardware, check pin numbers |
| E-stop stays engaged | Operator must send heartbeat — check Phase 1 is running and connected |
| Battery reads 0.0 | Check I2C: `i2cdetect -y 1`, verify ADS1115 at address 0x48 |
| Temperature reads 0.0 | Check 1-Wire: `ls /sys/bus/w1/devices/28-*` |
| GPS no fix | Ensure antenna has sky view, check serial: `cat /dev/ttyAMA0` |
| Service won't start | Check logs: `journalctl -u ugv -e`, verify venv exists |

## Testing

```bash
# Run all tests (from project root)
source venv/bin/activate
pytest tests/ -v
```

## License

Internal project — UGV teleoperation system.
