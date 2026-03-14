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

# 1. Clone and install plugins (git-based)
if [ ! -d "$PLUGINS_DIR/.git" ]; then
  log "Cloning plugins..."
  git clone --depth 1 "$PLUGINS_REPO" "$PLUGINS_DIR" || true
fi
if [ -f "$PLUGINS_DIR/install.sh" ]; then
  log "Installing plugins..."
  bash "$PLUGINS_DIR/install.sh" --target "$OPENPILOT_DIR" || true
fi

# 2. Download COD latest release (tarball-based, no git)
if [ ! -f "$CONNECT_DIR/VERSION" ]; then
  log "Downloading connect on device..."
  TARBALL_URL=$(curl -sf "$CONNECT_RELEASE_API" | python3 -c "
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
