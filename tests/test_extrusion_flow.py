"""Executable custom-hotend flow bounds, independent of metadata claims."""
import math

import pytest

from bambu_workflow.flow import validate_extrusion_flow


AREA=math.pi*(1.75/2)**2
START="G21\nG90\nM83\nG0 X0 Y0 Z0 F600\n"


def test_header_cap_cannot_hide_fast_pure_e_startup_purge():
    with pytest.raises(ValueError,match=r"24\.0528.*cap 5"):
        validate_extrusion_flow("; filament_max_volumetric_speed = 5\nM83\nG1 E10 F600",5)


def test_safe_xyz_and_e_only_moves_have_expected_physical_flow():
    result=validate_extrusion_flow(START+"G1 X10 E1 F600\nG1 E1 F60",5)
    assert result["checked_extrusion_moves"]==2 and result["pure_extrusion_moves"]==1
    assert result["maximum_flow_mm3_s"]==pytest.approx(AREA)


def test_g0_calibration_extrusion_is_checked_too():
    with pytest.raises(ValueError,match="exceeds"):
        validate_extrusion_flow(START+"G0 X1 E10 F600",5)


def test_retraction_recovery_receives_no_free_extrusion_credit():
    with pytest.raises(ValueError,match="exceeds"):
        validate_extrusion_flow("M83\nG1 E-10 F600\nG1 E10 F600",5)
    result=validate_extrusion_flow("M83\nG1 E-1 F600\nG1 E1 F60",5)
    assert result["retraction_moves"]==1 and result["checked_extrusion_moves"]==1


def test_absolute_relative_xyz_and_e_modes_and_resets():
    text="G21\nG90\nM82\nG92 E0\nG0 X0 Y0 Z0 F600\nG1 X10 E1\nG92 E0\nG91\nG1 X10 E1\nM83\nG1 X10 E1\nG90\nG1 X40 E1"
    result=validate_extrusion_flow(text,5)
    assert result["checked_extrusion_moves"]==4 and result["maximum_flow_mm3_s"]==pytest.approx(AREA)


@pytest.mark.parametrize("suffix",["G1 X10 E1","M82\nG1 X10 E1 F600","M83\nG1 X10 E1 F600",
                                   "M83\nG1 E1","M83\nG1 E1 F0","M83\nG1 E1 Fnan",
                                   "M83\nG1 E1 Finf","M83\nG1 E1 F600 F60","M83\nG1 E1 Q7 F60"])
def test_missing_state_or_nonfinite_invalid_parameters_fail_closed(suffix):
    with pytest.raises(ValueError):validate_extrusion_flow(suffix,5)


def test_relative_xyz_distance_can_be_bounded_without_absolute_machine_position():
    result=validate_extrusion_flow("G91\nM83\nG1 X10 E1 F600",5)
    assert result["maximum_flow_mm3_s"]==pytest.approx(AREA)


@pytest.mark.parametrize("text",["G20\nM83\nG1 E1 F60","M200 D1.75","M200 D0","G93","G95","G51 X0.1",
                                 "M83\nG1 E1 F60\nE100 F600"])
def test_unmodeled_units_scaling_and_implicit_motion_are_rejected(text):
    with pytest.raises(ValueError):validate_extrusion_flow(text,5)


def test_feed_and_flow_boosts_are_applied_while_reductions_cannot_evade_cap():
    for multiplier in ("M220 S300","M221 S300"):
        with pytest.raises(ValueError,match="exceeds"):
            validate_extrusion_flow(START+multiplier+"\nG1 X10 E1 F600",5)
    with pytest.raises(ValueError,match="exceeds"):
        validate_extrusion_flow("M83\nM221 S10\nM220 S10\nG1 E10 F600",5)


@pytest.mark.parametrize("command",["G2 X0 Y10 I0 J5","G3 X0 Y10 I0 J5","G2 X0 Y10 R5","G3 X0 Y10 R-6"])
def test_xy_arcs_use_path_length_and_bound_their_extrusion(command):
    result=validate_extrusion_flow(START+command+" E1 F600",5)
    assert result["arc_extrusion_moves"]==1 and 0<result["maximum_flow_mm3_s"]<AREA
    with pytest.raises(ValueError,match="exceeds"):
        validate_extrusion_flow(START+command+" E100 F600",5)


def test_full_circle_and_helical_arc_and_absolute_center():
    circle=validate_extrusion_flow(START+"G2 I5 J0 E1 F600",5)
    assert circle["maximum_flow_mm3_s"]==pytest.approx(AREA*10/(2*math.pi*5))
    helix=validate_extrusion_flow(START+"G90.1\nG3 X0 Y10 Z10 I0 J5 E1 F600",5)
    assert helix["maximum_flow_mm3_s"]==pytest.approx(AREA*10/math.hypot(math.pi*5,10))


