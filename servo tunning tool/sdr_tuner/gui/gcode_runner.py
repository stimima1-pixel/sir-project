"""Dispatches ONE parsed G-code action (see gcode.parse()) to whichever connected axis/axes it names - shared by
the Terminal page's single-line console and the Motion Controller page's multi-line program editor, so both send
exactly what a Single Axis Motion page's own buttons would, through the exact same code (the Feedback Settings
safety check, the continuous-repeat mechanism, that page's own RUN/target bookkeeping) - never a separate, parallel
implementation.

This is also where a line's axis is matched against connected drives' own CAN node IDs. **Axis (0-based - what
gcode.py/kinematics.py speak) is NOT the same number as node ID (1-127 - what a real CAN network speaks)**: the
user's own correction, 2026-09-23, "node id 1 means axis 0, node is 2 means axis 1" - CAN node IDs are never 0, so
axis 0 (the first axis - G-code's own X, or a `.kinematics` chain's own first joint) is simply wired as the first
valid node, node 1; axis N is node N+1, uniformly. `axis_to_node()`/`node_to_axis()` are the ONE place this
translation happens - gcode.py and kinematics.py never see a real node ID at all, only axis indices; everywhere
else in THIS module, `clients` (keyed by node) and `client.node` are always real CAN node IDs. Real multi-axis: a
line naming SEVERAL connected axes (e.g. "G1030 [2]500 [5]-500", or a bare Reset/Off/Stop/Capture/Release with no
axis at all, meaning "every connected axis") dispatches to each of them in turn, combining their results into the
single `on_done` call the Terminal console and the Motion Controller both expect - one axis's own single-axis
message is passed through unchanged (byte-for-byte, for the single-connected-drive case this was originally built
for); more than one axis's results are joined, each labelled with its own node.

`Txx` (gcode.py's own "tool_move" action) solves Inverse Kinematics against a loaded `sdr_tuner.kinematics` model
(Item Configuration's Kinematics Model group) and dispatches the result through this SAME multi-axis mechanism -
see `_tool_move()`. `G02`/`G03` ("arc"/"tool_arc") discretise a circle (sdr_tuner/gui/trajectory.py) into waypoints
and dispatch each one, in turn, through this SAME mechanism too - see `_arc()`/`_tool_arc()`.

`move_to_pose()` (2026-09-24) is the ONE public entry point here with no `gcode.py` action of its own: it solves
FULL-POSE (position + orientation together) Inverse Kinematics for a named tool and dispatches through this same
mechanism, but is called directly by Item Configuration's own "Tool Pose" GUI control, never through `run()` - no
G-code command can express a target orientation at all (see kinematics.py's own module docstring for why), so
there is no line for `gcode.py` to parse one from.

**Several G-codes concatenated on one physical line** (gcode.parse_line() - the vendor's own real multi-axis
programs do this) are run through `run_actions()`, which calls `run()` once per action, in turn, and hands every
action's own result back to the caller - the Terminal console's own single-line combined message and Motion
Controller's own noop-vs-refusal halt decision each need a different thing from that list, so neither is decided
here (see `combine_action_results()` for the former)."""
import math

from PySide6.QtCore import QTimer

from ..client import DriveError
from . import feedback_gate, trajectory

MAX_DWELL_S = 24 * 3600.0       # a G04 longer than a day is a typo, not a wait - and beyond INT32 ms QTimer raised


