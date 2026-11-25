import time

import can
import numpy as np
import pytest
import serial
from usb_fakes import FakeSerial, UsbRig, serial_pair, wait_for

from sdr_tuner import defs, protocol as P, usb
from sdr_tuner.client import DriveClient, DriveError

REGS = defs.load_registers(False)      # the configuration parameters

# Three frames captured from a real SDR-CB-60R drive on its USB cable (node 7, no motor, no DC bus supply)
REAL_CAPTURE = b"t18780080FD1700000000\rt28780200010000000000\rt38780000000000000000\r"


# ---- text frame format ------------------------------------------------------------------------
def test_real_drive_frames_decode_with_the_rev_l_mapping():
    msgs = [usb.parse_frame(line) for line in REAL_CAPTURE.split(b"\r") if line]
    assert [(m.arbitration_id, bytes(m.data).hex()) for m in msgs] == [
        (0x187, "0080fd1700000000"), (0x287, "0200010000000000"), (0x387, "0000000000000000")]
    tpdos, fault_bits = defs.load_telemetry()
    values = {}
    for m, tpdo in zip(msgs, tpdos):
        cob, node = P.split_can_id(m.arbitration_id)
        assert node == 7 and cob == tpdo.cob                       # 0x180 / 0x280 / 0x380 + node 7
        for f in tpdo.fields:
            values[f.key] = P.decode_value(f.dtype, bytes(m.data)[f.offset:f.offset + P.type_size(f.dtype)])
    assert values["fault_bits"] == 0x8000 and fault_bits[15] == "Activation Key is Missing"
    assert values["vbus"] == pytest.approx(23.99, abs=0.01)        # "FLOAT16" 0x17FD = 6141 / 256: the 24 V supply (fixed point, 2026-09-25)
    assert values["speed_hz"] == 0.0 and values["ia"] == 0.0
    catalog = defs.telemetry_catalog()
    assert values["op_mode"] == 2 and catalog[(0x4000, 0x02)].options[2] == "Fault"       # the drive is faulted
    assert values["comm_mode"] == 1 and catalog[(0x4000, 0x15)].options[1] == "Kickstart"
    assert values["wz_count"] == 0 and values["wz_modulo"] == 0
    assert not {"ib", "elec_pos"} & values.keys()                  # not part of the broadcast


@pytest.mark.parametrize("line", [b"", b"z", b"Z", b"\x07", b"t18", b"tXYZ", b"t187", b"t18780080FD170000",
                                  b"t1879" + b"00" * 9, b"hello", b"t18780080FD1"])
def test_acks_bells_and_garbage_are_ignored(line):
    assert usb.parse_frame(line) is None


def test_bell_before_a_frame_is_skipped():
    m = usb.parse_frame(b"\x07t18780080FD1700000000")
    assert m.arbitration_id == 0x187


def test_extended_and_remote_frames_parse():
    ext = usb.parse_frame(b"T000001878" + b"00" * 8)
    assert ext.is_extended_id and ext.arbitration_id == 0x187
    remote = usb.parse_frame(b"r1870")
    assert remote.is_remote_frame and remote.arbitration_id == 0x187


def test_read_request_on_the_wire():
    can_id, data = P.read_request(7, 0x2000, 0x03)
    msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
    # 0x607, 8 bytes: code 0x40, index 0x2000 little-endian (00 20), sub 0x03, four zero bytes
    assert usb.format_frame(msg) == b"t60784000200300000000\r"


def test_write_request_on_the_wire():
    can_id, data = P.write_request(7, 0x2002, 0x22, "FLOAT32", 0.5)
    # 0x607, 8 bytes: code 0x20, index 0x2002 little-endian (02 20), sub 0x22, then 0.5f little-endian (00 00 00 3F)
    assert usb.format_frame(can.Message(arbitration_id=can_id, data=data, is_extended_id=False)) == b"t6078200220220000003F\r"


def test_extended_ids_cannot_be_sent():
    with pytest.raises(can.CanOperationError):
        usb.format_frame(can.Message(arbitration_id=0x187, is_extended_id=True, data=b""))


def test_format_and_parse_roundtrip():
    for cid in (0x001, 0x187, 0x607, 0x7FF):
        for data in (b"", b"\x01", bytes(range(8))):
            m = usb.parse_frame(usb.format_frame(can.Message(arbitration_id=cid, data=data, is_extended_id=False)).rstrip(b"\r"))
            assert (m.arbitration_id, bytes(m.data)) == (cid, data)


