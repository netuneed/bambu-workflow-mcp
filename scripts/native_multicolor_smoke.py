"""Opt-in native multicolor acceptance for an isolated CI runner. Never contacts a printer.

Uses two locally generated STL bodies, not an edited Bambu project. Do not run this
against a user's interactive Studio session. Profiles come from that runner's own
official Studio installation; publish the report and log, not the resolved presets.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import time
import zipfile

import trimesh

from bambu_workflow.geometry import analyze_mesh, sha256
from bambu_workflow.slicer import (
    PLATES, _configure_nozzle, _profile_paths, _validate_profiles,
    discover_slicer_executable, inspect_print, resolve_profile, slice_model,
)


def prepare_fixture(executable: Path, output: Path) -> tuple[list[str], dict, dict]:
    """Write owned geometry and installed presets, without launching any application."""
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    studio_data = output / "studio-data"
    studio_data.mkdir()
    first = {"filament_id": 1, "type": "PETG", "preset": "Bambu PETG HF",
             "profile_id": "GFG02", "color": "FF0000FF"}
    printer = {"model": "p2s", "nozzle_diameter": .4, "nozzle_type": "hardened_steel",
               "nozzle_volume_type": "Standard", "plate_type": "textured_plate", "material": "PETG",
               "materials": [first]}
    _, paths = _profile_paths(printer, {"executable": str(executable)})
    profiles = {kind: resolve_profile(paths[kind], paths["root"]) for kind in ("machine", "process", "filament")}
    _validate_profiles(profiles, printer)
    _configure_nozzle(profiles, printer)
    plate = PLATES[printer["plate_type"]]
    profiles["machine"]["curr_bed_type"] = plate
    profiles["process"].update(curr_bed_type=plate, enable_support="0", enable_prime_tower="1",
                               prime_tower_width="20", print_sequence="by layer",
                               flush_volumes_matrix=["0", "200", "200", "0"])
    generated = {}
    for kind in ("machine", "process"):
        path = output / f"resolved-{kind}.json"
        path.write_text(json.dumps(profiles[kind], indent=2), encoding="utf-8")
        generated[kind] = path
    sources, filaments = [], []
    source_reports = []
    for index, (name, color) in enumerate((("red", "FF0000FF"), ("blue", "0000FFFF")), 1):
        # Independent watertight bodies force both logical colors on all six layers.
        body = trimesh.creation.box((6, 12, 1.2))
        body.apply_translation((0, 0, .6))
        source = output / f"owned-{name}-body.stl"
        body.export(source)
        source_reports.append(analyze_mesh(source, printer))
        sources.append(source)
        filament = copy.deepcopy(profiles["filament"])
        filament["filament_colour"] = ["#" + color]
        path = output / f"resolved-filament-{index}.json"
        path.write_text(json.dumps(filament, indent=2), encoding="utf-8")
        filaments.append(path)
    printer["materials"] = [first, {**first, "filament_id": 2, "color": "0000FFFF"}]
    final = output / "multicolor.gcode.3mf"
    command = [str(executable.resolve()), "--datadir", str(studio_data), "--slice", "1",
               "--outputdir", str(output), "--export-3mf", final.name,
               "--load-settings", ";".join(str(generated[kind]) for kind in ("machine", "process")),
               "--load-filaments", ";".join(str(path) for path in filaments), "--load-defaultfila",
               "--load-filament-ids", "1,2", "--allow-multicolor-oneplate",
               "--filament-colour", "#FF0000FF;#0000FFFF", "--ensure-on-bed", "--orient", "0", "--arrange", "1",
               *(str(source) for source in sources)]
    provenance = {"sources": source_reports, "profile_sources": {
        kind: {"name": profiles[kind].get("name"), "sha256": sha256(paths[kind])}
        for kind in profiles}, "isolation_directory": str(studio_data), "timeout_seconds": 120}
    return command, printer, provenance


def run_native(command: list[str], output: Path) -> dict:
    """Run once, with a bounded wait. Termination is limited to the child we created."""
    log_path = output / "native-multicolor-slicer.log"
    (output / "native-multicolor-command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    started = time.monotonic()
    process_path = output / "native-multicolor-process.json"
    with log_path.open("wb") as log:
        child = subprocess.Popen(command, cwd=output, stdout=log, stderr=subprocess.STDOUT, **kwargs)
        process_path.write_text(json.dumps({"pid": child.pid, "state": "running", "timeout_seconds": 120}, indent=2), encoding="utf-8")
        try:
            code = child.wait(timeout=120)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=15)
            process_path.write_text(json.dumps({"pid": child.pid, "state": "timed_out", "timeout_seconds": 120,
                                               "only_own_child_terminated": True}, indent=2), encoding="utf-8")
            raise RuntimeError(f"Native multicolor test timed out after 120 seconds; only its child PID {child.pid} was terminated") from None
    result = {"pid": child.pid, "exit_code": code, "elapsed_seconds": round(time.monotonic()-started, 3),
              "log_path": str(log_path)}
    process_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if code:
        raise RuntimeError(f"Native multicolor test failed (PID {child.pid}, exit {code}); no retry was attempted. See {log_path}")
    return result


def validate_result(output: Path, printer: dict) -> dict:
    final = output / "multicolor.gcode.3mf"
    checked = inspect_print(final, printer)
    if checked["used_filament_ids"] != [1, 2] or checked["used_tool_ids"] != [1] or len(checked["palette"]) != 2:
        raise RuntimeError("Native result did not preserve two used logical filaments on one physical nozzle")
    with zipfile.ZipFile(final) as archive:
        text = archive.read(checked["gcode_path"]).decode("utf-8")
        settings = json.loads(archive.read("Metadata/project_settings.config"))
    selectors = {int(match[1]) for match in re.finditer(r"(?m)^\s*T(\d+)\s*(?:;[^\r\n]*)?$", text)}
    if not {0, 1}.issubset(selectors):
        raise RuntimeError("Executable output has no actual T0 and T1 selectors")
    features = checked["executable_checks"]["whole_print_extrusion"]["features"]
    if not any(value.casefold() in {"prime tower", "wipe tower"} for value in features):
        raise RuntimeError("Native output lacks a deposited prime tower")
    if len([obj for obj in checked["objects"] if obj.get("skipped", "false") == "false"]) != 2:
        raise RuntimeError("Native output did not preserve both owned bodies")
    matrix = settings.get("flush_volumes_matrix", [])
    if len(matrix) != 4 or any(not math.isfinite(float(value)) or float(value) < 0 for value in matrix):
        raise RuntimeError("Native result has an invalid two-color flushing matrix")
    imported = slice_model(final, output / "byte-exact-import", printer, {"support_mode": "manual"})
    if final.read_bytes() != Path(imported["print_path"]).read_bytes():
        raise RuntimeError("Sliced import changed the executable archive bytes")
    return {"inspection": checked, "actual_logical_T_selectors": sorted(selectors),
            "two_bodies_preserved": True, "prime_tower_deposition_checked": True,
            "full_extrusion_bounds_checked": True, "byte_exact_import": True,
            "print_sha256": sha256(final), "import_sha256": imported["sha256"], "print_path": str(final)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, help="Official Bambu Studio executable or .app bundle on an isolated runner.")
    parser.add_argument("--output-dir", required=True, type=Path, help="New or empty directory for test artifacts.")
    args = parser.parse_args()
    executable = discover_slicer_executable(args.executable)
    if executable is None:
        parser.error("Official Bambu Studio executable was not found")
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a new or empty output directory so stale files cannot satisfy acceptance")
    command, printer, provenance = prepare_fixture(executable, output)
    process = run_native(command, output)
    result = validate_result(output, printer)
    report = {"platform": platform.platform(), "architecture": platform.machine(), "printer_contacted": False,
              "slicer_executable_sha256": sha256(executable), "provenance": provenance, "process": process, **result}
    path = output / "native-multicolor-smoke.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "print_sha256": report["print_sha256"],
                      "used_filament_ids": result["inspection"]["used_filament_ids"], "printer_contacted": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
