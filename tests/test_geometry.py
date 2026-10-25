import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np
import trimesh

from bambu_workflow.geometry import analyze_mesh, build_volume_for, classify_3mf, load_mesh, normalized_stl


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.printer = {"model": "a1mini", "nozzle_diameter": .4}

    def tearDown(self):
        self.tmp.cleanup()

    def stl(self, dims=(12, 12, 1.2)):
        p = self.root / "model.stl"
        trimesh.creation.box(dims).export(p)
        return p

    def test_mm_dimensions_no_rescale(self):
        source = self.stl()
        before = source.read_bytes()
        report = normalized_stl(source, self.root / "normalized.stl", self.printer)
        self.assertTrue(np.allclose(report["dimensions_mm"], [12, 12, 1.2]))
        normalized = load_mesh(self.root / "normalized.stl")
        self.assertTrue(np.allclose(normalized.bounds[0], [-6, -6, 0]))
        self.assertEqual(before, source.read_bytes())

    def test_oversize_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceeds build volume"):
            analyze_mesh(self.stl((181, 12, 5)), self.printer)

    def test_model_specific_size_and_conservative_preset_height(self):
        source = self.stl((220, 200, 5))
        self.assertTrue(analyze_mesh(source, {"model":"p1s", "nozzle_diameter":.4})["watertight"])
        with self.assertRaisesRegex(ValueError,"exceeds build volume"):
            analyze_mesh(source, self.printer)
        with self.assertRaisesRegex(ValueError,"exceeds build volume"):
            analyze_mesh(self.stl((10,10,251)), {"model":"x1c"})
        self.assertEqual(build_volume_for({"model":"x2d"}, {"printable_height":"261"})[2],260)
        self.assertEqual(build_volume_for({"model":"a1mini","build_volume_mm":[256,256,256]}),[180,180,180])
        with self.assertRaisesRegex(ValueError,"Unsupported Bambu model"):
            analyze_mesh(self.stl(), {"model":"A1 something"})

    def test_open_mesh_is_rejected(self):
        m = trimesh.creation.box((12, 12, 2))
        m.update_faces(np.arange(len(m.faces) - 1))
        p = self.root / "open.stl"
        m.export(p)
        with self.assertRaisesRegex(ValueError, "not watertight"):
            analyze_mesh(p, self.printer)

    def test_exact_zero_area_records_are_removed_only_in_print_copy(self):
        m = trimesh.creation.box((12, 12, 2))
        m.faces = np.vstack([m.faces, [0, 0, 1], [2, 3, 3]])
        p = self.root / "zero-records.stl"
        m.export(p)
        original = p.read_bytes()
        report = normalized_stl(p, self.root / "clean-print.stl", self.printer)
        self.assertTrue(report["watertight"])
        self.assertEqual(report["cleanup"]["removed_triangles"], 2)
        self.assertTrue(report["cleanup"]["bounds_unchanged"])
        self.assertTrue(report["cleanup"]["signed_volume_unchanged"])
        self.assertEqual(len(load_mesh(self.root / "clean-print.stl").faces), 12)
        self.assertEqual(original, p.read_bytes())

    def test_zero_area_cleanup_cannot_change_bounds_or_hide_an_open_surface(self):
        m = trimesh.creation.box((12, 12, 2))
        m.vertices = np.vstack([m.vertices, [100, 100, 100]])
        m.faces = np.vstack([m.faces, [8, 8, 8]])
        p = self.root / "zero-outlier.stl"
        m.export(p)
        with self.assertRaisesRegex(ValueError, "unchanged bounds"):
            analyze_mesh(p, self.printer)
        m = trimesh.creation.box((12, 12, 2))
        m.faces = np.vstack([m.faces[:-1], [0, 0, 1]])
        m.export(p)
        with self.assertRaisesRegex(ValueError, "does not produce a closed surface"):
            analyze_mesh(p, self.printer)

    def test_distinct_nearby_vertices_are_never_tolerance_welded(self):
        a = trimesh.creation.box((2, 4, 1))
        b = a.copy()
        a.apply_translation((-1, 0, .5))
        b.apply_translation((1 + 1e-9, 0, .5))
        p = self.root / "tiny-gap.stl"
        trimesh.util.concatenate([a, b]).export(p)
        report = analyze_mesh(p, self.printer)
        self.assertTrue(report["watertight"])
        self.assertEqual(report["components"], 2)
        self.assertEqual(report["cleanup"]["removed_triangles"], 0)

    def test_negative_winding_is_rejected(self):
        m = trimesh.creation.box((12, 12, 2))
        m.invert()
        p = self.root / "inverted.stl"
        m.export(p)
        with self.assertRaisesRegex(ValueError, "non-positive"):
            analyze_mesh(p, self.printer)

    def test_3mf_units_components_build_transforms(self):
        p = self.root / "assembly.3mf"
        xml = '''<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="inch">
        <resources><object id="1"><mesh><vertices>
        <vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="1"/>
        </vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>
        <object id="2"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 1 0 0"/></components></object>
        </resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 2 0"/></build></model>'''
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("3D/3dmodel.model", xml)
        self.assertEqual(classify_3mf(p)["kind"], "geometry")
        m = load_mesh(p)
        self.assertTrue(np.allclose(m.bounds, [[25.4, 50.8, 0], [50.8, 76.2, 25.4]]))

    def test_3mf_classification_and_archive_traversal(self):
        p = self.root / "project.3mf"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("3D/3dmodel.model", "<model/>")
            z.writestr("Metadata/project_settings.config", "{}")
        self.assertEqual(classify_3mf(p)["kind"], "project")
        with zipfile.ZipFile(p, "a") as z:
            z.writestr("Metadata/plate_1.gcode", "; example")
        self.assertEqual(classify_3mf(p)["kind"], "sliced")
        with zipfile.ZipFile(p, "a") as z:
            z.writestr("../escape", "x")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            classify_3mf(p)


if __name__ == "__main__":
    unittest.main()
