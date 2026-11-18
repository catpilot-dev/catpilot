"""planner.e2e_accel: a plugin may adjust the e2e acceleration candidate (experimental mode only)."""
import math

import pytest

from opendbc.car.structs import car
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanSource
from openpilot.selfdrive.controls.tests.test_vtarget_plumbing import make_sm
from openpilot.selfdrive.plugins.hooks import hooks

HOOK = 'planner.e2e_accel'
A_E2E = 0.3
V_EGO, V_CRUISE = 20.0, 30.0


@pytest.fixture(autouse=True)
def clean_hooks():
  saved = (dict(hooks._hooks), hooks._loaded)
  hooks._hooks.pop(HOOK, None)
  hooks._loaded = True  # no plugin discovery in the unit test
  yield
  hooks._hooks.clear()
  hooks._hooks.update(saved[0])
  hooks._loaded = saved[1]


def run_planner(experimental=True):
  CP = car.CarParams.new_message(openpilotLongitudinalControl=True, steerRatio=15.0, wheelbase=2.7,
                                 longitudinalActuatorDelay=0.2)
  planner = LongitudinalPlanner(CP, init_v=V_EGO, init_a=0.0)
  sm = make_sm(V_EGO, V_CRUISE)
  sm['selfdriveState'].experimentalMode = experimental
  sm['modelV2'].action.desiredAcceleration = A_E2E
  planner.update(sm)
  return planner


def register(fn):
  hooks.register(HOOK, 'test-plugin', fn)


def test_no_plugin_passthrough():
  p = run_planner()
  assert math.isfinite(p.output_a_target)


def test_hook_value_becomes_e2e_candidate():
  base = run_planner().output_a_target
  seen = []

  def fn(a_e2e, v_ego, v_cruise, a_cruise, sm):
    seen.append((a_e2e, v_ego, v_cruise))
    return a_e2e - 1.0
  register(fn)
  p = run_planner()
  assert seen
  assert seen[0][1] == pytest.approx(V_EGO, abs=1e-3)
  assert p.output_a_target <= seen[0][0] - 1.0 + 1e-6
  assert p.output_a_target < base
  assert p.mpc.source == LongitudinalPlanSource.e2e


def test_hook_not_called_in_chill():
  calls = []
  register(lambda *a: calls.append(a) or a[0] - 1.0)
  run_planner(experimental=False)
  assert calls == []


def test_hook_error_passthrough():
  base = run_planner().output_a_target

  def boom(*a):
    raise RuntimeError('plugin bug')
  register(boom)
  assert run_planner().output_a_target == base


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf'), None, 'x'])
def test_hook_nonfinite_passthrough(bad):
  base = run_planner().output_a_target
  register(lambda *a: bad)
  assert run_planner().output_a_target == base
