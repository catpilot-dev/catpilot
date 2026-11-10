"""Tests for plugind's PID-file boot guard, service readiness check and process sync."""
import os
from unittest.mock import MagicMock

import pytest

from openpilot.selfdrive.plugins import plugind
from openpilot.selfdrive.plugins.plugind import PluginProcessManager


@pytest.fixture
def dirs(tmp_path, monkeypatch):
  pid_dir = tmp_path / 'pids'
  pid_dir.mkdir()
  boot_id_path = tmp_path / 'boot_id'
  monkeypatch.setattr(plugind, 'PID_DIR', str(pid_dir))
  monkeypatch.setattr(plugind, 'LOG_DIR', str(tmp_path / 'logs'))
  monkeypatch.setattr(plugind, 'BOOT_ID_PATH', str(boot_id_path))
  return pid_dir, boot_id_path


class TestBootIdGuard:
  def test_different_boot_removes_stale_pid_files(self, dirs):
    pid_dir, boot_id_path = dirs
    boot_id_path.write_text('new-boot\n')
    (pid_dir / '.boot_id').write_text('old-boot')
    (pid_dir / 'mapd.pid').write_text('1234')
    (pid_dir / 'speedlimitd.pid').write_text('5678')

    PluginProcessManager()

    assert not (pid_dir / 'mapd.pid').exists()
    assert not (pid_dir / 'speedlimitd.pid').exists()
    assert (pid_dir / '.boot_id').read_text() == 'new-boot'

  def test_missing_boot_id_file_removes_pid_files(self, dirs):
    pid_dir, boot_id_path = dirs
    boot_id_path.write_text('new-boot\n')
    (pid_dir / 'mapd.pid').write_text('1234')

    PluginProcessManager()

    assert not (pid_dir / 'mapd.pid').exists()
    assert (pid_dir / '.boot_id').read_text() == 'new-boot'

  def test_missing_boot_id_file_keeps_live_plugin_process(self, dirs):
    # first start of the guard within a boot: a running plugin process must not be orphaned
    pid_dir, boot_id_path = dirs
    boot_id_path.write_text('new-boot\n')
    with open(f'/proc/{os.getpid()}/cmdline') as f:
      name = os.path.basename(f.read().split('\0')[0])  # this process's own argv[0]
    (pid_dir / f'{name}.pid').write_text(str(os.getpid()))
    (pid_dir / 'mapd.pid').write_text(str(os.getpid()))  # live PID, but not a process named mapd

    PluginProcessManager()

    assert (pid_dir / f'{name}.pid').exists()
    assert not (pid_dir / 'mapd.pid').exists()
    assert (pid_dir / '.boot_id').read_text() == 'new-boot'

  def test_other_boot_removes_even_a_matching_process(self, dirs):
    pid_dir, boot_id_path = dirs
    boot_id_path.write_text('new-boot\n')
    (pid_dir / '.boot_id').write_text('old-boot')
    with open(f'/proc/{os.getpid()}/cmdline') as f:
      name = os.path.basename(f.read().split('\0')[0])
    (pid_dir / f'{name}.pid').write_text(str(os.getpid()))

    PluginProcessManager()

    assert not (pid_dir / f'{name}.pid').exists()

  def test_same_boot_keeps_pid_files(self, dirs):
    pid_dir, boot_id_path = dirs
    boot_id_path.write_text('this-boot\n')
    (pid_dir / '.boot_id').write_text('this-boot')
    (pid_dir / 'mapd.pid').write_text('1234')

    PluginProcessManager()

    assert (pid_dir / 'mapd.pid').read_text() == '1234'

  def test_unreadable_boot_id_removes_nothing(self, dirs):
    pid_dir, _ = dirs  # boot_id_path never created
    (pid_dir / 'mapd.pid').write_text('1234')

    PluginProcessManager()

    assert (pid_dir / 'mapd.pid').read_text() == '1234'
    assert not os.path.exists(pid_dir / '.boot_id')


class TestServicesReady:
  def test_empty(self):
    assert PluginProcessManager._services_ready([])
    assert PluginProcessManager._services_ready({})

  def test_dict_and_list_shapes(self, monkeypatch):
    present = {'/dev/shm/msgq_carState'}
    monkeypatch.setattr(plugind.os.path, 'exists', lambda p: p in present)
    assert PluginProcessManager._services_ready({'services': ['carState']})
    assert PluginProcessManager._services_ready(['carState'])
    assert not PluginProcessManager._services_ready({'services': ['carState', 'gpsLocation']})
    assert not PluginProcessManager._services_ready(['gpsLocation'])


class FakeProcesses:
  """Stands in for _start/_stop/_is_running so sync() can be tested without spawning."""

  def __init__(self, mgr, running=()):
    self.mgr = mgr
    self.running = set(running)
    self.started = []
    self.stopped = []
    for name in self.running:
      mgr._pids[name] = 1000
    mgr._start = self._start
    mgr._stop = self._stop
    mgr._is_running = self._is_running

  def _is_running(self, name):
    if name in self.running:
      return self.mgr._pids.setdefault(name, 1000)
    self.mgr._pids.pop(name, None)
    return None

  def _start(self, proc_def):
    self.started.append(proc_def['name'])
    self.running.add(proc_def['name'])
    self.mgr._pids[proc_def['name']] = 1000

  def _stop(self, name):
    self.stopped.append(name)
    self.running.discard(name)
    self.mgr._pids.pop(name, None)


