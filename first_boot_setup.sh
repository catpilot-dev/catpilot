#!/bin/bash
# First-boot auto-setup for catpilot
# Clones plugins, downloads COD release, runs install.sh.
# Runs once — skipped on subsequent boots via marker file.

MARKER="/data/.catpilot_setup_complete"
[ -f "$MARKER" ] && exit 0

PLUGINS_REPO="https://github.com/catpilot-dev/plugins.git"
PLUGINS_DIR="/data/plugins"
CONNECT_DIR="/data/connect-on-device"
CONNECT_RELEASE_API="https://api.github.com/repos/catpilot-dev/connect-on-device/releases/latest"
OPENPILOT_DIR="/data/openpilot"

log() { echo "[catpilot-setup] $*"; }

# Which catpilot channel is installed? (dev, main, or a vX.Y.Z release branch)
OP_BRANCH=$(git -C "$OPENPILOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)

# 1. Clone and install plugins (git-based), version-matched to the catpilot branch
FRESH_CLONE=0
if [ ! -d "$PLUGINS_DIR/.git" ]; then
  if [ -n "$OP_BRANCH" ] && git ls-remote --exit-code --heads "$PLUGINS_REPO" "$OP_BRANCH" >/dev/null 2>&1; then
    log "Cloning plugins ($OP_BRANCH)..."
    git clone --depth 1 -b "$OP_BRANCH" "$PLUGINS_REPO" "$PLUGINS_DIR" && FRESH_CLONE=1
  else
    log "Cloning plugins (default branch)..."
    git clone --depth 1 "$PLUGINS_REPO" "$PLUGINS_DIR" && FRESH_CLONE=1
  fi
fi
if [ -f "$PLUGINS_DIR/install.sh" ]; then
  log "Installing plugins..."
  bash "$PLUGINS_DIR/install.sh" --target "$OPENPILOT_DIR" || true
fi

# 2. Download COD release (always a pre-built tarball; COD is never cloned).
# Channel-scoped tags: rolling "vX.Y.Z-YYYY.MM.DD" preferred over the
# bootstrap "vX.Y.Z", falling back to the latest release.
if [ ! -f "$CONNECT_DIR/VERSION" ]; then
  log "Downloading connect on device..."
  RELEASE_JSON=""
  case "$OP_BRANCH" in
    v[0-9]*)
      # newest rolling release for this channel (list is newest-first)
      ROLLING_TAG=$(curl -sf "https://api.github.com/repos/catpilot-dev/connect-on-device/releases?per_page=100" | python3 -c "
import sys, json
chan = '$OP_BRANCH'
for r in json.load(sys.stdin):
    t = r.get('tag_name', '')
    if not r.get('draft') and not r.get('prerelease') and (t == chan or t.startswith(chan + '-')):
        print(t); break
" 2>/dev/null)
      if [ -n "$ROLLING_TAG" ]; then
        log "COD channel release: $ROLLING_TAG"
        RELEASE_JSON=$(curl -sf "https://api.github.com/repos/catpilot-dev/connect-on-device/releases/tags/$ROLLING_TAG")
      fi
      ;;
  esac
  [ -z "$RELEASE_JSON" ] && RELEASE_JSON=$(curl -sf "$CONNECT_RELEASE_API")
  TARBALL_URL=$(printf '%s' "$RELEASE_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
# Prefer cod-*.tar.gz asset, fall back to source tarball
for a in data.get('assets', []):
    if a['name'].startswith('cod-') and a['name'].endswith('.tar.gz'):
        print(a['browser_download_url']); sys.exit()
print(data.get('tarball_url', ''))
" 2>/dev/null)
  if [ -n "$TARBALL_URL" ]; then
    STAGING=$(mktemp -d)
    if curl -sfL "$TARBALL_URL" | tar xz -C "$STAGING"; then
      # GitHub tarballs extract to a subdirectory; find it
      SRC=$(find "$STAGING" -mindepth 1 -maxdepth 1 -type d | head -1)
      [ -z "$SRC" ] && SRC="$STAGING"
      mkdir -p "$CONNECT_DIR"
      cp -a "$SRC"/. "$CONNECT_DIR"/
      log "COD installed ($(cat "$CONNECT_DIR/VERSION" 2>/dev/null || echo 'unknown'))"
    else
      log "COD download failed"
    fi
    rm -rf "$STAGING"
  else
    log "Could not fetch COD release URL"
  fi
fi

# 3. Mark complete
touch "$MARKER"
log "First-boot setup complete."

# 4. If this fresh install brought boot patches (c3_compat on the comma three),
# they were not active for the current launch — the launch script's boot_patch
# check ran before this script cloned the plugins. Reboot once so they apply.
# The marker above makes this a one-shot: this script never runs again.
if [ "$FRESH_CLONE" = "1" ] && [ -f /data/plugins-runtime/c3_compat/boot_patch.sh ] && [ ! -f /data/plugins-runtime/c3_compat/.disabled ]; then
  log "Boot patches installed on first boot — rebooting once to apply."
  sudo reboot
fi
