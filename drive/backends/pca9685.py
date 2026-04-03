"""PCA9685 I2C servo/ESC motor backend."""

import logging

try:
    from adafruit_servokit import ServoKit
    _HAS_PCA = True
except ImportError:
    try:
        import board
        import busio
        from adafruit_pca9685 import PCA9685
        _HAS_PCA = True
    except ImportError:
        _HAS_PCA = False

from drive.backends.base import MotorBackend


class Pca9685Backend(MotorBackend):
    """Motor backend using PCA9685 I2C PWM driver for RC ESCs.

    Maps [-1.0, +1.0] speed to [min_pulse_us, max_pulse_us] with center at neutral.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("ugv.drive.pca9685")
        self._kit = None
        self._left_channel: int = 0
        self._right_channel: int = 1
        self._min_pulse: int = 1000
        self._center_pulse: int = 1500
        self._max_pulse: int = 2000

    def configure(self, config: dict) -> None:
        """Initialize PCA9685 via I2C."""
        cfg = config.get("pca9685", {})
        self._left_channel = cfg.get("left_channel", 0)
        self._right_channel = cfg.get("right_channel", 1)
        self._min_pulse = cfg.get("min_pulse_us", 1000)
        self._center_pulse = cfg.get("center_pulse_us", 1500)
        self._max_pulse = cfg.get("max_pulse_us", 2000)
        frequency = cfg.get("frequency", 50)

        if not _HAS_PCA:
            self.logger.warning("PCA9685 libraries not available — running in log-only mode")
            return

        try:
            self._kit = ServoKit(channels=16, address=cfg.get("i2c_address", 0x40))
            self._kit.frequency = frequency
            # Set actuation range for pulse width mapping
            for ch in [self._left_channel, self._right_channel]:
                self._kit.servo[ch].set_pulse_width_range(self._min_pulse, self._max_pulse)
            self.logger.info(
                f"PCA9685 configured: L=ch{self._left_channel} R=ch{self._right_channel} @ {frequency}Hz"
            )
        except Exception as e:
            self.logger.error(f"PCA9685 init failed: {e}")
            self._kit = None

    def _speed_to_pulse(self, speed: float) -> int:
        """Map [-1.0, +1.0] to pulse width in microseconds."""
        if speed >= 0:
            return int(self._center_pulse + speed * (self._max_pulse - self._center_pulse))
        else:
            return int(self._center_pulse + speed * (self._center_pulse - self._min_pulse))

    def set_speeds(self, left: float, right: float) -> None:
        """Set ESC pulse widths based on speed values."""
        if not self._kit:
            return
        try:
            left_pulse = self._speed_to_pulse(left)
            right_pulse = self._speed_to_pulse(right)
            self._kit.servo[self._left_channel].angle = None  # Raw pulse mode
            self._kit.servo[self._right_channel].angle = None
            self._kit._pca.channels[self._left_channel].duty_cycle = self._pulse_to_duty(left_pulse)
            self._kit._pca.channels[self._right_channel].duty_cycle = self._pulse_to_duty(right_pulse)
        except Exception as e:
            self.logger.error(f"PCA9685 set_speeds error: {e}")

    def _pulse_to_duty(self, pulse_us: int) -> int:
        """Convert pulse width in us to 16-bit duty cycle value."""
        # PCA9685 runs at configured frequency, period = 1/freq seconds
        # duty_cycle is 0-65535
        period_us = 1_000_000 / (self._kit.frequency if self._kit else 50)
        return int((pulse_us / period_us) * 65535)

    def stop(self) -> None:
        """Set both channels to neutral (center pulse)."""
        if not self._kit:
            self.logger.info("PCA9685 stop (simulated)")
            return
        try:
            center_duty = self._pulse_to_duty(self._center_pulse)
            self._kit._pca.channels[self._left_channel].duty_cycle = center_duty
            self._kit._pca.channels[self._right_channel].duty_cycle = center_duty
        except Exception as e:
            self.logger.error(f"PCA9685 stop error: {e}")

    def cleanup(self) -> None:
        """De-initialize PCA9685."""
        if self._kit:
            try:
                self.stop()
                self._kit._pca.deinit()
            except Exception as e:
                self.logger.error(f"PCA9685 cleanup error: {e}")
            self._kit = None
        self.logger.info("PCA9685 cleaned up")
