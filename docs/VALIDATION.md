# Validation

Version 0.4.0, updated 2026-11-06. Earlier hardware observations date from 2026-10-25/26; they do not become P2S/AMS/custom-nozzle evidence after a software update. Private manifests, source scenes, addresses, serial numbers, camera frames, and detailed telemetry remain local.

## 2026-11-06 extension review

Five independent advisors and two cross-reviewers examined CAD import, P2S/AMS/material/nozzle identity, support/diagonal failure boundaries, and acceptance criteria. [Review decisions](REVIEW-2026-11-06.md) and [versioned primary sources](SOURCES-2026-11-06.md) record the resulting scope. They did not operate printers or execute upstream Support Fins code.

The installed Bambu Studio CLI successfully prepared P2S ABS and exact `Bambu PETG HF` profiles with Standard and HighFlow machine variants. This is actual software/profile slicing evidence, not P2S hardware acceptance. Multicolor sliced import, artifact review, and feed checks have their own acceptance scope; do not infer it from a single-material coupon.

Three hand-authored Windows test projects triggered Studio's out-of-memory dialog and crash. Further native attempts on the user's host were stopped. No Studio process remained active; existing Studio configuration/backup modification times stayed unchanged, and no CAD source was overwritten. Runtime preparation rejects unsupported project structure before invoking Studio and uses an isolated Studio data directory.

