"""Find a drive's usable telemetry-frequency ceiling (`CANopen: telemetry frequency`, 0x3000:0x05) on its USB cable.
Sends ONLY SDO reads and SDO writes of that one parameter, to the exact values it steps through (a guard sits between
the client and the port and aborts the run if anything else would ever be sent); measures the frames really received
per TPDO message at each step; stops stepping up as soon as reads degrade or the rate stops rising; and restores the
original value in a `finally` block, whatever happens - the drive is left exactly as found.
    python tools/telemetry_rate_probe.py                (finds the drive's USB port and node by itself)
Measured on the bench drive (2026-09-25, node 1): the parameter applies IMMEDIATELY (no power cycle, despite the
catalog's flag - see the README), and the drive caps at 1000 messages/s total = 333 Hz per TPDO message (asked for
3000, got 1000), with SDO reads still answering in 1 ms throughout.
Second pass the same day (user: "see if you can go higher"): 1200 / 1500 / 2000 / 5000 / 10000 Hz all give exactly
1000/s - a firmware cap; raising `CANopen: receiving frequency` (the drive's tick) to 2000 Hz changes nothing; but with
telemetry messages 1 and 2 UNMAPPED the one remaining message gets the whole budget - 1000 frames/s on its own. So
the way to sample faster is fewer messages: put the signals that matter (up to 8 bytes) in ONE message and they are
sampled at 1000 Hz, not 333. Everything was restored and read back after each pass; fault bits stayed 0.
"""
import collections, statistics, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sdr_tuner import defs, protocol as P
from sdr_tuner.client import DriveClient, DriveError
from sdr_tuner.usb import UsbSerialBus, find_drive_ports

STEPS = [30.0, 100.0, 300.0, 1000.0, 3000.0]
MEASURE_S = 3.0

class Guard:
    def __init__(self, bus):
        self._bus, self._writes = bus, {}
    def allow_write(self, reg, values):
        self._writes[reg.key] = {P.encode_value(reg.dtype, v).ljust(4, b"\0") for v in values}
    def send(self, msg, timeout=None):
        data = bytes(msg.data)
        ok = (not msg.is_extended_id and len(data) == 8 and (msg.arbitration_id & P.COB_MASK) == P.COB_REQUEST
              and (data[0] == P.CMD_READ or (data[0] == P.CMD_WRITE and data[4:8] in self._writes.get((data[1] | data[2] << 8, data[3]), ()))))
        if not ok:
            raise RuntimeError(f"BLOCKED: refusing to send 0x{msg.arbitration_id:03X} {data.hex(' ')}")
        self._bus.send(msg, timeout)
    def recv(self, timeout=None):
        return self._bus.recv(timeout)
    def shutdown(self):
        self._bus.shutdown()

ports = find_drive_ports()
assert len(ports) == 1, ports
counts = collections.Counter()
guard = Guard(UsbSerialBus(ports[0]))
client = DriveClient(guard, 1, timeout=0.5, retries=1)
client.frame_listeners.append(lambda d, t, cid, data: counts.update([cid & 0x780]) if d == "rx" else None)
client.start()
reg = next(r for r in defs.load_registers(False) if r.name == "CANopen: telemetry frequency")
print("register:", f"0x{reg.index:04X}:0x{reg.sub:02X}", reg.dtype, "power_cycle flag:", getattr(reg, "power_cycle", None))

def measure(label):
    counts.clear()
    t0 = time.monotonic(); time.sleep(MEASURE_S); dt = time.monotonic() - t0
    per = {hex(c): round(n / dt, 1) for c, n in sorted(counts.items()) if c in (0x180, 0x280, 0x380)}
    total = round(sum(counts[c] for c in (0x180, 0x280, 0x380)) / dt, 1)
    rtts = []
    fails = 0
    for _ in range(5):
        t = time.monotonic()
        try:
            client.read(reg); rtts.append((time.monotonic() - t) * 1000)
        except DriveError:
            fails += 1
    print(f"  {label:<18} per-message {per}  total {total}/s  | SDO read: {fails} failed, "
          f"median {statistics.median(rtts):.0f} ms" if rtts else f"  {label:<18} per-message {per}  total {total}/s  | SDO read: all {fails} failed")
    return total, fails

original = None
try:
    time.sleep(2.0)
    nodes = client.detect_nodes(wait=0.1)
    print("nodes heard:", nodes)
    assert len(nodes) == 1, "one node expected"
    client.node = nodes[0]
    print(f"USING NODE {client.node}")
    original = client.read(reg)
    receiving = client.read(next(r for r in defs.load_registers(False) if r.name == "CANopen: receiving frequency"))
    print(f"current telemetry frequency = {original} Hz (receiving frequency = {receiving} Hz)")
    guard.allow_write(reg, STEPS + [original])
    base, _ = measure(f"at {original:g} Hz:")
    last = base
    for value in STEPS:
        print(f"writing {value:g} Hz ...")
        client.write(reg, value)
        time.sleep(1.0)
        total, fails = measure(f"at {value:g} Hz:")
        if fails:
            print("  -> SDO reads failing at this rate: stopping here"); break
        if total < last * 1.2 and value > STEPS[0]:
            print("  -> rate no longer rising: the ceiling is about here"); break
        last = total
finally:
    if original is not None:
        print(f"restoring {original:g} Hz ...")
        try:
            client.write(reg, original)
            time.sleep(1.0)
            print("  read back:", client.read(reg))
            measure("restored:")
        except Exception as exc:
            print("  RESTORE FAILED:", exc)
    client.close()
