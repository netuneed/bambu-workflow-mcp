# First installation and laboratory setup

Use this route when setting up the skill/MCP or when no working `bambu_workflow` connection exists. Release 0.5.0 is pinned by the bundled bootstrap script. It installs a separate runtime from the published Windows/macOS release, verifies the release archive and its complete checksum manifest, and preserves printer configuration and jobs. It needs Python 3.12+, official Bambu Studio with the intended printer profiles, and network access to GitHub/Python package downloads. Git and Blender are unnecessary for STL/3MF input.

## Install the runtime

Locate this installed skill's `scripts/bootstrap.py`. Use an actual Python 3.12+ interpreter to run it by absolute path:

```text
python /absolute/path/to/bambu-print/scripts/bootstrap.py
```

On macOS the command is usually `python3`; verify its version. The script uses the selected interpreter. It reports `python`, `runtime_dir`, and `onboard_command` in its JSON result. A normal run does not contact printers, register MCP, or start/recover print jobs.

Do not use `--install-skill` when this complete skill is already installed. That option is for installing the skill from another distribution route; keep one discoverable copy. Use `--skill-root` only with that explicit skill installation. Use `--config` to retain a known existing configuration, `--install-root` for a chosen runtime location, and `--resume` only for an interrupted installation of these exact release bytes. Never replace a rejected same-version wheel with different bytes.

If release access fails, independently check the pinned release page and direct download before concluding it is unavailable. Do not choose an unreviewed latest version, disable TLS verification, run an unchecked installer, or build a different version as an unnoticed fallback. A previously downloaded bundle may be passed with `--bundle` and its independently verified `--sha256`.

## Configure on the owner's computer

Give the owner the exact `onboard_command` returned by bootstrap, formatted for their terminal. They run it in their own interactive terminal because its hidden LAN-code prompt requires local input. Do not put a LAN code in chat, shell arguments, a report, or repository files. When operating in an actual owner-controlled interactive terminal, bootstrap's `--onboard` option combines installation with this wizard. Do not run `--onboard` through a noninteractive Codex execution tool.

The installed command is:

```text
RUNTIME_PYTHON -m bambu_workflow.cli onboard
```

Replace `RUNTIME_PYTHON` with the reported launcher, retaining its virtual-environment path. If a custom configuration was selected, retain the global `--config` option before `onboard`. The wizard configures only when configuration is absent, preserves existing settings, and registers `bambu_workflow` without printing or contacting hardware. Resolve a conflicting existing registration deliberately; do not delete other servers or silently switch a configured device. To add a different printer to existing configuration, use `add-printer` and a unique device ID first.

The owner supplies the intended model/ID, LAN address and serial, code, physically installed nozzle, actual plate, loaded polymer and exact installed filament preset. `PETG` is a polymer family; `Bambu PETG HF` is a specific preset. Do not guess these from another lab or a prior job. Select file-based input unless the owner actually uses a working Blender integration. Do not change firmware, printer LAN modes, or a running job automatically.

Open a new Codex chat or reload MCP, then discover its current tools. Read [hardware and materials](hardware-materials.md) for multiple printers, AMS or non-stock nozzles.

## Check and prepare the first job

Use the installed CLI's `lab-check --printer ACTUAL_ID --output lab-readiness.json` for an offline, sanitized report. Add `--online` only for explicitly requested read-only telemetry. It does not upload, heat, move, mark the bed ready, or start a job. A successful command means the report was generated: inspect its `status`, findings and `ready_for_preparation`, because it can still report `blocked`. `printing_authorized` remains false. Treat missing checks and unknown identity or hardware facts as unknown; report readiness is not permission to print. Share only that report, never the private configuration or raw diagnostic logs.

Locate `examples/acceptance-coupon-12x12x1.2mm.stl` beneath the installed runtime. Prepare it on the explicit printer with observed nozzle/plate/material/feed facts and one stable request ID. Verify its 12 x 12 x 1.2 mm dimensions and actual sliced layers, setting `require_layer_review=true` for this first laboratory review. Preserve the job ID and artifact hash. Open the returned `prepared.gcode.3mf` in Bambu Studio's sliced preview or use an executable-layer viewer; if the owner reviews it, obtain their actual layer numbers and observations. Record only the review that occurred, as described in [support review](support-review.md#mandatory-actual-layer-evidence). Preparation does not authorize physical start.

Once the owner explicitly requests printing that job, require their fresh clear-bed/correct-plate observation, record required reviews, and use the normal checked start. A busy or faulted printer remains blocked. Follow the same job through fresh matching telemetry; `start_sent` is not proof of printing. At `start_uncertain`, reconcile without another start or replacement ID.

Published software tests do not establish P2S/AMS, aftermarket-nozzle or finned-print hardware acceptance. For a first laboratory test, prefer an already qualified stock nozzle and one known filament/feed. Complex materials, multiple colors and custom hardware require their own verified workflow. The owner must inspect the finished part before recording physical acceptance.
