"""Thread-safe internal pub/sub message bus with error isolation."""

import logging
import threading
from collections import defaultdict
from typing import Any, Callable


class MessageBus:
    """Internal publish/subscribe message bus.

    Provides decoupled communication between nodes via named topics.
    Thread-safe: multiple nodes can publish/subscribe from different threads.
    Error-isolated: a failing subscriber does not affect other subscribers.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()
        self._logger = logging.getLogger("ugv.bus")

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        """Register a callback for a topic."""
        with self._lock:
            self._subscribers[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        """Remove a callback from a topic."""
        with self._lock:
            self._subscribers[topic] = [
                cb for cb in self._subscribers[topic] if cb is not callback
            ]

    def publish(self, topic: str, message: Any) -> None:
        """Publish a message to all subscribers of a topic.

        Subscriber exceptions are caught and logged — they never propagate
        to the publisher or affect other subscribers.
        """
        with self._lock:
            listeners = list(self._subscribers.get(topic, []))
        for cb in listeners:
            try:
                cb(message)
            except Exception as e:
                self._logger.error(f"Subscriber error on '{topic}': {e}")
