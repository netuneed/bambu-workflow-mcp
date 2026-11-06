from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

from .config import Config, default_config_path, update_config
from .hardware import confirm_nozzle, stock_nozzle_facts, validate_nozzle
from .workflow import Workflow
from . import __version__


def _nozzle_setup(model: str, diameter: float, material: str) -> dict:
    if material.strip().lower() != "custom":
        actual_material, separator, flow = material.partition("/")
        facts = stock_nozzle_facts(model, diameter, material=actual_material.strip(),
                                   flow_class=flow.strip() if separator else "Standard")
    else:
        facts = {"manufacturer": input("Installed nozzle manufacturer: ").strip(),
                 "model": input("Installed nozzle product/model: ").strip(),
                 "diameter": diameter, "custom": True, "compatible_printer_model": model,
                 "material": input("Physical nozzle material (stainless_steel or hardened_steel): ").strip(),
                 "flow_class": input("Nozzle flow class [Standard]: ").strip() or "Standard",
                 "stock_geometry_compatible": input("Verified stock-compatible mount and installed length? Type YES: ") == "YES",
                 "max_volumetric_speed": float(input("Calibrated maximum volumetric speed, mm3/s: ")),
                 "max_temperature_c": float(input("Nozzle manufacturer's maximum safe temperature, C: ")),
                 "installation_id": uuid.uuid4().hex}
        facts["human_evidence"] = input("Describe your physical check of the installed nozzle and calibration: ").strip()
        if not facts["human_evidence"]:
            raise ValueError("An actual physical check and calibrated limits are required for a custom nozzle.")
    facts.setdefault("human_evidence", "User confirmed the installed factory nozzle during local configuration.")
    facts.update(confirmed_at=time.time(), revision=1)
    validate_nozzle({"model": model, "nozzle": facts}, require_fresh=True)
    return facts


def configure(path: Path):
    if path.exists():
        raise ValueError("Configuration already exists. Run add-printer to retain existing devices and secrets: "+str(path))
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
    default_nozzle_type=stock_nozzle_facts(model,nozzle)["material"]
    nozzle_type=input(f"Factory nozzle material [{default_nozzle_type}] (append /HighFlow if installed), or type custom: ").strip() or default_nozzle_type
    nozzle_facts=_nozzle_setup(model,nozzle,nozzle_type)
    nozzle_type=nozzle_facts["material"]
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
    data={"version":1,"state_dir":str(path.parent/"state"),"default_printer":pid,
          "printers":[{"id":pid,"model":model,"host":host,"serial":serial,"secret_ref":pid,
                       "nozzle_diameter":nozzle,"nozzle_type":nozzle_type,"plate_type":plate,
                       "nozzle":nozzle_facts,"nozzle_volume_type":nozzle_facts["flow_class"],
                       "nozzle_confirmed":True,"plate_confirmed":True,"material":material,
                       "material_source":"user_configuration","tls_verify":False}],
          "blender":{"command":blender,"args":blender_args},"slicer":{"executable":str(exe)}}
    update_config(path,lambda current:current.update(data),create=True,
                  before_write=lambda:keyring.set_password("bambu-workflow",pid,code))
    return {"config":str(path),"secret_storage":type(keyring.get_keyring()).__name__,
            "model":model,"launch_supported":spec["launch_supported"],"limitations":spec.get("limitations",[]),
            "next":"Enable LAN Developer Mode on the printer if necessary, then run doctor. No firmware or mode was changed."}


