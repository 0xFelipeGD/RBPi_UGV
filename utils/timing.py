"""Rate tracking and latency measurement utilities."""

import time


class RateTracker:
    """Measures the frequency (Hz) of recurring events.

    Call tick() on each event. Read the hz property for the current rate.
    """

    def __init__(self, window: float = 1.0) -> None:
        self._window = window
        self._count = 0
        self._last_reset = time.monotonic()
        self._hz = 0.0

    def tick(self) -> None:
        """Record one event occurrence."""
        self._count += 1
        now = time.monotonic()
        dt = now - self._last_reset
        if dt >= self._window:
            self._hz = self._count / dt
            self._count = 0
            self._last_reset = now

    @property
    def hz(self) -> float:
        """Current measured rate in Hz."""
        return self._hz


class LatencyTimer:
    """Simple start/stop timer for measuring latency in milliseconds."""

    def __init__(self) -> None:
        self._start: float = 0.0

    def start(self) -> None:
        """Record the start time."""
        self._start = time.monotonic()

    def elapsed_ms(self) -> float:
        """Return milliseconds elapsed since start()."""
        return (time.monotonic() - self._start) * 1000.0
