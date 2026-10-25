"""Decision report around mandatory checks, not a replacement for them."""
from __future__ import annotations

from copy import deepcopy


def assess(analysis: dict, inspected: dict, *, error: dict | None = None) -> dict:
    findings = []

    def add(code, severity, message):
        findings.append(dict(code=code, severity=severity, message=message))

    if error:
        add("mandatory_check_failed", "critical", error.get("message", "A mandatory check failed."))
    if analysis.get("bed_contact_area_mm2", 1) <= 1e-8:
        add("no_bed_contact", "critical", "No planar bed contact was found. Prepare a stable orientation before launch.")
    elif analysis.get("bed_contact_area_mm2", 5) < 5:
        add("small_bed_contact", "medium", "Small bed contact. Consider a different orientation or adhesion settings; retain the model if no change is requested.")
    if analysis.get("support_recommended"):
        add("overhang_review", "medium", "Elevated downward faces need layer review. Check supports and bridges in the prepared file before continuing.")
    if analysis.get("components", 1) > 1:
        add("multiple_shells", "medium", "Several shells were exported together. Check their contact, spacing and assembly before continuing.")
    cleanup = analysis.get("cleanup", {})
    if cleanup.get("removed_triangles"):
        add("exact_zero_area_cleanup", "info", "Only exactly zero-area records were omitted from the print copy; bounds, coordinates and signed volume are preserved.")
    for warning in inspected.get("warnings", []):
        message = warning.get("msg", str(warning)) if isinstance(warning, dict) else str(warning)
        if message == "not_support_traditional_timelapse":
            add("timelapse_unavailable", "info", "Traditional timelapse is unavailable; this does not affect the print geometry.")
        else:
            # Unknown slicer diagnostics must be understood before dispatch. Numeric warning
            # levels are vendor-specific and are not guessed to mean harmless/critical.
            add("unclassified_slicer_warning", "critical", message)
    critical = any(f["severity"] == "critical" for f in findings)
    medium = any(f["severity"] == "medium" for f in findings)
    return {"schema_version": 1, "level": "critical" if critical else "medium" if medium else "clear",
            "can_print": not critical, "mandatory_checks_passed": not bool(error) and not critical,
            "findings": findings, "geometry_policy": "retain original dimensions and geometry",
            "unanswered_action": "block" if critical else "print_original_after_optional_review" if medium else "print",
            "checks_not_performed": analysis.get("checks_not_performed", []),
            "scope": "Passed implemented checks only; this is not a strength or physical print-success guarantee."}


def for_job(job: dict) -> dict:
    manifest = job.get("manifest", {})
    if not manifest and not job.get("error"):
        return {"level": "pending", "can_print": False, "mandatory_checks_passed": False,
                "findings": [], "unanswered_action": "wait_for_preparation"}
    report = deepcopy(manifest.get("assessment") or assess(job.get("analysis", manifest.get("analysis", {})),
                                                          manifest.get("inspection", {})))
    if job.get("error"):
        report["level"] = "critical"
        report["can_print"] = False
        report["mandatory_checks_passed"] = False
        report["unanswered_action"] = "block"
        report["findings"].append({"code": "current_blocker", "severity": "critical",
                                   "message": job["error"]["message"]})
    report["review_pending"] = bool(job.get("review_pending"))
    return report
