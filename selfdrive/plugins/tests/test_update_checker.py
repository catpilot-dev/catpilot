"""Tests for the ecosystem update checker."""
import json
import os
import pytest
import sys
import time
from unittest.mock import MagicMock, patch, call


@pytest.fixture(autouse=True)
def mock_openpilot(monkeypatch):
  """Mock openpilot imports."""
  for mod in ['openpilot', 'openpilot.common', 'openpilot.common.swaglog']:
    monkeypatch.setitem(sys.modules, mod, MagicMock())


@pytest.fixture
def uc_module():
  """Import update_checker with mocked deps."""
  import importlib
  # Ensure fresh import
  mod_name = 'overlays.selfdrive.plugins.update_checker'
  if mod_name in sys.modules:
    del sys.modules[mod_name]

  # Add overlays to path for direct import
  repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
  if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

  # Also make it importable as openpilot.selfdrive.plugins.update_checker
  spec = importlib.util.spec_from_file_location(
    'openpilot.selfdrive.plugins.update_checker',
    os.path.join(repo_root, 'overlays', 'selfdrive', 'plugins', 'update_checker.py'),
  )
  mod = importlib.util.module_from_spec(spec)
  sys.modules['openpilot.selfdrive.plugins.update_checker'] = mod
  spec.loader.exec_module(mod)
  return mod


# ============================================================
# _git_has_updates
# ============================================================

class TestGitHasUpdates:
  def test_no_git_dir(self, uc_module, tmp_path):
    assert uc_module._git_has_updates(str(tmp_path)) is False

  def test_detached_head_returns_false(self, uc_module, tmp_path):
    (tmp_path / '.git').mkdir()
    with patch.object(uc_module.subprocess, 'run') as mock_run:
      mock_run.return_value = MagicMock(stdout='HEAD\n')
      assert uc_module._git_has_updates(str(tmp_path)) is False

  def test_empty_branch_returns_false(self, uc_module, tmp_path):
    (tmp_path / '.git').mkdir()
    with patch.object(uc_module.subprocess, 'run') as mock_run:
      mock_run.return_value = MagicMock(stdout='\n')
      assert uc_module._git_has_updates(str(tmp_path)) is False

  def test_no_updates_returns_false(self, uc_module, tmp_path):
    (tmp_path / '.git').mkdir()
    repo = str(tmp_path)

    def side_effect(cmd, **kwargs):
      if 'rev-parse' in cmd:
        return MagicMock(stdout='dev\n')
      if 'fetch' in cmd:
        return MagicMock()
      if 'rev-list' in cmd:
        return MagicMock(stdout='0\n')
      return MagicMock()

    with patch.object(uc_module.subprocess, 'run', side_effect=side_effect):
      assert uc_module._git_has_updates(repo) is False

  def test_has_updates_returns_true(self, uc_module, tmp_path):
    (tmp_path / '.git').mkdir()
    repo = str(tmp_path)

    def side_effect(cmd, **kwargs):
      if 'rev-parse' in cmd:
        return MagicMock(stdout='dev\n')
      if 'fetch' in cmd:
        return MagicMock()
      if 'rev-list' in cmd:
        return MagicMock(stdout='3\n')
      return MagicMock()

    with patch.object(uc_module.subprocess, 'run', side_effect=side_effect):
      assert uc_module._git_has_updates(repo) is True

  def test_timeout_returns_false(self, uc_module, tmp_path):
    (tmp_path / '.git').mkdir()
    import subprocess as sp
    with patch.object(uc_module.subprocess, 'run', side_effect=sp.TimeoutExpired('git', 10)):
      assert uc_module._git_has_updates(str(tmp_path)) is False

  def test_nonexistent_path_returns_false(self, uc_module):
    assert uc_module._git_has_updates('/nonexistent/path') is False


# ============================================================
# UpdateChecker
# ============================================================

