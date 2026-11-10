"""Tests for lazy per-process plugin loading, dependency/conflict checks and process listing."""
import json
import os

from unittest.mock import patch, MagicMock
from openpilot.selfdrive.plugins.hooks import HookRegistry


class TestLazyLoading:
  """Verify that _ensure_loaded triggers plugin discovery on first run() call."""

  def test_lazy_loading_not_triggered_before_run(self):
    hooks = HookRegistry()
    assert not hooks._loaded

  def test_lazy_loading_triggered_on_first_run(self):
    hooks = HookRegistry()
    with patch('openpilot.selfdrive.plugins.registry.PluginRegistry') as MockRegistry:
      mock_instance = MagicMock()
      MockRegistry.return_value = mock_instance
      hooks.run('test.hook', 42)
      assert hooks._loaded
      mock_instance.discover.assert_called_once()
      mock_instance.load_enabled.assert_called_once()

  def test_lazy_loading_only_once(self):
    hooks = HookRegistry()
    with patch('openpilot.selfdrive.plugins.registry.PluginRegistry') as MockRegistry:
      mock_instance = MagicMock()
      MockRegistry.return_value = mock_instance
      hooks.run('test.hook', 1)
      hooks.run('test.hook', 2)
      hooks.run('test.hook', 3)
      # discover() should only be called once despite 3 run() calls
      assert mock_instance.discover.call_count == 1

  def test_lazy_loading_failure_is_safe(self):
    hooks = HookRegistry()
    with patch('openpilot.selfdrive.plugins.registry.PluginRegistry', side_effect=ImportError("no plugins")):
      # Should not raise, should return default
      result = hooks.run('test.hook', 99)
      assert result == 99
      assert hooks._loaded  # Still marked as loaded to prevent retry storm

  def test_register_before_run_works(self):
    """Hooks registered directly (e.g., in tests) work without lazy loading."""
    hooks = HookRegistry()
    hooks.register('test.direct', 'test-plugin', lambda v: v + 1)
    # Set loaded to prevent lazy load attempt
    hooks._loaded = True
    result = hooks.run('test.direct', 0)
    assert result == 1


class TestMultipleRegistrants:
  """Verify that multiple hooks on the same point work correctly."""

  def test_multiple_hooks_allowed(self):
    hooks = HookRegistry()
    hooks._loaded = True

    hooks.register('controls.curvature_correction', 'plugin-a', lambda v: v + 1)
    hooks.register('controls.curvature_correction', 'plugin-b', lambda v: v + 2)

    # Both should be registered
    registered = hooks.get_registered_hooks()
    assert len(registered['controls.curvature_correction']) == 2


class TestDependencyChecking:
  """Verify manifest dependency and conflict checking."""

  def test_check_dependencies_satisfied(self):
    from openpilot.selfdrive.plugins.manifest import check_dependencies
    manifest = {'dependencies': ['plugin-a', 'plugin-b']}
    loaded = {'plugin-a', 'plugin-b', 'plugin-c'}
    ok, reason = check_dependencies(manifest, loaded)
    assert ok
    assert reason == ""

  def test_check_dependencies_missing(self):
    from openpilot.selfdrive.plugins.manifest import check_dependencies
    manifest = {'dependencies': ['plugin-a', 'plugin-missing']}
    loaded = {'plugin-a'}
    ok, reason = check_dependencies(manifest, loaded)
    assert not ok
    assert 'plugin-missing' in reason

  def test_check_dependencies_empty(self):
    from openpilot.selfdrive.plugins.manifest import check_dependencies
    manifest = {}
    loaded = set()
    ok, reason = check_dependencies(manifest, loaded)
    assert ok

  def test_check_conflicts_none(self):
    from openpilot.selfdrive.plugins.manifest import check_conflicts
    manifest = {'conflicts': ['enemy-plugin']}
    loaded = {'friend-plugin'}
    ok, reason = check_conflicts(manifest, loaded)
    assert ok

  def test_check_conflicts_detected(self):
    from openpilot.selfdrive.plugins.manifest import check_conflicts
    manifest = {'conflicts': ['enemy-plugin']}
    loaded = {'enemy-plugin', 'friend-plugin'}
    ok, reason = check_conflicts(manifest, loaded)
    assert not ok
    assert 'enemy-plugin' in reason

  def test_check_conflicts_empty(self):
    from openpilot.selfdrive.plugins.manifest import check_conflicts
    manifest = {}
    loaded = {'any-plugin'}
    ok, reason = check_conflicts(manifest, loaded)
    assert ok


class TestManifestTypes:
  """Verify new plugin types are accepted."""

  def test_car_type_valid(self):
    from openpilot.selfdrive.plugins.manifest import load_manifest
    import tempfile
    with tempfile.TemporaryDirectory() as d:
      manifest = {'id': 'test', 'name': 'Test', 'version': '1.0.0', 'type': 'car'}
      with open(os.path.join(d, 'plugin.json'), 'w') as f:
        json.dump(manifest, f)
      result = load_manifest(d)
      assert result is not None
      assert result['type'] == 'car'

  def test_firmware_type_valid(self):
    from openpilot.selfdrive.plugins.manifest import load_manifest
    import tempfile
    with tempfile.TemporaryDirectory() as d:
      manifest = {'id': 'test', 'name': 'Test', 'version': '1.0.0', 'type': 'firmware'}
      with open(os.path.join(d, 'plugin.json'), 'w') as f:
        json.dump(manifest, f)
      result = load_manifest(d)
      assert result is not None
      assert result['type'] == 'firmware'

  def test_invalid_type_rejected(self):
    from openpilot.selfdrive.plugins.manifest import load_manifest
    import tempfile
    with tempfile.TemporaryDirectory() as d:
      manifest = {'id': 'test', 'name': 'Test', 'version': '1.0.0', 'type': 'invalid'}
      with open(os.path.join(d, 'plugin.json'), 'w') as f:
        json.dump(manifest, f)
      result = load_manifest(d)
      assert result is None


