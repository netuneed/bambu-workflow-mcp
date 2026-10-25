"""Geometry inspection and exact zero-area sanitation of a separate print copy."""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path, PurePosixPath
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import trimesh

from .models import get_model

MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
UNIT_MM = {"micron": .001, "millimeter": 1., "centimeter": 10., "inch": 25.4, "foot": 304.8, "meter": 1000.}


def build_volume_for(printer: dict, settings: dict | None = None) -> list[float]:
    """Use the tighter of registered physical limits, installed preset and user restriction."""
    spec = get_model(printer.get("model"))
    volume = np.minimum(spec["build_volume_mm"], spec.get("profile_volume_mm", spec["build_volume_mm"])).astype(float)
    if printer.get("build_volume_mm") is not None:
        supplied = np.asarray(printer["build_volume_mm"], dtype=float)
        if supplied.shape != (3,) or not np.isfinite(supplied).all() or np.any(supplied <= 0):
            raise ValueError("build_volume_mm must contain three positive finite millimetre values")
        volume = np.minimum(volume, supplied)
    if settings and settings.get("printable_height") is not None:
        height = float(settings["printable_height"])
        if not math.isfinite(height) or height <= 0:
            raise ValueError("Invalid installed machine printable_height")
        volume[2] = min(volume[2], height)
    return volume.tolist()


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
        return {"kind": kind, "plates": plates, "model_members": models,
                "has_settings": "Metadata/project_settings.config" in names}


def _transform(value: str | None) -> np.ndarray:
    if not value:
        return np.eye(4)
    v = np.array([float(x) for x in value.split()], dtype=float)
    if v.size != 12 or not np.isfinite(v).all():
        raise ValueError("Invalid 3MF component transform")
    # 3MF serializes the transpose of the usual affine 3x4 matrix.
    a = np.eye(4)
    a[:3, :3] = v[:9].reshape(3, 3).T
    a[:3, 3] = v[9:]
    return a


def _load_3mf(path: Path) -> trimesh.Trimesh:
    """Resolve build items and components, including production-extension paths."""
    with checked_archive(path) as z:
        roots: dict[str, ET.Element] = {}
        resources: dict[tuple[str, str], ET.Element] = {}
        for n in z.namelist():
            if n.lower().endswith(".model"):
                data = z.read(n)
                if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                    raise ValueError("XML entities are not accepted")
                r = ET.fromstring(data)
                roots[n] = r
                for ob in r.findall("m:resources/m:object", NS):
                    resources[n, ob.attrib["id"]] = ob
        root_name = "3D/3dmodel.model"
        if root_name not in roots:
            root_name = next(iter(roots), "")
        if not root_name:
            raise ValueError("3MF has no model root")
        meshes = []

        def append_object(model: str, oid: str, transform: np.ndarray, trail: tuple):
            key = (model, oid)
            if key in trail or len(trail) > 50:
                raise ValueError("Cyclic or excessively deep 3MF components")
            ob = resources.get(key)
            if ob is None:
                raise ValueError(f"Missing 3MF object {oid}")
            unit = UNIT_MM.get(roots[model].attrib.get("unit", "millimeter"))
            if unit is None:
                raise ValueError("Unsupported 3MF length unit")
            geom = ob.find("m:mesh", NS)
            if geom is not None:
                vs = np.array([[float(v.attrib[x]) for x in ("x", "y", "z")]
                               for v in geom.findall("m:vertices/m:vertex", NS)], dtype=float)
                fs = np.array([[int(t.attrib[x]) for x in ("v1", "v2", "v3")]
                               for t in geom.findall("m:triangles/m:triangle", NS)], dtype=int)
                if not len(vs) or not len(fs):
                    raise ValueError("Empty 3MF mesh")
                if fs.min() < 0 or fs.max() >= len(vs):
                    raise ValueError("3MF triangle references a missing vertex")
                m = trimesh.Trimesh(vs, fs, process=False)
                m.apply_scale(unit)
                m.apply_transform(transform)
                meshes.append(m)
            for c in ob.findall("m:components/m:component", NS):
                target = next((v for k, v in c.attrib.items() if k.endswith("}path")), model)
                target = target.lstrip("/")
                if target not in roots:
                    raise ValueError("Missing external 3MF component")
                tx = _transform(c.attrib.get("transform"))
                tx[:3, 3] *= unit
                append_object(target, c.attrib["objectid"], transform @ tx, trail + (key,))

        unit = UNIT_MM.get(roots[root_name].attrib.get("unit", "millimeter"))
        if unit is None:
            raise ValueError("Unsupported 3MF length unit")
        items = roots[root_name].findall("m:build/m:item", NS)
        if not items:
            raise ValueError("3MF has no printable build items")
        for item in items:
            if item.attrib.get("printable", "1").lower() in ("0", "false"):
                continue
            tx = _transform(item.attrib.get("transform"))
            tx[:3, 3] *= unit
            append_object(root_name, item.attrib["objectid"], tx, ())
        if not meshes:
            raise ValueError("3MF has no printable mesh")
        return trimesh.util.concatenate(meshes)


def load_mesh(path: Path) -> trimesh.Trimesh:
    p = Path(path)
    if not p.is_file() or p.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source must be a regular file no larger than 512 MiB")
    if p.suffix.lower() == ".3mf":
        return _load_3mf(p)
    if p.suffix.lower() != ".stl":
        raise ValueError("Expected STL or 3MF")
    m = trimesh.load(p, force="mesh", process=False)
    if not isinstance(m, trimesh.Trimesh):
        raise ValueError("Source contains no mesh")
    return m


