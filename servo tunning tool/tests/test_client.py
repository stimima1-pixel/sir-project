import threading
import time
import uuid

import can
import pytest

from sdr_tuner import defs, protocol as P
from sdr_tuner.client import DriveClient, DriveError, DriveTimeout
from sdr_tuner.simulator import SimulatedDrive

REGS = defs.load_registers(False)                      # configuration parameters (the read-only telemetry ones: TELEMETRY)
TELEMETRY = defs.load_registers(True)
BY_ADDR = {r.address: r for r in REGS + TELEMETRY}
NODE = 3


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def make_rig(ack_writes=False, client_node=NODE, sim_kw=None, **client_kw):
    channel = f"test-{uuid.uuid4().hex}"
    sim = SimulatedDrive(channel, node_id=NODE, tau=0.05, ack_writes=ack_writes, **(sim_kw or {}))
    client = DriveClient(can.Bus(interface="virtual", channel=channel), client_node,
                         timeout=client_kw.pop("timeout", 0.3), esc_period=0.05, **client_kw)
    client.start()
    return sim, client


@pytest.fixture
def rig():
    sim, client = make_rig()
    yield sim, client
    client.close()
    sim.close()


def sample_value(reg):
    if reg.options:
        return list(reg.options)[-1]
    if reg.dtype == "BOOL":
        return True
    if reg.dtype == "UINT16":
        return 3
    if reg.dtype == "UINT32":
        return 12345
    if reg.dtype == "INT32":
        return -321
    if reg.dtype == "INT16":
        return -3
    v = 1.5
    if reg.min is not None:
        v = max(v, reg.min)
    if reg.max is not None:
        v = min(v, reg.max)
    return v


def test_read_every_register(rig):
    sim, client = rig
    for reg in REGS:
        value = client.read(reg)
        assert value == P.decode_value(reg.dtype, P.encode_value(reg.dtype, sim.params[reg.key])), reg.name


def test_read_every_telemetry_parameter_by_request(rig):
    _, client = rig
    for reg in TELEMETRY:                                   # each one answers with its own size
        client.read(reg)


@pytest.mark.parametrize("ack_writes", [False, True])
def test_write_every_register_and_verify(ack_writes):
    sim, client = make_rig(ack_writes=ack_writes, write_settle=0.01)      # 242 writes: keep the settle wait short
    try:
        for reg in REGS:
            value = sample_value(reg)
            stored = client.write(reg, value)
            assert stored == P.decode_value(reg.dtype, P.encode_value(reg.dtype, value)), reg.name
            assert sim.params[reg.key] == stored
    finally:
        client.close()
        sim.close()


def test_unknown_address_gives_error_response(rig):
    _, client = rig
    with pytest.raises(DriveError, match="error"):
        client.read_raw(0x1234, 0x05)


def test_write_to_read_only_telemetry_is_rejected(rig):
    sim, client = rig
    speed = defs.Register("t", "Speed", 0x4000, 0x10, "FLOAT32")
    with pytest.raises(DriveError, match="rejected"):
        client.write(speed, 99.0)
    assert sim.speed_hz == 0.0


def test_sdo_read_of_telemetry(rig):
    sim, client = rig
    sim.fault_bits = 0x0104
    r = client.read_raw(0x4000, 0x03)
    assert r.value("UINT16") == 0x0104 and r.size == 2
    assert client.read(BY_ADDR["0x4000:0x03"]) == 0x0104


def test_telemetry_is_read_only_in_the_client(rig):
    _, client = rig
    with pytest.raises(DriveError, match="read-only"):
        client.write(BY_ADDR["0x4000:0x10"], 1.0)


def test_timeout_when_node_does_not_answer():
    sim, client = make_rig(client_node=NODE + 1, timeout=0.05, retries=0)
    try:
        t0 = time.monotonic()
        with pytest.raises(DriveTimeout):
            client.read(BY_ADDR["0x2000:0x03"])
        assert time.monotonic() - t0 < 1.0
    finally:
        client.close()
        sim.close()


def test_bad_values_are_never_sent(rig):
    sim, client = rig
    reg = BY_ADDR["0x2000:0x03"]        # UINT16 poles
    before = sim.params[reg.key]
    with pytest.raises(ValueError):
        client.write(reg, 70000)
    with pytest.raises(ValueError):
        client.write(BY_ADDR["0x2000:0x02"], float("nan"))
    time.sleep(0.05)
    assert sim.params[reg.key] == before


def test_identify_reads_the_pole_count(rig):
    _, client = rig
    assert client.poles is None
    assert client.identify() == 14 and client.poles == 14


def test_speed_command_reaches_the_motor_and_telemetry(rig):
    sim, client = rig
    client.set_speed_hz(20.0)
    assert wait_for(lambda: abs(sim.speed_hz - 20.0) < 0.5)
    assert wait_for(lambda: abs(client.telemetry_snapshot().get("speed_hz", 0) - 20.0) < 1.0)
    assert client.commanded == ("esc_hz", 20.0)
    snap = client.telemetry_snapshot()
    assert snap["vbus"] == pytest.approx(24.0, abs=0.2)
    # what the drive broadcasts by default (rev L): Phase B, |I| and the electrical angle are NOT in it
    assert set(snap) == {"fault_bits", "vbus", "speed_hz", "op_mode", "comm_mode", "wz_count", "ia", "wz_modulo"}
    assert wait_for(lambda: client.telemetry_snapshot()["wz_count"] > 0)
    assert client.telemetry_snapshot()["op_mode"] == 6                  # Electronic Speed Control


def test_speed_command_is_kept_alive(rig):
    sim, client = rig
    client.set_speed_hz(5.0)
    n = len(sim.commands_received)
    time.sleep(0.4)
    assert len(sim.commands_received) - n >= 5        # ~ every 50 ms
    assert all(c == ("esc_hz", (5.0,)) for c in sim.commands_received)


def test_stop_sends_the_stop_command_and_ends_keepalive(rig):
    sim, client = rig
    client.set_speed_hz(20.0)
    assert wait_for(lambda: sim.speed_hz > 15)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: abs(sim.speed_hz) < 0.5)
    assert sim.commands_received[-1] == ("stop", ())
    n = len(sim.commands_received)
    time.sleep(0.25)
    assert len(sim.commands_received) == n            # no more keep-alive frames


