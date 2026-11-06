# Setup

Install Python 3.12+ and official Bambu Studio separately. Live Blender export additionally requires the user's running Blender MCP with `execute_blender_code`. File-based CAD export does not require Blender. The Windows/macOS runtime, configuration, jobs, and secrets are independent of the checkout; see [INSTALL.md](INSTALL.md).

## Configure the runtime

Use the launcher returned by the installer:

```powershell
& $runtimePython -m bambu_workflow.cli configure
& $runtimePython -m bambu_workflow.cli doctor
& $runtimePython -m bambu_workflow.cli install-codex
```

`configure` refuses to overwrite an existing configuration. `doctor` checks installed tools and read access to printer channels; it does not test printing or prove successful slicing/export. `install-codex` adds the MCP entry with a configuration backup. For an existing file, pass the global option before the command: `bambu-workflow --config C:\path\config.json doctor`.

When Blender MCP is missing, the previously exercised package offers `uvx mcp-for-blender install-addon`. Enable its addon and start its local server, then set `blender.command="uvx"`, `blender.args=["mcp-for-blender"]`. Check the actual installed addon/tool names. Do not replace an already working connection just for this setup. Export from the live active scene, including unsaved edits, in Object Mode. A closed `.blend` file is not the live scene.

## Device identity and LAN permission

Independently match each configured model and serial to the physical printer. Give each device a unique ID, even when two share the same model. Jobs name `printer_id` explicitly. `list_printers` enumerates configuration, not every printer on the LAN. Verify the model's required storage and local-control permission.

Only A1 mini firmware 01.08.01.00 has this project's physical launch evidence. That is not a requirement to update another printer to that firmware. A1 mini firmware 01.05.00.00 introduced Authorization Control. Firmware/model-specific permission can reject commands while telemetry still works. Follow the model's current official instructions; setup never updates firmware or toggles modes automatically.

On compatible firmware the owner enables LAN Only Mode, then Developer Mode on the printer screen and accepts its notice. LAN Only disconnects Bambu Cloud/Handy. Menu labels and availability vary. See the official [Developer Mode instructions](https://wiki.bambulab.com/en/knowledge-sharing/enable-developer-mode), [A1 mini history](https://wiki.bambulab.com/en/a1-mini/manual/a1-mini-firmware-release-history), and [Authorization Control explanation](https://blog.bambulab.com/firmware-update-introducing-new-authorization-control-system-2/). A status reply does not prove launch permission. Never change access mode or reboot for diagnosis during an active print.

## Configuration and secrets

Default configuration:

- Windows: `%LOCALAPPDATA%\BambuWorkflow\config.json`.
- macOS: `~/Library/Application Support/BambuWorkflow/config.json`.
- Existing macOS `~/.config/BambuWorkflow/config.json` remains in use when the preferred file does not yet exist.

`BAMBU_WORKFLOW_CONFIG` or `--config` selects another file. Preserve the existing path during upgrades. Each printer has separate `id`, `model`, `host`, and `serial`; [config.example.json](../config.example.json) contains placeholders only.

Access codes use OS keyring service `bambu-workflow` and the printer's `secret_ref`, or the named `access_code_env` passed to the MCP process. No plaintext fallback is used. Do not put access codes in Git, logs, command arguments, or example JSON. If keyring fails, isolate its backend/permission failure rather than saving the secret in a file.

The setup wizard uses `tls_verify=false` for the printer's self-signed certificate. This does not authenticate it against a public certificate authority. Configure `tls_ca_file` or `tls_fingerprint` from independently verified certificate evidence if required. Setup does not modify the system certificate store.

## Profiles, hardware, and feed

Set `slicer.executable` and optionally `slicer.profile_root` to the actual installed Bambu Studio paths. macOS commonly uses `/Applications/BambuStudio.app/Contents/MacOS/BambuStudio` and `Contents/Resources/profiles/BBL`; some builds use `Contents/resources`. Use absolute paths. Optional explicit presets are `slicer.profiles.machine`, `.process`, and `.filament`. The resolver includes inherited settings and G-code includes, then saves the result with the job. Renaming another printer's profile does not make it compatible.

Confirm actual nozzle identity, material, diameter, and Standard/HighFlow class. Custom nozzles need `confirm_nozzle`, calibrated limits, stock geometry compatibility, and renewed evidence before start. Do not relabel an unknown nozzle to pass validation. Exact installed profiles remain necessary. [Nozzle facts and P2S limits](../skills/bambu-print/references/hardware-materials.md).

Use `material_inventory` for unit/slot feed facts and select exact product presets, such as `Bambu PETG HF` or the intended ABS variant. Type, preset/profile, color, and feed are separate. A historical PETG setting does not confirm the loaded spool. An empty AMS does not prove external feed is empty. Missing external-spool identity or remaining mass stays unknown until measured/confirmed. P2S supports the documented AMS 2 Pro/AMS HT topology, never AMS lite; verify installed compatibility and current topology rather than guessing tray numbers.

Plate names include `textured_plate`, `hot_plate`, `cool_plate`, `engineering_plate`, and `supertack_plate`, subject to the exact installed model/profile. Matching names do not establish compatibility across models. The selected pre-sliced plate uses 1-based `slicer.plate`; new geometry slicing creates plate 1. Unsupported project modifiers or material paint are rejected rather than flattened silently.

For a Blender scene with units `NONE`, set `blender.mm_per_unit` only from verified source scale. Value 1 means one scene unit is really one millimeter. For Fusion/QymCAD, use neutral exports with known units and control dimensions, then record [CAD provenance](../skills/bambu-print/references/cad-import.md).

## First acceptance job

Prepare a small representative model without starting. Review dimensions, selected device/nozzle/plate, exact material/feed, resolved profiles, and actual layers. Record required artifact-bound review. Development/setup authorization alone is not a physical print instruction.

Once the owner explicitly requests that test print, they check a clear bed and correct plate. `mark_bed_ready` records one use for at most 30 minutes. Use `start_prepared`, then observe fresh telemetry identifying that exact job, preparation, and first layer. Hardware acceptance of a nozzle/material/support combination requires inspecting the finished result; dispatch alone is insufficient.

## Diagnosis

| Symptom | Independent check and next step |
| --- | --- |
| Status works; commands fail | Check model/firmware LAN permission separately. MQTT subscription does not prove command authorization. |
| FTPS connects; upload stalls | Check storage, free space, data-channel TLS and partial-file identity. Verify full retrieval checksum before deciding to retry. |
| New MQTT TLS sessions fail | Probe TCP/TLS separately. Identify existing connection owners before changing monitoring. No universal client limit has been established. |
| Slicing fails | Read the saved log, actual CLI help/version, resolved profiles and full build envelope. |
| Start reply is lost | Reconcile `job_status` for the same job. Never resend or change request ID. |
| Camera fails | Compare the stream in Bambu Studio and the separate camera transport/dependency. MQTT success does not prove camera support. |
| MCP restarts | Recover the existing job and durable state. Do not delete the database, lease, or start intent. |

Physical fault causes require human resolution. HMS is not cleared automatically and a print is not resumed automatically.
