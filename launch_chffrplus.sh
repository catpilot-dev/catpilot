#!/usr/bin/env bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

source "$DIR/launch_env.sh"

function agnos_init {
  # TODO: move this to agnos
  sudo rm -f /data/etc/NetworkManager/system-connections/*.nmmeta
  rm -f /data/scons_cache/config.lock

  # set success flag for current boot slot
  sudo abctl --set_success

  # TODO: do this without udev in AGNOS
  # udev does this, but sometimes we startup faster
  sudo chgrp gpu /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0
  sudo chmod 660 /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0

  # Check if AGNOS update is required
  if [ "$(< /VERSION)" != "$AGNOS_VERSION" ]; then
    if grep -q "comma tici" /sys/firmware/devicetree/base/model 2>/dev/null; then
      # comma three is restricted to AGNOS 12.8 and this repo carries no
      # 12.8 images — never flash the manifest's AGNOS onto it.
      echo "comma three requires AGNOS $AGNOS_VERSION, found $(< /VERSION); not flashing"
    else
      AGNOS_PY="$DIR/openpilot/common/hardware/comma/agnos.py"
      MANIFEST="$DIR/openpilot/system/hardware/comma/agnos.json"
      if "$AGNOS_PY" --verify "$MANIFEST"; then
        sudo reboot
      fi
      while true; do
        "$DIR/openpilot/common/hardware/comma/updater" "$AGNOS_PY" "$MANIFEST"
      done
    fi
  fi
}

function launch {
  # Remove orphaned git lock if it exists on boot
  [ -f "$DIR/.git/index.lock" ] && rm -f "$DIR/.git/index.lock"

  # Check to see if there's a valid overlay-based update available. Conditions
  # are as follows:
  #
  # 1. The DIR init file has to exist, with a newer modtime than anything in
  #    the DIR Git repo. This checks for local development work or the user
  #    switching branches/forks, which should not be overwritten.
  # 2. The FINALIZED consistent file has to exist, indicating there's an update
  #    that completed successfully and synced to disk.

  if [ -f "${DIR}/.overlay_init" ]; then
    find "${DIR}/.git" -newer "${DIR}/.overlay_init" | grep -q '.' 2> /dev/null
    if [ $? -eq 0 ]; then
      echo "${DIR} has been modified, skipping overlay update installation"
    else
      if [ -f "${STAGING_ROOT}/finalized/.overlay_consistent" ]; then
        if [ ! -d /data/safe_staging/old_openpilot ]; then
          echo "Valid overlay update found, installing"
          LAUNCHER_LOCATION="${BASH_SOURCE[0]}"

          mv "$DIR" /data/safe_staging/old_openpilot
          mv "${STAGING_ROOT}/finalized" "$DIR"
          cd "$DIR"
          # releases before the openpilot/ move can't swap AGNOS on newer AGNOS, use ours
          [ ! -L system/hardware/tici/agnos.json ] && /data/safe_staging/old_openpilot/openpilot/common/hardware/comma/agnos.py --verify system/hardware/tici/agnos.json && sudo reboot

          echo "Restarting launch script ${LAUNCHER_LOCATION}"
          unset AGNOS_VERSION
          exec "${LAUNCHER_LOCATION}"
        else
          echo "openpilot backup found, not updating"
          # TODO: restore backup? This means the updater didn't start after swapping
        fi
      fi
    fi
  fi

  # Install plugins: refresh /data/plugins-runtime/ (including c3_compat's boot
  # patcher) from the /data/plugins checkout. Runs after the overlay swap and
  # before the boot patches below, so they run from the version just installed.
  PLUGIN_INSTALL=/data/plugins/install.sh
  if [ -f "$PLUGIN_INSTALL" ]; then
    bash "$PLUGIN_INSTALL" --target "$DIR" >> /tmp/plugin_install.log 2>&1 || true
  fi

  # First-boot auto-setup: clone plugins + connect on device (runs install.sh
  # itself after a fresh clone). Retried on each boot until it completes.
  if [ ! -f /data/.catpilot_setup_complete ]; then
    bash "$DIR/first_boot_setup.sh" >> /tmp/catpilot_first_boot.log 2>&1 || true
  fi

  # install.sh (run above, and by first_boot_setup.sh) leaves .needs_restart for
  # plugind, which then stops all plugin processes and kills the UI. That is
  # meant for installs done while the system runs; at boot nothing is running
  # yet and everything starts from the freshly installed files, so drop it.
  rm -f /data/plugins-runtime/.needs_restart

  # Apply c3_compat boot patches: after the overlay swap (so the code that runs is
  # what gets patched), after the plugin install (so the patcher is current, also
  # on the boot that first clones the plugins), and before build.py.
  BOOT_PATCH=/data/plugins-runtime/c3_compat/boot_patch.sh
  if [ -f "$BOOT_PATCH" ] && [ ! -f /data/plugins-runtime/c3_compat/.disabled ]; then
    source "$BOOT_PATCH" "$DIR"
  fi

  # Start COD server if installed
  if [ -f /data/connect-on-device/setup_service.sh ]; then
    bash /data/connect-on-device/setup_service.sh
  fi


  # handle pythonpath
  ln -sfn "$(pwd)" /data/pythonpath
  export PYTHONPATH="$PWD"

  # AGNOS 12.8 (comma three): cereal's SConscript runs a bare `capnpc`, which
  # must be the comma-deps-capnproto wheel's so the generated headers match the
  # libcapnp the build links. The system scons runs under /usr/bin/python3 and
  # needs the venv's site-packages (and opendbc_repo) for the build's imports.
  if [ "$AGNOS_MAJOR" -lt 16 ] 2>/dev/null; then
    export PATH="/usr/local/venv/lib/python3.12/site-packages/capnproto/install/bin:/usr/local/venv/bin:$PATH"
    export PYTHONPATH="$PYTHONPATH:$PWD/opendbc_repo:/usr/local/venv/lib/python3.12/site-packages"
  fi

  # submodule package symlinks for PYTHONPATH imports on device.
  # on PC these come from editable installs via pyproject.toml / uv.
  ln -sfn msgq_repo/msgq msgq
  ln -sfn opendbc_repo/opendbc opendbc
  ln -sfn rednose_repo/rednose rednose
  ln -sfn teleoprtc_repo/teleoprtc teleoprtc
  ln -sfn tinygrad_repo/tinygrad tinygrad

  # hardware specific init
  if [ -f /AGNOS ]; then
    agnos_init
  fi

  # write tmux scrollback to a file
  tmux capture-pane -pq -S-1000 > /tmp/launch_log

  # start manager
  cd openpilot/system/manager
  if [ ! -f "$DIR/prebuilt" ]; then
    ./build.py
  fi
  ./manager.py

  # if broken, keep on screen error
  while true; do sleep 1; done
}

launch
