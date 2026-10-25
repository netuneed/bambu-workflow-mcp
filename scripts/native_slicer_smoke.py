"""Slice the bundled coupon with an installed official CLI. Never connects to a printer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform

from bambu_workflow.geometry import sha256
from bambu_workflow.slicer import discover_slicer_executable, inspect_print, slice_model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, help="Official Bambu Studio executable or .app bundle.")
    parser.add_argument("--output-dir", type=Path, required=True, help="New/empty directory for all generated files.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "examples" / "acceptance-coupon-12x12x1.2mm.stl")
    args = parser.parse_args()
    executable = discover_slicer_executable(args.executable)
    if executable is None:
        parser.error("The official Bambu Studio executable was not found.")
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a new/empty output directory so old output cannot satisfy the smoke test.")
    source = args.source.resolve()
    printer = {"model": "a1mini", "nozzle_diameter": 0.4, "nozzle_type": "stainless_steel",
               "material": "PETG", "plate_type": "textured_plate", "plate_confirmed": True,
               "nozzle_confirmed": True}
    result = slice_model(source, output, printer, {"executable": str(executable), "timeout_seconds": 900,
                                                  "process_overrides": {"enable_support": False}})
    checked = inspect_print(Path(result["print_path"]), printer, plate=1)
    if checked["model"] != "a1mini" or checked["model_height_mm"] <= 0:
        raise RuntimeError("The generated archive did not pass model/geometry inspection.")
    report = {"platform": platform.platform(), "architecture": platform.machine(),
              "printer_contacted": False, "slicer_executable": str(executable),
              "source_sha256": sha256(source), "print_sha256": sha256(Path(result["print_path"])),
              "print_path": result["print_path"], "inspection": checked, "result": result}
    (output / "native-slicer-smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("platform", "architecture", "printer_contacted", "print_path", "print_sha256")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
