"""Wire format of the SDR-CB-60R CAN protocol (Stimita Device Reference, revision L).

CAN ID (11 bit) = COB ID + Node ID (1..127); payload = 8 bytes, little-endian values.

Taken from the device-reference figures: read/write request (COB 0x600, code 0x40 / 0x20,
index, sub-index, value at byte 4), read/error response (COB 0x580, size codes
0x43/0x47/0x4B/0x4F, 0x80 = error) and commands (COB per command, code in byte 0).
Little-endian byte order and the size of unused value bytes follow CANopen SDO
conventions and were confirmed on a real drive (see README).
"""
import math
import struct
from dataclasses import dataclass

COB_MASK, NODE_MASK = 0x780, 0x7F
COB_REQUEST = 0x600     # read / write request, host -> drive
COB_RESPONSE = 0x580    # read / error response, drive -> host
CMD_READ = 0x40         # "read, unknown size"
CMD_WRITE = 0x20        # "write, undefined size"
RESPONSE_ERROR = 0x80
RESPONSE_ACK = 0x60     # write acknowledgement: echoes index, sub-index and the written value (seen on the real drive)
# response command code -> value size in bytes (None: drive does not report the size)
RESPONSE_SIZE = {0x43: 4, 0x47: 3, 0x4B: 2, 0x4F: 1, 0x40: None}

# dtype -> (little-endian struct format, size in bytes).
# "FLOAT16" is the reference's own name, but on the drive it is NOT an IEEE-754 half float: it is a 16-bit FIXED-POINT
# number, the value times 256 (8 fractional bits, step 1/256). Measured on the bench drive on 2026-09-25: the DC-bus
# "FLOAT16" register read raw 0x180A = 6154 -> 24.04 V (its FLOAT32 twin said 24.03 V; as a half float it would be
# 0.002, the "always ~0 V" once taken for a firmware defect), and the test commands' Period ran at raw/256 seconds
# for every value tried (raw 256 -> a clean 1.000 s sine, raw 64 -> 0.25 s; a half-float 1.0 = 0x3C00 -> 60 s).
# Stored signed (two's complement) so a negative Brushed channel voltage can be sent; whether the drive reads the
# top bit that way is untested (nothing on the bench can take a negative one) - see README, Still to confirm.
FIXED_POINT_SCALE = 256.0
TYPES = {
    "BOOL": ("<B", 1), "UINT8": ("<B", 1), "INT8": ("<b", 1),
    "UINT16": ("<H", 2), "INT16": ("<h", 2),
    "UINT32": ("<I", 4), "INT32": ("<i", 4),
    "FLOAT32": ("<f", 4), "FLOAT16": ("<h", 2),
}


def is_float(dtype):
    return dtype.startswith("FLOAT")


def type_size(dtype):
    return TYPES[dtype][1]


def encode_value(dtype, value):
    """Python value -> little-endian bytes of exactly type_size(dtype). Raises ValueError if it does not fit."""
    fmt = TYPES[dtype][0]
    try:
        if dtype == "BOOL":
            value = 1 if value else 0
        elif is_float(dtype):
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{value} is not a finite number")
            if dtype == "FLOAT16":
                value = int(round(value * FIXED_POINT_SCALE))          # fixed point: the nearest 1/256
        else:
            if int(value) != value:
                raise ValueError(f"{value!r} is not a whole number")
            value = int(value)
        return struct.pack(fmt, value)
    except (struct.error, OverflowError) as exc:
        raise ValueError(f"{value!r} does not fit in {dtype}") from exc


def decode_value(dtype, raw):
    fmt, size = TYPES[dtype]
    (value,) = struct.unpack(fmt, bytes(raw[:size]).ljust(size, b"\0"))
    if dtype == "FLOAT16":
        return value / FIXED_POINT_SCALE
    return bool(value) if dtype == "BOOL" else value


def wire_value(dtype, value):
    """The value the drive will really hold once `value` has been sent as `dtype`: a FLOAT32 loses what single
    precision cannot carry, a FLOAT16 is rounded to the nearest 1/256, an integer type is unchanged."""
    return decode_value(dtype, encode_value(dtype, value))


def can_id(cob, node):
    if not 1 <= node <= 127:
        raise ValueError(f"node ID must be 1..127, got {node}")
    return cob + node


def split_can_id(arbitration_id):
    """-> (cob, node)"""
    return arbitration_id & COB_MASK, arbitration_id & NODE_MASK


def read_request(node, index, sub):
    """-> (can_id, payload)"""
    return can_id(COB_REQUEST, node), bytes([CMD_READ, index & 0xFF, index >> 8, sub, 0, 0, 0, 0])


def write_request(node, index, sub, dtype, value):
    raw = encode_value(dtype, value)
    return can_id(COB_REQUEST, node), bytes([CMD_WRITE, index & 0xFF, index >> 8, sub]) + raw.ljust(4, b"\0")


def command_frame(node, cob, code, params=()):
    """params: iterable of (byte offset, dtype, value)."""
    data = bytearray(8)
    data[0] = code
    for offset, dtype, value in params:
        raw = encode_value(dtype, value)
        data[offset:offset + len(raw)] = raw
    return can_id(cob, node), bytes(data)


@dataclass(frozen=True)
class Response:
    node: int
    index: int
    sub: int
    code: int
    raw: bytes          # payload bytes 4..7

    @property
    def error(self):
        return self.code == RESPONSE_ERROR

    @property
    def size(self):
        return RESPONSE_SIZE.get(self.code)

    def value(self, dtype):
        return decode_value(dtype, self.raw)


def parse_response(arbitration_id, data):
    """Return a Response for a read/error response frame, else None."""
    cob, node = split_can_id(arbitration_id)
    data = bytes(data).ljust(8, b"\0")
    if cob != COB_RESPONSE or (data[0] != RESPONSE_ERROR and data[0] not in RESPONSE_SIZE):
        return None
    return Response(node, data[1] | data[2] << 8, data[3], data[0], data[4:8])
