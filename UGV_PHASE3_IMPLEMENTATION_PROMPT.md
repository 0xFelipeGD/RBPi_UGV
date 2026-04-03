# UGV Phase 3 — Raspberry Pi On-Board Software (Full Implementation Prompt)

> **Purpose**: This document is a self-contained, step-by-step implementation guide for **Phase 3** of the UGV system — the Raspberry Pi on-board software. A single AI (or team of agents) should be able to read ONLY this file and produce a fully working system on the first attempt.

---

## TABLE OF CONTENTS

1. [Project Context](#1-project-context)
2. [Multi-Agent Workflow](#2-multi-agent-workflow)
3. [Node-Based Architecture](#3-node-based-architecture)
4. [Final Directory Structure](#4-final-directory-structure)
5. [Module 1 — Core Framework](#5-module-1--core-framework)
6. [Module 2 — Configuration](#6-module-2--configuration)
7. [Module 3 — MQTT Bridge](#7-module-3--mqtt-bridge)
8. [Module 4 — Drive System](#8-module-4--drive-system)
9. [Module 5 — Safety & Watchdog](#9-module-5--safety--watchdog)
10. [Module 6 — Sensors](#10-module-6--sensors)
11. [Module 7 — Telemetry Publisher](#11-module-7--telemetry-publisher)
12. [Module 8 — Utilities](#12-module-8--utilities)
13. [Module 9 — Entry Point & Launcher](#13-module-9--entry-point--launcher)
14. [Module 10 — Setup & Installation](#14-module-10--setup--installation)
15. [Module 11 — Testing](#15-module-11--testing)
16. [Module 12 — Documentation](#16-module-12--documentation)
17. [MQTT Protocol Contract (Phase 1 ↔ Phase 3)](#17-mqtt-protocol-contract-phase-1--phase-3)
18. [Joystick Payload Reference](#18-joystick-payload-reference)
19. [Hardware Wiring Reference](#19-hardware-wiring-reference)
20. [Acceptance Criteria](#20-acceptance-criteria)

---

## 1. PROJECT CONTEXT

### 1.1 What This System Does

This is the **on-board Raspberry Pi software** for a teleoperated UGV (Unmanned Ground Vehicle). It runs on a Raspberry Pi mounted on the vehicle and does four things:

1. **Receives** joystick commands and heartbeat signals from the operator's ground station (RCS) via an MQTT broker
2. **Translates** joystick inputs into motor commands using configurable drive mixing (arcade or tank mode)
3. **Reads** vehicle sensors (battery voltage, motor temperatures, GPS) and publishes telemetry back to the operator
4. **Enforces** safety via a heartbeat watchdog — if the operator goes silent for more than N seconds, motors are ramped to zero and an E-stop relay is engaged

### 1.2 What Phase 3 Covers (YOUR SCOPE)

Phase 3 is ONLY the Raspberry Pi application. You are NOT building:

- The operator ground station GUI (Phase 1 — already built)
- The VPS/Mosquitto MQTT broker (Phase 2 — already deployed)
- The PLC ladder logic or motor controller firmware (Phase 4)
- Any video streaming (separate system)

### 1.3 System Architecture Diagram

```
[Operator PC — Phase 1 (BUILT)]
  |  Publishes: ugv/joystick (30-50 Hz), ugv/heartbeat (1 Hz), ugv/ping (0.5 Hz)
  |  Subscribes: ugv/telemetry, ugv/pong
  v
[VPS — Mosquitto Broker — Phase 2 (DEPLOYED)]
  |
  v  (inbound MQTT over TLS)
[Raspberry Pi UGV Software — THIS IS WHAT YOU BUILD]
  |-- MqttBridgeNode   : MQTT client, receives commands, sends telemetry
  |-- DriveNode         : Joystick → motor mixing → hardware output
  |-- SafetyNode        : Heartbeat watchdog, E-stop relay, fault detection
  |-- SensorNode        : Battery ADC, temperature probes, GPS serial
  |-- TelemetryNode     : Packages sensor data, publishes at 2 Hz
  |-- StateManager      : Thread-safe central state store
  |-- MessageBus        : Internal pub/sub (same pattern as Phase 1)
  |-- Launcher          : Node lifecycle manager
      |
      v  (hardware interfaces)
  [GPIO PWM / Serial]  →  Motor controller / PLC (Phase 4)
  [I2C / ADC]           →  Battery voltage, temperature sensors
  [Serial UART]         →  GPS module (NMEA)
  [GPIO Digital Out]    →  E-stop relay
```

### 1.4 Latency Budget (Control Path)

| Segment                           | Target     |
|-----------------------------------|------------|
| Broker → Pi MQTT receive          | 5-20 ms    |
| JSON parse + drive mixing         | < 1 ms     |
| PWM/Serial output to motors       | < 2 ms     |
| **Total Pi-side processing**      | **< 5 ms** |

The Pi must contribute MINIMAL latency. Commands arrive at 30-50 Hz and must reach the motor controller within one frame.

### 1.5 Non-Negotiable Constraints

- Python 3.11+ (Raspberry Pi OS Bookworm ships 3.11)
- `paho-mqtt` for MQTT (must match Phase 1 protocol exactly)
- `RPi.GPIO` or `lgpio` / `gpiozero` for GPIO PWM and digital output
- `smbus2` or `adafruit-circuitpython-ads1x15` for I2C ADC
- `pyserial` for GPS NMEA and optional PLC serial
- No GUI — this is a headless daemon
- All config externalized (YAML)
- Must run on Raspberry Pi 4B / 5 with Raspberry Pi OS Bookworm (64-bit)
- Must auto-start on boot via systemd
- Clone → setup.sh → run.sh → works
- Safety watchdog is **non-optional** — if heartbeat times out, motors MUST stop

---

## 2. MULTI-AGENT WORKFLOW

The implementation MUST follow this agent pattern:

### Agent Roles

| Agent     | Role                                          |
|-----------|-----------------------------------------------|
| PLANNER   | Reads this prompt, validates understanding, plans execution order |
| BUILDER   | Implements code module-by-module per the checklists below |
| REVIEWER  | After each module: checks code quality, types, correctness, integration |

### Execution Flow

```
PLANNER -> BUILDER -> REVIEWER
               ^          |
               +----------+ (iterate if REVIEWER finds issues)
```

### Rules

- PLANNER goes first: confirm understanding of each module before BUILDER starts
- BUILDER implements ONE module at a time, in the order listed (Module 1 through 12)
- REVIEWER validates after each module: type hints present, no syntax errors, interfaces match
- If REVIEWER flags an issue, BUILDER fixes it before moving to the next module
- Do NOT skip modules or reorder them — dependencies flow downward

---

## 3. NODE-BASED ARCHITECTURE

This project uses the same **ROS2-inspired** internal architecture as Phase 1. This does NOT mean using ROS2 itself. It means applying ROS2 design patterns in pure Python.

### 3.1 Core Concepts

| ROS2 Concept  | Our Implementation          | Purpose                                       |
|---------------|-----------------------------|-----------------------------------------------|
| **Node**      | `BaseNode` class            | Independent component with lifecycle           |
| **Topic**     | `MessageBus` channels       | Named pub/sub channels for internal messages   |
| **Message**   | `dataclass` types           | Typed data structures for inter-node comms     |
| **Lifecycle** | `NodeState` enum            | CREATED → CONFIGURED → ACTIVE → SHUTDOWN       |
| **Launch**    | `Launcher` class            | Starts, monitors, and shuts down all nodes     |
| **Parameter** | YAML config                 | Runtime configuration loaded at startup        |

### 3.2 Node Lifecycle

```
CREATED  -->  on_configure()  -->  CONFIGURED  -->  on_activate()  -->  ACTIVE
                                                                          |
                                                                     on_shutdown()
                                                                          |
                                                                          v
                                                                      SHUTDOWN
```

- `on_configure()`: Load config, validate parameters, create resources (but don't start)
- `on_activate()`: Start threads/timers, begin processing
- `on_shutdown()`: Stop threads/timers, release resources, engage E-stop

### 3.3 Internal Message Bus Topics

| Topic                 | Message Type       | Producer       | Consumer(s)                | Rate          |
|-----------------------|--------------------|----------------|----------------------------|---------------|
| `command.joystick`    | JoystickCommand    | MqttBridgeNode | DriveNode, SafetyNode      | 30-50 Hz      |
| `command.heartbeat`   | Heartbeat          | MqttBridgeNode | SafetyNode                 | 1 Hz          |
| `command.ping`        | PingRequest        | MqttBridgeNode | MqttBridgeNode (echo)      | 0.5 Hz        |
| `drive.output`        | DriveOutput        | DriveNode      | StateManager               | 30-50 Hz      |
| `safety.status`       | SafetyStatus       | SafetyNode     | DriveNode, TelemetryNode   | On change     |
| `sensor.battery`      | BatteryReading     | SensorNode     | StateManager, TelemetryNode| 2 Hz          |
| `sensor.temperature`  | TemperatureReading | SensorNode     | StateManager, TelemetryNode| 1 Hz          |
| `sensor.gps`          | GpsReading         | SensorNode     | StateManager, TelemetryNode| 1 Hz          |
| `telemetry.outbound`  | TelemetryPayload   | TelemetryNode  | MqttBridgeNode             | 2 Hz          |

### 3.4 Node Data Flow

```
MQTT Broker
    │
    ▼
MqttBridgeNode ──publishes──► "command.joystick"  ──► DriveNode ──► Motor PWM/Serial
    │                          "command.heartbeat" ──► SafetyNode ──► E-stop relay
    │                          "command.ping"       ──► (echo pong)
    │
    │◄─────────subscribes────── "telemetry.outbound" ◄── TelemetryNode
    │                                                        ▲
    │                                                        │
SensorNode ──publishes──► "sensor.battery"   ────────────────┘
                          "sensor.temperature"
                          "sensor.gps"
```

---

## 4. FINAL DIRECTORY STRUCTURE

```
ugv-software/
├── main.py                           # Entry point (headless daemon)
├── requirements.txt                  # Python dependencies
├── setup.sh                          # One-command Raspberry Pi installer
├── run.sh                            # One-command launcher
├── ugv.service                       # systemd unit file for auto-start
├── config/
│   ├── __init__.py
│   ├── default_config.yaml           # Default configuration (ships with code)
│   ├── config.yaml.example           # Copy-and-edit template
│   └── config_loader.py              # YAML config loader + deep merge
├── core/
│   ├── __init__.py
│   ├── node.py                       # BaseNode class with lifecycle
│   ├── message_bus.py                # Internal pub/sub message bus
│   ├── messages.py                   # Message dataclass definitions
│   ├── state_manager.py              # Thread-safe central state
│   └── launcher.py                   # Node lifecycle manager
├── mqtt/
│   ├── __init__.py
│   ├── mqtt_bridge.py                # MqttBridgeNode: MQTT ↔ internal bus
│   ├── serializer.py                 # JSON parse/format for MQTT payloads
│   └── topics.py                     # MQTT topic string constants
├── drive/
│   ├── __init__.py
│   ├── drive_node.py                 # DriveNode: joystick → motor commands
│   ├── mixer.py                      # Arcade/tank mixing algorithms
│   └── backends/
│       ├── __init__.py
│       ├── base.py                   # Abstract motor backend interface
│       ├── gpio_pwm.py               # RPi.GPIO hardware PWM output
│       ├── pca9685.py                # I2C PCA9685 servo/ESC driver
│       └── serial_plc.py             # Serial UART to PLC (JSON or Modbus)
├── safety/
│   ├── __init__.py
│   └── watchdog_node.py              # SafetyNode: heartbeat watchdog + E-stop
├── sensors/
│   ├── __init__.py
│   ├── sensor_node.py                # SensorNode: polls all sensors
│   ├── battery.py                    # ADC battery voltage reader
│   ├── temperature.py                # DS18B20 / thermistor reader
│   └── gps.py                        # Serial NMEA GPS parser
├── telemetry/
│   ├── __init__.py
│   └── telemetry_node.py             # TelemetryNode: packages + publishes
├── utils/
│   ├── __init__.py
│   ├── logging_setup.py              # Structured logging configuration
│   └── timing.py                     # RateTracker, LatencyTimer
└── tests/
    ├── __init__.py
    ├── test_message_bus.py
    ├── test_serializer.py
    ├── test_mixer.py
    ├── test_watchdog.py
    └── test_config_loader.py
```

---

## 5. MODULE 1 — CORE FRAMEWORK

> **Depends on**: nothing
> **Build this FIRST** — all other modules depend on it.

### 5.1 `core/node.py` — BaseNode

Identical pattern to Phase 1. Copy the design exactly:

```python
from abc import ABC, abstractmethod
from enum import Enum, auto
import logging

class NodeState(Enum):
    CREATED = auto()
    CONFIGURED = auto()
    ACTIVE = auto()
    SHUTDOWN = auto()

class BaseNode(ABC):
    def __init__(self, name: str, bus: "MessageBus", config: dict):
        self.name = name
        self.bus = bus
        self.config = config
        self.state = NodeState.CREATED
        self.logger = logging.getLogger(f"ugv.{name}")

    def configure(self) -> None:
        assert self.state == NodeState.CREATED
        self.on_configure()
        self.state = NodeState.CONFIGURED

    def activate(self) -> None:
        assert self.state == NodeState.CONFIGURED
        self.on_activate()
        self.state = NodeState.ACTIVE

    def shutdown(self) -> None:
        if self.state != NodeState.SHUTDOWN:
            self.on_shutdown()
            self.state = NodeState.SHUTDOWN

    @abstractmethod
    def on_configure(self) -> None: ...

    @abstractmethod
    def on_activate(self) -> None: ...

    @abstractmethod
    def on_shutdown(self) -> None: ...
```

### 5.2 `core/message_bus.py` — Internal Pub/Sub

Identical to Phase 1. Thread-safe pub/sub with error isolation:

```python
import threading
from typing import Any, Callable
from collections import defaultdict

class MessageBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        with self._lock:
            self._subscribers[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        with self._lock:
            self._subscribers[topic] = [
                cb for cb in self._subscribers[topic] if cb is not callback
            ]

    def publish(self, topic: str, message: Any) -> None:
        with self._lock:
            listeners = list(self._subscribers.get(topic, []))
        for cb in listeners:
            try:
                cb(message)
            except Exception as e:
                import logging
                logging.getLogger("ugv.bus").error(
                    f"Subscriber error on '{topic}': {e}"
                )
```

### 5.3 `core/messages.py` — Message Types

Define ALL internal message dataclasses:

```python
from dataclasses import dataclass, field
import time

@dataclass(frozen=True)
class JoystickCommand:
    """Parsed joystick state from MQTT. Published on 'command.joystick'."""
    timestamp: float                        # time.monotonic() when received
    remote_timestamp_ms: int                # Epoch ms from operator (the "t" field)
    stick_axes: dict[str, float]            # "0" → X, "1" → Y (bipolar -1..+1)
    throttle_axes: dict[str, float]         # "2" → left throttle, "5" → right (0..+1)
    stick_buttons: dict[str, bool]          # Only pressed keys present
    throttle_buttons: dict[str, bool]
    stick_hats: dict[str, list[int]]        # "H1" → [x, y]
    throttle_hats: dict[str, list[int]]

@dataclass(frozen=True)
class Heartbeat:
    """Operator heartbeat signal. Published on 'command.heartbeat'."""
    timestamp: float
    remote_timestamp_ms: int

@dataclass(frozen=True)
class PingRequest:
    """Latency ping from operator. Published on 'command.ping'."""
    timestamp: float
    remote_timestamp_ms: int
    seq: int

@dataclass(frozen=True)
class DriveOutput:
    """Motor output command. Published on 'drive.output'."""
    timestamp: float
    left_speed: float       # -1.0 (full reverse) to +1.0 (full forward)
    right_speed: float      # -1.0 (full reverse) to +1.0 (full forward)
    source: str             # "operator" or "safety_stop"

@dataclass(frozen=True)
class SafetyStatus:
    """Safety system state. Published on 'safety.status'."""
    timestamp: float
    armed: bool             # True = operator has control, False = E-stop active
    reason: str             # "ok", "heartbeat_timeout", "manual_estop", "fault"
    heartbeat_age_ms: float # Time since last heartbeat

@dataclass(frozen=True)
class BatteryReading:
    """Published on 'sensor.battery'."""
    timestamp: float
    voltage: float          # Raw voltage (e.g., 12.6V)
    percent: float          # Estimated SOC (0-100)
    current: float          # Amps (if current sensor available, else 0)

@dataclass(frozen=True)
class TemperatureReading:
    """Published on 'sensor.temperature'."""
    timestamp: float
    motor_left_c: float     # Celsius
    motor_right_c: float    # Celsius

@dataclass(frozen=True)
class GpsReading:
    """Published on 'sensor.gps'."""
    timestamp: float
    latitude: float         # Decimal degrees
    longitude: float        # Decimal degrees
    speed_mps: float        # Meters per second (from GPS)
    fix: bool               # True if GPS has a fix

@dataclass(frozen=True)
class TelemetryPayload:
    """Assembled telemetry for MQTT. Published on 'telemetry.outbound'."""
    timestamp: float
    speed: float
    battery_voltage: float
    battery_percent: float
    motor_temp_left: float
    motor_temp_right: float
    signal_strength: int
    gps_lat: float
    gps_lon: float
    custom: dict = field(default_factory=dict)
```

### 5.4 `core/state_manager.py` — Central State Store

```python
import threading
import time
from core.messages import (
    JoystickCommand, DriveOutput, SafetyStatus,
    BatteryReading, TemperatureReading, GpsReading,
)

class StateManager:
    """Thread-safe central state. Nodes publish updates; TelemetryNode reads snapshots."""
    def __init__(self, bus: "MessageBus"):
        self._lock = threading.RLock()
        self._bus = bus

        self.joystick: JoystickCommand | None = None
        self.drive: DriveOutput | None = None
        self.safety: SafetyStatus | None = None
        self.battery: BatteryReading | None = None
        self.temperature: TemperatureReading | None = None
        self.gps: GpsReading | None = None

        bus.subscribe("command.joystick", self._on_joystick)
        bus.subscribe("drive.output", self._on_drive)
        bus.subscribe("safety.status", self._on_safety)
        bus.subscribe("sensor.battery", self._on_battery)
        bus.subscribe("sensor.temperature", self._on_temperature)
        bus.subscribe("sensor.gps", self._on_gps)

    def _on_joystick(self, msg: JoystickCommand) -> None:
        with self._lock:
            self.joystick = msg

    def _on_drive(self, msg: DriveOutput) -> None:
        with self._lock:
            self.drive = msg

    def _on_safety(self, msg: SafetyStatus) -> None:
        with self._lock:
            self.safety = msg

    def _on_battery(self, msg: BatteryReading) -> None:
        with self._lock:
            self.battery = msg

    def _on_temperature(self, msg: TemperatureReading) -> None:
        with self._lock:
            self.temperature = msg

    def _on_gps(self, msg: GpsReading) -> None:
        with self._lock:
            self.gps = msg

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "joystick": self.joystick,
                "drive": self.drive,
                "safety": self.safety,
                "battery": self.battery,
                "temperature": self.temperature,
                "gps": self.gps,
            }
```

### 5.5 `core/launcher.py` — Node Lifecycle Manager

Same as Phase 1, but also registers a SIGTERM handler for clean systemd shutdown:

```python
import signal
import logging
from core.node import BaseNode

class Launcher:
    def __init__(self):
        self.nodes: list[BaseNode] = []
        self.logger = logging.getLogger("ugv.launcher")

    def register(self, node: BaseNode) -> None:
        self.nodes.append(node)

    def start_all(self) -> None:
        for node in self.nodes:
            self.logger.info(f"Configuring: {node.name}")
            node.configure()
        for node in self.nodes:
            self.logger.info(f"Activating: {node.name}")
            node.activate()

    def shutdown_all(self) -> None:
        for node in reversed(self.nodes):
            self.logger.info(f"Shutting down: {node.name}")
            try:
                node.shutdown()
            except Exception as e:
                self.logger.error(f"Error shutting down {node.name}: {e}")

    def setup_signal_handlers(self) -> None:
        def handler(signum, frame):
            self.logger.info(f"Signal {signum} received, shutting down...")
            self.shutdown_all()
            raise SystemExit(0)
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
```

### Module 1 Checklist

- [ ] `core/__init__.py` exists
- [ ] `core/node.py` — `NodeState` enum + `BaseNode` ABC with lifecycle
- [ ] `core/message_bus.py` — thread-safe pub/sub with error isolation
- [ ] `core/messages.py` — all 9 message dataclasses defined above
- [ ] `core/state_manager.py` — thread-safe state with bus subscriptions
- [ ] `core/launcher.py` — ordered start/stop, SIGINT + SIGTERM handlers
- [ ] All classes have type hints on every method signature
- [ ] All classes have docstrings

---

## 6. MODULE 2 — CONFIGURATION

> **Depends on**: Module 1 (for type references only)

### 6.1 `config/default_config.yaml`

```yaml
# UGV On-Board Software — Default Configuration
# Copy config.yaml.example to config.yaml and edit for your setup.

mqtt:
  host: "localhost"
  port: 8883
  username: ""
  password: ""
  tls:
    enabled: true
    ca_certs: ""
    certfile: ""
    keyfile: ""
  client_id: "ugv-onboard"
  keepalive: 30
  qos_control: 0
  qos_telemetry: 1

topics:
  joystick_control: "ugv/joystick"
  heartbeat: "ugv/heartbeat"
  telemetry: "ugv/telemetry"
  latency_ping: "ugv/ping"
  latency_pong: "ugv/pong"

drive:
  mode: "arcade"               # "arcade" or "tank"

  # Arcade mode mapping (stick axes)
  arcade:
    speed_axis: "1"            # Stick Y axis (evdev code as string)
    steer_axis: "0"            # Stick X axis (evdev code as string)
    speed_source: "stick"      # "stick" or "throttle"
    steer_source: "stick"      # "stick" or "throttle"
    max_speed: 1.0             # Scale factor (0.0-1.0)
    steer_sensitivity: 0.7     # Steering mix ratio (0.0-1.0)
    invert_speed: false        # True if pushing stick forward gives negative values
    invert_steer: false

  # Tank mode mapping (throttle axes)
  tank:
    left_axis: "2"             # Left throttle (evdev code as string)
    right_axis: "5"            # Right throttle (evdev code as string)
    left_source: "throttle"    # "stick" or "throttle"
    right_source: "throttle"
    invert_left: false
    invert_right: false

  # Motor output backend
  backend: "gpio_pwm"         # "gpio_pwm", "pca9685", or "serial_plc"

  # GPIO PWM backend config
  gpio_pwm:
    left_pin: 18               # BCM pin number for left motor PWM
    right_pin: 19              # BCM pin number for right motor PWM
    left_dir_pin: 23           # BCM pin for left motor direction (HIGH=forward)
    right_dir_pin: 24          # BCM pin for right motor direction
    frequency: 1000            # PWM frequency in Hz
    min_duty: 0                # Minimum duty cycle (0-100)
    max_duty: 100              # Maximum duty cycle (0-100)

  # PCA9685 I2C backend config
  pca9685:
    i2c_address: 0x40
    left_channel: 0
    right_channel: 1
    frequency: 50              # For standard servo ESCs
    min_pulse_us: 1000         # Full reverse
    center_pulse_us: 1500      # Neutral
    max_pulse_us: 2000         # Full forward

  # Serial PLC backend config
  serial_plc:
    port: "/dev/ttyUSB0"
    baudrate: 115200
    protocol: "json"           # "json" or "modbus"
    timeout: 0.1

  # Ramp rate limiter (safety: prevents instant full-speed jumps)
  ramp_rate: 2.0               # Max change per second (1.0 = 0→full in 1s)

safety:
  heartbeat_timeout: 3.0       # Seconds without heartbeat before E-stop
  estop_pin: 25                # BCM pin for E-stop relay (active LOW = engaged)
  startup_armed: false         # If false, waits for first heartbeat before arming
  ramp_down_time: 0.5          # Seconds to ramp motors to zero on E-stop

sensors:
  battery:
    enabled: true
    backend: "ads1115"         # "ads1115", "mcp3008", or "mock"
    i2c_address: 0x48          # ADS1115 I2C address
    channel: 0                 # ADC channel
    voltage_divider_ratio: 4.0 # R1+R2/R2 (e.g., 30K+10K = 4.0 for 0-16.8V → 0-4.2V)
    poll_interval: 0.5         # Seconds between reads
    cell_count: 4              # Battery cell count (for SOC estimation)
    cell_min_v: 3.0            # Minimum cell voltage (0% SOC)
    cell_max_v: 4.2            # Maximum cell voltage (100% SOC)

  temperature:
    enabled: true
    backend: "ds18b20"         # "ds18b20" (1-Wire) or "mock"
    left_sensor_id: ""         # 1-Wire ROM ID (auto-detect if empty)
    right_sensor_id: ""
    poll_interval: 1.0

  gps:
    enabled: false
    port: "/dev/ttyAMA0"       # UART port for GPS module
    baudrate: 9600
    poll_interval: 1.0

telemetry:
  publish_rate_hz: 2           # Telemetry publish rate to MQTT

logging:
  level: "INFO"
  file: "/var/log/ugv/ugv.log"
  console: true
  max_bytes: 5242880           # 5 MB log rotation
  backup_count: 3
```

### 6.2 `config/config.yaml.example`

```yaml
# UGV Configuration — COPY TO config.yaml AND EDIT
# Required: set mqtt.host, mqtt.username, mqtt.password

mqtt:
  host: "your-vps-ip-here"     # <-- CHANGE THIS
  port: 8883
  username: "ugv_user"          # <-- CHANGE THIS
  password: "your_password"     # <-- CHANGE THIS
  tls:
    enabled: true
    ca_certs: ""
  client_id: "ugv-onboard"

# Drive mode: "arcade" (stick Y=speed, X=steer) or "tank" (dual throttle)
drive:
  mode: "arcade"
  backend: "gpio_pwm"
  gpio_pwm:
    left_pin: 18
    right_pin: 19
    left_dir_pin: 23
    right_dir_pin: 24

safety:
  heartbeat_timeout: 3.0
  estop_pin: 25

sensors:
  battery:
    enabled: true
    voltage_divider_ratio: 4.0
    cell_count: 4
  temperature:
    enabled: true
  gps:
    enabled: false
```

### 6.3 `config/config_loader.py`

Same pattern as Phase 1 — `load_config()` with `deep_merge()`:

```python
import os
import yaml
import copy
import logging

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(CONFIG_DIR, "default_config.yaml")
USER_CONFIG_CANDIDATES = [
    os.path.join(CONFIG_DIR, "config.yaml"),
    os.path.join(CONFIG_DIR, "..", "config.yaml"),
]

def deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result

def load_config() -> dict:
    with open(DEFAULT_CONFIG, "r") as f:
        config = yaml.safe_load(f)
    for candidate in USER_CONFIG_CANDIDATES:
        if os.path.isfile(candidate):
            with open(candidate, "r") as f:
                user_cfg = yaml.safe_load(f) or {}
            config = deep_merge(config, user_cfg)
            logging.getLogger("ugv.config").info(f"Loaded user config: {candidate}")
            break
    return config
```

### Module 2 Checklist

- [ ] `config/__init__.py` exists
- [ ] `config/default_config.yaml` with ALL settings documented
- [ ] `config/config.yaml.example` with user instructions
- [ ] `config/config_loader.py` with `load_config()` and `deep_merge()`
- [ ] Loader does NOT crash if `config.yaml` is missing
- [ ] Deep merge so users only override specific keys

---

## 7. MODULE 3 — MQTT BRIDGE

> **Depends on**: Module 1, Module 2

### 7.1 `mqtt/topics.py`

```python
DEFAULT_TOPICS: dict[str, str] = {
    "joystick_control": "ugv/joystick",
    "heartbeat":        "ugv/heartbeat",
    "telemetry":        "ugv/telemetry",
    "latency_ping":     "ugv/ping",
    "latency_pong":     "ugv/pong",
}
```

### 7.2 `mqtt/serializer.py`

This is the MIRROR of Phase 1's serializer. Phase 1 serializes, Phase 3 deserializes (and vice versa for telemetry).

```python
import json
import time
from core.messages import JoystickCommand, Heartbeat, PingRequest, TelemetryPayload

def deserialize_joystick(payload: bytes) -> JoystickCommand:
    """
    Parse joystick JSON from Phase 1 into JoystickCommand.

    Phase 1 sends compact JSON with these keys:
      t  = epoch ms timestamp
      sa = stick axes        {str(evdev_code): float}
      ta = throttle axes     {str(evdev_code): float}
      sb = stick buttons     {str(evdev_code): true}   (only pressed)
      tb = throttle buttons  {str(evdev_code): true}   (only pressed)
      sh = stick hats        {hat_name: [x, y]}
      th = throttle hats     {hat_name: [x, y]}
    """
    data: dict = json.loads(payload)
    return JoystickCommand(
        timestamp=time.monotonic(),
        remote_timestamp_ms=int(data.get("t", 0)),
        stick_axes=data.get("sa", {}),
        throttle_axes=data.get("ta", {}),
        stick_buttons=data.get("sb", {}),
        throttle_buttons=data.get("tb", {}),
        stick_hats=data.get("sh", {}),
        throttle_hats=data.get("th", {}),
    )

def deserialize_heartbeat(payload: bytes) -> Heartbeat:
    """Parse heartbeat JSON: {"t": epoch_ms}"""
    data: dict = json.loads(payload)
    return Heartbeat(
        timestamp=time.monotonic(),
        remote_timestamp_ms=int(data.get("t", 0)),
    )

def deserialize_ping(payload: bytes) -> PingRequest:
    """Parse latency ping: {"t": epoch_ms, "seq": int}"""
    data: dict = json.loads(payload)
    return PingRequest(
        timestamp=time.monotonic(),
        remote_timestamp_ms=int(data.get("t", 0)),
        seq=int(data.get("seq", 0)),
    )

def serialize_pong(ping: PingRequest) -> bytes:
    """Echo ping back as pong — same t and seq for RTT calculation."""
    return json.dumps(
        {"t": ping.remote_timestamp_ms, "seq": ping.seq},
        separators=(",", ":"),
    ).encode("utf-8")

def serialize_telemetry(telem: TelemetryPayload) -> bytes:
    """
    Serialize telemetry to JSON for Phase 1.

    Phase 1 expects these keys:
      speed, bat_v, bat_pct, temp_l, temp_r, rssi, lat, lon
    Any extra keys in telem.custom are included as-is.
    """
    payload: dict = {
        "speed":  round(telem.speed, 2),
        "bat_v":  round(telem.battery_voltage, 2),
        "bat_pct": round(telem.battery_percent, 1),
        "temp_l": round(telem.motor_temp_left, 1),
        "temp_r": round(telem.motor_temp_right, 1),
        "rssi":   telem.signal_strength,
        "lat":    round(telem.gps_lat, 6),
        "lon":    round(telem.gps_lon, 6),
    }
    payload.update(telem.custom)
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")
```

### 7.3 `mqtt/mqtt_bridge.py` — MqttBridgeNode

```python
import ssl
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt

from core.node import BaseNode
from core.message_bus import MessageBus
from core.messages import TelemetryPayload
from mqtt.serializer import (
    deserialize_joystick, deserialize_heartbeat, deserialize_ping,
    serialize_pong, serialize_telemetry,
)
from mqtt.topics import DEFAULT_TOPICS
```

**Responsibilities:**

- [ ] `on_configure()`:
  - Read MQTT config (host, port, username, password, TLS, client_id, keepalive)
  - Read topic names from config
  - Create `paho.mqtt.client.Client` instance (MQTTv311, clean_session=True)
  - Configure TLS if enabled (same pattern as Phase 1)
  - Set paho callbacks: `on_connect`, `on_disconnect`, `on_message`

- [ ] `on_activate()`:
  - Connect to broker (non-blocking `connect_async` + `loop_start`)
  - Subscribe to internal bus topic `"telemetry.outbound"` to forward to MQTT

- [ ] `on_shutdown()`:
  - Stop paho loop
  - Disconnect

- [ ] `_on_mqtt_connect()`:
  - Subscribe to MQTT topics: `joystick_control` (QoS 0), `heartbeat` (QoS 0), `latency_ping` (QoS 0)
  - Log connection success

- [ ] `_on_mqtt_message()`:
  - If `joystick_control` topic → `deserialize_joystick()` → publish on `"command.joystick"`
  - If `heartbeat` topic → `deserialize_heartbeat()` → publish on `"command.heartbeat"`
  - If `latency_ping` topic → `deserialize_ping()` → publish on `"command.ping"` AND immediately publish pong response back to MQTT `latency_pong` topic

- [ ] `_on_telemetry_outbound()` (internal bus callback):
  - `serialize_telemetry()` → publish to MQTT `telemetry` topic with QoS 1

**CRITICAL: Pong echo must be immediate.** When a ping arrives on MQTT, deserialize it and publish the pong response back to MQTT in the same callback — do NOT route through the internal bus for the echo. This minimizes latency measurement error. You may still publish on the internal bus for logging/monitoring.

### Module 3 Checklist

- [ ] `mqtt/__init__.py` exists
- [ ] `mqtt/topics.py` — `DEFAULT_TOPICS` dict (same topics as Phase 1)
- [ ] `mqtt/serializer.py`:
  - [ ] `deserialize_joystick()` — parses Phase 1's compact JSON
  - [ ] `deserialize_heartbeat()` — parses `{"t": ms}`
  - [ ] `deserialize_ping()` — parses `{"t": ms, "seq": n}`
  - [ ] `serialize_pong()` — echoes ping back with same `t` and `seq`
  - [ ] `serialize_telemetry()` — formats telemetry with Phase 1's expected keys
- [ ] `mqtt/mqtt_bridge.py` — `MqttBridgeNode(BaseNode)`:
  - [ ] Subscribes to joystick, heartbeat, ping on MQTT
  - [ ] Routes to internal bus as typed messages
  - [ ] Echoes pings as pongs immediately
  - [ ] Forwards telemetry from internal bus to MQTT
  - [ ] Handles disconnection gracefully (paho auto-reconnect)
- [ ] All JSON key names match Phase 1 EXACTLY (see Section 17)

---

## 8. MODULE 4 — DRIVE SYSTEM

> **Depends on**: Module 1, Module 2, Module 3 (message types)

### 8.1 `drive/mixer.py` — Joystick-to-Motor Mixing

Two mixing modes. Both output `(left_speed, right_speed)` in range `[-1.0, +1.0]`:

```python
def arcade_mix(speed: float, steer: float, steer_sensitivity: float = 0.7) -> tuple[float, float]:
    """
    Arcade drive: one axis for speed, one for steering.

    speed: -1.0 (full reverse) to +1.0 (full forward)
    steer: -1.0 (full left) to +1.0 (full right)

    Returns (left_speed, right_speed) each in [-1.0, +1.0].
    """
    steer = steer * steer_sensitivity
    left = speed + steer
    right = speed - steer

    # Normalize if either exceeds ±1.0 (preserve ratio)
    max_val = max(abs(left), abs(right), 1.0)
    left /= max_val
    right /= max_val

    return (
        max(-1.0, min(1.0, left)),
        max(-1.0, min(1.0, right)),
    )

def tank_mix(left_throttle: float, right_throttle: float) -> tuple[float, float]:
    """
    Tank drive: independent left/right throttle control.

    Throttle values from Phase 1 are unipolar 0.0..+1.0.
    Convert to bipolar: 0.0 = full reverse, 0.5 = stop, 1.0 = full forward.

    Returns (left_speed, right_speed) each in [-1.0, +1.0].
    """
    left = (left_throttle * 2.0) - 1.0
    right = (right_throttle * 2.0) - 1.0
    return (
        max(-1.0, min(1.0, left)),
        max(-1.0, min(1.0, right)),
    )
```

- [ ] Both functions MUST be pure (no side effects) for easy unit testing
- [ ] Both functions MUST clamp output to `[-1.0, +1.0]`

### 8.2 `drive/backends/base.py` — Abstract Motor Backend

```python
from abc import ABC, abstractmethod

class MotorBackend(ABC):
    """Abstract interface for motor output hardware."""

    @abstractmethod
    def configure(self, config: dict) -> None:
        """Initialize hardware with given config section."""
        ...

    @abstractmethod
    def set_speeds(self, left: float, right: float) -> None:
        """
        Set motor speeds.
        left, right: -1.0 (full reverse) to +1.0 (full forward). 0.0 = stop.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Immediately stop all motors (emergency stop)."""
        ...

    @abstractmethod
    def cleanup(self) -> None:
        """Release hardware resources."""
        ...
```

### 8.3 `drive/backends/gpio_pwm.py` — RPi.GPIO PWM Backend

```python
import RPi.GPIO as GPIO
from drive.backends.base import MotorBackend
```

- [ ] `configure()`:
  - Set BCM mode
  - Setup PWM pins and direction pins from config
  - Start PWM at 0% duty cycle
- [ ] `set_speeds(left, right)`:
  - Set direction pins (HIGH = forward, LOW = reverse)
  - Set PWM duty cycle: `abs(speed) * (max_duty - min_duty) + min_duty`
  - Clamp duty to `[0, 100]`
- [ ] `stop()`: Set all PWM to 0%, all direction pins LOW
- [ ] `cleanup()`: `GPIO.cleanup()` for used pins

### 8.4 `drive/backends/pca9685.py` — I2C PCA9685 Backend

For use with standard RC ESCs that accept servo-style PWM signals:

- [ ] Use `adafruit-circuitpython-pca9685` or `adafruit-circuitpython-servokit`
- [ ] `configure()`: init I2C, set frequency (50 Hz for ESCs)
- [ ] `set_speeds(left, right)`: map `[-1.0, +1.0]` to `[min_pulse_us, max_pulse_us]` with center at neutral
- [ ] `stop()`: set both channels to `center_pulse_us` (neutral)
- [ ] `cleanup()`: de-init PCA9685

### 8.5 `drive/backends/serial_plc.py` — Serial/UART PLC Backend

For communicating with an external PLC (Phase 4):

- [ ] `configure()`: open serial port from config
- [ ] `set_speeds(left, right)`: send JSON command: `{"cmd":"drive","l":0.5,"r":0.5}\n`
- [ ] `stop()`: send `{"cmd":"stop"}\n`
- [ ] `cleanup()`: close serial port
- [ ] Configurable protocol: `"json"` (default) or `"modbus"` (future)

### 8.6 `drive/drive_node.py` — DriveNode

```python
import time
import threading
from core.node import BaseNode
from core.messages import JoystickCommand, DriveOutput, SafetyStatus
from drive.mixer import arcade_mix, tank_mix
from drive.backends.base import MotorBackend
```

**Responsibilities:**

- [ ] `on_configure()`:
  - Load drive config (mode, backend, ramp_rate, axis mappings)
  - Instantiate the correct `MotorBackend` based on `drive.backend` config
  - Call `backend.configure(config)`
  - Subscribe to `"command.joystick"` and `"safety.status"` on internal bus

- [ ] `on_activate()`:
  - Start a control loop thread (runs at ~50 Hz, matching input rate)

- [ ] `on_shutdown()`:
  - Stop control loop
  - Call `backend.stop()` then `backend.cleanup()`

- [ ] Control loop logic (runs on background thread):
  ```
  1. Read latest JoystickCommand from bus
  2. Check SafetyStatus — if not armed, output zero
  3. Extract axes based on config (arcade or tank mode)
  4. Run mixer → (left_raw, right_raw)
  5. Apply ramp rate limiter:
       delta = target - current
       max_step = ramp_rate * dt
       current += clamp(delta, -max_step, +max_step)
  6. Output to backend: backend.set_speeds(left, right)
  7. Publish DriveOutput on "drive.output"
  ```

- [ ] Ramp rate limiter is CRITICAL for safety:
  - Prevents instant jumps from 0 to full speed
  - `ramp_rate = 2.0` means 0→100% takes 0.5 seconds
  - Applied independently to left and right channels
  - On safety stop: use faster `ramp_down_time` from safety config

### Module 4 Checklist

- [ ] `drive/__init__.py` exists
- [ ] `drive/mixer.py` — `arcade_mix()` and `tank_mix()`, both pure and clamped
- [ ] `drive/backends/__init__.py` exists
- [ ] `drive/backends/base.py` — `MotorBackend` ABC
- [ ] `drive/backends/gpio_pwm.py` — RPi.GPIO implementation
- [ ] `drive/backends/pca9685.py` — I2C PCA9685 implementation
- [ ] `drive/backends/serial_plc.py` — Serial/UART implementation
- [ ] `drive/drive_node.py` — `DriveNode(BaseNode)`:
  - [ ] Configurable mixing mode (arcade/tank)
  - [ ] Configurable axis mappings
  - [ ] Ramp rate limiter on output
  - [ ] Respects SafetyStatus (zero output when not armed)
  - [ ] Publishes DriveOutput on internal bus
- [ ] Backend selection is config-driven (no hardcoded hardware)
- [ ] All backends implement stop() for emergency use

---

## 9. MODULE 5 — SAFETY & WATCHDOG

> **Depends on**: Module 1, Module 2
> **THIS IS THE MOST CRITICAL MODULE** — incorrect implementation can damage hardware or injure people.

### 9.1 `safety/watchdog_node.py` — SafetyNode

```python
import time
import threading
import logging

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False

from core.node import BaseNode
from core.messages import Heartbeat, SafetyStatus
```

**State machine:**

```
            ┌──────────────────┐
            │    DISARMED       │  (startup_armed=false: waiting for first heartbeat)
            │  Motors locked    │
            └────────┬─────────┘
                     │  First heartbeat received
                     v
            ┌──────────────────┐
            │     ARMED         │  (operator has control)
            │  Motors enabled   │◄──── heartbeat received (reset timer)
            └────────┬─────────┘
                     │  heartbeat_timeout exceeded
                     v
            ┌──────────────────┐
            │   ESTOP_ACTIVE    │  (motors stopped, relay engaged)
            │  Waiting for HB   │
            └────────┬─────────┘
                     │  heartbeat resumes
                     v
            ┌──────────────────┐
            │     ARMED         │  (auto-recovers when heartbeat resumes)
            └──────────────────┘
```

**Responsibilities:**

- [ ] `on_configure()`:
  - Read `safety.heartbeat_timeout`, `safety.estop_pin`, `safety.startup_armed`, `safety.ramp_down_time`
  - Setup E-stop GPIO pin (output, initially ENGAGED = active LOW)
  - Subscribe to `"command.heartbeat"` on internal bus

- [ ] `on_activate()`:
  - Start watchdog timer thread (checks heartbeat age every 100ms)
  - If `startup_armed` is True, arm immediately; otherwise stay disarmed until first heartbeat

- [ ] `on_shutdown()`:
  - Engage E-stop
  - Stop watchdog thread
  - Clean up GPIO

- [ ] Watchdog timer thread (100ms loop):
  ```
  every 100ms:
      age = time.monotonic() - last_heartbeat_time
      if state == ARMED and age > heartbeat_timeout:
          transition to ESTOP_ACTIVE
          engage E-stop relay (GPIO LOW)
          publish SafetyStatus(armed=False, reason="heartbeat_timeout")
      elif state == ESTOP_ACTIVE and age < heartbeat_timeout:
          transition to ARMED
          disengage E-stop relay (GPIO HIGH)
          publish SafetyStatus(armed=True, reason="ok")
  ```

- [ ] `_on_heartbeat()` callback:
  ```
  update last_heartbeat_time = time.monotonic()
  if state == DISARMED:
      transition to ARMED
      disengage E-stop relay
      publish SafetyStatus(armed=True, reason="ok")
  ```

- [ ] E-stop relay control:
  - **Active LOW** convention: GPIO LOW = relay energized = E-stop ENGAGED (motors cannot run)
  - **GPIO HIGH** = relay de-energized = E-stop RELEASED (motors can run)
  - This is **fail-safe**: if the Pi loses power or crashes, GPIO goes LOW → E-stop engages
  - If `_HAS_GPIO` is False (development machine), log warnings instead of GPIO calls

- [ ] Publish `SafetyStatus` on EVERY state transition

### Module 5 Checklist

- [ ] `safety/__init__.py` exists
- [ ] `safety/watchdog_node.py` — `SafetyNode(BaseNode)`:
  - [ ] Three states: DISARMED, ARMED, ESTOP_ACTIVE
  - [ ] Monitors heartbeat age at 10 Hz (100ms interval)
  - [ ] Transitions to E-stop when `heartbeat_timeout` exceeded
  - [ ] Auto-recovers to ARMED when heartbeat resumes
  - [ ] E-stop relay GPIO: active LOW (fail-safe)
  - [ ] Publishes SafetyStatus on every transition
  - [ ] Graceful degradation if RPi.GPIO unavailable (log-only mode)
- [ ] Safety system CANNOT be bypassed by config (heartbeat_timeout minimum = 1.0s)
- [ ] On node shutdown → E-stop engages

---

## 10. MODULE 6 — SENSORS

> **Depends on**: Module 1, Module 2

### 10.1 `sensors/battery.py` — Battery Voltage Reader

**ADS1115 (default) via I2C:**

```python
try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    _HAS_ADS = True
except ImportError:
    _HAS_ADS = False
```

- [ ] `BatteryReader` class:
  - `configure(config)`: init I2C, create ADS1115 object, create AnalogIn channel
  - `read() -> tuple[float, float]`: returns `(voltage, percent)`
    - Raw ADC voltage × `voltage_divider_ratio` = actual battery voltage
    - SOC estimation: `percent = (cell_voltage - cell_min_v) / (cell_max_v - cell_min_v) * 100`
    - Clamp percent to `[0, 100]`
  - If `_HAS_ADS` is False → return mock values and log warning

**MCP3008 alternative via SPI:**

- [ ] Support `backend: "mcp3008"` using `spidev` or `adafruit-circuitpython-mcp3xxx`
- [ ] Same interface: `configure()`, `read()` → `(voltage, percent)`

**Mock backend:**

- [ ] `backend: "mock"` returns configurable static values (for development without hardware)

### 10.2 `sensors/temperature.py` — Temperature Reader

**DS18B20 (1-Wire):**

- [ ] Read from `/sys/bus/w1/devices/<sensor_id>/temperature`
  - Value is in millidegrees Celsius (divide by 1000)
- [ ] Auto-detect sensor IDs if config values are empty:
  - Glob `/sys/bus/w1/devices/28-*/temperature`
  - Assign first found to left, second to right
- [ ] If no sensors found → return 0.0 and log warning

**Mock backend:**

- [ ] `backend: "mock"` returns configurable static values

### 10.3 `sensors/gps.py` — GPS NMEA Parser

```python
import serial
```

- [ ] `GpsReader` class:
  - `configure(config)`: open serial port
  - `read() -> GpsReading`: parse NMEA sentences
    - Look for `$GPRMC` or `$GNRMC` for lat, lon, speed, fix status
    - Parse fix status: 'A' = active fix, 'V' = void
    - Convert NMEA ddmm.mmmm to decimal degrees
    - Convert speed from knots to m/s
  - If serial not available → return empty GpsReading with `fix=False`

- [ ] NMEA parsing must handle:
  - Incomplete sentences (buffer across reads)
  - Checksum validation
  - Missing fields (GPS not yet locked)

### 10.4 `sensors/sensor_node.py` — SensorNode

```python
import time
import threading
from core.node import BaseNode
from core.messages import BatteryReading, TemperatureReading, GpsReading
from sensors.battery import BatteryReader
from sensors.temperature import TemperatureReader
from sensors.gps import GpsReader
```

- [ ] `on_configure()`:
  - Create reader instances based on config `sensors.*` sections
  - Only create readers for `enabled: true` sensors

- [ ] `on_activate()`:
  - Start a polling thread that reads each sensor at its configured `poll_interval`

- [ ] `on_shutdown()`:
  - Stop polling thread
  - Cleanup readers

- [ ] Polling logic:
  ```
  Use separate timers per sensor (different poll rates):
    battery: every 0.5s → publish BatteryReading on "sensor.battery"
    temperature: every 1.0s → publish TemperatureReading on "sensor.temperature"
    gps: every 1.0s → publish GpsReading on "sensor.gps"
  ```

- [ ] Sensor failures MUST NOT crash the node. Catch exceptions, log, and continue.

### Module 6 Checklist

- [ ] `sensors/__init__.py` exists
- [ ] `sensors/battery.py` — `BatteryReader` with ADS1115, MCP3008, and mock backends
- [ ] `sensors/temperature.py` — `TemperatureReader` with DS18B20 and mock backends
- [ ] `sensors/gps.py` — `GpsReader` with NMEA parsing
- [ ] `sensors/sensor_node.py` — `SensorNode(BaseNode)`:
  - [ ] Polls enabled sensors at configured intervals
  - [ ] Publishes typed messages on internal bus
  - [ ] Graceful degradation if hardware unavailable
  - [ ] Sensor errors are logged but do not crash the node

---

## 11. MODULE 7 — TELEMETRY PUBLISHER

> **Depends on**: Module 1, Module 3 (TelemetryPayload), Module 6 (sensor messages)

### 11.1 `telemetry/telemetry_node.py` — TelemetryNode

```python
import time
import threading
from core.node import BaseNode
from core.messages import (
    TelemetryPayload, BatteryReading, TemperatureReading, GpsReading, SafetyStatus,
)
```

**Responsibilities:**

- [ ] `on_configure()`:
  - Read `telemetry.publish_rate_hz` from config
  - Subscribe to sensor topics on internal bus
  - Maintain latest reading of each sensor (thread-safe)

- [ ] `on_activate()`:
  - Start a timer thread that fires at `publish_rate_hz`

- [ ] `on_shutdown()`:
  - Stop timer thread

- [ ] Timer callback (every `1.0 / publish_rate_hz` seconds):
  ```
  Assemble TelemetryPayload from latest sensor readings:
    speed = gps.speed_mps (if GPS enabled) or 0.0
    battery_voltage = battery.voltage or 0.0
    battery_percent = battery.percent or 0.0
    motor_temp_left = temperature.motor_left_c or 0.0
    motor_temp_right = temperature.motor_right_c or 0.0
    signal_strength = 0  (reserved for WiFi RSSI, future)
    gps_lat = gps.latitude or 0.0
    gps_lon = gps.longitude or 0.0
    custom = {"armed": safety.armed, "hb_age": safety.heartbeat_age_ms}

  Publish TelemetryPayload on "telemetry.outbound"
  ```

- [ ] Missing sensor data defaults to 0.0 — never skip a telemetry publish because one sensor failed

### Module 7 Checklist

- [ ] `telemetry/__init__.py` exists
- [ ] `telemetry/telemetry_node.py` — `TelemetryNode(BaseNode)`:
  - [ ] Subscribes to all sensor bus topics
  - [ ] Publishes at configured rate (default 2 Hz)
  - [ ] Assembles TelemetryPayload with latest readings
  - [ ] Includes safety status in `custom` field
  - [ ] Missing readings default to 0.0

---

## 12. MODULE 8 — UTILITIES

> **Depends on**: nothing

### 12.1 `utils/logging_setup.py`

Same pattern as Phase 1, but with log rotation for long-running daemon:

```python
import logging
import sys
from logging.handlers import RotatingFileHandler
import os

def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger("ugv")
    root.setLevel(level)

    if log_cfg.get("console", True):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)

    log_file = log_cfg.get("file", "")
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        max_bytes = log_cfg.get("max_bytes", 5_242_880)
        backup_count = log_cfg.get("backup_count", 3)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count,
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Silence noisy libraries
    logging.getLogger("paho").setLevel(logging.WARNING)
```

### 12.2 `utils/timing.py`

Same as Phase 1:

```python
import time

class RateTracker:
    def __init__(self, window: float = 1.0):
        self._window = window
        self._count = 0
        self._last_reset = time.monotonic()
        self._hz = 0.0

    def tick(self) -> None:
        self._count += 1
        now = time.monotonic()
        dt = now - self._last_reset
        if dt >= self._window:
            self._hz = self._count / dt
            self._count = 0
            self._last_reset = now

    @property
    def hz(self) -> float:
        return self._hz

class LatencyTimer:
    def __init__(self):
        self._start: float = 0.0

    def start(self) -> None:
        self._start = time.monotonic()

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._start) * 1000.0
```

### Module 8 Checklist

- [ ] `utils/__init__.py` exists
- [ ] `utils/logging_setup.py` — `setup_logging()` with RotatingFileHandler
- [ ] `utils/timing.py` — `RateTracker`, `LatencyTimer`

---

## 13. MODULE 9 — ENTRY POINT & LAUNCHER

> **Depends on**: all previous modules

### 13.1 `main.py`

```python
#!/usr/bin/env python3
"""
UGV On-Board Software — Phase 3
Raspberry Pi headless daemon for teleoperated UGV.

Receives MQTT commands from operator, drives motors, reads sensors, publishes telemetry.
"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.config_loader import load_config
from utils.logging_setup import setup_logging
from core.message_bus import MessageBus
from core.state_manager import StateManager
from core.launcher import Launcher
from mqtt.mqtt_bridge import MqttBridgeNode
from drive.drive_node import DriveNode
from safety.watchdog_node import SafetyNode
from sensors.sensor_node import SensorNode
from telemetry.telemetry_node import TelemetryNode


def main() -> int:
    # 1. Load config
    config = load_config()

    # 2. Setup logging
    setup_logging(config)

    import logging
    logger = logging.getLogger("ugv.main")
    logger.info("UGV On-Board Software starting...")

    # 3. Create shared infrastructure
    bus = MessageBus()
    state = StateManager(bus)

    # 4. Create nodes (order matters for dependencies)
    safety_node = SafetyNode("safety", bus, config)
    mqtt_node = MqttBridgeNode("mqtt", bus, config)
    drive_node = DriveNode("drive", bus, config)
    sensor_node = SensorNode("sensors", bus, config)
    telemetry_node = TelemetryNode("telemetry", bus, config)

    # 5. Register with launcher (shutdown runs in reverse order)
    launcher = Launcher()
    launcher.register(safety_node)      # First to start, last to stop
    launcher.register(mqtt_node)
    launcher.register(sensor_node)
    launcher.register(telemetry_node)
    launcher.register(drive_node)       # Last to start (needs safety + MQTT first)
    launcher.setup_signal_handlers()

    # 6. Start all nodes
    try:
        launcher.start_all()
    except Exception as e:
        logger.fatal(f"Failed to start: {e}")
        launcher.shutdown_all()
        return 1

    logger.info("All nodes active. Waiting for operator heartbeat...")

    # 7. Run forever (headless daemon — no GUI event loop)
    #    Block main thread until signal received.
    stop_event = threading.Event()

    def on_signal(signum, frame):
        logger.info(f"Signal {signum} received, shutting down...")
        stop_event.set()

    import signal
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        stop_event.wait()  # Block until SIGINT/SIGTERM
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down all nodes...")
        launcher.shutdown_all()
        logger.info("UGV software stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Key differences from Phase 1:**

- No Qt event loop — uses `threading.Event().wait()` to block the main thread
- SafetyNode starts FIRST (E-stop engaged from the start)
- DriveNode starts LAST (needs safety + MQTT running)
- On shutdown, nodes stop in reverse order: DriveNode stops first → safety last (E-stop engaged on shutdown)

### Module 9 Checklist

- [ ] `main.py` at project root
- [ ] Loads config → logging → bus → state
- [ ] Creates nodes in correct dependency order
- [ ] SafetyNode starts first
- [ ] DriveNode starts last
- [ ] Main thread blocks on `threading.Event` (not busy-wait)
- [ ] Clean shutdown on SIGINT/SIGTERM
- [ ] Returns exit code 0 on clean shutdown

---

## 14. MODULE 10 — SETUP & INSTALLATION

> **Depends on**: knowing the final dependency list

### 14.1 `requirements.txt`

```
paho-mqtt>=2.0.0
PyYAML>=6.0
RPi.GPIO>=0.7.1
pyserial>=3.5
smbus2>=0.4.0
adafruit-circuitpython-ads1x15>=2.2.0
```

**NOTE**: `RPi.GPIO` will fail to install on non-Pi systems. Development machines should use `pip install --no-deps` or mock the imports.

### 14.2 `setup.sh`

```bash
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

# ── Install systemd service ──
echo "Installing systemd service..."
sudo cp "$SCRIPT_DIR/ugv.service" /etc/systemd/system/ugv.service
sudo systemctl daemon-reload
sudo systemctl enable ugv.service
echo "[OK] systemd service installed and enabled (starts on boot)"
echo "  Manual control:"
echo "    sudo systemctl start ugv"
echo "    sudo systemctl stop ugv"
echo "    sudo systemctl status ugv"
echo "    journalctl -u ugv -f"

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
```

### 14.3 `run.sh`

```bash
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
exec python3 main.py "$@"
```

### 14.4 `ugv.service` — systemd Unit File

```ini
[Unit]
Description=UGV On-Board Software
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/home/pi/ugv-software
ExecStart=/home/pi/ugv-software/venv/bin/python3 /home/pi/ugv-software/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Safety: if the process is killed, systemd restarts it
WatchdogSec=30
NotifyAccess=none

# Resource limits
MemoryMax=256M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
```

### Module 10 Checklist

- [ ] `requirements.txt` with all dependencies
- [ ] `setup.sh`:
  - [ ] Checks Python 3.11+
  - [ ] Installs system packages (apt)
  - [ ] Enables I2C, Serial, 1-Wire via raspi-config
  - [ ] Creates venv and installs pip requirements
  - [ ] Creates log directory
  - [ ] Copies config example
  - [ ] Installs and enables systemd service
- [ ] `run.sh` — venv activation + launch
- [ ] `ugv.service` — systemd unit with auto-restart
- [ ] `Clone → setup.sh → reboot → running` path validated

---

## 15. MODULE 11 — TESTING

> **Depends on**: all modules

### 15.1 Test Files

| Test File               | Coverage                                      |
|-------------------------|-----------------------------------------------|
| `test_message_bus.py`   | Pub/sub, thread safety, error isolation        |
| `test_serializer.py`    | Joystick deserialization, telemetry serialization, pong echo |
| `test_mixer.py`         | Arcade mix, tank mix, edge cases, clamping     |
| `test_watchdog.py`      | State transitions, timeout, recovery, E-stop   |
| `test_config_loader.py` | YAML loading, deep merge, missing file         |

### 15.2 Key Test Cases

**`test_serializer.py`:**

```python
def test_deserialize_joystick_matches_phase1_format():
    """Phase 1 sends this exact format — we MUST parse it correctly."""
    payload = b'{"t":1712099123000,"sa":{"0":0.1234,"1":-0.5678},"ta":{"2":0.8,"5":0.3},"sb":{"288":true},"tb":{},"sh":{"H1":[0,1]},"th":{"CS":[-1,0]}}'
    cmd = deserialize_joystick(payload)
    assert cmd.stick_axes["0"] == 0.1234
    assert cmd.stick_axes["1"] == -0.5678
    assert cmd.throttle_axes["2"] == 0.8
    assert cmd.stick_buttons["288"] == True
    assert cmd.stick_hats["H1"] == [0, 1]

def test_serialize_telemetry_matches_phase1_expectations():
    """Phase 1 expects these exact keys."""
    telem = TelemetryPayload(
        timestamp=0, speed=2.5, battery_voltage=12.3, battery_percent=85.0,
        motor_temp_left=45.0, motor_temp_right=42.0, signal_strength=-68,
        gps_lat=40.1234, gps_lon=-105.5678,
    )
    data = json.loads(serialize_telemetry(telem))
    assert "speed" in data
    assert "bat_v" in data
    assert "bat_pct" in data
    assert "temp_l" in data
    assert "temp_r" in data

def test_pong_echoes_ping_exactly():
    ping_payload = b'{"t":1712099123000,"seq":42}'
    ping = deserialize_ping(ping_payload)
    pong = serialize_pong(ping)
    pong_data = json.loads(pong)
    assert pong_data["t"] == 1712099123000
    assert pong_data["seq"] == 42
```

**`test_mixer.py`:**

```python
def test_arcade_neutral():
    left, right = arcade_mix(0.0, 0.0)
    assert left == 0.0 and right == 0.0

def test_arcade_forward():
    left, right = arcade_mix(1.0, 0.0)
    assert left == 1.0 and right == 1.0

def test_arcade_turn_right():
    left, right = arcade_mix(1.0, 1.0, steer_sensitivity=1.0)
    assert left > right  # Left faster than right = turn right

def test_arcade_output_clamped():
    left, right = arcade_mix(1.0, 1.0)
    assert -1.0 <= left <= 1.0
    assert -1.0 <= right <= 1.0

def test_tank_center_is_stop():
    left, right = tank_mix(0.5, 0.5)
    assert abs(left) < 0.01 and abs(right) < 0.01

def test_tank_full_forward():
    left, right = tank_mix(1.0, 1.0)
    assert left == 1.0 and right == 1.0
```

**`test_watchdog.py`:**

```python
def test_watchdog_disarmed_on_startup():
    """With startup_armed=False, safety starts disarmed."""
    # Create SafetyNode with mock bus, startup_armed=False
    # Assert initial status is not armed

def test_watchdog_arms_on_first_heartbeat():
    """First heartbeat transitions DISARMED → ARMED."""

def test_watchdog_estop_on_timeout():
    """Missing heartbeat beyond timeout transitions to ESTOP_ACTIVE."""

def test_watchdog_recovers_on_heartbeat():
    """Heartbeat after timeout transitions ESTOP_ACTIVE → ARMED."""
```

### Module 11 Checklist

- [ ] `tests/__init__.py` exists
- [ ] `test_message_bus.py` — pub/sub, thread safety
- [ ] `test_serializer.py` — Phase 1 payload compatibility
- [ ] `test_mixer.py` — arcade + tank mixing
- [ ] `test_watchdog.py` — safety state machine
- [ ] `test_config_loader.py` — YAML loading
- [ ] All tests pass with `pytest tests/`

---

## 16. MODULE 12 — DOCUMENTATION

### 16.1 `README.md`

Create a README with:

- Overview (what the software does)
- Architecture diagram (node graph)
- Requirements (Raspberry Pi 4B/5, Pi OS Bookworm)
- Quick start (`setup.sh` → `run.sh`)
- Configuration reference (all YAML keys)
- Hardware wiring diagram reference
- Troubleshooting (common issues)
- Safety warnings

### Module 12 Checklist

- [ ] `README.md` with all sections above
- [ ] Clear safety warnings about E-stop relay
- [ ] Pin wiring table

---

## 17. MQTT PROTOCOL CONTRACT (Phase 1 ↔ Phase 3)

This section defines the EXACT wire format for all MQTT messages. Phase 1 (operator ground station) is already built and deployed. Phase 3 (this software) MUST be 100% compatible.

### 17.1 Topics

| Topic            | Direction (from Pi's perspective) | QoS | Rate     | Purpose                     |
|------------------|-----------------------------------|-----|----------|-----------------------------|
| `ugv/joystick`   | **INBOUND** (subscribe)           | 0   | 30-50 Hz | Joystick state from operator|
| `ugv/heartbeat`  | **INBOUND** (subscribe)           | 0   | 1 Hz     | Operator alive signal       |
| `ugv/ping`       | **INBOUND** (subscribe)           | 0   | 0.5 Hz   | Latency probe               |
| `ugv/pong`       | **OUTBOUND** (publish)            | 0   | 0.5 Hz   | Latency echo response       |
| `ugv/telemetry`  | **OUTBOUND** (publish)            | 1   | 2 Hz     | Vehicle telemetry           |

### 17.2 Inbound: `ugv/joystick` Payload

Published by Phase 1 at 30-50 Hz. Compact JSON:

```json
{
  "t": 1712099123000,
  "sa": {"0": 0.1234, "1": -0.5678},
  "ta": {"0": 0.0, "2": 0.8, "5": 0.3},
  "sb": {"288": true, "293": true},
  "tb": {},
  "sh": {"H1": [0, 1]},
  "th": {"CS": [-1, 0]}
}
```

| Key  | Type                         | Description                                     |
|------|------------------------------|-------------------------------------------------|
| `t`  | `int`                        | Epoch milliseconds (operator's wall clock)      |
| `sa` | `dict[str, float]`           | Stick axes. Key = evdev code as string. Value = normalized -1.0 to +1.0 (bipolar) |
| `ta` | `dict[str, float]`           | Throttle axes. Key = evdev code as string. Value = 0.0 to +1.0 (unipolar) |
| `sb` | `dict[str, bool]`            | Stick buttons. **Only pressed buttons included** (absent = not pressed) |
| `tb` | `dict[str, bool]`            | Throttle buttons. Same convention.              |
| `sh` | `dict[str, list[int, int]]`  | Stick hats. Key = hat name (e.g., "H1"). Value = [x, y] where x,y ∈ {-1, 0, 1} |
| `th` | `dict[str, list[int, int]]`  | Throttle hats. Same format.                     |

**Axis evdev codes used by Thrustmaster HOTAS Warthog:**

| Device   | Code | Name             | Range          |
|----------|------|------------------|----------------|
| Stick    | `0`  | ABS_X (roll)     | -1.0 to +1.0   |
| Stick    | `1`  | ABS_Y (pitch)    | -1.0 to +1.0   |
| Throttle | `0`  | Slew X           | -1.0 to +1.0   |
| Throttle | `1`  | Slew Y           | -1.0 to +1.0   |
| Throttle | `2`  | Left Throttle    | 0.0 to +1.0    |
| Throttle | `5`  | Right Throttle   | 0.0 to +1.0    |
| Throttle | `40` | Friction         | 0.0 to +1.0    |

### 17.3 Inbound: `ugv/heartbeat` Payload

Published by Phase 1 at 1 Hz:

```json
{"t": 1712099123000}
```

Single field: epoch milliseconds. The Pi uses the arrival time (not the timestamp value) for watchdog timing.

### 17.4 Inbound: `ugv/ping` Payload

Published by Phase 1 at 0.5 Hz:

```json
{"t": 1712099123000, "seq": 42}
```

| Key   | Type  | Description                        |
|-------|-------|------------------------------------|
| `t`   | `int` | Epoch ms (operator's wall clock)   |
| `seq` | `int` | Sequence number (monotonic)        |

### 17.5 Outbound: `ugv/pong` Payload

Pi MUST echo back with the SAME `t` and `seq` values:

```json
{"t": 1712099123000, "seq": 42}
```

Phase 1 computes RTT as: `now - original_send_time` (using `seq` to match).

**CRITICAL: Do NOT modify the `t` value.** Phase 1 stored the send time associated with `seq` locally — it does NOT use the `t` field for RTT calculation, but the field must be echoed for protocol compatibility.

### 17.6 Outbound: `ugv/telemetry` Payload

Pi publishes at 2 Hz with QoS 1:

```json
{
  "speed": 2.5,
  "bat_v": 12.3,
  "bat_pct": 85.0,
  "temp_l": 45.0,
  "temp_r": 42.0,
  "rssi": -68,
  "lat": 40.123456,
  "lon": -105.567890
}
```

| Key      | Type    | Description                                | Default |
|----------|---------|--------------------------------------------|---------|
| `speed`  | `float` | Vehicle speed in m/s                        | 0.0     |
| `bat_v`  | `float` | Battery voltage (V)                         | 0.0     |
| `bat_pct`| `float` | Battery state of charge (0-100%)            | 0.0     |
| `temp_l` | `float` | Left motor temperature (°C)                 | 0.0     |
| `temp_r` | `float` | Right motor temperature (°C)                | 0.0     |
| `rssi`   | `int`   | Signal strength (dBm, negative, 0 if N/A)   | 0       |
| `lat`    | `float` | GPS latitude (decimal degrees)               | 0.0     |
| `lon`    | `float` | GPS longitude (decimal degrees)              | 0.0     |

Phase 1 also accepts any additional keys — they are stored in `TelemetryData.custom`. Use this to send extra data like `"armed": true`, `"hb_age": 150.0`, etc.

---

## 18. JOYSTICK PAYLOAD REFERENCE

### 18.1 Default Drive Mapping (Arcade Mode)

For arcade mode, the DriveNode reads:

```
speed = joystick.stick_axes.get("1", 0.0)   # Stick Y axis (pitch forward/back)
steer = joystick.stick_axes.get("0", 0.0)   # Stick X axis (roll left/right)
```

Then calls `arcade_mix(speed, steer)` → `(left_motor, right_motor)`.

**Stick axis orientation (Phase 1 normalizes):**
- Stick Y (`"1"`): **-1.0 = full forward push**, **+1.0 = full back pull** (inverted — set `invert_speed: true` in config to flip)
- Stick X (`"0"`): **-1.0 = full left**, **+1.0 = full right**

### 18.2 Default Drive Mapping (Tank Mode)

For tank mode, the DriveNode reads:

```
left_throttle  = joystick.throttle_axes.get("2", 0.0)   # Left throttle (0..1)
right_throttle = joystick.throttle_axes.get("5", 0.0)   # Right throttle (0..1)
```

Then calls `tank_mix(left_throttle, right_throttle)` → `(left_motor, right_motor)`.

**Throttle orientation:**
- 0.0 = throttle fully forward (idle)
- 1.0 = throttle fully back (full)
- Center (0.5) maps to motor stop in tank_mix

### 18.3 Button Reference

These buttons can be used for special functions (configurable in future):

| Button Code | Name  | Suggested Use          |
|-------------|-------|------------------------|
| `288`       | TG1   | Primary trigger (fire) |
| `292`       | S1    | Paddle (E-stop toggle) |
| `291`       | S4    | Pinky (mode switch)    |

For Phase 3, buttons are available in `JoystickCommand.stick_buttons` and `throttle_buttons` but are not required for basic drive functionality. Implement button handling as extensible hooks for future use.

---

## 19. HARDWARE WIRING REFERENCE

### 19.1 GPIO Pin Assignments (Default Config)

| BCM Pin | Function           | Direction | Notes                              |
|---------|--------------------|-----------|------------------------------------|
| 18      | Left motor PWM     | Output    | Hardware PWM (PWM0)                |
| 19      | Right motor PWM    | Output    | Hardware PWM (PWM1)                |
| 23      | Left motor DIR     | Output    | HIGH = forward, LOW = reverse      |
| 24      | Right motor DIR    | Output    | HIGH = forward, LOW = reverse      |
| 25      | E-stop relay       | Output    | Active LOW (LOW = E-stop engaged)  |

### 19.2 I2C Devices

| Address | Device       | Purpose               |
|---------|-------------|-----------------------|
| 0x48    | ADS1115     | Battery voltage ADC   |
| 0x40    | PCA9685     | Servo/ESC PWM driver  |

### 19.3 Serial Ports

| Port          | Device        | Purpose             |
|---------------|---------------|---------------------|
| `/dev/ttyAMA0`| GPS module    | NMEA sentences      |
| `/dev/ttyUSB0`| PLC           | Motor control serial|

### 19.4 1-Wire (DS18B20)

| Path                                     | Purpose              |
|------------------------------------------|----------------------|
| `/sys/bus/w1/devices/28-xxxx/temperature`| Motor temperature    |

---

## 20. ACCEPTANCE CRITERIA

The implementation is COMPLETE when ALL of the following are true:

### 20.1 Functional

- [ ] Software connects to MQTT broker and subscribes to joystick, heartbeat, ping topics
- [ ] Joystick payloads from Phase 1 are correctly parsed (test with Phase 1 running)
- [ ] Pong responses echo ping with correct `t` and `seq` values
- [ ] Phase 1 displays RTT latency (proves pong echo works)
- [ ] Telemetry publishes at ~2 Hz and Phase 1 displays it correctly
- [ ] Drive mixing converts joystick inputs to motor outputs (arcade and tank modes)
- [ ] Motor backend outputs correct PWM/serial commands
- [ ] Ramp rate limiter prevents instant speed jumps

### 20.2 Safety

- [ ] E-stop relay engages on startup (fail-safe until first heartbeat)
- [ ] E-stop engages within 500ms of heartbeat timeout
- [ ] E-stop disengages when heartbeat resumes
- [ ] Motors ramp to zero (not instant cut) on E-stop
- [ ] Node shutdown always engages E-stop
- [ ] Pi power loss results in E-stop engaged (GPIO default LOW)
- [ ] Safety cannot be disabled by config (minimum heartbeat_timeout = 1.0s)

### 20.3 Reliability

- [ ] Software auto-starts on boot via systemd
- [ ] systemd restarts software if it crashes
- [ ] MQTT disconnect/reconnect works automatically
- [ ] Sensor failures don't crash the software
- [ ] Missing sensors result in 0.0 telemetry values (not errors)
- [ ] Log rotation prevents disk fill

### 20.4 Code Quality

- [ ] All modules have type hints on every function signature
- [ ] All classes have docstrings
- [ ] All tests pass with `pytest tests/`
- [ ] No hardcoded pin numbers (all from config)
- [ ] No hardcoded MQTT topics (all from config)
- [ ] Clean separation: core framework has no hardware imports

### 20.5 Integration Test (End-to-End)

The ultimate test:

```
1. Start Phase 1 (operator PC) connected to MQTT broker
2. Start Phase 3 (this software on Raspberry Pi) connected to same broker
3. Phase 1 HUD shows:
   - MQTT: GREEN
   - RTT latency: measured and displayed
   - Telemetry tab: shows live battery, temperature data
4. Move HOTAS stick → motor outputs change
5. Disconnect Phase 1 → E-stop engages within 3 seconds
6. Reconnect Phase 1 → motors resume
```

---

## END OF DOCUMENT

This document contains everything needed to build the UGV Phase 3 Raspberry Pi software. No external references are required. Build modules 1 through 12 in order, validate each with the REVIEWER before proceeding, and confirm all acceptance criteria before declaring complete.
