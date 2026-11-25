"""Documentation exports for the drive's whole object dictionary: a human-readable HTML report and a CANopen
Electronic Data Sheet (.eds). Found missing from the vendor's own IDE during the 2026-09-24 feature audit. No Qt -
see gui/main_window.py for the ribbon wiring that calls these.

The EDS format follows CiA 306 (Electronic Data Sheet Specification) - checked directly against the actual
specification document (not assumed): the required [FileInfo]/[DeviceInfo] keywords, the per-object section
keywords (ObjectType/DataType/AccessType/DefaultValue/PDOMapping/LowLimit/HighLimit), the [MandatoryObjects]/
[OptionalObjects]/[ManufacturerObjects] address-range split (1000h/1001h/1018h mandatory, the rest of 1000h-1FFFh
and 6000h-FFFFh optional, 2000h-5FFFh manufacturer), and CiA301's own predefined DataType code table.

This drive answers none of the standard identity objects (1000h/1001h/1018h - its own Device Type/serials live at
manufacturer-specific 5000h:9Fh-A1h instead, confirmed directly), so [MandatoryObjects] is genuinely empty here -
not a bug, an honest reflection of what this hardware actually implements, not what a generic CANopen node would.

Every one of this drive's own registers packs several unrelated parameters under one shared Index using the
Sub-Index purely as a flat address slot (confirmed: zero registers use sub 0, the sub CiA301's own ARRAY/RECORD
convention reserves for a "highest sub-index" count) - so each register is described as its own independent VAR
object at "[<index>sub<sub>]", not wrapped in a synthetic RECORD header with a fabricated sub-0 entry that would
claim an object this drive does not actually answer."""
import json
from collections import namedtuple
from datetime import datetime, timezone
from html import escape

VENDOR_NAME = "Stimita Motion Control"
PRODUCT_NAME = "SDR-CB-60R-D"

# CiA301's own predefined basic Data Type indices (the subset this drive's own registers use).
DATA_TYPE_CODE = {
    "BOOL": 0x0001, "INT8": 0x0002, "INT16": 0x0003, "INT32": 0x0004,
    "UINT8": 0x0005, "UINT16": 0x0006, "UINT32": 0x0007, "FLOAT32": 0x0008,
}
# The reference's "FLOAT16" is, on the drive, a signed 16-bit FIXED-POINT number (the value x 256 - see protocol.py),
# so INTEGER16 IS its wire type; the x256 scaling is what a reader must know, flagged with a comment in the file.
FLOAT16_FALLBACK_CODE = 0x0003
MANDATORY_INDEXES = frozenset((0x1000, 0x1001, 0x1018))

ExportEntry = namedtuple("ExportEntry", "index sub name section dtype units writable desc min max options "
                                        "power_cycle value has_value")


def entry(reg, value=None, has_value=False):
    """An ExportEntry from a `defs.Register` (+ its last known value, if any - from a live ParamsTab's own rows)."""
    return ExportEntry(reg.index, reg.sub, reg.name, reg.section, reg.dtype, reg.units, reg.writable, reg.desc,
                       reg.min, reg.max, reg.options, reg.power_cycle, value, has_value)


def _data_type_code(dtype):
    return FLOAT16_FALLBACK_CODE if dtype == "FLOAT16" else DATA_TYPE_CODE[dtype]


def _object_range(index):
    if index in MANDATORY_INDEXES:
        return "mandatory"
    if 0x1000 <= index <= 0x1FFF or 0x6000 <= index <= 0xFFFF:
        return "optional"
    return "manufacturer"


def _format_number(value):
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