def test_off_switches_the_power_stage_off_and_ends_keepalive(rig):
    sim, client = rig
    client.set_speed_hz(20.0)
    assert wait_for(lambda: sim.speed_hz > 15)
    client.off()
    assert client.commanded is None
    assert wait_for(lambda: sim.off and client.telemetry_snapshot().get("op_mode") == 1)      # Off
    assert sim.commands_received[-1] == ("off", ())
    n = len(sim.commands_received)
    time.sleep(0.25)
    assert len(sim.commands_received) == n


def test_speed_in_rpm_is_converted_by_the_drive(rig):
    sim, client = rig
    client.send_command("esc_rpm", 60.0)                # 14 poles: 7 pole pairs -> 60 rpm = 7 Hz electrical
    assert wait_for(lambda: sim.esc_target_hz == pytest.approx(7.0))
    assert sim.commands_received[-1] == ("esc_rpm", (60.0,))


def test_rpm_speed_command_is_kept_alive_and_stopped(rig):
    sim, client = rig
    client.identify()
    client.set_speed_rpm(120.0)
    assert client.commanded == ("esc_rpm", 120.0)
    assert wait_for(lambda: abs(sim.speed_hz - 14.0) < 0.5)                   # 120 rpm with 7 pole pairs = 14 Hz
    n = len(sim.commands_received)
    time.sleep(0.4)
    assert len(sim.commands_received) - n >= 5                                # repeated every 50 ms
    assert all(c == ("esc_rpm", (120.0,)) for c in sim.commands_received)
    assert wait_for(lambda: abs(client.telemetry_snapshot().get("speed_hz", 0) - 14.0) < 0.5)      # what the drive reports back
    client.set_speed_rpm(-60.0)                                               # a new target replaces the old one
    assert client.commanded == ("esc_rpm", -60.0)
    assert wait_for(lambda: sim.esc_target_hz == pytest.approx(-7.0))
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim.commands_received[-1] == ("stop", ()))
    n = len(sim.commands_received)
    time.sleep(0.25)
    assert len(sim.commands_received) == n


def test_switching_between_hz_and_rpm_commands_never_mixes_them(rig):
    sim, client = rig
    client.set_speed_hz(10.0)
    client.set_speed_rpm(60.0)
    assert wait_for(lambda: sim.commands_received[-1:] == [("esc_rpm", (60.0,))])     # the drive has both frames by now
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(c == ("esc_rpm", (60.0,)) for c in repeated)      # only the latest command is repeated
    with pytest.raises(ValueError):
        client.set_speed_rpm(float("nan"))
    assert client.commanded == ("esc_rpm", 60.0)                              # a refused value changes nothing


def test_command_argument_errors_are_clear(rig):
    sim, client = rig
    with pytest.raises(DriveError, match="needs 1 value"):
        client.send_command("esc_hz")
    with pytest.raises(DriveError, match="needs 0 value"):
        client.send_command("stop", 1.0)
    with pytest.raises(DriveError, match="FLOAT24"):
        client.send_command("servo_linear_position_with_velocity_feed_forward", 1, 2.0)
    with pytest.raises(ValueError):
        client.send_command("esc_hz", float("nan"))
    assert sim.commands_received == []


def test_a_faulted_drive_ignores_commands_until_reset(rig):
    sim, client = rig
    sim.fault_bits = 0x0100
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 2)                 # Fault
    client.set_speed_hz(10.0)
    assert wait_for(lambda: sim.ignored_commands)
    assert sim.commands_received == [] and sim.esc_target_hz == 0.0
    client.reset()
    assert wait_for(lambda: client.telemetry_snapshot().get("fault_bits") == 0)
    assert sim.commands_received == [("reset", ())]
    time.sleep(0.3)
    assert client.commanded is None and sim.esc_target_hz == 0.0        # the old speed command is not sent again


def test_a_drive_without_activation_key_cannot_be_reset():
    sim, client = make_rig(sim_kw={"activated": False})
    try:
        assert wait_for(lambda: client.telemetry_snapshot().get("fault_bits") == 0x8000)
        snap = client.telemetry_snapshot()
        assert snap["op_mode"] == 2 and client.fault_bit_names[15] == "Activation Key is Missing"
        client.reset()
        client.set_speed_hz(5.0)
        assert wait_for(lambda: sim.ignored_commands)
        time.sleep(0.15)
        assert client.telemetry_snapshot()["fault_bits"] == 0x8000 and sim.esc_target_hz == 0.0
    finally:
        client.close()
        sim.close()


def test_closing_the_client_stops_a_running_motor():
    sim, client = make_rig()
    try:
        client.set_speed_hz(30.0)
        assert wait_for(lambda: sim.speed_hz > 20)
        client.close()
        assert wait_for(lambda: sim.esc_target_hz == 0.0)
        assert sim.commands_received[-1] == ("stop", ())
        assert wait_for(lambda: abs(sim.speed_hz) < 1.0)
    finally:
        client.close()
        sim.close()


def test_hold_position_commands_servo_control_at_the_given_count_and_is_kept_alive(rig):
    sim, client = rig
    client.hold_position(5000)
    assert client.commanded == ("servo", 5000.0)
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 8)           # Servo Control
    assert wait_for(lambda: client.telemetry_snapshot().get("wz_count") == 5000)
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(c == ("servo", (5000.0,)) for c in repeated)               # repeated like a speed command
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") != 8)
    assert sim._servo_target is None


def test_move_linear_commands_the_linear_interpolation_command_and_is_kept_alive(rig):
    sim, client = rig
    client.move_linear(5000, 800, 3)
    assert client.commanded == ("servo_linear_interpolation", (5000.0, 800.0, 3))
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 8)             # Servo Control, same as plain servo
    assert wait_for(lambda: client.telemetry_snapshot().get("wz_count") == 5000)
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(c == ("servo_linear_interpolation", (5000.0, 800.0, 3)) for c in repeated)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim._servo_target is None)


