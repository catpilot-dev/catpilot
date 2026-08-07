<div align="center" style="text-align: center;">

<h1>catpilot</h1>

<p>
  <b>stock openpilot with extra features through a lightweight plugin framework.</b>
</p>

<h3>
  <a href="https://github.com/catpilot-dev/plugins">Plugins</a>
  <span> · </span>
  <a href="https://github.com/catpilot-dev/connect-on-device">Connect on Device</a>
  <span> · </span>
  <a href="DESIGN.md">How it works</a>
</h3>

Install: `installer.catpilot.dev`

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<img src="docs/assets/catpilot_onroad.jpg" width="49%" alt="catpilot onroad view" /> <img src="docs/assets/catpilot_offroad.png" width="49%" alt="catpilot home screen" />

</div>

## What is catpilot?

catpilot is [openpilot](https://github.com/commaai/openpilot) with a lightweight plugin framework to extend a set of features as [*plugins*](https://github.com/catpilot-dev/plugins).

| Feature | What it does for you |
|---------|----------------------|
| [**Speed limit assist**](https://github.com/catpilot-dev/plugins/tree/main/plugins/speedlimitd) | Watches the speed limit for you — from offline map data and from what the camera sees (lane count, road type) — and eases the car down for sharp curves and highway ramps |
| [**Lane keeping**](https://github.com/catpilot-dev/plugins/tree/main/plugins/lane_keeping) | Damps the slow left-right sway ("ping-pong") inside the lane |
| [**Model selector**](https://github.com/catpilot-dev/plugins/tree/main/plugins/model_selector) | Download and switch between driving models from the Software panel |
| [**comma three support**](https://github.com/catpilot-dev/plugins/tree/main/plugins/c3_compat) | Keeps the original comma three working on current catpilot (display, panda, OS) |

## Supported cars

+ [Supported cars](docs/CARS.md)
+ [**BMW E8x / E9x**](https://github.com/catpilot-dev/plugins/tree/main/plugins/bmw_e9x_e8x)

## Supported devices

+ [**comma three**](https://github.com/commaai/hardware/tree/master/comma_three) (2021)
+ [comma 3X](https://github.com/commaai/hardware/tree/master/comma_3X) (2023)
+ [comma four](https://github.com/commaai/hardware/tree/master/comma_four) (2025)

*comma three support comes from the [c3_compat](https://github.com/catpilot-dev/plugins/tree/main/plugins/c3_compat) plugin, which is enabled automatically on that hardware.*

## Safety

catpilot keeps openpilot's safety model as-is: [SAFETY.md](docs/SAFETY.md) and [LIMITATIONS.md](docs/LIMITATIONS.md).

## Companion projects

- [plugins](https://github.com/catpilot-dev/plugins) — the extra features
- [connect-on-device](https://github.com/catpilot-dev/connect-on-device) — on-device web app for routes, OSM map tiles, and plugin management

## How it works

If you want to know what catpilot changes in openpilot and how plugins attach to it,
that's in [DESIGN.md](DESIGN.md). If you want to write a plugin, start with the
[plugin development guide](https://github.com/catpilot-dev/plugins/blob/main/docs/DEVELOPMENT.md).

## Disclaimer

catpilot is not affiliated with or endorsed by comma.ai or BMW.
