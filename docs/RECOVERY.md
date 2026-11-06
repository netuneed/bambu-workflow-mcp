# Jobs and recovery

The MCP returns `job_id` when it creates a persistent job. SQLite state lives under the configured user `state_dir`; artifacts are in `state_dir/jobs/<job_id>/attempts/<attempt_id>`, with the current directory exposed as `artifact_dir`. Preserve the database, leases, source snapshots, and `start_intent`. Deleting them is not a recovery method.

Some stdio clients terminate their worker process tree when the connection closes. A later MCP process can recover interrupted preparation from its source snapshot. Saved launch authorization does not remove readiness checks. An already recorded start intent is never replayed. Medium review remains pending across restarts; there is no unattended countdown.

## Calls and identity

Core operations:

```python
prepare_print(request_id, printer_id=None, source="blender", path=None,
              selection=None, user_prompt="", feed=None,
              process_profile=None, process_overrides=None,
              materials=None, support_mode="auto", cad_provenance=None,
              require_layer_review=False)
print_model(...)  # same preparation options, only after explicit print instruction
job_status(job_id, reconcile=True)
list_jobs(limit=20)
mark_bed_ready(printer_id, bed_is_clear, plate_type)
start_prepared(job_id)
retry_preparation(job_id)
```

Discover the installed schemas for current defaults and constraints. Explicitly select `printer_id`. `source="file"` requires an absolute supported STL/3MF path; Blender selection accepts a named object, object list, or active-scene collection. The actual `user_prompt` is part of request evidence.

One instruction keeps one stable `request_id` and identical arguments on retry. A changed file selection, material/profile, pose, or approved geometry requires a new preparation. This must never be used to replay an unresolved physical start. A default-printer change cannot redirect the existing job.

`materials` records a contiguous 1-based filament palette with exact preset/type, optional profile/color, and explicit external or AMS unit/slot feeds. Legacy single-material `feed` remains supported. `cad_provenance` records application/version/units and optional source path/control dimensions. `support_mode` and `require_layer_review` control additional review requirements. See [USAGE.md](USAGE.md) for request examples.

`process_profile` is a compatible process JSON path. Job-only `process_overrides` is persisted and participates in retry identity:

| Key | Allowed scope |
| --- | --- |
| `layer_height` | Finite value within the selected nozzle's machine-profile limits |
| `wall_loops` | Integer 2-8 |
| `top_shell_layers`, `bottom_shell_layers` | Integer 3-12 |
| `sparse_infill_density` | String from `"0%"` to `"100%"` |
| `sparse_infill_pattern` | `gyroid`, `grid`, `cubic`, `rectilinear` |
| `enable_support` | Boolean or `"0"`/`"1"`, subject to support-mode constraints |

Other keys are rejected; overrides cannot replace machine G-code or arbitrary temperatures. CLI supports `--process-profile` and `--process-overrides`; use MCP for advanced options absent from the installed CLI.

## State meanings

| State | Meaning and next action |
| --- | --- |
| `queued`, `exporting`, `analyzing`, `slicing` | Read `job_status`. A dead preparation worker can use `retry_preparation` before any start intent. A live worker is protected by a lock. |
| `prepared` | Artifact exists. Inspect assessment, review requirements, and readiness. Preparation-only jobs need an explicit print instruction. Medium stays pending until the client handles it. |
| `uploading`, `transfer_failed`, `uploaded` | Before start intent, retry the same job only after inspecting transfer state and integrity. Do not overwrite an unrelated remote file. |
| `start_sent` | Start intention was saved before network publication. This is not proof of acceptance or printing. |
| `start_uncertain` | Reconcile only. Do not resend start or create a replacement request ID. |
| `accepted`, `preparing`, `printing`, `paused` | Fresh evidence must match this job's unique file/name. `preparing` is distinct from actual layer printing. Paused stays paused. |
| `completed`, `canceled`, `failed` | Final result. Any manual resolution is retained separately. |

When current communication fails, the last proven state can remain in the response with an observation timestamp and `last_connection_error`. An old `printing` value does not prove the printer is observable now.

At launch, artifacts/configuration are checked again. A changed nozzle revision, selected material/profile/color/feed, printer identity, artifact hash, or missing required layer review blocks launch. Custom-nozzle evidence must be renewed within its validity period. Renewal with unchanged facts is not a physical qualification. Layer review records must describe an actual review of the exact artifact, never an inferred approval.

The bed observation is one-use and valid for at most 30 minutes. Idle, cooldown, or a camera frame cannot replace human observation. A retained `FAILED` firmware state can count as idle only under the runtime's strict no-active-file/no-error/no-target-heat conditions. This does not clear faults or remove physical readiness.

## Unknown launch result

First call `job_status(job_id, reconcile=True)`. Reconciliation checks observation freshness, device/model identity, unique filename/task, and printing-stage evidence. A recorded intention survives a crash between saving it and publishing the command; the runtime deliberately does not guess whether the start happened.

On A1 mini firmware 01.08.01.00, one physical start took effect without a received PUBACK. The job temporarily became `start_uncertain`; fresh exact-file telemetry subsequently proved preparation and layer 1. No second start was sent. A late transport error cannot demote already established printing evidence; it remains a warning.

After a human independently verifies device history and physical state, the MCP offers:

```python
resolve_uncertain_job(job_id, outcome, human_evidence)
```

`outcome` is `not_started`, `completed`, or `canceled`. Evidence must be the human's actual observation, not invented from idle, a camera, or elapsed time. The device must match and have no active print. This call sends no hardware command.

`not_started` closes the old job as canceled with a separate manual-resolution record, releases its hold, and preserves the old start intent. That old job cannot start again. A newly authorized attempt requires a new job after uncertainty is resolved and a fresh bed observation. `resolve_uncertain_job` and `retry_preparation` use MCP, without separate CLI subcommands.

## Individual controls and diagnosis

`printer_control(printer_id, action, parameters=None)` supports pause/resume/cancel/temperature/fan/speed within the runtime's checks. Resume requires `parameters={"cause_resolved":true}` after the physical cause is actually resolved. Do not use pause/cancel to free a printer without its owner's instruction. Never clear HMS automatically.

Read tools include `diagnose_printer`, `printer_status`, `printer_files`, `camera_snapshot`, and `material_inventory`. They use separate channels. If access fails, compare independent read-only probes before calling the resource unavailable. During a print, do not switch network modes or reboot. Identify monitoring connection owners before changing their sessions.
