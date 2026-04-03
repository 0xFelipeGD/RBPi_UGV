"""MQTT topic string constants matching Phase 1 protocol."""

DEFAULT_TOPICS: dict[str, str] = {
    "joystick_control": "ugv/joystick",
    "heartbeat":        "ugv/heartbeat",
    "telemetry":        "ugv/telemetry",
    "latency_ping":     "ugv/ping",
    "latency_pong":     "ugv/pong",
}
