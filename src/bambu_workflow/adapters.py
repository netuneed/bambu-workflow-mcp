"""Explicit adapter registry. A new brand implements the protocol and registers a factory here."""
from typing import Protocol, Callable
from pathlib import Path
import re


class PrinterAdapter(Protocol):
    def capabilities(self) -> dict: ...
    def remote_path(self, filename: str) -> str: ...
    def snapshot(self, timeout: float = 12) -> dict: ...
    def list_files(self, path: str | None = None) -> list: ...
    def upload(self, local: Path, remote: str, expected_sha256: str) -> dict: ...
    def start(self, remote: str, plate: int = 1, job_name: str = "", options: dict | None = None) -> dict: ...
    def command(self, action: str, **kwargs) -> dict: ...
    def camera(self, output: Path) -> dict: ...
    def probe(self) -> dict: ...


_registry: dict[str,Callable] = {}


def register_adapter(name: str, factory: Callable):
    if not isinstance(name,str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}",name) or not callable(factory):
        raise ValueError("An adapter needs a simple lowercase name and a callable factory.")
    if name in _registry:
        raise ValueError("Adapter is already registered: "+name)
    _registry[name]=factory


def registered_adapters() -> list[str]:
    return sorted({"bambu_lan", *_registry})


def create_adapter(printer: dict, secret: str) -> PrinterAdapter:
    from .transport import BambuLAN
    if "bambu_lan" not in _registry:
        register_adapter("bambu_lan",BambuLAN)
    name=printer.get("adapter","bambu_lan")
    if name not in _registry:
        raise ValueError("Unsupported printer adapter: "+name)
    return _registry[name](printer,secret)
