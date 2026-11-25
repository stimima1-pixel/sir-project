"""Automatic Test - the engine behind the Automatic Test page (2026-09-25, user: "just press one button and software
will perform some test and give you the result ... press another button and it will show what to change and if user
agree then software automatically apply it and check again ... until it resolves the issue (name 2nd function as beta)").

No Qt here: everything runs in a worker thread with a `cancel` threading.Event and a `progress(text)` callback, on a
DriveClient. What RUN TESTS does (`run_tests`): read-only checks (`health_checks`), optionally the drive's own
automatic configuration first (`run_autoconfiguration` - the same rules as the tuner page: the command sent ONCE,
harmless reads keep the drive alive, completion = the Datasheet changed, then a capped wait for Operation Mode to
leave Autoconfiguration), then a step test and a chirp on the current loop and, when an encoder is configured, on the
speed loop (`measure_loop` - the Frequency Response page's own switch-telemetry / test / restore procedure, in one
synchronous function). What BETA does (`propose` + `apply`, driven round by round by the page): "as fast as the drive
allows" - FOC T set to L/R, FOC Kp raised by half each round while the loop stays clean (no peaking above
PEAK_LIMIT_DB, no step overshoot above OVERSHOOT_LIMIT_PCT), backed off to the last clean gain when a limit appears,
stopped when the bandwidth outruns what the sampling can verify or the gain reaches the cap; a notch filter on a
resonance peak of the speed loop; ESC Kp the same way as FOC Kp once an encoder makes the speed loop measurable.
"""
import math
import time
from collections import namedtuple

import numpy as np

from . import bode, defs, protocol as P
from .client import DriveError, DriveTimeout

Check = namedtuple("Check", "name status value note")            # status: ok | warn | fail | skip
Measurement = namedtuple("Measurement", "loop amplitude sample_hz rise_ms overshoot_pct bandwidth_hz peak_db coherent_to_hz points margins")
# margins: bode.Margins from the chirp's points (the Nyquist view) - the phase margin at the gain crossover and the gain
# margin at the phase crossover, None where a crossing lies outside the measured range
Proposal = namedtuple("Proposal", "key name old new reason")
Report = namedtuple("Report", "checks values current speed autotuned")

FOC_KP, FOC_T = (0x2002, 0x12), (0x2002, 0x13)
ESC_KP, ESC_T = (0x2002, 0x22), (0x2002, 0x23)
SPEED_FILTER_T, SPEED_FILTER_ON = (0x2002, 0x02), (0x2004, 0x73)
MOTOR_TYPE, I_MAX, POLES, R_LL, L_LL = (0x2000, 0x01), (0x2000, 0x02), (0x2000, 0x03), (0x2000, 0x04), (0x2000, 0x05)
MOTOR_ENCODER, SERVO_ENCODER = (0x2000, 0x0A), (0x2000, 0x0B)
NOTCHES = (((0x2014, 0x02), (0x2014, 0x03), (0x2014, 0x04)), ((0x2015, 0x02), (0x2015, 0x03), (0x2015, 0x04)))   # enable, centre, bandwidth
HEARTBEAT_TIMEOUT, TELEMETRY_FREQUENCY = (0x3000, 0x04), (0x3000, 0x05)
TIMER_ISR, CONTROL_FREQUENCY = (0x5000, 0xA9), (0x5000, 0xA2)
MAPPING_SLOTS = [(0x1A00 + message, slot) for message in range(3) for slot in range(1, 5)]
MEASURED_KEYS = ((0x2000, 0x04), (0x2000, 0x05), (0x2000, 0x06), (0x2000, 0x08), (0x2000, 0x07), (0x2000, 0x09))
LOOPS = {"current": ("testing_iq_current_control", (0x4000, 0x20), (0x4001, 0x03)),        # Reference: Current -> FOC: Iq Current
         "speed": ("testing_electronic_speed_control_esc", (0x4000, 0x21), (0x4000, 0x10))}  # Reference: Speed -> Speed
