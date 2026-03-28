"""Tests for plugin_bus — ZMQ pub/sub IPC."""
import os
import time
import pytest
import zmq

from openpilot.selfdrive.plugins.plugin_bus import PluginPub, PluginSub, BUS_DIR


@pytest.fixture(autouse=True)
def clean_bus():
  """Clean up IPC sockets before and after each test."""
  for f in os.listdir(BUS_DIR) if os.path.isdir(BUS_DIR) else []:
    os.unlink(os.path.join(BUS_DIR, f))
  yield
  for f in os.listdir(BUS_DIR) if os.path.isdir(BUS_DIR) else []:
    os.unlink(os.path.join(BUS_DIR, f))


class TestPubSub:
  def test_basic_send_recv(self):
    pub = PluginPub("test_basic")
    sub = PluginSub(["test_basic"])
    time.sleep(0.05)  # ZMQ connect handshake

    pub.send({"value": 42})
    time.sleep(0.01)

    result = sub.recv()
    assert result is not None
    topic, data = result
    assert topic == "test_basic"
    assert data["value"] == 42

    pub.close()
    sub.close()

  def test_recv_returns_none_when_empty(self):
    sub = PluginSub(["nonexistent"])
    assert sub.recv() is None
    sub.close()

  def test_multiple_messages(self):
    pub = PluginPub("test_multi")
    sub = PluginSub(["test_multi"])
    time.sleep(0.05)

    pub.send({"seq": 1})
    pub.send({"seq": 2})
    pub.send({"seq": 3})
    time.sleep(0.01)

    received = []
    for _ in range(10):
      msg = sub.recv()
      if msg is None:
        break
      received.append(msg[1]["seq"])

    assert received == [1, 2, 3]

    pub.close()
    sub.close()

  def test_multiple_topics(self):
    pub_a = PluginPub("topic_a")
    pub_b = PluginPub("topic_b")
    sub = PluginSub(["topic_a", "topic_b"])
    time.sleep(0.05)

    pub_a.send({"from": "a"})
    pub_b.send({"from": "b"})
    time.sleep(0.01)

    topics = set()
    for _ in range(10):
      msg = sub.recv()
      if msg is None:
        break
      topics.add(msg[0])

    assert topics == {"topic_a", "topic_b"}

    pub_a.close()
    pub_b.close()
    sub.close()

  def test_multiple_subscribers(self):
    pub = PluginPub("test_fans")
    sub1 = PluginSub(["test_fans"])
    sub2 = PluginSub(["test_fans"])
    time.sleep(0.05)

    pub.send({"msg": "hello"})
    time.sleep(0.01)

    r1 = sub1.recv()
    r2 = sub2.recv()
    assert r1 is not None
    assert r2 is not None
    assert r1[1]["msg"] == "hello"
    assert r2[1]["msg"] == "hello"

    pub.close()
    sub1.close()
    sub2.close()


class TestPoll:
  def test_poll_timeout_no_message(self):
    sub = PluginSub(["poll_empty"])
    start = time.monotonic()
    result = sub.poll(timeout_ms=50)
    elapsed = time.monotonic() - start
    assert result is None
    assert elapsed >= 0.04
    sub.close()

  def test_poll_receives_message(self):
    pub = PluginPub("poll_msg")
    sub = PluginSub(["poll_msg"])
    time.sleep(0.05)

    pub.send({"status": "ok"})
    result = sub.poll(timeout_ms=200)
    assert result is not None
    assert result[1]["status"] == "ok"

    pub.close()
    sub.close()


class TestDrain:
  def test_drain_returns_last(self):
    pub = PluginPub("drain_test")
    sub = PluginSub(["drain_test"])
    time.sleep(0.05)

    pub.send({"v": 1})
    pub.send({"v": 2})
    pub.send({"v": 3})
    time.sleep(0.01)

    result = sub.drain()
    assert result is not None
    assert result[1]["v"] == 3

    # Queue should be empty now
    assert sub.recv() is None

    pub.close()
    sub.close()

  def test_drain_with_topic_filter(self):
    pub_a = PluginPub("drain_a")
    pub_b = PluginPub("drain_b")
    sub = PluginSub(["drain_a", "drain_b"])
    time.sleep(0.05)

    pub_a.send({"v": 10})
    pub_b.send({"v": 20})
    pub_a.send({"v": 30})
    time.sleep(0.01)

    result = sub.drain(topic="drain_a")
    assert result is not None
    assert result[1]["v"] == 30

    pub_a.close()
    pub_b.close()
    sub.close()

  def test_drain_returns_none_when_empty(self):
    sub = PluginSub(["drain_empty"])
    assert sub.drain() is None
    sub.close()


class TestEdgeCases:
  def test_stale_socket_cleanup(self):
    """Publisher cleans up stale IPC socket from previous run."""
    os.makedirs(BUS_DIR, exist_ok=True)
    stale = os.path.join(BUS_DIR, "stale_topic")
    with open(stale, "w") as f:
      f.write("")

    pub = PluginPub("stale_topic")
    assert os.path.exists(stale)  # ZMQ recreated it
    pub.close()

  def test_complex_data_types(self):
    pub = PluginPub("test_types")
    sub = PluginSub(["test_types"])
    time.sleep(0.05)

    data = {
      "int": 42,
      "float": 3.14,
      "string": "hello",
      "bool": True,
      "null": None,
      "list": [1, 2, 3],
      "nested": {"a": {"b": 1}},
    }
    pub.send(data)
    time.sleep(0.01)

    result = sub.recv()
    assert result is not None
    _, received = result
    assert received["int"] == 42
    assert received["float"] == pytest.approx(3.14)
    assert received["nested"]["a"]["b"] == 1

    pub.close()
    sub.close()
