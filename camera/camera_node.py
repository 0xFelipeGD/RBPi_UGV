"""CameraNode: manages WebRTC video streaming from Pi Camera via aiortc.

Also drives the optional Local Mode MJPEG encoder (spec §6.2 §7.3) when
``local_mode.enabled`` is set in the config: a second picamera2 encoder
attaches lazily to the `lores` stream and feeds raw JPEG frames into an
asyncio.Queue consumed by ``camera/mjpeg_server.py``.
"""

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

# picamera2 MJPEG encoder is hardware-only (Pi). Guard the import so the
# camera_node module still imports on dev machines / CI without picamera2.
# Falls back to None — attach_mjpeg_encoder will refuse to start if absent.
try:
    from picamera2.encoders import MJPEGEncoder  # type: ignore
    _HAS_MJPEG_ENCODER = True
except ImportError:
    MJPEGEncoder = None  # type: ignore
    _HAS_MJPEG_ENCODER = False

# Monkey-patch VP8 encoder for better quality over TURN relay:
# 1. Increase bitrate from 500kbps default to 2000kbps (720p needs it)
# 2. Force keyframe every 30 frames (~1s at 30fps) so browser recovers from packet loss
import aiortc.codecs.vpx as _vpx

_vpx_orig_init = _vpx.Vp8Encoder.__init__
_vpx_orig_encode = _vpx.Vp8Encoder.encode

def _vpx_patched_init(self, *args, **kwargs):
    _vpx_orig_init(self, *args, **kwargs)
    self._Vp8Encoder__target_bitrate = 2_000_000  # 2 Mbps (default is 500kbps)

def _vpx_kf_encode(self, frame, force_keyframe=False):
    if not hasattr(self, '_kf_counter'):
        self._kf_counter = 0
    self._kf_counter += 1
    if self._kf_counter % 30 == 0:  # keyframe every ~1 second at 30fps
        force_keyframe = True
    return _vpx_orig_encode(self, frame, force_keyframe)

_vpx.Vp8Encoder.__init__ = _vpx_patched_init
_vpx.Vp8Encoder.encode = _vpx_kf_encode


