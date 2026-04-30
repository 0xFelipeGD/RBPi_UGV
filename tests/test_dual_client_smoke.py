"""Smoke test for DualClient lifecycle (spec §14.1).

Verifies: instantiation, link state transitions, message dedup. ~50 lines.
Does NOT start real network connections.
"""
from unittest.mock import MagicMock

from mqtt.dual_client import BrokerEndpoint, DualClient
from mqtt.dual_client_state import LinkState


def _ep(name: str = "local") -> BrokerEndpoint:
    return BrokerEndpoint(
        name=name, host="127.0.0.1", port=1883, ca_path="/dev/null",
        username="u", password="p"
    )


def test_dual_client_constructs_and_can_be_stopped_clean():
    on_msg = MagicMock()
    on_link = MagicMock()
    client = DualClient(_ep("local"), _ep("vps"), on_msg, on_link)
    snap = client.snapshot()
    assert snap.local == LinkState.DOWN
    assert snap.vps == LinkState.DOWN
    client.stop()  # should be a no-op when not started


def test_dual_client_dedup_by_seq_drops_old_joystick():
    on_msg = MagicMock()
    on_link = MagicMock()
    client = DualClient(_ep("local"), None, on_msg, on_link)
    cb = client._make_on_message(_ep("local"))

    msg_old = MagicMock()
    msg_old.topic = "ugv/joystick"
    msg_old.payload = b'{"seq": 5, "sa": 0.1}'

    msg_new = MagicMock()
    msg_new.topic = "ugv/joystick"
    msg_new.payload = b'{"seq": 6, "sa": 0.2}'

    msg_dup = MagicMock()
    msg_dup.topic = "ugv/joystick"
    msg_dup.payload = b'{"seq": 5, "sa": 0.3}'  # older — must be dropped

    cb(None, None, msg_old)
    cb(None, None, msg_new)
    cb(None, None, msg_dup)
    assert on_msg.call_count == 2  # the duplicate was dropped


def test_dual_client_link_state_transitions_call_back():
    on_msg = MagicMock()
    on_link = MagicMock()
    client = DualClient(_ep("local"), None, on_msg, on_link)
    client._update_link_state("local", LinkState.UP)
    assert on_link.called
    snap = on_link.call_args[0][0]
    assert snap.local == LinkState.UP
    assert snap.vps == LinkState.DOWN