CONFIG_KEYS = (FOC_KP, FOC_T, ESC_KP, ESC_T, SPEED_FILTER_T, SPEED_FILTER_ON, MOTOR_TYPE, I_MAX, POLES, R_LL, L_LL,
               MOTOR_ENCODER, SERVO_ENCODER, HEARTBEAT_TIMEOUT, TELEMETRY_FREQUENCY) + tuple(k for notch in NOTCHES for k in notch)

AMPLITUDE_FRACTION, AMPLITUDE_CAP_A = 0.25, 1.0     # the current tests use a quarter of Maximum Continuous Current, at most 1 A
SPEED_AMPLITUDE_HZ = 10.0                           # the speed tests (electrical Hz)
STEP_PERIOD_S, STEP_RECORD_S = 0.2, 2.0             # a square wave: an edge every 0.1 s, ten edges recorded
CHIRP = {"current": (0.1, 6), "speed": (0.5, 3)}    # (Period, cycles): 10-450 Hz in 7 s / 2-450 Hz in 16 s (see bode.py)
PEAK_LIMIT_DB, OVERSHOOT_LIMIT_PCT = 1.0, 20.0      # "clean": no gain peak above the pass band, no step overshoot beyond this
PHASE_MARGIN_FLOOR_DEG, GAIN_MARGIN_FLOOR_DB = 45.0, 6.0   # ... and, where the Nyquist crossings are in range, not below these
KP_STEP, KP_BACKOFF = 1.5, 1.0 / 1.5                # Beta raises a gain by half per round, lowers it by the same factor
MAX_ROUNDS = 5
VERIFIABLE_FRACTION = 0.3                           # a bandwidth beyond 0.3 x the sampling rate cannot be checked for peaking
T_TOLERANCE = 0.2                                   # FOC T within 20 % of L/R counts as the pole cancelled
CAP_DIVISOR = 20.0                                  # the current loop is capped at control frequency / 20 (Kp = 2 pi f L)
DEFAULT_CONTROL_HZ, DEFAULT_TIMER_HZ = 20850.0, 1000.0
FIRST_POLL_S, POLL_S, AUTOCONFIG_TIMEOUT_S, SETTLE_MAX_S = 20.0, 4.0, 240.0, 10.0   # the tuner page's own timing (user, 2026-09-26)
NO_BUS_VOLTS = 5.0
QUALITY_FLOOR = 0.6


class Cancelled(Exception):
    """STOP was pressed: the drive has been stopped and the telemetry put back."""


def _regs():
    return {r.key: r for r in defs.load_registers(False)}


def _sleep(cancel, seconds):
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0.0:
            return
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        time.sleep(min(0.05, left))


def heartbeat_period(timeout):
    return max(0.25, min(1.0, float(timeout) / 4)) if timeout else 0.5


# ---- reading and checking ----------------------------------------------------------------------------------------------
def read_values(client):
    """Every parameter the checks and Beta reason about, plus the drive's timer and control rates (defaults when a drive
    - or the simulator - does not answer those two telemetry registers)."""
    regs = _regs()
    values = {key: client.read(regs[key]) for key in CONFIG_KEYS}
    for key, default, floor in ((TIMER_ISR, DEFAULT_TIMER_HZ, 100.0), (CONTROL_FREQUENCY, DEFAULT_CONTROL_HZ, 1000.0)):
        try:
            value = float(client.read_raw(*key).value("FLOAT32"))
        except (DriveError, DriveTimeout, ValueError):
            value = default
        values[key] = value if value >= floor else default          # a drive (or the simulator) answering 0 gets the known rate
    return values


def phase_l(values):
    return float(values[L_LL]) / 2.0


def phase_r(values):
    return float(values[R_LL]) / 2.0


def has_encoder(values):
    return int(values.get(MOTOR_ENCODER, 0) or 0) != 0 or int(values.get(SERVO_ENCODER, 0) or 0) != 0


