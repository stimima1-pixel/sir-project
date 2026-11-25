"""CAN TRAFFIC tab: every frame on the bus, decoded with the device-reference tables, and a G-code console (the drive maker's
Motion Controller dialect) that translates a typed line into one of this drive's own commands - see gcode.py."""
import queue

from PySide6.QtCore import QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import defs, protocol as P
from . import gcode, theme
from .gcode_runner import GcodeRunner, combine_action_results
from .widgets import make_button, mono_font, set_prop

MAX_ROWS = 1500
MAX_PER_TICK = 300
GCODE_HELP = gcode.HELP    # kept as a name here too: some callers/tests referred to log_tab.GCODE_HELP before this
                            # moved to gcode.py itself (a page-independent home for it)


class FrameDecoder:
    """Turns (can_id, data) into a human-readable description."""

    def __init__(self):
        self.regs = {r.key: r for r in defs.load_registers()}
        self.set_tpdos(defs.load_telemetry()[0])
        self.commands = {(c.cob, c.code): c for c in defs.load_commands().values() if c.defined}

    def set_tpdos(self, tpdos):
        """The telemetry messages to decode (the drive's mapping is configurable)."""
        self.tpdos = tpdos
        self.tpdo_by_cob = {t.cob: t for t in tpdos}
        self.telemetry_regs = {(f.index, f.sub): f for t in tpdos for f in t.fields}
        self.telemetry_regs.update({(f.index, f.sub): f for f in defs.poll_only_fields()})     # polled by request

    def is_telemetry(self, can_id, data=b""):
        """Telemetry broadcast, or a read request / response for a telemetry value (telemetry polling)."""
        cob, _ = P.split_can_id(can_id)
        if cob in self.tpdo_by_cob:
            return True
        return cob in (P.COB_REQUEST, P.COB_RESPONSE) and len(data) >= 4 and (data[1] | data[2] << 8, data[3]) in self.telemetry_regs

    def _name_and_type(self, index, sub):
        reg = self.regs.get((index, sub))
        return (reg.name, reg.dtype) if reg else ("unknown parameter", None)

    def describe(self, can_id, data):
        data = bytes(data).ljust(8, b"\0")
        cob, node = P.split_can_id(can_id)
        if cob == P.COB_REQUEST:
            index, sub = data[1] | data[2] << 8, data[3]
            name, dtype = self._name_and_type(index, sub)
            head = f"0x{index:04X}:0x{sub:02X}  {name}"
            if data[0] == P.CMD_READ:
                return f"Read request  {head}"
            if data[0] == P.CMD_WRITE:
                value = P.decode_value(dtype, data[4:8]) if dtype else data[4:8].hex(" ")
                return f"Write request  {head}  =  {value:.6g}" if isinstance(value, float) else f"Write request  {head}  =  {value}"
            return f"Request code 0x{data[0]:02X}  {head}"
        if cob == P.COB_RESPONSE:
            if data[0] == P.RESPONSE_ACK:
                index, sub = data[1] | data[2] << 8, data[3]
                name, dtype = self._name_and_type(index, sub)
                value = P.decode_value(dtype, data[4:8]) if dtype else data[4:8].hex(" ")
                shown = f"{value:.6g}" if isinstance(value, float) else f"{value}"
                return f"Write acknowledged  0x{index:04X}:0x{sub:02X}  {name}  =  {shown}"
            r = P.parse_response(can_id, data)
            if r is None:
                return f"Response code 0x{data[0]:02X} (not a read/error response)"
            name, dtype = self._name_and_type(r.index, r.sub)
            head = f"0x{r.index:04X}:0x{r.sub:02X}  {name}"
            if r.error:
                return f"ERROR response  {head}  (code {r.raw[::-1].hex()})"
            value = r.value(dtype) if dtype else r.raw.hex(" ")
            return f"Read response  {head}  =  {value:.6g}" if isinstance(value, float) else f"Read response  {head}  =  {value}"
        if cob in self.tpdo_by_cob:
            parts = []
            for f in self.tpdo_by_cob[cob].fields:
                v = P.decode_value(f.dtype, data[f.offset:f.offset + P.type_size(f.dtype)])
                parts.append(f"{f.key}={v:.4g}" if isinstance(v, float) else f"{f.key}={v}")
            return "Telemetry  " + "  ".join(parts)
        cmd = self.commands.get((cob, data[0]))
        if cmd:
            values = [P.decode_value(p.dtype, data[p.offset:p.offset + P.type_size(p.dtype)]) for p in cmd.params]
            return f"Command  {cmd.name}  " + "  ".join(f"{p.name}={v:.6g} {p.units}" for p, v in zip(cmd.params, values))
        return ""


