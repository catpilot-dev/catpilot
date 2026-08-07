<div align="center" style="text-align: center;">

<h1>catpilot</h1>

<p>
  <b>openpilot, with the extras you actually want.</b>
  <br>
  Everything openpilot does — plus features you can switch on and off one by one.
</p>

<h3>
  <a href="https://github.com/catpilot-dev/plugins">Plugins</a>
  <span> · </span>
  <a href="https://github.com/catpilot-dev/connect-on-device">Connect on Device</a>
  <span> · </span>
  <a href="DESIGN.md">How it works</a>
</h3>

Install: `installer.comma.ai/catpilot-dev/catpilot`

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<img src="docs/assets/catpilot_onroad.jpg" width="49%" alt="catpilot onroad view" /> <img src="docs/assets/catpilot_offroad.png" width="49%" alt="catpilot home screen" />

</div>

## What is catpilot?

catpilot is [openpilot](https://github.com/commaai/openpilot) with a plugin layer on top.

Everything stock openpilot does, catpilot does — same driving models, same safety
model, same supported cars. On top of that it ships a set of features as *plugins*:
self-contained add-ons you can turn on and off individually in Settings, without
reflashing your device or picking a different fork.

The openpilot code underneath is left untouched, so catpilot tracks upstream
releases closely rather than drifting away from them.

## What you get on top of openpilot

Everything below comes pre-installed. Flash catpilot and it is already on the device.

### On the road

| Feature | What it does for you |
|---------|----------------------|
| **Speed limit assist** | Watches the speed limit for you — from offline map data and from what the camera sees (lane count, road type) — and eases the car down for sharp curves and highway ramps |
| **Steadier lane centering** | Damps the slow left-right sway ("ping-pong") inside the lane, so the car holds its line more calmly |
| **BMW E8x / E9x support** | Adds a car openpilot doesn't support out of the box — see [Supported cars](#supported-cars) for the hardware this needs |

### On the screen

| Feature | What it does for you |
|---------|----------------------|
| **catpilot home screen** | Drive statistics and a map of your route on the offroad screen |
| **Your car on the driving screen** | Your car's emblem, plus extra settings panels (Driving, Vehicle, Plugins) |
| **Model selector** | Download and switch between driving models from the Software panel |
| **Screenshots** | Tap the camera icon to save a picture of the driving screen |
| **Drive replay video** | Renders your drives to video with the HUD drawn on top, for review in Connect on Device |

### Under the hood

| Feature | What it does for you |
|---------|----------------------|
| **comma three support** | Keeps the original comma three working on current catpilot (display, panda, OS) |
| **Connect on Device** | An on-device web app for browsing your routes and managing plugins from a phone or laptop |
| **Diagnostics** | Plugin status is written into the drive logs, so problems can be worked out after a drive |

The full list, with a page per plugin, lives in the
[plugins repo](https://github.com/catpilot-dev/plugins).

## Supported cars

All [cars openpilot supports](docs/CARS.md), unchanged.

catpilot additionally supports the **BMW E8x / E9x** family — 1-Series coupe and
convertible (E82 / E88, 2004–13) and 3-Series sedan, wagon, coupe and convertible
(E90 / E91 / E92 / E93, 2005–11). Be aware this is not a plug-in-and-drive setup:
these cars have no factory driver assistance to build on, so they need DIY cruise
control wiring and an aftermarket Ocelot stepper servo on the steering rack. See the
[BMW plugin page](https://github.com/catpilot-dev/plugins/tree/main/plugins/bmw_e9x_e8x)
before buying anything.

## Supported devices

| Device | Status |
|--------|--------|
| **comma three** (2021) | Community supported* |
| [comma 3X](https://github.com/commaai/hardware/tree/master/comma_3X) (2023) | Supported |
| [comma four](https://github.com/commaai/hardware/tree/master/comma_four) (2025) | Supported |

*\* comma three support comes from the [c3_compat](https://github.com/catpilot-dev/plugins/tree/main/plugins/c3_compat) plugin, which is enabled automatically on that hardware.*

## Installing

On your comma device, choose **Custom Software** during setup and enter:

```
installer.comma.ai/catpilot-dev/catpilot
```

On first boot catpilot sets up the plugins and Connect on Device for you. There is
nothing else to install.

## Turning features on and off

- **Settings → Plugins** — one switch per plugin.
- Individual features have their own switches in the panel they belong to. Lane
  keeping lives in **Driving**, the model selector in **Software**, and your car's
  options in **Vehicle**.
- A few plugins are required by the car itself and can't be switched off wholesale.
  Use their feature switch instead.

## Updating

catpilot updates the way openpilot does, and brings the matching plugins with it.
Nothing to do by hand.

## Safety

catpilot keeps openpilot's safety model as-is: the limits that matter are enforced
in compiled C on the panda, below anything a plugin can touch, and the driver
monitoring is unchanged. Plugins cannot loosen those limits.

That does not make the car self-driving. openpilot is
[Level 2 driver assistance](https://en.wikipedia.org/wiki/Self-driving_car#Levels_of_driving_automation)
— you are driving, you are responsible, and you must be paying attention at all
times. Read [SAFETY.md](docs/SAFETY.md) and [LIMITATIONS.md](docs/LIMITATIONS.md)
before your first drive.

The BMW support in particular is a community effort on cars with no factory driver
assistance, using aftermarket steering hardware. Treat it accordingly.

## Companion projects

- [catpilot-dev/plugins](https://github.com/catpilot-dev/plugins) — the features described above, one directory each
- [catpilot-dev/connect-on-device](https://github.com/catpilot-dev/connect-on-device) — on-device web app for routes and plugin management

## How it works

If you want to know what catpilot changes in openpilot and how plugins attach to it,
that's in [DESIGN.md](DESIGN.md). If you want to write a plugin, start with the
[plugin development guide](https://github.com/catpilot-dev/plugins/blob/main/docs/DEVELOPMENT.md).

## License

MIT — see [LICENSE](LICENSE) for details.

catpilot is a community project. It is not affiliated with or endorsed by comma.ai
or BMW.
