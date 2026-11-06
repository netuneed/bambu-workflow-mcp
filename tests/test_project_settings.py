"""Synthetic GUI-configuration regression cases; no private models or printers."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from bambu_workflow.project_settings import form_generated_project_settings, validate_project_settings


VARIANTS = ["Direct Drive Standard", "Direct Drive High Flow", "Direct Drive E3D High Flow"]


def project_settings(materials=1):
    rows = materials * len(VARIANTS)
    return {"printer_model": "Bambu Lab P2S", "nozzle_diameter": ["0.4"],
            "extruder_type": ["Direct Drive"], "nozzle_volume_type": ["Standard"],
            "extruder_variant_list": [",".join(VARIANTS)],
            "printer_extruder_variant": VARIANTS.copy(), "printer_extruder_id": ["1"] * 3,
            "print_extruder_variant": VARIANTS.copy(), "print_extruder_id": ["1"] * 3,
            "retraction_length": ["0.8"] * 3, "machine_max_speed_x": ["500"] * 6,
            "outer_wall_speed": ["60"] * 3,
            "filament_colour": ["#102030"] * materials, "filament_type": ["ABS"] * materials,
            "filament_ids": ["GFB99"] * materials, "filament_settings_id": ["Generic ABS @BBL P2S"] * materials,
            "filament_diameter": ["1.75"] * materials,
            "filament_extruder_variant": VARIANTS * materials,
            "filament_self_index": [str(i) for i in range(1, materials + 1) for _ in VARIANTS],
            "filament_max_volumetric_speed": ["15"] * rows, "filament_flow_ratio": ["0.95"] * rows,
            "nozzle_temperature": ["260"] * rows, "hot_plate_temp": ["90"] * materials,
            "filament_map": ["1"] * materials, "filament_nozzle_map": ["1"] * materials,
            "filament_map_2": ["1"] * materials, "filament_volume_map": ["0"] * materials,
            "flush_volumes_matrix": ["0"] * 16, "flush_volumes_vector": ["140"] * 8,
            "support_filament": "0"}


def test_one_material_has_three_parameter_rows_and_one_physical_extruder():
    settings = project_settings()
    original = deepcopy(settings)
    facts = validate_project_settings(settings, require_palette=True)
    assert facts["logical_filament_count"] == 1 and facts["physical_extruder_count"] == 1
    assert facts["filament_variant_rows"] == 3 and facts["filament_self_index"] == [1, 1, 1]
    assert settings == original


def test_valid_multicolor_preserves_all_logical_filaments_and_variant_domains():
    settings = project_settings(2)
    # Historical keys belong to both sets; get_parameter_size gives machine
    # variants precedence, so these have three rows even with six filament rows.
    settings["long_retractions_when_cut"] = ["0"] * 3
    facts = validate_project_settings(settings, require_palette=True)
    assert facts["logical_filament_count"] == 2
    assert facts["filament_self_index"] == [1, 1, 1, 2, 2, 2]


def test_native_default_single_tool_mapping_and_larger_purge_capacity_are_valid():
    settings = project_settings(2)
    for key in ("filament_map", "filament_map_2", "filament_nozzle_map"):
        settings[key] = ["1"]
    settings["filament_volume_map"] = ["0"]
    facts = validate_project_settings(settings, require_palette=True)
    assert facts["logical_filament_count"] == 2
    assert not facts["advanced_nozzle_mapping_unresolved"]


def test_two_physical_tools_are_not_confused_with_six_logical_variant_rows():
    settings = project_settings(2)
    settings.update(nozzle_diameter=["0.4", "0.4"], extruder_type=["Direct Drive"] * 2,
                    nozzle_volume_type=["Standard"] * 2, extruder_variant_list=[",".join(VARIANTS)] * 2,
                    printer_extruder_variant=VARIANTS * 2, printer_extruder_id=["1"] * 3 + ["2"] * 3,
                    print_extruder_variant=VARIANTS * 2, print_extruder_id=["1"] * 3 + ["2"] * 3,
                    retraction_length=["0.8"] * 6, machine_max_speed_x=["500"] * 12,
                    outer_wall_speed=["60"] * 6, filament_map=["1", "2"])
    assert validate_project_settings(settings, require_palette=True)["physical_extruder_count"] == 2


@pytest.mark.parametrize("mapping", [["1"], [], ["1", "1"], ["1", "1", "1", "1"]])
def test_p2s_gui_row_length_mismatch_is_actionable(mapping):
    settings = project_settings()
    settings["filament_self_index"] = mapping
    with pytest.raises(ValueError, match="filament_self_index"):
        validate_project_settings(settings, require_palette=True)


@pytest.mark.parametrize("mapping", [["0", "1", "1"], ["2", "2", "2"], ["-1", "1", "1"],
                                      ["1.0", "1", "1"], [1.0, 1, 1], [True, 1, 1], [None, 1, 1]])
def test_invalid_noninteger_or_out_of_palette_ids_never_pass(mapping):
    settings = project_settings()
    settings["filament_self_index"] = mapping
    with pytest.raises(ValueError, match="filament_self_index"):
        validate_project_settings(settings, require_palette=True)


@pytest.mark.parametrize("mapping", [[1, 1, 1, 1, 1, 1], [1, 2, 1, 2, 2, 2], [2, 2, 2, 1, 1, 1], [1, 1, 3, 3, 3, 3]])
def test_multicolor_groups_must_cover_every_logical_filament_contiguously(mapping):
    settings = project_settings(2)
    settings["filament_self_index"] = mapping
    with pytest.raises(ValueError, match="filament_self_index"):
        validate_project_settings(settings, require_palette=True)


@pytest.mark.parametrize("key,value", [
    ("filament_type", ["ABS", "ABS"]), ("filament_max_volumetric_speed", ["15"]),
    ("nozzle_temperature", ["260"]), ("retraction_length", ["0.8"]),
    ("machine_max_speed_x", ["500"] * 3), ("outer_wall_speed", ["60"]),
    ("printer_extruder_id", ["1"]), ("print_extruder_id", ["2"] * 3),
    ("extruder_variant_list", [",".join(VARIANTS)] * 2), ("filament_map", ["2"]),
    ("filament_map", ["0"]), ("filament_nozzle_map", ["-2"]),
    ("filament_volume_map", ["-1"]), ("support_filament", "2"),
    ("master_extruder_id", "2"), ("filament_extruder_variant", "Direct Drive Standard"),
    ("flush_volumes_matrix", ["0"] * 3), ("flush_volumes_vector", ["140"] * 3),
])
def test_related_size_and_index_domains_are_checked(key, value):
    settings = project_settings()
    settings[key] = value
    with pytest.raises(ValueError, match=key):
        validate_project_settings(settings, require_palette=True)


def test_missing_palette_and_empty_gui_palette_are_refused():
    assert validate_project_settings({})["logical_filament_count"] is None
    with pytest.raises(ValueError, match="filament_colour"):
        validate_project_settings({}, require_palette=True)
    with pytest.raises(ValueError, match="filament_colour"):
        validate_project_settings({"filament_colour": []}, require_palette=True)


def test_legacy_absent_self_index_uses_identity_only_for_one_row_per_filament():
    settings = {"filament_colour": ["#102030", "#405060"],
                "filament_extruder_variant": ["Direct Drive Standard"] * 2}
    assert validate_project_settings(settings, require_palette=True)["filament_self_index"] == [1, 2]
    settings["filament_extruder_variant"] *= 3
    with pytest.raises(ValueError, match="filament_self_index"):
        validate_project_settings(settings, require_palette=True)


def test_independent_filament_profile_does_not_need_project_self_index():
    profile = {"filament_type": ["ABS"], "filament_extruder_variant": VARIANTS,
               "filament_max_volumetric_speed": ["15"] * 3}
    facts = validate_project_settings(profile)
    assert facts["filament_variant_rows"] == 3 and facts["filament_self_index"] is None


def test_unused_filament_variants_need_not_be_available_on_target_printer():
    settings = project_settings()
    settings["extruder_variant_list"] = [",".join(VARIANTS[:2])]
    for prefix in ("printer", "print"):
        settings[prefix + "_extruder_variant"] = VARIANTS[:2]
        settings[prefix + "_extruder_id"] = ["1"] * 2
    settings.update(retraction_length=["0.8"] * 2, machine_max_speed_x=["500"] * 4,
                    outer_wall_speed=["60"] * 2)
    assert validate_project_settings(settings, require_palette=True)["filament_variant_rows"] == 3


def test_generated_single_material_mapping_is_formed_without_changing_other_fields():
    settings = project_settings()
    settings["filament_self_index"] = ["1"]
    original = deepcopy(settings)
    generated = form_generated_project_settings(settings)
    assert generated["filament_self_index"] == ["1", "1", "1"]
    assert settings == original
    assert {k: v for k, v in generated.items() if k != "filament_self_index"} == {
        k: v for k, v in original.items() if k != "filament_self_index"}


def test_generation_requires_truthful_explicit_multicolor_mapping_and_never_blind_expands_one():
    settings = project_settings(2)
    del settings["filament_self_index"]
    with pytest.raises(ValueError, match="explicit variant_rows"):
        form_generated_project_settings(settings)
    correct = form_generated_project_settings(settings, variant_rows=[1, 1, 1, 2, 2, 2])
    assert correct["filament_self_index"] == ["1"] * 3 + ["2"] * 3
    with pytest.raises(ValueError, match="filament_self_index"):
        form_generated_project_settings(settings, variant_rows=[1] * 6)
    assert "filament_self_index" not in settings


def test_automatic_unassigned_extruder_marker_is_not_a_valid_manual_mapping():
    settings = project_settings()
    settings["filament_map"] = ["-1"]
    settings["filament_map_mode"] = "Auto For Flush"
    validate_project_settings(settings, require_palette=True)
    settings["filament_map_mode"] = "Manual"
    with pytest.raises(ValueError, match="filament_map"):
        validate_project_settings(settings, require_palette=True)


def test_auto_for_quality_accepts_unassigned_marker_for_inspection_only():
    settings = project_settings()
    settings.update(filament_map=["-1"], filament_map_mode="Auto For Quality")
    assert validate_project_settings(settings, require_palette=True)["logical_filament_count"] == 1


@pytest.mark.parametrize("value", range(6))
def test_known_nozzle_volume_enum_values_are_accepted(value):
    settings = project_settings()
    settings["filament_volume_map"] = [str(value)]
    assert validate_project_settings(settings, require_palette=True)["physical_extruder_count"] == 1


def test_unknown_nozzle_volume_enum_is_an_actionable_blocker():
    settings = project_settings()
    settings["filament_volume_map"] = ["999999"]
    with pytest.raises(ValueError, match="filament_volume_map.*0..5"):
        validate_project_settings(settings, require_palette=True)


@pytest.mark.parametrize("key,value", [("filament_map_2", "999999"), ("filament_map_2", "0"),
                                       ("filament_nozzle_map", "2"), ("filament_nozzle_map", "-1")])
def test_nondefault_composite_or_group_maps_remain_unresolved(key, value):
    settings = project_settings()
    settings[key] = [value]
    assert validate_project_settings(settings, require_palette=True)["advanced_nozzle_mapping_unresolved"]


@pytest.mark.parametrize("two_tools", [False, True])
def test_purge_matrix_can_contain_multiple_tables_from_flush_multiplier(two_tools):
    settings = project_settings(2)
    if two_tools:
        settings.update(nozzle_diameter=["0.4"] * 2, extruder_type=["Direct Drive"] * 2,
                        nozzle_volume_type=["Standard"] * 2, extruder_variant_list=[",".join(VARIANTS)] * 2,
                        printer_extruder_variant=VARIANTS * 2, printer_extruder_id=["1"] * 3 + ["2"] * 3,
                        print_extruder_variant=VARIANTS * 2, print_extruder_id=["1"] * 3 + ["2"] * 3,
                        retraction_length=["0.8"] * 6, machine_max_speed_x=["500"] * 12,
                        outer_wall_speed=["60"] * 6, filament_map=["1", "2"])
    settings["flush_multiplier"] = ["1", "1"]
    settings["flush_volumes_matrix"] = ["0"] * 8
    facts = validate_project_settings(settings, require_palette=True)
    assert facts["purge_table_count"] == 2 and facts["purge_palette_capacity"] == 2
    settings["flush_volumes_matrix"] = ["0"] * 3
    with pytest.raises(ValueError, match="flush_volumes_matrix"):
        validate_project_settings(settings, require_palette=True)


def test_project_semantics_remain_standard_library_only_in_fresh_process():
    result = subprocess.run([sys.executable, "-I", "-c", "import json,sys; import bambu_workflow.project_settings; print(json.dumps([n for n in ('numpy','trimesh','bambu_workflow.geometry') if n in sys.modules]))"],
                            capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(result.stdout) == []