# ---- the bus object ---------------------------------------------------------------------------
def make_bus():
    host, drive = serial_pair()
    return usb.UsbSerialBus("fake", serial_port=host), drive, host


def test_frames_split_across_reads_are_reassembled():
    bus, drive, _ = make_bus()
    f1, f2, f3 = (line + b"\r" for line in REAL_CAPTURE.split(b"\r") if line)
    stream = b"\x07" + f1 + b"noise\r" + f2 + b"z\r" + f3        # a bell, garbage and an ack between real frames
    for i in range(0, len(stream), 5):                        # delivered 5 bytes at a time, splitting frames
        drive.write(stream[i:i + 5])
    got = [bus.recv(timeout=0.5) for _ in range(3)]
    assert [m.arbitration_id for m in got] == [0x187, 0x287, 0x387]
    assert bus.recv(timeout=0.05) is None


def test_recv_returns_none_when_the_line_is_quiet():
    bus, _, _ = make_bus()
    assert bus.recv(timeout=0.05) is None


def test_send_writes_exactly_one_frame_and_nothing_else():
    bus, drive, _ = make_bus()
    assert drive.read(100) == b""                              # nothing on open (no channel-open command)
    bus.send(can.Message(arbitration_id=0x607, data=bytes([0x40, 3, 0x20, 0, 0, 0, 0, 0]), is_extended_id=False))
    assert drive.read(100) == b"t60784003200000000000\r"
    bus.shutdown()
    assert drive.read(100) == b""                              # nothing on close either


def test_garbage_without_line_end_cannot_grow_without_bound():
    bus, drive, _ = make_bus()
    drive.write(b"x" * 10000)
    bus.recv(timeout=0.2)
    assert len(bus._buffer) < usb.MAX_LINE


def test_port_failures_become_can_errors():
    class Broken(FakeSerial):
        def read(self, size=1):
            raise serial.SerialException("device disconnected")

        def write(self, data):
            raise serial.SerialException("device disconnected")

    host, _ = serial_pair()
    bus = usb.UsbSerialBus("fake", serial_port=Broken(host._rx, host._tx))
    with pytest.raises(can.CanOperationError, match="USB read failed"):
        bus.recv(timeout=0.1)
    with pytest.raises(can.CanOperationError, match="USB write failed"):
        bus.send(can.Message(arbitration_id=0x607, data=bytes(8), is_extended_id=False))


def test_opening_a_missing_port_is_reported():
    with pytest.raises(can.CanInitializationError, match="cannot open"):
        usb.UsbSerialBus("/dev/definitely-not-a-serial-port")


def test_find_drive_ports_matches_on_usb_ids(monkeypatch):
    class Port:
        def __init__(self, device, vid, pid):
            self.device, self.vid, self.pid = device, vid, pid
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: [
        Port("/dev/cu.Bluetooth-Incoming-Port", None, None), Port("/dev/cu.usbmodemAAA", 0x0483, 0x5740),
        Port("/dev/cu.usbserialFTDI", 0x0403, 0x6001), Port("COM7", 0x0483, 0x5740)])
    assert usb.find_drive_ports() == ["/dev/cu.usbmodemAAA", "COM7"]


# ---- a whole client on a USB cable ------------------------------------------------------------
@pytest.fixture
def rig():
    r = UsbRig(node=7)
    yield r
    r.close()


def test_client_over_usb_reads_writes_and_streams(rig):
    client = DriveClient(rig.bus(), 7, timeout=0.5)
    client.start()
    try:
        assert client.identify() == 14
        for reg in REGS:
            assert client.read(reg) == P.decode_value(reg.dtype, P.encode_value(reg.dtype, rig.sim.params[reg.key])), reg.name
        esc_kp = next(r for r in REGS if r.address == "0x2002:0x22")
        assert client.write(esc_kp, 0.25) == 0.25 and rig.sim.params[esc_kp.key] == pytest.approx(0.25)
        client.set_speed_hz(15.0)
        assert wait_for(lambda: abs(client.telemetry_snapshot().get("speed_hz", 0) - 15.0) < 1.0)
        client.stop()
    finally:
        client.close()
    assert wait_for(lambda: rig.sim.esc_target_hz == 0.0)


