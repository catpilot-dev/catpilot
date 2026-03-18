#!/usr/bin/env python3

import argparse
import asyncio
import json
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

# aiortc and its dependencies have lots of internal warnings :(
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)  # TODO: remove when google-crc32c publishes a python3.12 wheel

from aiohttp import web

from openpilot.system.webrtc.cereal_bridge import (
  CerealOutgoingMessageProxy, CerealIncomingMessageProxy,
  CerealProxyRunner, DynamicPubMaster,
)
from openpilot.system.webrtc.sdp import strip_to_h264, parse_offer_info
from openpilot.system.webrtc.schema import generate_field
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.plugins.hooks import hooks
from cereal import messaging, log

if TYPE_CHECKING:
  from aiortc.rtcdatachannel import RTCDataChannel


class StreamSession:
  """Default WebRTC session implementation — pure aiortc, no teleoprtc dependency.

  Handles one WebRTC peer connection: video + optional audio streaming in
  both directions, plus a bidirectional cereal ↔ data-channel JSON bridge.

  Can be replaced entirely via the webrtc.session_factory hook (one-shot at
  webrtcd startup).  Any replacement class must accept the same constructor
  signature and expose get_answer(), get_messaging_channel(), start(), stop().
  """

  shared_pub_master = DynamicPubMaster([])

  def __init__(self, sdp: str, cameras: list[str],
               incoming_services: list[str], outgoing_services: list[str],
               debug_mode: bool = False):
    import aiortc
    from aiortc.contrib.media import MediaRelay, MediaBlackhole
    from openpilot.system.webrtc.device.video import LiveStreamVideoStreamTrack
    from openpilot.system.webrtc.device.audio import AudioInputStreamTrack, AudioOutputSpeaker

    n_video, wants_audio_out, has_audio_in, has_datachannel = parse_offer_info(sdp)
    assert len(cameras) == n_video, \
      f"Offer has {n_video} video track(s) but {len(cameras)} camera(s) requested"

    self._pc    = aiortc.RTCPeerConnection()
    self._relay = MediaRelay()
    self._offer = sdp
    self.identifier = str(uuid.uuid4())

    # Outgoing tracks (device → client)
    self._video_tracks = [
      LiveStreamVideoStreamTrack(c) if not debug_mode else aiortc.mediastreams.VideoStreamTrack()
      for c in cameras
    ]
    self._audio_out: Optional[aiortc.MediaStreamTrack] = (
      (AudioInputStreamTrack() if not debug_mode else aiortc.mediastreams.AudioStreamTrack())
      if wants_audio_out else None
    )

    # Incoming audio (client → device speaker)
    self._has_audio_in = has_audio_in
    self._audio_output_cls = AudioOutputSpeaker if not debug_mode else MediaBlackhole
    self._audio_output = None
    self._incoming_audio_track = None

    # Data channel — created by the client, we receive it via on("datachannel")
    self._messaging_channel: Optional[RTCDataChannel] = None

    # ── Lifecycle events ──────────────────────────────────────────────────────
    self._connected    = asyncio.Event()   # ICE reached 'connected'
    self._failed       = asyncio.Event()   # ICE reached 'failed'
    self._disconnected = asyncio.Event()   # peer went away after connecting
    self._channel_open = asyncio.Event()   # data channel is open and ready

    # Track expected incoming items (data channel + optional audio).
    # _incoming_ready fires when all expected items have arrived and are open,
    # unblocking run() so it can wire up the bridges.
    _expected = int(has_datachannel) + int(has_audio_in)
    self._incoming_ready  = asyncio.Event()
    self._incoming_count  = 0
    self._expected_incoming = _expected
    if _expected == 0:
      self._incoming_ready.set()

    # ── RTCPeerConnection callbacks ───────────────────────────────────────────
    @self._pc.on("connectionstatechange")
    async def _on_state():
      s = self._pc.connectionState
      self.logger.debug("session %s: connectionState=%s", self.identifier, s)
      if s == "connected":
        self._connected.set()
      if s == "failed":
        self._failed.set()
      if s in ("disconnected", "closed", "failed"):
        self._disconnected.set()

    @self._pc.on("track")
    def _on_track(track):
      if track.kind == "audio" and self._has_audio_in:
        self._incoming_audio_track = self._relay.subscribe(track, buffered=False)
        self._incoming_count += 1
        if self._incoming_count >= self._expected_incoming:
          self._incoming_ready.set()

    @self._pc.on("datachannel")
    def _on_datachannel(channel):
      if channel.label != "data":
        return
      self._messaging_channel = channel

      def _opened():
        self._channel_open.set()
        self._incoming_count += 1
        if self._incoming_count >= self._expected_incoming:
          self._incoming_ready.set()

      if channel.readyState == "open":
        _opened()
      else:
        channel.on("open", _opened)

    # ── Cereal bridges ────────────────────────────────────────────────────────
    self._incoming_bridge   = CerealIncomingMessageProxy(self.shared_pub_master) if incoming_services else None
    self._incoming_services = incoming_services
    self._outgoing_bridge: Optional[CerealOutgoingMessageProxy] = None
    self._outgoing_runner:  Optional[CerealProxyRunner]         = None
    if outgoing_services:
      self._outgoing_bridge = CerealOutgoingMessageProxy(messaging.SubMaster(outgoing_services))
      self._outgoing_runner = CerealProxyRunner(self._outgoing_bridge)

    self.run_task: Optional[asyncio.Task] = None
    self.logger = logging.getLogger("webrtcd")
    self.logger.info(
      "new session (%s) cameras=%s audio_out=%s audio_in=%s in_services=%s out_services=%s",
      self.identifier, cameras, wants_audio_out, has_audio_in, incoming_services, outgoing_services,
    )
    cloudlog.info(f"webrtcd: new session {self.identifier} cameras={cameras}")

  async def get_answer(self):
    """Perform SDP offer/answer exchange and return the local answer description."""
    import aiortc

    # Strip offer to H264-only: our tracks deliver raw H264 packets from cereal
    patched = strip_to_h264(self._offer)
    await self._pc.setRemoteDescription(
      aiortc.RTCSessionDescription(sdp=patched, type="offer")
    )

    # Add outgoing video tracks and lock the transceiver to H264
    for track in self._video_tracks:
      sender = self._pc.addTrack(track)
      pref = getattr(track, "codec_preference", lambda: None)()
      if pref:
        transceiver = next(
          (t for t in self._pc.getTransceivers() if t.sender == sender), None
        )
        if transceiver:
          caps = [
            c for c in aiortc.RTCRtpSender.getCapabilities("video").codecs
            if c.mimeType.upper() == f"VIDEO/{pref.upper()}"
          ]
          if caps:
            transceiver.setCodecPreferences(caps)

    if self._audio_out:
      self._pc.addTrack(self._audio_out)

    answer = await self._pc.createAnswer()
    await self._pc.setLocalDescription(answer)
    return self._pc.localDescription

  def get_messaging_channel(self) -> Optional['RTCDataChannel']:
    return self._messaging_channel

  async def _on_message(self, message: bytes):
    assert self._incoming_bridge is not None
    try:
      self._incoming_bridge.send(message)
    except Exception:
      self.logger.exception("session %s: cereal incoming proxy error", self.identifier)

  def start(self):
    self.run_task = asyncio.create_task(self.run())

  def stop(self):
    if self.run_task and not self.run_task.done():
      self.run_task.cancel()

  async def run(self):
    try:
      # Wait for ICE to either connect or fail
      connected_task    = asyncio.create_task(self._connected.wait())
      failed_task       = asyncio.create_task(self._failed.wait())
      done, pending = await asyncio.wait(
        {connected_task, failed_task},
        return_when=asyncio.FIRST_COMPLETED,
      )
      for t in pending:
        t.cancel()

      if self._failed.is_set() and not self._connected.is_set():
        raise ConnectionError("ICE negotiation failed")

      # Wait until all expected incoming media (data channel + audio) is ready
      await self._incoming_ready.wait()

      # Wire up cereal bridges
      if self._messaging_channel is not None:
        if self._incoming_bridge is not None:
          await self.shared_pub_master.add_services_if_needed(self._incoming_services)
          self._messaging_channel.on("message", self._on_message)
        if self._outgoing_runner is not None:
          self._outgoing_bridge.add_channel(self._messaging_channel)
          self._outgoing_runner.start()

      # Set up incoming audio output
      if self._incoming_audio_track is not None:
        self._audio_output = self._audio_output_cls()
        self._audio_output.addTrack(self._incoming_audio_track)
        self._audio_output.start()

      self.logger.info("session (%s) connected", self.identifier)
      cloudlog.info(f"webrtcd: session {self.identifier} connected")
      hooks.run('webrtc.session_started', None, self.identifier)

      await self._disconnected.wait()
      await self._cleanup()

      self.logger.info("session (%s) ended", self.identifier)
      cloudlog.info(f"webrtcd: session {self.identifier} ended")
      hooks.run('webrtc.session_ended', None, self.identifier)

    except asyncio.CancelledError:
      await self._cleanup()
      raise
    except Exception as e:
      self.logger.exception("session (%s) error", self.identifier)
      cloudlog.error(f"webrtcd: session {self.identifier} failed: {e}")
      await self._cleanup()

  async def _cleanup(self):
    await self._pc.close()
    if self._outgoing_runner:
      self._outgoing_runner.stop()
    if self._audio_output:
      self._audio_output.stop()


