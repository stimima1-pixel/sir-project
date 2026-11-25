from collections import Counter

from sdr_tuner import defs


def test_definitions_are_consistent():
    assert defs.check_definitions() == []


def test_register_counts_match_device_reference_rev_l():
    # 242/175/417 are the PDF's own counts; +1/+1/+2 are Controlword/Statusword, the firmware-level CiA 402
    # remap patch's own objects (not in the Device Reference at all - see EXTRA_PARAMS in the importer).
    config, telemetry = defs.load_registers(False), defs.load_registers(True)
    assert (len(config), len(telemetry), len(defs.load_registers())) == (243, 176, 419)
    assert not any(r.telemetry for r in config) and all(r.telemetry and not r.writable for r in telemetry)
    sections = Counter(r.section for r in config)
    assert sections["Datasheet"] == 13
    assert sections["Control Laws"] == 18
    assert sections["Features"] == 10
    assert sections["Networks"] == 8
    assert sections["Telemetry Mapping: TPDO Message 1"] == 4
    assert Counter(r.section for r in telemetry)["Telemetry - System Status"] == 18


def test_no_two_parameters_share_an_address():
    addresses = [r.address for r in defs.load_registers()]
    assert len(addresses) == len(set(addresses))


def test_spot_check_against_device_reference():
    by_addr = {r.address: r for r in defs.load_registers()}
    assert by_addr["0x2000:0x02"].name.startswith("Maximum Limit on Continuous Current")
    assert by_addr["0x2000:0x02"].dtype == "FLOAT32" and by_addr["0x2000:0x02"].units == "A"
    assert by_addr["0x2002:0x22"].name == "Electronic Speed Control: ESC Kp"
    assert by_addr["0x2002:0x36"].dtype == "UINT32"
    assert by_addr["0x2034:0xB0"].dtype == "INT32"
    assert by_addr["0x2044:0x02"].dtype == "UINT16" and by_addr["0x2044:0x02"].name == "Hide Faults"
    assert by_addr["0x3013:0x02"].name == "counts per revolution"
    assert by_addr["0x2000:0x0A"].options[2] == "BISS-C/SSI Encoder"
    assert by_addr["0x2002:0x14"].min == -90 and by_addr["0x2002:0x14"].max == 90
    assert by_addr["0x20FF:0x02"].name == "Activation Key"


def test_power_cycle_parameters_are_flagged():
    by_addr = {r.address: r for r in defs.load_registers()}
    for address in ("0x3000:0x02", "0x3000:0x03", "0x3000:0x06"):        # CAN bit rate, node ID, receiving frequency
        assert by_addr[address].power_cycle, address
    assert not by_addr["0x2000:0x02"].power_cycle
    assert by_addr["0x3000:0x06"].name == "CANopen: receiving frequency"


def test_telemetry_parameters_are_read_only_with_names():
    catalog = defs.telemetry_catalog()
    assert len(catalog) == 176   # +1 is Statusword, the CiA 402 remap patch's own object
    fault = catalog[(0x4000, 0x03)]
    assert fault.name == "Fault Bits" and fault.dtype == "UINT16" and fault.options[0] == "No fault"
    assert catalog[(0x4000, 0x02)].options[2] == "Fault"                # Operation Mode
    assert catalog[(0x4000, 0x15)].options[1] == "Kickstart"           # Commutation Mode
    assert catalog[(0x5001, 0x04)].dtype == "FLOAT16"


def test_mapping_registers_take_any_number():
    for r in defs.load_registers(False):
        if r.section.startswith("Telemetry Mapping"):
            assert r.dtype == "UINT32" and not r.options, r.address       # "Non-zero: any code" is not a closed list


