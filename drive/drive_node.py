"""DriveNode: converts joystick commands to motor output via configurable mixing."""

import time
import threading
from core.node import BaseNode
from core.message_bus import MessageBus
from core.messages import JoystickCommand, DriveOutput, SafetyStatus
from drive.mixer import arcade_mix, tank_mix
from drive.backends.base import MotorBackend
from drive.backends.gpio_pwm import GpioPwmBackend
from drive.backends.pca9685 import Pca9685Backend
from drive.backends.serial_plc import SerialPlcBackend


_BACKENDS: dict[str, type[MotorBackend]] = {
    "gpio_pwm": GpioPwmBackend,
    "pca9685": Pca9685Backend,
    "serial_plc": SerialPlcBackend,
}


class DriveNode(BaseNode):
    """Joystick-to-motor drive node.

    Reads joystick commands, applies mixing (arcade or tank), applies ramp
    rate limiting, and outputs to the configured motor backend.
    Respects SafetyStatus — outputs zero when not armed.
    """

    def __init__(self, name: str, bus: MessageBus, config: dict) -> None:
        super().__init__(name, bus, config)
        self._backend: MotorBackend | None = None
        self._mode: str = "arcade"
        self._ramp_rate: float = 2.0
        self._ramp_down_time: float = 0.5

        # Latest state (updated by bus callbacks)
        self._latest_joystick: JoystickCommand | None = None
        self._armed: bool = False
        self._joy_lock = threading.Lock()

        # Ramp state
        self._current_left: float = 0.0
        self._current_right: float = 0.0

        # Control loop
        self._running = False
        self._thread: threading.Thread | None = None

        # Axis config
        self._arcade_cfg: dict = {}
        self._tank_cfg: dict = {}

    def on_configure(self) -> None:
        """Load drive config and initialize motor backend."""
        drive_cfg = self.config.get("drive", {})
        self._mode = drive_cfg.get("mode", "arcade")
        if self._mode not in ("arcade", "tank"):
            raise ValueError(f"Unknown drive mode: {self._mode!r}. Expected 'arcade' or 'tank'.")
        self._ramp_rate = drive_cfg.get("ramp_rate", 2.0)
        self._ramp_down_time = self.config.get("safety", {}).get("ramp_down_time", 0.5)
        self._arcade_cfg = drive_cfg.get("arcade", {})
        self._tank_cfg = drive_cfg.get("tank", {})

        # Instantiate backend
        backend_name = drive_cfg.get("backend", "gpio_pwm")
        backend_cls = _BACKENDS.get(backend_name)
        if backend_cls is None:
            raise ValueError(f"Unknown drive backend: {backend_name}")
        self._backend = backend_cls()
        self._backend.configure(drive_cfg)

        # Subscribe to bus topics
        self.bus.subscribe("command.joystick", self._on_joystick)
        self.bus.subscribe("safety.status", self._on_safety)

        self.logger.info(f"Drive configured: mode={self._mode}, backend={backend_name}")

    def on_activate(self) -> None:
        """Start the control loop thread at ~50 Hz."""
        self._running = True
        self._thread = threading.Thread(target=self._control_loop, daemon=True)
        self._thread.start()

    def on_shutdown(self) -> None:
        """Stop control loop and motors."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._backend:
            self._backend.stop()
            self._backend.cleanup()

    def _on_joystick(self, msg: JoystickCommand) -> None:
        with self._joy_lock:
            self._latest_joystick = msg

    def _on_safety(self, msg: SafetyStatus) -> None:
        with self._joy_lock:
            self._armed = msg.armed

    def _control_loop(self) -> None:
        """Main drive control loop running at ~50 Hz."""
        last_time = time.monotonic()
        loop_interval = 1.0 / 50.0  # 50 Hz

        while self._running:
            now = time.monotonic()
            dt = now - last_time
            last_time = now

            with self._joy_lock:
                joy = self._latest_joystick
                armed = self._armed

            # Compute target speeds
            if not armed or joy is None:
                target_left, target_right = 0.0, 0.0
            else:
                target_left, target_right = self._compute_mix(joy)

            # Apply ramp rate limiter
            if armed:
                max_step = self._ramp_rate * dt
            else:
                # Faster ramp down on safety stop
                ramp_down_rate = 1.0 / max(self._ramp_down_time, 0.01)
                max_step = ramp_down_rate * dt

            self._current_left = self._ramp(self._current_left, target_left, max_step)
            self._current_right = self._ramp(self._current_right, target_right, max_step)

            # Output to backend
            if self._backend:
                self._backend.set_speeds(self._current_left, self._current_right)

            # Publish on internal bus
            self.bus.publish("drive.output", DriveOutput(
                timestamp=now,
                left_speed=self._current_left,
                right_speed=self._current_right,
                source="operator" if armed else "safety_stop",
            ))

            # Maintain loop rate
            elapsed = time.monotonic() - now
            sleep_time = loop_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _compute_mix(self, joy: JoystickCommand) -> tuple[float, float]:
        """Extract axes from joystick and compute motor mix."""
        if self._mode == "arcade":
            cfg = self._arcade_cfg
            speed_axis = cfg.get("speed_axis", "1")
            steer_axis = cfg.get("steer_axis", "0")
            speed_source = cfg.get("speed_source", "stick")
            steer_source = cfg.get("steer_source", "stick")

            axes_map = {
                "stick": joy.stick_axes,
                "throttle": joy.throttle_axes,
            }
            speed = axes_map.get(speed_source, joy.stick_axes).get(speed_axis, 0.0)
            steer = axes_map.get(steer_source, joy.stick_axes).get(steer_axis, 0.0)

            if cfg.get("invert_speed", False):
                speed = -speed
            if cfg.get("invert_steer", False):
                steer = -steer

            max_speed = cfg.get("max_speed", 1.0)
            speed *= max_speed
            sensitivity = cfg.get("steer_sensitivity", 0.7)

            return arcade_mix(speed, steer, sensitivity)

        elif self._mode == "tank":
            cfg = self._tank_cfg
            left_axis = cfg.get("left_axis", "2")
            right_axis = cfg.get("right_axis", "5")
            left_source = cfg.get("left_source", "throttle")
            right_source = cfg.get("right_source", "throttle")

            axes_map = {
                "stick": joy.stick_axes,
                "throttle": joy.throttle_axes,
            }
            left_val = axes_map.get(left_source, joy.throttle_axes).get(left_axis, 0.0)
            right_val = axes_map.get(right_source, joy.throttle_axes).get(right_axis, 0.0)

            if cfg.get("invert_left", False):
                left_val = 1.0 - left_val
            if cfg.get("invert_right", False):
                right_val = 1.0 - right_val

            return tank_mix(left_val, right_val)

        else:
            # on_configure already validated self._mode — this branch is unreachable.
            raise AssertionError(f"Unexpected drive mode: {self._mode!r}")

    @staticmethod
    def _ramp(current: float, target: float, max_step: float) -> float:
        """Apply ramp rate limiting to a single channel."""
        delta = target - current
        if abs(delta) <= max_step:
            return target
        return current + max_step * (1.0 if delta > 0 else -1.0)
