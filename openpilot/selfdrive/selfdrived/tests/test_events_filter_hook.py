"""The selfdrived.events_filter hook site must fire at the end of
SelfdriveD.update_events with the populated Events object, so car plugins can
implement engagement policies (e.g. BMW LKA two-stage disengagement)."""
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import MagicMock

from opendbc.car.structs import car
from openpilot.cereal import log, messaging
from openpilot.selfdrive.plugins.hooks import hooks
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD

EventName = log.OnroadEvent.EventName


class FakeSM(dict):
  def __init__(self, services):
    super().__init__()
    for s in services:
      self[s] = getattr(messaging.new_message(s), s)
    self['pandaStates'] = []
    self.frame = 1000
    self.recv_frame = defaultdict(int)
    self.updated = defaultdict(bool)
    self.valid = defaultdict(lambda: True)
    self.alive = defaultdict(lambda: True)
    self.freq_ok = defaultdict(lambda: True)

  def all_alive(self, service_list=None):
    return True

  def all_freq_ok(self, service_list=None):
    return True

  def all_checks(self, service_list=None):
    return True


def make_fake_selfdrived():
  s = SimpleNamespace()
  s.events = Events()
  s.sm = FakeSM(['controlsState', 'driverMonitoringState', 'carControl', 'deviceState',
                 'peripheralState', 'extrinsicsCalibration', 'driverAssistance', 'modelV2',
                 'managerState', 'radarState', 'deviceMotion', 'vehicleParameters', 'longitudinalPlan',
                 'lateralDelay', 'lateralTorqueParameters', 'lateralManeuverPlan'])
  s.sm['extrinsicsCalibration'].calStatus = log.ExtrinsicsCalibration.Status.calibrated
  s.CP = car.CarParams.new_message()
  s.CS_prev = car.CarState.new_message()
  s.car_events = MagicMock()
  s.car_events.update.return_value.to_msg.return_value = []
  s.params = MagicMock()
  s.pose_calibrator = MagicMock()
  s.rk = SimpleNamespace(lagging=False)
  s.startup_event = None
  s.initialized = True
  s.enabled = True
  s.disengage_on_accelerator = True
  s.is_ldw_enabled = False
  s.recalibrating_seen = False
  s.calibrated_pose = None
  s.excessive_actuation = False
  s.mismatch_counter = 0
  s.not_running_prev = None
  s.logged_comm_issue = None
  s.sensor_packets = []
  s.camera_packets = []
  s.gps_location_service = 'gpsLocation'
  s.distance_traveled = 0.0
  s.cruise_mismatch_counter = 0
  s.last_steering_pressed_frame = 0
  s.last_functional_fan_frame = 0
  s.personality = log.LongitudinalPersonality.standard
  # 0.11.2: big-model (chestnut) load state, DM lockout flags, actuation checker
  s.big_model_active = False
  s.big_model_failed = False
  s.big_model_loading = False
  s.big_model_ready_t = 0.0
  s.dm_lockout_set = False
  s.dm_uncertain_alerted = False
  s.excessive_actuation_check = MagicMock()
  s.excessive_actuation_check.update.return_value = False
  return s


class TestEventsFilterHook:
  def teardown_method(self):
    hooks.unregister('selfdrived.events_filter', 'test-lka')

  def test_hook_sees_populated_events_and_can_filter(self):
    seen = {}

    def probe(default, events, CS, CS_prev, enabled):
      seen['names'] = list(events.events)
      seen['enabled'] = enabled
      events.events[:] = [e for e in events.events if e != EventName.pedalPressed]
      return default

    hooks.register('selfdrived.events_filter', 'test-lka', probe)

    s = make_fake_selfdrived()
    CS = car.CarState.new_message(canValid=True, brakePressed=True)
    SelfdriveD.update_events(s, CS)

    # hook ran after event generation: it saw the brake-press disengage event
    assert EventName.pedalPressed in seen['names']
    assert seen['enabled'] is True
    # and its filtering is reflected in the final event set
    assert EventName.pedalPressed not in s.events.events

  def test_events_unchanged_without_hook(self):
    s = make_fake_selfdrived()
    CS = car.CarState.new_message(canValid=True, brakePressed=True)
    SelfdriveD.update_events(s, CS)
    assert EventName.pedalPressed in s.events.events