# ---- EDS (CANopen Electronic Data Sheet) -------------------------------------------------------
def build_eds(entries, node_id=None, poles=None, now=None):
    entries = sorted(entries, key=lambda e: (e.index, e.sub))
    now = now or datetime.now(timezone.utc)
    date, time = now.strftime("%m-%d-%Y"), now.strftime("%I:%M%p")
    by_range = {"mandatory": [], "optional": [], "manufacturer": []}
    for e in entries:
        by_range[_object_range(e.index)].append(e.index)

    lines = [
        "[FileInfo]",
        f"FileName={PRODUCT_NAME}.eds",
        "FileVersion=1",
        "FileRevision=1",
        "EDSVersion=4.0",
        f"Description=Object dictionary for the {PRODUCT_NAME} servo drive",
        f"CreationTime={time}",
        f"CreationDate={date}",
        "CreatedBy=STIMITA MOTION CONTROL - SDR Servo Tuning Tool",
        f"ModificationTime={time}",
        f"ModificationDate={date}",
        "ModifiedBy=STIMITA MOTION CONTROL - SDR Servo Tuning Tool",
        "",
        "[DeviceInfo]",
        f"VendorName={VENDOR_NAME}",
        f"ProductName={PRODUCT_NAME}",
        f"OrderCode={PRODUCT_NAME}",
        "BaudRate_125=1",
        "BaudRate_250=1",
        "BaudRate_500=1",
        "BaudRate_1000=1",
        "SimpleBootUpMaster=0",
        "SimpleBootUpSlave=1",
        "Granularity=8",
        "NrOfRXPDO=0",
        "NrOfTXPDO=3",
        "LSS_Supported=0",
    ]
    if node_id:
        lines.append(f"; connected at node {node_id} when this file was generated")
    if poles:
        lines.append(f"; {poles} rotor poles read from the drive when this file was generated")
    lines.append("")

    for section_name, key in (("[MandatoryObjects]", "mandatory"), ("[OptionalObjects]", "optional"),
                              ("[ManufacturerObjects]", "manufacturer")):
        indexes = sorted(set(by_range[key]))
        lines.append(section_name)
        lines.append(f"SupportedObjects={len(indexes)}")
        for i, index in enumerate(indexes, 1):
            lines.append(f"{i}=0x{index:04X}")
        lines.append("")

    last_index = None
    standalone_var = False
    for e in entries:
        if e.index != last_index:
            # CiA 306: every object listed above needs its own main section, with the sub-index count - the
            # [XXXXsubN] sections alone were all this file had, and a standard reader (canopen.import_od) failed
            # on the very first object, "0x1A00 was not found in Object Dictionary": none of the 47 ever loaded.
            # Every object of this drive's OWN has sub-indices 1+ of mixed types (no register sits at sub 0),
            # hence RECORD, with no fabricated sub0 "highest sub-index" entry (test_eds_never_fabricates_a_sub0_
            # entry - this hardware does not answer sub-index 0 on any of its own registers); a standard reader
            # loads the file fine without one (checked directly with canopen: all 47 objects, all 417 sub-indices,
            # no warnings). The CiA 402 remap patch's own objects (Controlword/Statusword) are different: each is
            # a genuine standalone VAR at real sub-index 0 - the standard CiA 402 convention for a plain
            # Controlword/Statusword object, not a record - so those get one section directly at [index],
            # ObjectType=0x7, with no separate RECORD wrapper or [indexsub0] section at all.
            subs = [x.sub for x in entries if x.index == e.index]
            standalone_var = len(subs) == 1 and subs[0] == 0
            if not standalone_var:
                lines.append(f"[{e.index:X}]")
                lines.append(f"ParameterName={e.section}")
                lines.append("ObjectType=0x9")
                lines.append(f"SubNumber={len(subs)}")
                lines.append("")
            last_index = e.index
        section = f"{e.index:X}" if standalone_var else f"{e.index:X}sub{e.sub:X}"
        if e.options:
            lines.append(f"; {e.name}: " + ", ".join(f"{v}={label}" for v, label in sorted(e.options.items())))
        if e.dtype == "FLOAT16":
            lines.append(f"; {e.name} is fixed point on the wire: INTEGER16 holding the value x 256 "
                         f"(the reference calls the type FLOAT16; it is not an IEEE half float)")
        if e.power_cycle:
            lines.append(f"; {e.name} takes effect only after a power cycle of the drive")
        lines.append(f"[{section}]")
        lines.append(f"ParameterName={e.name}")
        lines.append("ObjectType=0x7")
        lines.append(f"DataType=0x{_data_type_code(e.dtype):04X}")
        lines.append(f"AccessType={'rw' if e.writable else 'ro'}")
        if e.min is not None:
            lines.append(f"LowLimit={_format_number(e.min)}")
        if e.max is not None:
            lines.append(f"HighLimit={_format_number(e.max)}")
        lines.append("DefaultValue=")
        lines.append("PDOMapping=0")
        lines.append("")
    return "\n".join(lines) + "\n"


