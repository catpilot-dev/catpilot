#!/usr/bin/env python3
import os
import pyray as rl

from openpilot.system.hardware import TICI
from openpilot.common.realtime import config_realtime_process, set_core_affinity
from openpilot.system.ui.lib.application import gui_app


def main():
  cores = {5, }
  config_realtime_process(0, 51)

  # Open window early so the user sees a black screen instead of nothing
  # while heavy imports (plugins, cereal, layouts) load
  gui_app.init_window("UI")

  # Render a blank frame immediately to claim the display
  rl.begin_drawing()
  rl.clear_background(rl.Color(0, 0, 0, 255))
  rl.end_drawing()

  # Heavy imports — triggers plugin discovery, hook loading, cereal SubMaster
  from openpilot.selfdrive.ui.layouts.main import MainLayout
  from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
  from openpilot.selfdrive.ui.ui_state import ui_state

  if gui_app.big_ui():
    MainLayout()
  else:
    MiciMainLayout()

  for should_render in gui_app.render():
    ui_state.update()
    if should_render:
      # reaffine after power save offlines our core
      if TICI and os.sched_getaffinity(0) != cores:
        try:
          set_core_affinity(list(cores))
        except OSError:
          pass


if __name__ == "__main__":
  main()
