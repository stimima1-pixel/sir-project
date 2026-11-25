"""The drive's G-code dialect, translated into this drive's own CAN commands.

Source: the drive maker's G-code reference (revision D, December 2023), which the user shared directly. Earlier,
fragments of the same dialect (G1000, G1010, G1030, G04, G00/G01/G02/G03, G93/G94) were found embedded in the
vendor's own Servo-Drive-IDE.exe - see the README for that trail.

**Axis identifies which drive a line affects - but axis is NOT the same number as the drive's own CAN node ID.**
The user's own two corrections, together: (2026-09-22) "axis means node id so node id of servo drive and axis of
G-code has to match otherwise code will not run"; (2026-09-23, refining that) "node id 1 means axis 0, node is 2
means axis 1" - CAN node IDs are never 0 (1-127 only), so axis 0 (the first axis) is simply wired as the first
valid node: **node ID = axis + 1**, uniformly. X/Y/Z/A/B/C name axes 0-5, U/V/W the same axes 0-2 incrementally,
and [N]/P<n> name axis N explicitly - on the real (multi-axis) system, a line only ever affects the axis(es) it
names; a line naming an axis that is not the one you are connected to does nothing to your drive, exactly as it
would do nothing to that OTHER axis's drive on a real system. This tool only ever has ONE connected drive, so:
  - `parse()` only decides WHAT a line means - a P<n> axis selector or a set of {axis: value} targets, always in
    AXIS terms (0-based) - and does NOT know or check which node is connected, or even that axis and node are
    different numbers; converting an axis into the real node ID it is wired as, and matching that against the
    connected drive's own node ID, both happen in `gcode_runner.py`'s own `_axis_to_node()`/`_node_to_axis()`,
    which does know, at the moment a line actually runs.
  - a P<n>-style command (Reset/Off/Stop/Capture/Release) with NO axis at all means "all axes", which includes
    whichever one you are connected to - so those still run.
  - a value command (Move/Linear Move/Speed/Torque) with several axes on the same line (a real line addressing
    several drives at once) has its OWN axis's entry picked out, if any is present, once run() knows the node ID;
    if none of the line's axes is the connected one, the line does not run - not a guess, an accurate reflection of
    what would happen on the real multi-axis system.
  - **Txx (Inverse Kinematics)** IS supported, config-driven: `Txx` on a G00/G01/G1020 line names a chain in a
    loaded `.kinematics` model (Item Configuration's Kinematics Model group - see `sdr_tuner/kinematics.py`) and
    the line's X/Y/Z become a Cartesian target for that chain's own tool tip, solved into each of the chain's own
    joints (gcode_runner.py does the solving and the multi-axis dispatch, since it is the one place that knows
    which axes are actually connected - `parse()` here only extracts the tool number and the Cartesian target).
  - **Several G-codes concatenated on one physical line** ARE supported - the vendor's own real multi-axis
    programs do this (`DOG.NC`'s own gait: four tab-separated `G01`/`G03` commands, each with its own `Txx`, on one
    line, moving four legs together). `parse()` still only ever accepts ONE G-code per line, unchanged since before
    this existed; `parse_line()` is the new entry point that splits a line into its own concatenated segments and
    parses each with the exact same per-command logic (`_parse_segment`), returning a list - see its own docstring.
  - **G02/G03 (arc/circle interpolation)** IS supported, both the radius (R) and centre-offset (I/J/K) forms the
    reference gives - see `sdr_tuner/gui/trajectory.py` for the actual geometry. This tool does not track a
    G17/G18/G19 modal plane, so the arc's own plane is simply whichever two of X/Y/Z (or, for a plain non-Txx arc,
    whichever two axis letters/brackets) the line itself gives a target for - exactly what the vendor's own real
    example does (`G03 T6 Z-400 X0 R500`: X and Z given, Y not - the arc lies in the XZ plane, whatever Y already
    is, held constant through it - `gcode_runner.py` fills that in, since `parse()` itself has no idea what Y
    currently is). An `F` (feed rate) is optional here too, exactly like G00/G01: without one the waypoints are
    dispatched back-to-back, as fast as the bus allows (a "rapid" arc, matching G00 - the vendor's own `DOG.NC`
    never gives arcs an `F` of their own, relying on a modal `G93`/`G94` this tool does not track); with one, each
    waypoint is timed by it, like G01.
  - G-codes about units/modal state this tool does not track (G09/G20/G21/G61/G64/G93/G94/G90/G91) are accepted as
    no-ops with a note, so a line copied from a real program does not hard-fail on unrelated modal setup.
"""
import math
import re

