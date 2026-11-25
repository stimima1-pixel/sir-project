"""Turns a Stimita Device Reference PDF into the tool's data files.

    python tools/import_device_reference.py "/path/to/Stimita-Device-Reference-0xA020192.pdf"

Writes sdr_tuner/data/registers.json (configuration parameters and telemetry parameters), commands.json (all commands)
and telemetry.json (default telemetry message layout and fault-bit names). Needs PyMuPDF:  pip install pymupdf

Only the tables of the PDF are read (a Word document with real table borders). Nothing outside the output folder is
written. Run it again when a new revision of the reference arrives.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pymupdf

DATA_DIR = Path(__file__).resolve().parent.parent / "sdr_tuner" / "data"
CANOPEN = re.compile(r"^([A-Z0-9]+)\s*,\s*(0x[0-9A-Fa-f]+)\s*,\s*(0x[0-9A-Fa-f]+)\s*,\s*(rw|ro|wo)\b")
DTYPES = "FLOAT32|FLOAT24|FLOAT16|UINT32|UINT16|UINT8|INT32|INT16|INT8|BOOL"
SHORT_KEYS = {    # stable keys for the commands the tool itself uses; every other command gets a key made from its name
    "Electronic Speed Control (ESC), Hz": "esc_hz", "Electronic Speed Control (ESC), RPM": "esc_rpm", "Stop": "stop",
    "Off": "off", "Reset": "reset", "Reset Work Zone": "reset_work_zone", "Virtual Brake": "virtual_brake"}
SDR_FAULT_BITS = {   # the fault bits THIS drive reports, as its own documentation lists them (the generic PDF also has bit 11)
    0: "No fault", 1: "Driver Fault", 2: "Driver Warning (Maskable)", 3: "=1+2", 4: "Overheating Protection (firmware-controlled)",
    8: "Overcurrent Protection (firmware-controlled)", 9: "=1+8", 10: "=2+8", 16: "Thermistor Overheating", 32: "Hall Sensors Fault",
    64: "Quadrature Encoder Fault", 128: "SSI Encoder Fault", 256: "SPI Encoder Fault", 512: "PWM Encoder Fault",
    1024: "RC PWM Input Fault", 16384: "Emergency Stop", 32768: "Activation Key is Missing"}
POWER_CYCLE = {   # settings the vendor's own tool marks "Power Cycle" (the PDF does not say): they apply after a power cycle
    (0x3000, 0x02), (0x3000, 0x03), (0x3000, 0x06), (0x3000, 0x10), (0x3000, 0x12), (0x3000, 0x20)}
# NOT (0x3000, 0x05), CANopen: telemetry frequency, although the vendor tool marks it: measured on the real drive
# (2026-09-25, tools/telemetry_rate_probe.py) a write changes the live broadcast at once - 9 -> 30 -> 100 -> 333 ->
# 1000 messages/s exactly as written, no power cycle.

EXTRA_PARAMS = [   # NOT from the Device Reference PDF - added here so a re-import never drops them (see the sibling
                   # SDR-CB-60R-D firmware project's own README/NOTES.md for the full derivation). Confirmed live on
                   # the real bench drive, 2026-09-29: a binary patch to that drive's own firmware (not anything in
                   # this tool) adds these two CiA 402 objects to its object dictionary. Controlword genuinely drives
                   # Operation Mode (Off/Servo/Stop all confirmed); Statusword is computed fresh every cycle from
                   # Operation Mode, Fault Bits and STO status. This firmware patch is specific to that one bench
                   # drive (serial 2078396D4843) - a different, unpatched SDR-CB-60R-D/120R-C will not answer these.
    {"section": "CiA 402 (firmware patch)", "name": "Controlword", "units": "", "index": "0x6040", "sub": "0x00",
     "type": "UINT16", "access": "rw", "options": {}, "min": None, "max": None,
     "desc": "Not part of the Stimita Device Reference - added by a firmware-level binary patch on this specific "
             "bench drive (see the sibling SDR-CB-60R-D firmware project). Write to command the drive: 0x0000 = "
             "Stop, 0x0006 = Off, 0x000F = Servo (holds the drive's current position). Edge-triggered - the drive "
             "only acts when this value actually changes. Bit 7 (Fault reset) does not perform a real fault-"
             "clearing reset; it is simplified to the same safe state as Off."},
    {"section": "Telemetry - CiA 402 (firmware patch)", "name": "Statusword", "units": "", "index": "0x6041",
     "sub": "0x00", "type": "UINT16", "access": "ro", "options": {}, "min": None, "max": None,
     "desc": "Not part of the Stimita Device Reference - added by the same firmware patch as Controlword. "
             "Computed fresh every cycle: bits 0/1/2/5 (Operation enabled) when Operation Mode >= 4; bits 0/1/5 "
             "(Switched on) for Virtual Brake; bit 3 (Fault) when Fault Bits has any bit set other than the one "
             "maskable Warning bit; bit 4 (Voltage enabled) from STO status - polarity unverified; bit 6 (Switch "
             "on disabled) is the default/idle fallback; bit 7 (Warning) from the maskable Fault Bits bit. "
             "Quick-Stop-Active is not separately reported."},
]


def flat(text):
    """Cell text on one line, with the hyphenation and unit spacing of the Word layout undone."""
    text = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    text = re.sub(r"(?<=\w)- (?=[A-Za-z(])", "-", text)
    return text.replace("/ ", "/").replace("kg*m 2", "kg*m^2").replace("De celeration", "Deceleration")    # a word the PDF splits


def heading_lines(page):
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                yield line["bbox"][1], text


def tables_by_section(doc, titles, first_page, last_page):
    """[(section title, page, rows)] - every table assigned to the last heading above it."""
    events = []
    for pno in range(first_page, last_page + 1):
        page = doc[pno - 1]
        for y, text in heading_lines(page):
            if text in titles:
                events.append((pno, y, 0, text))
        for table in page.find_tables().tables:
            events.append((pno, table.bbox[1], 1, table.extract()))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    section, out = None, []
    for pno, _y, kind, payload in events:
        if kind == 0:
            section = payload
        elif section:
            out.append((section, pno, payload))
    return out


def options_from(raw):
    """{value: label} from the bullets ('- 0: label') that open a description cell. Only the bullets at the very start
    count; the first line that is neither a bullet nor the wrapped end of one closes the list."""
    options, last = {}, None
    for line in (raw or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^[\u2022\u25cf\u00b7]\s*(-?\d+)\s*(?::\s*(.*))?$", line)
        if m:
            last = int(m.group(1))
            options[last] = flat(m.group(2)) if m.group(2) else m.group(1)
        elif options and re.match(r"^[\u2022\u25cf\u00b7]\s*non-zero", line, re.I):
            return {}                                       # "Non-zero: ..." = any other number is valid too: not a closed list
        elif options and last is not None and (line[0].islower() or line[0] == "(") and len(options[last]) < 120:
            options[last] = flat(options[last] + " " + line)         # a bullet that wraps onto a second line
            last = None if line.endswith((".", ")")) else last
        else:
            break
    return options


def range_from(units):
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*(?:-|to)\s*(-?\d+(?:\.\d+)?)", units.replace("- ", "-"))
    if m and float(m.group(1)) < float(m.group(2)):
        return float(m.group(1)), float(m.group(2))
    return None, None


def parse_parameters(doc, toc):
    titles = {t for lvl, t, p in toc if lvl == 2 and (t.startswith("Configuration -") or t.startswith("Telemetry -"))}
    params, current, previous = [], None, None

    def finish():
        nonlocal current
        if current:
            params.append(current)
            current = None
    for section, page, rows in tables_by_section(doc, titles, 4, 82):
        if section != previous:
            finish()
            previous = section
        for r in rows:
            if len(r) < 5:
                continue
            idx, name, units, desc, can = (c or "" for c in r[:5])
            if idx.strip() == "#" or flat(name) == "Parameter":
                continue
            if idx.strip().isdigit():
                finish()
                current = {"section": section, "name": flat(name), "units": flat(units), "raw": desc.strip(), "can": flat(can)}
            elif current and any(flat(c) for c in (name, units, desc, can)):
                current["name"] = flat(current["name"] + " " + name) if flat(name) else current["name"]
                current["units"] = flat(current["units"] + " " + units) if flat(units) else current["units"]
                current["raw"] = (current["raw"] + "\n" + desc.strip()).strip()
                current["can"] = flat(current["can"] + " " + can) if flat(can) else current["can"]
    finish()
    out = []
    for p in params:
        m = CANOPEN.match(p["can"])
        if not m:
            sys.exit(f"cannot read the CANopen cell of '{p['name']}' ({p['section']}): {p['can']!r}")
        dtype, index, sub, access = m.groups()
        lo, hi = range_from(p["units"])
        options = options_from(p["raw"])
        if not options:                                             # "0,1,2" / "0/1/2" in the units column
            m2 = re.match(r"^(\d)([,/]\d)+$", p["units"])
            if m2:
                options = {int(v): str(v) for v in re.split(r"[,/]", p["units"])}
        desc = flat(re.sub(r"[•●]\s*-?\d+\s*(:[^\n]*)?\n?", "", p["raw"]) if options else p["raw"])
        entry = {"section": p["section"], "name": p["name"], "units": p["units"] if p["units"] != "-" else "",
                 "index": index, "sub": sub, "type": dtype, "access": access, "options": options, "min": lo, "max": hi,
                 "desc": desc}
        out.append(entry)
    return out


def parse_commands(doc, toc):
    titles = [t for lvl, t, p in toc if lvl == 2 and t.startswith("Command - ")]
    events = []
    for pno in range(83, 100):
        page = doc[pno - 1]
        for y, text in heading_lines(page):
            if text in titles:
                events.append((pno, y, 0, text))
            m = re.search(r"Function Code for COB-ID is (0x[0-9A-Fa-f]+)", text)
            if m:
                events.append((pno, y, 2, ("cob", m.group(1))))
            m = re.search(r"The Command Code is (0x[0-9A-Fa-f]+)", text)
            if m:
                events.append((pno, y, 2, ("code", m.group(1))))
            if "CONTINUOUSLY" in text:
                events.append((pno, y, 2, ("continuous", True)))
            if "not required to continuously" in text:
                events.append((pno, y, 2, ("continuous", False)))
        for table in page.find_tables().tables:
            events.append((pno, table.bbox[1], 1, table.extract()))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    commands, cur = [], None
    for _pno, _y, kind, payload in events:
        if kind == 0:
            cur = {"name": payload[len("Command - "):], "params": []}
            commands.append(cur)
        elif cur is None:
            continue
        elif kind == 2:
            cur[payload[0]] = payload[1]
        else:
            for r in payload:
                cells = [flat(c) for c in r]
                joined = " ".join(cells)
                m = re.search(r"(" + DTYPES + r")\s+(\d+)\s*$", joined) or None
                if not m:
                    m1, m2 = re.search(r"(" + DTYPES + r")", joined), re.search(r"\b(\d+)\s*$", cells[-1])
                    m = (m1, m2) if m1 and m2 else None
                    dtype, offset = (m1.group(1), int(m2.group(1))) if m else (None, None)
                else:
                    dtype, offset = m.group(1), int(m.group(2))
                if dtype and not joined.startswith("Parameter"):
                    name = cells[1] if len(cells) > 1 and cells[1] else cells[0]
                    cur["params"].append({"name": name.strip(), "units": cells[2] if len(cells) > 2 else "",
                                          "offset": offset, "type": dtype})
    out = []
    for i, c in enumerate(commands):
        if "code" not in c or "cob" not in c:
            sys.exit(f"command '{c['name']}' has no COB / code")
        out.append({"key": SHORT_KEYS.get(c["name"]) or re.sub(r"[^a-z0-9]+", "_", c["name"].lower()).strip("_"), "name": c["name"], "cob": c["cob"],
                    "code": c["code"], "continuous": bool(c.get("continuous")), "params": c["params"]})
    return out


def parse_tpdo(doc):
    messages = []
    for _sec, _page, rows in tables_by_section(doc, {"Telemetry Message with COB-ID 0x180", "Telemetry Message with COB-ID 0x280",
                                                    "Telemetry Message with COB-ID 0x380"}, 100, 100):
        fields = []
        for r in rows:
            cells = [flat(c) for c in r]
            if len(cells) >= 7 and cells[0].isdigit():
                fields.append({"name": cells[1], "units": cells[2], "offset": int(cells[3]), "type": cells[4],
                               "index": cells[5], "sub": cells[6]})
        messages.append(fields)
    cobs = ["0x180", "0x280", "0x380"]
    return [{"cob": cob, "fields": fields} for cob, fields in zip(cobs, messages)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", default=str(DATA_DIR), help="folder for the JSON files (default: sdr_tuner/data)")
    args = ap.parse_args()
    out = Path(args.out)
    doc = pymupdf.open(args.pdf)
    toc = doc.get_toc()
    revision = re.search(r"Revision\s+\w+\s*\([^)]*\)", doc[0].get_text()) or re.search(r"Revision[^\n]*", doc[0].get_text())
    revision = flat(revision.group(0)) if revision else "unknown revision"

    params = parse_parameters(doc, toc) + EXTRA_PARAMS
    for p in params:
        if p["section"] == "Telemetry - System Status" and p["name"] == "Fault Bits":
            p["options"] = dict(SDR_FAULT_BITS)                     # bits 0-10, 14 and 15 only
    sections, order = [], {}
    for p in params:
        name = p["section"]
        if name.startswith("Configuration - "):
            name = name[len("Configuration - "):]
        if name not in order:
            order[name] = {"name": name, "params": []}
            sections.append(order[name])
        entry = {"name": p["name"], "index": p["index"], "sub": p["sub"], "type": p["type"]}
        if p["units"]:
            entry["units"] = p["units"]
        if p["access"] != "rw":
            entry["access"] = p["access"]
        if p["options"]:
            entry["options"] = {str(k): v for k, v in p["options"].items()}
        if p["min"] is not None:
            entry["min"], entry["max"] = p["min"], p["max"]
        if (int(p["index"], 0), int(p["sub"], 0)) in POWER_CYCLE:
            entry["power_cycle"] = True
        if p["desc"]:
            entry["desc"] = p["desc"]
        order[name]["params"].append(entry)
    (out / "registers.json").write_text(json.dumps({
        "_comment": f"Parameters of the SDR servo drive, generated by tools/import_device_reference.py from the Stimita Device Reference ({revision}). "
                    "Sections starting with 'Telemetry - ' are read-only. index/sub are hex strings. Optional keys: units, access (rw|ro, "
                    "default rw), options {value: label}, min, max, power_cycle, desc.",
        "sections": sections}, indent=1, ensure_ascii=False) + "\n")

    commands = parse_commands(doc, toc)
    (out / "commands.json").write_text(json.dumps({
        "_comment": f"Commands (host -> drive), generated by tools/import_device_reference.py from the Device Reference ({revision}). "
                    "CAN ID = cob + node ID; payload byte 0 = command code; parameters at 'offset'. continuous = must be re-sent "
                    "regularly to avoid the drive's heartbeat timeout.",
        "commands": commands}, indent=1) + "\n")

    fault = next(p for p in params if p["section"] == "Telemetry - System Status" and p["name"] == "Fault Bits")
    fault_bits = {}
    for value, label in fault["options"].items():
        if value > 0 and value & (value - 1) == 0:                  # a single bit
            fault_bits[str(value.bit_length() - 1)] = label
    tpdo = parse_tpdo(doc)
    telemetry_path = out / "telemetry.json"
    (telemetry_path).write_text(json.dumps({
        "_comment": f"Default telemetry messages (TPDO) and fault-bit names, generated by tools/import_device_reference.py from the "
                    f"Device Reference ({revision}). CAN ID = cob + node ID. The drive's real mapping is configurable and is read "
                    "from the drive at connect time.",
        "tpdo": tpdo, "fault_bits": fault_bits}, indent=1) + "\n")

    n_cfg = sum(1 for p in params if p["section"].startswith("Configuration"))
    print(f"{revision}: {n_cfg} configuration parameters, {len(params) - n_cfg} telemetry parameters, {len(commands)} commands, "
          f"{sum(len(t['fields']) for t in tpdo)} default telemetry fields, {len(fault_bits)} fault bits named")
    print("written to", out)


if __name__ == "__main__":
    main()
