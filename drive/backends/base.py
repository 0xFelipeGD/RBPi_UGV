"""Abstract motor backend interface."""

from abc import ABC, abstractmethod


class MotorBackend(ABC):
    """Abstract interface for motor output hardware.

    All backends must implement configure, set_speeds, stop, and cleanup.
    Speed values are always in the range [-1.0, +1.0].
    """

    @abstractmethod
    def configure(self, config: dict) -> None:
        """Initialize hardware with given config section."""
        ...

    @abstractmethod
    def set_speeds(self, left: float, right: float) -> None:
        """Set motor speeds. left, right: -1.0 (full reverse) to +1.0 (full forward)."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Immediately stop all motors (emergency stop)."""
        ...

    @abstractmethod
    def cleanup(self) -> None:
        """Release hardware resources."""
        ...
