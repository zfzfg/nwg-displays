# !/usr/bin/env python3
"""
Hyprland configuration detection, Lua parsing/merging, live keyword apply, and atomic operations.
Maintains full compatibility with modular Lua configurations (e.g. CachyOS) and preserves
unmanaged properties such as sdr_max_luminance = 400.
"""

import datetime
import json
import os
import re
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def strip_lua_comment(line: str) -> Tuple[str, str]:
    """
    Strips inline Lua comment (-- comment) from line, respecting quotes.
    Returns (code_part, comment_part).
    """
    in_single = False
    in_double = False
    for i in range(len(line)):
        ch = line[i]
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '-' and not in_single and not in_double:
            if i + 1 < len(line) and line[i + 1] == '-':
                return line[:i].strip(), line[i:].strip()
    return line.strip(), ""


def detect_hyprland_monitors_path(hypr_config_dir: str) -> Dict[str, Any]:
    """
    Analyzes hyprland configuration entry points to determine the active monitors config path.
    Checks hyprland.lua first (>= 0.55 / 0.56), scanning for require(...) calls line-by-line.
    Falls back to hyprland.conf with source = ... directives.
    """
    hypr_config_dir = os.path.expanduser(hypr_config_dir)
    lua_entry = os.path.join(hypr_config_dir, "hyprland.lua")
    conf_entry = os.path.join(hypr_config_dir, "hyprland.conf")

    # 1. Lua configuration (Hyprland 0.55+)
    if os.path.isfile(lua_entry):
        try:
            with open(lua_entry, "r", encoding="utf-8") as f:
                lines = f.readlines()

            monitor_requires = []
            for line in lines:
                code_part, _ = strip_lua_comment(line)
                if not code_part:
                    continue
                m = re.search(r"""^\s*require\s*\(?\s*["']([^"']+)["']\s*\)?""", code_part)
                if m and "monitor" in m.group(1).lower():
                    monitor_requires.append(m.group(1))

            if len(monitor_requires) == 1:
                mod_name = monitor_requires[0]
                rel_parts = mod_name.split(".")
                rel_path = os.path.join(*rel_parts) + ".lua"
                resolved = os.path.join(hypr_config_dir, rel_path)
                return {
                    "config_type": "lua",
                    "path": resolved,
                    "module": mod_name,
                    "include_found": True,
                    "ambiguous": False,
                    "entry_file": lua_entry,
                }
            elif len(monitor_requires) > 1:
                # Multiple monitor requires found
                # Prefer exact "config.monitors" or "monitors"
                preferred = [r for r in monitor_requires if r in ("config.monitors", "monitors")]
                chosen = preferred[0] if preferred else monitor_requires[0]
                rel_path = os.path.join(*chosen.split(".")) + ".lua"
                return {
                    "config_type": "lua",
                    "path": os.path.join(hypr_config_dir, rel_path),
                    "module": chosen,
                    "include_found": True,
                    "ambiguous": True,
                    "entry_file": lua_entry,
                }
            else:
                # hyprland.lua exists but no monitor require found
                # Default path is monitors.lua, but include_found = False
                return {
                    "config_type": "lua",
                    "path": os.path.join(hypr_config_dir, "monitors.lua"),
                    "module": "monitors",
                    "include_found": False,
                    "ambiguous": False,
                    "entry_file": lua_entry,
                }
        except Exception as e:
            eprint(f"[HyprlandHelper] Error reading {lua_entry}: {e}")

    # 2. Conf configuration (Legacy Hyprland)
    if os.path.isfile(conf_entry):
        try:
            with open(conf_entry, "r", encoding="utf-8") as f:
                lines = f.readlines()

            monitor_sources = []
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                m = re.search(r"""^\s*source\s*=\s*(.+)$""", stripped)
                if m and "monitor" in m.group(1).lower():
                    monitor_sources.append(m.group(1).strip())

            if monitor_sources:
                chosen = monitor_sources[0]
                chosen = os.path.expanduser(chosen)
                if not os.path.isabs(chosen):
                    chosen = os.path.join(hypr_config_dir, chosen)
                return {
                    "config_type": "conf",
                    "path": chosen,
                    "module": None,
                    "include_found": True,
                    "ambiguous": len(monitor_sources) > 1,
                    "entry_file": conf_entry,
                }
            else:
                return {
                    "config_type": "conf",
                    "path": os.path.join(hypr_config_dir, "monitors.conf"),
                    "module": None,
                    "include_found": False,
                    "ambiguous": False,
                    "entry_file": conf_entry,
                }
        except Exception as e:
            eprint(f"[HyprlandHelper] Error reading {conf_entry}: {e}")

    # Default fallback
    return {
        "config_type": "conf",
        "path": os.path.join(hypr_config_dir, "monitors.conf"),
        "module": None,
        "include_found": False,
        "ambiguous": False,
        "entry_file": None,
    }