def test_speed_and_stop_frames_on_the_wire(rig):
    client = DriveClient(rig.bus(), 7, timeout=0.5, esc_period=10.0)      # no keep-alive frames in between
    client.start()

    def sent(expected):
        return wait_for(lambda: rig.bridge.sent_lines[-1:] == [expected])
    try:
        client.set_speed_hz(5.0)
        assert sent(b"t2078200000000000A040")                             # 0x207: code 0x20, 5.0f at byte 4
        client.stop()
        assert sent(b"t20780400000000000000")                             # 0x207: Stop command (code 0x04), no parameters
        client.off()
        assert sent(b"t20780600000000000000")                             # Off (code 0x06)
        client.reset()
        assert sent(b"t20780100000000000000")                             # Reset (code 0x01)
    finally:
        client.close()


def test_client_reads_the_drives_telemetry_mapping_over_usb(rig):
    client = DriveClient(rig.bus(), 7, timeout=0.5)
    client.start()
    try:
        tpdos = client.load_tpdo_mapping()
        assert [len(t.fields) for t in tpdos] == [3, 3, 2]
        assert len(rig.bridge.sent_lines) == 12                            # 3 messages x 4 slots, all read requests
        assert all(line.startswith(b"t6078" + b"40") for line in rig.bridge.sent_lines)
    finally:
        client.close()


def test_a_read_only_session_sends_only_read_requests(rig):
    client = DriveClient(rig.bus(), 7, timeout=0.5)
    client.start()
    try:
        client.identify()
        for reg in REGS:
            client.read(reg)
    finally:
        client.close()
    lines = rig.bridge.sent_lines
    assert len(lines) == 1 + len(REGS)
    assert all(line.startswith(b"t6078" + b"40") for line in lines)        # 0x607, 8 bytes, command code 0x40 = read


def test_nodes_are_detected_from_the_broadcast(rig):
    client = DriveClient(rig.bus(), 1, timeout=0.2, retries=0)             # wrong node selected on purpose
    client.start()
    try:
        assert client.detect_nodes(wait=2.0) == [7]
        with pytest.raises(DriveError):
            client.identify()                                              # node 1 does not answer
        client.node = 7
        assert client.identify() == 14
    finally:
        client.close()


# ---- fast telemetry (polling) -----------------------------------------------------------------
def make_client(rig, node=7, **kw):
    client = DriveClient(rig.bus(), node, timeout=kw.pop("timeout", 0.5), **kw)
    client.start()
    return client


def test_polling_gives_far_more_samples_than_the_3_hz_broadcast():
    rig = UsbRig(node=7, tpdo_hz=3.0, sdo_unreadable=("wz_count",))    # 3 Hz like the real drive over USB; one value refused by request
    client = make_client(rig)
    try:
        client.identify()
        time.sleep(1.2)
        broadcast_only = len(client.history_snapshot("speed_hz", 1.0)[0])
        client.start_polling(100.0)
        time.sleep(1.5)
        polled = len(client.history_snapshot("speed_hz", 1.0)[0])
        assert broadcast_only <= 5 and polled >= 20
        assert client.polling and client.poll_error is None
        assert {"speed_hz", "vbus", "ia", "ib", "fault_bits", "elec_pos"} <= client.polled_keys
        assert "wz_count" not in client.polled_keys            # a drive that refuses it by request ...
        assert "wz_count" in client.telemetry_snapshot()       # ... so the broadcast keeps feeding it
    finally:
        client.close()
        rig.close()


def test_polled_history_is_ordered_and_follows_the_motor():
    rig = UsbRig(node=7, tpdo_hz=3.0)
    client = make_client(rig)
    try:
        client.identify()
        client.start_polling(100.0)
        client.set_speed_hz(10.0)
        assert wait_for(lambda: abs(client.telemetry_snapshot().get("speed_hz", 0) - 10.0) < 0.5, timeout=4)
        time.sleep(0.6)                                                    # keep sampling at the final speed
        t, v = client.history_snapshot("speed_hz", 3.0)
        assert (t[1:] >= t[:-1]).all() and len(t) >= 25
        assert v[0] < 5 and v[-1] == pytest.approx(10.0, abs=0.5)          # a smooth step response, not 3 Hz steps
        assert client.telemetry_snapshot()["ib"] != 0.0
        client.stop()
    finally:
        client.close()
        rig.close()