@dataclass
class StreamRequestBody:
  sdp: str
  cameras: list[str]
  bridge_services_in:  list[str] = field(default_factory=list)
  bridge_services_out: list[str] = field(default_factory=list)


async def get_stream(request: 'web.Request'):
  stream_dict  = request.app['streams']
  debug_mode   = request.app['debug']
  SessionClass = request.app['session_class']

  raw_body = await request.json()
  try:
    body = StreamRequestBody(**raw_body)
  except TypeError as e:
    cloudlog.error(f"webrtcd: bad stream request body: {e}")
    raise web.HTTPBadRequest(text=f"Invalid request body: {e}") from e

  try:
    session = SessionClass(body.sdp, body.cameras, body.bridge_services_in, body.bridge_services_out, debug_mode)
    answer  = await session.get_answer()
  except Exception as e:
    cloudlog.error(f"webrtcd: failed to create session: {e}")
    raise web.HTTPInternalServerError(text=f"Session setup failed: {e}") from e

  session.start()
  stream_dict[session.identifier] = session
  return web.json_response({"sdp": answer.sdp, "type": answer.type})


async def get_schema(request: 'web.Request'):
  services = request.query["services"].split(",")
  services = [s for s in services if s]
  assert all(s in log.Event.schema.fields and not s.endswith("DEPRECATED") for s in services), \
    "Invalid service name"
  schema_dict = {s: generate_field(log.Event.schema.fields[s]) for s in services}
  return web.json_response(schema_dict)


