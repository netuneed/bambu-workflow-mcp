"""A selected plate belongs to one durable job, without global or physical changes."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import zipfile

import pytest

from bambu_workflow import cli, server
from bambu_workflow.config import Config
from bambu_workflow.workflow import Workflow


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path=tmp_path/'config.json'
    path.write_text(json.dumps({'version':1,'state_dir':str(tmp_path/'state'),
        'printers':[{'id':'p2s','model':'p2s','host':'printer.example.invalid','serial':'22EPLATETEST',
            'access_code_env':'NO_PLATE_TEST_SECRET','nozzle_diameter':0.4,'nozzle_type':'hardened_steel',
            'plate_type':'textured_plate','plate_confirmed':True,'material':'PETG'}],
        'slicer':{'plate':5,'profiles':{'filament':'owned preset path'}}}),encoding='utf-8')
    config=Config(path)
    spawned=[];snapshots=[]
    monkeypatch.setattr(Workflow,'spawn',lambda self,jid,action:spawned.append((jid,action)))
    class Device:
        def snapshot(self):
            snapshots.append(True)
            return {'model':'p2s','raw':{'vt_tray':{'tray_type':'PETG'}}}
    monkeypatch.setattr(Config,'adapter',lambda *a:Device())
    return config,spawned,snapshots


def sliced_file(path):
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('Metadata/project_settings.config',json.dumps({'filament_colour':['#FFFFFF'],
            'filament_type':['PETG'],'nozzle_diameter':['0.4']}))
        for plate in (1,5,13):z.writestr(f'Metadata/plate_{plate}.gcode','; owned placeholder, not executable acceptance')
    return path


@pytest.mark.parametrize('plate',[0,-1,True,False,1.0,13.0,'13',[],{}])
def test_invalid_plate_is_rejected_before_worker_or_device(setup,plate):
    config,spawned,snapshots=setup
    workflow=Workflow(config)
    with pytest.raises(ValueError,match='positive one-based integer'):
        workflow.submit('invalid-plate','p2s',source='file',path='unused.3mf',plate=plate)
    assert not spawned and not snapshots and not workflow.store.list()


@pytest.mark.parametrize('plate,global_plate,expected',[(13,5,13),(None,13,13),(1,13,1)])
def test_selected_sliced_plate_reaches_only_this_job_without_config_mutation(setup,tmp_path,monkeypatch,plate,global_plate,expected):
    config,_,_=setup
    config.data['slicer']['plate']=global_plate
    original=deepcopy(config.data);private_bytes=config.path.read_bytes()
    source=sliced_file(tmp_path/'owned.3mf')
    captured=[];inspected=[]
    def slice_owned(source,folder,printer,slicer,progress=None):
        captured.append(deepcopy(slicer))
        output=Path(folder)/'prepared.gcode.3mf'
        output.write_bytes(source.read_bytes())
        return {'print_path':str(output),'plate':slicer.get('plate',1),'source_kind':'sliced'}
    monkeypatch.setattr('bambu_workflow.slicer.slice_model',slice_owned)
    monkeypatch.setattr('bambu_workflow.slicer.inspect_print',lambda path,printer,plate=1:inspected.append(plate) or {'gcode_md5':'a'*32})
    monkeypatch.setattr('bambu_workflow.geometry.analyze_mesh',lambda *a,**k:pytest.fail('Sliced files must not be flattened'))
    monkeypatch.setattr('bambu_workflow.materials.bind_materials',lambda status,printer,materials:deepcopy(materials))
    monkeypatch.setattr('bambu_workflow.preflight.assessed',lambda *a:{'level':'clear','can_print':True,'findings':[]})
    materials=[{'filament_id':1,'type':'PETG','preset':'Generic PETG','feed':{'kind':'external'}}]
    workflow=Workflow(config)
    result=workflow.submit('sliced-job','p2s',source='file',path=source,materials=materials,plate=plate)
    workflow.prepare(result['job_id'])
    job=workflow.store.get(result['job_id'])
    assert job['state']=='prepared',job.get('error')
    assert captured[0]['plate']==expected and inspected==[expected]
    assert job['manifest']['plate']==expected
    assert config.data==original and config.path.read_bytes()==private_bytes
    assert job['request'].get('plate')==plate


@pytest.mark.parametrize('plate,global_plate',[(13,1),(None,13)])
def test_non_sliced_plate_above_one_blocks_before_device_or_mesh(setup,tmp_path,monkeypatch,plate,global_plate):
    config,_,snapshots=setup
    config.data['slicer']['plate']=global_plate
    source=tmp_path/'owned.stl';source.write_bytes(b'not parsed because selection is invalid')
    monkeypatch.setattr('bambu_workflow.geometry.analyze_mesh',lambda *a:pytest.fail('Do not parse mesh for an unsupported plate'))
    monkeypatch.setattr('bambu_workflow.slicer.slice_model',lambda *a,**k:pytest.fail('Do not invoke native slicing'))
    workflow=Workflow(config)
    result=workflow.submit('unsupported-plate','p2s',source='file',path=source,plate=plate)
    workflow.prepare(result['job_id'])
    job=workflow.store.get(result['job_id'])
    assert job['state']=='failed' and 'requires a sliced 3MF' in job['error']['message']
    assert not snapshots


def test_plate_selection_is_part_of_durable_request_identity(setup):
    config,spawned,_=setup
    workflow=Workflow(config)
    first=workflow.submit('same-request','p2s',source='file',path='owned.3mf',plate=13)
    duplicate=workflow.submit('same-request','p2s',source='file',path='owned.3mf',plate=13)
    assert duplicate['duplicate'] and duplicate['job_id']==first['job_id'] and len(spawned)==1
    with pytest.raises(ValueError,match='different request'):
        workflow.submit('same-request','p2s',source='file',path='owned.3mf',plate=1)


def test_explicit_first_plate_avoids_inheriting_another_sliced_selection(setup,tmp_path,monkeypatch):
    config,_,_=setup
    config.data['slicer']['plate']=13
    original=deepcopy(config.data)
    source=tmp_path/'owned.stl';source.write_bytes(b'owned synthetic geometry stand-in')
    captured=[]
    def slice_owned(source,folder,printer,slicer,progress=None):
        captured.append(deepcopy(slicer))
        output=Path(folder)/'prepared.gcode.3mf';output.write_bytes(b'owned output')
        return {'print_path':str(output),'plate':1,'source_kind':'geometry'}
    monkeypatch.setattr('bambu_workflow.slicer.slice_model',slice_owned)
    monkeypatch.setattr('bambu_workflow.slicer.inspect_print',lambda *a,**k:{'gcode_md5':'a'*32})
    monkeypatch.setattr('bambu_workflow.geometry.analyze_mesh',lambda *a:{'dimensions_mm':[1,1,1]})
    monkeypatch.setattr('bambu_workflow.preflight.assessed',lambda *a:{'level':'clear','can_print':True,'findings':[]})
    workflow=Workflow(config)
    result=workflow.submit('first-plate','p2s',source='file',path=source,plate=1)
    workflow.prepare(result['job_id'])
    job=workflow.store.get(result['job_id'])
    assert job['state']=='prepared',job.get('error')
    assert job['manifest']['plate']==1 and captured[0].get('plate',1)==1
    assert config.data==original


@pytest.mark.parametrize('tool',['prepare_print','print_model'])
def test_tool_forwards_plate_at_end_without_changing_other_arguments(monkeypatch,tool):
    class FakeWorkflow:
        def submit(self,*args,**kwargs):return {'plate':kwargs.get('plate'),'print_now':kwargs.get('print_now',False)}
    monkeypatch.setattr(server,'workflow',lambda:FakeWorkflow())
    result=getattr(server,tool)('owned-request',source='file',path='owned.3mf',plate=13)
    assert result=={'plate':13,'print_now':tool=='print_model'}


@pytest.mark.parametrize('command',['prepare','print'])
def test_cli_passes_job_plate(setup,monkeypatch,capsys,command):
    config,_,_=setup
    captured=[]
    monkeypatch.setenv('BAMBU_WORKFLOW_CONFIG',str(config.path))
    monkeypatch.setattr(sys,'argv',['bambu-workflow','--config',str(config.path),command,
        '--printer','p2s','--request-id','owned-request','--file','owned.3mf','--plate','13'])
    monkeypatch.setattr(Workflow,'submit',lambda self,*args,**kwargs:captured.append(kwargs) or {'job_id':'owned-job'})
    cli.main()
    assert json.loads(capsys.readouterr().out)['job_id']=='owned-job'
    assert captured[0]['plate']==13 and captured[0]['print_now']==(command=='print')
