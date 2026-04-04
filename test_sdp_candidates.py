#!/usr/bin/env python3
"""Check if TURN relay candidates appear in the actual SDP offer."""
import asyncio
from aiortc import RTCPeerConnection, RTCConfiguration, RTCIceServer, MediaStreamTrack
from av import VideoFrame
import numpy as np
from fractions import Fraction

class DummyTrack(MediaStreamTrack):
    kind = "video"
    async def recv(self):
        await asyncio.sleep(1/15)
        arr = np.zeros((240, 320, 3), dtype=np.uint8)
        frame = VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = 0
        frame.time_base = Fraction(1, 15)
        return frame

async def test():
    config = RTCConfiguration(iceServers=[
        RTCIceServer(urls=["stun:72.60.132.162:3478"]),
        RTCIceServer(urls=["turn:72.60.132.162:3478"], username="ugv", credential="ugvturn2026"),
    ])
    pc = RTCPeerConnection(configuration=config)
    pc.addTrack(DummyTrack())

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Wait for ICE gathering
    print("Waiting for ICE gathering...")
    for _ in range(100):
        await asyncio.sleep(0.1)
        if pc.iceGatheringState == "complete":
            break
    print(f"Gathering state: {pc.iceGatheringState}\n")

    # Check SDP for candidates
    sdp = pc.localDescription.sdp
    print("SDP candidate lines:")
    has_relay = False
    for line in sdp.split("\r\n"):
        if "candidate" in line.lower() and line.startswith("a="):
            print(f"  {line}")
            if "relay" in line or "typ relay" in line:
                has_relay = True

    # Also check internal ice gatherer
    print("\nInternal ICE candidates:")
    for t in pc.getTransceivers():
        if t.sender and t.sender.transport:
            ice = t.sender.transport.transport
            for c in ice.local_candidates:
                tag = " <<<< RELAY!" if c.type == "relay" else ""
                print(f"  {c.type:8s} {c.host}:{c.port}{tag}")

    if has_relay:
        print("\n✓ TURN relay candidates found in SDP!")
    else:
        print("\n✗ NO relay candidates in SDP")

    await pc.close()

asyncio.run(test())
