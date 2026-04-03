"""Battery voltage reader with ADS1115, MCP3008, and mock backends."""

import logging

try:
    import board
    import busio
    import adafruit_ads1x15.ads1115 as ADS
    from adafruit_ads1x15.analog_in import AnalogIn
    _HAS_ADS = True
except ImportError:
    _HAS_ADS = False


class BatteryReader:
    """Reads battery voltage via ADC and estimates state of charge.

    Supports ADS1115 (I2C), MCP3008 (SPI), or mock backend.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("ugv.sensors.battery")
        self._backend: str = "ads1115"
        self._channel: "AnalogIn | None" = None
        self._voltage_divider_ratio: float = 4.0
        self._cell_count: int = 4
        self._cell_min_v: float = 3.0
        self._cell_max_v: float = 4.2
        self._mock_voltage: float = 12.6

    def configure(self, config: dict) -> None:
        """Initialize the ADC backend from config."""
        self._backend = config.get("backend", "ads1115")
        self._voltage_divider_ratio = config.get("voltage_divider_ratio", 4.0)
        self._cell_count = config.get("cell_count", 4)
        self._cell_min_v = config.get("cell_min_v", 3.0)
        self._cell_max_v = config.get("cell_max_v", 4.2)

        if self._backend == "mock":
            self.logger.info("Battery reader: mock backend")
            return

        if self._backend == "ads1115":
            if not _HAS_ADS:
                self.logger.warning("ADS1115 library not available — falling back to mock")
                self._backend = "mock"
                return
            try:
                i2c = busio.I2C(board.SCL, board.SDA)
                ads = ADS.ADS1115(i2c, address=config.get("i2c_address", 0x48))
                channel_num = config.get("channel", 0)
                channel_map = {0: ADS.P0, 1: ADS.P1, 2: ADS.P2, 3: ADS.P3}
                self._channel = AnalogIn(ads, channel_map.get(channel_num, ADS.P0))
                self.logger.info(f"ADS1115 battery reader configured on channel {channel_num}")
            except Exception as e:
                self.logger.error(f"ADS1115 init failed: {e} — falling back to mock")
                self._backend = "mock"

        elif self._backend == "mcp3008":
            self.logger.warning("MCP3008 backend: using mock (install spidev for hardware)")
            self._backend = "mock"

    def read(self) -> tuple[float, float]:
        """Read battery voltage and estimate SOC percentage.

        Returns:
            (voltage, percent) where voltage is actual battery voltage
            and percent is 0-100 state of charge estimate.
        """
        if self._backend == "mock":
            return self._mock_voltage, self._estimate_soc(self._mock_voltage)

        if self._backend == "ads1115" and self._channel is not None:
            try:
                raw_voltage = self._channel.voltage
                voltage = raw_voltage * self._voltage_divider_ratio
                percent = self._estimate_soc(voltage)
                return voltage, percent
            except Exception as e:
                self.logger.error(f"Battery read error: {e}")
                return 0.0, 0.0

        return 0.0, 0.0

    def _estimate_soc(self, voltage: float) -> float:
        """Estimate state of charge from total pack voltage."""
        if self._cell_count <= 0:
            return 0.0
        cell_voltage = voltage / self._cell_count
        v_range = self._cell_max_v - self._cell_min_v
        if v_range <= 0:
            return 0.0
        percent = (cell_voltage - self._cell_min_v) / v_range * 100.0
        return max(0.0, min(100.0, percent))
