# Usage

The `bambu_workflow` MCP performs checks, slicing, and persistent job control. The English [bambu-print skill](../skills/bambu-print/SKILL.md) handles source selection, conversation, and review. Server-side blockers apply to every caller. Discover the installed tool schemas before use.

## Chat requests

```text
$bambu-print Prepare the current Blender scene's Print collection on workshop-p2s. Do not start it.
$bambu-print Check this Fusion export, its 80 mm control dimension, and the supports before printing.
$bambu-print Prepare this sliced two-color PETG HF 3MF using the two confirmed AMS slots.
$bambu-print Print the reviewed job JOB_ID on workshop-p2s.
```

An explicit instruction to print the specific model authorizes normal checked preparation and start. "Prepare", "check", setup, or the skill name alone does not authorize physical printing. A busy printer is not canceled or silently queued.

For Blender, name an object/collection or select visible objects in the active scene. `Print.001` in that scene takes precedence over an unrelated global `Print`. If several collections or configured printers match, choose explicitly. For CAD, export selected bodies in millimeters and retain the native source. [CAD import guidance](../skills/bambu-print/references/cad-import.md).

## Inspect a portable project

Before preparing an editable Bambu Studio 3MF, call `inspect_project(path)` with its absolute path. The CLI equivalent uses the installed runtime:

```powershell
& $runtimePython -m bambu_workflow.cli inspect-project 'C:\models\project.3mf'
```

The check reads the archive, validates its logical filament/extruder-variant mapping and inventories objects and plates. It does not initialize a print job, launch Studio, slice, contact a printer or certify native GUI opening. A configuration error blocks further preparation with its specific cause; a later Studio "no geometry" dialog does not establish that the mesh is absent. See [portable-project validation](../skills/bambu-print/references/portable-projects.md) for the distinction between semantic, CLI, GUI and sliced-layer evidence.

Keep a multi-object or multi-plate native project in Studio when preserving its authored placement. Slice it there, then set the request's optional one-based `plate` field, for example `plate=13`, on `prepare_print` or `print_model`. The CLI equivalents accept `--plate 13`. Selection belongs to that stable job request and does not edit global slicer settings. The full sliced archive is preserved; only the selected plate is inspected and selected for launch. New geometry preparation creates plate 1 and rejects a request for another plate before contacting the printer.

## Preparation request

`prepare_print` and `print_model` accept the same preparation options. The latter additionally represents a print request and must only follow an explicit human instruction. Example shape for a single PETG HF slot:

```json
{
  "request_id": "p2s-part-001",
  "printer_id": "workshop-p2s",
  "source": "file",
  "path": "C:/models/part.3mf",
  "user_prompt": "Prepare part.3mf on workshop-p2s using the confirmed red PETG HF slot. Do not start.",
  "materials": [
    {
      "filament_id": 1,
      "type": "PETG",
      "preset": "Bambu PETG HF",
      "profile_id": "GFG02",
      "color": "FF0000FF",
      "feed": {"kind": "ams", "ams_id": 0, "slot_id": 0}
    }
  ],
  "support_mode": "native",
  "require_layer_review": true,
  "cad_provenance": {
    "application": "Fusion",
    "version": "ACTUAL_INSTALLED_VERSION",
    "units": "millimeter",
    "expected_dimensions_mm": [80, 32, 18]
  }
}
```

Replace all example values with the actual request and observed facts. `profile_id`, `color`, expected dimensions, and native source path are optional evidence fields; do not invent them. Filament IDs form a contiguous 1-based palette. AMS unit/slot IDs come from `material_inventory(printer_id)`. External feed is `{"kind":"external"}`. The legacy single-material `feed={"kind":"ams","tray_id":0}` remains available. Exact filament variants use matching installed presets; no silent Generic substitution is permitted.

`source="blender"` uses `selection={"object":"Part"}`, `{"objects":["Part","Insert"]}`, or `{"collection":"Print"}`. With no selection it uses selected visible objects. File input requires an absolute path to supported STL/3MF. Native Fusion/QymCAD/STEP files are not direct print inputs.

Geometry reslicing is single material. Multiple colors, paint, or complex authored supports should be sliced in Bambu Studio and imported through the supported sliced-3MF path with every used filament mapped. Every imported sliced file requires an explicit complete `materials` plan, including a single-color file, so product and color cannot be checked by polymer family alone. Unsupported project features are rejected instead of dropped. Imported sliced/multicolor jobs require actual-layer review.

Keep one stable `request_id` per instruction and identical arguments on retries. Save the returned `job_id`. An approved geometry or settings change creates a new preparation and review; never create a new ID to bypass an uncertain physical start.

## Configuration and reviews

