"""Install the optional reviewed Support Fins CLI outside the workflow package.

Downloads one pinned artifact, verifies its SHA256, and prints a configuration
snippet. Does not install Node, edit user configuration, or run geometry code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request


VERSION = "1.0.5"
SHA256 = "2ad0469d91d378baade31d785f2fba825d6fe7fbab115f17c6175ea6ab3b97f7"
SIZE_BYTES = 1471473
DOWNLOAD_URL = "https://github.com/gittrahan/support-fins/releases/download/v1.0.5/support-fins.mjs"


class _HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if urllib.parse.urlsplit(new_url).scheme != "https":
            raise urllib.error.HTTPError(new_url, code, "Only HTTPS release redirects are allowed", headers, response)
        return super().redirect_request(request, response, code, message, headers, new_url)


def _download(destination: Path) -> None:
    opener = urllib.request.build_opener(_HTTPSRedirects())
    request = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent":"bambu-workflow-support-fins-installer"})
    total = 0
    with opener.open(request, timeout=30) as response, destination.open("wb") as stream:
        while block := response.read(64 * 1024):
            total += len(block)
            if total > SIZE_BYTES:
                raise ValueError("Downloaded Support Fins artifact exceeds its pinned size.")
            stream.write(block)
        stream.flush()
        os.fsync(stream.fileno())
    if total != SIZE_BYTES:
        raise ValueError("Downloaded Support Fins artifact has an unexpected size.")


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()


def install_support_fins(directory: str | Path, node: str | Path) -> dict:
    destination = Path(directory).expanduser()
    command = Path(node).expanduser()
    if not destination.is_absolute() or not command.is_absolute():
        raise ValueError("Use absolute paths for the external install directory and Node executable.")
    destination = destination.resolve()
    package_root = Path(__file__).resolve().parents[1]
    if destination.is_relative_to(package_root):
        raise ValueError("Install this optional third-party tool outside the workflow repository/package directory.")
    command = command.resolve(strict=True)
    if not command.is_file():
        raise ValueError("Node must be an existing executable file.")
    env = {key:value for key,value in os.environ.items() if key in {"SystemRoot","SYSTEMROOT","WINDIR","TEMP","TMP","HOME","USERPROFILE"}}
    version = subprocess.run([str(command),"--version"], check=False, capture_output=True, text=True,
                             timeout=10, shell=False, env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    matched = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*",version.stdout)
    if version.returncode or not matched or tuple(map(int,matched.groups())) < (20,10,0):
        raise ValueError("Select a Node.js executable version 20.10 or newer.")
    destination.mkdir(parents=True,exist_ok=True)
    target = destination / ("support-fins-v"+VERSION+".mjs")
    if target.exists():
        if not target.is_file() or target.is_symlink() or target.stat().st_size!=SIZE_BYTES or _digest(target)!=SHA256:
            raise ValueError("An existing Support Fins file does not match the pin. It was not overwritten; choose a fresh directory or inspect the file.")
    else:
        descriptor, temporary = tempfile.mkstemp(prefix="support-fins-download-",suffix=".tmp",dir=destination)
        os.close(descriptor)
        staged = Path(temporary)
        try:
            _download(staged)
            if staged.stat().st_size!=SIZE_BYTES or _digest(staged)!=SHA256:
                raise ValueError("Support Fins checksum verification failed. No tool was installed or executed.")
            # Atomic, no-overwrite publication on both Windows and POSIX.
            os.link(staged,target)
        finally:
            staged.unlink(missing_ok=True)
    return {"installed":True,"version":VERSION,"sha256":SHA256,"node_version":version.stdout.strip(),
            "support_fins":{"command":str(command),"cli_path":str(target),"sha256":SHA256},
            "personal_config_modified":False,
            "next":"Copy the support_fins object into your existing workflow configuration. Geometry generation still requires an explicit user instruction and layer review."}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",required=True,type=Path,help="Absolute external tools directory")
    parser.add_argument("--node",required=True,type=Path,help="Absolute Node.js 20.10+ executable")
    arguments=parser.parse_args()
    try:
        print(json.dumps(install_support_fins(arguments.directory,arguments.node),indent=2))
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        parser.exit(1,"Support Fins installation failed: "+str(error)+"\n")


if __name__=="__main__":
    main()
