import pytest

from sdr_tuner import defs, export


def _entries():
    regs = defs.load_registers(False) + defs.load_registers(True)
    return [export.entry(r) for r in regs]


# ---- DataType codes, verified against the actual CiA 306 spec document (not assumed) -----------
def test_data_type_codes_match_the_cia301_predefined_basic_types():
    # Cross-checked against CiA 306's own worked examples: object 1000h (Device Type, UNSIGNED32) uses
    # DataType=0x0007, and 1003sub0 (Number of Errors, UNSIGNED8) uses DataType=0x0005.
    assert export.DATA_TYPE_CODE["UINT32"] == 0x0007
    assert export.DATA_TYPE_CODE["UINT8"] == 0x0005
    assert export.DATA_TYPE_CODE["BOOL"] == 0x0001
    assert export.DATA_TYPE_CODE["INT8"] == 0x0002
    assert export.DATA_TYPE_CODE["INT16"] == 0x0003
    assert export.DATA_TYPE_CODE["INT32"] == 0x0004
    assert export.DATA_TYPE_CODE["UINT16"] == 0x0006
    assert export.DATA_TYPE_CODE["FLOAT32"] == 0x0008


def test_float16_is_exported_as_the_signed_16_bit_integer_it_really_is():
    """The reference's "FLOAT16" is a signed 16-bit fixed-point number on the wire (value x 256, measured 2026-09-25),
    so INTEGER16 is its true wire type; the scaling is flagged with a comment in the generated EDS."""
    assert export._data_type_code("FLOAT16") == export.DATA_TYPE_CODE["INT16"] == 0x0003


# ---- object range classification (CiA 306's own address-range rule) ----------------------------
def test_manufacturer_range_covers_this_drives_real_registers():
    """Every one of this drive's own registers lives at 0x2000 or above - all manufacturer-specific, none of
    them the standard CiA301 identity/communication objects. Controlword/Statusword (0x6040/0x6041) are excluded
    here - they aren't this drive's own registers at all, but the CiA 402 remap patch's own new, genuinely
    standard, optional-range objects (see test_cia_402_remap_objects_are_optional_by_address_range)."""
    for e in _entries():
        if e.index not in (0x1A00, 0x1A01, 0x1A02, 0x6040, 0x6041):
            assert export._object_range(e.index) == "manufacturer"


def test_cia_402_remap_objects_are_optional_by_address_range():
    assert export._object_range(0x6040) == "optional"
    assert export._object_range(0x6041) == "optional"


def test_telemetry_mapping_registers_are_optional_by_address_range():
    assert export._object_range(0x1A00) == "optional"


def test_standard_identity_objects_are_mandatory():
    assert export._object_range(0x1000) == "mandatory"
    assert export._object_range(0x1018) == "mandatory"


# ---- build_eds ------------------------------------------------------------------------------------
def test_eds_has_required_fileinfo_and_deviceinfo_keywords():
    text = export.build_eds(_entries())
    for keyword in ("FileVersion", "FileRevision", "EDSVersion", "CreationTime", "CreationDate", "CreatedBy"):
        assert f"{keyword}=" in text
    for keyword in ("VendorName", "ProductName", "BaudRate_1000", "NrOfRXPDO", "NrOfTXPDO"):
        assert f"{keyword}=" in text
    assert "[FileInfo]" in text and "[DeviceInfo]" in text


def test_eds_mandatory_objects_is_genuinely_empty_for_this_drive():
    """This drive answers none of the standard identity objects (1000h/1001h/1018h) - its own Device Type/serials
    live at manufacturer-specific 5000h:9Fh-A1h instead (confirmed directly against client.py's DEVICE_INFO) - so
    a real [MandatoryObjects] listing them would be actively wrong, not just incomplete."""
    text = export.build_eds(_entries())
    assert "[MandatoryObjects]\nSupportedObjects=0\n" in text


