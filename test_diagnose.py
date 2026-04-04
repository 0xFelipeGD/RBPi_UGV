#!/usr/bin/env python3
"""Diagnose: does aiortc actually handle TURN servers?"""
import os
import aiortc

# Find aiortc source file
src = os.path.join(os.path.dirname(aiortc.__file__), "rtcpeerconnection.py")
print(f"aiortc source: {src}\n")

# Search for TURN/STUN/ICE server references
with open(src) as f:
    lines = f.readlines()

print("Lines mentioning turn/stun/ice_server/iceServer:")
for i, line in enumerate(lines, 1):
    low = line.lower()
    if any(kw in low for kw in ["turn", "stun", "ice_server", "iceserver"]):
        print(f"  {i:4d}: {line.rstrip()}")

# Also check rtcicetransport.py if it exists
transport_src = os.path.join(os.path.dirname(aiortc.__file__), "rtcicetransport.py")
if os.path.exists(transport_src):
    print(f"\n{transport_src}:")
    with open(transport_src) as f:
        lines = f.readlines()
    for i, line in enumerate(lines, 1):
        low = line.lower()
        if any(kw in low for kw in ["turn", "stun", "ice_server", "iceserver"]):
            print(f"  {i:4d}: {line.rstrip()}")
