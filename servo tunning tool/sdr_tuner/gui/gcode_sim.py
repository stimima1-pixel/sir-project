"""Offline (no hardware, real OR simulated) preview of a G-code program's own motion, against a loaded kinematics
model only - the third mode scoped for the 3D Simulation page (see the README): LIVE mode (`sim3d.py`, already
built) needs a real connected axis broadcasting telemetry; this needs no connection at all, driving the SAME 3D
view instead from a purely virtual per-axis state this module owns (`session.virtual_axes`). Reuses `gcode.py`'s
own parser and `kinematics.py`'s own chains/solve completely unchanged - no new G-code dialect, no new IK math -
only the DISPATCH differs from `gcode_runner.GcodeRunner`'s own real-hardware dispatch (`client.set_speed_rpm()`
etc.): a `VirtualAxis` update instead of a CAN command, and no Feedback Settings check at all (there is no real
encoder to ask about offline). `MotionControllerPage` owns one `VirtualGcodeRunner` alongside its existing
`GcodeRunner`, and reuses its EXISTING RUN/PAUSE/RESUME/STOP/Loop state machine for both - `VirtualGcodeRunner`'s
own `run()`/`run_actions()`/`cancel()` are shaped to match `GcodeRunner`'s closely, on purpose, so
`MotionControllerPage._advance()` only needs a small branch (which runner), not a second state machine.

**Continuous vs timed vs instant motion - three different, deliberately distinct treatments**, chosen with the
user (2026-09-24 scoping, since a naive "snap to target" preview would show nothing at all for most of this
project's own bundled sample programs):
- **esc_rpm (G1030)**: the axis SPINS CONTINUOUSLY at the commanded rate (a plain rpm -> deg/sec conversion,
  360 * rpm / 60) for as long as that speed stays commanded - `speed_ramp.nc`/`esc_rpm_sweep.nc`, two of the four
  bundled samples, are pure speed ramps with no fixed position target at all, so this is what actually makes them
  visibly move rather than sit still. Only meaningful for a ROTARY axis - a LINEAR one has no defined "rpm"
  motion, so it simply holds (see `_spin`).
- **move / linear_move / tool_move (G00/G01/G1020/Txx)**: a FIXED target. G01/Txx-with-a-feed-rate ANIMATE to it
  over a feed-rate-derived duration (F is counts/sec, the same convention `gcode.py`'s own docstring already
  documents for real dispatch); G00/G1020/Txx-without-a-feed-rate JUMP to it instantly - there is no accel/decel
  model to animate with at all without a real drive's own motion-profile parameters to ask.
- **torque (G1040)**: no motion is modelled - an honest limitation, not a guess: torque has no defined
  relationship to motion without a real load/inertia this tool has no way to know offline. The axis simply holds
  wherever it already is.
- **reset / off / stop / capture / release**: freeze - stop any ongoing spin/animation and hold the CURRENT
  (interpolated) pose. Physically the closest real match (Off/Stop de-energise or halt the motor; Capture
  explicitly means "hold here"), and the only sensible thing to do for "all axes" (`axis is None`) offline, where
  there is no live connection list to check against - every axis this run has ever touched is "affected".

**arc / tool_arc (G02/G03) are refused, not (yet) half-implemented** - a clear "not yet supported" message rather
than silently mis-animating a curve; none of the bundled sample programs use one. The same waypoint-discretising
approach `GcodeRunner`'s own `_arc`/`_tool_arc` already use (`trajectory.py`) could extend this the same way, if
it turns out to matter.

**Every SIMULATE run starts from the model's own home pose** (`VirtualGcodeRunner.reset()` - every joint at 0) -
the user's own choice, for a clean, repeatable preview independent of whatever LIVE mode or an earlier run left
on screen; the pose reached is left visible afterwards (not cleared), so the result of a run stays inspectable.

**`_wait`/`cancel()` deliberately duplicate `GcodeRunner._wait`/`cancel()`'s own small (cancellable, single-shot
QTimer) pattern rather than extracting a shared helper** - a considered choice, not an oversight: the two are
otherwise so different (this class touches no client, no button, no Feedback Settings check at all) that
factoring out just this one piece would add an import/indirection for roughly fifteen simple lines, while
touching `GcodeRunner`'s own, already-hardware-tested dwell timing for a purely stylistic dedup was judged not
worth the risk."""
import math
import time
from collections import namedtuple

from PySide6.QtCore import QTimer

VirtualAxis = namedtuple("VirtualAxis", "value rate_per_sec since until")
# value: this axis's own joint value (degrees/mm, native kinematics unit) AT time `since`
# rate_per_sec: an ongoing rate of change (0.0 = holding still)
# since: time.monotonic() this entry took effect
# until: time.monotonic() this rate stops applying (None = forever - a continuous esc_rpm spin) - once `now` passes
#        it, the value is clamped at exactly what it reached at `until`, so a caller reading this AFTER a timed
#        move's own duration has elapsed sees the move's real target, not an overshoot

