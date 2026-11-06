"""Explicit logical-filament to physical-feed bindings. Telemetry is metadata, not a spool sensor."""
from __future__ import annotations

from copy import deepcopy
import math
import re


def family(value) -> str:
    value = str(value or "").strip().upper()
    if value in {"PETG HF", "PETG-HF", "PETG - HF", "BAMBU PETG HF"}:
        return "PETG"
    return value


def color(value) -> str | None:
    if value in (None, ""):
        return None
    value = str(value).lstrip("#").upper()
    if re.fullmatch(r"[0-9A-F]{6}", value):
        value += "FF"
    if not re.fullmatch(r"[0-9A-F]{8}", value):
        raise ValueError("Filament color must be RRGGBB or RRGGBBAA hexadecimal.")
    return value


def normalize_feed(feed: dict) -> dict:
    if not isinstance(feed, dict):
        raise ValueError("Choose an explicit external or AMS feed.")
    if feed.get("kind") == "external":
        return {"kind": "external"}
    if feed.get("kind") != "ams":
        raise ValueError("Feed kind must be external or ams.")
    if "ams_id" in feed or "slot_id" in feed:
        aid, sid = feed.get("ams_id"), feed.get("slot_id")
        if type(aid) is not int or type(sid) is not int:
            raise ValueError("AMS unit and slot IDs must be explicit integers from material_inventory.")
        if 0 <= aid <= 3 and 0 <= sid <= 3:
            tid = aid * 4 + sid
        elif 128 <= aid <= 135 and sid == 0:
            # DevFilaSystem.cpp GetTrayId: AMS HT is not unit_id * 4.
            tid = 16 + aid - 128
        else:
            raise ValueError("Unsupported AMS topology. Use a reported four-slot unit or an AMS HT slot.")
        if "tray_id" in feed and feed["tray_id"] != tid:
            raise ValueError("Absolute AMS tray and unit/slot IDs disagree.")
        return {"kind": "ams", "ams_id": aid, "slot_id": sid, "tray_id": tid}
    tid = feed.get("tray_id")
    if type(tid) is not int or not 0 <= tid <= 23:
        raise ValueError("Choose a verified absolute AMS tray ID, or use explicit ams_id and slot_id.")
    return normalize_feed({"kind": "ams", "ams_id": tid // 4 if tid < 16 else 128 + tid - 16,
                           "slot_id": tid % 4 if tid < 16 else 0})


def _tray(tray: dict, feed: dict, source: str) -> dict:
    remaining = tray.get("remain_g")
    if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining < 0:
        remaining = None
    return {"type": family(tray.get("tray_type")) or None,
            "profile_id": tray.get("tray_info_idx") or None,
            "setting_id": tray.get("setting_id") or None,
            "color": color(tray.get("tray_color")),
            "spool_id": tray.get("tray_uuid") or tray.get("tag_uid") or None,
            "feed": feed, "source": source, "remaining_grams": remaining,
            "remaining": tray.get("remain"), "live_metadata_verified": True,
            "note": "Reported metadata cannot verify the physical spool, dryness or usable remaining mass."}


