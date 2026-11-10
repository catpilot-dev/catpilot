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

Nine commits sit on top of upstream (openpilot v0.11.2). This is the entire delta.

| Commit | Scope | What it changes |
|--------|-------|-----------------|
| `brand` | 6 files | Naming, README, release notes, this document, screenshots |
| `feat: plugin framework` | 36 files | `openpilot/selfdrive/plugins/` (the framework itself, ~10 files + tests) plus the UI, onroad and manager hook call sites, and screen-capture/recording infrastructure |
| `ui: Factory Reset` | 2 files | Moves Uninstall under Device as Factory Reset |
| `controls: lateral hooks` | 1 file | Lateral hook call sites in `controlsd.py` |
| `feat(planner): vTarget` | 7 files | Publishes `vTarget`, the plan speed at the actuator delay: the MPC speed capped at the plugin-adjusted cruise speed and at the speed `aTarget`'s own slope reaches (0.11.2's MPC only follows leads, and cruise is a separate `aTarget` candidate). It never implies more acceleration than `aTarget`; a set speed below `vEgo` makes it a destination |
| `selfdrived: events_filter` | 2 files | `selfdrived.events_filter` hook at the end of `update_events`, plus its test |
| `ui: version text` | 1 file | Drops the redundant version from the home screen when the branch name carries it |
| `launch` | 2 files | Boot chain in `launch_chffrplus.sh` (plugin install, first boot, then c3_compat patches from the just-installed plugins, COD) and the comma three AGNOS 12.8 policy in `launch_env.sh` |
| `first boot` | 1 file | `first_boot_setup.sh`: clones the plugins branch and COD release matching the installed channel; retried on every boot until both are in place, no reboot |

The framework, lateral-hooks and events_filter commits add hook call sites. The rest is
branding, boot plumbing and one upstream backport. comma three hardware support lives in
the `c3_compat` plugin (on-device boot patches), not in this repo.

## The hook mechanism

`openpilot/selfdrive/plugins/hooks.py` implements the whole dispatch layer. A single module-level
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

### Plugin processes belong to plugind

Processes declared in a plugin's `processes` list are not manager processes. plugind
spawns them in their own process group (so they survive a plugind restart) and enforces
each one's `condition`: `always_run`, `only_onroad` or `only_offroad`, from
`deviceState.started`. Until plugind has received `deviceState`, conditional processes
are left as they are. An onroad/offroad transition reaches them within about a second.

## Where the call sites are

19 hooks. 18 are dispatched from 12 upstream files; `device.health_check` is dispatched
by plugind, catpilot's own daemon. Grouped by where they sit in openpilot's flow;
signatures and current consumers are in the plugins repo's
[`HOOK_INTEGRATION_POINTS.md`](https://github.com/catpilot-dev/plugins/blob/main/docs/HOOK_INTEGRATION_POINTS.md).

### Controls — `openpilot/selfdrive/controls/`

| Hook | File | Point in the flow |
|------|------|-------------------|
| `controls.lat_controller_init` | `controlsd.py` | Once at init, after the lateral controller is constructed — lets a plugin configure or replace it |
| `controls.curvature_correction` | `controlsd.py` | Every frame, on the model's curvature before it reaches the lateral controller |
| `controls.post_actuators` | `controlsd.py` | Every frame, after actuators are computed — read-only observation point |
| `planner.v_cruise` | `lib/longitudinal_planner.py` | Every planner cycle, on target cruise speed — runs before the force-decel override |

### Car and device

| Hook | File | Point in the flow |
|------|------|-------------------|
| `car.cruise_initialized` | `openpilot/selfdrive/car/card.py` | When cruise control engages |
| `selfdrived.events_filter` | `openpilot/selfdrive/selfdrived/selfdrived.py` | End of `update_events`, on the populated event set — lets car plugins filter events (engagement policies) |
| `device.health_check` | `openpilot/selfdrive/plugins/plugind.py` | Every plugind full pass (5 s), accumulating a `{plugin: status}` dict |

### UI — `openpilot/selfdrive/`

| Hook | File | Point in the flow |
|------|------|-------------------|
| `ui.settings_extend` | `ui/layouts/settings/settings.py` | Add settings panels |
| `ui.network_settings_extend` | `ui/layouts/settings/settings.py` | Extend the network panel |
| `ui.software_settings_extend` | `ui/layouts/settings/software.py` | Extend the software panel |
| `ui.home_extend` | `ui/layouts/home.py` | Add offroad home-screen widgets |
| `ui.main_extend` | `ui/layouts/main.py` | Customise the main layout |
| `ui.state_tick` | `ui/ui_state.py` | Every UI state update |
| `ui.state_subscriptions` | `ui/ui_state.py` | Add cereal subscriptions to the UI |
| `ui.render_overlay` | `ui/onroad/augmented_road_view.py` | Draw over the onroad view |
| `ui.onroad_exp_button` | `ui/onroad/hud_renderer.py` | Customise the experimental-mode button |
| `ui.hud_set_speed_override` | `ui/onroad/hud_renderer.py` | Override the displayed set speed |
| `ui.pre_end_drawing` | `openpilot/system/ui/lib/application.py` | Before the frame ends — screen capture |
| `ui.post_end_drawing` | `openpilot/system/ui/lib/application.py` | After the frame ends — screen capture |

`ui.vehicle_settings` is **not** a catpilot call site. It is dispatched by the
`ui_mod` plugin so car plugins can populate the Driving panel; it lives entirely in
the plugins repo.

## Keeping cereal clean: build-time schema injection

Plugins that publish logged messages need entries in `openpilot/cereal/custom.capnp`,
`openpilot/cereal/log.capnp` and `openpilot/cereal/services.py`. Committing those would violate the
no-overlay rule and would conflict on every rebase.

Instead `openpilot/selfdrive/plugins/builder.py` patches them in the working tree at boot,
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

Two consequences for anyone working in this repo: a dirty `openpilot/cereal/` working tree after
boot is expected, not a mistake; and `git checkout -- openpilot/cereal/` is the escape
hatch that restores those files.

## Rebasing onto a new openpilot release

The delta is small on purpose. In practice:

1. Rebase the catpilot commits onto the new upstream tag.
2. Expect conflicts only where a hook call site sits inside code upstream also
   touched. Resolve by re-inserting the `hooks.run()` line into the new control flow —
   the hook contract rarely needs to change, only its placement.
3. Check the reserved capnp slot `@ID`s in `builder.py` still match upstream's
   `custom.capnp`. Upstream renumbering here is silent and would corrupt plugin
   message decoding.
4. Update `OPENPILOT_VERSION` in `openpilot/selfdrive/plugins/manifest.py`. Plugins declare
   `min_openpilot` / `max_openpilot` against this constant, and a stale value silently
   rejects plugins as incompatible.
5. Run the framework tests: `PYTHONPATH=. uv run pytest openpilot/selfdrive/plugins/tests/`.

Past upgrades are recorded in [RELEASES.md](RELEASES.md).
