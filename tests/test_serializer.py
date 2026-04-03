"""Tests for MQTT payload serialization — must match Phase 1 protocol exactly."""

import json
from core.messages import TelemetryPayload
from mqtt.serializer import (
    deserialize_joystick,
    deserialize_heartbeat,
    deserialize_ping,
    serialize_pong,
    serialize_telemetry,
)


def test_deserialize_joystick_matches_phase1_format():
    """Phase 1 sends this exact format — we MUST parse it correctly."""
    payload = (
        b'{"t":1712099123000,"sa":{"0":0.1234,"1":-0.5678},'
        b'"ta":{"2":0.8,"5":0.3},"sb":{"288":true},"tb":{},'
        b'"sh":{"H1":[0,1]},"th":{"CS":[-1,0]}}'
    )
    cmd = deserialize_joystick(payload)
    assert cmd.stick_axes["0"] == 0.1234
    assert cmd.stick_axes["1"] == -0.5678
    assert cmd.throttle_axes["2"] == 0.8
    assert cmd.stick_buttons["288"] is True
    assert cmd.stick_hats["H1"] == [0, 1]
    assert cmd.throttle_hats["CS"] == [-1, 0]
    assert cmd.remote_timestamp_ms == 1712099123000


def test_deserialize_joystick_empty_fields():
    """Handle minimal payload with no axes/buttons."""
    payload = b'{"t":0}'
    cmd = deserialize_joystick(payload)
    assert cmd.stick_axes == {}
    assert cmd.stick_buttons == {}
    assert cmd.remote_timestamp_ms == 0


def test_deserialize_heartbeat():
    payload = b'{"t":1712099123000}'
    hb = deserialize_heartbeat(payload)
    assert hb.remote_timestamp_ms == 1712099123000
    assert hb.timestamp > 0


def test_deserialize_ping():
    payload = b'{"t":1712099123000,"seq":42}'
    ping = deserialize_ping(payload)
    assert ping.remote_timestamp_ms == 1712099123000
    assert ping.seq == 42


def test_pong_echoes_ping_exactly():
    """Pong must echo the same t and seq values for RTT calculation."""
    ping_payload = b'{"t":1712099123000,"seq":42}'
    ping = deserialize_ping(ping_payload)
    pong = serialize_pong(ping)
    pong_data = json.loads(pong)
    assert pong_data["t"] == 1712099123000
    assert pong_data["seq"] == 42


def test_serialize_telemetry_matches_phase1_expectations():
    """Phase 1 expects these exact keys."""
    telem = TelemetryPayload(
        timestamp=0,
        speed=2.5,
        battery_voltage=12.3,
        battery_percent=85.0,
        motor_temp_left=45.0,
        motor_temp_right=42.0,
        signal_strength=-68,
        gps_lat=40.1234,
        gps_lon=-105.5678,
    )
    data = json.loads(serialize_telemetry(telem))
    assert data["speed"] == 2.5
    assert data["bat_v"] == 12.3
    assert data["bat_pct"] == 85.0
    assert data["temp_l"] == 45.0
    assert data["temp_r"] == 42.0
    assert data["rssi"] == -68
    assert data["lat"] == 40.1234
    assert data["lon"] == -105.5678


def test_serialize_telemetry_includes_custom():
    telem = TelemetryPayload(
        timestamp=0, speed=0, battery_voltage=0, battery_percent=0,
        motor_temp_left=0, motor_temp_right=0, signal_strength=0,
        gps_lat=0, gps_lon=0, custom={"armed": True, "hb_age": 150.0},
    )
    data = json.loads(serialize_telemetry(telem))
    assert data["armed"] is True
    assert data["hb_age"] == 150.0
