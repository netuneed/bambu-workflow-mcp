# Acceptance coupon

`acceptance-coupon-12x12x1.2mm.stl` is an owned 12 x 12 x 1.2 mm plate, with dimensions in millimeters. It contains geometry only, without a printer profile, filament assignment, or executable G-code. It is intended for a small first workflow test, not dimensional, strength, material, or adhesion qualification.

The release installs this folder under the versioned runtime. Use the exact runtime path reported by the installer. The [Lab quickstart](../docs/LAB_QUICKSTART.md) covers configuration and the first P2S test.

In a configured Codex chat, ask:

```text
$bambu-print Prepare the installed acceptance-coupon-12x12x1.2mm.stl on lab-p2s. Confirm the installed nozzle, plate, exact filament preset and physical feed. Check the 12 x 12 x 1.2 mm dimensions and review the actual sliced layers. Do not start printing.
```

Replace `lab-p2s` with the intended configured printer ID. The installed `tools/call_mcp.py` client can make the same `prepare_print` request; see [usage](../docs/USAGE.md). Keep one stable request ID and the returned job ID.

A physical test needs the owner's explicit instruction for that reviewed job and a fresh observation of a clear bed with the correct plate. Setup, preparation, a readiness report, or an upload does not start a print. An uncertain start must be reconciled for the existing job, never repeated.
