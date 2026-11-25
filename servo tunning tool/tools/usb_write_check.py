"""Write and speed-command check for a drive on its USB cable.  MOTOR NOT CONNECTED, DC BUS UNPOWERED.

What it does, in this order, and nothing else:
  1. listens, finds the node, checks that the DC bus is unpowered (below 2 V)
  2. reads all configuration parameters (those the drive answers) and saves them as a backup file (load it in the tool to
     restore by hand)
  3. for 6 harmless parameters (encoder and work-zone settings that are unused with no encoder fitted):
     writes a new value, reads it back, writes the ORIGINAL value back, reads it back again
  4. re-reads all parameters and compares them with the backup
  5. only if the DC bus is still unpowered: sends a speed command (5 Hz for 2 s), then the Stop command
  6. re-reads all parameters again and compares them with the backup

A guard between the client and the port lets through ONLY: read requests; writes to those 6 parameters with exactly
the planned new / original value; and, once the DC-bus check has armed it, speed frames of exactly 5.0 or 0.0 Hz and
the Stop command.
Anything else raises and stops the run. Original values are restored in a `finally` block, whatever happens.

    python tools/usb_write_check.py /dev/cu.usbmodem...
"""
import argparse
import json
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdr_tuner import defs, protocol as P  # noqa: E402
from sdr_tuner.client import DriveClient, DriveError  # noqa: E402
from sdr_tuner.usb import UsbSerialBus  # noqa: E402

TEST_PARAMS = [                 # address - what it is
    "0x2014:0x03",              # Notch Filter 0: center frequency (FLOAT32)
    "0x2014:0x02",              # Notch Filter 0: enable (BOOL)
    "0x2034:0xC2",              # Work Zone: Dimension (UINT32)
    "0x3013:0x31",              # SSI/BiSS-C MULTI-TURN start bit (UINT16)
    "0x2034:0xB0",              # Work Zone: zero offset (INT32)
    "0x3013:0x12",              # SSI/BiSS-C clock polarity (UINT16, option list)
]
SPEED_HZ, SPEED_SECONDS, ESC_COB = 5.0, 2.0, 0x200
ESC_CODE, STOP_CODE = 0x20, 0x04
MAX_BUS_VOLTS = 2.0


class Guard:
    """Sits between the client and the port. Lets through only what this check is meant to send."""

    def __init__(self, bus):
        self._bus = bus
        self._writes = {}               # (index, sub) -> allowed 4-byte values
        self._speeds = set()            # allowed 4-byte speed values (empty until armed)
        self._stop_armed = False        # the Stop command is allowed once the speed test is armed
        self.sent = []

    def allow_write(self, reg, values):
        self._writes[reg.key] = {P.encode_value(reg.dtype, v).ljust(4, b"\0") for v in values}

    def arm_speed(self, hz_values):
        self._speeds = {struct.pack("<f", v) for v in hz_values}
        self._stop_armed = True

    def send(self, msg, timeout=None):
        data = bytes(msg.data)
        cob = msg.arbitration_id & P.COB_MASK
        ok = not msg.is_extended_id and len(data) == 8
        if ok and cob == P.COB_REQUEST:
            if data[0] == P.CMD_READ:
                pass
            elif data[0] == P.CMD_WRITE:
                ok = data[4:8] in self._writes.get((data[1] | data[2] << 8, data[3]), ())
            else:
                ok = False
        elif ok and cob == ESC_COB:
            if data[0] == ESC_CODE:
                ok = data[1:4] == bytes(3) and data[4:8] in self._speeds
            elif data[0] == STOP_CODE:
                ok = self._stop_armed and data[1:] == bytes(7)
            else:
                ok = False
        else:
            ok = False
        if not ok:
            raise RuntimeError(f"BLOCKED: refusing to send 0x{msg.arbitration_id:03X} {data.hex(' ')}")
        self.sent.append((msg.arbitration_id, data))
        self._bus.send(msg, timeout)

    def recv(self, timeout=None):
        return self._bus.recv(timeout)

    def shutdown(self):
        self._bus.shutdown()


def new_value(reg, orig):
    """A different, valid value for the write test."""
    if reg.dtype == "BOOL":
        return not orig
    if reg.options:
        return next(k for k in reg.options if k != orig)
    if reg.dtype == "FLOAT32":
        return orig + 50.0
    if reg.dtype == "UINT32":
        return orig + 1
    if reg.dtype == "INT32":
        return orig - 5
    return orig + 1


def stored_as(reg, value):
    return P.decode_value(reg.dtype, P.encode_value(reg.dtype, value))