@pytest.mark.parametrize("arc",["G2 X10 E1 F600","G2 X10 R1 E1 F600","G2 X10 I0 J1 E1 F600",
                                "G2 X0 Y10 I0 J5 R5 E1 F600","G2 X0 Y10 I0 J5 P2 E1 F600",
                                "G18\nG2 X0 Y10 I0 J5 E1 F600"])
def test_ambiguous_invalid_or_unsupported_arcs_are_rejected(arc):
    with pytest.raises(ValueError):validate_extrusion_flow(START+arc,5)


def test_all_conditional_arms_are_checked_even_before_layer_markers():
    with pytest.raises(ValueError,match="exceeds"):
        validate_extrusion_flow(START+"M622 J0\nG1 E10 F600\nM623",5)
    result=validate_extrusion_flow(START+"M622 J0\nG1 E1 F60\nM623\nG1 E1 F60",5)
    assert result["conditional_blocks_checked"]==1 and result["checked_extrusion_moves"]==2


def test_skippable_branch_cannot_supply_missing_feed_or_absolute_position():
    for text in ("M83\nM622 J1\nG1 F60\nM623\nG1 E1",
                 "G90\nM83\nM622 J1\nG0 X0 Y0 Z0 F600\nM623\nG1 X10 E1 F600"):
        with pytest.raises(ValueError):validate_extrusion_flow(text,5)
    # The skipped route retains F600, so F60 inside an optional branch cannot
    # hide the faster E-only purge after the branch.
    with pytest.raises(ValueError,match="exceeds"):
        validate_extrusion_flow("M83\nG1 F600\nM622 J1\nG1 F60\nM623\nG1 E1",5)


@pytest.mark.parametrize("text",["M623","M622 J1\nM83\nG1 E1 F60"])
def test_unmatched_conditionals_are_rejected(text):
    with pytest.raises(ValueError,match="conditional"):validate_extrusion_flow(text,5)


@pytest.mark.parametrize("command",["M620.1 E F600 T240","M620.10 A1 F600 L50 H0.4 T240",
                                    "M620.11 S1 E20 F600","M983 F10 A0.3 H0.4",
                                    "M9833 F10 A0.3","M984 A0.1 E1 S1 F10 H0.4"])
def test_known_vendor_extrusion_rates_use_conservative_template_unit_inference(command):
    with pytest.raises(ValueError,match="exceeds"):
        validate_extrusion_flow(command,5)
    safe=command.replace("F600","F60").replace("F10","F1")
    result=validate_extrusion_flow(safe,5)
    assert result["vendor_extrusion_rate_commands"]==1
    assert result["maximum_flow_mm3_s"]==pytest.approx(AREA)


def test_retraction_configuration_disabled_macro_and_decimal_commands_stay_distinct():
    result=validate_extrusion_flow("M620.11 S0\nM620.11 S1 E-10 F1200\nM622.1 S1",5)
    assert result["vendor_extrusion_rate_commands"]==0 and result["conditional_blocks_checked"]==0


@pytest.mark.parametrize("text",["M983 A0.3","M620.1 E Fnan","G150 T240","G10","G11","M999 E100 F600"])
def test_unbounded_firmware_extrusion_macros_are_rejected(text):
    with pytest.raises(ValueError):validate_extrusion_flow(text,5)


def test_compact_numbered_motion_comments_and_175_or_285_filament():
    result=validate_extrusion_flow("(comment only)\nG90\nM83\nN1G0X0Y0Z0F600*1\nN2G1X10E1F600*2",10,2.85)
    assert result["maximum_flow_mm3_s"]==pytest.approx(math.pi*(2.85/2)**2)


@pytest.mark.parametrize("cap,diameter",[(0,1.75),(float("nan"),1.75),(5,0),(True,1.75),(5,float("inf")),(5,100)])
def test_invalid_calibration_inputs_are_rejected(cap,diameter):
    with pytest.raises(ValueError):validate_extrusion_flow("M83\nG1 E1 F60",cap,diameter)


def test_current_p2s_decimal_dynamic_calibration_and_retraction_configuration():
    with pytest.raises(ValueError, match="exceeds"):
        validate_extrusion_flow("M983.3 F10 A0.4", 5)
    result = validate_extrusion_flow("M620.10 R1.2\nM983.3 F1 A0.4", 5)
    assert result["vendor_extrusion_rate_commands"] == 1


def test_extruder_acceleration_and_velocity_limits_do_not_extrude():
    result = validate_extrusion_flow("M201 E5000\nM203 E100\nM204 E1000\nM205 E10", 5)
    assert result["checked_extrusion_moves"] == 0
