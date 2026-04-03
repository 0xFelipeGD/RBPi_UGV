"""MqttBridgeNode: bridges external MQTT broker to internal message bus."""

import ssl
import time
from typing import Any

import paho.mqtt.client as mqtt

from core.node import BaseNode
from core.message_bus import MessageBus
from core.messages import TelemetryPayload
from mqtt.serializer import (
    deserialize_joystick,
    deserialize_heartbeat,
    deserialize_ping,
    serialize_pong,
    serialize_telemetry,
)
from mqtt.topics import DEFAULT_TOPICS


class MqttBridgeNode(BaseNode):
    """Bridges the external MQTT broker to the internal message bus.

    Inbound: subscribes to joystick, heartbeat, ping on MQTT -> publishes typed
    messages on the internal bus.
    Outbound: subscribes to telemetry.outbound on internal bus -> publishes to MQTT.
    Pong echo is immediate in the MQTT callback for minimal latency.
    """

    def __init__(self, name: str, bus: MessageBus, config: dict) -> None:
        super().__init__(name, bus, config)
        self._client: mqtt.Client | None = None
        self._topics: dict[str, str] = {}
        self._qos_control: int = 0
        self._qos_telemetry: int = 1

    def on_configure(self) -> None:
        """Create and configure the paho MQTT client."""
        mqtt_cfg = self.config.get("mqtt", {})
        topics_cfg = self.config.get("topics", {})

        # Resolve topic names (user config overrides defaults)
        self._topics = {**DEFAULT_TOPICS, **topics_cfg}
        self._qos_control = mqtt_cfg.get("qos_control", 0)
        self._qos_telemetry = mqtt_cfg.get("qos_telemetry", 1)

        # Create paho client
        client_id = mqtt_cfg.get("client_id", "ugv-onboard")
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        # Auth
        username = mqtt_cfg.get("username", "")
        password = mqtt_cfg.get("password", "")
        if username:
            self._client.username_pw_set(username, password)

        # TLS
        tls_cfg = mqtt_cfg.get("tls", {})
        if tls_cfg.get("enabled", False):
            ca_certs = tls_cfg.get("ca_certs", "") or None
            certfile = tls_cfg.get("certfile", "") or None
            keyfile = tls_cfg.get("keyfile", "") or None
            self._client.tls_set(
                ca_certs=ca_certs,
                certfile=certfile,
                keyfile=keyfile,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            if not ca_certs:
                self._client.tls_insecure_set(True)

        # Paho callbacks
        self._client.on_connect = self._on_mqtt_connect
        self._client.on_disconnect = self._on_mqtt_disconnect
        self._client.on_message = self._on_mqtt_message

        # Store connection params
        self._host = mqtt_cfg.get("host", "localhost")
        self._port = mqtt_cfg.get("port", 8883)
        self._keepalive = mqtt_cfg.get("keepalive", 30)

    def on_activate(self) -> None:
        """Connect to broker and subscribe to internal telemetry topic."""
        self.bus.subscribe("telemetry.outbound", self._on_telemetry_outbound)
        self._client.connect_async(self._host, self._port, self._keepalive)
        self._client.loop_start()
        self.logger.info(f"Connecting to MQTT broker {self._host}:{self._port}")

    def on_shutdown(self) -> None:
        """Disconnect from broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self.logger.info("MQTT client disconnected")

    def _on_mqtt_connect(
        self, client: mqtt.Client, userdata: Any, flags: Any, rc: Any, properties: Any = None
    ) -> None:
        """Called when connected to broker. Subscribe to inbound topics."""
        self.logger.info(f"Connected to MQTT broker (rc={rc})")
        client.subscribe(self._topics["joystick_control"], qos=self._qos_control)
        client.subscribe(self._topics["heartbeat"], qos=self._qos_control)
        client.subscribe(self._topics["latency_ping"], qos=self._qos_control)

    def _on_mqtt_disconnect(
        self, client: mqtt.Client, userdata: Any, flags: Any = None, rc: Any = None, properties: Any = None
    ) -> None:
        """Called on disconnect. Paho handles auto-reconnect."""
        self.logger.warning(f"MQTT disconnected (rc={rc}). Auto-reconnect active.")

    def _on_mqtt_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        """Route inbound MQTT messages to the internal bus."""
        try:
            topic = msg.topic
            payload = msg.payload

            if topic == self._topics["joystick_control"]:
                cmd = deserialize_joystick(payload)
                self.bus.publish("command.joystick", cmd)

            elif topic == self._topics["heartbeat"]:
                hb = deserialize_heartbeat(payload)
                self.bus.publish("command.heartbeat", hb)

            elif topic == self._topics["latency_ping"]:
                ping = deserialize_ping(payload)
                # Immediate pong echo on MQTT — do NOT route through internal bus
                pong_bytes = serialize_pong(ping)
                client.publish(
                    self._topics["latency_pong"],
                    pong_bytes,
                    qos=0,
                )
                # Also publish on internal bus for monitoring
                self.bus.publish("command.ping", ping)

        except Exception as e:
            self.logger.error(f"Error processing MQTT message on '{msg.topic}': {e}")

    def _on_telemetry_outbound(self, telem: TelemetryPayload) -> None:
        """Forward telemetry from internal bus to MQTT broker."""
        if self._client and self._client.is_connected():
            payload = serialize_telemetry(telem)
            self._client.publish(
                self._topics["telemetry"],
                payload,
                qos=self._qos_telemetry,
            )
