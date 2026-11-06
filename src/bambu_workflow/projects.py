"""Read-only 3MF project inspection without native geometry libraries or hardware."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .artifacts import MAX_SOURCE_BYTES, classify_3mf


def _source_hash(source: Path) -> str:
    if not source.is_file() or source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Project source must be a regular file no larger than 512 MiB")
    digest = hashlib.sha256()
    remaining = MAX_SOURCE_BYTES
    with source.open("rb") as stream:
        while data := stream.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(data)
            if remaining < 0:
                raise ValueError("Project source grew beyond the 512 MiB limit during inspection")
            digest.update(data)
    return digest.hexdigest()


def inspect_project(path: str | Path) -> dict:
    source = Path(path)
    if not source.is_absolute() or source.suffix.lower() != ".3mf":
        raise ValueError("Project inspection requires an absolute path to a 3MF file")
    before = _source_hash(source)
    info = classify_3mf(source)
    if _source_hash(source) != before:
        raise ValueError("The source project changed during inspection; inspect the stable file again")
    return {"source_sha256": before, "kind": info["kind"],
            "settings_validation": info["project_settings_validation"],
            "inventory": {"model_members": len(info["model_members"]),
                          "authored_object_count": info["authored_object_count"],
                          "root_build_item_count": info["root_build_item_count"],
                          "authored_plates": info["authored_plates"],
                          "sliced_plates": info["plates"]},
            "requires_preservation": info["requires_preservation"],
            "preservation_reasons": info["preservation_reasons"],
            "gui_validation": "not_observed", "can_print": False,
            "source_modified": False, "printer_contacted": False,
            "limits": ["Settings semantics and XML inventory do not establish native GUI loading.",
                       "Geometry dimensions, topology, executable layers and physical acceptance need separate checks.",
                       "Editable multi-object or multi-plate layouts remain in native Studio; import the inspected sliced file to print a selected plate."]}
