"""Frequency response (Bode plot) analysis - pure functions, no Qt, like gcode.py and kinematics.py.

The drive has no frequency-response function of its own, but its four Testing commands (Device Reference pp. 89-91/95)
take Wave Form 2 = a SINE WAVE into the current, speed or servo loop at 1/Period Hz, and both the loop's reference
(Reference: Current / Speed / Work Zone Count) and its response are telemetry. So a Bode plot is a STEPPED SINE: run
the sine at one frequency, let the loop settle, record a few cycles of reference and response, fit a sinusoid at that
frequency to each (least squares - robust to a non-integer number of cycles and to the host-side timestamp jitter of
the USB link), and the ratio of the two amplitudes is the gain, the difference of the two phases the phase lag. Then
the next frequency. gui/bode_page.py drives the drive; this module only does the arithmetic."""
import cmath
import math
from collections import namedtuple

import numpy as np

from . import protocol as P

BodePoint = namedtuple("BodePoint", "frequency gain_db phase_deg quality")   # quality: 0..1, see fit_sine
Margins = namedtuple("Margins", "phase_margin_deg gain_crossover_hz gain_margin_db phase_crossover_hz")   # None where not in range
QUALITY_FLOOR = 0.6          # a point whose fit / coherence explains less than this is drawn hollow and kept out of the readings
MIN_FREQUENCY, MAX_FREQUENCY = 0.05, 256.0       # Hz - below: minutes per point; above: the Period's 1/256 s step (raw 1)
MIN_POINTS, MAX_POINTS = 2, 60                    # telemetry loop gives 5 samples per cycle at 200 Hz, the floor for a fit
MIN_SAMPLES = 8


def snap_frequency(frequency):
    """The frequency the drive will really run: its Period parameter is "FLOAT16" - on this drive a 16-bit fixed-point
    number in steps of 1/256 s (protocol.py) - so 1/f is rounded to what the wire can carry before the analysis assumes
    it. Coarse at the top: 256/1 = 256 Hz, 256/2 = 128, 256/5 = 51.2, 256/13 = 19.7 Hz ... (measured 2026-09-25: raw
    256 gave a 1.000 s sine, raw 64 a 0.25 s one)."""
    period = max(P.decode_value("FLOAT16", P.encode_value("FLOAT16", 1.0 / frequency)), 1.0 / P.FIXED_POINT_SCALE)
    return 1.0 / period                                              # never raw 0 (a period the drive cannot run)


def frequencies(start, stop, points):
    """`points` log-spaced test frequencies from `start` to `stop` Hz, each snapped to what the drive can run
    (snap_frequency), duplicates dropped. ValueError says what is wrong."""
    if not (MIN_FREQUENCY <= start < stop <= MAX_FREQUENCY):
        raise ValueError(f"the frequencies must satisfy {MIN_FREQUENCY:g} Hz <= start < stop <= {MAX_FREQUENCY:g} Hz")
    if not MIN_POINTS <= points <= MAX_POINTS:
        raise ValueError(f"points must be {MIN_POINTS} to {MAX_POINTS}")
    out = []
    for f in np.geomspace(start, stop, int(points)):
        snapped = snap_frequency(float(f))
        if not out or abs(snapped - out[-1]) > 1e-9 * snapped:
            out.append(snapped)
    return out


