"""Interactive overlay zone registry.

Overlay plugins register hit zones during render (which runs before mouse
event processing).  AugmentedRoadView checks these zones on tap — if the
tap lands inside any registered zone, the sidebar toggle is suppressed.

Zones are cleared at the start of each frame so overlays must re-register
every frame they are visible.

Usage in an overlay hook:
    from openpilot.selfdrive.ui.onroad.overlay_zones import register_circle_zone
    register_circle_zone(cx, cy, radius)
"""

_circles: list[tuple[float, float, float]] = []   # (cx, cy, r)
_rects: list[tuple[float, float, float, float]] = []  # (x, y, w, h)


def clear():
  """Called once per frame before render."""
  _circles.clear()
  _rects.clear()


def register_circle_zone(cx: float, cy: float, r: float):
  """Register a circular interactive zone (e.g. speed limit sign)."""
  _circles.append((cx, cy, r))


def register_rect_zone(x: float, y: float, w: float, h: float):
  """Register a rectangular interactive zone."""
  _rects.append((x, y, w, h))


def hit_test(px: float, py: float) -> bool:
  """Return True if (px, py) is inside any registered zone."""
  for cx, cy, r in _circles:
    dx = px - cx
    dy = py - cy
    if dx * dx + dy * dy <= r * r:
      return True
  for x, y, w, h in _rects:
    if x <= px <= x + w and y <= py <= y + h:
      return True
  return False