class _AsyncQueueOutput:
    """picamera2 output target that pushes JPEG frames into an asyncio.Queue.

    picamera2 calls ``write()`` from a worker thread; we hop onto the asyncio
    loop via ``call_soon_threadsafe``. Drop-oldest policy ensures latency
    stays low even if the HTTP consumer falls behind. Used by
    ``CameraNode.attach_mjpeg_encoder`` (spec §7.3).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue,
                 max_queued: int = 1) -> None:
        self._loop = loop
        self._queue = queue
        self._max_queued = max_queued

    def write(self, buf: bytes) -> int:
        def _put() -> None:
            while self._queue.qsize() >= self._max_queued:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            self._queue.put_nowait(bytes(buf))
        try:
            self._loop.call_soon_threadsafe(_put)
        except RuntimeError:
            # Loop is closed/closing — drop the frame silently.
            pass
        return len(buf)

    def flush(self) -> None:
        pass


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
        self._noir_correction: dict = {}

        # WebRTC state
        self._pc: RTCPeerConnection | None = None
        self._track: PiCameraTrack | None = None

        # Asyncio event loop running in a daemon thread
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

        # Local Mode MJPEG state (spec §6.2 §7.3). All None unless
        # local_mode.enabled is true and a client attaches.
        self._local_mode_enabled: bool = False
        self._local_mode_video_cfg: dict = {}
        self._mjpeg_server: Any = None  # camera.mjpeg_server.MjpegServer
        self._mjpeg_encoder: Any = None  # picamera2.encoders.MJPEGEncoder
        self._mjpeg_queue: asyncio.Queue | None = None

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
        # Pass-through: PiCameraTrack reads this dict directly to configure
        # libcamera AWB / colour gains / colour correction matrix. See
        # config/default_config.yaml for the shape and default values.
        self._noir_correction = cam_cfg.get("noir_color_correction", {}) or {}

        # Local Mode video config (spec §6.2 §7.3). Optional — when absent
        # or disabled, the MJPEG encoder/server is never instantiated and
        # the existing WebRTC-only path is preserved bit-for-bit.
        local_cfg = self.config.get("local_mode", {}) or {}
        self._local_mode_enabled = bool(local_cfg.get("enabled", False))
        self._local_mode_video_cfg = (local_cfg.get("video", {}) or {}).get("mjpeg", {}) or {}
        if self._local_mode_enabled:
            self.logger.info(
                "Local Mode MJPEG path enabled — will start aiohttp server on activate"
            )

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

        # Start the Local Mode MJPEG aiohttp server if configured
        # (spec §6.2 §7.3). Server runs on the same asyncio loop as aiortc.
        if self._local_mode_enabled:
            asyncio.run_coroutine_threadsafe(
                self._async_start_mjpeg_server(), self._loop
            )

        self.logger.info("Camera node active — waiting for start command")

    def on_shutdown(self) -> None:
        """Clean up: stop MJPEG server, close peer connection, stop camera, stop asyncio loop."""
        if self._loop is not None and self._loop.is_running():
            # Stop the Local Mode MJPEG server first (its lazy detach hook
            # will also be invoked if a client is still connected).
            if self._mjpeg_server is not None:
                fut = asyncio.run_coroutine_threadsafe(
                    self._async_stop_mjpeg_server(), self._loop
                )
                try:
                    fut.result(timeout=3.0)
                except Exception as exc:
                    self.logger.warning(f"MJPEG server stop error: {exc}")
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

            # Create and start camera track. When Local Mode MJPEG is
            # enabled, request a secondary `lores` stream sized from config
            # so MJPEGEncoder has its own buffer pipeline (spec §7.3).
            lores_size: tuple[int, int] | None = None
            if self._local_mode_enabled and self._local_mode_video_cfg:
                w = self._local_mode_video_cfg.get("width")
                h = self._local_mode_video_cfg.get("height")
                if w and h:
                    lores_size = (int(w), int(h))
            self._track = PiCameraTrack(
                width=self._resolution[0],
                height=self._resolution[1],
                framerate=self._framerate,
                noir_correction=self._noir_correction,
                lores_size=lores_size,
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
        # Ignore duplicate answers (multiple WS clients may send the same answer)
        if self._pc.signalingState != "have-local-offer":
            self.logger.debug(
                f"Ignoring SDP answer in state '{self._pc.signalingState}'"
            )
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
    # Local Mode MJPEG (spec §6.2 §7.3)
    # ------------------------------------------------------------------

    async def _async_start_mjpeg_server(self) -> None:
        """Instantiate and start the Local Mode MJPEG aiohttp server."""
        try:
            from camera.mjpeg_server import MjpegServer
            vid = self._local_mode_video_cfg
            self._mjpeg_server = MjpegServer(
                bind_host=vid["bind_host"],
                bind_port=int(vid["bind_port"]),
                cert_path=vid["cert_path"],
                key_path=vid["key_path"],
                auth_username=vid["auth_username"],
                auth_password_hash_check=self._verify_mqtt_password,
                attach_encoder=self.attach_mjpeg_encoder,
                detach_encoder=self.detach_mjpeg_encoder,
                endpoint_path=vid.get("endpoint_path", "/stream.mjpg"),
            )
            await self._mjpeg_server.start()
            self.logger.info("Local Mode MJPEG server started")
        except Exception as exc:
            self.logger.error(f"Failed to start MJPEG server: {exc}")
            self._mjpeg_server = None

    async def _async_stop_mjpeg_server(self) -> None:
        """Stop the MJPEG aiohttp server and detach the encoder if attached."""
        if self._mjpeg_server is not None:
            try:
                await self._mjpeg_server.stop()
            except Exception as exc:
                self.logger.warning(f"MJPEG server stop raised: {exc}")
            self._mjpeg_server = None
        # Belt-and-braces: ensure the encoder is detached even if the
        # server stopped without going through the lazy detach path.
        if self._mjpeg_encoder is not None:
            await self.detach_mjpeg_encoder()

    async def attach_mjpeg_encoder(self) -> asyncio.Queue:
        """Lazy-attach the MJPEG encoder to picamera2. Spec §7.3.

        Called by ``MjpegServer`` on first client. Returns the asyncio.Queue
        that will receive raw JPEG bytes per frame (drop-oldest, max-1).
        """
        if not _HAS_MJPEG_ENCODER:
            raise RuntimeError(
                "picamera2.encoders.MJPEGEncoder not available — "
                "Local Mode MJPEG requires running on a Raspberry Pi with "
                "picamera2 installed (apt python3-picamera2)."
            )
        if self._track is None or self._track.picam2 is None:
            raise RuntimeError(
                "Camera track is not running — start the WebRTC capture "
                "(camera/cmd action=start) before opening the MJPEG stream."
            )

        cfg = self._local_mode_video_cfg
        # MJPEGEncoder.bitrate is a coarse parameter; we map jpeg_quality
        # (1..100) to a rough bitrate ceiling. Tunable via config.
        jpeg_quality = int(cfg.get("jpeg_quality", 80))
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._mjpeg_queue = queue
        self._mjpeg_encoder = MJPEGEncoder(bitrate=jpeg_quality * 100_000)
        output = _AsyncQueueOutput(asyncio.get_running_loop(), queue, max_queued=1)
        # Attach to the lores stream so MJPEG and the raw `main` capture
        # consumed by aiortc do not contend for the same frame buffers.
        self._track.picam2.start_encoder(self._mjpeg_encoder, output, name="lores")
        self.logger.info("MJPEG encoder started (lores stream)")
        return queue

    async def detach_mjpeg_encoder(self) -> None:
        """Stop the MJPEG encoder. Idempotent."""
        if self._mjpeg_encoder is not None:
            try:
                if self._track is not None and self._track.picam2 is not None:
                    self._track.picam2.stop_encoder(self._mjpeg_encoder)
            except Exception as exc:
                self.logger.warning(f"stop_encoder raised: {exc}")
            self._mjpeg_encoder = None
            self._mjpeg_queue = None
            self.logger.info("MJPEG encoder stopped")

    def _verify_mqtt_password(self, password: str) -> bool:
        """Stub password verifier for Local Mode MJPEG BasicAuth.

        TODO(local-mode v1.1): verify ``password`` against the
        ``rcs_operator`` entry in ``/etc/mosquitto/passwd`` (PBKDF2-SHA512
        as per Mosquitto's ``mosquitto_passwd`` format). For v1.0.0 we
        accept any non-empty password — the LAN is trusted (per spec §13)
        and the connection is still TLS-encrypted; an attacker would need
        to be on the same Tailscale tailnet AND know the username.
        """
        return bool(password)

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
