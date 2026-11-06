# Capabilities

Custom-nozzle execution additionally checks actual G0-G3 extrusion throughput, E-only purges, modal/conditional state and modeled vendor rates against the calibrated cap. Generated flow settings retain a 1% margin for the official templates' rounded nominal filament area. Proprietary rate units are a documented inference from official template formulas. Unmodeled extrusion macros such as G150, unsupported physical nozzle materials, or unprovable flow block automatic preparation; retain a verified native Studio workflow for those combinations. This is not certification of arbitrary aftermarket hardware.

Version 0.4.0. [Validation](VALIDATION.md) separates hardware observations, software checks, and work still requiring acceptance. Primary sources were reviewed on [2026-11-06](SOURCES-2026-11-06.md).

| Capability | Scope |
| --- | --- |
| MCP stdio, CLI, persistent jobs and request identity | Implemented; source snapshots, launch intentions, and locks survive restarts |
| Blender export | Evaluated selected geometry, world transforms, millimeters, active-scene selection and source preservation |
| Fusion and QymCAD | Neutral STL/3MF exports with provenance and dimension checks; no native-document parser |
| Geometry reslicing | Single-material normalized mesh using inherited installed Bambu Studio profiles |
| Imported sliced 3MF | Selected executable plate with explicit used-filament palette/feed validation and artifact-bound layer review |
| Materials | Exact preset/product variant, polymer, optional profile/color, external or unit/slot AMS feed |
| Nozzles | Explicit manufacturer/model/diameter/material/flow facts; custom geometry compatibility, calibrated limits, and expiring confirmation |
| Supports | Explicit auto/native/baked-fins/manual modes; mandatory review for manual/finned and flagged tilted jobs |
| Support Fins | Optional locally installed checksum-pinned CLI; prepares a derivative, never starts a print |
| Printer selection | Configured devices with explicit printer ID; no claim of automatic LAN discovery |
| Assessment | Critical/medium/clear with mandatory independent launch gates and persistent medium review |
| Camera | Read-only observation; never proof of a clear bed |

## Models and physical evidence

The model registry follows official Bambu Studio profiles. Recognition, profile slicing, a read-only status reply, and upload success are separate from physical launch validation. Unknown models are rejected.

| Model IDs | Preparation | Automatic launch | Hardware evidence |
| --- | --- | --- | --- |
| `a1mini` | Matching installed profiles | Implemented | A1 mini firmware 01.08.01.00, from Windows |
| `a1`, `p1p`, `p1s`, `x1`, `x1c`, `x1e` | Matching installed profiles | Single-tool LAN workflow implemented | None |
| `p2s`, `h2s`, `a2l` | Matching installed profiles | Model-specific single-tool LAN workflow implemented | None |
| `h2d`, `h2dpro`, `h2c`, `x2d` | Restricted one-material factory multi-tool profiles | Blocked pending per-tool execution/telemetry/feed support | None |

The multi-tool block also applies to a file that appears to use one material. X2D's native eMMC upload path is unavailable. Laser, cutting, engraving, tool changing, and cloud/Bambu Connect launch are not implemented. No P2S, AMS, high-flow/custom-nozzle, or finned physical acceptance is claimed.

P2S supports up to four AMS 2 Pro plus four AMS HT units (20 positions) according to the official FAQ. AMS lite does not connect to the P series. P2S has no active chamber heater. The exact installed hardware, firmware, profiles, and feed telemetry still require verification. See the skill's [hardware reference](../skills/bambu-print/references/hardware-materials.md).

Local JPEG camera transport on TCP 6000 is declared for A1 mini/A1/P1P/P1S/A2L. Other registered families use their model's RTSP path through external `ffmpeg`. Hardware frame evidence exists only for A1 mini. A missing dependency or stream returns a specific error; MQTT success does not prove camera access.

## Import and material boundaries

Native `.f3d`, QymCAD documents, and STEP are not direct print inputs. Keep native sources and export only selected bodies in known units. QymCAD release notes document structured 3MF export in v0.1.0-dev.20261001 and an all-bodies export fix in v0.1.1-dev.20261026. This is upstream evidence, not an installed-QymCAD acceptance result.

Geometry reslicing combines source shells into one slicer object while retaining relative geometry. It does not retain CAD color, per-object material painting, arbitrary negative volumes/modifiers, or per-object skipping. Unsupported settings are refused. Preserve multicolor/manual-support projects through Bambu Studio and import a supported sliced plate; do not flatten to STL silently.

A material is more than a polymer name. `Bambu PETG HF` cannot be silently replaced by Generic PETG. Explicit mappings bind the full palette to exact presets, feed positions, and supplied profile/color facts. Missing or changed required facts block start. Automatic multicolor requires the same polymer and AMS for every entry. Mixed-polymer recipes, including ABS plus PETG, are rejected. Firmware metadata and optional external-spool confirmation remain evidence limits, especially for non-RFID products and remaining mass.

Stock Standard and HighFlow selection requires an exact compatible installed profile. Custom-nozzle confirmation records actual part identity, compatible printer/geometry, and calibrated temperature/flow limits. It does not automatically derive a new machine's wiping, clearance, or thermal behavior. Unverified geometry or limits remain unsupported. Renewed identical evidence preserves the nozzle revision; changed facts invalidate earlier preparation.

## What preflight proves

Checks cover finite geometry, explicit scale, closed consistent mesh, model build limits, matching resolved profiles, executable plate, artifact integrity, printer identity, idle/error state, configured nozzle/material/feed facts, required review, and one-use physical bed readiness. Launch uncertainty is reconciled rather than replayed.

Heuristics identify limited bed contact, multiple shells, downward faces, and support concerns. They do not fully simulate self-intersections, minimum printable walls, strength, adhesion, support removal, or physical/toolhead collisions. A concrete unsupported island, lost functional feature, or unexplained collision remains a blocker even if an aggregate assessment is clear.

Support Fins can report unsupported overhangs or skipped objects while exiting successfully. Fins and manual supports need real-layer inspection of contact continuity, island support, removal access, assignments, and the whole envelope. Expected local tine contact is not permission to ignore every collision warning. [Support review procedure](../skills/bambu-print/references/support-review.md).

## Platform boundary

Windows and macOS use private versioned Python environments and operating-system keyrings. Bambu Studio is installed separately. Prior native arm64 macOS CI exercised installation, software tests, and actual coupon slicing; this does not establish macOS-to-printer launch evidence. See [installation](INSTALL.md) and the exact dated results in [validation](VALIDATION.md).
