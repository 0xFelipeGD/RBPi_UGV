"""mDNS advertiser using Avahi.

Spec §6.2 + §7.4. The static service definition lives at
/etc/avahi/services/ugv.service (installed by setup.sh). This module's job
is to ensure avahi-daemon is reachable and confirm our service is published.

Why subprocess instead of pyavahi: pyavahi is unmaintained; the Avahi DBus
interface is stable but verbose. Calling `avahi-publish-service` keeps it
simple and idiomatic for a Pi appliance.
"""
import logging
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


class MdnsAdvertiser:
    """Wraps Avahi service availability checks.

    The actual advertising is done by avahi-daemon reading
    /etc/avahi/services/ugv.service. This class verifies the service is
    visible via `avahi-browse` at startup and can re-publish via
    `avahi-publish-service` as a fallback.
    """

    def __init__(self, hostname: str, service_type: str = "_ugv._tcp",
                 port: int = 8883):
        self.hostname = hostname
        self.service_type = service_type
        self.port = port
        self._fallback_proc: Optional[subprocess.Popen] = None

    def verify_or_fallback(self) -> bool:
        """Ensure mDNS visibility. Return True if visible.

        If `avahi-browse` confirms our service, return True without spawning
        a fallback. Otherwise, start `avahi-publish-service` as a subprocess
        that lives for the lifetime of this advertiser.
        """
        if not shutil.which("avahi-browse"):
            logger.error("avahi-browse not installed; mDNS unavailable")
            return False

        try:
            result = subprocess.run(
                ["avahi-browse", "-r", "-t", self.service_type, "--no-db-lookup"],
                capture_output=True, text=True, timeout=5
            )
            if self.hostname in result.stdout:
                logger.info("mDNS service visible: %s.%s", self.hostname,
                            self.service_type)
                return True
        except Exception:
            logger.exception("avahi-browse failed")

        # Fallback: publish manually
        if not shutil.which("avahi-publish-service"):
            logger.error("avahi-publish-service not installed")
            return False
        try:
            self._fallback_proc = subprocess.Popen([
                "avahi-publish-service",
                self.hostname, self.service_type, str(self.port)
            ])
            logger.warning("mDNS fallback published via avahi-publish-service "
                           "(static service file may be missing)")
            return True
        except Exception:
            logger.exception("avahi-publish-service failed")
            return False

    def stop(self) -> None:
        if self._fallback_proc is not None:
            try:
                self._fallback_proc.terminate()
                self._fallback_proc.wait(timeout=3)
            except Exception:
                logger.exception("error stopping fallback avahi-publish-service")
            self._fallback_proc = None
