"""SDP utilities for WebRTC session negotiation.

Replaces teleoprtc.info (parse_info_from_offer) and the
WebRTCAnswerStream._override_incoming_video_codecs SDP rewrite.
"""

from __future__ import annotations

from typing import Tuple


def strip_to_h264(sdp: str) -> str:
  """Filter all video m-sections in an SDP string to H264-only codecs.

  Our camera tracks deliver raw H264 packets from the cereal encode pipeline.
  aiortc must therefore negotiate H264 regardless of the browser's codec
  preference order.  Stripping all non-H264 entries from the remote offer
  before calling setRemoteDescription ensures this unconditionally.

  Raises ValueError if a video m-section contains no H264 codec at all.
  """
  import aiortc.sdp as _sdp

  desc = _sdp.SessionDescription.parse(sdp)
  for m in desc.media:
    if m.kind != "video":
      continue
    h264 = [c for c in m.rtp.codecs if c.mimeType.upper() == "VIDEO/H264"]
    if not h264:
      raise ValueError(
        "Offer SDP contains no H264 codec in a video m-section — cannot negotiate. "
        f"Available codecs: {[c.mimeType for c in m.rtp.codecs]}"
      )
    m.rtp.codecs = h264
    m.fmt = [c.payloadType for c in h264]
  return str(desc)


def parse_offer_info(sdp: str) -> Tuple[int, bool, bool, bool]:
  """Parse an offer SDP and return session media requirements.

  Returns:
    (n_video, wants_audio_out, has_audio_in, has_datachannel)

    n_video         — number of video tracks the peer expects to receive
                      (recvonly or sendrecv from the peer's perspective)
    wants_audio_out — peer wants to receive audio from us (recvonly/sendrecv)
    has_audio_in    — peer will send us audio (sendonly/sendrecv)
    has_datachannel — offer includes a data channel (application m-section)
  """
  import aiortc.sdp as _sdp

  desc = _sdp.SessionDescription.parse(sdp)
  media = desc.media

  n_video = sum(
    1 for m in media
    if m.kind == "video" and m.direction in ("recvonly", "sendrecv")
  )
  wants_audio_out = any(
    m.kind == "audio" and m.direction in ("recvonly", "sendrecv")
    for m in media
  )
  has_audio_in = any(
    m.kind == "audio" and m.direction in ("sendonly", "sendrecv")
    for m in media
  )
  has_datachannel = any(m.kind == "application" for m in media)

  return n_video, wants_audio_out, has_audio_in, has_datachannel
