"""Bounded artifact hashing and 3MF metadata checks without geometry-library imports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile
import xml.etree.ElementTree as ET

MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_SETTINGS_BYTES = 16 * 1024 * 1024


def read_project_settings(archive: zipfile.ZipFile) -> tuple[dict | None, dict | None]:
    """Validate complete project settings, never isolated machine/filament presets."""
    name = "Metadata/project_settings.config"
    if name not in archive.namelist():
        return None, None
    if archive.getinfo(name).file_size > MAX_SETTINGS_BYTES:
        raise ValueError("3MF project settings exceed the 16 MiB metadata limit")
    try:
        settings = json.loads(archive.read(name))
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid Bambu 3MF project_settings.config JSON; geometry may still be present") from error
    from .project_settings import validate_project_settings
    return settings, validate_project_settings(settings, require_palette=True)


def _xml(data: bytes) -> ET.Element:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML entities are not accepted")
    return ET.fromstring(data)


def authored_plate_inventory(root: ET.Element) -> list[dict]:
    plates = []
    used = set()
    for ordinal, node in enumerate(root.findall("plate"), 1):
        meta = {m.get("key"): m.get("value") for m in node.findall("metadata")}
        value = meta.get("plater_id", meta.get("index"))
        try:
            plate_id = int(value) if value is not None else ordinal
        except (ValueError, TypeError):
            raise ValueError("3MF authored plate ID must be a positive integer") from None
        if plate_id < 1 or plate_id in used:
            raise ValueError("3MF authored plate IDs must be positive and unique; preserve the source layout")
        used.add(plate_id)
        plates.append({"plate_id": plate_id, "id_source": "metadata" if value is not None else "order",
                       "object_instances": len(node.findall("model_instance"))})
    return plates

def sha256(path: Path) -> str:
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def checked_archive(path: Path) -> zipfile.ZipFile:
    """Open a bounded archive without extracting untrusted members."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source must be a regular file no larger than 512 MiB")
    z = zipfile.ZipFile(p)
    infos = z.infolist()
    names = [x.filename for x in infos]
    if len(infos) > 10000 or len(set(names)) != len(names):
        z.close()
        raise ValueError("3MF has duplicate names or too many archive members")
    if sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES:
        z.close()
        raise ValueError("3MF decompressed size exceeds 1 GiB")
    for i in infos:
        n = PurePosixPath(i.filename)
        if n.is_absolute() or ".." in n.parts or "\\" in i.filename or i.flag_bits & 1:
            z.close()
            raise ValueError("3MF contains unsafe or encrypted archive members")
        if i.file_size > MAX_SOURCE_BYTES:
            z.close()
            raise ValueError("3MF member exceeds 512 MiB")
    return z


def classify_3mf(path: Path) -> dict:
    with checked_archive(path) as z:
        names = z.namelist()
        plates = sorted(int(PurePosixPath(n).stem.split("_")[-1]) for n in names
                        if n.startswith("Metadata/plate_") and n.endswith(".gcode")
                        and PurePosixPath(n).stem.split("_")[-1].isdigit())
        models = [n for n in names if n.lower().endswith(".model")]
        if plates:
            kind = "sliced"
        elif "Metadata/project_settings.config" in names:
            kind = "project"
        elif models:
            kind = "geometry"
        else:
            raise ValueError("3MF contains neither model geometry nor sliced plates")
        _, settings_validation = read_project_settings(z)
        reasons = set()
        authored_plates = []
        authored_objects = 0
        root_build_items = 0
        if "Metadata/bambu_workflow_supports.json" in names:
            reasons.add("separate part/fin support geometry with generation receipt")
        # These properties cannot survive the geometry-only STL preparation path.
        # Inspect every production-extension member, not just the root model.
        for name in models:
            root = _xml(z.read(name))
            if name.lower() == "3d/3dmodel.model" or (name == models[0] and not any(n.lower() == "3d/3dmodel.model" for n in models)):
                root_build_items = sum(1 for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "build" for child in node if child.tag.rsplit("}", 1)[-1] == "item")
            for node in root.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                attrs = {key.rsplit("}", 1)[-1]: value for key, value in node.attrib.items()}
                if tag in {"basematerials", "colorgroup", "texture2d", "texture2dgroup", "multiproperties", "compositematerials"}:
                    reasons.add("material/color resources")
                if set(attrs) & {"pid", "pindex", "p1", "p2", "p3"}:
                    reasons.add("object/triangle material properties")
                if tag == "triangle" and set(attrs) - {"v1", "v2", "v3", "pid", "p1", "p2", "p3"}:
                    reasons.add("triangle painting or extension properties")
        if "Metadata/model_settings.config" in names:
            if z.getinfo("Metadata/model_settings.config").file_size > MAX_SETTINGS_BYTES:
                raise ValueError("3MF authored model settings exceed the 16 MiB metadata limit")
            root = _xml(z.read("Metadata/model_settings.config"))
            authored_plates = authored_plate_inventory(root)
            authored_objects = len(root.findall("object"))
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] == "part" and node.get("subtype", "normal_part") != "normal_part":
                    reasons.add("modifier/negative/manual support volumes")
                if node.tag.rsplit("}", 1)[-1] == "metadata":
                    key, value = node.get("key", ""), node.get("value", "")
                    if key == "extruder" and value not in {"", "0", "1"}:
                        reasons.add("per-object filament assignments")
                    if any(word in key.lower() for word in ("paint", "support", "seam")):
                        reasons.add("per-object support/paint settings")
        if kind in {"project", "sliced"}:
            if len(authored_plates) > 1:
                reasons.add("multiple authored plates")
            if authored_objects > 1 or root_build_items > 1 or any(p["object_instances"] > 1 for p in authored_plates):
                reasons.add("multiple authored objects and their placement")
        return {"kind": kind, "plates": plates, "model_members": models,
                "has_settings": "Metadata/project_settings.config" in names,
                "project_settings_validation": settings_validation,
                "authored_plates": authored_plates, "authored_object_count": authored_objects,
                "root_build_item_count": root_build_items,
                "requires_preservation": bool(reasons), "preservation_reasons": sorted(reasons)}

