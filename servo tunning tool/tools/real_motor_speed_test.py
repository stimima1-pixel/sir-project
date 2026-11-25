"""Real-motor speed command test for a drive on its USB cable. MOTOR PHYSICALLY ATTACHED, DC bus POWERED.

Unlike usb_write_check.py's own speed test (which only runs with the DC bus confirmed UNPOWERED - no motor), this
one is for exactly the opposite case: a real motor attached and the power stage genuinely live. User-confirmed,
2026-09-24 ("physical servo drive is connected check all parameters... go ahead and test it" - AskUserQuestion
answers: "Full functional test (moves the motor)", "Yes, a motor is attached"). Verification is by TELEMETRY, not a
person watching the shaft (the user's own instruction - "you don't need physical person to inspect, you can see
from drive telemetry as well").

What it does, in this order, and nothing else:
  1. listens, finds the node, reads Fault Bits/DC bus/Motor & Servo Encoder configuration - ABORTS if any fault is
     already latched (never commands a faulted drive) or DC bus is not plausibly powered
  2. sends the ESC RPM command (0x24) for exactly RPM_VALUE, for exactly RUN_SECONDS, polling telemetry every
     ~0.4 s (Work Zone Speed, DC bus, phase currents, fault bits) so the actual result is visible without anyone
     watching the motor
  3. sends Stop, then confirms (from telemetry, averaged over several samples - not just one, see the "after Stop"
     block's own comment for why a single sample is unreliable with no physical encoder fitted) the drive actually
     returns to ~0 speed and stays fault-free

A guard between the client and the port lets through ONLY: read requests, the ESC RPM command with exactly
RPM_VALUE (or 0.0, for a clean end), and the Stop command with an empty payload. Anything else raises and aborts
the run. No configuration parameter is ever written.

    python tools/real_motor_speed_test.py /dev/cu.usbmodem...
"""
import argparse
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sdr_tuner import defs, protocol as P  # noqa: E402
from sdr_tuner.client import DriveClient, DriveError  # noqa: E402
from sdr_tuner.usb import UsbSerialBus  # noqa: E402

RPM_VALUE, RUN_SECONDS = 50.0, 3.0
ESC_COB, ESC_RPM_CODE, STOP_CODE = 0x200, 0x24, 0x04
MIN_BUS_VOLTS = 5.0                # below this, "powered" is not plausible - abort rather than guess


class Guard:
    """Lets through only: read requests, the ESC RPM command at exactly one of `rpm_values`, and Stop."""

    def __init__(self, bus, rpm_values):
        self._bus = bus
        self._rpms = {struct.pack("<f", v) for v in rpm_values}
        self.sent = []

    def send(self, msg, timeout=None):
        data = bytes(msg.data)
        cob = msg.arbitration_id & P.COB_MASK
        ok = not msg.is_extended_id and len(data) == 8
        if ok and cob == P.COB_REQUEST:
            ok = data[0] == P.CMD_READ
        elif ok and cob == ESC_COB:
            if data[0] == ESC_RPM_CODE:
                ok = data[1:4] == bytes(3) and data[4:8] in self._rpms
            elif data[0] == STOP_CODE:
                ok = data[1:] == bytes(7)
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


