"""Local lab setup never supplies physical print authorization or leaks device facts."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import pytest

from bambu_workflow import cli
from bambu_workflow.config import Config
from bambu_workflow.hardware import stock_nozzle_facts
from bambu_workflow.onboarding import lab_check, write_report


@pytest.fixture
def lab(tmp_path, monkeypatch):
    executable=tmp_path/'private-user'/'Studio.exe'
    executable.parent.mkdir()
    executable.write_text('Synthetic executable; never launched')
    profiles=tmp_path/'private-user'/'profiles'
    for kind in ('machine','process','filament'):
        (profiles/kind).mkdir(parents=True)
    machine='Bambu Lab P2S 0.4 nozzle'
    process='0.20mm Standard synthetic'
    values={
        'machine': {'name':machine,'printer_model':'Bambu Lab P2S','nozzle_diameter':['0.4'],
                    'nozzle_type':['hardened_steel'],'machine_start_gcode':'; synthetic startup',
                    'default_print_profile':process},
        'process': {'name':process,'compatible_printers':[machine]},
        'filament': {'name':'Bambu PETG HF @BBL P2S','instantiation':'true','compatible_printers':[machine],
                     'filament_type':['PETG'],'filament_id':'GFG02'}}
    for kind,data in values.items():
        (profiles/kind/(data['name']+'.json')).write_text(json.dumps(data))
    device={'id':'private-room-printer','model':'p2s','host':'192.0.2.222','serial':'22EPRIVATE123',
            'secret_ref':'private-keyring-ref','nozzle_diameter':0.4,'nozzle_type':'hardened_steel',
            'nozzle_volume_type':'Standard','nozzle':stock_nozzle_facts('p2s'),
            'nozzle_confirmed':True,'plate_type':'textured_plate','plate_confirmed':True,
            'material':'PETG','materials':[{'filament_id':1,'type':'PETG','preset':'Bambu PETG HF'}]}
    path=tmp_path/'private-user'/'config.json'
    data={'version':1,'printers':[device],'state_dir':str(tmp_path/'never-created-state'),
          'slicer':{'executable':str(executable),'profile_root':str(profiles)}}
    path.write_text(json.dumps(data))
    monkeypatch.setattr(Config,'adapter',lambda *args,**kwargs:pytest.fail('No default network access'))
    return path,data


def result(report,code):
    return next(c['result'] for c in report['checks'] if c['code']==code)


def test_offline_checks_real_profile_metadata_without_touching_device_or_state(lab,monkeypatch):
    path,data=lab
    monkeypatch.setattr('subprocess.Popen',lambda *a,**k:pytest.fail('No application launches'))
    monkeypatch.setattr(Config,'secret',lambda *a,**k:pytest.fail('No keyring reads offline'))
    before=path.read_bytes()
    report=lab_check(path)
    assert report['status']=='local_checks_passed'
    assert report['ready_for_preparation'] is True
    assert report['printing_authorized'] is False and report['print_started'] is False
    assert report['printer_contacted'] is False
    assert result(report,'installed_profiles')=='pass'
    assert result(report,'live_device')=='not_checked'
    assert not Path(data['state_dir']).exists()
    assert path.read_bytes()==before


def test_shareable_report_uses_allowlist_not_redacting_raw_status(lab,monkeypatch):
    path,data=lab
    marker='private-status-secret-C:\\Users\\Someone\\design.3mf'
    status={'received_at':time.time(),'identity_verified':True,'model':'p2s','gcode_state':'IDLE',
            'firmware':marker,'gcode_file':marker,'raw':{'sdcard':True,'hms':[],'print_error':0,'sn':data['printers'][0]['serial']}}
    class Device:
        def snapshot(self):return status
    monkeypatch.setattr(Config,'adapter',lambda *a:Device())
    report=lab_check(path,online=True)
    payload=json.dumps(report)
    for private in (marker,str(path),data['printers'][0]['serial'],data['printers'][0]['host'],data['printers'][0]['id'],'private-user','private-keyring-ref'):
        assert private not in payload
    assert report['mode']=='online_read_only' and report['printer_contacted'] is True
    assert report['live']['command_permission']=='not_tested'
    assert report['printing_authorized'] is False


def test_live_exception_text_never_leaves_report(lab,monkeypatch):
    path,_=lab
    def broken(*args):raise RuntimeError('PASSWORD-should-not-be-shared C:/Users/Private 192.0.2.222')
    monkeypatch.setattr(Config,'adapter',broken)
    report=lab_check(path,online=True)
    assert report['status']=='blocked'
    assert 'PASSWORD' not in json.dumps(report)
    assert 'C:/' not in json.dumps(report)


def test_missing_fault_telemetry_never_passes_as_fault_free(lab,monkeypatch):
    path,_=lab
    class Device:
        def snapshot(self):return {'received_at':time.time(),'identity_verified':True,'model':'p2s','gcode_state':'IDLE','raw':{}}
    monkeypatch.setattr(Config,'adapter',lambda *a:Device())
    assert result(lab_check(path,online=True),'printer_faults')=='blocked'


def test_missing_and_malformed_configuration_are_shareable(tmp_path):
    path=tmp_path/'private-name-config.json'
    report=lab_check(path)
    assert report['status']=='blocked' and report['printer_contacted'] is False
    path.write_text('invalid private123')
    report=lab_check(path)
    assert report['status']=='blocked'
    assert 'private123' not in json.dumps(report) and str(path) not in json.dumps(report)


def test_multi_printer_needs_explicit_selection_even_for_online(lab):
    path,data=lab
    second=deepcopy(data['printers'][0]);second.update(id='other',serial='22ESECONDPRIVATE')
    data['printers'].append(second);path.write_text(json.dumps(data))
    assert result(lab_check(path,online=True),'printer_selection')=='blocked'
    assert lab_check(path,printer_id='other')['printer']['configured_device_number']==2
    assert result(lab_check(path,printer_id='not-configured'),'printer_selection')=='blocked'


def test_unknown_exact_preset_is_blocked_without_substitution(lab):
    path,data=lab
    data['printers'][0]['materials'][0]['preset']='Uninstalled PETG product'
    path.write_text(json.dumps(data))
    report=lab_check(path)
    assert result(report,'installed_profiles')=='blocked'
    assert not report['ready_for_preparation']


@pytest.mark.parametrize('state',['RUNNING','PAUSE','FAILED','PREPARE'])
def test_online_report_never_clears_busy_or_faulted_device(lab,monkeypatch,state):
    path,_=lab
    class Device:
        def snapshot(self):return {'received_at':time.time(),'identity_verified':True,'model':'p2s','gcode_state':state,'raw':{}}
    monkeypatch.setattr(Config,'adapter',lambda *a:Device())
    assert result(lab_check(path,online=True),'printer_idle')=='blocked'


def test_report_writer_does_not_replace_private_configuration_or_existing_file(lab):
    path,_=lab
    original=path.read_bytes()
    with pytest.raises(FileExistsError):write_report(path,lab_check(path))
    assert path.read_bytes()==original
    output=path.parent/'lab-report.json'
    write_report(output,lab_check(path))
    assert json.loads(output.read_text())['printing_authorized'] is False


def test_material_setup_separates_petg_hf_product_from_family(monkeypatch):
    answers=iter(['PETG HF','Bambu PETG HF'])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    assert cli._material_setup()==('PETG',[{'filament_id':1,'type':'PETG','preset':'Bambu PETG HF'}])


@pytest.mark.parametrize('answers',[['Bambu something'],['PETG',''],['PETG','C:/private/preset.json']])
def test_material_setup_rejects_unknown_family_or_unidentified_product(monkeypatch,answers):
    values=iter(answers)
    monkeypatch.setattr('builtins.input',lambda _:next(values))
    with pytest.raises(ValueError):cli._material_setup()


def test_file_only_configure_does_not_require_blender(lab,monkeypatch):
    path,data=lab
    destination=path.parent/'new.json'
    answers=iter(['p2s','new-p2s','printer.example.invalid','22ENEW','0.4','',
                  'textured_plate','PETG HF','Bambu PETG HF',data['slicer']['executable'],''])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    monkeypatch.setattr('getpass.getpass',lambda _:'local-only-secret')
    import keyring
    stored=[]
    monkeypatch.setattr(keyring,'set_password',lambda *a:stored.append(a))
    monkeypatch.setattr(keyring,'get_keyring',lambda:object())
    cli.configure(destination)
    saved=json.loads(destination.read_text())
    assert 'blender' not in saved
    assert saved['printers'][0]['material']=='PETG'
    assert saved['printers'][0]['materials'][0]['preset']=='Bambu PETG HF'
    assert 'local-only-secret' not in destination.read_text()
    assert stored[0][1] != 'new-p2s'


def test_onboard_preserves_existing_configuration_and_other_codex_servers(lab,tmp_path,monkeypatch):
    path,_=lab
    home=tmp_path/'codex';home.mkdir()
    config=home/'config.toml';config.write_text('[mcp_servers.other]\ncommand="other-program"\n')
    monkeypatch.setenv('CODEX_HOME',str(home))
    monkeypatch.setattr('builtins.input',lambda _:pytest.fail('Existing config must not be replaced'))
    before=path.read_bytes()
    report=cli.onboard(path)
    assert report['mcp_registered'] and not report['configuration_created']
    assert path.read_bytes()==before and 'other-program' in config.read_text()
    assert cli.onboard(path)['mcp_registered']
    assert not report['printer_contacted'] and not report['print_started']


def test_conflicting_registration_is_preserved(lab,tmp_path,monkeypatch):
    path,_=lab
    home=tmp_path/'codex';home.mkdir()
    target=home/'config.toml';target.write_text('[mcp_servers.bambu_workflow]\ncommand="different-runtime"\n')
    monkeypatch.setenv('CODEX_HOME',str(home))
    before=target.read_bytes();private=path.read_bytes()
    with pytest.raises(ValueError,match='different'):
        cli.onboard(path)
    assert target.read_bytes()==before and path.read_bytes()==private


def test_fresh_onboard_requires_interactive_terminal_before_configuration(tmp_path,monkeypatch):
    destination=tmp_path/'never-created-config.json'
    monkeypatch.setattr(sys.stdin,'isatty',lambda:False)
    monkeypatch.setattr(cli,'configure',lambda *a,**k:pytest.fail('No credentials may be collected without a terminal'))
    with pytest.raises(ValueError,match='interactive local terminal'):
        cli.onboard(destination)
    assert not destination.exists()


def test_hidden_prompt_refuses_getpass_echo_fallback(monkeypatch):
    import warnings
    def fallback(_):
        warnings.warn('Password input may be echoed',cli.getpass.GetPassWarning)
        pytest.fail('Echo fallback must stop before reading input')
    monkeypatch.setattr('getpass.getpass',fallback)
    with pytest.raises(ValueError,match='echoed input is refused'):
        cli._access_code_prompt()


def test_registration_preserves_exact_original_bytes_in_backup_and_ignores_fixed_temp_path(lab,tmp_path,monkeypatch):
    path,_=lab
    home=tmp_path/'codex';home.mkdir()
    target=home/'config.toml'
    original=b'[mcp_servers.other]\r\ncommand="other-program"\r\n'
    target.write_bytes(original)
    fixed=target.with_suffix('.bambu-workflow.tmp');fixed.write_text('unrelated data')
    monkeypatch.setenv('CODEX_HOME',str(home))
    installed=cli.install_codex(Config(path))
    assert installed['server']=='bambu_workflow'
    backups=list(home.glob('config.toml.bambu-workflow-backup-*'))
    assert len(backups)==1 and backups[0].read_bytes()==original
    assert target.read_bytes().startswith(original)
    assert fixed.read_text()=='unrelated data'


def test_simultaneous_registration_keeps_other_servers_and_is_idempotent(lab,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    path,_=lab
    home=tmp_path/'codex';home.mkdir()
    target=home/'config.toml';target.write_text('[mcp_servers.other]\ncommand="other-program"\n')
    monkeypatch.setenv('CODEX_HOME',str(home))
    config=Config(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:cli.install_codex(config),range(2)))
    assert all(r['configured'] for r in responses)
    assert sum(bool(r.get('already_present')) for r in responses)==1
    assert target.read_text().count('[mcp_servers.bambu_workflow]')==1


def test_cli_lab_check_succeeds_as_report_with_missing_configuration(tmp_path,monkeypatch,capsys):
    path=tmp_path/'missing.json';output=tmp_path/'report.json'
    monkeypatch.setattr(sys,'argv',['bambu-workflow','--config',str(path),'lab-check','--output',str(output)])
    cli.main()
    report=json.loads(capsys.readouterr().out)
    assert report['status']=='blocked' and report==json.loads(output.read_text())
    assert not path.exists()
