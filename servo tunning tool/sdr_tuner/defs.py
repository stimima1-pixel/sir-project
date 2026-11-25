"""Loads the parameter, telemetry and command definitions from sdr_tuner/data/*.json (generated from the Device
Reference PDF by tools/import_device_reference.py)."""
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import protocol

DATA_DIR = Path(__file__).parent / "data"

# Telemetry parameters the client and the GUI know by a short key: (index, sub-index) -> key
TELEMETRY_KEYS = {
    (0x4000, 0x03): "fault_bits", (0x5001, 0x04): "vbus", (0x5001, 0x0D): "vbus", (0x4000, 0x10): "speed_hz",
    # ^ DC bus voltage as "FLOAT16" (the drive's factory-default mapping; fixed point x256 on the wire - protocol.py)
    # or as FLOAT32 ("Udc, f32") - the same physical value, so a drive's telemetry mapping can point at either one and
    # this tool shows it as "vbus" either way. (Until 2026-09-25 the FLOAT16 one was decoded as a half float and
    # read ~0 V - that was this tool, not the drive.)
    (0x4000, 0x02): "op_mode", (0x4000, 0x15): "comm_mode", (0x4000, 0x19): "wz_count", (0x4000, 0x1C): "wz_dimension",
    (0x5001, 0x05): "ia", (0x4000, 0x1A): "wz_modulo",
    (0x5001, 0x06): "ib", (0x4000, 0x11): "elec_pos", (0x4000, 0x16): "wz_speed",
    # The loop references and the measured Iq current: not in any default mapping, mapped by the Frequency
    # Response page for the length of a sweep (gui/bode_page.py) - a Bode plot needs the reference AND the
    # response of one loop in the same telemetry message.
    (0x4000, 0x21): "ref_speed_hz", (0x4000, 0x20): "ref_current", (0x4001, 0x03): "iq",
    (0x4000, 0x23): "ref_wz_count",
}
# Useful values that are not in the drive's default telemetry messages: they can only be read by request
POLL_ONLY = ((0x5001, 0x06), (0x4000, 0x11))
TPDO_COBS = (0x180, 0x280, 0x380)
OP_MODE_AUTOCONFIG = 3          # Operation Mode 3 = Autoconfiguration (the configuration mode: the drive's Stop command does nothing in it, Reset ends it)
SPEED_HZ = (0x4000, 0x10)       # Speed (Electrical Frequency), Hz          - mapping code 4194320
SPEED_RPS = (0x4000, 0x16)      # Work Zone: Speed, revolutions per second  - mapping code 4194326


def _num(value):
    return int(value, 0) if isinstance(value, str) else int(value)