class GcodeRunner:
    def __init__(self, session, drive_tab_for):
        """`drive_tab_for(node_id) -> DriveTab or None`: the ONE DriveTab bound to that axis's own connection (the
        primary drive's own page, or an additional axis's own page - see main_window.py's `_drive_tab_for`) - used
        for the Feedback-Settings-gated actions (Move/Linear Move/Capture), which reuse that page's own
        `_go_to_position`/`go_to_position_linear` exactly as the motion page's own buttons do. Looked up FRESH each
        time a line runs, never captured once, since axes connect and disconnect at runtime."""
        self.s = session
        self.drive_tab_for = drive_tab_for
        self._wait_timer = None
        self._generation = 0        # bumped by cancel(): every async continuation captured the value it started
                                     # under and does nothing once it no longer matches - see _alive()

    def cancel(self):
        """Ends everything this runner has in flight: a wait counting down (a G04 dwell, or an arc's own pause
        between waypoints - `_run_arc_waypoints` uses the same timer) is cut short, and NO continuation of anything
        started before this call runs afterwards - a Feedback Settings check that finishes later sends nothing,
        no next axis/waypoint/action of a chain is dispatched, and no `on_done` is called for any of it (a caller
        running a whole PROGRAM has already finished itself when it pressed STOP). It used to cut only the WAIT
        short: a check still in flight at STOP - almost always one, on an arc or a gait line - then sent its
        hold_position AFTER the emergency stop, the remaining axes, waypoints and actions all still ran, and a
        RUN pressed meanwhile got a second, interleaved program once the old line's own callback finally fired."""
        self._generation += 1
        if self._wait_timer is not None:
            self._wait_timer.stop()
            self._wait_timer = None

    def _alive(self, generation):
        return generation == self._generation

    def run(self, action, button, on_done, on_progress=None):
        """Runs a parsed action (`sdr_tuner.gui.gcode.parse()`'s result). `button` is used only as the (non-visual)
        Feedback Settings check's own re-entrancy key, when the action needs one - pass a QPushButton not currently
        in use for another check (typically the caller's own RUN button). `on_done(text, state)` - `text` is meant
        to be shown to the user as-is, `state` is "ok" / "warn" / "error" - is called exactly once, when the action
        has fully finished on every axis it named, including any async check some actions need; `on_progress(text,
        state)`, if given, may be called zero or more times first, for a message that is not the final result yet
        (the Feedback Settings check itself running, or a dwell counting down)."""
        clients = self.s.all_clients()
        if not clients:
            on_done("Connect to a drive first.", "warn")
            return
        kind = action["action"]
        if kind == "noop":
            on_done(action["text"], "warn")
            return
        if kind == "dwell":
            self._dwell(action["seconds"], on_done, on_progress)
            return
        if kind == "tool_move":
            self._tool_move(action, clients, button, on_done, on_progress)
            return
        if kind == "arc":
            self._arc(action, clients, button, on_done, on_progress)
            return
        if kind == "tool_arc":
            self._tool_arc(action, clients, button, on_done, on_progress)
            return
        if kind in ("reset", "off", "stop", "capture", "release"):
            axis = action["axis"]
            node = axis_to_node(axis) if axis is not None else None
            targets = sorted(clients) if node is None else ([node] if node in clients else [])
            if not targets:
                on_done(f"This line addresses axis {axis} - no connected axis is node {node} - skipped.", "warn")
                return
            self._run_chain(targets, button, on_progress, on_done,
                            lambda node, client, drive, done_one: self._dispatch_simple(kind, client, drive, button,
                                                                                        done_one, on_progress))
            return
        if kind in ("esc_rpm", "torque", "move", "linear_move"):
            targets = [n for n in sorted(axis_to_node(a) for a in action["axes"]) if n in clients]
            if not targets:
                on_done(f"This line addresses axis {sorted(action['axes'])} - no connected axis matches - skipped.",
                       "warn")
                return
            self._run_chain(targets, button, on_progress, on_done,
                            lambda node, client, drive, done_one: self._dispatch_value(kind, action, client, drive,
                                                                                       button, done_one, on_progress))
            return

    def run_actions(self, actions, button, on_all_done, on_progress=None):
        """Runs several parsed actions - see gcode.parse_line(), one physical line concatenating several G-codes
        (the vendor's own real multi-axis programs do this, e.g. DOG.NC's own gait) - IN TURN (same "never
        concurrently" reasoning as `_run_chain`, one level up: one `button` reused as every action's own Feedback
        Settings guard, no two actions racing to update the same status line). `on_all_done([(action, text,
        state), ...])` hands back EVERY action's own result, unlike `run()`'s own `on_done` - a caller that only
        wants one combined message for the whole line (the Terminal's single-line console) passes the list
        straight to `combine_action_results()`; a caller that cares WHICH action caused a problem (Motion
        Controller's own noop-vs-refusal halt decision) looks at each one itself."""
        results = []
        generation = self._generation

        def next_action(i):
            if not self._alive(generation):     # cancelled meanwhile: the program has finished itself already
                return
            if i >= len(actions):
                on_all_done(results)
                return
            action = actions[i]

            def done(text, state):
                results.append((action, text, state))
                next_action(i + 1)

            self.run(action, button, done, on_progress=on_progress)

        next_action(0)

    # ---- multi-axis sequencing --------------------------------------------------------------
    def _run_chain(self, targets, button, on_progress, on_done, dispatch_one):
        """Runs `dispatch_one(node, client, drive, done_one)` for each of `targets` IN TURN (never concurrently -
        so a single `button` can safely be reused as every axis's own Feedback Settings guard, and so two axes
        never race to update the same status line), then combines their results into the single `on_done` call
        this whole module promises. Exactly one target (the common, single-drive case this was originally built
        for) passes its own message straight through, unchanged - see `_combine`."""
        results = []
        generation = self._generation

        def next_target(i):
            if not self._alive(generation):     # cancelled meanwhile
                return
            if i >= len(targets):
                text, state = _combine(targets, results)
                on_done(text, state)
                return
            node = targets[i]
            client = self.s.all_clients().get(node)     # looked up NOW, not once at the start of the chain: an axis
                                                          # that disconnected during an earlier target's own turn is
                                                          # skipped below instead of being sent its dead client's command
            if client is None:      # disconnected between this line starting and its own turn - skip, keep going
                results.append((node, "disconnected while this line was running - skipped.", "warn"))
                next_target(i + 1)
                return
            drive = self.drive_tab_for(node)

            def done_one(text, state):
                results.append((node, text, state))
                next_target(i + 1)

            dispatch_one(node, client, drive, done_one)

        next_target(0)

    def _dispatch_simple(self, kind, client, drive, button, on_done, on_progress):
        if kind == "reset":
            self._simple(drive, client.reset, "Reset sent.", on_done, kind)
        elif kind == "off":
            self._simple(drive, client.off, "Off sent.", on_done, kind)
        elif kind == "stop":
            self._simple(drive, client.stop, "Stop sent.", on_done, kind)
        elif kind == "release":
            self._simple(drive, client.end_repeat, "Released: no longer repeating a command - the drive is left to "
                                                    "time out on its own heartbeat.", on_done, kind)
        elif kind == "capture":
            self._capture(client, drive, button, on_done, on_progress)

    def _dispatch_value(self, kind, action, client, drive, button, on_done, on_progress):
        value, relative = action["axes"][node_to_axis(client.node)]
        if kind == "esc_rpm":
            self._command(drive, lambda: client.set_speed_rpm(value), f"Commanding {value:.1f} rpm.", on_done, kind, value)
        elif kind == "torque":
            self._command(drive, lambda: client.set_torque(value), f"Commanding {value:.3f} N·m.", on_done, kind, value)
        elif kind == "move":
            self._move(client, drive, value, relative, button, on_done, on_progress)
        elif kind == "linear_move":
            self._linear_move(client, drive, value, relative, action["feed_rate"], button, on_done, on_progress)

    def _tool_move(self, action, clients, button, on_done, on_progress):
        """Txx: solves Inverse Kinematics for the named tool's own chain (a loaded kinematics model - Item
        Configuration's Kinematics Model group, sdr_tuner/kinematics.py) into each of the chain's own joints, then
        dispatches the result through the SAME multi-axis mechanism as a plain multi-axis move (`_run_chain`/
        `_dispatch_value`) - Txx does not duplicate that dispatch, it only feeds it a target it computed instead
        of one gcode.py already gave it directly."""
        tool = action["tool"]
        model = self.s.kinematics_model
        if model is None:
            on_done(f"T{tool}: no kinematics model loaded - see Item Configuration's Kinematics Model group.", "warn")
            return
        chain = model.chains().get(tool)
        if chain is None:
            on_done(f"T{tool}: the loaded kinematics model has no chain with that tool number.", "warn")
            return
        missing = [axis for axis in chain.axes if axis_to_node(axis) not in clients]
        if missing:
            on_done(f"T{tool} needs axis(es) {missing}, not connected - skipped.", "warn")
            return
        current = [self._current_joint_value(chain, axis, clients[axis_to_node(axis)]) for axis in chain.axes]
        if any(v is None for v in current):
            on_done(f"T{tool} needs the Work Zone Count of every one of its axes in telemetry - not all have "
                   "arrived yet.", "warn")
            return
        result = chain.solve(action["target"], initial=current)
        if not result.converged:
            on_done(f"T{tool}: inverse kinematics did not converge (still {result.final_error:.1f} mm off "
                   "target) - not moved.", "error")
            return
        if result.out_of_limits:
            on_done(_limits_text(tool, chain, result), "error")
            return
        move_kind = "linear_move" if "feed_rate" in action else "move"
        synthetic = {"action": move_kind,
                    "axes": {axis: (_to_count(value * self.s.counts_per_unit.get(axis, 1.0)), False)
                             for axis, value in zip(chain.axes, result.joint_values)}}
        if move_kind == "linear_move":
            synthetic["feed_rate"] = action["feed_rate"]
        self._run_chain([axis_to_node(axis) for axis in chain.axes], button, on_progress, on_done,
                        lambda node, client, drive, done_one: self._dispatch_value(
                            move_kind, synthetic, client, drive, button, done_one, on_progress))

    def move_to_pose(self, tool, target_position, target_rotation, button, on_done, on_progress=None):
        """Solves FULL-POSE (position + orientation together) Inverse Kinematics for the named tool's own chain and
        dispatches the result through the SAME multi-axis mechanism `_tool_move` already uses (`_run_chain`/
        `_dispatch_value`) - mirrors `_tool_move` exactly, just for a pose instead of a position. Called directly
        by Item Configuration's own "Tool Pose" GUI control (`config_page.py`), not from a G-code line: there is no
        G-code command that can express a target orientation at all (see kinematics.py's own module docstring), so
        this has no `gcode.py` action and is never reached through `run()`."""
        clients = self.s.all_clients()
        model = self.s.kinematics_model
        if model is None:
            on_done(f"T{tool}: no kinematics model loaded - see Item Configuration's Kinematics Model group.", "warn")
            return
        chain = model.chains().get(tool)
        if chain is None:
            on_done(f"T{tool}: the loaded kinematics model has no chain with that tool number.", "warn")
            return
        missing = [axis for axis in chain.axes if axis_to_node(axis) not in clients]
        if missing:
            on_done(f"T{tool} needs axis(es) {missing}, not connected - skipped.", "warn")
            return
        current = [self._current_joint_value(chain, axis, clients[axis_to_node(axis)]) for axis in chain.axes]
        if any(v is None for v in current):
            on_done(f"T{tool} needs the Work Zone Count of every one of its axes in telemetry - not all have "
                   "arrived yet.", "warn")
            return
        # tolerance=0.05mm, not solve_pose_ik's own default (1e-3mm = 1 micron): the GUI's own Target Position
        # fields only offer 2 decimal places (0.01mm), so a target typed there is already rounded to a coarser
        # granularity than that default tolerance can even represent - found 2026-09-24 verifying this live
        # against a real drive: a target computed from the chain's own forward_pose (so genuinely, exactly
        # reachable) still "did not converge" once rounded to 2 decimals the way the GUI would show/accept it, at
        # a residual of ~0.002mm - correct, unreachable-looking behaviour for a target that is really perfectly
        # fine to any precision a person could type or a drive could act on. 0.05mm keeps real headroom above
        # typed-value rounding while staying far tighter than any real servo's own positioning resolution.
        result = chain.solve_pose(target_position, target_rotation, initial=current, tolerance=0.05)
        if not result.converged:
            on_done(f"T{tool}: inverse kinematics did not converge (still {result.position_error:.1f} mm / "
                   f"{result.orientation_error_deg:.1f} deg off target) - not moved.", "error")
            return
        if result.out_of_limits:
            on_done(_limits_text(tool, chain, result), "error")
            return
        synthetic = {"action": "move",
                    "axes": {axis: (_to_count(value * self.s.counts_per_unit.get(axis, 1.0)), False)
                             for axis, value in zip(chain.axes, result.joint_values)}}
        self._run_chain([axis_to_node(axis) for axis in chain.axes], button, on_progress, on_done,
                        lambda node, client, drive, done_one: self._dispatch_value(
                            "move", synthetic, client, drive, button, done_one, on_progress))

    def _current_joint_value(self, chain, axis, client):
        """That axis's own current joint value, in the SAME native unit (degrees/mm) `Chain.solve()` expects to
        seed its search with - converted back from the drive's raw Work Zone Count via `counts_per_unit` (the
        inverse of what dispatching an IK result already does going the other way). None if no Work Zone Count
        has arrived yet for this axis."""
        snap = client.telemetry_snapshot()
        if "wz_count" not in snap:
            return None
        cpu = self.s.counts_per_unit.get(axis, 1.0)
        return snap["wz_count"] / cpu if cpu else 0.0

    # ---- arcs (G02/G03) -----------------------------------------------------------------------
    def _arc(self, action, clients, button, on_done, on_progress):
        """A plain (non-Txx) arc: its own plane is two of the drive's own raw axes - whichever the line named (see
        gcode.py's own docstring) - and its START point is simply wherever those two axes already are, from their
        own telemetry (genuinely current, unlike a straight move's own target, which parse() already knows
        outright). Each waypoint dispatches as an ordinary two-axis move, through the SAME multi-axis mechanism a
        real coordinated G01 already uses (`_run_chain`/`_dispatch_value`) - an arc does not duplicate that
        dispatch, it only feeds it a sequence of targets instead of the one gcode.py already gave it directly."""
        axis_a, axis_b = action["plane"]
        missing = [axis for axis in (axis_a, axis_b) if axis_to_node(axis) not in clients]
        if missing:
            on_done(f"This arc needs axis(es) {missing}, not connected - skipped.", "warn")
            return
        start = []
        for axis in (axis_a, axis_b):
            snap = clients[axis_to_node(axis)].telemetry_snapshot()
            if "wz_count" not in snap:
                on_done(f"Axis {axis} needs its Work Zone Count in telemetry - none has arrived yet.", "warn")
                return
            start.append(snap["wz_count"])
        try:
            waypoints = trajectory.arc_waypoints(tuple(start), action["end"], action["clockwise"],
                                                 radius=action["radius"], offset=action["offset"])
        except trajectory.TrajectoryError as exc:
            on_done(f"Arc: {exc}", "error")
            return
        feed_rate = action.get("feed_rate")
        move_kind = "linear_move" if feed_rate is not None else "move"
        points = [tuple(start)] + waypoints

        def dispatch_waypoint(i, done_one):
            a, b = _to_count(points[i][0]), _to_count(points[i][1])
            prev_a, prev_b = _to_count(points[i - 1][0]), _to_count(points[i - 1][1])
            delta = max(abs(a - prev_a), abs(b - prev_b))
            synthetic = {"action": move_kind, "axes": {axis_a: (a, False), axis_b: (b, False)}}
            if feed_rate is not None:
                synthetic["feed_rate"] = feed_rate
            self._run_chain([axis_to_node(axis_a), axis_to_node(axis_b)], button, on_progress,
                            lambda text, state: done_one(text, state, delta),
                            lambda node, client, drive, done: self._dispatch_value(
                                move_kind, synthetic, client, drive, button, done, on_progress))

        self._run_arc_waypoints(len(waypoints), dispatch_waypoint, feed_rate, on_done, on_progress)

    def _tool_arc(self, action, clients, button, on_done, on_progress):
        """Txx G02/G03: the same idea as `_arc`, but the plane is two Cartesian components of the chain's own tool
        tip - forward kinematics (`kinematics.Chain.forward`) gives its CURRENT ones, from the same per-axis joint
        values `_tool_move` already reads - and each waypoint is solved into the chain's own joints via Inverse
        Kinematics (`kinematics.Chain.solve`), exactly like a straight Txx move, just once per waypoint instead of
        once for the whole line - each one seeded from the PREVIOUS waypoint's own solution (never re-read from
        telemetry - the drive has not necessarily arrived there yet), so every search after the first is a small,
        fast correction instead of a fresh global one."""
        tool = action["tool"]
        model = self.s.kinematics_model
        if model is None:
            on_done(f"T{tool}: no kinematics model loaded - see Item Configuration's Kinematics Model group.", "warn")
            return
        chain = model.chains().get(tool)
        if chain is None:
            on_done(f"T{tool}: the loaded kinematics model has no chain with that tool number.", "warn")
            return
        missing = [axis for axis in chain.axes if axis_to_node(axis) not in clients]
        if missing:
            on_done(f"T{tool} needs axis(es) {missing}, not connected - skipped.", "warn")
            return
        current = [self._current_joint_value(chain, axis, clients[axis_to_node(axis)]) for axis in chain.axes]
        if any(v is None for v in current):
            on_done(f"T{tool} needs the Work Zone Count of every one of its axes in telemetry - not all have "
                   "arrived yet.", "warn")
            return

        letter_index = {"X": 0, "Y": 1, "Z": 2}
        letter_a, letter_b = action["plane"]
        third_letter = next(letter for letter in ("X", "Y", "Z") if letter not in (letter_a, letter_b))
        tip = chain.forward(current)
        start_2d = (tip[letter_index[letter_a]], tip[letter_index[letter_b]])
        third_value = tip[letter_index[third_letter]]
        try:
            waypoints_2d = trajectory.arc_waypoints(start_2d, action["end"], action["clockwise"],
                                                    radius=action["radius"], offset=action["offset"])
        except trajectory.TrajectoryError as exc:
            on_done(f"T{tool}: {exc}", "error")
            return

        def to_3d(point_2d):
            by_letter = {letter_a: point_2d[0], letter_b: point_2d[1], third_letter: third_value}
            return (by_letter["X"], by_letter["Y"], by_letter["Z"])

        feed_rate = action.get("feed_rate")
        move_kind = "linear_move" if feed_rate is not None else "move"
        # Every waypoint is solved up front, BEFORE any is sent: one that turns out unreachable (or only reachable
        # outside a joint's limits) refuses the whole arc with nothing moved. Solving them one at a time as they
        # were dispatched used to send waypoints 1-2 and then abort on 3 - the leg left part-way along an arc.
        cpu = [self.s.counts_per_unit.get(axis, 1.0) for axis in chain.axes]
        solutions, seed = [], list(current)
        for n, point_2d in enumerate(waypoints_2d, 1):
            result = chain.solve(to_3d(point_2d), initial=seed)
            if not result.converged:
                on_done(f"T{tool}: waypoint {n}/{len(waypoints_2d)}: inverse kinematics did not converge (still "
                        f"{result.final_error:.1f} mm off target) - the arc was not started.", "error")
                return
            if result.out_of_limits:
                on_done(f"waypoint {n}/{len(waypoints_2d)}: {_limits_text(tool, chain, result)}", "error")
                return
            delta = max(abs((a - b) * c) for a, b, c in zip(result.joint_values, seed, cpu))
            solutions.append((result.joint_values, delta))
            seed = result.joint_values

        def dispatch_waypoint(i, done_one):
            joint_values, delta = solutions[i - 1]
            synthetic = {"action": move_kind,
                        "axes": {axis: (_to_count(value * c), False)
                                 for axis, value, c in zip(chain.axes, joint_values, cpu)}}
            if feed_rate is not None:
                synthetic["feed_rate"] = feed_rate
            self._run_chain([axis_to_node(axis) for axis in chain.axes], button, on_progress,
                            lambda text, state: done_one(text, state, delta),
                            lambda node, client, drive, done: self._dispatch_value(
                                move_kind, synthetic, client, drive, button, done, on_progress))

        self._run_arc_waypoints(len(waypoints_2d), dispatch_waypoint, feed_rate, on_done, on_progress)

    def _run_arc_waypoints(self, count, dispatch_waypoint, feed_rate, on_done, on_progress):
        """Dispatches `dispatch_waypoint(i, done_one)` for i = 1..count IN TURN (never concurrently, same reasoning
        as `_run_chain`/`run_actions` - one at a time, one level up: waypoints along an arc rather than axes for
        one move, or actions on one line). `done_one(text, state, delta=0.0)` reports that waypoint's own result
        AND (only meaningful when `feed_rate` is given) the size of the move it just sent, in whatever unit
        `feed_rate` itself is in - raw counts for a plain arc, or a chain's own largest single-joint raw-count
        delta for a Txx arc (see `_arc`/`_tool_arc`) - used to pace the NEXT waypoint by delta/feed_rate seconds,
        the same "slowest one sets the pace" idea a synchronised multi-axis move already needs. This is a REAL
        wait, not an arrival signal (this tool has none to wait on) - the same honest stand-in a G04 dwell already
        is. No `feed_rate`: a "rapid" arc (matching G00) - waypoints are sent back-to-back, no artificial pacing at
        all. Stops early, without dispatching the rest, the moment one waypoint's own result is an outright error -
        more waypoints will not fix an already-bad configuration. The final `on_done` carries the WORST state any
        waypoint reported (error already stops early above; a "warn" - e.g. an axis disconnecting partway through -
        still lets the rest run, same as a plain multi-axis line already does, but must not be silently reported as
        "ok" once it happened)."""
        order = {"error": 0, "warn": 1, "ok": 2}
        worst = ["ok"]
        generation = self._generation

        def next_waypoint(i):
            if not self._alive(generation):     # cancelled meanwhile
                return
            if i > count:
                on_done(f"Arc: {count} waypoint(s) sent.", worst[0])
                return

            def done_one(text, state, delta=0.0):
                if state == "error":
                    on_done(f"Arc: waypoint {i}/{count} failed: {text}", "error")
                    return
                if order[state] < order[worst[0]]:
                    worst[0] = state
                if on_progress:
                    on_progress(f"Arc: waypoint {i}/{count}: {text}", state)
                if feed_rate:
                    self._wait(delta / feed_rate, lambda: next_waypoint(i + 1))
                else:
                    next_waypoint(i + 1)

            dispatch_waypoint(i, done_one)

        next_waypoint(1)

    # ---- one axis's own action --------------------------------------------------------------
    def _simple(self, drive, fn, ok_text, on_done, kind=None):
        """Reset / Off / Stop / Release: end whatever was running, and that axis's own motion page follows
        (`note_gcode(False, ...)`: its RUN/target bookkeeping, its target field back to 0, the same "... sent."
        note under Speed Control its own STOP/OFF/RESET show), so it never claims a stale command is still active."""
        try:
            fn()
        except DriveError as exc:
            on_done(f"Failed: {exc}", "error")
            return
        if drive is not None:
            drive.note_gcode(False, kind=kind, text=ok_text)
        on_done(ok_text, "ok")

    def _command(self, drive, fn, ok_text, on_done, kind=None, value=None):
        """A continuous speed/torque command: sent, then MIRRORED on that axis's own motion page (`note_gcode`) -
        target field, card status, the "sent every 100 ms" note - exactly as if its own RUN had been pressed
        (2026-09-25, user: running a program under Motion left Servo Drive > Motion - Single Axis unchanged)."""
        try:
            fn()
        except (DriveError, ValueError) as exc:
            on_done(f"Command failed: {exc}", "error")
            return
        if drive is not None:
            drive.note_gcode(True, kind=kind, value=value, text=ok_text)
        on_done(ok_text, "ok")

    def _relative_target(self, client, value, relative, on_done):
        """-> (target, ok). For a relative (U/V/W) target: that axis's current Work Zone Count from the broadcast
        (never requested) plus the given delta; ok=False (on_done already called with the reason) if none has
        arrived yet."""
        if not relative:
            return value, True
        snap = client.telemetry_snapshot()
        if "wz_count" not in snap:
            on_done("Needs the drive's Work Zone Count in its telemetry - none has arrived yet.", "warn")
            return None, False
        return snap["wz_count"] + value, True

    def _capture(self, client, drive, button, on_done, on_progress):
        """G1010: energize and hold at the CURRENT position - the same action and the same Feedback Settings check
        as that axis's own motion page ENABLE, reused directly from there."""
        snap = client.telemetry_snapshot()
        if "wz_count" not in snap:
            on_done("Needs the drive's Work Zone Count in its telemetry - none has arrived yet.", "warn")
            return
        target = snap["wz_count"]
        self._go(drive, button, lambda label: drive._go_to_position(target, "Holding position", label), on_done, on_progress)

    def _move(self, client, drive, value, relative, button, on_done, on_progress):
        """G00 / G1020: move to (and hold) a Work Zone Count - the same action and the same Feedback Settings check
        as that axis's own motion page MOVE TO / MOVE BY, reused directly from there."""
        target, ok = self._relative_target(client, value, relative, on_done)
        if not ok:
            return
        self._go(drive, button, lambda label: drive._go_to_position(target, "Moving to", label), on_done, on_progress)

    def _linear_move(self, client, drive, value, relative, feed_rate, button, on_done, on_progress):
        """G01: move to a Work Zone Count at a feed rate (counts/sec in this tool - G93/G94 are not tracked)."""
        target, ok = self._relative_target(client, value, relative, on_done)
        if not ok:
            return
        self._go(drive, button, lambda label: drive.go_to_position_linear(target, feed_rate, label), on_done, on_progress)

    def _go(self, drive, button, action_fn, on_done, on_progress):
        """Runs `action_fn(label)` behind the Feedback Settings check (`feedback_gate.check` - the same one a
        motion page's own Enable/Move To/By use via `DriveTab._with_feedback_check`) - `action_fn` is one of that
        axis's own motion page methods, which normally write into one of its QLabel status lines; `label` here is
        a plain callback instead (`DriveTab._status` accepts either). The check's own progress/refusal go straight
        to `on_progress`/`on_done`; the action's own eventual result (once the check passes) becomes the single
        `on_done` call."""
        if drive is None:
            on_done("This axis has no motion page to move through yet.", "error")
            return
        generation = self._generation
        feedback_gate.check(
            drive.s.client, button,
            on_progress=lambda text, state: on_progress(text, state) if on_progress else None,
            on_ok=lambda: action_fn(lambda text, state: on_done(text, state)) if self._alive(generation) else None,
            # ^ the check passed - but if STOP came while it ran, send nothing (this used to move the motor AFTER it)
            on_warn=lambda text: on_done(text, "warn"),
            on_failed=lambda exc: on_done(f"Could not check Feedback Settings: {exc}", "error"),
        )

    def _dwell(self, seconds, on_done, on_progress):
        """No CAN traffic: the reference says the axes just keep moving during a dwell - this is a program-level
        pause between G-code lines, which only the Motion Controller's multi-line runner can meaningfully use (the
        Terminal's single-line console has no next line to pause before). Not per-axis: it is a pause in the
        PROGRAM, before every axis's next line, not any one drive's own action."""
        if not math.isfinite(seconds) or seconds > MAX_DWELL_S:
            on_done(f"G04: a dwell of {seconds} s is not a wait this tool will start (up to {MAX_DWELL_S:g} s).",
                    "error")                     # `G04 PINF` / `G04 P3000000` used to raise inside _wait instead,
            return                               # leaving the program "running" until STOP
        seconds = max(0.0, seconds)
        if on_progress:
            on_progress(f"Dwelling for {seconds:.1f} s…", "warn")
        self._wait(seconds, lambda: on_done(f"Dwelt {seconds:.1f} s.", "ok"))

    def _wait(self, seconds, on_fire):
        """A real (cancellable) countdown - QTimer, not QTimer.singleShot, so a caller's STOP (`cancel()`) can cut
        it short instead of waiting out the full delay. Shared by a G04 dwell and an arc's own pause between
        waypoints (`_run_arc_waypoints`) - the same nature either way: a real wait, not an arrival signal (this
        tool has none to wait on)."""
        timer = QTimer()
        timer.setSingleShot(True)
        self._wait_timer = timer

        def fire():
            self._wait_timer = None
            on_fire()

        timer.timeout.connect(fire)
        timer.start(max(0, min(int(seconds * 1000), 2**31 - 1)))     # QTimer takes an INT32 of ms - beyond it, it raised