def _mesh_for_print(path: Path) -> tuple[trimesh.Trimesh, dict]:
    """Weld identical STL coordinates, never nearby points, and drop proven zero faces.

    No positive-area face or vertex coordinate changes. Removing zero-area records is
    accepted only if the resulting surface is closed, consistently oriented, and has
    the exact same bounds and signed volume as the source.
    """
    m = load_mesh(Path(path))
    if not len(m.faces) or not len(m.vertices) or not np.isfinite(m.vertices).all():
        raise ValueError("Mesh is empty or contains non-finite coordinates")
    source_faces = len(m.faces)
    vertices, inverse = np.unique(m.vertices, axis=0, return_inverse=True)
    m = trimesh.Trimesh(vertices=vertices, faces=inverse[m.faces], process=False)
    zero = m.area_faces == 0.0
    cleanup = {"operation": "none", "source_triangles": source_faces, "removed_triangles": 0,
               "vertex_welding": "identical coordinates only; no tolerance or coordinate changes"}
    if np.any(zero):
        candidate = m.copy()
        candidate.update_faces(~zero)
        candidate.remove_unreferenced_vertices()
        unchanged = len(candidate.faces) > 0 and np.array_equal(candidate.bounds, m.bounds)
        same_volume = bool(unchanged and candidate.volume == m.volume)
        if not unchanged or not same_volume or not candidate.is_watertight or not candidate.is_winding_consistent:
            raise ValueError(f"Mesh contains {int(np.count_nonzero(zero))} zero-area triangles; exact removal does not produce a closed surface with unchanged bounds and volume")
        cleanup.update(operation="remove_exact_zero_area_faces", removed_triangles=int(np.count_nonzero(zero)),
                       removed_face_indices=np.flatnonzero(zero).tolist(), bounds_unchanged=True,
                       signed_volume_unchanged=True, source_watertight=bool(m.is_watertight))
        m = candidate
    cleanup["print_triangles"] = len(m.faces)
    return m, cleanup


def analyze_mesh(path: Path, printer: dict) -> dict:
    p = Path(path)
    m, cleanup = _mesh_for_print(p)
    dims = m.extents
    spec = get_model(printer.get("model"))
    build = build_volume_for(printer)
    if np.any(dims <= .001):
        raise ValueError("Mesh has zero or near-zero thickness")
    if np.any(dims > np.array(build) + .01):
        raise ValueError(f"Model {dims.round(3).tolist()} mm exceeds build volume {build} mm; scaling is never automatic")
    degenerate = int(np.count_nonzero(m.area_faces < 1e-10))
    if degenerate:
        raise ValueError(f"Mesh contains {degenerate} degenerate triangles")
    if not m.is_watertight:
        counts = np.bincount(m.edges_unique_inverse)
        raise ValueError(f"Mesh is not watertight: {int(np.count_nonzero(counts == 1))} boundary edges, {int(np.count_nonzero(counts > 2))} non-manifold edges")
    if not m.is_winding_consistent:
        raise ValueError("Mesh triangle winding is inconsistent")
    if m.volume <= 0:
        raise ValueError("Mesh has non-positive signed volume; check mirrored normals")
    normals = m.face_normals
    elevated = m.triangles_center[:, 2] > m.bounds[0, 2] + max(.25, float(printer.get("nozzle_diameter", .4)))
    overhang = (normals[:, 2] < -math.cos(math.radians(45))) & elevated
    ratio = float(m.area_faces[overhang].sum() / max(float(m.area), .001))
    floor = m.vertices[:, 2] <= m.bounds[0, 2] + .025
    contact_area = float(m.area_faces[np.all(floor[m.faces], axis=1)].sum())
    warnings = []
    if cleanup["removed_triangles"]:
        warnings.append(f"Print copy omits {cleanup['removed_triangles']} exactly zero-area triangle records; source, bounds, vertex coordinates and signed volume are unchanged")
    if ratio > .02:
        warnings.append("Elevated downward faces detected; conservative automatic support will be enabled unless an explicit process profile overrides it")
    if contact_area < 5:
        warnings.append("Small or point bed contact; verify orientation or supply an explicit process profile")
    # Count shells directly instead of requiring optional scipy/networkx engines.
    parents = list(range(len(m.faces)))
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for a, b in m.face_adjacency:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parents[ra] = rb
    components = len({find(i) for i in range(len(parents))})
    if components > 1:
        warnings.append(f"Mesh contains {components} disconnected shells; intersections and internal shell collisions are not tested")
    return {"path": str(p.resolve()), "sha256": sha256(p), "dimensions_mm": dims.tolist(),
            "bounds_mm": m.bounds.tolist(), "model": spec["id"], "build_volume_mm": build,
            "triangles": len(m.faces), "watertight": bool(m.is_watertight),
            "winding_consistent": bool(m.is_winding_consistent), "volume_mm3": float(m.volume),
            "components": components, "bed_contact_area_mm2": contact_area, "cleanup": cleanup,
            "elevated_overhang_area_ratio": ratio, "support_recommended": ratio > .02,
            "warnings": warnings, "checks_not_performed": ["self-intersection", "minimum wall thickness", "mechanical strength", "bridge feasibility", "physical adhesion"]}


def normalized_stl(path: Path, destination: Path, printer: dict) -> dict:
    """Create a new bed-centered mm STL, retaining orientation and dimensions."""
    report = analyze_mesh(path, printer)
    m, cleanup = _mesh_for_print(path)
    offset = -np.array([(m.bounds[0, 0] + m.bounds[1, 0]) / 2,
                        (m.bounds[0, 1] + m.bounds[1, 1]) / 2, m.bounds[0, 2]])
    m.apply_translation(offset)
    Path(destination).write_bytes(trimesh.exchange.stl.export_stl(m))
    report["normalized_path"] = str(Path(destination).resolve())
    report["normalized_sha256"] = sha256(Path(destination))
    report["translation_mm"] = offset.tolist()
    return report
