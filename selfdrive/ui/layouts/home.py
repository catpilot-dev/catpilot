import time
import pyray as rl
from collections.abc import Callable
from enum import IntEnum
from openpilot.common.params import Params
from openpilot.selfdrive.ui.widgets.offroad_alerts import UpdateAlert, OffroadAlert
from openpilot.selfdrive.ui.widgets.exp_mode_button import ExperimentalModeButton
from openpilot.selfdrive.ui.widgets.prime import PrimeWidget
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.system.ui.lib.multilang import tr, trn
from openpilot.system.ui.widgets.label import gui_label
from openpilot.system.ui.widgets import Widget

HEADER_HEIGHT = 80
HEAD_BUTTON_FONT_SIZE = 40
CONTENT_MARGIN = 40
SPACING = 25
RIGHT_COLUMN_WIDTH = 750
REFRESH_INTERVAL = 10.0

# Green badge for ecosystem updates (plugins, COD, models, mapd)
BADGE_COLOR = rl.Color(76, 175, 80, 255)
BADGE_FONT_SIZE = 32


class HomeLayoutState(IntEnum):
  HOME = 0
  UPDATE = 1
  ALERTS = 2


class HomeLayout(Widget):
  def __init__(self):
    super().__init__()
    self.params = Params()

    self.update_alert = UpdateAlert()
    self.offroad_alert = OffroadAlert()

    self._layout_widgets = {HomeLayoutState.UPDATE: self.update_alert, HomeLayoutState.ALERTS: self.offroad_alert}

    self.current_state = HomeLayoutState.HOME
    self.last_refresh = 0
    self.settings_callback: Callable[[], None] | None = None
    self.plugins_callback: Callable[[], None] | None = None

    self.update_available = False
    self.alert_count = 0
    self.eco_update_count = 0
    self._version_text = ""
    self._prev_update_available = False
    self._prev_alerts_present = False
    self._eco_update_checker: callable | None = None

    self.header_rect = rl.Rectangle(0, 0, 0, 0)
    self.content_rect = rl.Rectangle(0, 0, 0, 0)
    self.left_column_rect = rl.Rectangle(0, 0, 0, 0)
    self.right_column_rect = rl.Rectangle(0, 0, 0, 0)

    self.update_notif_rect = rl.Rectangle(0, 0, 200, HEADER_HEIGHT - 10)
    self.alert_notif_rect = rl.Rectangle(0, 0, 220, HEADER_HEIGHT - 10)
    self.eco_badge_rect = rl.Rectangle(0, 0, 60, 60)

    self._prime_widget = PrimeWidget()
    self._left_widget = self._prime_widget
    self._right_widget = None  # plugins can set via set_right_widget()

    self._exp_mode_button = ExperimentalModeButton()
    self._setup_callbacks()

    # Plugin hook: let plugins customize the home layout
    try:
      from openpilot.selfdrive.plugins.hooks import hooks
      hooks.run('ui.home_extend', None, self)
    except ImportError:
      pass

  def show_event(self):
    super().show_event()
    self._exp_mode_button.show_event()
    if self._left_widget:
      self._left_widget.show_event()
    if self._right_widget:
      self._right_widget.show_event()
    self.last_refresh = time.monotonic()
    self._refresh()

  def _setup_callbacks(self):
    self.update_alert.set_dismiss_callback(lambda: self._set_state(HomeLayoutState.HOME))
    self.offroad_alert.set_dismiss_callback(lambda: self._set_state(HomeLayoutState.HOME))
    self._exp_mode_button.set_click_callback(lambda: self.settings_callback() if self.settings_callback else None)

  def set_left_widget(self, widget):
    self._left_widget = widget

  def set_right_widget(self, widget):
    self._right_widget = widget

  def set_plugins_callback(self, callback: Callable):
    self.plugins_callback = callback

  def set_eco_update_checker(self, checker):
    self._eco_update_checker = checker

  def set_settings_callback(self, callback: Callable):
    self.settings_callback = callback

  def _set_state(self, state: HomeLayoutState):
    # propagate show/hide events
    if state != self.current_state:
      if state == HomeLayoutState.HOME:
        self._exp_mode_button.show_event()

      if state in self._layout_widgets:
        self._layout_widgets[state].show_event()
      if self.current_state in self._layout_widgets:
        self._layout_widgets[self.current_state].hide_event()

    self.current_state = state

  def _render(self, rect: rl.Rectangle):
    current_time = time.monotonic()
    if current_time - self.last_refresh >= REFRESH_INTERVAL:
      self._refresh()
      self.last_refresh = current_time

    self._render_header()

    # Render content based on current state
    if self.current_state == HomeLayoutState.HOME:
      self._render_home_content()
    elif self.current_state == HomeLayoutState.UPDATE:
      self._render_update_view()
    elif self.current_state == HomeLayoutState.ALERTS:
      self._render_alerts_view()

  def _update_state(self):
    self.header_rect = rl.Rectangle(
      self._rect.x + CONTENT_MARGIN, self._rect.y + CONTENT_MARGIN, self._rect.width - 2 * CONTENT_MARGIN, HEADER_HEIGHT
    )

    content_y = self._rect.y + CONTENT_MARGIN + HEADER_HEIGHT + SPACING
    content_height = self._rect.height - CONTENT_MARGIN - HEADER_HEIGHT - SPACING - CONTENT_MARGIN

    self.content_rect = rl.Rectangle(
      self._rect.x + CONTENT_MARGIN, content_y, self._rect.width - 2 * CONTENT_MARGIN, content_height
    )

    left_width = self.content_rect.width - RIGHT_COLUMN_WIDTH - SPACING

    self.left_column_rect = rl.Rectangle(self.content_rect.x, self.content_rect.y, left_width, self.content_rect.height)

    self.right_column_rect = rl.Rectangle(
      self.content_rect.x + left_width + SPACING, self.content_rect.y, RIGHT_COLUMN_WIDTH, self.content_rect.height
    )

    self.update_notif_rect.x = self.header_rect.x
    self.update_notif_rect.y = self.header_rect.y + (self.header_rect.height - 60) // 2

    notif_x = self.header_rect.x + (220 if self.update_available else 0)
    self.alert_notif_rect.x = notif_x
    self.alert_notif_rect.y = self.header_rect.y + (self.header_rect.height - 60) // 2

    # Eco badge: positioned after alert button (or update button, or at the start)
    eco_x = self.header_rect.x
    if self.update_available:
      eco_x += 220
    if self.alert_count > 0:
      eco_x += 240
    self.eco_badge_rect.x = eco_x
    self.eco_badge_rect.y = self.header_rect.y + (self.header_rect.height - 60) // 2

  def _handle_mouse_release(self, mouse_pos: MousePos):
    super()._handle_mouse_release(mouse_pos)

    if self.update_available and rl.check_collision_point_rec(mouse_pos, self.update_notif_rect):
      self._set_state(HomeLayoutState.UPDATE)
    elif self.alert_count > 0 and rl.check_collision_point_rec(mouse_pos, self.alert_notif_rect):
      self._set_state(HomeLayoutState.ALERTS)
    elif self.eco_update_count > 0 and rl.check_collision_point_rec(mouse_pos, self.eco_badge_rect):
      if self.plugins_callback:
        self.plugins_callback()

  def _render_header(self):
    font = gui_app.font(FontWeight.MEDIUM)

    version_text_width = self.header_rect.width

    # Update notification button
    if self.update_available:
      version_text_width -= self.update_notif_rect.width

      # Highlight if currently viewing updates
      highlight_color = rl.Color(75, 95, 255, 255) if self.current_state == HomeLayoutState.UPDATE else rl.Color(54, 77, 239, 255)
      rl.draw_rectangle_rounded(self.update_notif_rect, 0.3, 10, highlight_color)

      text = tr("UPDATE")
      text_size = measure_text_cached(font, text, HEAD_BUTTON_FONT_SIZE)
      text_x = self.update_notif_rect.x + (self.update_notif_rect.width - text_size.x) // 2
      text_y = self.update_notif_rect.y + (self.update_notif_rect.height - text_size.y) // 2
      rl.draw_text_ex(font, text, rl.Vector2(int(text_x), int(text_y)), HEAD_BUTTON_FONT_SIZE, 0, rl.WHITE)

    # Alert notification button
    if self.alert_count > 0:
      version_text_width -= self.alert_notif_rect.width

      # Highlight if currently viewing alerts
      highlight_color = rl.Color(255, 70, 70, 255) if self.current_state == HomeLayoutState.ALERTS else rl.Color(226, 44, 44, 255)
      rl.draw_rectangle_rounded(self.alert_notif_rect, 0.3, 10, highlight_color)

      alert_text = trn("{} ALERT", "{} ALERTS", self.alert_count).format(self.alert_count)
      text_size = measure_text_cached(font, alert_text, HEAD_BUTTON_FONT_SIZE)
      text_x = self.alert_notif_rect.x + (self.alert_notif_rect.width - text_size.x) // 2
      text_y = self.alert_notif_rect.y + (self.alert_notif_rect.height - text_size.y) // 2
      rl.draw_text_ex(font, alert_text, rl.Vector2(int(text_x), int(text_y)), HEAD_BUTTON_FONT_SIZE, 0, rl.WHITE)

    # Ecosystem update badge (green circle with count)
    if self.eco_update_count > 0:
      version_text_width -= self.eco_badge_rect.width + SPACING // 2

      badge_cx = self.eco_badge_rect.x + self.eco_badge_rect.width // 2
      badge_cy = self.eco_badge_rect.y + self.eco_badge_rect.height // 2
      rl.draw_circle(int(badge_cx), int(badge_cy), self.eco_badge_rect.width // 2, BADGE_COLOR)

      badge_text = str(self.eco_update_count)
      text_size = measure_text_cached(font, badge_text, BADGE_FONT_SIZE)
      text_x = badge_cx - text_size.x // 2
      text_y = badge_cy - text_size.y // 2
      rl.draw_text_ex(font, badge_text, rl.Vector2(int(text_x), int(text_y)), BADGE_FONT_SIZE, 0, rl.WHITE)

    # Version text (right aligned)
    if self.update_available or self.alert_count > 0 or self.eco_update_count > 0:
      version_text_width -= SPACING * 1.5

    version_rect = rl.Rectangle(self.header_rect.x + self.header_rect.width - version_text_width, self.header_rect.y,
                                version_text_width, self.header_rect.height)
    gui_label(version_rect, self._version_text, 48, rl.WHITE, alignment=rl.GuiTextAlignment.TEXT_ALIGN_RIGHT)

  def _render_home_content(self):
    self._render_left_column()
    self._render_right_column()

  def _render_update_view(self):
    self.update_alert.render(self.content_rect)

  def _render_alerts_view(self):
    self.offroad_alert.render(self.content_rect)

  def _render_left_column(self):
    if self._left_widget:
      self._left_widget.render(self.left_column_rect)

  def _render_right_column(self):
    exp_height = 125
    exp_rect = rl.Rectangle(
      self.right_column_rect.x, self.right_column_rect.y, self.right_column_rect.width, exp_height
    )
    self._exp_mode_button.render(exp_rect)

    if self._right_widget:
      widget_rect = rl.Rectangle(
        self.right_column_rect.x,
        self.right_column_rect.y + exp_height + SPACING,
        self.right_column_rect.width,
        self.right_column_rect.height - exp_height - SPACING,
      )
      self._right_widget.render(widget_rect)

  def _refresh(self):
    self._version_text = self._get_version_text()
    update_available = self.update_alert.refresh()
    alert_count = self.offroad_alert.refresh()
    alerts_present = alert_count > 0

    if self._eco_update_checker:
      try:
        self.eco_update_count = self._eco_update_checker()
      except Exception:
        self.eco_update_count = 0

    # Show panels on transition from no alert/update to any alerts/update
    if not update_available and not alerts_present:
      self._set_state(HomeLayoutState.HOME)
    elif update_available and ((not self._prev_update_available) or (not alerts_present and self.current_state == HomeLayoutState.ALERTS)):
      self._set_state(HomeLayoutState.UPDATE)
    elif alerts_present and ((not self._prev_alerts_present) or (not update_available and self.current_state == HomeLayoutState.UPDATE)):
      self._set_state(HomeLayoutState.ALERTS)

    self.update_available = update_available
    self.alert_count = alert_count
    self._prev_update_available = update_available
    self._prev_alerts_present = alerts_present

  def _get_version_text(self) -> str:
    brand = "catpilot"
    description = self.params.get("UpdaterCurrentDescription")
    return f"{brand} {description}" if description else brand
