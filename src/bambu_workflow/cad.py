"""Neutral CAD exchange provenance. Native design documents are archived, never executed."""
from __future__ import annotations

import math
from pathlib import Path
import shutil

from .geometry import MAX_SOURCE_BYTES, sha256


def validate_provenance(value: dict) -> dict:
    allowed = {"application", "version", "units", "expected_dimensions_mm", "source_path", "export_options"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("CAD provenance accepts application, version, units, expected_dimensions_mm, source_path and export_options.")
    for key in ("application", "version", "units"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError("Record the actual CAD application, version and export units.")
    result = dict(value)
    if result["units"].lower() not in {"mm", "millimeter", "millimeters"}:
        raise ValueError("Export the print mesh in millimeters; this workflow never guesses STL units or scales a CAD part silently.")
    result["units"] = "millimeter"
    dims = result.get("expected_dimensions_mm")
    if dims is not None and (not isinstance(dims, list) or len(dims) != 3 or any(
            type(n) not in (int, float) or not math.isfinite(n) or n <= 0 for n in dims)):
        raise ValueError("Expected CAD dimensions must be three positive finite millimeter values in the chosen print pose.")
    if result.get("source_path"):
        result["source_path"] = str(Path(result["source_path"]).expanduser().resolve())
    if result.get("export_options") is not None and not isinstance(result["export_options"], dict):
        raise ValueError("CAD export_options must be an object of observed exporter settings.")
    return result


def capture_provenance(value: dict, mesh: Path, folder: Path) -> dict:
    result = validate_provenance(value)
    result["export_sha256"] = sha256(mesh)
    if result.get("source_path"):
        source = Path(result["source_path"])
        if not source.is_file() or source.stat().st_size > MAX_SOURCE_BYTES or source.suffix.lower() not in {
                ".f3d", ".f3z", ".qcad", ".step", ".stp"}:
            raise ValueError("Native CAD archive must be an existing F3D/F3Z/QCAD/STEP file no larger than 512 MiB.")
        digest = sha256(source)
        destination = folder / ("cad-original" + source.suffix.lower())
        shutil.copyfile(source, destination)
        if sha256(destination) != digest or sha256(source) != digest:
            raise ValueError("CAD original changed during snapshot. Retry with a stable export.")
        result["native_snapshot"] = {"path": str(destination), "sha256": digest}
    return result


def check_dimensions(analysis: dict, provenance: dict):
    expected = provenance.get("expected_dimensions_mm")
    actual = analysis.get("dimensions_mm")
    if expected is not None and (not actual or len(actual) != 3 or any(abs(a - b) > .05 for a, b in zip(actual, expected))):
        raise ValueError("Exported mesh dimensions differ from the declared CAD dimensions by more than 0.05 mm. Check units, bodies and pose; no automatic scaling was applied.")


def import_guidance() -> dict:
    return {"accepted": ["STL in confirmed millimeters", "geometry-only 3MF without unhandled material assignments",
                         "inspected sliced Bambu Studio 3MF with an explicit complete logical material plan"],
            "fusion": {"export": "Select intended body/component, Save As Mesh, 3MF or binary STL, millimeters, suitable refinement.",
                       "native": "Archive F3D/F3Z. Native Fusion documents are not parsed by the printer service.",
                       "source": "https://help.autodesk.com/view/fusion360/ENU/?contextId=MESH-SAVE-AS-MESH"},
            "qymcad": {"export": "Export selected part/subassembly as 3MF or STL. Record actual version and check all bodies, names, transforms and dimensions.",
                       "version_note": "Official October 2026 development releases add structured 3MF and fix every-body export; older README format lists are incomplete.",
                       "native": "Archive QCAD plus neutral STEP; never run an in-place document converter automatically.",
                       "source": "https://github.com/QymIs-Tech/QymCAD/releases"},
            "step": "Open STEP in installed Bambu Studio, choose model/nozzle/materials, review assembly and supports, slice and save sliced 3MF for checked import.",
            "colors_and_manual_supports": "Keep the native Studio project. Do not flatten painting, modifiers, manual supports or negative volumes to a single STL.",
            "checked_at": "2026-11-06"}
