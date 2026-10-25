"""Small MCP stdio client. Default: list tool names without contacting a printer."""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


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
            result = await session.call_tool(arguments.tool, payload, read_timeout_seconds=timedelta(seconds=arguments.timeout))
            value = result.structuredContent
            if value is None:
                value = [entry.text for entry in result.content if entry.type == "text"]
            print(json.dumps({"is_error":bool(result.isError), "result":value}, ensure_ascii=False, indent=2))
            return 1 if result.isError else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Installed runtime Python; defaults to this interpreter.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--tool", help="Explicit tool name. Omit to list schemas safely.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--arguments", default="{}", help="Tool arguments as a JSON object.")
    source.add_argument("--arguments-file", type=Path, help="UTF-8 JSON object, useful for original user text.")
    parser.add_argument("--timeout", type=float, default=120)
    arguments = parser.parse_args()
    try:
        payload = json.loads(arguments.arguments_file.read_text(encoding="utf-8-sig") if arguments.arguments_file else arguments.arguments)
    except (ValueError, OSError) as error:
        parser.error("Arguments must be a readable JSON object: " + str(error))
    if not isinstance(payload, dict):
        parser.error("Tool arguments must be a JSON object.")
    if not 1 <= arguments.timeout <= 600:
        parser.error("--timeout must be from 1 to 600 seconds.")
    if arguments.tool in {"prepare_print", "print_model"} and not str(payload.get("request_id", "")).strip():
        parser.error("prepare_print/print_model require a stable request_id reused for retries of this human instruction.")
    if arguments.tool == "start_prepared" and not str(payload.get("job_id", "")).strip():
        parser.error("start_prepared requires the existing stable job_id.")
    return asyncio.run(run(arguments, payload))


if __name__ == "__main__":
    raise SystemExit(main())
