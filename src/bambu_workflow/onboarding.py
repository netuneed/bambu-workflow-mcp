"""Setup-only checks with an allowlisted, shareable report. Never starts a print."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import platform
import sys
import time

from . import __version__
from .config import Config, _validate_config, default_config_path
from .hardware import validate_nozzle
from .models import get_model


def lab_check(path: str | Path | None = None, *, printer_id: str | None = None,
              online: bool = False) -> dict:
    """Read configuration/profiles; online reads only one selected device snapshot.

    Private config, profile names, arbitrary telemetry/error text and paths are
    intentionally never copied into this report. Offline checks do not load a
    keyring, instantiate Workflow/Store, launch Studio, or create state folders.
    """
    report = {"schema_version": 1, "workflow_version": __version__,
              "platform": "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "other",
              "python_version": platform.python_version(), "mode": "online_read_only" if online else "offline",
              "status": "blocked", "ready_for_preparation": False,
              "printing_authorized": False, "print_started": False, "printer_contacted": False,
              "checks": [], "physical_validation": "not_performed_by_setup",
              "still_required": ["model-specific preparation and actual-layer review when required",
                                 "current physical plate, bed, nozzle and spool observations",
                                 "explicit print instruction and verified unique job telemetry",
                                 "inspection of the finished acceptance print"],
              "privacy": "No device identifiers, credentials, filenames, local paths or raw telemetry are included."}

    def check(code: str, passed: bool, message: str, *, blocking: bool = True):
        report["checks"].append({"code": code, "result": "pass" if passed else "blocked" if blocking else "not_checked",
                                  "message": message})

    def finish():
        report["ready_for_preparation"] = not any(c["result"] == "blocked" for c in report["checks"])
        report["status"] = "local_checks_passed" if report["ready_for_preparation"] else "blocked"
        return report

    check("python_version", sys.version_info >= (3, 12), "Python 3.12 or newer is required.")
    config_path = Path(path or os.environ.get("BAMBU_WORKFLOW_CONFIG", default_config_path())).expanduser()
    if not config_path.is_file():
        check("configuration", False, "Run onboard locally to configure this computer and its printer.")
        return finish()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        _validate_config(data)
    except Exception:
        check("configuration", False, "Configuration is invalid. Repair the local file before continuing; do not share it.")
        return finish()
    check("configuration", True, "A structurally valid local configuration exists.")
    devices = data["printers"]
    selected = [p for p in devices if p["id"] == printer_id] if printer_id is not None else devices if len(devices) == 1 else []
    if len(selected) != 1:
        check("printer_selection", False, "Select exactly one configured printer with --printer. No device was contacted.")
        return finish()
    printer = dict(selected[0])
    try:
        spec = get_model(printer.get("model"))
        printer["model"] = spec["id"]
    except Exception:
        check("printer_model", False, "Select a model supported by this runtime.")
        return finish()
    report["printer"] = {"model": spec["id"], "configured_device_number": devices.index(selected[0]) + 1,
                         "launch_adapter_supported": bool(spec["launch_supported"])}
    check("launch_adapter", bool(spec["launch_supported"]), "This model needs an implemented launch adapter for automatic printing.")
    check("access_code_reference", bool(printer.get("secret_ref") or printer.get("access_code_env")),
          "An OS keyring or environment reference is needed; secret availability is checked only during online access.")
    try:
        facts = validate_nozzle(printer, require_fresh=True)
        report["printer"]["nozzle"] = {k: facts[k] for k in ("diameter", "material", "flow_class", "custom")}
        check("nozzle_declaration", printer.get("nozzle_confirmed") is True, "Confirm the physically installed nozzle; custom observations must still be current at start.")
    except Exception:
        check("nozzle_declaration", False, "Nozzle facts are missing, incompatible or expired. Confirm the actual installed hardware.")
    from .slicer import PLATES, _configure_nozzle, _profile_paths, _validate_profiles, resolve_profile
    plate = printer.get("plate_type")
    check("plate_declaration", plate in PLATES and printer.get("plate_confirmed") is True,
          "Confirm the actual installed plate and a fresh clear bed again before each start.")
    materials = printer.get("materials")
    exact = (isinstance(materials, list) and len(materials) == 1 and isinstance(materials[0], dict)
             and bool(materials[0].get("preset"))) or bool(data.get("slicer", {}).get("profiles", {}).get("filament"))
    check("exact_filament_preset", bool(exact), "Choose the exact installed filament product preset; polymer family alone is insufficient for lab acceptance.")
    if exact:
        try:
            _, paths = _profile_paths(printer, data.get("slicer", {}))
            profiles = {kind: resolve_profile(paths[kind], paths["root"]) for kind in ("machine", "process", "filament")}
            _validate_profiles(profiles, printer)
            _configure_nozzle(profiles, printer)
            check("installed_profiles", True, "Installed machine, process, filament and nozzle variants resolve together. Studio was not launched.")
        except Exception:
            check("installed_profiles", False, "Matching installed Studio profiles could not be resolved. Check the selected model, nozzle, product preset and Studio profile installation locally.")
    else:
        check("installed_profiles", False, "Select an exact filament preset before validating installed profiles.")
    if not online:
        check("live_device", False, "Network identity, status and local-control permission remain untested. Use --online to read only this selected printer.", blocking=False)
        return finish()
    try:
        config = Config(config_path)
        report["printer_contacted"] = True
        status = config.adapter(printer["id"]).snapshot()
        stamp = status.get("received_at")
        fresh = type(stamp) in (int, float) and math.isfinite(stamp) and -5 <= time.time() - stamp <= 20
        check("live_device", fresh and status.get("identity_verified") is True and status.get("model") == spec["id"],
              "Fresh live model and serial must match the selected local device.")
        state = status.get("gcode_state")
        state = state if state in {"IDLE", "FINISH", "FAILED", "RUNNING", "PAUSE", "PREPARE", "SLICING", "INIT"} else "UNKNOWN"
        report["live"] = {"state": state, "identity_verified": status.get("identity_verified") is True, "fresh": bool(fresh),
                          "upload_channel": "not_tested", "command_permission": "not_tested"}
        check("printer_idle", state in {"IDLE", "FINISH"}, "The selected device must be idle; setup never interrupts or resumes an existing print.")
        raw = status.get("raw", status)
        fault_facts_known = "hms" in raw and "print_error" in raw
        check("printer_faults", fault_facts_known and not (raw.get("hms") or raw.get("print_error")),
              "Fresh HMS and print-error facts must be present and clear. Missing fault telemetry is not proof of a fault-free printer.")
        if spec.get("requires_sdcard", True):
            check("required_storage", raw.get("sdcard") is True, "This device requires present readable print storage.")
        try:
            validate_nozzle(printer, require_fresh=True, live_status=status)
            check("live_nozzle_consistency", True, "Reported nozzle facts do not contradict the local declaration; they do not establish aftermarket identity.")
        except Exception:
            check("live_nozzle_consistency", False, "Reported nozzle facts conflict with the declaration or current custom-nozzle evidence is missing.")
    except Exception:
        check("live_device", False, "Fresh status could not be read. Check LAN reachability, saved credentials and firmware permissions locally. No raw error was included.")
    return finish()


def write_report(path: str | Path, report: dict) -> None:
    """Refuse replacement of an existing file, including a private configuration."""
    with Path(path).expanduser().open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
