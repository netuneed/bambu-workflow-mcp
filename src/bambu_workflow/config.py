from __future__ import annotations

import json
import os
import sys
import math
from pathlib import Path
from typing import Any


def default_config_path() -> Path:
    if sys.platform == "darwin":
        preferred = Path.home() / "Library" / "Application Support" / "BambuWorkflow" / "config.json"
        legacy = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "BambuWorkflow" / "config.json"
        return legacy if legacy.is_file() and not preferred.exists() else preferred
    if sys.platform != "win32" and not os.environ.get("LOCALAPPDATA"):
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "BambuWorkflow" / "config.json"
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".config"))
    return base / "BambuWorkflow" / "config.json"


class Config:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("BAMBU_WORKFLOW_CONFIG", default_config_path())).resolve()
        self.data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if self.data.get("version") != 1:
            raise ValueError("Unsupported config version. Expected version=1.")
        printers = self.data.get("printers", [])
        ids = [p.get("id") for p in printers]
        if not ids or len(ids) != len(set(ids)) or any(not x for x in ids):
            raise ValueError("Configure at least one printer with a unique id.")
        for p in printers:
            if not all(p.get(x) for x in ("model", "host", "serial")):
                raise ValueError("Each printer needs model, host and serial.")
            if any(x in p for x in ("access_code", "password", "token")):
                raise ValueError("Store access codes in the OS keyring or an environment variable.")
        self.state_dir = Path(self.data.get("state_dir", self.path.parent / "state")).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def printer(self, printer_id: str | None = None) -> dict[str, Any]:
        printers = self.data["printers"]
        if printer_id is None:
            printer_id = self.data.get("default_printer")
            if not printer_id and len(printers) == 1:
                printer_id = printers[0]["id"]
        found = [p for p in printers if p["id"] == printer_id]
        if len(found) != 1:
            raise ValueError("Choose printer_id explicitly: " + ", ".join(p["id"] for p in printers))
        printer = dict(found[0])
        if printer.get("adapter", "bambu_lan") == "bambu_lan":
            from .models import normalize_model, get_model
            printer["model"] = normalize_model(printer["model"])
            model = get_model(printer["model"])
            limit=[min(a,b) for a,b in zip(model["build_volume_mm"],model.get("profile_volume_mm",model["build_volume_mm"]))]
            printer.setdefault("build_volume_mm", limit)
            volume=printer["build_volume_mm"]
            if (not isinstance(volume,(list,tuple)) or len(volume)!=3
                    or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0
                           or v>limit[i] for i,v in enumerate(volume))):
                raise ValueError("Configured build volume must be positive and cannot exceed this model's verified profile limits.")
        return printer

    def secret(self, printer: dict) -> str:
        env = printer.get("access_code_env")
        value = os.environ.get(env, "") if env else ""
        if not value and printer.get("secret_ref"):
            import keyring
            value = keyring.get_password("bambu-workflow", printer["secret_ref"]) or ""
        if not value:
            raise ValueError("Missing printer access code. Run bambu-workflow configure or set access_code_env.")
        return value

    def adapter(self, printer_id: str | None = None):
        from .adapters import create_adapter
        printer = self.printer(printer_id)
        return create_adapter(printer, self.secret(printer))

    def redact(self, value: str) -> str:
        for p in self.data["printers"]:
            for part in (p.get("host"), p.get("serial")):
                if part:
                    value = value.replace(str(part), "[private]")
            try:
                secret = self.secret(p)
                if secret:
                    value = value.replace(secret, "[secret]")
            except Exception:
                pass
        return value

