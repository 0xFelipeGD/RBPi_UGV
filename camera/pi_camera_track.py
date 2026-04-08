"""Custom aiortc MediaStreamTrack that captures frames from the Pi Camera Module 3."""

import asyncio
import logging
import time
from fractions import Fraction

import numpy as np
from aiortc import MediaStreamTrack
from av import VideoFrame

logger = logging.getLogger("ugv.camera.track")

# Try to import picamera2 — only available on Raspberry Pi OS with libcamera.
# This import frequently fails when the venv was created WITHOUT
# --system-site-packages, because python3-picamera2 / python3-libcamera are
# installed via apt (they bundle compiled libcamera bindings and cannot be
# pip-installed). When this happens we fall back to an SMPTE test pattern,
# but the warning below MUST stay loud and explicit so the operator can find
# it in `journalctl -u ugv` instead of silently shipping a rainbow video feed.
_PICAMERA2_IMPORT_ERROR: str | None = None
try:
    from picamera2 import Picamera2

    _HAS_PICAMERA2 = True
except ImportError as _exc:
    _HAS_PICAMERA2 = False
    _PICAMERA2_IMPORT_ERROR = str(_exc)
    logger.warning(
        "CameraNode: picamera2 unavailable (%s) -- using SMPTE test pattern. "
        "Check that the venv was created with --system-site-packages and that "
        "the apt package python3-picamera2 is installed.",
        _PICAMERA2_IMPORT_ERROR,
    )


class PiCameraTrack(MediaStreamTrack):
    """Video track that captures frames from the Pi Camera via picamera2.

    If picamera2 is not available (dev machine), generates a coloured test
    pattern so the WebRTC pipeline can be exercised without hardware.
    """

    kind = "video"

    def __init__(self, width: int = 1280, height: int = 720, framerate: int = 30) -> None:
        super().__init__()
        self._width = width
        self._height = height
        self._framerate = framerate

        # Frame timing
        self._pts = 0
        self._time_base_num = 1
        self._time_base_den = framerate

        # picamera2 instance (None until start() is called)
        self._picam: "Picamera2 | None" = None

        # Test-pattern state (used when picamera2 is unavailable)
        self._frame_count = 0

    def start_camera(self) -> None:
        """Initialise and start the camera hardware (or test-pattern mode)."""
        if _HAS_PICAMERA2:
            try:
                self._picam = Picamera2()
                config = self._picam.create_video_configuration(
                    main={"size": (self._width, self._height), "format": "RGB888"},
                )
                self._picam.configure(config)
                self._picam.start()
                logger.info(
                    f"picamera2 started: {self._width}x{self._height}@{self._framerate}fps"
                )
                return
            except Exception as exc:
                # Hardware/runtime failure (no camera attached, libcamera mismatch,
                # permission denied, etc). Fall back LOUDLY to test pattern so the
                # operator can see WHY video is wrong in `journalctl -u ugv`.
                self._picam = None
                logger.warning(
                    "CameraNode: picamera2 construction/start FAILED (%s) -- "
                    "falling back to SMPTE test pattern. Check that the camera "
                    "is connected, the libcamera stack is healthy, and the "
                    "venv was created with --system-site-packages.",
                    exc,
                )
        else:
            logger.warning(
                "CameraNode: picamera2 module not importable -- using SMPTE "
                "test pattern at %dx%d@%dfps. Reason: %s",
                self._width,
                self._height,
                self._framerate,
                _PICAMERA2_IMPORT_ERROR or "unknown",
            )

    def stop_camera(self) -> None:
        """Stop the camera hardware."""
        if self._picam is not None:
            try:
                self._picam.stop()
                self._picam.close()
            except Exception as exc:
                logger.warning(f"Error stopping picamera2: {exc}")
            finally:
                self._picam = None
        logger.info("Camera stopped")

    async def recv(self) -> VideoFrame:
        """Deliver the next video frame to aiortc.

        Called by the aiortc event loop at the negotiated framerate.
        """
        # Pace the delivery to the configured framerate
        if self._pts > 0:
            wait = 1.0 / self._framerate
            await asyncio.sleep(wait)

        # Capture frame (run in executor to avoid blocking the event loop —
        # blocking kills RTP/ICE packet processing, especially over TURN relay)
        if self._picam is not None:
            loop = asyncio.get_event_loop()
            array = await loop.run_in_executor(None, self._picam.capture_array, "main")
            # picamera2 RGB888 may deliver BGR on some libcamera versions
            array = array[:, :, ::-1]
        else:
            array = self._generate_test_pattern()

        # Convert numpy array (RGB) to av.VideoFrame
        frame = VideoFrame.from_ndarray(array, format="rgb24")
        frame.pts = self._pts
        frame.time_base = Fraction(self._time_base_num, self._time_base_den)
        self._pts += 1

        return frame

    def _generate_test_pattern(self) -> np.ndarray:
        """Generate a colour-bar test pattern with a moving indicator."""
        self._frame_count += 1
        frame = np.zeros((self._height, self._width, 3), dtype=np.uint8)

        # Colour bars (8 vertical stripes)
        colours = [
            (192, 192, 192),  # White/grey
            (192, 192, 0),    # Yellow
            (0, 192, 192),    # Cyan
            (0, 192, 0),      # Green
            (192, 0, 192),    # Magenta
            (192, 0, 0),      # Red
            (0, 0, 192),      # Blue
            (0, 0, 0),        # Black
        ]
        bar_width = self._width // len(colours)
        for i, colour in enumerate(colours):
            x_start = i * bar_width
            x_end = x_start + bar_width
            frame[:, x_start:x_end] = colour

        # Moving white bar (horizontal scan line)
        y = (self._frame_count * 3) % self._height
        frame[y : min(y + 4, self._height), :] = (255, 255, 255)

        return frame
