from copy import deepcopy
import json
import time

import pytest

from bambu_workflow.materials import (inventory, normalize_feed, validate_requests, bind_materials,
                                      validate_bindings, launch_mapping)
from bambu_workflow.preflight import assessed, make_review, validate_review
from test_workflow import rig, prepared, mark_ready


def status():
    return {"raw": {"ams": {"ams": [
        {"id": "0", "tray": [{"id": "0", "tray_type": "PETG", "tray_info_idx": "GFG02", "tray_color": "FF0000FF", "tray_uuid": "spool-a"},
                               {"id": "1", "tray_type": "PETG", "tray_info_idx": "GFG02", "tray_color": "0000FFFF", "tray_uuid": "spool-b"}]},
        {"id": "128", "tray": [{"id": "0", "tray_type": "ABS", "tray_info_idx": "GFB00"}]}]}}}


def plan():
    return [{"filament_id": n, "type": "PETG", "preset": "Bambu PETG HF", "profile_id": "GFG02",
             "color": c, "feed": {"kind": "ams", "ams_id": 0, "slot_id": n - 1}}
            for n, c in enumerate(("FF0000FF", "0000FFFF"), 1)]


def test_ht_uses_separate_topology_not_unit_times_four():
    entries = inventory(status())
    assert entries[2]["feed"] == {"kind": "ams", "ams_id": 128, "slot_id": 0, "tray_id": 16}
    assert normalize_feed({"kind": "ams", "tray_id": 16})["ams_id"] == 128
    with pytest.raises(ValueError, match="disagree"):
        normalize_feed({"kind": "ams", "ams_id": 128, "slot_id": 0, "tray_id": 512})


@pytest.mark.parametrize("feed", [{"kind": "ams", "tray_id": True}, {"kind": "ams", "ams_id": 128, "slot_id": 1},
                                  {"kind": "ams", "ams_id": 7, "slot_id": 0}, {"kind": "ams", "tray_id": -1}])
def test_unknown_or_unloaded_feeds_cannot_be_selected(feed):
    with pytest.raises(ValueError):
        normalize_feed(feed)


def test_every_logical_color_is_bound_to_its_actual_slot():
    bound = bind_materials(status(), {"model": "p2s"}, plan())
    options = launch_mapping(bound)
    assert options == {"use_ams": True, "ams_mapping": [0, 1],
                       "ams_mapping2": [{"ams_id": 0, "slot_id": 0}, {"ams_id": 0, "slot_id": 1}]}
    validate_bindings(status(), {"model": "p2s"}, bound)


def test_unspecified_requested_color_retains_the_selected_live_color():
    requested = plan()
    requested[0].pop("color")
    bound = bind_materials(status(), {"model": "p2s"}, requested)
    assert bound[0]["color"] == "FF0000FF"


@pytest.mark.parametrize("key,value", [("tray_info_idx", "GFG99"), ("tray_color", "00FF00FF"),
                                       ("tray_uuid", "another-spool"), ("tray_type", "ABS")])
def test_spool_change_or_metadata_loss_blocks_existing_binding(key, value):
    bound = bind_materials(status(), {"model": "p2s"}, plan())
    changed = status()
    changed["raw"]["ams"]["ams"][0]["tray"][0][key] = value
    with pytest.raises(ValueError, match="changed"):
        validate_bindings(changed, {"model": "p2s"}, bound)


