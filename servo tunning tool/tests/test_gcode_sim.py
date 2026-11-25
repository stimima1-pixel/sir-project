"""sdr_tuner.gui.gcode_sim - the offline "Simulate" mode's own dispatch (VirtualGcodeRunner), no Qt event loop
needed for any of this except the dwell's own QTimer (QT_QPA_PLATFORM=offscreen, same as every other GUI test
here). A tiny FakeSession stands in for the real Session - VirtualGcodeRunner only ever reads
`.virtual_axes`/`.counts_per_unit`/`.kinematics_model` from it, same shape as the real one."""
import time
from pathlib import Path

import pytest

from sdr_tuner import kinematics as k
from sdr_tuner.gui import gcode
from sdr_tuner.gui import gcode_sim as gs

SAMPLES = Path(__file__).resolve().parent.parent / "sdr_tuner" / "data" / "kinematics_samples"


class FakeSession:
    def __init__(self, model=None, counts_per_unit=None):
        self.virtual_axes = {}
        self.counts_per_unit = counts_per_unit or {}
        self.kinematics_model = model


def collect(runner, line):
    """Runs one line's worth of actions (parse_line - handles a concatenated line too) and returns [(text, state)]
    in order, synchronously - true for every action kind here except dwell, which schedules a real QTimer; callers
    that dwell use qtbot.waitUntil instead (see the dwell-specific tests)."""
    results = []
    for action in gcode.parse_line(line):
        runner.run(action, lambda text, state: results.append((text, state)))
    return results


# ---- axis_value_now ------------------------------------------------------------------------------
def test_a_held_axis_never_changes_value():
    state = gs.VirtualAxis(value=42.0, rate_per_sec=0.0, since=time.monotonic(), until=None)
    assert gs.axis_value_now(state, now=state.since + 100) == 42.0


def test_a_spinning_axis_advances_linearly_with_time():
    state = gs.VirtualAxis(value=0.0, rate_per_sec=10.0, since=100.0, until=None)
    assert gs.axis_value_now(state, now=105.0) == pytest.approx(50.0)


def test_a_ramping_axis_clamps_at_its_own_until():
    state = gs.VirtualAxis(value=0.0, rate_per_sec=10.0, since=100.0, until=105.0)
    assert gs.axis_value_now(state, now=102.5) == pytest.approx(25.0)
    assert gs.axis_value_now(state, now=200.0) == pytest.approx(50.0)      # clamped, not overshooting


# ---- esc_rpm: continuous spin ---------------------------------------------------------------------
def test_esc_rpm_on_a_rotary_axis_spins_continuously():
    s = FakeSession()
    runner = gs.VirtualGcodeRunner(s)
    results = collect(runner, "G1030 X60")            # 60 rpm = 360 deg/s
    assert results[0][1] == "ok"
    state = s.virtual_axes[0]
    assert state.rate_per_sec == pytest.approx(360.0)
    assert state.until is None                        # spins forever, until something else commands axis 0


def test_esc_rpm_on_a_linear_axis_has_no_rpm_motion_to_preview():
    text = ("#;Name;Parent;Axis;Axis Type;Axis Direction X;Y;Z;Axis Origin Point(mm) X;Y;Z;"
           "Axis Limits(mm or deg) Min;Max;Axis Tolerance(mm or deg);"
           "Tool Direction X;Y;Z;Tool Tip(mm) X;Y;Z;Body Center of Mass(mm) X;Y;Z;Mass(kg);IMU\n"
           "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;0\n"
           "1;rail;0;0;linear;1;0;0;0;0;0;;;;1;0;0;100;0;0;;;;0.1;0\n")
    model = k.parse_kinematics_text(text)
    s = FakeSession(model)
    runner = gs.VirtualGcodeRunner(s)
    text_result, state_result = collect(runner, "G1030 X60")[0]
    assert "no rpm motion" in text_result
    assert s.virtual_axes[0].rate_per_sec == 0.0


def test_esc_rpm_spin_actually_accumulates_degrees_over_real_time():
    s = FakeSession()
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G1030 X60")
    before = gs.axis_value_now(s.virtual_axes[0])
    time.sleep(0.2)
    after = gs.axis_value_now(s.virtual_axes[0])
    assert after > before + 60.0                     # 60 rpm = 360 deg/s; 0.2s should add ~72 deg, well over 60


