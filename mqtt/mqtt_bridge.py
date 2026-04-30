"""MqttBridgeNode: bridges external MQTT broker(s) to internal message bus.

Refactored in Task A3 to delegate connection management to `DualClient`
(spec §6.2). The public interface (class name, constructor signature,
lifecycle methods, internal bus topic contracts) is preserved so that
DriveNode / WatchdogNode / TelemetryNode / CameraNode do not need to change.

Internally this node now operates two MQTT links:

- ``local`` — always required, points at ``127.0.0.1`` on the bind port
  configured under ``local_mode.mqtt`` (typically the local mosquitto
  bridge).
- ``vps``   — optional, points at the existing ``mqtt.host``/``mqtt.port``
  TLS endpoint. Disabled when ``local_mode`` requests it or when the
  legacy ``mqtt:`` block is missing required fields.

Inbound: joystick / heartbeat / ping / camera signaling messages from
EITHER link are routed (after seq dedup inside DualClient) to typed
publications on the internal bus.

Outbound: ``telemetry.outbound`` / ``camera.offer.outbound`` /
``camera.ice.outbound`` / ``camera.status`` from the internal bus are
forwarded to whichever links are currently UP (DualClient handles the
fanout — see spec §7.5).

Pong echo for ``ugv/ping`` is performed immediately inside the message
callback to keep the latency-measurement path tight.
"""

import json
import time

from core.message_bus import MessageBus
from core.messages import TelemetryPayload
from core.node import BaseNode
from mqtt.dual_client import BrokerEndpoint, DualClient
from mqtt.dual_client_state import DualLinkSnapshot
from mqtt.serializer import (
    deserialize_heartbeat,
    deserialize_joystick,
    deserialize_ping,
    serialize_pong,
    serialize_telemetry,
)
from mqtt.topics import DEFAULT_TOPICS


