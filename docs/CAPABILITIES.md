# Capabilities

Version 0.3.0. [Validation](VALIDATION.md) distinguishes hardware evidence from software tests.

| Capability | Status |
| --- | --- |
| Standard stdio MCP, CLI and Python client | Implemented and exercised |
| Evaluated Blender export with units, world transforms and source snapshot | Exercised with Blender 5.1.2; source state preserved |
| STL and supported geometry/project/sliced 3MF | Implemented; complex per-object modifier settings rejected |
| Actual slicing with inherited installed Bambu Studio profiles | Exercised with 02.08.02.61 |
| Critical/medium/clear assessment | Mandatory checks enforced; persistent medium review; conversational fallback in skill |
| A1 mini LAN status, FTPS verified upload and launch | Hardware exercised on firmware 01.08.01.00 |
| Exact-job reconciliation and persistent duplicate protection | Hardware exercised, including missing PUBACK and client restart |
| Camera frame | Hardware exercised; never bed-clear evidence |
| Pause, resume, cancel, fan, temperature and speed | Software tested; not individually hardware validated |
| AMS selection | Software tested; not hardware validated |
| Windows runtime installer | User-local venv, protected version directory and offline catalog check |
| macOS runtime installer | Native arm64 CI exercised private venv, Keychain backend, repeated installation and stdio catalog |
| Model registry | 14 Bambu models; per-model launch and camera restrictions below |

## Models

Registry names and device features follow the official [BBL profiles](https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL.json) and [printer definitions](https://github.com/bambulab/BambuStudio/tree/master/resources/printers). Recognition and installed profile support do not establish hardware validation. Unknown models are rejected.

| Model IDs | Preparation and inspection | LAN launch | Hardware evidence |
| --- | --- | --- | --- |
| `a1mini` | Implemented | Implemented | Firmware 01.08.01.00; exact job launch and layers verified |
| `a1`, `p1p`, `p1s`, `x1`, `x1c`, `x1e` | Software support with matching installed profiles | Software support; requires matching live identity and configuration | None |
| `p2s`, `h2s`, `a2l` | Software support with matching installed profiles | Software support; requires matching live identity and configuration | None |
| `h2d`, `h2dpro`, `h2c`, `x2d` | One-material preparation with matching stock nozzles and actual used-tool workspace checks | Blocked pending per-tool G-code, live telemetry and feed mapping support | None |

The dual-tool launch block applies even to a file that appears to use one material. Device recognition, a read-only status reply or a successful upload cannot remove it. X2D uses native eMMC; its upload and launch path is not implemented. Laser, cutting, engraving, tool changing and cloud/Bambu Connect control are not implemented.

The local JPEG camera protocol on TCP 6000 is selected for A1 mini, A1, P1P, P1S and A2L. Other registered families use their declared RTSP path through an external `ffmpeg` executable. Camera declarations are software support, with hardware frame evidence only for A1 mini. A missing stream or dependency returns a specific error; MQTT success does not prove camera support.

Checks cover finite geometry, explicit scale, closed consistent mesh, build size, compatible printer/nozzle/plate/material, executable plate, integrity, fresh identity, idle/error state and physical bed readiness. They do not certify mechanical strength or print success.

Medium findings include multiple shells, limited bed contact and downward faces needing layer review. These are heuristic concerns. Concrete unsupported islands, collisions or lost functional geometry found independently remain blockers even if the automatic report is clear.

Original scenes are preserved. Print-copy sanitation removes only exactly zero-area records with unchanged geometry invariants. Scaling, topology changes, cutting and fit compensation require explicit authorization.

One material per plate and stock Standard nozzle flow are supported. High-flow nozzle variants are not automatically selected or validated. Source objects currently become one slicer object with their relative geometry retained. Per-source-object skip is unavailable. AMS metadata and selection require model-appropriate telemetry and explicit confirmed feed configuration; unknown tray mappings remain blockers. Hardware AMS validation has not been performed.

The macOS bundle uses a private Python 3.12+ environment and OS keyring, while Bambu Studio remains an independently installed program. [Native arm64 CI](https://github.com/netuneed/bambu-workflow-mcp/actions/runs/40237028433/job/121300666771) passed software tests, repeated installation and actual Bambu Studio coupon slicing. Its results do not establish macOS-to-printer launch evidence or certify every firmware/profile combination.