def predicted_bandwidth_hz(values):
    """The closed current loop with the PI zero on the motor's pole is first order with bandwidth Kp / L (measured to a
    hertz on the bench drive, 2026-09-25)."""
    l_phase = phase_l(values)
    return float(values[FOC_KP]) / (2.0 * math.pi * l_phase) if l_phase > 0 else float("nan")


def kp_cap(values):
    """FOC Kp for a current-loop bandwidth of control frequency / CAP_DIVISOR - beyond that the loop runs into its own
    sampling and the current-sense noise."""
    return 2.0 * math.pi * (float(values[CONTROL_FREQUENCY]) / CAP_DIVISOR) * phase_l(values)


def health_checks(client, values):
    checks = [Check("Connection", "ok", f"node {client.node}", "the drive answers")]
    snap = client.telemetry_snapshot()
    faults = snap.get("fault_bits")
    if faults is None:
        checks.append(Check("Faults", "warn", "not broadcast", "Fault Bits are not in the telemetry layout"))
    elif int(faults):
        checks.append(Check("Faults", "fail", f"0x{int(faults):04X}", defs.fault_names_text(int(faults), defs.load_telemetry()[1])))
    else:
        checks.append(Check("Faults", "ok", "none", ""))
    vbus = snap.get("vbus")
    if vbus is None:
        checks.append(Check("DC bus", "warn", "not broadcast", ""))
    elif float(vbus) < NO_BUS_VOLTS:
        checks.append(Check("DC bus", "fail", f"{float(vbus):.1f} V", "the power stage is not supplied"))
    else:
        checks.append(Check("DC bus", "ok", f"{float(vbus):.1f} V", ""))
    r, l_ll, poles, i_max = float(values[R_LL]), float(values[L_LL]), int(values[POLES]), float(values[I_MAX])
    motor_ok = r > 0 and l_ll > 0 and poles >= 2 and poles % 2 == 0 and i_max > 0
    checks.append(Check("Motor data", "ok" if motor_ok else "fail",
                        f"R {r:.3g} Ω, L {l_ll * 1000:.3g} mH, {poles} poles, {i_max:g} A",
                        "" if motor_ok else "resistance, inductance, an even pole count and a current limit are needed"))
    brushless = int(values[MOTOR_TYPE]) == 0
    checks.append(Check("Motor type", "ok" if brushless else "warn", "brushless" if brushless else "brushed",
                        "" if brushless else "the automatic test measures the brushless control loops"))
    if has_encoder(values):
        checks.append(Check("Feedback", "ok", f"motor encoder {int(values[MOTOR_ENCODER])}, servo encoder {int(values[SERVO_ENCODER])}",
                            "the speed loop can be measured"))
    else:
        checks.append(Check("Feedback", "warn", "sensorless", "no encoder: the speed loop's response is an estimate and is not measured"))
    if not client.mapping_from_drive:
        checks.append(Check("Telemetry layout", "fail", "unknown", "the mapping could not be read, so it cannot be switched for a measurement"))
    elif client.tpdo_overflow:
        checks.append(Check("Telemetry layout", "warn", "does not fit", "; ".join(reason for _c, _s, reason in client.tpdo_overflow)))
    else:
        checks.append(Check("Telemetry layout", "ok", "read from the drive", ""))
    timer = float(values[TIMER_ISR])
    checks.append(Check("Timer rate", "ok", f"{timer:g} Hz",
                        "sampling for the measurements" + (" - 2000 Hz would double it (Frequency Response page)" if timer < 2000 else "")))
    lr = phase_l(values) / phase_r(values) if phase_r(values) > 0 else None
    t = float(values[FOC_T])
    if lr is None:
        checks.append(Check("FOC T vs L/R", "warn", f"{t * 1000:.3g} ms", "no resistance to compare with"))
    elif abs(t - lr) <= T_TOLERANCE * lr:
        checks.append(Check("FOC T vs L/R", "ok", f"{t * 1000:.3g} ms vs {lr * 1000:.3g} ms", "the PI zero cancels the motor's electrical pole"))
    else:
        checks.append(Check("FOC T vs L/R", "warn", f"{t * 1000:.3g} ms vs {lr * 1000:.3g} ms", "Beta would set it to L/R"))
    bw, cap, kp = predicted_bandwidth_hz(values), kp_cap(values), float(values[FOC_KP])
    checks.append(Check("Current loop, predicted", "ok" if kp <= cap else "warn", f"{bw:.3g} Hz from Kp {kp:.3g} V/A",
                        f"cap {cap:.3g} V/A for this motor and control rate" + ("" if kp <= cap else " - exceeded")))
    return checks


