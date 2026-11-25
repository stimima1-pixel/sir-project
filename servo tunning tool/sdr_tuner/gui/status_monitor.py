"""Drive Setup and Motion > Status Monitor: pick any register - configuration or telemetry, on any connected axis -
and watch it, matching Elmo Application Studio III's own Status Monitor (UI reference only, not a protocol
reference - see the README). A row whose picked signal is currently in that axis's own broadcast updates live from
the exact same `client.telemetry_snapshot()` every other page already reads, at no extra bus cost - the same
"never poll the drive" discipline `drive_tab.py`'s own docstring states. A row whose signal is NOT broadcast shows
"not broadcast" and only updates when its own Read is pressed (one on-demand SDO read, the same
`run_async`/`client.read` pattern Peripherals/Parameters Explorer already use) - this page never starts a
background poll for a value the drive is not already sending. Not per-axis itself: one table, like Axes/2D
Plotter, since a row's own Target picks which connected axis it watches."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QCompleter, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import defs
from ..client import DriveTimeout
from .axes_page import drive_label
from .params_tab import tooltip_html
from .tuner import show_value
from .widgets import ComboBox, Gauge, make_button, set_prop
from .workers import run_async

FAULT_BITS_KEY = "fault_bits"     # defs.TELEMETRY_KEYS' own short name for it - see _format_value
REFRESH_MS = 200
COLUMNS = ("Target", "Signal", "Value", "Units", "", "Gauge", "")   # the last two: Read, Remove - no header text
COL_TARGET, COL_SIGNAL, COL_VALUE, COL_UNITS, COL_READ, COL_GAUGE, COL_REMOVE = range(7)
ROW_HEIGHT = 32


class _Row:
    """One watched signal: which register, on which axis, and the widgets that show it."""

    def __init__(self, target_combo, signal_combo, value_item, units_item, read_btn, gauge_check, gauge, remove_btn):
        self.target_combo, self.signal_combo = target_combo, signal_combo
        self.value_item, self.units_item = value_item, units_item
        self.read_btn, self.gauge_check, self.gauge, self.remove_btn = read_btn, gauge_check, gauge, remove_btn
        self.reg = None               # the picked Register, or None
        self.snap_key = None          # reg's own key in client.telemetry_snapshot() - see _on_signal_picked
        self.last_value = None        # the last value known - from the broadcast, or the last successful Read
        self.reading = False          # a one-shot Read is in flight - _tick must not re-enable the button meanwhile


class StatusMonitorPage(QWidget):
    def __init__(self, session, primary_name):
        super().__init__()
        self.s = session
        self._primary_name = primary_name      # callable () -> str, e.g. main_window.py's _target_name
        self._regs = sorted(defs.load_registers(), key=lambda r: r.name.lower())   # every config + telemetry item
        self._fault_names = defs.load_telemetry()[1]   # {bit: name} - Fault Bits gets its own formatting, see _format_value
        self._rows = []                         # list of _Row, in table order
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        note = QLabel("Pick any parameter or telemetry value, on any connected axis, and watch it. A value the "
                      "drive is already broadcasting updates live, for free; anything else shows “not "
                      "broadcast” until you press its own Read - this page never polls the drive on its own.")
        note.setWordWrap(True)
        note.setObjectName("formNote")
        outer.addWidget(note)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.add_btn = make_button(QPushButton("+ ADD SIGNAL"), "primary")
        bar.addWidget(self.add_btn)
        bar.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("ctlStatus")
        bar.addWidget(self.status)
        outer.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        head = self.table.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)   # Qt's own default is
                                                                                                # centred - never sits
                                                                                                # above left-aligned data
        head.setSectionResizeMode(COL_TARGET, QHeaderView.ResizeMode.Fixed)
        head.setSectionResizeMode(COL_SIGNAL, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(COL_VALUE, QHeaderView.ResizeMode.Fixed)
        head.setSectionResizeMode(COL_GAUGE, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_TARGET, 130)
        self.table.setColumnWidth(COL_VALUE, 140)
        self.table.setColumnWidth(COL_UNITS, 90)
        self.table.setColumnWidth(COL_READ, 96)       # the cell is ~12 px narrower than the column; "Read" needs 71
        self.table.setColumnWidth(COL_GAUGE, 110)
        self.table.setColumnWidth(COL_REMOVE, 44)
        head.setHighlightSections(False)
        outer.addWidget(self.table, 1)

        self.add_btn.clicked.connect(lambda: self._add_row())
        session.connected.connect(self._refresh_targets)
        session.disconnected.connect(self._refresh_targets)
        session.axis_connected.connect(lambda _node: self._refresh_targets())
        session.axis_disconnected.connect(lambda _node: self._refresh_targets())
        session.node_detected.connect(self._on_node_detected)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)
        self._say("No signals yet - press + Add Signal to watch one.", "")

    # ---- row management ----------------------------------------------------------------------
    def _add_row(self):
        i = self.table.rowCount()
        self.table.insertRow(i)

        target_combo = ComboBox()
        self.table.setCellWidget(i, COL_TARGET, target_combo)

        signal_combo = ComboBox()
        signal_combo.setEditable(True)
        signal_combo.setInsertPolicy(ComboBox.InsertPolicy.NoInsert)
        for reg in self._regs:
            signal_combo.addItem(reg.name, reg.key)
        signal_combo.setCurrentIndex(-1)
        completer = QCompleter([reg.name for reg in self._regs], signal_combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        signal_combo.setCompleter(completer)
        self.table.setCellWidget(i, COL_SIGNAL, signal_combo)

        value_item = QTableWidgetItem("—")
        units_item = QTableWidgetItem("")
        self.table.setItem(i, COL_VALUE, value_item)
        self.table.setItem(i, COL_UNITS, units_item)

        read_btn = make_button(QPushButton("Read"), size="small")
        read_btn.setEnabled(False)
        self.table.setCellWidget(i, COL_READ, read_btn)

        gauge_check = QCheckBox()
        gauge_check.setEnabled(False)
        gauge = Gauge(0.0, 1.0)
        gauge_cell = QWidget()
        gauge_lay = QHBoxLayout(gauge_cell)
        gauge_lay.setContentsMargins(4, 2, 4, 2)
        gauge_lay.setSpacing(6)
        gauge_lay.addWidget(gauge_check)
        gauge_lay.addWidget(gauge, 1)
        self.table.setCellWidget(i, COL_GAUGE, gauge_cell)

        remove_btn = QPushButton("✕")
        remove_btn.setObjectName("removeRow")     # theme: tight padding - the default 18 px sides hid the glyph entirely
        remove_btn.setFixedWidth(28)
        self.table.setCellWidget(i, COL_REMOVE, remove_btn)

        row = _Row(target_combo, signal_combo, value_item, units_item, read_btn, gauge_check, gauge, remove_btn)
        self._rows.insert(i, row)
        self._refresh_row_targets(row)
        signal_combo.currentIndexChanged.connect(lambda _idx, r=row: self._on_signal_picked(r))
        read_btn.clicked.connect(lambda _c=False, r=row: self._read_row(r))
        remove_btn.clicked.connect(lambda _c=False, r=row: self._remove_row(r))
        self._say(f"{len(self._rows)} signal(s) watched.", "")

    def _remove_row(self, row):
        i = self._rows.index(row)
        self.table.removeRow(i)
        self._rows.remove(row)
        self._say(f"{len(self._rows)} signal(s) watched." if self._rows else
                  "No signals yet - press + Add Signal to watch one.", "")

    # ---- target (which connected axis a row watches) ------------------------------------------
    def _refresh_targets(self):
        for row in self._rows:
            self._refresh_row_targets(row)

    def _on_node_detected(self, node):
        """The primary drive answered on a different node than the one selected - found by listening AFTER
        `connected` already fired (always so on the USB cable), which re-keys all_clients(). A row still pointing
        at the node the drive was supposed to be on now follows it to the node it really is on; it used to keep
        the stale node forever - "—", Read disabled - since nothing here ever listened for this."""
        self._refresh_targets()
        connected = self.s.all_clients()
        for row in self._rows:
            if row.target_combo.currentData() not in connected:
                row.target_combo.setCurrentIndex(row.target_combo.findData(node))     # announces, like a user pick
                self._refresh_row_targets(row)                                       # drops the stale node's own entry

    def _refresh_row_targets(self, row):
        connected = {node: drive_label(node, self.s.node_id, self._primary_name()) for node in self.s.all_clients()}
        current = row.target_combo.currentData()
        options = dict(connected)
        if current is not None and current not in options:
            options[current] = row.target_combo.currentText() or f"Drive{current:02d} (SDR)"
        row.target_combo.blockSignals(True)
        row.target_combo.clear()
        for node in sorted(options):
            row.target_combo.addItem(options[node], node)
        if current is not None:
            index = row.target_combo.findData(current)
            if index >= 0:
                row.target_combo.setCurrentIndex(index)
        elif options:
            row.target_combo.setCurrentIndex(0)
        row.target_combo.blockSignals(False)

    def _client_for(self, row):
        node = row.target_combo.currentData()
        if node is None:
            return None
        return self.s.all_clients().get(node)

    # ---- signal (which register a row watches) -----------------------------------------------
    def _on_signal_picked(self, row):
        key = row.signal_combo.currentData()
        reg = next((r for r in self._regs if r.key == key), None)
        row.reg = reg
        row.last_value = None
        if reg is None:
            row.snap_key = None
            row.units_item.setText("")
            row.signal_combo.setToolTip("")
            row.gauge_check.setEnabled(False)
            row.gauge_check.setChecked(False)
            return
        # client.telemetry_snapshot()'s own keys: a short name for the handful of registers defs.TELEMETRY_KEYS
        # curates (e.g. "wz_count"), that register's own address string otherwise - never reg.key (index, sub)
        # itself, which is a DIFFERENT namespace only used to look a Register up, not to read a live snapshot.
        row.snap_key = defs.TELEMETRY_KEYS.get(reg.key, reg.address)
        row.units_item.setText(reg.units)
        row.signal_combo.setToolTip(tooltip_html(reg))
        has_range = reg.min is not None and reg.max is not None
        row.gauge_check.setEnabled(has_range)
        if has_range:
            row.gauge.set_range(reg.min, reg.max)
        else:
            row.gauge_check.setChecked(False)

    # ---- reading -------------------------------------------------------------------------------
    def _read_row(self, row):
        client, reg = self._client_for(row), row.reg
        if client is None or reg is None:
            return
        row.reading = True                       # _tick re-evaluates the button every 200 ms - without this flag it
        row.read_btn.setEnabled(False)           # re-enabled it while the read was still in flight (double reads)

        def job(_emit, _cancel):
            return client.read(reg)

        def done(value):
            row.last_value = value
            row.reading = False
            row.read_btn.setEnabled(True)
            self._say(f"Read {reg.name}.", "ok")

        def failed(exc):
            row.reading = False
            row.read_btn.setEnabled(True)
            kind = "timed out" if isinstance(exc, DriveTimeout) else "failed"
            self._say(f"Read {reg.name} {kind}: {exc}", "error")

        run_async(job, on_done=done, on_failed=failed, owner=self)

    # ---- live refresh ----------------------------------------------------------------------
    def _tick(self):
        for row in self._rows:
            reg = row.reg
            if reg is None:
                continue
            client = self._client_for(row)
            broadcasting = False
            value = row.last_value
            if client is not None:
                snap = client.telemetry_snapshot()
                if row.snap_key in snap:
                    value = snap[row.snap_key]
                    row.last_value = value
                    broadcasting = True
            row.read_btn.setEnabled(not broadcasting and client is not None and not row.reading)
            row.value_item.setText(self._format_value(row, value) if value is not None else
                                   ("not broadcast" if client is not None else "—"))
            if row.gauge_check.isChecked() and value is not None:
                row.gauge.set_value(value)
            else:
                row.gauge.set_value(None)

    def _format_value(self, row, value):
        """Fault Bits is a COMBINATION of bits, not a single enumerated state - its own `options` table (built for
        a plain single-value enum) only names the individual/well-known combinations it happened to list, so a
        genuine multi-fault value falls through to a raw placeholder like "=1+2". Every other page that shows
        Fault Bits (`DriveTab`'s own card, `AxesStatusPage`) already decodes it bit by bit with
        `defs.fault_names_text` instead - this page matches that, rather than being the one place Fault Bits reads
        differently. Everything else still goes through the generic `show_value` (options-mapped text, or the raw
        number) - correct for the other ~416 registers, which are genuine single-value enums or plain numbers."""
        if row.snap_key == FAULT_BITS_KEY:
            bits = int(value)
            return "No Faults" if not bits else defs.fault_names_text(bits, self._fault_names)
        return show_value(row.reg, value)

    def _say(self, text, state):
        self.status.setText(text)
        set_prop(self.status, "state", state)
