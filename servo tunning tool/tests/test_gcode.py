"""G-code parsing - sdr_tuner.gui.gcode. Examples are taken from the drive maker's G-code reference (December 2023) (the user
shared it directly) wherever the reference gives one, so this also serves as a check against the real dialect.

parse() itself does not know which node is connected - it only says WHAT a line means (a P<n> axis selector, or a
set of {axis: value} targets); matching that against the connected drive is gcode_runner.py's job (see
test_gui.py's own G-code / Motion Controller tests for that part), per the user's own correction: "axis means node
id so node id of servo drive and axis of G-code has to match otherwise code will not run"."""
import pytest

from sdr_tuner.gui import gcode


def test_the_single_value_commands_parse_axis_selector_and_bracket_forms_the_same_way():
    assert gcode.parse("G1030 X150") == {"action": "esc_rpm", "axes": {0: (150.0, False)}}       # X = axis 0
    assert gcode.parse("G1030 [0]500") == {"action": "esc_rpm", "axes": {0: (500.0, False)}}     # the reference's own example
    assert gcode.parse("G1030 [7]500") == {"action": "esc_rpm", "axes": {7: (500.0, False)}}     # any axis/node, e.g. 7
    assert gcode.parse("G1030 [0]3000 [1]-1000 [10]12000") == {
        "action": "esc_rpm", "axes": {0: (3000.0, False), 1: (-1000.0, False), 10: (12000.0, False)}}
    assert gcode.parse("G1040 [0]0.14 [1]-1.2 [10]0.75") == {
        "action": "torque", "axes": {0: (0.14, False), 1: (-1.2, False), 10: (0.75, False)}}


def test_reset_off_stop_capture_release_carry_their_own_axis_selector():
    assert gcode.parse("G1000") == {"action": "reset", "axis": None}             # None = all axes
    assert gcode.parse("G1000 P5") == {"action": "reset", "axis": 5}             # "Reset Axis 5 only"
    assert gcode.parse("G1001") == {"action": "off", "axis": None}
    assert gcode.parse("G1001 P5") == {"action": "off", "axis": 5}
    assert gcode.parse("G1002") == {"action": "stop", "axis": None}
    assert gcode.parse("G1010") == {"action": "capture", "axis": None}
    assert gcode.parse("G1010 P0") == {"action": "capture", "axis": 0}
    assert gcode.parse("G1011") == {"action": "release", "axis": None}


def test_move_and_servo_move_accept_a_target_by_letter_or_bracket():
    assert gcode.parse("G00 X-100 [5]100 [6]200 [7]0 [8]-90") == {
        "action": "move", "axes": {0: (-100.0, False), 5: (100.0, False), 6: (200.0, False), 7: (0.0, False), 8: (-90.0, False)}}
    assert gcode.parse("G1020 X400.5 Y-119.785 Z100") == {
        "action": "move", "axes": {0: (400.5, False), 1: (-119.785, False), 2: (100.0, False)}}    # X,Y,Z = axes 0,1,2
    assert gcode.parse("G1020 [0]1000.5 [1]-40.18") == {"action": "move", "axes": {0: (1000.5, False), 1: (-40.18, False)}}


def test_u_v_w_are_relative_x_y_z_are_absolute_same_axes():
    assert gcode.parse("G00 U-10") == {"action": "move", "axes": {0: (-10.0, True)}}     # U is the incremental version of X (axis 0)
    assert gcode.parse("G00 X-10") == {"action": "move", "axes": {0: (-10.0, False)}}
    assert gcode.parse("G00 V5") == {"action": "move", "axes": {1: (5.0, True)}}          # V -> axis 1, like Y


def test_linear_move_needs_both_a_target_and_a_feed_rate():
    assert gcode.parse("G01 X1000 F500") == {"action": "linear_move", "axes": {0: (1000.0, False)}, "feed_rate": 500.0}
    assert gcode.parse("G01 U-10 F500") == {"action": "linear_move", "axes": {0: (-10.0, True)}, "feed_rate": 500.0}
    with pytest.raises(gcode.GcodeError, match="needs a target and a feed rate"):
        gcode.parse("G01 X-100 Y200 Z-100")                          # the reference's own example has no F: modal G93/G94 supply it there


def test_dwell_needs_a_duration():
    assert gcode.parse("G04 P2.5") == {"action": "dwell", "seconds": 2.5}
    with pytest.raises(gcode.GcodeError, match="needs a duration"):
        gcode.parse("G04")


def test_comments_are_stripped_like_the_real_dialect():
    assert gcode.parse("G1030 X150\t(Axis 0, Electronic Speed Control 150 RPM)") == {"action": "esc_rpm", "axes": {0: (150.0, False)}}
    assert gcode.parse("g1030 x150 (lowercase, with a comment)") == {"action": "esc_rpm", "axes": {0: (150.0, False)}}
    with pytest.raises(gcode.GcodeError, match="empty command"):
        gcode.parse(";a whole-line comment")


