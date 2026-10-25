"""Exercise Windows installer config selection without creating a runtime."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


INSTALLER = Path(__file__).parents[1] / "scripts" / "install.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell installer")


@pytest.mark.parametrize("explicit,stored,expected", [
    (True,True,"explicit"), (False,True,"stored"), (False,False,"environment"),
])
def test_config_selection_preserves_marked_path_before_environment(tmp_path,explicit,stored,expected):
    wheel=tmp_path/"bambu_workflow_mcp-0.3.0-py3-none-any.whl"
    wheel.write_bytes(b"offline fixture: pip must not run during WhatIf")
    root=tmp_path/"app";target=root/"0.3.0";target.mkdir(parents=True)
    paths={key:tmp_path/f"{key} private config.json" for key in ("explicit","stored","environment")}
    for path in paths.values():path.write_text('{"private":"unchanged"}',encoding="utf-8")
    marker={"package":"bambu-workflow-mcp","version":"0.3.0","status":"installed",
            "wheel_sha256":hashlib.sha256(wheel.read_bytes()).hexdigest()}
    if stored:marker["config_path"]=str(paths["stored"])
    marker_path=target/".bambu-workflow-runtime.json"
    marker_path.write_text(json.dumps(marker),encoding="utf-8")
    before=marker_path.read_bytes()
    command=[POWERSHELL,"-NoProfile","-ExecutionPolicy","RemoteSigned","-File",str(INSTALLER),
             "-WheelPath",str(wheel),"-InstallRoot",str(root),"-WhatIf"]
    if explicit:command += ["-ConfigPath",str(paths["explicit"])]
    environment=dict(os.environ,BAMBU_WORKFLOW_CONFIG=str(paths["environment"]))
    # A Python child of PowerShell 7 inherits its incompatible module search
    # path. Use this selected shell's own modules, as a native launch would.
    environment["PSMODULEPATH"]=str(Path(POWERSHELL).parent/"Modules")
    result=subprocess.run(command,capture_output=True,text=True,env=environment,timeout=20)
    assert result.returncode == 0,result.stderr
    assert json.loads(result.stdout)["config"] == str(paths[expected])
    assert marker_path.read_bytes() == before
    assert not (target/".venv").exists()
    assert all(path.read_text(encoding="utf-8") == '{"private":"unchanged"}' for path in paths.values())