def test_hold_modulo_position_commands_servo_modulo_and_is_kept_alive(rig):
    sim, client = rig
    client.hold_modulo_position(5000, direction=2)
    assert client.commanded == ("servo_modulo", (5000.0, 2.0))
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 8)             # Servo Control, same as plain servo
    assert wait_for(lambda: client.telemetry_snapshot().get("wz_count") == 5000)
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(c == ("servo_modulo", (5000, 2)) for c in repeated)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim._servo_target is None)


def test_hold_modulo_position_defaults_to_shortest_cyclic_route(rig):
    sim, client = rig
    client.hold_modulo_position(1000)
    assert client.commanded == ("servo_modulo", (1000.0, 3.0))                            # 3 = Shortest Cyclic Route


def test_hold_turns_and_modulo_position_commands_servo_turns_and_modulo_and_is_kept_alive(rig):
    sim, client = rig
    client.hold_turns_and_modulo_position(1500, -4)
    assert client.commanded == ("servo_turns_and_modulo", (1500.0, -4.0))
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 8)
    assert wait_for(lambda: client.telemetry_snapshot().get("wz_count") == 1500)
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(c == ("servo_turns_and_modulo", (1500, -4)) for c in repeated)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim._servo_target is None)


def test_set_direct_field_rotation_commands_the_rotating_field_and_is_kept_alive(rig):
    sim, client = rig
    client.set_direct_field_rotation(0.5, 12.0)
    key, value = client.commanded
    assert key == "direct_field_control_rotation" and value == pytest.approx((0.5, 12.0), abs=0.01)
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 11)   # Direct Field Control - Rotation
    assert wait_for(lambda: sim.esc_target_hz == pytest.approx(12.0))
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(k == "direct_field_control_rotation" for k, _v in repeated)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim._direct_field_mode is None)


def test_set_direct_field_position_holds_a_static_field_and_is_kept_alive(rig):
    sim, client = rig
    client.set_direct_field_position(0.5, 1.25)
    key, value = client.commanded
    assert key == "direct_field_control_electrical_position" and value == pytest.approx((0.5, 1.25), abs=0.01)
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 12)   # Direct Field Control - Electrical Position
    assert wait_for(lambda: sim.elec_pos == pytest.approx(1.25, abs=0.01))
    assert wait_for(lambda: sim.esc_target_hz == 0.0)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim._direct_field_mode is None)


def test_engage_virtual_brake_commands_it_with_no_parameters_and_is_kept_alive(rig):
    sim, client = rig
    client.engage_virtual_brake()
    assert client.commanded == ("virtual_brake", ())
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 10)   # Virtual Brake
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(c == ("virtual_brake", ()) for c in repeated)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim._virtual_brake is False)


def test_kickstart_commands_the_speed_and_is_kept_alive(rig):
    sim, client = rig
    client.kickstart(15.0)
    assert client.commanded == ("kickstart", 15.0)
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 9)   # Kickstart
    assert wait_for(lambda: sim.esc_target_hz == pytest.approx(15.0))
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(c == ("kickstart", (15.0,)) for c in repeated)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim._kickstart_mode is False)


def test_generate_power_commands_the_voltage_and_is_kept_alive(rig):
    sim, client = rig
    client.generate_power(48.0)
    assert client.commanded == ("power_generation", 48.0)
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 14)   # Power Generation
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(c == ("power_generation", (48.0,)) for c in repeated)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim._power_generation is False)


def test_brushed_open_loop_commands_both_channels_and_is_kept_alive(rig):
    sim, client = rig
    client.brushed_open_loop(5.0, -3.0)
    key, value = client.commanded
    assert key == "brushed_open_loop_control_1_2_motors" and value == pytest.approx((5.0, -3.0), abs=0.01)
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 19)   # Brushed Motor or Solenoid Control
    n = len(sim.commands_received)
    time.sleep(0.3)
    repeated = sim.commands_received[n:]
    assert repeated and all(k == "brushed_open_loop_control_1_2_motors" for k, _v in repeated)
    client.stop()
    assert client.commanded is None
    assert wait_for(lambda: sim._brushed_mode is False)


def test_brushed_open_loop_channel_2_defaults_to_zero(rig):
    sim, client = rig
    client.brushed_open_loop(2.5)
    key, value = client.commanded
    assert key == "brushed_open_loop_control_1_2_motors" and value == pytest.approx((2.5, 0.0), abs=0.01)


def test_set_electromagnetic_brake_is_a_one_shot_command_not_repeated(rig):
    sim, client = rig
    client.set_electromagnetic_brake(True)
    assert wait_for(lambda: sim.brake_engaged is True)
    assert client.commanded is None                          # discrete: does not touch the continuous repeat slot
    n = len(sim.commands_received)
    time.sleep(0.3)
    assert len(sim.commands_received) == n                   # nothing repeated
    client.set_electromagnetic_brake(False)
    assert wait_for(lambda: sim.brake_engaged is False)


def test_set_gpio_output_is_a_one_shot_command(rig):
    sim, client = rig
    client.set_gpio_output(True)
    assert wait_for(lambda: sim.gpio_output is True)
    assert client.commanded is None
    client.set_gpio_output(False)
    assert wait_for(lambda: sim.gpio_output is False)


def test_set_gpio_pwm_is_a_one_shot_command(rig):
    sim, client = rig
    client.set_gpio_pwm(0.4)
    assert wait_for(lambda: sim.gpio_pwm_duty == pytest.approx(0.4))
    assert client.commanded is None


def test_end_repeat_stops_repeating_without_sending_anything(rig):
    sim, client = rig
    client.set_speed_hz(10.0)
    assert wait_for(lambda: sim.esc_target_hz == pytest.approx(10.0))
    n = len(sim.commands_received)
    client.end_repeat()
    assert client.commanded is None
    time.sleep(0.3)
    assert len(sim.commands_received) == n                    # nothing new was sent - not even a stop
    assert sim.esc_target_hz == pytest.approx(10.0)            # the drive was not told to stop: it keeps going until it times out