COMMENT_RE = re.compile(r";.*$|\([^)]*\)")
ABS_AXIS_LETTERS = {"X": 0, "Y": 1, "Z": 2, "A": 3, "B": 4, "C": 5}     # absolute target; letter -> axis (node) index
REL_AXIS_LETTERS = {"U": 0, "V": 1, "W": 2}                             # relative version of X/Y/Z, same axes
BRACKET_TOKEN_RE = re.compile(r"^\[(\d+)\](-?[\d.]+)?$")                # '[5]100' (value attached) or '[5]' (bare)
TOOL_RE = re.compile(r"^T(\d+)$")                                       # Txx: a kinematics chain's own tool number
TOOL_MOVE_CODES = {0, 1, 1020}                                          # G-codes Txx can attach to for a MOVE
                                                                         # (G02/G03 - an arc - are handled on their
                                                                         # own, earlier, whether or not Txx-qualified)
NOOP_CODES = {9: "Exact Stop", 20: "Use Inches", 21: "Use Millimeters", 61: "Enable Exact Stop",
             64: "Disable Exact Stop", 93: "Speed in Moves per Minute",
             94: "Speed in Millimeters/Inches/Degrees per Minute"}
IJK_FOR_AXIS = {0: "I", 1: "J", 2: "K"}                                 # the centre-offset letters, X/Y/Z axes only
SUPPORTED = "G00, G01, G02, G03, G04, G1000, G1001, G1002, G1010, G1011, G1020, G1030, G1040"
HELP = ("The drive's G-code dialect (see the reference PDF), translated into this drive's own commands. Axis "
       "identifies a drive, but is NOT the same number as its CAN node ID - node ID = axis + 1, since node IDs are "
       "never 0 (X/Y/Z/A/B/C name axes 0-5, so node 1-6; U/V/W the same axes incrementally, [N] or P<n> name axis "
       "N, so node N+1) - a line naming an axis that is not the one you are connected to does not run, exactly as it "
       "would not affect a different drive on a real multi-axis system. Several G-codes may be concatenated on one "
       "physical line (the vendor's own multi-axis programs do this - see parse_line()), each addressing its own "
       "axis or Txx tool. Supported: " + SUPPORTED + " (G02/G03 as a radius R or a centre offset I/J/K, plane = "
       "whichever two of X/Y/Z or two axes the line gives a target for), and Txx (Inverse Kinematics, with "
       "G00/G01/G1020/G02/G03) once a .kinematics model is loaded (Item Configuration's Kinematics Model group). "
       "Everything else (G20/G21/G90/G91/G93/G94/G09/G61/G64) isn't tracked by this tool, and is noted instead of "
       "guessed at.")


class GcodeError(ValueError):
    pass


def strip_comment(line):
    """Comments in parentheses, like the reference's own dialect ('G1030 X150 (Axis 0, ESC 150 RPM)'), and a
    trailing ';' comment, like standard G-code."""
    return COMMENT_RE.sub(" ", line).strip()


def _axis_targets(tokens):
    """{axis_index: (value, relative)} for every axis-addressed value token in the line - X/Y/Z/A/B/C (absolute),
    U/V/W (relative), or the bracket form '[n]value' / '[n]' 'value' (absolute, axis n explicit). A line can name
    several axes (a real multi-axis move); the caller picks out its own once it knows which node it is."""
    targets = {}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        letter = t[:1]
        if letter in ABS_AXIS_LETTERS and len(t) > 1:
            try:
                targets[ABS_AXIS_LETTERS[letter]] = (float(t[1:]), False)
            except ValueError:
                pass
        elif letter in REL_AXIS_LETTERS and len(t) > 1:
            try:
                targets[REL_AXIS_LETTERS[letter]] = (float(t[1:]), True)
            except ValueError:
                pass
        else:
            m = BRACKET_TOKEN_RE.match(t)
            if m:
                axis, value = int(m.group(1)), m.group(2)
                if value is not None:
                    targets[axis] = (float(value), False)
                elif i + 1 < len(tokens):
                    try:
                        targets[axis] = (float(tokens[i + 1]), False)
                        i += 1                                          # consumed the next token as this one's value
                    except ValueError:
                        pass
        i += 1
    return targets


