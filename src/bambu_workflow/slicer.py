"""Bambu Studio CLI adapter using installed, resolved machine presets."""
from __future__ import annotations

import hashlib
import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

from .geometry import analyze_mesh, build_volume_for, checked_archive, classify_3mf, normalized_stl, sha256
from .models import get_model, normalize_model

PLATES = {"textured_plate": "Textured PEI Plate", "hot_plate": "High Temp Plate",
          "supertack_plate": "Supertack Plate", "cool_plate": "Cool Plate",
          "engineering_plate": "Engineering Plate"}


def discover_slicer_executable(configured: str | Path | None = None) -> Path | None:
    """Find an installed Bambu Studio binary; a configured .app bundle is accepted."""
    def binary(path):
        candidate = Path(path).expanduser()
        if candidate.is_dir() and candidate.suffix.lower() == ".app":
            candidate = candidate / "Contents" / "MacOS" / "BambuStudio"
        return candidate.resolve() if candidate.is_file() else None
    if configured:
        return binary(configured)
    candidates = []
    for name in ("bambu-studio", "bambu-studio.exe", "BambuStudio"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    if sys.platform == "darwin":
        candidates.extend((Path("/Applications/BambuStudio.app"), Path.home()/"Applications/BambuStudio.app"))
    elif os.name == "nt":
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(key)
            if base:
                candidates.append(Path(base)/"Bambu Studio"/"bambu-studio.exe")
    else:
        candidates.extend((Path("/usr/bin/bambu-studio"), Path("/usr/local/bin/bambu-studio")))
    for candidate in candidates:
        found = binary(candidate)
        if found:
            return found
    return None


def discover_profile_root(executable: Path, configured: str | Path | None = None) -> Path | None:
    """Resolve the installed profiles for Windows, macOS bundles and common Linux layouts."""
    if configured:
        candidate = Path(configured).expanduser()
        return candidate.resolve() if all((candidate/kind).is_dir() for kind in ("machine","process","filament")) else None
    exe = Path(executable).resolve()
    candidates = [exe.parent/"resources/profiles/BBL", exe.parent/"Resources/profiles/BBL"]
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        candidates = [exe.parent.parent/"Resources/profiles/BBL", exe.parent.parent/"resources/profiles/BBL"] + candidates
    if os.name != "nt":
        candidates.extend((Path("/usr/share/BambuStudio/profiles/BBL"),
                           Path("/usr/share/bambu-studio/resources/profiles/BBL"),
                           Path("/app/share/BambuStudio/profiles/BBL")))
    for candidate in candidates:
        if (candidate/"machine").is_dir() and (candidate/"process").is_dir() and (candidate/"filament").is_dir():
            return candidate.resolve()
    return None


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def _number(v):
    return float(_first(v))


def _values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value]
    return [item.strip() for item in next(csv.reader([str(value)], delimiter=";", skipinitialspace=True))]


def _color(value) -> str:
    value = str(value).strip().lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}(?:[0-9A-F]{2})?", value):
        raise ValueError("Filament color must be a six- or eight-digit RGB/RGBA hex color")
    return value + "FF" if len(value) == 6 else value


def _flow(value) -> str:
    value = re.sub(r"[ _-]", "", str(value)).casefold()
    if value not in {"standard", "highflow"}:
        raise ValueError("Unsupported nozzle high-flow/variant; choose confirmed Standard or HighFlow")
    return "HighFlow" if value == "highflow" else "Standard"


def _preset_matches(actual, requested) -> bool:
    actual, requested = str(actual).strip().casefold(), str(requested).strip().casefold()
    return bool(requested and (actual == requested or (" @" not in requested and actual.startswith(requested + " @"))))


def _requested_materials(printer: dict) -> list[dict]:
    entries = printer.get("materials")
    if entries is None:
        return []
    if not isinstance(entries, list) or not entries or len(entries) > 64:
        raise ValueError("materials must contain 1-64 logical filament entries")
    result = []
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or type(entry.get("filament_id")) is not int or entry["filament_id"] != index:
            raise ValueError("materials filament_id values must be contiguous one-based logical palette IDs")
        item = dict(entry)
        item["type"] = str(item.get("type", "")).strip().upper()
        if not item["type"]:
            raise ValueError("Every requested filament needs its polymer type")
        if item.get("color"):
            item["color"] = _color(item["color"])
        result.append(item)
    if len({item["type"] for item in result}) > 1:
        raise ValueError("Mixed polymer materials are not implemented; prepare a same-polymer color job or use Bambu Studio for a separately reviewed process")
    return result


def resolve_profile(path: Path, root: Path | None = None, _trail=()) -> dict:
    p = Path(path).resolve()
    if p in _trail or len(_trail) > 30:
        raise ValueError("Cyclic or excessively deep profile inheritance")
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    root = Path(root or p.parent.parent)

    def locate(name):
        direct = p.parent / f"{name}.json"
        if direct.is_file():
            return direct
        matches = [x for x in root.rglob(f"{name}.json") if x.is_file()]
        if len(matches) != 1:
            raise ValueError(f"Profile dependency {name!r} has {len(matches)} matches")
        return matches[0]

    result = {}
    inherited = data.get("inherits")
    if inherited:
        if not isinstance(inherited, str):
            raise ValueError("Profile inherits must be a single preset name")
        result.update(resolve_profile(locate(inherited), root, _trail + (p,)))
    includes = data.get("include", [])
    if isinstance(includes, str):
        includes = [includes]
    for name in includes:
        result.update(resolve_profile(locate(name), root, _trail + (p,)))
    result.update({k: v for k, v in data.items() if k not in ("inherits", "include")})
    return result


def _profile_paths(printer: dict, config: dict) -> tuple[Path, dict]:
    spec = get_model(printer.get("model"))
    configured = config.get("path") or config.get("executable")
    exe = discover_slicer_executable(configured)
    if exe is None:
        raise ValueError("Installed Bambu Studio executable was not found; configure slicer.executable or its .app bundle")
    root = discover_profile_root(exe, config.get("profile_root"))
    if root is None:
        raise ValueError("Bambu Studio profile directories were not found; configure slicer.profile_root ending in profiles/BBL")
    diameter = float(printer.get("nozzle_diameter", .4))
    if not math.isfinite(diameter) or diameter not in spec["supported_nozzle_diameters_mm"]:
        raise ValueError("Choose an installed, supported nozzle diameter for this exact printer model")
    nozzle = f"{diameter:g}"
    requests = _requested_materials(printer)
    if len(requests) > 1:
        raise ValueError("Automatic mesh preparation supports one filament; assign body colors in Bambu Studio, slice there, then import the sliced .gcode.3mf with its full materials palette")
    requested = requests[0] if requests else {}
    material = str(requested.get("type") or printer.get("material") or config.get("material") or "").strip().upper()
    if not material:
        raise ValueError("Configure the confirmed material before choosing a filament preset")
    profiles = config.get("profiles", {})
    machine = Path(profiles.get("machine") or root/"machine"/f"{spec['profile_model_name']} {nozzle} nozzle.json").resolve()
    if not machine.is_file():
        raise ValueError(f"Required installed profile is missing: {machine.name}")
    resolved_machine = resolve_profile(machine, root)
    machine_name = resolved_machine.get("name", machine.stem)
    default_process = resolved_machine.get("default_print_profile")
    process = Path(profiles["process"]).resolve() if profiles.get("process") else None
    if process is None and default_process:
        process = root/"process"/(default_process+".json")
    if process is None:
        # The fallback is limited to compatible official Standard presets, never a renamed machine.
        candidates = []
        for path in (root/"process").rglob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if "Standard" in data.get("name", "") and machine_name in data.get("compatible_printers", []):
                candidates.append(path)
        if len(candidates) != 1:
            raise ValueError("The machine preset has no unique compatible default process; configure profiles.process")
        process = candidates[0]
    filament = Path(profiles["filament"]).resolve() if profiles.get("filament") else None
    if filament is None:
        candidates = []
        prefix = str(requested.get("preset") or "Generic " + material).strip()
        for path in (root/"filament").rglob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            name = data.get("name", "")
            if not _preset_matches(name, prefix):
                continue
            if data.get("instantiation") not in (True, "true", "1"):
                continue
            resolved = resolve_profile(path, root)
            compatible = resolved.get("compatible_printers", [])
            if machine_name not in compatible or str(_first(resolved.get("filament_type", ""))).upper() != material:
                continue
            candidates.append((len(compatible), name, path))
        if not candidates:
            raise ValueError(f"No compatible installed {prefix} preset for {machine_name}; install/select the exact preset, no substitute will be used")
        filament = sorted(candidates)[0][2]
    for p in (machine, process, filament):
        if not p.is_file():
            raise ValueError(f"Required installed profile is missing: {p.name}")
    model_path = root/"machine"/(spec["profile_model_name"]+".json")
    return exe, {"root": root, "machine": machine.resolve(), "process": process.resolve(),
                 "filament": filament.resolve(), "model": model_path if model_path.is_file() else None}


