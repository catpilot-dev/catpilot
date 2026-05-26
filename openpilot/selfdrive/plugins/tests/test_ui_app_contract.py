"""Contracts the plugins rely on in upstream files catpilot patches."""
import os

from openpilot.common.basedir import BASEDIR


def test_gui_app_exposes_set_target_fps():
  # ui_mod switches the UI frame rate on/offroad through this method
  from openpilot.system.ui.lib.application import GuiApplication, gui_app
  assert callable(getattr(GuiApplication, 'set_target_fps', None))
  gui_app.set_target_fps(30)
  assert gui_app.target_fps == 30


def test_builder_stock_cereal_files_exist():
  # restore_stock() checks these paths out of git; they must match the tree layout
  from openpilot.selfdrive.plugins import builder
  assert len(builder.STOCK_CEREAL_FILES) == 3
  for rel in builder.STOCK_CEREAL_FILES:
    assert os.path.isfile(os.path.join(BASEDIR, rel)), rel
