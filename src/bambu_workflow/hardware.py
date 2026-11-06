"""Physical nozzle declarations, separate from telemetry and filament branding.

Telemetry may contradict diameter/material/flow. It cannot establish the brand,
custom geometry, calibration, or that a person checked the installed hotend.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

from .models import get_model, normalize_model


CUSTOM_CONFIRMATION_MAX_AGE_SECONDS = 30 * 60
_MATERIALS = {"stainless_steel", "hardened_steel"}
_HIGH_FLOW_MODELS = {"p2s", "h2s", "h2d", "h2dpro", "h2c"}
_IDENTITY_FIELDS = ("manufacturer", "model", "diameter", "material", "flow_class", "custom",
                    "compatible_printer_model", "stock_geometry_compatible", "max_volumetric_speed",
                    "max_temperature_c", "installation_id")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(label + " must be a finite positive number.")
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(label + " must be a finite positive number.") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(label + " must be a finite positive number.")
    return number


def _flow(value: Any) -> str:
    token = str(value or "").replace("_", "").replace(" ", "").lower()
    if token in {"standard", "standardflow"}:
        return "Standard"
    if token in {"highflow", "hf"}:
        return "HighFlow"
    raise ValueError("Nozzle flow_class must be Standard or HighFlow. PETG HF is a filament, not a nozzle flow class.")


def stock_nozzle_facts(model: str, diameter: float = 0.4, *, flow_class: str = "Standard",
                       material: str | None = None) -> dict:
    """Return factory choices, never a claim about what is physically installed."""
    spec = get_model(model)
    diameter = _number(diameter, "Nozzle diameter")
    default_material = ("stainless_steel" if diameter == 0.2 else "hardened_steel"
                        if diameter in {0.6, 0.8} else spec["default_nozzle_type"])
    return {"manufacturer": "Bambu Lab", "model": "Bambu hotend", "diameter": diameter,
            "material": material or default_material, "flow_class": _flow(flow_class), "custom": False,
            "compatible_printer_model": spec["id"], "stock_geometry_compatible": True}


def _canonical(printer: dict) -> dict:
    spec = get_model(printer.get("model"))
    declared = printer.get("nozzle")
    if declared is not None and not isinstance(declared, dict):
        raise ValueError("Nozzle facts must be an object. Confirm the actual installed nozzle.")
    if not declared:
        # Existing configurations described stock material and diameter only.
        # A declared custom/unknown hotend must never enter this legacy route.
        if printer.get("custom_nozzle") or printer.get("nozzle_custom"):
            raise ValueError("Custom nozzle facts are missing. Confirm manufacturer, model, geometry and calibrated limits.")
        declared = stock_nozzle_facts(spec["id"], printer.get("nozzle_diameter", 0.4),
                                     material=printer.get("nozzle_type"),
                                     flow_class=printer.get("nozzle_volume_type", "Standard"))
    if type(declared.get("custom")) is not bool:
        raise ValueError("Declare nozzle custom as true or false; unknown custom hardware is blocked.")
    facts = {key: declared[key] for key in _IDENTITY_FIELDS if key in declared}
    for key in ("manufacturer", "model"):
        if not isinstance(facts.get(key), str) or not facts[key].strip() or len(facts[key]) > 200:
            raise ValueError("Confirm the installed nozzle manufacturer and model.")
        facts[key] = facts[key].strip()
    facts["diameter"] = _number(facts.get("diameter"), "Nozzle diameter")
    if facts["diameter"] not in spec["supported_nozzle_diameters_mm"]:
        raise ValueError("Nozzle diameter needs an exact compatible installed Bambu Studio machine preset.")
    if not isinstance(facts.get("material"), str) or facts["material"] not in _MATERIALS:
        raise ValueError("Physical nozzle material is unsupported by this workflow's validated Bambu Studio adapter. "
                         "Do not label brass or tungsten as steel; use a verified custom Studio profile and manual launch.")
    facts["flow_class"] = _flow(facts.get("flow_class"))
    compatible = normalize_model(facts.get("compatible_printer_model"))
    if compatible != spec["id"]:
        raise ValueError("Nozzle compatibility must explicitly match this printer model.")
    facts["compatible_printer_model"] = compatible
    if facts.get("stock_geometry_compatible") is not True:
        raise ValueError("Confirm stock-compatible nozzle geometry, installed length and mount before automatic preparation.")
    if facts["flow_class"] == "HighFlow" and (
            spec["id"] not in _HIGH_FLOW_MODELS or facts["diameter"] == 0.2
            or facts["material"] != "hardened_steel"):
        raise ValueError("No validated factory HighFlow compatibility exists for this model, diameter and nozzle material.")
    if not facts["custom"]:
        if facts["manufacturer"].replace(" ", "").lower() not in {"bambu", "bambulab"}:
            raise ValueError("Aftermarket nozzle brands must be declared custom with calibrated limits.")
        if facts["diameter"] == 0.2 and facts["material"] != "stainless_steel":
            raise ValueError("The stock 0.2 mm nozzle is stainless steel; declare an aftermarket nozzle custom.")
        if spec["id"] in _HIGH_FLOW_MODELS and facts["diameter"] != 0.2 and facts["material"] != "hardened_steel":
            raise ValueError("This model's stock 0.4/0.6/0.8 mm nozzle is hardened steel; verify the installed hotend.")
        facts.pop("max_volumetric_speed", None)
        facts.pop("max_temperature_c", None)
    else:
        facts["max_volumetric_speed"] = _number(facts.get("max_volumetric_speed"), "Calibrated maximum volumetric speed")
        if facts["max_volumetric_speed"] > 100:
            raise ValueError("Calibrated maximum volumetric speed exceeds this adapter's 100 mm3/s validation bound.")
        facts["max_temperature_c"] = _number(facts.get("max_temperature_c"), "Custom nozzle maximum temperature")
        if facts["max_temperature_c"] > spec["nozzle_max_c"]:
            raise ValueError("Custom nozzle temperature cannot exceed the printer's thermal limit.")
    if "installation_id" in facts:
        if not isinstance(facts["installation_id"], str) or not facts["installation_id"].strip() or len(facts["installation_id"]) > 200:
            raise ValueError("Nozzle installation_id must be nonempty text of at most 200 characters.")
        facts["installation_id"] = facts["installation_id"].strip()
    for key in ("confirmed_at", "revision", "human_evidence"):
        if key in declared:
            facts[key] = declared[key]
    for key, actual in (("nozzle_diameter", facts["diameter"]), ("nozzle_type", facts["material"]),
                        ("nozzle_volume_type", facts["flow_class"])):
        if key not in printer:
            continue
        current = _number(printer[key], "Configured nozzle diameter") if key == "nozzle_diameter" else (
            _flow(printer[key]) if key == "nozzle_volume_type" else printer[key])
        if current != actual:
            raise ValueError("Structured nozzle facts contradict configured " + key + "; confirm the installed setup again.")
    return facts


def validate_nozzle(printer: dict, *, require_fresh: bool = False, now: float | None = None,
                    live_status: dict | None = None) -> dict:
    facts = _canonical(printer)
    if facts["custom"] and require_fresh:
        checked_at = _number(facts.get("confirmed_at"), "Custom nozzle confirmation timestamp")
        current = time.time() if now is None else now
        if not 0 <= current - checked_at <= CUSTOM_CONFIRMATION_MAX_AGE_SECONDS:
            raise ValueError("Custom nozzle confirmation expired or has a future timestamp. Recheck the installed nozzle within 30 minutes of start.")
        if not isinstance(facts.get("human_evidence"), str) or not facts["human_evidence"].strip():
            raise ValueError("A person's current observation of the installed custom nozzle is required.")
    if live_status is not None:
        # Check both decoded and legacy metadata. Neither may be overridden by a
        # human declaration when it contradicts physical dimensions/material.
        rows = [live_status]
        if isinstance(live_status.get("raw"), dict):
            rows.append(live_status["raw"])
        for row in rows:
            diameter = row.get("nozzle_diameter")
            if diameter not in (None, "") and _number(diameter, "Reported nozzle diameter") != facts["diameter"]:
                raise ValueError("Live nozzle diameter contradicts the confirmed installed nozzle.")
            material = row.get("nozzle_type")
            if material not in (None, "", "N/A"):
                if isinstance(material, str) and len(material) >= 4 and material not in _MATERIALS:
                    # Packed Bambu IDs are decoded by the shared telemetry parser.
                    from .transport import _nozzle_type
                    decoded, decoded_flow = _nozzle_type(material)
                    if decoded:
                        material = decoded
                        if decoded_flow and _flow(decoded_flow) != facts["flow_class"]:
                            raise ValueError("Live nozzle flow contradicts the confirmed installed nozzle.")
                if material != facts["material"]:
                    raise ValueError("Live nozzle material contradicts the confirmed installed nozzle.")
            flow = row.get("nozzle_flow", row.get("nozzle_volume_type"))
            if flow not in (None, "") and _flow(flow) != facts["flow_class"]:
                raise ValueError("Live nozzle flow contradicts the confirmed installed nozzle.")
    return facts


def _identity(facts: dict) -> dict:
    return {key: facts[key] for key in _IDENTITY_FIELDS if key in facts}


def nozzle_fingerprint(printer: dict) -> str:
    facts = validate_nozzle(printer)
    content = {**_identity(facts), "revision": facts.get("revision", 0)}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def confirm_nozzle(config, printer_id: str, facts: dict, human_evidence: str) -> dict:
    """Record a person's observation; no printer is contacted or controlled."""
    if not isinstance(facts, dict):
        raise ValueError("Provide the actual nozzle facts as an object.")
    if not isinstance(human_evidence, str) or not human_evidence.strip() or len(human_evidence) > 2000:
        raise ValueError("Describe the person's physical observation of the installed nozzle (up to 2000 characters).")
    confirmed: dict = {}

    def mutate(data: dict) -> None:
        matches = [p for p in data["printers"] if p["id"] == printer_id]
        if len(matches) != 1:
            raise ValueError("Choose an existing printer_id explicitly.")
        printer = matches[0]
        candidate = dict(printer)
        candidate["nozzle"] = dict(facts)
        # A new declaration replaces old dimensions instead of inheriting them.
        for key in ("nozzle_diameter", "nozzle_type", "nozzle_volume_type"):
            candidate.pop(key, None)
        normalized = _canonical(candidate)
        old = None
        try:
            old = validate_nozzle(printer)
        except ValueError:
            pass
        if old and "installation_id" not in normalized and "installation_id" in old:
            normalized["installation_id"] = old["installation_id"]
        revision = old.get("revision", 0) if old else 0
        if type(revision) is not int or revision < 0:
            revision = 0
        normalized["revision"] = revision + (0 if old and _identity(old) == _identity(normalized) and revision > 0 else 1)
        normalized["confirmed_at"] = time.time()
        normalized["human_evidence"] = human_evidence.strip()
        printer.update(nozzle=normalized, nozzle_diameter=normalized["diameter"], nozzle_type=normalized["material"],
                       nozzle_volume_type=normalized["flow_class"], nozzle_confirmed=True)
        confirmed.update(normalized)

    config.update(mutate)
    return {"printer_id": printer_id, "nozzle": confirmed, "nozzle_fingerprint": nozzle_fingerprint(config.printer(printer_id)),
            "physical_check_required_before_start": confirmed["custom"],
            "confirmation_max_age_seconds": CUSTOM_CONFIRMATION_MAX_AGE_SECONDS if confirmed["custom"] else None}
