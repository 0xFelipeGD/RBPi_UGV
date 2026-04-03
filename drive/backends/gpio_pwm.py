"""RPi.GPIO hardware PWM motor backend."""

import logging

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False

from drive.backends.base import MotorBackend


class GpioPwmBackend(MotorBackend):
    """Motor backend using RPi.GPIO hardware PWM and direction pins.

    Direction convention: HIGH = forward, LOW = reverse.
    PWM duty cycle maps abs(speed) to [min_duty, max_duty].
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("ugv.drive.gpio_pwm")
        self._left_pwm = None
        self._right_pwm = None
        self._left_pin: int = 18
        self._right_pin: int = 19
        self._left_dir_pin: int = 23
        self._right_dir_pin: int = 24
        self._frequency: int = 1000
        self._min_duty: float = 0
        self._max_duty: float = 100

    def configure(self, config: dict) -> None:
        """Setup GPIO pins and start PWM at 0% duty."""
        cfg = config.get("gpio_pwm", {})
        self._left_pin = cfg.get("left_pin", 18)
        self._right_pin = cfg.get("right_pin", 19)
        self._left_dir_pin = cfg.get("left_dir_pin", 23)
        self._right_dir_pin = cfg.get("right_dir_pin", 24)
        self._frequency = cfg.get("frequency", 1000)
        self._min_duty = cfg.get("min_duty", 0)
        self._max_duty = cfg.get("max_duty", 100)

        if not _HAS_GPIO:
            self.logger.warning("RPi.GPIO not available — running in log-only mode")
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # PWM pins
        GPIO.setup(self._left_pin, GPIO.OUT)
        GPIO.setup(self._right_pin, GPIO.OUT)
        self._left_pwm = GPIO.PWM(self._left_pin, self._frequency)
        self._right_pwm = GPIO.PWM(self._right_pin, self._frequency)
        self._left_pwm.start(0)
        self._right_pwm.start(0)

        # Direction pins
        GPIO.setup(self._left_dir_pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self._right_dir_pin, GPIO.OUT, initial=GPIO.LOW)

        self.logger.info(
            f"GPIO PWM configured: L={self._left_pin}/{self._left_dir_pin} "
            f"R={self._right_pin}/{self._right_dir_pin} @ {self._frequency}Hz"
        )

    def set_speeds(self, left: float, right: float) -> None:
        """Set motor speeds and directions via GPIO."""
        if not _HAS_GPIO:
            return

        for speed, pwm, dir_pin in [
            (left, self._left_pwm, self._left_dir_pin),
            (right, self._right_pwm, self._right_dir_pin),
        ]:
            # Direction: HIGH = forward, LOW = reverse
            GPIO.output(dir_pin, GPIO.HIGH if speed >= 0 else GPIO.LOW)
            # Duty cycle from absolute speed
            duty = abs(speed) * (self._max_duty - self._min_duty) + self._min_duty
            duty = max(0.0, min(100.0, duty))
            if abs(speed) < 0.01:
                duty = 0.0
            pwm.ChangeDutyCycle(duty)

    def stop(self) -> None:
        """Immediately stop all motors."""
        if not _HAS_GPIO:
            self.logger.info("GPIO stop (simulated)")
            return
        if self._left_pwm:
            self._left_pwm.ChangeDutyCycle(0)
        if self._right_pwm:
            self._right_pwm.ChangeDutyCycle(0)
        GPIO.output(self._left_dir_pin, GPIO.LOW)
        GPIO.output(self._right_dir_pin, GPIO.LOW)

    def cleanup(self) -> None:
        """Release GPIO resources."""
        if not _HAS_GPIO:
            return
        if self._left_pwm:
            self._left_pwm.stop()
        if self._right_pwm:
            self._right_pwm.stop()
        GPIO.cleanup([
            self._left_pin, self._right_pin,
            self._left_dir_pin, self._right_dir_pin,
        ])
        self.logger.info("GPIO PWM cleaned up")
