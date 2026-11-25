"""Capture the RAW telemetry of the speed loop's reference (0x4000:0x21) and response (0x4000:0x10) while the drive's
own ESC test runs, on the USB cable - the bench tool that found (2026-09-25) that "FLOAT16" is fixed point x256: asked
for a 1 s sine Period the drive ran 60 s, because 1.0 as a half float is 0x3C00 = 15360 and the drive divides by 256.
    python tools/test_signal_probe.py                                   (1 Hz sine, then 1 Hz square, 3 s each, 6 Hz amplitude)
    python tools/test_signal_probe.py '[["sine_4hz", 2, 0.25, 2.0, 4.0]]'   (label, wave form, period s, seconds, fit Hz)
A guard between the client and the port lets through ONLY: SDO reads, SDO writes of the 12 telemetry-mapping slots and
the telemetry frequency to the exact values used (the measurement layout, then the originals), the listed test command
frames, and Stop. The motor MOVES (the test drives the speed loop with the wave you ask for at AMP Hz electrical) - run
it only with the user's go-ahead. Everything is restored in `finally` and read back; each window is written to
`test_signal_<label>.csv` (t, reference, speed) next to the current directory, with min/max/mean and a sine fit at the
expected frequency printed (fit quality 1.0 = a clean sine at that frequency: the drive's own sine, once the period is
encoded right).
"""

import csv
import json
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np                                                   # noqa: E402
from sdr_tuner import bode, defs, protocol as P                      # noqa: E402
from sdr_tuner.client import DriveClient, DriveError                 # noqa: E402
from sdr_tuner.usb import UsbSerialBus, find_drive_ports             # noqa: E402

OUT = Path.cwd()
NODE = 1
AMP, PERIOD = 6.0, 1.0
TESTS = json.loads(sys.argv[1]) if len(sys.argv) > 1 else [["sine_1hz", 2, 1.0, 3.0, 1.0], ["square_1hz", 0, 1.0, 3.0, 1.0]]
LOOP = sys.argv[2] if len(sys.argv) > 2 else "esc"          # esc: Reference: Speed -> Speed | iq: Reference: Current -> FOC: Iq Current
COMMAND = {"esc": "testing_electronic_speed_control_esc", "iq": "testing_iq_current_control"}[LOOP]
REF, RESP = {"esc": ((0x4000, 0x21), (0x4000, 0x10)), "iq": ((0x4000, 0x20), (0x4001, 0x03))}[LOOP]
AMP = float(sys.argv[3]) if len(sys.argv) > 3 else AMP
TIMER_ISR, FAULT_BITS, VBUS, OP_MODE = (0x5000, 0xA9), (0x4000, 0x03), (0x5001, 0x0D), (0x4000, 0x02)
SLOTS = [(0x1A00 + m, s) for m in range(3) for s in range(1, 5)]
TELEMETRY_FREQUENCY, RECEIVING = (0x3000, 0x05), (0x3000, 0x06)


class Guard:
    def __init__(self, bus):
        self._bus, self._writes, self._commands = bus, {}, set()

    def allow_write(self, reg, values):
        self._writes.setdefault(reg.key, set()).update(P.encode_value(reg.dtype, v).ljust(4, b"\0") for v in values)

    def allow_command(self, can_id, data):
        self._commands.add((can_id, bytes(data)))

    def send(self, msg, timeout=None):
        data = bytes(msg.data)
        cid = msg.arbitration_id
        ok = False
        if not msg.is_extended_id and len(data) == 8:
            if (cid & P.COB_MASK) == P.COB_REQUEST:
                ok = data[0] == P.CMD_READ or (data[0] == P.CMD_WRITE and
                                               data[4:8] in self._writes.get((data[1] | data[2] << 8, data[3]), ()))
            else:
                ok = (cid, data) in self._commands
        if not ok:
            raise RuntimeError(f"BLOCKED: refusing to send 0x{cid:03X} {data.hex(' ')}")
        self._bus.send(msg, timeout)

    def recv(self, timeout=None):
        return self._bus.recv(timeout)

    def shutdown(self):
        self._bus.shutdown()


ports = find_drive_ports()
assert len(ports) == 1, ports
guard = Guard(UsbSerialBus(ports[0]))
client = DriveClient(guard, NODE, timeout=0.5, retries=1)
raw = []                                                            # (t, can_id, data) of every rx frame on 0x180+node
client.frame_listeners.append(lambda d, t, cid, data: raw.append((t, cid, bytes(data))) if d == "rx" and cid == 0x180 + NODE else None)
client.start()
config = {r.key: r for r in defs.load_registers(False)}
tele = {r.key: r for r in defs.load_registers(True)}
cmds = defs.load_commands()


def tread(key, dtype=None):
    return client.read_raw(*key).value(dtype or tele[key].dtype)


def frame(key, *values):
    c = cmds[key]
    return P.command_frame(NODE, c.cob, c.code, [(p.offset, p.dtype, v) for p, v in zip(c.params, values)])


def decode(window):
    t0 = window[0][0] if window else 0
    return np.array([(t - t0, *struct.unpack("<ff", d[:8])) for t, _cid, d in window])


