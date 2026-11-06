from __future__ import annotations

import hashlib
from copy import deepcopy
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from filelock import FileLock, Timeout

from .config import Config
from .store import DISPATCHED, TERMINAL, Store
from . import __version__
from .assessment import assess


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def task_identity(status: dict, printer: dict | None = None) -> str:
    raw = status.get("raw", status)
    identity = {k: raw.get(k, status.get(k)) for k in
                ("gcode_file", "subtask_name", "task_id", "subtask_id", "project_id")}
    if printer:
        identity["device_hash"] = hashlib.sha256((printer["host"]+"/"+printer["serial"]).encode()).hexdigest()
    return json.dumps(identity,sort_keys=True)


def live_material(status: dict, feed: dict | None = None) -> dict:
    from .materials import live_material as read_material
    return read_material(status, feed)


def selected_material(status: dict, printer: dict, feed: dict | None = None) -> dict:
    material = live_material(status, feed)
    if not material.get("type") and material.get("feed", {}).get("kind") == "external" and printer.get("material"):
        material.update(type=printer["material"], source=printer.get("material_source", "user_configuration"),
                        live_metadata_verified=False,
                        note="External spool type comes from explicit user setup; this printer did not report it.")
    return material


def ensure_ready(status: dict, printer: dict, manifest: dict, require_idle=True):
    if manifest.get("device_identity_hash") and hashlib.sha256((printer["host"] + "/" + printer["serial"]).encode()).hexdigest() != manifest["device_identity_hash"]:
        raise ValueError("Printer identity/configuration changed since preparation.")
    age = time.time() - float(status.get("received_at", 0))
    if not math.isfinite(age) or age < -5 or age > 20:
        raise ValueError("Printer telemetry is stale. Obtain a new pushall before dispatch.")
    if status.get("model") != printer["model"]:
        raise ValueError("Live printer model differs from the prepared job.")
    if manifest["printer"]["model"] != printer["model"]:
        raise ValueError("Printer model configuration changed since preparation.")
    if not status.get("identity_verified"):
        raise ValueError("Live printer serial and model identity have not been verified.")
    from .models import get_model
    model = get_model(printer["model"])
    if not model["launch_supported"]:
        raise ValueError("Launch is not supported for this model: " + "; ".join(model.get("limitations", [])))
    raw = status.get("raw",status)
    if raw.get("hms") or raw.get("print_error",0):
        raise ValueError("Printer reports HMS/print_error. Diagnose and remove the physical cause first.")
    if model.get("requires_sdcard", True) and raw.get("sdcard") is not True:
        raise ValueError("This printer requires a present readable SD card for the LAN file workflow.")
    state = str(status.get("gcode_state", raw.get("gcode_state",""))).upper()
    if require_idle and state not in {"IDLE","FINISH","FAILED"}:
        raise ValueError("Printer is busy or not in a verified idle state: " + state)
    if state == "FAILED" and (raw.get("gcode_file") or raw.get("print_type") not in {None,"idle"}
                              or raw.get("nozzle_target_temper",0) or raw.get("bed_target_temper",0)):
        raise ValueError("Previous failed job has not returned to an idle, non-heating state.")
    expected = manifest["printer"]
    diameter = status.get("nozzle_diameter")
    nozzle_type = status.get("nozzle_type")
    if diameter in (None, ""): diameter=raw.get("nozzle_diameter")
    if nozzle_type in (None, ""): nozzle_type=raw.get("nozzle_type")
    if diameter in (None, "") or nozzle_type in (None, ""):
        if printer.get("nozzle_confirmed") is not True:
            raise ValueError("No live nozzle metadata. Confirm the actual installed nozzle in printer setup first.")
        if (abs(float(printer["nozzle_diameter"]) - float(expected["nozzle_diameter"])) > .001
                or printer["nozzle_type"] != expected["nozzle_type"]):
            raise ValueError("Confirmed nozzle configuration changed since preparation.")
        diameter = printer["nozzle_diameter"] if diameter in (None, "") else diameter
        nozzle_type = printer["nozzle_type"] if nozzle_type in (None, "") else nozzle_type
    if not math.isfinite(float(diameter)) or float(diameter)<=0:
        raise ValueError("Live or confirmed nozzle diameter is invalid.")
    if abs(float(diameter) - float(expected["nozzle_diameter"])) > 0.001:
        raise ValueError("Installed nozzle diameter differs from the sliced file.")
    if nozzle_type != expected["nozzle_type"]:
        raise ValueError("Installed nozzle material differs from the sliced file.")
    from .hardware import validate_nozzle, nozzle_fingerprint
    validate_nozzle(printer, require_fresh=True, live_status=status)
    if manifest.get("nozzle_fingerprint") and nozzle_fingerprint(printer) != manifest["nozzle_fingerprint"]:
        raise ValueError("Nozzle installation, profile limits or flow changed since preparation. Prepare a new job.")
    if printer.get("plate_confirmed") is not True:
        raise ValueError("Physical build plate type has not been confirmed in initial configuration.")
    if printer.get("plate_type") != manifest["printer"]["plate_type"]:
        raise ValueError("Configured plate differs from the prepared file.")
    if manifest.get("materials"):
        from .materials import validate_bindings
        validate_bindings(status, printer, manifest["materials"])
    else:
        material = selected_material(status, printer, manifest["material"]["feed"])
        if not material.get("type") or material["type"].upper() != manifest["material"]["type"].upper():
            raise ValueError("Live material metadata differs from the prepared job or is unknown.")
        for key in ("profile_id", "color", "spool_id"):
            if manifest["material"].get(key) and material.get(key) != manifest["material"][key]:
                raise ValueError("Selected spool " + key + " changed since preparation.")


