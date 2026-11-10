"""REST API for COD integration.

Runs inside plugind, serves on localhost for COD to list plugins and toggle them:
  GET /v1/plugins                 status of every discovered plugin
  PUT /v1/plugins/<id>/enable     remove the .disabled marker and load the plugin
  PUT /v1/plugins/<id>/disable    write the .disabled marker and unload the plugin
Lightweight HTTP server — no external dependencies.
"""
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

from openpilot.common.swaglog import cloudlog

# Set by plugind before starting the server
_registry = None
API_PORT = 8083


def set_registry(registry):
  global _registry
  _registry = registry


class PluginAPIHandler(BaseHTTPRequestHandler):
  def log_message(self, format, *args):
    pass  # Suppress default HTTP logging

  def _send_json(self, data, status=200):
    self.send_response(status)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Access-Control-Allow-Origin', '*')
    self.end_headers()
    self.wfile.write(json.dumps(data).encode())

  def do_OPTIONS(self):
    self.send_response(200)
    self.send_header('Access-Control-Allow-Origin', '*')
    self.send_header('Access-Control-Allow-Methods', 'GET, PUT')
    self.send_header('Access-Control-Allow-Headers', 'Content-Type')
    self.end_headers()

  def do_GET(self):
    if _registry is None:
      self._send_json({'error': 'registry not initialized'}, 503)
      return

    if self.path.rstrip('/') == '/v1/plugins':
      self._send_json(_registry.get_status())
    else:
      self._send_json({'error': 'not found'}, 404)

  def do_PUT(self):
    if _registry is None:
      self._send_json({'error': 'registry not initialized'}, 503)
      return

    # /v1/plugins/<id>/enable or /v1/plugins/<id>/disable
    parts = self.path.rstrip('/').split('/')
    if len(parts) != 5 or parts[:3] != ['', 'v1', 'plugins'] or parts[4] not in ('enable', 'disable'):
      self._send_json({'error': 'not found'}, 404)
      return

    plugin_id = parts[3]
    if plugin_id not in _registry.plugins:
      self._send_json({'error': 'plugin not found'}, 404)
      return

    enabled = parts[4] == 'enable'
    _registry.set_enabled(plugin_id, enabled)
    if enabled:
      _registry.load_plugin(plugin_id)
    else:
      _registry.unload_plugin(plugin_id)
    self._send_json({'id': plugin_id, 'enabled': enabled})


def start_api_server():
  """Start the plugin API server in a background thread."""
  try:
    server = HTTPServer(('127.0.0.1', API_PORT), PluginAPIHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cloudlog.info(f"Plugin API server started on port {API_PORT}")
    return server
  except Exception:
    cloudlog.exception("Failed to start plugin API server")
    return None
