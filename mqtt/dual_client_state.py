"""Pure data classes for the dual MQTT client.

Separated from `dual_client.py` to keep state logic testable without paho-mqtt.
"""
from dataclasses import dataclass, field
from enum import Enum


class LinkState(str, Enum):
    """State of a single MQTT link.

    See spec §7.5 / §11. DEGRADED == reconnecting (was up, currently retrying).
    """
    UP = "UP"
    DOWN = "DOWN"
    DEGRADED = "DEGRADED"


@dataclass
class DualLinkSnapshot:
    """Aggregated snapshot of both links, published to internal bus + telemetry."""
    local: LinkState = LinkState.DOWN
    vps: LinkState = LinkState.DOWN

    def to_telemetry(self) -> dict:
        """Serialize to the `links` field of `ugv/telemetry` (spec §11)."""
        return {"local": self.local.value, "vps": self.vps.value}

    def any_up(self) -> bool:
        """True iff at least one link is UP — used by watchdog (spec §13)."""
        return LinkState.UP in (self.local, self.vps)