def test_eds_lists_every_real_index_exactly_once_in_optional_or_manufacturer():
    entries = _entries()
    text = export.build_eds(entries)
    real_indexes = sorted({e.index for e in entries})
    manufacturer = [i for i in real_indexes if export._object_range(i) == "manufacturer"]
    optional = [i for i in real_indexes if export._object_range(i) == "optional"]
    assert len(manufacturer) + len(optional) == len(real_indexes)   # every real index falls in exactly one range
    assert f"SupportedObjects={len(optional)}" in text
    assert f"SupportedObjects={len(manufacturer)}" in text
    for index in real_indexes:
        assert f"0x{index:04X}" in text


def test_eds_never_fabricates_a_sub0_entry():
    """None of this drive's own registers use sub-index 0 (confirmed directly) - a synthetic RECORD wrapper with
    a fabricated sub0 "highest sub-index" entry would describe an object this hardware does not actually answer."""
    text = export.build_eds(_entries())
    assert "sub0]" not in text


def test_eds_describes_a_real_register_correctly():
    reg = next(r for r in defs.load_registers(False) if r.name == "Motor Type (Brushless or Brushed)")
    text = export.build_eds([export.entry(reg)])
    section = f"[{reg.index:X}sub{reg.sub:X}]"
    assert section in text
    body = text.split(section, 1)[1]
    assert "ParameterName=Motor Type (Brushless or Brushed)" in body
    assert "ObjectType=0x7" in body
    assert f"DataType=0x{export.DATA_TYPE_CODE[reg.dtype]:04X}" in body
    assert "AccessType=rw" in body


def test_eds_marks_a_read_only_register_ro():
    reg = next(r for r in defs.load_registers(True))
    text = export.build_eds([export.entry(reg)])
    assert "AccessType=ro" in text


def test_eds_notes_power_cycle_registers():
    reg = next(r for r in defs.load_registers(False) if r.power_cycle)
    text = export.build_eds([export.entry(reg)])
    assert "takes effect only after a power cycle" in text


def test_eds_notes_enum_options_as_a_comment():
    reg = next(r for r in defs.load_registers(False) if r.options)
    text = export.build_eds([export.entry(reg)])
    label = next(iter(reg.options.values()))
    assert label in text


def test_eds_includes_node_and_poles_when_given():
    text = export.build_eds(_entries(), node_id=7, poles=10)
    assert "node 7" in text
    assert "10 rotor poles" in text


# ---- build_html_report ------------------------------------------------------------------------------
def test_html_report_is_well_formed_and_lists_every_entry():
    from html import escape
    entries = _entries()
    html = export.build_html_report(entries)
    assert html.startswith("<!DOCTYPE html>")
    assert html.count("<table>") == len({e.section for e in entries})
    for e in entries:
        assert escape(e.name) in html


def test_html_report_shows_a_known_value_and_dash_for_an_unknown_one():
    reg = next(r for r in defs.load_registers(False))
    known = export.entry(reg, value=42, has_value=True)
    unknown = export.entry(reg, value=None, has_value=False)
    assert ">42<" in export.build_html_report([known])
    assert "&mdash;" in export.build_html_report([unknown])


def test_html_report_escapes_a_dangerous_description():
    from sdr_tuner.defs import Register
    reg = Register(section="Test", name="<script>", index=0x2000, sub=1, dtype="UINT8", units="", access="ro",
                   options={}, desc="<b>bold</b>", min=None, max=None, power_cycle=False)
    html = export.build_html_report([export.entry(reg)])
    assert "<script>" not in html.split("<h1>", 1)[1].replace("&lt;script&gt;", "")
    assert "&lt;script&gt;" in html


# ---- parameter snapshots (Parameters Comparison) -----------------------------------------------
def test_snapshot_only_includes_entries_with_a_known_value():
    regs = defs.load_registers(False)[:3]
    entries = [export.entry(regs[0], value=1, has_value=True), export.entry(regs[1], value=2, has_value=True),
              export.entry(regs[2])]                                              # never read - has_value=False
    snap = export.parse_snapshot(export.build_snapshot(entries))
    assert len(snap["parameters"]) == 2
    assert {p["index"] for p in snap["parameters"]} == {regs[0].index, regs[1].index}


