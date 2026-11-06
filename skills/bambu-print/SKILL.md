---
name: bambu-print
description: Set up local Bambu Lab printing and prepare, inspect, or start explicitly authorized jobs through bambu_workflow MCP, from Blender or exported CAD STL/3MF files. Use for installation, printer selection, material and nozzle checks, AMS mapping, and support review tied to a print job. Ordinary CAD modeling belongs to the CAD workflow.
---

# Bambu Print

For first installation, lab onboarding, or a missing `bambu_workflow` connection, read [first run](references/first-run.md). The complete skill includes a pinned installer; copying the skill alone does not install its MCP runtime. Setup does not authorize a physical print.

Use the connected `bambu_workflow` MCP. Discover its current tools and schemas first. If it fails, isolate the connection, runtime, file, or device failure with an independent read-only check. Do not bypass a blocked workflow with direct MQTT, an older MCP, or a different transport.

Read only the reference needed for the job:

- Fusion or QymCAD exports, assemblies, units, and provenance: [CAD import](references/cad-import.md).
- Multiple printers, P2S, AMS, filament variants, stock or custom nozzles: [Hardware and materials](references/hardware-materials.md).
- Manual supports, tilted or diagonal printing, and Support Fins: [Support review](references/support-review.md).

## Authorization and source

Only an explicit human instruction to print the specific model authorizes a physical start. "Prepare", "check", setup work, and invoking this skill do not. A print instruction covers normal checked preparation and launch without repeated general approval.

Use `list_printers` and `list_models`, then name `printer_id` explicitly on the job. When multiple devices could match the request, ask one concrete selection question and continue independent preparation. Do not silently select the first discovered printer, redirect a prepared job, or cancel a busy printer. Preparation support, launch support, and hardware validation are separate facts. When automatic launch is unsupported, retain the checked artifact for owner-controlled Bambu Studio launch.

For live Blender, use selected visible objects or explicitly named objects/collections in the active scene. For a request naming `Print`, find `Print`/`Print.NNN` collections in that scene and use the single matching candidate's actual name. A global `Print` can belong to another project. Never export the whole scene by default. If the intended selection is ambiguous, ask one specific question.

Preserve CAD/native files, unsaved Blender state, control dimensions, and requested orientation. Do not scale, cut, remesh, alter holes, fits, walls, or functional geometry without an explicit answer to a concrete proposal. Make approved changes in a derivative and create a new job. Adding modeled fins or manual supports also changes geometry and requires explicit authorization. Silence about a medium finding never authorizes a change.

## Preparation and review

Use `prepare_print` for a new, complex, imported, tilted, finned, manual-support, or multicolor model. Pass the actual `user_prompt`, source selection, one stable `request_id`, and explicit printer and material facts. Keep the returned `job_id`; retries use identical arguments and ID. Never change IDs to bypass uncertain launch protection.

Confirm the actual installed nozzle, plate, filament product and feed. A past job, material family, RFID type, or matching diameter alone does not establish the current configuration. Read [hardware and materials](references/hardware-materials.md) when any of those facts is unknown or changed.

Wait for the prepared artifact, then inspect real sliced toolpaths. Check first layer, critical functional features, representative middle/top layers, new islands, bridges, support contact transitions, removability, and the complete extrusion envelope including supports, brim, and purge tower. `bbox_all` can describe only the first layer. A text report is not a layer review.

Manual/finned/tilted, custom-nozzle, and imported sliced or multicolor jobs require artifact-bound review. Set `require_layer_review=true` for tilt or other cases the server cannot infer. After actually inspecting the file, use `record_layer_review` with the current artifact SHA256, actual one-based layer numbers including 1, and truthful evidence/checks. A changed artifact invalidates that evidence. Do not record a review from a generic preview, another job, or an analyzer summary. See [support review](references/support-review.md).

Choose matching installed machine, process, and exact filament presets, resolving inheritance. Preserve the requested product variant, such as `Bambu PETG HF`; do not substitute Generic PETG silently. A nozzle's flow class and a filament's HF product name are different settings. Unknown required facts and unavailable exact profiles remain blockers. `print_model` is suitable only for an already reviewed model or a simple variation for which automatic checks are sufficient and no mandatory layer review remains.

## Assessment decision

Read `print_assessment(job_id)`, `job_status`, and analyzer limitations. The assessment contains `level`, `can_print`, and `findings`. Mandatory equipment, identity, material, nozzle, artifact, and review checks apply at every level. Mesh analysis does not certify strength, adhesion, or future success.

- `critical`: block launch, state the concrete cause and next step. Silence cannot clear a blocker. Do not disable a check or bypass the tool's refusal.
- `medium`: continue only if mandatory checks pass and `can_print=true`. Briefly propose a concrete optional improvement and give the user about 60 seconds to answer while performing independent checks. Without an answer, preserve the original geometry and settings and continue a previously authorized print through `start_prepared`. Do not apply suggested brim, supports, profile, or geometry changes. A preparation-only request stays prepared. Incorporate a late answer received before launch.
- `clear`: continue an already authorized print without another general approval once mandatory checks pass. A preparation-only request stays prepared.

Concrete unsupported islands, lost functional features, unresolved collisions, or unverified support contacts block launch even if the automatic assessment says `clear`. Required layer review is not an optional medium suggestion. The server has no background countdown that turns silence into a start.

## Physical start and reporting

Before every start, require a fresh one-use `mark_bed_ready` observation by a human who saw a clear bed and the correct plate. It lasts at most 30 minutes and is bound to the device and current job identity. Idle telemetry or a camera image does not replace that observation. Custom-nozzle facts also require current human evidence as described in the hardware reference.

Use `start_prepared` for the reviewed job when printing was authorized. A busy printer stays busy; retain preparation without promising a later automatic start. Do not clear HMS or resume after a physical fault automatically.

An upload, PUBACK, or `start_sent` is not proof of printing. Follow `job_status` for fresh telemetry matching the unique file and job. Distinguish acceptance, printer preparation, first layer, and completion. At `start_uncertain`, reconcile only; never resend or create a replacement ID. Preserve durable job state across restarts.

Report briefly: prepared or actually observed state, job ID, selected printer, exact material/feed, important blocker or limitation, and slicer estimates. Do not claim P2S, AMS, custom-nozzle, or finned hardware validation unless the current evidence establishes it.