def fit_sine(t, x, frequency):
    """-> (amplitude, phase_rad, quality): the sinusoid at `frequency` that best fits x(t), by least squares on
    cos/sin plus a constant (so an offset, or a non-integer number of cycles, does not skew it). `quality` is the share of the signal's power the fit explains: 1.0 for a clean sine at that
    frequency, near 0 for noise or a different frequency. Phase is relative to t[0] - meaningless alone, meaningful as
    the DIFFERENCE between two signals fitted over the same t."""
    t = np.asarray(t, dtype=float)
    x = np.asarray(x, dtype=float)
    if len(t) < MIN_SAMPLES or len(t) != len(x):
        raise ValueError(f"at least {MIN_SAMPLES} samples are needed, with one value per time stamp")
    w = 2.0 * math.pi * frequency * (t - t[0])
    basis = np.column_stack([np.cos(w), np.sin(w), np.ones_like(w)])   # the offset is FITTED, not subtracted as the
    (a, b, offset), *_ = np.linalg.lstsq(basis, x, rcond=None)         # mean: over a non-integer number of cycles the
    x = x - offset                                                       # mean is biased and would skew the phase
    fitted = a * np.cos(w) + b * np.sin(w)
    power = float(np.dot(x, x))
    quality = float(np.dot(fitted, fitted) / power) if power > 0.0 else 0.0
    return math.hypot(a, b), math.atan2(-b, a), min(max(quality, 0.0), 1.0)   # a cos wt + b sin wt = A cos(wt + phi)


REFINE_SPAN = 0.06          # the drive's sine drifts: +-6 % around the asked frequency is searched for the real one


def refine_frequency(t, x, nominal, span=REFINE_SPAN):
    """-> (frequency, quality): the frequency within +-span of `nominal` at which a sine fits x(t) best. The drive's
    own sine generator does not hold the asked frequency exactly - measured on the bench drive (2026-09-25): asked
    for 128 Hz it ran 128.00 Hz half a second in, 127.2 Hz after 10 s and 132.4 Hz after 40 s, a clean sine each
    time (quality 1.000 at the real frequency, 0.6 and 0.01 at the asked one) - so a point is fitted at the
    frequency the reference really shows, not at the one that was asked for. Coarse scan, then a parabola through
    the best three points, then a fine scan around it."""
    def q(f):
        return fit_sine(t, x, f)[2]
    lo, hi = nominal * (1.0 - span), nominal * (1.0 + span)
    coarse = np.linspace(lo, hi, 61)
    qs = np.array([q(f) for f in coarse])
    i = int(np.argmax(qs))
    best, best_q = float(coarse[i]), float(qs[i])
    if 0 < i < len(coarse) - 1:
        f0, f1, f2 = coarse[i - 1:i + 2]
        q0, q1, q2 = qs[i - 1:i + 2]
        denom = (q0 - 2.0 * q1 + q2)
        if denom < 0.0:                                          # a peak: the parabola's vertex
            vertex = f1 - 0.5 * (q2 - q0) / denom * (f2 - f1)
            if lo <= vertex <= hi and q(vertex) > best_q:
                best, best_q = float(vertex), q(vertex)
    step = (coarse[1] - coarse[0]) / 10.0
    for f in np.arange(best - 5.0 * step, best + 5.0 * step, step):
        if lo <= f <= hi and q(f) > best_q:
            best, best_q = float(f), q(f)
    return best, best_q


def response_point(t, reference, response, frequency, refine=True):
    """One Bode point from `reference` and `response` sampled at the same times `t`, driven at about `frequency`:
    with `refine` the frequency the reference really shows (refine_frequency) is fitted and reported."""
    if refine:
        frequency, _q = refine_frequency(t, reference, frequency)
    a_ref, p_ref, q_ref = fit_sine(t, reference, frequency)
    a_res, p_res, q_res = fit_sine(t, response, frequency)
    if a_ref <= 0.0:
        raise ValueError("the reference shows no sinusoid at this frequency")
    gain_db = 20.0 * math.log10(a_res / a_ref) if a_res > 0.0 else -math.inf
    phase = math.degrees(p_res - p_ref)
    phase = (phase + 180.0) % 360.0 - 180.0
    return BodePoint(frequency, gain_db, phase, min(q_ref, q_res))


def align(t_ref, reference, t_res, response):
    """The samples the two signals share - both come from the same telemetry frames, so matching time stamps mean the
    same frame; a frame decoded for one and not (yet) the other is dropped."""
    common, i, j = np.intersect1d(np.asarray(t_ref, float), np.asarray(t_res, float), return_indices=True)
    return common, np.asarray(reference, float)[i], np.asarray(response, float)[j]


