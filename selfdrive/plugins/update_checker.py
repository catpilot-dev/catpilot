"""Ecosystem update checker — polls git remotes for available updates.

Checks plugins repo, COD, models, and mapd for newer versions.
Results written to /data/plugins-runtime/.updates.json for the UI.
Runs inside plugind at a low frequency (every 5 minutes).
"""
import json
import os
import subprocess
import time

from openpilot.common.swaglog import cloudlog

UPDATES_FILE = '/data/plugins-runtime/.updates.json'
CHECK_INTERVAL = 300  # 5 minutes

# Repos to check for updates
REPOS = {
  'plugins': '/data/plugins',
  'cod': '/data/connect-on-device',
}


def _git_has_updates(repo_path: str) -> bool:
  """Check if a git repo has upstream commits not yet pulled."""
  if not os.path.isdir(os.path.join(repo_path, '.git')):
    return False

  try:
    # Get current branch
    branch = subprocess.run(
      ['git', '-C', repo_path, 'rev-parse', '--abbrev-ref', 'HEAD'],
      capture_output=True, text=True, timeout=10,
    ).stdout.strip()

    if not branch or branch == 'HEAD':
      return False

    # Fetch from remote (quiet, no tags)
    subprocess.run(
      ['git', '-C', repo_path, 'fetch', 'origin', branch, '--quiet'],
      capture_output=True, timeout=30,
      env={**os.environ, 'GIT_SSL_NO_VERIFY': '1'},
    )

    # Count commits behind
    result = subprocess.run(
      ['git', '-C', repo_path, 'rev-list', '--count', f'HEAD..origin/{branch}'],
      capture_output=True, text=True, timeout=10,
    )
    count = int(result.stdout.strip())
    return count > 0

  except (subprocess.TimeoutExpired, ValueError, OSError):
    return False


def _check_mapd_update() -> bool:
  """Check if mapd binary has an update available.

  Placeholder — mapd versioning TBD.
  """
  return False


def _check_model_updates() -> bool:
  """Check if driving models have updates available.

  Placeholder — model versioning TBD.
  """
  return False


class UpdateChecker:
  def __init__(self):
    self.last_check: float = 0
    self.updates: dict[str, bool] = {}
    # Load cached state if available
    self._load()

  def _load(self):
    try:
      with open(UPDATES_FILE) as f:
        data = json.load(f)
      self.updates = data.get('updates', {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
      self.updates = {}

  def _save(self):
    data = {
      'updates': self.updates,
      'last_check': time.time(),
    }
    try:
      with open(UPDATES_FILE, 'w') as f:
        json.dump(data, f)
    except OSError:
      pass

  def check(self):
    """Run update checks if enough time has elapsed."""
    now = time.monotonic()
    if now - self.last_check < CHECK_INTERVAL:
      return
    self.last_check = now

    cloudlog.info("update_checker: checking for updates")

    for name, repo_path in REPOS.items():
      try:
        self.updates[name] = _git_has_updates(repo_path)
      except Exception:
        cloudlog.exception(f"update_checker: error checking {name}")

    try:
      self.updates['mapd'] = _check_mapd_update()
    except Exception:
      pass

    try:
      self.updates['models'] = _check_model_updates()
    except Exception:
      pass

    self._save()
    available = [k for k, v in self.updates.items() if v]
    if available:
      cloudlog.info(f"update_checker: updates available: {available}")

  @property
  def update_count(self) -> int:
    return sum(1 for v in self.updates.values() if v)

  @property
  def has_updates(self) -> bool:
    return self.update_count > 0


def get_update_status() -> dict[str, bool]:
  """Read cached update status from disk (for UI consumption)."""
  try:
    with open(UPDATES_FILE) as f:
      data = json.load(f)
    return data.get('updates', {})
  except (FileNotFoundError, json.JSONDecodeError, OSError):
    return {}
