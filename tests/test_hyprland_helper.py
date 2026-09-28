import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from nwg_displays.hyprland_helper import (
    atomic_write_file,
    detect_hyprland_monitors_path,
    detect_hyprland_workspaces_path,
    generate_hyprctl_keyword_commands,
    merge_and_generate_lua,
    parse_existing_lua_monitors,
    parse_lua_monitors_string,
    strip_lua_comment,
    verify_live_monitors,
)


CACHYOS_HYPRLAND_LUA = """-- CachyOS Hyprland Configuration
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

UPSTREAM_HYPRLAND_LUA = """-- Upstream standard Hyprland
require("monitors")
require("workspaces")
"""

LEGACY_HYPRLAND_CONF = """# Legacy Hyprland configuration
source = ~/.config/hypr/monitors.conf
source = ~/.config/hypr/workspaces.conf
"""

SAMPLE_MONITORS_LUA = """-- Eizo hochkant links
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


class TestHyprlandHelper(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.hypr_dir = self.tmpdir.name

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_detect_cachyos_modular_lua(self):
        with open(os.path.join(self.hypr_dir, "hyprland.lua"), "w") as f:
            f.write(CACHYOS_HYPRLAND_LUA)

        res = detect_hyprland_monitors_path(self.hypr_dir)
        self.assertEqual(res["config_type"], "lua")
        self.assertEqual(res["module"], "config.monitors")
        self.assertTrue(res["include_found"])
        expected_path = os.path.join(self.hypr_dir, "config", "monitors.lua")
        self.assertEqual(res["path"], expected_path)

        ws_res = detect_hyprland_workspaces_path(self.hypr_dir)
        self.assertEqual(ws_res["config_type"], "lua")
        self.assertEqual(ws_res["module"], "config.workspaces")
        self.assertTrue(ws_res["include_found"])
        expected_ws_path = os.path.join(self.hypr_dir, "config", "workspaces.lua")
        self.assertEqual(ws_res["path"], expected_ws_path)

    def test_detect_upstream_lua(self):
        with open(os.path.join(self.hypr_dir, "hyprland.lua"), "w") as f:
            f.write(UPSTREAM_HYPRLAND_LUA)

        res = detect_hyprland_monitors_path(self.hypr_dir)
        self.assertEqual(res["config_type"], "lua")
        self.assertEqual(res["module"], "monitors")
        self.assertTrue(res["include_found"])
        expected_path = os.path.join(self.hypr_dir, "monitors.lua")
        self.assertEqual(res["path"], expected_path)

    def test_detect_legacy_conf(self):
        with open(os.path.join(self.hypr_dir, "hyprland.conf"), "w") as f:
            f.write(LEGACY_HYPRLAND_CONF)

        res = detect_hyprland_monitors_path(self.hypr_dir)
        self.assertEqual(res["config_type"], "conf")
        self.assertTrue(res["include_found"])
        self.assertTrue(res["path"].endswith("monitors.conf"))

    def test_parse_existing_lua_monitors(self):
        monitors = parse_lua_monitors_string(SAMPLE_MONITORS_LUA)
        self.assertIn("HDMI-A-1", monitors)
        self.assertIn("DP-3", monitors)
        self.assertIn("DP-2", monitors)

        msi = monitors["DP-3"]
        self.assertIn("MSI waagerecht in der Mitte", msi["comment"])
        props = msi["props"]
        self.assertEqual(props["output"], '"DP-3"')
        self.assertEqual(props["mode"], '"2560x1440@300"')
        self.assertEqual(props["bitdepth"], "10")
        self.assertEqual(props["sdr_max_luminance"], "400")
        self.assertEqual(props["cm"], '"hdr"')

    def test_merge_and_generate_lua_preserves_hdr_properties(self):
        existing = parse_lua_monitors_string(SAMPLE_MONITORS_LUA)

        # GUI updates MSI resolution slightly or position
        displays = [
            {
                "name": "DP-3",
                "description": "Microstep MAG 274 X30MV",
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
                # Note: GUI didn't touch sdr_max_luminance
            }
        ]

        header = "-- Generated test"
        lines = merge_and_generate_lua(displays, existing, header=header)
        lua_output = "\n".join(lines)

        self.assertIn("sdr_max_luminance = 400", lua_output)
        self.assertIn("bitdepth = 10", lua_output)
        self.assertIn('cm = "hdr"', lua_output)
        self.assertIn('mode = "2560x1440@300.0"', lua_output)
        self.assertIn("scale = 1.25", lua_output)
        self.assertIn("MSI waagerecht in der Mitte", lua_output)

    def test_generate_hyprctl_keyword_commands(self):
        displays = [
            {
                "name": "HDMI-A-1",
                "description": "Eizo",
                "active": True,
                "physical_width": 1680,
                "physical_height": 1050,
                "refresh": 59.95,
                "x": 0,
                "y": 0,
                "scale": 1.0,
                "transform": "90",
            },
            {
                "name": "DP-3",
                "description": "MSI",
                "active": True,
                "physical_width": 2560,
                "physical_height": 1440,
                "refresh": 300.0,
                "x": 1050,
                "y": 0,
                "scale": 1.25,
                "transform": "normal",
                "color_mode": "hdr",
                "ten_bit": True,
            },
        ]

        cmds = generate_hyprctl_keyword_commands(displays)
        self.assertEqual(len(cmds), 2)
        self.assertIn("keyword monitor HDMI-A-1,1680x1050@59.95,0x0,1.0,transform,1,vrr,0", cmds[0])
        self.assertIn("keyword monitor DP-3,2560x1440@300.0,1050x0,1.25,bitdepth,10,cm,hdr,vrr,0", cmds[1])

    def test_atomic_write_file(self):
        target = os.path.join(self.hypr_dir, "test_target.lua")
        lines = ["line 1", "line 2"]
        atomic_write_file(lines, target)

        self.assertTrue(os.path.isfile(target))
        with open(target, "r") as f:
            content = f.read()
        self.assertEqual(content, "line 1\nline 2\n")

        # Update and verify backup is created
        lines_v2 = ["line 1 updated", "line 2 updated"]
        atomic_write_file(lines_v2, target)

        self.assertTrue(os.path.isfile(f"{target}.bak"))
        with open(f"{target}.bak", "r") as f:
            bak_content = f.read()
        self.assertEqual(bak_content, "line 1\nline 2\n")

    def test_verify_live_monitors(self):
        expected = [
            {"name": "DP-3", "x": 1050, "y": 0, "scale": 1.25, "active": True},
            {"name": "HDMI-A-1", "x": 0, "y": 0, "scale": 1.0, "active": True},
        ]

        def mock_hyprctl_ok(cmd):
            return json.dumps([
                {"name": "DP-3", "x": 1050, "y": 0, "scale": 1.25},
                {"name": "HDMI-A-1", "x": 0, "y": 0, "scale": 1.0},
            ])

        ok, msg = verify_live_monitors(expected, mock_hyprctl_ok)
        self.assertTrue(ok)

        def mock_hyprctl_mismatch(cmd):
            return json.dumps([
                {"name": "DP-3", "x": 0, "y": 0, "scale": 1.25},
                {"name": "HDMI-A-1", "x": 0, "y": 0, "scale": 1.0},
            ])

        ok, msg = verify_live_monitors(expected, mock_hyprctl_mismatch)
        self.assertFalse(ok)
        self.assertIn("Position mismatch", msg)

    def test_disabled_and_mirrored_monitor_generation(self):
        displays = [
            {
                "name": "HDMI-A-1",
                "description": "Eizo",
                "active": False,
                "physical_width": 1680,
                "physical_height": 1050,
                "refresh": 59.95,
                "x": 0,
                "y": 0,
                "scale": 1.0,
            },
            {
                "name": "DP-2",
                "description": "Asus",
                "active": True,
                "physical_width": 1920,
                "physical_height": 1080,
                "refresh": 60.0,
                "x": 0,
                "y": 0,
                "scale": 1.0,
                "mirror": "DP-3",
            },
        ]
        cmds = generate_hyprctl_keyword_commands(displays)
        self.assertIn("keyword monitor HDMI-A-1,disable", cmds)
        self.assertIn("mirror,DP-3", cmds[1])

        lines = merge_and_generate_lua(displays, {}, header="-- Test")
        lua_code = "\n".join(lines)
        self.assertIn("disabled = true", lua_code)
        self.assertIn('mirror = "DP-3"', lua_code)

    def test_unincluded_config_detection(self):
        # hyprland.lua with no monitor require
        with open(os.path.join(self.hypr_dir, "hyprland.lua"), "w") as f:
            f.write("-- empty config without monitors require\nrequire('config.binds')\n")

        res = detect_hyprland_monitors_path(self.hypr_dir)
        self.assertEqual(res["config_type"], "lua")
        self.assertFalse(res["include_found"])

    def test_apply_from_gui_hyprland_flow(self):
        from unittest.mock import MagicMock, patch
        from nwg_displays.settings_applier import SettingsApplier

        # Setup mock cachyos config
        with open(os.path.join(self.hypr_dir, "hyprland.lua"), "w") as f:
            f.write(CACHYOS_HYPRLAND_LUA)
        config_sub = os.path.join(self.hypr_dir, "config")
        os.makedirs(config_sub, exist_ok=True)
        active_mon = os.path.join(config_sub, "monitors.lua")
        with open(active_mon, "w") as f:
            f.write(SAMPLE_MONITORS_LUA)

        # Mock display button
        db1 = MagicMock()
        db1.name = "HDMI-A-1"
        db1.description = "Eizo"
        db1.active = True
        db1.physical_width = 1680
        db1.physical_height = 1050
        db1.refresh = 59.95
        db1.x = 0
        db1.y = 0
        db1.scale = 1.0
        db1.transform = "90"
        db1.dpms = True
        db1.adaptive_sync = False
        db1.mirror = ""
        db1.ten_bit = False
        db1.color_mode = ""
        db1.sdr_brightness = 1.0
        db1.sdr_saturation = 1.0
        db1.sdr_max_luminance = None

        db2 = MagicMock()
        db2.name = "DP-3"
        db2.description = "MSI"
        db2.active = True
        db2.physical_width = 2560
        db2.physical_height = 1440
        db2.refresh = 300.0
        db2.x = 1050
        db2.y = 0
        db2.scale = 1.25
        db2.transform = "normal"
        db2.dpms = True
        db2.adaptive_sync = False
        db2.mirror = ""
        db2.ten_bit = True
        db2.color_mode = "hdr"
        db2.sdr_brightness = 1.0
        db2.sdr_saturation = 1.0
        db2.sdr_max_luminance = 400

        callback_mock = MagicMock()

        with patch("nwg_displays.settings_applier.settings_applier.hyprctl") as mock_hyprctl:
            mock_hyprctl.return_value = json.dumps([
                {"name": "HDMI-A-1", "x": 0, "y": 0, "scale": 1.0},
                {"name": "DP-3", "x": 1050, "y": 0, "scale": 1.25},
            ])

            dummy_conf = os.path.join(self.hypr_dir, "monitors.conf")
            SettingsApplier._apply_hyprland_gui(
                [db1, db2],
                outputs_activity={"HDMI-A-1": True, "DP-3": True},
                outputs_path=dummy_conf,
                use_desc=False,
                create_confirm_win_callback=callback_mock,
            )

        # Confirm callback was called with 4-tuple backup and active path
        self.assertTrue(callback_mock.called)
        call_args = callback_mock.call_args[0]
        backup_tuple = call_args[0]
        self.assertIsInstance(backup_tuple, tuple)
        self.assertEqual(len(backup_tuple), 4)
        # Check active file written
        with open(active_mon, "r") as f:
            content = f.read()
        self.assertIn("sdr_max_luminance = 400", content)
        self.assertIn("transform = 1", content)

    def test_parse_inline_comments_and_hyprctl_commands(self):
        lua_input = """-- Monitor with inline comments
hl.monitor({
    output = "DP-3",
    mode = "2560x1440@300",
    sdr_max_luminance = 400, -- Peak HDR nits
    cm = "hdr", -- color mode
})
"""
        parsed = parse_lua_monitors_string(lua_input)
        self.assertIn("DP-3", parsed)
        props = parsed["DP-3"]["props"]
        self.assertEqual(props["sdr_max_luminance"], "400")
        self.assertEqual(props["cm"], '"hdr"')
        inline_c = parsed["DP-3"]["inline_comments"]
        self.assertEqual(inline_c["sdr_max_luminance"], "-- Peak HDR nits")
        self.assertEqual(inline_c["cm"], "-- color mode")

        displays = [
            {
                "name": "DP-3",
                "physical_width": 2560,
                "physical_height": 1440,
                "refresh": 300.0,
                "x": 1050,
                "y": 0,
                "scale": 1.25,
                "active": True,
                "transform": "normal",
                "adaptive_sync": False,
            }
        ]

        # Verify hyprctl keyword command is clean without inline comments inside command string
        cmds = generate_hyprctl_keyword_commands(displays, parsed)
        self.assertEqual(len(cmds), 1)
        self.assertNotIn("--", cmds[0])
        self.assertIn("cm,hdr", cmds[0])

        # Verify merge output generates valid Lua with comma before comment
        lines = merge_and_generate_lua(displays, parsed, header="-- Generated")
        out_lua = "\n".join(lines)
        self.assertIn("sdr_max_luminance = 400, -- Peak HDR nits", out_lua)
        self.assertIn('cm = "hdr", -- color mode', out_lua)

    def test_detect_ignores_commented_requires_and_supports_no_parens(self):
        config_content = """-- Custom CachyOS Hyprland Entry
-- require("monitors")
require "config.monitors"
-- require("config.workspaces")
require 'config.workspaces'
"""
        entry_file = os.path.join(self.hypr_dir, "hyprland.lua")
        with open(entry_file, "w") as f:
            f.write(config_content)

        mon_info = detect_hyprland_monitors_path(self.hypr_dir)
        self.assertEqual(mon_info["config_type"], "lua")
        self.assertEqual(mon_info["module"], "config.monitors")
        self.assertTrue(mon_info["include_found"])

        ws_info = detect_hyprland_workspaces_path(self.hypr_dir)
        self.assertEqual(ws_info["config_type"], "lua")
        self.assertEqual(ws_info["module"], "config.workspaces")
        self.assertTrue(ws_info["include_found"])

    def test_sdr_display_does_not_get_sdr_max_luminance_80(self):
        displays = [
            {
                "name": "HDMI-A-1",
                "physical_width": 1680,
                "physical_height": 1050,
                "refresh": 59.95,
                "x": 0,
                "y": 0,
                "scale": 1.0,
                "active": True,
                "transform": "normal",
                "adaptive_sync": False,
                "color_mode": "",
                "sdr_max_luminance": None,
            }
        ]
        # Even if existing config previously had sdr_max_luminance = 80 from buggy run
        existing = {
            "HDMI-A-1": {
                "props": {"output": '"HDMI-A-1"', "sdr_max_luminance": "80"},
                "inline_comments": {},
            }
        }
        lines = merge_and_generate_lua(displays, existing, header="-- Test")
        out_lua = "\n".join(lines)
        self.assertNotIn("sdr_max_luminance", out_lua)

    def test_preserve_unmanaged_unplugged_monitors_in_merge(self):
        existing = {
            "HDMI-A-1": {
                "comment": "-- Left monitor",
                "props": {"output": '"HDMI-A-1"', "mode": '"1680x1050@59.95"'},
                "inline_comments": {},
            },
            "eDP-1": {
                "comment": "-- Built-in laptop screen (currently unplugged/docked)",
                "props": {"output": '"eDP-1"', "mode": '"1920x1080@60"', "scale": "1"},
                "inline_comments": {},
            },
        }
        # GUI only manages HDMI-A-1 right now
        displays = [
            {
                "name": "HDMI-A-1",
                "physical_width": 1680,
                "physical_height": 1050,
                "refresh": 59.95,
                "x": 0,
                "y": 0,
                "scale": 1.0,
                "active": True,
                "transform": "normal",
            }
        ]
        lines = merge_and_generate_lua(displays, existing, header="-- Test")
        out_lua = "\n".join(lines)
        self.assertIn('output = "HDMI-A-1"', out_lua)
        # eDP-1 MUST be preserved!
        self.assertIn('output = "eDP-1"', out_lua)
        self.assertIn("Built-in laptop screen", out_lua)

    def test_on_workspaces_apply_writes_modular_lua(self):
        import nwg_displays.main as main_mod

        config_dir = os.path.join(self.hypr_dir, "config")
        os.makedirs(config_dir, exist_ok=True)
        ws_lua_file = os.path.join(config_dir, "workspaces.lua")

        main_mod.workspaces_path = ws_lua_file
        main_mod.config = {"use-desc": False}
        main_mod.workspaces = {"1": "HDMI-A-1", "2": "DP-3"}

        # Call on_workspaces_apply_btn_hypr
        dummy_btn = None
        dummy_win = None
        # Mock close_dialog and notify
        with patch.object(main_mod, "close_dialog"), patch.object(main_mod, "notify"):
            main_mod.on_workspaces_apply_btn_hypr(dummy_btn, dummy_win, old_workspaces={})

        self.assertTrue(os.path.isfile(ws_lua_file))
        with open(ws_lua_file, "r") as f:
            content = f.read()

        # Must be valid Lua workspace_rule syntax, NOT .conf syntax
        self.assertIn("hl.workspace_rule({", content)
        self.assertIn('workspace = "1"', content)
        self.assertIn('monitor = "HDMI-A-1"', content)
        self.assertNotIn("workspace=1,monitor", content)


if __name__ == "__main__":
    unittest.main()

