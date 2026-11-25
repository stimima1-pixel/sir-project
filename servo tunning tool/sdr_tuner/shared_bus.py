"""Lets several DriveClient instances (one per axis/node) safely share ONE physical CAN connection - needed for
real multi-axis use (several servo drives on one shared CAN bus, coordinated together - see the README's the vendor's multi-axis controller
notes), on transports that cannot be opened twice from one process (a serial-based adapter such as CANable/slcan,
and - conservatively, until proven otherwise on real hardware - PCAN/Kvaser/SocketCAN/Vector too).

DriveClient (sdr_tuner/client.py) only ever calls three methods on `self.bus`: `send(msg)`, `recv(timeout)`,
`shutdown()` - exactly what `sdr_tuner/usb.py`'s UsbSerialBus already duck-types in place of a real `can.Bus`.
`SharedBusHub` owns the ONE real connection and a single background fan-out thread; `attach()` hands out a
lightweight `_AxisHandle` satisfying that same three-method interface, so DriveClient itself needs no changes at
all for multi-axis to work over a shared physical link - each attached DriveClient's own `_dispatch()` already
filters every frame by `node == self.node`, exactly as it does today when it owns a bus outright.

The drive's own dedicated USB cable is a one-drive-only link (see usb.py) and never uses this - and the simulator
does not need it either: independent `SimulatedDrive`/`can.Bus("virtual", ...)` pairs on the same channel string
already fan out correctly through python-can's own virtual backend (confirmed directly - that is how the GUI's own
bus already talks to a `SimulatedDrive` today), so multi-axis simulation just opens N independent pairs, no hub."""
import queue
import threading


class _HubFailed:
    """Sentinel pushed to every attached handle's queue once the shared bus itself has failed (the real transport's
    own `recv()` raised) - `_AxisHandle.recv()` re-raises the wrapped exception instead of returning it as a frame,
    so each attached DriveClient's own `_rx_loop` reports the SAME failure `link_error_listeners` already handle
    for a single-axis link, exactly as if its own dedicated bus had failed."""

    def __init__(self, exc):
        self.exc = exc


class SharedBusHub:
    """Owns one already-open bus-like object (`real_bus`: a `can.Bus(...)` or `UsbSerialBus(...)`, built exactly
    the way a single-axis connection already is). Runs one background thread that reads every frame off `real_bus`
    and hands a copy to every attached handle; a handle's own `send()` goes through one shared lock, so concurrent
    sends from different axes' keep-alive/command threads never interleave on the wire. `on_closed`, if given, is
    called once the real connection actually closes (the last handle detached) - the caller's cue to forget this
    hub so a later reconnect opens a fresh one instead of reusing a dead one."""

    def __init__(self, real_bus, on_closed=None):
        self.real_bus = real_bus
        self.on_closed = on_closed
        self._send_lock = threading.Lock()
        self._handles = set()
        self._handles_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self._thread = threading.Thread(target=self._fan_out, name="sdr-bus-hub", daemon=True)
        self._thread.start()

    def attach(self):
        """-> a new `_AxisHandle` for one more axis on this same physical connection."""
        handle = _AxisHandle(self)
        with self._handles_lock:
            self._handles.add(handle)
        return handle

    def detach(self, handle):
        """Called by a handle's own `shutdown()` - the real connection closes once the LAST handle detaches, not
        before (closing it earlier would cut off every other axis still using this same physical link)."""
        with self._handles_lock:
            self._handles.discard(handle)
            empty = not self._handles
        if empty:
            self._close()

    def send(self, msg):
        with self._send_lock:
            self.real_bus.send(msg)

    def _fan_out(self):
        while not self._stop.is_set():
            try:
                msg = self.real_bus.recv(timeout=0.05)
            except Exception as exc:            # the adapter itself failed - every attached axis hears about it
                self._fail(exc)
                return
            if msg is not None:
                with self._handles_lock:
                    handles = list(self._handles)
                for handle in handles:
                    handle._push(msg)

    def _fail(self, exc):
        with self._handles_lock:
            handles = list(self._handles)
        for handle in handles:
            handle._push(_HubFailed(exc))

    def _close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)
        try:
            self.real_bus.shutdown()
        except Exception:
            pass
        if self.on_closed:
            self.on_closed()


class _AxisHandle:
    """A bus-like object (`send`/`recv`/`shutdown`) for exactly one axis, backed by a `SharedBusHub` - handed to
    `DriveClient(bus=handle, node_id=...)` in place of a real bus. DriveClient needs no changes at all: it already
    only calls these three methods on whatever `self.bus` is."""

    def __init__(self, hub):
        self._hub = hub
        self._queue = queue.SimpleQueue()

    def send(self, msg, timeout=None):
        self._hub.send(msg)

    def recv(self, timeout=None):
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, _HubFailed):
            self._queue.put(item)      # a caller that calls recv() again (it shouldn't, but just in case) sees it too
            raise item.exc
        return item

    def shutdown(self):
        self._hub.detach(self)

    def _push(self, item):
        self._queue.put(item)
