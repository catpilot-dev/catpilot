import pytest
import asyncio
import json

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)  # TODO: remove when google-crc32c publishes python3.12 wheel

import aiortc
from parameterized import parameterized_class

from openpilot.system.webrtc.webrtcd import get_stream


@parameterized_class(("in_services", "out_services"), [
  (["testJoystick"], ["carState"]),
  ([],               ["carState"]),
  (["testJoystick"], []),
  ([],               []),
])
@pytest.mark.asyncio
class TestWebrtcdProc:
  async def assertCompletesWithTimeout(self, awaitable, timeout=1):
    try:
      async with asyncio.timeout(timeout):
        await awaitable
    except TimeoutError:
      pytest.fail("Timeout while waiting for awaitable to complete")

  async def test_webrtcd(self, mocker):
    want_messaging = len(self.in_services) > 0 or len(self.out_services) > 0

    # ── Client-side peer connection (pure aiortc, no teleoprtc) ──────────────
    client_pc  = aiortc.RTCPeerConnection()
    video_recv = asyncio.Event()
    audio_recv = asyncio.Event()

    incoming_video_track = None
    incoming_audio_track = None
    incoming_data_channel = None

    @client_pc.on("track")
    def _on_track(track):
      nonlocal incoming_video_track, incoming_audio_track
      if track.kind == "video":
        incoming_video_track = track
        video_recv.set()
      elif track.kind == "audio":
        incoming_audio_track = track
        audio_recv.set()

    # Transceivers: client wants to receive video and audio from device
    client_pc.addTransceiver("video", direction="recvonly")
    client_pc.addTransceiver("audio", direction="recvonly")

    if want_messaging:
      incoming_data_channel = client_pc.createDataChannel("data", ordered=True)

    # ── Build offer ───────────────────────────────────────────────────────────
    offer = await client_pc.createOffer()
    await client_pc.setLocalDescription(offer)

    # ── Call the server handler (simulates COD proxy → webrtcd /stream) ──────
    async def do_connect():
      nonlocal client_pc
      body = {
        "sdp":                 client_pc.localDescription.sdp,
        "cameras":             ["road"],
        "bridge_services_in":  self.in_services,
        "bridge_services_out": self.out_services,
      }
      mock_request = mocker.MagicMock()
      mock_request.json.side_effect = mocker.AsyncMock(return_value=body)
      mock_request.app = {
        "streams":       {},
        "debug":         True,   # use debug tracks — no real cereal needed
        "session_class": __import__(
          "openpilot.system.webrtc.webrtcd", fromlist=["StreamSession"]
        ).StreamSession,
      }
      response = await get_stream(mock_request)
      answer_json = json.loads(response.text)
      return aiortc.RTCSessionDescription(**answer_json), mock_request

    answer, mock_request = await do_connect()
    await client_pc.setRemoteDescription(answer)

    # ── Wait for ICE to connect ───────────────────────────────────────────────
    connected = asyncio.Event()

    @client_pc.on("connectionstatechange")
    async def _on_state():
      if client_pc.connectionState == "connected":
        connected.set()

    await self.assertCompletesWithTimeout(connected.wait())

    # ── Verify incoming tracks ────────────────────────────────────────────────
    await self.assertCompletesWithTimeout(video_recv.wait())
    await self.assertCompletesWithTimeout(audio_recv.wait())

    assert incoming_video_track is not None, "Expected road video track"
    assert incoming_audio_track is not None, "Expected audio track"
    assert (incoming_data_channel is not None) == want_messaging

    # Receive at least one frame from each track
    await self.assertCompletesWithTimeout(incoming_video_track.recv())
    await self.assertCompletesWithTimeout(incoming_audio_track.recv())

    # ── Cleanup ───────────────────────────────────────────────────────────────
    await client_pc.close()

    # Clean up the server-side session
    session = list(mock_request.app["streams"].values())[0] if mock_request.app["streams"] else None
    if session:
      await self.assertCompletesWithTimeout(session._cleanup())