class LogTab(QWidget):
    def __init__(self, session, drive_tab_for, parent=None):
        super().__init__(parent)
        self.s = session
        self.runner = GcodeRunner(session, drive_tab_for)
        self.decoder = FrameDecoder()
        self._t0 = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 14)
        outer.setSpacing(12)
        outer.addLayout(self._gcode_console())

        bar = QHBoxLayout()
        self.pause = QCheckBox("Pause")
        self.show_telemetry = QCheckBox("Show telemetry frames (broadcast and polling)")
        self.clear_btn = QPushButton("CLEAR")
        self.count = QLabel("")
        self.count.setProperty("muted", "true")
        bar.addWidget(self.pause)
        bar.addSpacing(14)
        bar.addWidget(self.show_telemetry)
        bar.addStretch(1)
        bar.addWidget(self.count)
        bar.addSpacing(12)
        bar.addWidget(self.clear_btn)
        outer.addLayout(bar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["TIME (s)", "DIR", "CAN ID", "DATA", "MEANING"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setFont(mono_font(12))
        head = self.table.horizontalHeader()
        for col, width in enumerate((90, 50, 80, 210)):
            head.resizeSection(col, width)
        head.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        head.setHighlightSections(False)
        outer.addWidget(self.table, 1)

        self.clear_btn.clicked.connect(self._clear)
        session.connected.connect(self._clear)
        session.probed.connect(self._on_probed)
        session.mapping_changed.connect(self._on_mapping_changed)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain)
        self._timer.start(100)

    # ---- G-code console -----------------------------------------------------------------------
    def _gcode_console(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel("G-code")
        label.setProperty("muted", "true")
        self.gcode_input = QLineEdit()
        self.gcode_input.setPlaceholderText("e.g. G1030 X500 - the drive's G-code dialect, see the ? for what this tool translates")
        self.gcode_input.setFont(mono_font(13))
        self.gcode_run_btn = make_button(QPushButton("RUN"), "primary")
        self.gcode_run_btn.setToolTip(GCODE_HELP)
        self.gcode_help_btn = QPushButton("?")
        self.gcode_help_btn.setFixedWidth(28)
        self.gcode_help_btn.setToolTip(GCODE_HELP)
        row.addWidget(label)
        row.addWidget(self.gcode_input, 1)
        row.addWidget(self.gcode_run_btn)
        row.addWidget(self.gcode_help_btn)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addLayout(row)
        self.gcode_status = QLabel("")
        self.gcode_status.setObjectName("ctlStatus")
        self.gcode_status.setWordWrap(True)
        col.addWidget(self.gcode_status)
        self.gcode_input.returnPressed.connect(self._run_gcode)
        self.gcode_run_btn.clicked.connect(self._run_gcode)
        self.gcode_help_btn.clicked.connect(lambda: self._gcode_feedback(GCODE_HELP, ""))
        return col

    def _run_gcode(self):
        text = self.gcode_input.text().strip()
        if not text:
            return
        try:
            actions = gcode.parse_line(text)
        except gcode.GcodeError as exc:
            self._gcode_feedback(str(exc), "error")
            return
        self.runner.run_actions(actions, self.gcode_run_btn,
                                lambda results: self._gcode_feedback(*combine_action_results(results)),
                                on_progress=self._gcode_feedback)

    def _gcode_feedback(self, text, state):
        self.gcode_status.setText(text)
        set_prop(self.gcode_status, "state", state)

    def _on_probed(self, ok, message):
        self._on_mapping_changed()

    def _on_mapping_changed(self):
        if self.s.client is not None:
            self.decoder.set_tpdos(self.s.client.tpdos)

    def _clear(self):
        self.table.setRowCount(0)
        self._t0 = None
        self.count.setText("")

    def _drain(self):
        frames = []
        try:
            while len(frames) < MAX_PER_TICK:
                frames.append(self.s.frames.get_nowait())
        except queue.Empty:
            pass
        if not frames or self.pause.isChecked():
            return
        show_tel = self.show_telemetry.isChecked()
        frames = [f for f in frames if show_tel or not self.decoder.is_telemetry(f[2], f[3])]
        if not frames:
            return
        self.table.setUpdatesEnabled(False)
        first = self.table.rowCount()
        self.table.setRowCount(first + len(frames))            # one insertion for the batch, not one per frame
        for row, (direction, t, can_id, data) in enumerate(frames, first):
            if self._t0 is None:
                self._t0 = t
            meaning = self.decoder.describe(can_id, data)
            cells = [f"{t - self._t0:9.3f}", direction.upper(), f"0x{can_id:03X}", bytes(data).hex(" ").upper(), meaning]
            colour = theme.RUBY if meaning.startswith("ERROR") else theme.GOLD if direction == "tx" else theme.TEXT
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(QBrush(QColor(colour)))
                self.table.setItem(row, col, item)
        overflow = self.table.rowCount() - MAX_ROWS
        if overflow > 0:
            self.table.model().removeRows(0, overflow)          # one pass (removeRow(0) per row shifted every row each time)
        self.table.setUpdatesEnabled(True)
        if self.isVisible():                                    # a hidden log scrolls when it is next shown
            self.table.scrollToBottom()
        self.count.setText(f"{self.table.rowCount()} frames shown")

    def showEvent(self, event):
        super().showEvent(event)
        self.table.scrollToBottom()
