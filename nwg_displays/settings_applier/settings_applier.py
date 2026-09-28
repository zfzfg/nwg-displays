import os
import shutil
import datetime
import json
import time
from nwg_displays.tools import (
    eprint,
    hyprctl,
    niri_msg,
    niri_reload_config,
    save_list_to_text_file,
    save_kdl_output,
    ensure_niri_config_include,
    load_text_file,
    inactive_output_description,
    load_json,
    save_json,
    notify,
)
from nwg_displays.tools import get_config, get_config_home
from nwg_displays.hyprland_helper import (
    detect_hyprland_monitors_path,
    parse_existing_lua_monitors,
    merge_and_generate_lua,
    generate_hyprctl_keyword_commands,
    atomic_write_file,
    verify_live_monitors,
)

class SettingsApplier:
    @staticmethod
    def apply_from_json(profile_data, outputs_path, config_dir, profile_name):
        """Applies configuration based on a Profile JSON file."""
        SettingsApplier._save_current_state_to_previous_profile(config_dir)

        displays = profile_data["displays"]
        config = profile_data["config"]
        use_desc = config.get("use-desc", False)

        if os.getenv("NIRI_SOCKET"):
            SettingsApplier._apply_niri_json(
                displays, use_desc, outputs_path, profile_data
            )

        elif os.getenv("HYPRLAND_INSTANCE_SIGNATURE"):
            SettingsApplier._apply_hyprland_json(
                displays, use_desc, outputs_path, profile_data
            )

        elif os.getenv("SWAYSOCK"):
            SettingsApplier._apply_sway_json(displays, use_desc)

        SettingsApplier._set_active_profile(config_dir, profile_name)

    @staticmethod
    def _apply_hyprland_json(displays, use_desc, outputs_path, profile_data):
        hypr_config_dir = os.path.dirname(outputs_path) if outputs_path else os.path.join(get_config_home(), "hypr")
        info = detect_hyprland_monitors_path(hypr_config_dir)

        if outputs_path:
            if outputs_path.endswith(".lua"):
                outputs_path_lua = os.path.expanduser(outputs_path)
                outputs_path_conf = outputs_path_lua.removesuffix(".lua") + ".conf"
            elif outputs_path.endswith(".conf"):
                default_conf = os.path.join(hypr_config_dir, "monitors.conf")
                if info["config_type"] == "lua" and outputs_path == default_conf:
                    outputs_path_lua = info["path"]
                    outputs_path_conf = outputs_path_lua.removesuffix(".lua") + ".conf"
                else:
                    outputs_path_conf = os.path.expanduser(outputs_path)
                    outputs_path_lua = outputs_path_conf.removesuffix(".conf") + ".lua"
            else:
                outputs_path_lua = os.path.expanduser(outputs_path)
                outputs_path_conf = outputs_path_lua + ".conf"
        else:
            if info["config_type"] == "lua":
                outputs_path_lua = info["path"]
                outputs_path_conf = outputs_path_lua.removesuffix(".lua") + ".conf"
            else:
                outputs_path_conf = info["path"]
                outputs_path_lua = outputs_path_conf.removesuffix(".conf") + ".lua"

        print(f"[Profile] Applying {len(displays)} displays for Hyprland to {outputs_path_lua}...")

        existing_by_output = parse_existing_lua_monitors(outputs_path_lua)

        # 1. Live apply via keyword monitor
        cmds = generate_hyprctl_keyword_commands(displays, existing_by_output, use_desc=use_desc)
        for cmd in cmds:
            hyprctl(cmd)

        for d in displays:
            cmd = "on" if d.get("dpms", True) else "off"
            hyprctl(f"dispatch dpms {cmd} {d['name']}")

        # 2. Verify live compositor response
        ok, msg = verify_live_monitors(displays, hyprctl)
        if not ok:
            eprint(f"[Hyprland] Post-apply verification warning: {msg}")
            notify("Hyprland Display Warning", msg)

        # 3. Generate Lua lines preserving existing options like sdr_max_luminance
        header = SettingsApplier._get_header("Profile Loader")
        lines_lua = merge_and_generate_lua(displays, existing_by_output, header=header, use_desc=use_desc)

        # 4. Generate legacy conf lines
        lines_conf = [header]
        transforms = {"normal": 0, "90": 1, "180": 2, "270": 3, "flipped": 4, "flipped-90": 5, "flipped-180": 6, "flipped-270": 7}
        for d in displays:
            name = d["name"] if not use_desc else f"desc:{d['description']}"
            if not d.get("active", True):
                lines_conf.append(f"monitor={name},disable")
            else:
                conf_line = f"monitor={name},{d['physical_width']}x{d['physical_height']}@{d['refresh']},{d['x']}x{d['y']},{d['scale']}"
                if d.get("mirror"):
                    conf_line += f",mirror,{d['mirror']}"
                if d.get("ten_bit") or (d.get("color_mode") in ("hdr", "hdredid")):
                    conf_line += ",bitdepth,10"
                if d.get("color_mode"):
                    conf_line += f",cm,{d['color_mode']}"
                vrr = "1" if d.get("adaptive_sync") else "0"
                conf_line += f",vrr,{vrr}"
                lines_conf.append(conf_line)
                if d.get("transform", "normal") != "normal":
                    t_code = transforms.get(d["transform"], 0)
                    lines_conf.append(f"monitor={name},transform,{t_code}")

        # 5. Atomic writes
        atomic_write_file(lines_lua, outputs_path_lua)
        if info["config_type"] == "conf" or (outputs_path and outputs_path.endswith(".conf")):
            atomic_write_file(lines_conf, outputs_path_conf)

        hyprctl("reload")

        config, config_file = get_config()
        if "wallpapers" in profile_data and config.get("profile-bound-wallpapers", True):
            print("[Profile] Applying wallpapers...")
            time.sleep(1)
            WallpaperManager.apply_wallpapers(profile_data["wallpapers"])

    @staticmethod
    def _apply_niri_json(displays, use_desc, outputs_path, profile_data):
        """Apply niri configuration by writing monitor.kdl file"""
        print(f"[Profile] Applying {len(displays)} displays for niri...")
        
        kdl_data = []
        for d in displays:
            name = d["name"]
            
            display_config = {
                "name": name,
                "active": d["active"],
                "physical_width": d["physical_width"],
                "physical_height": d["physical_height"],
                "refresh": d["refresh"],
                "x": d["x"],
                "y": d["y"],
                "scale": d["scale"],
                "transform": d["transform"],
                "adaptive_sync": d.get("adaptive_sync", False)
            }
            kdl_data.append(display_config)
        
        # Save to monitor.kdl
        save_kdl_output(kdl_data, outputs_path)
        
        # Ensure config.kdl includes monitor.kdl
        niri_config_dir = os.path.dirname(outputs_path)
        ensure_niri_config_include(niri_config_dir, outputs_path)
        
        # Reload niri configuration
        niri_reload_config()
        
        config, config_file = get_config()
        
        if "wallpapers" in profile_data and config.get(
            "profile-bound-wallpapers", True
        ):
            print("[Profile] Applying wallpapers...")
            import time
            time.sleep(1)
            WallpaperManager.apply_wallpapers(profile_data["wallpapers"])

    @staticmethod
    def _apply_sway_json(displays, use_desc):
        from i3ipc import Connection

        cmds = []
        for d in displays:
            name = d["description"] if use_desc else d["name"]
            if not d["active"]:
                cmds.append(f'output "{name}" disable')
                continue

            cmd = 'output "{}"'.format(name)
            custom = "--custom" if d["custom_mode"] else ""
            cmd += " mode {} {}x{}@{}Hz".format(
                custom, d["physical_width"], d["physical_height"], d["refresh"]
            )
            cmd += " pos {} {}".format(d["x"], d["y"])
            cmd += " transform {}".format(d["transform"])
            cmd += " scale {}".format(d["scale"])

            if d.get("scale_filter"):
                cmd += " scale_filter {}".format(d["scale_filter"])

            a_s = "on" if d["adaptive_sync"] else "off"
            cmd += " adaptive_sync {}".format(a_s)

            dpms = "on" if d["dpms"] else "off"
            cmd += " dpms {}".format(dpms)
            cmds.append(cmd)

        i3 = Connection()
        for cmd in cmds:
            i3.command(cmd)

    @staticmethod
    def apply_from_gui(
        display_buttons,
        outputs_activity,
        outputs_path,
        use_desc=False,
        create_confirm_win_callback=None,
        config_dir=None,
        profile_name=None,
    ):
        """
        Applies configuration based on GUI buttons state.
        Refactored from original 'apply_settings'.
        """
        if config_dir:
            SettingsApplier._save_current_state_to_previous_profile(config_dir)

        if os.getenv("NIRI_SOCKET"):
            print(f"[DEBUG] Applying niri config to {outputs_path}")
            SettingsApplier._apply_niri_gui(
                display_buttons,
                outputs_activity,
                outputs_path,
                use_desc,
                create_confirm_win_callback,
                config_dir,
                profile_name,
            )

        elif os.getenv("SWAYSOCK"):
            SettingsApplier._apply_sway_gui(
                display_buttons,
                outputs_activity,
                outputs_path,
                use_desc,
                create_confirm_win_callback,
                config_dir,
                profile_name,
            )

        elif os.getenv("HYPRLAND_INSTANCE_SIGNATURE"):
            SettingsApplier._apply_hyprland_gui(
                display_buttons,
                outputs_activity,
                outputs_path,
                use_desc,
                create_confirm_win_callback,
                config_dir,
                profile_name,
            )
        else:
            print("[Error] No compositor detected (Sway/Hyprland/Niri)")

        if config_dir and profile_name:
            SettingsApplier._set_active_profile(config_dir, profile_name)

    @staticmethod
    def _apply_sway_gui(
        display_buttons,
        outputs_activity,
        outputs_path,
        use_desc,
        create_confirm_win_callback,
        config_dir=None,
        profile_name=None,
    ):
        from i3ipc import Connection

        lines = [SettingsApplier._get_header()]
        cmds = []
        db_names = []

        for db in display_buttons:
            name = db.name if not use_desc else db.description
            db_names.append(name)

            lines.append('output "%s" {' % name)
            cmd = 'output "{}"'.format(name)

            custom_mode_str = "--custom" if db.custom_mode else ""
            lines.append(
                "    mode {} {}x{}@{}Hz".format(
                    custom_mode_str,
                    db.physical_width,
                    db.physical_height,
                    db.refresh,
                )
            )
            cmd += " mode {} {}x{}@{}Hz".format(
                custom_mode_str, db.physical_width, db.physical_height, db.refresh
            )

            lines.append("    pos {} {}".format(db.x, db.y))
            cmd += " pos {} {}".format(db.x, db.y)

            lines.append("    transform {}".format(db.transform))
            cmd += " transform {}".format(db.transform)

            lines.append("    scale {}".format(db.scale))
            cmd += " scale {}".format(db.scale)

            lines.append("    scale_filter {}".format(db.scale_filter))
            cmd += " scale_filter {}".format(db.scale_filter)

            a_s = "on" if db.adaptive_sync else "off"
            lines.append("    adaptive_sync {}".format(a_s))
            cmd += " adaptive_sync {}".format(a_s)

            dpms = "on" if db.dpms else "off"
            lines.append("    dpms {}".format(dpms))
            cmd += " dpms {}".format(dpms)

            lines.append("}")
            cmds.append(cmd)

        if not use_desc:
            for key in outputs_activity:
                if key not in db_names:
                    lines.append('output "{}" disable'.format(key))
                    cmds.append('output "{}" disable'.format(key))
        else:
            for key in outputs_activity:
                desc = inactive_output_description(key)
                if desc not in db_names:
                    lines.append('output "{}" disable'.format(desc))
                    cmds.append('output "{}" disable'.format(desc))

        if os.path.isfile(outputs_path):
            backup = load_text_file(outputs_path).splitlines()
        else:
            backup = []

        save_list_to_text_file(lines, outputs_path)

        i3 = Connection()
        for cmd in cmds:
            i3.command(cmd)

        if create_confirm_win_callback:
            create_confirm_win_callback(backup, outputs_path, config_dir, profile_name)

    @staticmethod
    def _apply_niri_gui(
        display_buttons,
        outputs_activity,
        outputs_path,
        use_desc,
        create_confirm_win_callback,
        config_dir=None,
        profile_name=None,
    ):
        """Apply niri configuration from GUI by writing monitor.kdl file"""
        print(f"[niri] Applying {len(display_buttons)} displays...")
        
        # Save backup BEFORE applying new settings
        backup_path = outputs_path + ".bak"
        if os.path.isfile(outputs_path):
            shutil.copy2(outputs_path, backup_path)
            print(f"[niri] Backup saved to {backup_path}")
        else:
            backup_path = None
        
        kdl_data = []
        for db in display_buttons:
            display_config = {
                "name": db.name,
                "active": db.name not in outputs_activity or outputs_activity.get(db.name, True),
                "physical_width": db.physical_width,
                "physical_height": db.physical_height,
                "refresh": db.refresh,
                "x": db.x,
                "y": db.y,
                "scale": db.scale,
                "transform": db.transform,
                "adaptive_sync": db.adaptive_sync
            }
            kdl_data.append(display_config)
        
        # Save to monitor.kdl in KDL format
        save_kdl_output(kdl_data, outputs_path)
        
        # Ensure config.kdl includes monitor.kdl
        niri_config_dir = os.path.dirname(outputs_path)
        ensure_niri_config_include(niri_config_dir, outputs_path)
        
        # Reload niri configuration
        niri_reload_config()
        
        # Pass backup file path and current file path to confirm window
        if create_confirm_win_callback:
            create_confirm_win_callback(backup_path, outputs_path, config_dir, profile_name)

    @staticmethod
    def _apply_hyprland_gui(
        display_buttons,
        outputs_activity,
        outputs_path,
        use_desc,
        create_confirm_win_callback,
        config_dir=None,
        profile_name=None,
    ):
        hypr_config_dir = os.path.dirname(outputs_path) if outputs_path else os.path.join(get_config_home(), "hypr")
        info = detect_hyprland_monitors_path(hypr_config_dir)

        if outputs_path:
            if outputs_path.endswith(".lua"):
                outputs_path_lua = os.path.expanduser(outputs_path)
                outputs_path_conf = outputs_path_lua.removesuffix(".lua") + ".conf"
            elif outputs_path.endswith(".conf"):
                default_conf = os.path.join(hypr_config_dir, "monitors.conf")
                if info["config_type"] == "lua" and outputs_path == default_conf:
                    outputs_path_lua = info["path"]
                    outputs_path_conf = outputs_path_lua.removesuffix(".lua") + ".conf"
                else:
                    outputs_path_conf = os.path.expanduser(outputs_path)
                    outputs_path_lua = outputs_path_conf.removesuffix(".conf") + ".lua"
            else:
                outputs_path_lua = os.path.expanduser(outputs_path)
                outputs_path_conf = outputs_path_lua + ".conf"
        else:
            if info["config_type"] == "lua":
                outputs_path_lua = info["path"]
                outputs_path_conf = outputs_path_lua.removesuffix(".lua") + ".conf"
            else:
                outputs_path_conf = info["path"]
                outputs_path_lua = outputs_path_conf.removesuffix(".conf") + ".lua"

        print(f"[GUI] Applying {len(display_buttons)} displays for Hyprland to {outputs_path_lua}...")

        displays = []
        for db in display_buttons:
            active = outputs_activity.get(db.name, db.active) if db.name in outputs_activity else db.active
            d = {
                "name": db.name,
                "description": db.description,
                "active": active,
                "physical_width": db.physical_width,
                "physical_height": db.physical_height,
                "refresh": db.refresh,
                "x": db.x,
                "y": db.y,
                "scale": db.scale,
                "transform": db.transform,
                "dpms": db.dpms,
                "adaptive_sync": db.adaptive_sync,
                "mirror": db.mirror,
                "ten_bit": db.ten_bit,
                "color_mode": getattr(db, "color_mode", "") or "",
                "sdr_brightness": getattr(db, "sdr_brightness", 1.0),
                "sdr_saturation": getattr(db, "sdr_saturation", 1.0),
                "sdr_max_luminance": getattr(db, "sdr_max_luminance", None),
            }
            displays.append(d)

        existing_by_output = parse_existing_lua_monitors(outputs_path_lua)

        # 1. Live apply via keyword monitor
        cmds = generate_hyprctl_keyword_commands(displays, existing_by_output, use_desc=use_desc)
        for cmd in cmds:
            hyprctl(cmd)

        for d in displays:
            cmd = "on" if d.get("dpms", True) else "off"
            hyprctl(f"dispatch dpms {cmd} {d['name']}")

        # 2. Verify live compositor response
        ok, msg = verify_live_monitors(displays, hyprctl)
        if not ok:
            eprint(f"[Hyprland] Post-apply verification warning: {msg}")
            notify("Hyprland Display Warning", msg)

        # 3. Backup prior to writing
        backup_conf = load_text_file(outputs_path_conf).splitlines() if os.path.isfile(outputs_path_conf) else []
        backup_lua = load_text_file(outputs_path_lua).splitlines() if os.path.isfile(outputs_path_lua) else []

        # 4. Generate Lua lines preserving existing options like sdr_max_luminance
        header = SettingsApplier._get_header()
        lines_lua = merge_and_generate_lua(displays, existing_by_output, header=header, use_desc=use_desc)

        # 5. Generate legacy conf lines
        lines_conf = [header]
        transforms = {"normal": 0, "90": 1, "180": 2, "270": 3, "flipped": 4, "flipped-90": 5, "flipped-180": 6, "flipped-270": 7}
        for d in displays:
            name = d["name"] if not use_desc else f"desc:{d['description']}"
            if not d.get("active", True):
                lines_conf.append(f"monitor={name},disable")
            else:
                conf_line = f"monitor={name},{d['physical_width']}x{d['physical_height']}@{d['refresh']},{d['x']}x{d['y']},{d['scale']}"
                if d.get("mirror"):
                    conf_line += f",mirror,{d['mirror']}"
                if d.get("ten_bit") or (d.get("color_mode") in ("hdr", "hdredid")):
                    conf_line += ",bitdepth,10"
                if d.get("color_mode"):
                    conf_line += f",cm,{d['color_mode']}"
                vrr = "1" if d.get("adaptive_sync") else "0"
                conf_line += f",vrr,{vrr}"
                lines_conf.append(conf_line)
                if d.get("transform", "normal") != "normal":
                    t_code = transforms.get(d["transform"], 0)
                    lines_conf.append(f"monitor={name},transform,{t_code}")

        # 6. Atomic writes
        atomic_write_file(lines_lua, outputs_path_lua)
        if info["config_type"] == "conf" or (outputs_path and outputs_path.endswith(".conf")):
            atomic_write_file(lines_conf, outputs_path_conf)

        hyprctl("reload")

        backup = (backup_conf, backup_lua, outputs_path_conf, outputs_path_lua)

        if create_confirm_win_callback:
            create_confirm_win_callback(backup, outputs_path_lua, config_dir, profile_name)

    @staticmethod
    def _get_header(source="nwg-displays"):
        now = datetime.datetime.now()
        return "# Generated by {} on {} at {}. Do not edit manually.\n".format(
            source,
            datetime.datetime.strftime(now, "%Y-%m-%d"),
            datetime.datetime.strftime(now, "%H:%M:%S"),
        )

    @staticmethod
    def _save_current_state_to_previous_profile(config_dir):
        """
        Reads the last active profile name, gets current wallpapers,
        and updates that profile's JSON file.
        """
        state_file = os.path.join(config_dir, "active_profile.json")

        if not os.path.isfile(state_file):
            return

        try:
            data = load_json(state_file)
            last_profile_name = data.get("active_profile") if data else None

            if not last_profile_name:
                return

            config, _ = get_config()
            if not config.get("profile-bound-wallpapers", True):
                return

            prev_profile_path = os.path.join(
                config_dir, "profiles", f"{last_profile_name}.json"
            )

            if not os.path.isfile(prev_profile_path):
                print(
                    f"[Warning] Previous profile '{last_profile_name}' file not found. Skipping save."
                )
                return

            current_walls = WallpaperManager.get_current_wallpapers()
            if not current_walls:
                return

            with open(prev_profile_path, "r") as f:
                data = json.load(f)

            if "wallpapers" not in data:
                data["wallpapers"] = {}

            data["wallpapers"].update(current_walls)

            with open(prev_profile_path, "w") as f:
                json.dump(data, f, indent=2)

            print(f"[Persistence] Saved current wallpapers to '{last_profile_name}'")

        except Exception as e:
            print(f"[Error] Failed to save previous state: {e}")

    @staticmethod
    def _set_active_profile(config_dir, profile_name):
        state_file = os.path.join(config_dir, "active_profile.json")
        try:
            save_json({"active_profile": profile_name}, state_file)
        except Exception as e:
            print(f"[Error] Failed to set active profile: {e}")
