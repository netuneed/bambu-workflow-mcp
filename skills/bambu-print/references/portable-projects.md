# Portable Bambu Studio projects

Use this reference for creating, importing or delivering an editable Bambu Studio 3MF, especially one with multiple objects or plates. Keep the original native/CAD files. File-only preparation does not require Blender.

## Inspect configuration and geometry separately

Call `inspect_project` with the absolute 3MF path before preparation. This offline check does not open Studio, slice, contact a printer or authorize printing. Read its material/variant validation and object/plate inventory. Inspect the archive's actual geometry when diagnosing a load failure; an "Invalid configuration file" dialog followed by "The file does not contain any geometry data" can result from rejected settings even when meshes are present.

Studio's GUI loader and CLI load/export follow different checks. Record semantic validation, CLI roundtrip, native GUI opening and actual sliced-layer review as separate evidence. Do not claim GUI acceptance from a successful CLI exit or exported archive. A native GUI observation must establish that the project opens without the configuration error and shows the intended objects and plates. If GUI opening was not observed, say so.

## Keep logical materials distinct from expanded variants

In the reviewed GUI loader, `filament_colour` establishes the number of logical filaments. A one-nozzle machine can still have several available extruder variants. Those variant slots are not extra colors, physical nozzles or AMS slots.

When `extruder_variant_list` is present, the GUI requires the expanded `filament_extruder_variant` and `filament_self_index` arrays to have equal lengths, with at least one slot for every logical filament. Each self-index identifies the actual logical filament using a 1-based index. Preserve the actual variant names, material identity and slot ordering; validate related arrays and indices against the installed schema before delivery.

When generating project settings from inherited machine, process and filament presets, derive the logical palette and expanded variant associations together. Validate the final merged project settings, not only each source preset. A source machine preset's available variants do not imply that every expanded row belongs to logical material 1.

For a new generator, use `bambu_workflow.project_settings.form_generated_project_settings(merged_settings, variant_rows=actual_row_ids)` and then `validate_project_settings(..., require_palette=True)`. Omit `variant_rows` only for an unambiguous single-material mapping or an already valid explicit mapping. The builder copies configuration, preserves unrelated values, and refuses an unidentified multicolor mapping. It does not repair imported archives or write geometry.

Composite/nozzle-group mappings outside the resolved single-tool defaults remain unresolved. Automatic launch-capable preparation blocks them; an offline inventory result does not qualify those routing adapters. Known nozzle-volume enums are bounded, and purge capacity is checked per retained nozzle table rather than against physical tool count.

For the confirmed one-material P2S case with three expanded variant slots, `filament_self_index` must contain three references to material 1: `["1", "1", "1"]`. A single `["1"]` is inconsistent with those three slots. This example is not a repair recipe for multiple materials. Derive each expanded row from its real material/variant association; never fill a multicolor project's indices with ones or resize every filament array to the same count.

An inconsistent length, missing logical material, unsupported variant or out-of-range index is a configuration blocker. Do not remove variant metadata, label every material as the first filament, or fall back to flattened STL to hide it. Resolve the concrete mapping in a derivative and validate it again.

Primary source: Bambu Studio [`PresetBundle::load_config_file_config`](https://github.com/bambulab/BambuStudio/blob/79df7ae309be398ed30c9f397c13c9911c2edc57/src/libslic3r/PresetBundle.cpp#L3732), revision `79df7ae309be398ed30c9f397c13c9911c2edc57`, reviewed 2026-11-06. The GUI branch keyed by `extruder_variant_list` checks the expanded lengths before loading presets.

## Preserve the intended plate layout

Record object/build-item counts, plate count, per-plate assignments, units and transforms before changing settings. A project with 13 objects on one plate and a project with one object on each of 13 plates are different deliverables. Confirm the owner's intended layout and selected plate; printing both duplicates the set.

The normalized geometry path does not preserve native multi-object or multi-plate projects. Keep authored layouts in Bambu Studio, slice the intended plates there, then pass the actual one-based `plate` to `prepare_print` or `print_model` for the sliced import (`--plate` in the CLI). One stable request selects one plate; do not change its plate on a retry. Geometry preparation creates only plate 1. Do not merge objects, move parts between plates, rescale, auto-orient or export the whole scene to make an unsupported route accept the file.

If a proven configuration-only repair is required, retain a backup and change only the required settings in a derivative. Compare every other ZIP member payload, including meshes, model settings and build transforms, before and after. Recheck the object and plate inventory. Repacking an archive can change its whole-file SHA256 even when individual payloads are identical; bind later job reviews to the resulting artifact hash.

After native GUI opening, confirm the actual printer, nozzle, plate and exact material presets, then inspect real sliced layers for each intended plate. GUI compatibility does not certify support contacts, physical fit, adhesion, strength, AMS routing or a successful future print. Keep normal explicit-print authorization, fresh bed observation and no-replay protections.