def _limits_text(tool, chain, result):
    """The refusal for an IK solution that converged only by putting a joint outside its own Axis Limits (see
    kinematics._out_of_limits) - the runner used to dispatch it regardless; the limits were never consulted."""
    axes = ", ".join(f"axis {chain.axes[i]} at {result.joint_values[i]:.1f}" for i in result.out_of_limits)
    return f"T{tool}: the solution needs {axes}, outside that axis's own limits in the kinematics file - not moved."


def axis_to_node(axis):
    """A G-code axis (0-based - X/Y/Z/A/B/C name axes 0-5, [N]/P<n> name axis N, a .kinematics file's own Axis
    column is the SAME 0-based numbering, per its own module docstring) -> the real CAN node ID that actually
    drives it. The user's own correction, 2026-09-23: "node id 1 means axis 0, node is 2 means axis 1" - node IDs
    are never 0 (1..127 only), so axis 0 - simply the first axis - is wired as the first valid node; axis N is
    always node N+1. See this module's own docstring for where this translation does (and does not) apply."""
    return axis + 1


def node_to_axis(node):
    """The inverse of `axis_to_node` - a connected client's own real CAN node ID -> the axis index gcode.py's own
    parsed action (or a .kinematics chain's own `axes`) actually names it by."""
    return node - 1


