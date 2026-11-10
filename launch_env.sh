#!/usr/bin/env bash

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

# models get lower priority than ui
# - ui is ~5ms
# - modeld is 20ms
# - DM is 10ms
# in order to run ui at 60fps (16.67ms), we need to allow
# it to preempt the model workloads. we have enough
# headroom for this until ui is moved to the CPU.
export QCOM_PRIORITY=12

if [ -z "$AGNOS_VERSION" ]; then
  # comma three is restricted to AGNOS 12.8 — c3_compat bridges newer
  # openpilot releases onto it; the OS never moves. Other devices track
  # the AGNOS this release declares, like stock openpilot.
  if grep -q "comma tici" /sys/firmware/devicetree/base/model 2>/dev/null; then
    export AGNOS_VERSION="12.8"
  else
    export AGNOS_VERSION="19.9"
  fi
fi

# AGNOS 12.8: venv Python has pyray/raylib dependencies
# AGNOS 16+: system Python has everything
export PATH="/usr/local/venv/bin:$PATH"

# AGNOS 12.8 (comma three): raylib draws on DRM directly and c3_compat stops
# Weston. There is no DRM fd broker ("magic", AGNOS 19), so the raylib comma
# backend must open /dev/dri/card0 itself, which it does only when NO_MASTER is set.
AGNOS_MAJOR=$(echo "$AGNOS_VERSION" | cut -d. -f1)
if [ "$AGNOS_MAJOR" -lt 16 ] 2>/dev/null; then
  export NO_MASTER=1
fi

export STAGING_ROOT="/data/safe_staging"
