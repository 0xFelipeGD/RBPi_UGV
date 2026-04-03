"""Node lifecycle manager with ordered start/stop and signal handling."""

import signal
import logging
from core.node import BaseNode


class Launcher:
    """Manages the lifecycle of all registered nodes.

    Nodes are configured and activated in registration order.
    On shutdown, nodes are stopped in reverse order (last started = first stopped).
    Handles SIGINT and SIGTERM for clean systemd shutdown.
    """

    def __init__(self) -> None:
        self.nodes: list[BaseNode] = []
        self.logger = logging.getLogger("ugv.launcher")

    def register(self, node: BaseNode) -> None:
        """Register a node for lifecycle management."""
        self.nodes.append(node)

    def start_all(self) -> None:
        """Configure then activate all registered nodes in order."""
        for node in self.nodes:
            self.logger.info(f"Configuring: {node.name}")
            node.configure()
        for node in self.nodes:
            self.logger.info(f"Activating: {node.name}")
            node.activate()

    def shutdown_all(self) -> None:
        """Shutdown all nodes in reverse registration order."""
        for node in reversed(self.nodes):
            self.logger.info(f"Shutting down: {node.name}")
            try:
                node.shutdown()
            except Exception as e:
                self.logger.error(f"Error shutting down {node.name}: {e}")

    def setup_signal_handlers(self) -> None:
        """Register SIGINT/SIGTERM handlers for clean daemon shutdown."""

        def handler(signum: int, frame: object) -> None:
            self.logger.info(f"Signal {signum} received, shutting down...")
            self.shutdown_all()
            raise SystemExit(0)

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