def run(bus, *, node=None, listen=2.0, rpm=RPM_VALUE, seconds=RUN_SECONDS, out=print):
    by_addr = {r.address: r for r in defs.load_registers()}       # both configuration and telemetry registers
    guard = Guard(bus, [rpm, 0.0])
    client = DriveClient(guard, node or 1, timeout=0.5, retries=1)
    client.start()
    problems = []
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
        try:
            client.load_tpdo_mapping()
        except DriveError as exc:
            out(f"  (could not read the telemetry mapping, using the default: {exc})")
        time.sleep(1.2)

        fault_bits = client.read(by_addr["0x4000:0x03"])
        vbus = client.read(by_addr["0x5001:0x0D"])
        motor_enc = client.read(by_addr["0x2000:0x0A"])
        servo_enc = client.read(by_addr["0x2000:0x0B"])
        poles = client.identify()
        out(f"node {client.node}: fault bits 0x{int(fault_bits):04X}, DC bus {vbus:.2f} V, "
            f"motor encoder {motor_enc}, servo encoder {servo_enc}, {poles} rotor poles")
        if fault_bits:
            out("ABORTED: a fault is already latched - refusing to command a faulted drive")
            return False
        if vbus < MIN_BUS_VOLTS:
            out(f"ABORTED: DC bus {vbus:.2f} V is not plausibly powered (need >= {MIN_BUS_VOLTS} V)")
            return False

        out(f"\nSPEED COMMAND TEST: node {client.node}, {rpm} rpm for {seconds:.1f} s")
        wz_speed_reg = by_addr["0x4000:0x16"]        # read on-demand (SDO), NOT from the broadcast snapshot - a
                                                        # decode gap in this client drops this one field from
                                                        # telemetry_snapshot() even though the drive DOES broadcast
                                                        # it (confirmed separately; worth its own fix later, not
                                                        # blocking this test since a plain read works fine)
        client.set_speed_rpm(rpm)
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end:
            time.sleep(0.4)
            s = client.telemetry_snapshot()
            try:
                wz_rps = client.read(wz_speed_reg)
            except DriveError as exc:
                wz_rps = None
                out(f"  (could not read Work Zone Speed: {exc})")
            out(f"  commanding {rpm} rpm: Work Zone Speed {wz_rps if wz_rps is None else round(wz_rps * 60, 1)} rpm "
                f"({wz_rps!r} rps), DC bus {round(s.get('vbus', 0), 2)} V, fault bits 0x{int(s.get('fault_bits', 0)):04X}, "
                f"Ia {s.get('ia')!r}, Ib {s.get('ib')!r}")
            if s.get("fault_bits"):
                problems.append(f"fault bits 0x{int(s['fault_bits']):04X} appeared during the run - stopping")
                break
    except Exception as exc:                        # noqa: BLE001 - report, then stop below
        out(f"ABORTED: {type(exc).__name__}: {exc}")
        problems.append(f"aborted: {exc}")
    finally:
        try:
            client.stop()
            out("\nStop sent.")
        except Exception:                           # noqa: BLE001
            pass
        time.sleep(0.8)
        try:
            # A SINGLE reading here is not reliable evidence either way: with no physical encoder configured, Work
            # Zone Speed is a sensorless ESTIMATE, and that estimate is genuinely noisy close to zero (confirmed
            # directly on this same real drive, 2026-09-24 - 8 on-demand reads with NOTHING commanded at all still
            # ranged -14.0 to +9.7 rpm sample to sample). A real runaway shows a persistently non-zero speed; noise
            # averages toward zero. So: several samples, judge the AVERAGE, not any one of them.
            rpms = []
            for _ in range(5):
                rpms.append(client.read(by_addr["0x4000:0x16"]) * 60)
                time.sleep(0.4)
            avg = sum(rpms) / len(rpms)
            fault_bits = int(client.read(by_addr["0x4000:0x03"]))
            out(f"  after Stop: Work Zone Speed {[round(r, 1) for r in rpms]} rpm over {len(rpms)} samples "
                f"(average {avg:.1f} rpm), fault bits 0x{fault_bits:04X}")
            if abs(avg) > 8.0:
                problems.append(f"still moving after Stop: average {avg:.1f} rpm over {len(rpms)} samples")
            if fault_bits:
                problems.append(f"fault bits 0x{fault_bits:04X} present after Stop")
        except Exception as exc:                    # noqa: BLE001
            problems.append(f"could not confirm the stopped state: {exc}")
        client.close()

    reads = sum(1 for cid, d in guard.sent if (cid & P.COB_MASK) == P.COB_REQUEST and d[0] == P.CMD_READ)
    rpms = [round(struct.unpack("<f", d[4:8])[0], 3) for cid, d in guard.sent
            if (cid & P.COB_MASK) == ESC_COB and d[0] == ESC_RPM_CODE]
    stops = sum(1 for cid, d in guard.sent if (cid & P.COB_MASK) == ESC_COB and d[0] == STOP_CODE)
    out(f"\nEXACTLY WHAT WAS SENT: {len(guard.sent)} frame(s): {reads} read request(s); "
        f"{len(rpms)} ESC RPM frame(s) {sorted(set(rpms))}; {stops} stop command(s)")
    out("RESULT: " + ("PASS" if not problems else "PROBLEMS: " + "; ".join(problems)))
    return not problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port")
    ap.add_argument("--node", type=int)
    ap.add_argument("--rpm", type=float, default=RPM_VALUE)
    ap.add_argument("--seconds", type=float, default=RUN_SECONDS)
    args = ap.parse_args()
    ok = run(UsbSerialBus(args.port), node=args.node, rpm=args.rpm, seconds=args.seconds)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
