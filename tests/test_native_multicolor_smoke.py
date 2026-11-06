"""Exercise the opt-in native harness without launching Studio."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile


_spec = importlib.util.spec_from_file_location("native_multicolor_smoke", Path(__file__).resolve().parents[1]/"scripts/native_multicolor_smoke.py")
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


class NativeMulticolorHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_fixture_uses_owned_stls_explicit_colors_and_isolated_data_without_launch(self):
        machine_name = "Bambu Lab P2S 0.4 nozzle"
        profiles = {
            "machine": {"name": machine_name, "printer_model": "Bambu Lab P2S", "nozzle_diameter": [".4"],
                        "nozzle_type": ["hardened_steel"], "machine_start_gcode": "; installed fixture", "printer_extruder_variant": ["Direct Drive Standard"]},
            "process": {"name": "process", "compatible_printers": [machine_name]},
            "filament": {"name": "Bambu PETG HF @BBL P2S 0.4 nozzle", "filament_type": ["PETG"],
                         "filament_id": "GFG02", "compatible_printers": [machine_name]},
        }
        paths = {"root": self.root}
        for kind, values in profiles.items():
            paths[kind] = self.root/(kind+".json")
            paths[kind].write_text(json.dumps(values))
        executable = self.root/"never-executed-Studio"
        output = self.root/"test-output"
        with patch.object(smoke, "_profile_paths", return_value=(executable, paths)), patch.object(smoke.subprocess, "Popen") as launch:
            command, printer, provenance = smoke.prepare_fixture(executable, output)
        launch.assert_not_called()
        self.assertEqual(command[command.index("--load-filament-ids")+1], "1,2")
        self.assertIn("--allow-multicolor-oneplate", command)
        self.assertEqual(Path(command[command.index("--datadir")+1]).resolve(), (output/"studio-data").resolve())
        self.assertEqual([entry["color"] for entry in printer["materials"]], ["FF0000FF", "0000FFFF"])
        self.assertEqual(len(provenance["sources"]), 2)
        self.assertTrue(all(entry["watertight"] for entry in provenance["sources"]))
        self.assertEqual(len(json.loads((output/"resolved-process.json").read_text())["flush_volumes_matrix"]), 4)
        self.assertEqual([Path(item).suffix for item in command[-2:]], [".stl", ".stl"])

    def test_timeout_kills_only_the_created_child_without_retry(self):
        child = MagicMock(pid=12345)
        child.wait.side_effect = [subprocess.TimeoutExpired("Studio", 120), 0]
        with patch.object(smoke.subprocess, "Popen", return_value=child) as launch:
            with self.assertRaisesRegex(RuntimeError, "only its child PID 12345"):
                smoke.run_native(["never-executed-Studio"], self.root)
        launch.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_args_list[0].kwargs, {"timeout": 120})
        self.assertEqual(child.wait.call_args_list[1].kwargs, {"timeout": 15})

    def test_validator_rejects_metadata_claim_without_actual_second_selector(self):
        final = self.root/"multicolor.gcode.3mf"
        with zipfile.ZipFile(final, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", "T0\nM620 S1A\n")
            archive.writestr("Metadata/project_settings.config", "{}")
        inspection = {"used_filament_ids": [1, 2], "used_tool_ids": [1], "palette": [{}, {}],
                      "gcode_path": "Metadata/plate_1.gcode"}
        with patch.object(smoke, "inspect_print", return_value=inspection), patch.object(smoke, "slice_model") as imported:
            with self.assertRaisesRegex(RuntimeError, "actual T0 and T1"):
                smoke.validate_result(self.root, {})
        imported.assert_not_called()


if __name__ == "__main__":
    unittest.main()
