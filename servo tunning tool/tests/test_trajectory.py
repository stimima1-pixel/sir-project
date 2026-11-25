"""sdr_tuner.gui.trajectory - pure arc math, no Qt, no GUI dependency. See gcode.py/gcode_runner.py for how a
G02/G03 line's own two axes (or a Txx chain's own two Cartesian components) become the `start`/`end` this module
works with."""
import math

import pytest

from sdr_tuner.gui import trajectory


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def test_a_quarter_circle_by_radius_lands_exactly_on_the_target():
    # (100, 0) -> (0, 100), CCW (G03), around the origin: a textbook quarter circle, radius 100.
    points = trajectory.arc_waypoints((100.0, 0.0), (0.0, 100.0), clockwise=False, radius=100.0)
    assert points[-1] == (0.0, 100.0)                                  # exact, not a trig round-off
    for p in points:
        assert _dist(p, (0.0, 0.0)) == pytest.approx(100.0, abs=1e-6)  # every waypoint stays on the circle


def test_more_waypoints_for_a_bigger_sweep_each_step_no_more_than_the_limit():
    quarter = trajectory.arc_waypoints((100.0, 0.0), (0.0, 100.0), clockwise=False, radius=100.0)
    half = trajectory.arc_waypoints((100.0, 0.0), (-100.0, 0.0), clockwise=False, radius=100.0)
    assert len(half) > len(quarter)
    for pts, start in ((quarter, (100.0, 0.0)), (half, (100.0, 0.0))):
        prev = start
        for p in pts:
            step_deg = math.degrees(abs(trajectory._signed_angle(prev, p, (0.0, 0.0))))
            assert step_deg <= trajectory.MAX_STEP_DEG + 1e-6
            prev = p


def test_clockwise_and_counterclockwise_go_opposite_ways_round():
    # (100,0) -> (-100,0), radius 100: the chord IS the diameter, so there is only one possible circle (centred on
    # the origin) - CCW and CW must therefore trace its two different semicircles, one through (0,100), the other
    # through (0,-100), meeting only at the shared start/end.
    ccw = trajectory.arc_waypoints((100.0, 0.0), (-100.0, 0.0), clockwise=False, radius=100.0)
    cw = trajectory.arc_waypoints((100.0, 0.0), (-100.0, 0.0), clockwise=True, radius=100.0)
    mid = len(ccw) // 2 - 1                    # the exact halfway waypoint (90 degrees of a 180-degree sweep)
    assert _dist(ccw[mid], (0.0, 100.0)) < 1e-6
    assert _dist(cw[mid], (0.0, -100.0)) < 1e-6


def test_negative_radius_picks_the_major_arc_positive_the_minor_one():
    minor = trajectory.arc_waypoints((100.0, 0.0), (0.0, -100.0), clockwise=False, radius=100.0)
    major = trajectory.arc_waypoints((100.0, 0.0), (0.0, -100.0), clockwise=False, radius=-100.0)
    assert len(major) > len(minor)                        # the major (>180 degree) arc has more, smaller steps
    assert major[-1] == (0.0, -100.0) == minor[-1]         # both still land exactly on the same target


def test_centre_offset_format_is_relative_to_the_start_point():
    # "G03 X500 Y500 I1000 J0" is the reference's own syntax example for this format (I/J are an OFFSET from
    # the start to the centre, not an absolute point) - checked here with numbers that actually land back on the
    # same circle (the reference's own example numbers do not - I1000/J0 from (0,0) would put the centre 1000 away
    # while the end (500,500) is only 707 from that centre - fine for illustrating the syntax, not for a geometry
    # check), start=(0,0), centre offset (0,100) -> centre (0,100), radius 100, end (100,100) sits exactly on it.
    start = (0.0, 0.0)
    end = (100.0, 100.0)
    center = trajectory.arc_center(start, end, clockwise=False, offset=(0.0, 100.0))
    assert center == (0.0, 100.0)
    points = trajectory.arc_waypoints(start, end, clockwise=False, offset=(0.0, 100.0))
    assert points[-1] == end
    for p in points:
        assert _dist(p, center) == pytest.approx(_dist(start, center), abs=1e-6)


def test_a_full_circle_needs_a_centre_offset_not_a_radius():
    with pytest.raises(trajectory.TrajectoryError, match="full circle"):
        trajectory.arc_waypoints((100.0, 0.0), (100.0, 0.0), clockwise=False, radius=100.0)
    # the offset form has no such problem - it sweeps the full 360 degrees instead.
    full = trajectory.arc_waypoints((100.0, 0.0), (100.0, 0.0), clockwise=False, offset=(-100.0, 0.0))
    assert full[-1] == (100.0, 0.0)
    assert len(full) == math.ceil(360 / trajectory.MAX_STEP_DEG)


def test_a_radius_too_small_to_reach_the_target_is_refused_with_a_clear_reason():
    with pytest.raises(trajectory.TrajectoryError, match="too far"):
        trajectory.arc_waypoints((0.0, 0.0), (1000.0, 0.0), clockwise=False, radius=10.0)


def test_needs_exactly_one_of_radius_or_offset():
    with pytest.raises(trajectory.TrajectoryError, match="exactly one"):
        trajectory.arc_center((0.0, 0.0), (10.0, 0.0), clockwise=False)
    with pytest.raises(trajectory.TrajectoryError, match="exactly one"):
        trajectory.arc_center((0.0, 0.0), (10.0, 0.0), clockwise=False, radius=10.0, offset=(5.0, 0.0))


# ---- audit fixes (2026-09-25) --------------------------------------------------------------------------------
def test_a_centre_offset_the_end_point_is_not_on_is_refused():
    """'G02 X100 Y0 I10 J0' from (0, 0) used to trace a radius-10 circle and then JUMP the last 80 in a straight
    line to the end; 'I0 J0' gave 23 waypoints at the start point and one jump."""
    import pytest
    from sdr_tuner.gui import trajectory as t
    with pytest.raises(t.TrajectoryError, match="same circle"):
        t.arc_center((0.0, 0.0), (100.0, 0.0), True, offset=(10.0, 0.0))
    with pytest.raises(t.TrajectoryError, match="same circle"):
        t.arc_waypoints((0.0, 0.0), (100.0, 0.0), True, offset=(0.0, 0.0))
    assert t.arc_center((0.0, 0.0), (20.0, 0.0), True, offset=(10.0, 0.0)) == (10.0, 0.0)     # on it: fine


def test_a_chord_exactly_the_diameter_survives_float_noise():
    from sdr_tuner.gui import trajectory as t
    assert t.arc_waypoints((1.1, 0.0), (1.4, 0.0), True, radius=0.15)[-1] == (1.4, 0.0)      # 0.30000000000000004 > 0.3