def _validate_profiles(profiles: dict, printer: dict):
    spec = get_model(printer.get("model"))
    machine, process, filament = (profiles[k] for k in ("machine", "process", "filament"))
    if normalize_model(machine.get("printer_model")) != spec["id"]:
        raise ValueError("Machine profile model does not match the selected printer")
    diameters = machine.get("nozzle_diameter", [])
    diameters = diameters if isinstance(diameters, list) else [diameters]
    if len(diameters) != spec["tool_count"] or any(not math.isfinite(float(d)) or abs(float(d)-float(printer.get("nozzle_diameter", .4))) > .001 for d in diameters):
        raise ValueError("Machine profile nozzle diameter does not match the installed nozzle")
    for kind, profile in (("process", process), ("filament", filament)):
        compatible = profile.get("compatible_printers", [])
        if compatible and machine.get("name") not in compatible:
            raise ValueError(f"{kind.title()} profile is not compatible with the selected machine preset")
    material = str(printer.get("material", "")).upper()
    if material and str(_first(filament.get("filament_type", ""))).upper() != material:
        raise ValueError("Filament profile material does not match the configured material")
    requests = _requested_materials(printer)
    if requests:
        requested = requests[0]
        if requested["type"] != str(_first(filament.get("filament_type", ""))).upper():
            raise ValueError("Filament profile polymer does not match the requested palette")
        if requested.get("preset") and not _preset_matches(filament.get("name", ""), requested["preset"]):
            raise ValueError("Filament profile does not match the exact requested product/preset")
        if requested.get("profile_id") and requested["profile_id"] != filament.get("filament_id"):
            raise ValueError("Filament profile ID does not match the requested product")
    if not machine.get("machine_start_gcode"):
        raise ValueError("Machine startup G-code was not resolved from the exact installed preset")
    if spec["tool_count"] > 1:
        types = machine.get("nozzle_type", [])
        requested = printer.get("nozzle_type", spec["default_nozzle_type"])
        if not types or any(value != requested for value in types):
            raise ValueError("Dual-tool preparation currently requires matching stock nozzle materials; mixed/custom tools are not implemented")


def _configure_nozzle(profiles: dict, printer: dict):
    """Select an installed variant, retaining its resolved per-variant parameter vectors."""
    spec = get_model(printer.get("model"))
    flow = _flow(printer.get("nozzle_volume_type", "Standard"))
    if spec["tool_count"] > 1 and flow != "Standard":
        raise ValueError("Dual-tool high-flow preparation is not implemented")
    machine = profiles["machine"]
    variant = "Direct Drive High Flow" if flow == "HighFlow" else "Direct Drive Standard"
    if flow == "HighFlow" and variant not in _values(machine.get("printer_extruder_variant")):
        raise ValueError("Exact installed machine preset has no compatible HighFlow extruder variant")
    serialized = "High Flow" if flow == "HighFlow" else "Standard"
    machine["nozzle_volume_type"] = [serialized] * spec["tool_count"]
    machine["default_nozzle_volume_type"] = [serialized] * spec["tool_count"]
    if spec["tool_count"] == 1:
        machine["nozzle_type"] = [printer["nozzle_type"]] * max(1, len(_values(machine.get("printer_extruder_variant"))))
    nozzle = printer.get("nozzle") or {}
    if nozzle.get("custom"):
        if nozzle.get("stock_geometry_compatible") is not True:
            raise ValueError("Custom nozzle needs confirmed stock geometry compatibility before reusing an installed machine preset")
        cap, temp = float(nozzle.get("max_volumetric_speed", 0)), float(nozzle.get("max_temperature_c", 0))
        if not math.isfinite(cap) or not 0 < cap <= 100 or not math.isfinite(temp) or not 0 < temp <= spec["nozzle_max_c"]:
            raise ValueError("Custom nozzle requires bounded calibrated flow and temperature limits")
        filament = profiles["filament"]
        for key in ("filament_max_volumetric_speed", "filament_flush_volumetric_speed", "filament_ramming_volumetric_speed", "filament_ramming_volumetric_speed_nc"):
            values = _numeric_values(filament.get(key, [cap]))
            if key == "filament_max_volumetric_speed" and any(value <= 0 for value in values):
                raise ValueError("Custom nozzle cannot reuse undefined/non-positive filament flow values")
            # 0/-1 are automatic fallbacks in vendor flush/ramming presets.
            # Purge templates use the rounded nominal area 2.4 mm2. Keep a
            # 1% margin before checking the physical 1.75 mm filament area.
            filament[key] = [f"{min(value, cap * .99) if value > 0 else cap * .99:g}" for value in values]
        if any(value > temp for value in _numeric_values(filament.get("nozzle_temperature_range_low", [0]))):
            raise ValueError("Custom nozzle maximum temperature is below this filament's required minimum")
        for key in ("nozzle_temperature", "nozzle_temperature_initial_layer", "nozzle_temperature_range_high", "filament_flush_temp", "filament_flush_temp_fast"):
            if key in filament:
                filament[key] = [f"{min(value, temp):g}" for value in _numeric_values(filament[key])]


def validate_process_overrides(overrides: dict | None, printer: dict, machine: dict | None = None) -> dict:
    """Normalize a small per-job process whitelist without accepting machine G-code."""
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError("process_overrides must be an object")
    allowed = {"layer_height", "wall_loops", "sparse_infill_density", "sparse_infill_pattern",
               "top_shell_layers", "bottom_shell_layers", "enable_support", "support_type",
               "support_style", "support_threshold_angle", "support_top_z_distance", "support_bottom_z_distance",
               "support_object_xy_distance", "support_interface_top_layers", "support_interface_bottom_layers",
               "support_on_build_plate_only", "brim_type", "brim_width", "brim_object_gap"}
    unknown = set(overrides) - allowed
    if unknown:
        raise ValueError("Unsupported process override: " + ", ".join(sorted(unknown)))
    result = {}
    nozzle = float(printer.get("nozzle_diameter", .4))
    minimum = _number((machine or {}).get("min_layer_height", [nozzle * .2]))
    maximum = _number((machine or {}).get("max_layer_height", [nozzle * .7]))
    for key, value in overrides.items():
        if key == "layer_height":
            if type(value) not in {int, float} or not math.isfinite(value) or not minimum - 1e-9 <= value <= maximum + 1e-9:
                raise ValueError(f"layer_height must be a finite number between {minimum:g} and {maximum:g} mm for this installed nozzle")
            result[key] = f"{value:g}"
        elif key in {"wall_loops", "top_shell_layers", "bottom_shell_layers"}:
            minimum_count, maximum_count = (2, 8) if key == "wall_loops" else (3, 12)
            if type(value) is not int or not minimum_count <= value <= maximum_count:
                raise ValueError(f"{key} must be an integer from {minimum_count} to {maximum_count}")
            result[key] = str(value)
        elif key == "sparse_infill_density":
            if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?%", value) or not 0 <= float(value[:-1]) <= 100:
                raise ValueError("sparse_infill_density must be a percentage string from 0% to 100%")
            result[key] = f"{float(value[:-1]):g}%"
        elif key == "sparse_infill_pattern":
            if not isinstance(value, str) or value not in {"gyroid", "grid", "cubic", "rectilinear"}:
                raise ValueError("sparse_infill_pattern must be gyroid, grid, cubic or rectilinear")
            result[key] = value
        elif key in {"enable_support", "support_on_build_plate_only"}:
            if type(value) is bool:
                result[key] = "1" if value else "0"
            elif value in ("0", "1"):
                result[key] = value
            else:
                raise ValueError(f"{key} must be a boolean or the string 0 or 1")
        elif key in {"support_type", "support_style", "brim_type"}:
            options = {"support_type": {"normal(auto)", "normal(manual)", "tree(auto)", "tree(manual)"},
                       "support_style": {"default", "grid", "snug", "tree_slim", "tree_strong", "tree_hybrid", "tree_organic"},
                       "brim_type": {"no_brim", "auto_brim", "outer_only", "inner_only", "outer_and_inner"}}
            if not isinstance(value, str) or value not in options[key]:
                raise ValueError(f"Unsupported {key}; choose one of " + ", ".join(sorted(options[key])))
            result[key] = value
        elif key in {"support_interface_top_layers", "support_interface_bottom_layers"}:
            if type(value) is not int or not 0 <= value <= 6:
                raise ValueError(f"{key} must be an integer from 0 to 6")
            result[key] = str(value)
        else:
            bounds = {"support_threshold_angle": (0, 90), "support_top_z_distance": (0, 2),
                      "support_bottom_z_distance": (0, 2), "support_object_xy_distance": (0, 5),
                      "brim_width": (0, 30), "brim_object_gap": (0, 2)}
            lower, upper = bounds[key]
            if type(value) not in {int, float} or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{key} must be a finite number between {lower} and {upper}")
            result[key] = f"{value:g}"
    return result


