"""Bambu Studio CLI adapter using installed, resolved machine presets."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile

from .geometry import analyze_mesh, checked_archive, classify_3mf, normalized_stl, sha256

PLATES = {"textured_plate": "Textured PEI Plate", "hot_plate": "High Temp Plate",
          "supertack_plate": "SuperTack Plate", "cool_plate": "Cool Plate",
          "engineering_plate": "Engineering Plate"}
MODELS = {"a1mini": "Bambu Lab A1 mini"}


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def _number(v):
    return float(_first(v))


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
    exe = Path(config.get("path") or config.get("executable") or "")
    if not exe.is_file():
        detected = shutil.which("bambu-studio") or shutil.which("bambu-studio.exe")
        if not detected:
            raise ValueError("Configure the installed Bambu Studio executable in slicer.path")
        exe = Path(detected)
    root = Path(config.get("profile_root") or exe.parent / "resources" / "profiles" / "BBL")
    nozzle = str(float(printer.get("nozzle_diameter", .4)))
    if printer.get("model") != "a1mini":
        raise ValueError("Automatic slicing is currently implemented only for A1 mini")
    material = str(printer.get("material") or config.get("material") or "").upper()
    if material not in ("PETG", "PLA", "TPU"):
        raise ValueError("Configure a confirmed material or supply explicit filament profile and material")
    profiles = config.get("profiles", {})
    machine = Path(profiles.get("machine") or root / "machine" / f"Bambu Lab A1 mini {nozzle} nozzle.json")
    if nozzle == ".4" or nozzle == "0.4":
        process_name = "0.20mm Standard @BBL A1M"
    elif nozzle == "0.2":
        process_name = "0.10mm Standard @BBL A1M 0.2 nozzle"
    elif nozzle == "0.6":
        process_name = "0.30mm Standard @BBL A1M 0.6 nozzle"
    else:
        process_name = "0.40mm Standard @BBL A1M 0.8 nozzle"
    process = Path(profiles.get("process") or root / "process" / f"{process_name}.json")
    filament_name = f"Generic {material} @BBL A1M" + (" 0.2 nozzle" if nozzle == "0.2" else "")
    filament = Path(profiles.get("filament") or root / "filament" / f"{filament_name}.json")
    for p in (machine, process, filament):
        if not p.is_file():
            raise ValueError(f"Required installed profile is missing: {p.name}")
    return exe, {"root": root, "machine": machine, "process": process, "filament": filament}


def _validate_profiles(profiles: dict, printer: dict):
    machine, process, filament = (profiles[k] for k in ("machine", "process", "filament"))
    if machine.get("printer_model") != MODELS.get(printer.get("model")):
        raise ValueError("Machine profile model does not match the selected printer")
    if abs(_number(machine.get("nozzle_diameter", 0)) - float(printer.get("nozzle_diameter", .4))) > .001:
        raise ValueError("Machine profile nozzle diameter does not match the installed nozzle")
    for kind, profile in (("process", process), ("filament", filament)):
        compatible = profile.get("compatible_printers", [])
        if compatible and machine.get("name") not in compatible:
            raise ValueError(f"{kind.title()} profile is not compatible with the selected machine preset")
    material = str(printer.get("material", "")).upper()
    if material and str(_first(filament.get("filament_type", ""))).upper() != material:
        raise ValueError("Filament profile material does not match the configured material")
    if not machine.get("machine_start_gcode") or "A1 mini" not in machine.get("machine_start_gcode", ""):
        raise ValueError("A1 mini startup G-code was not resolved from the installed preset")


def validate_process_overrides(overrides: dict | None, printer: dict, machine: dict | None = None) -> dict:
    """Normalize a small per-job process whitelist without accepting machine G-code."""
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError("process_overrides must be an object")
    allowed = {"layer_height", "wall_loops", "sparse_infill_density", "sparse_infill_pattern",
               "top_shell_layers", "bottom_shell_layers", "enable_support"}
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
        elif key == "enable_support":
            if type(value) is bool:
                result[key] = "1" if value else "0"
            elif value in ("0", "1"):
                result[key] = value
            else:
                raise ValueError("enable_support must be a boolean or the string 0 or 1")
    return result


def _project_overrides(source: Path, profiles: dict, printer: dict, config: dict) -> dict:
    """Retain compatible global process/material settings, refusing lossy object overrides."""
    if source.suffix.lower() != ".3mf" or classify_3mf(source)["kind"] != "project":
        return {"policy": "configured profiles"}
    with checked_archive(source) as z:
        values = json.loads(z.read("Metadata/project_settings.config"))
        if values.get("printer_model") != MODELS[printer["model"]]:
            raise ValueError("Source 3MF project targets a different printer model")
        if abs(_number(values.get("nozzle_diameter", 0)) - float(printer["nozzle_diameter"])) > .001:
            raise ValueError("Source 3MF project targets a different nozzle diameter")
        materials = values.get("filament_type", [])
        if len(materials) != 1 or str(materials[0]).upper() != str(printer["material"]).upper():
            raise ValueError("Source 3MF project needs a different or multiple materials")
        if "Metadata/model_settings.config" in z.namelist():
            xml = z.read("Metadata/model_settings.config")
            if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                raise ValueError("XML entities are not accepted")
            tree = ET.fromstring(xml)
            for part in tree.iter("part"):
                if part.attrib.get("subtype", "normal_part") != "normal_part":
                    raise ValueError("3MF modifier/negative/support volumes cannot be retained by single-mesh preparation")
            for object_node in list(tree.iter("object")) + list(tree.iter("part")):
                for meta in object_node.findall("metadata"):
                    key, value = meta.attrib.get("key"), meta.attrib.get("value")
                    if key == "extruder" and value not in (None, "0", "1"):
                        raise ValueError("3MF per-object material assignment is unsupported")
                    if key in profiles["process"] and key not in {"name", "type", "from"}:
                        raise ValueError(f"3MF per-object setting {key!r} cannot be retained by single-mesh preparation")
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


def _check_executable_limits(text: str, model: str) -> dict:
    if model != "a1mini":
        raise ValueError("Executable limits are not implemented for this model")
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
            maximum = 300 if kind == "nozzle" else 80
            params = re.findall(r"(?:^|\s)([SRH])\s*([^\s]+)", heat.group(2), re.I)
            for _, token in params:
                try:
                    temp = float(token)
                except ValueError:
                    raise ValueError(f"Non-numeric {kind} temperature in executable line {number}") from None
                if not math.isfinite(temp) or not 0 <= temp <= maximum:
                    raise ValueError(f"Executable {kind} target {token} exceeds A1 mini limit 0-{maximum} C (line {number})")
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
                if not math.isfinite(value) or not -60 <= value <= 260:
                    raise ValueError(f"Extreme {axis} coordinate {token} in executable line {number}")
    if not moves or not targets["bed"] or not targets["nozzle"]:
        raise ValueError("Executable plate lacks motion or explicit nozzle/bed temperature targets")
    return {"checked_move_lines": moves,
            "maximum_nozzle_target_c": max(targets["nozzle"]), "maximum_bed_target_c": max(targets["bed"]),
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


def _arc_xy_extrema(start: dict, end: dict, params: dict, clockwise: bool, absolute_center: bool) -> list[tuple[float, float]]:
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
    points = [(sx, sy), (ex, ey)]
    for a in (0, math.pi/2, math.pi, 3*math.pi/2):
        crossed = (first-a) % tau if clockwise else (a-first) % tau
        if crossed <= delta + 1e-9:
            points.append((cx+radius*math.cos(a), cy+radius*math.sin(a)))
    return points


def _whole_print_extrusion_bounds(text: str, nozzle_diameter: float, default_width: float | None = None) -> dict:
    """Bound actual deposited moves after layer markers, separately from startup/wipe G-code.

    Supports absolute/relative XYZ and E, G92 E, G17 I/J or R arcs, and
    absolute/relative arc centers. Width is the slicer's per-move declaration.
    This is bounded toolpath inspection, not complete vendor-G-code simulation.
    """
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
                    if deposited_low[0] < -.01 or deposited_low[1] < -.01 or deposited_high[0] > 180.01 or deposited_high[1] > 180.01:
                        raise ValueError(f"Whole printed extrusion exceeds A1 mini XY build area (line {line_number})")
                    if zlo < -.01 or zhi > 180.01:
                        raise ValueError(f"Whole printed extrusion exceeds A1 mini Z build height (line {line_number})")
            position = end
    if not moves:
        raise ValueError("No model extrusion moves were found after recognized layer/feature markers")
    if not all(math.isfinite(v) for v in first_layer_xy):
        raise ValueError("No model/raft/brim deposition was found on the first layer")
    return {"extrusion_path_bounds_mm": [low, high], "whole_print_bounds_mm": [deposited_low, deposited_high],
            "first_layer_extrusion_bounds_xy_mm": first_layer_xy, "layers": layers,
            "checked_extrusion_moves": moves, "checked_extrusion_arcs": arcs,
            "features": sorted(features), "ignored_custom_extrusions_after_layer_marker": ignored_custom,
            "xy_envelope": "conservative per-move half line width around path and analytic arc extrema",
            "startup_policy": "model deposition after layer markers; explicit Custom feature excluded"}


def inspect_print(path: Path, printer: dict, plate: int = 1) -> dict:
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
        if model != MODELS.get(printer.get("model")):
            raise ValueError(f"Prepared file targets {model!r}, not the selected printer")
        nozzle = _number(settings.get("nozzle_diameter", 0))
        if not math.isfinite(nozzle) or nozzle <= 0 or abs(nozzle - float(printer.get("nozzle_diameter", .4))) > .001:
            raise ValueError("Prepared file nozzle diameter does not match the installed nozzle")
        nozzle_type = _first(settings.get("nozzle_type", ""))
        if nozzle_type and printer.get("nozzle_type") and nozzle_type != printer["nozzle_type"]:
            raise ValueError("Prepared file nozzle material does not match the installed nozzle")
        # Compare both project metadata and the actual selected executable plate.
        gsettings = _gcode_settings(gtext)
        if gsettings.get("printer_model") != model:
            raise ValueError("Project and executable plate disagree on printer model")
        executable_nozzle = float(gsettings.get("nozzle_diameter", "0").split(",")[0])
        if not math.isfinite(executable_nozzle) or abs(executable_nozzle - nozzle) > .001:
            raise ValueError("Project and executable plate disagree on nozzle diameter")
        executable_limits = _check_executable_limits(gtext, printer["model"])
        try:
            default_width = float(gsettings.get("line_width", nozzle)) or nozzle
        except ValueError:
            default_width = nozzle
        extrusion_bounds = _whole_print_extrusion_bounds(gtext, nozzle, default_width)
        executable_limits["whole_print_extrusion"] = extrusion_bounds
        plate_type = settings.get("curr_bed_type")
        expected_plate = PLATES.get(printer.get("plate_type", ""))
        if expected_plate and plate_type != expected_plate:
            raise ValueError("Prepared file uses a different bed plate type")
        if expected_plate and gsettings.get("curr_bed_type") != expected_plate:
            raise ValueError("Executable G-code uses a different bed plate type")
        filament_types = settings.get("filament_type", [])
        if not isinstance(filament_types, list):
            filament_types = [filament_types]
        material = str(printer.get("material", "")).upper()
        if material and any(str(x).upper() != material for x in filament_types):
            raise ValueError("Prepared file uses a different filament material")
        actual_materials = [x.strip().strip('"') for x in gsettings.get("filament_type", "").split(";")]
        if material and any(x.upper() != material for x in actual_materials):
            raise ValueError("Executable G-code uses a different filament material")
        actual_nozzle_type = gsettings.get("nozzle_type", "").strip('"')
        if nozzle_type and actual_nozzle_type != nozzle_type:
            raise ValueError("Project and executable plate disagree on nozzle material")
        bed_json_name = f"Metadata/plate_{plate}.json"
        if bed_json_name not in z.namelist():
            raise ValueError("Selected plate lacks placement bounds metadata")
        placement = json.loads(z.read(bed_json_name))
        bbox = placement.get("bbox_all", [])
        if len(bbox) != 4 or not all(math.isfinite(float(x)) for x in bbox):
            raise ValueError("Selected plate has invalid placement bounds")
        if bbox[0] < -.01 or bbox[1] < -.01 or bbox[2] > 180.01 or bbox[3] > 180.01:
            raise ValueError("Selected plate placement exceeds A1 mini 180x180 mm build area")
        max_z = re.search(r"^;\s*max_z_height:\s*([^\s]+)", gtext, re.M)
        if not max_z or not math.isfinite(float(max_z.group(1))) or not 0 < float(max_z.group(1)) <= 180.01:
            raise ValueError("Selected plate model height exceeds A1 mini build height or is absent")
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
        if meta.get("nozzle_diameters") and any(abs(float(x) - nozzle) > .001 for x in meta["nozzle_diameters"].split(";")):
            raise ValueError("Selected plate nozzle metadata is incompatible")
        filaments = [dict(x.attrib) for x in pnode.findall("filament")]
        if len(filaments) != 1:
            raise ValueError("Current dispatch supports a single material/filament per plate")
        if material and str(filaments[0].get("type", "")).upper() != material:
            raise ValueError("Selected plate filament metadata is incompatible")
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
                "nozzle_diameter": nozzle, "nozzle_type": nozzle_type, "plate_type": plate_type,
                "material": filament_types, "filaments": filaments, "slicer_version": version.group(1) if version else "unknown",
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
                       "source_kind": "sliced", "profiles": {}, "profile_provenance": "source sliced file settings"})
        return result
    report = normalized_stl(source, out / "normalized-mm.stl", printer)
    exe, paths = _profile_paths(printer, slicer)
    profiles = {k: resolve_profile(paths[k], paths["root"]) for k in ("machine", "process", "filament")}
    _validate_profiles(profiles, printer)
    project_settings = _project_overrides(source, profiles, printer, slicer)
    plate_type = PLATES.get(printer.get("plate_type", ""))
    if not plate_type:
        raise ValueError("Configure a plate_type before preparing a printer-specific file")
    if printer["plate_type"] in ("cool_plate", "engineering_plate"):
        raise ValueError("This plate type is not supported by the installed A1 mini machine model")
    profiles["machine"]["nozzle_type"] = [printer.get("nozzle_type", "stainless_steel")]
    profiles["machine"]["curr_bed_type"] = plate_type
    profiles["machine"]["printer_model_id"] = "N1"
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
    generated = {}
    for kind, values in profiles.items():
        dest = out / f"resolved-{kind}.json"
        dest.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
        generated[kind] = str(dest.resolve())
    final = out / "prepared.gcode.3mf"
    args = [str(exe), "--slice", "1", "--outputdir", str(out.resolve()), "--export-3mf", final.name,
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
    if code != 0 or not final.is_file():
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"Slicer failed (exit {code}, output present={final.is_file()}): {tail}")
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
                   "process_overrides": process_overrides,
                   "orientation_policy": "retain orientation and dimensions; center placement, translate to bed; conservative support for elevated downward faces"})
    result["warnings"].extend(report["warnings"])
    return result