def test_hold_position_replaces_a_running_speed_command_and_off_ends_it():
    sim, client = make_rig()
    try:
        client.set_speed_rpm(100)
        assert client.commanded[0] == "esc_rpm"
        client.hold_position(1234)
        assert client.commanded == ("servo", 1234.0)                                   # the hold took over the repeat slot
        assert wait_for(lambda: sim._servo_target == pytest.approx(1234.0))
        client.off()
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 1) and client.commanded is None    # Off
        assert sim._servo_target is None
    finally:
        client.close()
        sim.close()


def test_set_current_and_set_torque_are_kept_alive_and_mutually_exclusive(rig):
    sim, client = rig
    client.set_current(2.5)
    assert client.commanded == ("current_control_field_oriented_control_foc", 2.5)
    assert wait_for(lambda: sim._foc_current == pytest.approx(2.5))
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 4)            # Field Oriented Control (FOC)
    n = len(sim.commands_received)
    time.sleep(0.3)
    assert len(sim.commands_received) - n >= 5                                          # repeated like a speed command
    client.set_torque(0.8)
    assert client.commanded == ("electronic_torque_control_etc", 0.8)
    assert wait_for(lambda: sim._foc_torque == pytest.approx(0.8) and sim._foc_current is None)   # one replaces the other
    client.stop()
    assert wait_for(lambda: sim._foc_torque is None and client.telemetry_snapshot().get("op_mode") != 4)


def test_starting_a_speed_or_position_command_clears_a_running_current_or_torque_command(rig):
    sim, client = rig
    client.set_current(1.0)
    assert wait_for(lambda: sim._foc_current == pytest.approx(1.0))
    client.set_speed_hz(10.0)
    assert wait_for(lambda: sim._foc_current is None and sim.esc_target_hz == pytest.approx(10.0))
    client.set_torque(0.5)
    assert wait_for(lambda: sim.esc_target_hz == 0.0 and sim._foc_torque == pytest.approx(0.5))
    client.hold_position(999)
    assert wait_for(lambda: sim._foc_torque is None and sim._servo_target == pytest.approx(999.0))


def test_reset_work_zone_stops_the_motor_and_zeroes_the_count(rig):
    sim, client = rig
    client.set_speed_hz(20.0)
    assert wait_for(lambda: sim.speed_hz > 15)
    sim.wz_count = 12345.0
    client.reset_work_zone()
    assert client.commanded is None
    assert wait_for(lambda: sim.commands_received[-2:] == [("stop", ()), ("reset_work_zone", ())])
    assert wait_for(lambda: sim.wz_count != 12345.0)          # the reference point changed: no longer the old count
    assert wait_for(lambda: abs(sim.speed_hz) < 0.5)          # and the motor was told to stop, coasting down


def test_reset_work_zone_ends_a_running_position_hold_so_it_cannot_snap_to_the_old_target(rig):
    sim, client = rig
    client.hold_position(50000)
    assert wait_for(lambda: sim._servo_target == pytest.approx(50000.0))
    client.reset_work_zone()
    assert wait_for(lambda: sim._servo_target is None and sim.wz_count == 0.0)
    time.sleep(0.3)
    assert sim._servo_target is None and sim.wz_count == 0.0                            # nothing repeats the old target back


def test_fault_bits_and_history(rig):
    sim, client = rig
    sim.fault_bits = 0b101
    assert wait_for(lambda: client.telemetry_snapshot().get("fault_bits") == 5)
    time.sleep(0.3)
    t, v = client.history_snapshot("vbus", seconds=5)
    assert len(t) >= 5 and (t[1:] > t[:-1]).all()
    assert client.telemetry_age() < 0.5


def test_frame_listener_sees_tx_and_rx(rig):
    _, client = rig
    seen = []
    client.frame_listeners.append(lambda d, t, cid, data: seen.append((d, cid)))
    client.read(BY_ADDR["0x2000:0x03"])
    assert ("tx", 0x600 + NODE) in seen and ("rx", 0x580 + NODE) in seen


def test_concurrent_requests_do_not_mix(rig):
    sim, client = rig
    errors = []

    def worker(regs):
        try:
            for reg in regs * 3:
                assert client.read(reg) == P.decode_value(reg.dtype, P.encode_value(reg.dtype, sim.params[reg.key]))
        except Exception as exc:    # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(REGS[i::4][:10],)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_load_tpdo_mapping_reads_the_drives_default_layout(rig):
    _, client = rig
    tpdos = client.load_tpdo_mapping()
    assert [(t.cob, [f.key for f in t.fields]) for t in tpdos] == [
        (0x180, ["fault_bits", "vbus", "speed_hz"]), (0x280, ["op_mode", "comm_mode", "wz_count"]), (0x380, ["ia", "wz_modulo"])]
    assert [f.offset for f in tpdos[1].fields] == [0, 2, 4]


def test_the_mapping_codes_the_drive_holds_are_recorded(rig):
    _, client = rig
    assert not client.mapping_from_drive and client.mapping_codes == {}
    client.load_tpdo_mapping()
    assert client.mapping_from_drive
    assert client.mapping_codes[(0x1A00, 1)] == 4194307                # Fault Bits: 0x4000:0x03
    assert client.mapping_codes[(0x1A00, 3)] == 4194320                # Speed (Electrical Frequency), Hz: 0x4000:0x10
    assert client.mapping_codes[(0x1A02, 1)] == 5243141                # Phase A: 0x5001:0x05
    assert client.mapping_codes[(0x1A00, 4)] == 0 and len(client.mapping_codes) == 12


def test_a_broadcast_is_not_decoded_until_the_layout_is_known(rig):
    sim, client = rig
    client.decode_telemetry = False
    time.sleep(0.3)
    assert client.telemetry_snapshot() == {} and client.telemetry_age() is None       # frames arrived, nothing was decoded
    assert client.detect_nodes(wait=0.1) == [NODE]                                     # ... but the node is still noticed
    client.decode_telemetry = True
    assert wait_for(lambda: "vbus" in client.telemetry_snapshot())