def _project_overrides(source: Path, profiles: dict, printer: dict, config: dict) -> dict:
    """Retain compatible global process/material settings, refusing lossy object overrides."""
    if source.suffix.lower() != ".3mf" or classify_3mf(source)["kind"] != "project":
        return {"policy": "configured profiles"}
    with checked_archive(source) as z:
        values = json.loads(z.read("Metadata/project_settings.config"))
        spec = get_model(printer.get("model"))
        if normalize_model(values.get("printer_model")) != spec["id"]:
            raise ValueError("Source 3MF project targets a different printer model")
        if any(abs(d-float(printer["nozzle_diameter"]))>.001 for d in _numeric_values(values.get("nozzle_diameter",0))):
            raise ValueError("Source 3MF project targets a different nozzle diameter")
        if spec["tool_count"]>1 and values.get("filament_map",["1"]) != ["1"]:
            raise ValueError("Reslicing a non-default dual-tool project mapping is not implemented; preserve it as a sliced 3MF instead")
        materials = values.get("filament_type", [])
        if len(materials) != 1 or str(materials[0]).upper() != str(printer["material"]).upper():
            raise ValueError("Source 3MF project needs a different or multiple materials; retain assignments in Bambu Studio and import its sliced .gcode.3mf")
        requests = _requested_materials(printer)
        if requests and requests[0].get("preset") and not _preset_matches(_first(values.get("filament_settings_id", [])), requests[0]["preset"]):
            raise ValueError("Source 3MF project uses a different filament preset; change the product in Bambu Studio and import the sliced .gcode.3mf")
        if "Metadata/model_settings.config" in z.namelist():
            xml = z.read("Metadata/model_settings.config")
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise ValueError("XML entities are not accepted")
            tree = ET.fromstring(xml)
            for part in tree.iter("part"):
                if part.attrib.get("subtype", "normal_part") != "normal_part":
                    raise ValueError("3MF modifier/negative/support volumes cannot be retained by single-mesh preparation; retain them in Bambu Studio and import the sliced .gcode.3mf")
            for object_node in list(tree.iter("object")) + list(tree.iter("part")):
                for meta in object_node.findall("metadata"):
                    key, value = meta.attrib.get("key"), meta.attrib.get("value")
                    if key == "extruder" and value not in (None, "0", "1"):
                        raise ValueError("3MF per-object material assignment is unsupported")
                    if key in profiles["process"] and key not in {"name", "type", "from"}:
                        raise ValueError(f"3MF per-object setting {key!r} cannot be retained by single-mesh preparation; retain it in Bambu Studio and import the sliced .gcode.3mf")
        excluded = {"name", "type", "from", "instantiation", "setting_id", "compatible_printers", "compatible_printers_condition",
                    "print_settings_id", "filament_settings_id", "printer_settings_id"}
        copied = {}
        for kind in ("process", "filament"):
            if config.get("profiles", {}).get(kind):
                continue
            keys = set(profiles[kind]) - excluded
            copied[kind] = sorted(k for k in keys if k in values)
            profiles[kind].update({k: values[k] for k in copied[kind]})
        return {"policy": "preserve compatible global project process/material settings; installed machine startup code",
                "retained_keys": copied, "per_object_modifiers": "refused"}


def _gcode_settings(text: str) -> dict:
    settings = {}
    for line in text.splitlines():
        match = re.match(r";\s*([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)$", line)
        if match:
            settings[match.group(1)] = match.group(2)
    return settings


def _numeric_values(value) -> list[float]:
    raw = value if isinstance(value, (list, tuple)) else re.split(r"[,;]", str(value))
    result = [float(v) for v in raw]
    if not result or not all(math.isfinite(v) for v in result):
        raise ValueError("Expected finite numeric nozzle diameter/profile values")
    return result


def _polygon(value) -> list[tuple[float, float]]:
    if value is None or value == [] or value == "":
        return []
    raw = value.split(",") if isinstance(value, str) else value
    points = []
    for item in raw:
        xy = re.split(r"[xX]", item.strip()) if isinstance(item, str) else item
        if len(xy) != 2:
            raise ValueError("Invalid printable-area point")
        point = (float(xy[0]), float(xy[1]))
        if not all(math.isfinite(v) for v in point):
            raise ValueError("Non-finite printable-area point")
        points.append(point)
    if len(points) < 3:
        raise ValueError("A printable-area polygon needs at least three points")
    return points


def _workspace(printer: dict, settings: dict | None = None, tool_id: int | None = None) -> dict:
    spec = get_model(printer.get("model"))
    settings = settings or {}
    volume = build_volume_for(printer, settings)
    area = _polygon(settings.get("printable_area")) or [(0.,0.),(volume[0],0.),(volume[0],volume[1]),(0.,volume[1])]
    if spec["tool_count"] > 1 and tool_id is not None:
        if not 1 <= tool_id <= spec["tool_count"]:
            raise ValueError("Selected sliced extruder is not present on this model")
        per_tool = settings.get("extruder_printable_area", [])
        heights = settings.get("extruder_printable_height", [])
        if len(per_tool) != spec["tool_count"] or len(heights) != spec["tool_count"]:
            raise ValueError("Dual-tool sliced file lacks validated per-tool printable areas/heights")
        # Bambu's bbs_3mf.cpp reader stores XML extruder_id minus one in NozzleInfo.
        # This is the logical preset-vector index, not the device's physical nozzle ID.
        area = _polygon(per_tool[tool_id-1])
        volume[2] = min(volume[2], float(heights[tool_id-1]))
        baseline_areas = spec.get("profile_extruder_printable_areas_mm",[])
        baseline_heights = spec.get("profile_extruder_heights_mm",[])
        if len(baseline_areas)==spec["tool_count"]:
            baseline = _polygon(baseline_areas[tool_id-1])
            if any(not _inside_polygon(point,baseline) for point in area):
                raise ValueError("The sliced preset expands the registered per-tool printable area")
        if len(baseline_heights)==spec["tool_count"]:
            volume[2]=min(volume[2],float(baseline_heights[tool_id-1]))
    raw_exclusions = settings.get("bed_exclude_area", [])
    excluded = []
    if raw_exclusions:
        # Bambu's flat coPoints bed_exclude_area encodes four corners per reserved region.
        flat = raw_exclusions.split(",") if isinstance(raw_exclusions, str) else raw_exclusions
        if len(flat) % 4:
            raise ValueError("Unsupported bed-exclusion encoding; expected four corners per reserved region")
        excluded = [_polygon(flat[i:i+4]) for i in range(0, len(flat), 4)]
    for baseline in spec.get("bed_exclude_areas_mm",[]):
        polygon=_polygon(baseline)
        if polygon not in excluded:
            excluded.append(polygon)
    xs,ys = [p[0] for p in area],[p[1] for p in area]
    rectangle = len(area) == 4 and set(area) == {(min(xs),min(ys)),(min(xs),max(ys)),(max(xs),min(ys)),(max(xs),max(ys))}
    return {"model":spec["id"],"display_name":spec["display_name"],"volume_mm":volume,
            "printable_area_mm":area,"bed_exclude_areas_mm":excluded,"rectangular":rectangle,
            "area_bounds_xy_mm":[min(xs),min(ys),max(xs),max(ys)],"selected_extruder_id":tool_id}


