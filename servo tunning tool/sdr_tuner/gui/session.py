"""Owns the CAN connection (real adapter or built-in simulator) for the whole GUI."""
import queue

import can
from PySide6.QtCore import QObject, Signal

from .. import kinematics
from ..client import DriveClient, DriveError
from ..shared_bus import SharedBusHub
from ..simulator import SimulatedDrive
from ..usb import UsbSerialBus
from .workers import run_async

SIMULATOR = "Simulator (no hardware)"
USB = "USB cable to the drive"
SEARCH_PORTS = "Search available ports"
SIM_CHANNEL = "sdr-gui-simulator"
SIM_TPDO_HZ = 3.0    # each of the 3 telemetry messages, per second - matches the real drive's own default (~9 messages/s total).
                     # `SimulatedDrive` itself defaults to 100 Hz, chosen so the test suite does not have to wait for samples;
                     # that speed is 30x too fast for real use - it fills the 20000-sample history in ~3 minutes instead of
                     # ~2 hours, after which every Recorder tick and the motion page's own plots redraw the maximum history
                     # on every refresh. Tests restore the fast rate via the `_fast_simulated_telemetry` fixture.
INTERFACES = {      # label -> python-can interface name
    SIMULATOR: None,
    USB: "usb",
    "PCAN (PEAK-System)": "pcan",
    "Kvaser": "kvaser",
    "CANable / slcan (serial)": "slcan",
    "SocketCAN (Linux)": "socketcan",
    "Vector": "vector",
}
DEFAULT_CHANNEL = {"usb": "", "pcan": "PCAN_USBBUS1", "kvaser": "0", "slcan": "/dev/tty.usbmodem1101",
                   "socketcan": "can0", "vector": "0"}
BITRATES = [(125_000, "125 kbit/s"), (250_000, "250 kbit/s"), (500_000, "500 kbit/s"), (1_000_000, "1 Mbit/s")]


class AxisContext(QObject):
    """A per-axis facade for one ADDITIONAL axis (`Session.connect_axis`), shaped exactly like `Session` itself
    for the attributes/signals a single-axis page (DriveTab, ParamsTab, ...) already reads and connects to
    (`.client`, `.node_id`, `.is_simulated`, `.interface`, `.is_connected`, and the connected/probed/
    mapping_changed/disconnecting/disconnected signals) - so an existing page class can be given one of these in
    place of the whole `Session` and work completely unmodified, bound to this one specific axis instead of the
    primary connection. Built and owned by `Session.connect_axis`/`disconnect_axis`, not constructed directly."""
    connected = Signal()
    disconnecting = Signal()
    disconnected = Signal()
    probed = Signal(bool, str)
    node_detected = Signal(int)     # never emitted today (connect_axis never guesses a node - see its own docstring)
    mapping_changed = Signal()

    def __init__(self, client, node_id, is_simulated, interface):
        super().__init__()
        self.client = client
        self.node_id = node_id
        self.is_simulated = is_simulated
        self.interface = interface

    @property
    def is_connected(self):
        return self.client is not None


