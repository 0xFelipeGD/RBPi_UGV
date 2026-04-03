"""Typed message dataclasses for inter-node communication."""

from dataclasses import dataclass, field
import time


@dataclass(frozen=True)
class JoystickCommand:
    """Parsed joystick state from MQTT. Published on 'command.joystick'."""

    timestamp: float
    remote_timestamp_ms: int
    stick_axes: dict[str, float]
    throttle_axes: dict[str, float]
    stick_buttons: dict[str, bool]
    throttle_buttons: dict[str, bool]
    stick_hats: dict[str, list[int]]
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
    left_speed: float
    right_speed: float
    source: str


@dataclass(frozen=True)
class SafetyStatus:
    """Safety system state. Published on 'safety.status'."""

    timestamp: float
    armed: bool
    reason: str
    heartbeat_age_ms: float


@dataclass(frozen=True)
class BatteryReading:
    """Published on 'sensor.battery'."""

    timestamp: float
    voltage: float
    percent: float
    current: float


@dataclass(frozen=True)
class TemperatureReading:
    """Published on 'sensor.temperature'."""

    timestamp: float
    motor_left_c: float
    motor_right_c: float


@dataclass(frozen=True)
class GpsReading:
    """Published on 'sensor.gps'."""

    timestamp: float
    latitude: float
    longitude: float
    speed_mps: float
    fix: bool


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