# ---- the drive's own automatic configuration -----------------------------------------------------------------------------
def run_autoconfiguration(client, values, cancel, progress):
    """-> the Datasheet keys the procedure changed. The command goes out ONCE; harmless reads keep the drive going; the
    Datasheet changing IS completion (then a capped wait for Operation Mode to leave Autoconfiguration). STOP sends Reset -
    the only command the drive obeys in that mode."""
    regs = _regs()
    before = {k: client.read(regs[k]) for k in MEASURED_KEYS}
    key = "autoconfiguration_brushless_motor" if int(values[MOTOR_TYPE]) == 0 else "autoconfiguration_brushed_motor"
    client.send_command(key, float(values[I_MAX]), int(values[POLES]))
    client.set_heartbeat(heartbeat_period(values.get(HEARTBEAT_TIMEOUT)))
    started = time.monotonic()
    changed, polled = [], False
    try:
        while not changed:
            elapsed = time.monotonic() - started
            if elapsed > AUTOCONFIG_TIMEOUT_S:
                raise DriveError(f"the automatic tuning did not change the Datasheet within {AUTOCONFIG_TIMEOUT_S:.0f} s")
            progress(f"Automatic tuning running… {elapsed:.0f} s")
            _sleep(cancel, POLL_S if polled else FIRST_POLL_S)            # once at the start (above), 20 s later, then every 4 s
            polled = True
            try:
                now = {k: client.read(regs[k]) for k in MEASURED_KEYS}
            except DriveTimeout:
                continue                                                   # busy: asked again next time
            changed = [k for k in MEASURED_KEYS if P.wire_value(regs[k].dtype, before[k]) != P.wire_value(regs[k].dtype, now[k])]
        settled = time.monotonic()
        while time.monotonic() - settled < SETTLE_MAX_S:
            mode = client.telemetry_snapshot().get("op_mode")
            if mode is None or int(mode) != defs.OP_MODE_AUTOCONFIG:
                break
            progress("Automatic tuning: the drive is finishing…")
            _sleep(cancel, 0.5)
    except Cancelled:
        client.reset()
        raise
    finally:
        client.set_heartbeat(None)
    return changed


# ---- measuring a loop ----------------------------------------------------------------------------------------------------
def _switch_telemetry(client, ref_reg, resp_reg, rate):
    regs = _regs()
    original = {"frequency": client.read(regs[TELEMETRY_FREQUENCY]), "mapping": {k: client.read(regs[k]) for k in MAPPING_SLOTS}}
    wanted = {k: 0 for k in MAPPING_SLOTS}
    wanted[(0x1A00, 1)], wanted[(0x1A00, 2)] = defs.mapping_code(*ref_reg), defs.mapping_code(*resp_reg)
    for k in MAPPING_SLOTS:
        if original["mapping"][k] != wanted[k]:
            client.write(regs[k], wanted[k])
    if original["frequency"] != rate:
        client.write(regs[TELEMETRY_FREQUENCY], rate)
    return original


