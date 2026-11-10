"""Tests for the plugind REST API that COD calls."""
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from openpilot.selfdrive.plugins import api


class FakeRegistry:
  def __init__(self):
    self.plugins = {'mapd': object()}
    self.calls = []

  def get_status(self):
    return [{'id': 'mapd', 'enabled': True}]

  def set_enabled(self, plugin_id, enabled):
    self.calls.append(('set_enabled', plugin_id, enabled))

  def load_plugin(self, plugin_id):
    self.calls.append(('load', plugin_id))
    return True

  def unload_plugin(self, plugin_id):
    self.calls.append(('unload', plugin_id))


@pytest.fixture
def server(monkeypatch):
  registry = FakeRegistry()
  monkeypatch.setattr(api, '_registry', registry)
  httpd = HTTPServer(('127.0.0.1', 0), api.PluginAPIHandler)
  thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
  thread.start()
  yield f'http://127.0.0.1:{httpd.server_address[1]}', registry
  httpd.shutdown()
  httpd.server_close()


def _request(url, method='GET'):
  req = urllib.request.Request(url, method=method)
  try:
    with urllib.request.urlopen(req, timeout=5) as resp:
      return resp.status, json.loads(resp.read())
  except urllib.error.HTTPError as e:
    return e.code, json.loads(e.read())


def test_list_plugins(server):
  base, _ = server
  assert _request(f'{base}/v1/plugins') == (200, [{'id': 'mapd', 'enabled': True}])


def test_enable(server):
  base, registry = server
  assert _request(f'{base}/v1/plugins/mapd/enable', 'PUT') == (200, {'id': 'mapd', 'enabled': True})
  assert registry.calls == [('set_enabled', 'mapd', True), ('load', 'mapd')]


def test_disable(server):
  base, registry = server
  assert _request(f'{base}/v1/plugins/mapd/disable', 'PUT') == (200, {'id': 'mapd', 'enabled': False})
  assert registry.calls == [('set_enabled', 'mapd', False), ('unload', 'mapd')]


def test_unknown_plugin_is_404(server):
  base, registry = server
  status, body = _request(f'{base}/v1/plugins/nope/enable', 'PUT')
  assert status == 404 and 'error' in body
  assert registry.calls == []


@pytest.mark.parametrize('method,path', [
  ('GET', '/v1/plugins/status'),
  ('GET', '/v1/plugins/mapd/config'),
  ('PUT', '/v1/plugins/mapd/config'),
  ('PUT', '/v1/plugins/enable'),
])
def test_unknown_path_is_404(server, method, path):
  base, registry = server
  status, body = _request(base + path, method)
  assert status == 404 and body == {'error': 'not found'}
  assert registry.calls == []