class Session(QObject):
    connected = Signal()
    disconnecting = Signal()        # the connection is about to be closed (a page can still send the drive something)
    disconnected = Signal()
    error = Signal(str)             # could not connect
    link_lost = Signal(str)         # adapter failed while connected
    probed = Signal(bool, str)      # did the drive answer? (ok, message)
    node_detected = Signal(int)     # the drive was found on a different node ID than the one selected
    mapping_changed = Signal()      # the drive's telemetry layout was (re)read: what the broadcast contains may differ now
    axis_connected = Signal(int)    # an ADDITIONAL axis (connect_axis, not connect_drive) came up - its node ID
    axis_disconnected = Signal(int)
    axis_error = Signal(int, str)   # an additional axis could not connect, or its link failed - its node ID, message
    kinematics_changed = Signal()   # a .kinematics model was loaded, or cleared

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = None
        self.node_id = None
        self.is_simulated = False
        self.interface = None
        self._sim = None
        self.frames = queue.SimpleQueue()       # (direction, time, can_id, data) for the CAN log tab
        self.axes = {}                          # node id -> DriveClient, for axes ADDED via connect_axis (see below) -
                                                 # the primary connection (connect_drive) is NOT included here, it stays
                                                 # exactly `self.client`/`self.node_id`, unchanged from before multi-axis
        self._axis_sims = {}                    # node id -> SimulatedDrive, for additional simulated axes only -
                                                 # mirrors self._sim (the primary's own), needed so disconnect_axis
                                                 # can stop that axis's own simulator thread, not just its client
        self._hubs = {}                         # (interface, channel, bitrate) -> SharedBusHub, real adapters only -
                                                 # lets a second/third axis join the SAME physical connection instead
                                                 # of failing to open the same port/channel twice
        self.kinematics_model = None            # the loaded sdr_tuner.kinematics.KinematicModel, or None -
                                                 # what a G-code Txx (Inverse Kinematics) line solves against
        self.counts_per_unit = {}               # axis index (0-based - NOT the CAN node id directly, see
                                                 # gcode_runner.py's own _axis_to_node()) -> raw Work Zone Counts
                                                 # per kinematics unit (degrees for a rotary joint, mm for a linear
                                                 # one) - the one number this tool cannot derive on its own (see
                                                 # kinematics.py's own docstring): default 1.0 for any axis not set
        self.virtual_axes = {}                  # axis index -> gcode_sim.VirtualAxis, the 3D Simulation page's own
                                                 # OFFLINE "Simulation" mode (no drive, real or simulated, needed at
                                                 # all) - written by gcode_sim.VirtualGcodeRunner as a program runs,
                                                 # read by gui/sim3d.py's own Sim3DPage every tick; empty = every
                                                 # axis at its home pose (never explicitly populated with 0s)

    @property
    def is_connected(self):
        return self.client is not None

    def load_kinematics(self, path):
        """Loads a .kinematics file (sdr_tuner.kinematics's own vendor CSV format - see its own module docstring)
        as the model a G-code Txx line solves against. Raises kinematics.KinematicsFileError - meant to be shown
        to the user as-is - if the file cannot be read as one; the previously loaded model (if any) is kept in
        that case. Resets counts_per_unit to 1.0 for every axis the new model's chains use - a different model
        means different joints, so a stale factor from a PREVIOUS model would silently mean the wrong thing.
        Clears virtual_axes for the same reason (gcode_sim.py's own offline "Simulate" state, sim3d.py's own
        Simulation mode) - axis 0 in the new model is not necessarily the same joint axis 0 named in whichever
        model was loaded before it."""
        model = kinematics.parse_kinematics_file(path)
        self.kinematics_model = model
        axes = {axis for chain in model.chains().values() for axis in chain.axes}
        self.counts_per_unit = {axis: 1.0 for axis in axes}
        self.virtual_axes = {}
        self.kinematics_changed.emit()
        return model

    def clear_kinematics(self):
        self.kinematics_model = None
        self.counts_per_unit = {}
        self.virtual_axes = {}
        self.kinematics_changed.emit()

    def all_clients(self):
        """{node id: DriveClient} of EVERY connected axis - the primary connection and every additional one added
        via connect_axis - the shape multi-axis G-code dispatch needs (gcode_runner.py) to find whichever axis(es)
        a line actually names, rather than only ever checking the one primary connection."""
        result = {}
        if self.client is not None:
            result[self.node_id] = self.client
        for node_id, ctx in self.axes.items():
            if ctx.client is not None:
                result[node_id] = ctx.client
        return result

    def _open_axis_bus(self, interface, channel, bitrate, node_id):
        """-> (bus-like object, SimulatedDrive or None, SharedBusHub or None). Raises whatever the underlying open
        call raises. The Simulator gets its own independent bus handle per axis (python-can's virtual backend
        already fans out several handles on the same channel string correctly - no hub needed, confirmed directly).
        USB is the drive's own dedicated cable, always exactly one drive - callers must not offer it for a second
        axis. Every other interface shares ONE real connection per (interface, channel, bitrate), through a
        SharedBusHub - reused across axes so a second/third axis does not try to open the same port/channel twice."""
        sim = None
        try:
            if interface == SIMULATOR:
                sim = SimulatedDrive(SIM_CHANNEL, node_id=node_id)
                sim.autoconfig_seconds = 6.0                    # a tuning that lasts long enough to watch
                sim.params[(0x3000, 0x05)] = 3.0 * SIM_TPDO_HZ  # publish telemetry at a realistic rate, not the fast test default
                bus = can.Bus(interface="virtual", channel=SIM_CHANNEL)
                return bus, sim, None
            if interface == USB:
                return UsbSerialBus(channel), None, None
            key = (interface, channel, bitrate)
            hub = self._hubs.get(key)
            if hub is None:
                real_bus = can.Bus(interface=INTERFACES[interface], channel=channel, bitrate=bitrate)
                hub = SharedBusHub(real_bus, on_closed=lambda: self._hubs.pop(key, None))
                self._hubs[key] = hub
            return hub.attach(), None, hub
        except Exception:
            if sim:
                sim.close()
            raise

    def connect_drive(self, interface, channel, bitrate, node_id):
        if self.client is not None:
            return
        try:
            bus, sim, _hub = self._open_axis_bus(interface, channel, bitrate, node_id)
        except Exception as exc:    # noqa: BLE001 - shown to the user
            self.error.emit(f"Cannot open the CAN interface '{interface}'.\n\n{exc}")
            return
        client = DriveClient(bus, node_id)
        client.decode_telemetry = False         # the broadcast is not decoded before the drive's own layout has been read
        client.mapping_listeners.append(lambda: self.mapping_changed.emit())
        client.frame_listeners.append(lambda *frame: self.frames.put(frame))
        client.link_error_listeners.append(lambda exc: self.link_lost.emit(str(exc)))
        client.start()
        self.client, self._sim, self.node_id, self.is_simulated = client, sim, node_id, sim is not None
        self.interface = interface
        self.connected.emit()
        run_async(lambda emit, cancel: self._probe(client, emit, interface == USB),
                  on_done=lambda poles: self._probed(client, True, self._probe_summary(client, poles)),
                  on_failed=lambda exc: self._probed(client, False, str(exc)),
                  on_progress=lambda node: self._node_found(client, node))

    def connect_axis(self, interface, channel, bitrate, node_id):
        """Connects an ADDITIONAL axis (a second, third, ... servo drive) alongside the primary connection
        (`self.client`) - real multi-axis: several drives on one shared CAN bus, coordinated together (see
        the README's Multi-axis section). The primary connection must already be up (there is nothing to "add" to
        otherwise); the drive's own dedicated USB cable reaches exactly one drive and always refuses a second axis,
        real hardware or not - use a shared CAN adapter (PCAN, Kvaser, CANable, SocketCAN, Vector) instead."""
        if self.client is None:
            self.axis_error.emit(node_id, "Connect the first drive before adding another axis.")
            return None
        if node_id == self.node_id or node_id in self.axes:
            self.axis_error.emit(node_id, f"Axis {node_id} is already connected.")
            return None
        if interface == USB:
            self.axis_error.emit(node_id, "The drive's own USB cable reaches exactly one drive - use a shared CAN "
                                          "adapter (PCAN, Kvaser, CANable, SocketCAN, Vector) to add another axis.")
            return None
        try:
            bus, sim, _hub = self._open_axis_bus(interface, channel, bitrate, node_id)
        except Exception as exc:    # noqa: BLE001 - shown to the user
            self.axis_error.emit(node_id, f"Cannot connect axis {node_id}: {exc}")
            return None
        client = DriveClient(bus, node_id)
        client.decode_telemetry = False
        ctx = AxisContext(client, node_id, sim is not None, interface)
        client.mapping_listeners.append(lambda: ctx.mapping_changed.emit())
        client.frame_listeners.append(lambda *frame: self.frames.put(frame))
        client.link_error_listeners.append(lambda exc: self.axis_error.emit(node_id, str(exc)))
        client.start()
        self.axes[node_id] = ctx
        if sim is not None:
            self._axis_sims[node_id] = sim
        # `axis_connected` fires BEFORE `ctx.connected` (and well before the probe below resolves), so a listener
        # that builds this axis's own page in reaction to `axis_connected` (main_window.py) has already subscribed
        # to `ctx.connected`/`ctx.probed` by the time either one actually fires - it misses nothing.
        self.axis_connected.emit(node_id)
        ctx.connected.emit()
        run_async(lambda emit, cancel: self._probe(client, emit, on_usb=False),
                  on_done=lambda poles: self._axis_probed(ctx, True, self._probe_summary(client, poles)),
                  on_failed=lambda exc: self._axis_probed(ctx, False, str(exc)),
                  on_progress=lambda node: None)
        return ctx

    def _axis_probed(self, ctx, ok, message):
        if self.axes.get(ctx.node_id) is ctx:      # not stale (this axis was not disconnected meanwhile)
            ctx.probed.emit(ok, message)

    def disconnect_axis(self, node_id):
        """Disconnects one additional axis (added via connect_axis) - the primary connection (disconnect_drive) is
        entirely separate and unaffected, and vice versa."""
        ctx = self.axes.pop(node_id, None)
        if ctx is None:
            return
        ctx.disconnecting.emit()
        client, ctx.client = ctx.client, None      # ctx.is_connected is False from here on, like Session's own
        client.close()      # stops that axis's motor if it was being commanded, and releases its own bus handle
        sim = self._axis_sims.pop(node_id, None)
        if sim is not None:
            sim.close()
        ctx.disconnected.emit()
        self.axis_disconnected.emit(node_id)

    @staticmethod
    def _probe(client, emit, on_usb):
        """Read the drive's pole count and its telemetry layout (which parameters each broadcast message carries).

        On a USB cable - a dedicated link to exactly one drive - the node is never guessed: the user is not expected to
        know it, so the client always listens for whatever node is broadcasting telemetry (it always is) and uses that.
        On a shared CAN bus, several drives can be on the same wire, so the node the user configured is tried first, and
        the client only switches away from it if it is silent while exactly one other node is talking.

        The broadcast is decoded only once the layout is known."""
        try:
            try:
                if on_usb:
                    raise DriveError("no answer yet")           # skip straight to listening: never guess on a dedicated cable
                poles = client.identify()
                Session._load_mapping(client)
                client.read_device_info()
                return poles
            except DriveError as exc:
                nodes = client.detect_nodes(wait=1.0)
                candidates = nodes if on_usb else [n for n in nodes if n != client.node]
                if len(candidates) != 1:
                    if on_usb:
                        message = ("Nothing is talking on this cable. Check that it is plugged in and the drive is powered." if not nodes
                                  else f"More than one node is talking on this cable: {', '.join(map(str, nodes))}.")
                    else:
                        heard = f" Nodes heard on this bus: {', '.join(map(str, nodes))}." if nodes else " Nothing is talking on this bus."
                        message = f"{exc}.{heard}"
                    raise DriveError(message) from exc
                if candidates[0] != client.node:
                    client.node = candidates[0]
                    emit(client.node)
                poles = client.identify()
                Session._load_mapping(client)
                client.read_device_info()
                return poles
        finally:
            client.decode_telemetry = True      # with the drive's layout, or (if it cannot be read) the reference default

    @staticmethod
    def _load_mapping(client):
        """Which parameters the drive broadcasts is configurable: read it. If the drive cannot say, the reference's default
        layout stays in use and client.mapping_from_drive stays False (the window says so)."""
        try:
            client.load_tpdo_mapping()
        except DriveError:
            pass

    @staticmethod
    def _probe_summary(client, poles):
        if not client.mapping_from_drive:
            return f"{poles} rotor poles; the drive's telemetry layout could NOT be read, the reference default is assumed"
        summary = f"{poles} rotor poles; telemetry layout read from the drive"
        if client.tpdo_overflow:                # 2026-09-24: make a drive-side mapping overflow visible, not silent -
                                                  # see load_tpdo_mapping's own docstring for why this can happen
            worst = client.tpdo_overflow[0]
            more = f" (+{len(client.tpdo_overflow) - 1} more)" if len(client.tpdo_overflow) > 1 else ""
            summary += f"; WARNING - this drive's own telemetry mapping does not fit an 8-byte message: {worst[2]}{more}"
        return summary

    def _node_found(self, client, node):
        if client is self.client:
            self.node_id = node
            self.node_detected.emit(node)

    def _probed(self, client, ok, message):
        if client is self.client:
            self.probed.emit(ok, message)

    def disconnect_drive(self):
        """Disconnects the primary connection AND every additional axis (connect_axis) - there is not yet a way in
        the UI to disconnect one extra axis on its own while keeping the rest (Stage 2), so a full Disconnect (or
        closing the window) must not leave any axis's threads/simulator running behind it."""
        client, sim = self.client, self._sim
        if client is None:
            return
        self.disconnecting.emit()
        self.client = self._sim = None
        client.close()              # stops the motor if it is being commanded
        if sim:
            sim.close()
        for node_id in list(self.axes):
            self.disconnect_axis(node_id)
        self.disconnected.emit()
