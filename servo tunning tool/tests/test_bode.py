"""sdr_tuner/bode.py - the frequency-response arithmetic, against synthetic signals with known answers."""
import cmath
import math

import numpy as np
import pytest

from sdr_tuner import bode


def lag_response(t, f, amplitude, tau, noise=0.0, seed=0):
    """A first-order lag's reference and response at `f` Hz: |H| = 1/sqrt(1+(wt)^2), phase = -atan(wt)."""
    w = 2 * math.pi * f
    ref = amplitude * np.sin(w * t)
    h = 1 / (1 + 1j * w * tau)
    rng = np.random.default_rng(seed)
    res = amplitude * abs(h) * np.sin(w * t + np.angle(h)) + rng.normal(0, noise, len(t))
    return ref, res, 20 * math.log10(abs(h)), math.degrees(np.angle(h))


def test_frequencies_are_log_spaced_snapped_to_the_fixed_point_period_and_bounded():
    f = bode.frequencies(0.5, 50, 12)
    assert len(f) == 12 and f[0] == pytest.approx(0.5) and f[-1] == pytest.approx(51.2)     # 256 / 5: the nearest 1/256 s period
    ratios = [b / a for a, b in zip(f, f[1:])]
    assert max(ratios) / min(ratios) < 1.2                                 # log spacing survives the (coarse, at the top) snapping
    assert bode.snap_frequency(37.3) == pytest.approx(256 / 7)              # 1/37.3 s = 6.86/256 -> raw 7
    assert bode.snap_frequency(4.0) == 4.0                                  # 0.25 s = raw 64, exact
    assert bode.snap_frequency(256.0) == 256.0 and bode.snap_frequency(1000.0) == 256.0   # raw 1 is the shortest period
    for bad in ((0.01, 50, 12), (50, 0.5, 12), (0.5, 500, 12), (0.5, 50, 1), (0.5, 50, 100)):
        with pytest.raises(ValueError):
            bode.frequencies(*bad)
    assert len(bode.frequencies(1.0, 1.001, 5)) < 5                        # duplicates after snapping are dropped


def test_fit_sine_recovers_amplitude_phase_and_a_quality_that_drops_with_noise():
    t = np.arange(0, 2.0, 0.005)
    x = 3.0 * np.cos(2 * math.pi * 2.5 * t + 0.7) + 1.0                    # an offset must not matter
    a, phi, q = bode.fit_sine(t, x, 2.5)
    assert a == pytest.approx(3.0, rel=1e-6) and phi == pytest.approx(0.7, abs=1e-6) and q == pytest.approx(1.0)
    noisy = x + np.random.default_rng(1).normal(0, 3.0, len(t))
    a2, _phi2, q2 = bode.fit_sine(t, noisy, 2.5)
    assert abs(a2 - 3.0) < 0.5 and q2 < 0.7
    _a3, _p3, q3 = bode.fit_sine(t, x, 4.0)                                 # the wrong frequency explains little
    assert q3 < 0.3
    with pytest.raises(ValueError):
        bode.fit_sine(t[:4], x[:4], 2.5)


def test_response_point_matches_a_first_order_lag_to_a_fraction_of_a_db_and_a_degree():
    t = np.arange(0, 3.0, 0.005)
    for f in (0.5, 1.0, 4.0, 20.0):
        ref, res, gain, phase = lag_response(t, f, 10.0, 0.15, noise=0.05)
        p = bode.response_point(t, ref, res, f)
        assert p.frequency == f and abs(p.gain_db - gain) < 0.2 and abs(p.phase_deg - phase) < 1.0 and p.quality > 0.95