class TestUpdateChecker:
  def test_init_empty(self, uc_module, tmp_path):
    with patch.object(uc_module, 'UPDATES_FILE', str(tmp_path / 'updates.json')):
      checker = uc_module.UpdateChecker()
      assert checker.updates == {}
      assert checker.update_count == 0
      assert checker.has_updates is False

  def test_load_cached_state(self, uc_module, tmp_path):
    updates_file = tmp_path / 'updates.json'
    updates_file.write_text(json.dumps({
      'updates': {'plugins': True, 'cod': False},
      'last_check': time.time(),
    }))

    with patch.object(uc_module, 'UPDATES_FILE', str(updates_file)):
      checker = uc_module.UpdateChecker()
      assert checker.updates == {'plugins': True, 'cod': False}
      assert checker.update_count == 1
      assert checker.has_updates is True

  def test_load_corrupted_json(self, uc_module, tmp_path):
    updates_file = tmp_path / 'updates.json'
    updates_file.write_text('not json {{{')

    with patch.object(uc_module, 'UPDATES_FILE', str(updates_file)):
      checker = uc_module.UpdateChecker()
      assert checker.updates == {}

  def test_check_respects_interval(self, uc_module, tmp_path):
    updates_file = str(tmp_path / 'updates.json')
    with patch.object(uc_module, 'UPDATES_FILE', updates_file), \
         patch.object(uc_module, '_git_has_updates', return_value=False) as mock_git:
      checker = uc_module.UpdateChecker()

      # First check should run
      checker.check()
      assert mock_git.call_count >= 1

      first_count = mock_git.call_count

      # Immediate second check should be skipped
      checker.check()
      assert mock_git.call_count == first_count

  def test_check_runs_after_interval(self, uc_module, tmp_path):
    updates_file = str(tmp_path / 'updates.json')
    with patch.object(uc_module, 'UPDATES_FILE', updates_file), \
         patch.object(uc_module, 'CHECK_INTERVAL', 0), \
         patch.object(uc_module, '_git_has_updates', return_value=False) as mock_git:
      checker = uc_module.UpdateChecker()

      checker.check()
      first_count = mock_git.call_count

      # With interval=0, second check should also run
      checker.last_check = 0
      checker.check()
      assert mock_git.call_count > first_count

  def test_check_saves_results(self, uc_module, tmp_path):
    updates_file = tmp_path / 'updates.json'
    with patch.object(uc_module, 'UPDATES_FILE', str(updates_file)), \
         patch.object(uc_module, '_git_has_updates', return_value=True):
      checker = uc_module.UpdateChecker()
      checker.check()

    saved = json.loads(updates_file.read_text())
    assert saved['updates']['plugins'] is True
    assert saved['updates']['cod'] is True
    assert 'last_check' in saved

  def test_check_handles_git_exception(self, uc_module, tmp_path):
    updates_file = str(tmp_path / 'updates.json')
    with patch.object(uc_module, 'UPDATES_FILE', updates_file), \
         patch.object(uc_module, '_git_has_updates', side_effect=RuntimeError('boom')):
      checker = uc_module.UpdateChecker()
      # Should not raise
      checker.check()
      # updates may be empty or partial, but no crash
      assert isinstance(checker.updates, dict)

  def test_update_count_mixed(self, uc_module, tmp_path):
    with patch.object(uc_module, 'UPDATES_FILE', str(tmp_path / 'updates.json')):
      checker = uc_module.UpdateChecker()
      checker.updates = {'plugins': True, 'cod': False, 'mapd': True, 'models': False}
      assert checker.update_count == 2
      assert checker.has_updates is True

  def test_update_count_none(self, uc_module, tmp_path):
    with patch.object(uc_module, 'UPDATES_FILE', str(tmp_path / 'updates.json')):
      checker = uc_module.UpdateChecker()
      checker.updates = {'plugins': False, 'cod': False}
      assert checker.update_count == 0
      assert checker.has_updates is False


# ============================================================
# get_update_status
# ============================================================

class TestGetUpdateStatus:
  def test_reads_from_file(self, uc_module, tmp_path):
    updates_file = tmp_path / 'updates.json'
    updates_file.write_text(json.dumps({
      'updates': {'plugins': True, 'cod': False, 'mapd': True},
    }))
    with patch.object(uc_module, 'UPDATES_FILE', str(updates_file)):
      status = uc_module.get_update_status()
      assert status == {'plugins': True, 'cod': False, 'mapd': True}

  def test_missing_file_returns_empty(self, uc_module, tmp_path):
    with patch.object(uc_module, 'UPDATES_FILE', str(tmp_path / 'nonexistent.json')):
      assert uc_module.get_update_status() == {}

  def test_corrupted_file_returns_empty(self, uc_module, tmp_path):
    updates_file = tmp_path / 'updates.json'
    updates_file.write_text('garbage')
    with patch.object(uc_module, 'UPDATES_FILE', str(updates_file)):
      assert uc_module.get_update_status() == {}


# ============================================================
# Placeholder checks
# ============================================================

class TestPlaceholders:
  def test_mapd_always_false(self, uc_module):
    assert uc_module._check_mapd_update() is False

  def test_models_always_false(self, uc_module):
    assert uc_module._check_model_updates() is False
