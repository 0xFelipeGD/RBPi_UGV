"""Dual MQTT client: maintains two simultaneous connections (local + VPS).

See spec §6.2 / §7.5. Exposes the same public interface as the legacy
single-client `mqtt_bridge.py` so DriveNode/WatchdogNode/etc. don't change.

Decisions encoded:
- Local link to `localhost:8883` is always required; VPS link is best-effort.
- Heartbeat from EITHER link is sufficient for watchdog (spec §13).
- Outgoing publishes go to all currently-CONNECTED clients.
- Joystick dedup by `seq` field across links.
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt

from mqtt.dual_client_state import DualLinkSnapshot, LinkState

logger = logging.getLogger(__name__)


@dataclass
class BrokerEndpoint:
    """Connection parameters for one MQTT link."""
    name: str           # "local" or "vps"
    host: str
    port: int
    ca_path: str
    username: str
    password: str
    keepalive: int = 30


class DualClient:
    """Two paho-mqtt clients orchestrated as one logical bridge."""

    # Reconnect backoff schedule (seconds). Spec §7.5.
    RECONNECT_BACKOFF = [1, 2, 4, 8, 16]

    def __init__(self,
                 local: BrokerEndpoint,
                 vps: Optional[BrokerEndpoint],
                 on_message: Callable[[str, str, bytes], None],
                 on_link_change: Callable[[DualLinkSnapshot], None]):
        """Construct with both endpoints and event callbacks."""
        self._local_ep = local
        self._vps_ep = vps
        self._on_message = on_message
        self._on_link_change = on_link_change

        self._snapshot = DualLinkSnapshot()
        self._lock = threading.Lock()
        self._last_seq_by_topic: dict[str, int] = {}

        self._local_client: Optional[mqtt.Client] = None
        self._vps_client: Optional[mqtt.Client] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Connect both clients (if vps is configured) and start their loops."""
        self._local_client = self._build_client(self._local_ep)
        self._connect_with_retry(self._local_client, self._local_ep)
        self._local_client.loop_start()

        if self._vps_ep is not None:
            self._vps_client = self._build_client(self._vps_ep)
            self._connect_with_retry(self._vps_client, self._vps_ep)
            self._vps_client.loop_start()

    def stop(self) -> None:
        """Cleanly disconnect both clients."""
        self._stop_event.set()
        for c in (self._local_client, self._vps_client):
            if c is None:
                continue
            try:
                c.loop_stop()
                c.disconnect()
            except Exception:
                logger.exception("error during DualClient.stop")

    def publish(self, topic: str, payload: bytes | str, qos: int = 0,
                retain: bool = False) -> None:
        """Publish to all CONNECTED clients (spec §7.5 outgoing rule)."""
        with self._lock:
            snap = self._snapshot
        targets = []
        if snap.local == LinkState.UP and self._local_client is not None:
            targets.append(self._local_client)
        if snap.vps == LinkState.UP and self._vps_client is not None:
            targets.append(self._vps_client)
        for c in targets:
            try:
                c.publish(topic, payload=payload, qos=qos, retain=retain)
            except Exception:
                logger.exception("publish failed on one link; continuing")

    def subscribe(self, topic: str, qos: int = 0) -> None:
        """Subscribe both clients to the same topic."""
        for c in (self._local_client, self._vps_client):
            if c is not None:
                c.subscribe(topic, qos=qos)

    # ---- internals ----

    def _build_client(self, ep: BrokerEndpoint) -> mqtt.Client:
        """Build a configured paho client. TLS via ep.ca_path."""
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"ugv-{ep.name}",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(ep.username, ep.password)
        client.tls_set(ca_certs=ep.ca_path, tls_version=ssl.PROTOCOL_TLSv1_2)
        client.on_connect = self._make_on_connect(ep)
        client.on_disconnect = self._make_on_disconnect(ep)
        client.on_message = self._make_on_message(ep)
        return client

    def _connect_with_retry(self, client: mqtt.Client, ep: BrokerEndpoint) -> None:
        """Synchronous initial connect with exponential backoff."""
        for delay in self.RECONNECT_BACKOFF:
            try:
                client.connect(ep.host, ep.port, keepalive=ep.keepalive)
                return
            except Exception as e:
                logger.warning("[%s] initial connect failed: %s; retry in %ss",
                               ep.name, e, delay)
                if self._stop_event.wait(delay):
                    return
        # Final attempt — let it raise if it still fails so caller sees it.
        client.connect(ep.host, ep.port, keepalive=ep.keepalive)

    def _make_on_connect(self, ep: BrokerEndpoint):
        # paho-mqtt 2.x VERSION2 callback signature:
        # (client, userdata, flags, reason_code, properties)
        def cb(client: mqtt.Client, userdata: Any, flags: Any,
               reason_code: Any, properties: Any = None) -> None:
            rc_ok = (reason_code == 0) if isinstance(reason_code, int) \
                else (getattr(reason_code, "is_failure", True) is False)
            if rc_ok:
                logger.info("[%s] connected", ep.name)
                self._update_link_state(ep.name, LinkState.UP)
            else:
                logger.error("[%s] connect failed rc=%s", ep.name, reason_code)
                self._update_link_state(ep.name, LinkState.DEGRADED)
        return cb

    def _make_on_disconnect(self, ep: BrokerEndpoint):
        # paho-mqtt 2.x VERSION2 callback signature:
        # (client, userdata, disconnect_flags, reason_code, properties)
        def cb(client: mqtt.Client, userdata: Any, disconnect_flags: Any = None,
               reason_code: Any = None, properties: Any = None) -> None:
            logger.warning("[%s] disconnected rc=%s", ep.name, reason_code)
            rc_clean = (reason_code == 0) if isinstance(reason_code, int) \
                else (getattr(reason_code, "is_failure", True) is False)
            self._update_link_state(
                ep.name,
                LinkState.DOWN if rc_clean else LinkState.DEGRADED,
            )
        return cb

    def _make_on_message(self, ep: BrokerEndpoint):
        def cb(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
            # Joystick dedup by `seq` (spec §7.5 incoming rule).
            if msg.topic.endswith("/joystick"):
                seq = self._extract_seq(msg.payload)
                if seq is not None:
                    with self._lock:
                        last = self._last_seq_by_topic.get(msg.topic, -1)
                        if seq <= last:
                            return  # drop duplicate / older
                        self._last_seq_by_topic[msg.topic] = seq
            self._on_message(ep.name, msg.topic, msg.payload)
        return cb

    @staticmethod
    def _extract_seq(payload: bytes) -> Optional[int]:
        """Pull `seq` out of a JSON joystick payload. None if not present."""
        try:
            obj = json.loads(payload)
            return int(obj.get("seq")) if "seq" in obj else None
        except Exception:
            return None

    def _update_link_state(self, link_name: str, new_state: LinkState) -> None:
        with self._lock:
            if link_name == "local":
                self._snapshot.local = new_state
            elif link_name == "vps":
                self._snapshot.vps = new_state
            snap_copy = DualLinkSnapshot(local=self._snapshot.local,
                                         vps=self._snapshot.vps)
        self._on_link_change(snap_copy)

    def snapshot(self) -> DualLinkSnapshot:
        with self._lock:
            return DualLinkSnapshot(local=self._snapshot.local,
                                    vps=self._snapshot.vps)
