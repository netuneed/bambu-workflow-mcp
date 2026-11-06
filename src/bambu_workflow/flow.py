"""Conservative executable extrusion-flow bounds for calibrated custom hotends."""
from __future__ import annotations

from copy import deepcopy
import math
import re


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_WORD = re.compile(r"([A-Za-z])\s*(" + _NUMBER + r")?")


def _parameters(text: str, line: int, *, flags: bool = False) -> dict:
    result = {}
    cursor = 0
    while cursor < len(text):
        if text[cursor].isspace():
            cursor += 1
            continue
        match = _WORD.match(text, cursor)
        if not match:
            raise ValueError(f"Unprovable extrusion-flow parameter at line {line}.")
        key, token = match.group(1).upper(), match.group(2)
        if key in result or (token is None and not flags):
            raise ValueError(f"Missing, nonfinite or duplicate {key} parameter at line {line}.")
        value = float(token) if token is not None else None
        if value is not None and not math.isfinite(value):
            raise ValueError(f"Nonfinite extrusion-flow parameter at line {line}.")
        result[key] = value
        cursor = match.end()
    return result


def _arc_length(start: dict, end: dict, params: dict, clockwise: bool, absolute_center: bool, dz: float) -> float:
    """A lower path-length bound, so the resulting flow is an upper bound."""
    if any(start.get(axis) is None or end.get(axis) is None for axis in "XY"):
        raise ValueError("Extrusion arc has no known XY start/end position.")
    if set(params) - set("XYZEFIJKR") or "K" in params:
        raise ValueError("Unsupported extrusion arc parameters or multi-turn arc.")
    sx, sy, ex, ey = start["X"], start["Y"], end["X"], end["Y"]
    tau = 2 * math.pi
    def sweep(cx, cy):
        a, b = math.atan2(sy-cy, sx-cx), math.atan2(ey-cy, ex-cx)
        result = (a-b) % tau if clockwise else (b-a) % tau
        return tau if math.hypot(ex-sx, ey-sy) <= 1e-10 else result
    if "I" in params or "J" in params:
        if "R" in params:
            raise ValueError("Extrusion arc cannot mix I/J and R.")
        cx = params.get("I", sx) if absolute_center else sx + params.get("I", 0.)
        cy = params.get("J", sy) if absolute_center else sy + params.get("J", 0.)
    elif "R" in params:
        chord = math.hypot(ex-sx, ey-sy)
        radius = abs(params["R"])
        if chord <= 1e-10 or radius < chord / 2:
            raise ValueError("Invalid or ambiguous radius-form extrusion arc.")
        height = math.sqrt(max(0., radius*radius-chord*chord/4))
        candidates = [((sx+ex)/2+sign*(-(ey-sy)/chord)*height,
                       (sy+ey)/2+sign*((ex-sx)/chord)*height) for sign in (-1, 1)]
        minor = params["R"] >= 0
        selected = [center for center in candidates if (sweep(*center) <= math.pi+1e-10 if minor else sweep(*center) >= math.pi-1e-10)]
        if not selected:
            raise ValueError("Cannot prove extrusion arc direction.")
        cx, cy = selected[0]
    else:
        raise ValueError("Extrusion arc requires I/J or R.")
    radius_a, radius_b = math.hypot(sx-cx, sy-cy), math.hypot(ex-cx, ey-cy)
    if radius_a <= 1e-10 or abs(radius_a-radius_b) > max(1e-7, min(radius_a,radius_b)*1e-7):
        raise ValueError("Extrusion arc endpoints disagree with the center; its flow cannot be bounded.")
    # The smaller radius avoids understating flow due to rounded endpoint radii.
    distance = math.hypot(min(radius_a,radius_b) * sweep(cx,cy), dz)
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("Extrusion arc has no provable positive path length.")
    return distance


def _merge(before: dict, after: dict) -> dict:
    merged = deepcopy(after)
    for key in ("xyz_absolute", "e_absolute", "e", "plane", "center_absolute"):
        if before[key] != after[key]:
            merged[key] = None
    for axis in "XYZ":
        if before["position"][axis] != after["position"][axis]:
            merged["position"][axis] = None
    merged["feed"] = max(before["feed"],after["feed"]) if before["feed"] is not None and after["feed"] is not None else None
    for key in ("speed_factor", "flow_factor"):
        merged[key] = max(before[key],after[key])
    return merged