def test_arc_moves_parse_with_a_radius_or_a_centre_offset():
    """G02 (clockwise) / G03 (counterclockwise) - see trajectory.py for the actual geometry this feeds. F is
    optional here, unlike G01 - a "rapid" arc with no explicit rate (the vendor's own real example, checked below,
    never gives one)."""
    assert gcode.parse("G02 X1000 Y2000 R1000 F500") == {
        "action": "arc", "clockwise": True, "plane": (0, 1), "end": (1000.0, 2000.0), "radius": 1000.0,
        "offset": None, "feed_rate": 500.0}
    assert gcode.parse("G03 X500 Y500 I1000 J0 F500") == {                       # the reference's own I/J example
        "action": "arc", "clockwise": False, "plane": (0, 1), "end": (500.0, 500.0), "radius": None,
        "offset": (1000.0, 0.0), "feed_rate": 500.0}
    assert gcode.parse("G02 [4]90 [5]45 R50") == {                               # any axis pair, no F given
        "action": "arc", "clockwise": True, "plane": (4, 5), "end": (90.0, 45.0), "radius": 50.0, "offset": None}


def test_arc_moves_need_exactly_two_axes_and_a_radius_or_offset():
    with pytest.raises(gcode.GcodeError, match="needs exactly two axes.*found 1"):
        gcode.parse("G02 X100 R50")
    with pytest.raises(gcode.GcodeError, match="needs exactly two axes.*found 3"):
        gcode.parse("G02 X100 Y200 Z300 R50")
    with pytest.raises(gcode.GcodeError, match="needs a radius .R. or a centre offset"):
        gcode.parse("G02 X100 Y200")
    with pytest.raises(gcode.GcodeError, match="only pairs with axes 0/1/2"):
        gcode.parse("G02 [4]90 [5]45 I10 J10")                                   # I/J/K only means X/Y/Z here
    with pytest.raises(gcode.GcodeError, match="does not support a relative"):
        gcode.parse("G02 U10 Y20 R50")                                          # U/V/W arc targets are refused, not guessed


def test_txx_arcs_take_exactly_two_of_x_y_z_the_third_held_by_gcode_runner():
    """This is the vendor's own real DOG.NC line, verbatim - no F (a modal G93/G94 this tool does not track set
    the real system's own rate; here it just means a rapid arc, see the module docstring). The plane comes out
    Z THEN X - G18's own ZX ordering, a right-handed pair, so this G03 lifts the foot (Z -400 -> -200 mid-arc)
    the way a gait swing must; ordered X then Z it was mirrored and dipped it 200 into the ground (2026-09-25,
    checked numerically against trajectory.arc_waypoints - see gcode._plane_order)."""
    assert gcode.parse("G03 T6 Z-400 X0 R500") == {
        "action": "tool_arc", "tool": 6, "clockwise": False, "plane": ("Z", "X"), "end": (-400.0, 0.0),
        "radius": 500.0, "offset": None}
    with pytest.raises(gcode.GcodeError, match="needs exactly two of X/Y/Z"):
        gcode.parse("G02 T6 R500")
    with pytest.raises(gcode.GcodeError, match="needs exactly two of X/Y/Z"):
        gcode.parse("G02 T6 X0 Y100 Z-400 R500")


def test_txx_arcs_also_take_a_centre_offset_matched_to_their_own_two_letters():
    """I pairs with X, K with Z here (J, unused by this X/Z-plane example, is simply not given) - NOT the plane's
    own X/Z target values again (a real bug this test would have caught: I/J/K name the OFFSET, a different pair
    of numbers from the target `_cartesian_pair` already extracted)."""
    assert gcode.parse("G03 T6 X0 Z-400 I50 K-50") == {
        "action": "tool_arc", "tool": 6, "clockwise": False, "plane": ("Z", "X"), "end": (-400.0, 0.0),
        "radius": None, "offset": (-50.0, 50.0)}       # (Z, X) order - K then I - see the test above


def test_txx_parses_a_tool_move_with_a_cartesian_target():
    """Txx (Inverse Kinematics) is config-driven, not refused - see kinematics.py/gcode_runner.py for the actual
    solving and dispatch; parse() itself only extracts the tool number and the Cartesian target."""
    assert gcode.parse("G00 T3 X-159 Y-400 Z800") == {"action": "tool_move", "tool": 3, "target": (-159.0, -400.0, 800.0)}
    assert gcode.parse("G1020 T5 X100") == {"action": "tool_move", "tool": 5, "target": (100.0, 0.0, 0.0)}   # Y/Z default 0


def test_txx_with_g01_needs_a_feed_rate_like_any_other_linear_move():
    assert gcode.parse("G01 T3 X-159 Y-400 Z800 F500") == {
        "action": "tool_move", "tool": 3, "target": (-159.0, -400.0, 800.0), "feed_rate": 500.0}
    with pytest.raises(gcode.GcodeError, match="needs a feed rate"):
        gcode.parse("G01 T3 X-159 Y-400 Z800")


