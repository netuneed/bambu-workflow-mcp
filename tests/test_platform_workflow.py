"""Portable paths and readiness on model families. No real printer connections."""
import json
from pathlib import Path

import pytest

from bambu_workflow.config import Config, default_config_path
from bambu_workflow.workflow import selected_material
from test_workflow import rig, prepared, mark_ready


@pytest.mark.parametrize("model", ["a1mini","a1","p1p","p1s","x1","x1c","x1e","p2s","h2s","a2l"])
def test_software_supported_model_reaches_single_durable_launch(rig, model):
    config, device=rig
    config.data["printers"][0]["model"]=model
    device.status["model"]=model
    wf,jid=prepared(config)
    mark_ready(wf,device)
    assert wf.dispatch(jid)["state"]=="printing"
    assert wf.dispatch(jid)["state"]=="printing"
    assert len(device.starts)==1


@pytest.mark.parametrize("model", ["h2d","h2dpro","h2c","x2d"])
def test_dual_tool_launch_remains_blocked_without_an_implemented_tool_feed_path(rig, model):
    config,device=rig
    config.data["printers"][0]["model"]=model
    device.status["model"]=model
    wf,jid=prepared(config)
    mark_ready(wf,device)
    result=wf.dispatch(jid)
    assert result["state"]=="prepared" and result["error"]
    assert device.uploads==[] and device.starts==[]


def remove_nozzle_metadata(device):
    for key in ["nozzle_diameter","nozzle_type"]:
        device.status[key]=None
        device.status["raw"].pop(key,None)


def test_missing_legacy_nozzle_metadata_needs_user_setup_then_allows_confirmed_nozzle(rig):
    config,device=rig
    config.data["printers"][0]["model"]="p1s"
    device.status["model"]="p1s"
    remove_nozzle_metadata(device)
    wf,jid=prepared(config)
    mark_ready(wf,device)
    assert "Confirm the actual" in wf.dispatch(jid)["error"]["message"]
    assert not device.starts
    config.data["printers"][0]["nozzle_confirmed"]=True
    assert wf.dispatch(jid)["state"]=="printing"
    assert len(device.starts)==1


def test_user_setup_never_overrides_known_live_nozzle_mismatch(rig):
    config,device=rig
    config.data["printers"][0]["nozzle_confirmed"]=True
    wf,jid=prepared(config)
    device.status.update(nozzle_diameter=None,nozzle_type=None)
    device.status["raw"].update(nozzle_diameter="0.6",nozzle_type="stainless_steel")
    mark_ready(wf,device)
    assert "differs" in wf.dispatch(jid)["error"]["message"]
    assert device.starts==[]


def test_external_material_fallback_does_not_hide_known_mismatch_or_missing_ams(rig):
    config,device=rig
    p=config.printer()
    status=device.snapshot()
    status["raw"]["vt_tray"]={}
    assert selected_material(status,p,{"kind":"external"})["source"]=="user_configuration"
    status["raw"]["vt_tray"]={"tray_type":"PLA"}
    assert selected_material(status,p,{"kind":"external"})["type"]=="PLA"
    with pytest.raises(ValueError,match="AMS tray is absent"):
        selected_material(status,p,{"kind":"ams","tray_id":0})


@pytest.mark.parametrize("model",["p2s","h2s"])
def test_internal_storage_models_do_not_require_a_microsd_card(rig,model):
    config,device=rig
    config.data["printers"][0]["model"]=model
    device.status["model"]=model
    device.status["raw"]["sdcard"]=False
    wf,jid=prepared(config)
    mark_ready(wf,device)
    assert wf.dispatch(jid)["state"]=="printing"


def test_invalid_live_nozzle_diameter_cannot_pass_comparison(rig):
    config,device=rig
    wf,jid=prepared(config)
    device.status["nozzle_diameter"]="nan"
    mark_ready(wf,device)
    assert "invalid" in wf.dispatch(jid)["error"]["message"]
    assert device.starts==[]


def test_macos_config_uses_application_support_and_keeps_existing_legacy_file(tmp_path,monkeypatch):
    monkeypatch.setattr("bambu_workflow.config.sys.platform","darwin")
    monkeypatch.setattr(Path,"home",classmethod(lambda cls:tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME",raising=False)
    expected=tmp_path/"Library/Application Support/BambuWorkflow/config.json"
    assert default_config_path()==expected
    legacy=tmp_path/".config/BambuWorkflow/config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}")
    assert default_config_path()==legacy
    expected.parent.mkdir(parents=True)
    expected.write_text("{}")
    assert default_config_path()==expected


def test_config_canonicalizes_model_in_memory_without_changing_file(rig):
    config,_=rig
    data=config.data
    data["printers"][0]["model"]="Bambu Lab P1S"
    config.path.write_text(json.dumps(data))
    before=config.path.read_bytes()
    printer=Config(config.path).printer()
    assert printer["model"]=="p1s" and printer["build_volume_mm"]==[256,256,250]
    assert config.path.read_bytes()==before


@pytest.mark.parametrize("volume", [[181,180,180],[180,180,0],[float("nan"),180,180],[180,180]])
def test_config_cannot_expand_printer_limits(rig,volume):
    config,_=rig
    config.data["printers"][0]["build_volume_mm"]=volume
    with pytest.raises(ValueError,match="build volume"):
        config.printer()
