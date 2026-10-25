# Bambu Workflow MCP

Local Blender-to-printer workflows for Bambu Lab A1 mini. Version 0.2.0.

Export Blender objects, check millimetres and mesh topology, slice with installed Bambu Studio, inspect the executable plate, upload with a full checksum verification and confirm the exact job through fresh printer telemetry. STL and supported 3MF projects are also accepted.

Jobs, source snapshots, launch intentions and printer locks survive MCP restarts. Retrying the same request cannot silently create a second print. Access codes stay in the operating system keyring.

## Install on Windows

Requirements: Python 3.12+, Git, installed Bambu Studio and an A1 mini on your local network. Live export additionally needs a running Blender MCP connection. Printer firmware and LAN permissions must permit local control; see [setup](docs/SETUP.md).

```powershell
git clone https://github.com/didyp3073-oss/bambu-workflow-mcp.git
cd bambu-workflow-mcp
python -m pip install build
python -m build --wheel
.\scripts\install.ps1 -WheelPath '.\dist\bambu_workflow_mcp-0.2.0-py3-none-any.whl' -Python python -InstallSkill
```

The installer creates a separate versioned environment under `%LOCALAPPDATA%\BambuWorkflow\app`. Bambu Studio, Blender and a Python interpreter are not bundled. [Installation, updates and release ZIPs](docs/INSTALL.md).

Run `bambu-workflow configure` in the installed environment, then `bambu-workflow doctor` and `bambu-workflow install-codex`. Configuration and persistent jobs live outside the checkout. Select a non-default configuration with `BAMBU_WORKFLOW_CONFIG` or `--config`.

## Use from Codex

Install the included [bambu-print skill](skills/bambu-print/SKILL.md), then ask:

```text
$bambu-print Print the model in the current Blender scene's Print collection.
```

For preparation only, ask to prepare or export. That does not authorize printing. An explicit print instruction covers normal checked preparation and launch.

The skill resolves the collection in the active scene, checks real slicer layers and preserves the original model. It never selects an old collection from another scene merely because it has the same base name.

## Three decision levels

| Level | Behaviour |
| --- | --- |
| Critical | Block launch. Invalid scale or mesh, oversized model, incompatible profile, unclassified slicer diagnostic, printer fault and missing physical bed readiness are blockers. Silence cannot override them. |
| Medium | Keep the job prepared, describe the concern and offer a concrete optional change. If the owner does not answer after a reasonable opportunity and mandatory checks pass, continue with the original printable geometry. |
| Clear | Continue the authorized print without another general approval. Informational notices do not force a review. |

An unanswered suggestion never authorizes scaling, cutting, remeshing or changing functional dimensions. Required repairs remain blocked. The skill handles the conversation; the server enforces mandatory checks and retains medium review across restarts. There is no unattended countdown that launches after a disconnected client.

## Use from code

The server speaks standard MCP over stdio:

```powershell
python -m bambu_workflow.server
```

Tools include `prepare_print`, `print_model`, `print_assessment`, `start_prepared`, `job_status` and `mark_bed_ready`. Supply one stable `request_id` per human instruction and reuse identical arguments on retries. A sent command is not proof of printing. An uncertain launch is reconciled, never blindly resent.

[Usage and Python examples](docs/USAGE.md) cover MCP clients and CLI calls. [scripts/call_mcp.py](scripts/call_mcp.py) lists tools by default; printing needs explicit arguments. Printer addresses are configurable.

Before every physical start, a human must observe a clear bed and the correct plate. A readiness token lasts at most 30 minutes and is consumed once. Camera images and idle status cannot prove an empty bed. A busy printer is never canceled to make room for another job.

## Validation and limits

The complete export, slice, upload and start path was exercised on A1 mini firmware 01.08.01.00 and Bambu Studio 02.08.02.61. One print reached its reported final layer and FINISH; a second independently reviewed model was launched. Finished-part dimensions, strength and surface quality have not been measured. [Validation](docs/VALIDATION.md).

Launch is enabled only for A1 mini. One material per plate is supported. Complex per-object 3MF modifiers are rejected. Self-intersections, minimum wall thickness, strength and physical collisions are not fully simulated. Multiple source shells become one slicer object; individual-object skipping is unavailable. AMS and individual pause, cancel, heat and fan commands have software tests but have not been hardware validated.

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

[Recovery](docs/RECOVERY.md) · [Capabilities](docs/CAPABILITIES.md) · [Protocol sources](docs/PROTOCOL.md) · [Third-party notices](docs/THIRD_PARTY.md)

Own code: [MIT](LICENSE). No access codes, printer addresses, private models, runtime state or proprietary network plugin are distributed. This project is not affiliated with Bambu Lab.
