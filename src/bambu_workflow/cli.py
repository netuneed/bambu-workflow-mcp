from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import sys
import time

from .config import Config, default_config_path
from .workflow import Workflow
from . import __version__


def configure(path: Path):
    if path.exists():
        raise ValueError("Configuration already exists. Edit it in place to retain existing devices and secrets: "+str(path))
    import keyring
    pid=input("Printer id [a1mini]: ").strip() or "a1mini"
    host=input("Printer LAN IP/hostname: ").strip()
    serial=input("Serial number (Settings / Device): ").strip()
    code=getpass.getpass("LAN access code (saved only to OS keyring): ")
    nozzle=float(input("Installed nozzle diameter, mm [0.4]: ").strip() or "0.4")
    nozzle_type=input("Nozzle material [stainless_steel]: ").strip() or "stainless_steel"
    plate=input("Build plate type, e.g. textured_plate: ").strip()
    exe=input("Full path to installed Bambu Studio executable: ").strip().strip('"')
    blender=input("Blender MCP executable, e.g. uvx: ").strip() or "uvx"
    args=input('Blender MCP args as JSON ["mcp-for-blender"]: ').strip() or '["mcp-for-blender"]'
    if not host or not serial or not code or not Path(exe).is_file():
        raise ValueError("Host, serial, access code and an installed slicer are required.")
    if plate not in {"textured_plate","hot_plate"}:
        raise ValueError("This A1 mini adapter currently supports textured_plate and hot_plate only.")
    keyring.set_password("bambu-workflow",pid,code)
    data={"version":1,"state_dir":str(path.parent/"state"),"default_printer":pid,
          "printers":[{"id":pid,"model":"a1mini","host":host,"serial":serial,"secret_ref":pid,
                       "nozzle_diameter":nozzle,"nozzle_type":nozzle_type,"plate_type":plate,
                       "plate_confirmed":True,"tls_verify":False}],
          "blender":{"command":blender,"args":json.loads(args)},"slicer":{"executable":exe}}
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2),encoding="utf-8")
    return {"config":str(path),"secret_storage":type(keyring.get_keyring()).__name__,
            "next":"Enable LAN Developer Mode on the printer if necessary, then run doctor. No firmware or mode was changed."}


def install_codex(config: Config):
    import tomllib
    target=Path(os.environ.get("CODEX_HOME",Path.home()/".codex"))/"config.toml"
    existing=target.read_text(encoding="utf-8") if target.exists() else ""
    parsed=tomllib.loads(existing)
    name="bambu_workflow"
    entry={"command":sys.executable,"args":["-m","bambu_workflow.server"],
           "env":{"BAMBU_WORKFLOW_CONFIG":str(config.path)},"startup_timeout_sec":30,"tool_timeout_sec":120}
    old=parsed.get("mcp_servers",{}).get(name)
    if old:
        if all(old.get(k)==v for k,v in entry.items()):
            return {"configured":True,"already_present":True,"server":name}
        raise ValueError("A different bambu_workflow registration already exists. Inspect it before replacing it.")
    block=(f'\n[mcp_servers.{name}]\ncommand = {json.dumps(sys.executable)}\n'
           'args = ["-m", "bambu_workflow.server"]\n'
           'startup_timeout_sec = 30\ntool_timeout_sec = 120\n'
           f'env = {{ BAMBU_WORKFLOW_CONFIG = {json.dumps(str(config.path))} }}\n')
    tomllib.loads(existing+block)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        shutil.copy2(target,target.with_name(target.name+".bambu-workflow-backup-"+str(time.time_ns())))
        if target.read_text(encoding="utf-8") != existing:
            raise RuntimeError("Codex configuration changed during registration; retry safely.")
    temp=target.with_suffix(".bambu-workflow.tmp")
    temp.write_text(existing+block,encoding="utf-8")
    temp.replace(target)
    return {"configured":True,"server":name,"config":str(target),"other_servers_preserved":True,
            "next":"Start a new chat or reload MCP servers if the current chat does not expose the new tools."}


def main():
    parser=argparse.ArgumentParser(description="Bambu Workflow MCP")
    parser.add_argument("--version",action="version",version=__version__)
    parser.add_argument("--config",type=Path,default=None)
    sub=parser.add_subparsers(dest="command",required=True)
    for command in ["configure","doctor","serve","install-codex","printers","jobs"]:
        sub.add_parser(command)
    for command in ["prepare","print"]:
        p=sub.add_parser(command)
        p.add_argument("--printer")
        p.add_argument("--request-id",required=True)
        p.add_argument("--file")
        p.add_argument("--objects",nargs="+")
        p.add_argument("--collection")
        p.add_argument("--user-prompt",default="")
        p.add_argument("--process-profile")
        p.add_argument("--process-overrides",type=json.loads)
    for command in ["job","start"]:
        p=sub.add_parser(command);p.add_argument("job_id")
    p=sub.add_parser("bed-ready");p.add_argument("--printer",required=True);p.add_argument("--plate",required=True)
    args=parser.parse_args()
    try:
        if args.command=="configure":
            result=configure(args.config or default_config_path())
        else:
            config=Config(args.config)
            if args.config: os.environ["BAMBU_WORKFLOW_CONFIG"]=str(config.path)
            wf=Workflow(config)
            if args.command=="serve":
                from .server import main as serve
                serve();return
            elif args.command=="install-codex": result=install_codex(config)
            elif args.command=="doctor":
                from .transport import BambuLAN
                result={"config":str(config.path),"slicer_exists":Path(config.data.get("slicer",{}).get("executable","")).is_file(),
                        "printers":{p["id"]:config.adapter(p["id"]).probe() for p in config.data["printers"]}}
            elif args.command=="printers":
                from .server import list_printers
                result=list_printers()
            elif args.command=="jobs": result=wf.store.list()
            elif args.command in {"prepare","print"}:
                selection={"objects":args.objects} if args.objects else {"collection":args.collection} if args.collection else None
                result=wf.submit(args.request_id,args.printer,source="file" if args.file else "blender",path=args.file,
                                 selection=selection,user_prompt=args.user_prompt,print_now=args.command=="print",
                                 process_profile=args.process_profile,process_overrides=args.process_overrides)
            elif args.command=="job": result=wf.reconcile(args.job_id)
            elif args.command=="start":
                from .server import start_prepared
                result=start_prepared(args.job_id)
            elif args.command=="bed-ready":
                if input("I observed that the bed is clear and the stated plate is installed. Type CLEAR: ") != "CLEAR":
                    raise ValueError("No physical bed observation recorded.")
                from .server import mark_bed_ready
                result=mark_bed_ready(args.printer,True,args.plate)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as exc:
        try: message=Config(args.config).redact(str(exc))
        except Exception: message=str(exc)
        print(json.dumps({"error":message},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__": main()

