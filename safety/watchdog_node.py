"""SafetyNode: heartbeat watchdog with E-stop relay control.

THIS IS THE MOST CRITICAL MODULE — incorrect implementation can damage hardware
or injure people.

State machine:
    DISARMED -> (first heartbeat) -> ARMED -> (timeout) -> ESTOP_ACTIVE
    ESTOP_ACTIVE -> (heartbeat resumes) -> ARMED

E-stop relay uses active LOW convention (fail-safe):
    GPIO LOW  = relay energized = E-stop ENGAGED (motors cannot run)
    GPIO HIGH = relay released  = E-stop RELEASED (motors can run)
    If Pi loses power, GPIO goes LOW -> E-stop engages automatically.
"""

import time
import threading
import logging
from enum import Enum, auto

try:
    import RPi.GPIO as GPIO
    _HAS_GPIO = True
except ImportError:
    _HAS_GPIO = False

from core.node import BaseNode
from core.message_bus import MessageBus
from core.messages import Heartbeat, SafetyStatus


class WatchdogState(Enum):
    """Safety system states."""
    DISARMED = auto()
    ARMED = auto()
    ESTOP_ACTIVE = auto()


class SafetyNode(BaseNode):
    """Heartbeat watchdog with E-stop relay control.

    Monitors operator heartbeat messages. If heartbeat is not received within
    the timeout period, engages the E-stop relay and notifies all nodes via
    SafetyStatus messages on the internal bus.

    Safety cannot be bypassed: minimum heartbeat_timeout is enforced at 1.0s.
    """

    def __init__(self, name: str, bus: MessageBus, config: dict) -> None:
        super().__init__(name, bus, config)
        self._watchdog_state = WatchdogState.DISARMED
        self._last_heartbeat: float = 0.0
        self._heartbeat_timeout: float = 3.0
        self._estop_pin: int = 25
        self._startup_armed: bool = False
        self._ramp_down_time: float = 0.5
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        # Per-link aliveness (observability-only; does not affect watchdog logic).
        self._link_local_alive = False
        self._link_vps_alive = False

    def on_configure(self) -> None:
        """Load safety config and setup E-stop GPIO."""
        safety_cfg = self.config.get("safety", {})
        self._heartbeat_timeout = max(safety_cfg.get("heartbeat_timeout", 3.0), 1.0)
        self._estop_pin = safety_cfg.get("estop_pin", 25)
        self._startup_armed = safety_cfg.get("startup_armed", False)
        self._ramp_down_time = safety_cfg.get("ramp_down_time", 0.5)

        # Setup E-stop GPIO — start ENGAGED (LOW = safe)
        if _HAS_GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self._estop_pin, GPIO.OUT, initial=GPIO.LOW)
            self.logger.info(f"E-stop GPIO {self._estop_pin} initialized (ENGAGED)")
        else:
            self.logger.warning("RPi.GPIO not available — E-stop in log-only mode")

        # Subscribe to heartbeat
        self.bus.subscribe("command.heartbeat", self._on_heartbeat)
        # Subscribe to dual-link state for telemetry observability (no behavior change).
        self.bus.subscribe("mqtt.link_state", self._on_link_state)

        self.logger.info(
            f"Safety configured: timeout={self._heartbeat_timeout}s, "
            f"estop_pin={self._estop_pin}, startup_armed={self._startup_armed}"
        )

    def on_activate(self) -> None:
        """Start watchdog timer thread."""
        if self._startup_armed:
            self._watchdog_state = WatchdogState.ARMED
            self._last_heartbeat = time.monotonic()
            self._set_estop(engaged=False)
            self._publish_status("ok")
            self.logger.info("Safety armed on startup (startup_armed=true)")
        else:
            self._watchdog_state = WatchdogState.DISARMED
            self._set_estop(engaged=True)
            self._publish_status("waiting_for_heartbeat")
            self.logger.info("Safety disarmed — waiting for first heartbeat")

        self._running = True
        self._thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._thread.start()

    def on_shutdown(self) -> None:
        """Engage E-stop and stop watchdog thread."""
        self._running = False
        self._set_estop(engaged=True)
        self.logger.info("E-stop engaged on shutdown")
        if self._thread:
            self._thread.join(timeout=2.0)
        if _HAS_GPIO:
            GPIO.cleanup([self._estop_pin])

    def _on_heartbeat(self, msg: Heartbeat) -> None:
        """Handle heartbeat from operator."""
        with self._lock:
            self._last_heartbeat = time.monotonic()
            if self._watchdog_state == WatchdogState.DISARMED:
                self._watchdog_state = WatchdogState.ARMED
                self._set_estop(engaged=False)
                self._publish_status("ok")
                self.logger.info("First heartbeat received — ARMED")

    def _on_link_state(self, snapshot):
        # Accept both DualLinkSnapshot and plain dict from the bus.
        d = snapshot.to_telemetry() if hasattr(snapshot, "to_telemetry") else dict(snapshot)
        self._link_local_alive = d.get("local") == "UP"
        self._link_vps_alive = d.get("vps") == "UP"

    def _watchdog_loop(self) -> None:
        """Monitor heartbeat age every 100ms."""
        while self._running:
            with self._lock:
                age = time.monotonic() - self._last_heartbeat if self._last_heartbeat > 0 else float("inf")
                current_state = self._watchdog_state

                if current_state == WatchdogState.ARMED and age > self._heartbeat_timeout:
                    self._watchdog_state = WatchdogState.ESTOP_ACTIVE
                    self._set_estop(engaged=True)
                    self._publish_status("heartbeat_timeout")
                    self.logger.warning(
                        f"HEARTBEAT TIMEOUT ({age:.1f}s > {self._heartbeat_timeout}s) — E-STOP ENGAGED"
                    )

                elif current_state == WatchdogState.ESTOP_ACTIVE and age <= self._heartbeat_timeout:
                    self._watchdog_state = WatchdogState.ARMED
                    self._set_estop(engaged=False)
                    self._publish_status("ok")
                    self.logger.info("Heartbeat resumed — ARMED")

            time.sleep(0.1)  # 10 Hz watchdog check

    def _set_estop(self, engaged: bool) -> None:
        """Control E-stop relay. Active LOW = engaged."""
        if _HAS_GPIO:
            GPIO.output(self._estop_pin, GPIO.LOW if engaged else GPIO.HIGH)
        else:
            state_str = "ENGAGED" if engaged else "RELEASED"
            self.logger.info(f"E-stop {state_str} (simulated)")

    def _publish_status(self, reason: str) -> None:
        """Publish SafetyStatus on the internal bus."""
        armed = self._watchdog_state == WatchdogState.ARMED
        age_ms = 0.0
        if self._last_heartbeat > 0:
            age_ms = (time.monotonic() - self._last_heartbeat) * 1000.0
        self.bus.publish("safety.status", SafetyStatus(
            timestamp=time.monotonic(),
            armed=armed,
            reason=reason,
            heartbeat_age_ms=age_ms,
        ))
