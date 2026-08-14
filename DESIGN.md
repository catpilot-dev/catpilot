# How catpilot attaches to openpilot

This document covers one thing: **what catpilot changes in upstream openpilot, and
how plugins attach to it.**

Everything on the plugin side of the boundary is documented in the
[plugins repo](https://github.com/catpilot-dev/plugins) and is deliberately not
repeated here:

| For | Read |
|-----|------|
| Hook signatures, argument order, and which plugin consumes each one | [`docs/HOOK_INTEGRATION_POINTS.md`](https://github.com/catpilot-dev/plugins/blob/main/docs/HOOK_INTEGRATION_POINTS.md) |
| `plugin.json` schema, on-device layout, boot flow, plugin params | [`docs/INTEGRATION_ARCHITECTURE.md`](https://github.com/catpilot-dev/plugins/blob/main/docs/INTEGRATION_ARCHITECTURE.md) |
| Writing, installing, testing and debugging a plugin | [`docs/DEVELOPMENT.md`](https://github.com/catpilot-dev/plugins/blob/main/docs/DEVELOPMENT.md) |
| What each shipped plugin does, and its own design notes | plugin `README.md` / `DESIGN.md` under [`plugins/`](https://github.com/catpilot-dev/plugins/tree/main/plugins) |

## Design goal: stay rebasable

catpilot exists to add features to openpilot without becoming a fork that drifts.
Two rules follow from that, and everything below is a consequence of them:

1. **No file overlays.** catpilot never ships a modified copy of an upstream file
   that a plugin then replaces at runtime. Behaviour is added by *calling out* from
   upstream code, never by substituting it.
2. **Additive call sites only.** Where catpilot does touch an upstream file, the
   change is a hook call inserted into existing control flow — not a rewrite of it.
   A hook call site is typically three lines and does not change upstream behaviour
   when no plugin is registered.

The payoff is the upgrade path: moving to a new openpilot release is a rebase of a
handful of commits, and conflicts are confined to the lines immediately around each
insertion point.

## What catpilot changes in openpilot

Six commits sit on top of upstream. This is the entire delta.

| Commit | Scope | What it changes |
|--------|-------|-----------------|
| `brand` | 2 files | Naming, welcome screen, repo references, release notes |
| `feat(C3)` | 33 files | comma three hardware: AR0231 sensor driver, Venus firmware wait, pandad F4 support, LogReader recovery, build compatibility |
| `feat: plugin framework` | 45 files | `selfdrive/plugins/` (the framework itself, ~10 files + tests) plus the UI, onroad and manager hook call sites, and screen-capture/recording infrastructure |
| `ui: Factory Reset` | 2 files | Moves Uninstall under Device as Factory Reset |
| `controls: lateral hooks` | 2 files | Lateral hook call sites in `controlsd.py`, plus native consecutive lane change in `desire_helper.py` |
| `feat(planner): vTarget` | 5 files | Publishes `vTarget` time-aligned with `aTarget` (backported upstream plumbing) |

Only the third and fifth add hook call sites. The rest is hardware support, branding
and one upstream backport.

## The hook mechanism

`selfdrive/plugins/hooks.py` implements the whole dispatch layer. A single module-level
singleton, `hooks`, is what upstream files import.

### Call sites

Every insertion into upstream code has the same shape:

```python
from openpilot.selfdrive.plugins.hooks import hooks
...
value = hooks.run('some.hook_name', value, extra_context, ...)
```

`run()` takes the hook name, the value that would otherwise have been used, and
whatever context plugins need. When no plugin is registered it returns that value
immediately, so an unused call site costs roughly a dict lookup.

### Dispatch is a fold, not a broadcast

Callbacks have the signature `callback(current_value, *args, **kwargs) -> new_value`.
`run()` seeds the accumulator with `default` and threads each callback's return value
into the next, in ascending `priority` order (default 50, **lower runs first**).

This means a hook is a value transformation, and several plugins can compose on one
hook — each sees what the previous one produced. Priority is the only ordering
guarantee and it is global across plugins, so plugins sharing a hook have to agree on
numbers.

Hooks used purely for side effects (`ui.*_extend`, `controls.post_actuators`,
`car.cruise_initialized`) pass `default=None` and ignore the result. There is no
separate void-hook mechanism; they are just folds whose value nobody reads.

### Fail-safe: the chain aborts, upstream continues

If any callback raises, `run()` logs the exception and returns the **original
`default`** — not the partially accumulated value. Remaining callbacks are skipped
for that call.

This is the important safety property, and it is worth being precise about it: a
misbehaving plugin cannot corrupt an upstream value, because on failure upstream gets
back exactly what it passed in. It also means a plugin failure discards the work of
earlier plugins on that hook for that tick. Plugins that want to no-op on error must
catch internally and return `current_value` rather than raising.

### Loading is lazy and per-process

Plugins are not loaded at import. The first `hooks.run()` in a process triggers
discovery and loading of enabled plugins, once — a failure is not retried in that
process.

The consequence worth internalising: **controlsd, plannerd, card, the UI and plugind
each load their own copy of the plugin code, in their own address space.** Plugin
state is per-process and is not shared by virtue of being "the same plugin". Anything
that genuinely has to cross process boundaries goes over the plugin bus or cereal.

This is also why every UI import inside a plugin must be lazy — a module-level UI
import would execute in controlsd too.

## Where the call sites are

24 hooks. 23 are dispatched from 15 upstream files; `device.health_check` is dispatched
by plugind, catpilot's own daemon. Grouped by where they sit in openpilot's flow;
signatures and current consumers are in the plugins repo's
[`HOOK_INTEGRATION_POINTS.md`](https://github.com/catpilot-dev/plugins/blob/main/docs/HOOK_INTEGRATION_POINTS.md).

### Controls — `selfdrive/controls/`

| Hook | File | Point in the flow |
|------|------|-------------------|
| `controls.lat_controller_init` | `controlsd.py` | Once at init, after the lateral controller is constructed — lets a plugin configure or replace it |
| `controls.curvature_correction` | `controlsd.py` | Every frame, on the model's curvature before it reaches the lateral controller |
| `controls.post_actuators` | `controlsd.py` | Every frame, after actuators are computed — read-only observation point |
| `desire.post_update` | `lib/desire_helper.py` | After the lane-change state machine updates, on the resulting desire |
| `planner.subscriptions` | `plannerd.py` | Once at init, on the service list — lets a plugin add cereal subscriptions |
| `planner.v_cruise` | `lib/longitudinal_planner.py` | Every planner cycle, on target cruise speed |
| `planner.accel_limits` | `lib/longitudinal_planner.py` | Every planner cycle, on the acceleration clip |

### Car and device

| Hook | File | Point in the flow |
|------|------|-------------------|
| `car.cruise_initialized` | `selfdrive/car/card.py` | When cruise control engages |
| `torqued.allowed_cars` | `selfdrive/locationd/torqued.py` | **At module import**, on the allow-list for steering torque learning — not per-frame |
| `device.health_check` | `selfdrive/plugins/plugind.py` | Every plugind tick, accumulating a `{plugin: status}` dict |

### UI

| Hook | File | Point in the flow |
|------|------|-------------------|
| `ui.settings_extend` | `ui/layouts/settings/settings.py` | Add settings panels |
| `ui.network_settings_extend` | `ui/layouts/settings/settings.py` | Extend the network panel |
| `ui.software_settings_extend` | `ui/layouts/settings/software.py` | Extend the software panel |
| `ui.home_extend` | `ui/layouts/home.py` | Add offroad home-screen widgets |
| `ui.main_extend` | `ui/layouts/main.py` | Customise the main layout |
| `ui.state_tick` | `ui/ui_state.py` | Every UI state update |
| `ui.state_subscriptions` | `ui/ui_state.py` | Add cereal subscriptions to the UI |
| `ui.connectivity_check` | `ui/layouts/sidebar.py` | Report connectivity into the sidebar |
| `ui.render_overlay` | `ui/onroad/augmented_road_view.py` | Draw over the onroad view |
| `ui.onroad_exp_button` | `ui/onroad/hud_renderer.py` | Customise the experimental-mode button |
| `ui.hud_set_speed_override` | `ui/onroad/hud_renderer.py` | Override the displayed set speed |
| `ui.hud_speed_color` | `ui/onroad/hud_renderer.py` | Colour the speed readout |
| `ui.pre_end_drawing` | `system/ui/lib/application.py` | Before the frame ends — screen capture |
| `ui.post_end_drawing` | `system/ui/lib/application.py` | After the frame ends — screen capture |

`ui.vehicle_settings` is **not** a catpilot call site. It is dispatched by the
`ui_mod` plugin so car plugins can populate the Driving panel; it lives entirely in
the plugins repo.

## Keeping cereal clean: build-time schema injection

Plugins that publish logged messages need entries in `cereal/custom.capnp`,
`cereal/log.capnp` and `cereal/services.py`. Committing those would violate the
no-overlay rule and would conflict on every rebase.

Instead `selfdrive/plugins/builder.py` patches them in the working tree at boot,
called once from `manager_init()` *before* any process imports cereal. The committed
files stay byte-identical to upstream in git.

Upstream reserves 20 slots in `custom.capnp` with fixed struct `@ID`s. A plugin claims
a slot; the builder swaps in that plugin's schema fragment, matching by `@ID` rather
than by name, and renames the corresponding `Event` union field so the plugin's
messages appear under their own name in rlogs. A `services.py` entry is generated
alongside, which is what makes loggerd record them.

The rebuild is skipped unless a hash over plugin ids, versions, schema mtimes and
enable state changes — cached in `/tmp/plugin_build_hash`, so it is recomputed each
boot. Deleting that file forces a rebuild.

Two consequences for anyone working in this repo: a dirty `cereal/` working tree after
boot is expected, not a mistake; and `restore_stock()` in `builder.py` is the escape
hatch that checks those files back out.

## Rebasing onto a new openpilot release

The delta is small on purpose. In practice:

1. Rebase the six commits onto the new upstream tag.
2. Expect conflicts only where a hook call site sits inside code upstream also
   touched. Resolve by re-inserting the `hooks.run()` line into the new control flow —
   the hook contract rarely needs to change, only its placement.
3. Check the reserved capnp slot `@ID`s in `builder.py` still match upstream's
   `custom.capnp`. Upstream renumbering here is silent and would corrupt plugin
   message decoding.
4. Update `OPENPILOT_VERSION` in `selfdrive/plugins/manifest.py`. Plugins declare
   `min_openpilot` / `max_openpilot` against this constant, and a stale value silently
   rejects plugins as incompatible.
5. Run the framework tests: `PYTHONPATH=. uv run pytest selfdrive/plugins/tests/`.

Past upgrades are recorded in [RELEASES.md](RELEASES.md).
