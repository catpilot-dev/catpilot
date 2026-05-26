"""Plugin message bus — lightweight ZMQ pub/sub for inter-plugin IPC.

Each topic is an IPC socket under /tmp/plugin_bus/. One publisher per topic,
multiple subscribers per topic. No broker process needed.

Usage:

  # Publisher (e.g. speedlimitd)
  pub = PluginPub("speedlimit")
  pub.send({"ceiling": 88.0, "lead_override": True})

  # Subscriber (e.g. UI)
  sub = PluginSub(["speedlimit", "model_selector"])
  msg = sub.recv()  # returns ("speedlimit", {"ceiling": 88.0, ...}) or None

Design choices:
  - ZMQ IPC (unix domain sockets) — already on C3, no new deps
  - JSON serialization — stdlib, no msgpack dependency
  - No broker — each topic socket is independent
  - Late joiners miss nothing important — publishers send state at regular Hz
  - /tmp cleans up on reboot
"""
import json
import os

import zmq

BUS_DIR = "/tmp/plugin_bus"


class PluginPub:
  """Publish messages on a named topic.

  One publisher per topic. Bind happens on init; subscribers connect.
  """

  def __init__(self, topic: str):
    os.makedirs(BUS_DIR, exist_ok=True)
    self.topic = topic
    self._ctx = zmq.Context.instance()
    self._sock = self._ctx.socket(zmq.PUB)
    ipc_path = os.path.join(BUS_DIR, topic)
    # Remove stale socket file from previous run
    if os.path.exists(ipc_path):
      os.unlink(ipc_path)
    self._sock.bind(f"ipc://{ipc_path}")

  def send(self, data: dict):
    """Publish a dict. Topic is embedded in the payload."""
    payload = json.dumps({"_t": self.topic, **data}).encode()
    self._sock.send(payload)

  def close(self):
    self._sock.close(linger=0)


class PluginSub:
  """Subscribe to one or more topics.

  Connects to each topic's IPC socket. Non-blocking recv by default.
  """

  def __init__(self, topics: list[str]):
    self._ctx = zmq.Context.instance()
    self._sock = self._ctx.socket(zmq.SUB)
    self._sock.subscribe(b"")  # accept all from connected endpoints
    self._sock.setsockopt(zmq.RCVTIMEO, 0)  # non-blocking
    for t in topics:
      self._sock.connect(f"ipc://{os.path.join(BUS_DIR, t)}")

  def recv(self) -> tuple[str, dict] | None:
    """Non-blocking receive. Returns (topic, data) or None."""
    try:
      raw = self._sock.recv()
      msg = json.loads(raw)
      topic = msg.pop("_t", "unknown")
      return topic, msg
    except zmq.Again:
      return None

  def poll(self, timeout_ms: int = 100) -> tuple[str, dict] | None:
    """Blocking receive with timeout. Returns (topic, data) or None."""
    if self._sock.poll(timeout_ms):
      try:
        raw = self._sock.recv()
        msg = json.loads(raw)
        topic = msg.pop("_t", "unknown")
        return topic, msg
      except zmq.Again:
        return None
    return None

  def drain(self, topic: str | None = None) -> dict | None:
    """Read all pending messages, return the last one (most recent state).

    Useful for subscribers that only care about latest state, not every update.
    Optionally filter by topic.
    """
    last = None
    while True:
      msg = self.recv()
      if msg is None:
        break
      if topic is None or msg[0] == topic:
        last = msg
    return last

  def close(self):
    self._sock.close(linger=0)