def test_each_value_can_have_its_own_polling_interval():
    rig = UsbRig(node=7, tpdo_hz=3.0)
    client = make_client(rig)
    try:
        client.identify()
        slow = {"ia": 0.25, "ib": 0.25, "vbus": 0.25}
        client.start_polling(20.0, intervals=slow)                # everything every 50 ms, except these: every 250 ms
        time.sleep(2.1)
        client.stop_polling()
        counts = {k: len(client.history_snapshot(k)[0]) for k in ("speed_hz", "elec_pos", "fault_bits", "ia", "ib", "vbus")}
        for key in ("speed_hz", "elec_pos", "fault_bits"):
            assert 32 <= counts[key] <= 46, counts                # about 20 per second
        for key in ("ia", "ib", "vbus"):
            assert 6 <= counts[key] <= 10, counts                 # about 4 per second
        assert abs(counts["ia"] - counts["ib"]) <= 1              # Ia and Ib stay paired
        t, _ = client.history_snapshot("ia")
        assert abs(float(np.median(np.diff(t))) - 0.25) < 0.05
    finally:
        client.close()
        rig.close()


def test_polling_does_not_disturb_parameter_access(rig):
    client = make_client(rig)
    try:
        client.identify()
        client.start_polling(100.0)
        for reg in REGS:
            assert client.read(reg) == P.decode_value(reg.dtype, P.encode_value(reg.dtype, rig.sim.params[reg.key])), reg.name
        esc_kp = next(r for r in REGS if r.address == "0x2002:0x22")
        assert client.write(esc_kp, 0.3) == pytest.approx(0.3, rel=1e-6)
        assert client.polling
    finally:
        client.close()


def test_stopping_polling_stops_the_requests_and_the_broadcast_takes_over(rig):
    client = make_client(rig)
    try:
        client.identify()
        client.start_polling(100.0)
        time.sleep(0.3)
        client.stop_polling()
        assert not client.polling and client.polled_keys == set()
        n = len(rig.bridge.sent_lines)
        time.sleep(0.3)
        assert len(rig.bridge.sent_lines) == n
        rig.sim.fault_bits = 5
        assert wait_for(lambda: client.telemetry_snapshot().get("fault_bits") == 5)     # from the broadcast again
    finally:
        client.close()


def test_polling_gives_up_when_the_drive_does_not_answer(rig):
    client = make_client(rig, node=1)                          # nobody on node 1
    try:
        client.start_polling(100.0)
        assert wait_for(lambda: client.poll_error, timeout=4)
        assert "stopped answering" in client.poll_error and not client.polling and client.polled_keys == set()
    finally:
        client.close()


def test_close_stops_polling(rig):
    client = make_client(rig)
    client.identify()
    client.start_polling(100.0)
    client.close()
    assert not client.polling
    n = len(rig.bridge.sent_lines)
    time.sleep(0.3)
    assert len(rig.bridge.sent_lines) == n


def test_the_port_is_opened_with_an_exclusive_lock_and_a_second_open_is_refused_clearly(monkeypatch):
    """2026-09-25: the user's own instance was connected while a second one connected too - both shared the port and
    both failed with a cryptic read error. The port is opened exclusively now, and the refusal says who has it."""
    import serial
    import can
    opened = {}

    class Locked(serial.SerialException):
        pass

    def fake_serial(port, baudrate, timeout, exclusive=False):
        opened.update(port=port, exclusive=exclusive)
        raise Locked("[Errno 35] Could not exclusively lock port /dev/cu.usbmodemX: [Errno 35] Resource temporarily unavailable")

    monkeypatch.setattr(serial, "Serial", fake_serial)
    with pytest.raises(can.CanInitializationError) as info:
        usb.UsbSerialBus("/dev/cu.usbmodemX")
    assert opened["exclusive"] is True
    assert "already open in another program" in str(info.value) and "another instance" in str(info.value)


def test_two_exclusive_opens_of_one_real_pty_really_conflict():
    """The lock itself, on a pseudo-terminal of this machine: the first exclusive open succeeds, the second is refused."""
    import os
    import pty
    import serial
    master, slave = pty.openpty()
    name = os.ttyname(slave)
    try:
        first = serial.Serial(name, 115200, timeout=0.02, exclusive=True)
    except (serial.SerialException, OSError) as exc:
        pytest.skip(f"this platform cannot lock a pty: {exc}")
    try:
        with pytest.raises((serial.SerialException, OSError)):
            serial.Serial(name, 115200, timeout=0.02, exclusive=True)
    finally:
        first.close()
        os.close(master)
        os.close(slave)

