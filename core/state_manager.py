"""Thread-safe central state store for the UGV system."""

import threading
from core.message_bus import MessageBus
from core.messages import (
    JoystickCommand,
    DriveOutput,
    SafetyStatus,
    BatteryReading,
    TemperatureReading,
    GpsReading,
)


class StateManager:
    """Thread-safe central state. Nodes publish updates; TelemetryNode reads snapshots.

    Subscribes to all relevant internal bus topics and stores the latest
    message of each type. Any node can read a consistent snapshot via snapshot().
    """

    def __init__(self, bus: MessageBus) -> None:
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
        """Return a consistent snapshot of all current state."""
        with self._lock:
            return {
                "joystick": self.joystick,
                "drive": self.drive,
                "safety": self.safety,
                "battery": self.battery,
                "temperature": self.temperature,
                "gps": self.gps,
            }
