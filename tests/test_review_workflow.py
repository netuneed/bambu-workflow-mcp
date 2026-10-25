"""Exercise review holds through real stdio clients with an isolated fake printer."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
import trimesh

from test_workflow import rig


@pytest.fixture
def wire_review(rig, tmp_path):
    config, _ = rig
    source = tmp_path / "original-part.stl"
    source.write_bytes(trimesh.creation.box(extents=[1, 1, 4]).export(file_type="stl"))
    original = source.read_bytes()
    tests_dir = Path(__file__).resolve().parent
    worker = tmp_path / "fake_printer_mcp.py"
    worker.write_text(
        "import sys\nsys.path.insert(0, " + repr(str(tests_dir)) + ")\n" + r'''
import json, os
from pathlib import Path
from test_workflow import MemoryPrinter
from bambu_workflow.config import Config
from bambu_workflow.workflow import Workflow
from bambu_workflow import slicer

state_file = Path(os.environ['BAMBU_TEST_PRINTER_STATE'])

class PersistentPrinter(MemoryPrinter):
    def __init__(self):
        super().__init__()
        if state_file.exists():
            old = json.loads(state_file.read_text(encoding='utf-8'))
            self.status, self.starts, self.uploads = old['status'], old['starts'], old['uploads']

    def save(self):
        state_file.write_text(json.dumps(dict(status=self.status, starts=self.starts, uploads=self.uploads)), encoding='utf-8')

    def upload(self, *args):
        result = super().upload(*args)
        self.save()
        return result

    def start(self, *args, **kwargs):
        result = super().start(*args, **kwargs)
        self.save()
        return result

device = PersistentPrinter()
device.save()
Config.adapter = lambda self, printer_id=None: device

def fake_slice(source, folder, printer, settings, progress=None):
    output = Path(folder) / 'prepared.gcode.3mf'
    output.write_bytes(b'Isolated fake slicing output; no hardware or slicer process is called')
    return dict(print_path=str(output), plate=1)

def fake_inspection(*args, **kwargs):
    warnings = [{'msg':'unknown_vendor_safety_diagnostic','level':'9'}] if os.environ.get('BAMBU_TEST_CRITICAL') == '1' else []
    return dict(gcode_md5='a' * 32, warnings=warnings)

slicer.slice_model = fake_slice
slicer.inspect_print = fake_inspection
Workflow.spawn = lambda self, jid, action: self.run_worker(jid, action)
Workflow.monitor = lambda self, jid, seconds=180: self.reconcile(jid)

from bambu_workflow.server import main
main()
''', encoding="utf-8")
    env = os.environ.copy()
    env.pop("BAMBU_WORKFLOW_SKIP_RECOVERY", None)
    env["BAMBU_WORKFLOW_CONFIG"] = str(config.path)
    env["BAMBU_TEST_PRINTER_STATE"] = str(tmp_path / "fake-printer-state.json")
    env["PYTHONUTF8"] = "1"

    @asynccontextmanager
    async def connect(*, critical=False):
        process_env = {**env, "BAMBU_TEST_CRITICAL": "1" if critical else "0"}
        params = StdioServerParameters(command=sys.executable, args=[str(worker)], env=process_env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    def printer_state():
        return json.loads(Path(env["BAMBU_TEST_PRINTER_STATE"]).read_text(encoding="utf-8"))

    return config, source, original, connect, printer_state


async def call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    assert not result.isError, result.content
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(entry.text for entry in result.content if entry.type == "text"))


def test_medium_print_survives_stdio_disconnect_and_starts_original_once(wire_review):
    config, source, original, connect, printer_state = wire_review

    async def scenario():
        arguments = dict(request_id="one-human-print-instruction", printer_id="mini", source="file",
                         path=str(source), user_prompt="Print this model", feed={"kind": "external"})
        async with connect() as session:
            submitted = await call(session, "print_model", arguments)
            jid = submitted["job_id"]
            pending = await call(session, "job_status", {"job_id": jid})
            assessment = await call(session, "print_assessment", {"job_id": jid})
            assert pending["state"] == "prepared" and pending["review_pending"] is True
            assert assessment["level"] == "medium" and assessment["can_print"] is True
            prepared_bytes = Path(pending["manifest"]["print_path"]).read_bytes()
        assert printer_state()["starts"] == [] and printer_state()["uploads"] == []

        # No optional reply has been supplied; a new real stdio server performs normal recovery.
        async with connect() as session:
            repeated = await call(session, "print_model", arguments)
            assert repeated["duplicate"] is True and repeated["job_id"] == jid
            pending = await call(session, "job_status", {"job_id": jid})
            assert pending["state"] == "prepared" and pending["review_pending"] is True
            assert "start_intent" not in pending
            assert printer_state()["starts"] == [] and printer_state()["uploads"] == []
            await call(session, "mark_bed_ready", {"printer_id": "mini", "bed_is_clear": True,
                                                    "plate_type": "textured_plate"})
            await call(session, "start_prepared", {"job_id": jid})
            await call(session, "start_prepared", {"job_id": jid})
            result = await call(session, "job_status", {"job_id": jid})
            assert result["state"] == "printing"
            assert result["request"]["print_now"] is True
            assert result["review_outcome"]["action"] == "print_original"
            assert Path(result["manifest"]["print_path"]).read_bytes() == prepared_bytes
            assert Path(result["source_snapshot"]["path"]).read_bytes() == original
        assert len(printer_state()["starts"]) == 1
        assert len(printer_state()["uploads"]) == 1
        assert source.read_bytes() == original

    asyncio.run(scenario())


def test_unclassified_warning_stays_critical_across_stdio_restart(wire_review):
    config, source, original, connect, printer_state = wire_review

    async def scenario():
        async with connect(critical=True) as session:
            submitted = await call(session, "print_model", dict(
                request_id="unknown-diagnostic", printer_id="mini", source="file", path=str(source),
                user_prompt="Print this model", feed={"kind": "external"}))
            jid = submitted["job_id"]
            report = await call(session, "print_assessment", {"job_id": jid})
            assert report["level"] == "critical" and report["can_print"] is False
            assert any(finding["code"] == "unclassified_slicer_warning" for finding in report["findings"])
        async with connect(critical=True) as session:
            blocked = await session.call_tool("start_prepared", {"job_id": jid})
            assert blocked.isError
            report = await call(session, "print_assessment", {"job_id": jid})
            assert report["can_print"] is False
        assert printer_state()["starts"] == [] and printer_state()["uploads"] == []
        assert source.read_bytes() == original

    asyncio.run(scenario())
