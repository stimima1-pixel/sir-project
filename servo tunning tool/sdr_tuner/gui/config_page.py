"""System Configuration > Item Configuration: what the drive is, and how this tool reaches it."""
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import defs
from ..client import DriveError, DriveTimeout
from .params_tab import RISKY, make_editor
from .session import BITRATES, INTERFACES, SEARCH_PORTS, USB
from .tuner import read_registers, same_value
from .widgets import ComboBox, DoubleSpinBox, SpinBox, make_button, set_prop
from .workers import run_async

UNKNOWN = "Unknown"
KINEMATICS_SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "kinematics_samples"
KINEMATICS_FILE_FILTER = "Kinematics models (*.kinematics);;All files (*)"


class PropertyGroup(QFrame):
    """A group of 'name | value' rows under a header that folds them away."""

    def __init__(self, title):
        super().__init__()
        self.setObjectName("gridGroup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = QPushButton()
        self.header.setObjectName("gridHeader")
        self.header.setText(f"−   {title}")
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setToolTip("Fold or unfold the group")
        outer.addWidget(self.header)
        self.rows = QFrame()
        self.rows_layout = QVBoxLayout(self.rows)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        outer.addWidget(self.rows)
        self._title = title
        self.header.toggled.connect(self._toggled)

    def add_row(self, name, widget):
        row = QFrame()
        row.setObjectName("gridRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(12, 3, 12, 3)
        line.setSpacing(0)
        label = QLabel(name)
        label.setObjectName("gridKey")
        line.addWidget(label, 1)
        divider = QFrame()                          # a real column-height rule, not a border on the label
        divider.setObjectName("gridDivider")         # (a border would only be as tall as the label's own text) -
        divider.setFixedWidth(1)                     # EAS3's own Item Configuration is a literal two-column
        line.addWidget(divider)                      # property grid; this is the closest match that keeps the
        line.addSpacing(9)                            # black+gold theme rather than copying its light grey look
        line.addWidget(widget, 1)
        self.rows_layout.addWidget(row)
        return label

    def add_note(self, text):
        note = QLabel(text)
        note.setObjectName("gridKey")
        set_prop(note, "muted", True)
        note.setWordWrap(True)
        row = QFrame()
        row.setObjectName("gridRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(12, 5, 12, 5)
        line.addWidget(note)
        self.rows_layout.addWidget(row)

    def _toggled(self, open_):
        self.rows.setVisible(open_)
        self.header.setText(f"{'−' if open_ else '+'}   {self._title}")
        # Folding/unfolding changes this group's height, which every sibling group below it in the scroll
        # area's column needs to reflow around. Qt normally does that via an async LayoutRequest event, which
        # can lag a paint by a frame or two (visible as a stale blank gap where the folded content used to be,
        # especially while other timers are keeping the event loop busy) - force it through immediately instead.
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().activate()


class _Row:
    def __init__(self, reg, widget, get, set_, label):
        self.reg, self.widget, self.get, self.set, self.label = reg, widget, get, set_, label
        self.last = None            # the value the drive holds (None: not read yet)
        self.touched = False        # the user edited it, so an empty or wrong entry is an error rather than 'not read yet'
        self.refused = ""           # why the drive last refused this write


class ParameterGroup(PropertyGroup):
    """A PropertyGroup of drive parameters: the drive's own persisted settings, not the client-side settings that connect to
    it. Nothing is read until the user presses **Read** (connecting reads only the pole count, the telemetry mapping and the
    device identity - see the README); written only with **Write**, which asks for a confirmation."""
    message = Signal(str, str)      # (text, level) for the window's status bar

    def __init__(self, title, session, keys, note="", title_text="Write parameters"):
        super().__init__(title)
        self.s = session
        self._title_text = title_text
        if note:
            self.add_note(note)
        regs = {r.key: r for r in defs.load_registers()}
        self.param_rows = {}
        for key in keys:
            reg = regs[key]
            widget, get, set_ = make_editor(reg, lambda k=key: self._edited(k), on_submit=lambda: self.write_changed())
            label = self.add_row(reg.name, widget)
            self.param_rows[key] = _Row(reg, widget, get, set_, label)
        buttons = QFrame()
        buttons.setObjectName("gridRow")
        row = QHBoxLayout(buttons)
        row.setContentsMargins(12, 6, 12, 8)
        row.setSpacing(10)
        self.read_btn = QPushButton("Read")
        self.write_btn = make_button(QPushButton("Write"), "primary")
        self.revert_btn = QPushButton("Revert")
        for w in (self.read_btn, self.write_btn, self.revert_btn):
            row.addWidget(w)
        row.addStretch(1)
        self.rows_layout.addWidget(buttons)
        self._sync()

        self.read_btn.clicked.connect(lambda _c=False: self.read_now())
        self.write_btn.clicked.connect(lambda _c=False: self.write_changed())
        self.revert_btn.clicked.connect(lambda _c=False: self.revert())
        self.s.probed.connect(self._on_probed)
        self.s.disconnected.connect(self._on_disconnected)

    # ---- state ------------------------------------------------------------------------------
    def _edited(self, key):
        row = self.param_rows[key]
        row.touched, row.refused = True, ""
        self._paint(row)
        self._sync()

    def _state(self, row):
        if row.refused:
            return "error", row.refused
        if not row.touched and row.last is None:
            return "", ""
        try:
            value = row.get()
        except ValueError as exc:
            return ("error", str(exc)) if row.touched else ("", "")
        return ("dirty", "") if row.last is None or not same_value(row.reg, value, row.last) else ("", "")

    def _paint(self, row):
        kind, why = self._state(row)
        set_prop(row.label, "state", {"dirty": "warn", "error": "error"}.get(kind, ""))
        row.widget.setToolTip(why if why else "")

    def is_dirty(self):
        return any(self._state(r)[0] == "dirty" for r in self.param_rows.values())

    def errors(self):
        return [f"{r.reg.name}: {why}" for r in self.param_rows.values() for kind, why in [self._state(r)] if kind == "error"]

    def _sync(self):
        connected = self.s.is_connected
        self.read_btn.setEnabled(connected)
        self.write_btn.setEnabled(connected and self.is_dirty())
        self.revert_btn.setEnabled(self.is_dirty())

    # ---- reading and writing ------------------------------------------------------------------
    def read_now(self):
        client = self.s.client
        if client is None:
            return
        regs = [r.reg for r in self.param_rows.values()]

        def job(emit, cancel):
            return read_registers(client, regs, cancel)

        def done(values):
            if self.s.client is not client:     # a Disconnect (or reconnect) landed while these reads were in flight -
                return                          # seconds, on a real bus: _on_disconnected has already cleared the rows,
            for key, value in values.items():   # painting them "read" now would leave a DISCONNECTED page showing live
                row = self.param_rows[key]      # values (the same `client is` guard Session._probed uses)
                row.set(value)
                row.last, row.touched, row.refused = value, False, ""
                self._paint(row)
            missing = len(regs) - len(values)
            self.message.emit(f"Read {len(values)} parameter(s)" + (f" - {missing} could not be read." if missing else "."),
                              "warn" if missing else "info")
            self._sync()

        def failed(exc):
            if self.s.client is client:
                self.message.emit(f"Could not read: {exc}", "error")

        run_async(job, on_done=done, on_failed=failed, owner=self)

    def revert(self):
        for row in self.param_rows.values():
            if row.last is not None:
                row.set(row.last)
            row.touched, row.refused = False, ""
            self._paint(row)
        self._sync()

    def _confirm(self, text):
        return QMessageBox.question(self, self._title_text, text) == QMessageBox.StandardButton.Yes

    def write_changed(self):
        client = self.s.client
        if client is None:
            return
        items = []
        for row in self.param_rows.values():
            kind, why = self._state(row)
            if kind == "dirty":
                try:
                    items.append((row, row.get()))
                except ValueError:
                    pass
        if not items:
            self.message.emit("Nothing to write - edit a value first.", "info")
            return
        power_cycle = [r.reg.name for r, _v in items if r.reg.power_cycle]
        notes = "\n\nThese take effect only after a power cycle of the drive:\n  " + "\n  ".join(power_cycle) if power_cycle else ""
        if any(r.reg.key in RISKY for r, _v in items):
            notes += "\n\n⚠  A wrong CAN bit rate or node ID can make the drive unreachable."
        if not self._confirm(f"Write {len(items)} changed parameter(s) to node {self.s.node_id}?{notes}"):
            return

        def job(emit, cancel):
            for row, value in items:
                if cancel.is_set():
                    break
                try:
                    emit((row, client.write(row.reg, value), None))
                except DriveTimeout as exc:
                    emit((row, None, exc))
                    raise DriveTimeout("the drive is not answering - stopped") from None
                except DriveError as exc:
                    emit((row, None, exc))
            return len(items)

        failed = []

        def progress(payload):
            row, stored, exc = payload
            if self.s.client is not client:     # this connection is gone (Disconnect mid-write): its result is stale
                return
            if exc is not None:
                failed.append(row)
                row.refused = str(exc)
                self._paint(row)
            else:
                row.set(stored)
                row.last, row.touched, row.refused = stored, False, ""
                self._paint(row)

        def done(n):
            if self.s.client is not client:
                return
            self._sync()
            self.message.emit(f"Wrote {n - len(failed)} parameter(s)" + (f" - {len(failed)} failed." if failed else "."),
                              "warn" if failed else "ok")

        def failed_job(exc):
            if self.s.client is not client:
                return
            self._sync()
            self.message.emit(f"Stopped: {exc}", "error")

        run_async(job, on_done=done, on_failed=failed_job, on_progress=progress, owner=self)

    # ---- connection ---------------------------------------------------------------------------
    def _on_probed(self, _ok, _message=""):
        self._sync()

    def _on_disconnected(self):
        for row in self.param_rows.values():
            row.last, row.touched, row.refused = None, False, ""
            self._paint(row)
        self._sync()


class ItemConfigurationPage(QWidget):
    message = Signal(str, str)      # (text, level) for the window's status bar

    def __init__(self, session):
        super().__init__()
        self.s = session
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = self.scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.target_name = QLineEdit("Drive01")
        self.target_name.setPlaceholderText("a name for this drive")
        self.device_type = self._value()
        self.firmware = self._value()
        self.target_type = self._value("SDR-CB-60R-D servo drive")
        self.serial = self._value()
        general = PropertyGroup("General")
        general.add_row("Target Name", self.target_name)
        general.add_row("Target Type", self.target_type)
        general.add_row("Device Type", self.device_type)
        general.add_row("Device Serial Number", self.serial)
        general.add_row("Firmware Serial Number", self.firmware)
        column.addWidget(general)

        self.iface = ComboBox()
        self.iface.addItems(list(INTERFACES))
        self.iface.addItem(SEARCH_PORTS)
        self.channel = QLineEdit()
        self.bitrate = ComboBox()
        for bps, label in BITRATES:
            self.bitrate.addItem(label, bps)
        self.bitrate.setCurrentIndex(len(BITRATES) - 1)
        self.node = SpinBox()
        self.node.setRange(1, 127)
        self.node.setValue(1)
        self.node.setToolTip("Node ID of the servo drive (1..127)")
        self.state = self._value("Offline")
        connection = PropertyGroup("Target Connection")
        connection.add_row("Connection Type", self.iface)
        connection.add_row("Port / Channel", self.channel)
        connection.add_row("Bit Rate", self.bitrate)
        connection.add_row("Node ID", self.node)
        connection.add_row("Connection State", self.state)
        column.addWidget(connection)

        # Additional Axes, Kinematics Model and Tool Pose used to sit here; they are the Axes page's own now (Motion
        # activity, gui/axes_setup.py - user, 2026-09-26: "move three parameters ... from system configuration to
        # motion / axis (on top) and show axis and other parameters below it")
        self.activation = ParameterGroup("Product Activation", session, [(0x20FF, 0x02)],
                                         "A drive without a key reports fault 15, \"Activation Key is Missing\". Give Stimita "
                                         "support the Device Type and both serial numbers above; they issue the key for this "
                                         "drive. Enter it, then Write.", "Write the activation key")
        column.addWidget(self.activation)

        self.networks = ParameterGroup("Networks (drive's own settings)", session, [
            (0x3000, 0x02), (0x3000, 0x03), (0x3000, 0x04), (0x3000, 0x05), (0x3000, 0x06), (0x3000, 0x10), (0x3000, 0x12),
            (0x3000, 0x20)],
            "What the drive itself has stored - separate from Target Connection above, which is only how this tool reaches it. "
            "Most of these apply only after a power cycle of the drive; a wrong Bit Rate or Node ID can make it unreachable "
            "until Target Connection is changed to match.", "Write network settings")
        column.addWidget(self.networks)
        column.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        self.activation.message.connect(self.message)
        self.networks.message.connect(self.message)

    @staticmethod
    def _value(text=UNKNOWN):
        label = QLabel(text)
        label.setObjectName("gridValue")
        return label

    @staticmethod
    def _show_device_value(label, value):
        label.setText(UNKNOWN if value is None else f"{value}   (0x{value:X})")

    def show_device_info(self, info):
        """info: {'device_type', 'serial', 'firmware'} read from the drive, or None (not connected / not readable)."""
        info = info or {}
        self._show_device_value(self.device_type, info.get("device_type"))
        self._show_device_value(self.serial, info.get("serial"))
        self._show_device_value(self.firmware, info.get("firmware"))


class AxisConfigurationPage(QWidget):
    """An additional axis's own complete "System Configuration" equivalent - General identity, Target Connection,
    Product Activation and Networks, all genuinely its own, not the primary's (2026-09-23: after two earlier,
    smaller attempts - an inline expandable row, then a tree click that revealed it in place on the PRIMARY's own
    page - the user asked for a real, separate page: "when drive 2 is selected then every single information
    showing must be about drive 2 only"). Deliberately does NOT include Additional Axes (adding/removing axes is a
    workspace-level concern, done from the primary's own page) or Kinematics Model (one shared multi-axis model,
    not per-axis) - both stay exclusively on `ItemConfigurationPage`.

    `ParameterGroup` (Product Activation, Networks) needed ZERO changes to work here: it already only reads
    `.client`/`.node_id`/`.is_connected` and the `probed`/`disconnected` signals from whatever it is given, exactly
    the `AxisContext` shape every other per-axis page in this app already relies on."""
    message = Signal(str, str)

    def __init__(self, ctx, channel_text, bitrate_text):
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.device_type, self.serial, self.firmware = ItemConfigurationPage._value(), ItemConfigurationPage._value(), \
            ItemConfigurationPage._value()
        general = PropertyGroup("General")
        general.add_row("Target Name", ItemConfigurationPage._value(f"Drive{ctx.node_id:02d} (SDR)"))
        general.add_row("Target Type", ItemConfigurationPage._value("SDR-CB-60R-D servo drive"))
        general.add_row("Device Type", self.device_type)
        general.add_row("Device Serial Number", self.serial)
        general.add_row("Firmware Serial Number", self.firmware)
        column.addWidget(general)

        self.state = ItemConfigurationPage._value("Offline")
        connection = PropertyGroup("Target Connection")
        connection.add_row("Connection Type", ItemConfigurationPage._value(ctx.interface))
        connection.add_row("Port / Channel", ItemConfigurationPage._value(channel_text or "virtual bus"))
        connection.add_row("Bit Rate", ItemConfigurationPage._value(bitrate_text))
        connection.add_row("Node ID", ItemConfigurationPage._value(str(ctx.node_id)))
        connection.add_row("Connection State", self.state)
        column.addWidget(connection)

        self.activation = ParameterGroup("Product Activation", ctx, [(0x20FF, 0x02)],
                                         "A drive without a key reports fault 15, \"Activation Key is Missing\". Give Stimita "
                                         "support the Device Type and both serial numbers above; they issue the key for this "
                                         "drive. Enter it, then Write.", "Write the activation key")
        column.addWidget(self.activation)

        self.networks = ParameterGroup("Networks (drive's own settings)", ctx, [
            (0x3000, 0x02), (0x3000, 0x03), (0x3000, 0x04), (0x3000, 0x05), (0x3000, 0x06), (0x3000, 0x10), (0x3000, 0x12),
            (0x3000, 0x20)],
            "What the drive itself has stored - separate from Target Connection above, which is only how this tool reaches it. "
            "Most of these apply only after a power cycle of the drive; a wrong Bit Rate or Node ID can make it unreachable "
            "until Target Connection is changed to match.", "Write network settings")
        column.addWidget(self.networks)
        column.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        self.activation.message.connect(self.message)
        self.networks.message.connect(self.message)
        ctx.probed.connect(self._refresh)
        self._refresh()

    def _refresh(self, ok=True, _message=""):
        info = self.ctx.client.device_info if self.ctx.is_connected else None
        ItemConfigurationPage._show_device_value(self.device_type, (info or {}).get("device_type"))
        ItemConfigurationPage._show_device_value(self.serial, (info or {}).get("serial"))
        ItemConfigurationPage._show_device_value(self.firmware, (info or {}).get("firmware"))
        if not self.ctx.is_connected:
            self.state.setText("Offline")
        elif not ok:
            self.state.setText(f"NO RESPONSE  ·  NODE {self.ctx.node_id}")
        elif self.ctx.is_simulated:
            self.state.setText(f"SIMULATED DRIVE  ·  NODE {self.ctx.node_id}")
        else:
            self.state.setText(f"ONLINE  ·  NODE {self.ctx.node_id}")
