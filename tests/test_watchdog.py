"""Tests for safety watchdog state machine."""

import time
import threading
from unittest.mock import patch
from core.message_bus import MessageBus
from core.messages import Heartbeat, SafetyStatus
from safety.watchdog_node import SafetyNode, WatchdogState


def _make_safety(startup_armed: bool = False, timeout: float = 0.3) -> tuple[SafetyNode, MessageBus, list]:
    """Helper to create a SafetyNode for testing."""
    bus = MessageBus()
    config = {
        "safety": {
            "heartbeat_timeout": timeout,
            "estop_pin": 25,
            "startup_armed": startup_armed,
            "ramp_down_time": 0.5,
        }
    }
    node = SafetyNode("test_safety", bus, config)
    statuses: list[SafetyStatus] = []
    bus.subscribe("safety.status", lambda msg: statuses.append(msg))
    return node, bus, statuses


@patch("safety.watchdog_node._HAS_GPIO", False)
def test_watchdog_disarmed_on_startup():
    """With startup_armed=False, safety starts disarmed."""
    node, bus, statuses = _make_safety(startup_armed=False)
    node.configure()
    node.activate()
    time.sleep(0.05)
    assert node._watchdog_state == WatchdogState.DISARMED
    assert len(statuses) >= 1
    assert statuses[0].armed is False
    node.shutdown()


@patch("safety.watchdog_node._HAS_GPIO", False)
def test_watchdog_arms_on_first_heartbeat():
    """First heartbeat transitions DISARMED -> ARMED."""
    node, bus, statuses = _make_safety(startup_armed=False)
    node.configure()
    node.activate()
    time.sleep(0.05)

    # Send heartbeat
    bus.publish("command.heartbeat", Heartbeat(timestamp=time.monotonic(), remote_timestamp_ms=0))
    time.sleep(0.05)
    assert node._watchdog_state == WatchdogState.ARMED
    armed_statuses = [s for s in statuses if s.armed]
    assert len(armed_statuses) >= 1
    node.shutdown()


@patch("safety.watchdog_node._HAS_GPIO", False)
def test_watchdog_estop_on_timeout():
    """Missing heartbeat beyond timeout transitions to ESTOP_ACTIVE."""
    node, bus, statuses = _make_safety(startup_armed=False, timeout=0.2)
    node.configure()
    # Override the clamped timeout for faster testing
    node._heartbeat_timeout = 0.2
    node.activate()

    # Arm it
    bus.publish("command.heartbeat", Heartbeat(timestamp=time.monotonic(), remote_timestamp_ms=0))
    time.sleep(0.05)
    assert node._watchdog_state == WatchdogState.ARMED

    # Wait for timeout
    time.sleep(0.4)
    assert node._watchdog_state == WatchdogState.ESTOP_ACTIVE
    timeout_statuses = [s for s in statuses if s.reason == "heartbeat_timeout"]
    assert len(timeout_statuses) >= 1
    node.shutdown()


@patch("safety.watchdog_node._HAS_GPIO", False)
def test_watchdog_recovers_on_heartbeat():
    """Heartbeat after timeout transitions ESTOP_ACTIVE -> ARMED."""
    node, bus, statuses = _make_safety(startup_armed=False, timeout=0.2)
    node.configure()
    # Override the clamped timeout for faster testing
    node._heartbeat_timeout = 0.2
    node.activate()

    # Arm, then let it timeout
    bus.publish("command.heartbeat", Heartbeat(timestamp=time.monotonic(), remote_timestamp_ms=0))
    time.sleep(0.4)
    assert node._watchdog_state == WatchdogState.ESTOP_ACTIVE

    # Resume heartbeat
    bus.publish("command.heartbeat", Heartbeat(timestamp=time.monotonic(), remote_timestamp_ms=0))
    time.sleep(0.2)
    assert node._watchdog_state == WatchdogState.ARMED
    node.shutdown()


@patch("safety.watchdog_node._HAS_GPIO", False)
def test_startup_armed():
    """With startup_armed=True, safety starts armed immediately."""
    node, bus, statuses = _make_safety(startup_armed=True)
    node.configure()
    node.activate()
    time.sleep(0.05)
    assert node._watchdog_state == WatchdogState.ARMED
    assert statuses[0].armed is True
    node.shutdown()


@patch("safety.watchdog_node._HAS_GPIO", False)
def test_minimum_timeout_enforced():
    """Safety enforces minimum heartbeat_timeout of 1.0s."""
    bus = MessageBus()
    config = {
        "safety": {
            "heartbeat_timeout": 0.1,  # Too low — should be clamped to 1.0
            "estop_pin": 25,
            "startup_armed": False,
            "ramp_down_time": 0.5,
        }
    }
    node = SafetyNode("test", bus, config)
    node.configure()
    assert node._heartbeat_timeout >= 1.0
    node.shutdown()