class Workflow:
    def __init__(self, config: Config):
        self.config = config
        self.store = Store(config.state_dir)

    def spawn(self, jid: str, action: str):
        folder = self.config.state_dir / "jobs" / jid
        folder.mkdir(parents=True,exist_ok=True)
        with (folder / "worker.log").open("ab") as log:
            subprocess.Popen([sys.executable,"-m","bambu_workflow.worker","--config",str(self.config.path),
                              "--job",jid,"--action",action], stdout=log,stderr=log,
                             stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
                             close_fds=True)

    def submit(self, request_id: str, printer_id: str | None = None, *, source="blender", path=None,
               selection=None, print_now=False, user_prompt="", feed=None,process_profile=None,process_overrides=None,
               materials=None, support_mode="auto", cad_provenance=None, require_layer_review=False):
        printer = self.config.printer(printer_id)
        from .materials import validate_requests
        materials = validate_requests(materials)
        if materials is not None and feed is not None:
            raise ValueError("Choose materials or the legacy single feed argument, not both.")
        if support_mode not in {"auto", "native", "baked_fins", "manual"}:
            raise ValueError("support_mode must be auto, native, baked_fins or manual.")
        if type(require_layer_review) is not bool:
            raise ValueError("require_layer_review must be a boolean.")
        if cad_provenance is not None:
            from .cad import validate_provenance
            cad_provenance = validate_provenance(cad_provenance)
        if source not in {"blender","file"}:
            raise ValueError("source must be blender or file.")
        if source == "file" and not path:
            raise ValueError("A file source needs path.")
        payload = dict(source=source,path=str(Path(path).resolve()) if path else None,selection=selection,
                       print_now=print_now,user_prompt=user_prompt,feed=feed)
        if process_profile is not None:
            payload["process_profile"]=str(Path(process_profile).resolve())
        if process_overrides is not None:
            payload["process_overrides"]=process_overrides
        if materials is not None:
            payload["materials"] = materials
        if support_mode != "auto":
            payload["support_mode"] = support_mode
        if cad_provenance is not None:
            payload["cad_provenance"] = cad_provenance
        if require_layer_review:
            payload["require_layer_review"] = True
        job,created = self.store.create(request_id,printer["id"],payload)
        if created:
            self.spawn(job["id"],"prepare")
        return {"job_id":job["id"],"state":job["state"],"duplicate":not created,
                "next":"Poll job_status(job_id). No second worker or second print is created by this request_id."}

    def _error(self, jid, exc, stage, uncertain=False):
        job = self.store.get(jid)
        uncertain = uncertain or bool(job.get("start_intent"))
        error = {"stage":stage,"code":getattr(exc,"code",exc.__class__.__name__),
                 "message":self.config.redact(str(exc)),
                 "next_step":"Reconcile this job against fresh printer telemetry. Do not repeat start." if uncertain
                             else "Correct the reported condition, then retry preparation or start_prepared for this job."}
        if (uncertain and job["state"] in {"accepted","preparing","printing","paused","completed","canceled"}
                and job.get("observation",{}).get("identity_match")
                and job["observation"].get("at",0) >= job.get("start_intent",{}).get("at",float("inf"))):
            self.store.update(jid,transport_warning=error)
            return
        state = "start_uncertain" if uncertain else "failed"
        if not uncertain and job.get("manifest"):
            state = "prepared" if stage != "uploading" else "transfer_failed"
            self.store.release(jid)
        self.store.update(jid,state,error=error)

    def prepare(self, jid):
        from .blender import export_blender
        from .geometry import analyze_mesh, classify_3mf
        from .slicer import inspect_print, slice_model
        job = self.store.get(jid)
        if job["state"] != "queued" or job.get("start_intent"):
            return
        folder = self.config.state_dir / "jobs" / jid / "attempts" / str(time.time_ns())
        folder.mkdir(parents=True,exist_ok=True)
        printer = self.config.printer(job["printer_id"])
        request = job["request"]
        stage = "exporting"
        try:
            from .hardware import validate_nozzle, nozzle_fingerprint
            validate_nozzle(printer)
            self.store.update(jid,stage,progress=5,artifact_dir=str(folder))
            captured = job.get("source_snapshot")
            if captured:
                source = Path(captured["path"])
                if sha256(source) != captured["sha256"]:
                    raise ValueError("Captured source changed. Create a new request for a new source.")
            elif request["source"] == "blender":
                result = export_blender(self.config.data.get("blender",{}),folder,
                                        selection=request.get("selection"),user_prompt=request.get("user_prompt",""))
                source = Path(result["stl_path"])
                self.store.update(jid,export=result)
            else:
                origin = Path(request["path"])
                if origin.suffix.lower() not in {".stl",".3mf"} or not origin.is_file():
                    raise ValueError("Source must be an existing STL or 3MF file.")
                source = folder / ("source" + origin.suffix.lower())
                shutil.copyfile(origin,source)
            self.store.update(jid,source_snapshot={"path":str(source),"sha256":sha256(source)})
            if source.suffix.lower() == ".3mf":
                from .supports import read_support_receipt
                support_receipt = read_support_receipt(source)
                if support_receipt:
                    if support_receipt.get("generation_issues"):
                        raise ValueError("Support generation has unresolved regions or warnings. Revise the derivative in native Studio before slicing; a successful generator exit does not clear them.")
                    request = {**request, "support_mode": "baked_fins", "require_layer_review": True}
                    self.store.update(jid,support_receipt=support_receipt)
            if request.get("cad_provenance"):
                from .cad import capture_provenance
                self.store.update(jid,cad_provenance=capture_provenance(request["cad_provenance"],source,folder))
            stage = "analyzing"
            self.store.update(jid,stage,progress=15)
            materials = None
            try:
                status = self.config.adapter(printer["id"]).snapshot()
                if request.get("materials"):
                    from .materials import bind_materials
                    materials = bind_materials(status, printer, request["materials"])
                    material = materials[0]
                else:
                    material = selected_material(status, printer, request.get("feed"))
            except Exception as exc:
                if request.get("materials") or (request.get("feed") or {}).get("kind") == "ams":
                    raise ValueError("Explicit AMS/material selection could not be verified: " + self.config.redact(str(exc))) from None
                if not printer.get("material"):
                    raise ValueError("No readable material metadata and no explicit configured material.")
                material = {"type":printer["material"],"source":printer.get("material_source","user_configuration"),
                            "live_metadata_verified":False,"fallback_reason":self.config.redact(str(exc)),
                            "feed":request.get("feed") or {"kind":"external"},"remaining":None}
            if not material.get("type"):
                raise ValueError("Material is unknown. Configure the actual external spool or select an AMS tray.")
            printer["material"] = material["type"]
            if materials:
                printer["materials"] = materials
            kind = classify_3mf(source)["kind"] if source.suffix.lower() == ".3mf" else "geometry"
            if kind == "sliced" and not materials:
                raise ValueError("Imported sliced 3MF requires the complete explicit materials palette with exact preset, profile/color and physical feed bindings. Polymer family alone cannot validate an executable file.")
            analysis = ({"source_kind":"sliced","geometry_checks":"Executable plate inspection applies; no source mesh changes."}
                        if kind == "sliced" else analyze_mesh(source,printer))
            if request.get("cad_provenance") and kind != "sliced":
                from .cad import check_dimensions
                check_dimensions(analysis, request["cad_provenance"])
            self.store.update(jid,analysis=analysis,material=material)
            stage = "slicing"
            self.store.update(jid,stage,progress=30)
            slicer_config=deepcopy(self.config.data.get("slicer",{}))
            slicer_config["support_mode"] = request.get("support_mode", "auto")
            if request.get("process_profile"):
                slicer_config.setdefault("profiles",{})["process"]=request["process_profile"]
            if request.get("process_overrides") is not None:
                slicer_config["process_overrides"]=request["process_overrides"]
            sliced = slice_model(source,folder,printer,slicer_config,
                                 progress=lambda stage,detail:self.store.update(jid,progress=40,slicer_progress={"stage":stage,**detail}))
            output = Path(sliced["print_path"])
            inspected = inspect_print(output,printer,plate=int(sliced.get("plate",1)))
            digest = sha256(output)
            manifest = {"version":1,"workflow_version":__version__,"job_id":jid,"created_at":time.time(),
                        "source":{"path":str(source),"sha256":sha256(source)},
                        "printer":{k:printer.get(k) for k in ("id","model","nozzle_diameter","nozzle_type","plate_type")},
                        "device_identity_hash":hashlib.sha256((printer["host"]+"/"+printer["serial"]).encode()).hexdigest(),
                        "material":material,"slice":sliced,"inspection":inspected,
                        "print_path":str(output),"sha256":digest,"plate":int(sliced.get("plate",1)),
                        "analysis":analysis,"nozzle_fingerprint":nozzle_fingerprint(printer),
                        "support_mode":request.get("support_mode", "auto"),
                        "limitations":["No automatic self-intersection or minimum-wall guarantee.",
                        "Camera is not a reliable empty-bed sensor; physical bed readiness expires after one start."]}
            if materials:
                manifest["materials"] = materials
            if self.store.get(jid).get("cad_provenance"):
                manifest["cad_provenance"] = self.store.get(jid)["cad_provenance"]
            from .preflight import review_requirement, assessed
            manifest["layer_review"] = review_requirement(request, kind, analysis, inspected, printer)
            assessment = assessed(manifest)
            manifest["assessment"] = assessment
            manifest_path = folder/"manifest.json"
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
            self.store.update(jid,"prepared",progress=70,manifest=manifest,manifest_path=str(manifest_path),error=None,
                              assessment=assessment,review_pending=assessment["level"] == "medium")
            if not assessment["can_print"] and any(f["severity"] == "critical" and f["code"] != "layer_review_required" for f in assessment["findings"]):
                raise ValueError("Print assessment contains critical findings. Inspect print_assessment(job_id); no launch is allowed.")
            if request["print_now"] and assessment["can_print"] and not self.store.get(jid).get("review_pending"):
                self.dispatch(jid)
        except Exception as exc:
            self._error(jid,exc,stage)

    def dispatch(self, jid):
        from .slicer import inspect_print
        job = self.store.get(jid)
        if job["state"] in DISPATCHED or job.get("start_intent"):
            return self.reconcile(jid)
        if job["state"] not in {"prepared","uploaded","uploading","transfer_failed"}:
            raise ValueError("Prepare the job successfully before launch.")
        printer = self.config.printer(job["printer_id"])
        manifest = job["manifest"]
        from .preflight import assessed, validate_review
        assessment = assessed(manifest, job.get("layer_review"))
        if not assessment["can_print"]:
            raise ValueError("Critical print assessment blocks launch. Correct the condition and prepare again.")
        if job.get("review_pending"):
            return job
        stage = "readiness"
        attempted = False
        try:
            self.store.update(jid,error=None)
            validate_review(manifest, job.get("layer_review"))
            previous = self.store.leased_job(printer["id"])
            if previous and previous != jid:
                self.reconcile(previous)
            self.store.acquire(jid)
            identity_hash = hashlib.sha256((printer["host"]+"/"+printer["serial"]).encode()).hexdigest()
            if identity_hash != manifest["device_identity_hash"]:
                raise ValueError("Printer identity/configuration changed since preparation.")
            printer["material"] = manifest["material"]["type"]
            if manifest.get("materials"):
                printer["materials"] = manifest["materials"]
            path = Path(manifest["print_path"])
            if sha256(path) != manifest["sha256"]:
                raise ValueError("Prepared file changed. A new preparation is required.")
            inspect_print(path,printer,plate=manifest["plate"])
            adapter = self.config.adapter(printer["id"])
            status = adapter.snapshot()
            ensure_ready(status,printer,manifest)
            self.store.validate_bed(printer["id"],task_identity(status,printer))
            stage = "uploading"
            # An interrupted STOR may leave a partial file. A new pre-start transfer gets a new name;
            # the original partial artifact is retained, and a durable start intent is never replaced.
            filename = f"bwf_{jid}_{manifest['sha256'][:12]}_{uuid.uuid4().hex[:8]}.3mf"
            remote = adapter.remote_path(filename) if hasattr(adapter,"remote_path") else "cache/"+filename
            self.store.update(jid,stage,progress=75,remote=remote)
            upload = adapter.upload(path,remote,manifest["sha256"])
            self.store.update(jid,"uploaded",progress=80,upload=upload)
            printer = self.config.printer(job["printer_id"])
            printer["material"] = manifest["material"]["type"]
            if manifest.get("materials"):
                printer["materials"] = manifest["materials"]
            status = adapter.snapshot()
            ensure_ready(status,printer,manifest)
            if sha256(path) != manifest["sha256"]:
                raise ValueError("Prepared file changed during transfer. Start canceled.")
            name = Path(remote).name.removesuffix(".3mf")
            intent = {"at":time.time(),"remote":remote,"job_name":name,
                      "sha256":manifest["sha256"],"plate":manifest["plate"]}
            from .materials import launch_mapping
            options = {**launch_mapping(manifest.get("materials") or [manifest["material"]]),
                       "bed_type":printer["plate_type"], "bed_leveling":True,"sha256":manifest["sha256"],
                       "md5":manifest["inspection"].get("gcode_md5",manifest["inspection"].get("md5",""))}
            # The durable marker precedes network I/O. A crash here is deliberately ambiguous, never retried.
            self.store.consume_bed_and_record_start(jid,task_identity(status,printer),intent)
            attempted = True
            stage = "start_sent"
            reply = adapter.start(remote,plate=manifest["plate"],job_name=name,options=options)
            self.store.update(jid,start_transport=reply)
            self.monitor(jid)
        except Exception as exc:
            self._error(jid,exc,stage,uncertain=attempted)
        return self.store.get(jid)

    def reconcile(self, jid, status=None):
        job = self.store.get(jid)
        if job["state"] not in DISPATCHED or job["state"] in TERMINAL:
            return job
        try:
            status = status or self.config.adapter(job["printer_id"]).snapshot()
            age = time.time()-float(status.get("received_at",0))
            if not math.isfinite(age) or not -5 <= age <= 20:
                raise ValueError("Reconciliation requires fresh telemetry.")
            printer = self.config.printer(job["printer_id"])
            expected_identity = hashlib.sha256((printer["host"]+"/"+printer["serial"]).encode()).hexdigest()
            if (not status.get("identity_verified") or status.get("model") != printer["model"]
                    or expected_identity != job["manifest"]["device_identity_hash"]):
                raise ValueError("Reconciliation requires the original device with a verified live identity.")
            raw = status.get("raw",status)
            intent = job["start_intent"]
            actual_file = str(status.get("gcode_file",raw.get("gcode_file",""))).replace("\\","/")
            actual_name = status.get("subtask_name",raw.get("subtask_name"))
            state = str(status.get("gcode_state",raw.get("gcode_state",""))).upper()
            layers = int(status.get("layer_num",raw.get("layer_num",0)) or 0)
            # Task names can be retained after a print; matching is considered only after our durable start intent.
            matched = Path(actual_file).name == Path(intent["remote"]).name and actual_name == intent["job_name"]
            previous = job.get("observation", {})
            total_layers = int(raw.get("total_layer_num", 0) or 0)
            finished_after_verified_print = (state == "FINISH" and not actual_file
                and actual_name == intent["job_name"] and previous.get("identity_match") is True
                and previous.get("at", 0) >= intent["at"] and previous.get("state") == "RUNNING"
                and previous.get("layer", 0) > 0 and total_layers > 0 and layers >= total_layers
                and layers >= previous["layer"] and raw.get("mc_percent") == 100
                and not raw.get("hms") and not raw.get("print_error"))
            matched = matched or finished_after_verified_print
            if not matched:
                return self.store.update(jid,"start_uncertain",last_observation={"at":time.time(),"identity_match":False},
                                         error={"stage":"reconcile","message":"Printer has not reported this exact file and task name. Start will not be repeated.",
                                                "next_step":"Check printer history and Developer Mode. Resolve the pending attempt explicitly."})
            stage_code = status.get("stage_id",raw.get("stg_cur"))
            result = "accepted"
            if state == "FINISH": result = "completed"
            elif state in {"CANCELED","CANCELLED"}: result = "canceled"
            elif state == "FAILED": result = "canceled" if job.get("cancel_intent") and not raw.get("print_error") else "failed"
            elif state == "PAUSE": result = "paused"
            elif state == "RUNNING":
                result = "printing" if layers > 0 and str(stage_code) == "0" else "preparing"
            elif state == "PREPARE": result = "preparing"
            return self.store.update(jid,result,progress=90 if result != "completed" else 100,
                                     observation={"at":status["received_at"],"identity_match":True,"state":state,
                                                  "identity_source":"completed_after_verified_print" if finished_after_verified_print else "file_and_task",
                                                  "layer":layers,"stage_code":stage_code,"percent":raw.get("mc_percent"),
                                                  "remaining_minutes":raw.get("mc_remaining_time"),"hms":raw.get("hms",[]),
                                                  "print_error":raw.get("print_error",0)},error=None,last_connection_error=None)
        except Exception as exc:
            return self.store.update(jid,last_connection_error=self.config.redact(str(exc)))

    def monitor(self, jid, seconds=180):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            job = self.reconcile(jid)
            if job["state"] in TERMINAL or job["state"] == "printing":
                return job
            time.sleep(3)
        return self.store.get(jid)

    def control(self, printer_id, action, parameters=None):
        adapter=self.config.adapter(printer_id)
        jid=self.store.leased_job(printer_id)
        if action=="cancel" and jid:
            job=self.store.get(jid)
            status=adapter.snapshot()
            raw=status.get("raw",status)
            intent=job.get("start_intent",{})
            if (Path(str(raw.get("gcode_file",""))).name==Path(intent.get("remote","")).name
                    and raw.get("subtask_name")==intent.get("job_name")):
                self.store.update(jid,cancel_intent={"at":time.time(),"source":"explicit_user_command"})
        result=adapter.command(action,**(parameters or {}))
        if jid:
            self.reconcile(jid)
        return result

    def recover_workers(self):
        """Recover preparations after a stdio client terminates the server's process tree.

        Captured sources and durable intents outlive the worker. An existing start intent is never replayed.
        """
        recovered=[]
        for job in self.store.list(1000):
            jid=job["id"]
            if job.get("start_intent") or job.get("review_pending"):
                continue
            preparing=job["state"] in {"queued","exporting","analyzing","slicing"}
            interrupted_transfer=(job["state"] in {"uploading","uploaded","prepared"}
                                  and not job.get("error")
                                  and (job["request"].get("print_now") or job.get("dispatch_requested_at")))
            if not preparing and not interrupted_transfer:
                continue
            root=self.config.state_dir/"jobs"/jid
            root.mkdir(parents=True,exist_ok=True)
            lock=FileLock(str(root/"worker.lock"))
            try:
                with lock.acquire(timeout=0):
                    job=self.store.get(jid)
                    if job.get("start_intent") or job.get("review_pending"):
                        continue
                    preparing=job["state"] in {"queued","exporting","analyzing","slicing"}
                    interrupted_transfer=(job["state"] in {"uploading","uploaded","prepared"}
                                          and not job.get("error")
                                          and (job["request"].get("print_now") or job.get("dispatch_requested_at")))
                    if not preparing and not interrupted_transfer:
                        continue
                    self.store.update(jid,"queued" if preparing else job["state"],recovered_at=time.time())
                self.spawn(jid,"prepare" if preparing else "dispatch")
                recovered.append(jid)
            except Timeout:
                pass
        return recovered

    def run_worker(self,jid,action):
        lock = FileLock(str(self.config.state_dir / "jobs" / jid / "worker.lock"))
        try:
            with lock.acquire(timeout=0):
                if action == "prepare": self.prepare(jid)
                elif action == "dispatch": self.dispatch(jid)
                elif action == "reconcile": self.monitor(jid)
        except Timeout:
            return

