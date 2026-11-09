catpilot v0.11.2 (2026-11-09)
========================
* Based on stock openpilot v0.11.2 (2026-08-12)
* comma three stays supported: c3_compat now builds the camera sensor and panda
  USB support on the device, so the first boot after installing takes longer
* Plugin framework: `selfdrived.events_filter` hook (two-stage disengagement
  for car plugins), lateral/desire hooks and time-aligned `vTarget` for
  longitudinal plugins, manager restarts the UI if it crashes
* Plugins 0.11.2 (see the plugins repo): BMW E9x/E8x Direct Tracking lateral
  controller and steering-only (LKA) mode, speedlimitd map curve look-ahead on
  expressways, Model Selector with the POP driving model carried to 0.11.2,
  mapd back on v2.4.0

catpilot v0.11.1 (2026-08-07)
========================
* First public release, based on stock openpilot v0.11.1 (2026-05-18)
