"""REST API for COD integration.

Runs inside plugind, serves on localhost for COD to manage plugins.
Lightweight HTTP server — no external dependencies.
"""
import json
import os
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

  def _read_body(self) -> dict:
    length = int(self.headers.get('Content-Length', 0))
    if length == 0:
      return {}
    return json.loads(self.rfile.read(length))

  def do_OPTIONS(self):
    self.send_response(200)
    self.send_header('Access-Control-Allow-Origin', '*')
    self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE')
    self.send_header('Access-Control-Allow-Headers', 'Content-Type')
    self.end_headers()

  def do_GET(self):
    if _registry is None:
      self._send_json({'error': 'registry not initialized'}, 503)
      return

    path = self.path.rstrip('/')

    if path == '/v1/plugins':
      self._send_json(_registry.get_status())

    elif path == '/v1/plugins/available':
      # List available plugins from known sources
      self._send_json(_list_available_plugins())

    elif path == '/v1/plugins/status':
      from openpilot.selfdrive.plugins.hooks import hooks
      self._send_json({
        'plugins': _registry.get_status(),
        'hooks': hooks.get_registered_hooks(),
        'conflicts': hooks.get_conflicts(),
        'process_overrides': _registry.get_process_overrides(),
      })

    elif path.startswith('/v1/plugins/') and path.endswith('/config'):
      plugin_id = path.split('/')[3]
      info = _registry.plugins.get(plugin_id)
      if info is None:
        self._send_json({'error': 'plugin not found'}, 404)
      else:
        self._send_json(_get_plugin_config(info))

    else:
      self._send_json({'error': 'not found'}, 404)

  def do_POST(self):
    if _registry is None:
      self._send_json({'error': 'registry not initialized'}, 503)
      return

    path = self.path.rstrip('/')

    if path == '/v1/plugins/install':
      body = self._read_body()
      url = body.get('url', '')
      plugin_id = body.get('id')
      if not url:
        self._send_json({'error': 'url required'}, 400)
        return
      result = _registry.install_plugin(url, plugin_id)
      if result:
        self._send_json({'id': result, 'status': 'installed'})
      else:
        self._send_json({'error': 'install failed'}, 500)

    elif path.startswith('/v1/plugins/') and path.endswith('/update'):
      plugin_id = path.split('/')[3]
      info = _registry.plugins.get(plugin_id)
      if info is None:
        self._send_json({'error': 'plugin not found'}, 404)
        return
      # Re-install from same location (git pull)
      result = _registry.install_plugin(info.plugin_dir, plugin_id)
      if result:
        self._send_json({'id': result, 'status': 'updated'})
      else:
        self._send_json({'error': 'update failed'}, 500)

    else:
      self._send_json({'error': 'not found'}, 404)

  def do_PUT(self):
    if _registry is None:
      self._send_json({'error': 'registry not initialized'}, 503)
      return

    path = self.path.rstrip('/')

    if path.startswith('/v1/plugins/') and path.endswith('/enable'):
      plugin_id = path.split('/')[3]
      if plugin_id not in _registry.plugins:
        self._send_json({'error': 'plugin not found'}, 404)
        return
      _registry.set_enabled(plugin_id, True)
      _registry.load_plugin(plugin_id)
      self._send_json({'id': plugin_id, 'enabled': True})

    elif path.startswith('/v1/plugins/') and path.endswith('/disable'):
      plugin_id = path.split('/')[3]
      if plugin_id not in _registry.plugins:
        self._send_json({'error': 'plugin not found'}, 404)
        return
      _registry.set_enabled(plugin_id, False)
      _registry.unload_plugin(plugin_id)
      self._send_json({'id': plugin_id, 'enabled': False})

    elif path.startswith('/v1/plugins/') and path.endswith('/config'):
      plugin_id = path.split('/')[3]
      info = _registry.plugins.get(plugin_id)
      if info is None:
        self._send_json({'error': 'plugin not found'}, 404)
        return
      body = self._read_body()
      _update_plugin_config(info, body)
      self._send_json({'id': plugin_id, 'config': body})

    else:
      self._send_json({'error': 'not found'}, 404)

  def do_DELETE(self):
    if _registry is None:
      self._send_json({'error': 'registry not initialized'}, 503)
      return

    path = self.path.rstrip('/')

    if path.startswith('/v1/plugins/'):
      plugin_id = path.split('/')[3]
      if _registry.uninstall_plugin(plugin_id):
        self._send_json({'id': plugin_id, 'status': 'uninstalled'})
      else:
        self._send_json({'error': 'plugin not found'}, 404)
    else:
      self._send_json({'error': 'not found'}, 404)


def _list_available_plugins() -> list[dict]:
  """List plugins available from c3pilot-plugins repo."""
  manifest_path = '/data/c3pilot-plugins/manifest.json'
  if os.path.exists(manifest_path):
    try:
      with open(manifest_path) as f:
        return json.load(f).get('plugins', [])
    except (json.JSONDecodeError, OSError):
      pass
  return []


def _plugin_data_dir(info) -> str:
  """Return the plugin's data directory path."""
  return os.path.join(info.plugin_dir, 'data')


def _get_plugin_config(info) -> dict:
  """Get current config values from plugin data dir.

  Plugin params are stored at <plugin_dir>/data/<key>, NOT in openpilot's
  Params (/data/params/d/) — those get wiped by Params::clearAll on boot.
  """
  data_dir = _plugin_data_dir(info)
  config = {}
  for key, schema in info.manifest.get('params', {}).items():
    param_file = os.path.join(data_dir, key)
    try:
      raw = open(param_file).read().strip()
      config[key] = raw
    except (FileNotFoundError, OSError):
      config[key] = schema.get('default')
  return config


def _update_plugin_config(info, updates: dict):
  """Update config values in plugin data dir."""
  data_dir = _plugin_data_dir(info)
  os.makedirs(data_dir, exist_ok=True)
  valid_keys = set(info.manifest.get('params', {}).keys())
  for key, value in updates.items():
    if key in valid_keys:
      param_file = os.path.join(data_dir, key)
      if isinstance(value, bool):
        open(param_file, 'w').write("1" if value else "0")
      else:
        open(param_file, 'w').write(str(value))


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