def _tool_number(tokens):
    """The tool number named by a bare 'Txx' token (a chain in a loaded kinematics model - see the module
    docstring). None if absent."""
    for t in tokens:
        m = TOOL_RE.match(t)
        if m:
            return int(m.group(1))
    return None


def _cartesian_target(tokens):
    """(x, y, z) mm for a Txx line - always absolute, always all three axes (0.0 for any not given), unlike the
    per-node axis letters: a tool's Cartesian target is a single point, not several independent node targets.
    None if none of X/Y/Z is present at all."""
    x, y, z = _param(tokens, "X"), _param(tokens, "Y"), _param(tokens, "Z")
    if x is None and y is None and z is None:
        return None
    return (x or 0.0, y or 0.0, z or 0.0)


def _cartesian_pair(tokens):
    """(("X"/"Y"/"Z", value), ("X"/"Y"/"Z", value)) for a Txx ARC's own end target - EXACTLY two of X/Y/Z, unlike a
    straight Txx move's target (always all three, an absent one defaulting to 0.0 - see _cartesian_target() above):
    an arc is inherently planar, so the omitted THIRD component means "wherever the tool's tip already is on that
    axis, held constant through the arc" - gcode_runner.py fills that in (it knows the chain's current position via
    forward kinematics; parse() itself does not). None if not exactly two of X/Y/Z are given."""
    given = [(letter, value) for letter in ("X", "Y", "Z") for value in [_param(tokens, letter)] if value is not None]
    if len(given) != 2:
        return None
    return _plane_order(given[0], given[1])


def _plane_order(first, second):
    """The two (name, value) pairs of an arc's plane in the order the arc's direction is judged in - X then Y for
    the XY plane and Y then Z for the YZ plane, as given, but Z THEN X for the XZ plane (G18's own ZX ordering):
    a right-handed pair, so that "clockwise" means what it does in every other G-code tool. Ordered X then Z it
    was mirrored - the vendor's own DOG.NC gait swing, `G03 T6 Z-400 X0 R500`, dipped the foot 200 into the
    ground instead of lifting it 200 (checked numerically against trajectory.arc_waypoints)."""
    names = (first[0], second[0])
    if names in (("X", "Z"), (0, 2)):
        return second, first
    return first, second


def _axis_param(tokens):
    """The axis named by a bare 'P<n>' token (Reset/Off/Stop/Capture/Release's own axis selector - not to be
    confused with G04's P, which is a duration, handled separately). None if absent, meaning "all axes"."""
    for t in tokens:
        if t[:1] == "P" and len(t) > 1:
            try:
                return int(float(t[1:]))
            except ValueError:
                pass
    return None


def _param(tokens, letter):
    """The value carried by a single-letter parameter token (e.g. 'F500' -> 500.0). None if absent."""
    for t in tokens:
        if t[:1] == letter and len(t) > 1:
            try:
                value = float(t[1:])
            except ValueError:
                continue
            if not math.isfinite(value):        # float() happily reads NAN/INF: 'RNAN' used to become a semicircle,
                raise GcodeError(f"'{t}' is not a finite number")     # 'G04 PINF' an OverflowError inside a timer
            return value
    return None


def _feed(tokens):
    """The F parameter, if given - a feed rate has to be a positive number: F0 (and F-5) used to be passed to the
    drive as they were, a "linear" move that never arrives."""
    feed = _param(tokens, "F")
    if feed is not None and feed <= 0:
        raise GcodeError(f"F{feed:g}: a feed rate must be positive")
    return feed


def parse(line):
    """-> {"action": ..., ...params} for the ONE G-code this line carries - see _parse_segment()'s own docstring for
    every action shape. Raises GcodeError if the line has anything other than EXACTLY one G-code - see parse_line()
    for a line that concatenates several (the vendor's own real multi-axis programs do - DOG.NC's own gait)."""
    line = strip_comment(line)
    if not line:
        raise GcodeError("empty command")
    tokens = line.upper().split()
    g_tokens = [t for t in tokens if re.fullmatch(r"G\d+", t)]
    if not g_tokens:
        raise GcodeError("no G-code found (expected a token starting with G, e.g. 'G1030')")
    if len(g_tokens) > 1:
        raise GcodeError(f"only one G-code per line here - found {len(g_tokens)} ({', '.join(g_tokens)}); use "
                         "parse_line() (the Terminal console and Motion Controller programs already do) to run "
                         "several concatenated G-codes as one step, or put each on its own line")
    return _parse_segment(tokens)