def test_the_real_frequency_is_found_when_the_drive_drifts_off_the_asked_one():
    """Measured on the bench drive (2026-09-25): asked for 128 Hz, the drive's sine ran 128.00 Hz half a second in,
    127.2 Hz after 10 s and 132.4 Hz after 40 s - a clean sine each time. Fitted at the ASKED frequency over 0.5 s
    the quality was 0.6 and 0.01; at the real one, 1.000. So a point is fitted at the frequency the reference shows."""
    t = np.arange(0, 0.5, 0.001)                                            # 1000 Hz, half a second - the page's own window
    for drift in (0.994, 1.0, 1.034):                                       # 127.2 / 128 / 132.4 Hz for 128 asked
        real = 128.0 * drift
        ref, res, gain, phase = lag_response(t, real, 0.5, 0.002, noise=0.01)
        f, q = bode.refine_frequency(t, ref, 128.0)
        assert abs(f - real) / real < 2e-4 and q > 0.99
        p = bode.response_point(t, ref, res, 128.0)                         # refined by default
        assert p.frequency == pytest.approx(real, rel=2e-4)
        assert abs(p.gain_db - gain) < 0.2 and abs(p.phase_deg - phase) < 1.5 and p.quality > 0.95
        unrefined = bode.response_point(t, ref, res, 128.0, refine=False)
        if drift != 1.0:
            assert unrefined.quality < 0.7                                  # what the sweep showed before this fix
    assert bode.refine_frequency(t, ref, 100.0)[0] <= 100.0 * (1 + bode.REFINE_SPAN)   # never outside the search span


def test_the_chirp_model_starts_at_one_over_period_and_repeats_every_ten_periods():
    """The drive's sweep as measured on the bench (2026-09-25): 1/Period at the start of a cycle, faster than
    exponential, a few hundred hertz at the very end, the cycle 10 x Period."""
    assert bode.chirp_frequency(0.0, 0.1) == pytest.approx(10.0)
    assert bode.chirp_frequency(0.0, 1.0) == pytest.approx(1.0)
    assert bode.chirp_frequency(10.0, 1.0) == pytest.approx(1.0)                       # the next cycle starts over
    f = [bode.chirp_frequency(tau, 1.0) for tau in (2.0, 5.0, 8.0, 9.0, 9.9)]
    assert f == sorted(f) and 10 < f[2] < 16 and 24 < f[3] < 32 and f[-1] > 50        # measured: 1.4 / 3.2 / 12.7 / 27.5 / ~80 Hz
    assert bode.chirp_frequency(9.999, 1.0) < 1.1 * bode.CHIRP_TOP_HZ


def test_a_welch_estimate_of_a_chirp_through_a_lag_matches_the_lag_where_the_coherence_is_good():
    """The Chirp method: the drive's sweep recorded, the response over the reference by Welch's method, band-averaged
    into points whose quality is the coherence. A synthetic sweep (the measured shape) through a first-order lag
    must give the lag's gain and phase to a fraction of a dB / a few degrees wherever the coherence is high."""
    fs, period, tau = 1000.0, 0.5, 0.003                                               # 5 s cycles from 2 Hz; a 53 Hz lag
    dt = 1.0 / fs
    n = int(2 * bode.CHIRP_CYCLE * period * fs)
    phase, ref, res, y = 0.0, [], [], 0.0
    rng = np.random.default_rng(3)
    for i in range(n):
        phase += 2 * math.pi * bode.chirp_frequency(i * dt, period) * dt
        x = 0.5 * math.sin(phase)
        y += (x - y) * (1.0 - math.exp(-dt / tau))
        ref.append(x)
        res.append(y + rng.normal(0, 0.01))
    f, h, coh = bode.welch_response(ref, res, fs)
    assert len(f) == len(h) == len(coh) and f[1] - f[0] == pytest.approx(fs / 2048)
    points = bode.chirp_response(ref, res, fs, 1.0 / period, 0.45 * fs)
    assert points and points[0].frequency > 1.0 / period and points[-1].frequency < 0.45 * fs
    assert [p.frequency for p in points] == sorted(p.frequency for p in points)
    good = [p for p in points if p.quality > 0.9 and p.frequency <= 150.0]
    assert len(good) >= 20 and max(p.frequency for p in good) > 100.0             # the short segments carry it well past 20 Hz
    a = 1.0 - math.exp(-dt / tau)                          # the synthetic lag is a filter stepped at fs: compare with ITS
    for p in good:                                         # exact response a / (1 - (1 - a) e^-jw dt), not the continuous one
        z = complex(math.cos(2 * math.pi * p.frequency * dt), -math.sin(2 * math.pi * p.frequency * dt))
        expected = a / (1.0 - (1.0 - a) * z)
        assert abs(p.gain_db - 20 * math.log10(abs(expected))) < (0.5 if p.frequency < 20 else 1.0), p   # short segments above 20 Hz
        assert abs(p.phase_deg - math.degrees(math.atan2(expected.imag, expected.real))) < 5.0, p     # a band of 2-3 bins
    with pytest.raises(ValueError):
        bode.welch_response(ref[:100], res[:100], fs)                                   # too short for two segments
    with pytest.raises(ValueError):
        bode.chirp_points(f, h, coh, 50.0, 10.0)