Use `list_models` for model operation support and `list_printers` for configured device IDs. Bind the job to one explicit ID. `material_inventory` reads current feed facts; unknown data remains unknown. A known product name alone does not confirm the spool currently loaded in a slot.

Use `confirm_nozzle(printer_id, facts, human_evidence)` after a nozzle change or when live facts are insufficient. Fields include manufacturer, model, diameter, material, flow class, and custom flag. Custom parts additionally require compatible printer/geometry, calibrated maximum volumetric speed and temperature, and renewed human evidence within 30 minutes of start. See [hardware and materials](../skills/bambu-print/references/hardware-materials.md). Nozzle facts are separate from the one-use clear-bed observation.

`support_mode` is `auto`, `native`, `baked_fins`, or `manual`. Set `require_layer_review=true` for tilt and any condition the server cannot infer. Manual/finned and custom-nozzle jobs always need it. After inspecting the actual sliced artifact, call `record_layer_review(job_id, artifact_sha256, review)` using its current hash, reviewer/evidence, actual one-based `layers` including 1, and completed checks for first layer, critical features, support contacts, unsupported islands, material assignments, and removability. Changing the artifact invalidates its review. [Exact review shape and procedure](../skills/bambu-print/references/support-review.md).

Optional `generate_support_fins` creates a derivative through a local checksum-pinned Support Fins CLI. It requires explicit geometry-change authorization and records inputs, settings, outputs, hashes, and warnings. It never starts a print. Exit 0 does not prove every overhang is supported. Keep the original and inspect real sliced contact layers.

`process_profile` is an explicit compatible process JSON path. Job-only `process_overrides` accepts validated layer height, 2-8 wall loops, 3-12 top/bottom shell layers, 0-100% sparse infill, supported infill patterns, and support enablement. It cannot replace machine G-code or arbitrary temperatures. Exact field constraints are in the installed schema and [recovery reference](RECOVERY.md).

## Assessment and physical start

| Level | Action |
| --- | --- |
| `critical` | Block until the stated cause is fixed. Silence does not clear it. |
| `medium` | Keep prepared, offer a concrete optional improvement, and allow about 60 seconds to answer. If mandatory checks pass, an already authorized print may continue with the original geometry and settings. |
| `clear` | Continue an authorized print once mandatory conditions pass. Preparation-only requests stay prepared. |

No answer authorizes no change. Scale, cutting, remeshing, holes, fits, wall changes, fins, supports, and proposed profile changes need an explicit answer to the concrete proposal. A required layer review or known unsupported island cannot expire into launch permission. There is no server-side countdown.

Before start, a human must see a clear bed and the correct plate. `mark_bed_ready` records a one-use observation valid for at most 30 minutes and bound to the device/job identity. Idle status or a camera frame is insufficient. Then use `start_prepared(job_id)` when printing was authorized. Unknown required nozzle/feed/plate facts and a busy or faulted printer remain blockers.

Follow `job_status`. `start_sent`, upload completion, and PUBACK are not proof of printing. Fresh telemetry must identify this unique file/job. `preparing` means printer preparation; a first layer requires printing/layer evidence. At `start_uncertain`, reconcile the existing job and never resend a start. [Recovery](RECOVERY.md).

## Installed client and CLI

Install and register the versioned runtime as described in [INSTALL.md](INSTALL.md). On Windows, use the actual launcher returned by the installer; the usual location is:

```powershell
$runtimePython = Join-Path $env:LOCALAPPDATA 'BambuWorkflow\app\0.5.1\.venv\Scripts\python.exe'
$client = Join-Path $env:LOCALAPPDATA 'BambuWorkflow\app\0.5.1\tools\call_mcp.py'
& $runtimePython $client --python $runtimePython
```

With no `--tool`, the helper lists the MCP catalog without printer access or recovery of old workers. Printer tools do not have that read-only guarantee. For substantial requests, save exact JSON in a file:

```powershell
& $runtimePython $client --python $runtimePython --tool prepare_print --arguments-file .\prepare-request.json --timeout 3600
```

The helper keeps stdio open and polls long-running preparation/start tools. `prepare_print` finishes waiting at `prepared`; `print_model` with medium stops at `prepared`/`review_pending=true`. An uncertain start, failed job, or critical outcome returns an error. Timeout means pending work, not permission to restart. Inspect the same job. `--no-wait` closes early and can interrupt a worker in some clients.

Basic CLI preparation and inspection:

```powershell
& $runtimePython -m bambu_workflow.cli prepare --printer workshop-p2s --request-id part-001 --file C:\models\part.stl
& $runtimePython -m bambu_workflow.cli job JOB_ID
```

Use discovered MCP schemas for advanced material, nozzle, review, and support options unless the installed CLI explicitly exposes them. Do not assume a CLI example supplies missing hardware or material facts.
