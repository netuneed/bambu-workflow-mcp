"""Pinned optional support generation on synthetic geometry. No printer access."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
import trimesh

from bambu_workflow import supports


_MODEL = b'''<?xml version="1.0"?><model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources><object id="1" type="model"><mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object></resources><build><item objectid="1"/></build></model>'''
_TYPES = b'''<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'''


def archive(path):
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_DEFLATED) as target:
        target.writestr("[Content_Types].xml",_TYPES)
        target.writestr("3D/3dmodel.model",_MODEL)


def report(output, **changes):
    return {"output":str(output),"stats":{"unserved":0,"floating":0,"overhangRegions":1,
                                          "finTriangles":20,"padTriangles":12},"report":"Synthetic report",**changes}


@pytest.fixture
def fake_tool(tmp_path,monkeypatch):
    source=tmp_path/"source with spaces;$(literal).stl"
    trimesh.creation.box(extents=[6,6,40]).export(source)
    cli=tmp_path/"synthetic.mjs"
    cli.write_text("// Synthetic bytes, never executed")
    pin=hashlib.sha256(cli.read_bytes()).hexdigest()
    monkeypatch.setattr(supports,"SUPPORT_FINS_SHA256",pin)
    monkeypatch.setattr(supports,"_configured_tool",lambda config:(Path(sys.executable),cli))
    calls=[]
    def run(argv,cwd,**kwargs):
        calls.append((argv,cwd,kwargs))
        output=Path(argv[argv.index("--output")+1])
        archive(output)
        return 0,json.dumps(report(output)),""
    monkeypatch.setattr(supports,"_run_bounded",run)
    return source,tmp_path/"outputs",calls


def generate(rig,**kwargs):
    source,directory,_=rig
    arguments={"path":source,"output_dir":directory,"settings":{"rotation_deg":[0,35,0]},
               "geometry_change_authorized":True,"user_prompt":"Create a separate tilted print copy with fins.","config":{}}
    arguments.update(kwargs)
    return supports.generate_support_fins(**arguments)


@pytest.mark.parametrize("authorization,prompt",[(False,"Add fins"),("true","Add fins"),(True,""),(True,None)])
def test_generation_requires_explicit_geometry_authorization_before_tool_call(fake_tool,authorization,prompt):
    with pytest.raises(ValueError,match="explicit"):
        generate(fake_tool,geometry_change_authorized=authorization,user_prompt=prompt)
    assert not fake_tool[2] and not fake_tool[1].exists()


@pytest.mark.parametrize("settings",[{"shell":"run"},{"output":"overwrite.stl"},{"rotation_deg":[0,0]},
                                     {"rotation_deg":[0,float("nan"),0]},{"rotation_deg":[0,361,0]},
                                     {"nozzle":.5},{"layer_height":0},{"gap":.8},{"gap":True},{"mode":"manual"}])
def test_settings_are_whitelisted_and_finite(fake_tool,settings):
    with pytest.raises(ValueError):
        generate(fake_tool,settings=settings)
    assert not fake_tool[2]


def test_stl_generation_keeps_source_uses_fixed_argv_and_preserves_geometry_bytes(fake_tool):
    before=fake_tool[0].read_bytes()
    result=generate(fake_tool)
    assert fake_tool[0].read_bytes()==before
    assert result["source_sha256"]==hashlib.sha256(before).hexdigest()
    assert result["can_print"] is False and result["layer_review_required"] is True and result["printer_contacted"] is False
    assert result["support_mode"]=="baked_fins" and result["generation_issues"]==[]
    argv,cwd,_=fake_tool[2][0]
    assert argv[argv.index("--rot")+1]=="0,35,0"
    assert argv[argv.index("--nozzle")+1]=="0.4" and argv[argv.index("--layer-height")+1]=="0.2"
    assert argv[argv.index("--gap")+1]=="0.2" and argv[argv.index("--mode")+1]=="auto"
    assert Path(argv[2]).parent==cwd and Path(argv[2]).name=="source.stl"
    output=Path(result["output_path"])
    assert output.parent==cwd and Path(result["receipt_path"]).is_file()
    assert result["output_sha256"]==supports.sha256(output)
    with zipfile.ZipFile(output) as project:
        assert project.read("3D/3dmodel.model")==_MODEL
        assert b"application/json" in project.read("[Content_Types].xml")
    assert supports.read_support_receipt(output)["settings"]["rotation_deg"]==[0,35,0]


def test_each_generation_uses_a_new_folder_and_never_overwrites(fake_tool):
    first=generate(fake_tool)
    before=Path(first["output_path"]).read_bytes()
    second=generate(fake_tool)
    assert first["output_path"]!=second["output_path"]
    assert Path(first["output_path"]).read_bytes()==before


@pytest.mark.parametrize("stats,stderr",[(None,""),({},""),({"unserved":2,"floating":0},""),
                                         ({"unserved":0,"floating":1},""),
                                         ({"unserved":0,"floating":0},"warning: uncertain contact")])
def test_exit_zero_and_incomplete_or_bad_support_statistics_never_approve_print(fake_tool,monkeypatch,stats,stderr):
    def run(argv,cwd,**kwargs):
        output=Path(argv[argv.index("--output")+1]);archive(output)
        return 0,json.dumps(report(output,stats=stats)),stderr
    monkeypatch.setattr(supports,"_run_bounded",run)
    result=generate(fake_tool)
    assert result["can_print"] is False and result["generation_issues"]


def test_modified_geometry_invalidates_embedded_receipt(fake_tool):
    result=generate(fake_tool)
    path=Path(result["output_path"])
    changed=path.with_name("changed.3mf")
    with zipfile.ZipFile(path) as original,zipfile.ZipFile(changed,"w") as target:
        for item in original.infolist():
            content=original.read(item)
            if item.filename=="3D/3dmodel.model":content=content.replace(b'x="1"',b'x="2"')
            target.writestr(item,content)
    with pytest.raises(ValueError,match="changed after receipt"):
        supports.read_support_receipt(changed)


def test_untagged_archive_has_no_generated_receipt(tmp_path):
    path=tmp_path/"untagged.3mf";archive(path)
    assert supports.read_support_receipt(path) is None


def test_3mf_source_refuses_loss_of_project_data_before_tool_call(fake_tool):
    path=fake_tool[0].with_suffix(".3mf");archive(path)
    with pytest.raises(ValueError,match="preserve-project"):
        generate(fake_tool,path=path)
    assert not fake_tool[2]


def test_source_change_during_generation_is_rejected(fake_tool,monkeypatch):
    def run(argv,cwd,**kwargs):
        output=Path(argv[argv.index("--output")+1]);archive(output)
        fake_tool[0].write_bytes(b"changed external source")
        return 0,json.dumps(report(output)),""
    monkeypatch.setattr(supports,"_run_bounded",run)
    with pytest.raises(ValueError,match="Source changed during"):
        generate(fake_tool)


def test_pinned_script_change_before_execution_is_rejected(fake_tool,monkeypatch):
    original=supports.shutil.copyfile
    def copy(source,target):
        result=original(source,target)
        if Path(target).suffix==".mjs":Path(target).write_text("changed")
        return result
    monkeypatch.setattr(supports.shutil,"copyfile",copy)
    with pytest.raises(ValueError,match="script changed"):
        generate(fake_tool)
    assert not fake_tool[2]


@pytest.mark.parametrize("returncode,stdout,stderr",[(1,"","geometry failed"),(0,"not JSON",""),(0,"[]",""),
                                                    (0,'{"output":"unexpected.3mf"}',"")])
def test_malformed_or_failed_cli_never_creates_approved_receipt(fake_tool,monkeypatch,returncode,stdout,stderr):
    monkeypatch.setattr(supports,"_run_bounded",lambda *args,**kwargs:(returncode,stdout,stderr))
    with pytest.raises(ValueError):generate(fake_tool)
    assert not list(fake_tool[1].rglob("support-receipt.json"))


def test_unconfigured_unpinned_and_wrong_artifact_tools_are_rejected(tmp_path):
    with pytest.raises(ValueError,match="not configured"):
        supports._configured_tool({})
    with pytest.raises(ValueError,match="exact pinned"):
        supports._configured_tool({"support_fins":{"sha256":"wrong"}})
    cli=tmp_path/"wrong.mjs";cli.write_text("do not execute")
    config={"support_fins":{"sha256":supports.SUPPORT_FINS_SHA256,"command":sys.executable,"cli_path":str(cli)}}
    with pytest.raises(ValueError,match="does not match"):
        supports._configured_tool(config)


def test_node_environment_excludes_printer_secrets_and_startup_hooks(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PRINTER_ACCESS_CODE","test-only-code")
    monkeypatch.setenv("NODE_OPTIONS","--require unexpected-code")
    monkeypatch.setenv("NODE_PATH","unreviewed-packages")
    env=supports._node_environment()
    assert "SYNTHETIC_PRINTER_ACCESS_CODE" not in env and "NODE_OPTIONS" not in env and "NODE_PATH" not in env


def test_subprocess_timeout_and_excess_output_are_bounded(tmp_path):
    with pytest.raises(ValueError,match="timed out"):
        supports._run_bounded([sys.executable,"-c","import time; time.sleep(10)"],tmp_path,timeout=.1)
    with pytest.raises(ValueError,match="exceeded"):
        supports._run_bounded([sys.executable,"-c","import sys; sys.stdout.write('x'*140000)"],tmp_path,timeout=5)


def installer():
    script=Path(__file__).resolve().parents[1]/"scripts"/"install_support_fins.py"
    spec=importlib.util.spec_from_file_location("support_fins_installer",script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_optional_installer_pin_matches_adapter_and_never_installs_bad_download(tmp_path,monkeypatch):
    module=installer()
    assert module.SHA256==supports.SUPPORT_FINS_SHA256 and module.DOWNLOAD_URL==supports.SUPPORT_FINS_DOWNLOAD_URL
    monkeypatch.setattr(module.subprocess,"run",lambda *args,**kwargs:subprocess.CompletedProcess(args,0,"v24.22.1\n",""))
    monkeypatch.setattr(module,"_download",lambda target:target.write_bytes(b"wrong release bytes"))
    directory=tmp_path/"external-tools"
    with pytest.raises(ValueError,match="checksum"):
        module.install_support_fins(directory,sys.executable)
    assert not list(directory.iterdir())


def test_optional_installer_never_overwrites_existing_file_or_touches_config(tmp_path,monkeypatch):
    module=installer()
    monkeypatch.setattr(module.subprocess,"run",lambda *args,**kwargs:subprocess.CompletedProcess(args,0,"v24.22.1\n",""))
    directory=tmp_path/"external-tools";directory.mkdir()
    target=directory/("support-fins-v"+module.VERSION+".mjs");target.write_bytes(b"existing content")
    monkeypatch.setattr(module,"_download",lambda target:pytest.fail("Must not download over an existing file"))
    with pytest.raises(ValueError,match="not overwritten"):
        module.install_support_fins(directory,sys.executable)
    assert target.read_bytes()==b"existing content" and not list(directory.glob("*.json"))


def test_optional_installer_success_is_verified_idempotent_and_external(tmp_path,monkeypatch):
    module=installer()
    payload=b"synthetic installer bytes, never executed"
    monkeypatch.setattr(module,"SIZE_BYTES",len(payload))
    monkeypatch.setattr(module,"SHA256",hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(module.subprocess,"run",lambda *args,**kwargs:subprocess.CompletedProcess(args,0,"v24.22.1\n",""))
    downloaded=[]
    def download(target):
        downloaded.append(str(target));target.write_bytes(payload)
    monkeypatch.setattr(module,"_download",download)
    directory=tmp_path/"external-tools"
    first=module.install_support_fins(directory,sys.executable)
    second=module.install_support_fins(directory,sys.executable)
    assert first==second and len(downloaded)==1
    assert Path(first["support_fins"]["cli_path"]).read_bytes()==payload
    assert first["personal_config_modified"] is False and not list(directory.glob("*.json"))
    with pytest.raises(ValueError,match="outside"):
        module.install_support_fins(Path(__file__).resolve().parents[1]/"should-not-create",sys.executable)


def test_installer_rejects_https_downgrade_before_following_redirect():
    module=installer()
    with pytest.raises(module.urllib.error.HTTPError,match="HTTPS"):
        module._HTTPSRedirects().redirect_request(None,None,302,"redirect",{},"http://example.invalid/untrusted")


@pytest.mark.parametrize("version",["v20.9.9\n","v18.20.0\n","unexpected"])
def test_node_version_must_meet_reviewed_runtime(tmp_path,monkeypatch,version):
    cli=tmp_path/"reviewed.mjs";cli.write_text("Synthetic test bytes, not executed")
    monkeypatch.setattr(supports,"sha256",lambda path:supports.SUPPORT_FINS_SHA256)
    monkeypatch.setattr(supports,"_run_bounded",lambda *args,**kwargs:(0,version,""))
    with pytest.raises(ValueError,match="20.10"):
        supports._configured_tool({"support_fins":{"sha256":supports.SUPPORT_FINS_SHA256,
                                                    "command":sys.executable,"cli_path":str(cli)}})


@pytest.mark.skipif(not (os.environ.get("SUPPORT_FINS_REAL_NODE") and os.environ.get("SUPPORT_FINS_REAL_CLI")),reason="Explicit reviewed local CLI/Node paths are required")
@pytest.mark.parametrize("dimensions,rotation",[([12,12,1.2],[0,0,0]),([60,12,8],[0,35,0])])
def test_real_pinned_cli_on_synthetic_coupon_and_tilted_bar(tmp_path,dimensions,rotation):
    path=tmp_path/"synthetic-only.stl";trimesh.creation.box(extents=dimensions).export(path)
    before=path.read_bytes()
    result=supports.generate_support_fins(path,tmp_path/"derivatives",{"rotation_deg":rotation},True,
                                        "Generate an isolated synthetic support test derivative.",
                                        {"support_fins":{"command":os.environ["SUPPORT_FINS_REAL_NODE"],
                                                         "cli_path":os.environ["SUPPORT_FINS_REAL_CLI"],
                                                         "sha256":supports.SUPPORT_FINS_SHA256}})
    assert path.read_bytes()==before and result["can_print"] is False and result["layer_review_required"] is True
    assert result["tool_version"]=="1.0.5" and result["tool_report"]["stats"]["floating"]==0
    assert supports.read_support_receipt(result["output_path"])["source_sha256"]==hashlib.sha256(before).hexdigest()
    if rotation[1]:assert result["tool_report"]["stats"]["finTriangles"]>0
