import time

from opendbc.car.structs import car
from openpilot.common.params import Params
from openpilot.system.manager.process import NativeProcess, ensure_running


def _wait_dead(p, timeout=5.0):
  t = time.monotonic()
  while time.monotonic() - t < timeout and (p.proc is None or p.proc.exitcode is None):
    time.sleep(0.01)
  return p.proc is not None and p.proc.exitcode is not None


def test_restart_if_crash_restarts_a_dead_process():
  """catpilot: plugind kills the UI after a plugin install and relies on the manager
  bringing it back (upstream dropped restart_if_crash in 0.11.2)."""
  p = NativeProcess("crashy", "", ["/bin/true"], lambda started, params, CP: True, restart_if_crash=True)
  try:
    params, CP = Params(), car.CarParams.new_message()
    ensure_running([p], False, params=params, CP=CP)
    first = p.proc
    assert _wait_dead(p), "test process should exit on its own"
    ensure_running([p], False, params=params, CP=CP)
    assert p.proc is not None and p.proc is not first, "dead process flagged restart_if_crash must be restarted"
  finally:
    p.stop(block=True)


def test_without_flag_a_dead_process_stays_dead():
  p = NativeProcess("oneshot", "", ["/bin/true"], lambda started, params, CP: True)
  try:
    params, CP = Params(), car.CarParams.new_message()
    ensure_running([p], False, params=params, CP=CP)
    first = p.proc
    assert _wait_dead(p)
    ensure_running([p], False, params=params, CP=CP)
    assert p.proc is first
  finally:
    p.stop(block=True)
