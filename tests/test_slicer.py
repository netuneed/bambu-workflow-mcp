import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import zipfile

import trimesh

from bambu_workflow.slicer import inspect_print, resolve_profile, slice_model, _check_executable_limits, _project_overrides, validate_process_overrides, _whole_print_extrusion_bounds, _workspace, _profile_paths, _validate_profiles, _configure_nozzle, discover_slicer_executable, discover_profile_root
from bambu_workflow.models import get_model, list_models


class SlicerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.printer = {"model": "a1mini", "nozzle_diameter": .4, "nozzle_type": "stainless_steel", "material": "PETG", "plate_type": "textured_plate"}

    def tearDown(self):
        self.tmp.cleanup()

    def sliced(self, model="Bambu Lab A1 mini", nozzle=.4, md5=True, outside=False, bed_temp=70, body=None):
        p = self.root / "test.gcode.3mf"
        settings = {"printer_model": model, "nozzle_diameter": [str(nozzle)], "nozzle_type": ["stainless_steel"],
                    "curr_bed_type": "Textured PEI Plate", "filament_type": ["PETG"], "filament_colour": ["#000000"]}
        if body is None:
            body = ('G90\nM83\n; FEATURE: Custom\nG1 X-13 Y90 Z0.2 E1\n'
                    '; CHANGE_LAYER\n; FEATURE: Outer wall\n; LINE_WIDTH: 0.4\n'
                    'G1 X50 Y50 Z0.2 F1200\nG1 X51 Y50 E1\n')
        gcode = (f'; BambuStudio test\n; printer_model = {model}\n; nozzle_diameter = {nozzle}\n'
                 '; curr_bed_type = Textured PEI Plate\n; nozzle_type = stainless_steel\n; filament_type = PETG\n; filament_colour = #000000\n; max_z_height: 1.2\n'
                 f'; EXECUTABLE_BLOCK_START\nM104 S255\nM140 S{bed_temp}\n{body}; EXECUTABLE_BLOCK_END\n').encode()
        xml = f'''<config><plate><metadata key="index" value="1"/><metadata key="outside" value="{str(outside).lower()}"/>
        <metadata key="prediction" value="120"/><metadata key="weight" value=".5"/><metadata key="nozzle_diameters" value="{nozzle}"/>
        <filament id="1" type="PETG"/></plate></config>'''
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("Metadata/project_settings.config", json.dumps(settings))
            z.writestr("Metadata/plate_1.gcode", gcode)
            z.writestr("Metadata/plate_1.gcode.md5", hashlib.md5(gcode).hexdigest() if md5 else "0"*32)
            z.writestr("Metadata/slice_info.config", xml)
            z.writestr("Metadata/plate_1.json", '{"bbox_all":[50,50,62,62]}')
        return p

    def test_profile_inheritance_and_includes(self):
        for name, data in {"base": {"name": "base", "speed": "20", "nozzle_diameter": [".4"]},
                           "template": {"machine_start_gcode": "A1 mini"},
                           "final": {"name": "final", "inherits": "base", "include": ["template"], "speed": "30"}}.items():
            (self.root / (name + ".json")).write_text(json.dumps(data))
        r = resolve_profile(self.root / "final.json", self.root)
        self.assertEqual(r["speed"], "30")
        self.assertEqual(r["machine_start_gcode"], "A1 mini")
        self.assertNotIn("inherits", r)
        self.assertNotIn("include", r)

    def test_macos_bundle_executable_and_both_resource_layouts(self):
        for index,folder in enumerate(('Resources','resources')):
            app=self.root/f'{index}-BambuStudio.app'
            executable=app/'Contents/MacOS/BambuStudio'
            executable.parent.mkdir(parents=True);executable.write_text('fixture')
            root=app/'Contents'/folder/'profiles/BBL'
            for kind in ('machine','process','filament'):(root/kind).mkdir(parents=True)
            self.assertEqual(discover_slicer_executable(app),executable.resolve())
            self.assertTrue(discover_profile_root(executable).samefile(root))
        self.assertIsNone(discover_slicer_executable(self.root/'missing.app'))

    def test_registry_profile_discovery_uses_defaults_and_nested_filaments(self):
        exe=self.root/'BambuStudio';exe.write_text('fixture')
        for spec in list_models():
            with self.subTest(model=spec['id']):
                root=self.root/spec['id']/'BBL'
                for kind in ('machine','process','filament'):(root/kind).mkdir(parents=True)
                name=spec['profile_model_name']+' 0.4 nozzle'
                process_name='0.20mm Standard fixture '+spec['id']
                machine={'name':name,'printer_model':spec['profile_model_name'],'nozzle_diameter':['.4']*spec['tool_count'],
                         'nozzle_type':[spec['default_nozzle_type']]*spec['tool_count'],
                         'machine_start_gcode':'; resolved fixture startup','default_print_profile':process_name}
                process={'name':process_name,'compatible_printers':[name]}
                filament={'name':'Generic PETG fixture @'+spec['id'],'filament_type':['PETG'],'compatible_printers':[name],'instantiation':'true'}
                filament['name']='Generic PETG @'+spec['id']
                (root/'machine'/(name+'.json')).write_text(json.dumps(machine))
                (root/'process'/(process_name+'.json')).write_text(json.dumps(process))
                filament_dir=root/'filament'/('P1P' if spec['id']=='p1p' else 'stock');filament_dir.mkdir()
                (filament_dir/'Generic PETG.json').write_text(json.dumps(filament))
                p={'model':spec['id'],'nozzle_diameter':.4,'nozzle_type':spec['default_nozzle_type'],'material':'PETG'}
                _,paths=_profile_paths(p,{'executable':str(exe),'profile_root':str(root)})
                self.assertTrue(paths['filament'].parent.samefile(filament_dir))
                _validate_profiles({'machine':machine,'process':process,'filament':filament},p)

    def test_model_thermal_limits_do_not_reuse_a1mini_limits(self):
        code='M104 S315\nM140 S105\nG1 X300 Y100 Z1\n'
        self.assertEqual(_check_executable_limits(code,'h2s')['maximum_nozzle_target_c'],315)
        self.assertEqual(_check_executable_limits('M104 S315\nM140 S100\nG1 X200 Y100 Z1\n','x1e')['maximum_nozzle_target_c'],315)
        with self.assertRaisesRegex(ValueError,'nozzle target'):
            _check_executable_limits(code,'a1mini')
        with self.assertRaisesRegex(ValueError,'bed target'):
            _check_executable_limits('M104 S255\nM140 S105\nG1 X100 Y100 Z1\n','p1s')

    def test_profile_exclusions_and_nonrectangular_area_check_actual_paths(self):
        body='G90\nM83\n; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X30 Y20 Z.2\nG2 X30 Y60 I0 J20 E1\n'
        workspace=_workspace({'model':'p1s'},{'bed_exclude_area':['0x0','18x0','18x28','0x28']})
        for checked in (workspace,_workspace({'model':'p1s'})):
            with self.assertRaisesRegex(ValueError,'reserved bed-exclusion'):
                _whole_print_extrusion_bounds(body,.4,workspace=checked)
        nonrect=_workspace({'model':'a1mini'},{'printable_area':['0x0','180x0','180x80','80x80','80x180','0x180']})
        # Both endpoints are inside an L-shaped bed, but their connecting move crosses its cutout.
        diagonal='G90\nM83\n; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X50 Y150 Z.2\nG1 X150 Y50 E1\n'
        with self.assertRaisesRegex(ValueError,'printable.area'):
            _whole_print_extrusion_bounds(diagonal,.4,workspace=nonrect)

    def test_dual_tool_workspace_respects_offset_and_individual_height(self):
        settings={'printable_area':['0x0','350x0','350x320','0x320'],'printable_height':'325',
                  'extruder_printable_area':['0x0,325x0,325x320,0x320','25x0,350x0,350x320,25x320'],
                  'extruder_printable_height':['320','325']}
        left=_workspace({'model':'h2d'},settings,1);right=_workspace({'model':'h2d'},settings,2)
        body='G90\nM83\n; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X330 Y100 Z.2\nG1 X340 E1\n'
        with self.assertRaisesRegex(ValueError,"selected tool's XY"):
            _whole_print_extrusion_bounds(body,.4,workspace=left)
        self.assertEqual(_whole_print_extrusion_bounds(body,.4,workspace=right)['checked_extrusion_moves'],1)
        tall='G90\nM83\n; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X100 Y100 Z321\nG1 X101 E1\n'
        with self.assertRaisesRegex(ValueError,'Z build height'):
            _whole_print_extrusion_bounds(tall,.4,workspace=left)

    def test_dual_prepared_file_mapping_and_unsupported_modes_are_explicit(self):
        base=self.sliced(model='Bambu Lab H2D')
        with zipfile.ZipFile(base) as z:members={name:z.read(name) for name in z.namelist()}
        settings=json.loads(members['Metadata/project_settings.config'])
        settings.update(nozzle_diameter=['.4','.4'],nozzle_type=['hardened_steel','hardened_steel'],
                        nozzle_volume_type=['Standard','Standard'],filament_map=['1'],filament_nozzle_map=['1'],
                        printable_height='325',printable_area=['0x0','350x0','350x320','0x320'],
                        extruder_printable_area=['0x0,325x0,325x320,0x320','25x0,350x0,350x320,25x320'],
                        extruder_printable_height=['320','325'])
        gcode=members['Metadata/plate_1.gcode'].decode().replace('; nozzle_diameter = 0.4','; nozzle_diameter = 0.4,0.4').replace('; nozzle_type = stainless_steel','; nozzle_type = hardened_steel;hardened_steel').encode()
        members['Metadata/plate_1.gcode']=gcode
        members['Metadata/plate_1.gcode.md5']=hashlib.md5(gcode).hexdigest().encode()
        xml=members['Metadata/slice_info.config'].decode().replace('</plate>','<nozzle id="0" extruder_id="1" volume_type="Standard"/></plate>')
        p={**self.printer,'model':'h2d','nozzle_type':'hardened_steel'}
        def write_case(name,config,info):
            path=self.root/(name+'.3mf')
            with zipfile.ZipFile(path,'w') as z:
                for member,content in members.items():
                    if member=='Metadata/project_settings.config':content=json.dumps(config).encode()
                    elif member=='Metadata/slice_info.config':content=info.encode()
                    z.writestr(member,content)
            return path
        result=inspect_print(write_case('valid',settings,xml),p)
        self.assertEqual(result['used_tool_ids'],[1])
        self.assertFalse(result['launch_supported'])
        with self.assertRaisesRegex(ValueError,'mappings disagree'):
            inspect_print(write_case('wrong-map',{**settings,'filament_map':['2']},xml),p)
        with self.assertRaisesRegex(ValueError,'Multiple/unknown used-tool'):
            inspect_print(write_case('multiple-tools',settings,xml.replace('</plate>','<nozzle id="1" extruder_id="2" volume_type="Standard"/></plate>')),p)
        with self.assertRaisesRegex(ValueError,'nozzle diameter'):
            inspect_print(write_case('mixed-diameters',{**settings,'nozzle_diameter':['.4','.6']},xml),p)
        with self.assertRaisesRegex(ValueError,'high-flow'):
            inspect_print(write_case('high-flow',{**settings,'nozzle_volume_type':['High Flow','High Flow']},xml),p)

    def test_cyclic_inheritance_is_rejected(self):
        (self.root / "loop.json").write_text('{"inherits":"loop"}')
        with self.assertRaisesRegex(ValueError, "Cyclic"):
            resolve_profile(self.root / "loop.json", self.root)

    def test_project_global_settings_are_retained(self):
        p = self.root / "source-project.3mf"
        values = {"printer_model": "Bambu Lab A1 mini", "nozzle_diameter": [".4"], "filament_type": ["PETG"], "filament_colour": ["#000000"],
                  "wall_loops": "5", "enable_support": "1", "nozzle_temperature": ["245"]}
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("3D/3dmodel.model", "<model/>")
            z.writestr("Metadata/project_settings.config", json.dumps(values))
        profiles = {"process": {"wall_loops": "2", "enable_support": "0"}, "filament": {"nozzle_temperature": ["255"]}}
        _project_overrides(p, profiles, self.printer, {})
        self.assertEqual(profiles["process"]["wall_loops"], "5")
        self.assertEqual(profiles["process"]["enable_support"], "1")
        self.assertEqual(profiles["filament"]["nozzle_temperature"], ["245"])
        with zipfile.ZipFile(p, "a") as z:
            z.writestr("Metadata/model_settings.config", '<config><object><metadata key="wall_loops" value="4"/></object></config>')
        with self.assertRaisesRegex(ValueError, "per-object setting"):
            _project_overrides(p, profiles, self.printer, {})

    def test_executable_limits_reject_dangerous_or_unresolved_values(self):
        base = "M104 S255\nM140 S70\nG1 X50 Y50 Z1 E1\n"
        r = _check_executable_limits(base, "a1mini")
        self.assertEqual(r["maximum_bed_target_c"], 70)
        for extra in ("M104 S301", "M140 Snan", "M140 S81", "G1 X1000", "G1 Ynan", "M109 S{temperature}"):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                _check_executable_limits(base + extra, "a1mini")

    def test_process_override_whitelist_and_machine_layer_bounds(self):
        values = {"layer_height": .16, "wall_loops": 4, "sparse_infill_density": "30%", "sparse_infill_pattern": "gyroid",
                  "top_shell_layers": 6, "bottom_shell_layers": 5, "enable_support": False}
        r = validate_process_overrides(values, self.printer, {"min_layer_height":[".08"], "max_layer_height":[".28"]})
        self.assertEqual(r["layer_height"], "0.16")
        self.assertEqual(r["wall_loops"], "4")
        self.assertEqual(r["enable_support"], "0")
        invalid = [{"machine_start_gcode":"G1 X1000"}, {"layer_height": float("nan")}, {"layer_height": .3},
                   {"wall_loops": True}, {"wall_loops": 9}, {"sparse_infill_density":"101%"},
                   {"sparse_infill_pattern": "arbitrary"}, {"top_shell_layers":2}, {"enable_support":1}]
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_process_overrides(args, self.printer)
        with self.assertRaises(ValueError):
            validate_process_overrides({"layer_height": .16}, {"nozzle_diameter":.2})

    def test_selected_plate_and_checksum(self):
        p = self.sliced()
        r = inspect_print(p, self.printer, 1)
        self.assertEqual(r["plate"], 1)
        self.assertEqual(r["model"], "a1mini")
        self.assertEqual(r["estimates"]["time_seconds"], 120)
        with self.assertRaisesRegex(ValueError, "one-based"):
            inspect_print(p, self.printer, 0)
        with self.assertRaisesRegex(ValueError, "no sliced G-code"):
            inspect_print(p, self.printer, 2)
        with self.assertRaisesRegex(ValueError, "checksum"):
            inspect_print(self.sliced(md5=False), self.printer)

    def test_upper_layer_outside_ignores_misleading_first_layer_metadata(self):
        base = ('G90\nM83\n; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X50 Y50 Z0.2\nG1 X51 E1\n'
                '; CHANGE_LAYER\nG1 Z10\n')
        with self.assertRaisesRegex(ValueError, "Whole printed extrusion exceeds.*XY"):
            inspect_print(self.sliced(body=base+'G1 X181 E1\n'), self.printer)
        with self.assertRaisesRegex(ValueError, "Whole printed extrusion exceeds.*Z"):
            inspect_print(self.sliced(body=base+'G1 Z181 E1\n'), self.printer)

    def test_sliced_plate_thirteen_keeps_all_authored_layouts_and_archive_bytes(self):
        import xml.etree.ElementTree as ET
        from bambu_workflow.artifacts import classify_3mf
        source=self.sliced()
        with zipfile.ZipFile(source) as z:
            members={n:z.read(n) for n in z.namelist()}
        root=ET.fromstring(members['Metadata/slice_info.config'])
        plate=ET.fromstring(ET.tostring(root.find('plate')))
        next(m for m in plate.findall('metadata') if m.get('key')=='index').set('value','13')
        root.append(plate)
        members['Metadata/slice_info.config']=ET.tostring(root)
        members['Metadata/plate_13.gcode']=members['Metadata/plate_1.gcode']
        members['Metadata/plate_13.gcode.md5']=members['Metadata/plate_1.gcode.md5']
        members['Metadata/plate_13.json']=members['Metadata/plate_1.json']
        authored='<config>'+''.join(f'<plate><metadata key="plater_id" value="{i}"/><model_instance><metadata key="object_id" value="{i}"/></model_instance></plate>' for i in range(1,14))+'</config>'
        members['Metadata/model_settings.config']=authored.encode()
        with zipfile.ZipFile(source,'w') as z:
            for name,data in members.items():z.writestr(name,data)
        before=source.read_bytes()
        result=slice_model(source,self.root/'selected13',self.printer,{'plate':13})
        self.assertEqual(result['plate'],13)
        self.assertEqual(len(result['authored_plates']),13)
        self.assertEqual(Path(result['print_path']).read_bytes(),before)
        self.assertEqual(len(classify_3mf(Path(result['project_path']))['authored_plates']),13)
        with zipfile.ZipFile(result['project_path']) as z:
            self.assertEqual(z.read('Metadata/model_settings.config'),members['Metadata/model_settings.config'])

    def test_arc_extrema_and_width_are_checked_between_inside_endpoints(self):
        base = 'G90\nM83\n; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X170 Y90 Z0.2\n'
        # Endpoints remain at X170, but a full I/J circle reaches X190.
        for command in ('G2', 'G3'):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "Whole printed extrusion exceeds.*XY"):
                inspect_print(self.sliced(body=base+command+' X170 Y90 I10 J0 E1\n'), self.printer)
        # A legal centerline at X179.9 still places half of a 0.4-mm bead outside the bed.
        with self.assertRaisesRegex(ValueError, "Whole printed extrusion exceeds.*XY"):
            inspect_print(self.sliced(body=base+'G1 X179.9 E1\n'), self.printer)

    def test_startup_and_explicit_custom_purge_do_not_define_model_bounds(self):
        body = ('G90\nM83\n; FEATURE: Custom\nG1 X-13 Y90 Z0.2 E1\n'
                '; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X50 Y50\nG1 X51 E1\n'
                '; FEATURE: Custom\nG1 X-13 E1\n')
        r = inspect_print(self.sliced(body=body), self.printer)
        bounds = r['executable_checks']['whole_print_extrusion']
        self.assertAlmostEqual(bounds['extrusion_path_bounds_mm'][0][0], 50)
        self.assertEqual(bounds['ignored_custom_extrusions_after_layer_marker'], 1)
        self.assertIn('first-layer', r['placement_bounds_source'])

    def test_relative_motion_absolute_extrusion_and_radius_arc_modes(self):
        body = ('G90\nM82\nG92 E0\n; CHANGE_LAYER\n; FEATURE: Outer wall\n'
                'G1 X50 Y50 Z0.2\nG1 X51 E1\nG91\nG1 X1 Y1 E2\nG90\n'
                'G1 X60 Y60\nG2 X60 Y80 R10 E3\n')
        r = _whole_print_extrusion_bounds(body,.4,printer=self.printer)
        self.assertEqual(r['checked_extrusion_moves'], 3)
        self.assertEqual(r['checked_extrusion_arcs'], 1)
        self.assertGreaterEqual(r['extrusion_path_bounds_mm'][1][1],80)
        with self.assertRaisesRegex(ValueError,'coordinate offsets'):
            _whole_print_extrusion_bounds(body+'G92 X0\n',.4,printer=self.printer)
        with self.assertRaisesRegex(ValueError,'line width'):
            _whole_print_extrusion_bounds(body,.4,float('nan'),printer=self.printer)

    def test_wrong_printer_nozzle_or_placement_rejected(self):
        with self.assertRaisesRegex(ValueError, "not the selected"):
            inspect_print(self.sliced(model="Bambu Lab A1"), self.printer)
        with self.assertRaisesRegex(ValueError, "nozzle diameter"):
            inspect_print(self.sliced(nozzle=.6), self.printer)
        with self.assertRaisesRegex(ValueError, "nozzle diameter"):
            inspect_print(self.sliced(nozzle=float('nan')), self.printer)
        with self.assertRaisesRegex(ValueError, "outside"):
            inspect_print(self.sliced(outside=True), self.printer)
        with self.assertRaisesRegex(ValueError, "bed target"):
            inspect_print(self.sliced(bed_temp=100), self.printer)

    def test_slicer_failure_retains_log(self):
        root = self.root / "profiles"
        for kind in ("machine", "process", "filament"):
            (root / kind).mkdir(parents=True)
        files = {
            "machine/Bambu Lab A1 mini 0.4 nozzle.json": {"name":"Bambu Lab A1 mini 0.4 nozzle", "printer_model":"Bambu Lab A1 mini", "nozzle_diameter":[".4"], "machine_start_gcode":"A1 mini", "default_print_profile":"0.20mm Standard @BBL A1M"},
            "process/0.20mm Standard @BBL A1M.json": {"name":"0.20mm Standard @BBL A1M", "compatible_printers":["Bambu Lab A1 mini 0.4 nozzle"]},
            "filament/Generic PETG @BBL A1M.json": {"name":"Generic PETG @BBL A1M", "filament_type":["PETG"], "instantiation":"true", "compatible_printers":["Bambu Lab A1 mini 0.4 nozzle"]}}
        for name, data in files.items():
            (root / name).write_text(json.dumps(data))
        exe = self.root / "slicer.exe"
        exe.write_bytes(b"not executable; Popen mocked for failure test")
        source = self.root / "source.stl"
        trimesh.creation.box((12, 12, 2)).export(source)
        mock = MagicMock()
        mock.wait.return_value = 9
        out = self.root / "job"
        with patch("bambu_workflow.slicer.subprocess.Popen", return_value=mock):
            with self.assertRaisesRegex(RuntimeError, "exit 9"):
                slice_model(source, out, self.printer, {"path": str(exe), "profile_root": str(root)})
        self.assertTrue((out / "slicer.log").is_file())
        self.assertTrue((out / "slicer-command.json").is_file())
        # Even an overhang recommendation must not add native supports over baked fins.
        baked = self.root / 'baked'
        with patch('bambu_workflow.slicer.subprocess.Popen', return_value=mock), patch(
                'bambu_workflow.slicer.normalized_stl', return_value={'support_recommended': True}):
            with self.assertRaisesRegex(RuntimeError, 'exit 9'):
                slice_model(source, baked, self.printer, {'path': str(exe), 'profile_root': str(root),
                                                       'support_mode': 'baked_fins'})
        self.assertEqual(json.loads((baked/'resolved-process.json').read_text())['enable_support'], '0')
        with patch('bambu_workflow.slicer.normalized_stl', return_value={'support_recommended': True}):
            with self.assertRaisesRegex(ValueError, 'cannot enable native supports'):
                slice_model(source, self.root/'conflict', self.printer, {'path': str(exe), 'profile_root': str(root),
                    'support_mode': 'baked_fins', 'process_overrides': {'enable_support': True}})

    def test_unresolved_advanced_mapping_blocks_automatic_launch_inspection(self):
        source=self.sliced()
        with zipfile.ZipFile(source) as z:
            members={n:z.read(n) for n in z.namelist()}
        settings=json.loads(members['Metadata/project_settings.config'])
        settings['filament_map_2']=['999999']
        members['Metadata/project_settings.config']=json.dumps(settings).encode()
        with zipfile.ZipFile(source,'w') as z:
            for name,data in members.items():z.writestr(name,data)
        with self.assertRaisesRegex(ValueError,'unresolved nozzle-group'):
            inspect_print(source,self.printer)

    def multicolor(self):
        source = self.sliced(body='T0\nM620 S0A\nG90\nM83\n; CHANGE_LAYER\n; FEATURE: Outer wall\n'
                                 'G1 X50 Y50 Z0.2\nG1 X51 E1\nT1\nM620 S1A\nG1 X52 E1\nT65535\n')
        with zipfile.ZipFile(source) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        settings = json.loads(members['Metadata/project_settings.config'])
        settings.update(filament_type=['PETG', 'PETG'], filament_ids=['GFG02', 'GFG02'],
                        filament_colour=['#FF0000', '#0000FF'],
                        filament_settings_id=['Bambu PETG HF @BBL A1M']*2,
                        filament_map=['1', '1'], filament_nozzle_map=['1', '1'])
        members['Metadata/project_settings.config'] = json.dumps(settings).encode()
        text = members['Metadata/plate_1.gcode'].decode().replace('; filament_type = PETG\n',
                '; filament_type = PETG;PETG\n; filament_ids = GFG02;GFG02\n'
                '; filament_colour = #FF0000;#0000FF\n'
                '; filament_settings_id = "Bambu PETG HF @BBL A1M";"Bambu PETG HF @BBL A1M"\n').replace('; filament_colour = #000000\n','')
        members['Metadata/plate_1.gcode'] = text.encode()
        members['Metadata/slice_info.config'] = members['Metadata/slice_info.config'].replace(
            b'<filament id="1" type="PETG"/>',
            b'<filament id="1" type="PETG" tray_info_idx="GFG02" color="#FF0000"/>'
            b'<filament id="2" type="PETG" tray_info_idx="GFG02" color="#0000FF"/>')
        printer = {**self.printer, 'materials': [
            {'filament_id': 1, 'type': 'PETG', 'preset': 'Bambu PETG HF', 'profile_id': 'GFG02', 'color': 'FF0000FF'},
            {'filament_id': 2, 'type': 'PETG', 'preset': 'Bambu PETG HF', 'profile_id': 'GFG02', 'color': '0000FFFF'}]}
        return members, printer

    def write_members(self, name, members):
        path = self.root / (name + '.gcode.3mf')
        with zipfile.ZipFile(path, 'w') as archive:
            for key, content in members.items():
                if key == 'Metadata/plate_1.gcode.md5':
                    content = hashlib.md5(members['Metadata/plate_1.gcode']).hexdigest().encode()
                archive.writestr(key, content)
        return path

    def test_single_nozzle_two_colors_validate_full_palette_and_byte_exact_import(self):
        members, printer = self.multicolor()
        path = self.write_members('two-colors', members)
        result = inspect_print(path, printer)
        self.assertEqual(result['used_filament_ids'], [1, 2])
        self.assertEqual(result['used_tool_ids'], [1])
        self.assertEqual(result['palette'][1]['color'], '0000FFFF')
        result = slice_model(path, self.root / 'imported', printer, {'support_mode': 'manual'})
        self.assertEqual(path.read_bytes(), Path(result['print_path']).read_bytes())
        self.assertEqual(result['support_mode'], 'manual')

    def test_multicolor_rejects_spool_substitution_or_executable_palette_changes(self):
        members, printer = self.multicolor()
        path = self.write_members('two-colors', members)
        for field, wrong in [('color', '00FF00FF'), ('profile_id', 'GFG99'), ('preset', 'Generic PETG'), ('type', 'ABS')]:
            changed = {**printer, 'materials': [dict(item) for item in printer['materials']]}
            changed['materials'][1][field] = wrong
            with self.subTest(field=field), self.assertRaises(ValueError):
                inspect_print(path, changed)
        for old, new, error in [(b'T1\n', b'T2\n', 'selector'), (b'T1\nM620 S1A\n', b'', 'every used color'),
                                (b'#0000FF\n', b'#00FF00\n', 'disagree'),
                                (b'PETG;PETG', b'PETG;ABS', 'different filament material')]:
            changed = dict(members)
            changed['Metadata/plate_1.gcode'] = changed['Metadata/plate_1.gcode'].replace(old, new)
            with self.subTest(new=new), self.assertRaisesRegex(ValueError, error):
                inspect_print(self.write_members('changed', changed), printer)
        changed = dict(members)
        changed['Metadata/slice_info.config'] = changed['Metadata/slice_info.config'].replace(b'id="2" type=', b'id="3" type=')
        with self.assertRaisesRegex(ValueError, 'out-of-palette'):
            inspect_print(self.write_members('bad-id', changed), printer)

    def test_single_tool_studio_default_mapping_applies_to_remaining_colors(self):
        members, printer = self.multicolor()
        settings = json.loads(members['Metadata/project_settings.config'])
        settings.update(filament_map=['1'], filament_nozzle_map=['1'])
        members['Metadata/project_settings.config'] = json.dumps(settings).encode()
        checked = inspect_print(self.write_members('native-default-map', members), printer)
        self.assertEqual(checked['used_filament_ids'], [1, 2])
        settings['filament_nozzle_map'] = ['1', '2']
        members['Metadata/project_settings.config'] = json.dumps(settings).encode()
        with self.assertRaisesRegex(ValueError, 'physical-nozzle'):
            inspect_print(self.write_members('wrong-physical-tool', members), printer)

    def test_exact_product_and_highflow_variant_selection_with_custom_caps(self):
        profiles = {'machine': {'printer_extruder_variant': ['Direct Drive Standard', 'Direct Drive High Flow']},
                    'filament': {'filament_max_volumetric_speed': ['21', '30'],
                                 'nozzle_temperature': ['245', '245'], 'filament_flush_temp': ['270', '270']}}
        printer = {'model': 'p2s', 'nozzle_type': 'hardened_steel', 'nozzle_volume_type': 'HighFlow',
                   'nozzle': {'custom': True, 'stock_geometry_compatible': True, 'max_volumetric_speed': 15,
                              'max_temperature_c': 260}}
        _configure_nozzle(profiles, printer)
        self.assertEqual(profiles['machine']['nozzle_volume_type'], ['High Flow'])
        self.assertEqual(profiles['filament']['filament_max_volumetric_speed'], ['14.85', '14.85'])
        self.assertEqual(profiles['filament']['filament_flush_temp'], ['260', '260'])
        with self.assertRaisesRegex(ValueError, 'no compatible HighFlow'):
            _configure_nozzle({'machine': {}, 'filament': {}}, printer)

    def test_requested_petg_hf_uses_exact_compatible_product_without_generic_fallback(self):
        exe = self.root/'Studio.exe'
        exe.write_bytes(b'fixture executable never launched')
        profile_root = self.root/'stock'
        for folder in ('machine', 'process', 'filament'):
            (profile_root/folder).mkdir(parents=True)
        machine_name = 'Bambu Lab P2S 0.4 nozzle'
        machine = {'name': machine_name, 'default_print_profile': 'process'}
        (profile_root/'machine'/(machine_name+'.json')).write_text(json.dumps(machine))
        (profile_root/'process/process.json').write_text('{}')
        for name, product in [('generic', 'Generic PETG'), ('hf', 'Bambu PETG HF')]:
            (profile_root/'filament'/(name+'.json')).write_text(json.dumps({
                'name': product+' @BBL P2S 0.4 nozzle', 'filament_type': ['PETG'],
                'instantiation': 'true', 'compatible_printers': [machine_name]}))
        printer = {'model': 'p2s', 'nozzle_diameter': .4, 'material': 'PETG', 'materials': [
            {'filament_id': 1, 'type': 'PETG', 'preset': 'Bambu PETG HF'}]}
        config = {'executable': str(exe), 'profile_root': str(profile_root)}
        _, paths = _profile_paths(printer, config)
        self.assertEqual(paths['filament'].name, 'hf.json')
        (profile_root/'filament/hf.json').unlink()
        with self.assertRaisesRegex(ValueError, 'no substitute'):
            _profile_paths(printer, config)

    def test_support_and_brim_overrides_are_bounded_and_manual_requires_project(self):
        values = validate_process_overrides({'support_type': 'tree(auto)', 'brim_type': 'outer_only', 'brim_width': 8,
                                             'support_top_z_distance': .2, 'support_on_build_plate_only': True}, self.printer)
        self.assertEqual(values['brim_width'], '8')
        for invalid in ({'support_top_z_distance': -1}, {'brim_width': 31}, {'support_type': 'G1 X1000'},
                        {'support_on_build_plate_only': 1}, {'support_interface_top_layers': 7}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_process_overrides(invalid, self.printer)
        with self.assertRaisesRegex(ValueError, 'Manual supports require'):
            slice_model(self.root/'source.stl', self.root/'manual', self.printer, {'support_mode': 'manual'})


if __name__ == "__main__":
    unittest.main()
