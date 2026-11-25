import pytest

from sdr_tuner import defs, protocol as P


def test_read_request_matches_device_reference():
    # Node 3, "Torque" telemetry: index 0x4001, sub-index 0x02 -> COB 0x600 + node, code 0x40
    can_id, data = P.read_request(3, 0x4001, 0x02)
    assert can_id == 0x603
    assert data == bytes([0x40, 0x01, 0x40, 0x02, 0, 0, 0, 0])


def test_write_request_layout():
    can_id, data = P.write_request(1, 0x2000, 0x02, "FLOAT32", 5.0)
    assert can_id == 0x601
    assert data[:4] == bytes([0x20, 0x00, 0x20, 0x02])
    assert data[4:] == bytes.fromhex("0000a040")     # 5.0f little-endian


def test_write_request_pads_small_types():
    _, data = P.write_request(1, 0x2000, 0x03, "UINT16", 0x1234)
    assert data[4:] == bytes([0x34, 0x12, 0, 0])
    _, data = P.write_request(1, 0x2000, 0x09, "BOOL", True)
    assert data[4:] == bytes([1, 0, 0, 0])


def test_esc_command_frame_matches_device_reference():
    # "Electronic Speed Control": COB 0x200, code 0x20, Speed FLOAT32 starting at byte 4
    esc = defs.load_commands()["esc_hz"]
    can_id, data = P.command_frame(5, esc.cob, esc.code, [(4, "FLOAT32", 12.5)])
    assert can_id == 0x205
    assert data == bytes([0x20, 0, 0, 0]) + bytes.fromhex("00004841")


def test_command_frames_without_and_with_mixed_parameters():
    stop = defs.load_commands()["stop"]
    assert P.command_frame(7, stop.cob, stop.code, []) == (0x207, bytes([0x04, 0, 0, 0, 0, 0, 0, 0]))
    # a test-wave command mixes FLOAT16 (byte 2: fixed point, 2.5 s x 256 = 640 = 0x0280), FLOAT32 (byte 4) and UINT8 (byte 1)
    can_id, data = P.command_frame(1, 0x200, 0xB4, [(2, "FLOAT16", 2.5), (4, "FLOAT32", 12.5), (1, "UINT8", 3)])
    assert (can_id, data) == (0x201, bytes([0xB4, 3, 0x80, 0x02, 0x00, 0x00, 0x48, 0x41]))


@pytest.mark.parametrize("code,size", [(0x43, 4), (0x47, 3), (0x4B, 2), (0x4F, 1), (0x40, None)])
def test_response_size_codes(code, size):
    r = P.parse_response(0x581, bytes([code, 0x01, 0x40, 0x02, 1, 2, 3, 4]))
    assert (r.node, r.index, r.sub, r.size, r.error) == (1, 0x4001, 0x02, size, False)


def test_error_response():
    r = P.parse_response(0x582, bytes([0x80, 0x00, 0x20, 0x77, 0, 0, 2, 6]))
    assert r.error and r.node == 2 and r.index == 0x2000 and r.sub == 0x77


def test_parse_response_ignores_other_frames():
    assert P.parse_response(0x601, bytes(8)) is None                                     # request, not response
    assert P.parse_response(0x181, bytes(8)) is None                                     # telemetry
    assert P.parse_response(0x581, bytes([0x60, 0, 0x20, 1, 0, 0, 0, 0])) is None        # write ack / unknown code


def test_value_roundtrip():
    for dtype, value in [("UINT16", 65535), ("INT32", -123456), ("UINT32", 4294967295), ("UINT8", 255), ("INT8", -128),
                         ("FLOAT32", 0.5), ("BOOL", True), ("FLOAT16", 24.0)]:
        assert P.decode_value(dtype, P.encode_value(dtype, value)) == value


def test_float16_is_fixed_point_times_256_as_measured_on_the_real_drive():
    """The reference calls the type FLOAT16, but the drive holds it as a 16-bit fixed-point number, the value x 256
    (2026-09-25): its DC-bus 'FLOAT16' register read raw 0x180A while the FLOAT32 twin said 24.03 V, and the test
    commands' Period ran at raw/256 s (raw 256 -> a 1.000 s sine, raw 64 -> 0.25 s; a half-float 1.0, 0x3C00,
    ran for 60 s - which is why the Bode sweep and the step tests fitted nothing until this was found)."""
    assert P.decode_value("FLOAT16", bytes.fromhex("0a18")) == pytest.approx(24.039, abs=0.001)   # 0x180A / 256
    assert P.decode_value("FLOAT16", bytes.fromhex("003c")) == 60.0                                # NOT 1.0
    assert P.encode_value("FLOAT16", 1.0) == bytes.fromhex("0001")                                 # raw 256
    assert P.encode_value("FLOAT16", 0.25) == bytes.fromhex("4000")                                # raw 64
    assert P.encode_value("FLOAT16", 0.1) == bytes.fromhex("1a00") and P.decode_value("FLOAT16", bytes.fromhex("1a00")) == 26 / 256
    assert P.encode_value("FLOAT16", -1.5) == bytes.fromhex("80fe")                                # signed, two's complement
    assert P.wire_value("FLOAT16", 6.53) == 6.53125 and P.wire_value("FLOAT16", 0.001) == 0.0     # the 1/256 step
    for bad in (128.0, -128.01, 1e6):
        with pytest.raises(ValueError):
            P.encode_value("FLOAT16", bad)


def test_out_of_range_values_are_rejected():
    for dtype, value in [("UINT16", 70000), ("UINT16", -1), ("INT32", 2 ** 31), ("UINT8", 256), ("INT8", 128), ("FLOAT32", 1e39),
                         ("FLOAT32", float("nan")), ("FLOAT32", float("inf")), ("UINT32", 1.5)]:
        with pytest.raises(ValueError):
            P.encode_value(dtype, value)


def test_node_id_range():
    for bad in (0, 128, -1):
        with pytest.raises(ValueError):
            P.read_request(bad, 0x2000, 1)
    assert P.read_request(127, 0x2000, 1)[0] == 0x600 + 127


def test_split_can_id():
    assert P.split_can_id(0x1FF) == (0x180, 0x7F)
    assert P.split_can_id(0x581) == (0x580, 1)
