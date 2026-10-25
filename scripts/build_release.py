"""Build a compact public wheel bundle from the tested local dependency versions."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib
import uuid
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dependency_lock(requirements: list[str]) -> str:
    """Resolve only runtime dependency metadata from the environment already tested.

    Constraint lines do not force Windows-only dependencies onto other platforms.
    Optional development packages are omitted unless a runtime package requires them.
    """
    extras: dict[str, set[str]] = defaultdict(set)
    visited: dict[str, frozenset[str]] = {}
    versions: dict[str, str] = {}
    pending = [Requirement(value) for value in requirements]
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({"extra":""}):
            continue
        name = canonicalize_name(requirement.name)
        extras[name].update(requirement.extras)
        active = frozenset(extras[name])
        if visited.get(name) == active:
            continue
        package = metadata.distribution(name)
        visited[name] = active
        versions[name] = package.version
        if requirement.specifier and package.version not in requirement.specifier:
            raise RuntimeError(f"Installed {name} {package.version} does not satisfy {requirement.specifier}.")
        for value in package.requires or []:
            child = Requirement(value)
            if child.marker and not any(child.marker.evaluate({"extra":extra}) for extra in {"", *active}):
                continue
            child.marker = None
            pending.append(child)
    header = f"# Tested runtime constraints: CPython {platform.python_version()}, {platform.system()}.\n"
    return header + "".join(f"{name}=={versions[name]}\n" for name in sorted(versions))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--skip-build", action="store_true", help="Use the existing wheel for this exact project version.")
    arguments = parser.parse_args()
    root = arguments.source.resolve()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    output = (arguments.output_dir or root / "dist").resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f"bambu-workflow-mcp-{version}-windows.zip"
    if archive_path.exists():
        raise FileExistsError("The release ZIP already exists. Preserve it and select a new output directory/version.")
    if not arguments.skip_build:
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(output), str(root)], check=True)
    wheel = output / f"bambu_workflow_mcp-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise FileNotFoundError("The wheel for this exact version has not been built.")
    staging = output / (f"staging-{version}-" + uuid.uuid4().hex[:8])
    bundle = staging / f"bambu-workflow-mcp-{version}"
    (bundle / "wheel").mkdir(parents=True)
    (bundle / "scripts").mkdir()
    shutil.copy2(wheel, bundle / "wheel" / wheel.name)
    for name in ("README.md", "LICENSE", "config.example.json"):
        shutil.copy2(root / name, bundle / name)
    for name in ("install.ps1", "call_mcp.py"):
        shutil.copy2(root / "scripts" / name, bundle / "scripts" / name)
    for name in ("docs", "examples", "skills"):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, bundle / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    skill = bundle / "skills" / "bambu-print" / "SKILL.md"
    if not skill.is_file():
        raise FileNotFoundError("The public bambu-print skill must be present before release packaging.")
    (bundle / "requirements.lock.txt").write_text(dependency_lock(project["dependencies"]), encoding="utf-8")
    release = {"package":project["name"], "version":version, "built_utc":datetime.now(timezone.utc).isoformat(),
               "wheel_sha256":digest(wheel), "runtime_python":platform.python_version(),
               "runtime_platform":platform.system(), "contains_personal_configuration":False}
    (bundle / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    files = sorted(path for path in bundle.rglob("*") if path.is_file())
    (bundle / "SHA256SUMS.txt").write_text("".join(f"{digest(path)}  {path.relative_to(bundle).as_posix()}\n" for path in files), encoding="utf-8")
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    print(json.dumps({"archive":str(archive_path), "bytes":archive_path.stat().st_size,
                      "sha256":digest(archive_path), "wheel":str(wheel), "staging":str(bundle)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