def detect_hyprland_workspaces_path(hypr_config_dir: str) -> Dict[str, Any]:
    """
    Detects the active workspaces configuration path (workspaces.lua vs config/workspaces.lua vs workspaces.conf).
    """
    hypr_config_dir = os.path.expanduser(hypr_config_dir)
    lua_entry = os.path.join(hypr_config_dir, "hyprland.lua")

    if os.path.isfile(lua_entry):
        try:
            with open(lua_entry, "r", encoding="utf-8") as f:
                lines = f.readlines()

            ws_requires = []
            for line in lines:
                code_part, _ = strip_lua_comment(line)
                if not code_part:
                    continue
                m = re.search(r"""^\s*require\s*\(?\s*["']([^"']+)["']\s*\)?""", code_part)
                if m and "workspace" in m.group(1).lower():
                    ws_requires.append(m.group(1))

            if ws_requires:
                chosen = ws_requires[0]
                rel_path = os.path.join(*chosen.split(".")) + ".lua"
                return {
                    "config_type": "lua",
                    "path": os.path.join(hypr_config_dir, rel_path),
                    "module": chosen,
                    "include_found": True,
                }
            else:
                return {
                    "config_type": "lua",
                    "path": os.path.join(hypr_config_dir, "workspaces.lua"),
                    "module": "workspaces",
                    "include_found": False,
                }
        except Exception as e:
            eprint(f"[HyprlandHelper] Error reading {lua_entry}: {e}")

    return {
        "config_type": "conf",
        "path": os.path.join(hypr_config_dir, "workspaces.conf"),
        "module": None,
        "include_found": False,
    }


