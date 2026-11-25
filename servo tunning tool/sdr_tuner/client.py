"""Thread-safe client for one SDR servo drive on a python-can bus.

* read / write of configuration and telemetry parameters (SDO-style request / response)
* decoding of the telemetry messages (TPDO) into `telemetry` and `history`
* the drive's commands; the speed command (ESC) is re-sent periodically, and the motor is stopped when the client closes
* optional polling of telemetry by request (only the bench tools use it: the GUI just shows the drive's broadcast)
"""
import threading
import time
from collections import defaultdict

import can
import numpy as np

from . import defs, protocol as P

HISTORY_SAMPLES = 20000     # per telemetry channel (~6 min at 50 Hz)
POLES_ADDRESS = (0x2000, 0x03)
MAPPING_INDEXES = (0x1A00, 0x1A01, 0x1A02)     # the telemetry mapping registers of the three telemetry messages
DEVICE_INFO = {"device_type": (0x5000, 0x9F), "serial": (0x5000, 0xA0), "firmware": (0x5000, 0xA1)}   # what activation needs


class _History:
    """One telemetry channel's newest HISTORY_SAMPLES (time, value) pairs in two preallocated numpy rings. Appending
    is two item assignments (the rx thread, once per frame); a snapshot is a slice copy. It was a deque of tuples
    that history_snapshot() rebuilt into an array on every call - milliseconds per curve per tick in the GUI thread
    once the 1000-2000 Hz telemetry rates had filled it (2026-09-27)."""
    __slots__ = ("t", "v", "n", "i")

    def __init__(self, capacity=None):
        capacity = HISTORY_SAMPLES if capacity is None else capacity
        self.t, self.v = np.empty(capacity), np.empty(capacity)
        self.n = self.i = 0                     # samples held; the next write position

    def append(self, t, v):
        self.t[self.i] = t
        self.v[self.i] = v
        self.i = (self.i + 1) % len(self.t)
        if self.n < len(self.t):
            self.n += 1

    def arrays(self):
        """(times, values), oldest first - copies, so a caller reads them outside the lock."""
        if self.n < len(self.t):
            return self.t[:self.n].copy(), self.v[:self.n].copy()
        return np.concatenate((self.t[self.i:], self.t[:self.i])), np.concatenate((self.v[self.i:], self.v[:self.i]))


class DriveError(Exception):
    """The drive (or the link) refused or failed a request."""


class DriveTimeout(DriveError):
    """No answer from the drive."""


class _Waiter:
    def __init__(self, index, sub, errors_only=False):
        self.index, self.sub, self.errors_only = index, sub, errors_only
        self.response = None
        self._event = threading.Event()

    def deliver(self, response):
        self.response = response
        self._event.set()

    def wait(self, timeout):
        return self.response if self._event.wait(timeout) else None