def test_a_broadcast_frame_is_read_with_the_layout_of_its_own_drive():
    sim, client = make_rig()
    try:
        for slot, code in enumerate([4194326, 5243140, 0, 0], 1):                      # speed in RPS, DC bus - and no Fault Bits
            sim.params[(0x1A00, slot)] = code
        client.decode_telemetry = False
        time.sleep(0.2)
        client.load_tpdo_mapping()
        client.decode_telemetry = True
        sim.speed_hz = 14.0                                                            # 14 poles: 7 pole pairs -> 2 rev/s
        assert wait_for(lambda: client.telemetry_snapshot().get("wz_speed") is not None and "vbus" in client.telemetry_snapshot())
        assert "speed_hz" not in client.telemetry_snapshot() and "fault_bits" not in client.telemetry_snapshot()
    finally:
        client.close()
        sim.close()


def test_writing_a_mapping_register_reads_the_layout_again(rig):
    sim, client = rig
    client.load_tpdo_mapping()
    changes = []
    client.mapping_listeners.append(lambda: changes.append([f.key for f in client.tpdos[0].fields]))
    client.write(BY_ADDR["0x1A00:0x03"], 4194326)                       # speed in Hz -> speed in RPS, the way a user would
    assert changes == [["fault_bits", "vbus", "wz_speed"]]              # the new layout was read straight away
    assert client.decode_telemetry and client.mapping_from_drive
    client.set_speed_hz(14.0)
    assert wait_for(lambda: abs(sim.speed_hz - 14.0) < 0.2)
    assert wait_for(lambda: abs(client.telemetry_snapshot().get("wz_speed", 0) - 2.0) < 0.1)     # 14 Hz / 7 pole pairs
    seen = len(client.history_snapshot("speed_hz")[0])
    time.sleep(0.3)
    assert len(client.history_snapshot("speed_hz")[0]) == seen          # the old value is no longer in the broadcast
    client.write(BY_ADDR["0x1A00:0x03"], 4194320)                       # and back
    assert [f.key for f in client.tpdos[0].fields] == ["fault_bits", "vbus", "speed_hz"]
    assert wait_for(lambda: len(client.history_snapshot("speed_hz")[0]) > seen)


def test_a_writes_to_a_mapping_register_that_cannot_be_checked_says_so(rig, monkeypatch):
    sim, client = rig
    client.load_tpdo_mapping()
    changes = []
    client.mapping_listeners.append(lambda: changes.append(client.mapping_from_drive))

    def silent():
        raise DriveError("no answer")
    monkeypatch.setattr(client, "load_tpdo_mapping", silent)
    client.write(BY_ADDR["0x1A00:0x03"], 4194326)
    assert changes == [False] and not client.mapping_from_drive         # the layout is no longer known
    assert client.decode_telemetry                                       # decoding carries on (with the last known layout)


def test_a_write_to_something_else_leaves_the_layout_alone(rig):
    _, client = rig
    changes = []
    client.mapping_listeners.append(lambda: changes.append(1))
    client.write(BY_ADDR["0x2000:0x02"], 3.0)
    assert changes == [] and not client.mapping_from_drive


def test_a_changed_telemetry_mapping_is_followed(rig):
    sim, client = rig
    # message 0 now carries: Phase B (FLOAT32, 4 bytes), Fault Bits (UINT16), DC bus (FLOAT16)
    for slot, (index, sub) in enumerate([(0x5001, 0x06), (0x4000, 0x03), (0x5001, 0x04)], 1):
        client.write(BY_ADDR[f"0x1A00:0x{slot:02X}"], (index << 8) | sub)
    client.write(BY_ADDR["0x1A00:0x04"], 0)
    tpdos = client.load_tpdo_mapping()
    assert [(f.key, f.offset) for f in tpdos[0].fields] == [("ib", 0), ("fault_bits", 4), ("vbus", 6)]
    sim.fault_bits = 0x0002
    assert wait_for(lambda: "ib" in client.telemetry_snapshot() and client.telemetry_snapshot().get("fault_bits") == 2)
    assert client.telemetry_snapshot()["vbus"] == pytest.approx(24.0, abs=0.2)


def test_a_slot_the_client_cannot_place_ends_the_message():
    class OneFieldTooMany:
        def __init__(self, values):
            self.values = values

    sim, client = make_rig()
    try:
        client.write(BY_ADDR["0x1A00:0x01"], (0x4000 << 8) | 0x10)      # Speed FLOAT32
        client.write(BY_ADDR["0x1A00:0x02"], (0x4000 << 8) | 0x10)      # Speed FLOAT32 again (8 bytes used)
        client.write(BY_ADDR["0x1A00:0x03"], (0x4000 << 8) | 0x03)      # UINT16: no room left
        assert [f.key for f in client.load_tpdo_mapping()[0].fields] == ["speed_hz", "speed_hz"]
    finally:
        client.close()
        sim.close()


def test_a_slot_that_does_not_fit_is_recorded_not_just_silently_dropped():
    """2026-09-24, found while checking a real drive: the client already correctly refused to place a slot that
    does not fit (test above), but did so completely silently - a configured field would just never appear in
    telemetry_snapshot(), with nothing to say why. `tpdo_overflow` (and session.py's own connect-time summary)
    exist so this is visible instead. Real-world case that triggered this: Fault Bits (2B) + DC-bus FLOAT32 (4B,
    from an earlier remap) + Work Zone Speed FLOAT32 (4B) = 10 bytes, over the 8-byte CAN limit."""
    sim, client = make_rig()
    try:
        assert client.tpdo_overflow == []                                # nothing yet - load_tpdo_mapping() not called
        client.write(BY_ADDR["0x1A00:0x01"], (0x4000 << 8) | 0x03)       # Fault Bits, UINT16 (2 bytes)
        client.write(BY_ADDR["0x1A00:0x02"], (0x4000 << 8) | 0x10)       # Speed, FLOAT32 (4 bytes) -> 6 used
        client.write(BY_ADDR["0x1A00:0x03"], (0x4000 << 8) | 0x11)       # Speed (Electrical Frequency), FLOAT32:
                                                                            # needs 4 more, only 2 left - overflows
        client.load_tpdo_mapping()
        assert [f.key for f in client.tpdos[0].fields] == ["fault_bits", "speed_hz"]      # unchanged behaviour
        assert len(client.tpdo_overflow) == 1
        cob, slot, reason = client.tpdo_overflow[0]
        assert cob == 0x180 and slot == 3
        assert "needs 4 more byte(s)" in reason and "2 left" in reason
    finally:
        client.close()
        sim.close()