def _restore_telemetry(client, original):
    regs, failures = _regs(), []
    for k, code in original["mapping"].items():
        try:
            if client.read(regs[k]) != code:
                client.write(regs[k], code)
        except (DriveError, ValueError) as exc:
            failures.append(f"0x{k[0]:04X}:0x{k[1]:02X} = {code} ({exc})")
    try:
        if client.read(regs[TELEMETRY_FREQUENCY]) != original["frequency"]:
            client.write(regs[TELEMETRY_FREQUENCY], original["frequency"])
    except (DriveError, ValueError) as exc:
        failures.append(f"CANopen: telemetry frequency = {original['frequency']:g} ({exc})")
    return failures


def _capture(client, ref_key, resp_key, seconds):
    t_ref, ref = client.history_snapshot(ref_key, seconds=seconds)
    t_res, res = client.history_snapshot(resp_key, seconds=seconds)
    return bode.align(t_ref, ref, t_res, res)


def step_metrics(t, ref, res, amplitude):
    """-> (rise_ms, overshoot_pct, edges) from the rising edges of a square-wave reference: the response's 10-90 % rise
    and its overshoot over the settled level; None when no edge settled."""
    t, ref, res = np.asarray(t, float), np.asarray(ref, float), np.asarray(res, float)
    if len(t) < 30:
        return None
    dt = float(np.median(np.diff(t)))
    lead, tail = max(3, int(0.01 / dt)), max(6, int(0.06 / dt))         # 10 ms before, 60 ms after an edge
    half = 0.5 * amplitude
    rising = np.where((ref[1:] > half) & (ref[:-1] < -half))[0] + 1
    rises, overs = [], []
    for i in rising:
        if i < lead + 2 or i + tail >= len(t):
            continue
        base = res[i - lead:i - 1].mean()
        final = res[i + tail * 2 // 3:i + tail].mean()
        span = final - base
        if abs(span) < 0.4 * amplitude:
            continue
        seg = (res[i - 1:i + tail] - base) / span
        ts = t[i - 1:i + tail] - t[i]
        t10 = ts[int(np.argmax(seg >= 0.1))]
        t90 = ts[int(np.argmax(seg >= 0.9))]
        rises.append((t90 - t10) * 1000.0)
        overs.append((float(seg.max()) - 1.0) * 100.0)
    if not rises:
        return None
    return float(np.mean(rises)), float(np.mean(overs)), len(rises)


def chirp_metrics(points):
    """-> (bandwidth_hz, peak_db, coherent_to_hz) from the trustworthy points of a chirp: the -3 dB frequency, the highest
    gain above the pass band, the highest frequency still coherent."""
    good = [p for p in points if p.quality >= QUALITY_FLOOR]
    if len(good) < 3:
        return None, None, None
    base = float(np.mean([p.gain_db for p in good[:3]]))
    peak = max(p.gain_db for p in good) - base
    return bode.bandwidth(good), peak, max(p.frequency for p in good)


def measure_loop(client, loop, values, cancel, progress):
    """The Frequency Response page's procedure, synchronously: telemetry switched to this loop's reference and response
    in message 0 at the timer rate, a square-wave step test, a chirp, the telemetry put back - always, STOP included."""
    key, ref_reg, resp_reg = LOOPS[loop]
    amplitude = min(AMPLITUDE_FRACTION * float(values[I_MAX]), AMPLITUDE_CAP_A) if loop == "current" else SPEED_AMPLITUDE_HZ
    rate = float(values[TIMER_ISR])
    ref_key, resp_key = defs.TELEMETRY_KEYS[ref_reg], defs.TELEMETRY_KEYS[resp_reg]
    period, cycles = CHIRP[loop]
    original = _switch_telemetry(client, ref_reg, resp_reg, rate)
    try:
        _sleep(cancel, 0.5)
        progress(f"{loop.capitalize()} loop: step test, {amplitude:g} {'A' if loop == 'current' else 'Hz'}…")
        client.run_test(key, STEP_PERIOD_S, amplitude, 0)
        _sleep(cancel, 0.5 + STEP_RECORD_S)
        t, ref, res = _capture(client, ref_key, resp_key, STEP_RECORD_S)
        client.stop()
        step = step_metrics(t, ref, res, amplitude)
        _sleep(cancel, 0.5)
        seconds = cycles * bode.CHIRP_CYCLE * period * 1.08
        progress(f"{loop.capitalize()} loop: chirp from {1.0 / period:g} Hz, {seconds:.0f} s…")
        client.run_test(key, period, amplitude, bode.CHIRP)
        _sleep(cancel, seconds + 0.3)
        t, ref, res = _capture(client, ref_key, resp_key, seconds)
        client.stop()
        if len(t) < bode.MIN_SAMPLES or t[-1] <= t[0]:
            raise DriveError("no telemetry samples arrived for the chirp")
        fs = (len(t) - 1) / float(t[-1] - t[0])
        points = bode.chirp_response(ref, res, fs, 1.0 / period, min(bode.CHIRP_TOP_HZ, 0.45 * fs))
        bandwidth, peak, coherent_to = chirp_metrics(points)
        rise, overshoot = (step[0], step[1]) if step else (None, None)
        return Measurement(loop, amplitude, fs, rise, overshoot, bandwidth, peak, coherent_to, points, bode.margins(points))
    finally:
        try:
            client.stop()
        except DriveError:
            pass
        _restore_telemetry(client, original)


# ---- RUN TESTS -----------------------------------------------------------------------------------------------------------
def run_tests(client, options, cancel, progress):
    """options: {"autotune": bool, "current": bool, "speed": bool} -> Report."""
    progress("Reading the drive…")
    values = read_values(client)
    checks = health_checks(client, values)
    autotuned = None
    if options.get("autotune") and not any(c.status == "fail" for c in checks):
        progress("Automatic tuning: starting the drive's own procedure…")
        autotuned = run_autoconfiguration(client, values, cancel, progress)
        values = read_values(client)
        checks = health_checks(client, values)
        checks.append(Check("Automatic tuning", "ok", f"{len(autotuned)} measured value(s) changed", "the drive's own procedure completed"))
    current = speed = None
    if any(c.status == "fail" for c in checks):
        checks.append(Check("Measurements", "skip", "not run", "a failed check above must be cleared first"))
        return Report(checks, values, None, None, autotuned)
    if options.get("current"):
        current = measure_loop(client, "current", values, cancel, progress)
        checks += measurement_checks(current, values)
    else:
        checks.append(Check("Current loop", "skip", "not selected", ""))
    if not options.get("speed"):
        checks.append(Check("Speed loop", "skip", "not selected", ""))
    elif has_encoder(values):
        speed = measure_loop(client, "speed", values, cancel, progress)
        checks += measurement_checks(speed, values)
    else:
        checks.append(Check("Speed loop", "skip", "not measured", "no encoder is configured (Feedback Settings)"))
    return Report(checks, values, current, speed, autotuned)


def measurement_checks(m, values):
    name = "Current loop" if m.loop == "current" else "Speed loop"
    out = []
    if m.rise_ms is None:
        out.append(Check(f"{name} step", "warn", "no clean edge", "the response did not settle between edges"))
    else:
        over = m.overshoot_pct
        out.append(Check(f"{name} step", "ok" if over <= OVERSHOOT_LIMIT_PCT else "warn",
                         f"rise {m.rise_ms:.1f} ms, overshoot {over:.0f} %", "" if over <= OVERSHOOT_LIMIT_PCT else "overshoot beyond the limit"))
    if m.bandwidth_hz is None:
        if m.coherent_to_hz is None:
            out.append(Check(f"{name} bandwidth", "warn", "not measured", "the chirp's response was not coherent with its reference"))
        else:
            out.append(Check(f"{name} bandwidth", "ok", f"above {m.coherent_to_hz:.3g} Hz", "no -3 dB point within the coherent range"))
    else:
        out.append(Check(f"{name} bandwidth", "ok", f"{m.bandwidth_hz:.3g} Hz (-3 dB)", f"coherent to {m.coherent_to_hz:.3g} Hz"))
    if m.peak_db is not None:
        out.append(Check(f"{name} peaking", "ok" if m.peak_db <= PEAK_LIMIT_DB else "warn", f"{m.peak_db:+.1f} dB",
                         "" if m.peak_db <= PEAK_LIMIT_DB else "a resonance or too much gain"))
    mg = m.margins
    if mg is None or mg.phase_margin_deg is None:
        out.append(Check(f"{name} margins", "skip", "crossover beyond the range", "the open loop stays above 1 within the measured range"))
    else:
        low = mg.phase_margin_deg < PHASE_MARGIN_FLOOR_DEG
        value = f"phase {mg.phase_margin_deg:.0f}° at {mg.gain_crossover_hz:.3g} Hz"
        note = f"floor {PHASE_MARGIN_FLOOR_DEG:g}°"
        if mg.gain_margin_db is not None:
            value += f", gain {mg.gain_margin_db:.1f} dB at {mg.phase_crossover_hz:.3g} Hz"
            note += f" / {GAIN_MARGIN_FLOOR_DB:g} dB"
            low = low or mg.gain_margin_db < GAIN_MARGIN_FLOOR_DB
        else:
            note += "; no phase crossover in range"
        out.append(Check(f"{name} margins", "warn" if low else "ok", value, note + (" - below the floor" if low else "")))
    if m.loop == "current" and m.bandwidth_hz is not None:
        predicted = predicted_bandwidth_hz(values)
        out.append(Check("Current loop vs Kp/L", "ok" if abs(m.bandwidth_hz - predicted) < 0.3 * predicted else "warn",
                         f"measured {m.bandwidth_hz:.3g} Hz, predicted {predicted:.3g} Hz", ""))
    return out


# ---- BETA ------------------------------------------------------------------------------------------------------------------
def _resonance(points):
    good = [p for p in points if p.quality >= QUALITY_FLOOR]
    if len(good) < 3:
        return None
    peak = bode.resonance(good, PEAK_LIMIT_DB)
    return peak


def _limits_hit(m):
    """-> the reasons this measurement is not 'clean': peaking, step overshoot, or a Nyquist margin below its floor
    (only where the crossing is inside the measured range - an unreadable margin is not a failed one)."""
    reasons = []
    if m.peak_db is not None and m.peak_db > PEAK_LIMIT_DB:
        reasons.append(f"peaking {m.peak_db:+.1f} dB")
    if m.overshoot_pct is not None and m.overshoot_pct > OVERSHOOT_LIMIT_PCT:
        reasons.append(f"overshoot {m.overshoot_pct:.0f} %")
    mg = m.margins
    if mg is not None and mg.phase_margin_deg is not None and mg.phase_margin_deg < PHASE_MARGIN_FLOOR_DEG:
        reasons.append(f"phase margin {mg.phase_margin_deg:.0f}° under the {PHASE_MARGIN_FLOOR_DEG:g}° floor")
    if mg is not None and mg.gain_margin_db is not None and mg.gain_margin_db < GAIN_MARGIN_FLOOR_DB:
        reasons.append(f"gain margin {mg.gain_margin_db:.1f} dB under the {GAIN_MARGIN_FLOOR_DB:g} dB floor")
    return reasons


def propose(report, state):
    """-> [Proposal]; `state` (a dict the page keeps across rounds) records the last clean gains and, once Beta is finished,
    state["done"] = the reason. "Clean" = no peaking, no overshoot beyond the limit, and - where the Nyquist crossings
    are in range - phase and gain margins above their floors (_limits_hit)."""
    values, props = report.values, []
    l_phase, r_phase = phase_l(values), phase_r(values)
    lr = l_phase / r_phase if r_phase > 0 else None
    t = float(values[FOC_T])
    if lr is not None and abs(t - lr) > T_TOLERANCE * lr:
        props.append(Proposal(FOC_T, "FOC T", t, round(lr, 6), "set the integral time to L/R: the PI zero then cancels the motor's electrical pole"))
    m = report.current
    if m is not None:
        kp, cap = float(values[FOC_KP]), kp_cap(values)
        reasons = _limits_hit(m)
        if reasons:
            what = " and ".join(reasons)
            last = state.get("last_good_kp")
            if last is not None and last < kp:
                props.append(Proposal(FOC_KP, "FOC Kp", kp, last, f"{what}: back to the last clean gain"))
                state["done"] = f"FOC Kp backed off to {last:.3g} V/A - the last gain with no peaking or overshoot"
            else:
                props.append(Proposal(FOC_KP, "FOC Kp", kp, round(kp * KP_BACKOFF, 4), f"{what} at the starting gain: lowering it"))
        else:
            state["last_good_kp"] = kp
            verifiable = m.bandwidth_hz is not None and m.bandwidth_hz < VERIFIABLE_FRACTION * m.sample_hz
            if m.bandwidth_hz is None and m.coherent_to_hz is not None:
                state["done"] = f"the current loop is flat to {m.coherent_to_hz:.3g} Hz - beyond what this measurement can verify"
            elif not verifiable:
                state["done"] = f"the current loop is as fast as {m.sample_hz:g} Hz sampling can verify ({m.bandwidth_hz:.3g} Hz)"
            elif kp * KP_STEP > cap * 1.001:
                state["done"] = f"FOC Kp is at the cap for this motor and control rate ({cap:.3g} V/A)"
            else:
                props.append(Proposal(FOC_KP, "FOC Kp", kp, round(min(kp * KP_STEP, cap), 4),
                                      f"clean at {m.bandwidth_hz:.3g} Hz: raise the gain by half (cap {cap:.3g} V/A)"))
    s = report.speed
    if s is not None:
        peak = _resonance(s.points)
        if peak is not None:
            free = next((n for n in NOTCHES if not int(values[n[0]])), None)
            if free is not None:
                enable, centre, width = free
                props += [Proposal(centre, "Notch filter centre", values[centre], round(peak.frequency, 1), f"a resonance peak of {peak.gain_db:+.1f} dB at {peak.frequency:.3g} Hz"),
                          Proposal(width, "Notch filter bandwidth", values[width], round(peak.frequency / 4.0, 2), "a quarter of the centre, for drift with wear and payload"),
                          Proposal(enable, "Notch filter enable", values[enable], 1, "switch it on")]
        else:
            esc_kp = float(values[ESC_KP])
            reasons = _limits_hit(s)
            if reasons:
                what = " and ".join(reasons)
                last = state.get("last_good_esc_kp")
                if last is not None and last < esc_kp:
                    props.append(Proposal(ESC_KP, "ESC Kp", esc_kp, last, f"speed loop: {what}: back to the last clean gain"))
                    state.setdefault("done", f"ESC Kp backed off to {last:.3g} - the last clean gain")
                else:
                    props.append(Proposal(ESC_KP, "ESC Kp", esc_kp, round(esc_kp * KP_BACKOFF, 5), f"speed loop: {what} at the starting gain: lowering it"))
            elif s.bandwidth_hz is not None and s.bandwidth_hz < VERIFIABLE_FRACTION * s.sample_hz:
                state["last_good_esc_kp"] = esc_kp
                props.append(Proposal(ESC_KP, "ESC Kp", esc_kp, round(esc_kp * KP_STEP, 5), f"the speed loop is clean at {s.bandwidth_hz:.3g} Hz: raise the gain by half"))
    if not props and "done" not in state:
        state["done"] = "nothing left to change"
    return props


def apply(client, proposals):
    """Writes each proposal (RAM only - Save to Flash makes it survive a power cycle) and reads it back."""
    regs, applied = _regs(), []
    for p in proposals:
        client.write(regs[p.key], p.new)
        applied.append((p, client.read(regs[p.key])))
    return applied
