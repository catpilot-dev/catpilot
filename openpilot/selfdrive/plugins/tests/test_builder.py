"""Tests for the boot-time plugin builder's manifest handling."""
import json

from openpilot.selfdrive.plugins import builder


def test_malformed_manifest_is_skipped(tmp_path, monkeypatch):
  monkeypatch.setattr(builder, 'PLUGINS_DIR', str(tmp_path))

  good = tmp_path / 'good'
  good.mkdir()
  (good / 'plugin.json').write_text(json.dumps({'id': 'good', 'version': '1.0.0'}))

  broken = tmp_path / 'broken'
  broken.mkdir()
  (broken / 'plugin.json').write_text('{"id": "broken",')

  not_object = tmp_path / 'not_object'
  not_object.mkdir()
  (not_object / 'plugin.json').write_text('["valid json", "but not a manifest"]')

  assert builder._load_manifest(str(broken)) == {}
  assert builder._load_manifest(str(not_object)) == {}
  assert builder._get_enabled_plugins() == [str(good)]