def test_the_default_layout_has_no_overflow(rig):
    """The reference's own default telemetry layout (the one this rig starts with, before any remap) must not
    overflow - a sanity check that the new tracking does not false-positive on the ordinary case."""
    _, client = rig
    client.load_tpdo_mapping()
    assert client.tpdo_overflow == []


def test_a_slot_after_an_unrecognised_mapping_code_is_recorded_too():
    """The OTHER way a slot can fail to place (test_a_slot_the_client_cannot_place_ends_the_message's own sibling
    case, for an unrecognised code rather than an overflow) is recorded the same way, and so is anything configured
    after it in the same message."""
    sim, client = make_rig()
    try:
        client.write(BY_ADDR["0x1A00:0x01"], 999999)                     # not a real telemetry parameter's code
        client.write(BY_ADDR["0x1A00:0x02"], (0x4000 << 8) | 0x03)       # Fault Bits - configured, but after the bad slot
        client.write(BY_ADDR["0x1A00:0x03"], 0)                          # unused (clearing the rig's own default mapping)
        client.write(BY_ADDR["0x1A00:0x04"], 0)
        client.load_tpdo_mapping()
        assert client.tpdos[0].fields == ()
        assert len(client.tpdo_overflow) == 2
        assert "not a recognised telemetry parameter" in client.tpdo_overflow[0][2]
        assert "an earlier slot" in client.tpdo_overflow[1][2]
    finally:
        client.close()
        sim.close()


def test_telemetry_frequency_sets_the_broadcast_rate():
    sim, client = make_rig()
    try:
        counts = []
        client.frame_listeners.append(lambda d, t, cid, data: counts.append(t) if cid == 0x180 + NODE else None)
        client.write(BY_ADDR["0x3000:0x05"], 30.0)                      # 30 Hz for the 3 messages together: 10 Hz each
        counts.clear()
        time.sleep(1.0)
        assert 5 <= len(counts) <= 14, len(counts)
    finally:
        client.close()
        sim.close()


def test_link_failure_is_reported():
    class DeadBus:
        def recv(self, timeout=None):
            raise can.CanOperationError("adapter unplugged")

        def shutdown(self):
            pass

    client = DriveClient(DeadBus(), 1)
    seen = []
    client.link_error_listeners.append(seen.append)
    client.start()
    try:
        assert wait_for(lambda: seen)
        assert "unplugged" in str(seen[0])
    finally:
        client.close()


# ---- the drive's automatic configuration (tuning) -------------------------------------------------
def autoconfig_rig(seconds=1.0, heartbeat=0.6, **sim_kw):
    sim, client = make_rig(sim_kw={"autoconfig_seconds": seconds, **sim_kw})
    sim.params[(0x3000, 0x04)] = heartbeat                                   # the drive gives up after this long without a message
    return sim, client


def test_the_automatic_configuration_measures_the_motor_and_saves_it():
    sim, client = autoconfig_rig(seconds=0.6, heartbeat=3.5)
    try:
        before = dict(sim.params)
        client.send_command("autoconfiguration_brushless_motor", 6.5, 8)
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 3)                 # Operation Mode: Autoconfiguration
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 0, timeout=4)      # ... and back to Idle when done
        assert sim.params[(0x2000, 0x02)] == 6.5 and sim.params[(0x2000, 0x03)] == 8              # the inputs are stored
        assert sim.params[(0x2000, 0x04)] != before[(0x2000, 0x04)] and sim.params[(0x2000, 0x08)] == pytest.approx(3.1e-5)
        assert sim.params[(0x2002, 0x22)] == pytest.approx(0.058) and sim.flash_saved and sim.autoconfig_runs == 1
    finally:
        client.close()
        sim.close()


def test_a_repeated_command_does_not_start_the_procedure_again():
    sim, client = autoconfig_rig(seconds=0.6, heartbeat=3.5)
    try:
        for _ in range(4):
            client.send_command("autoconfiguration_brushless_motor", 6.5, 8)
            time.sleep(0.05)
        assert wait_for(lambda: sim.flash_saved, timeout=4)
        assert sim.autoconfig_runs == 1
    finally:
        client.close()
        sim.close()


def test_the_drive_gives_up_without_messages_but_the_heartbeat_keeps_it_going():
    sim, client = autoconfig_rig(seconds=1.4, heartbeat=0.6)
    try:
        client.send_command("autoconfiguration_brushless_motor", 6.5, 8)
        assert wait_for(lambda: sim.autoconfig_aborted == 1, timeout=4)          # one command, then silence: the drive stops
        assert not sim.flash_saved
    finally:
        client.close()
        sim.close()
    sim, client = autoconfig_rig(seconds=1.4, heartbeat=0.6)
    try:
        client.set_heartbeat(0.2)                                                # what the tuner does while the procedure runs
        client.send_command("autoconfiguration_brushless_motor", 6.5, 8)
        assert wait_for(lambda: sim.flash_saved, timeout=5)
        assert sim.autoconfig_aborted == 0 and sim.autoconfig_runs == 1          # ... and it was not started a second time
        client.set_heartbeat(None)
        seen = len(sim.commands_received)
        time.sleep(0.4)
        assert len(sim.commands_received) == seen                                # the heartbeat is only reads, never commands
    finally:
        client.close()
        sim.close()


def test_only_reset_ends_the_procedure_stop_does_not_and_a_faulted_drive_ignores_it():
    sim, client = autoconfig_rig(seconds=3.0, heartbeat=3.5)
    try:
        client.set_heartbeat(0.2)
        client.send_command("autoconfiguration_brushless_motor", 6.5, 8)
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 3)
        client.stop()                                                            # the user reports: Stop does not end the automatic configuration
        time.sleep(0.6)
        assert client.telemetry_snapshot().get("op_mode") == 3 and sim.autoconfig_aborted == 0
        client.reset()                                                           # Reset does (and clears errors, cuts the current to the motor)
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 0) and not sim.flash_saved and sim.autoconfig_aborted == 1
        assert [name for name, _ in sim.commands_received] == ["autoconfiguration_brushless_motor", "stop", "reset"]
        sim.fault_bits = 0x0100
        client.send_command("autoconfiguration_brushless_motor", 6.5, 8)
        assert wait_for(lambda: ("autoconfiguration_brushless_motor", (6.5, 8)) in sim.ignored_commands)
        assert sim.autoconfig_runs == 1                                          # only the first one ever started
    finally:
        client.close()
        sim.close()