def _point_segment_distance(point, a, b):
    dx,dy = b[0]-a[0],b[1]-a[1]
    denominator = dx*dx+dy*dy
    t = max(0.,min(1.,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/denominator)) if denominator else 0.
    return math.hypot(point[0]-a[0]-t*dx,point[1]-a[1]-t*dy)


def _inside_polygon(point, polygon):
    inside = False
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        if _point_segment_distance(point,a,b) < 1e-8:
            return True
        if (a[1]>point[1]) != (b[1]>point[1]) and point[0] < (b[0]-a[0])*(point[1]-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


def _segment_distance(a,b,c,d):
    def cross(p,q,r): return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    if cross(a,b,c)*cross(a,b,d) <= 0 and cross(c,d,a)*cross(c,d,b) <= 0:
        if max(min(a[0],b[0]),min(c[0],d[0])) <= min(max(a[0],b[0]),max(c[0],d[0]))+1e-9 and max(min(a[1],b[1]),min(c[1],d[1])) <= min(max(a[1],b[1]),max(c[1],d[1]))+1e-9:
            return 0.
    return min(_point_segment_distance(a,c,d),_point_segment_distance(b,c,d),_point_segment_distance(c,a,b),_point_segment_distance(d,a,b))


def _zone_candidates(bounds, workspace):
    result=[]
    for polygon in workspace["bed_exclude_areas_mm"]:
        xs,ys=[p[0] for p in polygon],[p[1] for p in polygon]
        if not(bounds[2]<min(xs) or bounds[0]>max(xs) or bounds[3]<min(ys) or bounds[1]>max(ys)):
            result.append(polygon)
    return result


def _check_path_zones(points, radius, workspace, zones):
    area=workspace["printable_area_mm"]
    for a,b in zip(points,points[1:]):
        if not workspace["rectangular"]:
            if not _inside_polygon(a,area) or not _inside_polygon(b,area):
                raise ValueError("Printed extrusion leaves the machine's non-rectangular printable area")
            if any(_segment_distance(a,b,c,d)<radius-.01 for c,d in zip(area,area[1:]+area[:1])):
                raise ValueError("Extrusion bead crosses the machine's printable-area boundary")
        for polygon in zones:
            if _inside_polygon(a,polygon) or _inside_polygon(b,polygon) or any(_segment_distance(a,b,c,d)<radius-.01 for c,d in zip(polygon,polygon[1:]+polygon[:1])):
                raise ValueError("Printed extrusion enters a reserved bed-exclusion area")


def _check_executable_limits(text: str, model: str) -> dict:
    spec = get_model(model)
    volume = spec["build_volume_mm"]
    envelope = [[-max(60.,v*.25),v+max(80.,v*.25)] for v in volume]
    targets = {"nozzle": [], "bed": []}
    moves = 0
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        if "{" in code or "}" in code or "[" in code or "]" in code:
            raise ValueError(f"Unresolved slicer expression in executable line {number}")
        heat = re.match(r"(?:N\d+\s+)?(M104|M109|M140|M190)\b(.*)", code, re.I)
        if heat:
            kind = "nozzle" if heat.group(1).upper() in {"M104", "M109"} else "bed"
            maximum = spec["nozzle_max_c"] if kind == "nozzle" else spec["bed_max_c"]
            params = re.findall(r"(?:^|\s)([SRH])\s*([^\s]+)", heat.group(2), re.I)
            for _, token in params:
                try:
                    temp = float(token)
                except ValueError:
                    raise ValueError(f"Non-numeric {kind} temperature in executable line {number}") from None
                if not math.isfinite(temp) or not 0 <= temp <= maximum:
                    raise ValueError(f"Executable {kind} target {token} exceeds {spec['display_name']} limit 0-{maximum} C (line {number})")
                targets[kind].append(temp)
        move = re.match(r"(?:N\d+\s+)?G(?:0?0|0?1)\b(.*)", code, re.I)
        if move:
            moves += 1
            for axis, token in re.findall(r"(?:^|\s)([XYZ])\s*([^\s]+)", move.group(1), re.I):
                try:
                    value = float(token)
                except ValueError:
                    raise ValueError(f"Non-numeric {axis} coordinate in executable line {number}") from None
                # Broad sanity envelope retains official purge/wipe/end motions outside the bed.
                lower,upper = envelope["XYZ".index(axis.upper())]
                if not math.isfinite(value) or not lower <= value <= upper:
                    raise ValueError(f"Extreme {axis} coordinate {token} in executable line {number}")
    if not moves or not targets["bed"] or not targets["nozzle"]:
        raise ValueError("Executable plate lacks motion or explicit nozzle/bed temperature targets")
    return {"checked_move_lines": moves,
            "maximum_nozzle_target_c": max(targets["nozzle"]), "maximum_bed_target_c": max(targets["bed"]),
            "startup_sanity_envelope_mm":envelope,
            "checks_not_performed": ["complete modal G-code simulation", "physical collision detection", "every vendor-specific command semantic"]}


def _motion_parameters(tail: str) -> dict[str, float]:
    result = {}
    for match in re.finditer(r"([A-Za-z])([^A-Za-z]*)", tail.split("*", 1)[0]):
        key, value = match.group(1).upper(), match.group(2).strip()
        if key not in "XYZEFIJKR":
            continue
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"Non-numeric motion parameter {key}") from None
        if not math.isfinite(number):
            raise ValueError(f"Non-finite motion parameter {key}")
        result[key] = number
    return result


def _arc_xy_extrema(start: dict, end: dict, params: dict, clockwise: bool, absolute_center: bool, sample_error: float | None = None) -> list[tuple[float, float]]:
    """Analytic extrema of a G17 arc, including quadrants crossed between endpoints."""
    sx, sy, ex, ey = start["X"], start["Y"], end["X"], end["Y"]
    tau = 2 * math.pi
    def span(cx, cy):
        a, b = math.atan2(sy-cy, sx-cx), math.atan2(ey-cy, ex-cx)
        d = (a-b) % tau if clockwise else (b-a) % tau
        if math.hypot(ex-sx, ey-sy) <= 1e-9:
            d = tau
        return a, d
    if "I" in params or "J" in params:
        if "R" in params:
            raise ValueError("An extrusion arc cannot specify both R and I/J")
        cx = params.get("I", sx) if absolute_center else sx + params.get("I", 0)
        cy = params.get("J", sy) if absolute_center else sy + params.get("J", 0)
    elif "R" in params:
        dx, dy = ex-sx, ey-sy
        chord, radius = math.hypot(dx, dy), abs(params["R"])
        if chord <= 1e-9 or radius < chord / 2 - 1e-6:
            raise ValueError("Invalid or ambiguous radius-form extrusion arc")
        height = math.sqrt(max(0, radius*radius - chord*chord/4))
        candidates = [((sx+ex)/2 + sign*(-dy/chord)*height,
                       (sy+ey)/2 + sign*(dx/chord)*height) for sign in (-1, 1)]
        expected_minor = params["R"] >= 0
        selected = [c for c in candidates if (span(*c)[1] <= math.pi+1e-8 if expected_minor else span(*c)[1] >= math.pi-1e-8)]
        if not selected:
            raise ValueError("Cannot resolve extrusion arc direction")
        cx, cy = selected[0]
    else:
        raise ValueError("Extrusion arc lacks an I/J center or R radius")
    ra, rb = math.hypot(sx-cx, sy-cy), math.hypot(ex-cx, ey-cy)
    if ra <= 1e-9 or abs(ra-rb) > max(.05, ra*.002):
        raise ValueError("Extrusion arc endpoints disagree with its center")
    radius = max(ra, rb)
    first, delta = span(cx, cy)
    if sample_error is not None:
        step = 2*math.acos(max(-1., min(1.,1-sample_error/radius)))
        count = max(1,math.ceil(delta/max(step,1e-6)))
        if count > 50000:
            raise ValueError("Extrusion arc is too complex for bounded reserved-zone inspection")
        direction = -1 if clockwise else 1
        return [(sx,sy)] + [(cx+radius*math.cos(first+direction*delta*i/count),cy+radius*math.sin(first+direction*delta*i/count)) for i in range(1,count)] + [(ex,ey)]
    points = [(sx, sy), (ex, ey)]
    for a in (0, math.pi/2, math.pi, 3*math.pi/2):
        crossed = (first-a) % tau if clockwise else (a-first) % tau
        if crossed <= delta + 1e-9:
            points.append((cx+radius*math.cos(a), cy+radius*math.sin(a)))
    return points


