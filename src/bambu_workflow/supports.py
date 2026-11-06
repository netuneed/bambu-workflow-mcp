"""Optional pinned Support Fins geometry generation. Never slices or prints."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

from .artifacts import MAX_SOURCE_BYTES, checked_archive, classify_3mf, sha256


SUPPORT_FINS_VERSION = "1.0.5"
SUPPORT_FINS_REVISION = "514549694e816999a5e4c6674829eba7ea852019"
SUPPORT_FINS_SHA256 = "2ad0469d91d378baade31d785f2fba825d6fe7fbab115f17c6175ea6ab3b97f7"
SUPPORT_FINS_DOWNLOAD_URL = "https://github.com/gittrahan/support-fins/releases/download/v1.0.5/support-fins.mjs"
SUPPORT_RECEIPT_MEMBER = "Metadata/bambu_workflow_supports.json"
SUPPORT_RECEIPT_SCHEMA = "bambu_workflow_supports_v1"
MAX_TOOL_STDOUT_BYTES = 128 * 1024
MAX_TOOL_STDERR_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 256 * 1024
TOOL_TIMEOUT_SECONDS = 180
_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"


def _finite(value, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be a finite number from {minimum:g} to {maximum:g}.")
    return float(value)


def support_settings(settings: dict | None) -> dict:
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError("Support settings must be an object.")
    unknown = set(settings) - {"rotation_deg", "nozzle", "layer_height", "gap", "mode"}
    if unknown:
        raise ValueError("Unsupported Support Fins settings: " + ", ".join(sorted(str(v) for v in unknown)))
    rotation = settings.get("rotation_deg", [0, 0, 0])
    if not isinstance(rotation, (list, tuple)) or len(rotation) != 3:
        raise ValueError("rotation_deg must contain exactly three angles in X,Y,Z order.")
    nozzle = _finite(settings.get("nozzle", .4), "Nozzle diameter", .2, .8)
    if nozzle not in {.2, .4, .6, .8}:
        raise ValueError("Nozzle must be 0.2, 0.4, 0.6 or 0.8 mm, matching the installed printer profile.")
    mode = settings.get("mode", "auto")
    if mode not in ("auto", "full"):
        raise ValueError("Support Fins mode must be auto or full.")
    return {"rotation_deg": [_finite(v, "Rotation angle", -360, 360) for v in rotation],
            "nozzle": nozzle, "layer_height": _finite(settings.get("layer_height", .2), "Layer height", .08, .6),
            "gap": _finite(settings.get("gap", .2), "Support gap", .1, .4), "mode": mode}


def _node_environment() -> dict:
    # The reviewed geometry tool does not need printer secrets or Node startup hooks.
    allowed = {"SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _run_bounded(argv: list[str], cwd: Path, *, timeout: float = TOOL_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    """Bound both process time and output while it is running, without a shell."""
    if timeout <= 0 or timeout > TOOL_TIMEOUT_SECONDS:
        raise ValueError("Support Fins timeout must be positive and at most 180 seconds.")
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   shell=False, env=_node_environment(),
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + timeout
        failure = None
        while True:
            if os.fstat(stdout.fileno()).st_size > MAX_TOOL_STDOUT_BYTES or os.fstat(stderr.fileno()).st_size > MAX_TOOL_STDERR_BYTES:
                failure = "Support Fins output exceeded the bounded JSON/stderr limit."
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "Support Fins timed out. No output is approved for printing."
                break
            try:
                process.wait(timeout=min(.05, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if failure:
            process.kill()
            process.wait(timeout=5)
            raise ValueError(failure)
        if os.fstat(stdout.fileno()).st_size > MAX_TOOL_STDOUT_BYTES or os.fstat(stderr.fileno()).st_size > MAX_TOOL_STDERR_BYTES:
            raise ValueError("Support Fins output exceeded the bounded JSON/stderr limit.")
        stdout.seek(0)
        stderr.seek(0)
        return process.returncode, stdout.read(MAX_TOOL_STDOUT_BYTES).decode("utf-8", errors="replace"), stderr.read(MAX_TOOL_STDERR_BYTES).decode("utf-8", errors="replace")


def _configured_tool(config) -> tuple[Path, Path]:
    data = config.data if hasattr(config, "data") else config
    section = data.get("support_fins") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        raise ValueError("Support Fins is optional and is not configured. Install pinned v1.0.5 outside this package and set support_fins.command, cli_path and sha256.")
    if section.get("sha256") != SUPPORT_FINS_SHA256:
        raise ValueError("Support Fins must use this adapter's exact pinned v1.0.5 SHA256; arbitrary scripts and newer unreviewed versions are refused.")
    paths = []
    for key in ("command", "cli_path"):
        value = section.get(key)
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise ValueError("support_fins." + key + " must be an absolute path to an existing local file.")
        path = Path(value).resolve(strict=True)
        if not path.is_file():
            raise ValueError("support_fins." + key + " must be a regular file.")
        paths.append(path)
    if paths[0] == paths[1] or paths[1].suffix.lower() != ".mjs" or sha256(paths[1]) != SUPPORT_FINS_SHA256:
        raise ValueError("Support Fins script does not match the reviewed pinned artifact.")
    code, output, _ = _run_bounded([str(paths[0]), "--version"], paths[0].parent, timeout=10)
    version = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*", output)
    if code or not version or tuple(map(int, version.groups())) < (20, 10, 0):
        raise ValueError("Support Fins requires an actual Node.js executable version 20.10 or newer.")
    return paths[0], paths[1]


def _report_issues(report: dict, stderr: str) -> list[str]:
    issues = []
    stats = report.get("stats")
    required = ("unserved", "floating", "overhangRegions", "finTriangles", "padTriangles")
    if not isinstance(stats, dict):
        issues.append("Support Fins did not return its required geometry statistics.")
    else:
        for key in required:
            value = stats.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                issues.append("Missing or invalid Support Fins statistic: " + key)
        for key in ("unserved", "floating"):
            value = stats.get(key)
            if type(value) in (int, float) and math.isfinite(value) and value > 0:
                issues.append(f"Support Fins reports {value:g} {key} regions/pieces. Revise the geometry or supports before layer review.")
    if report.get("warnings") or stderr.strip():
        issues.append("Support Fins emitted warnings. Read the tool report before accepting this derivative.")
    return issues


def _reject_json_constant(value: str):
    raise ValueError("Nonfinite JSON value: " + value)


def _embed_receipt(path: Path, receipt: dict) -> None:
    """Add only receipt/content-type metadata. Every geometry byte stays exact."""
    kind = classify_3mf(path)
    if kind["kind"] == "sliced" or not kind["model_members"]:
        raise ValueError("Support Fins did not create an unsliced 3MF geometry project.")
    updated = path.with_name("receipt-bound.3mf")
    with checked_archive(path) as source:
        if SUPPORT_RECEIPT_MEMBER in source.namelist():
            raise ValueError("Refusing to replace an existing support receipt.")
        if "[Content_Types].xml" not in source.namelist():
            raise ValueError("Support Fins output has no valid 3MF content-type manifest.")
        content_types = source.read("[Content_Types].xml")
        if b"<!DOCTYPE" in content_types.upper() or b"<!ENTITY" in content_types.upper():
            raise ValueError("XML entities are not accepted in Support Fins output.")
        root = ET.fromstring(content_types)
        if root.tag != "{" + _CONTENT_TYPES + "}Types":
            raise ValueError("Invalid 3MF content-type manifest.")
        matching = [node for node in root if node.get("Extension", "").lower() == "json"]
        if not matching:
            ET.SubElement(root, "{" + _CONTENT_TYPES + "}Default", {"Extension":"json", "ContentType":"application/json"})
            content_types = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        elif any(node.get("ContentType") != "application/json" for node in matching):
            raise ValueError("Conflicting JSON content type in Support Fins output.")
        members = {}
        with zipfile.ZipFile(updated, "x", compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                content = content_types if info.filename == "[Content_Types].xml" else source.read(info)
                target.writestr(info, content)
                members[info.filename] = hashlib.sha256(content).hexdigest()
            receipt["member_sha256"] = members
            payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
            if len(payload) > MAX_RECEIPT_BYTES:
                raise ValueError("Support receipt exceeds its size limit.")
            target.writestr(SUPPORT_RECEIPT_MEMBER, payload)
    updated.replace(path)


def read_support_receipt(path: str | Path) -> dict | None:
    """Return a validated artifact-bound marker, or None when it has no marker."""
    with checked_archive(Path(path)) as archive:
        if SUPPORT_RECEIPT_MEMBER not in archive.namelist():
            return None
        info = archive.getinfo(SUPPORT_RECEIPT_MEMBER)
        if info.file_size > MAX_RECEIPT_BYTES:
            raise ValueError("Support receipt exceeds its size limit.")
        try:
            receipt = json.loads(archive.read(info), parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError):
            raise ValueError("Invalid support receipt JSON.") from None
        if (not isinstance(receipt, dict) or receipt.get("schema") != SUPPORT_RECEIPT_SCHEMA
                or receipt.get("tool_sha256") != SUPPORT_FINS_SHA256
                or receipt.get("tool_version") != SUPPORT_FINS_VERSION
                or receipt.get("tool_revision") != SUPPORT_FINS_REVISION
                or receipt.get("support_mode") != "baked_fins"
                or receipt.get("layer_review_required") is not True or receipt.get("can_print") is not False
                or receipt.get("geometry_change_authorized") is not True):
            raise ValueError("Invalid Support Fins receipt or tool pin.")
        if not re.fullmatch(r"[a-f0-9]{64}", str(receipt.get("source_sha256", ""))):
            raise ValueError("Support receipt has no valid source hash.")
        support_settings(receipt.get("settings"))
        members = {entry.filename: hashlib.sha256(archive.read(entry)).hexdigest() for entry in archive.infolist()
                   if entry.filename != SUPPORT_RECEIPT_MEMBER}
        if members != receipt.get("member_sha256"):
            raise ValueError("Support Fins geometry/project members changed after receipt creation. Regenerate and repeat layer review.")
        return receipt


def generate_support_fins(path: str | Path, output_dir: str | Path, settings: dict | None,
                          geometry_change_authorized: bool, user_prompt: str, config) -> dict:
    """Generate a separate authorized STL derivative, then require layer review."""
    if geometry_change_authorized is not True or not isinstance(user_prompt, str) or not user_prompt.strip() or len(user_prompt) > 8000:
        raise ValueError("Adding fins and rotating the part require explicit geometry_change_authorized=true and the actual user instruction.")
    supplied = Path(path).expanduser()
    if not supplied.is_absolute():
        raise ValueError("Choose an absolute source path.")
    source = supplied.resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_SOURCE_BYTES or source.stat().st_size == 0:
        raise ValueError("Support Fins source must be a nonempty regular file no larger than 512 MiB.")
    if source.suffix.lower() == ".3mf":
        raise ValueError("This adapter accepts STL geometry only. Keep 3MF parts, colors, modifiers and painted/manual supports in Bambu Studio; use upstream --preserve-project with explicit object selection only after a separate preservation review.")
    if source.suffix.lower() != ".stl":
        raise ValueError("Export a separate millimetre STL derivative from Fusion/QymCAD for this Support Fins adapter.")
    normalized = support_settings(settings)
    node, cli = _configured_tool(config)
    output_root = Path(output_dir).expanduser()
    if not output_root.is_absolute():
        raise ValueError("Choose an absolute output directory.")
    output_root = output_root.resolve()
    if output_root == source or (output_root.exists() and not output_root.is_dir()):
        raise ValueError("Support output directory cannot alias the input file.")
    output_root.mkdir(parents=True, exist_ok=True)
    folder = output_root / ("support-fins-" + uuid.uuid4().hex)
    folder.mkdir(exist_ok=False)
    source_hash = sha256(source)
    snapshot = folder / "source.stl"
    shutil.copyfile(source, snapshot)
    if snapshot.stat().st_size > MAX_SOURCE_BYTES or sha256(snapshot) != source_hash:
        raise ValueError("Source changed while making the immutable support-generation copy.")
    tool_copy = folder / "support-fins-pinned.mjs"
    shutil.copyfile(cli, tool_copy)
    if sha256(tool_copy) != SUPPORT_FINS_SHA256:
        raise ValueError("Support Fins script changed before execution.")
    output = folder / "part-with-fins.3mf"
    argv = [str(node), str(tool_copy), str(snapshot), "--output", str(output), "--json",
            "--rot", ",".join(format(v, ".12g") for v in normalized["rotation_deg"]),
            "--nozzle", format(normalized["nozzle"], ".12g"),
            "--layer-height", format(normalized["layer_height"], ".12g"),
            "--gap", format(normalized["gap"], ".12g"), "--mode", normalized["mode"]]
    code, stdout, stderr = _run_bounded(argv, folder)
    if code:
        raise ValueError("Support Fins generation failed (exit " + str(code) + "): " + stderr[:4000].strip())
    try:
        report = json.loads(stdout, parse_constant=_reject_json_constant)
    except (ValueError, UnicodeError):
        raise ValueError("Support Fins did not return one bounded JSON object. No output is approved for printing.") from None
    if not isinstance(report, dict) or not isinstance(report.get("output"), str) or Path(report["output"]).resolve() != output:
        raise ValueError("Support Fins reported an unexpected output path.")
    if not output.is_file() or output.is_symlink() or output.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Support Fins did not produce a bounded regular 3MF output.")
    if sha256(snapshot) != source_hash or sha256(source) != source_hash:
        raise ValueError("Source changed during support generation. Regenerate from an unchanged CAD export.")
    issues = _report_issues(report, stderr)
    receipt = {"schema": SUPPORT_RECEIPT_SCHEMA, "tool": "Support Fins", "tool_version": SUPPORT_FINS_VERSION,
               "tool_revision": SUPPORT_FINS_REVISION, "tool_sha256": SUPPORT_FINS_SHA256,
               "tool_source": SUPPORT_FINS_DOWNLOAD_URL, "source_path": str(source), "source_sha256": source_hash,
               "source_snapshot": str(snapshot), "tool_output_sha256": sha256(output),
               "tool_configuration": {"command":str(node), "cli_path":str(cli), "sha256":SUPPORT_FINS_SHA256},
               "unit_assumption": "STL coordinates are interpreted as millimetres. Verify CAD control dimensions before slicing.",
               "settings": normalized, "rotation_deg": normalized["rotation_deg"], "nozzle": normalized["nozzle"],
               "layer_height": normalized["layer_height"], "support_mode": "baked_fins",
               "geometry_change_authorized": True, "user_prompt": user_prompt.strip(),
               "layer_review_required": True, "can_print": False, "generation_issues": issues,
               "tool_report": report, "tool_stderr": stderr, "created_at": time.time()}
    _embed_receipt(output, receipt)
    verified = read_support_receipt(output)
    result = {**verified, "output_path": str(output), "output_sha256": sha256(output),
              "receipt_member": SUPPORT_RECEIPT_MEMBER, "receipt_path": str(folder / "support-receipt.json"),
              "printer_contacted": False,
              "next": "Open the 3MF in Bambu Studio with native supports disabled. Review the first layer, islands, tine contacts and removal access at the exact recorded nozzle and layer height. Do not print until the artifact-bound layer review passes."}
    Path(result["receipt_path"]).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result
