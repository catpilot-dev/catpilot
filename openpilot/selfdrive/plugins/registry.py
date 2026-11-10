"""Plugin discovery, loading, and lifecycle management.

Scans /data/plugins-runtime/ for installed plugins, validates manifests,
loads enabled plugins, and manages their lifecycle.

Supports plugin types: hook, process, hybrid, car, firmware
Handles: dependency ordering, conflict detection, device filtering,
         standalone process listing for plugind, and per-process lazy loading.
"""
import importlib
import importlib.util
import os
import sys

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.plugins import hooks as hooks_module
from openpilot.selfdrive.plugins.manifest import load_manifest, check_compatibility, \
  check_dependencies, check_conflicts

PLUGINS_DIR = '/data/plugins-runtime'


class PluginInfo:
  """Runtime state for a loaded plugin."""

  def __init__(self, manifest: dict, plugin_dir: str):
    self.manifest = manifest
    self.plugin_dir = plugin_dir
    self.id = manifest['id']
    self.name = manifest['name']
    self.version = manifest['version']
    self.type = manifest['type']
    self.enabled = False
    self.loaded = False
    self.error: str | None = None
    self.module = None


class PluginRegistry:
  """Discovers, loads, and manages plugin lifecycle."""

  def __init__(self, plugins_dir: str = PLUGINS_DIR):
    self.plugins_dir = plugins_dir
    self.plugins: dict[str, PluginInfo] = {}

  def discover(self) -> list[str]:
    """Scan plugins directory and load manifests. Returns list of plugin IDs found."""
    if not os.path.isdir(self.plugins_dir):
      return []

    discovered = []
    for entry in os.listdir(self.plugins_dir):
      plugin_dir = os.path.join(self.plugins_dir, entry)
      if not os.path.isdir(plugin_dir):
        continue

      manifest = load_manifest(plugin_dir)
      if manifest is None:
        continue

      plugin_id = manifest['id']
      if not check_compatibility(manifest):
        cloudlog.warning(f"Plugin '{plugin_id}' incompatible, skipping")
        continue

      self.plugins[plugin_id] = PluginInfo(manifest, plugin_dir)
      discovered.append(plugin_id)
      cloudlog.info(f"Discovered plugin: {plugin_id} v{manifest['version']}")

    return discovered

  def is_enabled(self, plugin_id: str) -> bool:
    """Check if a plugin is enabled (no .disabled marker in plugin dir)."""
    info = self.plugins.get(plugin_id)
    if info is None:
      return False
    return not os.path.exists(os.path.join(info.plugin_dir, '.disabled'))

  def set_enabled(self, plugin_id: str, enabled: bool):
    """Enable or disable a plugin via .disabled file marker."""
    info = self.plugins.get(plugin_id)
    if info is None:
      return
    marker = os.path.join(info.plugin_dir, '.disabled')
    if enabled:
      if os.path.exists(marker):
        os.remove(marker)
    else:
      with open(marker, 'w') as f:
        f.write('')

  def load_plugin(self, plugin_id: str) -> bool:
    """Load a plugin module and register its hooks.

    Checks dependencies and conflicts before loading.
    Supports plugin types: hook, process, hybrid, car, firmware.

    Hook/car plugins define hooks in their manifest:
      "hooks": {
        "controls.curvature_correction": {
          "module": "correction",
          "function": "on_curvature_correction",
          "priority": 50
        }
      }

    Process/hybrid plugins also declare processes, which plugind spawns
    (see get_standalone_processes()); loading does not start them:
      "processes": [{"name": "speedlimitd", "module": "speedlimitd", "condition": "only_onroad"}]
    """
    info = self.plugins.get(plugin_id)
    if info is None:
      cloudlog.error(f"Plugin '{plugin_id}' not found")
      return False

    if info.loaded:
      return True

    plugin_dir = info.plugin_dir
    manifest = info.manifest

    # Check dependencies — all required plugins must be loaded
    loaded_ids = {pid for pid, pi in self.plugins.items() if pi.loaded}
    deps_ok, deps_reason = check_dependencies(manifest, loaded_ids)
    if not deps_ok:
      info.error = f"Dependency check failed: {deps_reason}"
      cloudlog.warning(f"Plugin '{plugin_id}' cannot load: {deps_reason}")
      return False

    # Check conflicts — no conflicting plugins may be loaded
    conflicts_ok, conflicts_reason = check_conflicts(manifest, loaded_ids)
    if not conflicts_ok:
      info.error = f"Conflict: {conflicts_reason}"
      cloudlog.warning(f"Plugin '{plugin_id}' cannot load: {conflicts_reason}")
      return False

    try:
      # Add plugins root + plugin dir to sys.path for imports
      # (root is needed for shared config.py, plugin dir for local modules)
      for p in (self.plugins_dir, plugin_dir):
        if p not in sys.path:
          sys.path.insert(0, p)

      # Load hook callbacks from manifest (hook, hybrid, car, firmware types)
      plugin_type = manifest['type']
      if plugin_type in ('hook', 'hybrid', 'car', 'firmware'):
        loaded_modules: dict[str, object] = {}
        for hook_name, hook_def in manifest.get('hooks', {}).items():
          module_name = hook_def['module']
          func_name = hook_def['function']
          priority = hook_def.get('priority', 50)

          # Reuse already-loaded module when multiple hooks share the same file
          canonical_name = f"plugins.{plugin_id}.{module_name}"
          if canonical_name not in loaded_modules:
            # Resolve module path — support nested modules (e.g., "bmw.register")
            module_file = os.path.join(plugin_dir, *module_name.split('.'))
            if os.path.isdir(module_file):
              module_file = os.path.join(module_file, '__init__.py')
            else:
              module_file += '.py'

            # Use canonical dotted path so regular imports find the same instance
            spec = importlib.util.spec_from_file_location(
              canonical_name,
              module_file
            )
            if spec is None or spec.loader is None:
              raise ImportError(f"Cannot find module '{module_name}' in {plugin_dir}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[canonical_name] = module
            spec.loader.exec_module(module)
            loaded_modules[canonical_name] = module

          module = loaded_modules[canonical_name]
          callback = getattr(module, func_name)
          hooks_module.hooks.register(hook_name, plugin_id, callback, priority)
          info.module = module

      info.loaded = True
      info.enabled = True
      info.error = None
      cloudlog.info(f"Loaded plugin: {plugin_id}")
      return True

    except Exception as e:
      info.error = str(e)
      info.loaded = False
      cloudlog.exception(f"Failed to load plugin '{plugin_id}'")
      return False

  def unload_plugin(self, plugin_id: str):
    """Unload a plugin and unregister its hooks."""
    info = self.plugins.get(plugin_id)
    if info is None or not info.loaded:
      return

    hooks_module.hooks.unregister_all(plugin_id)

    # Remove plugin modules from sys.modules so a subsequent load_plugin()
    # creates fresh instances instead of reusing stale ones.
    prefix = f"plugins.{plugin_id}."
    for key in [k for k in sys.modules if k.startswith(prefix)]:
      del sys.modules[key]

    info.loaded = False
    info.enabled = False
    info.module = None
    cloudlog.info(f"Unloaded plugin: {plugin_id}")

  def load_enabled(self):
    """Load all enabled plugins (no .disabled marker) and unload disabled ones.

    Uses dependency-aware ordering: plugins with no dependencies load first,
    then plugins whose dependencies are already loaded.
    Retries once to handle circular-free dependency chains.
    """
    # First pass: unload disabled plugins
    for plugin_id, info in list(self.plugins.items()):
      if not self.is_enabled(plugin_id) and info.loaded:
        self.unload_plugin(plugin_id)

    # Second pass: load enabled plugins with dependency ordering
    # Try up to 2 passes to resolve dependency chains (A depends on B)
    for _ in range(2):
      pending = False
      for plugin_id, info in self.plugins.items():
        if self.is_enabled(plugin_id) and not info.loaded:
          if self.load_plugin(plugin_id):
            continue
          # If failed due to dependency, retry next pass
          if info.error and 'Dependency' in info.error:
            pending = True
      if not pending:
        break

  def get_standalone_processes(self) -> list[dict]:
    """Return every process declared by loaded process/hybrid plugins.

    plugind spawns and supervises these itself, honouring each one's condition:
      [{"name": "mapd", "module": "mapd_runner", "plugin_id": "...", "plugin_dir": "...",
        "condition": "always_run", "requires": [...]}]
    """
    processes = []
    for plugin_id, info in self.plugins.items():
      if not info.loaded:
        continue
      manifest = info.manifest
      if manifest['type'] not in ('process', 'hybrid'):
        continue
      for proc_def in manifest.get('processes', []):
        processes.append({
          'name': proc_def['name'],
          'module': proc_def['module'],
          'plugin_id': plugin_id,
          'plugin_dir': info.plugin_dir,
          'condition': proc_def.get('condition', 'always_run'),
          'requires': proc_def.get('requires', []),
        })
    return processes

  def get_status(self) -> list[dict]:
    """Return status of all discovered plugins."""
    return [
      {
        'id': info.id,
        'name': info.name,
        'version': info.version,
        'type': info.type,
        'enabled': self.is_enabled(info.id),
        'loaded': info.loaded,
        'error': info.error,
        'hooks': list(info.manifest.get('hooks', {}).keys()),
        'params': info.manifest.get('params', {}),
        'dependencies': info.manifest.get('dependencies', []),
        'conflicts': info.manifest.get('conflicts', []),
        'device_filter': info.manifest.get('device_filter'),
      }
      for info in self.plugins.values()
    ]