def read_all(client, regs, skip_unreadable=False):
    """-> {key: value}, or with skip_unreadable ({key: value}, [(reg, error)]) for parameters the drive refuses to give."""
    values, unreadable = {}, []
    for reg in regs:
        try:
            values[reg.key] = client.read(reg)
        except DriveError as exc:
            if not skip_unreadable:
                raise
            unreadable.append((reg, exc))
    return (values, unreadable) if skip_unreadable else values


def save_backup(path, regs, values, node):
    params = []
    for reg in regs:
        v = values[reg.key]
        if P.is_float(reg.dtype):
            v = float(str(np.float32(v)))
        params.append({"index": f"0x{reg.index:04X}", "sub": f"0x{reg.sub:02X}", "name": reg.name, "value": v})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": "sdr-parameters", "version": 1, "device": "SDR-CB-60R", "node": node,
                                "params": params}, indent=2))


def compare(regs, before, after):
    return [(reg, before[reg.key], after[reg.key]) for reg in regs if stored_as(reg, before[reg.key]) != stored_as(reg, after[reg.key])]


def run(bus, *, node=None, listen=2.0, max_bus_volts=MAX_BUS_VOLTS, backup_dir=None, out=print):
    """Returns True if everything passed."""
    regs = defs.load_registers(False)               # configuration; the telemetry parameters change all the time
    by_addr = {r.address: r for r in regs}
    guard = Guard(bus)
    client = DriveClient(guard, node or 1, timeout=0.5, retries=1)
    tpdo_cobs = {t.cob for t in client.tpdos}
    events = []                                     # every non-telemetry frame, to see how the drive answers writes
    client.frame_listeners.append(
        lambda d, t, cid, data: events.append((d, cid, bytes(data))) if (cid & P.COB_MASK) not in tpdo_cobs else None)
    problems = []
    client.start()
    original = None
    try:
        out(f"listening for {listen:.1f} s (nothing sent)")
        time.sleep(listen)
        nodes = client.detect_nodes(wait=0.1)
        out(f"  nodes heard: {nodes}")
        if not node:
            if len(nodes) != 1:
                out(f"cannot choose a node (heard {nodes}); pass --node")
                return False
            client.node = nodes[0]
        time.sleep(1.2)
        snap = client.telemetry_snapshot()
        vbus, fault0 = snap.get("vbus"), snap.get("fault_bits")
        unpowered = vbus is not None and vbus < max_bus_volts
        out(f"node {client.node}: DC bus {vbus if vbus is None else round(vbus, 3)} V, fault bits {fault0!r} -> "
            f"power stage {'UNPOWERED' if unpowered else 'NOT confirmed unpowered: the speed test will be skipped'}")

        out(f"poles: {client.identify()}")
        try:
            client.load_tpdo_mapping()              # decode the broadcast the way this drive is configured
        except DriveError as exc:
            out(f"  (could not read the telemetry mapping, using the default: {exc})")
        original, unreadable = read_all(client, regs, skip_unreadable=True)
        for reg, exc in unreadable:
            out(f"  not readable, left out of the backup and the comparisons: {reg.address} {reg.name}: {exc}")
        regs = [r for r in regs if r.key in original]
        backup = Path(backup_dir or ROOT / "tools" / "backups") / f"node{client.node}_{datetime.now():%Y%m%d_%H%M%S}.json"
        save_backup(backup, regs, original, client.node)
        out(f"backup of {len(original)} parameters saved: {backup}")

        out("\nWRITE TEST (new value, read back, original value, read back)")
        plan = []
        for addr in TEST_PARAMS:
            reg = by_addr[addr]
            orig = original[reg.key]
            new = new_value(reg, orig)
            guard.allow_write(reg, [orig, new])
            plan.append((reg, orig, new))
        for reg, orig, new in plan:
            mark = len(events)
            try:
                got = client.write(reg, new)
                good = got == stored_as(reg, new)
                out(f"  {reg.address} {reg.name}: {orig!r} -> wrote {new!r}, read back {got!r}  {'ok' if good else 'MISMATCH'}")
                if not good:
                    problems.append(f"{reg.address}: wrote {new!r}, read back {got!r}")
            except (DriveError, ValueError) as exc:
                out(f"  {reg.address} {reg.name}: write FAILED: {exc}")
                problems.append(f"{reg.address}: write failed: {exc}")
            finally:
                for attempt in (1, 2):              # always put the original value back
                    try:
                        back = client.write(reg, orig)
                        if back == stored_as(reg, orig):
                            out(f"      restored {orig!r}, read back {back!r}  ok")
                            break
                    except (DriveError, ValueError) as exc:
                        out(f"      restore attempt {attempt} failed: {exc}")
                else:
                    problems.append(f"{reg.address}: COULD NOT RESTORE {orig!r} - use the backup file")
            for d, cid, data in events[mark:]:
                out(f"        {d} 0x{cid:03X} {data.hex(' ')}")

        diff = compare(regs, original, read_all(client, regs))
        out(f"\nafter the write test: {'all ' + str(len(regs)) + ' parameters identical to the backup' if not diff else 'DIFFERENT: ' + str(diff)}")
        problems += [f"{r.address} differs after the write test" for r, _, _ in diff]

        out("\nSPEED COMMAND TEST")
        if not unpowered:
            out("  skipping the speed test (DC bus not confirmed unpowered)")
        else:
            guard.arm_speed([SPEED_HZ, 0.0])
            mark = len(events)
            try:
                client.set_speed_hz(SPEED_HZ)
                t_end = time.monotonic() + SPEED_SECONDS
                while time.monotonic() < t_end:
                    time.sleep(0.4)
                    s = client.telemetry_snapshot()
                    out(f"  commanding {SPEED_HZ} Hz: speed {s.get('speed_hz')!r} Hz, DC bus {round(s.get('vbus', 0), 3)} V, "
                        f"fault bits 0x{int(s.get('fault_bits', 0)):04X}, Ia {s.get('ia')!r}, Ib {s.get('ib')!r}")
                    if s.get("vbus", 0) >= max_bus_volts:
                        problems.append("DC bus appeared during the speed test - stopped")
                        break
            finally:
                client.stop()
            time.sleep(1.0)
            s = client.telemetry_snapshot()
            out(f"  stopped (Stop command sent): speed {s.get('speed_hz')!r} Hz, fault bits 0x{int(s.get('fault_bits', 0)):04X}")
            others = [(d, cid, data) for d, cid, data in events[mark:] if d == "rx"]
            out(f"  frames the drive sent back during the test (other than telemetry): {len(others)}")
            for d, cid, data in others[:6]:
                out(f"        {d} 0x{cid:03X} {data.hex(' ')}")

        diff = compare(regs, original, read_all(client, regs))
        out(f"\nfinal check: {'all ' + str(len(regs)) + ' parameters identical to the backup' if not diff else 'DIFFERENT: ' + str(diff)}")
        problems += [f"{r.address} differs at the end" for r, _, _ in diff]
    except Exception as exc:                        # noqa: BLE001 - report, then restore below
        out(f"ABORTED: {type(exc).__name__}: {exc}")
        problems.append(f"aborted: {exc}")
    finally:
        try:
            if client.commanded is not None:
                client.stop()
        except Exception:                           # noqa: BLE001
            pass
        if original is not None:                    # last line of defence: anything still different gets restored
            for reg in (by_addr[a] for a in TEST_PARAMS):
                try:
                    if stored_as(reg, client.read(reg)) != stored_as(reg, original[reg.key]):
                        client.write(reg, original[reg.key])
                        out(f"  cleanup: restored {reg.address} to {original[reg.key]!r}")
                except Exception as exc:            # noqa: BLE001
                    problems.append(f"cleanup of {reg.address} failed: {exc}")
        client.close()

    reads = sum(1 for cid, d in guard.sent if (cid & P.COB_MASK) == P.COB_REQUEST and d[0] == P.CMD_READ)
    writes = [(cid, d) for cid, d in guard.sent if (cid & P.COB_MASK) == P.COB_REQUEST and d[0] == P.CMD_WRITE]
    speeds = [struct.unpack("<f", d[4:8])[0] for cid, d in guard.sent if (cid & P.COB_MASK) == ESC_COB and d[0] == ESC_CODE]
    stops = sum(1 for cid, d in guard.sent if (cid & P.COB_MASK) == ESC_COB and d[0] == STOP_CODE)
    out(f"\nEXACTLY WHAT WAS SENT: {len(guard.sent)} frame(s): {reads} read request(s); {len(writes)} write(s); "
        f"{len(speeds)} speed frame(s) {sorted(set(speeds))}; {stops} stop command(s)")
    for cid, d in writes:
        out(f"  write 0x{cid:03X} {d.hex(' ')}")
    out("RESULT: " + ("PASS - everything restored" if not problems else "PROBLEMS: " + "; ".join(problems)))
    return not problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port")
    ap.add_argument("--node", type=int)
    ap.add_argument("--max-bus-volts", type=float, default=MAX_BUS_VOLTS)
    args = ap.parse_args()
    ok = run(UsbSerialBus(args.port), node=args.node, max_bus_volts=args.max_bus_volts)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
