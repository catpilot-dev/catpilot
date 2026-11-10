"""Ecosystem update checker — polls git remotes for available updates.

Checks each git checkout in REPOS (currently the plugins repo) for upstream
commits not yet pulled. Results written to /data/plugins-runtime/.updates.json
as {"updates": {name: bool}, "last_check": ...} for the UI.
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

    # Rebuilt from REPOS so keys no longer checked drop out of the cached file
    updates = {}
    for name, repo_path in REPOS.items():
      try:
        updates[name] = _git_has_updates(repo_path)
      except Exception:
        updates[name] = self.updates.get(name, False)
        cloudlog.exception(f"update_checker: error checking {name}")
    self.updates = updates

    self._save()
    available = [k for k, v in self.updates.items() if v]
    if available:
      cloudlog.info(f"update_checker: updates available: {available}")


def get_update_status() -> dict[str, bool]:
  """Read cached update status from disk (for UI consumption)."""
  try:
    with open(UPDATES_FILE) as f:
      data = json.load(f)
    return data.get('updates', {})
  except (FileNotFoundError, json.JSONDecodeError, OSError):
    return {}
