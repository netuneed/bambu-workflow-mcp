#!/bin/sh
# User-local macOS/POSIX installation. No sudo, printer connection or Codex edits.
set -eu
runtime_python=${BAMBU_WORKFLOW_PYTHON:-python3}
expect_python=false
for argument in "$@"; do
    if [ "$expect_python" = true ]; then
        runtime_python=$argument
        expect_python=false
    else
        case "$argument" in
            --python) expect_python=true ;;
            --python=*) runtime_python=${argument#--python=} ;;
        esac
    fi
done
if [ "$expect_python" = true ]; then
    printf '%s\n' '--python requires an executable path.' >&2
    exit 2
fi
bundle_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$runtime_python" -I - "$bundle_root" "$@" <<'PY'
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12 or newer is required. Use --python /path/to/python3.12.")

bundle = Path(sys.argv[1]).resolve()
default_data = (Path.home() / "Library" / "Application Support" / "BambuWorkflow" if sys.platform == "darwin"
                else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "BambuWorkflow")
parser = argparse.ArgumentParser(description="Install a private Bambu Workflow runtime; never contacts a printer.")
parser.add_argument("--python", help="Python 3.12+ executable (also selected by the shell wrapper).")
parser.add_argument("--wheel-path", type=Path)
parser.add_argument("--install-root", type=Path, default=default_data / "app")
parser.add_argument("--config", "--config-path", type=Path)
parser.add_argument("--wheelhouse", type=Path)
parser.add_argument("--install-skill", action="store_true")
parser.add_argument("--skill-root", type=Path)
parser.add_argument("--resume", action="store_true", help="Resume this exact wheel after an interrupted install.")
args = parser.parse_args(sys.argv[2:])


def fail(message):
    raise SystemExit(message + " Existing configuration, jobs and secrets were preserved.")


def child(parent, path):
    parent, resolved = parent.resolve(), path.resolve()
    if resolved == parent or not resolved.is_relative_to(parent):
        fail("Refusing a path outside the intended installation directory.")
    return resolved


def write_json(path, value):
    temporary = path.with_name(path.name + ".new")
    if path.is_symlink() or temporary.is_symlink():
        fail("Refusing a symbolic link in runtime metadata.")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def invoke(command, **kwargs):
    return subprocess.run([str(item) for item in command], check=True, **kwargs)


wheel = args.wheel_path
if wheel is None:
    candidates = list((bundle / "wheel").glob("bambu_workflow_mcp-*.whl"))
    if len(candidates) != 1:
        fail("Supply --wheel-path, or use an extracted release with exactly one project wheel.")
    wheel = candidates[0]
wheel = wheel.expanduser().resolve()
match = re.fullmatch(r"bambu_workflow_mcp-([0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+]*)-[^-]+-[^-]+-[^-]+\.whl", wheel.name)
if not wheel.is_file() or not match or ".." in match[1]:
    fail("The wheel filename must identify one safe Bambu Workflow release.")
version = match[1]
wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
manifest = bundle / "SHA256SUMS.txt"
if manifest.is_file():
    try:
        wheel_relative = wheel.relative_to(bundle).as_posix()
    except ValueError:
        fail("A release checksum manifest requires its own bundled wheel.")
    hashes = {line.split("  ", 1)[1]: line.split("  ", 1)[0]
              for line in manifest.read_text(encoding="utf-8").splitlines() if "  " in line}
    if hashes.get(wheel_relative) != wheel_hash:
        fail("The bundled project wheel checksum does not match SHA256SUMS.txt.")
with zipfile.ZipFile(wheel) as archive:
    metadata_paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
    if len(metadata_paths) != 1:
        fail("The project wheel has ambiguous metadata.")
    metadata = archive.read(metadata_paths[0]).decode("utf-8")
    if "\nName: bambu-workflow-mcp\n" not in "\n" + metadata or f"\nVersion: {version}\n" not in "\n" + metadata:
        fail("The wheel metadata does not match its filename.")

root = args.install_root.expanduser().resolve()
target_path = root / version
if target_path.is_symlink():
    fail("Refusing a symbolic link as the version directory.")
target = child(root, target_path)
marker_path = target / ".bambu-workflow-runtime.json"
marker = None
if target.exists():
    if not marker_path.is_file() or marker_path.is_symlink():
        fail("Refusing to modify a directory without its runtime ownership marker.")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if (marker.get("package"), marker.get("version"), marker.get("wheel_sha256")) != ("bambu-workflow-mcp", version, wheel_hash):
        fail("This version directory belongs to another wheel. Use a new version or installation root.")
    if marker.get("status") != "complete" and not args.resume:
        fail("Installation was interrupted. Use --resume with the exact same wheel.")
else:
    target.mkdir(parents=True)
    marker = {"package": "bambu-workflow-mcp", "version": version, "wheel_sha256": wheel_hash, "status": "installing"}
    write_json(marker_path, marker)

venv = target / ".venv"
if venv.is_symlink():
    fail("Refusing a symbolic link as the private virtual environment.")
for name in ("bin", "Scripts", "lib", "include", "pyvenv.cfg"):
    if (venv / name).is_symlink():
        fail("Refusing a symbolic link redirecting the virtual environment layout.")
runtime = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
reused = marker.get("status") == "complete"
if not reused:
    base_python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    invoke([base_python, "-m", "venv", venv])
prefix = json.loads(invoke([runtime, "-I", "-c", "import json,sys; print(json.dumps(dict(prefix=sys.prefix,base=sys.base_prefix)))"],
                          capture_output=True, text=True).stdout)
if Path(prefix["prefix"]).resolve() != venv.resolve() or prefix["prefix"] == prefix["base"]:
    fail("The selected runtime does not belong to this private virtual environment.")
if not reused:
    pip = [runtime, "-m", "pip", "install", "--disable-pip-version-check"]
    constraints = bundle / "requirements.lock.txt"
    if constraints.is_file():
        pip += ["--constraint", constraints]
    if args.wheelhouse:
        pip += ["--no-index", "--find-links", args.wheelhouse.expanduser().resolve()]
    invoke(pip + [wheel])
invoke([runtime, "-m", "pip", "check"])
environment = os.environ.copy()
environment["BAMBU_WORKFLOW_SKIP_RECOVERY"] = "1"
config_choice = args.config or marker.get("config_path") or os.environ.get("BAMBU_WORKFLOW_CONFIG")
config = str(Path(config_choice).expanduser().resolve()) if config_choice else None
if config:
    environment["BAMBU_WORKFLOW_CONFIG"] = config
inspection = (
    "import asyncio,json,os,sys,keyring; from bambu_workflow import __version__; "
    "from bambu_workflow.server import mcp; "
    "print(json.dumps(dict(version=__version__,python=os.path.abspath(sys.executable),"
    "tool_count=len(asyncio.run(mcp.list_tools())),keyring_backend=type(keyring.get_keyring()).__module__)))"
)
info = json.loads(invoke([runtime, "-I", "-c", inspection], capture_output=True, text=True, env=environment).stdout)
if info["version"] != version or info["tool_count"] < 1:
    fail("The installed runtime did not pass offline metadata verification.")

for name in ("docs", "skills", "examples"):
    source = bundle / name
    destination = target / name
    if source.is_dir() and source.resolve() != destination.resolve():
        if destination.is_symlink() or (destination.exists() and any(path.is_symlink() for path in destination.rglob("*"))):
            fail("Refusing a symbolic link as an installed asset directory.")
        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
tools = target / "tools"
if tools.is_symlink():
    fail("Refusing a symbolic link as the installed tools directory.")
tools.mkdir(exist_ok=True)
for name in ("install.sh", "install.ps1", "call_mcp.py", "native_slicer_smoke.py"):
    source, destination = bundle / "scripts" / name, tools / name
    if source.is_file() and source.resolve() != destination.resolve():
        if destination.is_symlink():
            fail("Refusing a symbolic link as an installed tool.")
        shutil.copy2(source, destination)

if args.install_skill:
    source = bundle / "skills" / "bambu-print"
    if not (source / "SKILL.md").is_file():
        fail("The complete bambu-print skill is absent from this release.")
    skill_root = (args.skill_root or (Path(os.environ["CODEX_HOME"]) / "skills" if os.environ.get("CODEX_HOME")
                                    else Path.home() / ".agents" / "skills")).expanduser().resolve()
    skill = skill_root / "bambu-print"
    if source.resolve() == skill.resolve():
        fail("Choose a skill installation directory different from the bundled source.")
    if skill.is_symlink():
        fail("Refusing to replace a symbolic link as an installed skill.")
    child(skill_root, skill)
    skill_root.mkdir(parents=True, exist_ok=True)
    if skill.exists():
        backup_root = skill_root.parent / "skill-backups"
        backup_root.mkdir(exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = child(backup_root, backup_root / ("bambu-print-" + stamp))
        skill.rename(backup)
    shutil.copytree(source, skill)
    info["skill_path"] = str(skill)

marker.update(status="complete", config_path=config, verified=info)
write_json(marker_path, marker)
info.update(install_dir=str(target), config_path=config, reused=reused, codex_modified=False, printer_contacted=False)
print(json.dumps(info, indent=2))
PY
