"""The read-only interlock of tools/usb_readonly_check.py must let reads through and stop everything else."""
import importlib.util
import json
import struct
from pathlib import Path

import can
import pytest
from usb_fakes import UsbRig

from sdr_tuner import defs, protocol as P
from sdr_tuner.client import DriveClient, DriveError

spec = importlib.util.spec_from_file_location("usb_readonly_check", Path(__file__).resolve().parent.parent / "tools" / "usb_readonly_check.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, msg, timeout=None):
        self.sent.append(msg)

    def recv(self, timeout=None):
        return None

    def shutdown(self):
        pass


def as_msg(frame):
    can_id, data = frame
    return can.Message(arbitration_id=can_id, data=data, is_extended_id=False)


def test_read_requests_pass_through():
    rec = Recorder()
    bus = tool.ReadOnlyBus(rec)
    for node in (1, 7, 127):
        bus.send(as_msg(P.read_request(node, 0x2000, 0x03)))
    assert len(rec.sent) == 3


@pytest.mark.parametrize("frame", [
    P.write_request(7, 0x2002, 0x22, "FLOAT32", 0.5),                          # parameter write
    P.write_request(7, 0x2000, 0x01, "UINT16", 1),
    P.command_frame(7, 0x200, 0x20, [(4, "FLOAT32", 10.0)]),                   # speed command
    P.command_frame(7, 0x200, 0x20, [(4, "FLOAT32", 0.0)]),                    # speed 0
    P.command_frame(7, 0x200, 0x04, []),                                       # the Stop command
    P.command_frame(7, 0x200, 0x06, []),                                       # Off
    P.command_frame(7, 0x200, 0x01, []),                                       # Reset
    (0x607, bytes([0x2F, 0, 0x20, 1, 0, 0, 0, 0])),                            # other SDO code
    (0x607, bytes([0x40, 0, 0x20, 1])),                                        # short frame
    (0x000, bytes(8)),                                                         # NMT
])
def test_everything_else_is_blocked(frame):
    rec = Recorder()
    with pytest.raises(RuntimeError, match="BLOCKED"):
        tool.ReadOnlyBus(rec).send(as_msg(frame))
    assert rec.sent == []


def test_a_read_only_client_never_needs_anything_else():
    """A DriveClient that only reads and closes never triggers the guard (close() sends stop only if a speed was set)."""
    rec = Recorder()
    client = DriveClient(tool.ReadOnlyBus(rec), 7, timeout=0.01, retries=0)
    client.start()
    try:
        with pytest.raises(Exception):
            client.read(defs.load_registers(False)[0])     # times out (recorder never answers) but must not be blocked
    finally:
        client.close()
    assert all(bytes(m.data)[0] == P.CMD_READ for m in rec.sent) and rec.sent


# ---- tools/usb_write_check.py -----------------------------------------------------------------
wspec = importlib.util.spec_from_file_location("usb_write_check", Path(__file__).resolve().parent.parent / "tools" / "usb_write_check.py")
wtool = importlib.util.module_from_spec(wspec)
wspec.loader.exec_module(wtool)
BY_ADDR = {r.address: r for r in defs.load_registers(False)}


def test_write_guard_allows_only_the_planned_frames():
    rec = Recorder()
    guard = wtool.Guard(rec)
    reg = BY_ADDR["0x2014:0x03"]
    guard.allow_write(reg, [700.0, 750.0])
    guard.send(as_msg(P.read_request(7, 0x1234, 1)))                              # any read
    guard.send(as_msg(P.write_request(7, reg.index, reg.sub, "FLOAT32", 750.0)))    # planned new value
    guard.send(as_msg(P.write_request(7, reg.index, reg.sub, "FLOAT32", 700.0)))    # original value
    assert len(rec.sent) == 3


@pytest.mark.parametrize("frame", [
    P.write_request(7, 0x2014, 0x03, "FLOAT32", 800.0),                # right address, other value
    P.write_request(7, 0x2014, 0x04, "FLOAT32", 750.0),                # other address
    P.write_request(7, 0x2000, 0x01, "UINT16", 1),                     # motor type: never
    P.command_frame(7, 0x200, 0x20, [(4, "FLOAT32", 5.0)]),            # speed frame while not armed
    P.command_frame(7, 0x200, 0x04, []),                               # Stop while not armed
    (0x607, bytes([0x2F, 0x14, 0x20, 0x03, 0, 0, 0, 0])),              # other SDO code
    (0x000, bytes(8)),                                                 # NMT
    (0x080, bytes(8)),                                                 # sync / emergency range
])
def test_write_guard_blocks_everything_else(frame):
    rec = Recorder()
    guard = wtool.Guard(rec)
    guard.allow_write(BY_ADDR["0x2014:0x03"], [700.0, 750.0])
    with pytest.raises(RuntimeError, match="BLOCKED"):
        guard.send(as_msg(frame))
    assert rec.sent == []


def test_speed_frames_need_arming_and_exact_values():
    rec = Recorder()
    guard = wtool.Guard(rec)
    guard.arm_speed([5.0, 0.0])
    for hz in (5.0, 0.0):
        guard.send(as_msg(P.command_frame(7, 0x200, 0x20, [(4, "FLOAT32", hz)])))
    assert len(rec.sent) == 2
    for hz in (5.5, 50.0, -5.0, 0.5):
        with pytest.raises(RuntimeError, match="BLOCKED"):
            guard.send(as_msg(P.command_frame(7, 0x200, 0x20, [(4, "FLOAT32", hz)])))
    with pytest.raises(RuntimeError, match="BLOCKED"):                 # other bytes set in the frame
        guard.send(as_msg((0x207, bytes([0x20, 1, 0, 0]) + struct.pack("<f", 5.0))))


def test_the_stop_command_needs_arming_and_carries_nothing_else():
    rec = Recorder()
    guard = wtool.Guard(rec)
    guard.arm_speed([5.0, 0.0])
    guard.send(as_msg(P.command_frame(7, 0x200, 0x04, [])))
    assert len(rec.sent) == 1
    with pytest.raises(RuntimeError, match="BLOCKED"):                 # a Stop frame with a payload
        guard.send(as_msg((0x207, bytes([0x04, 0, 0, 0, 1, 0, 0, 0]))))
    for code in (0x06, 0x01, 0x24, 0x10):                              # Off, Reset, ESC rpm, current control: never
        with pytest.raises(RuntimeError, match="BLOCKED"):
            guard.send(as_msg(P.command_frame(7, 0x200, code, [])))
    assert len(rec.sent) == 1


def test_new_values_are_different_and_valid():
    for addr in wtool.TEST_PARAMS:
        reg = BY_ADDR[addr]
        orig = {"BOOL": False, "FLOAT32": 700.0, "UINT32": 0, "UINT16": 1, "INT32": 0}[reg.dtype]
        new = wtool.new_value(reg, orig)
        assert new != orig
        P.encode_value(reg.dtype, new)                                    # fits the type
        if reg.options:
            assert new in reg.options
    assert all(BY_ADDR[a].writable for a in wtool.TEST_PARAMS)


def dry_run(tmp_path, **kw):
    rig = UsbRig(node=7)
    before = dict(rig.sim.params)
    lines = []
    ok = wtool.run(rig.bus(), listen=0.5, backup_dir=tmp_path, out=lines.append, **kw)
    return rig, before, lines, ok


def test_dry_run_against_the_simulator_restores_everything(tmp_path):
    rig, before, lines, ok = dry_run(tmp_path, max_bus_volts=100.0)      # the simulator's DC bus is 24 V
    try:
        text = "\n".join(lines)
        assert ok, text
        assert rig.sim.params == before                                   # every parameter is back to what it was
        assert rig.sim.esc_target_hz == 0.0
        assert ("esc_hz", (5.0,)) in rig.sim.commands_received and rig.sim.commands_received[-1] == ("stop", ())
        assert "PASS - everything restored" in text and "1 stop command(s)" in text
        assert text.count("restored") >= len(wtool.TEST_PARAMS)
        backups = list(tmp_path.glob("node7_*.json"))
        assert len(backups) == 1 and len(json.loads(backups[0].read_text())["params"]) == 243
        assert "6 write(s)" not in text and "12 write(s)" in text         # 6 test writes + 6 restores, nothing else
    finally:
        rig.close()


def test_a_parameter_the_drive_will_not_give_is_left_out_of_the_backup(tmp_path):
    rig = UsbRig(node=7)
    real_sdo = rig.sim._sdo

    def hide_activation_key(data):                  # a write-only parameter: reading it is an error response
        if data[0] == P.CMD_READ and (data[1] | data[2] << 8, data[3]) == (0x20FF, 0x02):
            rig.sim._abort(bytes(data[1:4]), 0)
            return
        return real_sdo(data)
    rig.sim._sdo = hide_activation_key
    lines = []
    try:
        ok = wtool.run(rig.bus(), listen=0.5, backup_dir=tmp_path, out=lines.append, max_bus_volts=100.0)
        text = "\n".join(lines)
        assert ok, text
        assert "not readable, left out of the backup" in text and "0x20FF:0x02" in text
        params = json.loads(next(tmp_path.glob("node7_*.json")).read_text())["params"]
        assert len(params) == 242 and all(p["index"] != "0x20FF" for p in params)
    finally:
        rig.close()


def test_speed_test_is_skipped_unless_the_dc_bus_is_unpowered(tmp_path):
    rig, before, lines, ok = dry_run(tmp_path)                            # default limit 2 V, simulator bus is 24 V
    try:
        text = "\n".join(lines)
        assert ok, text
        assert "skipping the speed test" in text and rig.sim.commands_received == []
        assert rig.sim.params == before
    finally:
        rig.close()


def test_a_failed_write_is_reported_and_everything_is_still_restored(tmp_path, monkeypatch):
    real_write = DriveClient.write

    def flaky(self, reg, value, verify=True):
        if reg.address == "0x2034:0xC2" and value != 0:
            raise DriveError("simulated failure")
        return real_write(self, reg, value, verify)
    monkeypatch.setattr(DriveClient, "write", flaky)
    rig, before, lines, ok = dry_run(tmp_path, max_bus_volts=100.0)
    try:
        assert not ok and "simulated failure" in "\n".join(lines)
        assert rig.sim.params == before
    finally:
        rig.close()


def test_an_error_in_the_speed_test_still_ends_with_everything_restored_and_stopped(tmp_path, monkeypatch):
    def boom(self, hz):
        raise DriveError("simulated speed failure")
    monkeypatch.setattr(DriveClient, "set_speed_hz", boom)
    rig, before, lines, ok = dry_run(tmp_path, max_bus_volts=100.0)
    try:
        assert not ok
        assert rig.sim.params == before and rig.sim.esc_target_hz == 0.0
    finally:
        rig.close()


def test_a_write_that_the_drive_stores_differently_is_flagged_and_restored(tmp_path):
    rig = UsbRig(node=7)
    before = dict(rig.sim.params)
    real_handle = rig.sim._sdo

    test_value = struct.pack("<f", 350.0)           # simulator default 300.0 + the tool's 50.0

    def clamp_test_value(data):                     # the "drive" silently stores 340.0 when 350.0 is written
        if data[0] == P.CMD_WRITE and (data[1] | data[2] << 8, data[3]) == (0x2014, 0x03) and data[4:8] == test_value:
            data = data[:4] + struct.pack("<f", 340.0)
        return real_handle(data)
    rig.sim._sdo = clamp_test_value
    lines = []
    try:
        ok = wtool.run(rig.bus(), listen=0.5, backup_dir=tmp_path, out=lines.append, max_bus_volts=100.0)
        text = "\n".join(lines)
        assert not ok and "write FAILED" in text and "holds 340.0" in text
        assert rig.sim.params == before                                  # the original 300.0 is back
    finally:
        rig.close()


# ---- what the read-only check prints about the telemetry mapping ---------------------------------
def test_the_readonly_tool_says_what_each_mapping_code_means():
    rig = UsbRig(node=7)
    client = DriveClient(rig.bus(), 7, timeout=0.5)
    client.start()
    try:
        client.load_tpdo_mapping()
        lines = tool.describe_mapping(client)
        assert len(lines) == 12
        assert lines[0] == "  TPDO message 0 (COB 0x180 + node), slot 1: 0x1A00:0x01 = 4194307  ->  0x4000:0x03  Fault Bits [bits]"
        assert "0x1A00:0x03 = 4194320  ->  0x4000:0x10  Speed (Electrical Frequency) [Hz]" in lines[2]
        assert lines[3].endswith("= 0  ->  not used")
        rig.sim.params[(0x1A00, 3)] = 4194326                                # someone switched the speed to rps
        rig.sim.params[(0x1A00, 4)] = 0x7FFFFF                               # and something that is not a telemetry parameter
        client.load_tpdo_mapping()
        lines = tool.describe_mapping(client)
        assert "0x1A00:0x03 = 4194326  ->  0x4000:0x16  Work Zone: Speed [RPS]" in lines[2]
        assert lines[3].endswith("NOT A KNOWN TELEMETRY PARAMETER")
    finally:
        client.close()
        rig.close()