def _proc(name, condition=None):
  proc = {'name': name, 'module': name, 'plugin_id': name, 'plugin_dir': '/nonexistent', 'requires': []}
  if condition is not None:
    proc['condition'] = condition
  return proc


@pytest.fixture
def mgr(dirs, monkeypatch):
  monkeypatch.setattr(plugind, 'cloudlog', MagicMock())
  return PluginProcessManager()


class TestSyncConditions:
  @pytest.mark.parametrize('started', [True, False, None])
  @pytest.mark.parametrize('condition', [None, 'always_run', 'something_else'])
  def test_unconditional_always_started(self, mgr, condition, started):
    fake = FakeProcesses(mgr)
    mgr.sync([_proc('mapd', condition)], started)
    assert fake.started == ['mapd']

  @pytest.mark.parametrize('started', [True, False, None])
  def test_unconditional_running_is_left_alone(self, mgr, started):
    fake = FakeProcesses(mgr, running={'mapd'})
    mgr.sync([_proc('mapd', 'always_run')], started)
    assert fake.started == [] and fake.stopped == []

  def test_only_onroad_started_onroad(self, mgr):
    fake = FakeProcesses(mgr)
    mgr.sync([_proc('speedlimitd', 'only_onroad')], True)
    assert fake.started == ['speedlimitd']

  def test_only_onroad_not_started_offroad(self, mgr):
    fake = FakeProcesses(mgr)
    mgr.sync([_proc('speedlimitd', 'only_onroad')], False)
    assert fake.started == [] and fake.stopped == []

  def test_only_onroad_stopped_offroad(self, mgr):
    fake = FakeProcesses(mgr, running={'speedlimitd'})
    mgr.sync([_proc('speedlimitd', 'only_onroad')], False)
    assert fake.stopped == ['speedlimitd']
    assert 'speedlimitd' not in fake.running

  def test_only_onroad_already_stopped_is_not_stopped_again(self, mgr):
    fake = FakeProcesses(mgr, running={'speedlimitd'})
    for _ in range(3):
      mgr.sync([_proc('speedlimitd', 'only_onroad')], False)
    assert fake.stopped == ['speedlimitd']

  def test_unknown_state_leaves_running_only_onroad_alone(self, mgr):
    fake = FakeProcesses(mgr, running={'speedlimitd'})
    mgr.sync([_proc('speedlimitd', 'only_onroad')], None)
    assert fake.started == [] and fake.stopped == []
    assert 'speedlimitd' in fake.running

  def test_unknown_state_does_not_start_stopped_only_onroad(self, mgr):
    fake = FakeProcesses(mgr)
    mgr.sync([_proc('speedlimitd', 'only_onroad')])
    assert fake.started == [] and fake.stopped == []

  def test_only_offroad_mirrors_only_onroad(self, mgr):
    fake = FakeProcesses(mgr)
    procs = [_proc('offroad_job', 'only_offroad')]
    mgr.sync(procs, True)
    assert fake.started == []
    mgr.sync(procs, False)
    assert fake.started == ['offroad_job']
    mgr.sync(procs, None)
    assert fake.stopped == []
    mgr.sync(procs, True)
    assert fake.stopped == ['offroad_job']

  def test_drive_cycle(self, mgr):
    fake = FakeProcesses(mgr)
    procs = [_proc('mapd', 'always_run'), _proc('bus_logger', 'only_onroad')]
    mgr.sync(procs)  # plugind start, deviceState not received yet
    assert fake.running == {'mapd'}
    mgr.sync(procs, False)
    assert fake.running == {'mapd'}
    mgr.sync(procs, True)
    assert fake.running == {'mapd', 'bus_logger'}
    mgr.sync(procs, False)
    assert fake.running == {'mapd'}
    assert fake.stopped == ['bus_logger']


class TestSyncRestartWarning:
  def test_died_process_is_restarted_with_warning(self, mgr):
    fake = FakeProcesses(mgr, running={'mapd'})
    fake.running.discard('mapd')  # tracked in _pids, but the process is gone
    mgr.sync([_proc('mapd', 'always_run')], True)
    assert fake.started == ['mapd']
    plugind.cloudlog.warning.assert_called_once_with("plugin process 'mapd' died, restarting")

  def test_first_start_does_not_warn(self, mgr):
    fake = FakeProcesses(mgr)
    mgr.sync([_proc('mapd', 'always_run')], True)
    assert fake.started == ['mapd']
    plugind.cloudlog.warning.assert_not_called()

  def test_start_after_condition_stop_does_not_warn(self, mgr):
    FakeProcesses(mgr, running={'bus_logger'})
    procs = [_proc('bus_logger', 'only_onroad')]
    mgr.sync(procs, False)
    mgr.sync(procs, True)
    plugind.cloudlog.warning.assert_not_called()


class TestUpdateCheckLoop:
  def test_keeps_polling_after_an_error(self, monkeypatch):
    calls = []

    class Checker:
      def check(self):
        calls.append(1)
        raise RuntimeError("boom")

    def sleep(_):
      if len(calls) >= 2:
        raise KeyboardInterrupt  # ends the otherwise endless loop

    monkeypatch.setattr(plugind.time, 'sleep', sleep)
    with pytest.raises(KeyboardInterrupt):
      plugind._update_check_loop(Checker())
    assert len(calls) == 2
