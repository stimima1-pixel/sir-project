"""Test helpers: an in-memory serial cable and a bridge that plays the drive's USB-to-CAN side."""
import threading
import time
import uuid

import can

from sdr_tuner import usb
from sdr_tuner.simulator import SimulatedDrive


class _Pipe:
    def __init__(self):
        self.data = bytearray()
        self.cond = threading.Condition()


class FakeSerial:
    """One end of an in-memory serial cable (the subset of pyserial that UsbSerialBus uses)."""

    def __init__(self, rx, tx):
        self._rx, self._tx = rx, tx
        self.timeout = 0.02
        self.closed = False

    @property
    def in_waiting(self):
        with self._rx.cond:
            return len(self._rx.data)

    def read(self, size=1):
        with self._rx.cond:
            if not self._rx.data:
                self._rx.cond.wait(self.timeout)
            chunk = bytes(self._rx.data[:size])
            del self._rx.data[:size]
            return chunk

    def write(self, data):
        with self._tx.cond:
            self._tx.data += data
            self._tx.cond.notify_all()
        return len(data)

    def close(self):
        self.closed = True


def serial_pair():
    """-> (host end, drive end)"""
    a, b = _Pipe(), _Pipe()
    return FakeSerial(rx=b, tx=a), FakeSerial(rx=a, tx=b)


class UsbBridge:
    """The drive's USB side: text frames from the host go onto a virtual CAN bus, bus frames go to the host."""

    def __init__(self, end, channel):
        self.end, self.bus = end, can.Bus(interface="virtual", channel=channel)
        self.sent_lines = []            # raw lines the host wrote, for assertions
        self._buffer = bytearray()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="usb-bridge", daemon=True)
        self._thread.start()

    def _run(self):
        self.end.timeout = 0.002
        while not self._stop.is_set():
            self._buffer += self.end.read(256)
            while (i := self._buffer.find(b"\r")) >= 0:
                line = bytes(self._buffer[:i])
                del self._buffer[:i + 1]
                self.sent_lines.append(line)
                msg = usb.parse_frame(line)
                if msg is not None:
                    self.bus.send(msg)
            while (msg := self.bus.recv(timeout=0)) is not None:       # forward everything that is waiting
                self.end.write(usb.format_frame(msg))

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.bus.shutdown()


class UsbRig:
    """Simulated drive on `node`, behind a fake USB cable. .host is the host-side serial end."""

    def __init__(self, node=7, **sim_kw):
        self.channel = channel = f"usb-{uuid.uuid4().hex}"
        self.sim = SimulatedDrive(channel, node_id=node, tau=0.05, **sim_kw)
        self.host, drive_end = serial_pair()
        self.bridge = UsbBridge(drive_end, channel)

    def bus(self):
        return usb.UsbSerialBus("fake", serial_port=self.host)

    def close(self):
        self.bridge.close()
        self.sim.close()


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False