[CI run 42175381693](https://github.com/netuneed/bambu-workflow-mcp/actions/runs/42175381693), commit `e16f39f`, then passed on Windows (432 tests, 2 skips, 49 subtests) and macOS arm64 (428 tests, 6 skips, 49 subtests). Native official Studio 02.08.02.61 on the isolated Mac generated two owned bodies in red and blue Bambu PETG HF. The independent inspector verified logical T0/T1 on one physical tool, both bodies, deposited prime tower, full extrusion bounds and byte-exact sliced import. Artifact SHA256: `3a90d61b808c5f519fe18cd2e8cf9c423a86a5d4db802e969b319a70b5846f81`. Both isolated installed runtimes exposed 21 tools and passed idempotent installation. No printer was contacted. This establishes multicolor preparation/inspection software evidence, not physical AMS routing or a P2S launch.

The same native coupon and two-color acceptance also passed with the current stable Studio 02.09.01.70 in [CI run 42175823628](https://github.com/netuneed/bambu-workflow-mcp/actions/runs/42175823628), code commit `0aa1636`. Its official DMG was checksum-verified. The two-color artifact SHA256 was `c131392a0f0d7af4c0ee604f2f7e14ef4915020299acb5b921c9ed960d969442`. This run also passed both platform test/install jobs. Studio on the user's Windows computer was not upgraded or relaunched for this check.

An exact-version advisory check of the development environment identified published filelock advisories affecting 3.32.7. The project declaration and runtime locks were raised to 4.2.1, and the development environment was updated. [Third-party notices](THIRD_PARTY.md) link the checked advisories. Time-specific advisory checks do not certify absence of unknown vulnerabilities.

The English skill separates ordinary preparation from mandatory actual-layer review for manual/finned/tilted, imported sliced, and multicolor jobs. Review evidence is tied to the prepared artifact SHA256. A review record is a truthful claim by its reviewer; the runtime cannot establish that a person actually inspected a preview from boolean fields alone.

No installed Fusion/QymCAD export round trip, P2S/AMS physical routing, high-flow/custom-nozzle physical qualification, or fin contact/removal print was performed for this update. P2S has no new hardware evidence. No physical print is authorized merely by preparing or testing this package.

## Hardware observations

Target: Bambu Lab A1 mini, 0.4 mm stainless steel nozzle, external PETG, textured PEI, firmware 01.08.01.00. Slicer: installed Bambu Studio 02.08.02.61. Device identity and nozzle metadata were read live. Neither firmware nor LAN mode was changed.

1. A three-body Blender collection was exported with evaluated modifiers and world transforms. Independent STL dimensions matched Blender. Two exactly zero-area records were omitted only from the print copy under exact geometry invariants. A separate BVH check found no mutual intersections. Six actual G-code layers were reviewed. The checked file was uploaded and retrieved with matching SHA256. One launch was issued; its exact unique file and task name were observed at the first and later layers. Fresh telemetry subsequently reported FINISH, 100% and final layer 453. Finished-part dimensions and quality were not measured.
2. A different single-body collection in the active scene was exported without editing the source. The prepared file used 0.20 mm layers, four walls and 25% gyroid without supports. Six actual G-code layers were independently reviewed. The whole-object extrusion envelope matched source dimensions; slicer plate metadata described the narrower first layer. Upload checksum and exact task identity were confirmed. This print was subsequently stopped after reported bed detachment. Successful dispatch and layer checks did not guarantee adhesion.
3. Repeating the original authorized request through a new MCP process returned the same job and issued no additional start. Its durable start intent and single launch event were unchanged.

A generic 12 x 12 x 1.2 mm coupon was sliced and upload-tested; it was not printed. Its original STL is included under `examples/`.

## Real faults addressed

- New TLS sessions initially failed while older monitoring clients held several connections. Independent Python and Node probes reproduced the failure before authentication. A user-authorized temporary removal of one duplicate connection allowed the new client to connect. The old configuration was restored and fresh telemetry remained readable. This does not establish a universal connection limit.
- Read-only MQTT snapshots succeeded with QoS 0. A physical start took effect despite a missing PUBACK. Exact subsequent telemetry resolved uncertainty without repeating the command. Late transport errors cannot demote independently proven printing.
- FTPS data shutdown could hang after all bytes arrived. Full retrieval with the expected checksum establishes completion without blindly repeating STOR.
- A client may terminate its worker process tree. Persisted source snapshots, locks and launch intentions allow preparation recovery without replaying a start. Medium review stays pending after restart.
- This firmware clears `gcode_file` after completion. The prior job can complete only with its retained unique task name, previously verified printing of the exact file, 100%, all reported layers and no error. A retained name alone is insufficient.

## Software verification

Run `python -m pytest` for the current suite. Mocked transports never send physical commands. Tests cover invalid geometry/profiles, executable plate inspection, partial transfer, file changes, lost replies, concurrent workers and leases, persistent request identity, restarts, stale telemetry, substituted devices and critical/medium/clear decisions.

The wheel is checked in a separate environment. Release ZIPs contain a checksum manifest and pinned runtime constraints. The skill has separate structural validation and behavioral review.

The GitHub Actions workflow includes native `windows-latest` and `macos-latest` jobs with Python 3.12, following GitHub's [Python workflow guidance](https://docs.github.com/en/actions/tutorials/build-and-test-code/python). Each job runs the full software suite, builds distributions and installs the wheel in a separate temporary runtime. A fake configuration is used only for a metadata-only stdio catalog; installation is repeated to check idempotence, the configuration hash must remain unchanged and no job state may be initialized. No skill installation, Codex registration or printer connection occurs in that check. A workflow definition is not a completed result; inspect the [actual CI run](https://github.com/netuneed/bambu-workflow-mcp/actions) for the tested commit and OS.

The macOS job also downloads the [official Bambu Studio v02.08.02.61 DMG](https://github.com/bambulab/BambuStudio/releases/tag/v02.08.02.61), verifies its pinned SHA256, mounts it read-only and copies the app to a temporary directory. `scripts/native_slicer_smoke.py` runs the actual CLI on the included 12 mm A1 mini PETG coupon and independently inspects the output archive. Generated logs and inspection results are CI artifacts; the app is not redistributed. This helper has also completed with the installed Windows CLI. Neither path creates a printer configuration or invokes a transport/worker.

[Native macOS validation passed](https://github.com/netuneed/bambu-workflow-mcp/actions/runs/40237028433/job/121300666771) at commit `0e87b42`: macOS 26.6.2 on arm64, Python 3.12, 206 tests and 31 subtests. The official app contains arm64 and x86_64 binaries; this job executed arm64. Coupon slicing and output inspection passed. The isolated installed runtime reported 16 MCP tools, `keyring.backends.macOS`, a venv launcher under the temporary install root and a successful second installation with `reused=true`. Configuration was unchanged, job state was not initialized and Codex registration was not modified. Keychain backend discovery was checked without reading or storing a printer secret.

POSIX installer refusal tests cover unowned directories, interrupted installs, replaced same-version wheels, mismatched checksums and symbolic-link redirection where the host permits creating test links. Platform-specific camera/feed/profile branches and all 14 registry models use mocks or installed profile data; those checks do not turn them into hardware-tested printers.

On Windows, the actual installed Bambu Studio CLI sliced and inspected the coupon with profiles for all 14 registered models. Dual-tool preparation was limited to the implemented one-material, matching-stock-nozzle path. This validates that software preparation path with those installed presets; it does not validate any new printer's telemetry, feed routing or launch.

## Remaining limits

Only A1 mini has hardware launch evidence, obtained from Windows. Native macOS installation tests do not establish a macOS print launch. AMS, other models, pause/resume/cancel and individual controls need separate physical validation. H2D, H2D Pro, H2C and X2D launches remain blocked. Minimum wall thickness, self-intersections, adhesion, strength and complete physical collision simulation are not guaranteed. External spool mass is unknown unless measured by the user.
