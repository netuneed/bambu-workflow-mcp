"""Read the live Blender dependency graph through its standard MCP server."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re

import numpy as np

from .geometry import load_mesh, sha256

RECEIPT = "BAMBU_EXPORT_RECEIPT:"


def export_script(output_dir: Path, selection: dict | None = None, mm_per_unit=None) -> str:
    args = json.dumps({"output": str(Path(output_dir).resolve()), "selection": selection or {}, "mm_per_unit": mm_per_unit})
    return "import json\n_p = json.loads(" + repr(args) + ")\n" + r'''
import bpy, math, struct, hashlib
from pathlib import Path
from mathutils import Vector

_scene = bpy.context.scene
if bpy.context.mode != 'OBJECT':
    raise ValueError('Switch Blender to Object Mode before export; no scene edits were made')
_selection = _p['selection']
if sum(bool(_selection.get(k)) for k in ('objects', 'object', 'collection')) > 1:
    raise ValueError('Choose objects or one collection, not both')
_visible_only = True
if _selection.get('objects') or _selection.get('object'):
    _names = _selection.get('objects') or [_selection['object']]
    _missing = [n for n in _names if n not in _scene.objects]
    if _missing:
        raise ValueError('Objects are absent from the current scene: ' + ', '.join(_missing))
    _roots = {_scene.objects[n] for n in _names}
    _visible_only = False
elif _selection.get('collection'):
    _coll = bpy.data.collections.get(_selection['collection'])
    if _coll is None or _coll not in list(_scene.collection.children_recursive) + [_scene.collection]:
        raise ValueError('Collection is absent from the current scene')
    _roots = {o for o in _coll.all_objects if o.visible_get()}
else:
    _roots = {o for o in bpy.context.selected_objects if o.visible_get()}
if not _roots:
    raise ValueError('No selected visible model. Select the intended model or name one object/collection')
_roots = {o for o in _roots if o.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'} or (o.type == 'EMPTY' and o.instance_type != 'NONE')}
if not _roots:
    raise ValueError('Selection contains no printable meshes or instances')
_override = _p['mm_per_unit']
if _override is not None:
    _factor = float(_override)
    _unit_source = 'explicit mm_per_unit'
elif _scene.unit_settings.system == 'NONE':
    raise ValueError('Blender scene has no units; configure blender.mm_per_unit once instead of guessing a scale')
else:
    _factor = float(_scene.unit_settings.scale_length) * 1000.0
    _unit_source = 'scene scale_length in metres'
if not math.isfinite(_factor) or _factor <= 0:
    raise ValueError('Invalid Blender unit scale')
_out = Path(_p['output'])
_out.mkdir(parents=True, exist_ok=True)
_stl = _out / 'blender-model-mm.stl'
_blend = _out / 'source.blend'
if _stl.exists() or _blend.exists():
    raise ValueError('Export destination already exists; use a new job directory')
_dg = bpy.context.evaluated_depsgraph_get()
_triangles = []
_objects = []
_mins = [float('inf')] * 3
_maxs = [float('-inf')] * 3
for _inst in _dg.object_instances:
    _obj = _inst.object
    _original = _obj.original
    _parent = _inst.parent.original if _inst.parent is not None else None
    _include = (_original in _roots and not _inst.is_instance) or (_inst.is_instance and _parent in _roots)
    if not _include or _obj.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
        continue
    _mesh = _obj.to_mesh(preserve_all_data_layers=False, depsgraph=_dg)
    try:
        if _mesh is None:
            continue
        _mesh.calc_loop_triangles()
        _matrix = _inst.matrix_world.copy()
        _flip = _matrix.to_3x3().determinant() < 0
        _count = 0
        for _tri in _mesh.loop_triangles:
            _indices = list(_tri.vertices)
            if _flip:
                _indices[1], _indices[2] = _indices[2], _indices[1]
            _coords = [(_matrix @ _mesh.vertices[i].co) * _factor for i in _indices]
            if any(not math.isfinite(v) for co in _coords for v in co):
                raise ValueError('Evaluated mesh contains non-finite coordinates')
            _normal = (_coords[1]-_coords[0]).cross(_coords[2]-_coords[0])
            if _normal.length > 0:
                _normal.normalize()
            _triangles.append(struct.pack('<12fH', *_normal, *_coords[0], *_coords[1], *_coords[2], 0))
            for co in _coords:
                for axis in range(3):
                    _mins[axis] = min(_mins[axis], float(co[axis]))
                    _maxs[axis] = max(_maxs[axis], float(co[axis]))
            _count += 1
        if _count:
            _objects.append({'name': _original.name, 'instance': bool(_inst.is_instance), 'triangles': _count,
                             'negative_world_determinant': _flip, 'world_matrix': [list(row) for row in _matrix]})
    finally:
        _obj.to_mesh_clear()
if not _triangles:
    raise ValueError('Selected objects produce no evaluated triangles')
if len(_triangles) > 10000000:
    raise ValueError('Evaluated model exceeds ten million triangles')
with _stl.open('xb') as _f:
    _f.write(b'Bambu Workflow MCP, evaluated Blender mesh in millimetres'.ljust(80, b'\0'))
    _f.write(struct.pack('<I', len(_triangles)))
    _f.writelines(_triangles)
# Write the current scene and its dependent datablocks as a separate .blend.
# libraries.write does not change bpy.data.filepath, selection, mode, or dirty state.
_before = {'filepath': bpy.data.filepath, 'dirty': bpy.data.is_dirty,
           'selected': sorted(o.name for o in bpy.context.selected_objects),
           'active': bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None}
bpy.data.libraries.write(str(_blend), {_scene}, path_remap='RELATIVE', fake_user=True, compress=True)
_after = {'filepath': bpy.data.filepath, 'dirty': bpy.data.is_dirty,
          'selected': sorted(o.name for o in bpy.context.selected_objects),
          'active': bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None}
if _before != _after:
    raise RuntimeError('Blender state changed during export; exported files retained for diagnosis')
_receipt = {'stl_path': str(_stl), 'blend_path': str(_blend), 'objects': _objects,
            'dimensions_mm': [_maxs[i]-_mins[i] for i in range(3)], 'bounds_mm': [_mins, _maxs],
            'mm_per_blender_unit': _factor, 'unit_source': _unit_source,
            'scene_unit_system': _scene.unit_settings.system, 'scene_scale_length': float(_scene.unit_settings.scale_length),
            'blender_version': bpy.app.version_string, 'source_scene': _scene.name, 'source_filepath': _before['filepath'],
            'source_had_unsaved_changes': _before['dirty'], 'scene_state_preserved': True,
            'sha256': hashlib.sha256(_stl.read_bytes()).hexdigest()}
print('BAMBU_EXPORT_RECEIPT:' + json.dumps(_receipt, ensure_ascii=False))
'''


def _texts(value):
    if isinstance(value, str):
        yield value
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return
        if decoded != value:
            yield from _texts(decoded)
    elif isinstance(value, dict):
        for x in value.values():
            yield from _texts(x)
    elif isinstance(value, list):
        for x in value:
            yield from _texts(x)


async def _export_async(config, out, selection, user_prompt):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    bc = config.get("blender", config)
    if not bc.get("command"):
        raise ValueError("Configure blender.command and blender.args for the existing Blender MCP server")
    params = StdioServerParameters(command=bc["command"], args=bc.get("args", []), env=bc.get("env"))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("execute_blender_code", {"code": export_script(out, selection, bc.get("mm_per_unit")),
                                                                      "user_prompt": user_prompt})
    data = result.model_dump()
    texts = list(_texts(data))
    if result.isError:
        raise RuntimeError("Blender MCP export failed: " + "\n".join(texts)[:3000])
    for text in texts:
        for line in text.splitlines():
            if RECEIPT in line:
                return json.loads(line.split(RECEIPT, 1)[1])
    raise RuntimeError("Blender did not return a verified export receipt: " + "\n".join(texts)[:3000])


def export_blender(config: dict, output_dir: Path, selection: dict | None = None, user_prompt: str = "") -> dict:
    if not user_prompt:
        raise ValueError("The user's original request must accompany live Blender MCP calls")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    timeout = float(config.get("blender", config).get("timeout_seconds", 120))
    async def run():
        return await asyncio.wait_for(_export_async(config, out, selection, user_prompt), timeout=timeout)
    receipt = asyncio.run(run())
    path = Path(receipt["stl_path"])
    mesh = load_mesh(path)
    if not np.allclose(mesh.extents, receipt["dimensions_mm"], atol=.001, rtol=.00001):
        raise RuntimeError("Independent STL dimensions do not match Blender's world-space export receipt")
    if sha256(path) != receipt["sha256"] or not Path(receipt["blend_path"]).is_file():
        raise RuntimeError("Blender export checksum or source backup verification failed")
    receipt["independent_dimensions_mm"] = mesh.extents.tolist()
    (out / "blender-export.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    return receipt
