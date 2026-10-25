"""Exercise real MCP stdio and preparation, including a disconnect during the worker.

This script cannot launch or control a printer. It invokes only prepare_print and job_status.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def unpack(result):
    if result.isError:
        raise RuntimeError("MCP tool failed: "+str(result.content))
    return result.structuredContent or json.loads(next(c.text for c in result.content if c.type=="text"))


async def run(args):
    from bambu_workflow.config import Config
    original=Config(args.config)
    isolated=Path(tempfile.mkdtemp(prefix="prepare-smoke-",dir=original.state_dir))
    data={**original.data,"state_dir":str(isolated/"state")}
    config_path=isolated/"config.json"
    config_path.write_text(json.dumps(data),encoding="utf-8")
    environment={**os.environ,"BAMBU_WORKFLOW_CONFIG":str(config_path)}
    # Recovery is exercised only within this isolated prepare-only history. Starting
    # the diagnostic server cannot recover someone else's authorized print.
    environment.pop("BAMBU_WORKFLOW_SKIP_RECOVERY",None)
    parameters=StdioServerParameters(command=sys.executable,args=["-m","bambu_workflow.server"],env=environment)
    arguments={"request_id":args.request_id,"source":"file","path":str(args.file.resolve())}
    if args.printer:
        arguments["printer_id"]=args.printer
    async with stdio_client(parameters) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            tools=await session.list_tools()
            first=unpack(await session.call_tool("prepare_print",arguments))
    # The first MCP process is gone. A second session must find the same persistent job.
    async with stdio_client(parameters) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            repeated=unpack(await session.call_tool("prepare_print",arguments))
            if repeated["job_id"] != first["job_id"] or not repeated["duplicate"]:
                raise AssertionError("MCP restart created a duplicate preparation")
            deadline=time.monotonic()+args.timeout
            while time.monotonic()<deadline:
                job=unpack(await session.call_tool("job_status",{"job_id":first["job_id"],"reconcile":False}))
                if job["state"] in {"prepared","failed"}:
                    break
                await asyncio.sleep(2)
            else:
                raise TimeoutError("Preparation worker did not finish before the smoke timeout")
            if job.get("start_intent"):
                raise AssertionError("A prepare-only job contains a start intent")
            report={"tool_count":len(tools.tools),"job_id":job["id"],"state":job["state"],
                    "same_job_after_mcp_restart":True,"start_intent":None,"error":job.get("error"),
                    "manifest_path":job.get("manifest_path"),"sha256":job.get("manifest",{}).get("sha256"),
                    "isolated_state":str(isolated)}
            print(json.dumps(report,ensure_ascii=False,indent=2))
            if job["state"]!="prepared":
                raise SystemExit(1)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file",type=Path,required=True)
    parser.add_argument("--config",type=Path)
    parser.add_argument("--request-id",required=True)
    parser.add_argument("--printer")
    parser.add_argument("--timeout",type=float,default=120)
    asyncio.run(run(parser.parse_args()))
