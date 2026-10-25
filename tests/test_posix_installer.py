"""Installer refusals must happen before pip or any printer/configuration access."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


INSTALLER = Path(__file__).parents[1] / "scripts" / "install.sh"
BODY = INSTALLER.read_text(encoding="utf-8").split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


@pytest.fixture
def release(tmp_path):
    bundle = tmp_path / "public release"
    bundle.mkdir()
    wheel = bundle / "bambu_workflow_mcp-0.3.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("bambu_workflow_mcp-0.3.0.dist-info/METADATA",
                         "Metadata-Version: 2.3\nName: bambu-workflow-mcp\nVersion: 0.3.0\nRequires-Python: >=3.12\n")
    root = tmp_path / "isolated app"
    config = tmp_path / "existing config.json"
    config.write_text('{"private_state":"must remain unchanged"}', encoding="utf-8")

    def invoke(*extra):
        before = config.read_bytes()
        result = subprocess.run([sys.executable, "-", str(bundle), "--wheel-path", str(wheel),
                                 "--install-root", str(root), "--config", str(config), *extra],
                                input=BODY, text=True, capture_output=True)
        assert config.read_bytes() == before
        return result

    return bundle, wheel, root, invoke


def marker(wheel, target, **extra):
    value = {"package": "bambu-workflow-mcp", "version": "0.3.0",
             "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "status": "installing", **extra}
    (target / ".bambu-workflow-runtime.json").write_text(json.dumps(value), encoding="utf-8")


def test_embedded_installer_is_valid_python():
    compile(BODY, str(INSTALLER), "exec")


def test_unmarked_version_is_preserved(release):
    _, _, root, invoke = release
    target = root / "0.3.0"
    target.mkdir(parents=True)
    sentinel = target / "user file.txt"
    sentinel.write_text("unrelated", encoding="utf-8")
    result = invoke()
    assert result.returncode != 0 and "ownership marker" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "unrelated"
    assert not (target / ".venv").exists()


def test_same_version_different_wheel_is_refused(release):
    _, wheel, root, invoke = release
    target = root / "0.3.0"
    target.mkdir(parents=True)
    marker(wheel, target, wheel_sha256="0" * 64, status="complete")
    result = invoke()
    assert result.returncode != 0 and "another wheel" in result.stderr
    assert not (target / ".venv").exists()


def test_interrupted_install_needs_explicit_resume(release):
    _, wheel, root, invoke = release
    target = root / "0.3.0"
    target.mkdir(parents=True)
    marker(wheel, target)
    result = invoke()
    assert result.returncode != 0 and "--resume" in result.stderr
    assert not (target / ".venv").exists()


def test_bundle_checksum_failure_does_not_create_runtime(release):
    bundle, wheel, root, invoke = release
    (bundle / "SHA256SUMS.txt").write_text("0" * 64 + "  " + wheel.name + "\n", encoding="utf-8")
    result = invoke()
    assert result.returncode != 0 and "checksum" in result.stderr
    assert not root.exists()


@pytest.mark.parametrize("link_location", ["version", "venv"])
def test_symbolic_link_cannot_redirect_installation(release, tmp_path, link_location):
    _, wheel, root, invoke = release
    outside = tmp_path / "unrelated"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    root.mkdir()
    target = root / "0.3.0"
    if link_location == "venv":
        target.mkdir()
        marker(wheel, target)
    link = target if link_location == "version" else target / ".venv"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This host does not permit creating test symlinks")
    result = invoke("--resume")
    assert result.returncode != 0 and "symbolic link" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
