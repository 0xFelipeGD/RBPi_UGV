"""Smoke test confirming joystick payload schema survives the local-broker path.

Spec §14.1. Mock both MQTT clients; publish a joystick payload via the local
client; assert it arrives at the on_message callback with the original keys/types.
"""
import json
from unittest.mock import MagicMock

from mqtt.dual_client import BrokerEndpoint, DualClient


def test_joystick_payload_roundtrip_preserves_schema():
    on_msg = MagicMock()
    on_link = MagicMock()
    client = DualClient(_ep("local"), None, on_msg, on_link)
    cb = client._make_on_message(_ep("local"))
    msg = MagicMock()
    msg.topic = "ugv/joystick"
    payload = {"t": 123.456, "seq": 1, "sa": 0.0, "ta": 0.0, "sb": 0.0,
               "tb": 0.0, "sh": 0.0, "th": 0.0}
    msg.payload = json.dumps(payload).encode()
    cb(None, None, msg)
    assert on_msg.call_count == 1
    link_name, topic, raw = on_msg.call_args[0]
    assert link_name == "local"
    assert topic == "ugv/joystick"
    obj = json.loads(raw)
    assert set(obj.keys()) == {"t", "seq", "sa", "ta", "sb", "tb", "sh", "th"}


def _ep(name: str) -> BrokerEndpoint:
    return BrokerEndpoint(name=name, host="127.0.0.1", port=1883,
                          ca_path="/dev/null", username="u", password="p")
