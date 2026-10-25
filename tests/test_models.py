"""Public model identifiers, limits and verification provenance without hardware."""
import pytest

from bambu_workflow.models import (SOURCE_REVISION, capabilities_for, get_model,
                                   infer_model_from_info, infer_model_from_serial,
                                   list_models, normalize_model)


@pytest.mark.parametrize("alias,expected", [
    ("N1","a1mini"), ("A1M","a1mini"), ("Bambu Lab A1 mini","a1mini"),
    ("N2S","a1"), ("C11","p1p"), ("C12","p1s"), ("BL-P001","x1c"),
    ("BL-P002","x1"), ("C13","x1e"), ("N7","p2s"), ("O1S","h2s"),
    ("O1D","h2d"), ("O1E","h2dpro"), ("H2D Pro","h2dpro"),
    ("O1C2","h2c"), ("O1C","h2c"), ("N6","x2d"), ("N9","a2l"),
])
def test_official_model_identifiers_and_friendly_names(alias, expected):
    assert normalize_model(alias) == expected
    assert get_model(alias)["id"] == expected


def test_preparation_and_launch_are_distinct_from_hardware_verified_operations():
    models = list_models()
    assert len(models) == 14
    assert all(model["preparation_supported"] for model in models)
    assert {model["id"] for model in models if model["launch_supported"]} == {
        "a1mini","a1","p1p","p1s","x1","x1c","x1e","p2s","h2s","a2l"}
    assert {model["id"] for model in models if model["hardware_verified"]} == {"a1mini"}
    assert capabilities_for("h2d")["pause"] is True
    assert capabilities_for("h2d")["start"] is False
    assert "nozzle" in capabilities_for("h2d")["start_block_reason"]
    assert "eMMC" in capabilities_for("x2d")["operation_block_reasons"]["upload"]
    assert "eMMC" in capabilities_for("x2d")["start_block_reason"]
    assert "pause" not in get_model("a1mini")["hardware_verified_operations"]


def test_registry_returns_private_copies_and_keeps_profile_limits_separate():
    first = get_model("p1s")
    assert first["build_volume_mm"] == [256,256,256]
    assert first["profile_volume_mm"] == [256,256,250]
    first["build_volume_mm"][0] = 900
    first["bed_exclude_areas_mm"].clear()
    assert get_model("p1s")["build_volume_mm"][0] == 256
    assert get_model("p1s")["bed_exclude_areas_mm"]
    assert all(model["source_revision"] == SOURCE_REVISION for model in list_models())


def test_unknown_models_and_ambiguous_board_types_do_not_default_to_a1mini():
    assert normalize_model("some future printer") is None
    with pytest.raises(ValueError, match="Unsupported"):
        get_model("some future printer")
    assert infer_model_from_serial("NEWUNREGISTEREDDEVICE") is None
    assert infer_model_from_info({"module":[{"name":"ap","hw_ver":"AP05"}]}) is None


def test_modern_product_name_and_legacy_project_code_identify_models():
    assert infer_model_from_info({"module":[{"name":"ota","product_name":"Bambu Lab H2D Pro","sn":"NEWDEVICE012345"}]}) == "h2dpro"
    assert infer_model_from_info({"module":[{"name":"esp32","project_name":"C12","hw_ver":"AP04"}]}) == "p1s"
    assert infer_model_from_info({"module":[{"name":"ota","sn":"030TESTPRINTER01"}]}) == "a1mini"


def test_thermal_and_storage_facts_do_not_generalize_one_model_to_the_family():
    assert get_model("a1mini")["bed_max_c"] == 80
    assert get_model("a1")["bed_max_c"] == 100
    assert get_model("p1s")["bed_max_c"] == 100
    assert get_model("p2s")["bed_max_c"] == 110
    assert get_model("h2s")["nozzle_max_c"] == 350
    assert get_model("x2d")["nozzle_max_c"] == 300
    assert get_model("p2s")["requires_sdcard"] is False
    assert get_model("a2l")["requires_sdcard"] is True
