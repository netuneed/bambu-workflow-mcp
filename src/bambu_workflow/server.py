from __future__ import annotations

import json
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import Config
from .store import DISPATCHED
from .workflow import Workflow, task_identity

mcp = FastMCP("Bambu Workflow", instructions=(
    "Use print_model for an explicit user instruction to print the current Blender model or a file. "
    "Use prepare_print to prepare without starting. Keep one stable request_id per human instruction, "
    "reuse it on retries. Never invent bed readiness or spool metadata. Get job_status for progress. "
    "Never repeat start on start_uncertain. mark_bed_ready requires the user's physical observation. "
    "Critical assessment blocks launch. Medium findings pause at prepared for optional review; "
    "without an answer, continue the original printable geometry with start_prepared, never silently edit it. "
    "Printing the development acceptance test requires a separate explicit user instruction."))


def workflow():
    return Workflow(Config())


@mcp.tool()
def list_printers() -> dict:
    """List configured printer IDs, models and actual implemented adapter capabilities. No secrets."""
    from .transport import CAPABILITIES
    config=Config()
    return {"printers":[{k:p.get(k) for k in ("id","model","nozzle_diameter","nozzle_type","plate_type","plate_confirmed")}
                        for p in config.data["printers"]],"capabilities":CAPABILITIES}


@mcp.tool()
def printer_status(printer_id: str | None = None) -> dict:
    """Obtain fresh live MQTT telemetry, firmware, temperatures, progress, HMS and material metadata."""
    return Config().adapter(printer_id).snapshot()


@mcp.tool()
def prepare_print(request_id: str, printer_id: str | None = None, source: str = "blender",
                  path: str | None = None, selection: dict | None = None,
                  user_prompt: str = "", feed: dict | None = None,process_profile: str | None=None,
                  process_overrides: dict | None=None) -> dict:
    """Asynchronously export current Blender selection or snapshot an STL/3MF, validate and slice.

    Never uploads or starts. request_id must stay identical on retries of this human instruction.
    source is blender or file. selection can specify objects or collection; default selected visible objects.
    user_prompt is the user's actual words, passed unchanged to Blender MCP. Poll job_status(job_id).
    process_profile and validated process_overrides apply only to this job; global configuration is retained.
    """
    return workflow().submit(request_id,printer_id,source=source,path=path,selection=selection,
                             user_prompt=user_prompt,feed=feed,process_profile=process_profile,process_overrides=process_overrides)


@mcp.tool()
def print_model(request_id: str, printer_id: str | None = None, source: str = "blender",
                path: str | None = None, selection: dict | None = None,
                user_prompt: str = "", feed: dict | None = None,process_profile: str | None=None,
                process_overrides: dict | None=None) -> dict:
    """One authorized print workflow: live Blender export/file snapshot, inspect, slice, verify, upload, start.

    Call only for an explicit user instruction to print this model. No repeated general approval is required.
    Requires initial setup and a fresh single-use mark_bed_ready based on an actual human observation.
    This returns job_id immediately. Poll job_status. start_sent means an attempt, never proof of printing.
    Reuse request_id and original arguments after timeouts. Never create a new request_id to retry a start.
    """
    return workflow().submit(request_id,printer_id,source=source,path=path,selection=selection,
                             print_now=True,user_prompt=user_prompt,feed=feed,
                             process_profile=process_profile,process_overrides=process_overrides)


@mcp.tool()
def job_status(job_id: str, reconcile: bool = True) -> dict:
    """Read persisted job, artifacts and manifest. Reconcile an attempted launch with fresh exact-job telemetry."""
    wf=workflow()
    job=wf.store.get(job_id)
    if reconcile and job["state"] in DISPATCHED:
        job=wf.reconcile(job_id)
    job["events"]=wf.store.events(job_id)
    return job


