# Protocol and source boundaries

Version 0.4.0. Existing protocol evidence was checked on 2026-10-26; P2S/CAD/AMS/nozzle/support sources were reviewed on [2026-11-06](SOURCES-2026-11-06.md). The selected route is independently installed Bambu Studio CLI followed by local MQTT/FTPS. Neither Bambu Studio nor its proprietary network plugin is bundled. Cloud login and Bambu Connect are not treated as proof that this LAN adapter can start a printer.

## Permission and models

The official A1 mini history lists firmware 01.05.00.00, dated 2025-06-03, as introducing Authorization Control and Developer Mode. Telemetry can remain readable while unauthorized commands are rejected. Developer Mode is an owner-enabled LAN Mode option. Read access and a reported flag are not physical control acceptance.

Primary sources: [A1 mini firmware history](https://wiki.bambulab.com/en/a1-mini/manual/a1-mini-firmware-release-history), [Developer Mode](https://wiki.bambulab.com/en/knowledge-sharing/enable-developer-mode), [Bambu update of 2025-01-20](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/), and [Security White Paper 2025.09](https://cdn1.bambulab.com/trust-center/file/bambulab-security-whitepaper-en.pdf). The wiki pages were checked by direct HTTPS after extraction failed. One model's firmware number is not a universal minimum.

The reviewed [BBL registry](https://github.com/bambulab/BambuStudio/blob/79df7ae309be398ed30c9f397c13c9911c2edc57/resources/profiles/BBL.json) contains 14 models. The later reviewed Studio source revision is [`355d71eee5a9ebc794693fe57e63674806019770`](https://github.com/bambulab/BambuStudio/tree/355d71eee5a9ebc794693fe57e63674806019770), 2026-11-06. Profiles do not establish live firmware compatibility. Multi-tool launch remains explicitly blocked; see [capabilities](CAPABILITIES.md).

## LAN transport

MQTT uses TLS port 8883, username `bblp`, the LAN access code, and `device/<serial>/request` / `device/<serial>/report`. A fresh full state can be requested with `pushing.command="pushall"`. Pause/resume/stop use `print.command` values `pause`, `resume`, and `stop`, with empty `param` and a separate `sequence_id`. These formats appear in official [DeviceManager.cpp](https://github.com/bambulab/BambuStudio/blob/355d71eee5a9ebc794693fe57e63674806019770/src/slic3r/GUI/DeviceManager.cpp), but firmware remains the actual interoperability boundary.

FTPS uses implicit TLS port 990 and a protected data channel. Firmware can require reuse of the control connection's TLS session for data transfer. Job-specific remote filenames prevent accidental overwrite. Compare the local artifact hash before upload and after complete retrieval; byte length alone is insufficient. An error during TLS data shutdown can occur after all bytes arrived, so reconcile the transferred file before retrying.

The main launch route uses a unique `.3mf`, selected `Metadata/plate_N.gcode`, URL/storage route, AMS mapping, and the selected G-code MD5. This is an interoperable third-party protocol path, not a published stable Bambu API. Official Studio delegates details through its closed network plugin; see [PrintJob.cpp](https://github.com/bambulab/BambuStudio/blob/355d71eee5a9ebc794693fe57e63674806019770/src/slic3r/GUI/Jobs/PrintJob.cpp) and [network interface](https://github.com/bambulab/BambuStudio/blob/355d71eee5a9ebc794693fe57e63674806019770/src/slic3r/Utils/bambu_networking.hpp). A primary third-party implementation documents [the LAN protocol](https://github.com/voska/bambu/blob/main/docs/protocol.md).

The low-level legacy `gcode_file` path is restricted to a single plate/external feed without project filament selectors and has no new physical launch acceptance. It does not bypass the main workflow's validation. Local artifact extensions do not determine the remote filename or launch semantics.

AMS unit/slot normalization follows current reported identities and selected-material metadata. Official [DevFilaSystem.cpp](https://github.com/bambulab/BambuStudio/blob/355d71eee5a9ebc794693fe57e63674806019770/src/slic3r/GUI/DeviceCore/DevFilaSystem.cpp) was inspected for source guidance. Telemetry coverage varies by firmware; no P2S feed-routing hardware result is claimed. Never infer a tray from its list position when its actual unit/slot identity is available.

Camera transport is model-specific: JPEG TCP for A1/A1 mini/P1/A2L families and an external-ffmpeg RTSP path for declared other families. See [MediaPlayCtrl.cpp](https://github.com/bambulab/BambuStudio/blob/355d71eee5a9ebc794693fe57e63674806019770/src/slic3r/GUI/MediaPlayCtrl.cpp) and [printer definitions](https://github.com/bambulab/BambuStudio/tree/355d71eee5a9ebc794693fe57e63674806019770/resources/printers). Only A1 mini camera capture has hardware evidence.

## Slicing and import

The official [CLI wiki](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage), checked 2026-10-26, describes `--slice 0` as all plates and `--slice i` as plate i. Check the installed binary's real `--help` and successful output rather than assuming a web page matches it.

`--load-settings` and `--load-filaments` need resolved configurations. Resource presets can contain only parent differences plus `include` G-code templates; resolving `inherits` alone is insufficient. Resolved machine/process/exact filament presets are retained with each job. A matching product family is not a matching PETG HF preset.

STL has no units. Blender export uses evaluated geometry, world transforms, and verified millimeter conversion while preserving scene state. CAD imports use explicit units/provenance and dimensions; native documents are retained rather than parsed. Geometry normalization does not preserve arbitrary assembly settings or painted filament regions. Supported pre-sliced 3MF is inspected separately and retains explicit used-filament mapping.

Support Fins is an optional external preparation tool pinned to a reviewed revision and local artifact checksum. Tool output must be resliced and actually reviewed; upstream success can still report unsupported overhangs. [Support procedure](../skills/bambu-print/references/support-review.md).

## Confirmation and licensing

Persistent states distinguish preparation, transfer, start intention, acceptance, printer preparation, real printing, and final result. A start intention is committed before publication. Uncertain outcomes permit reconciliation, never blind replay. `gcode_state=RUNNING` alone can describe preparation; exact job identity and stage/layer evidence are required. Camera observations do not replace job telemetry or human bed readiness.

Own code is MIT. Bambu Studio is AGPL-3.0 and its optional network plugin includes non-free components. External programs are separately installed. [Third-party notices](THIRD_PARTY.md) describe redistribution boundaries. Alternative forks and cloud tools are not dependencies of this LAN workflow.