def test_default_telemetry_matches_device_reference_rev_l():
    tpdos, fault_bits = defs.load_telemetry()
    by_cob = {t.cob: {f.key: f for f in t.fields} for t in tpdos}
    assert set(by_cob) == {0x180, 0x280, 0x380}
    f = by_cob[0x180]
    assert list(f) == ["fault_bits", "vbus", "speed_hz"]
    assert (f["fault_bits"].offset, f["fault_bits"].dtype, f["fault_bits"].index, f["fault_bits"].sub) == (0, "UINT16", 0x4000, 0x03)
    assert (f["vbus"].offset, f["vbus"].dtype, f["vbus"].index, f["vbus"].sub) == (2, "FLOAT16", 0x5001, 0x04)
    assert (f["speed_hz"].offset, f["speed_hz"].dtype, f["speed_hz"].sub) == (4, "FLOAT32", 0x10)
    g = by_cob[0x280]
    assert list(g) == ["op_mode", "comm_mode", "wz_count"]
    assert (g["op_mode"].offset, g["op_mode"].sub, g["comm_mode"].offset, g["comm_mode"].sub) == (0, 0x02, 2, 0x15)
    assert (g["wz_count"].offset, g["wz_count"].dtype, g["wz_count"].sub) == (4, "INT32", 0x19)
    h = by_cob[0x380]
    assert list(h) == ["ia", "wz_modulo"]
    assert (h["ia"].dtype, h["ia"].sub, h["wz_modulo"].offset, h["wz_modulo"].dtype, h["wz_modulo"].sub) == ("FLOAT32", 0x05, 4, "UINT32", 0x1A)
    assert fault_bits[15] == "Activation Key is Missing" and fault_bits[0] == "Driver Fault" and fault_bits[14] == "Emergency Stop"
    assert set(fault_bits) == set(range(11)) | {14, 15}                 # the fault bits this drive reports: 0-10, 14 and 15


def test_phase_b_and_electrical_angle_are_not_broadcast_by_default():
    broadcast = {f.key for t in defs.load_telemetry()[0] for f in t.fields}
    assert not broadcast & {"ib", "elec_pos"}
    assert {f.key for f in defs.poll_only_fields()} == {"ib", "elec_pos"}
    assert all(f.offset is None for f in defs.poll_only_fields())


def test_commands():
    cmds = defs.load_commands()
    assert len(cmds) == 32 and all(c.defined for c in cmds.values())
    assert cmds["esc_hz"].cob == 0x200 and cmds["esc_hz"].code == 0x20 and cmds["esc_hz"].continuous
    assert (cmds["esc_hz"].params[0].offset, cmds["esc_hz"].params[0].dtype) == (4, "FLOAT32")
    assert (cmds["esc_rpm"].cob, cmds["esc_rpm"].code) == (0x200, 0x24)
    assert (cmds["stop"].cob, cmds["stop"].code, cmds["stop"].params) == (0x200, 0x04, ())
    assert (cmds["off"].cob, cmds["off"].code) == (0x200, 0x06)
    assert (cmds["reset"].cob, cmds["reset"].code, cmds["reset"].continuous) == (0x200, 0x01, False)
    assert (cmds["servo"].cob, cmds["servo"].code) == (0x300, 0x31)
    assert (cmds["autoconfiguration_brushless_motor"].cob, cmds["autoconfiguration_brushless_motor"].code) == (0x400, 0xD0)


def test_commands_with_a_24_bit_float_are_not_supported():
    unsupported = {k for k, c in defs.load_commands().items() if not c.supported}
    assert unsupported == {"electronic_speed_control_esc_rpm_velocity_with_torque_feed_forward",
                           "servo_linear_position_with_velocity_feed_forward",
                           "servo_modulo_position_with_velocity_feed_forward"}


def test_mapping_codes_follow_the_reference():
    # the reference's own example: Index 0x4000 + Sub-Index 0x14 = hexadecimal 400014 = decimal 4194324
    assert defs.mapping_code(0x4000, 0x14) == 4194324
    assert defs.parse_mapping_code(4194324) == (0x4000, 0x14)
    catalog = defs.telemetry_catalog()
    # Speed in Hz is 4194320, speed in rps is 4194326
    assert defs.parse_mapping_code(4194320) == defs.SPEED_HZ == (0x4000, 0x10)
    assert catalog[defs.SPEED_HZ].name == "Speed (Electrical Frequency)" and catalog[defs.SPEED_HZ].units == "Hz"
    assert defs.parse_mapping_code(4194326) == defs.SPEED_RPS == (0x4000, 0x16)
    assert catalog[defs.SPEED_RPS].name == "Work Zone: Speed" and catalog[defs.SPEED_RPS].units == "RPS"
    assert defs.mapping_code(*defs.SPEED_HZ) == 4194320 and defs.mapping_code(*defs.SPEED_RPS) == 4194326
    for (index, sub), reg in catalog.items():                           # every telemetry parameter survives the round trip
        assert defs.parse_mapping_code(defs.mapping_code(index, sub)) == (index, sub) == reg.key


