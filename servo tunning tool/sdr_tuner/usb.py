"""The drive's USB cable: a virtual serial port that carries CAN frames as text (SLCAN / Lawicel format).

    t18780080FD1700000000\\r  =  't', CAN ID 0x187 (3 hex digits), length 8, then the 8 data bytes in hex.

The drive's USB port is always open and already streams its telemetry, so this transport sends only the
frames it is asked to send - no channel-open, bit-rate or close commands. It is a minimal python-can style
bus (send / recv / shutdown) that DriveClient can use exactly like a CAN adapter.
"""
import collections
import time

import can
import serial

USB_VID_PID = (0x1CBE, 0x0002)    # USB-serial chip of the drive (the drive's own USB device)
MAX_LINE = 4096


def parse_frame(line):
    """One text line (without the closing CR) -> can.Message, or None for acks, bells and noise."""
    line = line.lstrip(b"\x07\n ")
    if not line or chr(line[0]) not in "tTrR":
        return None
    kind = chr(line[0])
    id_len = 3 if kind in "tr" else 8
    try:
        arbitration_id = int(line[1:1 + id_len], 16)
        dlc = int(line[1 + id_len:2 + id_len], 16)
        remote = kind in "rR"
        data = b"" if remote else bytes.fromhex(line[2 + id_len:2 + id_len + 2 * dlc].decode("ascii"))
    except ValueError:
        return None
    if dlc > 8 or (not remote and len(data) != dlc):
        return None
    return can.Message(arbitration_id=arbitration_id, is_extended_id=kind in "TR", is_remote_frame=remote,
                       dlc=dlc, data=data, timestamp=time.time())


def format_frame(msg):
    """can.Message -> the text line to write to the port."""
    if msg.is_extended_id:
        raise can.CanOperationError("this drive uses 11-bit CAN IDs only")
    if msg.is_remote_frame:
        return f"r{msg.arbitration_id:03X}{msg.dlc:X}\r".encode("ascii")
    return f"t{msg.arbitration_id:03X}{len(msg.data):X}{bytes(msg.data).hex().upper()}\r".encode("ascii")


def find_drive_ports():
    """Serial ports that look like the drive's USB cable (by USB vendor / product ID)."""
    from serial.tools import list_ports
    return [p.device for p in list_ports.comports() if (p.vid, p.pid) == USB_VID_PID]


def find_serial_ports():
    """Every serial port currently reported by the operating system."""
    from serial.tools import list_ports
    return [p.device for p in list_ports.comports()]


class UsbSerialBus:
    def __init__(self, port, baudrate=115200, serial_port=None):
        """serial_port: an already open serial-like object (used by the tests); otherwise `port` is opened."""
        try:
            # exclusive: a second process opening the same port (another instance of this tool, 2026-09-25) used to
            # share it - both then read fragments of the drive's answers and both failed with "USB read failed:
            # device reports readiness to read but returned no data"; with the lock the second open is refused
            # cleanly and the first keeps working
            self._ser = serial_port or serial.Serial(port, baudrate, timeout=0.02, exclusive=True)
        except (serial.SerialException, OSError) as exc:
            if "exclusively lock" in str(exc) or "Resource busy" in str(exc) or "EBUSY" in str(exc):
                raise can.CanInitializationError(f"{port} is already open in another program - another instance of this "
                                                 f"tool, or the vendor IDE? Disconnect there first. ({exc})") from exc
            raise can.CanInitializationError(f"cannot open {port}: {exc}") from exc
        self.channel_info = f"USB serial port {port}"
        self._buffer = bytearray()
        self._frames = collections.deque()

    def send(self, msg, timeout=None):
        data = format_frame(msg)
        try:
            self._ser.write(data)
        except (serial.SerialException, OSError) as exc:
            raise can.CanOperationError(f"USB write failed: {exc}") from exc

    def recv(self, timeout=None):
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._frames:
                return self._frames.popleft()
            self._read_available()
            if not self._frames and end is not None and time.monotonic() >= end:
                return None

    def _read_available(self):
        try:
            chunk = self._ser.read(self._ser.in_waiting or 1)
        except (serial.SerialException, OSError) as exc:
            raise can.CanOperationError(f"USB read failed: {exc}") from exc
        if not chunk:
            return
        self._buffer += chunk
        while (end := self._buffer.find(b"\r")) >= 0:
            line = bytes(self._buffer[:end])
            del self._buffer[:end + 1]
            msg = parse_frame(line)
            if msg is not None:
                self._frames.append(msg)
        if len(self._buffer) > MAX_LINE:        # garbage without a line end
            self._buffer.clear()

    def shutdown(self):
        try:
            self._ser.close()
        except (serial.SerialException, OSError):
            pass
