# Laboratory quickstart

Send this page to the lab. It covers Bambu Workflow MCP 0.5.1 on Windows and macOS, including its English `bambu-print` skill. The first P2S physical print remains a laboratory acceptance test: this project has software/profile checks for P2S, but no P2S, AMS, aftermarket-nozzle or finned-print hardware acceptance yet.

## 1. Install with Codex

Use Codex on the computer that will communicate with the printer. It needs Python 3.12+, official Bambu Studio with P2S profiles, internet for package installation, and local-network access to the intended printer. Git and Blender are unnecessary for STL/3MF files. Use the lab's permitted LAN-control configuration; setup does not alter printer modes, firmware or active jobs.

For this local-control route, an authorized lab operator enables LAN Only and Developer Mode on the idle P2S, if the installed firmware requires them. The P2S manual places Developer Mode under Settings > LAN Only. LAN Only disables cloud/Handy access; agree this change with the lab before making it. Setup does not toggle either mode. See the official [P2S manual, sections 7.3.1 and 7.3.2](https://csm.bblcdn.com/hub/9425242c00344b0e801b6179f9a243aa.pdf) and [device setup](SETUP.md#device-identity-and-lan-permission).

Paste this into Codex:

```text
Install the complete bambu-print skill from https://github.com/netuneed/bambu-workflow-mcp/tree/v0.5.1/skills/bambu-print using the skill installer. Read its first-run reference and install the matching MCP runtime with its bundled bootstrap script. This is a local P2S setup using exported STL/3MF files. Preserve existing Codex servers and printer configuration. Give me the exact local terminal command for the onboarding wizard so I can enter the LAN access code privately. Do not contact the printer or start a print during installation.
```

The bundled bootstrap downloads the pinned release, checks its archive and complete inner manifest, and installs a separate runtime. The skill and MCP are both needed: the skill supplies instructions and the MCP supplies printer tools. See [official skill guidance](https://learn.chatgpt.com/docs/build-skills) and [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

For manual installation, download the correct ZIP from [release v0.5.1](https://github.com/netuneed/bambu-workflow-mcp/releases/tag/v0.5.1), verify its published SHA256, and follow [installation](INSTALL.md). Python and Bambu Studio are installed separately. Dependencies download during installation.

## 2. Enter the laboratory's printer facts locally

Run the exact onboarding command Codex gives you in your own terminal. It uses the installed Python launcher with `-m bambu_workflow.cli onboard`. A custom configuration path goes before `onboard` as `--config`.

For a new installation, choose P2S and a unique ID such as `lab-p2s`. Enter its actual LAN address, serial number and access code. The code prompt is hidden and stores the value in the operating system keyring. Keep the code out of Codex chat and reports. Confirm the physically installed nozzle, plate, loaded polymer and exact Studio filament preset. For example, PETG and `Bambu PETG HF` are separate family/preset facts; use that preset only for the corresponding product actually loaded.

File-based use does not need Blender. Existing configuration is retained; add another printer with the installed launcher's `-m bambu_workflow.cli add-printer` command. Choose the intended `printer_id` explicitly when several devices are configured. A registration conflict is reported so an existing MCP is not silently replaced.

Open a new Codex chat or reload MCP after registration. Confirm that `bambu_workflow` tools are available.

## 3. Check readiness and prepare the coupon

Ask Codex:

```text
$bambu-print Check lab-p2s for the first test. Run a sanitized lab readiness report with read-only online telemetry and save lab-readiness.json. Then prepare the installed examples/acceptance-coupon-12x12x1.2mm.stl on that exact printer. Confirm my installed nozzle, plate, exact filament preset and actual external/AMS feed. Check its 12 x 12 x 1.2 mm dimensions and require review of the actual sliced layers. Do not start printing. Keep the job ID for the next step.
```

Replace `lab-p2s` with your configured ID. Codex should use `lab-check --printer lab-p2s --online --output lab-readiness.json`, preparation and inspection, without uploading or starting a print. Without `--online`, `lab-check` is offline. Use a new output filename when repeating a report; existing files are preserved. A successful command only means the report was generated; its `status` can still be `blocked`. Inspect its findings and `ready_for_preparation`. `printing_authorized` remains false.

The report contains constrained diagnostic facts rather than addresses, serials, access codes, model filenames or personal file paths. It cannot establish bed readiness, material dryness, adhesion, nozzle calibration, command permission or a successful future print.

Open the returned `prepared.gcode.3mf` in Bambu Studio's sliced preview. Inspect its first, middle and final layers and transitions, without changing or reslicing the reviewed artifact. Tell Codex the actual layer numbers and observations so it can record the review for that file's hash. A generic mesh image is insufficient. Resolve any blocker before continuing.

For the first test, use an already qualified stock nozzle and one known filament/feed. ABS, multicolor AMS, custom nozzles and modeled fins need their own compatible profiles, physical conditions and checks. A two-color job currently needs one polymer and explicit AMS mapping; ABS and PETG in one recipe remain unsupported.

## 4. Request the first print

After inspecting the intended job and physically seeing that the bed is clear with the correct plate installed, send:

```text
$bambu-print Print the reviewed coupon job JOB_ID on lab-p2s. I have just checked that the bed is clear and the correct plate is installed. Use the confirmed nozzle, filament and feed. Report whether fresh telemetry shows this exact job preparing, printing or on its first layer. If launch is uncertain, reconcile the existing job without sending another start.
```

Replace both IDs and send this only when the physical statement is true. Bed confirmation lasts at most 30 minutes and is used once. Record the required layer review for the current artifact. A busy or faulted printer stays blocked. An upload or sent command alone does not prove printing.

Watch the first layer. Stop a failed print through the normal owner-controlled workflow if adhesion or another physical fault appears; do not leave a detached part printing. After completion, inspect the part before calling the setup hardware-tested.

## 5. Return test evidence

Send `lab-readiness.json`, the package version, operating system, Bambu Studio and printer firmware versions, selected material/nozzle/plate/feed, the observed job outcome, and a photo of the finished coupon if useful. State whether AMS was actually used and whether the nozzle was stock. A successful single-filament coupon does not qualify every AMS, material or custom-nozzle combination.

The report is generated locally and is not uploaded automatically. Do not send private `config.json`, access codes, raw telemetry, camera frames, job databases or full diagnostic logs. Review any additional screenshot/photo before sharing it. If a launch reply is lost, retain the existing job ID and state; do not delete history or issue a replacement print to test the connection.
