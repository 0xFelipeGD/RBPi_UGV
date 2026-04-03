"""GPS NMEA parser for serial GPS modules."""

import logging

try:
    import serial
    _HAS_SERIAL = True
except ImportError:
    _HAS_SERIAL = False

from core.messages import GpsReading
import time


class GpsReader:
    """Reads and parses NMEA sentences from a serial GPS module.

    Looks for $GPRMC or $GNRMC sentences for position, speed, and fix status.
    Handles incomplete sentences, checksum validation, and missing fields.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("ugv.sensors.gps")
        self._ser: "serial.Serial | None" = None
        self._buffer: str = ""

    def configure(self, config: dict) -> None:
        """Open serial port for GPS module."""
        port = config.get("port", "/dev/ttyAMA0")
        baudrate = config.get("baudrate", 9600)

        if not _HAS_SERIAL:
            self.logger.warning("pyserial not available — GPS in mock mode")
            return

        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=0.5,
            )
            self.logger.info(f"GPS serial opened: {port} @ {baudrate}")
        except Exception as e:
            self.logger.error(f"GPS serial open failed: {e}")
            self._ser = None

    def read(self) -> GpsReading:
        """Read and parse latest GPS data.

        Returns:
            GpsReading with current position, speed, and fix status.
            Returns empty reading with fix=False if GPS unavailable.
        """
        if not self._ser:
            return GpsReading(
                timestamp=time.monotonic(),
                latitude=0.0, longitude=0.0,
                speed_mps=0.0, fix=False,
            )

        try:
            # Read available data and buffer it
            if self._ser.in_waiting:
                raw = self._ser.read(self._ser.in_waiting)
                self._buffer += raw.decode("ascii", errors="ignore")

            # Process complete sentences
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                line = line.strip()
                if line.startswith("$GPRMC") or line.startswith("$GNRMC"):
                    reading = self._parse_rmc(line)
                    if reading is not None:
                        return reading

        except Exception as e:
            self.logger.error(f"GPS read error: {e}")

        return GpsReading(
            timestamp=time.monotonic(),
            latitude=0.0, longitude=0.0,
            speed_mps=0.0, fix=False,
        )

    def _parse_rmc(self, sentence: str) -> GpsReading | None:
        """Parse a $GPRMC/$GNRMC sentence.

        Format: $GPRMC,hhmmss.ss,A,ddmm.mmmm,N,dddmm.mmmm,W,knots,course,ddmmyy,...*cs
        """
        if not self._validate_checksum(sentence):
            return None

        # Strip checksum
        if "*" in sentence:
            sentence = sentence[:sentence.index("*")]

        parts = sentence.split(",")
        if len(parts) < 8:
            return None

        status = parts[2]  # A = active fix, V = void
        fix = status == "A"

        lat = 0.0
        lon = 0.0
        speed_mps = 0.0

        if fix:
            # Latitude: ddmm.mmmm
            if parts[3]:
                lat = self._nmea_to_decimal(parts[3], parts[4] if len(parts) > 4 else "N")
            # Longitude: dddmm.mmmm
            if parts[5]:
                lon = self._nmea_to_decimal(parts[5], parts[6] if len(parts) > 6 else "E")
            # Speed in knots -> m/s
            if parts[7]:
                try:
                    speed_knots = float(parts[7])
                    speed_mps = speed_knots * 0.514444
                except ValueError:
                    pass

        return GpsReading(
            timestamp=time.monotonic(),
            latitude=lat,
            longitude=lon,
            speed_mps=speed_mps,
            fix=fix,
        )

    @staticmethod
    def _nmea_to_decimal(value: str, direction: str) -> float:
        """Convert NMEA ddmm.mmmm to decimal degrees."""
        try:
            if not value:
                return 0.0
            # Find the decimal point to split degrees from minutes
            dot = value.index(".")
            degrees = int(value[:dot - 2])
            minutes = float(value[dot - 2:])
            decimal = degrees + minutes / 60.0
            if direction in ("S", "W"):
                decimal = -decimal
            return decimal
        except (ValueError, IndexError):
            return 0.0

    @staticmethod
    def _validate_checksum(sentence: str) -> bool:
        """Validate NMEA sentence checksum."""
        if "*" not in sentence:
            return False
        try:
            body, checksum_str = sentence.split("*", 1)
            # Remove leading $
            if body.startswith("$"):
                body = body[1:]
            expected = int(checksum_str[:2], 16)
            computed = 0
            for ch in body:
                computed ^= ord(ch)
            return computed == expected
        except (ValueError, IndexError):
            return False

    def cleanup(self) -> None:
        """Close serial port."""
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
