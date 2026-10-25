"""The example stdio client keeps asynchronous workflow workers alive; no hardware."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_workflow import prepared, rig


spec = importlib.util.spec_from_file_location("example_mcp_client", Path(__file__).parents[1] / "scripts" / "call_mcp.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def response(value, *, error=False, text=False):
    return SimpleNamespace(structuredContent=None if text else value, isError=error,
                           content=[SimpleNamespace(type="text", text=json.dumps(value))] if text else [])


@pytest.fixture
def wire(monkeypatch, capsys):
    """A session records whether every poll precedes closing its stdio process."""
    state = SimpleNamespace(calls=[], parameters=None, open=False, session_open=False, clock=0.0, replies=[])

    @asynccontextmanager
    async def stdio(parameters):
        state.parameters, state.open = parameters, True
        try:
            yield object(), object()
        finally:
            state.open = False

    class Session:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            assert state.open
            state.session_open = True
            return self

        async def __aexit__(self, *args):
            state.session_open = False

        async def initialize(self):
            assert state.open

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="prepare_print"), SimpleNamespace(name="job_status")])

        async def call_tool(self, name, payload, **kwargs):
            assert state.open and state.session_open
            state.calls.append((name, payload, kwargs))
            reply = state.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

    async def sleep(seconds):
        state.clock += seconds

    monkeypatch.setattr(client, "stdio_client", stdio)
    monkeypatch.setattr(client, "ClientSession", Session)
    monkeypatch.setattr(client.asyncio, "sleep", sleep)
    monkeypatch.setattr(client.time, "monotonic", lambda: state.clock)
    monkeypatch.setattr(client.time, "time", lambda: 1000.0 + state.clock)

    def run(tool, replies, *, timeout=1800, no_wait=False, payload=None):
        state.replies = replies[:]
        args = argparse.Namespace(python="isolated-python", config=None, tool=tool, timeout=timeout, no_wait=no_wait)
        code = asyncio.run(client.run(args, payload or {"request_id": "same-human-instruction"}))
        result = json.loads(capsys.readouterr().out)
        assert not state.open and not state.session_open
        assert state.parameters.env["BAMBU_WORKFLOW_SKIP_RECOVERY"] == "1"
        return code, result

    return state, run


def job(state, **extra):
    return {"id": "one-job", "state": state, "request": {"print_now": True}, **extra}


def live(state="preparing", **extra):
    return job(state, start_intent={"at": 990}, observation={"at": 1005, "identity_match": True}, **extra)


def test_prepare_waits_in_same_session_until_artifact_is_prepared(wire):
    state, run = wire
    prepared = job("prepared", request={"print_now": False}, manifest={"print_path": "snapshot.gcode.3mf"})
    code, output = run("prepare_print", [response({"job_id": "one-job", "state": "queued"}),
                                        response(job("slicing")), response(prepared)])
    assert code == 0 and output["state"] == "prepared" and output["waited"]
    assert output["result"]["manifest"]["print_path"] == "snapshot.gcode.3mf"
    assert [name for name, _, _ in state.calls] == ["prepare_print", "job_status", "job_status"]


def test_clear_print_does_not_exit_at_transient_prepared_or_uploaded(wire):
    state, run = wire
    code, output = run("print_model", [response({"job_id": "one-job", "state": "queued"}),
                                      response(job("prepared")), response(job("uploading")),
                                      response(job("uploaded")), response(live())])
    assert code == 0 and output["state"] == "preparing"
    assert [name for name, _, _ in state.calls].count("print_model") == 1
    assert len(state.calls) == 5
    assert all(payload == {"job_id": "one-job", "reconcile": True} for name, payload, _ in state.calls[1:])


def test_medium_is_successful_prepared_review_without_dispatch(wire):
    state, run = wire
    medium = job("prepared", review_pending=True, assessment={"level": "medium", "can_print": True})
    code, output = run("print_model", [response({"job_id": "one-job"}), response(medium)])
    assert code == 0 and output["state"] == "prepared" and output["review_pending"]
    assert len(state.calls) == 2 and "printing" not in output


@pytest.mark.parametrize("final", [job("failed"), job("transfer_failed"), job("start_uncertain"),
                                    job("prepared", error={"message": "Bed is not ready"}),
                                    job("prepared", assessment={"level": "critical", "can_print": False})])
def test_asynchronous_failure_is_nonzero_despite_successful_submission(wire, final):
    state, run = wire
    code, output = run("print_model", [response({"job_id": "one-job"}), response(final)])
    assert code == 1 and output["is_error"] and output["state"] == final["state"]
    assert len(state.calls) == 2


def test_start_waits_for_fresh_exact_job_never_replays(wire):
    state, run = wire
    stale = job("preparing", start_intent={"at": 900}, observation={"at": 950, "identity_match": True})
    foreign = job("preparing", start_intent={"at": 990}, observation={"at": 1000, "identity_match": False})
    disconnected = live(last_connection_error="Fresh read failed")
    code, output = run("start_prepared", [response({"job_id": "one-job", "state": "prepared"}),
                                         response(stale), response(foreign), response(disconnected), response(live("printing"))],
                       payload={"job_id": "one-job"})
    assert code == 0 and output["state"] == "printing"
    assert [name for name, _, _ in state.calls].count("start_prepared") == 1
    assert len(state.calls) == 5


def test_timeout_is_explicit_pending_and_retains_job_identity(wire):
    state, run = wire
    code, output = run("print_model", [response({"job_id": "one-job"}), response(job("slicing"))], timeout=1)
    assert code == 1 and output["timed_out"] and output["pending"] and output["job_id"] == "one-job"
    assert output["state"] == "slicing" and "Do not repeat start" in output["next_step"]
    assert len(state.calls) == 2


def test_poll_response_loss_is_pending_and_never_resubmits(wire):
    state, run = wire
    code, output = run("start_prepared", [response({"job_id": "one-job"}), TimeoutError("Lost status response")],
                       payload={"job_id": "one-job"})
    assert code == 1 and output["pending"] and output["job_id"] == "one-job"
    assert "Lost status response" in output["error"]
    assert [name for name, _, _ in state.calls] == ["start_prepared", "job_status"]


def test_submission_response_loss_preserves_original_request_without_retry(wire):
    state, run = wire
    code, output = run("print_model", [TimeoutError("Lost submission response")])
    assert code == 1 and output["pending"] and output["request_id"] == "same-human-instruction"
    assert [name for name, _, _ in state.calls] == ["print_model"]


def test_explicit_preparation_retry_also_keeps_the_session_open(wire):
    state, run = wire
    code, output = run("retry_preparation", [response({"job_id": "one-job", "state": "queued"}),
                                            response(job("slicing")), response(job("prepared", request={"print_now": False}))],
                       payload={"job_id": "one-job"})
    assert code == 0 and output["state"] == "prepared" and len(state.calls) == 3


def test_no_wait_is_opt_in_and_warns_about_worker_lifetime(wire):
    state, run = wire
    code, output = run("prepare_print", [response({"job_id": "one-job", "state": "queued"})], no_wait=True)
    assert code == 0 and not output["waited"] and "interrupt" in output["warning"]
    assert len(state.calls) == 1


def test_catalog_stays_metadata_only_without_poll_or_recovery(wire):
    state, run = wire
    code, output = run(None, [])
    assert code == 0 and output == {"tools": ["prepare_print", "job_status"], "printer_contacted": False}
    assert state.calls == []


def test_nonworkflow_json_text_response_is_decoded_without_wait(wire):
    state, run = wire
    code, output = run("list_printers", [response({"printers": []}, text=True)])
    assert code == 0 and output["result"] == {"printers": []} and len(state.calls) == 1


def test_explicit_tool_error_does_not_poll_or_start(wire):
    state, run = wire
    code, output = run("start_prepared", [response({"error": "Critical findings block launch"}, error=True)],
                       payload={"job_id": "one-job"})
    assert code == 1 and output["is_error"] and len(state.calls) == 1


def test_main_allows_long_workflow_budget(monkeypatch):
    observed = {}

    async def run(arguments, payload):
        observed.update(timeout=arguments.timeout, payload=payload, no_wait=arguments.no_wait)
        return 0

    monkeypatch.setattr(client, "run", run)
    monkeypatch.setattr(client.sys, "argv", ["call_mcp.py", "--timeout", "3600", "--tool", "prepare_print",
                                          "--arguments", '{"request_id":"stable"}'])
    assert client.main() == 0 and observed == {"timeout": 3600, "payload": {"request_id": "stable"}, "no_wait": False}


def test_start_clears_previous_refusal_before_async_worker_can_be_polled(rig, monkeypatch):
    from bambu_workflow import server
    config, device = rig
    workflow, jid = prepared(config)
    manifest = workflow.store.get(jid)["manifest"]
    manifest["assessment"] = {"level": "clear", "can_print": True, "findings": []}
    old_error = {"stage": "readiness", "message": "Previous readiness failure"}
    workflow.store.update(jid, manifest=manifest, error=old_error)
    observed = []
    monkeypatch.setattr(server, "workflow", lambda: workflow)
    # The worker has not run yet, as when job_status wins the process scheduling race.
    monkeypatch.setattr(workflow, "spawn", lambda job_id, action: observed.append(workflow.store.get(job_id)))
    server.start_prepared(jid)
    assert observed[0]["state"] == "prepared" and observed[0]["error"] is None
    assert observed[0]["dispatch_requested_at"] and device.starts == []
    # Clearing an old refusal does not suppress a real failure in the new attempt.
    result = workflow.dispatch(jid)  # No physical bed marker in this isolated mock fixture.
    assert result["state"] == "prepared" and result["error"]
    assert result["error"]["message"] != old_error["message"]
    assert not result.get("start_intent") and device.starts == []