@lru_cache(maxsize=None)
def _load(name):
    with open(DATA_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


@dataclass(frozen=True, eq=False)
class Register:
    section: str
    name: str
    index: int
    sub: int
    dtype: str
    units: str = ""
    access: str = "rw"
    options: dict = field(default_factory=dict)    # {int value: label}
    desc: str = ""
    min: float = None
    max: float = None
    power_cycle: bool = False                      # a change only takes effect after a power cycle

    @property
    def key(self):
        return (self.index, self.sub)

    @property
    def address(self):
        return f"0x{self.index:04X}:0x{self.sub:02X}"

    @property
    def writable(self):
        return self.access == "rw"

    @property
    def telemetry(self):
        return self.section.startswith("Telemetry - ")


@dataclass(frozen=True, eq=False)
class TpdoField:
    key: str
    name: str
    units: str
    offset: int
    dtype: str
    index: int
    sub: int


@dataclass(frozen=True, eq=False)
class Tpdo:
    cob: int
    fields: tuple


@dataclass(frozen=True, eq=False)
class CommandParam:
    name: str
    units: str
    offset: int
    dtype: str


@dataclass(frozen=True, eq=False)
class Command:
    key: str
    name: str
    cob: int
    code: int
    params: tuple
    continuous: bool = False       # must be re-sent regularly, or the drive's heartbeat timeout stops the motor

    @property
    def defined(self):
        return self.cob is not None and self.code is not None

    @property
    def supported(self):
        """False if a parameter uses a data type this tool cannot encode (FLOAT24)."""
        return all(p.dtype in protocol.TYPES for p in self.params)


@lru_cache(maxsize=None)
def _registers():
    regs = []
    for section in _load("registers.json")["sections"]:
        for p in section["params"]:
            regs.append(Register(
                section=section["name"], name=p["name"], index=_num(p["index"]), sub=_num(p["sub"]),
                dtype=p["type"], units=p.get("units", ""), access=p.get("access", "rw"),
                options={int(k): v for k, v in p.get("options", {}).items()},
                desc=p.get("desc", ""), min=p.get("min"), max=p.get("max"), power_cycle=p.get("power_cycle", False)))
    return tuple(regs)


def load_registers(telemetry=None):
    """All parameters; telemetry=False: configuration only, True: telemetry only."""
    return [r for r in _registers() if telemetry is None or r.telemetry == telemetry]


def telemetry_catalog():
    """{(index, sub): Register} of the read-only telemetry parameters."""
    return {r.key: r for r in _registers() if r.telemetry}


def mapping_code(index, sub):
    """The Telemetry Mapping Code of a telemetry parameter: its hexadecimal Index and Sub-Index written one after the
    other, read as one decimal number. The reference's own example: 0x4000 and 0x14 make hex 400014 = 4194324."""
    return (index << 8) | sub


def parse_mapping_code(code):
    """-> (index, sub-index) of the telemetry parameter a mapping code stands for.
    4194320 = hex 400010 = 0x4000:0x10, which is Speed (Electrical Frequency) in Hz."""
    return code >> 8, code & 0xFF


def speed_kind(name, units):
    """How a telemetry value is a rotation speed, or None: 'electrical' (Hz of the electrical frequency: divide by the pole
    pairs to get shaft revolutions), 'rps' or 'rpm' (shaft speed). Parameters that are only in Hz for another reason
    (limits, thresholds, clock frequencies) are not speeds."""
    units = (units or "").strip().lower()
    if units == "rps":
        return "rps"
    if units == "rpm":
        return "rpm"
    if units == "hz" and "speed (electrical frequency)" in (name or "").lower():
        return "electrical"
    return None


def tpdo_field(reg, offset):
    return TpdoField(TELEMETRY_KEYS.get(reg.key, reg.address), reg.name, reg.units, offset, reg.dtype, reg.index, reg.sub)


def load_telemetry():
    """-> (list[Tpdo] default telemetry messages, {fault bit number: name})"""
    raw = _load("telemetry.json")
    tpdos = []
    for t in raw["tpdo"]:
        fields = []
        for f in t["fields"]:
            key = (_num(f["index"]), _num(f["sub"]))
            fields.append(TpdoField(TELEMETRY_KEYS.get(key, f"0x{key[0]:04X}:0x{key[1]:02X}"), f["name"], f.get("units", ""),
                                    f["offset"], f["type"], key[0], key[1]))
        tpdos.append(Tpdo(_num(t["cob"]), tuple(fields)))
    return tpdos, {int(k): v for k, v in raw.get("fault_bits", {}).items()}


def fault_names_text(bits, names):
    """Comma-joined fault names for a Fault Bits value (the second item `load_telemetry()` returns), e.g. "Driver
    Fault, Emergency Stop" - empty string if `bits` is 0. A bit not in `names` (this drive's own list) still shows,
    as "Bit N (not in the drive's list)", rather than being silently dropped. Shared by every place that shows a
    drive's fault bits as words instead of a raw number (DriveTab's Fault Bits card and LED panel today; a future
    per-axis status table reuses it too - see the README)."""
    return ", ".join(names.get(b, f"Bit {b} (not in the drive's list)") for b in range(16) if bits >> b & 1)


def poll_only_fields():
    """Telemetry values that are not broadcast by default but can be read by request (no payload offset)."""
    catalog = telemetry_catalog()
    return [tpdo_field(catalog[key], None) for key in POLL_ONLY if key in catalog]


def load_commands():
    """-> {key: Command}"""
    cmds = {}
    for c in _load("commands.json")["commands"]:
        cmds[c["key"]] = Command(
            c["key"], c["name"],
            None if c.get("cob") is None else _num(c["cob"]),
            None if c.get("code") is None else _num(c["code"]),
            tuple(CommandParam(p["name"], p.get("units", ""), p["offset"], p["type"]) for p in c.get("params", ())),
            bool(c.get("continuous", False)))
    return cmds


def check_definitions():
    """Return a list of problems in the JSON files (empty list = consistent)."""
    problems, seen = [], {}
    for r in _registers():
        if r.dtype not in protocol.TYPES:
            problems.append(f"{r.name}: unknown type {r.dtype}")
        if r.key in seen:
            problems.append(f"{r.name}: address {r.address} already used by {seen[r.key]}")
        seen[r.key] = r.name
        if r.options and r.min is not None and r.dtype != "BOOL":
            problems.append(f"{r.name}: both an option list and a range")
    catalog = telemetry_catalog()
    tpdos, _ = load_telemetry()
    for t in tpdos:
        end = 0
        for f in t.fields:
            reg = catalog.get((f.index, f.sub))
            if reg is None:
                problems.append(f"TPDO 0x{t.cob:X} {f.name}: not a telemetry parameter")
            elif reg.dtype != f.dtype:
                problems.append(f"TPDO 0x{t.cob:X} {f.name}: type {f.dtype} but the parameter is {reg.dtype}")
            if f.dtype not in protocol.TYPES or f.offset < end or f.offset + protocol.type_size(f.dtype) > 8:
                problems.append(f"TPDO 0x{t.cob:X} {f.name}: bad type/offset")
            else:
                end = f.offset + protocol.type_size(f.dtype)
    for key in list(TELEMETRY_KEYS) + list(POLL_ONLY):
        if key not in catalog:
            problems.append(f"known telemetry key {key} is not a telemetry parameter")
    codes = {}
    for c in load_commands().values():
        if not c.defined:
            problems.append(f"command {c.key}: no COB / code")
            continue
        if (c.cob, c.code) in codes:
            problems.append(f"command {c.key}: same COB / code as {codes[(c.cob, c.code)]}")
        codes[(c.cob, c.code)] = c.key
        for p in c.params:
            if p.dtype in protocol.TYPES and p.offset + protocol.type_size(p.dtype) > 8:
                problems.append(f"command {c.key}: parameter {p.name} does not fit in the payload")
    return problems
