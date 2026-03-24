<div align="center" style="text-align: center;">

<h1>catpilot</h1>

<p>
  <b>A plugin framework for <a href="https://github.com/commaai/openpilot">openpilot</a>.</b>
  <br>
  Extend your comma device with plugins — no fork maintenance required.
</p>

<h3>
  <a href="https://github.com/catpilot-dev/plugins">Plugins</a>
  <span> · </span>
  <a href="https://github.com/catpilot-dev/connect">Connect on Device</a>
</h3>

Install: `installer.comma.ai/catpilot-dev/catpilot`

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

## What is catpilot?

catpilot is stock openpilot with a thin plugin layer on top. The openpilot code is untouched — catpilot only adds hook call sites that let plugins extend behavior at runtime. Upgrading to a new openpilot release means rebasing 5 commits.

**What catpilot adds to stock openpilot:**

| Commit | What it adds |
|--------|-------------|
| Brand as catpilot | Welcome screen, device paths, repo references |
| C3 hardware | AR0231 sensor driver, Venus firmware wait, LogReader recovery |
| Plugin framework | `selfdrive/plugins/` — hook dispatch, registry, manifest loader, plugin bus |
| UI layout | Settings sidebar reorder, home screen widget hooks |
| Screen capture | Render texture on device, `ui.pre_end_drawing` hook |

Everything else is upstream openpilot.

## Architecture

catpilot inserts lightweight hook call sites into openpilot's control loop, planner, UI, and manager. Each hook follows a fail-safe pattern: if a plugin callback raises an exception, the default value is returned and other plugins continue running.

Plugins live in a separate repo ([catpilot-dev/plugins](https://github.com/catpilot-dev/plugins)) and are installed to `/data/plugins-runtime/` on the device. Zero file overlays — all customization happens through hooks.

### Hook Call Sites

#### Controls & Planning
| Hook | Location | Description |
|------|----------|-------------|
| `controls.curvature_correction` | controlsd.py | Adjust steering curvature (lane centering) |
| `controls.post_actuators` | controlsd.py | Post-process actuators (e.g. vTarget override) |
| `planner.subscriptions` | plannerd.py | Add cereal services to planner |
| `planner.v_cruise` | longitudinal_planner.py | Modify target cruise speed |
| `planner.accel_limits` | longitudinal_planner.py | Adjust acceleration limits |

#### Lane Change
| Hook | Location | Description |
|------|----------|-------------|
| `desire.pre_lane_change` | desire_helper.py | Pre-state-machine hook |
| `desire.post_lane_change` | desire_helper.py | Post-state-machine trigger detection |
| `desire.post_update` | desire_helper.py | Modify lane change desire signals |

#### Car & Device
| Hook | Location | Description |
|------|----------|-------------|
| `car.cruise_initialized` | card.py | Called when cruise control engages |
| `torqued.allowed_cars` | torqued.py | Extend cars allowed for steering learning |
| `device.health_check` | plugin-defined | Device health monitoring |
| `manager.startup` | plugin-defined | Manager initialization |

#### UI
| Hook | Location | Description |
|------|----------|-------------|
| `ui.settings_extend` | settings.py | Add custom settings panels |
| `ui.home_extend` | home.py | Add home screen widgets |
| `ui.main_extend` | main.py | Customize main layout |
| `ui.state_tick` | ui_state.py | Called every UI state update |
| `ui.state_subscriptions` | ui_state.py | Add cereal subscriptions to UI |
| `ui.software_settings_extend` | software.py | Add items to software panel |
| `ui.network_settings_extend` | settings.py | Customize network settings |
| `ui.onroad_exp_button` | hud_renderer.py | Customize experimental mode button |
| `ui.hud_set_speed_override` | hud_renderer.py | Override HUD speed display |
| `ui.hud_speed_color` | hud_renderer.py | Customize speed indicator color |
| `ui.render_overlay` | augmented_road_view.py | Draw on onroad view |
| `ui.pre_end_drawing` | application.py | Draw before frame ends (screen capture) |
| `ui.vehicle_settings` | plugin-defined | Populate vehicle settings |
| `ui.connectivity_check` | sidebar.py | Report connectivity to sidebar |

## Supported Devices

| Device | Panda | Status |
|--------|-------|--------|
| **comma three** (2021) | STM32F4 | Community supported* |
| [comma 3X](https://github.com/commaai/hardware/tree/master/comma_3X) (2023) | STM32H7 | Supported |
| [comma four](https://github.com/commaai/hardware/tree/master/comma_four) (2025) | STM32H7 | Supported |

*\* comma three support enabled by [c3_compat](https://github.com/catpilot-dev/plugins/tree/main/plugins/c3_compat) plugin.*

## Installation

```
installer.comma.ai/catpilot-dev/catpilot
```

On first boot, catpilot automatically sets up plugins and connect on device.

## Companion Projects

- [catpilot-dev/plugins](https://github.com/catpilot-dev/plugins) — plugin packages and installer
- [catpilot-dev/connect](https://github.com/catpilot-dev/connect) — on-device web UI for route browsing and plugin management

## License

MIT — see [LICENSE](LICENSE) for details.