def test_txx_needs_an_xyz_target():
    with pytest.raises(gcode.GcodeError, match="needs an X/Y/Z target"):
        gcode.parse("G00 T3")


def test_txx_is_refused_on_anything_but_a_move():
    with pytest.raises(gcode.GcodeError, match="only works with G00, G01 or G1020"):
        gcode.parse("G1030 T3 X100")


def test_more_than_one_g_code_on_a_line_is_refused_by_parse_use_parse_line_instead():
    """parse() itself is unchanged - still exactly one G-code per call, for a caller with no reason to expect more
    (every existing test in this file uses it this way). Concatenated lines are parse_line()'s own job now - see
    the tests below - not something parse() started guessing at."""
    with pytest.raises(gcode.GcodeError, match="only one G-code per line here"):
        gcode.parse("G93 F60 G01 [5]100")
    with pytest.raises(gcode.GcodeError, match="only one G-code per line here"):
        gcode.parse("G09 G01 X100.50")


def test_parse_line_splits_the_vendor_own_multi_axis_gait_line_into_one_action_per_g_code():
    """DOG.NC's own real line, verbatim (minus an F on the two G01/Txx moves - this tool already needs one for
    ANY G01, Txx or not, unrelated to this feature): four tab-separated commands, each its own Txx, meant to move
    four legs together in one synchronised step."""
    actions = gcode.parse_line("G01 T3 X-800 Y159 Z-400 F300\tG01 T12 X100 Y-159 Z-400 F300\t"
                               "G03 T6 Z-400 X0 R500\tG03 T9 Z-400 X800 R500")
    assert [a["action"] for a in actions] == ["tool_move", "tool_move", "tool_arc", "tool_arc"]
    assert [a["tool"] for a in actions] == [3, 12, 6, 9]
    assert actions[0]["target"] == (-800.0, 159.0, -400.0)
    assert actions[2]["plane"] == ("Z", "X") and actions[2]["end"] == (-400.0, 0.0)     # G18's ZX order, see above


def test_parse_line_matches_ugv_ncs_own_synchronised_two_axis_move():
    assert gcode.parse_line("G01 [4]90 [5]90 F30") == [
        {"action": "linear_move", "axes": {4: (90.0, False), 5: (90.0, False)}, "feed_rate": 30.0}]


def test_parse_line_with_exactly_one_g_code_matches_parse_exactly():
    for line in ("G1030 X150", "G00 T3 X100 Y0 Z50", "G02 X1000 Y2000 R1000 F500", "G1000"):
        assert gcode.parse_line(line) == [gcode.parse(line)]


def test_parse_line_still_refuses_a_line_with_no_g_code_at_all():
    with pytest.raises(gcode.GcodeError, match="no G-code found"):
        gcode.parse_line("X100 Y200")
    with pytest.raises(gcode.GcodeError, match="empty command"):
        gcode.parse_line("")


def test_parse_line_lets_one_segments_own_error_surface_with_its_segment_still_identifiable():
    with pytest.raises(gcode.GcodeError, match="needs a feed rate"):
        gcode.parse_line("G1030 X150\tG01 T3 X100")     # the second segment, on its own, is missing F


def test_untracked_modal_codes_are_harmless_noops():
    for line in ("G09", "G20", "G21", "G61", "G64", "G93", "G94"):
        result = gcode.parse(line)
        assert result["action"] == "noop" and result["text"].startswith(line) and "not tracked" in result["text"]


def test_g90_g91_explain_the_relative_alternative_instead_of_doing_nothing_silently():
    for line in ("G90", "G91"):
        result = gcode.parse(line)
        assert result["action"] == "noop" and "relative" in result["text"]


def test_g1050_is_a_harmless_noop():
    assert gcode.parse("G1050")["action"] == "noop"


def test_unknown_or_malformed_lines_are_refused_clearly():
    with pytest.raises(gcode.GcodeError, match="no G-code found"):
        gcode.parse("garbage")
    with pytest.raises(gcode.GcodeError, match="empty command"):
        gcode.parse("")
    with pytest.raises(gcode.GcodeError, match="G500 is not one of"):
        gcode.parse("G500 X1")


# ---- audit fixes (2026-09-25) --------------------------------------------------------------------------------
def test_numbers_must_be_finite_and_a_feed_rate_positive():
    """float() happily reads NAN/INF: 'RNAN' used to become a semicircle and 'G04 PINF' an OverflowError inside a
    timer; F0 and F-5 went to the drive as they were, a "linear" move that never arrives."""
    with pytest.raises(gcode.GcodeError, match="not a finite number"):
        gcode.parse("G02 X10 Y0 RNAN")
    with pytest.raises(gcode.GcodeError, match="not a finite number"):
        gcode.parse("G04 PINF")
    for line in ("G01 X100 F0", "G01 X100 F-5"):
        with pytest.raises(gcode.GcodeError, match="feed rate must be positive"):
            gcode.parse(line)
