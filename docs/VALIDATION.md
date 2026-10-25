# Validation, 2026-10-25

Version 0.2.0. Hardware observations and software fault tests are separate evidence. Private job manifests, source scenes, addresses, serial numbers, camera frames and detailed telemetry remain local and are not distributed.

## Hardware observations

Target: Bambu Lab A1 mini, 0.4 mm stainless steel nozzle, external PETG, textured PEI, firmware 01.08.01.00. Slicer: installed Bambu Studio 02.08.02.61. Device identity and nozzle metadata were read live. Neither firmware nor LAN mode was changed.

1. A three-body Blender collection was exported with evaluated modifiers and world transforms. Independent STL dimensions matched Blender. Two exactly zero-area records were omitted only from the print copy under exact geometry invariants. A separate BVH check found no mutual intersections. Six actual G-code layers were reviewed. The checked file was uploaded and retrieved with matching SHA256. One launch was issued; its exact unique file and task name were observed at the first and later layers. Fresh telemetry subsequently reported FINISH, 100% and final layer 453. Finished-part dimensions and quality were not measured.
2. A different single-body collection in the active scene was exported without editing the source. The prepared file used 0.20 mm layers, four walls and 25% gyroid without supports. Six actual G-code layers were independently reviewed. The whole-object extrusion envelope matched source dimensions; slicer plate metadata described the narrower first layer. Upload checksum and exact task identity were confirmed. Completion of the second print has not yet been observed.
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

## Remaining limits

Only A1 mini has hardware launch evidence. AMS, other models, pause/resume/cancel and individual controls need separate physical validation. Minimum wall thickness, self-intersections, adhesion, strength and complete physical collision simulation are not guaranteed. External spool mass is unknown unless measured by the user.
