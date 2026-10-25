"""Review policy tests. No network or physical printer calls."""
import time
from pathlib import Path

import pytest
import trimesh

from bambu_workflow.assessment import assess, for_job
from bambu_workflow.workflow import Workflow
from test_workflow import rig, prepared, mark_ready


def test_exact_sanitation_and_timelapse_are_information_only():
    report = assess({"bed_contact_area_mm2": 20, "cleanup": {"removed_triangles": 8}},
                    {"warnings": [{"msg": "not_support_traditional_timelapse", "level": "3"}]})
    assert report["level"] == "clear" and report["can_print"]
    assert {f["severity"] for f in report["findings"]} == {"info"}


@pytest.mark.parametrize("contact,level,allowed", [(0, "critical", False), (2, "medium", True), (12, "clear", True)])
def test_bed_contact_levels(contact, level, allowed):
    report = assess({"bed_contact_area_mm2": contact}, {})
    assert (report["level"], report["can_print"]) == (level, allowed)


def test_unknown_slicer_warning_is_not_silently_treated_as_optional():
    report = assess({}, {"warnings": [{"msg": "object_partly_outside", "level": "1"}]})
    assert report["level"] == "critical" and not report["can_print"]


def test_failed_and_incomplete_jobs_cannot_appear_clear():
    assert for_job({"state": "slicing"})["level"] == "pending"
    failed = for_job({"state": "failed", "error": {"message": "Non-manifold surface"}})
    assert failed["level"] == "critical" and not failed["can_print"]


def test_medium_review_survives_restart_without_background_dispatch(rig, monkeypatch):
    config, device = rig
    wf, jid = prepared(config)
    wf.store.update(jid, request={"print_now": True}, review_pending=True)
    spawned = []
    monkeypatch.setattr(Workflow, "spawn", lambda self, *a: spawned.append(a))
    assert Workflow(config).recover_workers() == []
    assert wf.dispatch(jid)["review_pending"] is True
    assert spawned == [] and device.starts == [] and device.uploads == []


def test_critical_assessment_cannot_be_overridden_by_start_prepared(rig, monkeypatch):
    from bambu_workflow import server
    config, device = rig
    wf, jid = prepared(config)
    manifest = wf.store.get(jid)["manifest"]
    manifest["assessment"] = assess({}, {"warnings": ["outside build area"]})
    wf.store.update(jid, manifest=manifest, review_pending=False)
    monkeypatch.setattr(server, "workflow", lambda: wf)
    with pytest.raises(ValueError, match="Critical"):
        server.start_prepared(jid)
    with pytest.raises(ValueError, match="Critical"):
        wf.dispatch(jid)
    assert device.starts == [] and device.uploads == []


def test_start_prepared_retains_original_and_dispatches_once_after_optional_review(rig, monkeypatch):
    from bambu_workflow import server
    config, device = rig
    wf, jid = prepared(config)
    manifest = wf.store.get(jid)["manifest"]
    manifest["assessment"] = assess({"bed_contact_area_mm2": 2}, {})
    original = Path(manifest["print_path"]).read_bytes()
    wf.store.update(jid, manifest=manifest, review_pending=True)
    mark_ready(wf, device)
    monkeypatch.setattr(server, "workflow", lambda: wf)
    monkeypatch.setattr(Workflow, "spawn", lambda self, jid, action: self.run_worker(jid, action))
    server.start_prepared(jid)
    server.start_prepared(jid)
    job = wf.store.get(jid)
    assert job["state"] == "printing" and not job["review_pending"]
    assert job["review_outcome"]["action"] == "print_original"
    assert Path(manifest["print_path"]).read_bytes() == original
    assert len(device.starts) == 1


@pytest.mark.parametrize("base,expected_dispatch", [(1, False), (8, True)])
def test_print_model_holds_medium_but_dispatches_clear(rig, monkeypatch, tmp_path, base, expected_dispatch):
    config, device = rig
    source = tmp_path / "part.stl"
    source.write_bytes(trimesh.creation.box(extents=[base, base, 4]).export(file_type="stl"))
    wf = Workflow(config)
    job, _ = wf.store.create("one-print-instruction", "mini", {
        "source": "file", "path": str(source), "print_now": True, "feed": {"kind": "external"}})
    dispatched = []

    def fake_slice(source, folder, printer, settings, progress=None):
        output = Path(folder) / "prepared.gcode.3mf"
        output.write_bytes(b"mock slice")
        return {"print_path": str(output), "plate": 1}

    monkeypatch.setattr("bambu_workflow.slicer.slice_model", fake_slice)
    monkeypatch.setattr(Workflow, "dispatch", lambda self, jid: dispatched.append(jid))
    wf.prepare(job["id"])
    result = wf.store.get(job["id"])
    assert result["state"] == "prepared"
    assert bool(dispatched) is expected_dispatch
    assert result["review_pending"] is not expected_dispatch
    assert result["manifest"]["assessment"]["can_print"]
    assert device.starts == [] and device.uploads == []
