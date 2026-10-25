"""Bambu model facts and operation support, separate from hardware verification.

Model IDs and thermal limits were checked against Bambu Studio's official
resources at SOURCE_REVISION. Build-volume limits are nominal; the resolved
installed slicer preset can impose a smaller usable region or height.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
from urllib.parse import quote


SOURCE_REVISION = "79df7ae309be398ed30c9f397c13c9911c2edc57"
SOURCE_URL = f"https://github.com/bambulab/BambuStudio/tree/{SOURCE_REVISION}/resources"
_DUAL_LIMIT = (
    "Automatic launch requires validated per-tool G-code, physical-nozzle telemetry and filament-to-nozzle mappings. "
    "That multi-tool launch adapter is not implemented; use Bambu Studio for this device's launch."
)
_ROWS = [
    # id, profile name, profile abbreviation, model ID, nominal volume, nozzle/bed C,
    # physical tools, camera protocol, managed upload directory, project-file URL route.
    ("a1mini", "A1 mini", "A1M", "N1", (180,180,180), 300,80,1,"tcp6000","cache","sdcard"),
    ("a1", "A1", "A1", "N2S", (256,256,256), 300,100,1,"tcp6000","","sdcard"),
    ("p1p", "P1P", "P1P", "C11", (256,256,256), 300,100,1,"tcp6000","cache","sdcard"),
    ("p1s", "P1S", "X1C", "C12", (256,256,256), 300,100,1,"tcp6000","cache","sdcard"),
    ("x1", "X1", "X1", "BL-P002", (256,256,256), 300,110,1,"rtsps322","cache","sdcard"),
    ("x1c", "X1 Carbon", "X1C", "BL-P001", (256,256,256), 300,110,1,"rtsps322","cache","sdcard"),
    ("x1e", "X1E", "X1C", "C13", (256,256,256), 320,110,1,"rtsps322","cache","sdcard"),
    ("p2s", "P2S", "P2S", "N7", (256,256,256), 300,110,1,"rtsps322","cache","ftp"),
    ("h2s", "H2S", "H2S", "O1S", (340,320,340), 350,120,1,"rtsps322","","ftp"),
    ("a2l", "A2L", "A2L", "N9", (330,320,325), 300,80,1,"tcp6000","cache","sdcard"),
    ("h2d", "H2D", "H2D", "O1D", (350,320,325), 350,120,2,"rtsps322","","ftp"),
    ("h2dpro", "H2D Pro", "H2DP", "O1E", (350,320,325), 350,120,2,"rtsps322","","ftp"),
    ("h2c", "H2C", "H2D", "O1C2", (330,320,325), 350,120,2,"rtsps322","","ftp"),
    ("x2d", "X2D", "X2D", "N6", (256,256,260), 300,120,2,"rtsps322","","native_emmc"),
]
_SERIAL_PREFIXES = {
    "030":"a1mini", "039":"a1", "01S":"p1p", "01P":"p1s",
    "00M":"x1c", "00W":"x1", "03W":"x1e", "22E":"p2s",
    "093":"h2s", "094":"h2d", "239":"h2dpro", "31B":"h2c",
}


def _record(row: tuple) -> dict:
    identity, name, short, model_id, volume, nozzle, bed, tools, camera, directory, route = row
    profile = "Bambu Lab " + name
    legacy_profile = identity in {"p1p","p1s","x1","x1c","x1e"}
    limits = []
    if tools > 1:
        limits.append(_DUAL_LIMIT)
        limits.append("Preparation preserves factory multi-tool profiles and supports one material with matching stock nozzles; it validates the actual used-tool workspace from sliced metadata.")
    if identity == "x2d":
        limits.append("The native eMMC file uploader is not implemented. A successful legacy FTPS read is not proof of a printable upload route.")
    if legacy_profile:
        limits.append("Official default machine presets currently reserve the top 6 mm (printable_height=250); resolved profile bounds are checked separately.")
    if identity in {"x1","x1c","x1e"}:
        limits.append("Bed control uses the conservative 110 C limit. Official specifications distinguish 110 C at 220 V from 120 C at 110 V.")
    return {
        "id":identity, "display_name":profile, "profile_model_name":profile,
        "profile_short_name":short, "bambu_model_id":model_id,
        "bambu_model_ids":[model_id] + (["O1C"] if identity == "h2c" else []),
        "build_volume_mm":list(volume),
        "profile_volume_mm":[256,256,250] if legacy_profile else ([256,256,261] if identity == "x2d" else list(volume)),
        "bed_exclude_areas_mm":[[[0,0],[18,0],[18,28],[0,28]]] if legacy_profile else [],
        "profile_extruder_printable_areas_mm":(
            [[[0,0],[325,0],[325,320],[0,320]], [[25,0],[volume[0],0],[volume[0],320],[25,320]]]
            if identity in {"h2d","h2dpro","h2c"} else
            [[[0,0],[256,0],[256,256],[0,256]], [[20.5,0],[256,0],[256,256],[20.5,256]]]
            if identity == "x2d" else []),
        "profile_extruder_heights_mm":[320,325] if identity in {"h2d","h2dpro","h2c"} else [261,256] if identity == "x2d" else [],
        "nozzle_max_c":nozzle, "bed_max_c":bed, "tool_count":tools,
        "camera_protocol":camera, "remote_directory":directory, "project_url_route":route,
        "start_protocol":"project_file" if tools == 1 else None,
        "storage_kind":"sdcard" if route == "sdcard" else "internal",
        "requires_sdcard":route == "sdcard", "requires_ffmpeg":camera == "rtsps322",
        "default_nozzle_type":"stainless_steel" if identity in {"a1mini","a1","a2l","p1p","p1s","x1"} else "hardened_steel",
        "supported_nozzle_diameters_mm":[0.2,0.4,0.6,0.8],
        "geometry_supported":True, "preparation_supported":True,
        "slicing_supported":True, "launch_supported":tools == 1,
        "software_supported":True, "hardware_verified":identity == "a1mini",
        "hardware_verified_operations":["telemetry","files","upload","launch","reconcile","camera"] if identity == "a1mini" else [],
        "tested_firmware":"01.08.01.00" if identity == "a1mini" else None,
        "reported_nozzle_optional":identity in {"p1p","p1s","x1","x1c","x1e"},
        "limitations":limits,
        "profile_source":f"https://github.com/bambulab/BambuStudio/blob/{SOURCE_REVISION}/resources/profiles/BBL/machine/{quote(profile)}.json",
        "features_source":f"https://github.com/bambulab/BambuStudio/blob/{SOURCE_REVISION}/resources/printers/{model_id}.json",
        "thermal_source":"https://cdn1.bambulab.com/documentation/quick-start-2024-01-17/Quick-Start-X1%26X1C_en.pdf" if identity in {"x1","x1c"} else
                         f"https://github.com/bambulab/BambuStudio/blob/{SOURCE_REVISION}/resources/printers/{model_id}.json",
        "source_revision":SOURCE_REVISION,
    }


MODEL_REGISTRY = {row[0]:_record(row) for row in _ROWS}


def _token(value: Any) -> str:
    text = re.sub(r"[\s_-]+", "", str(value or "")).lower()
    return text.removeprefix("bambulab")


_ALIASES: dict[str,str] = {}
for _identity, _spec in MODEL_REGISTRY.items():
    for _alias in [_identity, _spec["display_name"], *_spec["bambu_model_ids"]]:
        _ALIASES[_token(_alias)] = _identity
_ALIASES.update({"a1m":"a1mini", "a1mini":"a1mini", "x1carbon":"x1c", "h2dp":"h2dpro"})


def normalize_model(value: Any) -> str | None:
    """Resolve canonical names, vendor model IDs and friendly Bambu product names."""
    return _ALIASES.get(_token(value))


def get_model(value: Any) -> dict:
    identity = normalize_model(value)
    if identity is None:
        raise ValueError("Unsupported Bambu model. Choose an explicit entry from list_models().")
    return deepcopy(MODEL_REGISTRY[identity])


def list_models() -> list[dict]:
    return [deepcopy(value) for value in MODEL_REGISTRY.values()]


def infer_model_from_serial(serial: Any) -> str | None:
    """Known device serial prefixes only; never guess new-family prefixes."""
    return _SERIAL_PREFIXES.get(str(serial or "")[:3].upper())


def infer_model_from_info(info: dict, status: dict | None = None) -> str | None:
    """Prefer explicit live product/project/model identifiers over old board IDs."""
    status = status or {}
    values = [status.get(key) for key in ("model","model_id","printer_model","product_name")]
    values += [info.get(key) for key in ("model","model_id","printer_model","product_name")]
    modules = info.get("module", info.get("modules", []))
    if isinstance(modules, list):
        for module in modules:
            if isinstance(module, dict):
                values.extend(module.get(key) for key in ("product_name","project_name","model","model_id"))
        for module in modules:
            if isinstance(module, dict) and module.get("name") == "ota":
                values.append(infer_model_from_serial(module.get("sn")))
    for value in values:
        model = normalize_model(value)
        if model:
            return model
    return None


def capabilities_for(value: Any) -> dict:
    spec = get_model(value)
    single = spec["tool_count"] == 1
    start_reason = _DUAL_LIMIT
    if spec["id"] == "x2d":
        start_reason += " X2D also requires its native eMMC uploader, which is not implemented in this adapter."
    operations = {
        "telemetry":True, "files":True, "upload":spec["id"] != "x2d",
        "start":spec["launch_supported"], "pause":True, "resume":True, "cancel":True,
        "camera":True, "temperature":True, "bed_temperature":True,
        "nozzle_temperature":single, "fan":True, "speed":True,
        "ams_lite_inventory":spec["id"] in {"a1mini","a1","a2l"},
        "ams_inventory":True, "ams_drying":False, "chamber_heater":False,
        "auxiliary_fan":spec["id"] in {"p1s","x1c","x1e"},
        "automatic_part_removal":False,
    }
    return {
        **operations, "model":spec["id"], "geometry":True,
        "prepare":spec["preparation_supported"], "slice":spec["slicing_supported"],
        "protocol":"lan_mqtt_ftps", "camera_protocol":spec["camera_protocol"],
        "requires_sdcard":spec["requires_sdcard"], "storage_kind":spec["storage_kind"],
        "remote_directory":spec["remote_directory"],
        "software_supported":[name for name,supported in operations.items() if supported],
        "hardware_verified":["telemetry","files","upload_sha256","camera","start","printing_identity"] if spec["hardware_verified"] else [],
        "hardware_verified_operations":spec["hardware_verified_operations"],
        "hardware_status":"launch_and_first_layer_verified" if spec["hardware_verified"] else "software_only_not_hardware_verified",
        "tested_firmware":spec["tested_firmware"], "limitations":spec["limitations"],
        "start_block_reason":None if spec["launch_supported"] else start_reason,
        "operation_block_reasons":{
            "start":start_reason,
            "upload":"X2D printable files require its native eMMC uploader, which is not implemented in the LAN FTPS adapter.",
            "nozzle_temperature":"Multi-tool nozzle heating requires a validated physical-nozzle selector; only global bed temperature control is implemented for this model.",
        },
        "conditions":["Fresh authenticated device identity", "LAN access and Developer Mode when firmware requires unsigned-command authorization",
                      "Installed compatible Bambu Studio profiles", "Physical bed/material/nozzle setup"],
    }
