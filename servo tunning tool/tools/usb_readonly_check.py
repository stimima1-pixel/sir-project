"""Read-only bring-up check for a drive on its USB cable.

Sends ONLY read requests (command code 0x40). A guard sits between the client and the port and aborts the run
if anything else would ever be sent - no parameter writes, no speed or motor commands.

    python tools/usb_readonly_check.py                              (finds the drive's USB port by itself)
    python tools/usb_readonly_check.py /dev/cu.usbmodem...          (macOS)
    python tools/usb_readonly_check.py COM5 --node 7                (Windows)
"""
import argparse
import collections
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sdr_tuner import defs, protocol as P  # noqa: E402
from sdr_tuner.client import DriveClient, DriveError  # noqa: E402
from sdr_tuner.usb import UsbSerialBus, find_drive_ports  # noqa: E402


class ReadOnlyBus:
    """Lets only SDO read requests through to the real bus."""

    def __init__(self, bus):
        self._bus = bus

    def send(self, msg, timeout=None):
        data = bytes(msg.data)
        is_read = ((msg.arbitration_id & P.COB_MASK) == P.COB_REQUEST and not msg.is_extended_id
                   and len(data) == 8 and data[0] == P.CMD_READ)
        if not is_read:
            raise RuntimeError(f"BLOCKED: refusing to send 0x{msg.arbitration_id:03X} {data.hex(' ')} (not a read request)")
        self._bus.send(msg, timeout)

    def recv(self, timeout=None):
        return self._bus.recv(timeout)

    def shutdown(self):
        self._bus.shutdown()


def describe_mapping(client):
    """The telemetry mapping the drive holds, slot by slot: the code it stores and the parameter that code stands for."""
    catalog = defs.telemetry_catalog()
    lines = []
    for message, cob in enumerate(defs.TPDO_COBS):
        for slot in range(1, 5):
            code = client.mapping_codes.get((0x1A00 + message, slot))
            if code is None:
                continue
            reg = catalog.get(defs.parse_mapping_code(code))
            meaning = ("not used" if code == 0 else
                       f"0x{reg.index:04X}:0x{reg.sub:02X}  {reg.name} [{reg.units or reg.dtype}]" if reg else "NOT A KNOWN TELEMETRY PARAMETER")
            lines.append(f"  TPDO message {message} (COB 0x{cob:03X} + node), slot {slot}: 0x{0x1A00 + message:04X}:0x{slot:02X} = {code}  ->  {meaning}")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", help="serial port of the drive (default: the drive's USB cable, found by its USB ID)")
    ap.add_argument("--node", type=int, help="node ID (default: the one node heard on the cable)")
    ap.add_argument("--listen", type=float, default=2.5, help="seconds to listen before asking anything")
    args = ap.parse_args()
    if not args.port:
        ports = find_drive_ports()
        if len(ports) != 1:
            sys.exit(f"cannot choose the drive's port automatically (found {ports or 'none'}); pass it as an argument")
        args.port = ports[0]

    sent = []
    heard = collections.Counter()
    client = DriveClient(ReadOnlyBus(UsbSerialBus(args.port)), args.node or 1, timeout=0.5, retries=1)
    client.frame_listeners.append(lambda d, t, cid, data: sent.append((cid, bytes(data))) if d == "tx" else heard.update([cid & 0x780]))
    client.start()
    try:
        print(f"opened {args.port}; listening for {args.listen:.1f} s (nothing sent)")
        time.sleep(args.listen)
        nodes = client.detect_nodes(wait=0.1)
        print(f"  frames heard by function code: { {hex(k): v for k, v in sorted(heard.items())} }")
        print(f"  nodes heard: {nodes}")
        if not args.node:
            if len(nodes) != 1:
                sys.exit(f"cannot choose a node automatically (heard {nodes}); pass --node")
            client.node = nodes[0]
        print(f"using node {client.node}")
        n0 = len(client.history_snapshot("speed_hz")[0])
        time.sleep(3.0)
        snap = client.telemetry_snapshot()
        rate = (len(client.history_snapshot("speed_hz")[0]) - n0) / 3.0
        print(f"  telemetry (TPDO) decoded for node {client.node}: {snap}")
        print(f"  telemetry rate: {rate:.1f} messages/s per TPDO message")

        t0 = time.monotonic()
        poles = client.identify()
        print(f"  identify: {poles} rotor poles ({(time.monotonic() - t0) * 1000:.0f} ms)")
        try:
            tpdos = client.load_tpdo_mapping()
            print("  the drive's telemetry mapping (a code is the hex index and sub-index written together, read as decimal):")
            print("\n".join(describe_mapping(client)))
            print("  so it broadcasts:", {hex(t.cob): [f.key for f in t.fields] for t in tpdos})
        except DriveError as exc:
            print(f"  ! could not read the telemetry mapping (the default one is used): {exc}")
        time.sleep(1.0)
        snap = client.telemetry_snapshot()

        print("reading all configuration and telemetry parameters (read requests only)")
        ok, problems, times = 0, [], []
        section = None
        for reg in defs.load_registers():
            t0 = time.monotonic()
            try:
                value = client.read(reg)
            except DriveError as exc:
                problems.append((reg, str(exc)))
                print(f"  ! {reg.address}  {reg.name}: {exc}")
                continue
            times.append((time.monotonic() - t0) * 1000)
            ok += 1
            if reg.section != section:
                section = reg.section
                print(f"  [{section}]")
            print(f"    {reg.address}  {reg.name} = {value!r} {reg.units}".rstrip())
        print(f"answered {ok} of {ok + len(problems)}; " +
              (f"round trip median {statistics.median(times):.1f} ms, max {max(times):.1f} ms" if times else "no answers"))

        print("reading the telemetry parameters by request, to compare with the broadcast")
        for tpdo in client.tpdos:
            for f in tpdo.fields:
                try:
                    r = client.read_raw(f.index, f.sub)
                    print(f"  0x{f.index:04X}:0x{f.sub:02X}  {f.name}: {r.value(f.dtype)!r}  (drive says {r.size} byte(s); broadcast has {snap.get(f.key)!r})")
                except DriveError as exc:
                    print(f"  ! 0x{f.index:04X}:0x{f.sub:02X}  {f.name}: {exc}")

        print("fast telemetry: polling by request for 3 s (read requests only)")
        client.start_polling(100.0)
        time.sleep(3.0)
        keys = ("speed_hz", "vbus", "ia", "ib", "fault_bits", "op_mode", "comm_mode", "elec_pos", "wz_count", "wz_modulo")
        print("  samples per second:", {k: round(len(client.history_snapshot(k, 3.0)[0]) / 3.0, 1) for k in keys})
        print("  fed by polling:", sorted(client.polled_keys), "| polling error:", client.poll_error)
        client.stop_polling()
    finally:
        client.close()

    kinds = collections.Counter((cid & 0x780, data[0]) for cid, data in sent)
    print(f"\nEXACTLY WHAT WAS SENT: {len(sent)} frame(s); by (CAN function code, first data byte): "
          f"{ {f'0x{a:03X}': f'0x{b:02X} x{n}' for (a, b), n in kinds.items()} }")
    print("first frames sent:", [f"0x{cid:03X} {data.hex(' ')}" for cid, data in sent[:3]])
    print("all frames were read requests:", all((cid & 0x780) == P.COB_REQUEST and data[0] == P.CMD_READ for cid, data in sent))


if __name__ == "__main__":
    main()
