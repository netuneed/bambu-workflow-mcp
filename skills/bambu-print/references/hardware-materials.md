# Hardware and materials

For a declared custom hotend, the executable must also stay within its calibrated flow cap, including startup purges and coordinated G0-G3 extrusion. Unknown firmware extrusion macros remain blockers. The current P2S stock startup contains G150, whose internal extrusion rate is not bounded by this adapter; custom hardware using that startup needs a separately validated machine adapter or owner-controlled native Studio workflow. A brand declaration and a matching diameter cannot waive this limit. Stock Bambu nozzle workflows use their installed official profiles.

Primary-source check date: 2026-11-06. Read `list_models` for current software support and `list_printers` for configured devices. Only A1 mini has physical launch evidence in the published validation. A P2S profile or status reply does not establish P2S/AMS hardware acceptance.

## Printer selection and P2S

Bind each job to a configured printer ID and independently verified device identity. Two printers of the same model remain different devices. A default-printer change must not redirect a prepared job. Ask for the intended ID when several match. Never claim discovery covers unconfigured LAN devices.

The official [P2S FAQ](https://bambulab.cn/zh-cn/p2s/faq) documents up to four AMS 2 Pro units plus four AMS HT units, totaling 20 feed positions. [AMS lite](https://store.bambulab.com/products/ams-lite) is incompatible with X/P/H2 series; a filament-compatibility table mentioning AMS lite is not evidence that it connects to P2S. P2S has an enclosed chamber but no active chamber heater. Use the installed P2S profile and relevant filament/plate conditions rather than inventing a chamber-temperature command. See the [P2S manual](https://csm.bblcdn.com/hub/9425242c00344b0e801b6179f9a243aa.pdf).

## Filament identity and AMS

Call `material_inventory(printer_id)` and inspect its freshness, unit/slot identity, and unknown fields. Treat a spool's polymer type, product variant, preset/profile ID, color, and physical feed as separate facts. PETG HF is a filament product variant; it does not require or identify a HighFlow nozzle by its name alone. Use the exact installed preset, resolved parameters, and manufacturer guidance. The [Bambu PETG HF data sheet](https://store.bblcdn.com/ce12d65176a94f1086e6aefa238e62e2.pdf) is a product source, not evidence that a particular loaded spool is dry or correctly identified.

Select contiguous 1-based slicer filament IDs. AMS unit and slot IDs come from current inventory, not a guessed flat tray number:

```json
{
  "materials": [
    {
      "filament_id": 1,
      "type": "PETG",
      "preset": "Bambu PETG HF",
      "profile_id": "GFG02",
      "color": "FF0000FF",
      "feed": {"kind": "ams", "ams_id": 0, "slot_id": 0}
    }
  ]
}
```

These values illustrate shape only. Use the actual installed preset and observed profile/color. For external feed use `{"kind":"external"}` and human-confirmed spool facts where telemetry is absent. Legacy `feed={"kind":"ams","tray_id":0}` remains available for single-material geometry preparation. Every imported sliced file, including one color, requires `materials` for the complete logical palette, including unused entries; legacy `feed` is insufficient. Prefer explicit unit/slot mapping for new requests.

Automatic multicolor currently requires one polymer and AMS for every palette entry. Mixed-polymer recipes, including ABS plus PETG, are rejected until a validated compatibility adapter exists. Retain object/face assignments through Bambu Studio, slice the correct plate, then import the supported sliced 3MF with the complete palette mapped. Check tool changes, purge/prime tower, complete envelope, assigned colors, and real layer transitions. Unsupported project modifiers or lost painting must be reported rather than silently removed.

Before start, reread current feed facts. A changed product/profile, color, missing slot, or ambiguous routing invalidates the prepared assumptions. Do not mark unknown spool mass or remaining length as known. Flexible or abrasive products need their own current manufacturer/AMS and nozzle compatibility check.

## Stock and custom nozzles

The official [H2/P2S/X2D hotend table](https://store.bambulab.com/products/bambu-hotend-h2d) lists Standard 0.2 mm stainless steel; Standard 0.4/0.6/0.8 mm hardened steel; and High Flow 0.4/0.6/0.8 mm hardened steel. Verify the selected model's exact part and installed profile. Do not infer nozzle material or flow class from diameter alone.

For a nozzle change or custom part, use `confirm_nozzle(printer_id, facts, human_evidence)` with actual manufacturer, model, diameter, material, `flow_class` (`Standard` or `HighFlow`), and `custom` flag. Custom facts additionally need printer compatibility, stock geometry compatibility, calibrated `max_volumetric_speed` and `max_temperature_c`. Live diameter/material telemetry cannot identify an aftermarket internal geometry or safe flow limit. Never label an unknown part Standard merely to pass a check.

The automatic adapter accepts only truthful stainless-steel or hardened-steel declarations with compatible stock geometry. Brass, tungsten, other unrepresented materials, and incompatible mounts/lengths need a verified custom Studio workflow and owner-controlled launch; never relabel them as steel. Custom nozzle confirmation must be renewed from human evidence within the required 30-minute window before start. Renewing unchanged facts preserves their configuration revision; changed facts invalidate earlier preparation. Record a new `installation_id` when a physical replacement/reinstallation needs to be distinguished from a renewed observation of the same installation. Custom jobs also require actual-layer review. A matching software profile is necessary but does not prove geometry, wiping/clearance, flow, temperature, or extrusion calibration. Unknown compatibility or uncalibrated limits remain blockers. No custom-nozzle hardware qualification is claimed by this package.

Keep bed-readiness and nozzle evidence distinct. Neither idle status nor a previous job proves a clear bed, the current plate, installed nozzle, or loaded filament.