def _whole_print_extrusion_bounds(text: str, nozzle_diameter: float, default_width: float | None = None,
                                  *, printer: dict | None = None, workspace: dict | None = None) -> dict:
    """Bound actual deposited moves after layer markers, separately from startup/wipe G-code.

    Supports absolute/relative XYZ and E, G92 E, G17 I/J or R arcs, and
    absolute/relative arc centers. Width is the slicer's per-move declaration.
    This is bounded toolpath inspection, not complete vendor-G-code simulation.
    """
    if workspace is None:
        if printer is None:
            raise ValueError("An explicit printer model/workspace is required for extrusion inspection")
        workspace = _workspace(printer)
    limits = workspace["volume_mm"]
    area_bounds = workspace["area_bounds_xy_mm"]
    position = {"X": 0., "Y": 0., "Z": 0., "E": 0.}
    xyz_absolute, e_relative, center_absolute, plane = True, False, False, "G17"
    layers, moves, arcs, ignored_custom = 0, 0, 0, 0
    width = float(default_width or nozzle_diameter)
    if not math.isfinite(width) or not 0 < width <= 5:
        raise ValueError("Invalid default extrusion line width")
    layer_height = float(nozzle_diameter) / 2
    feature = "Custom"
    features = set()
    low, high = [float("inf")]*3, [float("-inf")]*3
    deposited_low, deposited_high = list(low), list(high)
    first_layer_xy = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped in {"; CHANGE_LAYER", ";LAYER_CHANGE"}:
            layers += 1
        elif stripped.startswith("; FEATURE:"):
            feature = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("; LINE_WIDTH:"):
            width = float(stripped.split(":", 1)[1])
            if not math.isfinite(width) or not 0 < width <= 5:
                raise ValueError("Invalid declared extrusion line width")
        elif stripped.startswith("; LAYER_HEIGHT:"):
            layer_height = float(stripped.split(":", 1)[1])
            if not math.isfinite(layer_height) or not 0 < layer_height <= 1:
                raise ValueError("Invalid declared layer height")
        code = line.split(";", 1)[0].strip()
        match = re.match(r"(?:N\d+\s+)?([GM]\d+(?:\.\d+)?)(.*)", code, re.I)
        if not match:
            continue
        command, tail = match.group(1).upper(), match.group(2)
        if command == "G90": xyz_absolute = True
        elif command == "G91": xyz_absolute = False
        elif command == "M83": e_relative = True
        elif command == "M82": e_relative = False
        elif command == "G90.1": center_absolute = True
        elif command == "G91.1": center_absolute = False
        elif command in {"G17", "G18", "G19"}: plane = command
        elif command == "G20":
            raise ValueError("Inch-mode prepared G-code is not supported")
        elif command == "G92":
            params = _motion_parameters(tail)
            if any(axis in params for axis in "XYZ"):
                raise ValueError("G92 XYZ coordinate offsets require a full motion simulator")
            if "E" in params: position["E"] = params["E"]
        elif command in {"G0", "G00", "G1", "G01", "G2", "G02", "G3", "G03"}:
            params = _motion_parameters(tail)
            end = dict(position)
            for axis in "XYZ":
                if axis in params:
                    end[axis] = params[axis] if xyz_absolute else position[axis]+params[axis]
            amount = 0.
            if "E" in params:
                amount = params["E"] if e_relative else params["E"]-position["E"]
                end["E"] = position["E"]+params["E"] if e_relative else params["E"]
            is_arc = command in {"G2", "G02", "G3", "G03"}
            motion = any(abs(end[a]-position[a]) > 1e-10 for a in "XYZ") or is_arc
            if layers and amount > 1e-8 and motion:
                if feature.lower() == "custom":
                    ignored_custom += 1
                else:
                    points = [(position["X"],position["Y"]), (end["X"],end["Y"])]
                    if is_arc:
                        if plane != "G17":
                            raise ValueError("Only XY-plane extrusion arcs can be validated")
                        points = _arc_xy_extrema(position, end, params, command in {"G2", "G02"}, center_absolute)
                        arcs += 1
                    move_bounds = [min(p[0] for p in points)-width/2,min(p[1] for p in points)-width/2,
                                   max(p[0] for p in points)+width/2,max(p[1] for p in points)+width/2]
                    zones = _zone_candidates(move_bounds,workspace)
                    if zones or not workspace["rectangular"]:
                        path_points = _arc_xy_extrema(position,end,params,command in {"G2","G02"},center_absolute,.005) if is_arc else points
                        _check_path_zones(path_points,width/2+(.005 if is_arc else 0),workspace,zones)
                    moves += 1
                    features.add(feature)
                    for x, y in points:
                        low[0],low[1] = min(low[0],x),min(low[1],y)
                        high[0],high[1] = max(high[0],x),max(high[1],y)
                        deposited_low[0],deposited_low[1] = min(deposited_low[0],x-width/2),min(deposited_low[1],y-width/2)
                        deposited_high[0],deposited_high[1] = max(deposited_high[0],x+width/2),max(deposited_high[1],y+width/2)
                        if layers == 1:
                            first_layer_xy = [min(first_layer_xy[0],x-width/2),min(first_layer_xy[1],y-width/2),
                                              max(first_layer_xy[2],x+width/2),max(first_layer_xy[3],y+width/2)]
                    zlo,zhi = min(position["Z"],end["Z"]),max(position["Z"],end["Z"])
                    low[2],high[2] = min(low[2],zlo),max(high[2],zhi)
                    deposited_low[2],deposited_high[2] = min(deposited_low[2],max(0,zlo-layer_height)),max(deposited_high[2],zhi)
                    if deposited_low[0] < -.01 or deposited_low[1] < -.01 or deposited_high[0] > limits[0]+.01 or deposited_high[1] > limits[1]+.01:
                        raise ValueError(f"Whole printed extrusion exceeds {workspace['display_name']} XY build area (line {line_number})")
                    if workspace["rectangular"] and (deposited_low[0]<area_bounds[0]-.01 or deposited_low[1]<area_bounds[1]-.01 or deposited_high[0]>area_bounds[2]+.01 or deposited_high[1]>area_bounds[3]+.01):
                        raise ValueError(f"Whole printed extrusion exceeds the selected tool's XY printable area (line {line_number})")
                    if zlo < -.01 or zhi > limits[2]+.01:
                        raise ValueError(f"Whole printed extrusion exceeds {workspace['display_name']} Z build height (line {line_number})")
            position = end
    if not moves:
        raise ValueError("No model extrusion moves were found after recognized layer/feature markers")
    if not all(math.isfinite(v) for v in first_layer_xy):
        raise ValueError("No model/raft/brim deposition was found on the first layer")
    return {"extrusion_path_bounds_mm": [low, high], "whole_print_bounds_mm": [deposited_low, deposited_high],
            "first_layer_extrusion_bounds_xy_mm": first_layer_xy, "layers": layers,
            "checked_extrusion_moves": moves, "checked_extrusion_arcs": arcs,
            "features": sorted(features), "ignored_custom_extrusions_after_layer_marker": ignored_custom,
            "workspace":workspace,
            "xy_envelope": "conservative per-move half line width around path and analytic arc extrema",
            "startup_policy": "model deposition after layer markers; explicit Custom feature excluded"}


def _inspect_filament_selectors(text: str, used_ids: set[int], palette_size: int) -> dict:
    """Bambu T and main M620/M621 S select logical filaments, not physical nozzles."""
    # Literal non-filament commands found in official installed startup/end templates.
    sentinels = {255, 1000, 1001, 1100, 65279, 65535}
    selected, seen_sentinels = set(), set()
    for number, line in enumerate(text.splitlines(), 1):
        code = re.sub(r"^N\d+\s+", "", line.split(";", 1)[0].strip(), flags=re.I)
        match = re.match(r"^T(\d+)(?:\s|$)", code, re.I)
        if not match:
            # M620.10 and similar commands carry physical/variant parameters.
            match = re.match(r"^M62[01]\s+S(\d+)(?:A)?(?:\s|$)", code, re.I)
        if not match:
            if re.match(r"^T|^M62[01]\s+S", code, re.I):
                raise ValueError(f"Unknown executable filament selector in line {number}")
            continue
        value = int(match[1])
        if value in sentinels:
            seen_sentinels.add(value)
            continue
        filament_id = value + 1
        if not 1 <= filament_id <= palette_size or filament_id not in used_ids:
            raise ValueError(f"Executable filament selector {value} is absent from the selected plate's used palette (line {number})")
        selected.add(filament_id)
    if (len(used_ids) > 1 or used_ids != {1}) and selected != used_ids:
        raise ValueError("Executable filament selectors do not confirm every used color in slice metadata")
    return {"logical_filament_ids": sorted(selected), "non_filament_sentinels": sorted(seen_sentinels),
            "indexing": "T and main M620/M621 S are zero-based logical filament selectors; physical nozzle mapping is checked separately"}


