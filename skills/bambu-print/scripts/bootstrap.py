"""Install the pinned local MCP runtime. Standard library only; never prints."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

VERSION = "0.5.1"
REPOSITORY = "netuneed/bambu-workflow-mcp"
PREFIX = "bambu-workflow-mcp-" + VERSION
MAX_ARCHIVE = 8 * 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024


def platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    raise ValueError("This lab installer supports Windows and macOS.")


def checksum(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("An explicit SHA256 with 64 hexadecimal characters is required.")
    return value.lower()


class GitHubRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or not (host == "github.com" or host == "api.github.com"
                        or host.endswith(".githubusercontent.com"))):
            raise ValueError("The release download redirected outside GitHub HTTPS.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, limit: int) -> bytes:
    # No GitHub token, printer secret or credentials are sent by this downloader.
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "api.github.com"}:
        raise ValueError("Only the project's GitHub HTTPS release endpoints are allowed.")
    request = urllib.request.Request(url, headers={"User-Agent": "bambu-workflow-bootstrap/" + VERSION,
                                                 "Accept": "application/vnd.github+json"})
    opener = urllib.request.build_opener(GitHubRedirects())
    with opener.open(request, timeout=45) as response:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > limit:
            raise ValueError("Release download exceeds its size limit.")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Release download exceeds its size limit.")
    return data


def release_asset(release: dict, name: str) -> dict:
    assets = [a for a in release.get("assets", []) if a.get("name") == name]
    if len(assets) != 1:
        raise ValueError("The pinned release must contain exactly one " + name)
    asset = assets[0]
    url = f"https://github.com/{REPOSITORY}/releases/download/v{VERSION}/{name}"
    if asset.get("browser_download_url") != url:
        raise ValueError("Release asset URL does not match the pinned repository and tag.")
    digest = asset.get("digest", "")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("GitHub did not supply a SHA256 digest for the pinned asset.")
    checksum(digest[7:])
    return asset


def manifest_entries(data: bytes) -> dict[str, str]:
    result = {}
    for line in data.decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", line)
        if not match or match[2] in result:
            raise ValueError("Invalid or duplicated checksum manifest entry.")
        result[match[2]] = checksum(match[1])
    if not result:
        raise ValueError("The checksum manifest is empty.")
    return result


def download_bundle(target: str) -> tuple[bytes, str]:
    release = json.loads(fetch(f"https://api.github.com/repos/{REPOSITORY}/releases/tags/v{VERSION}", 1024 * 1024))
    if release.get("tag_name") != "v" + VERSION or release.get("draft") or release.get("prerelease"):
        raise ValueError("The pinned stable release is not published.")
    name = f"{PREFIX}-{target}.zip"
    asset = release_asset(release, name)
    sums = release_asset(release, "SHA256SUMS-" + VERSION + ".txt")
    manifest = fetch(sums["browser_download_url"], 16 * 1024)
    if hashlib.sha256(manifest).hexdigest() != sums["digest"][7:].lower():
        raise ValueError("Release checksum manifest failed its GitHub digest check.")
    expected = manifest_entries(manifest).get(name)
    if expected != asset["digest"][7:].lower():
        raise ValueError("Release manifest and GitHub asset digests disagree.")
    data = fetch(asset["browser_download_url"], MAX_ARCHIVE)
    if len(data) != asset.get("size") or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("The release ZIP failed its size or SHA256 check.")
    return data, expected


def validate_bundle(path: Path, expected: str, target: str) -> list[str]:
    if path.stat().st_size > MAX_ARCHIVE or hashlib.sha256(path.read_bytes()).hexdigest() != checksum(expected):
        raise ValueError("The local release ZIP failed its size or SHA256 check.")
    prefix = PREFIX + "/"
    with zipfile.ZipFile(path) as archive:
        items = archive.infolist()
        if not items or len(items) > 512 or sum(i.file_size for i in items) > MAX_EXPANDED:
            raise ValueError("The release archive exceeds its member or expanded size limit.")
        seen = set()
        files = []
        for item in items:
            name = item.filename
            parts = PurePosixPath(name).parts
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (not name.startswith(prefix) or "\\" in name or ":" in name or "\x00" in name
                    or name.startswith("/") or any(p in {".", ".."} for p in parts)
                    or str(PurePosixPath(name)) != name.rstrip("/")
                    or "\x00" in item.orig_filename
                    or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts)
                    or name.casefold() in seen or item.flag_bits & 1
                    or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or any(p.endswith((".", " ")) for p in parts)):
                raise ValueError("The release contains an unsafe, duplicate or non-regular member.")
            seen.add(name.casefold())
            if not item.is_dir():
                files.append(name)
        sums_name = prefix + "SHA256SUMS.txt"
        entries = manifest_entries(archive.read(sums_name))
        if set(files) != {prefix + n for n in entries} | {sums_name}:
            raise ValueError("The internal release manifest does not cover every file.")
        for name, digest in entries.items():
            if hashlib.sha256(archive.read(prefix + name)).hexdigest() != digest:
                raise ValueError("A bundled file failed its internal SHA256 check.")
        info = json.loads(archive.read(prefix + "release.json"))
        wheel = "wheel/bambu_workflow_mcp-" + VERSION + "-py3-none-any.whl"
        if (info.get("version") != VERSION or info.get("package") != "bambu-workflow-mcp"
                or info.get("target_platform") != target or info.get("contains_personal_configuration") is not False
                or info.get("wheel_sha256") != entries.get(wheel)):
            raise ValueError("The bundle identity, platform or wheel does not match this installer.")
        required = {wheel, "requirements.lock.txt", "scripts/install.ps1", "scripts/install.sh",
                    "scripts/call_mcp.py", "skills/bambu-print/SKILL.md",
                    "examples/acceptance-coupon-12x12x1.2mm.stl"}
        if not required.issubset(entries):
            raise ValueError("The release is missing required installation or first-print files.")
    return files


def unpack(path: Path, destination: Path, files: list[str]) -> Path:
    # destination is an exclusively created temporary directory. Never extractall.
    root = destination.resolve()
    with zipfile.ZipFile(path) as archive:
        for name in files:
            target = destination.joinpath(*PurePosixPath(name).parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError("An archive member escaped its private staging directory.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(archive.read(name))
    return destination / PREFIX


def installer_command(bundle: Path, args: argparse.Namespace, target: str) -> list[str]:
    if target == "windows":
        shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not shell:
            raise ValueError("Windows PowerShell is required for the local installer.")
        command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(bundle/"scripts/install.ps1"),
                   "-Python", sys.executable]
        options = (("-InstallRoot", args.install_root), ("-ConfigPath", args.config), ("-SkillRoot", args.skill_root))
        command += [v for option, value in options if value is not None for v in (option, str(value.resolve()))]
        if args.install_skill:
            command += ["-InstallSkill"]
        if args.resume:
            command += ["-Resume"]
    else:
        command = ["/bin/sh", str(bundle/"scripts/install.sh"), "--python", sys.executable]
        options = (("--install-root", args.install_root), ("--config", args.config), ("--skill-root", args.skill_root))
        command += [v for option, value in options if value is not None for v in (option, str(value.resolve()))]
        if args.install_skill:
            command += ["--install-skill"]
        if args.resume:
            command += ["--resume"]
    return command


def last_json(output: str) -> dict:
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"(?m)^\{", output))):
        try:
            result, _ = decoder.raw_decode(output[match.start():])
            if isinstance(result, dict) and isinstance(result.get("python"), str):
                return result
        except ValueError:
            pass
    raise ValueError("The installer did not return its verified runtime launcher.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, help="Use a locally downloaded release ZIP instead of the network.")
    parser.add_argument("--sha256", help="Required independently obtained checksum for --bundle.")
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--install-skill", action="store_true", help="Copy the print skill too; omit when it is already installed.")
    parser.add_argument("--skill-root", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--onboard", action="store_true", help="Run the local interactive setup wizard after installation. Never starts printing.")
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or newer is required. Install it separately before running setup.")
    if args.onboard and not sys.stdin.isatty():
        parser.error("Run --onboard in your local interactive terminal. Enter printer access codes there, never in chat.")
    if bool(args.bundle) != bool(args.sha256):
        parser.error("--bundle and --sha256 must be supplied together.")
    try:
        target = platform_name()
        with tempfile.TemporaryDirectory(prefix="bambu-workflow-setup-") as temporary:
            staging = Path(temporary)
            archive = staging / "release.zip"
            if args.bundle:
                if args.bundle.stat().st_size > MAX_ARCHIVE:
                    raise ValueError("The release ZIP exceeds its size limit.")
                shutil.copyfile(args.bundle, archive)
                expected = checksum(args.sha256)
            else:
                data, expected = download_bundle(target)
                archive.write_bytes(data)
            files = validate_bundle(archive, expected, target)
            bundle = unpack(archive, staging / "unpacked", files)
            result = subprocess.run(installer_command(bundle, args, target), capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=600)
            if result.returncode:
                # Installation paths and diagnostics stay local to this terminal.
                sys.stderr.write(result.stderr or result.stdout)
                raise ValueError("The local runtime installer failed; existing printer settings were preserved.")
            info = last_json(result.stdout)
        python = info["python"]
        command = [python, "-m", "bambu_workflow.cli"]
        config = args.config or info.get("config") or info.get("config_path")
        if config:
            command += ["--config", str(config)]
        command += ["onboard"]
        if args.onboard:
            return subprocess.run(command, check=False).returncode
        print(json.dumps({"installed": True, "version": VERSION, "python": python,
                          "runtime_dir": str(Path(python).parent.parent.parent),
                          "onboard_command": command, "codex_modified": False,
                          "printer_contacted": False, "print_started": False}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error), "print_started": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