def test_a_later_command_on_the_same_axis_replaces_the_spin():
    s = FakeSession(counts_per_unit={0: 1.0})
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G1030 X60")
    assert s.virtual_axes[0].rate_per_sec != 0.0
    collect(runner, "G00 X10")
    assert s.virtual_axes[0].rate_per_sec == 0.0      # the move replaced the spin, not layered on top of it


# ---- move / linear_move: instant jump vs timed ramp -----------------------------------------------
def test_g00_jumps_instantly_no_ramp():
    s = FakeSession(counts_per_unit={0: 10.0})        # 10 counts per degree
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G00 X100")                       # 100 counts -> 10 degrees
    state = s.virtual_axes[0]
    assert state.rate_per_sec == 0.0
    assert state.value == pytest.approx(10.0)


def test_g01_ramps_over_a_feed_rate_derived_duration():
    s = FakeSession(counts_per_unit={0: 10.0})
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G00 X100")                       # start at 10 deg
    collect(runner, "G01 X200 F10")                   # to 20 deg, F=10 counts/s -> 1 deg/s -> 10 s
    state = s.virtual_axes[0]
    assert state.rate_per_sec == pytest.approx(1.0)
    assert state.until == pytest.approx(state.since + 10.0)
    assert gs.axis_value_now(state, now=state.since + 5.0) == pytest.approx(15.0)
    assert gs.axis_value_now(state, now=state.since + 100.0) == pytest.approx(20.0)   # clamped at target


def test_relative_move_adds_to_the_axis_own_current_value():
    s = FakeSession(counts_per_unit={0: 1.0})
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G00 X100")
    collect(runner, "G1020 U-40")                     # relative: 100 - 40 = 60
    assert s.virtual_axes[0].value == pytest.approx(60.0)


def test_a_move_with_no_counts_per_unit_set_defaults_to_1_to_1():
    s = FakeSession()                                 # counts_per_unit deliberately empty
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G00 X250")
    assert s.virtual_axes[0].value == pytest.approx(250.0)


# ---- torque: no motion modelled --------------------------------------------------------------------
def test_torque_holds_the_current_pose_with_a_clear_note():
    s = FakeSession(counts_per_unit={0: 1.0})
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G00 X30")
    text, state = collect(runner, "G1040 X0.5")[0]
    assert "no motion modelled" in text
    assert s.virtual_axes[0].value == pytest.approx(30.0)
    assert s.virtual_axes[0].rate_per_sec == 0.0


# ---- reset/off/stop/capture/release: freeze --------------------------------------------------------
def test_stop_freezes_a_spinning_axis_at_its_current_interpolated_value():
    s = FakeSession()
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G1030 X60")
    time.sleep(0.1)
    mid_flight = gs.axis_value_now(s.virtual_axes[0])
    collect(runner, "G1002")                          # Stop, no axis given = every axis
    frozen = s.virtual_axes[0]
    assert frozen.rate_per_sec == 0.0
    assert frozen.value == pytest.approx(mid_flight, abs=0.5)


def test_stop_with_an_explicit_axis_only_freezes_that_one():
    s = FakeSession(counts_per_unit={0: 1.0, 1: 1.0})
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G1030 X60")
    collect(runner, "G1030 [1]90")
    collect(runner, "G1002 P0")                        # Stop, axis 0 only (P<n>, not [n] - see gcode.py's _axis_param)
    assert s.virtual_axes[0].rate_per_sec == 0.0
    assert s.virtual_axes[1].rate_per_sec != 0.0       # axis 1 keeps spinning


def test_reset_with_no_axes_ever_touched_does_not_crash():
    s = FakeSession()
    runner = gs.VirtualGcodeRunner(s)
    text, state = collect(runner, "G1000")[0]
    assert state == "ok"


# ---- Txx (Inverse Kinematics), reusing the real solver -----------------------------------------------
def test_tool_move_solves_ik_and_updates_every_joint_in_the_chain():
    model = k.parse_kinematics_file(SAMPLES / "radar.obj.kinematics")
    s = FakeSession(model, counts_per_unit={0: 1.0, 1: 1.0, 2: 1.0})
    runner = gs.VirtualGcodeRunner(s)
    chain = model.chains()[3]
    home = chain.home_tip
    text, state = collect(runner, f"G00 T3 X{home[0]:.3f} Y{home[1]:.3f} Z{home[2]:.3f}")[0]
    assert state == "ok"
    assert set(s.virtual_axes) == {0, 1, 2}
    for axis_state in s.virtual_axes.values():
        assert axis_state.value == pytest.approx(0.0, abs=1e-2)   # home pose is joints=0


