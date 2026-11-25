"""Discretises a circular arc (G02/G03) into evenly-spaced waypoints - pure math, no Qt, mirrors gcode.py's and
kinematics.py's own style (testable standalone via tests/test_trajectory.py, no GUI dependency at all).

This module only ever sees ONE 2D circle, in whatever plane a caller has already chosen - gcode_runner.py is the
one that picks the plane (two of the drive's own raw axes for a plain G02/G03, or two Cartesian components of a
kinematics chain's own tool tip for a Txx-qualified one - see kinematics.py) and fills in the third, unmentioned
coordinate (this tool does not track a G17/G18/G19 modal plane, so the plane is simply whichever two axis letters
the line itself gives a target for - the vendor's own real example, `G03 T6 Z-400 X0 R500`, does exactly this).

**Radius (R) and centre-offset (I/J/K) formats are both supported** - matching the two forms the reference PDF
itself gives (see the arc tests this module let tests/test_gcode.py stop refusing). R is signed: positive picks
the arc that sweeps <=180 degrees, negative the one that sweeps >180 degrees - the standard G-code convention,
resolved here from first principles (both candidate centres are computed, and the one whose own start->end sweep
matches the sign this convention needs is picked - see `arc_center`) rather than by copying any one G-code
implementation's own bit-tricks, which are easy to misremember and hard to verify from the formula alone.
"""
import math

MAX_STEP_DEG = 15.0     # a waypoint at least every 15 degrees of sweep - smooth enough to see, cheap enough to send


class TrajectoryError(ValueError):
    pass


def _normalize_angle(theta):
    """-> radians in (-pi, pi]."""
    theta = math.fmod(theta, 2 * math.pi)
    if theta <= -math.pi:
        theta += 2 * math.pi
    elif theta > math.pi:
        theta -= 2 * math.pi
    return theta


def _signed_angle(start, end, center):
    """The signed angle (radians, in (-pi, pi]) from vector (start-center) to vector (end-center) - positive means
    counterclockwise, matching the usual (x, y) convention (angles increasing from the +x axis towards +y)."""
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    return _normalize_angle(a1 - a0)


def arc_center(start, end, clockwise, radius=None, offset=None):
    """-> (cx, cy), the arc's own centre - exactly one of `radius` (signed R-format) or `offset` (an (i, j)
    displacement from START to the centre - I/J/K format) must be given. Raises TrajectoryError if neither/both
    are given, if `end` is unreachable from `start` with the given `radius` (the widest chord a circle has is its
    own diameter), or if `start == end` with R-format (a radius alone cannot tell a full circle apart from "already
    there" - a centre offset can, and does: see `arc_waypoints`)."""
    if (radius is None) == (offset is None):
        raise TrajectoryError("an arc needs exactly one of a radius (R) or a centre offset (I/J/K)")
    sx, sy = start
    ex, ey = end
    if offset is not None:
        cx, cy = sx + offset[0], sy + offset[1]
        r_start, r_end = math.hypot(sx - cx, sy - cy), math.hypot(ex - cx, ey - cy)
        if abs(r_end - r_start) > 1e-3 * r_start + 0.5:
            # a centre offset that the END point is not on: 'G02 X100 Y0 I10 J0' from (0, 0) used to trace a
            # radius-10 circle and then JUMP the last 80 in a straight line to the end (arc_waypoints pins the
            # last point to `end`), 'I0 J0' 23 waypoints at the start and one jump - refused instead
            raise TrajectoryError(f"the end point is {r_end:.3f} from the centre the offset describes, the start "
                                  f"{r_start:.3f} - both must lie on the same circle")
        return cx, cy
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy)
    if dist == 0:
        raise TrajectoryError("the start and end are the same point - a radius (R) cannot describe a full circle, "
                              "use a centre offset (I/J/K) instead")
    r = abs(radius)
    if dist > 2 * r * (1 + 1e-9):        # a hair of tolerance: (1.1, 0) -> (1.4, 0) with R0.15 is a diameter exactly,
                                          # but reads as 0.30000000000000004 > 0.3 - and FK-derived Txx starts hit this
        raise TrajectoryError(f"the target is {dist:.3f} away - too far for a radius of {r:.3f} to reach (a "
                              "circle's own widest chord is its diameter)")
    h = math.sqrt(max(0.0, r * r - (dist / 2) ** 2))
    mx, my = (sx + ex) / 2, (sy + ey) / 2
    px, py = -dy / dist, dx / dist                       # unit vector, start->end rotated +90 degrees (CCW)
    candidates = ((mx + h * px, my + h * py), (mx - h * px, my - h * py))
    want_positive = (not clockwise) == (radius > 0)      # see the module docstring's own derivation of this rule
    for c in candidates:
        if h == 0 or (_signed_angle(start, end, c) > 0) == want_positive:
            return c
    return candidates[-1]        # unreachable in practice (one of the two always matches) - a safe fallback


def arc_waypoints(start, end, clockwise, radius=None, offset=None, max_step_deg=MAX_STEP_DEG):
    """-> [(a, b), ...]: points along the arc from `start` to `end` around whichever centre `arc_center` finds,
    evenly spaced by angle (each step at most `max_step_deg`) - NOT including `start` itself (a caller dispatching
    one drive move per waypoint already knows where it is) but always ending with `end` EXACTLY (not a trig
    round-off of it), so the last move lands precisely on the commanded target. A centre OFFSET (I/J/K) with
    `start == end` is a full circle - handled correctly (sweeping the full 360 degrees in the given direction),
    unlike R-format, which `arc_center` refuses outright for the same input, since a radius alone can't tell the
    two apart."""
    center = arc_center(start, end, clockwise, radius=radius, offset=offset)
    r = math.hypot(start[0] - center[0], start[1] - center[1])
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    theta = _signed_angle(start, end, center)      # in (-pi, pi], CCW-positive
    if not clockwise and theta <= 0:
        theta += 2 * math.pi           # go the long way round, CCW (also correctly turns theta==0 into a full circle)
    elif clockwise and theta >= 0:
        theta -= 2 * math.pi           # go the long way round, CW (ditto)
    steps = max(1, math.ceil(abs(math.degrees(theta)) / max_step_deg))
    points = [(center[0] + r * math.cos(a0 + theta * i / steps), center[1] + r * math.sin(a0 + theta * i / steps))
             for i in range(1, steps + 1)]
    points[-1] = end
    return points
