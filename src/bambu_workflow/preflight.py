"""Artifact-bound review of features that mesh/envelope checks cannot establish."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import time

from .artifacts import sha256

CHECKS = ("first_layer", "critical_features", "support_contacts", "unsupported_islands",
          "material_assignments", "removability")


def review_requirement(request: dict, source_kind: str, analysis: dict, inspection: dict, printer: dict) -> dict:
    reasons = []
    if request.get("require_layer_review"):
        reasons.append("explicit layer review requested")
    if request.get("support_mode") in {"baked_fins", "manual"}:
        reasons.append("manual or baked support contact requires actual sliced-layer review")
    if analysis.get("support_recommended"):
        reasons.append("elevated downward surfaces require support/bridge review")
    if source_kind == "sliced":
        reasons.append("imported executable project must be reviewed with its saved material/support assignments")
    if len(inspection.get("used_filament_ids", inspection.get("filaments", [1]))) > 1:
        reasons.append("multiple logical filaments require color/material and purge review")
    if printer.get("nozzle", {}).get("custom"):
        reasons.append("custom nozzle requires actual thin-feature and contact review")
    return {"required": bool(reasons), "reasons": reasons,
            "required_checks": list(CHECKS) if reasons else [],
            "scope": "Actual sliced layers, including support contacts and islands; not mechanical or adhesion certification."}


def make_review(manifest: dict, artifact_sha256: str, review: dict) -> dict:
    path = Path(manifest["print_path"])
    if artifact_sha256 != manifest["sha256"] or sha256(path) != artifact_sha256:
        raise ValueError("Layer review refers to a different or changed sliced artifact. Reinspect the actual prepared file.")
    if not isinstance(review, dict) or not isinstance(review.get("checks"), dict):
        raise ValueError("Record the actual reviewer, evidence, layers and completed checks.")
    missing = [key for key in CHECKS if review["checks"].get(key) is not True]
    if missing:
        raise ValueError("Unresolved sliced-layer checks block dispatch: " + ", ".join(missing))
    for key in ("reviewer", "evidence"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            raise ValueError("Layer review needs an actual reviewer and specific observed evidence, never an assumed pass.")
    layers = review.get("layers")
    if not isinstance(layers, list) or not layers or any(type(n) is not int or n < 1 for n in layers) or 1 not in layers:
        raise ValueError("List the actual one-based layers inspected, including layer 1 and all critical contact/feature transitions.")
    evidence_files = []
    for name in review.get("evidence_paths", []):
        evidence = Path(name)
        if not evidence.is_absolute() or not evidence.is_file():
            raise ValueError("Layer-review evidence paths must identify existing absolute files.")
        evidence_files.append({"path": str(evidence), "sha256": sha256(evidence)})
    return {"artifact_sha256": artifact_sha256, "at": time.time(),
            "reviewer": review["reviewer"].strip(), "evidence": review["evidence"].strip(),
            "layers": sorted(set(layers)), "checks": {key: True for key in CHECKS},
            "evidence_files": evidence_files,
            "configuration_sha256": configuration_fingerprint(manifest)}


def configuration_fingerprint(manifest: dict) -> str:
    import json
    return hashlib.sha256(json.dumps({k: manifest.get(k) for k in (
        "sha256", "printer", "materials", "material", "nozzle_fingerprint", "support_mode")},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_review(manifest: dict, review: dict | None):
    if not manifest.get("layer_review", {}).get("required"):
        return
    if (not review or review.get("artifact_sha256") != manifest["sha256"]
            or review.get("configuration_sha256") != configuration_fingerprint(manifest)
            or any(review.get("checks", {}).get(k) is not True for k in CHECKS)):
        raise ValueError("Actual sliced-layer review is required for this artifact before dispatch. Use record_layer_review after inspecting contacts, islands and material assignments.")


def assessed(manifest: dict, review: dict | None = None) -> dict:
    from .assessment import assess
    result = assess(manifest.get("analysis", {}), manifest.get("inspection", {}))
    # A persisted mandatory finding may carry evidence outside the basic mesh
    # statistics. A layer review clears only its own pending-review finding.
    existing = manifest.get("assessment", {}).get("findings", [])
    for finding in existing:
        if finding.get("code") != "layer_review_required" and finding not in result["findings"]:
            result["findings"].append(deepcopy(finding))
    critical = any(f.get("severity") == "critical" for f in result["findings"])
    medium = any(f.get("severity") == "medium" for f in result["findings"])
    result.update(level="critical" if critical else "medium" if medium else "clear",
                  can_print=not critical, mandatory_checks_passed=not critical,
                  unanswered_action="block" if critical else "print_original_after_optional_review" if medium else "print")
    try:
        validate_review(manifest, review)
    except ValueError as exc:
        result = deepcopy(result)
        result["findings"].append({"code": "layer_review_required", "severity": "critical", "message": str(exc)})
        result.update(level="critical", can_print=False, mandatory_checks_passed=False, unanswered_action="block")
    return result
