"""Contracts the plugins rely on in upstream files catpilot patches."""


def test_gui_app_exposes_set_target_fps():
  # ui_mod switches the UI frame rate on/offroad through this method
  from openpilot.system.ui.lib.application import GuiApplication, gui_app
  assert callable(getattr(GuiApplication, 'set_target_fps', None))
  gui_app.set_target_fps(30)
  assert gui_app.target_fps == 30

