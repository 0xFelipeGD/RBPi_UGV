"""Temperature reader for DS18B20 1-Wire sensors and mock backend."""

import glob
import logging
import os


class TemperatureReader:
    """Reads motor temperatures from DS18B20 1-Wire sensors.

    Reads from /sys/bus/w1/devices/<sensor_id>/temperature.
    Auto-detects sensor IDs if config values are empty.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("ugv.sensors.temperature")
        self._backend: str = "ds18b20"
        self._left_path: str = ""
        self._right_path: str = ""
        self._mock_left: float = 25.0
        self._mock_right: float = 25.0

    def configure(self, config: dict) -> None:
        """Initialize temperature sensor paths."""
        self._backend = config.get("backend", "ds18b20")

        if self._backend == "mock":
            self.logger.info("Temperature reader: mock backend")
            return

        left_id = config.get("left_sensor_id", "")
        right_id = config.get("right_sensor_id", "")

        # Auto-detect if IDs not specified
        if not left_id or not right_id:
            sensors = sorted(glob.glob("/sys/bus/w1/devices/28-*/temperature"))
            if len(sensors) >= 1 and not left_id:
                left_id = sensors[0].split("/")[-2]
            if len(sensors) >= 2 and not right_id:
                right_id = sensors[1].split("/")[-2]

        if left_id:
            self._left_path = f"/sys/bus/w1/devices/{left_id}/temperature"
        if right_id:
            self._right_path = f"/sys/bus/w1/devices/{right_id}/temperature"

        if not self._left_path and not self._right_path:
            self.logger.warning("No DS18B20 sensors found — falling back to mock")
            self._backend = "mock"
        else:
            self.logger.info(
                f"DS18B20 sensors: left={self._left_path or 'none'}, right={self._right_path or 'none'}"
            )

    def read(self) -> tuple[float, float]:
        """Read left and right motor temperatures in Celsius.

        Returns:
            (left_temp_c, right_temp_c). Returns 0.0 for unavailable sensors.
        """
        if self._backend == "mock":
            return self._mock_left, self._mock_right

        left_c = self._read_sensor(self._left_path)
        right_c = self._read_sensor(self._right_path)
        return left_c, right_c

    def _read_sensor(self, path: str) -> float:
        """Read a single DS18B20 sensor. Returns 0.0 on failure."""
        if not path or not os.path.exists(path):
            return 0.0
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
            # Value is in millidegrees Celsius
            return int(raw) / 1000.0
        except Exception as e:
            self.logger.error(f"Temperature read error ({path}): {e}")
            return 0.0