def _inspect_palette(settings: dict, gsettings: dict, filaments: list[dict], text: str, printer: dict) -> tuple[list[dict], list[int], dict]:
    types, actual_types = _values(settings.get("filament_type")), _values(gsettings.get("filament_type"))
    if not types or len(types) > 64 or len(actual_types) != len(types):
        raise ValueError("Project and executable plate have an invalid or different filament palette size")
    if any(not value or value.upper() != actual.upper() for value, actual in zip(types, actual_types)):
        raise ValueError("Project and executable G-code use a different filament material")
    if len({value.upper() for value in types}) != 1:
        raise ValueError("Mixed polymer materials are not implemented; same-polymer multicolor plates are supported")
    material = str(printer.get("material", "")).upper()
    if material and any(value.upper() != material for value in types):
        raise ValueError("Prepared file uses a different filament material")
    requests = _requested_materials(printer)
    if requests and len(requests) != len(types):
        raise ValueError("Requested materials must match the complete logical project palette")
    palettes = {key: _values(settings.get(key)) for key in ("filament_settings_id", "filament_ids", "filament_colour")}
    executable = {key: _values(gsettings.get(key)) for key in palettes}
    palette = []
    for index, polymer in enumerate(types):
        entry = {"filament_id": index + 1, "type": polymer.upper(), "preset": None, "profile_id": None, "color": None}
        for key, field in (("filament_settings_id", "preset"), ("filament_ids", "profile_id"), ("filament_colour", "color")):
            declared, actual = palettes[key], executable[key]
            if declared or actual:
                if len(declared) != len(types) or len(actual) != len(types):
                    raise ValueError(f"Project and executable palette have incomplete {key}")
                left, right = declared[index], actual[index]
                if field == "color":
                    left, right = _color(left), _color(right)
                if left != right:
                    raise ValueError(f"Project and executable disagree on filament {index+1} {field}")
                entry[field] = left
            if requests and requests[index].get(field):
                requested = requests[index][field]
                matches = _preset_matches(entry[field], requested) if field == "preset" else entry[field] == requested
                if not matches:
                    raise ValueError(f"Filament {index+1} does not match the requested {field}")
        if requests and requests[index]["type"] != entry["type"]:
            raise ValueError(f"Filament {index+1} does not match the requested polymer type")
        palette.append(entry)
    used_ids = set()
    if not filaments:
        raise ValueError("Selected plate has no used filament metadata")
    for filament in filaments:
        try:
            index = int(filament.get("id", "0"))
        except ValueError:
            raise ValueError("Selected plate has an invalid logical filament ID") from None
        if not 1 <= index <= len(palette) or index in used_ids:
            raise ValueError("Selected plate has duplicate or out-of-palette filament IDs")
        used_ids.add(index)
        entry = palette[index-1]
        if str(filament.get("type", "")).upper() != entry["type"]:
            raise ValueError("Selected plate filament metadata is incompatible with the project palette")
        for key, field in (("color", "color"), ("tray_info_idx", "profile_id")):
            actual = filament.get(key)
            if entry[field] and (actual is not None or len(palette) > 1 or requests):
                actual = _color(actual) if field == "color" and actual else actual
                if actual != entry[field]:
                    raise ValueError(f"Selected plate filament {index} {field} differs from project/executable palette")
        if len(palette) > 1 and any(entry[field] is None for field in ("preset", "profile_id", "color")):
            raise ValueError("Multicolor plate requires complete product/profile/color metadata for every used filament")
    checks = _inspect_filament_selectors(text, used_ids, len(palette))
    return palette, sorted(used_ids), checks


