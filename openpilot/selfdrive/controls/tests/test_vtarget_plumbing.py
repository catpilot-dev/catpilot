"""vTarget (catpilot) must never ask for more than the plugin-adjusted cruise speed.

Since openpilot 0.11.2 the longitudinal MPC only follows leads (it invents a
fast lead when there is none) and cruise speed is a separate candidate, so the
MPC's own speed target is not bounded by v_cruise any more. BMW DCC steps its
setpoint toward vTarget, so vTarget has to stay capped at v_cruise."""
import numpy as np

from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.common.constants import CV
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.plugins.hooks import hooks


class FakeSM(dict):
  def __init__(self, services):
    super().__init__()
    for s in services:
      self[s] = getattr(messaging.new_message(s), s)


def make_sm(v_ego: float, v_cruise: float) -> FakeSM:
  sm = FakeSM(['carControl', 'carState', 'controlsState', 'selfdriveState', 'modelV2', 'radarState', 'vehicleParameters'])
  sm['carControl'].orientationNED = [0.0, 0.0, 0.0]
  sm['carState'].vEgo = v_ego
  sm['carState'].aEgo = 0.0
  sm['carState'].vCruise = v_cruise * CV.MS_TO_KPH
  sm['carState'].standstill = False
  sm['controlsState'].forceDecel = False
  sm['controlsState'].longControlState = LongCtrlState.pid
  sm['selfdriveState'].enabled = True
  sm['selfdriveState'].experimentalMode = False
  sm['modelV2'].meta.disengagePredictions.gasPressProbs = [0.0, 0.0]
  sm['radarState'].leadOne.present = False
  return sm


def test_vtarget_capped_at_cruise_speed_without_lead():
  hooks._loaded = True  # no plugin discovery in the unit test
  v_ego, v_cruise = 25.0, 20.0
  CP = car.CarParams.new_message(openpilotLongitudinalControl=True, steerRatio=15.0, wheelbase=2.7,
                                 longitudinalActuatorDelay=0.2)
  planner = LongitudinalPlanner(CP, init_v=v_ego, init_a=0.0)
  sm = make_sm(v_ego, v_cruise)
  for _ in range(40):
    planner.update(sm)
  assert np.isfinite(planner.output_v_target)
  assert planner.output_v_target <= v_cruise + 0.01, planner.output_v_target


def test_vtarget_never_implies_more_acceleration_than_atarget():
  """No lead, far below cruise: the MPC chases its fake lead at vEgo+10 and its speed at action_t runs
  ahead of the cruise candidate that actually wins aTarget. vTarget must encode aTarget's slope, i.e.
  (vTarget - v_now) must not exceed what integrating aTarget over action_t gives (get_accel_from_plan's
  own trapezoid, inverted)."""
  hooks._loaded = True
  v_ego, v_cruise = 20.0, 30.0
  action_t = 0.7 + 0.05  # BMW longitudinalActuatorDelay + DT_MDL
  CP = car.CarParams.new_message(openpilotLongitudinalControl=True, steerRatio=15.0, wheelbase=2.7,
                                 longitudinalActuatorDelay=0.7)
  planner = LongitudinalPlanner(CP, init_v=v_ego, init_a=0.0)
  sm = make_sm(v_ego, v_cruise)
  sm['modelV2'].meta.disengagePredictions.gasPressProbs = [0.0, 1.0]  # throttle allowed
  for _ in range(5):
    v_now = planner.v_desired_filter.x
    a_prev = planner.output_a_target
    planner.update(sm)
    v_consistent = v_now + (planner.output_a_target + a_prev) * action_t / 2.0
    assert planner.output_v_target <= v_consistent + 1e-6, (planner.output_v_target, v_consistent, planner.output_a_target)