@mcp.tool()
def print_assessment(job_id: str) -> dict:
    """Read critical blockers, optional medium findings and passed checks for a prepared job.

    Critical findings block launch. For medium findings, offer the concrete optional change and
    allow reasonable time to reply. If unanswered and mandatory checks pass, start_prepared retains
    the original geometry. Silence never authorizes a geometry change or overrides a critical blocker.
    """
    from .assessment import for_job
    return {"job_id": job_id, **for_job(workflow().store.get(job_id))}


@mcp.tool()
def list_jobs(limit: int = 20) -> list[dict]:
    """List persisted jobs across MCP restarts."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be from 1 to 100")
    return [{k:j.get(k) for k in ("id","printer_id","state","progress","updated_at","error","manifest_path")}
            for j in workflow().store.list(limit)]


@mcp.tool()
def mark_bed_ready(printer_id: str, bed_is_clear: bool, plate_type: str) -> dict:
    """Record a user's physical observation that the previous part is removed and the correct plate installed.

    Do not infer this from idle or camera. Call only from the user's actual observation. One use, 30 minutes.
    plate_type: textured_plate, hot_plate, cool_plate or engineering_plate. Also confirms initial plate type.
    """
    if not bed_is_clear:
        raise ValueError("Remove the previous part and prepare the bed physically first.")
    if plate_type not in {"textured_plate","hot_plate","cool_plate","engineering_plate"}:
        raise ValueError("Unsupported plate type.")
    wf=workflow()
    status=wf.config.adapter(printer_id).snapshot()
    if status.get("gcode_state") not in {"IDLE","FINISH","FAILED"}:
        raise ValueError("Bed cannot be marked ready while the printer is busy.")
    raw=status.get("raw",{})
    if status.get("gcode_state")=="FAILED" and (raw.get("gcode_file") or raw.get("print_type") not in {None,"idle"}
                                                   or raw.get("nozzle_target_temper",0) or raw.get("bed_target_temper",0)):
        raise ValueError("Previous failed job has not returned to an idle, non-heating state.")
    if status.get("raw",{}).get("hms") or status.get("raw",{}).get("print_error",0):
        raise ValueError("Resolve active printer errors before marking bed readiness.")
    from filelock import FileLock
    with FileLock(str(wf.config.path)+".lock"):
        current=Config(wf.config.path)
        for p in current.data["printers"]:
            if p["id"]==printer_id:
                p.update(plate_type=plate_type,plate_confirmed=True)
        temp=current.path.with_suffix(".new")
        temp.write_text(json.dumps(current.data,ensure_ascii=False,indent=2),encoding="utf-8")
        temp.replace(current.path)
    return wf.store.bed_ready(printer_id,task_identity(status,wf.config.printer(printer_id)))


@mcp.tool()
def start_prepared(job_id: str) -> dict:
    """Start this prepared job only when the user has instructed it. Uses the existing stable job identity.

    Does not require another general approval after the user's print instruction. Busy/error/readiness
    conditions are returned specifically. An uncertain prior launch is reconciled, never resent.
    """
    wf=workflow()
    job=wf.store.get(job_id)
    if job.get("start_intent"):
        return wf.reconcile(job_id)
    if job["state"] not in {"prepared","uploaded","uploading","transfer_failed"}:
        raise ValueError("Job is not prepared. Inspect job_status.")
    from .assessment import assess
    manifest=job["manifest"]
    assessment=manifest.get("assessment") or assess(manifest.get("analysis",{}),manifest.get("inspection",{}))
    if not assessment["can_print"]:
        raise ValueError("Critical findings block launch. Correct the condition and prepare again.")
    wf.store.update(job_id,dispatch_requested_at=time.time(),review_pending=False,
                    review_outcome={"action":"print_original","at":time.time(),
                                    "note":"Original geometry retained; this does not authorize edits."})
    wf.spawn(job_id,"dispatch")
    return {"job_id":job_id,"state":job["state"],"next":"Poll job_status."}


@mcp.tool()
def resolve_uncertain_job(job_id: str, outcome: str, human_evidence: str) -> dict:
    """Release a launch hold only after an explicit human verification of the printer/history.

    Allowed outcome: not_started, completed, canceled. This is a manual resolution, never an automatic
    conclusion from idle. human_evidence must quote the user's actual observation. Do not fabricate it.
    The old start intent remains permanently recorded and this job can never be started again.
    """
    if outcome not in {"not_started","completed","canceled"} or not human_evidence.strip():
        raise ValueError("Provide a supported outcome and the user's actual observation.")
    wf=workflow()
    job=wf.store.get(job_id)
    if job["state"] != "start_uncertain":
        raise ValueError("Only an uncertain launch can be resolved manually.")
    status=wf.config.adapter(job["printer_id"]).snapshot()
    import hashlib
    printer=wf.config.printer(job["printer_id"])
    device_hash=hashlib.sha256((printer["host"]+"/"+printer["serial"]).encode()).hexdigest()
    if (not status.get("identity_verified") or status.get("model") != printer["model"]
            or device_hash != job["manifest"]["device_identity_hash"]
            or status.get("gcode_state") not in {"IDLE","FINISH","FAILED"}):
        raise ValueError("The configured printer must have a verified identity and no active print.")
    state="completed" if outcome=="completed" else "canceled"
    return wf.store.update(job_id,state,manual_resolution={"at":time.time(),"outcome":outcome,
                           "human_evidence":human_evidence,"source":"human_inspection"},error=None)


@mcp.tool()
def retry_preparation(job_id: str) -> dict:
    """Recover an interrupted preparation using the original captured request. Never retries a launch."""
    wf=workflow()
    job=wf.store.get(job_id)
    if job.get("start_intent") or job.get("manifest"):
        raise ValueError("This job has a prepared artifact or prior launch. Inspect it; use start_prepared if appropriate.")
    if job["state"] not in {"failed","queued","exporting","analyzing","slicing"}:
        raise ValueError("Preparation cannot be retried in this state.")
    from filelock import FileLock,Timeout
    lock=FileLock(str(wf.config.state_dir/"jobs"/job_id/"worker.lock"))
    try:
        with lock.acquire(timeout=0):
            wf.store.update(job_id,"queued",error=None)
    except Timeout:
        return {"job_id":job_id,"state":job["state"],"message":"Worker is still active."}
    wf.spawn(job_id,"prepare")
    return {"job_id":job_id,"state":"queued"}


@mcp.tool()
def printer_files(printer_id: str | None = None, directory: str = "cache") -> list[dict]:
    """List SD-card files over FTPS. Does not delete or start files."""
    return Config().adapter(printer_id).list_files(directory)


@mcp.tool()
def printer_control(printer_id: str, action: str, parameters: dict | None = None) -> dict:
    """Explicit user-requested pause/resume/cancel, supported fan, temperature and speed settings.

    Supported actions and model limits are enforced by the adapter. Never clears HMS or auto-resumes.
    Do not use resume until the physical cause of a pause has been resolved.
    """
    return workflow().control(printer_id,action,parameters)


@mcp.tool()
def camera_snapshot(printer_id: str | None = None) -> dict:
    """Save one fresh A1 mini camera JPEG. Camera is not proof of an empty bed."""
    config=Config()
    folder=config.state_dir/"camera"
    folder.mkdir(exist_ok=True)
    return config.adapter(printer_id).camera(folder/(str(time.time_ns())+".jpg"))


@mcp.tool()
def diagnose_printer(printer_id: str | None = None) -> dict:
    """Read-only independent MQTT, FTPS and local dependency diagnostics. Never changes mode/firmware."""
    config=Config()
    result=config.adapter(printer_id).probe()
    result["slicer_executable_exists"]=Path(config.data.get("slicer",{}).get("executable","")).is_file()
    return result


def main():
    import os
    if os.environ.get("BAMBU_WORKFLOW_SKIP_RECOVERY") != "1":
        workflow().recover_workers()
    mcp.run(transport="stdio")

if __name__ == "__main__": main()