class TestRegistryDependencyOrdering:
  """Verify that load_enabled respects dependency ordering."""

  def test_dependency_blocks_loading(self, tmp_path):
    """Plugin with unmet dependency should not load."""
    from openpilot.selfdrive.plugins.registry import PluginRegistry
    from openpilot.selfdrive.plugins import hooks as hooks_module
    original_hooks = hooks_module.hooks
    hooks_module.hooks = HookRegistry()
    hooks_module.hooks._loaded = True

    try:
      # Create plugin with dependency
      plugin_dir = tmp_path / 'dependent'
      plugin_dir.mkdir()
      manifest = {
        'id': 'dependent',
        'name': 'Dependent Plugin',
        'version': '1.0.0',
        'type': 'hook',
        'dependencies': ['missing-plugin'],
        'hooks': {}
      }
      (plugin_dir / 'plugin.json').write_text(json.dumps(manifest))

      registry = PluginRegistry(str(tmp_path))
      registry.discover()
      result = registry.load_plugin('dependent')
      assert not result
      assert 'Dependency' in registry.plugins['dependent'].error
    finally:
      hooks_module.hooks = original_hooks

  def test_conflict_blocks_loading(self, tmp_path):
    """Plugin conflicting with loaded plugin should not load."""
    from openpilot.selfdrive.plugins.registry import PluginRegistry
    from openpilot.selfdrive.plugins import hooks as hooks_module
    original_hooks = hooks_module.hooks
    hooks_module.hooks = HookRegistry()
    hooks_module.hooks._loaded = True

    try:
      # Create "enemy" plugin (already loaded)
      enemy_dir = tmp_path / 'enemy'
      enemy_dir.mkdir()
      (enemy_dir / 'plugin.json').write_text(json.dumps({
        'id': 'enemy', 'name': 'Enemy', 'version': '1.0.0', 'type': 'hook', 'hooks': {}
      }))

      # Create "new" plugin that conflicts with enemy
      new_dir = tmp_path / 'new_plugin'
      new_dir.mkdir()
      (new_dir / 'plugin.json').write_text(json.dumps({
        'id': 'new_plugin', 'name': 'New', 'version': '1.0.0', 'type': 'hook',
        'conflicts': ['enemy'], 'hooks': {}
      }))

      registry = PluginRegistry(str(tmp_path))
      registry.discover()

      # Load enemy first
      registry.plugins['enemy'].loaded = True  # Simulate already loaded
      registry.plugins['enemy'].enabled = True

      # Try to load conflicting plugin
      result = registry.load_plugin('new_plugin')
      assert not result
      assert 'Conflict' in registry.plugins['new_plugin'].error
    finally:
      hooks_module.hooks = original_hooks

  @patch('openpilot.selfdrive.plugins.registry.PluginRegistry.is_enabled', return_value=True)
  def test_unmet_dependency_keeps_error_after_load_enabled(self, mock_enabled, tmp_path):
    """load_enabled() must leave the dependency reason in place for get_status()."""
    from openpilot.selfdrive.plugins.registry import PluginRegistry
    from openpilot.selfdrive.plugins import hooks as hooks_module
    original_hooks = hooks_module.hooks
    hooks_module.hooks = HookRegistry()
    hooks_module.hooks._loaded = True

    try:
      plugin_dir = tmp_path / 'dependent'
      plugin_dir.mkdir()
      (plugin_dir / 'plugin.json').write_text(json.dumps({
        'id': 'dependent', 'name': 'Dependent Plugin', 'version': '1.0.0', 'type': 'hook',
        'dependencies': ['missing-plugin'], 'hooks': {}
      }))

      registry = PluginRegistry(str(tmp_path))
      registry.discover()
      registry.load_enabled()
      info = registry.plugins['dependent']
      assert not info.loaded
      assert info.error is not None and 'Dependency' in info.error
    finally:
      hooks_module.hooks = original_hooks


class TestStandaloneProcesses:
  """Verify the process list plugind supervises."""

  def test_empty(self, tmp_path):
    from openpilot.selfdrive.plugins.registry import PluginRegistry
    registry = PluginRegistry(str(tmp_path))
    assert registry.get_standalone_processes() == []

  def test_every_declared_process_is_returned(self, tmp_path):
    from openpilot.selfdrive.plugins.registry import PluginRegistry, PluginInfo
    registry = PluginRegistry(str(tmp_path))

    manifest = {
      'id': 'speedlimitd',
      'name': 'Speed Limit',
      'version': '1.0.0',
      'type': 'hybrid',
      'processes': [
        {'name': 'speedlimitd', 'module': 'speedlimitd', 'condition': 'only_onroad',
         'requires': {'services': ['carState']}},
        {'name': 'helper', 'module': 'helper', 'replace': True},
      ]
    }
    info = PluginInfo(manifest, str(tmp_path))
    info.loaded = True
    registry.plugins['speedlimitd'] = info

    procs = {p['name']: p for p in registry.get_standalone_processes()}
    assert set(procs) == {'speedlimitd', 'helper'}
    assert procs['speedlimitd']['condition'] == 'only_onroad'
    assert procs['speedlimitd']['requires'] == {'services': ['carState']}
    assert procs['helper']['condition'] == 'always_run'
    assert procs['helper']['requires'] == []
    assert procs['helper']['plugin_dir'] == str(tmp_path)