def test_removed_slot_and_duplicate_inventory_do_not_fall_back():
    bound = bind_materials(status(), {"model": "p2s"}, plan())
    changed = status()
    changed["raw"]["ams"]["ams"][0]["tray"].pop()
    with pytest.raises(ValueError, match="absent"):
        validate_bindings(changed, {"model": "p2s"}, bound)
    changed = status()
    changed["raw"]["ams"]["ams"].append(deepcopy(changed["raw"]["ams"]["ams"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        inventory(changed)


def test_palette_and_mixed_polymer_boundary_are_explicit():
    invalid = plan()
    invalid[1]["filament_id"] = 3
    with pytest.raises(ValueError, match="contiguous"):
        validate_requests(invalid)
    invalid = plan()
    invalid[1]["type"] = "ABS"
    with pytest.raises(ValueError, match="Mixed-polymer"):
        validate_requests(invalid)


def test_petg_hf_does_not_change_hardware_flow():
    values = plan()
    values[0]["type"] = "PETG - HF"
    assert validate_requests(values)[0]["type"] == "PETG"
    assert "nozzle_volume_type" not in validate_requests(values)[0]


def test_changed_spool_blocks_before_upload_or_start(rig):
    config, device = rig
    wf, jid = prepared(config)
    device.status["raw"].update(status()["raw"])
    manifest = wf.store.get(jid)["manifest"]
    manifest["materials"] = bind_materials(device.snapshot(), config.printer("mini"), plan())
    wf.store.update(jid, manifest=manifest)
    mark_ready(wf, device)
    device.status["raw"]["ams"]["ams"][0]["tray"][1]["tray_color"] = "00FF00FF"
    wf.dispatch(jid)
    assert not device.uploads and not device.starts
    assert "color changed" in wf.store.get(jid)["error"]["message"]


def review_manifest(tmp_path):
    import hashlib
    p = tmp_path / "sliced.3mf"
    p.write_bytes(b"only a fake immutable artifact; layer checks tested separately")
    return {"print_path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "layer_review": {"required": True}, "printer": {"model": "p2s"}, "analysis": {}, "inspection": {}}


def review():
    from bambu_workflow.preflight import CHECKS
    return {"reviewer": "test reviewer", "evidence": "Actual-contact review fixture, no physical print",
            "layers": [1, 3, 8], "checks": dict.fromkeys(CHECKS, True)}


def test_unknown_contacts_never_clear_by_timeout(tmp_path):
    manifest = review_manifest(tmp_path)
    assert not assessed(manifest)["can_print"]
    with pytest.raises(ValueError, match="required"):
        validate_review(manifest, None)
    invalid = review()
    invalid["checks"]["support_contacts"] = False
    with pytest.raises(ValueError, match="support_contacts"):
        make_review(manifest, manifest["sha256"], invalid)


def test_review_bound_to_file_nozzle_material_and_support_configuration(tmp_path):
    manifest = review_manifest(tmp_path)
    record = make_review(manifest, manifest["sha256"], review())
    validate_review(manifest, record)
    assert assessed(manifest, record)["can_print"]
    changed = deepcopy(manifest)
    changed["support_mode"] = "native"
    with pytest.raises(ValueError, match="required"):
        validate_review(changed, record)
    with pytest.raises(ValueError, match="different"):
        make_review(manifest, "0" * 64, review())


def test_layer_review_never_erases_an_independent_critical_finding(tmp_path):
    manifest = review_manifest(tmp_path)
    manifest["assessment"] = {"findings": [{"code": "collision", "severity": "critical", "message": "unresolved toolpath collision"}]}
    record = make_review(manifest, manifest["sha256"], review())
    assert not assessed(manifest, record)["can_print"]


@pytest.mark.parametrize("during_upload", [False, True])
def test_concurrent_same_size_nozzle_replacement_invalidates_worker_config(rig, monkeypatch, during_upload):
    from bambu_workflow.config import Config
    from bambu_workflow.hardware import confirm_nozzle, stock_nozzle_facts, nozzle_fingerprint
    config, device = rig
    facts = stock_nozzle_facts("a1mini", .4, material="stainless_steel")
    confirm_nozzle(config, "mini", facts, "Test owner observed installed original nozzle")
    wf, jid = prepared(config)
    manifest = wf.store.get(jid)["manifest"]
    manifest["nozzle_fingerprint"] = nozzle_fingerprint(config.printer("mini"))
    wf.store.update(jid, manifest=manifest)
    mark_ready(wf, device)

    def replace():
        confirm_nozzle(Config(config.path), "mini", {**facts, "installation_id": "replacement-2"},
                       "Test owner observed a replacement with the same diameter and material")

    if during_upload:
        upload = device.upload
        def changed(*args):
            result = upload(*args)
            replace()
            return result
        monkeypatch.setattr(device, "upload", changed)
    else:
        replace()
    wf.dispatch(jid)
    assert device.starts == []
    assert bool(device.uploads) is during_upload
    assert "changed since preparation" in wf.store.get(jid)["error"]["message"]


def test_imported_sliced_file_needs_full_product_color_feed_plan_before_inspection(rig, monkeypatch):
    from test_slicer import SlicerTests
    from bambu_workflow.workflow import Workflow
    config, device = rig
    case = SlicerTests()
    case.setUp()
    try:
        source = case.sliced()
        monkeypatch.setattr(Workflow, "spawn", lambda *args: None)
        wf = Workflow(config)
        jid = wf.submit("import-unbound", "mini", source="file", path=str(source))["job_id"]
        wf.prepare(jid)
        result = wf.store.get(jid)
        assert "complete explicit materials palette" in result["error"]["message"]
        assert not device.uploads and not device.starts
    finally:
        case.tearDown()


def test_public_auto_support_mode_accepts_a_checked_sliced_import(tmp_path):
    from test_slicer import SlicerTests
    from bambu_workflow.slicer import slice_model
    case = SlicerTests()
    case.setUp()
    try:
        source = case.sliced()
        result = slice_model(source, tmp_path / "default-import", case.printer, {"support_mode": "auto"})
        assert source.read_bytes() == __import__("pathlib").Path(result["print_path"]).read_bytes()
    finally:
        case.tearDown()