def parse_line(line):
    """-> [action, ...], one per G-code CONCATENATED on this one physical line - the vendor's own real multi-axis
    programs put several tab-separated commands on one line to move several axes/tools together in one synchronised
    step (DOG.NC's own gait: four G01/G03 commands, each its own Txx, on one line - one per leg). Each segment
    (from one G-code token up to, but not including, the next) is parsed independently by the exact same
    per-command logic parse() itself uses (_parse_segment) - concatenation only decides how a LINE is split into
    segments, never how any one segment's own tokens are read. A line with exactly one G-code (the ordinary case)
    returns a list of exactly one action - the same action parse() alone would have returned for it."""
    line = strip_comment(line)
    if not line:
        raise GcodeError("empty command")
    tokens = line.upper().split()
    g_indices = [i for i, t in enumerate(tokens) if re.fullmatch(r"G\d+", t)]
    if not g_indices:
        raise GcodeError("no G-code found (expected a token starting with G, e.g. 'G1030')")
    segments = [tokens[start:(g_indices[i + 1] if i + 1 < len(g_indices) else len(tokens))]
               for i, start in enumerate(g_indices)]
    return [_parse_segment(segment) for segment in segments]


def _parse_segment(tokens):
    """tokens: one G-code token followed by its own parameters - a whole line's worth, for parse()'s single-command
    case, or one already-split-out segment of a concatenated line, for parse_line(). -> {"action": ..., ...params},
    one of: reset / off / stop / capture / release (each with "axis": an int or None for "all axes") / move /
    linear_move / esc_rpm / torque (each with "axes": {axis_index: (value, relative)}) / tool_move (Txx: "tool":
    int, "target": (x, y, z) mm, "feed_rate" if G01) / arc / tool_arc (G02/G03 - see the module docstring; "arc"
    carries "plane": (axis_a, axis_b) and "end": (value_a, value_b) in that same axis order, "tool_arc" carries
    "tool": int and "plane"/"end" in X/Y/Z-letter terms instead - both carry "clockwise": bool, "radius" or "offset"
    (whichever the line gave, the other None), and "feed_rate" if given, optional here unlike G01) / dwell / noop.
    Raises GcodeError, with a reason meant to be shown to the user as-is, for anything this tool does not
    translate. Does NOT know which node is connected, or what kinematics model (if any) is loaded, or the drive's
    current position - gcode_runner.py matches "axis"/"axes" against the connected node(s), "tool" against the
    loaded model's own chains, and fills in an arc's own start point and any axis an arc leaves unmentioned."""
    g = int(tokens[0][1:])
    rest = tokens[1:]
    tool = _tool_number(rest)

    if g in (2, 3):
        return _parse_arc(g, rest, tool)

    if tool is not None:
        if g not in TOOL_MOVE_CODES:
            raise GcodeError(f"Txx only works with G00, G01 or G1020 (a move to a Cartesian target) or G02/G03 "
                             f"(an arc) - not G{g:02d}")
        target = _cartesian_target(rest)
        if target is None:
            raise GcodeError(f"T{tool} needs an X/Y/Z target, e.g. 'G00 T{tool} X100 Y0 Z50'")
        action = {"action": "tool_move", "tool": tool, "target": target}
        if g == 1:
            feed = _feed(rest)
            if feed is None:
                raise GcodeError("G01 needs a feed rate (F), even with Txx")
            action["feed_rate"] = feed
        return action

    if g in NOOP_CODES:
        return {"action": "noop", "text": f"G{g:02d} {NOOP_CODES[g]} - noted, but not tracked by this tool "
                                          "(targets are always the drive's own raw Work Zone Count)"}
    if g in (90, 91):
        return {"action": "noop", "text": f"G{g} is not tracked by this tool - use U instead of X on G00/G01/G1020 "
                                          "for a move relative to the current position instead"}
    if g == 1050:
        return {"action": "noop", "text": "G1050 (Disable Inverse Kinematics) - nothing to disable, this tool "
                                          "never enables it"}

    if g == 1000:
        return {"action": "reset", "axis": _axis_param(rest)}
    if g == 1001:
        return {"action": "off", "axis": _axis_param(rest)}
    if g == 1002:
        return {"action": "stop", "axis": _axis_param(rest)}
    if g == 1010:
        return {"action": "capture", "axis": _axis_param(rest)}
    if g == 1011:
        return {"action": "release", "axis": _axis_param(rest)}
    if g in (0, 1020):
        axes = _axis_targets(rest)
        if not axes:
            raise GcodeError(f"G{g:02d} needs a target, e.g. 'G{g:02d} X1000' or 'G{g:02d} [0]1000'")
        return {"action": "move", "axes": axes}
    if g == 1:
        axes = _axis_targets(rest)
        feed = _feed(rest)
        if not axes or feed is None:
            raise GcodeError("G01 needs a target and a feed rate, e.g. 'G01 X1000 F500' (F is counts/sec in this "
                             "tool, not mm/min or moves/min - G93/G94 are not tracked)")
        return {"action": "linear_move", "axes": axes, "feed_rate": feed}
    if g == 1030:
        axes = _axis_targets(rest)
        if not axes:
            raise GcodeError("G1030 needs a speed, e.g. 'G1030 X500' or 'G1030 [0]500'")
        return {"action": "esc_rpm", "axes": axes}
    if g == 1040:
        axes = _axis_targets(rest)
        if not axes:
            raise GcodeError("G1040 needs a torque, e.g. 'G1040 X0.5' or 'G1040 [0]0.5'")
        return {"action": "torque", "axes": axes}
    if g == 4:
        seconds = _param(rest, "P")
        if seconds is None:
            raise GcodeError("G04 needs a duration, e.g. 'G04 P1.5'")
        return {"action": "dwell", "seconds": seconds}
    raise GcodeError(f"G{g} is not one of the G-codes this tool translates ({SUPPORTED})")