def bandwidth(points):
    """The -3 dB frequency, relative to the LOWEST-frequency gain (the loop's own pass-band, not 0 dB - a speed loop
    may sit a little below or above unity), interpolated in log-frequency between the two points it falls between;
    None if the sweep never drops 3 dB."""
    pts = sorted(points, key=lambda p: p.frequency)
    if len(pts) < 2:
        return None
    target = pts[0].gain_db - 3.0
    for prev, cur in zip(pts, pts[1:]):
        if prev.gain_db > target >= cur.gain_db:
            frac = (prev.gain_db - target) / (prev.gain_db - cur.gain_db)
            return prev.frequency * (cur.frequency / prev.frequency) ** frac
    return None


def phase_at(points, frequency):
    """The phase at `frequency`, interpolated in log-frequency; None outside the sweep."""
    pts = sorted(points, key=lambda p: p.frequency)
    if not pts or not pts[0].frequency <= frequency <= pts[-1].frequency:
        return None
    for prev, cur in zip(pts, pts[1:]):
        if prev.frequency <= frequency <= cur.frequency:
            if cur.frequency == prev.frequency:
                return prev.phase_deg
            frac = math.log(frequency / prev.frequency) / math.log(cur.frequency / prev.frequency)
            return prev.phase_deg + frac * (cur.phase_deg - prev.phase_deg)
    return pts[-1].phase_deg


def resonance(points, above_db=1.0):
    """The point with the highest gain, if it rises at least `above_db` above the lowest-frequency gain (a mechanical
    resonance the notch filters are for); None otherwise."""
    pts = sorted(points, key=lambda p: p.frequency)
    if len(pts) < 2:
        return None
    peak = max(pts, key=lambda p: p.gain_db)
    return peak if peak.gain_db >= pts[0].gain_db + above_db and peak is not pts[0] else None


# ---- the drive's own frequency sweep (Wave Form 3), measured on the bench drive 2026-09-25 -------------------------
CHIRP = 3                    # the Testing commands' Wave Form value for the frequency sweep
CHIRP_CYCLE = 10.0           # one sweep lasts 10 x Period, then it starts over
CHIRP_SHAPE = 4.1            # ln(f / f0) = CHIRP_SHAPE * (tau / cycle)^2 fits the measured sweep (faster than exponential)
CHIRP_TOP_HZ = 450.0         # where the zero crossings put the top of a cycle (reached only in its last few percent)


def chirp_frequency(tau, period):
    """The instantaneous frequency of the drive's frequency sweep `tau` seconds into it, for a Period of `period` s:
    starts at 1/Period, rises faster than exponentially and reaches a few hundred hertz only in the last few percent
    of a cycle of CHIRP_CYCLE x Period, then starts over (measured with Period 0.1 s and 1 s on the Iq loop, 2026-09-25).
    The simulator's sweep and the page's own notes use this; the measurement itself never assumes it."""
    cycle = CHIRP_CYCLE * period
    x = (tau % cycle) / cycle
    return math.exp(CHIRP_SHAPE * x * x) / period


EXCITED_FRACTION = 0.1       # a segment counts for a bin only if the reference's energy there is within 10 dB of the best segment
CHIRP_SPLIT_HZ = 20.0        # below: long segments (fine bins); above: short ones (the sweep races through, selectivity matters)
CHIRP_LONG_S, CHIRP_SHORT_S = 2.0, 0.25


