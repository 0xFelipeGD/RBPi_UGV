"""JSON serialization/deserialization matching Phase 1 MQTT protocol exactly."""

import json
import time
from core.messages import JoystickCommand, Heartbeat, PingRequest, TelemetryPayload


def deserialize_joystick(payload: bytes) -> JoystickCommand:
    """Parse joystick JSON from Phase 1 into JoystickCommand.

    Phase 1 sends compact JSON with these keys:
      t  = epoch ms timestamp
      sa = stick axes        {str(evdev_code): float}
      ta = throttle axes     {str(evdev_code): float}
      sb = stick buttons     {str(evdev_code): true}   (only pressed)
      tb = throttle buttons  {str(evdev_code): true}   (only pressed)
      sh = stick hats        {hat_name: [x, y]}
      th = throttle hats     {hat_name: [x, y]}
    """
    data: dict = json.loads(payload)
    return JoystickCommand(
        timestamp=time.monotonic(),
        remote_timestamp_ms=int(data.get("t", 0)),
        stick_axes=data.get("sa", {}),
        throttle_axes=data.get("ta", {}),
        stick_buttons=data.get("sb", {}),
        throttle_buttons=data.get("tb", {}),
        stick_hats=data.get("sh", {}),
        throttle_hats=data.get("th", {}),
    )


def deserialize_heartbeat(payload: bytes) -> Heartbeat:
    """Parse heartbeat JSON: {"t": epoch_ms}"""
    data: dict = json.loads(payload)
    return Heartbeat(
        timestamp=time.monotonic(),
        remote_timestamp_ms=int(data.get("t", 0)),
    )


def deserialize_ping(payload: bytes) -> PingRequest:
    """Parse latency ping: {"t": epoch_ms, "seq": int}"""
    data: dict = json.loads(payload)
    return PingRequest(
        timestamp=time.monotonic(),
        remote_timestamp_ms=int(data.get("t", 0)),
        seq=int(data.get("seq", 0)),
    )


def serialize_pong(ping: PingRequest) -> bytes:
    """Echo ping back as pong — same t and seq for RTT calculation."""
    return json.dumps(
        {"t": ping.remote_timestamp_ms, "seq": ping.seq},
        separators=(",", ":"),
    ).encode("utf-8")


def serialize_telemetry(telem: TelemetryPayload) -> bytes:
    """Serialize telemetry to JSON for Phase 1.

    Phase 1 expects these keys:
      speed, bat_v, bat_pct, temp_l, temp_r, rssi, lat, lon
    Any extra keys in telem.custom are included as-is.
    """
    payload: dict = {
        "speed":   round(telem.speed, 2),
        "bat_v":   round(telem.battery_voltage, 2),
        "bat_pct": round(telem.battery_percent, 1),
        "temp_l":  round(telem.motor_temp_left, 1),
        "temp_r":  round(telem.motor_temp_right, 1),
        "rssi":    telem.signal_strength,
        "lat":     round(telem.gps_lat, 6),
        "lon":     round(telem.gps_lon, 6),
    }
    payload.update(telem.custom)
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")