def capture(label, seconds, fit_hz=1.0):
    start = time.monotonic()
    time.sleep(seconds)
    end = time.monotonic()
    window = [(t, cid, d) for t, cid, d in raw if start <= t <= end]
    arr = decode(window)
    path = OUT / f"test_signal_{label}.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "reference_speed_hz", "speed_hz"])
        w.writerows(arr.tolist())
    n = len(arr)
    print(f"\n== {label}: {n} frames in {end - start:.2f} s ({n / (end - start):.0f}/s) -> {path.name}")
    if n > 2:
        dt = np.diff(arr[:, 0]) * 1000.0
        print(f"   frame spacing ms: median {np.median(dt):.3f}  p90 {np.percentile(dt, 90):.3f}  max {dt.max():.3f}  "
              f"zero-gaps {(dt < 0.05).sum()} of {len(dt)}  (bunching = timestamps that lie)")
    if n:
        for name, col in (("reference", 1), ("speed", 2)):
            v = arr[:, col]
            print(f"   {name:<10} min {v.min():9.3f}  max {v.max():9.3f}  mean {v.mean():9.3f}  std {v.std():8.3f}")
        if n >= bode.MIN_SAMPLES:
            for name, col in (("reference", 1), ("speed", 2)):
                a, ph, q = bode.fit_sine(arr[:, 0], arr[:, col], fit_hz)
                print(f"   sine fit @ {fit_hz:g} Hz  {name:<10} amplitude {a:8.3f}  phase {np.degrees(ph):8.1f} deg  quality {q:.3f}")
        step = max(1, n // 24)
        print("   every %d-th sample (t, ref, speed):" % step, [tuple(round(x, 2) for x in row) for row in arr[::step].tolist()])
    hist_ref = client.history_snapshot(defs.TELEMETRY_KEYS[REF], seconds=seconds)
    hist_res = client.history_snapshot(defs.TELEMETRY_KEYS[RESP], seconds=seconds)
    print(f"   client history: ref {len(hist_ref[0])} samples, response {len(hist_res[0])} samples "
          f"(the page reads these) ; snapshot {client.telemetry_snapshot()}")
    return arr


original = None
try:
    time.sleep(1.5)
    nodes = client.detect_nodes(wait=0.1)
    print("nodes heard:", nodes)
    assert nodes == [NODE], f"expected node {NODE} only"
    print(f"USING NODE {client.node}")
    faults, vbus, timer, receiving = tread(FAULT_BITS), tread(VBUS), tread(TIMER_ISR), client.read(config[RECEIVING])
    print(f"fault bits {faults} | DC bus {vbus:.2f} V | Timer ISR {timer:g} Hz | receiving frequency {receiving:g} Hz")
    assert int(faults) == 0 and vbus > 20.0, "fault or unpowered bus - not testing"
    original = {"frequency": client.read(config[TELEMETRY_FREQUENCY]), "mapping": {k: client.read(config[k]) for k in SLOTS}}
    print("original layout:", json.dumps({f"{k[0]:#x}:{k[1]}": v for k, v in original["mapping"].items()}), "frequency", original["frequency"])
    wanted = {k: 0 for k in SLOTS}
    wanted[(0x1A00, 1)], wanted[(0x1A00, 2)] = defs.mapping_code(*REF), defs.mapping_code(*RESP)
    for k in SLOTS:
        guard.allow_write(config[k], {wanted[k], original["mapping"][k]})
    guard.allow_write(config[TELEMETRY_FREQUENCY], {float(timer), original["frequency"]})
    for _label, wave, period, _secs, *_rest in TESTS:
        guard.allow_command(*frame(COMMAND, period, AMP, wave))
    guard.allow_command(*frame("stop"))
    print("switching the telemetry to reference + speed in message 0 at", timer, "Hz")
    for k in SLOTS:
        if original["mapping"][k] != wanted[k]:
            client.write(config[k], wanted[k])
    client.write(config[TELEMETRY_FREQUENCY], float(timer))
    print("client mapping now:", client.tpdo_overflow, "| keys in snapshot:", sorted(client.telemetry_snapshot()))
    capture("idle", 1.0)

    for label, wave, period, secs, *rest in TESTS:
        fit_hz = rest[0] if rest else 1.0 / period
        print(f"\n>>> {COMMAND} wave form {wave}, Period {period} s (FLOAT16 -> {P.decode_value('FLOAT16', P.encode_value('FLOAT16', period)):.6g}), amplitude {AMP} Hz, {secs} s")
        client.run_test(COMMAND, period, AMP, wave)
        time.sleep(0.5)
        capture(label, secs, fit_hz=fit_hz)
        client.stop()
        time.sleep(1.0)
    capture("after", 1.0)
finally:
    try:
        client.stop()
    except Exception as exc:                        # noqa: BLE001
        print("stop:", exc)
    if original is not None:
        print("\nrestoring the original layout and frequency")
        for k in SLOTS:
            try:
                if client.read(config[k]) != original["mapping"][k]:
                    client.write(config[k], original["mapping"][k])
            except DriveError as exc:
                print("restore", k, exc)
        client.write(config[TELEMETRY_FREQUENCY], original["frequency"])
        back = {"frequency": client.read(config[TELEMETRY_FREQUENCY]), "mapping": {k: client.read(config[k]) for k in SLOTS}}
        print("restored exactly:", back == original, "| fault bits now:", tread(FAULT_BITS), "| op mode:", tread(OP_MODE))
    client.close()
