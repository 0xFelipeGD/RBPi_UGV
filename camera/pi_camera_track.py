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

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        framerate: int = 30,
        noir_correction: dict | None = None,
        lores_size: tuple[int, int] | None = None,
    ) -> None:
        super().__init__()
        self._width = width
        self._height = height
        self._framerate = framerate

        # NoIR color-correction config. None or empty dict → no correction
        # applied (use picamera2 defaults, whatever they may be for the
        # specific camera module). See config/default_config.yaml for the
        # recommended starting values for the Pi Camera Module 3 NoIR under
        # typical indoor lighting.
        self._noir_correction: dict = noir_correction or {}

        # Optional secondary lores stream config (used by Local Mode MJPEG
        # encoder, spec §6.2 §7.3). When None, only the `main` stream is
        # configured — preserving the WebRTC-only behaviour. When set, a
        # YUV420 lores stream is added so MJPEGEncoder can attach to it
        # without disturbing the main RGB888 stream consumed by aiortc.
        self._lores_size: tuple[int, int] | None = lores_size

        # Frame timing
        self._pts = 0
        self._time_base_num = 1
        self._time_base_den = framerate

        # picamera2 instance (None until start() is called)
        self._picam: "Picamera2 | None" = None

        # Test-pattern state (used when picamera2 is unavailable)
        self._frame_count = 0

    @property
    def picam2(self) -> "Picamera2 | None":
        """Expose the underlying Picamera2 handle so encoders can attach.

        Used by CameraNode.attach_mjpeg_encoder for Local Mode MJPEG.
        Returns None if picamera2 is not running (test-pattern fallback).
        """
        return self._picam

    def _apply_noir_color_correction(self) -> None:
        """Apply NoIR color-correction controls to the running camera.

        The Pi Camera Module 3 NoIR has no infrared cut filter, so under
        normal indoor lighting (LED, fluorescent) IR bleeds into all three
        RGB channels, making whites look pinkish and dark colors look
        purple/blue. picamera2 exposes AWB-mode, manual color gains, and
        a full 3x3 color correction matrix — all three can be tuned here
        via config to compensate.

        Config model (read from self._noir_correction):
          enabled           — bool, master switch. If False, skip entirely.
          colour_gains      — optional [red, blue] list. If set, AWB is
                              disabled and these manual gains are used.
          awb_mode          — optional integer libcamera AwbModeEnum value
                              (0-7) used only when colour_gains is None.
          colour_correction_matrix — optional 9-element list (row-major
                              3x3 matrix). Independent of awb / gains.
                              Skip if None.

        Silently no-op on any error or when picamera2 is not running —
        we never want a color tuning failure to take down the camera.
        """
        if self._picam is None:
            return
        cfg = self._noir_correction or {}
        if not cfg.get("enabled", True):
            logger.info("NoIR color correction: disabled by config")
            return

        controls_to_set: dict = {}

        colour_gains = cfg.get("colour_gains")
        awb_mode = cfg.get("awb_mode")
        ccm = cfg.get("colour_correction_matrix")

        if colour_gains is not None:
            try:
                r_gain, b_gain = float(colour_gains[0]), float(colour_gains[1])
                controls_to_set["AwbEnable"] = False
                controls_to_set["ColourGains"] = (r_gain, b_gain)
            except (TypeError, ValueError, IndexError) as exc:
                logger.warning(
                    "NoIR color correction: invalid colour_gains %r (%s) — skipping",
                    colour_gains, exc,
                )
        elif awb_mode is not None:
            try:
                controls_to_set["AwbEnable"] = True
                controls_to_set["AwbMode"] = int(awb_mode)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "NoIR color correction: invalid awb_mode %r (%s) — skipping",
                    awb_mode, exc,
                )

        if ccm is not None:
            try:
                ccm_tuple = tuple(float(v) for v in ccm)
                if len(ccm_tuple) != 9:
                    raise ValueError(f"expected 9 floats, got {len(ccm_tuple)}")
                controls_to_set["ColourCorrectionMatrix"] = ccm_tuple
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "NoIR color correction: invalid colour_correction_matrix %r (%s) — skipping",
                    ccm, exc,
                )

        if not controls_to_set:
            logger.info(
                "NoIR color correction: no controls configured — using picamera2 defaults"
            )
            return

        try:
            self._picam.set_controls(controls_to_set)
            logger.info("NoIR color correction applied: %s", controls_to_set)
        except Exception as exc:
            # Silently degrade — better to have weirdly-coloured video than
            # no video at all if set_controls fails on this camera/libcamera combo.
            logger.warning(
                "NoIR color correction: set_controls failed (%s) — "
                "falling back to picamera2 defaults. Attempted: %s",
                exc, controls_to_set,
            )

    def start_camera(self) -> None:
        """Initialise and start the camera hardware (or test-pattern mode)."""
        if _HAS_PICAMERA2:
            try:
                self._picam = Picamera2()
                # If a secondary lores stream is requested (Local Mode MJPEG,
                # spec §7.3), configure both streams so MJPEGEncoder can run
                # on `lores` without disturbing the WebRTC `main` capture.
                # `encode="main"` keeps aiortc reading from the main stream.
                cfg_kwargs: dict = {
                    "main": {"size": (self._width, self._height), "format": "RGB888"},
                    "encode": "main",
                }
                if self._lores_size is not None:
                    cfg_kwargs["lores"] = {
                        "size": self._lores_size,
                        "format": "YUV420",
                    }
                config = self._picam.create_video_configuration(**cfg_kwargs)
                self._picam.configure(config)
                self._picam.start()
                # Apply NoIR color correction AFTER start() — picamera2's
                # set_controls works in either order but setting post-start
                # guarantees libcamera has the sensor initialised and
                # accepts the controls immediately.
                self._apply_noir_color_correction()
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