def _to_count(value):
    """A Work Zone Count is the drive's own raw INT32 - the protocol layer refuses anything else ('X is not a
    whole number'). Every OTHER G-code target is typed directly by the user as a plain integer already (a real
    line's own convention, documented in gcode.py) and needs no rounding; this is only for a value this tool
    computed itself from continuous math a user never typed digit-for-digit - a solved Inverse Kinematics joint
    value converted through `counts_per_unit` (`_tool_move`/`_tool_arc`), or a point along an arc's own circle
    (`_arc`) - always some float, essentially never already a whole number."""
    return round(value)


def _combine(targets, results):
    """-> (text, state) for one or more axes' own (node, text, state) results. Exactly one target (the single-
    connected-drive case this was originally built for) passes its own message and state straight through,
    unchanged - so every existing single-axis message stays byte-for-byte what it always was. More than one axis's
    results are joined, each labelled with its own node, and the combined state is the worst of them (error over
    warn over ok) - a whole line is not "ok" if even one of the axes it named failed."""
    if len(results) == 1:
        return results[0][1], results[0][2]
    order = {"error": 0, "warn": 1, "ok": 2}
    state = min((r[2] for r in results), key=lambda s: order.get(s, 1))
    text = "; ".join(f"Axis {node}: {text}" for node, text, _state in results)
    return text, state


