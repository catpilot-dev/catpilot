#!/usr/bin/env python3
"""
ui_streamd: stream the raylib UI framebuffer via WebRTC.

Reads raw RGBA frames from a FIFO written by application.py (STREAM_UI=1),
encodes them with libx264 (software, TICI h264_v4l2m2m is not accessible via
ffmpeg), and publishes H264 packets to cereal as livestreamRoadEncodeData —
the same socket that webrtcd already reads for camera streaming.

Intended process config (catpilot phone-display branch):
  STREAM_UI=1 → run ui_streamd (always_run) + webrtcd (always_run)
  STREAM_UI=0 → run stream_encoderd (only_onroad) + webrtcd (only_onroad)
"""
import os
import sys
import time
import subprocess
import threading

import av

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import HARDWARE

FIFO_PATH = os.getenv("STREAM_UI_FIFO", "/tmp/ui_stream.fifo")
STREAM_BITRATE = int(os.getenv("STREAM_BITRATE", "1000000"))  # 1 Mbps for 1080x540
GOP_SIZE = 15
V4L2_BUF_FLAG_KEYFRAME = 8

# UI canvas dimensions — must match GuiApplication defaults for big_ui (tici)
UI_W = 2160
UI_H = 1080
UI_FPS = int(os.getenv("FPS", "20"))
STREAM_FPS = UI_FPS // (int(os.getenv("STREAM_UI_SKIP", "1")) + 1)  # matches application.py skip

# Output resolution: scale down 2x to reduce encoder load on C3's throttled CPU
OUT_W = int(os.getenv("STREAM_UI_W", "1080"))
OUT_H = int(os.getenv("STREAM_UI_H", "540"))

# If STREAM_UI_W/H are set, application.py pre-scales before writing to FIFO —
# ffmpeg reads at output dimensions and no scale step is needed.
_pre_scaled = bool(os.getenv("STREAM_UI_W")) and bool(os.getenv("STREAM_UI_H"))
FIFO_W = OUT_W if _pre_scaled else UI_W
FIFO_H = OUT_H if _pre_scaled else UI_H

# libx264: h264_v4l2m2m is not accessible via ffmpeg on TICI (Qualcomm-specific ioctl path)
H264_ENCODER = "libx264"


SC4 = b'\x00\x00\x00\x01'


def split_nal_units(data: bytes) -> tuple[bytes, bytes]:
  """Split annex-B keyframe data into (sps_pps_header, idr_and_remaining)."""
  parts = data.split(SC4)
  header = bytearray()
  frame_data = bytearray()
  for part in parts:
    if not part:
      continue
    nal_type = part[0] & 0x1f
    chunk = SC4 + part
    if nal_type in (7, 8):  # SPS=7, PPS=8
      header.extend(chunk)
    else:
      frame_data.extend(chunk)
  return bytes(header), bytes(frame_data)


def build_ffmpeg_cmd() -> list[str]:
  return [
    "ffmpeg",
    "-v", "warning",
    "-f", "rawvideo",
    "-pix_fmt", "rgba",
    "-s", f"{FIFO_W}x{FIFO_H}",
    "-r", str(STREAM_FPS),
    "-i", FIFO_PATH,
    "-vf", "vflip,format=yuv420p" if _pre_scaled else f"vflip,scale={OUT_W}:{OUT_H},format=yuv420p",
    "-c:v", H264_ENCODER,
    "-b:v", str(STREAM_BITRATE),
    "-g", str(GOP_SIZE),
    "-preset", "ultrafast",
    "-tune", "zerolatency",
    "-f", "h264",
    "pipe:1",
  ]


def run(pm: messaging.PubMaster) -> None:
  # Create FIFO if needed — application.py also creates it, but race is fine
  if not os.path.exists(FIFO_PATH):
    os.mkfifo(FIFO_PATH)

  cloudlog.info(f"ui_streamd: starting ffmpeg ({H264_ENCODER}) at {UI_W}x{UI_H}@{UI_FPS}fps")
  proc = subprocess.Popen(
    build_ffmpeg_cmd(),
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
  )

  frame_id = 0
  encode_id = 0
  sps_pps: bytes = b""  # cache latest SPS/PPS for reconnecting clients

  try:
    container = av.open(
      proc.stdout,
      format="h264",
      options={
        "fflags": "nobuffer",
        "analyzeduration": "0",
        "probesize": "32",
      },
    )
    stream = next(s for s in container.streams if s.type == "video")

    for packet in container.demux(stream):
      if packet.size == 0:
        break

      raw = bytes(packet)
      is_kf = bool(packet.is_keyframe)

      if is_kf:
        header, frame_data = split_nal_units(raw)
        if header:
          sps_pps = header  # update cached SPS/PPS
      else:
        header = b""
        frame_data = raw

      msg = messaging.new_message("livestreamRoadEncodeData")
      edat = msg.livestreamRoadEncodeData
      edat.data = frame_data
      edat.header = header
      edat.width = OUT_W
      edat.height = OUT_H
      edat.unixTimestampNanos = int(time.time() * 1e9)

      idx = edat.idx
      idx.frameId = frame_id
      idx.encodeId = encode_id
      idx.segmentNum = 0
      idx.segmentId = frame_id
      idx.segmentIdEncode = frame_id
      idx.flags = V4L2_BUF_FLAG_KEYFRAME if is_kf else 0
      idx.len = len(frame_data)

      pm.send("livestreamRoadEncodeData", msg)

      frame_id += 1
      encode_id += 1

  except Exception as e:
    cloudlog.error(f"ui_streamd: stream loop error: {e}")
  finally:
    proc.terminate()
    proc.wait()


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
