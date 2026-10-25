"""Fault tests for durable launch semantics. No sockets or physical printer calls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time

from filelock import FileLock
import pytest

from bambu_workflow.config import Config
from bambu_workflow.store import Store
from bambu_workflow.workflow import Workflow, task_identity


def idle_status():
    raw = {
        "gcode_state": "IDLE", "gcode_file": "previous.gcode.3mf", "subtask_name": "previous",
        "task_id": "old", "subtask_id": "old", "project_id": "old", "stg_cur": 255,
        "sdcard": True, "hms": [], "print_error": 0,
        "nozzle_diameter": "0.4", "nozzle_type": "stainless_steel",
        "ams": {"tray_now": "254", "ams": []},
        "vt_tray": {"tray_type": "PETG", "tray_info_idx": "GFG99"},
    }
    return {"received_at": time.time(), "model": "a1mini", "identity_verified": True,
            "gcode_state": "IDLE", "gcode_file": raw["gcode_file"],
            "subtask_name": raw["subtask_name"], "nozzle_diameter": "0.4",
            "nozzle_type": "stainless_steel", "stage_id": 255, "raw": raw}


class MemoryPrinter:
    """A start can take effect while its transport answer is lost."""

    def __init__(self):
        self.status = idle_status()
        self.starts = []
        self.uploads = []
        self.lose_start_reply = False
        self.accept_start = True
        self.fail_upload = False
        self.mutate_uploaded_source = False

    def snapshot(self):
        result = deepcopy(self.status)
        result["received_at"] = time.time()
        return result

    def upload(self, local, remote, expected_sha256):
        self.uploads.append((str(local), remote, expected_sha256))
        assert hashlib.sha256(Path(local).read_bytes()).hexdigest() == expected_sha256
        if self.fail_upload:
            self.fail_upload = False
            raise TimeoutError("Data connection broke after a partial transfer")
        if self.mutate_uploaded_source:
            Path(local).write_bytes(b"changed while transferring")
        return {"state": "transferred", "sha256": expected_sha256, "remote": remote}

    def start(self, remote, *, plate, job_name, options):
        self.starts.append({"remote": remote, "plate": plate, "job_name": job_name,
                            "options": deepcopy(options)})
        if self.accept_start:
            self.status.update(gcode_state="RUNNING", gcode_file=remote,
                               subtask_name=job_name, layer_num=1, stage_id=0)
            self.status["raw"].update(gcode_state="RUNNING", gcode_file=remote,
                                      subtask_name=job_name, layer_num=1, stg_cur=0,
                                      task_id="new", subtask_id="new", project_id="new")
        if self.lose_start_reply:
            raise TimeoutError("Printer accepted the command but its reply was lost")
        return {"state": "start_command_sent", "printer_started": False}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    printer = {"id": "mini", "model": "a1mini", "host": "printer.example.invalid",
               "serial": "030TESTSERIAL01", "nozzle_diameter": .4,
               "nozzle_type": "stainless_steel", "plate_type": "textured_plate",
               "plate_confirmed": True, "material": "PETG"}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"version": 1, "printers": [printer],
                                       "state_dir": str(tmp_path / "state")}), encoding="utf-8")
    config = Config(config_path)
    device = MemoryPrinter()
    monkeypatch.setattr(Config, "adapter", lambda self, printer_id=None: device)
    monkeypatch.setattr("bambu_workflow.slicer.inspect_print", lambda *a, **k: {"gcode_md5": "a" * 32})
    monkeypatch.setattr(Workflow, "monitor", lambda self, jid, seconds=180: self.reconcile(jid))
    return config, device


def prepared(config, request_id="prepare-1"):
    wf = Workflow(config)
    job, _ = wf.store.create(request_id, "mini", {"source": "file", "print_now": False})
    folder = config.state_dir / "jobs" / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    artifact = folder / "print.gcode.3mf"
    artifact.write_bytes(b"immutable test artifact; slicing is outside this test")
    p = config.printer("mini")
    manifest = {
        "printer": {k: p[k] for k in ("id", "model", "nozzle_diameter", "nozzle_type", "plate_type")},
        "device_identity_hash": hashlib.sha256((p["host"] + "/" + p["serial"]).encode()).hexdigest(),
        "material": {"type": "PETG", "feed": {"kind": "external"}},
        "print_path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "inspection": {"gcode_md5": "a" * 32}, "plate": 1,
    }
    wf.store.update(job["id"], "prepared", manifest=manifest)
    return wf, job["id"]


def mark_ready(wf, device):
    wf.store.bed_ready("mini", task_identity(device.snapshot(), wf.config.printer("mini")))


def set_error_free_failed_idle(device):
    """Legacy firmware can retain FAILED after a canceled job while otherwise reporting idle."""
    device.status.update(gcode_state="FAILED", gcode_file="", subtask_name="", stage_id=255,
                         hms=[], print_error=0,
                         temperatures={"nozzle_target": 0, "bed_target": 0})
    device.status["raw"].update(gcode_state="FAILED", gcode_file="", subtask_name="", print_type="idle",
                                nozzle_target_temper=0, bed_target_temper=0, print_error=0, hms=[], stg_cur=255)


def test_store_request_identity_survives_database_reopen(tmp_path):
    root = tmp_path / "state"
    original, created = Store(root).create("human-instruction-1", "mini", {"path": "one.stl"})
    repeated, created_again = Store(root).create("human-instruction-1", "mini", {"path": "one.stl"})
    assert created and not created_again
    assert repeated["id"] == original["id"]
    with pytest.raises(ValueError, match="different request"):
        Store(root).create("human-instruction-1", "mini", {"path": "two.stl"})
    with pytest.raises(ValueError, match="different request"):
        Store(root).create("human-instruction-1", "other-printer", {"path": "one.stl"})


def test_repeated_preparing_observations_do_not_flood_event_history(tmp_path):
    store = Store(tmp_path)
    jid = store.create("one-preparation", "mini", {})[0]["id"]
    store.update(jid, "preparing", error=None, observation={"percent": 0})
    for percent in range(1, 20):
        store.update(jid, "preparing", error=None, observation={"percent": percent})
    assert [event["state"] for event in store.events(jid)] == ["queued", "preparing"]
    assert store.get(jid)["observation"]["percent"] == 19
    store.update(jid, "printing", error=None)
    assert [event["state"] for event in store.events(jid)] == ["queued", "preparing", "printing"]


def test_error_history_records_changes_and_recovery_once(tmp_path):
    store = Store(tmp_path)
    jid = store.create("diagnose-readiness", "mini", {})[0]["id"]
    first_error = {"stage": "readiness", "message": "Printer busy"}
    second_error = {"stage": "readiness", "message": "Bed not confirmed"}
    store.update(jid, error=None)
    store.update(jid, error=first_error)
    store.update(jid, error=deepcopy(first_error))
    store.update(jid, error=second_error)
    store.update(jid, error=None)
    store.update(jid, error=None)
    events = store.events(jid)
    assert len(events) == 4
    assert [json.loads(event["detail"])["error"] for event in events[1:]] == [first_error, second_error, None]


def test_concurrent_requests_get_one_durable_identity(tmp_path):
    root = tmp_path / "state"
    Store(root)
    barrier = threading.Barrier(2)

    def create():
        store = Store(root)
        barrier.wait()
        return store.create("same-human-instruction", "mini", {"model": "part"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert len({job["id"] for job, _ in results}) == 1
    assert sum(created for _, created in results) == 1


def test_concurrent_printer_lease_has_one_owner_and_survives_restart(tmp_path):
    root = tmp_path / "state"
    store = Store(root)
    jobs = [store.create(f"request-{n}", "mini", {})[0]["id"] for n in range(2)]
    barrier = threading.Barrier(2)

    def acquire(jid):
        local = Store(root)
        barrier.wait()
        try:
            local.acquire(jid)
            return jid
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(acquire, jobs))
    winner = next(jid for jid in owners if jid)
    loser = next(jid for jid in jobs if jid != winner)
    assert len([jid for jid in owners if jid]) == 1
    with pytest.raises(ValueError, match="owns this printer"):
        Store(root).acquire(loser)
    Store(root).release(winner)
    Store(root).acquire(loser)


def test_submit_retry_across_restart_does_not_spawn_a_second_worker(rig, monkeypatch):
    config, _ = rig
    spawned = []
    monkeypatch.setattr(Workflow, "spawn", lambda self, jid, action: spawned.append((jid, action)))
    first = Workflow(config).submit("print-current-part", "mini", source="blender", print_now=True)
    second = Workflow(Config(config.path)).submit("print-current-part", "mini", source="blender", print_now=True)
    assert first["job_id"] == second["job_id"]
    assert second["duplicate"] is True
    assert spawned == [(first["job_id"], "prepare")]


def test_actual_start_with_lost_response_is_never_sent_twice_after_restart(rig):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    device.lose_start_reply = True
    assert wf.dispatch(jid)["state"] == "start_uncertain"
    assert len(device.starts) == 1
    recovered = Workflow(Config(config.path))
    assert recovered.dispatch(jid)["state"] == "printing"
    assert recovered.dispatch(jid)["state"] == "printing"
    assert len(device.starts) == 1
    assert len(device.uploads) == 1


@pytest.mark.parametrize("observed_state", ["preparing", "printing"])
def test_late_start_transport_error_cannot_demote_already_verified_job(rig, observed_state):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    wf.dispatch(jid)
    if observed_state == "preparing":
        device.status.update(layer_num=0, stage_id=3)
        device.status["raw"].update(layer_num=0, stg_cur=3)
    assert wf.reconcile(jid)["state"] == observed_state
    wf._error(jid, TimeoutError("Late QoS1 publish confirmation timeout"), "start_sent", uncertain=True)
    result = wf.store.get(jid)
    assert result["state"] == observed_state
    assert result["observation"]["identity_match"] is True
    assert result["transport_warning"]["stage"] == "start_sent"
    assert result.get("error") is None
    assert wf.store.leased_job("mini") == jid
    assert wf.dispatch(jid)["state"] == observed_state
    assert len(device.starts) == 1 and len(device.uploads) == 1


def test_unaccepted_uncertain_start_keeps_lease_and_never_retries(rig):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    device.accept_start = False
    device.lose_start_reply = True
    assert wf.dispatch(jid)["state"] == "start_uncertain"
    restarted = Workflow(Config(config.path))
    for _ in range(3):
        assert restarted.dispatch(jid)["state"] == "start_uncertain"
    _, other = prepared(config, "different-part")
    with pytest.raises(ValueError, match="owns this printer"):
        restarted.store.acquire(other)
    assert len(device.starts) == 1


def test_two_dispatch_workers_for_same_job_produce_one_actual_start(rig, monkeypatch):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    entered_upload = threading.Event()
    finish_upload = threading.Event()
    original_upload = device.upload

    def blocked_upload(*args):
        entered_upload.set()
        assert finish_upload.wait(5), "test worker was never released"
        return original_upload(*args)

    monkeypatch.setattr(device, "upload", blocked_upload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(wf.run_worker, jid, "dispatch")
        try:
            assert entered_upload.wait(3)
            second = pool.submit(Workflow(Config(config.path)).run_worker, jid, "dispatch")
            second.result(timeout=3)
        finally:
            finish_upload.set()
        first.result(timeout=5)
    assert len(device.starts) == 1
    assert len(device.uploads) == 1


def test_crash_after_durable_marker_before_network_never_resends(rig):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    wf.store.acquire(jid)
    manifest = wf.store.get(jid)["manifest"]
    remote = f"cache/bwf_{jid}_{manifest['sha256'][:12]}.gcode.3mf"
    wf.store.consume_bed_and_record_start(jid, task_identity(device.snapshot(), wf.config.printer("mini")), {
        "at": time.time(), "remote": remote, "job_name": Path(remote).name.removesuffix(".gcode.3mf"),
        "sha256": manifest["sha256"], "plate": 1,
    })
    restarted = Workflow(Config(config.path))
    assert restarted.dispatch(jid)["state"] == "start_uncertain"
    assert device.starts == [] and device.uploads == []


@pytest.mark.parametrize("foreign_field", ["gcode_file", "subtask_name"])
def test_foreign_file_or_task_never_proves_this_print_started(rig, foreign_field):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    wf.dispatch(jid)
    device.status[foreign_field] = "some-other-job.gcode.3mf"
    device.status["raw"][foreign_field] = "some-other-job.gcode.3mf"
    assert wf.reconcile(jid)["state"] == "start_uncertain"
    assert len(device.starts) == 1


def test_running_before_first_layer_is_preparation_not_printing(rig):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    wf.dispatch(jid)
    device.status.update(layer_num=0, stage_id=2)
    device.status["raw"].update(layer_num=0, stg_cur=2)
    assert wf.reconcile(jid)["state"] == "preparing"


def test_stale_observation_cannot_confirm_start(rig):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    device.lose_start_reply = True
    wf.dispatch(jid)
    stale = device.snapshot()
    stale["received_at"] = time.time() - 60
    job = wf.reconcile(jid, stale)
    assert job["state"] == "start_uncertain"
    assert "fresh" in job["last_connection_error"].lower()
    recovered = wf.reconcile(jid)
    assert recovered["state"] == "printing"
    assert recovered.get("last_connection_error") is None


def test_finished_printer_may_clear_file_only_after_verified_print(rig):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    assert wf.dispatch(jid)["state"] == "printing"
    device.status.update(gcode_state="FINISH", gcode_file="", layer_num=20)
    device.status["raw"].update(gcode_state="FINISH", gcode_file="", layer_num=20,
                                total_layer_num=20, mc_percent=100)
    job = wf.reconcile(jid)
    assert job["state"] == "completed"
    assert job["observation"]["identity_source"] == "completed_after_verified_print"
    assert wf.store.leased_job("mini") is None
    assert len(device.starts) == 1


@pytest.mark.parametrize("condition", ["unproven", "other_file", "incomplete_layers"])
def test_retained_task_name_alone_cannot_release_launch_hold(rig, condition):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    wf.dispatch(jid)
    if condition == "unproven":
        wf.store.update(jid, "start_uncertain", observation={})
    filename = "another-job.3mf" if condition == "other_file" else ""
    device.status.update(gcode_state="FINISH", gcode_file=filename, layer_num=10)
    device.status["raw"].update(gcode_state="FINISH", gcode_file=filename, layer_num=10,
                                total_layer_num=20 if condition == "incomplete_layers" else 10, mc_percent=100)
    assert wf.reconcile(jid)["state"] == "start_uncertain"
    assert wf.store.leased_job("mini") == jid
    assert len(device.starts) == 1


@pytest.mark.parametrize("changed", [
    {"identity_verified": False},
    {"model": "a1"},
    {"received_at": "future"},
])
def test_untrusted_matching_observation_cannot_confirm_start(rig, changed):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    device.lose_start_reply = True
    wf.dispatch(jid)
    observation = device.snapshot()
    observation.update(changed)
    if observation["received_at"] == "future":
        observation["received_at"] = time.time() + 60
    assert wf.reconcile(jid, observation)["state"] == "start_uncertain"
    assert len(device.starts) == 1


def test_partial_upload_can_retry_without_any_duplicate_start(rig):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    device.fail_upload = True
    assert wf.dispatch(jid)["state"] == "transfer_failed"
    assert device.starts == []
    assert Workflow(Config(config.path)).dispatch(jid)["state"] == "printing"
    assert len(device.starts) == 1
    assert len(device.uploads) == 2
    assert device.uploads[0][1] != device.uploads[1][1]


def test_explicit_dispatch_retry_clears_previous_readiness_error_before_upload(rig, monkeypatch):
    config, device = rig
    wf, jid = prepared(config)
    wf.store.update(jid, error={"stage": "readiness", "message": "Previous printer-busy observation"})
    mark_ready(wf, device)
    observed_errors = []
    original_upload = device.upload

    def observe_retry(local, remote, expected):
        observed_errors.append(wf.store.get(jid).get("error"))
        return original_upload(local, remote, expected)

    monkeypatch.setattr(device, "upload", observe_retry)
    assert wf.dispatch(jid)["state"] == "printing"
    assert observed_errors == [None]
    assert len(device.starts) == 1


def test_changed_artifact_during_transfer_never_starts(rig):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    device.mutate_uploaded_source = True
    job = wf.dispatch(jid)
    assert device.starts == []
    assert "changed during transfer" in job["error"]["message"]


def test_busy_printer_never_receives_upload_or_start(rig):
    config, device = rig
    wf, jid = prepared(config)
    device.status["gcode_state"] = "RUNNING"
    device.status["raw"]["gcode_state"] = "RUNNING"
    job = wf.dispatch(jid)
    assert "busy" in job["error"]["message"]
    assert device.uploads == [] and device.starts == []


def test_error_free_failed_idle_can_start_after_explicit_physical_bed_confirmation(rig, monkeypatch):
    from bambu_workflow import server

    config, device = rig
    wf, jid = prepared(config)
    set_error_free_failed_idle(device)
    monkeypatch.setattr(server, "workflow", lambda: wf)
    result = server.mark_bed_ready("mini", True, "textured_plate")
    assert result["bed_ready"] is True
    assert device.starts == [] and device.uploads == []
    assert wf.dispatch(jid)["state"] == "printing"
    assert len(device.starts) == 1 and len(device.uploads) == 1
    # A retry of the same human instruction remains idempotent even after FAILED was accepted as idle.
    Workflow(Config(config.path)).dispatch(jid)
    assert len(device.starts) == 1


def test_error_free_failed_idle_still_requires_a_physical_bed_marker(rig):
    config, device = rig
    wf, jid = prepared(config)
    set_error_free_failed_idle(device)
    result = wf.dispatch(jid)
    assert "readiness is missing or expired" in result["error"]["message"]
    assert device.starts == [] and device.uploads == []


@pytest.mark.parametrize("blocker", [
    "print_error", "hms", "RUNNING", "active_file", "heating_nozzle", "heating_bed", "active_print_type",
])
def test_failed_idle_exception_never_bypasses_errors_or_active_state(rig, monkeypatch, blocker):
    from bambu_workflow import server

    config, device = rig
    wf, jid = prepared(config)
    set_error_free_failed_idle(device)
    # An older physical observation cannot override a new blocking error or an active print.
    mark_ready(wf, device)
    if blocker == "RUNNING":
        device.status["gcode_state"] = "RUNNING"
        device.status["raw"]["gcode_state"] = "RUNNING"
    elif blocker == "hms":
        device.status["hms"] = [{"attr": 83887360, "code": 65543}]
        device.status["raw"]["hms"] = deepcopy(device.status["hms"])
    elif blocker == "active_file":
        device.status["gcode_file"] = "cache/unfinished-job.3mf"
        device.status["raw"]["gcode_file"] = "cache/unfinished-job.3mf"
    elif blocker == "heating_nozzle":
        device.status["raw"]["nozzle_target_temper"] = 240
    elif blocker == "heating_bed":
        device.status["raw"]["bed_target_temper"] = 70
    elif blocker == "active_print_type":
        device.status["raw"]["print_type"] = "local"
    else:
        device.status["print_error"] = 123456
        device.status["raw"]["print_error"] = 123456
    before = deepcopy(device.status)
    monkeypatch.setattr(server, "workflow", lambda: wf)
    with pytest.raises(ValueError):
        server.mark_bed_ready("mini", True, "textured_plate")
    result = wf.dispatch(jid)
    assert result["state"] == "prepared"
    assert result.get("error")
    assert "start_intent" not in result
    assert device.status == before, "readiness must not clear errors or change an active printer"
    assert device.starts == [] and device.uploads == []


def test_changed_device_configuration_invalidates_prepared_job(rig):
    config, device = rig
    wf, jid = prepared(config)
    config.data["printers"][0]["serial"] = "030DIFFERENT01"
    job = wf.dispatch(jid)
    assert "identity/configuration changed" in job["error"]["message"]
    assert device.uploads == [] and device.starts == []


def test_bed_confirmation_from_old_device_does_not_authorize_replacement(rig):
    config, device = rig
    old_workflow = Workflow(config)
    mark_ready(old_workflow, device)
    config.data["printers"][0].update(host="replacement.example.invalid", serial="030REPLACEMENT01")
    # The new artifact is prepared for the replacement, so only the stale physical-bed token must block it.
    wf, jid = prepared(config)
    result = wf.dispatch(jid)
    assert "readiness is missing or expired" in result["error"]["message"]
    assert device.uploads == [] and device.starts == []


def test_interrupted_upload_after_restart_uses_new_target_and_starts_once(rig, monkeypatch):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    partial_target = "cache/previous-transfer-left-a-partial-file.3mf"
    wf.store.acquire(jid)
    wf.store.update(jid, "uploading", remote=partial_target)
    original_upload = device.upload

    def upload_without_overwrite(local, remote, expected_sha256):
        assert remote != partial_target, "restart must not overwrite the old partial remote artifact"
        return original_upload(local, remote, expected_sha256)

    monkeypatch.setattr(device, "upload", upload_without_overwrite)
    restarted = Workflow(Config(config.path))
    result = restarted.dispatch(jid)
    assert result["state"] == "printing"
    assert result["start_intent"]["remote"] != partial_target
    assert len(device.uploads) == 1 and len(device.starts) == 1
    restarted.dispatch(jid)
    assert len(device.uploads) == 1 and len(device.starts) == 1


def test_paused_job_remains_paused_and_holds_lease_after_restart(rig):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    wf.dispatch(jid)
    device.status.update(gcode_state="PAUSE", layer_num=3, stage_id=16)
    device.status["raw"].update(gcode_state="PAUSE", layer_num=3, stg_cur=16)
    assert wf.reconcile(jid)["state"] == "paused"
    restarted = Workflow(Config(config.path))
    assert restarted.dispatch(jid)["state"] == "paused"
    assert restarted.store.leased_job("mini") == jid
    assert len(device.starts) == 1


@pytest.mark.parametrize(("cancel_requested", "print_error", "expected"), [
    (True, 0, "canceled"),
    (False, 0, "failed"),
    (True, 123456, "failed"),
])
def test_failed_state_distinguishes_requested_cancel_from_printer_error(rig, cancel_requested, print_error, expected):
    config, device = rig
    wf, jid = prepared(config)
    mark_ready(wf, device)
    wf.dispatch(jid)
    if cancel_requested:
        wf.store.update(jid, cancel_intent={"at": time.time(), "source": "explicit_user_command"})
    device.status["gcode_state"] = "FAILED"
    device.status["raw"].update(gcode_state="FAILED", print_error=print_error)
    result = Workflow(Config(config.path)).reconcile(jid)
    assert result["state"] == expected
    assert result["observation"]["print_error"] == print_error
    assert wf.store.leased_job("mini") is None
    assert len(device.starts) == 1


@pytest.mark.parametrize("source_mode", ["file", "blender"])
def test_preparation_retry_uses_captured_geometry_in_new_attempt_folder(rig, monkeypatch, tmp_path, source_mode):
    import trimesh

    config, device = rig
    origin = tmp_path / "model.stl"
    origin.write_bytes(trimesh.creation.box(extents=[8, 7, 1]).export(file_type="stl"))
    original_bytes = origin.read_bytes()
    exports = []
    slices = []

    def export_live_scene(_config, folder, **kwargs):
        exports.append(Path(folder))
        target = Path(folder) / "blender-model-mm.stl"
        target.write_bytes(origin.read_bytes())
        blend = Path(folder) / "source.blend"
        blend.write_bytes(b"source backup for this test")
        return {"stl_path": str(target), "blend_path": str(blend)}

    def slice_with_one_failure(source, folder, _printer, _settings, progress=None):
        slices.append((Path(source), Path(folder), Path(source).read_bytes()))
        if len(slices) == 1:
            raise RuntimeError("Slicer process crashed after source capture")
        output = Path(folder) / "prepared.gcode.3mf"
        output.write_bytes(b"slicer result; print inspection is isolated by the fixture")
        return {"print_path": str(output), "plate": 1}

    monkeypatch.setattr("bambu_workflow.blender.export_blender", export_live_scene)
    monkeypatch.setattr("bambu_workflow.slicer.slice_model", slice_with_one_failure)
    wf = Workflow(config)
    job, _ = wf.store.create("retry-the-original-model", "mini", {
        "source": source_mode, "path": str(origin) if source_mode == "file" else None,
        "selection": {"objects": ["Part"]}, "user_prompt": "Prepare the Part object",
        "print_now": False, "feed": {"kind": "external"},
    })
    wf.prepare(job["id"])
    failed = wf.store.get(job["id"])
    assert failed["state"] == "failed"
    assert "manifest" not in failed
    first_folder = Path(failed["artifact_dir"])
    captured = Path(failed["source_snapshot"]["path"])
    assert captured.read_bytes() == original_bytes

    # The current Blender scene/file can change while the user diagnoses the slicer failure.
    origin.write_bytes(trimesh.creation.box(extents=[40, 30, 2]).export(file_type="stl"))
    reopened = Workflow(Config(config.path))
    reopened.store.update(job["id"], "queued", error=None)
    reopened.prepare(job["id"])
    result = reopened.store.get(job["id"])
    assert result["state"] == "prepared"
    assert Path(result["artifact_dir"]) != first_folder
    assert first_folder.is_dir() and Path(result["artifact_dir"]).is_dir()
    assert result["source_snapshot"]["path"] == str(captured)
    assert result["manifest"]["source"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert [source_bytes for _, _, source_bytes in slices] == [original_bytes, original_bytes]
    assert slices[0][0] == slices[1][0] == captured
    assert slices[0][1] != slices[1][1]
    assert len(exports) == (1 if source_mode == "blender" else 0)
    assert device.uploads == [] and device.starts == []


def test_expired_bed_confirmation_blocks_start(rig):
    config, device = rig
    wf, jid = prepared(config)
    wf.store.bed_ready("mini", task_identity(device.snapshot(), wf.config.printer("mini")), valid_for=-1)
    job = wf.dispatch(jid)
    assert "readiness is missing or expired" in job["error"]["message"]
    assert device.starts == []


def test_bed_token_and_start_intent_are_consumed_atomically(tmp_path):
    store = Store(tmp_path)
    first = store.create("one", "mini", {})[0]["id"]
    second = store.create("two", "mini", {})[0]["id"]
    store.acquire(first)
    store.bed_ready("mini", "idle-task")
    store.consume_bed_and_record_start(first, "idle-task", {"remote": "unique.gcode.3mf"})
    store.release(first)
    store.acquire(second)
    with pytest.raises(ValueError, match="missing or expired"):
        store.consume_bed_and_record_start(second, "idle-task", {"remote": "other.gcode.3mf"})
    reopened = Store(tmp_path)
    assert reopened.get(first)["state"] == "start_sent"
    assert reopened.get(first)["start_intent"]["remote"] == "unique.gcode.3mf"
    assert "start_intent" not in reopened.get(second)


@pytest.mark.parametrize("state", ["queued", "exporting", "analyzing", "slicing"])
def test_startup_recovers_abandoned_preparation_without_losing_print_authorization(rig, monkeypatch, state):
    config, device = rig
    wf = Workflow(config)
    job, _ = wf.store.create("interrupted-prepare", "mini", {"source": "blender", "print_now": True})
    wf.store.update(job["id"], state)
    spawned = []
    monkeypatch.setattr(Workflow, "spawn", lambda self, jid, action: spawned.append((jid, action)))
    restarted = Workflow(Config(config.path))
    assert restarted.recover_workers() == [job["id"]]
    recovered = restarted.store.get(job["id"])
    assert recovered["state"] == "queued"
    assert recovered["request"]["print_now"] is True
    assert not recovered.get("suppress_autostart")
    assert spawned == [(job["id"], "prepare")]
    assert device.starts == [] and device.uploads == []


@pytest.mark.parametrize("state", ["queued", "slicing", "uploading", "prepared"])
def test_startup_does_not_touch_a_live_worker_lock(rig, monkeypatch, state):
    config, _ = rig
    wf = Workflow(config)
    job, _ = wf.store.create("still-running-worker", "mini", {"source": "blender", "print_now": True})
    wf.store.update(job["id"], state)
    folder = config.state_dir / "jobs" / job["id"]
    folder.mkdir(parents=True)
    spawned = []
    monkeypatch.setattr(Workflow, "spawn", lambda self, jid, action: spawned.append((jid, action)))
    with FileLock(str(folder / "worker.lock")):
        assert Workflow(Config(config.path)).recover_workers() == []
    assert spawned == []
    assert wf.store.get(job["id"])["state"] == state
    assert "recovered_at" not in wf.store.get(job["id"])


@pytest.mark.parametrize("state", ["start_sent", "start_uncertain", "printing", "queued", "uploaded", "prepared"])
def test_startup_never_replays_any_job_with_a_durable_start_intent(rig, monkeypatch, state):
    config, device = rig
    wf = Workflow(config)
    job, _ = wf.store.create("start-might-already-have-happened", "mini", {"print_now": True})
    intent = {"remote": "cache/one-attempt-only.3mf", "at": time.time()}
    wf.store.update(job["id"], state, start_intent=intent, dispatch_requested_at=time.time())
    spawned = []
    monkeypatch.setattr(Workflow, "spawn", lambda self, jid, action: spawned.append((jid, action)))
    assert Workflow(Config(config.path)).recover_workers() == []
    result = wf.store.get(job["id"])
    assert result["start_intent"] == intent and result["state"] == state
    assert spawned == [] and device.starts == [] and device.uploads == []


def test_startup_rechecks_start_intent_after_taking_worker_lock(rig, monkeypatch):
    config, device = rig
    wf = Workflow(config)
    job, _ = wf.store.create("worker-finished-during-recovery-scan", "mini", {"print_now": True})
    stale_scan = [wf.store.get(job["id"])]
    intent = {"remote": "cache/worker-already-sent-this.3mf", "at": time.time()}
    # The scan saw queued, but the former live worker completed dispatch before recovery got its lock.
    wf.store.update(job["id"], "printing", start_intent=intent)
    monkeypatch.setattr(wf.store, "list", lambda *args: stale_scan)
    spawned = []
    monkeypatch.setattr(Workflow, "spawn", lambda self, jid, action: spawned.append((jid, action)))
    assert wf.recover_workers() == []
    assert wf.store.get(job["id"])["state"] == "printing"
    assert wf.store.get(job["id"])["start_intent"] == intent
    assert spawned == [] and device.starts == [] and device.uploads == []


@pytest.mark.parametrize("state", ["uploading", "uploaded", "prepared"])
@pytest.mark.parametrize("authorization", ["original_print", "start_prepared", "none"])
def test_startup_recovers_transfer_only_with_a_persisted_print_instruction(rig, monkeypatch, state, authorization):
    config, device = rig
    wf, jid = prepared(config)
    job = wf.store.get(jid)
    request = {**job["request"], "print_now": authorization == "original_print"}
    changes = {"request": request}
    if authorization == "start_prepared":
        changes["dispatch_requested_at"] = time.time()
    wf.store.update(jid, state, **changes)
    spawned = []
    monkeypatch.setattr(Workflow, "spawn", lambda self, job_id, action: spawned.append((job_id, action)))
    recovered = Workflow(Config(config.path)).recover_workers()
    expected = authorization != "none"
    assert recovered == ([jid] if expected else [])
    assert spawned == ([(jid, "dispatch")] if expected else [])
    assert "start_intent" not in wf.store.get(jid)
    assert device.starts == [] and device.uploads == []


@pytest.mark.parametrize("authorization", ["original_print", "start_prepared"])
def test_prepared_readiness_error_is_not_requeued_automatically(rig, monkeypatch, authorization):
    config, device = rig
    wf, jid = prepared(config)
    request = {**wf.store.get(jid)["request"], "print_now": authorization == "original_print"}
    error = {"stage": "readiness", "message": "Printer is occupied by another task"}
    fields = {"request": request, "error": error}
    if authorization == "start_prepared":
        fields["dispatch_requested_at"] = time.time()
    wf.store.update(jid, "prepared", **fields)
    spawned = []
    monkeypatch.setattr(Workflow, "spawn", lambda self, job_id, action: spawned.append((job_id, action)))
    assert Workflow(Config(config.path)).recover_workers() == []
    assert wf.store.get(jid)["error"] == error
    assert spawned == [] and device.starts == [] and device.uploads == []


def test_prepared_print_interrupted_before_dispatch_recovers_to_one_start(rig, monkeypatch):
    config, device = rig
    wf, jid = prepared(config)
    request = {**wf.store.get(jid)["request"], "print_now": True}
    wf.store.update(jid, "prepared", request=request, error=None)
    mark_ready(wf, device)
    spawned = []

    def resume_worker(self, job_id, action):
        spawned.append((job_id, action))
        self.run_worker(job_id, action)

    monkeypatch.setattr(Workflow, "spawn", resume_worker)
    restarted = Workflow(Config(config.path))
    assert restarted.recover_workers() == [jid]
    assert restarted.store.get(jid)["state"] == "printing"
    assert restarted.recover_workers() == []
    restarted.dispatch(jid)
    assert spawned == [(jid, "dispatch")]
    assert len(device.starts) == 1 and len(device.uploads) == 1


def test_blocked_dispatch_releases_its_lease_and_stays_blocked_across_restart(rig, monkeypatch):
    config, device = rig
    wf, jid = prepared(config)
    request = {**wf.store.get(jid)["request"], "print_now": True}
    wf.store.update(jid, request=request)
    mark_ready(wf, device)
    wf.store.acquire(jid)
    device.status["gcode_state"] = "RUNNING"
    device.status["raw"]["gcode_state"] = "RUNNING"
    result = wf.dispatch(jid)
    assert result["state"] == "prepared" and result["error"]["stage"] == "readiness"
    assert wf.store.leased_job("mini") is None
    spawned = []
    monkeypatch.setattr(Workflow, "spawn", lambda self, job_id, action: spawned.append((job_id, action)))
    assert Workflow(Config(config.path)).recover_workers() == []
    assert spawned == [] and device.starts == [] and device.uploads == []


def test_recovered_original_print_instruction_reaches_dispatch_without_new_approval(rig, monkeypatch, tmp_path):
    import trimesh

    config, device = rig
    origin = tmp_path / "approved-part.stl"
    origin.write_bytes(trimesh.creation.box(extents=[4, 4, 1]).export(file_type="stl"))
    wf = Workflow(config)
    job, _ = wf.store.create("approved-before-disconnection", "mini", {
        "source": "file", "path": str(origin), "selection": None, "user_prompt": "Print this model",
        "print_now": True, "feed": {"kind": "external"},
    })
    dispatched = []

    def sliced_result(source, folder, _printer, _settings, progress=None):
        output = Path(folder) / "prepared.gcode.3mf"
        output.write_bytes(b"prepared output for recovery authorization test")
        return {"print_path": str(output), "plate": 1}

    monkeypatch.setattr("bambu_workflow.slicer.slice_model", sliced_result)
    monkeypatch.setattr(Workflow, "dispatch", lambda self, jid: dispatched.append(jid))
    monkeypatch.setattr(Workflow, "spawn", lambda self, jid, action: self.run_worker(jid, action))
    assert Workflow(Config(config.path)).recover_workers() == [job["id"]]
    assert dispatched == [job["id"]]
    assert wf.store.get(job["id"])["request"]["print_now"] is True
    assert device.starts == [] and device.uploads == []


def test_next_job_reconciles_completed_previous_lease_before_acquiring(rig):
    config, device = rig
    wf, first = prepared(config)
    mark_ready(wf, device)
    assert wf.dispatch(first)["state"] == "printing"
    device.status["gcode_state"] = "FINISH"
    device.status["raw"]["gcode_state"] = "FINISH"
    # A person has cleared the previous part; its finished identity remains in telemetry.
    mark_ready(wf, device)
    _, second = prepared(config, "next-authorized-part")
    assert wf.store.leased_job("mini") == first
    result = Workflow(Config(config.path)).dispatch(second)
    assert result["state"] == "printing"
    assert wf.store.get(first)["state"] == "completed"
    assert wf.store.leased_job("mini") == second
    assert len(device.starts) == 2
