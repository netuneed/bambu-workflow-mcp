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
    from .models import get_model, list_models, normalize_model
    from .slicer import discover_slicer_executable
    print("Models: " + ", ".join(m["id"] for m in list_models()))
    model=normalize_model(input("Printer model [a1mini]: ").strip() or "a1mini")
    spec=get_model(model)
    pid=input(f"Printer id [{model}]: ").strip() or model
    host=input("Printer LAN IP/hostname: ").strip()
    serial=input("Serial number (Settings / Device): ").strip()
    code=getpass.getpass("LAN access code (saved only to OS keyring): ")
    nozzle=float(input("Installed nozzle diameter, mm [0.4]: ").strip() or "0.4")
    default_nozzle_type=spec["default_nozzle_type"]
    nozzle_type=input(f"Nozzle material [{default_nozzle_type}]: ").strip() or default_nozzle_type
    plate=input("Build plate type, e.g. textured_plate: ").strip()
    material=input("Actual loaded material, e.g. PLA or PETG: ").strip().upper()
    detected=discover_slicer_executable()
    chosen=input("Bambu Studio executable or .app" + (f" [{detected}]" if detected else "") + ": ").strip().strip('"')
    exe=discover_slicer_executable(chosen or detected)
    blender=input("Blender MCP executable, e.g. uvx: ").strip() or "uvx"
    args=input('Blender MCP args as JSON ["mcp-for-blender"]: ').strip() or '["mcp-for-blender"]'
    if not host or not serial or not code or not material or exe is None or not Path(exe).is_file():
        raise ValueError("Host, serial, access code, actual material and an installed slicer are required.")
    if plate not in {"textured_plate","hot_plate","cool_plate","engineering_plate","supertack_plate"}:
        raise ValueError("Unknown plate type. Select the actual plate supported by this printer profile.")
    if not 0 < nozzle <= 2 or nozzle_type not in {"stainless_steel","hardened_steel"}:
        raise ValueError("Enter the actual installed nozzle diameter and material.")
    blender_args=json.loads(args)
    if not isinstance(blender_args,list) or not all(isinstance(v,str) for v in blender_args):
        raise ValueError("Blender MCP args must be a JSON array of strings.")
    keyring.set_password("bambu-workflow",pid,code)
    data={"version":1,"state_dir":str(path.parent/"state"),"default_printer":pid,
          "printers":[{"id":pid,"model":model,"host":host,"serial":serial,"secret_ref":pid,
                       "nozzle_diameter":nozzle,"nozzle_type":nozzle_type,"plate_type":plate,
                       "nozzle_confirmed":True,"plate_confirmed":True,"material":material,
                       "material_source":"user_configuration","tls_verify":False}],
          "blender":{"command":blender,"args":blender_args},"slicer":{"executable":str(exe)}}
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2),encoding="utf-8")
    return {"config":str(path),"secret_storage":type(keyring.get_keyring()).__name__,
            "model":model,"launch_supported":spec["launch_supported"],"limitations":spec.get("limitations",[]),
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
    for command in ["configure","doctor","serve","install-codex","printers","jobs","models"]:
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
        if args.command=="models":
            from .models import list_models
            result={"models":list_models()}
        elif args.command=="configure":
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
                from .slicer import discover_slicer_executable
                result={"config":str(config.path),"slicer_exists":discover_slicer_executable(config.data.get("slicer",{}).get("executable")) is not None,
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

