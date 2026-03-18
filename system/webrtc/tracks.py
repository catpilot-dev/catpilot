"""Video stream track base for openpilot camera streams.

Replaces TiciVideoStreamTrack from teleoprtc with a self-contained
implementation that has no external library dependency beyond aiortc.
"""

import fractions
import logging
from typing import Optional

import aiortc
from aiortc.mediastreams import VIDEO_CLOCK_RATE, VIDEO_TIME_BASE

CAMERA_TYPES = frozenset({"driver", "wideRoad", "road"})


class VideoStreamTrack(aiortc.MediaStreamTrack):
  """Base class for openpilot camera video tracks.

  Encodes the camera type into the track ID so the receiving peer can
  identify which camera stream it is receiving without a separate
  signalling channel.  The track ID format is ``camera_type:uuid``.
  """

  kind = "video"

  def __init__(self, camera_type: str, dt: float,
               time_base: fractions.Fraction = VIDEO_TIME_BASE,
               clock_rate: int = VIDEO_CLOCK_RATE):
    if camera_type not in CAMERA_TYPES:
      raise ValueError(f"Unknown camera type: {camera_type!r}. Must be one of {CAMERA_TYPES}")
    super().__init__()
    self._id = f"{camera_type}:{self._id}"   # encode camera type into track ID
    self._dt = dt
    self._time_base = time_base
    self._clock_rate = clock_rate
    self._logger = logging.getLogger("webrtcd")

  def log_debug(self, msg, *args):
    self._logger.debug("%s %s", type(self).__name__, msg % args if args else msg)

  def codec_preference(self) -> Optional[str]:
    """Return the MIME subtype this track requires (e.g. 'H264'), or None."""
    return None
