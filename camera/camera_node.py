"""CameraNode: manages WebRTC video streaming from Pi Camera via aiortc."""

import asyncio
import json
import logging
import threading
from typing import Any

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer
from aiortc.sdp import candidate_from_sdp

from core.node import BaseNode
from core.message_bus import MessageBus
from camera.pi_camera_track import PiCameraTrack


class CameraNode(BaseNode):
    """WebRTC video streaming node.

    Manages a Pi Camera capture and an aiortc RTCPeerConnection.
    Signaling (SDP offer/answer, ICE candidates) is routed through the
    internal MessageBus -- the MqttBridgeNode handles MQTT transport.

    Internal bus topics consumed:
        camera.cmd          — {"action": "start"|"stop"}
        camera.answer       — {"type": "answer", "sdp": "..."}
        camera.ice.inbound  — {"candidate": "...", "sdpMid": "...", "sdpMLineIndex": int}

    Internal bus topics published:
        camera.offer.outbound  — {"type": "offer", "sdp": "..."}
        camera.ice.outbound    — {"candidate": "...", "sdpMid": "...", "sdpMLineIndex": int}
        camera.status          — {"status": "...", ...}
    """

    def __init__(self, name: str, bus: MessageBus, config: dict) -> None:
        super().__init__(name, bus, config)

        # Camera config (populated in on_configure)
        self._resolution: tuple[int, int] = (1280, 720)
        self._framerate: int = 30
        self._stun_servers: list[str] = []
        self._turn_servers: list[dict] = []

        # WebRTC state
        self._pc: RTCPeerConnection | None = None
        self._track: PiCameraTrack | None = None

        # Asyncio event loop running in a daemon thread
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_configure(self) -> None:
        """Load camera configuration from the config dict."""
        cam_cfg = self.config.get("camera", {})
        res = cam_cfg.get("resolution", [1280, 720])
        self._resolution = (int(res[0]), int(res[1]))
        self._framerate = int(cam_cfg.get("framerate", 30))
        self._stun_servers = cam_cfg.get("stun_servers", [])
        self._turn_servers = cam_cfg.get("turn_servers", [])
        self.logger.info(
            f"Camera configured: {self._resolution[0]}x{self._resolution[1]}"
            f"@{self._framerate}fps, STUN={len(self._stun_servers)}, TURN={len(self._turn_servers)}"
        )

    def on_activate(self) -> None:
        """Subscribe to internal bus topics and start the asyncio event loop thread."""
        self.bus.subscribe("camera.cmd", self._on_camera_cmd)
        self.bus.subscribe("camera.answer", self._on_camera_answer)
        self.bus.subscribe("camera.ice.inbound", self._on_ice_inbound)

        # Start a dedicated asyncio event loop in a daemon thread (aiortc requires it)
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, daemon=True, name="camera-asyncio"
        )
        self._loop_thread.start()
        self.logger.info("Camera node active — waiting for start command")

    def on_shutdown(self) -> None:
        """Clean up: close peer connection, stop camera, stop asyncio loop."""
        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._async_cleanup(), self._loop)
            try:
                future.result(timeout=5.0)
            except Exception as exc:
                self.logger.warning(f"Cleanup error: {exc}")
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=3.0)
        self.logger.info("Camera node shut down")

    # ------------------------------------------------------------------
    # Asyncio event loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Run the asyncio event loop (blocking — runs in daemon thread)."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _schedule(self, coro) -> None:
        """Schedule an async coroutine on the camera event loop from any thread."""
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ------------------------------------------------------------------
    # Internal bus callbacks (called from paho / bus threads)
    # ------------------------------------------------------------------

    def _on_camera_cmd(self, msg: dict) -> None:
        """Handle camera.cmd messages: {"action": "start"|"stop"}."""
        action = msg.get("action", "")
        if action == "start":
            self.logger.info("Camera start command received")
            self._schedule(self._async_start())
        elif action == "stop":
            self.logger.info("Camera stop command received")
            self._schedule(self._async_stop())
        else:
            self.logger.warning(f"Unknown camera action: {action}")

    def _on_camera_answer(self, msg: dict) -> None:
        """Handle camera.answer messages: {"type": "answer", "sdp": "..."}."""
        self.logger.info("Received SDP answer from RCS")
        self._schedule(self._async_set_answer(msg))

    def _on_ice_inbound(self, msg: dict) -> None:
        """Handle camera.ice.inbound messages (ICE candidates from RCS)."""
        self.logger.debug(f"Received ICE candidate from RCS: {msg.get('candidate', '')[:40]}...")
        self._schedule(self._async_add_ice(msg))

    # ------------------------------------------------------------------
    # Async operations (run on the camera event loop)
    # ------------------------------------------------------------------

    async def _async_start(self) -> None:
        """Start camera capture and create the WebRTC offer."""
        try:
            # Publish starting status
            self.bus.publish("camera.status", {"status": "starting"})

            # Clean up any existing session
            await self._async_cleanup()

            # Build RTCConfiguration with STUN servers
            ice_servers = []
            for url in self._stun_servers:
                ice_servers.append(RTCIceServer(urls=[url]))

            # Add TURN servers (relay for symmetric NAT traversal)
            for turn in self._turn_servers:
                ice_servers.append(RTCIceServer(
                    urls=[turn["url"]],
                    username=turn.get("username", ""),
                    credential=turn.get("credential", ""),
                ))

            rtc_config = RTCConfiguration(iceServers=ice_servers)

            # Create peer connection
            self._pc = RTCPeerConnection(configuration=rtc_config)
            self._setup_pc_callbacks()

            # Create and start camera track
            self._track = PiCameraTrack(
                width=self._resolution[0],
                height=self._resolution[1],
                framerate=self._framerate,
            )
            self._track.start_camera()

            # Add video track to peer connection
            self._pc.addTrack(self._track)

            # Create and set local SDP offer
            offer = await self._pc.createOffer()
            await self._pc.setLocalDescription(offer)

            # Publish the offer on the internal bus
            offer_dict = {
                "type": self._pc.localDescription.type,
                "sdp": self._pc.localDescription.sdp,
            }
            self.bus.publish("camera.offer.outbound", offer_dict)
            self.logger.info("SDP offer created and published")

        except Exception as exc:
            self.logger.error(f"Failed to start camera: {exc}")
            self.bus.publish("camera.status", {"status": "error", "error": str(exc)})

    async def _async_stop(self) -> None:
        """Stop camera and close WebRTC connection."""
        await self._async_cleanup()
        self.bus.publish("camera.status", {"status": "stopped"})
        self.logger.info("Camera stopped by command")

    async def _async_set_answer(self, msg: dict) -> None:
        """Set the remote SDP answer on the peer connection."""
        if self._pc is None:
            self.logger.warning("Received SDP answer but no peer connection exists")
            return
        try:
            answer = RTCSessionDescription(sdp=msg["sdp"], type="answer")
            await self._pc.setRemoteDescription(answer)
            self.logger.info("Remote SDP answer applied")
        except Exception as exc:
            self.logger.error(f"Failed to set SDP answer: {exc}")
            self.bus.publish("camera.status", {"status": "error", "error": str(exc)})

    async def _async_add_ice(self, msg: dict) -> None:
        """Add a remote ICE candidate to the peer connection."""
        if self._pc is None:
            self.logger.warning("Received ICE candidate but no peer connection exists")
            return
        try:
            candidate_str = msg.get("candidate", "")
            if not candidate_str:
                # Empty candidate signals end-of-candidates
                self.logger.debug("Received end-of-candidates signal")
                return
            # Parse the SDP candidate string into an RTCIceCandidate
            sdp_str = candidate_str
            if sdp_str.startswith("candidate:"):
                sdp_str = sdp_str[len("candidate:"):]
            candidate = candidate_from_sdp(sdp_str)
            candidate.sdpMid = msg.get("sdpMid", "0")
            candidate.sdpMLineIndex = msg.get("sdpMLineIndex", 0)
            await self._pc.addIceCandidate(candidate)
        except Exception as exc:
            self.logger.warning(f"Failed to add ICE candidate: {exc}")

    async def _async_cleanup(self) -> None:
        """Close peer connection and stop camera track."""
        if self._pc is not None:
            try:
                await self._pc.close()
            except Exception as exc:
                self.logger.warning(f"Error closing peer connection: {exc}")
            self._pc = None
        if self._track is not None:
            self._track.stop_camera()
            self._track = None

    # ------------------------------------------------------------------
    # RTCPeerConnection event handlers
    # ------------------------------------------------------------------

    def _setup_pc_callbacks(self) -> None:
        """Wire up event handlers on the RTCPeerConnection."""

        @self._pc.on("icecandidate")
        def on_ice_candidate(candidate: RTCIceCandidate) -> None:
            if candidate is None:
                # Gathering complete — send empty candidate as end signal
                self.bus.publish("camera.ice.outbound", {
                    "candidate": "",
                    "sdpMid": "",
                    "sdpMLineIndex": 0,
                })
                return
            ice_dict = {
                "candidate": candidate.candidate,
                "sdpMid": candidate.sdpMid,
                "sdpMLineIndex": candidate.sdpMLineIndex,
            }
            self.bus.publish("camera.ice.outbound", ice_dict)
            self.logger.debug(f"ICE candidate published: {candidate.candidate[:40]}...")

        # Capture reference so callback only fires for THIS pc, not a replacement
        pc = self._pc

        @pc.on("connectionstatechange")
        async def on_connection_state_change() -> None:
            # Ignore events from old peer connections after cleanup/restart
            if self._pc is not pc:
                return
            state = pc.connectionState
            self.logger.info(f"WebRTC connection state: {state}")
            if state == "connected":
                self.bus.publish("camera.status", {
                    "status": "streaming",
                    "resolution": list(self._resolution),
                    "fps": self._framerate,
                })
            elif state == "failed":
                self.bus.publish("camera.status", {"status": "error", "error": "connection failed"})
            elif state == "disconnected":
                self.bus.publish("camera.status", {"status": "error", "error": "peer disconnected"})
            # "closed" is expected during cleanup — don't publish anything
