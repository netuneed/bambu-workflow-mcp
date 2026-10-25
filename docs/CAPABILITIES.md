# Capabilities

Version 0.2.0. [Validation](VALIDATION.md) distinguishes hardware evidence from software tests.

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
| Other models | Adapter extension architecture exists; actuation blocked until validated |

Checks cover finite geometry, explicit scale, closed consistent mesh, build size, compatible printer/nozzle/plate/material, executable plate, integrity, fresh identity, idle/error state and physical bed readiness. They do not certify mechanical strength or print success.

Medium findings include multiple shells, limited bed contact and downward faces needing layer review. These are heuristic concerns. Concrete unsupported islands, collisions or lost functional geometry found independently remain blockers even if the automatic report is clear.

Original scenes are preserved. Print-copy sanitation removes only exactly zero-area records with unchanged geometry invariants. Scaling, topology changes, cutting and fit compensation require explicit authorization.

One material per plate is supported. Source objects currently become one slicer object with their relative geometry retained. Per-source-object skip is unavailable.
