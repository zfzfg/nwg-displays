import unittest
from unittest.mock import MagicMock
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk

import nwg_displays.main as main


class DummyEvent:
    def __init__(self, button=1, x=10.0, y=10.0):
        self.button = button
        self.x = x
        self.y = y


class DummyWidget(Gtk.Button):
    def __init__(self, name="DP-1", x=0, y=0, width=1920, height=1080):
        super().__init__()
        self.name = name
        self.description = "Test Display"
        self.x = x
        self.y = y
        self.logical_width = width
        self.logical_height = height
        self.physical_width = width
        self.physical_height = height
        self.scale = 1.0
        self.scale_filter = "nearest"
        self.refresh = 60.0
        self.modes = [{"width": width, "height": height, "refresh": 60000}]
        self.transform = "normal"
        self.dpms = True
        self.adaptive_sync = False
        self.custom_mode = False
        self.ten_bit = False
        self.color_mode = ""
        self.sdr_brightness = 1.0
        self.sdr_saturation = 1.0
        self.sdr_max_luminance = None
        self.mirror = ""
        self.indicator = MagicMock()
        self.indicator.show_up = MagicMock()

    def select(self):
        self.selected = True

    def unselect(self):
        self.selected = False


class TestCanvasDragging(unittest.TestCase):
    def setUp(self):
        main.config = {
            "view-scale": 0.15,
            "snap-threshold": 10,
            "custom-mode": [],
            "use-desc": False,
        }
        main.snap_threshold_scaled = 10
        main.display_buttons = []
        main.selected_output_button = None
        main.fixed = Gtk.Fixed()
        main.is_dragging = False
        main.updating_form = False

    def test_drag_event_lifecycle_and_flags(self):
        btn = DummyWidget("DP-1", x=100, y=100)
        main.display_buttons = [btn]
        main.fixed.put(btn, 15, 15)

        # Mock form update so we don't need all builder UI elements
        main.update_form_from_widget = MagicMock()

        # Simulate button press
        press_event = DummyEvent(button=1, x=25.0, y=30.0)
        main.on_button_press_event(btn, press_event)

        self.assertTrue(main.is_dragging, "is_dragging should be True after button 1 press")
        self.assertEqual(main.grab_offset_x, 25.0)
        self.assertEqual(main.grab_offset_y, 30.0)
        self.assertTrue(btn.selected)

        # Simulate button release
        release_event = DummyEvent(button=1)
        main.on_button_release_event(btn, release_event)

        self.assertFalse(main.is_dragging, "is_dragging should be False after button release")

    def test_spinbutton_feedback_loop_isolated(self):
        btn = DummyWidget("DP-1", x=50, y=50)
        main.display_buttons = [btn]
        main.selected_output_button = btn
        main.fixed.move = MagicMock()

        adj = Gtk.Adjustment(value=200, lower=0, upper=10000, step_increment=1)
        spin = Gtk.SpinButton(adjustment=adj)

        # Case 1: is_dragging is True -> on_pos_x_changed must return early
        main.is_dragging = True
        main.on_pos_x_changed(spin)
        main.on_pos_y_changed(spin)
        main.fixed.move.assert_not_called()
        self.assertEqual(btn.x, 50, "Button coordinate must not change when dragging")

        # Case 2: updating_form is True -> on_pos_x_changed must return early
        main.is_dragging = False
        main.updating_form = True
        main.on_pos_x_changed(spin)
        main.on_pos_y_changed(spin)
        main.fixed.move.assert_not_called()

        # Case 3: neither is True -> manual user spinbutton edit applies
        main.updating_form = False
        main.on_pos_x_changed(spin)
        main.fixed.move.assert_called_once()
        self.assertEqual(btn.x, 200)

    def test_canvas_size_updater(self):
        # 3 displays setup similar to user's 3-monitor configuration
        btn1 = DummyWidget("HDMI-A-1", x=0, y=0, width=1200, height=1920)
        btn2 = DummyWidget("DP-3", x=1200, y=384, width=2048, height=1152)
        btn3 = DummyWidget("DP-2", x=3248, y=0, width=1080, height=1920)

        main.display_buttons = [btn1, btn2, btn3]
        main.update_canvas_size()

        req_w, req_h = main.fixed.get_size_request()
        # Total logical width is 3248 + 1080 = 4328. At 0.15 scale: 649.2px + 400px = ~1049px -> min 1200px
        self.assertGreaterEqual(req_w, 1200, "Canvas width must be at least 1200px")
        # Max height is 1920. At 0.15 scale: 288px + 250px = 538px -> > 500px
        self.assertGreaterEqual(req_h, 500, "Canvas height must be at least 500px")


if __name__ == "__main__":
    unittest.main()
