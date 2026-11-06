"""Semantic checks for Bambu project configuration, without importing mesh libraries.

The row domains differ: palette fields use logical filament IDs; variant fields
have several rows per filament or physical extruder. Source contract reviewed at
BambuStudio 79df7ae309be398ed30c9f397c13c9911c2edc57:
PresetBundle::load_config_file_config and PrintConfig.cpp option-domain sets.
This validates configuration relationships, never GUI rendering or printability.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

SOURCE_REVISION = "79df7ae309be398ed30c9f397c13c9911c2edc57"
# NozzleVolumeType at this later reviewed revision adds TPU285=4 to the
# earlier 0,1,2,3,5 contract. Unknown future values must be qualified first.
NOZZLE_VOLUME_SCHEMA_REVISION = "355d71eee5a9ebc794693fe57e63674806019770"
KNOWN_NOZZLE_VOLUME_TYPES = frozenset(range(6))
MAX_ROWS = 16384

FILAMENT_OPTIONS_WITH_VARIANT = frozenset({
    "filament_flow_ratio",
    "filament_max_volumetric_speed",
    "filament_ramming_volumetric_speed",
    "filament_pre_cooling_temperature",
    "filament_ramming_travel_time",
    "filament_ramming_volumetric_speed_nc",
    "filament_pre_cooling_temperature_nc",
    "filament_ramming_travel_time_nc",
    "filament_extruder_variant",
    "filament_retraction_length",
    "filament_retract_length_nc",
    "filament_z_hop",
    "filament_z_hop_types",
    "filament_retract_restart_extra",
    "filament_retraction_speed",
    "filament_deretraction_speed",
    "filament_retraction_minimum_travel",
    "filament_retract_when_changing_layer",
    "filament_wipe",
    "filament_wipe_distance",
    "filament_retract_before_wipe",
    "filament_long_retractions_when_cut",
    "filament_retraction_distances_when_cut",
    "long_retractions_when_ec",
    "retraction_distances_when_ec",
    "nozzle_temperature_initial_layer",
    "nozzle_temperature",
    "filament_flush_volumetric_speed",
    "filament_flush_temp",
    "filament_flush_temp_fast",
    "filament_enable_overhang_speed",
    "filament_bridge_speed",
    "filament_overhang_1_4_speed",
    "filament_overhang_2_4_speed",
    "filament_overhang_3_4_speed",
    "filament_overhang_4_4_speed",
    "filament_overhang_totally_speed",
    "override_process_overhang_speed",
    "volumetric_speed_coefficients",
    "filament_adaptive_volumetric_speed",
    "filament_preheat_temperature_delta",
    "filament_cooling_before_tower",
    "slow_down_min_speed",
})

PRINT_OPTIONS_WITH_VARIANT = frozenset({
    "initial_layer_speed",
    "initial_layer_infill_speed",
    "outer_wall_speed",
    "inner_wall_speed",
    "small_perimeter_speed",
    "small_perimeter_threshold",
    "sparse_infill_speed",
    "internal_solid_infill_speed",
    "vertical_shell_speed",
    "top_surface_speed",
    "enable_overhang_speed",
    "overhang_1_4_speed",
    "overhang_2_4_speed",
    "overhang_3_4_speed",
    "overhang_4_4_speed",
    "overhang_totally_speed",
    "enable_height_slowdown",
    "slowdown_start_height",
    "slowdown_start_speed",
    "slowdown_start_acc",
    "slowdown_end_height",
    "slowdown_end_speed",
    "slowdown_end_acc",
    "bridge_speed",
    "gap_infill_speed",
    "support_speed",
    "support_interface_speed",
    "travel_speed",
    "travel_speed_z",
    "default_acceleration",
    "travel_acceleration",
    "travel_short_distance_acceleration",
    "initial_layer_travel_acceleration",
    "initial_layer_acceleration",
    "outer_wall_acceleration",
    "inner_wall_acceleration",
    "sparse_infill_acceleration",
    "top_surface_acceleration",
    "print_extruder_id",
    "print_extruder_variant",
    "top_solid_infill_flow_ratio",
})

PRINTER_OPTIONS_WITH_VARIANT_1 = frozenset({
    "nozzle_volume",
    "retraction_length",
    "z_hop",
    "retract_lift_above",
    "retract_lift_below",
    "z_hop_types",
    "retraction_speed",
    "deretraction_speed",
    "retraction_minimum_travel",
    "retract_when_changing_layer",
    "wipe",
    "wipe_distance",
    "retract_before_wipe",
    "retract_length_toolchange",
    "retract_restart_extra",
    "retract_restart_extra_toolchange",
    "long_retractions_when_cut",
    "retraction_distances_when_cut",
    "printer_extruder_id",
    "printer_extruder_variant",
    "hotend_cooling_rate",
    "hotend_heating_rate",
    "nozzle_flush_dataset",
})

PRINTER_OPTIONS_WITH_VARIANT_2 = frozenset({
    "machine_max_acceleration_x",
    "machine_max_acceleration_y",
    "machine_max_acceleration_z",
    "machine_max_acceleration_e",
    "machine_max_acceleration_extruding",
    "machine_max_acceleration_retracting",
    "machine_max_acceleration_travel",
    "machine_max_speed_x",
    "machine_max_speed_y",
    "machine_max_speed_z",
    "machine_max_speed_e",
    "machine_max_jerk_x",
    "machine_max_jerk_y",
    "machine_max_jerk_z",
    "machine_max_jerk_e",
})


# These are palette vectors, not expanded filament-variant parameter rows.
PALETTE_FIELDS = frozenset({
    "filament_colour", "filament_settings_id", "filament_ids", "filament_type",
    "filament_diameter", "filament_density", "filament_cost", "filament_soluble",
    "filament_is_support", "filament_multi_colour", "filament_diameter_map",
    "hot_plate_temp", "hot_plate_temp_initial_layer",
    "textured_plate_temp", "textured_plate_temp_initial_layer",
    "cool_plate_temp", "cool_plate_temp_initial_layer",
    "eng_plate_temp", "eng_plate_temp_initial_layer",
    "supertack_plate_temp", "supertack_plate_temp_initial_layer",
})
FILAMENT_SELECTORS = frozenset({
    "wall_filament", "sparse_infill_filament", "solid_infill_filament",
    "support_filament", "support_interface_filament",
})
AUTO_MAP_MODES = {None, "Auto For Flush", "Auto For Match", "Auto For Quality", "Default"}


def _invalid(message: str):
    raise ValueError("Invalid Bambu project settings: " + message
                     + ". Re-save the project with consistent material/variant mappings in Bambu Studio; "
                     "a successful CLI load/export alone does not prove GUI compatibility.")


def _array(settings: dict, key: str, *, empty: bool = False) -> list:
    value = settings.get(key)
    if not isinstance(value, list) or (not value and not empty) or len(value) > MAX_ROWS:
        _invalid(key + " must be a " + ("possibly empty " if empty else "non-empty ")
                 + "array of at most " + str(MAX_ROWS) + " entries")
    return value


def _integer(value, key: str) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    _invalid(key + " must contain integer IDs, not booleans, decimals or labels")


def _same_length(settings: dict, key: str, expected: int, domain: str):
    if key in settings:
        values = _array(settings, key)
        if len(values) != expected:
            _invalid(key + " has " + str(len(values)) + " rows; expected "
                     + str(expected) + " " + domain + " rows")


def _names(values: list, key: str) -> list[str]:
    if any(not isinstance(v, str) or not v.strip() for v in values):
        _invalid(key + " must contain non-empty variant names")
    return values


def _grouped_ids(values: list[int], count: int, key: str):
    if not values or values[0] != 1 or values[-1] != count:
        _invalid(key + " must cover every logical filament 1.." + str(count)
                 + " in contiguous ascending groups")
    for index, value in enumerate(values):
        if value < 1 or value > count or (index and value not in {values[index - 1], values[index - 1] + 1}):
            _invalid(key + " contains an out-of-range, missing or interleaved logical filament; "
                     + "expected contiguous ascending groups 1.." + str(count))


def validate_project_settings(settings: dict, *, require_palette: bool = False) -> dict:
    """Check complete project metadata; do not repair or mutate imported bytes.

    require_palette=True applies the GUI's non-empty filament_colour requirement.
    False supports partial/legacy settings and does not claim GUI compatibility.
    Known vector domains are checked when present. Advanced nozzle group maps
    are type/cardinality checked only; routing remains a separate adapter check.
    """
    if not isinstance(settings, dict):
        _invalid("project_settings.config must be a JSON object")
    defaults = []
    advanced_mapping_unresolved = False
    purge_table_count = 1
    purge_palette_capacity = None
    colour = _array(settings, "filament_colour") if "filament_colour" in settings else None
    if colour is not None:
        if any(not isinstance(v, str) or not v.strip() for v in colour):
            _invalid("filament_colour must contain a color for each logical filament")
        count = len(colour)
    elif require_palette:
        _invalid("filament_colour is missing; GUI projects require a non-empty logical material palette")
    else:
        candidates = [len(_array(settings, key)) for key in ("filament_type", "filament_settings_id", "filament_ids")
                      if key in settings]
        if candidates and any(n != candidates[0] for n in candidates):
            _invalid("legacy logical filament vectors disagree in size")
        count = candidates[0] if candidates else None

    tool_count = None
    if "nozzle_diameter" in settings:
        tool_count = len(_array(settings, "nozzle_diameter"))
    if tool_count is not None and tool_count != 1:
        advanced_mapping_unresolved = True
    available = None
    if "extruder_variant_list" in settings:
        declarations = _names(_array(settings, "extruder_variant_list"), "extruder_variant_list")
        if tool_count is None:
            if require_palette:
                _invalid("extruder_variant_list requires nozzle_diameter to identify physical extruders")
            tool_count = len(declarations)
        if len(declarations) != tool_count:
            _invalid("extruder_variant_list must have one comma-separated variant list per physical extruder")
        available = []
        for declaration in declarations:
            variants = [v.strip() for v in declaration.split(",")]
            if any(not v for v in variants) or len(set(variants)) != len(variants):
                _invalid("extruder_variant_list contains an empty or duplicated available variant")
            available.append(set(variants))

    for key in ("extruder_type", "nozzle_volume_type"):
        if tool_count is not None:
            _same_length(settings, key, tool_count, "physical extruder")
    for prefix in ("printer", "print"):
        variants_key, ids_key = prefix + "_extruder_variant", prefix + "_extruder_id"
        if variants_key not in settings and ids_key not in settings:
            continue
        if variants_key not in settings or ids_key not in settings:
            _invalid(variants_key + " and " + ids_key + " must be supplied together")
        variants = _names(_array(settings, variants_key), variants_key)
        ids = [_integer(v, ids_key) for v in _array(settings, ids_key)]
        if len(variants) != len(ids):
            _invalid(variants_key + " and " + ids_key + " have different row counts")
        if tool_count is None:
            if require_palette:
                _invalid(ids_key + " cannot be checked without nozzle_diameter")
        else:
            if any(i < 1 or i > tool_count for i in ids):
                _invalid(ids_key + " contains a physical extruder ID outside 1.." + str(tool_count))
            if set(ids) != set(range(1, tool_count + 1)):
                _invalid(ids_key + " must represent every configured physical extruder")
        if available:
            for variant, index in zip(variants, ids):
                if variant not in available[index - 1]:
                    _invalid(variants_key + " refers to a variant unavailable on its declared physical extruder")
        keys = PRINTER_OPTIONS_WITH_VARIANT_1 if prefix == "printer" else PRINT_OPTIONS_WITH_VARIANT
        for key in keys:
            _same_length(settings, key, len(variants), prefix + " variant")
        if prefix == "printer":
            for key in PRINTER_OPTIONS_WITH_VARIANT_2:
                _same_length(settings, key, len(variants) * 2, "printer variant and speed mode")

    if tool_count is not None and "master_extruder_id" in settings:
        master = _integer(settings["master_extruder_id"], "master_extruder_id")
        if not 1 <= master <= tool_count:
            _invalid("master_extruder_id is outside the physical extruder range")

    rows = None
    self_ids = None
    modern = "extruder_variant_list" in settings
    if "filament_extruder_variant" in settings:
        variants = _names(_array(settings, "filament_extruder_variant"), "filament_extruder_variant")
        rows = len(variants)
    elif modern:
        rows = 1
        defaults.append("filament_extruder_variant: Direct Drive Standard")

    if "filament_self_index" in settings:
        self_ids = [_integer(v, "filament_self_index") for v in _array(settings, "filament_self_index")]
    elif count is not None and colour is not None and (rows is not None or modern):
        self_ids = list(range(1, count + 1))
        defaults.append("filament_self_index: one identity row per logical filament")

    if rows is not None:
        if self_ids is None:
            if require_palette:
                _invalid("expanded filament variants require a logical palette and filament_self_index")
        elif len(self_ids) != rows:
            _invalid("filament_extruder_variant has " + str(rows) + " rows but filament_self_index has "
                     + str(len(self_ids)) + "; one logical material may have several extruder variants")
        if count is not None and rows < count:
            _invalid("filament_extruder_variant has fewer rows than logical filaments")
        for key in FILAMENT_OPTIONS_WITH_VARIANT:
            # PrintConfig::get_parameter_size gives printer-domain options
            # precedence where historical option sets overlap.
            if "printer_extruder_variant" in settings and key in (PRINTER_OPTIONS_WITH_VARIANT_1 | PRINTER_OPTIONS_WITH_VARIANT_2):
                continue
            _same_length(settings, key, rows, "filament variant")

    if count is not None:
        if self_ids is not None:
            _grouped_ids(self_ids, count, "filament_self_index")
        for key in PALETTE_FIELDS:
            _same_length(settings, key, count, "logical filament")
        for key in FILAMENT_SELECTORS:
            if key in settings:
                value = _integer(settings[key], key)
                if not 0 <= value <= count:
                    _invalid(key + " must be 0 (current filament) or a logical filament ID in 1.." + str(count))
        # Studio may retain a larger palette, and one square table per entry
        # of flush_multiplier. Its table count is not the physical tool count.
        if "flush_multiplier" in settings:
            purge_table_count = len(_array(settings, "flush_multiplier"))
        if "flush_volumes_matrix" in settings:
            matrix = _array(settings, "flush_volumes_matrix")
            table_size, remainder = divmod(len(matrix), purge_table_count)
            side = math.isqrt(table_size)
            if remainder or side * side != table_size or side < count:
                _invalid("flush_volumes_matrix must contain one square palette-covering table per flush_multiplier entry")
            purge_palette_capacity = side
        if "flush_volumes_vector" in settings:
            vector = _array(settings, "flush_volumes_vector")
            if len(vector) % 2 or len(vector) < 2 * count:
                _invalid("flush_volumes_vector needs two purge values per logical filament or retained palette slot")
        for key in ("filament_map", "filament_map_2", "filament_nozzle_map", "filament_volume_map"):
            if key not in settings:
                continue
            values = [_integer(v, key) for v in _array(settings, key)]
            # Native single-tool files may retain the default singleton mapping.
            # Missing logical slots fall back to tool0 in get_extruder_index.
            singleton_default = (len(values) == 1 and tool_count == 1 and values[0] == (0 if key == "filament_volume_map" else 1))
            if len(values) != count and not singleton_default:
                _invalid(key + " must cover the logical palette (or the native single-extruder default)")
            if key == "filament_map":
                automatic = settings.get("filament_map_mode") in AUTO_MAP_MODES
                if any(v < 1 and not (v == -1 and automatic) for v in values):
                    _invalid("filament_map contains an invalid physical extruder ID")
                if tool_count is not None and any(v > tool_count for v in values):
                    _invalid("filament_map contains a physical extruder ID outside 1.." + str(tool_count))
            elif key == "filament_volume_map":
                if any(v not in KNOWN_NOZZLE_VOLUME_TYPES for v in values):
                    _invalid("filament_volume_map must use a recognized nozzle-volume enum in 0..5")
            else:
                if any(v < -1 for v in values):
                    _invalid(key + " contains an invalid nozzle group/variant index")
                # Nozzle groups and composite variant slots are not physical
                # extruder IDs. Only the native single-tool default is resolved
                # here. Callers must block automatic launch for anything else.
                if tool_count != 1 or any(v != 1 for v in values):
                    advanced_mapping_unresolved = True

    return {"logical_filament_count": count, "physical_extruder_count": tool_count,
            "filament_variant_rows": rows, "filament_self_index": self_ids,
            "modern_variant_mapping": modern, "legacy_defaults_used": defaults,
            "palette_required": require_palette, "source_revision": SOURCE_REVISION,
            "nozzle_volume_schema_revision": NOZZLE_VOLUME_SCHEMA_REVISION,
            "advanced_nozzle_mapping_unresolved": advanced_mapping_unresolved,
            "purge_table_count": purge_table_count, "purge_palette_capacity": purge_palette_capacity,
            "limitations": ["Configuration semantics do not prove native GUI rendering, slicing, material routing or printability.",
                            "Advanced nozzle group/composite maps are type/cardinality checked; unresolved mappings require a separate qualified adapter and must block automatic launch."]}


def form_generated_project_settings(settings: dict, *, variant_rows: list[int] | None = None) -> dict:
    """Form mapping for a NEW generated project; never use this to repair imports.

    Single-material rows are unambiguously all1. For several logical materials,
    preserve a valid existing mapping or require an explicit row-to-filament list.
    All unrelated configuration is copied unchanged; parameter arrays must already
    be resolved to the correct variant sizes by the caller.
    """
    if not isinstance(settings, dict):
        _invalid("generated settings must be an object")
    generated = deepcopy(settings)
    count = len(_array(generated, "filament_colour"))
    rows = len(_array(generated, "filament_extruder_variant")) if "filament_extruder_variant" in generated else 1
    if variant_rows is not None:
        if not isinstance(variant_rows, list):
            _invalid("variant_rows must be an explicit list of logical filament IDs")
        generated["filament_self_index"] = [str(_integer(v, "variant_rows")) for v in variant_rows]
    elif count == 1:
        generated["filament_self_index"] = ["1"] * rows
    elif "filament_self_index" not in generated:
        _invalid("multi-material generation requires explicit variant_rows or an already valid filament_self_index")
    validate_project_settings(generated, require_palette=True)
    return generated