def test_the_advanced_command_carries_the_general_system_dynamics():
    sim, client = autoconfig_rig(seconds=0.5, heartbeat=3.5)
    try:
        client.send_command("autoconfiguration_advanced_brushless_motor", 6.5, 8, 6.32)          # twice the default dynamics
        assert wait_for(lambda: sim.flash_saved, timeout=4)
        name, values = sim.commands_received[-1]
        assert name == "autoconfiguration_advanced_brushless_motor" and values == pytest.approx((6.5, 8, 6.32), abs=0.01)   # a half float on the wire
        assert sim.params[(0x2002, 0x22)] == pytest.approx(0.058 * values[2] / 3.16)     # a more aggressive control system, from what the drive received
        assert sim.params[(0x2002, 0x22)] > 0.058 * 1.9
    finally:
        client.close()
        sim.close()


def test_a_test_period_is_sent_in_1_over_256_s_steps_and_never_as_zero(rig):
    """The tests' Period is a "FLOAT16" = fixed point x256 (measured on the real drive 2026-09-25: 1.0 s -> raw 256 ran
    exactly 1 s; the half-float encoding sent before ran 60 s). A period under 1/256 s would go out as raw 0 - refused."""
    sim, client = rig
    client.run_test("testing_electronic_speed_control_esc", 1.0, 6.0, 2)
    assert client.commanded == ("testing_electronic_speed_control_esc", (1.0, 6.0, 2))
    client.stop()
    with pytest.raises(ValueError):
        client.run_test("testing_electronic_speed_control_esc", 0.001, 6.0, 2)
    assert client.commanded is None


def test_the_autoconfiguration_frames_match_the_reference():
    frame = P.command_frame(7, 0x400, 0xD0, [(4, "FLOAT32", 6.5), (2, "UINT16", 8)])
    assert frame == (0x407, bytes([0xD0, 0, 8, 0]) + bytes.fromhex("0000d040"))         # current at byte 4 (FLOAT32), poles at byte 2
    advanced = defs.load_commands()["autoconfiguration_advanced_brushless_motor"]
    frame = P.command_frame(7, advanced.cob, advanced.code, [(p.offset, p.dtype, v) for p, v in zip(advanced.params, (6.5, 8, 3.16))])
    assert frame[0] == 0x407 and frame[1][0] == 0xD1 and frame[1][2:4] == bytes([8, 0])
    assert frame[1][6:8] == P.encode_value("FLOAT16", 6.5) == bytes.fromhex("8006")                                  # fixed point x256
    assert frame[1][4:6] == P.encode_value("FLOAT16", 3.16) == bytes.fromhex("2903")


# ---- the drive's built-in step-response tests ----------------------------------------------------------------------
def test_the_esc_step_response_test_drives_a_square_wave_of_speed():
    sim, client = make_rig()
    try:
        client.run_test("testing_electronic_speed_control_esc", 0.2, 40.0)
        assert client.commanded == ("testing_electronic_speed_control_esc", (0.2, 40.0, 0))
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 16)                 # Testing - Speed Step Response
        seen_positive = wait_for(lambda: client.telemetry_snapshot().get("speed_hz", 0) > 20)
        seen_negative = wait_for(lambda: client.telemetry_snapshot().get("speed_hz", 0) < -20)
        assert seen_positive and seen_negative                                                    # the sign alternates
        client.stop()
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 0) and client.commanded is None
    finally:
        client.close()
        sim.close()


def test_the_servo_step_response_test_steps_the_work_zone_count():
    sim, client = make_rig()
    try:
        client.run_test("testing_servo_control", 0.15, 5000)
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 17)                 # Testing - Servo Step Response
        seen_positive = wait_for(lambda: client.telemetry_snapshot().get("wz_count", 0) == 5000)
        seen_negative = wait_for(lambda: client.telemetry_snapshot().get("wz_count", 0) == -5000)
        assert seen_positive and seen_negative
    finally:
        client.close()
        sim.close()


def test_the_current_step_response_tests_show_up_as_extra_phase_current():
    sim, client = make_rig()
    try:
        baseline = abs(sim._ib)                                                                   # elec_pos sits at 0: ia alone can stay 0
        client.run_test("testing_field_oriented_control_foc", 0.2, 3.0)
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 15)                 # Testing - FOC Step Response
        assert wait_for(lambda: abs(sim._ib) > baseline + 1.0)
        client.run_test("testing_iq_current_control", 0.2, 2.0)
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 20)                 # Testing - Iq Current Step Response
    finally:
        client.close()
        sim.close()


def test_a_test_replaces_a_running_speed_command_and_stop_ends_either():
    sim, client = make_rig()
    try:
        client.set_speed_rpm(100)
        assert client.commanded[0] == "esc_rpm"
        client.run_test("testing_electronic_speed_control_esc", 0.2, 30.0)
        assert client.commanded[0] == "testing_electronic_speed_control_esc"                      # the test took over the repeat slot
        assert wait_for(lambda: sim.esc_target_hz != 0.0)
        client.off()
        assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 1) and client.commanded is None    # Off
        assert sim._test is None
    finally:
        client.close()
        sim.close()


def test_a_fault_during_the_test_is_reported_and_the_test_is_not_started_while_faulted():
    sim, client = make_rig()
    try:
        sim.fault_bits = 0x0002
        client.run_test("testing_electronic_speed_control_esc", 0.2, 30.0)

        def ignored():
            return any(key == "testing_electronic_speed_control_esc" and values[1] == pytest.approx(30.0)
                      for key, values in sim.ignored_commands)
        assert wait_for(ignored)
        assert sim._test is None and client.telemetry_snapshot().get("op_mode") == 2              # Fault
    finally:
        client.close()
        sim.close()


