"""The Automatic Test engine (sdr_tuner/autotest.py) without a GUI: the step metrics, the chirp metrics, the checks and,
above all, Beta's policy - "as fast as the drive allows" - as a pure function of a report and the state it keeps."""
import math

import numpy as np
import pytest

from sdr_tuner import autotest as at, bode


def values(over=None):
    base = {at.FOC_KP: 0.3035, at.FOC_T: 0.001671, at.ESC_KP: 0.05, at.ESC_T: 0.05, at.SPEED_FILTER_T: 0.005, at.SPEED_FILTER_ON: 1,
            at.MOTOR_TYPE: 0, at.I_MAX: 1.9, at.POLES: 10, at.R_LL: 1.1412, at.L_LL: 0.001907, at.MOTOR_ENCODER: 0, at.SERVO_ENCODER: 0,
            at.HEARTBEAT_TIMEOUT: 3.5, at.TELEMETRY_FREQUENCY: 9.0, at.TIMER_ISR: 1000.0, at.CONTROL_FREQUENCY: 20850.0}
    for notch in at.NOTCHES:
        base[notch[0]], base[notch[1]], base[notch[2]] = 0, 300.0, 50.0
    base.update(over or {})
    return base


def measurement(loop="current", bandwidth=50.7, peak=0.1, overshoot=9.0, rise=8.0, coherent_to=220.0, sample_hz=1000.0, points=(),
                margins=None):
    return at.Measurement(loop, 0.5, sample_hz, rise, overshoot, bandwidth, peak, coherent_to, list(points),
                          margins if margins is not None else bode.Margins(93.0, 53.0, None, None))


def test_the_bench_drives_numbers_predict_its_measured_current_loop():
    """FOC Kp 0.3035 V/A over 0.954 mH per phase = 50.6 Hz; the sweep measured 50.7 (2026-09-25); T = L/R = 1.67 ms."""
    v = values()
    assert at.predicted_bandwidth_hz(v) == pytest.approx(50.6, abs=0.2)
    assert at.phase_l(v) / at.phase_r(v) == pytest.approx(0.001671, rel=0.01)
    assert at.kp_cap(v) == pytest.approx(2 * math.pi * (20850 / 20) * 0.0009535, rel=1e-3)
    assert not at.has_encoder(v) and at.has_encoder(values({at.MOTOR_ENCODER: 2}))


def test_step_metrics_read_the_rise_and_overshoot_of_a_square_wave_response():
    t = np.arange(0, 2.0, 0.001)
    ref = 0.5 * np.sign(np.sin(2 * math.pi * 5 * t))                                 # edges every 0.1 s
    y, res, tau = 0.0, [], 0.003                                                      # a 3 ms lag: 10-90 % in 2.2 tau = 6.6 ms
    for x in ref:
        y += (x - y) * (1 - math.exp(-0.001 / tau))
        res.append(y)
    rise, overshoot, edges = at.step_metrics(t, ref, np.array(res), 0.5)
    assert 5.5 < rise < 7.5 and abs(overshoot) < 2.0 and edges >= 8
    assert at.step_metrics(t[:20], ref[:20], np.array(res[:20]), 0.5) is None


def test_chirp_metrics_take_only_the_trustworthy_points():
    pts = [bode.BodePoint(f, g, -10.0, q) for f, g, q in ((10, 0.0, 1.0), (20, 0.1, 1.0), (40, -0.2, 0.95), (80, -3.5, 0.9),
                                                          (160, -8.0, 0.7), (320, 30.0, 0.1))]
    bw, peak, coherent_to = at.chirp_metrics(pts)
    assert 55 < bw < 85 and peak == pytest.approx(0.1 + 0.0333, abs=0.01) and coherent_to == 160   # the +30 dB junk point is ignored
    assert at.chirp_metrics(pts[:2]) == (None, None, None)


def test_beta_raises_kp_by_half_while_the_loop_is_clean_and_sets_t_to_l_over_r():
    state = {}
    report = at.Report([], values({at.FOC_T: 0.003}), measurement(), None, None)
    props = at.propose(report, state)
    assert [p.key for p in props] == [at.FOC_T, at.FOC_KP]
    assert props[0].new == pytest.approx(0.001671, rel=0.01) and props[1].new == pytest.approx(0.3035 * 1.5, rel=1e-3)
    assert state["last_good_kp"] == 0.3035 and "done" not in state