def test_dc_bus_voltage_is_recognized_whichever_of_its_two_representations_is_mapped():
    """The DC bus voltage exists as a 'FLOAT16' register (fixed point x256, the factory-default mapping) and as a
    FLOAT32 twin 'Udc, f32'; a drive's telemetry mapping can point at either one, and this tool must call both 'vbus'
    (the bench drive's mapping was moved to the FLOAT32 one on 2026-09-22, back when the FLOAT16 one was misdecoded)."""
    float16, float32 = (0x5001, 0x04), (0x5001, 0x0D)
    catalog = defs.telemetry_catalog()
    assert catalog[float16].dtype == "FLOAT16" and catalog[float32].dtype == "FLOAT32"
    for reg in (float16, float32):
        assert defs.TELEMETRY_KEYS[reg] == "vbus"
        assert defs.tpdo_field(catalog[reg], 0).key == "vbus"


def test_only_real_speeds_are_speeds():
    kinds = {r.address: defs.speed_kind(r.name, r.units) for r in defs.load_registers(True)}
    assert kinds["0x4000:0x10"] == "electrical" and kinds["0x4000:0x16"] == "rps"
    assert kinds["0x500A:0x08"] == "rps"                                # a quadrature encoder speed
    assert kinds["0x4007:0x03"] == "electrical"                         # sensorless speed estimate
    for address in ("0x4007:0x09", "0x4007:0x08", "0x5000:0xA8", "0x5000:0xA3", "0x5007:0x0E"):
        assert kinds[address] is None, address                          # limits, thresholds, clocks, PWM frequency
    assert defs.speed_kind("Whatever", "RPM") == "rpm" and defs.speed_kind(None, None) is None


def test_the_fault_bits_are_the_ones_the_drive_lists():
    fault = defs.telemetry_catalog()[(0x4000, 0x03)]
    assert sorted(fault.options) == [0, 1, 2, 3, 4, 8, 9, 10, 16, 32, 64, 128, 256, 512, 1024, 16384, 32768]
    assert fault.options[1] == "Driver Fault" and fault.options[3] == "=1+2" and fault.options[16384] == "Emergency Stop"
    assert 2048 not in fault.options                                     # the generic PDF's RS485 / Dual Encoder bit is not this drive's
    _, names = defs.load_telemetry()
    assert [names[b] for b in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)] == [
        "Driver Warning (Maskable)", "Overheating Protection (firmware-controlled)", "Overcurrent Protection (firmware-controlled)",
        "Thermistor Overheating", "Hall Sensors Fault", "Quadrature Encoder Fault", "SSI Encoder Fault", "SPI Encoder Fault",
        "PWM Encoder Fault", "RC PWM Input Fault"]


def test_telemetry_frequency_is_not_flagged_as_needing_a_power_cycle():
    """Measured on the real bench drive (2026-09-25, tools/telemetry_rate_probe.py): a write to `CANopen: telemetry
    frequency` changes the live broadcast at once - 9 -> 30 -> 100 -> 333 -> 1000 messages/s as written, no power
    cycle. The importer's POWER_CYCLE set (which mirrors the vendor tool's own marks, not the PDF) used to flag it,
    so the GUI warned that it takes effect only after a power cycle."""
    reg = next(r for r in defs.load_registers(False) if r.name == "CANopen: telemetry frequency")
    assert reg.key == (0x3000, 0x05) and reg.power_cycle is False
    node = next(r for r in defs.load_registers(False) if r.key == (0x3000, 0x03))     # the node ID genuinely does
    assert node.power_cycle is True