def parse_existing_lua_monitors(file_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Lexically parses hl.monitor({ ... }) blocks from a lua file.
    Does not execute lua code.
    Returns a dict mapping output name to a dictionary of properties and leading comments:
    {
        "output_name": {
            "comment": "-- comment before block",
            "props": {"key": "raw_value_string", ...}
        }
    }
    """
    if not os.path.isfile(file_path):
        return {}

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        eprint(f"[HyprlandHelper] Cannot read {file_path}: {e}")
        return {}

    return parse_lua_monitors_string(content)


def parse_lua_monitors_string(content: str) -> Dict[str, Dict[str, Any]]:
    """
    Parses hl.monitor({ ... }) blocks from a string.
    Returns:
    {
        "output_name": {
            "comment": "-- comment before block",
            "props": {"key": "raw_value_string", ...},
            "inline_comments": {"key": "-- inline comment", ...}
        }
    }
    """
    monitors: Dict[str, Dict[str, Any]] = {}

    # Match hl.monitor({ ... }) blocks including optional preceding comment lines
    block_pattern = re.compile(
        r"((?:[ \t]*--[^\n]*\n)*)[ \t]*hl\.monitor\s*\(\s*\{([^}]*)\}\s*\)",
        re.MULTILINE | re.DOTALL,
    )

    for match in block_pattern.finditer(content):
        comment_block = match.group(1).strip()
        body = match.group(2)

        props: Dict[str, str] = {}
        inline_comments: Dict[str, str] = {}

        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue

            code_part, comment_part = strip_lua_comment(line)
            if not code_part:
                continue

            # Strip trailing comma
            if code_part.endswith(","):
                code_part = code_part[:-1].strip()

            # Separate key and value
            if "=" in code_part:
                key, val = code_part.split("=", 1)
                key = key.strip()
                val = val.strip()
                props[key] = val
                if comment_part:
                    inline_comments[key] = comment_part

        output_raw = props.get("output")
        if output_raw:
            clean_output = output_raw.strip('"\'')
            monitors[clean_output] = {
                "comment": comment_block,
                "props": props,
                "inline_comments": inline_comments,
            }

    return monitors


def merge_and_generate_lua(
    displays: List[Dict[str, Any]],
    existing_by_output: Dict[str, Dict[str, Any]],
    header: str,
    use_desc: bool = False,
) -> List[str]:
    """
    Merges updated GUI display properties into existing Lua monitor blocks,
    preserving unmanaged fields (sdr_max_luminance, bitdepth, custom properties, comments).
    Returns lines of the complete Lua file.
    """
    transforms = {
        "normal": 0,
        "90": 1,
        "180": 2,
        "270": 3,
        "flipped": 4,
        "flipped-90": 5,
        "flipped-180": 6,
        "flipped-270": 7,
    }

    lines = [header.strip().replace("#", "--"), ""]
    handled_names = set()

    for d in displays:
        name = d["name"] if not use_desc else f"desc:{d['description']}"
        handled_names.add(name)
        handled_names.add(d["name"])

        existing = existing_by_output.get(name) or existing_by_output.get(d["name"]) or {}
        existing_props = dict(existing.get("props", {}))
        inline_c = existing.get("inline_comments", {})
        comment = existing.get("comment", "")

        if comment:
            lines.append(comment)

        props_to_write: List[Tuple[str, str]] = []

        # 1. Output
        props_to_write.append(("output", f'"{name}"'))

        if not d.get("active", True):
            props_to_write.append(("disabled", "true"))
        else:
            mode = f"{d['physical_width']}x{d['physical_height']}@{d['refresh']}"
            pos = f"{d['x']}x{d['y']}"
            props_to_write.append(("mode", f'"{mode}"'))
            props_to_write.append(("position", f'"{pos}"'))
            props_to_write.append(("scale", str(d["scale"])))

            if d.get("mirror"):
                props_to_write.append(("mirror", f'"{d["mirror"]}"'))

            # Rotation / Transform
            transform_val = d.get("transform", "normal")
            if transform_val != "normal":
                t_code = transforms.get(transform_val, 0)
                props_to_write.append(("transform", str(t_code)))
            elif "transform" in existing_props and existing_props["transform"] == "0":
                props_to_write.append(("transform", "0"))

            # VRR
            vrr = "1" if d.get("adaptive_sync") else "0"
            props_to_write.append(("vrr", vrr))

            # Color Mode & HDR
            color_mode = d.get("color_mode") or ""
            hdr = color_mode in ("hdr", "hdredid")

            # Bitdepth (10-bit)
            if d.get("ten_bit") or hdr or existing_props.get("bitdepth") == "10":
                props_to_write.append(("bitdepth", "10"))
            elif "bitdepth" in existing_props:
                props_to_write.append(("bitdepth", existing_props["bitdepth"]))

            # sdr_max_luminance preservation (crucial for HDR displays like MSI MAG 274)
            sdr_max_lum = d.get("sdr_max_luminance")
            if "sdr_max_luminance" in existing_props:
                existing_lum = existing_props["sdr_max_luminance"].strip()
                # If existing config has an explicit setting (> 80 nits or display is in HDR mode)
                if existing_lum != "80" or hdr:
                    props_to_write.append(("sdr_max_luminance", existing_lum))
                elif sdr_max_lum and sdr_max_lum > 80:
                    props_to_write.append(("sdr_max_luminance", str(int(sdr_max_lum) if float(sdr_max_lum).is_integer() else sdr_max_lum)))
            elif sdr_max_lum and sdr_max_lum > 80:
                props_to_write.append(("sdr_max_luminance", str(int(sdr_max_lum) if float(sdr_max_lum).is_integer() else sdr_max_lum)))

            if color_mode:
                props_to_write.append(("cm", f'"{color_mode}"'))
                if hdr:
                    sdr_b = d.get("sdr_brightness", 1.0)
                    sdr_s = d.get("sdr_saturation", 1.0)
                    if sdr_b != 1.0:
                        props_to_write.append(("sdrbrightness", str(sdr_b)))
                    if sdr_s != 1.0:
                        props_to_write.append(("sdrsaturation", str(sdr_s)))
            elif "cm" in existing_props:
                props_to_write.append(("cm", existing_props["cm"]))

        # Preserve any unhandled keys from existing_props
        handled_keys = {
            "output", "mode", "position", "scale", "transform",
            "vrr", "disabled", "mirror", "bitdepth", "sdr_max_luminance",
            "cm", "sdrbrightness", "sdrsaturation"
        }
        for k, v in existing_props.items():
            if k not in handled_keys:
                props_to_write.append((k, v))

        # Format hl.monitor block
        block_lines = ["hl.monitor({"]
        for k, v in props_to_write:
            comment_suffix = f" {inline_c[k]}" if k in inline_c else ""
            block_lines.append(f"    {k} = {v},{comment_suffix}")
        block_lines.append("})\n")

        lines.extend(block_lines)

    # Preserve any existing monitor declarations from file that were not in GUI displays list
    for name, mon_info in existing_by_output.items():
        if name not in handled_names:
            comment = mon_info.get("comment", "")
            if comment:
                lines.append(comment)
            raw_props = mon_info.get("props", {})
            inline_c = mon_info.get("inline_comments", {})
            block_lines = ["hl.monitor({"]
            for k, v in raw_props.items():
                comment_suffix = f" {inline_c[k]}" if k in inline_c else ""
                block_lines.append(f"    {k} = {v},{comment_suffix}")
            block_lines.append("})\n")
            lines.extend(block_lines)

    return lines


def generate_hyprctl_keyword_commands(
    displays: List[Dict[str, Any]],
    existing_by_output: Optional[Dict[str, Dict[str, Any]]] = None,
    use_desc: bool = False,
) -> List[str]:
    """
    Builds 'hyprctl keyword monitor ...' commands for live application.
    Hyprland monitor syntax:
    monitor=name,res@hz,pos,scale,options...
    """
    transforms = {
        "normal": 0,
        "90": 1,
        "180": 2,
        "270": 3,
        "flipped": 4,
        "flipped-90": 5,
        "flipped-180": 6,
        "flipped-270": 7,
    }

    cmds: List[str] = []

    for d in displays:
        name = d["name"] if not use_desc else f"desc:{d['description']}"
        existing = (existing_by_output or {}).get(name) or (existing_by_output or {}).get(d["name"]) or {}
        existing_props = existing.get("props", {})

        if not d.get("active", True):
            cmds.append(f"keyword monitor {name},disable")
            continue

        cmd_parts = [
            name,
            f"{d['physical_width']}x{d['physical_height']}@{d['refresh']}",
            f"{d['x']}x{d['y']}",
            str(d["scale"]),
        ]

        if d.get("mirror"):
            cmd_parts.extend(["mirror", d["mirror"]])

        transform_val = d.get("transform", "normal")
        if transform_val != "normal":
            t_code = transforms.get(transform_val, 0)
            cmd_parts.extend(["transform", str(t_code)])

        color_mode = d.get("color_mode") or ""
        hdr = color_mode in ("hdr", "hdredid")

        if d.get("ten_bit") or hdr or existing_props.get("bitdepth") == "10":
            cmd_parts.extend(["bitdepth", "10"])

        if color_mode:
            cmd_parts.extend(["cm", color_mode])
            if hdr:
                sdr_b = d.get("sdr_brightness", 1.0)
                sdr_s = d.get("sdr_saturation", 1.0)
                if sdr_b != 1.0:
                    cmd_parts.extend(["sdrbrightness", str(sdr_b)])
                if sdr_s != 1.0:
                    cmd_parts.extend(["sdrsaturation", str(sdr_s)])
        elif "cm" in existing_props:
            clean_cm = existing_props["cm"].strip('"\'')
            cmd_parts.extend(["cm", clean_cm])

        vrr = "1" if d.get("adaptive_sync") else "0"
        cmd_parts.extend(["vrr", vrr])

        cmds.append(f"keyword monitor {','.join(cmd_parts)}")

    return cmds


def atomic_write_file(lines: List[str], target_path: str) -> None:
    """
    Safely writes content to target_path using an atomic temp-file replace.
    Maintains file permissions and creates a backup of existing target.
    """
    target_path = os.path.expanduser(target_path)
    target_dir = os.path.dirname(target_path)
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir, exist_ok=True)

    # Preserve file permissions if file already exists
    mode = 0o644
    if os.path.exists(target_path):
        mode = os.stat(target_path).st_mode

    # Write to temp file in the same directory (guarantees same filesystem for atomic rename)
    tmp_path = f"{target_path}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

        os.chmod(tmp_path, mode)

        # Create backup if original exists
        if os.path.exists(target_path):
            backup_path = f"{target_path}.bak"
            shutil.copy2(target_path, backup_path)

        # Atomic replace
        os.replace(tmp_path, target_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def verify_live_monitors(
    expected_displays: List[Dict[str, Any]],
    hyprctl_func,
    tolerance: int = 1,
) -> Tuple[bool, str]:
    """
    Verifies that the live Hyprland compositor state matches expected displays.
    hyprctl_func is a callable that executes hyprctl commands (e.g. hyprctl("j/monitors")).
    """
    try:
        output = hyprctl_func("j/monitors")
        monitors_data = json.loads(output)
    except Exception as e:
        return False, f"Failed to query compositor: {e}"

    monitors_by_name = {m["name"]: m for m in monitors_data}

    for d in expected_displays:
        name = d["name"]
        if not d.get("active", True):
            if name in monitors_by_name:
                return False, f"Monitor '{name}' was expected to be disabled, but is still active"
            continue

        if name not in monitors_by_name:
            return False, f"Expected monitor '{name}' not found in active compositor outputs"

        live = monitors_by_name[name]

        # Verify geometry
        if abs(live.get("x", 0) - d["x"]) > tolerance or abs(live.get("y", 0) - d["y"]) > tolerance:
            return False, f"Position mismatch on {name}: expected {d['x']}x{d['y']}, got {live.get('x')}x{live.get('y')}"

        if abs(live.get("scale", 1.0) - d["scale"]) > 0.01:
            return False, f"Scale mismatch on {name}: expected {d['scale']}, got {live.get('scale')}"

    return True, "All monitors verified successfully"