def test_beta_backs_off_to_the_last_clean_gain_when_peaking_or_overshoot_appears():
    state = {"last_good_kp": 1.0}
    report = at.Report([], values({at.FOC_KP: 1.5}), measurement(bandwidth=200.0, peak=2.4, overshoot=15.0), None, None)
    props = at.propose(report, state)
    assert len(props) == 1 and props[0].key == at.FOC_KP and props[0].new == 1.0 and "peaking +2.4 dB" in props[0].reason
    assert "backed off" in state["done"]
    state = {}                                                                          # no clean gain known yet: lower it
    props = at.propose(at.Report([], values({at.FOC_KP: 1.5}), measurement(overshoot=30.0), None, None), state)
    assert props[0].new == pytest.approx(1.0, rel=1e-3) and "done" not in state


def test_beta_stops_at_the_verifiable_bandwidth_and_at_the_gain_cap():
    state = {}
    props = at.propose(at.Report([], values({at.FOC_KP: 2.0}), measurement(bandwidth=350.0, sample_hz=1000.0), None, None), state)
    assert props == [] and "verify" in state["done"]
    state = {}
    v = values({at.FOC_KP: 6.0})                                                      # cap ~6.25 V/A: x1.5 would pass it
    props = at.propose(at.Report([], v, measurement(bandwidth=200.0), None, None), state)
    assert props == [] and "cap" in state["done"]
    state = {}
    props = at.propose(at.Report([], values(), measurement(bandwidth=None, coherent_to=220.0), None, None), state)
    assert props == [] and "flat to 220" in state["done"]


def test_beta_puts_a_notch_on_a_speed_loop_resonance_and_raises_esc_kp_when_clean():
    speed_pts = [bode.BodePoint(f, g, -20.0, 0.95) for f, g in ((2, 0.0), (4, 0.1), (8, -0.5), (16, -1.0), (33, 3.5), (40, -4.0))]
    state = {}
    v = values({at.MOTOR_ENCODER: 2})
    props = at.propose(at.Report([], v, measurement(), measurement("speed", bandwidth=20.0, peak=3.5, points=speed_pts), None), state)
    notch = [p for p in props if p.key in at.NOTCHES[0]]
    assert len(notch) == 3 and notch[0].new == 33.0 and notch[1].new == pytest.approx(8.25) and notch[2].new == 1
    clean = [bode.BodePoint(f, g, -20.0, 0.95) for f, g in ((2, 0.0), (4, 0.0), (8, -0.5), (16, -3.0), (33, -8.0))]
    state = {}
    props = at.propose(at.Report([], v, None, measurement("speed", bandwidth=12.0, peak=0.0, points=clean), None), state)
    assert [p.key for p in props] == [at.ESC_KP] and props[0].new == pytest.approx(0.075) and state["last_good_esc_kp"] == 0.05


def test_beta_backs_off_when_a_readable_phase_or_gain_margin_falls_under_its_floor():
    """The Nyquist rule (user: "add phase margin floor as beta stopping rule"): a margin only counts when its crossing is
    inside the measured range - an unreadable margin is not a failed one."""
    state = {"last_good_kp": 1.0}
    thin = bode.Margins(38.0, 300.0, None, None)                                        # 38 deg < the 45 deg floor
    props = at.propose(at.Report([], values({at.FOC_KP: 1.5}), measurement(bandwidth=250.0, margins=thin), None, None), state)
    assert len(props) == 1 and props[0].new == 1.0 and "phase margin 38° under the 45° floor" in props[0].reason and "backed off" in state["done"]
    state = {"last_good_kp": 1.0}
    weak = bode.Margins(70.0, 300.0, 4.0, 900.0)                                        # 4 dB < the 6 dB floor
    props = at.propose(at.Report([], values({at.FOC_KP: 1.5}), measurement(bandwidth=250.0, margins=weak), None, None), state)
    assert props[0].new == 1.0 and "gain margin 4.0 dB under the 6 dB floor" in props[0].reason
    state = {}
    unreadable = bode.Margins(None, None, None, None)                                   # crossover beyond the range: no verdict
    props = at.propose(at.Report([], values(), measurement(bandwidth=50.0, margins=unreadable), None, None), state)
    assert props and props[0].key == at.FOC_KP and props[0].new > 0.3035 and "done" not in state
    rows = {c.name: c for c in at.measurement_checks(measurement(margins=thin), values())}
    assert rows["Current loop margins"].status == "warn" and "below the floor" in rows["Current loop margins"].note
    rows = {c.name: c for c in at.measurement_checks(measurement(margins=unreadable), values())}
    assert rows["Current loop margins"].status == "skip"
    rows = {c.name: c for c in at.measurement_checks(measurement(), values())}
    assert rows["Current loop margins"].status == "ok" and "phase 93°" in rows["Current loop margins"].value


def test_nothing_to_change_is_reported_as_done():
    state = {}
    assert at.propose(at.Report([], values(), None, None, None), state) == [] and state["done"] == "nothing left to change"
