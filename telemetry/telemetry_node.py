"""TelemetryNode: assembles sensor data and publishes at configured rate."""

import time
import threading
from core.node import BaseNode
from core.message_bus import MessageBus
from core.messages import (
    TelemetryPayload,
    BatteryReading,
    TemperatureReading,
    GpsReading,
    SafetyStatus,
)


class TelemetryNode(BaseNode):
    """Packages latest sensor readings into telemetry payloads.

    Subscribes to all sensor and safety bus topics, maintains the latest
    reading of each, and publishes assembled TelemetryPayload at the
    configured rate (default 2 Hz).

    Missing sensor data defaults to 0.0 — a telemetry publish is never
    skipped because one sensor failed.
    """

    def __init__(self, name: str, bus: MessageBus, config: dict) -> None:
        super().__init__(name, bus, config)
        self._publish_rate_hz: float = 2.0
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Latest readings
        self._battery: BatteryReading | None = None
        self._temperature: TemperatureReading | None = None
        self._gps: GpsReading | None = None
        self._safety: SafetyStatus | None = None

        # Dual-link state snapshot (spec §11 — links field).
        # Default DOWN/DOWN until MqttBridgeNode publishes a real snapshot.
        self._link_snapshot: dict = {"local": "DOWN", "vps": "DOWN"}

    def on_configure(self) -> None:
        """Load telemetry config and subscribe to sensor topics."""
        telem_cfg = self.config.get("telemetry", {})
        self._publish_rate_hz = telem_cfg.get("publish_rate_hz", 2.0)

        self.bus.subscribe("sensor.battery", self._on_battery)
        self.bus.subscribe("sensor.temperature", self._on_temperature)
        self.bus.subscribe("sensor.gps", self._on_gps)
        self.bus.subscribe("safety.status", self._on_safety)
        self.bus.subscribe("mqtt.link_state", self._on_link_state)

        self.logger.info(f"Telemetry configured: publish rate = {self._publish_rate_hz} Hz")

    def on_activate(self) -> None:
        """Start the telemetry publish timer thread."""
        self._running = True
        self._thread = threading.Thread(target=self._publish_loop, daemon=True)
        self._thread.start()

    def on_shutdown(self) -> None:
        """Stop the publish timer."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _on_battery(self, msg: BatteryReading) -> None:
        with self._lock:
            self._battery = msg

    def _on_temperature(self, msg: TemperatureReading) -> None:
        with self._lock:
            self._temperature = msg

    def _on_gps(self, msg: GpsReading) -> None:
        with self._lock:
            self._gps = msg

    def _on_safety(self, msg: SafetyStatus) -> None:
        with self._lock:
            self._safety = msg

    def _on_link_state(self, snapshot) -> None:
        """Cache the latest dual-link snapshot for the next telemetry publish.

        MqttBridgeNode publishes ``DualLinkSnapshot`` instances to
        ``mqtt.link_state``. We accept either that object (preferred) or a
        plain ``{"local": ..., "vps": ...}`` dict for defensive coding.
        """
        # Duck-type: DualLinkSnapshot has a to_telemetry() method.
        to_telem = getattr(snapshot, "to_telemetry", None)
        if callable(to_telem):
            data = to_telem()
        elif isinstance(snapshot, dict):
            data = snapshot
        else:
            self.logger.warning(
                f"Unexpected mqtt.link_state payload type: {type(snapshot).__name__}"
            )
            return
        with self._lock:
            self._link_snapshot = {
                "local": data.get("local", "DOWN"),
                "vps": data.get("vps", "DOWN"),
            }

    def _publish_loop(self) -> None:
        """Publish telemetry at the configured rate."""
        interval = 1.0 / max(self._publish_rate_hz, 0.1)
        while self._running:
            start = time.monotonic()
            self._publish_telemetry()
            elapsed = time.monotonic() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _publish_telemetry(self) -> None:
        """Assemble and publish a single telemetry payload."""
        with self._lock:
            bat = self._battery
            temp = self._temperature
            gps = self._gps
            safety = self._safety
            links = dict(self._link_snapshot)

        # Build custom fields
        custom: dict = {}
        if safety:
            custom["armed"] = safety.armed
            custom["hb_age"] = round(safety.heartbeat_age_ms, 1)
        # Spec §11: links field — keys local/vps, values UP/DOWN/DEGRADED.
        custom["links"] = links

        payload = TelemetryPayload(
            timestamp=time.monotonic(),
            speed=gps.speed_mps if gps else 0.0,
            battery_voltage=bat.voltage if bat else 0.0,
            battery_percent=bat.percent if bat else 0.0,
            motor_temp_left=temp.motor_left_c if temp else 0.0,
            motor_temp_right=temp.motor_right_c if temp else 0.0,
            signal_strength=0,
            gps_lat=gps.latitude if gps else 0.0,
            gps_lon=gps.longitude if gps else 0.0,
            custom=custom,
        )

        self.bus.publish("telemetry.outbound", payload)