def validate_extrusion_flow(gcode_text: str, max_flow_mm3_s: float, filament_diameter_mm: float = 1.75) -> dict:
    """Reject excessive/unprovable G0-G3 extrusion, including startup and purge.

    Retraction recovery counts as fresh positive extrusion. Programmed feed gives
    a conservative bound because acceleration can only slow the move. M220/M221
    boosts increase the bound; reductions never justify a previously unsafe move.
    Firmware extrusion macros without a validated rate model fail closed.
    """
    for value, label in ((max_flow_mm3_s,"Calibrated maximum flow"),(filament_diameter_mm,"Filament diameter")):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(label + " must be a finite positive number.")
    if not isinstance(gcode_text,str):
        raise ValueError("Executable G-code text is required.")
    if filament_diameter_mm > 10:
        raise ValueError("Filament diameter exceeds the flow validator's physical validation bound.")
    area = math.pi*(filament_diameter_mm/2)**2
    state = {"position":dict.fromkeys("XYZ"),"xyz_absolute":None,"e_absolute":None,"e":None,"feed":None,
             "plane":"G17","center_absolute":False,"speed_factor":1.,"flow_factor":1.}
    branches = []
    report = {"cap_mm3_s":float(max_flow_mm3_s),"filament_diameter_mm":float(filament_diameter_mm),
              "maximum_flow_mm3_s":0.,"maximum_flow_line":None,"checked_extrusion_moves":0,
              "pure_extrusion_moves":0,"arc_extrusion_moves":0,"retraction_moves":0,
              "vendor_extrusion_rate_commands":0,
              "vendor_rate_units_basis":"Dimensional inference from official machine templates: M620 F=MVS/2.4*60; M983/M984 F=MVS/2.4. Firmware units are not physically verified.",
              "conditional_blocks_checked":0,"method":"programmed feed upper bound, all conditional arms, no retraction credit"}
    for line_number, original in enumerate(gcode_text.splitlines(),1):
        code = original.split(";",1)[0].strip()
        if not code:
            continue
        code = re.sub(r"\([^()]*\)","",code).strip()
        if not code:
            continue
        code = re.sub(r"\*\d+\s*$","",code)
        code = re.sub(r"^N\d+\s*","",code,flags=re.I)
        command_match = re.match(r"^([GMT])\s*(\d+(?:\.\d+)?)(.*)$",code,re.I)
        if not command_match:
            if re.match(r"^[XYZEF]",code,re.I):
                raise ValueError(f"Implicit modal motion cannot be bounded (line {line_number}).")
            raise ValueError(f"Unknown executable line prevents extrusion-flow validation (line {line_number}).")
        prefix, number, tail = command_match.groups()
        major,separator,minor = number.partition(".")
        command = prefix.upper()+str(int(major))+("."+minor if separator else "")
        if command == "M622":
            if len(branches) >= 32:
                raise ValueError("Conditional G-code nesting exceeds the flow validator's bound.")
            branches.append(deepcopy(state));report["conditional_blocks_checked"]+=1
            continue
        if command == "M623":
            if not branches:
                raise ValueError(f"Unmatched conditional end at line {line_number}.")
            state = _merge(branches.pop(),state)
            continue
        if command == "G20":
            raise ValueError(f"Inch-mode extrusion cannot be validated for a custom nozzle (line {line_number}).")
        if command == "M200":
            raise ValueError(f"Volumetric E mode M200 is not supported by the custom-nozzle flow validator (line {line_number}).")
        if command in {"G10","G11","G150","M600","M701","M702","G93","G95","G50","G51","G52","M92","M206"}:
            raise ValueError(f"Firmware extrusion macro {command} has no validated flow bound (line {line_number}).")
        if command in {"M620.1","M620.10","M620.11","M983","M983.3","M9833","M984"}:
            # Official machine templates derive M620 rates as MVS / 2.4 * 60
            # and M983/984 rates as MVS / 2.4. Thus filament mm/min versus
            # mm/s is a dimensional inference from the resolved template.
            values = _parameters(tail,line_number,flags=True)
            if command == "M620.10" and set(values) == {"R"} and values["R"] is not None and values["R"] >= 0:
                continue
            if command=="M620.11" and (values.get("S")==0 or values.get("E",0) is not None and values.get("E",0)<=0):
                continue
            if "F" not in values or values["F"] is None or values["F"]<0:
                raise ValueError(f"Vendor extrusion command {command} has no bounded feedrate (line {line_number}).")
            divisor = 1. if command in {"M983","M983.3","M9833","M984"} else 60.
            flow=values["F"]*area/divisor*state["speed_factor"]*state["flow_factor"]
            if not math.isfinite(flow):
                raise ValueError(f"Unbounded vendor extrusion rate at line {line_number}.")
            report["vendor_extrusion_rate_commands"]+=1
            if flow>report["maximum_flow_mm3_s"]:
                report["maximum_flow_mm3_s"]=flow;report["maximum_flow_line"]=line_number
            if flow>max_flow_mm3_s+max(1e-8,max_flow_mm3_s*1e-8):
                raise ValueError(f"Executable vendor extrusion flow {flow:.6g} mm3/s exceeds custom nozzle cap {max_flow_mm3_s:g} mm3/s (line {line_number}).")
            continue
        if command in {"G90","G91"}:
            state["xyz_absolute"] = command == "G90";continue
        if command in {"M82","M83"}:
            state["e_absolute"] = command == "M82";continue
        if command in {"G90.1","G91.1"}:
            state["center_absolute"] = command == "G90.1";continue
        if command in {"G17","G18","G19"}:
            state["plane"] = command;continue
        if command in {"M220","M221"}:
            values = _parameters(tail,line_number)
            if "S" not in values or values["S"] <= 0:
                raise ValueError(f"Unknown feed/flow multiplier at line {line_number}.")
            state["speed_factor" if command=="M220" else "flow_factor"] = max(1.,values["S"]/100)
            continue
        if command == "G92":
            values = _parameters(tail,line_number)
            if set(values)-set("XYZE"):
                raise ValueError(f"Unsupported coordinate reset at line {line_number}.")
            for axis in "XYZ":
                if axis in values:state["position"][axis]=values[axis]
            if "E" in values:state["e"]=values["E"]
            continue
        if command == "G28":
            values = _parameters(tail,line_number,flags=True)
            for axis in "XYZ":
                if not values or axis in values:state["position"][axis]=None
            continue
        if prefix.upper()=="T":
            state["e"]=None;continue
        if command not in {"G0","G1","G2","G3"}:
            # Vendor commands are not ordinary movement. Known extruder macros
            # above fail closed; separate slicer checks own M620 purge parameters.
            if prefix.upper()=="G" and re.search(r"(?:^|\s)E",tail,re.I):
                raise ValueError(f"Unknown extruding G-code {command} at line {line_number}.")
            if prefix.upper()=="M" and command not in {"M17","M1006","M201","M203","M204","M205"} and re.search(r"(?:^|\s)E\s*[+\-.0-9]",tail,re.I):
                raise ValueError(f"Unknown firmware extrusion parameter in {command} at line {line_number}.")
            continue
        values = _parameters(tail,line_number)
        allowed = set("XYZEF") if command in {"G0","G1"} else set("XYZEFIJKR")
        if set(values)-allowed:
            raise ValueError(f"Unsupported motion parameters at line {line_number}.")
        if "F" in values:
            if values["F"] <= 0:raise ValueError(f"Nonpositive extrusion feedrate at line {line_number}.")
            state["feed"]=values["F"]
        start = dict(state["position"])
        end = dict(start)
        delta = {}
        for axis in "XYZ":
            if axis not in values:
                delta[axis]=0.;continue
            if state["xyz_absolute"] is None:
                end[axis]=None;delta[axis]=None
            elif state["xyz_absolute"]:
                end[axis]=values[axis]
                delta[axis]=None if start[axis] is None else end[axis]-start[axis]
            else:
                delta[axis]=values[axis]
                end[axis]=None if start[axis] is None else start[axis]+values[axis]
            if (end[axis] is not None and not math.isfinite(end[axis])) or (delta[axis] is not None and not math.isfinite(delta[axis])):
                raise ValueError(f"Unbounded motion coordinate at line {line_number}.")
        positive = 0.
        if "E" in values:
            if state["e_absolute"] is None:
                raise ValueError(f"Extrusion mode M82/M83 is unknown at line {line_number}.")
            if state["e_absolute"]:
                if state["e"] is None:
                    raise ValueError(f"Absolute extrusion has no known E baseline; use G92 E (line {line_number}).")
                positive=values["E"]-state["e"];state["e"]=values["E"]
            else:
                positive=values["E"]
                if state["e"] is not None:state["e"]+=positive
        if positive < 0:
            report["retraction_moves"]+=1
        if positive > 0:
            if state["feed"] is None:
                raise ValueError(f"Positive extrusion has no known feedrate at line {line_number}.")
            if any(value is None for value in delta.values()):
                raise ValueError(f"Extruding move has an unknown starting position at line {line_number}.")
            if command in {"G2","G3"}:
                if state["plane"]!="G17" or state["center_absolute"] is None:
                    raise ValueError(f"Only a known G17 XY extrusion arc is supported (line {line_number}).")
                distance=_arc_length(start,end,values,command=="G2",state["center_absolute"],delta["Z"])
                report["arc_extrusion_moves"]+=1
            else:
                distance=math.hypot(*delta.values())
            if not math.isfinite(distance):
                raise ValueError(f"Unbounded extrusion path length at line {line_number}.")
            if distance == 0:
                # An E-only move's F is filament mm/min, not XYZ mm/min.
                duration=positive/state["feed"]*60
                report["pure_extrusion_moves"]+=1
            else:
                duration=distance/state["feed"]*60
            if not math.isfinite(duration) or duration<=0:
                raise ValueError(f"Unbounded extrusion duration at line {line_number}.")
            flow=positive*area/duration*state["speed_factor"]*state["flow_factor"]
            if not math.isfinite(flow):raise ValueError(f"Unbounded executable extrusion at line {line_number}.")
            report["checked_extrusion_moves"]+=1
            if flow>report["maximum_flow_mm3_s"]:
                report["maximum_flow_mm3_s"]=flow;report["maximum_flow_line"]=line_number
            if flow>max_flow_mm3_s+max(1e-8,max_flow_mm3_s*1e-8):
                raise ValueError(f"Executable extrusion flow {flow:.6g} mm3/s exceeds custom nozzle cap {max_flow_mm3_s:g} mm3/s (line {line_number}).")
        state["position"]=end
    if branches:
        raise ValueError("Unclosed conditional G-code prevents extrusion-flow validation.")
    return report