def test_tool_move_with_no_model_loaded_refuses_clearly():
    s = FakeSession()
    runner = gs.VirtualGcodeRunner(s)
    text, state = collect(runner, "G00 T3 X0 Y0 Z0")[0]
    assert state == "warn"
    assert "no kinematics model" in text


def test_tool_move_reports_when_ik_does_not_converge():
    model = k.parse_kinematics_file(SAMPLES / "radar.obj.kinematics")
    s = FakeSession(model, counts_per_unit={0: 1.0, 1: 1.0, 2: 1.0})
    runner = gs.VirtualGcodeRunner(s)
    text, state = collect(runner, "G00 T3 X99999 Y99999 Z99999")[0]   # far outside the chain's own reach
    assert state == "error"
    assert "did not converge" in text


# ---- arcs: refused, not half-implemented --------------------------------------------------------------
def test_a_plain_arc_is_refused_clearly():
    s = FakeSession()
    runner = gs.VirtualGcodeRunner(s)
    text, state = collect(runner, "G02 X100 Y100 R50")[0]
    assert state == "warn"
    assert "not yet supported" in text


def test_a_tool_arc_is_also_refused():
    model = k.parse_kinematics_file(SAMPLES / "radar.obj.kinematics")
    s = FakeSession(model)
    runner = gs.VirtualGcodeRunner(s)
    text, state = collect(runner, "G02 T3 X100 Y100 R50")[0]
    assert state == "warn"
    assert "not yet supported" in text


# ---- reset() and the "always start from home" rule ------------------------------------------------------
def test_reset_clears_every_tracked_axis_back_to_home():
    s = FakeSession(counts_per_unit={0: 1.0})
    runner = gs.VirtualGcodeRunner(s)
    collect(runner, "G00 X500")
    assert s.virtual_axes
    runner.reset()
    assert s.virtual_axes == {}
    assert gs.axis_value_now(gs.VirtualAxis(0.0, 0.0, time.monotonic(), None)) == 0.0   # a missing axis reads as 0


# ---- noop / run_actions (concatenated lines) --------------------------------------------------------------
def test_a_noop_gcode_does_not_touch_any_axis():
    s = FakeSession()
    runner = gs.VirtualGcodeRunner(s)
    text, state = collect(runner, "G90")[0]
    assert state == "warn"
    assert s.virtual_axes == {}


def test_run_actions_runs_a_concatenated_line_in_order_and_combines_results():
    s = FakeSession(counts_per_unit={0: 1.0, 1: 1.0})
    runner = gs.VirtualGcodeRunner(s)
    actions = gcode.parse_line("G00 X10\tG00 [1]20")
    assert len(actions) == 2
    all_results = []
    runner.run_actions(actions, lambda results: all_results.extend(results))
    assert len(all_results) == 2
    assert s.virtual_axes[0].value == pytest.approx(10.0)
    assert s.virtual_axes[1].value == pytest.approx(20.0)


# ---- dwell: a real, cancellable wait (needs the Qt event loop - qtbot) -----------------------------------
def test_dwell_actually_waits_before_calling_on_done(qtbot):
    s = FakeSession()
    runner = gs.VirtualGcodeRunner(s)
    action = gcode.parse("G04 P0.05")
    done = []
    runner.run(action, lambda text, state: done.append((text, state)))
    assert done == []                                  # not yet - the wait has not fired
    qtbot.waitUntil(lambda: len(done) == 1, timeout=1000)
    assert done[0][1] == "ok"


def test_cancel_cuts_a_dwell_short(qtbot):
    s = FakeSession()
    runner = gs.VirtualGcodeRunner(s)
    action = gcode.parse("G04 P10")
    done = []
    runner.run(action, lambda text, state: done.append((text, state)))
    runner.cancel()
    qtbot.wait(200)
    assert done == []                                  # on_done never fires for a cancelled wait