DEG_PER_SEC_PER_RPM = 360.0 / 60.0


def axis_value_now(state, now=None):
    """A VirtualAxis's own CURRENT value, projecting `rate_per_sec` forward from `since` - clamped at `until` if
    the rate is time-limited. `now`: time.monotonic(), defaults to right now - a caller may pass a fixed value to
    evaluate several axes at exactly the same instant."""
    if now is None:
        now = time.monotonic()
    reference = now if state.until is None else min(now, state.until)
    elapsed = max(0.0, reference - state.since)
    return state.value + state.rate_per_sec * elapsed


def _held(value, now):
    return VirtualAxis(value, 0.0, now, None)


def _spinning(value, rate_per_sec, now):
    return VirtualAxis(value, rate_per_sec, now, None)


def _ramping(value, rate_per_sec, now, duration):
    return VirtualAxis(value, rate_per_sec, now, now + duration)


class VirtualGcodeRunner:
    """Dispatches a parsed G-code action (`gcode.parse()`/`parse_line()`) into `session.virtual_axes` - see the
    module docstring for the per-action-kind treatment and why no client/button/Feedback Settings check is
    involved at all."""

    def __init__(self, session):
        self.s = session
        self._wait_timer = None

    def cancel(self):
        """STOP: cuts a dwell counting down short AND freezes every virtual axis where it is right now - the
        offline counterpart of the round STOP stopping every real motor. It used to cut only the dwell, so a
        G1030 axis in SIMULATE kept spinning in the 3D view forever after STOP."""
        if self._wait_timer is not None:
            self._wait_timer.stop()
            self._wait_timer = None
        self._freeze(None)

    def reset(self):
        """Every axis back to its home pose (0) - called once, at the start of a SIMULATE run."""
        self.s.virtual_axes = {}

    def run(self, action, on_done, on_progress=None):
        """Mirrors `GcodeRunner.run(action, button, on_done, on_progress)` minus `button` (nothing here ever needs
        a Feedback Settings re-entrancy key) - `on_done(text, state)` exactly once, `on_progress` zero or more
        times first for a non-final message (a dwell counting down)."""
        kind = action["action"]
        if kind == "noop":
            on_done(action["text"], "warn")
        elif kind == "dwell":
            self._dwell(action["seconds"], on_done, on_progress)
        elif kind == "tool_move":
            self._tool_move(action, on_done)
        elif kind in ("arc", "tool_arc"):
            on_done("Arcs (G02/G03) are not yet supported in Simulate mode.", "warn")
        elif kind in ("reset", "off", "stop", "capture", "release"):
            axis = action["axis"]
            self._freeze(axis)
            scope = "every axis" if axis is None else f"axis {axis}"
            on_done(f"{_SIMPLE_TEXT[kind]} {scope}.", "ok")
        elif kind == "esc_rpm":
            self._spin(action["axes"], on_done)
        elif kind == "torque":
            self._hold(action["axes"], on_done)
        elif kind in ("move", "linear_move"):
            self._move(action, on_done)

    def run_actions(self, actions, on_all_done, on_progress=None):
        """Mirrors `GcodeRunner.run_actions` exactly (several G-codes concatenated on one physical line, run in
        turn, combined into one `on_all_done([(action, text, state), ...])` call) minus `button`."""
        results = []

        def next_action(i):
            if i >= len(actions):
                on_all_done(results)
                return
            action = actions[i]

            def done(text, state):
                results.append((action, text, state))
                next_action(i + 1)

            self.run(action, done, on_progress=on_progress)

        next_action(0)

    # ---- reading the model -------------------------------------------------------------------
    def _axis_type(self, axis):
        model = self.s.kinematics_model
        if model is None:
            return None
        for body in model.bodies.values():
            if body.axis == axis:
                return body.axis_type or "rotary"
        return None

    def _current_native(self, axis, now):
        state = self.s.virtual_axes.get(axis)
        return 0.0 if state is None else axis_value_now(state, now)

    # ---- dispatch ------------------------------------------------------------------------------
    def _freeze(self, axis):
        """reset/off/stop/capture/release: hold wherever each affected axis currently, actually is (not its own
        pre-command value - an axis mid-spin or mid-move is frozen at its INTERPOLATED current position, exactly
        what stopping a real motor right now would do), ending any ongoing spin or timed move. `axis is None`
        means every axis this run has ever touched (gcode.py's own "no axis at all = all axes" convention -
        offline, that IS the full set, since there is no live connection list to fall back to)."""
        now = time.monotonic()
        targets = [axis] if axis is not None else list(self.s.virtual_axes)
        for a in targets:
            self.s.virtual_axes[a] = _held(self._current_native(a, now), now)

    def _spin(self, axes, on_done):
        now = time.monotonic()
        parts = []
        for axis, (rpm, _relative) in sorted(axes.items()):
            axis_type = self._axis_type(axis)
            current = self._current_native(axis, now)
            if axis_type == "linear":
                self.s.virtual_axes[axis] = _held(current, now)
                parts.append(f"axis {axis}: {rpm:.1f} rpm commanded - a linear axis has no rpm motion to preview")
            else:
                rate = rpm * DEG_PER_SEC_PER_RPM
                self.s.virtual_axes[axis] = _spinning(current, rate, now)
                parts.append(f"axis {axis} spinning at {rpm:.1f} rpm")
        on_done("Commanding " + "; ".join(parts) + ".", "ok")

    def _hold(self, axes, on_done):
        now = time.monotonic()
        for axis, (value, _relative) in sorted(axes.items()):
            self.s.virtual_axes[axis] = _held(self._current_native(axis, now), now)
        on_done("Commanding torque - no motion modelled, holding the current pose.", "ok")

    def _move(self, action, on_done):
        now = time.monotonic()
        feed_rate = action.get("feed_rate")
        parts = []
        for axis, (value_counts, relative) in sorted(action["axes"].items()):
            cpu = self.s.counts_per_unit.get(axis, 1.0) or 1.0
            current_native = self._current_native(axis, now)
            if relative:
                target_native = current_native + value_counts / cpu
            else:
                target_native = value_counts / cpu
            if feed_rate:
                rate_native = feed_rate / cpu
                distance = abs(target_native - current_native)
                duration = distance / rate_native if rate_native else 0.0
                if duration > 1e-6:
                    signed_rate = math.copysign(rate_native, target_native - current_native)
                    self.s.virtual_axes[axis] = _ramping(current_native, signed_rate, now, duration)
                else:
                    self.s.virtual_axes[axis] = _held(target_native, now)
            else:
                self.s.virtual_axes[axis] = _held(target_native, now)
            parts.append(f"axis {axis} -> {target_native:.2f}")
        on_done("Moving " + "; ".join(parts) + ".", "ok")

    def _tool_move(self, action, on_done):
        """Txx: solves Inverse Kinematics for the named tool's own chain, exactly like `GcodeRunner._tool_move` -
        then feeds the result through the SAME per-axis `_move` dispatch as a plain multi-axis move, mirroring how
        the real dispatch turns a solved Txx into a synthetic move/linear_move action rather than a separate
        code path."""
        tool = action["tool"]
        model = self.s.kinematics_model
        if model is None:
            on_done(f"T{tool}: no kinematics model loaded - see Item Configuration's Kinematics Model group.", "warn")
            return
        chain = model.chains().get(tool)
        if chain is None:
            on_done(f"T{tool}: the loaded kinematics model has no chain with that tool number.", "warn")
            return
        now = time.monotonic()
        current = [self._current_native(axis, now) for axis in chain.axes]
        result = chain.solve(action["target"], initial=current)
        if not result.converged:
            on_done(f"T{tool}: inverse kinematics did not converge (still {result.final_error:.1f} mm off "
                   "target) - not moved.", "error")
            return
        if result.out_of_limits:                     # same refusal as the real runner's - the limits are the model's
            axes = ", ".join(f"axis {chain.axes[i]} at {result.joint_values[i]:.1f}" for i in result.out_of_limits)
            on_done(f"T{tool}: the solution needs {axes}, outside that axis's own limits in the kinematics file - "
                    "not moved.", "error")
            return
        move_kind = "linear_move" if "feed_rate" in action else "move"
        synthetic = {"action": move_kind,
                    "axes": {axis: (value * self.s.counts_per_unit.get(axis, 1.0), False)
                             for axis, value in zip(chain.axes, result.joint_values)}}
        if move_kind == "linear_move":
            synthetic["feed_rate"] = action["feed_rate"]
        self._move(synthetic, on_done)

    def _dwell(self, seconds, on_done, on_progress):
        seconds = max(0.0, seconds)
        if on_progress:
            on_progress(f"Dwelling for {seconds:.1f} s…", "warn")
        self._wait(seconds, lambda: on_done(f"Dwelt {seconds:.1f} s.", "ok"))

    def _wait(self, seconds, on_fire):
        timer = QTimer()
        timer.setSingleShot(True)
        self._wait_timer = timer

        def fire():
            self._wait_timer = None
            on_fire()

        timer.timeout.connect(fire)
        timer.start(max(0, int(seconds * 1000)))


_SIMPLE_TEXT = {
    "reset": "Reset (simulated) - holding",
    "off": "Off (simulated) - holding",
    "stop": "Stop (simulated) - holding",
    "capture": "Capture (simulated) - holding",
    "release": "Released (simulated) - holding",
}