# ---- HTML report --------------------------------------------------------------------------------
def build_html_report(entries, node_id=None, poles=None, now=None):
    entries = sorted(entries, key=lambda e: (e.section, e.index, e.sub))
    now = now or datetime.now(timezone.utc)
    sections = {}
    for e in entries:
        sections.setdefault(e.section, []).append(e)

    device_line = f"Node {node_id}" if node_id else "Not connected"
    if poles:
        device_line += f" &middot; {poles} rotor poles"

    parts = [f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{escape(PRODUCT_NAME)} - Object Dictionary</title>
<style>
body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; background: #0C0D10; color: #EDE7D9; margin: 2em; }}
h1 {{ color: #D4AF37; }}
h2 {{ color: #D4AF37; border-bottom: 1px solid #2A2D36; padding-bottom: 4px; margin-top: 2em; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 1em; }}
th, td {{ text-align: left; padding: 4px 10px; border-bottom: 1px solid #2A2D36; font-size: 13px; }}
th {{ color: #D4AF37; }}
td.num {{ text-align: right; font-family: SF Mono, Menlo, monospace; }}
.meta {{ color: #9C978B; }}
</style></head><body>
<h1>{escape(PRODUCT_NAME)} &mdash; Object Dictionary</h1>
<p class="meta">{device_line} &middot; generated {escape(now.strftime('%Y-%m-%d %H:%M UTC'))} &middot; {len(entries)} parameter(s)</p>
"""]
    for section, rows in sections.items():
        parts.append(f"<h2>{escape(section)}</h2>\n<table><tr><th>Name</th><th>Address</th><th>Type</th>"
                     f"<th>Units</th><th>Access</th><th>Value</th><th>Description</th></tr>\n")
        for e in rows:
            value = _format_number(e.value) if e.has_value and e.value is not None else "&mdash;"
            parts.append(f"<tr><td>{escape(e.name)}</td><td class=\"num\">0x{e.index:04X}:0x{e.sub:02X}</td>"
                         f"<td>{escape(e.dtype)}</td><td>{escape(e.units or '')}</td>"
                         f"<td>{'read / write' if e.writable else 'read-only'}</td>"
                         f"<td class=\"num\">{value}</td><td>{escape(e.desc or '')}</td></tr>\n")
        parts.append("</table>\n")
    parts.append("</body></html>\n")
    return "".join(parts)


# ---- Parameter snapshots (for Parameters Comparison) ---------------------------------------------
# Found missing from the vendor's own IDE during the 2026-09-24 feature audit alongside HTML/EDS export - their
# own Parameters Comparison tool (checked directly, real Elmo Application Studio III) loads two saved parameter
# files and diffs them; this is our own file format for the same job, reusing the same ExportEntry rows the
# HTML/EDS export already builds rather than inventing a second read path.
SnapshotRow = namedtuple("SnapshotRow", "index sub name section value_a value_b differs only_in")


def build_snapshot(entries, node_id=None, poles=None, now=None):
    """A JSON snapshot of every entry this session actually has a value for (has_value) - registers never read
    are left out entirely rather than saved as null, so a diff against another snapshot can't mistake "never
    read" for "read as some sentinel value"."""
    now = now or datetime.now(timezone.utc)
    return json.dumps({
        "product": PRODUCT_NAME,
        "node_id": node_id,
        "poles": poles,
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parameters": [
            {"index": e.index, "sub": e.sub, "name": e.name, "section": e.section, "value": e.value}
            for e in sorted(entries, key=lambda e: (e.index, e.sub)) if e.has_value
        ],
    }, indent=2)


SNAPSHOT_ROW_KEYS = ("index", "sub", "name", "section", "value")     # what build_snapshot writes per parameter, and
                                                                      # exactly what diff_snapshots reads back unguarded


def parse_snapshot(text):
    """The inverse of build_snapshot. Raises ValueError (not a bare KeyError/JSONDecodeError) on anything that
    isn't a snapshot this tool wrote, so the dialog can show one clear message either way - including a file that
    merely HAS a "parameters" list but whose rows lack the keys diff_snapshots indexes (a hand-edited or foreign
    JSON): that used to pass here and KeyError inside the dialog's Compare instead."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("parameters"), list):
        raise ValueError("not a parameter snapshot file")
    for i, row in enumerate(data["parameters"], 1):
        if not isinstance(row, dict) or any(key not in row for key in SNAPSHOT_ROW_KEYS):
            raise ValueError(f"not a parameter snapshot file (parameter #{i} is malformed)")
    return data


def diff_snapshots(snap_a, snap_b):
    """Every parameter present in either snapshot, matched by (index, sub). A parameter only one side ever read
    is flagged via `only_in` rather than treated as an implicit match - two sessions rarely read the exact same
    subset of the object dictionary."""
    by_key_a = {(p["index"], p["sub"]): p for p in snap_a["parameters"]}
    by_key_b = {(p["index"], p["sub"]): p for p in snap_b["parameters"]}
    rows = []
    for key in sorted(set(by_key_a) | set(by_key_b)):
        pa, pb = by_key_a.get(key), by_key_b.get(key)
        value_a = pa["value"] if pa else None
        value_b = pb["value"] if pb else None
        only_in = "a" if pb is None else ("b" if pa is None else None)
        rows.append(SnapshotRow(key[0], key[1], (pa or pb)["name"], (pa or pb)["section"], value_a, value_b,
                                only_in is not None or value_a != value_b, only_in))
    return rows
