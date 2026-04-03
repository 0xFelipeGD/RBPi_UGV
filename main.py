#!/usr/bin/env python3
"""
UGV On-Board Software — Phase 3
Raspberry Pi headless daemon for teleoperated UGV.

Receives MQTT commands from operator, drives motors, reads sensors, publishes telemetry.
"""
import sys
import os
import signal
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.config_loader import load_config
from utils.logging_setup import setup_logging
from core.message_bus import MessageBus
from core.state_manager import StateManager
from core.launcher import Launcher
from mqtt.mqtt_bridge import MqttBridgeNode
from drive.drive_node import DriveNode
from safety.watchdog_node import SafetyNode
from sensors.sensor_node import SensorNode
from telemetry.telemetry_node import TelemetryNode


def main() -> int:
    """Entry point for the UGV on-board software."""
    # 1. Load config
    config = load_config()

    # 2. Setup logging
    setup_logging(config)

    import logging
    logger = logging.getLogger("ugv.main")
    logger.info("UGV On-Board Software starting...")

    # 3. Create shared infrastructure
    bus = MessageBus()
    state = StateManager(bus)

    # 4. Create nodes (order matters for dependencies)
    safety_node = SafetyNode("safety", bus, config)
    mqtt_node = MqttBridgeNode("mqtt", bus, config)
    drive_node = DriveNode("drive", bus, config)
    sensor_node = SensorNode("sensors", bus, config)
    telemetry_node = TelemetryNode("telemetry", bus, config)

    # 5. Register with launcher (shutdown runs in reverse order)
    launcher = Launcher()
    launcher.register(safety_node)      # First to start, last to stop
    launcher.register(mqtt_node)
    launcher.register(sensor_node)
    launcher.register(telemetry_node)
    launcher.register(drive_node)       # Last to start (needs safety + MQTT first)

    # 6. Start all nodes
    try:
        launcher.start_all()
    except Exception as e:
        logger.fatal(f"Failed to start: {e}")
        launcher.shutdown_all()
        return 1

    logger.info("All nodes active. Waiting for operator heartbeat...")

    # 7. Run forever (headless daemon — no GUI event loop)
    stop_event = threading.Event()

    def on_signal(signum, frame):
        logger.info(f"Signal {signum} received, shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down all nodes...")
        launcher.shutdown_all()
        logger.info("UGV software stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
