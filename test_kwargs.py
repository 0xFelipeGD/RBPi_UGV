#!/usr/bin/env python3
"""Check exactly what aiortc passes to aioice for ICE."""
from aiortc.rtcicetransport import connection_kwargs
from aiortc import RTCIceServer

servers = [
    RTCIceServer(urls=["stun:72.60.132.162:3478"]),
    RTCIceServer(urls=["turn:72.60.132.162:3478"], username="ugv", credential="ugvturn2026"),
]

kwargs = connection_kwargs(servers)
print("kwargs passed to aioice Connection():")
for k, v in sorted(kwargs.items()):
    print(f"  {k}: {v!r}")

if "turn_server" in kwargs:
    print("\n✓ TURN config IS being passed to aioice")
else:
    print("\n✗ TURN config is NOT being passed to aioice")