def inspect_print(path: Path, printer: dict, plate: int = 1) -> dict:
    spec = get_model(printer.get("model"))
    printer = dict(printer, model=spec["id"])
    p = Path(path)
    if not isinstance(plate, int) or isinstance(plate, bool) or plate < 1:
        raise ValueError("Plate must use Bambu Studio's one-based plate index")
    if p.suffix.lower() != ".3mf":
        raise ValueError("Dispatch requires a sliced .3mf, not bare G-code")
    archive_info = classify_3mf(p)
    if archive_info["kind"] != "sliced" or plate not in archive_info["plates"]:
        raise ValueError("Requested plate has no sliced G-code")
    gname = f"Metadata/plate_{plate}.gcode"
    with checked_archive(p) as z:
        if "Metadata/project_settings.config" not in z.namelist() or "Metadata/slice_info.config" not in z.namelist():
            raise ValueError("Sliced file lacks printer/project or slice metadata")
        settings = json.loads(z.read("Metadata/project_settings.config"))
        gcode = z.read(gname)
        gtext = gcode.decode("utf-8", errors="strict")
        if len(gcode) < 100 or "G1" not in gtext or "EXECUTABLE_BLOCK_END" not in gtext:
            raise ValueError("Plate G-code is incomplete or not Bambu Studio executable output")
        md5_name = gname + ".md5"
        if md5_name not in z.namelist():
            raise ValueError("Plate has no G-code MD5")
        stored = z.read(md5_name).decode("ascii").strip().lower().split()[0]
        if stored != hashlib.md5(gcode).hexdigest():
            raise ValueError("Plate G-code checksum does not match its metadata")
        model = settings.get("printer_model")
        if normalize_model(model) != spec["id"]:
            raise ValueError(f"Prepared file targets {model!r}, not the selected printer")
        diameters = _numeric_values(settings.get("nozzle_diameter",0))
        nozzle = diameters[0]
        if len(diameters)!=spec["tool_count"] or any(d<=0 or abs(d-float(printer.get("nozzle_diameter",.4)))>.001 for d in diameters):
            raise ValueError("Prepared file nozzle diameter does not match the installed nozzle")
        nozzle_type = _first(settings.get("nozzle_type", ""))
        if nozzle_type and printer.get("nozzle_type") and nozzle_type != printer["nozzle_type"]:
            raise ValueError("Prepared file nozzle material does not match the installed nozzle")
        # Compare both project metadata and the actual selected executable plate.
        gsettings = _gcode_settings(gtext)
        if normalize_model(gsettings.get("printer_model")) != spec["id"]:
            raise ValueError("Project and executable plate disagree on printer model")
        executable_nozzles = _numeric_values(gsettings.get("nozzle_diameter","0"))
        if len(executable_nozzles)!=spec["tool_count"] or any(abs(d-nozzle)>.001 for d in executable_nozzles):
            raise ValueError("Project and executable plate disagree on nozzle diameter")
        executable_limits = _check_executable_limits(gtext, printer["model"])
        try:
            default_width = float(gsettings.get("line_width", nozzle)) or nozzle
        except ValueError:
            default_width = nozzle
        plate_type = settings.get("curr_bed_type")
        expected_plate = PLATES.get(printer.get("plate_type", ""))
        if expected_plate and plate_type != expected_plate:
            raise ValueError("Prepared file uses a different bed plate type")
        if expected_plate and gsettings.get("curr_bed_type") != expected_plate:
            raise ValueError("Executable G-code uses a different bed plate type")
        filament_types = _values(settings.get("filament_type"))
        actual_nozzle_types = [v.strip().strip('"') for v in re.split(r"[,;]",gsettings.get("nozzle_type", ""))]
        if nozzle_type and any(v!=nozzle_type for v in actual_nozzle_types):
            raise ValueError("Project and executable plate disagree on nozzle material")
        bed_json_name = f"Metadata/plate_{plate}.json"
        if bed_json_name not in z.namelist():
            raise ValueError("Selected plate lacks placement bounds metadata")
        placement = json.loads(z.read(bed_json_name))
        bbox = placement.get("bbox_all", [])
        if len(bbox) != 4 or not all(math.isfinite(float(x)) for x in bbox):
            raise ValueError("Selected plate has invalid placement bounds")
        global_workspace = _workspace(printer,settings)
        volume = global_workspace["volume_mm"]
        if bbox[0] < -.01 or bbox[1] < -.01 or bbox[2] > volume[0]+.01 or bbox[3] > volume[1]+.01:
            raise ValueError("Selected plate placement exceeds the selected model's build area")
        max_z = re.search(r"^;\s*max_z_height:\s*([^\s]+)", gtext, re.M)
        if not max_z or not math.isfinite(float(max_z.group(1))) or not 0 < float(max_z.group(1)) <= volume[2]+.01:
            raise ValueError("Selected plate model height exceeds the selected model's build height or is absent")
        root_data = z.read("Metadata/slice_info.config")
        if b"<!DOCTYPE" in root_data.upper() or b"<!ENTITY" in root_data.upper():
            raise ValueError("XML entities are not accepted")
        root = ET.fromstring(root_data)
        pnode = None
        for node in root.findall("plate"):
            meta = {x.attrib.get("key"): x.attrib.get("value") for x in node.findall("metadata")}
            if meta.get("index") == str(plate):
                pnode = node
                break
        if pnode is None:
            raise ValueError("Requested plate is absent from slice_info metadata")
        meta = {x.attrib.get("key"): x.attrib.get("value") for x in pnode.findall("metadata")}
        if meta.get("outside", "false") != "false":
            raise ValueError("Slicer reports objects outside the build area")
        if meta.get("nozzle_diameters") and any(abs(x-nozzle)>.001 for x in _numeric_values(meta["nozzle_diameters"])):
            raise ValueError("Selected plate nozzle metadata is incompatible")
        filaments = [dict(x.attrib) for x in pnode.findall("filament")]
        if meta.get("enable_filament_dynamic_map", "false") != "false" or pnode.find("mixed_filament") is not None:
            raise ValueError("Dynamic/virtual mixed filament mappings are not implemented")
        palette, used_filament_ids, selector_checks = _inspect_palette(settings, gsettings, filaments, gtext, printer)
        executable_limits["filament_selectors"] = selector_checks
        nozzles = [dict(x.attrib) for x in pnode.findall("nozzle")]
        requested_flow = _flow(printer.get("nozzle_volume_type", "Standard"))
        volume_types = _values(settings.get("nozzle_volume_type"))
        executable_flows = _values(gsettings.get("nozzle_volume_type"))
        if requested_flow == "HighFlow" and (not volume_types or not executable_flows or not nozzles):
            raise ValueError("High-flow plate lacks explicit project/executable/used-nozzle variant metadata")
        if volume_types and (len(volume_types) != spec["tool_count"] or any(_flow(value) != requested_flow for value in volume_types)):
            raise ValueError("Prepared high-flow/nozzle variant does not match the installed nozzle flow")
        if executable_flows and (len(executable_flows) != spec["tool_count"] or any(_flow(value) != requested_flow for value in executable_flows)):
            raise ValueError("Executable nozzle high-flow/variant differs from the installed nozzle flow")
        if any(_flow(node.get("volume_type", "Standard")) != requested_flow for node in nozzles + filaments):
            raise ValueError("Selected plate nozzle/filament high-flow variant is incompatible")
        expected_variant = "Direct Drive High Flow" if requested_flow == "HighFlow" else "Direct Drive Standard"
        if requested_flow == "HighFlow" and _values(gsettings.get("printer_extruder_variant")) != [expected_variant]:
            raise ValueError("Executable high-flow extruder variant was not resolved to the matching installed stock variant")
        if spec["tool_count"]>1:
            if len(nozzles)!=1:
                raise ValueError("Multiple/unknown used-tool jobs are not implemented; dual-model preparation supports one used tool and one material")
            used_tool = int(nozzles[0].get("extruder_id","0"))
            mapping = [str(v) for v in settings.get("filament_map",[])]
            nozzle_mapping = [str(v) for v in settings.get("filament_nozzle_map",mapping)]
            if mapping!=[str(used_tool)] * len(palette) or nozzle_mapping!=mapping:
                raise ValueError("Dual-tool sliced metadata and filament-to-nozzle mappings disagree")
            if requested_flow != "Standard":
                raise ValueError("Dual-tool high-flow/variant preparation is not implemented; use matching stock Standard nozzles")
        else:
            used_tool = 1
            if len(nozzles)>1 or any(int(n.get("extruder_id","1"))!=1 for n in nozzles):
                raise ValueError("A single-tool printer cannot accept a multi-tool plate")
            for key in ("filament_map", "filament_nozzle_map"):
                mapping = _values(settings.get(key))
                if mapping and (len(mapping) != len(palette) or any(value != "1" for value in mapping)):
                    raise ValueError("Single-tool filament-to-physical-nozzle mapping is incompatible")
        workspace = _workspace(printer,settings,used_tool)
        extrusion_bounds = _whole_print_extrusion_bounds(gtext,nozzle,default_width,workspace=workspace)
        executable_limits["whole_print_extrusion"] = extrusion_bounds
        custom = printer.get("nozzle") or {}
        if custom.get("custom"):
            cap, limit = float(custom.get("max_volumetric_speed", 0)), float(custom.get("max_temperature_c", 0))
            if custom.get("stock_geometry_compatible") is not True or not math.isfinite(cap) or not 0 < cap <= 100 or not math.isfinite(limit) or not 0 < limit <= spec["nozzle_max_c"]:
                raise ValueError("Custom nozzle has no confirmed geometry/calibrated limits")
            flows = _numeric_values(settings.get("filament_max_volumetric_speed", 0))
            actual_flows = _numeric_values(gsettings.get("filament_max_volumetric_speed", 0))
            if any(value <= 0 or value > cap + 1e-6 for value in flows + actual_flows):
                raise ValueError("Prepared file exceeds the custom nozzle calibrated volumetric flow cap")
            if executable_limits["maximum_nozzle_target_c"] > limit:
                raise ValueError("Executable nozzle target exceeds the custom nozzle temperature limit")
            from .flow import validate_extrusion_flow
            filament_diameters = _numeric_values(settings.get("filament_diameter", [1.75]))
            if any(abs(value - 1.75) > .05 for value in filament_diameters):
                raise ValueError("Custom-nozzle flow validation requires nominal 1.75 mm filament")
            executable_limits["custom_nozzle_extrusion_flow"] = validate_extrusion_flow(gtext, cap, max(filament_diameters))
            # Vendor purge/wipe commands also accept nozzle targets outside M104/M109.
            for line in gtext.splitlines():
                code = line.split(";", 1)[0].strip()
                if re.match(r"^(?:M620\.10|G150)\b", code, re.I):
                    for token in re.findall(r"(?:^|\s)[TP]\s*(-?\d+(?:\.\d+)?)", code, re.I):
                        if float(token) > limit:
                            raise ValueError("Vendor purge/wipe target exceeds the custom nozzle temperature limit")
                if re.match(r"^M620\.10\b", code, re.I):
                    feed = re.search(r"(?:^|\s)F\s*(-?\d+(?:\.\d+)?)", code, re.I)
                    if feed and float(feed[1]) * 2.4053 / 60 > cap + .01:
                        raise ValueError("Executable purge flow exceeds the custom nozzle calibrated volumetric flow cap")
        warnings = [dict(x.attrib) for x in pnode.findall("warning")]
        # Block explicit error severity, while retaining informational/quality warnings.
        blocking = [x for x in warnings if x.get("level") in ("0", "1")]
        if blocking:
            raise ValueError(f"Slicer reports blocking warnings: {blocking}")
        version = re.search(r"; BambuStudio\s+([^\r\n]+)", gtext)
        estimates = {"time_seconds": float(meta["prediction"]) if meta.get("prediction") else None,
                     "filament_grams": float(meta["weight"]) if meta.get("weight") else None,
                     "source": "slicer estimates"}
        return {"path": str(p.resolve()), "sha256": sha256(p), "gcode_sha256": hashlib.sha256(gcode).hexdigest(),
                "gcode_md5": stored, "gcode_path": gname, "plate": plate, "model": printer["model"],
                "nozzle_diameter": nozzle, "nozzle_diameters":diameters,"nozzle_type": nozzle_type,"used_tool_ids":[used_tool],
                "tool_count":spec["tool_count"],"launch_supported":spec["launch_supported"],"plate_type": plate_type,
                "material": filament_types, "materials": palette, "palette": palette,
                "used_filament_ids": used_filament_ids, "filaments": filaments,
                "nozzle_volume_type": requested_flow, "slicer_version": version.group(1) if version else "unknown",
                "estimates": estimates, "warnings": warnings, "objects": [dict(x.attrib) for x in pnode.findall("object")],
                "available_plates": archive_info["plates"], "settings": {k: settings.get(k) for k in (
                    "printer_settings_id", "print_settings_id", "filament_settings_id", "layer_height", "wall_loops",
                    "sparse_infill_density", "sparse_infill_pattern", "top_shell_layers", "bottom_shell_layers",
                    "enable_support", "support_type", "curr_bed_type", "nozzle_temperature",
                    "nozzle_temperature_initial_layer", "textured_plate_temp", "textured_plate_temp_initial_layer")},
                "executable_checks": executable_limits, "placement_bounds_mm": bbox,
                "placement_bounds_source": "slicer first-layer metadata; whole-print extrusion checked separately",
                "whole_print_bounds_mm": extrusion_bounds["whole_print_bounds_mm"],
                "extrusion_path_bounds_mm": extrusion_bounds["extrusion_path_bounds_mm"],
                "model_height_mm": float(max_z.group(1))}