def _parse_arc(g, rest, tool):
    """G02 (clockwise)/G03 (counterclockwise) - see the module docstring and _parse_segment()'s own docstring for
    the two action shapes this returns ("tool_arc" if `tool` is not None, "arc" otherwise) and trajectory.py for
    what "radius"/"offset" actually mean. `F` is optional, unlike G01 - see the module docstring."""
    clockwise = (g == 2)
    radius = _param(rest, "R")
    feed = _feed(rest)
    action = {"clockwise": clockwise, "radius": radius}
    if feed is not None:
        action["feed_rate"] = feed

    if tool is not None:
        pair = _cartesian_pair(rest)
        if pair is None:
            raise GcodeError(f"T{tool} G{g:02d} needs exactly two of X/Y/Z for its target (the arc's own plane) - "
                             f"the third stays wherever the tool's tip already is, e.g. 'G{g:02d} T{tool} X0 "
                             "Z-400 R500'")
        (letter_a, value_a), (letter_b, value_b) = pair
        action.update(action="tool_arc", tool=tool, plane=(letter_a, letter_b), end=(value_a, value_b))
        if radius is None:
            ijk_for_letter = {"X": "I", "Y": "J", "Z": "K"}
            offset = (_param(rest, ijk_for_letter[letter_a]), _param(rest, ijk_for_letter[letter_b]))
            if offset == (None, None):
                raise GcodeError(f"G{g:02d} needs a radius (R) or a centre offset (I/J/K)")
            action["offset"] = (offset[0] or 0.0, offset[1] or 0.0)
        else:
            action["offset"] = None
        return action

    axes = _axis_targets(rest)
    if any(relative for _value, relative in axes.values()):
        raise GcodeError(f"G{g:02d} does not support a relative (U/V/W) target - use X/Y/Z or [n] instead")
    if len(axes) != 2:
        raise GcodeError(f"G{g:02d} needs exactly two axes for its target (the arc's own plane), e.g. 'G{g:02d} "
                         f"X1000 Y2000 R500' - found {len(axes)}")
    pairs = _plane_order(*[(axis, axes[axis][0]) for axis in sorted(axes)])     # (Z, X) for the XZ plane - see it
    plane = [axis for axis, _value in pairs]
    action.update(action="arc", plane=tuple(plane), end=tuple(value for _axis, value in pairs))
    if radius is None:
        if any(axis not in IJK_FOR_AXIS for axis in plane):
            raise GcodeError(f"G{g:02d} needs a radius (R) - a centre offset (I/J/K) only pairs with axes 0/1/2 "
                             "(X/Y/Z) in this tool")
        offset = tuple(_param(rest, IJK_FOR_AXIS[axis]) for axis in plane)
        if offset == (None, None):
            raise GcodeError(f"G{g:02d} needs a radius (R) or a centre offset (I/J/K)")
        action["offset"] = (offset[0] or 0.0, offset[1] or 0.0)
    else:
        action["offset"] = None
    return action