class MqttBridgeNode(BaseNode):
    """Bridges external MQTT broker(s) to the internal message bus.

    Public interface (preserved from the legacy single-client implementation):
      - ``MqttBridgeNode(name, bus, config)``
      - ``configure()`` / ``activate()`` / ``shutdown()`` (via BaseNode)
      - ``on_configure()`` / ``on_activate()`` / ``on_shutdown()`` overrides
      - Internal bus contracts (subscriptions/publications) listed above.
    """

    def __init__(self, name: str, bus: MessageBus, config: dict) -> None:
        super().__init__(name, bus, config)
        self._client: DualClient | None = None
        self._topics: dict[str, str] = {}
        self._qos_control: int = 0
        self._qos_telemetry: int = 1
        self._local_ep: BrokerEndpoint | None = None
        self._vps_ep: BrokerEndpoint | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def on_configure(self) -> None:
        """Resolve config, build BrokerEndpoint(s) for DualClient."""
        mqtt_cfg: dict = self.config.get("mqtt", {}) or {}
        topics_cfg: dict = self.config.get("topics", {}) or {}
        local_mode_cfg: dict = self.config.get("local_mode", {}) or {}

        # Resolve topic names (user config overrides defaults)
        self._topics = {**DEFAULT_TOPICS, **topics_cfg}
        self._qos_control = mqtt_cfg.get("qos_control", 0)
        self._qos_telemetry = mqtt_cfg.get("qos_telemetry", 1)

        # ---- Local link (spec §6.2). Built only when local_mode.enabled. ----
        self._local_ep = self._build_local_endpoint(local_mode_cfg, mqtt_cfg)

        # ---- VPS link (legacy single-broker config). May be absent. -------
        self._vps_ep = self._build_vps_endpoint(mqtt_cfg)

        if self._local_ep is None and self._vps_ep is None:
            raise RuntimeError(
                "MqttBridgeNode: neither local nor VPS endpoint is configured"
            )

        # If only the VPS endpoint exists, DualClient still needs a "local"
        # role assigned because its API requires one. We promote VPS to
        # the local slot in that case so DualClient can run with vps=None.
        if self._local_ep is None:
            self.logger.info(
                "local_mode disabled; running DualClient with VPS link only"
            )
            self._local_ep = self._vps_ep
            self._vps_ep = None

        self._client = DualClient(
            local=self._local_ep,
            vps=self._vps_ep,
            on_message=self._on_dual_message,
            on_link_change=self._on_dual_link_change,
        )

    def on_activate(self) -> None:
        """Wire internal bus subscriptions, start DualClient, subscribe topics."""
        # Internal bus → MQTT
        self.bus.subscribe("telemetry.outbound", self._on_telemetry_outbound)
        self.bus.subscribe("camera.offer.outbound", self._on_camera_offer_outbound)
        self.bus.subscribe("camera.ice.outbound", self._on_camera_ice_outbound)
        self.bus.subscribe("camera.status", self._on_camera_status)

        assert self._client is not None  # set in on_configure
        self._client.start()

        # Subscribe to all inbound topics on every active link.
        self._client.subscribe(self._topics["joystick_control"], qos=self._qos_control)
        self._client.subscribe(self._topics["heartbeat"], qos=self._qos_control)
        self._client.subscribe(self._topics["latency_ping"], qos=self._qos_control)
        # Camera signaling — QoS 1 for reliable session setup.
        self._client.subscribe(self._topics["camera_cmd"], qos=1)
        self._client.subscribe(self._topics["camera_answer"], qos=1)
        self._client.subscribe(self._topics["camera_ice_rcs"], qos=1)

        self.logger.info("MqttBridgeNode active (DualClient started)")

    def on_shutdown(self) -> None:
        """Stop DualClient cleanly."""
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:
                self.logger.exception("Error stopping DualClient")
            self.logger.info("MqttBridgeNode shut down")

    # ------------------------------------------------------------------ #
    # DualClient → internal bus                                           #
    # ------------------------------------------------------------------ #

    def _on_dual_message(self, link_name: str, topic: str, payload: bytes) -> None:
        """Route inbound MQTT messages from any link to the internal bus.

        ``link_name`` is recorded in debug logs only — downstream nodes do
        not need to know which link delivered the message.
        """
        try:
            if topic == self._topics["joystick_control"]:
                cmd = deserialize_joystick(payload)
                self.logger.debug(
                    "joystick rx (%s): sa=%s ta=%s",
                    link_name, cmd.stick_axes, cmd.throttle_axes,
                )
                self.bus.publish("command.joystick", cmd)

            elif topic == self._topics["heartbeat"]:
                hb = deserialize_heartbeat(payload)
                self.bus.publish("command.heartbeat", hb)

            elif topic == self._topics["latency_ping"]:
                rx_epoch_ms = int(time.time() * 1000)
                ping = deserialize_ping(payload)
                # Immediate pong echo on MQTT — fan out via DualClient so
                # whichever links are UP will deliver it. Do NOT route
                # through the internal bus.
                pong_bytes = serialize_pong(ping, rx_epoch_ms=rx_epoch_ms)
                if self._client is not None:
                    self._client.publish(
                        self._topics["latency_pong"],
                        pong_bytes,
                        qos=0,
                    )
                # Also publish on internal bus for monitoring.
                self.bus.publish("command.ping", ping)

            # --- Camera signaling (MQTT → internal bus) ---
            elif topic == self._topics["camera_cmd"]:
                self.bus.publish("camera.cmd", json.loads(payload))

            elif topic == self._topics["camera_answer"]:
                self.bus.publish("camera.answer", json.loads(payload))

            elif topic == self._topics["camera_ice_rcs"]:
                self.bus.publish("camera.ice.inbound", json.loads(payload))

        except Exception as exc:
            self.logger.error(
                "Error processing MQTT message on '%s' (link=%s): %s",
                topic, link_name, exc,
            )

    def _on_dual_link_change(self, snapshot: DualLinkSnapshot) -> None:
        """Publish a copy of the link-state snapshot onto the internal bus.

        Consumers (TelemetryNode in A8, WatchdogNode in A9) subscribe to
        ``mqtt.link_state``; they do not exist yet but the topic is wired
        now so they can simply subscribe later without further changes.
        """
        self.logger.info(
            "MQTT link state changed: local=%s vps=%s",
            snapshot.local.value, snapshot.vps.value,
        )
        self.bus.publish("mqtt.link_state", snapshot)

    # ------------------------------------------------------------------ #
    # Internal bus → DualClient                                           #
    # ------------------------------------------------------------------ #

    def _on_telemetry_outbound(self, telem: TelemetryPayload) -> None:
        """Forward telemetry from internal bus to MQTT broker(s)."""
        if self._client is None:
            return
        payload = serialize_telemetry(telem)
        self._client.publish(
            self._topics["telemetry"],
            payload,
            qos=self._qos_telemetry,
        )

    def _on_camera_offer_outbound(self, msg: dict) -> None:
        """Forward SDP offer from CameraNode to MQTT broker(s)."""
        if self._client is None:
            return
        payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        self._client.publish(self._topics["camera_offer"], payload, qos=1)
        self.logger.info("SDP offer published to MQTT")

    def _on_camera_ice_outbound(self, msg: dict) -> None:
        """Forward ICE candidate from CameraNode to MQTT broker(s)."""
        if self._client is None:
            return
        payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        self._client.publish(self._topics["camera_ice_ugv"], payload, qos=1)

    def _on_camera_status(self, msg: dict) -> None:
        """Forward camera status from CameraNode to MQTT broker(s)."""
        if self._client is None:
            return
        payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        self._client.publish(self._topics["camera_status"], payload, qos=1)
        self.logger.info("Camera status published: %s", msg.get("status", "?"))

    # ------------------------------------------------------------------ #
    # Endpoint construction                                               #
    # ------------------------------------------------------------------ #

    def _build_local_endpoint(
        self, local_mode_cfg: dict, mqtt_cfg: dict
    ) -> BrokerEndpoint | None:
        """Build the local-broker endpoint, or None if local_mode is disabled.

        Spec §6.2: the local link points at 127.0.0.1 on
        ``local_mode.mqtt.bind_port`` (default 8883), authenticating with
        the same credentials used for the VPS broker (the local mosquitto
        bridge re-uses the unified ACL — see Task A1).
        """
        if not local_mode_cfg.get("enabled", False):
            return None

        local_mqtt = local_mode_cfg.get("mqtt", {}) or {}
        bind_port = int(local_mqtt.get("bind_port", 8883))
        ca_path = local_mqtt.get("ca_path", "") or ""

        username = mqtt_cfg.get("username", "")
        password = mqtt_cfg.get("password", "")
        keepalive = int(mqtt_cfg.get("keepalive", 30))

        return BrokerEndpoint(
            name="local",
            host="127.0.0.1",
            port=bind_port,
            ca_path=ca_path,
            username=username,
            password=password,
            keepalive=keepalive,
        )

    def _build_vps_endpoint(self, mqtt_cfg: dict) -> BrokerEndpoint | None:
        """Build the VPS-broker endpoint from the legacy ``mqtt:`` block.

        Returns None when the legacy block has been explicitly disabled
        (``mqtt.enabled: false``) or lacks the credentials/CA needed to
        establish a TLS connection.
        """
        if mqtt_cfg.get("enabled", True) is False:
            return None

        host = mqtt_cfg.get("host", "")
        port = int(mqtt_cfg.get("port", 8883))
        username = mqtt_cfg.get("username", "")
        password = mqtt_cfg.get("password", "")
        keepalive = int(mqtt_cfg.get("keepalive", 30))

        tls_cfg = mqtt_cfg.get("tls", {}) or {}
        ca_path = tls_cfg.get("ca_certs", "") or ""

        # The VPS link mandates TLS + credentials; if the operator has not
        # filled them in, treat it as "no VPS link" instead of crashing on
        # connect. The local link will still operate.
        if not host or not username or not ca_path:
            self.logger.info(
                "VPS endpoint not fully configured "
                "(host=%r username=%r ca_certs=%r); skipping VPS link",
                host, username, ca_path,
            )
            return None

        return BrokerEndpoint(
            name="vps",
            host=host,
            port=port,
            ca_path=ca_path,
            username=username,
            password=password,
            keepalive=keepalive,
        )
