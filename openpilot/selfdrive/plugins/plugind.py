#!/usr/bin/env python3
"""Plugin manager daemon — discovers, loads, and monitors plugins.

Runs as an always_run process. Responsibilities:
  1. Scan /data/plugins-runtime/ for installed plugins
  2. Validate compatibility (min/max openpilot version)
  3. Load enabled plugins, register hooks + processes
  4. Spawn and monitor standalone plugin processes, honouring each one's
     condition (always_run, only_onroad, only_offroad) against deviceState.started
  5. Serve REST API on localhost for COD
  6. Poll for enable/disable changes

The main loop ticks at 1 Hz. Every POLL_INTERVAL seconds it does a full pass
(reload enabled plugins, sync processes, health hooks, restart marker); on any
other tick where the onroad state changed it only syncs processes, so onroad
processes start within about a second of a drive starting. The update check
runs on its own thread: its git fetch can block for tens of seconds.

Plugin processes use subprocess.Popen with os.setpgrp (like DaemonProcess
for athena) so they survive plugind restarts and are isolated from
signal propagation. PIDs are tracked on disk for reconnection.
"""
import os
import signal
import subprocess
import sys
import threading
import time

import openpilot.cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.plugins.hooks import hooks
from openpilot.selfdrive.plugins.plugin_bus import PluginPub
from openpilot.selfdrive.plugins.registry import PluginRegistry
from openpilot.selfdrive.plugins.api import set_registry, start_api_server
from openpilot.selfdrive.plugins.update_checker import UpdateChecker

POLL_INTERVAL = 5.0  # seconds between full passes (config changes, process sync, health)
PID_DIR = '/data/plugins-runtime/.pids'
LOG_DIR = '/tmp/plugin_logs'
RESTART_MARKER = '/data/plugins-runtime/.needs_restart'
BOOT_ID_PATH = '/proc/sys/kernel/random/boot_id'