def test_only_the_segments_that_excite_a_bin_are_summed_so_the_responses_own_noise_does_not_drown_the_sweep():
    """The bench drive's Iq telemetry carries ~0.12 A rms of broadband noise. A chirp excites each frequency only while
    it passes it; summing every segment adds 40 s of that noise to 0.3 s of signal and the coherence above ~100 Hz
    collapsed (all-segment average) - with the selection the same recording matched the stepped sine to 260 Hz."""
    fs, period, tau = 1000.0, 0.1, 0.001                                               # 1 s cycles from 10 Hz, a 160 Hz lag
    dt = 1.0 / fs
    n = int(6 * bode.CHIRP_CYCLE * period * fs)
    rng = np.random.default_rng(5)
    phase, ref, res, y = 0.0, [], [], 0.0
    for i in range(n):
        phase += 2 * math.pi * bode.chirp_frequency(i * dt, period) * dt
        x = 0.5 * math.sin(phase)
        y += (x - y) * (1.0 - math.exp(-dt / tau))
        ref.append(x)
        res.append(y + rng.normal(0, 0.12))                                            # the measured noise level
    f_all, h_all, coh_all = bode.welch_response(ref, res, fs, bode.CHIRP_SHORT_S, excited=0.0)   # every segment
    f_sel, h_sel, coh_sel = bode.welch_response(ref, res, fs, bode.CHIRP_SHORT_S)                 # excited ones only
    band = (f_all >= 150.0) & (f_all <= 250.0)
    assert coh_sel[band].mean() > coh_all[band].mean() + 0.2 and coh_sel[band].mean() > 0.8
    a = 1.0 - math.exp(-dt / tau)
    points = bode.chirp_points(f_sel, h_sel, coh_sel, 150.0, 250.0)                  # the page's own band averages
    assert len(points) >= 6
    for p in points:
        z = complex(math.cos(2 * math.pi * p.frequency * dt), -math.sin(2 * math.pi * p.frequency * dt))
        expected = a / (1.0 - (1.0 - a) * z)
        assert abs(p.gain_db - 20 * math.log10(abs(expected))) < 2.5 and p.quality > 0.8, p      # 0.12 A rms noise on 0.5 A, 6 cycles


def test_phase_is_wrapped_to_plus_minus_180_degrees():
    t = np.arange(0, 2.0, 0.002)
    ref = np.sin(2 * math.pi * 5 * t)
    res = np.sin(2 * math.pi * 5 * t - math.radians(200))                  # a 200 degree lag reads as +160
    p = bode.response_point(t, ref, res, 5.0)
    assert p.phase_deg == pytest.approx(160.0, abs=0.5)


def test_align_keeps_only_the_samples_both_signals_have():
    t_ref, t_res = np.array([1.0, 2.0, 3.0, 4.0]), np.array([2.0, 3.0, 4.0, 5.0])
    t, ref, res = bode.align(t_ref, [10, 20, 30, 40], t_res, [2, 3, 4, 5])
    assert list(t) == [2.0, 3.0, 4.0] and list(ref) == [20, 30, 40] and list(res) == [2, 3, 4]


def test_bandwidth_phase_and_resonance_are_read_from_the_points():
    tau = 0.15
    pts = []
    for f in bode.frequencies(0.2, 20, 15):
        h = 1 / (1 + 1j * 2 * math.pi * f * tau)
        pts.append(bode.BodePoint(f, 20 * math.log10(abs(h)), math.degrees(np.angle(h)), 1.0))
    bw = bode.bandwidth(pts)
    assert bw == pytest.approx(1 / (2 * math.pi * tau), rel=0.03)          # 1.06 Hz, interpolated between points
    assert bode.phase_at(pts, bw) == pytest.approx(-45.0, abs=2.0)
    assert bode.phase_at(pts, 0.01) is None and bode.resonance(pts) is None
    assert bode.bandwidth(pts[:1]) is None
    flat = [bode.BodePoint(f, 0.0, 0.0, 1.0) for f in (1, 2, 4, 8)]
    assert bode.bandwidth(flat) is None                                     # never drops 3 dB
    peaky = [bode.BodePoint(1, 0.0, 0, 1), bode.BodePoint(2, 4.0, -30, 1), bode.BodePoint(4, -6.0, -120, 1)]
    assert bode.resonance(peaky).frequency == 2 and bode.bandwidth(peaky) == pytest.approx(2 * (4 / 2) ** (7 / 10))