def add_printer(config: Config):
    """Append a distinct local device without contacting any physical printer."""
    import keyring
    from .models import get_model, list_models, normalize_model
    print("Models: " + ", ".join(m["id"] for m in list_models()))
    model=normalize_model(input("Printer model [p2s]: ").strip() or "p2s")
    spec=get_model(model)
    pid=input("Unique printer id (e.g. workshop-p2s): ").strip()
    if not pid or any(p["id"]==pid for p in config.data["printers"]):
        raise ValueError("Choose a new unique printer id. Existing printer entries are never replaced.")
    name=input("Display name (optional): ").strip()
    location=input("Location (optional): ").strip()
    host=input("Printer LAN IP/hostname: ").strip()
    serial=input("Serial number (Settings / Device): ").strip()
    if not host or not serial:
        raise ValueError("Host and serial are required.")
    if any(str(p["serial"]).casefold()==serial.casefold() for p in config.data["printers"]):
        raise ValueError("This printer serial is already configured. Select its existing printer_id.")
    code=getpass.getpass("LAN access code (saved only to OS keyring): ")
    if not code:
        raise ValueError("A LAN access code is required.")
    nozzle=float(input("Installed nozzle diameter, mm [0.4]: ").strip() or "0.4")
    default_material=stock_nozzle_facts(model,nozzle)["material"]
    material_answer=input(f"Factory nozzle material [{default_material}] (append /HighFlow if installed), or type custom: ").strip() or default_material
    facts=_nozzle_setup(model,nozzle,material_answer)
    plate=input("Actual build plate type, e.g. textured_plate: ").strip()
    if plate not in {"textured_plate","hot_plate","cool_plate","engineering_plate","supertack_plate"}:
        raise ValueError("Unknown plate type. Select the actual plate supported by this printer profile.")
    material=input("Actual loaded material, e.g. PLA, ABS or PETG HF: ").strip().upper()
    if not material:
        raise ValueError("The actual loaded filament material is required.")
    # A fresh unguessable keyring name cannot overwrite another device's secret,
    # including configurations whose secret_ref differs from their printer id.
    secret_ref="printer-"+uuid.uuid4().hex
    candidate={"id":pid,"name":name,"location":location,"model":model,"host":host,"serial":serial,
               "secret_ref":secret_ref,"nozzle":facts,"nozzle_diameter":facts["diameter"],
               "nozzle_type":facts["material"],"nozzle_volume_type":facts["flow_class"],
               "nozzle_confirmed":True,"plate_type":plate,"plate_confirmed":True,
               "material":material,"material_source":"user_configuration","tls_verify":False}
    config.add_printer(candidate,before_write=lambda:keyring.set_password("bambu-workflow",secret_ref,code))
    return {"config":str(config.path),"printer_id":pid,"name":name,"location":location,
            "model":model,"launch_supported":spec["launch_supported"],"printer_contacted":False,
            "explicit_selection_required":True,
            "next":"Run doctor to verify LAN identity and connectivity. Select this exact printer_id for preparation and printing."}


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
    for command in ["configure","add-printer","doctor","serve","install-codex","printers","jobs","models","cad-formats"]:
        sub.add_parser(command)
    p=sub.add_parser("materials");p.add_argument("--printer",required=True)
    p=sub.add_parser("confirm-nozzle")
    p.add_argument("--printer",required=True)
    p.add_argument("--facts",type=json.loads,required=True,help="Actual installed nozzle facts as a JSON object")
    p.add_argument("--human-evidence",required=True,help="Person's physical observation of the installed nozzle")
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
        p.add_argument("--feed",type=json.loads)
        p.add_argument("--materials",type=json.loads)
        p.add_argument("--support-mode",choices=["auto","native","baked_fins","manual"],default="auto")
        p.add_argument("--cad-provenance",type=json.loads)
        p.add_argument("--require-layer-review",action="store_true")
    for command in ["job","start"]:
        p=sub.add_parser(command);p.add_argument("job_id")
    p=sub.add_parser("bed-ready");p.add_argument("--printer",required=True);p.add_argument("--plate",required=True)
    args=parser.parse_args()
    try:
        if args.command=="models":
            from .models import list_models
            result={"models":list_models()}
        elif args.command=="cad-formats":
            from .cad import import_guidance
            result=import_guidance()
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
            elif args.command=="add-printer": result=add_printer(config)
            elif args.command=="confirm-nozzle": result=confirm_nozzle(config,args.printer,args.facts,args.human_evidence)
            elif args.command=="materials":
                from .server import material_inventory
                result=material_inventory(args.printer)
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
                                 process_profile=args.process_profile,process_overrides=args.process_overrides,
                                 feed=args.feed,materials=args.materials,support_mode=args.support_mode,
                                 cad_provenance=args.cad_provenance,require_layer_review=args.require_layer_review)
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

