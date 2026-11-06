# CAD import

Checked against primary sources on 2026-11-06. This workflow consumes neutral exports, not native Fusion or QymCAD document internals. Keep the native source and, when useful for later dimension checks, a separate STEP archive. STEP is not a direct `prepare_print` input.

## Fusion

Use the intended body/component's **Save As Mesh** export with millimeters, an explicit body selection, and sufficient tessellation for the smallest functional feature. Autodesk documents 3MF/STL output, unit selection, and one-file or one-body-per-file structure. Export a print copy and verify a known length and the full bounding dimensions after import. STL contains no units, object colors, or CAD constraints.

Use 3MF when structure matters, but do not assume CAD face appearance becomes a printable Bambu filament assignment. Check body count, relative transforms, holes, and material assignments in Bambu Studio. For multiple colors or manual support painting, prepare a Bambu Studio project, slice it there, inspect it, and use the supported sliced-3MF workflow. Do not flatten it to STL to get past an unsupported project feature.

For an editable Bambu Studio project, also follow [portable-project validation](portable-projects.md). Keep its authored plate layout. The geometry route's normalized mesh is not a replacement for a multi-plate native project; use native Studio preparation and the supported explicit sliced-plate route when layout must survive. File-based STL/3MF preparation does not require Blender.

Primary source: [Autodesk Save As Mesh](https://help.autodesk.com/view/fusion360/ENU/?contextId=MESH-SAVE-AS-MESH), checked 2026-11-06; [Autodesk export instructions](https://help.autodesk.com/view/fusion360/ENU/?caas=caas%2Fsfdcarticles%2Fsfdcarticles%2FHow-to-export-an-STL-file-from-Fusion-360.html), dated 2026-04-24.

## QymCAD

The intended product is [QymIs-Tech/QymCAD](https://github.com/QymIs-Tech/QymCAD). Identify the installed release before exporting. Its README can lag behind its release notes.

- [v0.1.0-dev.20261001](https://github.com/QymIs-Tech/QymCAD/releases/tag/v0.1.0-dev.20261001) added 3MF export with assembly structure, names, placements, and colors.
- [v0.1.1-dev.20261026](https://github.com/QymIs-Tech/QymCAD/releases/tag/v0.1.1-dev.20261026) states that STEP/glTF/3MF export now includes every body of a part. Use this fix or a demonstrably equivalent later build for multi-body export. The tag's date is part of its name; do not infer release publication time from it.

Preserve the original document before upgrading or rebuilding: release notes describe document-format changes and possible rebuild differences. Check an export fixture with at least two bodies, a non-origin transform, a known dimension, and distinct colors before relying on a new build. Confirm every selected body exists after export. Color preservation in QymCAD does not by itself prove sliced filament mapping.

Reviewed repository revision: [`bb5ff2dff25c90fd77b5ff7387ea6380d53d34bd`](https://github.com/QymIs-Tech/QymCAD/tree/bb5ff2dff25c90fd77b5ff7387ea6380d53d34bd), 2026-10-26. No native QymCAD adapter or installed-app export acceptance is implied.

## Job evidence

Pass `source="file"`, an absolute exported STL/3MF path, and available `cad_provenance`:

```json
{
  "application": "QymCAD",
  "version": "v0.1.1-dev.20261026",
  "units": "millimeter",
  "expected_dimensions_mm": [80, 32, 18],
  "source_path": "C:/models/part.qcad"
}
```

Use measured dimensions and the actual source path; omit optional unknown fields. Provenance records a claim, not a native-file interpretation. Investigate unexpected dimensions instead of auto-scaling. Archive export settings and source revision locally when available. Any approved geometry/export correction produces a new artifact and new job.
