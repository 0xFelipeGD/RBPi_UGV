#!/usr/bin/env python3
"""Test if aiortc includes TURN relay candidates in WebRTC offers."""
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

    candidates = []

    @pc.on("icecandidate")
    def on_candidate(candidate):
        if candidate:
            candidates.append(candidate)
            print(f"  ICE candidate: {candidate.type:8s} {candidate.host}:{candidate.port}")

    pc.addTrack(DummyTrack())
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Wait for gathering to finish
    print("Gathering ICE candidates...")
    for _ in range(100):
        await asyncio.sleep(0.1)
        if pc.iceGatheringState == "complete":
            break

    print(f"\nGathering state: {pc.iceGatheringState}")
    print(f"Total candidates: {len(candidates)}")

    types = [c.type for c in candidates]
    print(f"Types: {types}")

    if "relay" in types:
        print("\n✓ aiortc IS producing TURN relay candidates!")
    else:
        print("\n✗ aiortc is NOT producing relay candidates!")
        print("  aiortc may not pass TURN config to aioice.")

    await pc.close()

asyncio.run(test())
