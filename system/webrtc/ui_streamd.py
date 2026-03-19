#!/usr/bin/env python3
"""
ui_streamd: stream the raylib UI framebuffer via WebRTC.

Reads raw RGBA frames from a FIFO written by application.py (STREAM_UI=1),
encodes them with libx264 via PyAV (no ffmpeg subprocess), and publishes H264
packets to cereal as livestreamRoadEncodeData — the same socket that webrtcd
reads for camera streaming.

Pipeline (no subprocess):
  UI FIFO (RGBA bytes) → numpy read → av.VideoFrame → libx264 → cereal

Intended process config (catpilot phone-display branch):
  STREAM_UI=1 → run ui_streamd (always_run) + webrtcd (always_run)
  STREAM_UI=0 → run stream_encoderd (only_onroad) + webrtcd (only_onroad)

Hardware H264 (h264_v4l2m2m) is not accessible via libav on TICI —
Qualcomm-specific ioctl path is only used by the native stream_encoderd.
"""
import fractions
import os
import time

import av
import numpy as np

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog

FIFO_PATH     = os.getenv("STREAM_UI_FIFO",   "/tmp/ui_stream.fifo")
STREAM_BITRATE = int(os.getenv("STREAM_BITRATE", "1000000"))  # 1 Mbps
GOP_SIZE      = int(os.getenv("STREAM_GOP",   "5"))   # ~0.5s at 10fps
V4L2_BUF_FLAG_KEYFRAME = 8

# UI canvas dimensions — must match GuiApplication defaults for big_ui (tici)
UI_W    = 2160
UI_H    = 1080
UI_FPS  = int(os.getenv("FPS", "20"))
STREAM_FPS = UI_FPS // (int(os.getenv("STREAM_UI_SKIP", "1")) + 1)

# Output resolution.  If STREAM_UI_W/H are set, application.py pre-scales the
# RGBA frames before writing to the FIFO — no resize step needed here.
OUT_W = int(os.getenv("STREAM_UI_W", "1080"))
OUT_H = int(os.getenv("STREAM_UI_H",  "540"))
_pre_scaled = bool(os.getenv("STREAM_UI_W")) and bool(os.getenv("STREAM_UI_H"))
FIFO_W = OUT_W if _pre_scaled else UI_W
FIFO_H = OUT_H if _pre_scaled else UI_H

FRAME_BYTES = FIFO_W * FIFO_H * 4   # RGBA


SC4 = b'\x00\x00\x00\x01'


def split_nal_units(data: bytes) -> tuple[bytes, bytes]:
  """Split annex-B H264 data into (sps_pps_header, frame_data).

  PyAV's libx264 encoder emits packets with 3-byte start codes (\x00\x00\x01)
  for non-first NAL units inside the same keyframe packet.  We must handle
  both 3- and 4-byte start codes when splitting.
  """
  # Normalise: replace 3-byte start codes with 4-byte so we can split on SC4
  normalised = data.replace(b'\x00\x00\x01', SC4)

  header = bytearray()
  frame_data = bytearray()
  for part in normalised.split(SC4):
    if not part:
      continue
    nal_type = part[0] & 0x1f
    chunk = SC4 + part
    if nal_type in (7, 8):   # SPS=7, PPS=8
      header.extend(chunk)
    else:
      frame_data.extend(chunk)
  return bytes(header), bytes(frame_data)


def make_encoder() -> av.CodecContext:
  ctx = av.CodecContext.create('libx264', 'w')
  ctx.width     = OUT_W
  ctx.height    = OUT_H
  ctx.pix_fmt   = 'yuv420p'
  ctx.time_base = fractions.Fraction(1, STREAM_FPS)
  ctx.framerate = fractions.Fraction(STREAM_FPS)
  ctx.bit_rate  = STREAM_BITRATE
  ctx.gop_size  = GOP_SIZE
  ctx.options   = {
    'preset':        'ultrafast',
    'tune':          'zerolatency',
    'x264-params':   'nal-hrd=cbr:force-cfr=1',
  }
  ctx.open()
  return ctx


def run(pm: messaging.PubMaster) -> None:
  if not os.path.exists(FIFO_PATH):
    os.mkfifo(FIFO_PATH)

  cloudlog.info(
    f"ui_streamd: opening FIFO {FIFO_PATH} ({FIFO_W}x{FIFO_H} RGBA "
    f"→ libx264 {OUT_W}x{OUT_H} @ {STREAM_FPS}fps, GOP={GOP_SIZE})"
  )

  # open() blocks until application.py opens the write end
  with open(FIFO_PATH, 'rb', buffering=0) as fifo:
    enc = make_encoder()
    frame_id   = 0
    encode_id  = 0
    pts        = 0

    while True:
      # Read one raw RGBA frame — blocks until the full frame is available
      raw = fifo.read(FRAME_BYTES)
      if len(raw) < FRAME_BYTES:
        cloudlog.warning("ui_streamd: FIFO closed or short read, restarting")
        break

      arr = np.frombuffer(raw, dtype=np.uint8).reshape(FIFO_H, FIFO_W, 4)

      # Vertical flip (raylib renders bottom-up)
      arr = arr[::-1]

      # Scale to output resolution if not pre-scaled by application.py
      if not _pre_scaled and (OUT_W != FIFO_W or OUT_H != FIFO_H):
        frame_in = av.VideoFrame.from_ndarray(arr, format='rgba')
        frame_yuv = frame_in.reformat(width=OUT_W, height=OUT_H, format='yuv420p')
      else:
        frame_in = av.VideoFrame.from_ndarray(arr, format='rgba')
        frame_yuv = frame_in.reformat(format='yuv420p')

      frame_yuv.pts = pts
      pts += 1

      for packet in enc.encode(frame_yuv):
        raw_pkt = bytes(packet)
        is_kf = bool(packet.is_keyframe)

        if is_kf:
          header, frame_data = split_nal_units(raw_pkt)
        else:
          header, frame_data = b"", raw_pkt

        msg = messaging.new_message("livestreamRoadEncodeData")
        edat = msg.livestreamRoadEncodeData
        edat.data   = frame_data
        edat.header = header
        edat.width  = OUT_W
        edat.height = OUT_H
        edat.unixTimestampNanos = int(time.time() * 1e9)

        idx = edat.idx
        idx.frameId          = frame_id
        idx.encodeId         = encode_id
        idx.segmentNum       = 0
        idx.segmentId        = frame_id
        idx.segmentIdEncode  = frame_id
        idx.flags            = V4L2_BUF_FLAG_KEYFRAME if is_kf else 0
        idx.len              = len(frame_data)

        pm.send("livestreamRoadEncodeData", msg)
        frame_id  += 1
        encode_id += 1


def main() -> None:
  pm = messaging.PubMaster(["livestreamRoadEncodeData"])
  while True:
    try:
      run(pm)
    except Exception as e:
      cloudlog.error(f"ui_streamd: restarting after error: {e}")
    time.sleep(1)


if __name__ == "__main__":
  main()
