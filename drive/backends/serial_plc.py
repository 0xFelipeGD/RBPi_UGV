"""Serial/UART PLC motor backend for Phase 4 communication."""

import json
import logging

try:
    import serial
    _HAS_SERIAL = True
except ImportError:
    _HAS_SERIAL = False

from drive.backends.base import MotorBackend


class SerialPlcBackend(MotorBackend):
    """Motor backend communicating with an external PLC via serial UART.

    Sends JSON commands: {"cmd":"drive","l":0.5,"r":0.5}
    Supports "json" (default) or "modbus" (future) protocol.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("ugv.drive.serial_plc")
        self._ser: "serial.Serial | None" = None
        self._protocol: str = "json"

    def configure(self, config: dict) -> None:
        """Open serial port from config."""
        cfg = config.get("serial_plc", {})
        port = cfg.get("port", "/dev/ttyUSB0")
        baudrate = cfg.get("baudrate", 115200)
        timeout = cfg.get("timeout", 0.1)
        self._protocol = cfg.get("protocol", "json")

        if not _HAS_SERIAL:
            self.logger.warning("pyserial not available — running in log-only mode")
            return

        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=timeout,
            )
            self.logger.info(f"Serial PLC connected: {port} @ {baudrate} baud")
        except Exception as e:
            self.logger.error(f"Serial PLC open failed: {e}")
            self._ser = None

    def set_speeds(self, left: float, right: float) -> None:
        """Send drive command to PLC."""
        if not self._ser:
            return
        try:
            if self._protocol == "json":
                cmd = json.dumps(
                    {"cmd": "drive", "l": round(left, 4), "r": round(right, 4)},
                    separators=(",", ":"),
                )
                self._ser.write((cmd + "\n").encode("utf-8"))
        except Exception as e:
            self.logger.error(f"Serial PLC write error: {e}")

    def stop(self) -> None:
        """Send stop command to PLC."""
        if not self._ser:
            self.logger.info("Serial PLC stop (simulated)")
            return
        try:
            cmd = json.dumps({"cmd": "stop"}, separators=(",", ":"))
            self._ser.write((cmd + "\n").encode("utf-8"))
        except Exception as e:
            self.logger.error(f"Serial PLC stop error: {e}")

    def cleanup(self) -> None:
        """Close serial port."""
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        self.logger.info("Serial PLC cleaned up")
