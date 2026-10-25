"""One upload-only hardware check. Never starts, pauses or otherwise controls a print."""
from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path
import time
import uuid

from bambu_workflow.config import Config


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",type=Path,required=True)
    parser.add_argument("--printer")
    parser.add_argument("--report",type=Path)
    args=parser.parse_args()
    config = Config()
    report_path = args.report or config.state_dir / "acceptance" / "upload-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    source=args.file.resolve()
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    # Re-running the acceptance script reports the existing attempt rather than uploading again.
    if report_path.exists():
        previous=json.loads(report_path.read_text(encoding="utf-8"))
        if previous.get("sha256") != digest:
            raise ValueError("This report belongs to a different file. Choose a new --report path.")
        print(report_path.read_text(encoding="utf-8"))
        return 0 if previous.get("verified") else 1
    from bambu_workflow.slicer import inspect_print
    inspect_print(source,config.printer(args.printer))
    attempt_id = uuid.uuid4().hex
    adapter = config.adapter(args.printer)
    filename = f"bwf_transfer_acceptance_{attempt_id}.3mf"
    remote = adapter.remote_path(filename)
    report = {
        "version": 1, "attempt_id": attempt_id, "started_at": time.time(),
        "operation": "upload_only", "start_command_sent": False,
        "telemetry": "Not requested by this upload-only test; no readiness or print-start inference",
        "source": str(source), "sha256": digest, "remote": remote, "state": "attempt_recorded",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        # BambuLAN.upload performs STOR plus complete RETR/SHA256 verification only.
        # It contains no start() or command() call, so the existing print is unaffected by a print command.
        result = adapter.upload(source, remote, digest)
        report.update(state="transferred", completed_at=time.time(), result=result,
                      verified=result.get("sha256") == digest and result.get("verified_by") == "full_retrieve_sha256")
        exit_code = 0 if report["verified"] else 1
    except Exception as error:
        report.update(state="failed", completed_at=time.time(), error_code=getattr(error, "code", type(error).__name__),
                      error=config.redact(str(error)), verified=False, retry_performed=False)
        exit_code = 1
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), **report}, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