def test_csv_rows_have_a_header_and_one_row_per_point_with_the_open_loop_alongside():
    rows = list(bode.csv_rows([bode.BodePoint(1.5, -2.5, -40.0, 0.99)]))
    assert rows[0] == ("frequency_hz", "gain_db", "phase_deg", "quality", "open_loop_db", "open_loop_phase_deg")
    assert rows[1][:4] == ("1.5", "-2.500", "-40.00", "0.990")
    t = 10 ** (-2.5 / 20) * cmath.exp(1j * math.radians(-40.0))
    l = t / (1 - t)
    assert float(rows[1][4]) == pytest.approx(20 * math.log10(abs(l)), abs=0.001) and float(rows[1][5]) == pytest.approx(math.degrees(cmath.phase(l)), abs=0.01)


def test_the_open_loop_of_a_first_order_lag_is_an_integrator_with_a_90_degree_phase_margin():
    """T = 1/(1 + jwt) with unity feedback is L = 1/(jwt): |L| = 1/(wt), phase -90 deg at every frequency, the gain
    crossover at 1/(2 pi t), a 90 deg phase margin and no phase crossover - which is what the bench drive's current
    loop measured as (2026-09-25: -82..-95 deg across 1-256 Hz, 93-94 deg of margin at both Kp values)."""
    tau = 0.003
    pts = [bode.BodePoint(f, -10 * math.log10(1 + (2 * math.pi * f * tau) ** 2), -math.degrees(math.atan(2 * math.pi * f * tau)), 1.0)
           for f in np.geomspace(1.0, 300.0, 25)]
    for f, l, q in bode.open_loop(pts):
        assert abs(l) == pytest.approx(1 / (2 * math.pi * f * tau), rel=1e-6) and math.degrees(cmath.phase(l)) == pytest.approx(-90.0, abs=1e-6)
    m = bode.margins(pts)
    assert m.phase_margin_deg == pytest.approx(90.0, abs=0.5) and m.gain_crossover_hz == pytest.approx(1 / (2 * math.pi * tau), rel=0.02)
    assert m.gain_margin_db is None and m.phase_crossover_hz is None
    assert bode.margins(pts[:1]) == bode.Margins(None, None, None, None)
    weak = [p._replace(quality=0.1) for p in pts]                                       # hollow points are left out
    assert bode.margins(weak) == bode.Margins(None, None, None, None)


def test_margins_read_a_gain_margin_where_the_open_loops_phase_crosses_minus_180():
    """A loop with two more lags: L = K / (s (1 + s t1)(1 + s t2)) has a phase crossover; the margins recovered from
    the CLOSED-loop points must match the ones computed from L directly."""
    k, t1, t2 = 800.0, 0.002, 0.0005
    def l_of(f):
        s = 2j * math.pi * f
        return k / (s * (1 + s * t1) * (1 + s * t2))
    pts = []
    for f in np.geomspace(2.0, 800.0, 60):
        t = l_of(f) / (1 + l_of(f))                                                     # what a sweep measures
        pts.append(bode.BodePoint(float(f), 20 * math.log10(abs(t)), math.degrees(cmath.phase(t)), 1.0))
    m = bode.margins(pts)
    # the truth, from L itself, on a fine grid
    fine = np.geomspace(2.0, 800.0, 20000)
    mags = np.array([abs(l_of(f)) for f in fine]); phs = np.degrees(np.unwrap([cmath.phase(l_of(f)) for f in fine]))
    i_c = int(np.argmax(mags < 1.0)); i_p = int(np.argmax(phs < -180.0))
    assert m.gain_crossover_hz == pytest.approx(fine[i_c], rel=0.03) and m.phase_margin_deg == pytest.approx(180 + phs[i_c], abs=2.0)
    assert m.phase_crossover_hz == pytest.approx(fine[i_p], rel=0.03) and m.gain_margin_db == pytest.approx(-20 * math.log10(mags[i_p]), abs=0.5)
    assert m.gain_margin_db > 0 and 0 < m.phase_margin_deg < 90
