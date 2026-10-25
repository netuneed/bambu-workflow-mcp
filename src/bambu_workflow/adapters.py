"""Explicit adapter registry. A new brand implements the protocol and registers a factory here."""
from typing import Protocol, Callable
from pathlib import Path


class PrinterAdapter(Protocol):
    def snapshot(self, timeout: float = 12) -> dict: ...
    def list_files(self, path: str = "cache") -> list: ...
    def upload(self, local: Path, remote: str, expected_sha256: str) -> dict: ...
    def start(self, remote: str, plate: int = 1, job_name: str = "", options: dict | None = None) -> dict: ...
    def command(self, action: str, **kwargs) -> dict: ...
    def camera(self, output: Path) -> dict: ...
    def probe(self) -> dict: ...


_registry: dict[str,Callable] = {}


def register_adapter(name: str, factory: Callable):
    if name in _registry:
        raise ValueError("Adapter is already registered: "+name)
    _registry[name]=factory


def create_adapter(printer: dict, secret: str) -> PrinterAdapter:
    from .transport import BambuLAN
    if "bambu_lan" not in _registry:
        register_adapter("bambu_lan",BambuLAN)
    name=printer.get("adapter","bambu_lan")
    if name not in _registry:
        raise ValueError("Unsupported printer adapter: "+name)
    return _registry[name](printer,secret)
