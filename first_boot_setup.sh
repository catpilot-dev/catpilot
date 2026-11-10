#!/bin/bash
# First-boot auto-setup for catpilot
# Clones plugins, downloads COD release, runs install.sh.
# The marker file is written only once both the plugins repo and COD are in
# place; until then the launcher runs this again on every boot. Network calls
# are time-bounded because the launcher waits for this script.

MARKER="/data/.catpilot_setup_complete"
[ -f "$MARKER" ] && exit 0

PLUGINS_REPO="https://github.com/catpilot-dev/plugins.git"
PLUGINS_DIR="/data/plugins"
CONNECT_DIR="/data/connect-on-device"
CONNECT_RELEASE_API="https://api.github.com/repos/catpilot-dev/connect-on-device/releases/latest"
OPENPILOT_DIR="/data/openpilot"

log() { echo "[catpilot-setup] $*"; }

# Which catpilot channel is installed? Release branches are named
# "release-vX.Y.Z" (the bare vX.Y.Z name belongs to the tag); CHANNEL is the
# version part, which is what COD's release tags are keyed on.
OP_BRANCH=$(git -C "$OPENPILOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)
CHANNEL="${OP_BRANCH#release-}"

# 1. Clone and install plugins (git-based), version-matched to the catpilot branch.
# ls-remote exits 2 when the remote has no such branch (clone the default branch
# instead); any other failure means the remote is unreachable, and cloning the
# default branch then would pin the device to the wrong plugins version.
NET_OK=1
if [ ! -d "$PLUGINS_DIR/.git" ]; then
  CLONE_BRANCH=()
  if [ -n "$OP_BRANCH" ]; then
    timeout 30 git ls-remote --exit-code --heads "$PLUGINS_REPO" "$OP_BRANCH" >/dev/null 2>&1
    case $? in
      0) CLONE_BRANCH=(-b "$OP_BRANCH") ;;
      2) ;;
      *) NET_OK=0 ;;
    esac
  fi
  if [ "$NET_OK" = "1" ]; then
    log "Cloning plugins (${CLONE_BRANCH[1]:-default branch})..."
    # a stalled transfer gives up after 30 s; a slow one may take the full limit
    timeout 900 git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 \
      clone --depth 1 "${CLONE_BRANCH[@]}" "$PLUGINS_REPO" "$PLUGINS_DIR"
  else
    log "Plugins repo unreachable, not cloning"
  fi
  # (a checkout that was already present has been installed by the launcher)
  if [ -f "$PLUGINS_DIR/install.sh" ]; then
    log "Installing plugins..."
    bash "$PLUGINS_DIR/install.sh" --target "$OPENPILOT_DIR" || true
  fi
fi

# 2. Download COD release (always a pre-built tarball; COD is never cloned).
# Channel-scoped tags: rolling "vX.Y.Z-YYYY.MM.DD" preferred over the
# bootstrap "vX.Y.Z", falling back to the latest release.
# API responses are small; the tarball download below gets a longer limit
API_CURL=(curl -sf --connect-timeout 10 --max-time 20)
COD_OK=0
if [ -f "$CONNECT_DIR/VERSION" ]; then
  COD_OK=1
elif [ "$NET_OK" != "1" ]; then
  log "No network, not downloading connect on device"
else
  log "Downloading connect on device..."
  RELEASE_JSON=""
  case "$CHANNEL" in
    v[0-9]*)
      # newest rolling release for this channel (list is newest-first)
      ROLLING_TAG=$("${API_CURL[@]}" "https://api.github.com/repos/catpilot-dev/connect-on-device/releases?per_page=100" | python3 -c "
import sys, json
chan = '$CHANNEL'
for r in json.load(sys.stdin):
    t = r.get('tag_name', '')
    if not r.get('draft') and not r.get('prerelease') and (t == chan or t.startswith(chan + '-')):
        print(t); break
" 2>/dev/null)
      if [ -n "$ROLLING_TAG" ]; then
        log "COD channel release: $ROLLING_TAG"
        RELEASE_JSON=$("${API_CURL[@]}" "https://api.github.com/repos/catpilot-dev/connect-on-device/releases/tags/$ROLLING_TAG")
      fi
      ;;
  esac
  [ -z "$RELEASE_JSON" ] && RELEASE_JSON=$("${API_CURL[@]}" "$CONNECT_RELEASE_API")
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
    # pipefail: a failed or truncated download must not count as success
    if (set -o pipefail; curl -sfL --connect-timeout 10 --max-time 600 --speed-limit 1000 --speed-time 30 "$TARBALL_URL" | tar xz -C "$STAGING"); then
      # GitHub tarballs extract to a subdirectory; find it
      SRC=$(find "$STAGING" -mindepth 1 -maxdepth 1 -type d | head -1)
      [ -z "$SRC" ] && SRC="$STAGING"
      if mkdir -p "$CONNECT_DIR" && cp -a "$SRC"/. "$CONNECT_DIR"/; then
        COD_OK=1
        log "COD installed ($(cat "$CONNECT_DIR/VERSION" 2>/dev/null || echo 'unknown'))"
      else
        log "COD copy into $CONNECT_DIR failed"
      fi
    else
      log "COD download failed"
    fi
    rm -rf "$STAGING"
  else
    log "Could not fetch COD release URL"
  fi
fi

# 3. Mark complete only when both parts are in place; otherwise retry next boot
MISSING=""
[ -f "$PLUGINS_DIR/install.sh" ] || MISSING="plugins repo"
[ "$COD_OK" = "1" ] || MISSING="${MISSING:+$MISSING and }connect on device"
if [ -n "$MISSING" ]; then
  log "First-boot setup incomplete: $MISSING missing; will retry on next boot."
  exit 1
fi

touch "$MARKER"
log "First-boot setup complete."
