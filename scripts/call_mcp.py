"""Small MCP stdio client. Default: list tool names without contacting a printer."""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PREPARE_TOOLS = {"prepare_print", "retry_preparation"}
WORKFLOW_TOOLS = PREPARE_TOOLS | {"print_model", "start_prepared"}
LIVE_STATES = {"accepted", "preparing", "printing", "paused"}


def tool_value(result):
    """FastMCP normally returns structured content; also accept JSON text clients."""
    if result.structuredContent is not None:
        return result.structuredContent
    values = []
    for entry in result.content:
        if entry.type == "text":
            try:
                values.append(json.loads(entry.text))
            except (TypeError, ValueError):
                values.append(entry.text)
    return values[0] if len(values) == 1 else values


def stable_result(tool: str, job: dict) -> tuple[bool, bool]:
    """Return (finished waiting, error). Submission acknowledgments are not jobs."""
    state = job.get("state")
    assessment = job.get("assessment") or (job.get("manifest") or {}).get("assessment") or {}
    if (job.get("error") or assessment.get("level") == "critical" or assessment.get("can_print") is False
            or state in {"failed", "transfer_failed", "start_uncertain", "canceled"}):
        return True, True
    if state == "completed":
        return True, False
    if state == "prepared":
        if job.get("review_pending"):
            return True, False
        # A clear print may briefly be prepared immediately before its worker dispatches.
        if (tool in PREPARE_TOOLS and not (job.get("request") or {}).get("print_now")
                and not job.get("dispatch_requested_at") and not job.get("start_intent")):
            return True, False
    if state in LIVE_STATES and not job.get("last_connection_error"):
        observation = job.get("observation") or {}
        try:
            observed = float(observation.get("at", 0))
            intent = float((job.get("start_intent") or {}).get("at", float("inf")))
            age = time.time() - observed
            if (observation.get("identity_match") is True and observed >= intent
                    and math.isfinite(age) and -5 <= age <= 20):
                return True, bool(observation.get("hms") or observation.get("print_error"))
        except (TypeError, ValueError, OverflowError):
            pass
    return False, False


def emit(value: dict) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 1 if value["is_error"] else 0


async def wait_for_job(session, arguments, job_id: str, deadline: float) -> int:
    last_job = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return emit({"is_error": True, "pending": True, "timed_out": True, "job_id": job_id,
                         "state": (last_job or {}).get("state"), "result": last_job,
                         "next_step": "Inspect job_status for this job. Do not repeat start or create a new request_id. "
                                      "Closing this client can interrupt its worker; no completion is claimed."})
        try:
            response = await session.call_tool("job_status", {"job_id": job_id, "reconcile": True},
                                               read_timeout_seconds=timedelta(seconds=remaining))
        except Exception as error:
            return emit({"is_error": True, "pending": True, "job_id": job_id,
                         "state": (last_job or {}).get("state"), "result": last_job, "error": str(error),
                         "next_step": "Inspect job_status for this job. Do not repeat start or create a new request_id."})
        job = tool_value(response)
        if response.isError or not isinstance(job, dict):
            return emit({"is_error": True, "pending": True, "job_id": job_id, "result": job,
                         "next_step": "Inspect job_status for this job. Do not repeat start."})
        last_job = job
        done, failed = stable_result(arguments.tool, job)
        if done:
            return emit({"is_error": failed, "job_id": job_id, "state": job.get("state"),
                         "review_pending": bool(job.get("review_pending")), "waited": True, "result": job})
        await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))


async def run(arguments: argparse.Namespace, payload: dict) -> int:
    environment = os.environ.copy()
    # A short diagnostic client must not restart pre-existing workers as a side effect.
    # Explicit calls such as print_model still enqueue their requested new work.
    environment["BAMBU_WORKFLOW_SKIP_RECOVERY"] = "1"
    if arguments.config:
        environment["BAMBU_WORKFLOW_CONFIG"] = str(arguments.config.resolve())
    parameters = StdioServerParameters(command=arguments.python, args=["-m", "bambu_workflow.server"], env=environment)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if not arguments.tool:
                result = await session.list_tools()
                print(json.dumps({"tools":[tool.name for tool in result.tools], "printer_contacted":False}, ensure_ascii=False, indent=2))
                return 0
            deadline = time.monotonic() + arguments.timeout
            try:
                result = await session.call_tool(arguments.tool, payload, read_timeout_seconds=timedelta(seconds=arguments.timeout))
            except Exception as error:
                return emit({"is_error": True, "pending": arguments.tool in WORKFLOW_TOOLS,
                             "job_id": payload.get("job_id"), "request_id": payload.get("request_id"), "error": str(error),
                             "next_step": "Inspect the existing job. A lost response does not prove the call failed. "
                                          "Keep the same request_id/job_id and do not repeat start."})
            value = tool_value(result)
            if result.isError or arguments.tool not in WORKFLOW_TOOLS:
                return emit({"is_error": bool(result.isError), "result": value})
            job_id = (value.get("job_id") or value.get("id")) if isinstance(value, dict) else None
            job_id = job_id or payload.get("job_id")
            if not job_id:
                return emit({"is_error": True, "result": value, "error": "Workflow response has no job_id."})
            if arguments.no_wait:
                return emit({"is_error": False, "job_id": job_id, "waited": False, "result": value,
                             "warning": "Submission only. Closing stdio can interrupt the worker; --no-wait is not recommended. "
                                        "No preparation or launch completion is confirmed. Reuse this request_id/job_id."})
            return await wait_for_job(session, arguments, job_id, deadline)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Installed runtime Python; defaults to this interpreter.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--tool", help="Explicit tool name. Omit to list schemas safely.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--arguments", default="{}", help="Tool arguments as a JSON object.")
    source.add_argument("--arguments-file", type=Path, help="UTF-8 JSON object, useful for original user text.")
    parser.add_argument("--timeout", type=float, default=1800, help="Total workflow wait budget in seconds (default: 1800).")
    parser.add_argument("--no-wait", action="store_true", help="Return only the submission acknowledgment; can interrupt workers (not recommended).")
    arguments = parser.parse_args()
    try:
        payload = json.loads(arguments.arguments_file.read_text(encoding="utf-8-sig") if arguments.arguments_file else arguments.arguments)
    except (ValueError, OSError) as error:
        parser.error("Arguments must be a readable JSON object: " + str(error))
    if not isinstance(payload, dict):
        parser.error("Tool arguments must be a JSON object.")
    if not 1 <= arguments.timeout <= 86400:
        parser.error("--timeout must be from 1 to 86400 seconds.")
    if arguments.tool in {"prepare_print", "print_model"} and not str(payload.get("request_id", "")).strip():
        parser.error("prepare_print/print_model require a stable request_id reused for retries of this human instruction.")
    if arguments.tool == "start_prepared" and not str(payload.get("job_id", "")).strip():
        parser.error("start_prepared requires the existing stable job_id.")
    return asyncio.run(run(arguments, payload))


if __name__ == "__main__":
    raise SystemExit(main())
