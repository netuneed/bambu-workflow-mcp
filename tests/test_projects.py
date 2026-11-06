"""Synthetic portable projects exercise metadata validation without Studio or devices."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from bambu_workflow.artifacts import classify_3mf
from bambu_workflow.config import Config
from bambu_workflow.projects import inspect_project
from bambu_workflow.slicer import _write_project, slice_model


def settings():
    variants = ["Direct Drive Standard", "Direct Drive High Flow", "Direct Drive E3D High Flow"]
    return {"printer_model":"Bambu Lab P2S", "nozzle_diameter":["0.4"],
            "filament_colour":["#FFFFFF"], "filament_type":["ABS"],
            "filament_settings_id":["Generic ABS @BBL P2S"],
            "extruder_variant_list":[",".join(variants)],
            "filament_extruder_variant":variants, "filament_self_index":["1"]*3,
            "printer_extruder_variant":variants, "printer_extruder_id":["1"]*3,
            "print_extruder_variant":variants, "print_extruder_id":["1"]*3}


def portable(path, *, per_plate=False, bad_mapping=False):
    config=settings()
    if bad_mapping:config['filament_self_index']=['1']
    # Owned tiny tetrahedra are enough for inventory. No borrowed CAD geometry.
    mesh='<mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/><vertex x="0" y="0" z="1"/></vertices><triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/><triangle v1="1" v2="2" v3="3"/><triangle v1="2" v2="0" v3="3"/></triangles></mesh>'
    objects=''.join(f'<object id="{i}" type="model">{mesh}</object>' for i in range(1,14))
    build=''.join(f'<item objectid="{i}" transform="1 0 0 0 1 0 0 0 1 {i*5} 10 0"/>' for i in range(1,14))
    model='<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="millimeter"><resources>'+objects+'</resources><build>'+build+'</build></model>'
    groups=[[i] for i in range(1,14)] if per_plate else [list(range(1,14))]
    plates=''.join(f'<plate><metadata key="plater_id" value="{n}"/>'+''.join(f'<model_instance><metadata key="object_id" value="{i}"/><metadata key="instance_id" value="0"/></model_instance>' for i in ids)+'</plate>' for n,ids in enumerate(groups,1))
    authored='<config>'+''.join(f'<object id="{i}"/>' for i in range(1,14))+plates+'</config>'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('3D/3dmodel.model',model)
        z.writestr('Metadata/project_settings.config',json.dumps(config))
        z.writestr('Metadata/model_settings.config',authored)
    return path


@pytest.mark.parametrize('per_plate',[False,True])
def test_inventory_preserves_one_or_thirteen_plate_layout(tmp_path,monkeypatch,per_plate):
    path=portable(tmp_path/'owned.3mf',per_plate=per_plate)
    before=path.read_bytes()
    monkeypatch.setattr(Config,'__init__',lambda *a,**k:pytest.fail('No printer configuration access'))
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('No application launch'))
    result=inspect_project(path)
    assert result['source_sha256']==hashlib.sha256(before).hexdigest()
    assert result['settings_validation']['logical_filament_count']==1
    assert result['settings_validation']['filament_variant_rows']==3
    inventory=result['inventory']
    assert inventory['authored_object_count']==inventory['root_build_item_count']==13
    assert [p['object_instances'] for p in inventory['authored_plates']]==([1]*13 if per_plate else [13])
    assert result['requires_preservation'] and not result['printer_contacted'] and not result['can_print']
    assert result['gui_validation']=='not_observed' and not result['source_modified']
    assert path.read_bytes()==before


def test_invalid_settings_are_not_reported_as_missing_geometry(tmp_path):
    path=portable(tmp_path/'invalid.3mf',bad_mapping=True)
    with zipfile.ZipFile(path) as z:assert b'<vertices>' in z.read('3D/3dmodel.model')
    with pytest.raises(ValueError,match='filament_self_index'):
        inspect_project(path)


@pytest.mark.parametrize('per_plate',[False,True])
def test_authored_layout_is_not_flattened_or_sent_to_native_cli(tmp_path,monkeypatch,per_plate):
    path=portable(tmp_path/'owned.3mf',per_plate=per_plate)
    before=path.read_bytes()
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('Do not invoke Studio'))
    with pytest.raises(ValueError,match='retain these in Bambu Studio'):
        slice_model(path,tmp_path/'out',{'model':'p2s'},{})
    assert path.read_bytes()==before and not (tmp_path/'out/normalized-mm.stl').exists()


def test_bad_metadata_does_not_replace_existing_derivative(tmp_path):
    source=portable(tmp_path/'invalid.3mf',bad_mapping=True)
    destination=tmp_path/'project.3mf';destination.write_bytes(b'previous owned artifact')
    with pytest.raises(ValueError,match='filament_self_index'):
        _write_project(source,destination)
    assert destination.read_bytes()==b'previous owned artifact'


def test_derivative_copy_retains_every_geometry_and_authored_plate_payload(tmp_path):
    source=portable(tmp_path/'sliced.3mf',per_plate=True)
    with zipfile.ZipFile(source,'a') as z:
        z.writestr('Metadata/plate_13.gcode','; owned placeholder, never executable acceptance')
        z.writestr('Metadata/plate_13.gcode.md5','0'*32)
        z.writestr('Metadata/slice_info.config','<config/>')
    with zipfile.ZipFile(source) as z:before={i.filename:z.read(i) for i in z.infolist()}
    destination=tmp_path/'editable.3mf'
    _write_project(source,destination)
    with zipfile.ZipFile(destination) as z:after={i.filename:z.read(i) for i in z.infolist()}
    assert after=={k:v for k,v in before.items() if k not in {'Metadata/plate_13.gcode','Metadata/plate_13.gcode.md5','Metadata/slice_info.config'}}
    assert len(inspect_project(destination)['inventory']['authored_plates'])==13


def test_inspect_cli_needs_neither_configuration_nor_native_geometry(tmp_path):
    path=portable(tmp_path/'owned.3mf')
    result=subprocess.run([sys.executable,'-I','-m','bambu_workflow.cli','--config',str(tmp_path/'absent.json'),
                           'inspect-project',str(path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['inventory']['authored_object_count']==13
    assert not (tmp_path/'absent.json').exists()


def test_oversized_source_is_rejected_before_hashing_or_xml(tmp_path,monkeypatch):
    import bambu_workflow.projects as projects
    path=portable(tmp_path/'owned.3mf')
    monkeypatch.setattr(projects,'MAX_SOURCE_BYTES',10)
    monkeypatch.setattr(projects,'classify_3mf',lambda *a:pytest.fail('Reject size before archive parsing'))
    with pytest.raises(ValueError,match='no larger'):
        inspect_project(path)


def test_new_geometry_does_not_silently_ignore_requested_plate_thirteen(tmp_path,monkeypatch):
    source=tmp_path/'owned.stl';source.write_bytes(b'owned fixture; mesh parsing is not reached')
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('No Studio launch for wrong plate'))
    with pytest.raises(ValueError,match='plate 1 only'):
        slice_model(source,tmp_path/'out',{'model':'p2s'},{'plate':13})
