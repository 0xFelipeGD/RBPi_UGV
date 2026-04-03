"""MQTT topic string constants matching INTERFACE_CONTRACT.md."""

DEFAULT_TOPICS: dict[str, str] = {
    "joystick_control": "ugv/joystick",
    "heartbeat":        "ugv/heartbeat",
    "telemetry":        "ugv/telemetry",
    "latency_ping":     "ugv/ping",
    "latency_pong":     "ugv/pong",
    "camera_cmd":       "ugv/camera/cmd",
    "camera_offer":     "ugv/camera/offer",
    "camera_answer":    "ugv/camera/answer",
    "camera_ice_ugv":   "ugv/camera/ice/ugv",
    "camera_ice_rcs":   "ugv/camera/ice/rcs",
    "camera_status":    "ugv/camera/status",
}
