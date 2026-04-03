"""SensorNode: polls all enabled sensors at configured intervals."""

import time
import threading
from core.node import BaseNode
from core.message_bus import MessageBus
from core.messages import BatteryReading, TemperatureReading, GpsReading
from sensors.battery import BatteryReader
from sensors.temperature import TemperatureReader
from sensors.gps import GpsReader


class SensorNode(BaseNode):
    """Polls enabled sensors at configured intervals and publishes readings.

    Each sensor type has its own poll interval. Sensor failures are logged
    but never crash the node — missing data defaults to 0.0.
    """

    def __init__(self, name: str, bus: MessageBus, config: dict) -> None:
        super().__init__(name, bus, config)
        self._battery: BatteryReader | None = None
        self._temperature: TemperatureReader | None = None
        self._gps: GpsReader | None = None
        self._running = False
        self._thread: threading.Thread | None = None

        # Poll intervals
        self._battery_interval: float = 0.5
        self._temperature_interval: float = 1.0
        self._gps_interval: float = 1.0

    def on_configure(self) -> None:
        """Create sensor readers for enabled sensors."""
        sensors_cfg = self.config.get("sensors", {})

        # Battery
        bat_cfg = sensors_cfg.get("battery", {})
        if bat_cfg.get("enabled", False):
            self._battery = BatteryReader()
            self._battery.configure(bat_cfg)
            self._battery_interval = bat_cfg.get("poll_interval", 0.5)
            self.logger.info(f"Battery sensor enabled (poll: {self._battery_interval}s)")

        # Temperature
        temp_cfg = sensors_cfg.get("temperature", {})
        if temp_cfg.get("enabled", False):
            self._temperature = TemperatureReader()
            self._temperature.configure(temp_cfg)
            self._temperature_interval = temp_cfg.get("poll_interval", 1.0)
            self.logger.info(f"Temperature sensor enabled (poll: {self._temperature_interval}s)")

        # GPS
        gps_cfg = sensors_cfg.get("gps", {})
        if gps_cfg.get("enabled", False):
            self._gps = GpsReader()
            self._gps.configure(gps_cfg)
            self._gps_interval = gps_cfg.get("poll_interval", 1.0)
            self.logger.info(f"GPS sensor enabled (poll: {self._gps_interval}s)")

    def on_activate(self) -> None:
        """Start sensor polling thread."""
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def on_shutdown(self) -> None:
        """Stop polling and cleanup sensor resources."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._gps:
            self._gps.cleanup()

    def _poll_loop(self) -> None:
        """Poll each sensor at its own interval using separate timers."""
        last_battery = 0.0
        last_temperature = 0.0
        last_gps = 0.0

        while self._running:
            now = time.monotonic()

            # Battery
            if self._battery and (now - last_battery) >= self._battery_interval:
                last_battery = now
                try:
                    voltage, percent = self._battery.read()
                    self.bus.publish("sensor.battery", BatteryReading(
                        timestamp=now,
                        voltage=voltage,
                        percent=percent,
                        current=0.0,
                    ))
                except Exception as e:
                    self.logger.error(f"Battery read error: {e}")

            # Temperature
            if self._temperature and (now - last_temperature) >= self._temperature_interval:
                last_temperature = now
                try:
                    left_c, right_c = self._temperature.read()
                    self.bus.publish("sensor.temperature", TemperatureReading(
                        timestamp=now,
                        motor_left_c=left_c,
                        motor_right_c=right_c,
                    ))
                except Exception as e:
                    self.logger.error(f"Temperature read error: {e}")

            # GPS
            if self._gps and (now - last_gps) >= self._gps_interval:
                last_gps = now
                try:
                    reading = self._gps.read()
                    self.bus.publish("sensor.gps", reading)
                except Exception as e:
                    self.logger.error(f"GPS read error: {e}")

            time.sleep(0.05)  # 20 Hz base loop rate