def inventory(status: dict) -> list[dict]:
    raw = status.get("raw", status)
    ams = raw.get("ams") or {}
    units = ams.get("ams", []) if isinstance(ams, dict) else []
    if not isinstance(units, list):
        raise ValueError("Unrecognized AMS inventory schema; inspect firmware telemetry before selecting a feed.")
    entries, seen = [], set()
    for unit in units:
        if not isinstance(unit, dict) or not isinstance(unit.get("tray", []), list):
            raise ValueError("Unrecognized AMS unit schema.")
        try:
            aid = int(unit["id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Reported AMS unit lacks an unambiguous integer ID.") from None
        for tray in unit.get("tray", []):
            try:
                sid = int(tray["id"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("Reported AMS slot lacks an unambiguous integer ID.") from None
            feed = normalize_feed({"kind": "ams", "ams_id": aid, "slot_id": sid})
            key = (aid, sid)
            if key in seen:
                raise ValueError("Duplicate AMS unit/slot telemetry is ambiguous.")
            seen.add(key)
            item = _tray(tray, feed, "printer_ams_metadata")
            item["ams_model"] = unit.get("model") or unit.get("type")
            for key in ("humidity", "humidity_raw", "temp", "dry_time"):
                if key in unit:
                    item[key] = unit[key]
            if ams.get("tray_exist_bits") is not None:
                try:
                    bits = int(str(ams["tray_exist_bits"]), 16)
                except (ValueError, TypeError):
                    raise ValueError("AMS presence bitmap is invalid; refresh inventory before selecting a feed.") from None
                item["reported_present"] = bool(bits & (1 << feed["tray_id"]))
            entries.append(item)
    external = raw.get("vt_tray")
    if isinstance(external, dict):
        entries.append(_tray(external, {"kind": "external"}, "printer_external_metadata"))
    return entries


def live_material(status: dict, feed: dict | None = None) -> dict:
    raw = status.get("raw", status)
    if feed is None:
        now = str((raw.get("ams") or {}).get("tray_now", "254"))
        if now == "254":
            feed = {"kind": "external"}
        elif now.isdecimal() and 0 <= int(now) <= 23:
            feed = {"kind": "ams", "tray_id": int(now)}
        else:
            raise ValueError("No unambiguous active feed. Choose a physical slot from material_inventory.")
    selected = normalize_feed(feed)
    for item in inventory(status):
        if item["feed"] == selected:
            return item
    if selected["kind"] == "external":
        return _tray({}, selected, "printer_external_metadata")
    raise ValueError("Selected AMS tray is absent. Empty inventory never describes an external spool.")


def validate_requests(materials: list[dict] | None) -> list[dict] | None:
    if materials is None:
        return None
    if not isinstance(materials, list) or not 1 <= len(materials) <= 24:
        raise ValueError("materials must contain from 1 to 24 explicit logical filament entries.")
    result = []
    allowed = {"filament_id", "type", "preset", "profile_id", "color", "feed", "confirmation_evidence"}
    for index, value in enumerate(materials, 1):
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("Unknown material-plan fields.")
        if type(value.get("filament_id")) is not int or value["filament_id"] != index:
            raise ValueError("materials must enumerate the complete project palette in contiguous one-based filament_id order.")
        kind = family(value.get("type"))
        if not kind or not isinstance(value.get("preset"), str) or not value["preset"].strip():
            raise ValueError("Each logical filament needs a polymer type and exact product/preset.")
        entry = deepcopy(value)
        entry.update(type=kind, color=color(value.get("color")), feed=normalize_feed(value.get("feed")))
        if len(materials) > 1 and entry["feed"]["kind"] != "ams":
            raise ValueError("Multicolor automatic feed requires AMS for every logical filament.")
        if entry["feed"]["kind"] == "ams" and kind == "TPU" and entry.get("profile_id") not in {"GFU98", "GFU02"}:
            raise ValueError("Generic flexible TPU is not AMS-compatible. Select a verified TPU for AMS product or the external spool.")
        result.append(entry)
    if len({v["type"] for v in result}) > 1:
        raise ValueError("Mixed-polymer recipes need a validated compatibility adapter. Prepare the native Studio project; ABS and PETG must not be combined automatically.")
    return result


def bind_materials(status: dict, printer: dict, requests: list[dict]) -> list[dict]:
    bound = []
    for request in validate_requests(requests) or []:
        live = live_material(status, request["feed"])
        if live.get("reported_present") is False:
            raise ValueError("Selected AMS slot reports no filament present.")
        if printer["model"] == "p2s" and request["feed"]["kind"] == "ams" and request["feed"]["tray_id"] > 19:
            raise ValueError("P2S supports four four-slot AMS units plus four AMS HT units (20 feeds). This selected topology is unsupported.")
        if not live.get("type"):
            raise ValueError("Selected material metadata is unknown. Set the actual spool on this printer before preparation.")
        if live["type"] != request["type"]:
            raise ValueError("Requested polymer differs from the selected physical feed metadata.")
        for key in ("profile_id", "color"):
            if request.get(key) and request[key] != live.get(key):
                raise ValueError(f"Selected feed {key} differs from the requested material plan.")
        if request["feed"]["kind"] == "ams" and live.get("ams_model"):
            if "lite" in str(live["ams_model"]).lower() and printer["model"] == "p2s":
                raise ValueError("AMS lite is not compatible with P2S.")
        entry = {**live, **request, "binding": {k: live.get(k) for k in ("type", "profile_id", "color", "spool_id", "setting_id")}}
        if not request.get("color"):
            entry["color"] = live.get("color")
        bound.append(entry)
    return bound


def validate_bindings(status: dict, printer: dict, materials: list[dict]):
    for expected in materials:
        current = live_material(status, expected["feed"])
        if current.get("reported_present") is False:
            raise ValueError("Selected AMS slot no longer reports filament present.")
        binding = expected.get("binding") or {k: expected.get(k) for k in ("type", "profile_id", "color", "spool_id", "setting_id")}
        for key, value in binding.items():
            if value not in (None, "", "0", "0000000000000000") and current.get(key) != value:
                raise ValueError(f"Selected feed {key} changed or is no longer reported since preparation. Prepare a new job after checking the spool.")
        if not current.get("type"):
            raise ValueError("Selected feed is empty or its material metadata is unknown.")


def launch_mapping(materials: list[dict]) -> dict:
    feeds = [normalize_feed(m["feed"]) for m in materials]
    if len(feeds) == 1 and feeds[0]["kind"] == "external":
        return {"use_ams": False, "ams_mapping": []}
    if any(v["kind"] != "ams" for v in feeds):
        raise ValueError("Mixed external/AMS automatic feeding is not implemented.")
    return {"use_ams": True, "ams_mapping": [v["tray_id"] for v in feeds],
            "ams_mapping2": [{"ams_id": v["ams_id"], "slot_id": v["slot_id"]} for v in feeds]}