# ---- audit fixes (2026-09-25): the simulator's own thread, its test mode, and a write that never left ---------------
def test_the_simulator_keeps_publishing_once_work_zone_count_leaves_int32(rig):
    """Work Zone Count is INT32 telemetry; the simulated counter grows without bound (~33 min at 1000 rpm). Past
    2**31 encode_value raised inside the simulator's own loop, which only caught CanError - the thread died
    silently: telemetry stopped and every read timed out with "no answer". It wraps like a real counter now."""
    sim, client = rig
    sim.wz_count = 2.0**31 + 1000
    assert wait_for(lambda: (client.telemetry_snapshot().get("wz_count") or 0) < 0)     # wrapped, still published
    assert sim._thread.is_alive()
    assert client.identify() == 14                                                        # reads still answered


def test_a_new_speed_command_ends_a_running_step_response_test_in_the_simulator(rig):
    """A step-response test is a mode like any other: the real drive's repeat slot moves to the new command (the
    client's does too). The simulator's _start_exclusive_mode cleared every other mode but never the test, so its
    _step kept replaying the square wave into esc_target_hz and reported Operation Mode 16 (the test) forever."""
    sim, client = rig
    client.run_test("testing_electronic_speed_control_esc", 0.2, 40.0)
    assert wait_for(lambda: sim._test is not None)
    client.set_speed_hz(10.0)
    assert wait_for(lambda: sim._test is None)
    assert wait_for(lambda: client.telemetry_snapshot().get("op_mode") == 6)            # ESC, not 16


def test_a_mapping_write_that_never_left_does_not_reload_the_mapping(rig, monkeypatch):
    """write() to a telemetry-mapping register re-reads the whole mapping afterwards (12 SDO reads) - it used to
    do so in a `finally` even when the write was refused before anything was sent, and a timeout in THAT reload
    then flagged the mapping unknown although nothing had changed."""
    sim, client = rig
    reg = next(r for r in REGS if r.index == 0x1A00)
    calls = []
    monkeypatch.setattr(client, "load_tpdo_mapping", lambda: calls.append(1))
    with pytest.raises(ValueError):
        client.write(reg, 1.5)                                    # not a whole number: refused before it is sent
    assert calls == []
    client.write(reg, sim.params[reg.key], verify=False)         # a write that did go out still reloads
    assert calls == [1]


def test_save_to_flash_sends_the_vendors_store_request_and_the_simulator_keeps_it_across_a_reboot(rig):
    """A parameter write lives in RAM only (measured on the real drive, 2026-09-25: node ID, receiving frequency and
    the telemetry mapping all reverted at a power cycle). save_to_flash() sends what the vendor IDE's own Save to Flash
    button sends - the standard CANopen store request, 'save' -> 0x1010:0x01 - and the real drive acknowledged it
    with 0x60 and kept the value through a power cycle. The simulator now models both."""
    sim, client = rig
    sent = []
    client.frame_listeners.append(lambda d, t, cid, data: sent.append((cid, bytes(data))) if d == "tx" else None)
    receiving = next(r for r in defs.load_registers(False) if r.key == (0x3000, 0x06))
    assert client.read_raw(0x5000, 0xA9).value("FLOAT32") == 1000.0                  # Timer ISR Frequency: the boot value
    client.write(receiving, 2000.0)
    sim.reboot()
    assert client.read(receiving) == 1000.0 and sim.flash_saves == 0                 # never saved: the reboot forgot it
    client.write(receiving, 2000.0)
    assert client.save_to_flash() is True
    assert sent[-1] == (P.COB_REQUEST | NODE, bytes([0x23, 0x10, 0x10, 0x01]) + b"save")
    assert sim.flash_saves == 1 and sim.flash[(0x3000, 0x06)] == 2000.0
    sim.reboot()
    assert client.read(receiving) == 2000.0                                           # saved: the reboot kept it...
    assert client.read_raw(0x5000, 0xA9).value("FLOAT32") == 2000.0                  # ...and the timer took the new rate


def test_save_to_flash_raises_on_an_abort_and_on_silence():
    sim, client = make_rig()
    try:
        sim.flash_mode = "abort"
        with pytest.raises(DriveError, match="refused"):
            client.save_to_flash()
        sim.flash_mode = "silent"
        client.SAVE_TO_FLASH_TIMEOUT = 0.3
        with pytest.raises(DriveTimeout):
            client.save_to_flash()
        assert sim.flash_saves == 0 and sim.flash[(0x3000, 0x06)] == 1000.0
    finally:
        client.close()
        sim.close()


def test_history_is_a_ring_of_the_newest_samples_in_order():
    """The history was a deque of (time, value) tuples that history_snapshot() rebuilt into a numpy array on every
    call - milliseconds per curve per tick in the GUI thread once the 1000-2000 Hz telemetry rates had filled its
    20 000 samples (2026-09-27). Two preallocated numpy rings now; a snapshot is a slice copy."""
    from sdr_tuner.client import _History
    h = _History(capacity=5)
    t, v = h.arrays()
    assert len(t) == 0 and len(v) == 0
    for i in range(3):
        h.append(10.0 + i, i)
    t, v = h.arrays()
    assert t.tolist() == [10.0, 11.0, 12.0] and v.tolist() == [0.0, 1.0, 2.0]
    for i in range(3, 13):                                  # wraps round twice: the newest five, oldest first
        h.append(10.0 + i, i)
    t, v = h.arrays()
    assert t.tolist() == [18.0, 19.0, 20.0, 21.0, 22.0] and v.tolist() == [8.0, 9.0, 10.0, 11.0, 12.0]
    t[0] = -1.0                                             # a copy: the caller cannot corrupt the ring
    assert h.arrays()[0].tolist()[0] == 18.0


def test_history_snapshot_of_a_value_never_seen_is_empty_and_records_nothing(rig):
    _, client = rig
    t, v = client.history_snapshot("never_broadcast", seconds=5)
    assert len(t) == 0 and len(v) == 0 and "never_broadcast" not in client._history
