"""Cereal ↔ WebRTC data-channel bridge helpers.

These classes are shared between webrtcd and any plugin that provides
an alternative WebRTC session implementation via webrtc.session_factory.
"""

import asyncio
import json
import logging
from typing import Any, TYPE_CHECKING

import capnp

from cereal import messaging

if TYPE_CHECKING:
  from aiortc.rtcdatachannel import RTCDataChannel


class CerealOutgoingMessageProxy:
  """Polls a SubMaster and forwards updated cereal messages to WebRTC data channels."""

  def __init__(self, sm: messaging.SubMaster):
    self.sm = sm
    self.channels: list[RTCDataChannel] = []

  def add_channel(self, channel: 'RTCDataChannel'):
    self.channels.append(channel)

  def to_json(self, msg_content: Any):
    if isinstance(msg_content, capnp._DynamicStructReader):
      return msg_content.to_dict()
    if isinstance(msg_content, capnp._DynamicListReader):
      return [self.to_json(m) for m in msg_content]
    if isinstance(msg_content, bytes):
      return msg_content.decode()
    return msg_content

  def update(self):
    self.sm.update(0)
    for service, updated in self.sm.updated.items():
      if not updated:
        continue
      msg_dict = self.to_json(self.sm[service])
      outgoing = {
        "type": service,
        "logMonoTime": self.sm.logMonoTime[service],
        "valid": self.sm.valid[service],
        "data": msg_dict,
      }
      encoded = json.dumps(outgoing).encode()
      for ch in self.channels:
        ch.send(encoded)


class CerealIncomingMessageProxy:
  """Receives JSON messages from a WebRTC data channel and publishes them as cereal."""

  def __init__(self, pm: messaging.PubMaster):
    self.pm = pm

  def send(self, message: bytes):
    msg_json = json.loads(message)
    msg_type, msg_data = msg_json["type"], msg_json["data"]
    size = None
    if not isinstance(msg_data, dict):
      size = len(msg_data)
    msg = messaging.new_message(msg_type, size=size)
    setattr(msg, msg_type, msg_data)
    self.pm.send(msg_type, msg)


class CerealProxyRunner:
  """Async task wrapper that drives CerealOutgoingMessageProxy.update() at 100 Hz."""

  def __init__(self, proxy: CerealOutgoingMessageProxy):
    self.proxy = proxy
    self.task: asyncio.Task | None = None
    self._logger = logging.getLogger("webrtcd")

  def start(self):
    assert self.task is None
    self.task = asyncio.create_task(self._run())

  def stop(self):
    if self.task and not self.task.done():
      self.task.cancel()
    self.task = None

  async def _run(self):
    from aiortc.exceptions import InvalidStateError
    while True:
      try:
        self.proxy.update()
      except InvalidStateError:
        self._logger.warning("cereal outgoing proxy: data channel closed")
        break
      except Exception:
        self._logger.exception("cereal outgoing proxy error")
      await asyncio.sleep(0.01)


class DynamicPubMaster(messaging.PubMaster):
  """PubMaster that can lazily add sockets for new service names."""

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._lock = asyncio.Lock()

  async def add_services_if_needed(self, services):
    async with self._lock:
      for svc in services:
        if svc not in self.sock:
          self.sock[svc] = messaging.pub_sock(svc)
