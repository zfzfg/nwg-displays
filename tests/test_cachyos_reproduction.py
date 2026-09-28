import json
import os
import tempfile
import unittest
from unittest.mock import patch

from nwg_displays.hyprland_helper import detect_hyprland_monitors_path
from nwg_displays.settings_applier import SettingsApplier


REFERENCE_HYPRLAND_LUA = """-- CachyOS Hyprland Configuration

require("config.animations")
require("config.autostart")
require("config.colors")
require("config.decorations")
require("config.variables")
require("config.environment")
require("config.inputs")
require("config.binds")
require("config.misc")
require("config.monitors")
require("config.windowrules")
require("config.workspaces")
"""

REFERENCE_CONFIG_MONITORS_LUA = """-- Eizo hochkant links
hl.monitor({
    output = "HDMI-A-1",
    mode = "1680x1050@59.95",
    position = "0x0",
    scale = 1,
    transform = 1,
})

-- MSI waagerecht in der Mitte, 125 %, HDR
hl.monitor({
    output = "DP-3",
    mode = "2560x1440@300",
    position = "1050x0",
    scale = 1.25,
    bitdepth = 10,
    sdr_max_luminance = 400,
    cm = "hdr",
})

-- ASUS hochkant rechts
hl.monitor({
    output = "DP-2",
    mode = "1920x1080@60",
    position = "3098x0",
    scale = 1,
    transform = 1,
})
"""


class TestCachyOSReproduction(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.hypr_dir = os.path.join(self.tmpdir.name, "hypr")
        self.config_dir = os.path.join(self.hypr_dir, "config")
        os.makedirs(self.config_dir, exist_ok=True)

        self.hyprland_lua = os.path.join(self.hypr_dir, "hyprland.lua")
        with open(self.hyprland_lua, "w") as f:
            f.write(REFERENCE_HYPRLAND_LUA)

        self.monitors_lua = os.path.join(self.config_dir, "monitors.lua")
        with open(self.monitors_lua, "w") as f:
            f.write(REFERENCE_CONFIG_MONITORS_LUA)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_default_upstream_path_versus_detected_cachyos_config(self):
        """
        Shows that a naive replace of .conf with .lua (upstream default)
        would produce ~/.config/hypr/monitors.lua, whereas detect_hyprland_monitors_path
        correctly resolves to ~/.config/hypr/config/monitors.lua.
        """
        naive_default_lua = os.path.join(self.hypr_dir, "monitors.conf").removesuffix(".conf") + ".lua"
        self.assertNotEqual(naive_default_lua, self.monitors_lua)

        detected = detect_hyprland_monitors_path(self.hypr_dir)
        self.assertEqual(detected["config_type"], "lua")
        self.assertEqual(detected["path"], self.monitors_lua)
        self.assertTrue(detected["include_found"])

    @patch("nwg_displays.settings_applier.settings_applier.hyprctl")
    def test_fixed_modular_lua_writes_active_cachyos_config_and_preserves_hdr(self, mock_hyprctl):
        """
        Verifies that SettingsApplier now:
        1. Automatically detects and writes to active config/monitors.lua
        2. Preserves sdr_max_luminance = 400
        3. Preserves bitdepth = 10 and cm = 'hdr'
        """
        mock_hyprctl.return_value = json.dumps([
            {"name": "DP-3", "x": 1050, "y": 0, "scale": 1.25}
        ])

        profile_data = {
            "displays": [
                {
                    "name": "DP-3",
                    "description": "Microstep MAG 274 X30MV CF0H016100373",
                    "active": True,
                    "physical_width": 2560,
                    "physical_height": 1440,
                    "refresh": 300.0,
                    "x": 1050,
                    "y": 0,
                    "scale": 1.25,
                    "transform": "normal",
                    "dpms": True,
                    "color_mode": "hdr",
                    "ten_bit": True,
                }
            ],
            "config": {"use-desc": False},
        }

        target_conf = os.path.join(self.hypr_dir, "monitors.conf")

        with patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "test"}):
            SettingsApplier._apply_hyprland_json(
                profile_data["displays"],
                use_desc=False,
                outputs_path=target_conf,
                profile_data=profile_data,
            )

        # The active config/monitors.lua MUST have been updated
        self.assertTrue(os.path.exists(self.monitors_lua))
        with open(self.monitors_lua, "r") as f:
            content = f.read()

        self.assertIn('cm = "hdr"', content)
        self.assertIn("bitdepth = 10", content)
        # Crucially: sdr_max_luminance = 400 is PRESERVED!
        self.assertIn("sdr_max_luminance = 400", content)

    @patch("nwg_displays.settings_applier.settings_applier.hyprctl")
    def test_fixed_three_monitor_reference_setup(self, mock_hyprctl):
        """
        Tests the 3 reference monitors under the new generator to verify
        all rotations, positions, scale, and HDR properties are preserved.
        """
        mock_hyprctl.return_value = json.dumps([
            {"name": "HDMI-A-1", "x": 0, "y": 0, "scale": 1.0},
            {"name": "DP-3", "x": 1050, "y": 0, "scale": 1.25},
            {"name": "DP-2", "x": 3098, "y": 0, "scale": 1.0},
        ])

        displays = [
            {
                "name": "HDMI-A-1",
                "description": "Eizo Nanao Corporation EV2216W 79073039",
                "active": True,
                "physical_width": 1680,
                "physical_height": 1050,
                "refresh": 59.95,
                "x": 0,
                "y": 0,
                "scale": 1.0,
                "transform": "90",
                "dpms": True,
            },
            {
                "name": "DP-3",
                "description": "Microstep MAG 274 X30MV CF0H016100373",
                "active": True,
                "physical_width": 2560,
                "physical_height": 1440,
                "refresh": 300.0,
                "x": 1050,
                "y": 0,
                "scale": 1.25,
                "transform": "normal",
                "dpms": True,
                "color_mode": "hdr",
                "ten_bit": True,
            },
            {
                "name": "DP-2",
                "description": "Ancor Communications Inc ASUS PB238 DBLMTF248542",
                "active": True,
                "physical_width": 1920,
                "physical_height": 1080,
                "refresh": 60.0,
                "x": 3098,
                "y": 0,
                "scale": 1.0,
                "transform": "90",
                "dpms": True,
            },
        ]
        profile_data = {"displays": displays, "config": {"use-desc": False}}
        target_conf = os.path.join(self.hypr_dir, "monitors.conf")

        with patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": "test"}):
            SettingsApplier._apply_hyprland_json(
                displays,
                use_desc=False,
                outputs_path=target_conf,
                profile_data=profile_data,
            )

        with open(self.monitors_lua, "r") as f:
            lua_out = f.read()

        # Check rotations, positions, scale
        self.assertIn('output = "HDMI-A-1"', lua_out)
        self.assertIn("transform = 1", lua_out)
        self.assertIn('output = "DP-3"', lua_out)
        self.assertIn('position = "1050x0"', lua_out)
        self.assertIn("scale = 1.25", lua_out)
        self.assertIn("sdr_max_luminance = 400", lua_out)
        self.assertIn('cm = "hdr"', lua_out)
        self.assertIn("bitdepth = 10", lua_out)
        self.assertIn('output = "DP-2"', lua_out)
        self.assertIn('position = "3098x0"', lua_out)
        self.assertIn("transform = 1", lua_out)


if __name__ == "__main__":
    unittest.main()
