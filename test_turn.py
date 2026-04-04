#!/usr/bin/env python3
"""Test TURN allocation from the Pi to diagnose relay issues."""
import asyncio
from aioice import Connection

async def test():
    print("Testing TURN allocation to 72.60.132.162:3478...")
    print(f"  Username: ugv")
    print(f"  Password: ugvturn2026")
    print()

    conn = Connection(
        ice_controlling=True,
        components=1,
        stun_server=("72.60.132.162", 3478),
        turn_server=("72.60.132.162", 3478),
        turn_username="ugv",
        turn_password="ugvturn2026",
    )

    await conn.gather_candidates()

    print("ICE candidates gathered:")
    has_relay = False
    for c in conn.local_candidates:
        print(f"  {c.type:8s} {c.host}:{c.port}")
        if c.type == "relay":
            has_relay = True

    if has_relay:
        print("\n✓ TURN relay candidate found — TURN is working!")
    else:
        print("\n✗ NO relay candidate — TURN allocation FAILED!")
        print("  Check: coturn running? credentials correct? UDP 3478 open?")

    await conn.close()

asyncio.run(test())