def _write_project(sliced: Path, project: Path):
    """Preserve the slicer's editable geometry and settings without executable plates."""
    with checked_archive(sliced) as zin, zipfile.ZipFile(project, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            n = info.filename
            if n.endswith((".gcode", ".gcode.md5")) or n == "Metadata/slice_info.config":
                continue
            zout.writestr(info, zin.read(n))


def slice_model(source: Path, output_dir: Path, printer: dict, slicer: dict, progress=None) -> dict:
    spec = get_model(printer.get("model"))
    printer = dict(printer,model=spec["id"])
    printer.setdefault("nozzle_type",spec["default_nozzle_type"])
    support_mode = slicer.get("support_mode", "native")
    if support_mode == "auto":
        support_mode = "native"
    if support_mode not in {"native", "baked_fins", "manual"}:
        raise ValueError("support_mode must be native, baked_fins or manual")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    source = Path(source)
    if source.suffix.lower() == ".3mf" and classify_3mf(source)["kind"] == "sliced":
        result = inspect_print(source, printer, int(slicer.get("plate", 1)))
        destination = out / "prepared.gcode.3mf"
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
        project = out / "project.3mf"
        _write_project(destination, project)
        result.update({"print_path": str(destination.resolve()), "project_path": str(project.resolve()),
                       "source_kind": "sliced", "profiles": {}, "profile_provenance": "source sliced file settings",
                       "support_mode": support_mode})
        return result
    if support_mode == "manual":
        raise ValueError("Manual supports require the original Bambu Studio project; retain support painting/volumes there, slice, then import the exported sliced .gcode.3mf")
    if source.suffix.lower() == ".3mf":
        source_info = classify_3mf(source)
        if source_info["requires_preservation"]:
            raise ValueError("3MF contains " + ", ".join(source_info["preservation_reasons"]) +
                             "; retain these in Bambu Studio and import its sliced .gcode.3mf. Automatic single-mesh conversion would lose them")
    exe, paths = _profile_paths(printer, slicer)
    profiles = {k: resolve_profile(paths[k], paths["root"]) for k in ("machine", "process", "filament")}
    _validate_profiles(profiles, printer)
    printer["build_volume_mm"] = build_volume_for(printer,profiles["machine"])
    project_settings = _project_overrides(source, profiles, printer, slicer)
    report = normalized_stl(source, out / "normalized-mm.stl", printer)
    plate_type = PLATES.get(printer.get("plate_type", ""))
    if not plate_type:
        raise ValueError("Configure a plate_type before preparing a printer-specific file")
    model_data = json.loads(paths["model"].read_text(encoding="utf-8-sig")) if paths["model"] else {}
    unsupported_plates = model_data.get("not_support_bed_type", "").split(";")
    aliases = {"High Temp Plate":"Smooth PEI Plate / High Temp Plate"}
    if plate_type in unsupported_plates or aliases.get(plate_type) in unsupported_plates:
        raise ValueError("This plate type is not supported by the exact installed machine model")
    profiles["machine"]["curr_bed_type"] = plate_type
    profiles["machine"]["printer_model_id"] = spec["bambu_model_id"]
    profiles["process"]["curr_bed_type"] = plate_type
    # Preserve the user-chosen orientation; center placement and lift to bed only.
    if project_settings["policy"] == "configured profiles":
        profiles["process"]["enable_support"] = "1" if report["support_recommended"] else "0"
    if slicer.get("profiles", {}).get("process"):
        # An explicit process profile is intentional and owns support settings.
        profiles["process"] = resolve_profile(paths["process"], paths["root"])
        profiles["process"]["curr_bed_type"] = plate_type
    process_overrides = validate_process_overrides(slicer.get("process_overrides"), printer, profiles["machine"])
    profiles["process"].update(process_overrides)
    if support_mode == "baked_fins":
        if process_overrides.get("enable_support") == "1":
            raise ValueError("baked_fins support mode cannot enable native supports; review the fin contacts in layer preview")
        profiles["process"]["enable_support"] = "0"
    requests = _requested_materials(printer)
    if requests and requests[0].get("color"):
        profiles["filament"]["filament_colour"] = ["#" + requests[0]["color"]]
    _configure_nozzle(profiles, printer)
    generated = {}
    for kind, values in profiles.items():
        dest = out / f"resolved-{kind}.json"
        dest.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
        generated[kind] = str(dest.resolve())
    final = out / "prepared.gcode.3mf"
    args = [str(exe), "--datadir", str((out / "studio-data").resolve()),
            "--slice", "1", "--outputdir", str(out.resolve()), "--export-3mf", final.name,
            "--load-settings", generated["machine"] + ";" + generated["process"],
            "--load-filaments", generated["filament"], "--load-defaultfila",
            "--ensure-on-bed", "--orient", "0", "--arrange", "1", str((out / "normalized-mm.stl").resolve())]
    log_path = out / "slicer.log"
    (out / "slicer-command.json").write_text(json.dumps(args, indent=2), encoding="utf-8")
    if progress:
        progress("slicing", {"executable": exe.name, "plate": 1})
    start = time.monotonic()
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with log_path.open("wb") as log:
        proc = subprocess.Popen(args, cwd=out, stdout=log, stderr=subprocess.STDOUT, **kwargs)
        try:
            code = proc.wait(timeout=float(slicer.get("timeout_seconds", 900)))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise RuntimeError(f"Slicer timed out; artifacts retained in {out}") from None
    result_path = out/"result.json"
    try:
        cli_result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    except (OSError,ValueError):
        cli_result = {}
    if code != 0 or not final.is_file():
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        reason = str(cli_result.get("error_string", ""))[:1600]
        raise RuntimeError(f"Slicer failed (exit {code}, output present={final.is_file()}): {reason} {tail}")
    result = inspect_print(final, printer, 1)
    project = out / "project.3mf"
    _write_project(final, project)
    result.update({"print_path": str(final.resolve()), "project_path": str(project.resolve()),
                   "geometry": report, "source_kind": "geometry" if source.suffix.lower() == ".stl" else classify_3mf(source)["kind"],
                   "profiles": {k: {"source": str(paths[k]), "source_sha256": sha256(paths[k]),
                                      "resolved": generated[k], "resolved_sha256": sha256(Path(generated[k])),
                                      "name": profiles[k].get("name")} for k in generated},
                   "elapsed_seconds": round(time.monotonic() - start, 3), "slicer_executable": str(exe.resolve()),
                   "slicer_executable_sha256": sha256(exe), "log_path": str(log_path.resolve()),
                   "source_project_settings": project_settings,
                   "slicer_result_path":str(result_path.resolve()) if result_path.is_file() else None,
                   "process_overrides": process_overrides,
                   "support_mode": support_mode,
                   "model_support":{"model":spec["id"],"preparation_supported":True,
                                    "launch_supported":spec["launch_supported"],"hardware_verified":spec["hardware_verified"],
                                    "limitations":spec["limitations"]},
                   "orientation_policy": "retain orientation and dimensions; center placement, translate to bed; conservative support for elevated downward faces"})
    result["warnings"].extend(report["warnings"])
    return result
