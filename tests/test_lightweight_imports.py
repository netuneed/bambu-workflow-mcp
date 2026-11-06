"""MCP utility calls must not load native geometry DLLs in the server event loop."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["cad", "preflight", "supports", "hardware", "projects", "project_settings"])
def test_utility_modules_do_not_import_geometry_libraries(module):
    code = ("import importlib,sys,json; importlib.import_module('bambu_workflow.'+sys.argv[1]); "
            "print(json.dumps([n for n in ['numpy','trimesh','bambu_workflow.geometry'] if n in sys.modules]))")
    result = subprocess.run([sys.executable, "-I", "-c", code, module], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_metadata_utility_invocation_without_configuration_or_geometry():
    code = ("import sys; from bambu_workflow.server import cad_import_guidance; "
            "result=cad_import_guidance(); assert result['qymcad']; "
            "assert 'numpy' not in sys.modules and 'trimesh' not in sys.modules")
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stderr
