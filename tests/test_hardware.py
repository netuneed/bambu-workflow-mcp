"""Nozzle declarations and local config safety. No physical-printer calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time

import pytest

from bambu_workflow.config import Config, update_config
from bambu_workflow.hardware import (CUSTOM_CONFIRMATION_MAX_AGE_SECONDS, confirm_nozzle,
                                     nozzle_fingerprint, stock_nozzle_facts, validate_nozzle)


def device(identity="workshop", serial="22ESYNTHETIC1"):
    return {"id": identity, "name": "Workshop P2S", "location": "Desk", "model": "p2s",
            "host": "printer.example.invalid", "serial": serial, "secret_ref": "preserved-secret-ref",
            "nozzle_diameter": 0.4, "nozzle_type": "hardened_steel", "plate_type": "textured_plate"}


def custom(**changes):
    return {"manufacturer": "Synthetic nozzle maker", "model": "Synthetic compatible product",
            "diameter": 0.4, "material": "hardened_steel", "flow_class": "Standard", "custom": True,
            "compatible_printer_model": "p2s", "stock_geometry_compatible": True,
            "max_volumetric_speed": 12.5, "max_temperature_c": 280,
            "installation_id": "synthetic-installation-1", **changes}


@pytest.fixture
def local_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 1, "state_dir": str(tmp_path / "state"),
                                "default_printer": "workshop", "printers": [device()],
                                "slicer": {"executable": "preserved-slicer"}, "unknown_future_setting": {"keep": True}}))
    return Config(path)


@pytest.mark.parametrize("diameter,material", [(0.2,"stainless_steel"),(0.4,"hardened_steel"),
                                               (0.6,"hardened_steel"),(0.8,"hardened_steel")])
def test_p2s_factory_defaults_follow_physical_diameter(diameter, material):
    facts = stock_nozzle_facts("p2s", diameter)
    assert facts["material"] == material
    assert validate_nozzle({"model":"p2s", "nozzle": facts})["diameter"] == diameter


def test_factory_02_cannot_be_misdeclared_hardened_or_high_flow():
    for changes in ({"material":"hardened_steel"}, {"flow_class":"HighFlow"}):
        facts = {**stock_nozzle_facts("p2s", 0.2), **changes}
        with pytest.raises(ValueError):
            validate_nozzle({"model":"p2s", "nozzle": facts})


def test_high_flow_is_explicit_hardware_and_never_inferred_from_petg_hf():
    printer = {**device(), "material": "PETG HF"}
    assert validate_nozzle(printer)["flow_class"] == "Standard"
    printer["nozzle"] = stock_nozzle_facts("p2s", 0.4, flow_class="HighFlow")
    assert validate_nozzle(printer)["flow_class"] == "HighFlow"


@pytest.mark.parametrize("changes", [
    {"manufacturer":""}, {"model":""}, {"custom":"true"}, {"material":"brass"},
    {"material":{}},
    {"material":"tungsten_carbide"}, {"compatible_printer_model":"p1s"},
    {"stock_geometry_compatible":False}, {"max_temperature_c":301},
    {"max_volumetric_speed":0}, {"max_volumetric_speed":float("nan")},
    {"max_volumetric_speed":True}, {"diameter":0.5}, {"flow_class":"unknown"},
])
def test_custom_unknown_or_invalid_hardware_is_blocked(changes):
    with pytest.raises(ValueError):
        validate_nozzle({**device(), "nozzle":custom(**changes)})


def test_legacy_custom_marker_cannot_fall_back_to_factory_defaults():
    with pytest.raises(ValueError,match="Custom nozzle facts"):
        validate_nozzle({**device(), "custom_nozzle":True})


def test_inconsistent_structured_and_legacy_fields_are_blocked():
    with pytest.raises(ValueError,match="contradict"):
        validate_nozzle({**device(), "nozzle":custom(diameter=0.6)})


@pytest.mark.parametrize("status", [
    {"nozzle_diameter":"0.6"}, {"nozzle_type":"stainless_steel"}, {"nozzle_flow":"high_flow"},
    {"raw":{"nozzle_diameter":"0.2"}}, {"raw":{"nozzle_type":"stainless_steel"}},
])
def test_human_custom_declaration_cannot_override_contradictory_live_facts(status):
    with pytest.raises(ValueError,match="Live nozzle"):
        validate_nozzle({**device(), "nozzle":custom()},live_status=status)


def test_custom_confirmation_requires_current_human_observation(local_config, monkeypatch):
    moment = time.time()
    monkeypatch.setattr("bambu_workflow.hardware.time.time",lambda:moment)
    confirm_nozzle(local_config,"workshop",custom(),"I checked the product marking, mount, installed length and calibration record.")
    printer = local_config.printer("workshop")
    assert validate_nozzle(printer,require_fresh=True,now=moment+CUSTOM_CONFIRMATION_MAX_AGE_SECONDS)["custom"]
    with pytest.raises(ValueError,match="expired"):
        validate_nozzle(printer,require_fresh=True,now=moment+CUSTOM_CONFIRMATION_MAX_AGE_SECONDS+1)
    with pytest.raises(ValueError,match="future"):
        validate_nozzle(printer,require_fresh=True,now=moment-1)
    with pytest.raises(ValueError,match="observation"):
        confirm_nozzle(local_config,"workshop",custom(),"")


def test_nozzle_change_invalidates_artifact_but_observation_renewal_does_not(local_config, monkeypatch):
    first = confirm_nozzle(local_config,"workshop",custom(),"First physical observation")
    monkeypatch.setattr("bambu_workflow.hardware.time.time",lambda:first["nozzle"]["confirmed_at"]+2000)
    renewed = confirm_nozzle(local_config,"workshop",custom(),"Renewed physical observation")
    assert renewed["nozzle_fingerprint"] == first["nozzle_fingerprint"]
    assert renewed["nozzle"]["revision"] == first["nozzle"]["revision"]
    changed = confirm_nozzle(local_config,"workshop",custom(installation_id="replacement-of-same-product"),"Installed replacement nozzle")
    assert changed["nozzle_fingerprint"] != first["nozzle_fingerprint"]
    assert changed["nozzle"]["revision"] == first["nozzle"]["revision"]+1
    changed_cap = confirm_nozzle(local_config,"workshop",custom(max_volumetric_speed=10),"Recalibrated installed nozzle")
    assert changed_cap["nozzle_fingerprint"] != changed["nozzle_fingerprint"]


def test_nozzle_confirmation_is_persistent_and_preserves_other_settings(local_config):
    before = deepcopy(local_config.data)
    confirm_nozzle(local_config,"workshop",custom(),"Actual physical observation")
    loaded = Config(local_config.path)
    assert loaded.printer("workshop")["nozzle"]["custom"] is True
    assert loaded.data["unknown_future_setting"] == before["unknown_future_setting"]
    assert loaded.data["slicer"] == before["slicer"]
    assert loaded.printer("workshop")["secret_ref"] == "preserved-secret-ref"
    backups = list(local_config.path.parent.glob("config.json.backup-*"))
    assert len(backups)==1 and json.loads(backups[0].read_text())==before


def test_two_same_model_printers_require_explicit_identity_even_if_default_changes(local_config):
    local_config.add_printer(device("desk-p2s","22ESYNTHETIC2"))
    with pytest.raises(ValueError,match="explicitly"):
        local_config.printer()
    local_config.data["default_printer"]="desk-p2s"
    with pytest.raises(ValueError,match="explicitly"):
        local_config.printer()
    assert local_config.printer("workshop")["serial"]=="22ESYNTHETIC1"
    assert local_config.printer("desk-p2s")["serial"]=="22ESYNTHETIC2"


@pytest.mark.parametrize("candidate", [device("workshop","22ENEW"),device("new-id","22ESYNTHETIC1")])
def test_add_printer_never_overwrites_duplicate_identity_or_stores_secret(local_config,candidate):
    original=local_config.path.read_bytes()
    with pytest.raises(ValueError,match="already"):
        local_config.add_printer(candidate,before_write=lambda:pytest.fail("No keyring write allowed on a duplicate"))
    assert local_config.path.read_bytes()==original


def test_concurrent_adds_preserve_each_device_and_unknown_settings(local_config):
    def append(number):
        Config(local_config.path).add_printer(device("printer-"+str(number),"22ESYNTHETIC"+str(number+2)))
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(append,[1,2]))
    loaded=Config(local_config.path)
    assert {p["id"] for p in loaded.data["printers"]}=={"workshop","printer-1","printer-2"}
    assert loaded.data["unknown_future_setting"]=={"keep":True}


def test_failed_keyring_write_leaves_original_config_and_cleans_temporary_file(local_config):
    original=local_config.path.read_bytes()
    def fail():
        raise RuntimeError("Synthetic keyring failure")
    with pytest.raises(RuntimeError,match="keyring"):
        local_config.add_printer(device("new-id","22ENEW"),before_write=fail)
    assert local_config.path.read_bytes()==original
    assert not list(local_config.path.parent.glob("*.tmp"))


def test_config_creation_cannot_race_into_overwriting_existing_file(local_config):
    before=local_config.path.read_bytes()
    with pytest.raises(ValueError,match="already exists"):
        update_config(local_config.path,lambda data:data.clear(),create=True)
    assert local_config.path.read_bytes()==before


def test_cli_add_printer_preserves_defaults_secret_references_and_names(local_config,monkeypatch):
    from bambu_workflow.cli import add_printer
    import keyring
    original=deepcopy(local_config.data)
    answers=iter(["p2s","second-p2s","P2S at desk","Desk","second.example.invalid","22ESECOND",
                  "0.2","","textured_plate","ABS"])
    monkeypatch.setattr("builtins.input",lambda prompt:next(answers))
    monkeypatch.setattr("getpass.getpass",lambda prompt:"synthetic-test-only-code")
    writes=[]
    monkeypatch.setattr(keyring,"set_password",lambda *args:writes.append(args))
    monkeypatch.setattr(Config,"adapter",lambda *args,**kwargs:pytest.fail("Adding a printer must not contact hardware"))
    result=add_printer(local_config)
    assert result["printer_contacted"] is False
    assert local_config.data["default_printer"]==original["default_printer"]
    assert local_config.data["printers"][0]==original["printers"][0]
    added=local_config.printer("second-p2s")
    assert added["name"]=="P2S at desk" and added["location"]=="Desk"
    assert added["nozzle_type"]=="stainless_steel" and added["nozzle_diameter"]==0.2
    assert writes==[("bambu-workflow",added["secret_ref"],"synthetic-test-only-code")]
    assert added["secret_ref"] not in {"second-p2s",original["printers"][0]["secret_ref"]}
    assert "synthetic-test-only-code" not in local_config.path.read_text()


def test_cli_new_printer_rejects_duplicate_id_before_keyring_prompt(local_config,monkeypatch):
    from bambu_workflow.cli import add_printer
    answers=iter(["p2s","workshop"])
    monkeypatch.setattr("builtins.input",lambda prompt:next(answers))
    monkeypatch.setattr("getpass.getpass",lambda prompt:pytest.fail("Duplicate must fail before collecting a new secret"))
    original=local_config.path.read_bytes()
    with pytest.raises(ValueError,match="unique printer id"):
        add_printer(local_config)
    assert local_config.path.read_bytes()==original