class PluginProcessManager:
  """Spawns and monitors standalone plugin processes.

  Uses subprocess.Popen with preexec_fn=os.setpgrp (own process group)
  so plugin processes are isolated from signals sent to plugind's group
  and survive plugind restarts. PID files enable reconnection.
  """

  def __init__(self):
    self._pids: dict[str, int] = {}
    os.makedirs(PID_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    self._clear_stale_pid_files()

  @staticmethod
  def _clear_stale_pid_files():
    """Drop PID files written during a previous boot.

    PID_DIR is on persistent storage, so after a reboot its PIDs may belong to
    unrelated processes. Same-boot files are kept: plugin processes survive a
    plugind restart and are reconnected through them.
    """
    try:
      with open(BOOT_ID_PATH) as f:
        boot_id = f.read().strip()
    except OSError:
      return
    if not boot_id:
      return

    boot_id_file = os.path.join(PID_DIR, '.boot_id')
    other_boot = False
    try:
      with open(boot_id_file) as f:
        if f.read().strip() == boot_id:
          return
      other_boot = True
    except OSError:
      pass

    for f in os.listdir(PID_DIR):
      if not f.endswith('.pid'):
        continue
      path = os.path.join(PID_DIR, f)
      # No marker yet (first start of this code): the files may still be from this
      # boot, so keep one whose PID is a live process named after the plugin.
      if not other_boot and PluginProcessManager._pid_file_names_process(path, f[:-4]):
        continue
      try:
        os.unlink(path)
      except OSError:
        pass
    try:
      with open(boot_id_file, 'w') as f:
        f.write(boot_id)
    except OSError:
      cloudlog.exception("plugind: failed to write boot id")

  @staticmethod
  def _pid_file_names_process(pid_file: str, name: str) -> bool:
    """Is the PID in pid_file a live process whose cmdline names the plugin?"""
    try:
      with open(pid_file) as f:
        pid = int(f.read().strip())
      with open(f'/proc/{pid}/cmdline') as f:
        cmdline = f.read()
    except (OSError, ValueError):
      return False
    return f'plugin_{name}' in cmdline or name in cmdline

  def _pid_file(self, name: str) -> str:
    return os.path.join(PID_DIR, f'{name}.pid')

  def _log_file(self, name: str) -> str:
    return os.path.join(LOG_DIR, f'plugin_{name}.log')

  def _is_running(self, name: str) -> int | None:
    """Check if a plugin process is still alive. Returns PID or None.

    Validates via cmdline containing plugin_{name} OR the plugin module name,
    to handle processes that execv into a native binary (e.g. mapd_runner
    exec's into /data/media/0/osm/mapd). Falls back to checking if the PID
    was started after plugind (guards against stale PIDs from prior boot).
    """
    pid = self._pids.get(name)
    if pid is None:
      try:
        pid = int(open(self._pid_file(name)).read().strip())
      except (FileNotFoundError, ValueError):
        return None

    try:
      os.kill(pid, 0)
      # Check for zombie — os.kill(pid, 0) succeeds for zombies
      try:
        with open(f'/proc/{pid}/status') as f:
          for line in f:
            if line.startswith('State:'):
              if 'Z' in line:
                # Reap the zombie
                try:
                  os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                  pass
                raise OSError("zombie process")
              break
      except FileNotFoundError:
        raise OSError("process gone")
      # Verify this PID belongs to us — check cmdline or start time
      with open(f'/proc/{pid}/cmdline') as f:
        cmdline = f.read()
      if f'plugin_{name}' in cmdline or name in cmdline:
        self._pids[name] = pid
        return pid
      # Process may have execv'd — check it started after plugind
      pid_start = os.stat(f'/proc/{pid}').st_mtime
      plugind_start = os.stat(f'/proc/{os.getpid()}').st_mtime
      if pid_start >= plugind_start:
        self._pids[name] = pid
        return pid
    except (OSError, FileNotFoundError):
      pass

    self._pids.pop(name, None)
    return None

  @staticmethod
  def _services_ready(requires) -> bool:
    """Check if required cereal services are available via shared memory.

    Args:
      requires: dict with "services" list, a list of service names, or empty
    """
    if not requires:
      return True
    services = requires.get('services', []) if isinstance(requires, dict) else requires
    return all(os.path.exists(f'/dev/shm/msgq_{s}') for s in services)

  @staticmethod
  def _wanted(condition: str, started: bool | None) -> bool | None:
    """Should a process with this condition run? None: onroad state unknown, leave it as it is."""
    if condition == 'only_onroad':
      return started
    if condition == 'only_offroad':
      return None if started is None else not started
    return True

  def sync(self, desired: list[dict], started: bool | None = None):
    """Start/stop processes to match desired state.

    started is deviceState.started, or None before deviceState is received. While
    it is None, only_onroad/only_offroad processes are neither started nor stopped:
    plugind may have restarted mid-drive and must not kill a running onroad process.
    """
    desired_names = {p['name'] for p in desired}

    # Stop processes no longer needed
    for name in list(self._pids):
      if name not in desired_names:
        self._stop(name)
    # Also stop orphaned PID files
    if os.path.isdir(PID_DIR):
      for f in os.listdir(PID_DIR):
        if f.endswith('.pid'):
          name = f[:-4]
          if name not in desired_names:
            self._stop(name)

    # Start wanted processes that aren't running (respecting required services),
    # stop running ones their condition excludes
    for proc_def in desired:
      name = proc_def['name']
      wanted = self._wanted(proc_def.get('condition', 'always_run'), started)
      if wanted is None:
        continue

      was_tracked = name in self._pids  # _is_running() forgets a process that is gone
      running = self._is_running(name) is not None
      if not wanted:
        if running:
          self._stop(name)
        continue
      if running:
        continue

      # Probe required cereal services before spawning
      requires = proc_def.get('requires', [])
      if not self._services_ready(requires):
        continue

      if was_tracked:
        cloudlog.warning(f"plugin process '{name}' died, restarting")
      self._start(proc_def)

  def _start(self, proc_def: dict):
    name = proc_def['name']
    plugin_dir = proc_def['plugin_dir']
    module = proc_def['module']
    module_file = os.path.join(plugin_dir, *module.split('.')) + '.py'

    # Inherit parent sys.path so child can import cereal, openpilot, etc.
    # Filter out all plugin-runtime paths to avoid duplicate entries that
    # would let bare imports create second module instances.
    plugins_root = os.path.dirname(plugin_dir)
    parent_paths = [p for p in sys.path if p and p != plugin_dir and not p.startswith(plugins_root + '/')]
    launcher_code = (
      f"import sys, os; "
      f"sys.path[:0] = {[plugin_dir, plugins_root] + parent_paths!r}; "
      f"os.chdir({plugin_dir!r}); "
      f"os.environ['PWD'] = {plugin_dir!r}; "
      f"from setproctitle import setproctitle; "
      f"setproctitle('plugin_{name}'); "
      f"import importlib.util; "
      f"spec = importlib.util.spec_from_file_location('plugin_{name}', {module_file!r}); "
      f"mod = importlib.util.module_from_spec(spec); "
      f"spec.loader.exec_module(mod); "
      f"mod.main()"
    )

    log_path = self._log_file(name)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log_fd = open(log_path, 'w')
    proc = subprocess.Popen(
      [sys.executable, '-c', launcher_code],
      stdin=subprocess.DEVNULL,
      stdout=log_fd,
      stderr=log_fd,
      preexec_fn=os.setpgrp,
    )
    log_fd.close()

    self._pids[name] = proc.pid
    with open(self._pid_file(name), 'w') as f:
      f.write(str(proc.pid))

    cloudlog.info(f"plugin process '{name}' spawned (pid={proc.pid})")

  def _stop(self, name: str):
    pid = self._is_running(name)
    if pid is not None:
      try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
          time.sleep(0.1)
          try:
            os.kill(pid, 0)
          except OSError:
            break
        else:
          os.kill(pid, signal.SIGKILL)
      except OSError:
        pass
      cloudlog.info(f"plugin process '{name}' stopped")

    self._pids.pop(name, None)
    try:
      os.unlink(self._pid_file(name))
    except FileNotFoundError:
      pass

  def stop_all(self):
    """Stop all managed plugin processes."""
    for name in list(self._pids):
      self._stop(name)
    # Also stop any orphaned PID files
    if os.path.isdir(PID_DIR):
      for f in os.listdir(PID_DIR):
        if f.endswith('.pid'):
          self._stop(f[:-4])


def _kill_ui():
  """Kill the UI process so manager restarts it with fresh code."""
  try:
    result = subprocess.run(['pgrep', '-f', 'selfdrive.ui.ui'], capture_output=True, text=True)
    for pid_str in result.stdout.strip().split('\n'):
      if pid_str:
        os.kill(int(pid_str), signal.SIGTERM)
        cloudlog.info(f"plugind: killed UI process (pid={pid_str}) for restart")
  except Exception:
    cloudlog.exception("plugind: failed to kill UI process")


def _update_check_loop(checker: UpdateChecker):
  """Poll for ecosystem updates; check() rate-limits itself to its own interval.

  Runs on its own thread: git fetch can block for tens of seconds on a bad link,
  which must not hold up the main loop's process sync at an onroad transition.
  """
  while True:
    try:
      checker.check()
    except Exception:
      cloudlog.exception("plugind update check error")
    time.sleep(POLL_INTERVAL)


def main():
  cloudlog.info("plugind starting")

  registry = PluginRegistry()
  proc_mgr = PluginProcessManager()

  # Initial discovery
  discovered = registry.discover()
  cloudlog.info(f"plugind discovered {len(discovered)} plugins: {discovered}")

  # Load enabled plugins
  registry.load_enabled()

  # Spawn standalone plugin processes. The onroad state is not known yet, so only
  # unconditional ones start here; the loop handles the rest once deviceState arrives.
  standalone = registry.get_standalone_processes()
  if standalone:
    cloudlog.info(f"plugind spawning {len(standalone)} standalone processes: "
                  f"{[p['name'] for p in standalone]}")
    proc_mgr.sync(standalone)

  # Start REST API for COD
  set_registry(registry)
  _api_server = start_api_server()  # keep a reference; the server runs on its own thread

  # Ecosystem update checker (polls git remotes every 5 min)
  threading.Thread(target=_update_check_loop, args=(UpdateChecker(),), daemon=True).start()

  # Subscribe to deviceState for onroad/offroad detection
  sm = messaging.SubMaster(['deviceState'])

  # Plugin bus publisher for aggregated health check results
  health_pub = PluginPub("plugin_health")

  # Main loop at 1 Hz: a full pass every POLL_INTERVAL seconds, and a process sync
  # alone on any other tick where the onroad state changed.
  # Note: no finally/stop_all — plugin processes are independent (own process
  # group) and should survive plugind restarts. They die on reboot naturally.
  rk = Ratekeeper(1.0, print_delay_threshold=None)
  last_full_pass = 0.0
  prev_started = None
  while True:
    try:
      sm.update(0)
      # None until deviceState is received, so offroad is never assumed
      started = sm['deviceState'].started if sm.seen['deviceState'] else None

      if time.monotonic() - last_full_pass >= POLL_INTERVAL:
        last_full_pass = time.monotonic()

        # Re-check enabled state (user may toggle via COD)
        registry.load_enabled()

        # Sync standalone processes (restart crashed ones, stop disabled ones,
        # apply onroad/offroad conditions)
        proc_mgr.sync(registry.get_standalone_processes(), started)

        # Call plugin health checks and publish aggregated result to plugin_bus
        # bus_logger captures plugin_health topic into rlogs automatically
        health = hooks.run('device.health_check', {}, sm=sm)
        if health:
          health_pub.send(health)

        # Handle restart marker (written by install.sh after plugin updates)
        # Only restart when offroad (known, not assumed) for safety
        if os.path.exists(RESTART_MARKER) and started is False:
          cloudlog.warning("plugind: restart marker detected, restarting plugin processes and UI")
          os.unlink(RESTART_MARKER)
          proc_mgr.stop_all()
          # sync() will respawn with fresh code on the next full pass
          _kill_ui()

      elif started != prev_started:
        # Onroad/offroad transition: apply process conditions without waiting for the full pass
        proc_mgr.sync(registry.get_standalone_processes(), started)

      prev_started = started
    except Exception:
      cloudlog.exception("plugind poll error")

    rk.keep_time()


if __name__ == "__main__":
  main()