def test_snapshot_round_trips_node_and_poles():
    snap = export.parse_snapshot(export.build_snapshot([], node_id=7, poles=10))
    assert snap["node_id"] == 7
    assert snap["poles"] == 10


def test_parse_snapshot_rejects_a_file_that_is_not_one():
    with pytest.raises(ValueError):
        export.parse_snapshot("not json at all")
    with pytest.raises(ValueError):
        export.parse_snapshot('{"some": "other json"}')


def test_diff_snapshots_flags_a_changed_value():
    reg = defs.load_registers(False)[0]
    snap_a = export.parse_snapshot(export.build_snapshot([export.entry(reg, value=10, has_value=True)]))
    snap_b = export.parse_snapshot(export.build_snapshot([export.entry(reg, value=20, has_value=True)]))
    rows = export.diff_snapshots(snap_a, snap_b)
    assert len(rows) == 1
    row = rows[0]
    assert row.value_a == 10 and row.value_b == 20
    assert row.differs is True
    assert row.only_in is None


def test_diff_snapshots_flags_a_matching_value_as_not_differing():
    reg = defs.load_registers(False)[0]
    snap = export.parse_snapshot(export.build_snapshot([export.entry(reg, value=5, has_value=True)]))
    rows = export.diff_snapshots(snap, snap)
    assert rows[0].differs is False


def test_diff_snapshots_flags_a_parameter_only_present_in_one_file():
    regs = defs.load_registers(False)[:2]
    snap_a = export.parse_snapshot(export.build_snapshot(
        [export.entry(regs[0], value=1, has_value=True), export.entry(regs[1], value=2, has_value=True)]))
    snap_b = export.parse_snapshot(export.build_snapshot([export.entry(regs[0], value=1, has_value=True)]))
    rows = export.diff_snapshots(snap_a, snap_b)
    only_b_missing = next(r for r in rows if (r.index, r.sub) == (regs[1].index, regs[1].sub))
    assert only_b_missing.only_in == "a"
    assert only_b_missing.value_b is None
    assert only_b_missing.differs is True


# ---- audit fixes (2026-09-25) --------------------------------------------------------------------------------
def test_a_snapshot_whose_rows_lack_the_expected_keys_is_refused_cleanly():
    """parse_snapshot used to accept anything with a "parameters" key; diff_snapshots then KeyError'd on a
    hand-edited or foreign row (outside the dialog's own except) instead of one clear message."""
    import json
    import pytest
    with pytest.raises(ValueError, match="parameter #1 is malformed"):
        export.parse_snapshot(json.dumps({"parameters": [{"foo": 1}]}))
    with pytest.raises(ValueError, match="not a parameter snapshot"):
        export.parse_snapshot(json.dumps({"parameters": "nope"}))
    good = export.parse_snapshot(json.dumps({"parameters": [
        {"index": 0x2000, "sub": 1, "name": "x", "section": "s", "value": 1}]}))
    assert export.diff_snapshots(good, good)[0].differs is False


def test_the_eds_loads_in_a_standard_canopen_reader(tmp_path):
    """The .eds is for OTHER CANopen tools. It used to list every object but write only [XXXXsubN] sections -
    no main [XXXX] section per object - so canopen.import_od failed on the very first one."""
    import pytest
    canopen = pytest.importorskip("canopen")
    from sdr_tuner import defs
    entries = [export.entry(r) for r in defs.load_registers()]
    path = tmp_path / "drive.eds"
    path.write_text(export.build_eds(entries))
    od = canopen.import_od(str(path))
    for index in sorted({e.index for e in entries}):
        subs = [e.sub for e in entries if e.index == index]
        assert index in od
        if len(subs) == 1 and subs[0] == 0:
            # A genuine standalone VAR object (Controlword/Statusword) - canopen represents this as a plain
            # ODVariable, not a record/array, so len() means something else entirely for it (its data type's
            # bit width, e.g. 16 for a UINT16 - not a sub-index count). Check the type directly instead.
            assert isinstance(od[index], canopen.objectdictionary.ODVariable)
        else:
            assert len(od[index]) == len(subs)              # every real sub-index - and no fabricated sub0