async def post_notify(request: 'web.Request'):
  try:
    payload = await request.json()
  except Exception as e:
    raise web.HTTPBadRequest(text="Invalid JSON") from e

  for session in list(request.app.get('streams', {}).values()):
    try:
      ch = session.get_messaging_channel()
      if ch is not None:
        ch.send(json.dumps(payload))
    except Exception:
      continue

  return web.Response(status=200, text="OK")


async def on_shutdown(app: 'web.Application'):
  for session in app['streams'].values():
    session.stop()
  del app['streams']


def webrtcd_thread(host: str, port: int, debug: bool):
  logging.basicConfig(level=logging.CRITICAL, handlers=[logging.StreamHandler()])
  logging_level = logging.DEBUG if debug else logging.INFO
  logging.getLogger("webrtcd").setLevel(logging_level)

  cloudlog.info(f"webrtcd: starting on {host}:{port}")

  # Allow a plugin to provide a custom session class (one-shot at startup).
  # Default: StreamSession — pure aiortc, no external WebRTC library required.
  SessionClass = hooks.run('webrtc.session_factory', StreamSession)
  cloudlog.info(f"webrtcd: session class = {SessionClass.__name__}")

  app = web.Application()
  app['streams']       = {}
  app['debug']         = debug
  app['session_class'] = SessionClass
  app.on_shutdown.append(on_shutdown)
  app.router.add_post("/stream", get_stream)
  app.router.add_post("/notify", post_notify)
  app.router.add_get("/schema",  get_schema)

  # Allow plugins to register additional routes (e.g. phone_display /health)
  registered = hooks.run('webrtc.app_routes', [], app)
  if registered:
    cloudlog.info(f"webrtcd: plugin routes registered: {registered}")

  try:
    web.run_app(app, host=host, port=port)
  except Exception as e:
    cloudlog.error(f"webrtcd: fatal error: {e}")
    raise


def main():
  parser = argparse.ArgumentParser(description="WebRTC daemon")
  parser.add_argument("--host",  type=str,  default="0.0.0.0")
  parser.add_argument("--port",  type=int,  default=5001)
  parser.add_argument("--debug", action="store_true")
  args = parser.parse_args()
  webrtcd_thread(args.host, args.port, args.debug)


if __name__ == "__main__":
  main()
