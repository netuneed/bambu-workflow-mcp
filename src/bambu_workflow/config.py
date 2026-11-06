from __future__ import annotations

import json
import os
import sys
import math
import copy
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock


def _validate_config(data: dict) -> None:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported config version. Expected version=1.")
    printers = data.get("printers", [])
    if not isinstance(printers, list) or any(not isinstance(p, dict) for p in printers):
        raise ValueError("Configure printers as a list of objects.")
    ids = [p.get("id") for p in printers]
    if not ids or any(not isinstance(x, str) or not x.strip() for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("Configure at least one printer with a unique id.")
    for printer in printers:
        if not all(printer.get(x) for x in ("model", "host", "serial")):
            raise ValueError("Each printer needs model, host and serial.")
        if any(x in printer for x in ("access_code", "password", "token")):
            raise ValueError("Store access codes in the OS keyring or an environment variable.")
        if any(key in printer and (not isinstance(printer[key], str) or len(printer[key]) > 200)
               for key in ("name", "location")):
            raise ValueError("Printer name and location must be text of at most 200 characters.")


def update_config(path: str | Path, mutate: Callable[[dict], None], *, create: bool = False,
                  before_write: Callable[[], None] | None = None) -> dict:
    """Serialize local updates, back up the previous bytes and replace atomically.

    The mutation always sees the latest file, preserving unknown settings and secret
    references. The optional keyring write happens only after validation under the lock.
    """
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=10):
        if create and path.exists():
            raise ValueError("Configuration already exists. Use add-printer to preserve existing devices and secrets.")
        if not create and not path.is_file():
            raise ValueError("Configuration does not exist. Run configure first.")
        original = path.read_bytes() if path.exists() else None
        data = json.loads(original.decode("utf-8-sig")) if original is not None else {}
        mutate(data)
        _validate_config(data)
        payload = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if original is not None:
                backup = path.with_name(path.name + ".backup-" + str(time.time_ns()))
                with backup.open("xb") as stream:
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
            if before_write is not None:
                before_write()
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return copy.deepcopy(data)


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
        self._loaded_bytes = self.path.read_bytes()
        self.data = json.loads(self._loaded_bytes.decode("utf-8-sig"))
        _validate_config(self.data)
        self.state_dir = Path(self.data.get("state_dir", self.path.parent / "state")).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def printer(self, printer_id: str | None = None) -> dict[str, Any]:
        current_bytes = self.path.read_bytes()
        if current_bytes != self._loaded_bytes:
            refreshed = json.loads(current_bytes.decode("utf-8-sig"))
            _validate_config(refreshed)
            self.data = refreshed
            self._loaded_bytes = current_bytes
        printers = self.data["printers"]
        if printer_id is None:
            if len(printers) == 1:
                printer_id = printers[0]["id"]
            else:
                raise ValueError("Multiple printers are configured. Choose printer_id explicitly: " + ", ".join(p["id"] for p in printers))
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

    def update(self, mutate: Callable[[dict], None]) -> dict:
        self.data = update_config(self.path, mutate)
        return self.data

    def add_printer(self, printer: dict, *, before_write: Callable[[], None] | None = None) -> dict:
        candidate = copy.deepcopy(printer)

        def append(data: dict) -> None:
            if any(p["id"] == candidate.get("id") for p in data["printers"]):
                raise ValueError("Printer id already exists. Choose a unique id; existing entries are never replaced.")
            if any(str(p.get("serial", "")).casefold() == str(candidate.get("serial", "")).casefold()
                   for p in data["printers"]):
                raise ValueError("This printer serial is already configured. Select its existing printer_id.")
            data["printers"].append(candidate)

        self.data = update_config(self.path, append, before_write=before_write)
        return self.printer(candidate["id"])

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