class DriveClient:
    def __init__(self, bus, node_id, *, timeout=0.3, retries=1, write_settle=0.03, esc_period=0.1):
        self.bus, self.node = bus, node_id
        self.timeout, self.retries, self.write_settle, self.esc_period = timeout, retries, write_settle, esc_period
        self.commands = defs.load_commands()
        self.tpdos, self.fault_bit_names = defs.load_telemetry()
        self._tpdo_by_cob = {t.cob: t for t in self.tpdos}
        self.poles = None                       # rotor poles, filled by identify() (for information)
        self.mapping_from_drive = False         # True once load_tpdo_mapping() has read the drive's own telemetry layout
        self.mapping_codes = {}                 # (index, sub-index of a mapping slot) -> the code the drive holds there
        self.tpdo_overflow = []                 # [(cob, slot, reason), ...] set by load_tpdo_mapping() - a mapping slot
                                                 # the drive has configured but that could not be decoded from the
                                                 # broadcast (see load_tpdo_mapping's own docstring)
        self.decode_telemetry = True            # False: broadcast frames are not decoded (used until the layout is known)
        self.mapping_listeners = []             # callables(): the telemetry layout changed (or could not be read again)
        self.device_info = {}                   # device type / serial numbers, filled by read_device_info()
        self.telemetry = {}                     # key -> latest value
        self.last_tpdo = None                   # time.monotonic() of the latest telemetry sample
        self.nodes_seen = set()                 # node IDs heard on the bus (telemetry or read responses)
        self.polled_keys = set()                # telemetry keys currently fed by polling instead of the broadcast
        self.poll_error = None                  # why polling stopped by itself, if it did
        self.poll_hz = None                     # polling rate while polling
        self._poll_thread = None
        self._poll_stop = threading.Event()
        self._history = defaultdict(_History)   # key -> its newest HISTORY_SAMPLES (time, value) pairs
        self._lock = threading.Lock()           # telemetry + history
        self._send_lock = threading.Lock()
        self._sdo_lock = threading.Lock()       # one request in flight at a time
        self._waiter = None
        self._heartbeat = None                  # seconds between the harmless requests that keep the drive from timing out; None = off
        self._last_reset = None                 # when the Reset command was last sent
        self._next_beat = 0.0
        self._esc = None                        # speed command being repeated: (command key, value); None = not commanding
        self._esc_lock = threading.Lock()       # keeps "clear the speed, send Stop" apart from a keep-alive frame in flight
        self._stop = threading.Event()
        self._closed = False
        self.frame_listeners = []               # callables(direction, time, can_id, data)
        self.link_error_listeners = []          # callables(exception)
        self._threads = [threading.Thread(target=self._rx_loop, name="sdr-rx", daemon=True),
                         threading.Thread(target=self._keepalive_loop, name="sdr-esc", daemon=True)]

    # ---- lifecycle -------------------------------------------------------------------------
    def start(self):
        for t in self._threads:
            t.start()

    def close(self):
        """Stops the motor if it is being commanded, then releases the CAN interface."""
        if self._closed:
            return
        self._closed = True
        if self._esc is not None:
            try:
                self._stop_motor()
            except Exception:
                pass
        self.stop_polling()
        self._stop.set()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=1.0)
        try:
            self.bus.shutdown()
        except Exception:
            pass

    # ---- low level -------------------------------------------------------------------------
    def _send(self, can_id, data):
        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=False)
        with self._send_lock:
            try:
                self.bus.send(msg)
            except can.CanError as exc:
                raise DriveError(f"CAN send failed: {exc}") from exc
        self._notify_frame("tx", can_id, data)

    def _notify_frame(self, direction, can_id, data):
        t = time.monotonic()
        for cb in list(self.frame_listeners):
            cb(direction, t, can_id, data)

    def _link_failed(self, exc):
        if self._stop.is_set():
            return
        for cb in list(self.link_error_listeners):
            cb(exc)

    def _rx_loop(self):
        while not self._stop.is_set():
            try:
                msg = self.bus.recv(timeout=0.05)
            except Exception as exc:            # adapter unplugged, driver error, ...
                self._link_failed(exc)
                return
            if msg is None or msg.is_error_frame or msg.is_remote_frame or msg.is_extended_id:
                continue
            self._dispatch(msg.arbitration_id, bytes(msg.data))

    def _dispatch(self, can_id, data):
        self._notify_frame("rx", can_id, data)
        cob, node = P.split_can_id(can_id)
        if cob == P.COB_RESPONSE or cob in self._tpdo_by_cob:
            with self._lock:
                self.nodes_seen.add(node)
        if node != self.node:
            return
        if cob == P.COB_RESPONSE:
            r = P.parse_response(can_id, data)
            w = self._waiter
            if r and w and (r.index, r.sub) == (w.index, w.sub) and (r.error or not w.errors_only):
                w.deliver(r)
        elif cob in self._tpdo_by_cob:
            self._on_tpdo(self._tpdo_by_cob[cob], data.ljust(8, b"\0"))

    def _on_tpdo(self, tpdo, data):
        if not self.decode_telemetry:
            return
        t = time.monotonic()
        values = {f.key: P.decode_value(f.dtype, data[f.offset:f.offset + P.type_size(f.dtype)]) for f in tpdo.fields}
        if self.polled_keys:                                                # polled values are fresher (a set is only
            with self._lock:                                                # ever replaced or changed under the lock;
                polled = set(self.polled_keys)                              # an empty one needs no lock to see)
            values = {k: v for k, v in values.items() if k not in polled}
        if values:
            self._record(values, t)

    def _record(self, values, t):
        """Store fresh telemetry values (from the broadcast or from polling)."""
        with self._lock:
            self.telemetry.update(values)
            self.last_tpdo = t
            for key, value in values.items():
                self._history[key].append(t, value)

    def _keepalive_loop(self):
        while not self._stop.wait(self.esc_period):
            try:
                with self._esc_lock:
                    if self._esc is not None:
                        key, value = self._esc
                        self.send_command(key, *value) if isinstance(value, tuple) else self.send_command(key, value)
                beat, now = self._heartbeat, time.monotonic()
                if beat is not None and now >= self._next_beat:
                    self._next_beat = now + beat
                    self._send(*P.read_request(self.node, *POLES_ADDRESS))       # any message will do: the answer is ignored
            except DriveError as exc:
                self._link_failed(exc)
                return

    # ---- telemetry -------------------------------------------------------------------------
    def telemetry_snapshot(self):
        with self._lock:
            return dict(self.telemetry)

    def telemetry_age(self):
        """Seconds since the latest telemetry sample, or None if none arrived yet."""
        with self._lock:
            return None if self.last_tpdo is None else time.monotonic() - self.last_tpdo

    def history_snapshot(self, key, seconds=None):
        """-> (times, values) numpy arrays; times are time.monotonic() stamps."""
        with self._lock:
            history = self._history.get(key)                # .get: a value never seen makes no history
            if history is None:
                return np.empty(0), np.empty(0)
            t, v = history.arrays()
        if seconds is not None:
            keep = t >= time.monotonic() - seconds
            t, v = t[keep], v[keep]
        return t, v

    # ---- parameters ------------------------------------------------------------------------
    def read_raw(self, index, sub, timeout=None, retries=None):
        timeout = self.timeout if timeout is None else timeout
        retries = self.retries if retries is None else retries
        with self._sdo_lock:
            try:
                for _ in range(retries + 1):
                    self._waiter = w = _Waiter(index, sub)
                    self._send(*P.read_request(self.node, index, sub))
                    response = w.wait(timeout)
                    if response is not None:
                        break
                else:
                    raise DriveTimeout(f"no answer from node {self.node} for 0x{index:04X}:0x{sub:02X}")
            finally:
                self._waiter = None
        if response.error:
            raise DriveError(f"drive returned an error for 0x{index:04X}:0x{sub:02X} "
                             f"(code {response.raw[::-1].hex()})")
        return response

    def read(self, reg):
        response = self.read_raw(reg.index, reg.sub)
        if response.size is not None and response.size < P.type_size(reg.dtype):
            raise DriveError(f"{reg.name}: drive sent {response.size} byte(s), {reg.dtype} needs {P.type_size(reg.dtype)}")
        return response.value(reg.dtype)

    def write(self, reg, value, verify=True):
        """Write a parameter. With verify=True the value is read back and compared. Returns the stored value.
        Writing into a telemetry mapping register changes what the drive broadcasts: nothing more is decoded until the
        layout has been read again."""
        frame = self._write_frame(reg, value)       # read-only / does not fit: raised here, before anything is sent -
                                                     # nothing to reload for a write that never left (it used to run
                                                     # the 12-read mapping reload below anyway, and a timeout in THAT
                                                     # then flagged the mapping unknown although nothing had changed)
        if reg.index not in MAPPING_INDEXES:
            return self._send_write(reg, frame, value, verify)
        was_decoding, self.decode_telemetry = self.decode_telemetry, False
        try:
            return self._send_write(reg, frame, value, verify)
        finally:
            try:
                self.load_tpdo_mapping()
            except DriveError:
                self.mapping_from_drive = False
                self._mapping_changed()
            finally:
                self.decode_telemetry = was_decoding

    def _write_frame(self, reg, value):
        if not reg.writable:
            raise DriveError(f"{reg.name} is read-only")
        return P.write_request(self.node, reg.index, reg.sub, reg.dtype, value)     # ValueError if it does not fit

    def _send_write(self, reg, frame, value, verify):
        with self._sdo_lock:
            self._waiter = w = _Waiter(reg.index, reg.sub, errors_only=True)
            try:
                self._send(*frame)
                rejected = w.wait(self.write_settle)      # only an error reply is acted on; the 0x60 acknowledgement is ignored
            finally:
                self._waiter = None
        if rejected is not None:
            raise DriveError(f"{reg.name}: drive rejected the write (error code {rejected.raw[::-1].hex()})")
        if not verify:
            return value
        expected = P.decode_value(reg.dtype, P.encode_value(reg.dtype, value))
        stored = self.read(reg)
        if stored != expected:
            raise DriveError(f"{reg.name}: wrote {expected!r} but the drive holds {stored!r}")
        return stored

    SAVE_TO_FLASH_FRAME = bytes([0x23, 0x10, 0x10, 0x01]) + b"save"     # SDO expedited download 'save' -> 0x1010:0x01
    SAVE_TO_FLASH_TIMEOUT = 3.0                                          # a flash write can take a moment

    def save_to_flash(self):
        """Makes the drive's whole CURRENT configuration what it loads at power-up. A plain parameter write lives in
        RAM only (measured 2026-09-25: node ID, receiving frequency and the telemetry mapping all reverted at the next
        power cycle - see the README); the only documented save is the automatic tuning's last step. This sends exactly
        what the vendor's own IDE sends for its "Save to Flash" button (found in its machine code, absent from this
        drive's reference): the standard CANopen store request, an expedited SDO download of the four bytes 'save' to
        object 0x1010 sub-index 1 - and waits for the standard acknowledgement (0x60, same index/sub-index), which
        the real drive gives; a write ACTUALLY survived a power cycle after it. Raises DriveError on an abort reply or
        no reply. NEVER sends its twin ('load' to 0x1011:1 = factory defaults)."""
        done, reply = threading.Event(), []
        index_sub = self.SAVE_TO_FLASH_FRAME[1:4]

        def listener(direction, _t, can_id, data):
            if direction == "rx" and can_id == (P.COB_RESPONSE | self.node) and bytes(data[1:4]) == index_sub:
                reply.append(bytes(data))
                done.set()

        with self._sdo_lock:
            self.frame_listeners.append(listener)
            try:
                self._send(P.COB_REQUEST | self.node, self.SAVE_TO_FLASH_FRAME)
                done.wait(self.SAVE_TO_FLASH_TIMEOUT)
            finally:
                self.frame_listeners.remove(listener)
        if not reply:
            raise DriveTimeout(f"no answer from node {self.node} to the save-to-flash request")
        if reply[0][0] != P.RESPONSE_ACK:
            raise DriveError(f"drive refused the save-to-flash request (reply {reply[0].hex(' ')})")
        return True

    def detect_nodes(self, wait=1.0):
        """Node IDs heard on the bus. Waits up to `wait` seconds for the first one."""
        end = time.monotonic() + wait
        while time.monotonic() < end:
            with self._lock:
                if self.nodes_seen:
                    break
            time.sleep(0.02)
        with self._lock:
            return sorted(self.nodes_seen)

    def identify(self):
        """Read the number of rotor poles. The answer also tells that the drive is there. Raises DriveError if it is silent."""
        r = self.read_raw(*POLES_ADDRESS)
        self.poles = r.value("UINT16")
        return self.poles

    def read_device_info(self):
        """Read the drive's Device Type and its two serial numbers (the drive's maker needs them to issue an activation
        key). A number the drive does not give is None."""
        info = {}
        for key, (index, sub) in DEVICE_INFO.items():
            try:
                info[key] = self.read_raw(index, sub).value("UINT32")
            except DriveError:
                info[key] = None
        self.device_info = info
        return info

    def load_tpdo_mapping(self):
        """Read which parameters the drive broadcasts in each telemetry message (its mapping is configurable) and
        decode the broadcast accordingly. Each slot of registers 0x1A00-0x1A02 holds a Telemetry Mapping Code
        (see defs.mapping_code). Raises DriveError if the drive does not answer.

        A configured slot can fail to be placed - an unrecognised mapping code, or simply too many bytes for one
        8-byte CAN frame (2026-09-24: found on the user's own real drive - Fault Bits (2B) + the FLOAT32 DC-bus
        register (4B, from an earlier remap - see the README) + Work Zone Speed (4B) is 10 bytes, over the limit).
        Once one slot in a message cannot be placed, every slot after it in that SAME message is left undecoded
        too (not just skipped) - the real drive's own byte layout for anything after an unplaceable field is not
        something this client can know, so it does not guess (see test_a_slot_the_client_cannot_place_ends_the_
        message). This was already correct and stays unchanged; what was missing is VISIBILITY - previously a
        configured field silently never appeared in telemetry_snapshot(), with nothing to say why. Every such slot
        - the one that overflowed, and any later ones left undecoded because of it - is now recorded in
        `self.tpdo_overflow` as (cob, slot, reason), so a caller (session.py's own connect-time summary, in
        particular) can tell the user their drive's own mapping does not fit, instead of leaving them to notice a
        field is quietly missing."""
        catalog = defs.telemetry_catalog()
        tpdos, codes, overflow = [], {}, []
        for message, cob in enumerate(defs.TPDO_COBS):
            fields, offset, placing = [], 0, True
            for slot in range(1, 5):
                code = self.read_raw(0x1A00 + message, slot).value("UINT32")
                codes[(0x1A00 + message, slot)] = code
                if code == 0:                                       # slot not used
                    continue
                reg = catalog.get(defs.parse_mapping_code(code))
                if not placing:
                    label = reg.name if reg else f"mapping code {code}"
                    overflow.append((cob, slot, f"{label}: not decoded, an earlier slot in this message did not fit"))
                    continue
                if reg is None:
                    overflow.append((cob, slot, f"mapping code {code}: not a recognised telemetry parameter"))
                    placing = False                                 # unknown size: nothing further can be placed either
                    continue
                if offset + P.type_size(reg.dtype) > 8:
                    overflow.append((cob, slot, f"{reg.name}: needs {P.type_size(reg.dtype)} more byte(s) but only "
                                               f"{8 - offset} left in this 8-byte message"))
                    placing = False                                 # no room: nothing further can be placed either
                    continue
                fields.append(defs.tpdo_field(reg, offset))
                offset += P.type_size(reg.dtype)
            tpdos.append(defs.Tpdo(cob, tuple(fields)))
        self.tpdos = tpdos
        self._tpdo_by_cob = {t.cob: t for t in tpdos}
        self.mapping_codes = codes
        self.tpdo_overflow = overflow
        self.mapping_from_drive = True
        self._mapping_changed()
        return tpdos

    def _mapping_changed(self):
        for cb in list(self.mapping_listeners):
            cb()

    # ---- fast telemetry by polling ---------------------------------------------------------
    def start_polling(self, hz=100.0, keys=None, intervals=None):
        """Read the telemetry values by request instead of waiting for the drive's slow broadcast: each value `hz`
        times per second, or every `intervals[key]` seconds for the keys listed there. A value the drive refuses
        to give by request keeps coming from the broadcast."""
        self.stop_polling()
        fields = [f for t in self.tpdos for f in t.fields]
        fields += [f for f in defs.poll_only_fields() if f.key not in {g.key for g in fields}]    # values not in the broadcast
        fields = [f for f in fields if keys is None or f.key in keys]
        periods = {f.key: (intervals or {}).get(f.key, 1.0 / hz) for f in fields}
        self.poll_error = None
        self.poll_hz = hz
        self._poll_stop = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, args=(fields, periods, self._poll_stop),
                                             name="sdr-poll", daemon=True)
        self._poll_thread.start()

    def stop_polling(self):
        thread, self._poll_thread = self._poll_thread, None
        if thread is not None:
            self._poll_stop.set()
            thread.join(timeout=1.0)
        self.poll_hz = None
        with self._lock:
            self.polled_keys = set()

    @property
    def polling(self):
        return self._poll_thread is not None and self._poll_thread.is_alive()

    def _poll_loop(self, fields, periods, stop):
        active, timeouts = list(fields), 0
        due = {f.key: time.monotonic() for f in fields}         # when each value is next to be read
        while not stop.is_set() and not self._stop.is_set() and active:
            now = time.monotonic()
            for f in [f for f in active if due[f.key] <= now]:      # values that are due are read one after the other
                if stop.is_set() or self._stop.is_set():
                    return
                due[f.key] += periods[f.key]
                if due[f.key] < time.monotonic():                   # running late: skip the missed readings
                    due[f.key] = time.monotonic() + periods[f.key]
                try:
                    value = self.read_raw(f.index, f.sub, timeout=0.1, retries=0).value(f.dtype)
                except DriveTimeout:
                    timeouts += 1
                    if timeouts >= 5:
                        self.poll_error = "the drive stopped answering"
                        with self._lock:
                            self.polled_keys = set()
                        return
                    continue
                except DriveError:                  # refused by request (error response): the broadcast keeps it
                    active.remove(f)
                    with self._lock:
                        self.polled_keys.discard(f.key)
                    continue
                timeouts = 0
                with self._lock:
                    self.polled_keys.add(f.key)
                self._record({f.key: value}, time.monotonic())
            if active:
                stop.wait(max(min(due[f.key] for f in active) - time.monotonic(), 0.0))

    # ---- commands --------------------------------------------------------------------------
    def send_command(self, key, *values):
        cmd = self.commands[key]
        if not cmd.defined:
            raise DriveError(f"command '{cmd.name}' is not in the device reference yet (fill it in commands.json)")
        if not cmd.supported:
            raise DriveError(f"command '{cmd.name}' uses a data type this tool cannot encode yet (FLOAT24)")
        if len(values) != len(cmd.params):
            raise DriveError(f"command '{cmd.name}' needs {len(cmd.params)} value(s), got {len(values)}")
        params = [(p.offset, p.dtype, v) for p, v in zip(cmd.params, values)]
        self._send(*P.command_frame(self.node, cmd.cob, cmd.code, params))

    @property
    def commanded(self):
        """(command key, value) of the continuous command being repeated - the speed, position-hold/move, current or
        torque command (value: a number) or one of the drive's step-response tests (value: a (period, amplitude, wave
        form) tuple) - or None if nothing is."""
        return self._esc

    def hold_position(self, target_counts):
        """Commands the drive into Servo Control (the plain 'servo' command, COB 0x300 code 0x31), targeting
        `target_counts` (a Work Zone Count) - holds the motor still wherever `target_counts` is (used by Enable, when
        that already is the current position) or moves it there (Move Absolute / Move Relative, when it is not).
        Re-sent every `esc_period` seconds, like the speed command (and ends whatever speed command or test was
        running), until stop() / off() / reset(). Raises ValueError if the value does not fit an INT32."""
        self._command_speed("servo", target_counts)

    def set_speed_rpm(self, rpm):
        """Command a motor speed in rpm (the drive's ESC RPM command: the drive converts it with its Poles Number).
        Re-sent every `esc_period` seconds until stop() / off() / reset()."""
        self._command_speed("esc_rpm", rpm)

    def set_speed_hz(self, hz):
        """Command an electrical speed (ESC, Hz). Re-sent every `esc_period` seconds until stop() / off() / reset()."""
        self._command_speed("esc_hz", hz)

    def set_current(self, amps):
        """Commands a steady current, in the drive's own FOC current loop (the 'current_control_field_oriented_control_foc'
        command). Re-sent every `esc_period` seconds until stop() / off() / reset()."""
        self._command_speed("current_control_field_oriented_control_foc", amps)

    def set_torque(self, newton_metres):
        """Commands a steady torque (the drive's Electronic Torque Control command, which converts it to a current
        internally). Re-sent every `esc_period` seconds until stop() / off() / reset()."""
        self._command_speed("electronic_torque_control_etc", newton_metres)

    def kickstart(self, speed_hz):
        """Commands Kickstart (COB 0x200 code 0x58) - an alternate startup acceleration technique for a
        SENSORLESS brushless motor: gets it moving toward `speed_hz` (electrical Hz) using a control technique
        that does not need to know the rotor's position, tuned via the drive's own Control Laws (not exposed
        here). Re-sent every `esc_period` seconds until stop() / off() / reset()."""
        self._command_speed("kickstart", speed_hz)

    def generate_power(self, voltage):
        """Commands Power Generation (COB 0x200 code 0x60) - turns the servo drive into a power generator's
        electronic control unit, producing `voltage` (V) as an output DC voltage - must be enough to charge a
        battery through a BMS (battery management system), if one is used. Re-sent every `esc_period` seconds
        until stop() / off() / reset()."""
        self._command_speed("power_generation", voltage)

    def brushed_open_loop(self, voltage_1, voltage_2=0.0):
        """Commands Brushed: Open Loop Control (COB 0x300 code 0x90) - direct open-loop voltage control of 1-2
        brushed motors or solenoids, independently. Motor/solenoid #1 is wired to terminals A/B, #2 (if present)
        to C/B (they share terminal B) - `voltage_2` defaults to 0.0 for a single-motor setup. Both positive and
        negative voltages are supported (both directions). Re-sent every `esc_period` seconds until stop() /
        off() / reset(). Raises ValueError if a value does not fit FLOAT16."""
        with self._esc_lock:
            self.send_command("brushed_open_loop_control_1_2_motors", voltage_1, voltage_2)
            self._esc = ("brushed_open_loop_control_1_2_motors", (float(voltage_1), float(voltage_2)))

    def _command_speed(self, key, value):
        with self._esc_lock:
            self.send_command(key, value)       # raises ValueError for NaN / inf / out of range
            self._esc = (key, float(value))

    def hold_modulo_position(self, target_modulo, direction=3):
        """Commands the drive into Servo: Modulo (COB 0x300 code 0x32) - a single-turn CYCLIC target position, for
        a rotary actuator that keeps spinning past its own Work Zone Count range rather than hold_position()'s
        multi-turn absolute one. `direction`: 0 normal (same route hold_position() would take), 1 always negative,
        2 always positive, 3 shortest cyclic route (the drive's own default choice for this command). Re-sent every
        esc_period, like hold_position(). Raises ValueError if a value does not fit its type."""
        with self._esc_lock:
            self.send_command("servo_modulo", target_modulo, direction)
            self._esc = ("servo_modulo", (float(target_modulo), float(direction)))

    def hold_turns_and_modulo_position(self, target_modulo, target_turns):
        """Commands the drive into Servo: Turns and Modulo (COB 0x300 code 0x34) - the same kind of multi-turn
        target hold_position() takes as one Work Zone Count, but split into a single-turn (modulo) part and a
        whole-turns part instead. Re-sent every esc_period, like hold_position(). Raises ValueError if a value
        does not fit its type."""
        with self._esc_lock:
            self.send_command("servo_turns_and_modulo", target_modulo, target_turns)
            self._esc = ("servo_turns_and_modulo", (float(target_modulo), float(target_turns)))

    def set_direct_field_rotation(self, voltage, speed_hz):
        """Commands Direct Field Control: Rotation (COB 0x200 code 0x40) - an OPEN-LOOP mode that rotates a
        magnetic field inside the motor's coils at `speed_hz` (electrical Hz) using `voltage` (V); the rotor's
        permanent magnets follow the field rather than being driven by closed-loop commutation (FOC/ESC). Less
        efficient and can heat the motor - useful for e.g. gyro-stabilization. ATTENTION, per the reference: even
        a small voltage (0.2-0.3 V) can burn a low-resistance brushless motor - start low (~0.1 V) and raise
        gradually while watching phase current against Maximum Continuous Current. Re-sent every `esc_period`
        seconds until stop() / off() / reset(). Raises ValueError if a value does not fit its type."""
        with self._esc_lock:
            self.send_command("direct_field_control_rotation", voltage, speed_hz)
            self._esc = ("direct_field_control_rotation", (float(voltage), float(speed_hz)))

    def set_direct_field_position(self, voltage, electrical_position_rad):
        """Commands Direct Field Control: Electrical Position (COB 0x200 code 0x44) - an OPEN-LOOP mode that holds
        a STATIC magnetic field at `electrical_position_rad` using `voltage` (V); the rotor aligns with and holds
        against this static field. Same efficiency/heating caveats and voltage warning as
        set_direct_field_rotation(). Re-sent every `esc_period` seconds until stop() / off() / reset(). Raises
        ValueError if a value does not fit its type."""
        with self._esc_lock:
            self.send_command("direct_field_control_electrical_position", voltage, electrical_position_rad)
            self._esc = ("direct_field_control_electrical_position", (float(voltage), float(electrical_position_rad)))

    def engage_virtual_brake(self):
        """Commands Virtual Brake (COB 0x200 code 0x50, no parameters) - uses the motor itself to resist unwanted
        motion from external forces, like a brake, but without an actual physical braking device; draws no
        current while nothing is trying to move the shaft. Works best with Hall sensors or a Motor Encoder (to
        detect real motion and counter it); without either, the drive defaults to holding a statically positioned
        field instead (the same idea as Direct Field Control: Electrical Position), which draws current
        continuously and can heat a sensorless motor - the reference calls for extra care in that case, but does
        not say the command is meaningless without feedback (unlike the plain Servo command's Work Zone Count),
        so this is not gated behind a Feedback Settings check the way Position Control is. Re-sent every
        `esc_period` seconds until stop() / off() / reset()."""
        with self._esc_lock:
            self.send_command("virtual_brake")
            self._esc = ("virtual_brake", ())

    def move_linear(self, target_counts, feed_rate, extra_bits=0):
        """Commands a linear move to `target_counts` (a Work Zone Count) at `feed_rate` (counts/sec) - the drive's
        Servo: Linear Interpolation command. Re-sent every `esc_period` seconds, like the plain servo command, until
        stop() / off() / reset() or a new command. Raises ValueError if a value does not fit its type."""
        with self._esc_lock:
            self.send_command("servo_linear_interpolation", target_counts, feed_rate, extra_bits)
            self._esc = ("servo_linear_interpolation", (float(target_counts), float(feed_rate), int(extra_bits)))

    def end_repeat(self):
        """Stops repeating whatever continuous command is running (speed, position hold/move, current, torque)
        WITHOUT sending anything else - the drive is simply no longer told to keep going, and (per its own
        configuration) is left to time out on its own heartbeat and stop, brake or free-wheel on its own. Prefer
        stop() / off() / reset() when an immediate, definite drive-side response is wanted instead."""
        with self._esc_lock:
            self._esc = None

    def run_test(self, key, period, amplitude, wave_form=0):
        """Runs one of the drive's built-in step-response tests - key is 'testing_field_oriented_control_foc' (current loop),
        'testing_electronic_speed_control_esc' (speed loop), 'testing_servo_control' (servo loop) or
        'testing_iq_current_control' - a step of `amplitude` (units depend on the test) toggling sign every `period` seconds.
        Re-sent every `esc_period` seconds, like the speed command (and ends whatever speed command was running), until
        stop() / off() / reset(). Raises ValueError for a value the command's data types cannot hold."""
        if float(period) < 1.0 / P.FIXED_POINT_SCALE:            # the Period is fixed point in 1/256 s: raw 0 is no period at all
            raise ValueError(f"period must be at least {1.0 / P.FIXED_POINT_SCALE:.5f} s (the drive counts it in 1/256 s)")
        with self._esc_lock:
            self.send_command(key, period, amplitude, wave_form)
            self._esc = (key, (float(period), float(amplitude), int(wave_form)))

    def set_heartbeat(self, period=None):
        """While a long procedure runs on the drive (the automatic configuration) it must hear from the host at least every
        `CANopen: heartbeat timeout` (3.5 s by default), or it stops what it is doing. This sends a harmless read request every
        `period` seconds, so nothing that could start the procedure again is repeated. None switches it off."""
        self._next_beat = time.monotonic()
        self._heartbeat = period

    def _command_and_forget_speed(self, key):
        """Ends the periodic speed command, then sends `key` (no keep-alive frame can follow it and undo it)."""
        with self._esc_lock:
            self._esc = None
            self.send_command(key)

    def _stop_motor(self):
        self._command_and_forget_speed("stop")

    def stop(self):
        """Controlled stop (the drive's Stop command) and end of the periodic speed command."""
        self._stop_motor()

    def off(self):
        """Switch the power stage off (the motor coasts)."""
        self._command_and_forget_speed("off")

    def reset(self):
        """Clear a latched fault, cut the current supply to the motor and clear the work-zone count. Ends the periodic speed command, so
        the motor does not restart. It is also the command that ends the drive's automatic configuration (the configuration mode):
        Stop works in the velocity, position and other modes, but not there."""
        self._command_and_forget_speed("reset")
        self._last_reset = time.monotonic()

    @property
    def reset_age(self):
        """Seconds since a Reset was last sent, or None if never."""
        return None if self._last_reset is None else time.monotonic() - self._last_reset

    def reset_work_zone(self):
        """Zeroes the Work Zone Count at wherever the motor is right now (the drive's Reset Work Zone command) - this
        drive has no automatic homing sequence of its own: jog to the reference point with a speed command, then call
        this. Sends a Stop first and ends the periodic speed command, so the motor is not still moving (or a position
        hold/move is not left targeting its old, now-wrong absolute count) when the reference point changes."""
        with self._esc_lock:
            self._esc = None
            self.send_command("stop")
            self.send_command("reset_work_zone")

    def set_electromagnetic_brake(self, engaged):
        """Commands the Electromagnetic Brake (COB 0x400 code 0x52) - engages or disengages a brake by changing
        PWM-modulated voltage on a solenoid port. Not a continuous command - the reference does not ask for it to
        be repeated, so this sends it once and does not touch the periodic speed command."""
        self.send_command("electromagnetic_brake", 1 if engaged else 0)

    def set_gpio_output(self, high):
        """Commands GPIO: Generic Output (COB 0x400 code 0xA0) - sets a dedicated GPIO output pin to 0 or 1. Not
        a continuous command - sent once."""
        self.send_command("gpio_generic_output", 1 if high else 0)

    def set_gpio_pwm(self, duty_cycle):
        """Commands GPIO: PWM output (COB 0x200 code 0xA4) - sets the duty cycle (0.0 fully open - 1.0 fully
        closed) of a PWM signal on a dedicated GPIO output pin, typically used for solenoids/brakes wired to that
        pin. Not a continuous command - sent once. Raises ValueError if the value does not fit FLOAT32."""
        self.send_command("gpio_pwm_output", duty_cycle)
