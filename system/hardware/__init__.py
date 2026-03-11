import os
from typing import cast

from openpilot.system.hardware.base import HardwareBase
from openpilot.system.hardware.tici.hardware import Tici
from openpilot.system.hardware.pc.hardware import Pc

TICI = os.path.isfile('/TICI')
AGNOS = os.path.isfile('/AGNOS')

def _detect_rk3588() -> bool:
  try:
    with open('/proc/device-tree/compatible') as f:
      return 'rk3588' in f.read()
  except FileNotFoundError:
    return False

RK3588 = _detect_rk3588()
PC = not TICI and not RK3588


if TICI:
  HARDWARE = cast(HardwareBase, Tici())
elif RK3588:
  from openpilot.system.hardware.rk3588.hardware import Rk3588
  HARDWARE = cast(HardwareBase, Rk3588())
else:
  HARDWARE = cast(HardwareBase, Pc())