def welch_response(reference, response, fs, segment_s=2.0, overlap=4, excited=EXCITED_FRACTION):
    """-> (frequencies, H, coherence) - the frequency response `response`/`reference` of uniformly sampled signals
    (one telemetry frame per timer tick, `fs` frames per second), by Welch's method: Hann-windowed segments of about
    `segment_s` (a power of two of samples), a hop of 1/`overlap` of a segment, H = Pxy / Pxx, coherence =
    |Pxy|^2 / (Pxx Pyy). A swept sine excites each frequency only while it passes it, so for every bin only the
    segments in which the reference's energy there is within `excited` of the best segment's are summed: the
    others hold nothing but the response's own noise at that frequency, and summing them too drowned everything
    above ~100 Hz (measured 2026-09-25 - with the selection the same recording matched the stepped sine to 260 Hz).
    Coherence is 1.0 where the response follows the reference linearly, near 0 where it did not or nothing excited it."""
    x = np.asarray(reference, dtype=float)
    y = np.asarray(response, dtype=float)
    n = 1 << max(4, int(round(math.log2(max(fs * segment_s, 16.0)))))      # the power of two nearest segment_s
    if len(x) != len(y) or len(x) < 2 * n:
        raise ValueError(f"at least {2 * n} samples are needed ({2 * n / fs:.1f} s at {fs:g} Hz), got {len(x)}")
    window = np.hanning(n)
    hop = max(1, n // overlap)
    starts = range(0, len(x) - n + 1, hop)
    fx = np.array([np.fft.rfft((x[s:s + n] - x[s:s + n].mean()) * window) for s in starts])
    fy = np.array([np.fft.rfft((y[s:s + n] - y[s:s + n].mean()) * window) for s in starts])
    pxx_s, pyy_s, pxy_s = np.abs(fx) ** 2, np.abs(fy) ** 2, np.conj(fx) * fy
    use = pxx_s >= excited * pxx_s.max(axis=0)                              # per bin: the segments that excited it
    pxx, pyy, pxy = (pxx_s * use).sum(axis=0), (pyy_s * use).sum(axis=0), (pxy_s * use).sum(axis=0)
    frequencies = np.fft.rfftfreq(n, 1.0 / fs)
    with np.errstate(divide="ignore", invalid="ignore"):
        h = np.where(pxx > 0.0, pxy / np.where(pxx > 0.0, pxx, 1.0), 0.0)
        coherence = np.where(pxx * pyy > 0.0, np.abs(pxy) ** 2 / np.where(pxx * pyy > 0.0, pxx * pyy, 1.0), 0.0)
    return frequencies, h, np.clip(coherence, 0.0, 1.0)


def chirp_response(reference, response, fs, f_lo, f_hi, split_hz=CHIRP_SPLIT_HZ):
    """BodePoints for a recorded chirp: long segments (CHIRP_LONG_S, fine bins) for the bands below `split_hz`, short
    ones (CHIRP_SHORT_S) above it, where the sweep races through and only a short segment isolates its passage."""
    points = []
    if f_lo < split_hz:
        f, h, coh = welch_response(reference, response, fs, CHIRP_LONG_S)
        points += chirp_points(f, h, coh, f_lo, min(split_hz, f_hi))
    if f_hi > split_hz:
        f, h, coh = welch_response(reference, response, fs, CHIRP_SHORT_S)
        points += chirp_points(f, h, coh, max(split_hz, f_lo), f_hi)
    return points


def chirp_points(frequencies, h, coherence, f_lo, f_hi, per_octave=12):
    """BodePoints from a Welch estimate, averaged in log-spaced bands (`per_octave` of them per octave) between `f_lo`
    and `f_hi`: gain from |mean H|, phase from its angle, quality = the mean coherence of the band (drawn hollow below
    QUALITY_FLOOR like a stepped-sine point whose fit was poor)."""
    if not 0.0 < f_lo < f_hi:
        raise ValueError("f_lo must be positive and below f_hi")
    steps = int(math.ceil(math.log2(f_hi / f_lo) * per_octave))
    edges = f_lo * 2.0 ** (np.arange(steps + 1) / per_octave)
    points = []
    for lo, hi in zip(edges, edges[1:]):
        m = (frequencies >= lo) & (frequencies < hi)
        if not m.any():
            continue
        mean_h = complex(np.mean(h[m]))
        if abs(mean_h) <= 0.0:
            continue
        gain_db = 20.0 * math.log10(abs(mean_h))
        phase = (math.degrees(math.atan2(mean_h.imag, mean_h.real)) + 180.0) % 360.0 - 180.0
        points.append(BodePoint(float(math.sqrt(lo * hi)), gain_db, phase, float(np.mean(coherence[m]))))
    return points


# ---- the Nyquist view: from the measured CLOSED loop to the open loop and its margins -------------------------------------
def open_loop(points):
    """-> [(frequency, L, quality)]: the open-loop response behind each measured closed-loop point, L = T / (1 - T), for a
    loop with unity feedback whose reference enters at the summing point (the drive's tests inject the reference there;
    a feed-forward feature would bypass part of the loop and put this off). Near |T| = 1 (well inside the pass band)
    1 - T is tiny and L is mostly measurement noise - far outside the Nyquist view, harmless; the margins live at
    crossover, where |T| ~ 0.7 and the division is sound. Measured on the bench drive 2026-09-25: the current loop's L
    came out as the integrator Kp / (s L) the PI-with-pole-cancellation theory says, phase -90 deg across the band."""
    out = []
    for p in points:
        t = 10.0 ** (p.gain_db / 20.0) * cmath.exp(1j * math.radians(p.phase_deg))
        d = 1.0 - t
        if abs(d) < 1e-9:
            continue
        out.append((p.frequency, t / d, p.quality))
    return out


def margins(points, floor=QUALITY_FLOOR):
    """-> Margins: the phase margin (180 deg + the open loop's phase where |L| crosses 1, the gain crossover) and the gain
    margin (-|L| in dB where the phase crosses -180 deg, the phase crossover), each interpolated in log frequency between
    the trustworthy points that straddle it; None where the crossing is not within the measured range."""
    ol = [(f, l) for f, l, q in open_loop(sorted(points, key=lambda p: p.frequency)) if q >= floor]
    if len(ol) < 2:
        return Margins(None, None, None, None)
    f = np.array([x[0] for x in ol])
    mag = np.array([abs(x[1]) for x in ol])
    phase = np.degrees(np.unwrap(np.array([cmath.phase(x[1]) for x in ol])))
    shift = 360.0 * math.floor((float(np.median(phase)) + 360.0) / 360.0)             # the bulk of the curve in (-360, 0]
    phase -= shift
    pm = gm = fc = fp = None
    for i in range(len(f) - 1):
        if pm is None and mag[i] >= 1.0 > mag[i + 1]:
            w = (mag[i] - 1.0) / (mag[i] - mag[i + 1])
            fc = float(f[i] * (f[i + 1] / f[i]) ** w)
            pm = float(180.0 + phase[i] + w * (phase[i + 1] - phase[i]))
        if gm is None and phase[i] > -180.0 >= phase[i + 1]:
            w = (phase[i] + 180.0) / (phase[i] - phase[i + 1])
            fp = float(f[i] * (f[i + 1] / f[i]) ** w)
            crossing = mag[i] + w * (mag[i + 1] - mag[i])
            gm = float(-20.0 * math.log10(crossing)) if crossing > 0 else None
    return Margins(pm, fc, gm, fp)


def csv_rows(points):
    yield ("frequency_hz", "gain_db", "phase_deg", "quality", "open_loop_db", "open_loop_phase_deg")
    ol = {f: l for f, l, _q in open_loop(points)}
    for p in points:
        l = ol.get(p.frequency)
        yield (f"{p.frequency:.6g}", f"{p.gain_db:.3f}", f"{p.phase_deg:.2f}", f"{p.quality:.3f}",
               f"{20.0 * math.log10(abs(l)):.3f}" if l is not None and abs(l) > 0 else "",
               f"{math.degrees(cmath.phase(l)):.2f}" if l is not None else "")
