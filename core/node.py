"""BaseNode abstract class with ROS2-inspired lifecycle management."""

from abc import ABC, abstractmethod
from enum import Enum, auto
import logging


class NodeState(Enum):
    """Lifecycle states for a node."""

    CREATED = auto()
    CONFIGURED = auto()
    ACTIVE = auto()
    SHUTDOWN = auto()


class BaseNode(ABC):
    """Abstract base class for all UGV nodes.

    Provides a standard lifecycle: CREATED -> CONFIGURED -> ACTIVE -> SHUTDOWN.
    Subclasses implement on_configure(), on_activate(), and on_shutdown().
    """

    def __init__(self, name: str, bus: "MessageBus", config: dict) -> None:
        self.name = name
        self.bus = bus
        self.config = config
        self.state = NodeState.CREATED
        self.logger = logging.getLogger(f"ugv.{name}")

    def configure(self) -> None:
        """Transition from CREATED to CONFIGURED."""
        assert self.state == NodeState.CREATED, (
            f"{self.name}: configure() called in state {self.state}"
        )
        self.on_configure()
        self.state = NodeState.CONFIGURED

    def activate(self) -> None:
        """Transition from CONFIGURED to ACTIVE."""
        assert self.state == NodeState.CONFIGURED, (
            f"{self.name}: activate() called in state {self.state}"
        )
        self.on_activate()
        self.state = NodeState.ACTIVE

    def shutdown(self) -> None:
        """Transition to SHUTDOWN (idempotent)."""
        if self.state != NodeState.SHUTDOWN:
            self.on_shutdown()
            self.state = NodeState.SHUTDOWN

    @abstractmethod
    def on_configure(self) -> None:
        """Load config, validate parameters, create resources (but don't start)."""
        ...

    @abstractmethod
    def on_activate(self) -> None:
        """Start threads/timers, begin processing."""
        ...

    @abstractmethod
    def on_shutdown(self) -> None:
        """Stop threads/timers, release resources, engage E-stop."""
        ...