def combine_action_results(results):
    """-> (text, state) for one physical line's worth of parsed actions (see GcodeRunner.run_actions - each result
    is (action, text, state)) - the same "worst wins, one passes straight through unchanged" rule `_combine`
    already uses one level down (per AXIS, for one action); this is the same idea one level up (per ACTION, for one
    concatenated line). Exactly one action (the ordinary one-command-per-line case) passes its own message through
    unchanged, byte for byte; more than one (a real concatenated line) are joined, each labelled with its own
    G-code's own short description (`_describe_action`)."""
    if len(results) == 1:
        return results[0][1], results[0][2]
    order = {"error": 0, "warn": 1, "ok": 2}
    state = min((r[2] for r in results), key=lambda s: order.get(s, 1))
    text = "; ".join(f"{_describe_action(action)}: {text}" for action, text, _state in results)
    return text, state


def _describe_action(action):
    """A short label for one action, for a concatenated line's own combined message (combine_action_results) -
    never shown for the ordinary one-command-per-line case, where nothing needs distinguishing."""
    kind = action["action"]
    if kind in ("tool_move", "tool_arc"):
        return f"T{action['tool']}"
    if kind in ("move", "linear_move", "esc_rpm", "torque"):
        return f"Axis {', '.join(str(a) for a in sorted(action['axes']))}"
    if kind == "arc":
        return f"Axis {', '.join(str(a) for a in action['plane'])}"
    if kind in ("reset", "off", "stop", "capture", "release"):
        axis = action["axis"]
        return f"{kind.capitalize()} axis {axis}" if axis is not None else kind.capitalize()
    return kind.capitalize()
