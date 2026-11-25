"""PARAMETERS tab (configuration, readable and writable) and TELEMETRY tab (read-only values) of the device reference."""
import html
import json

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import defs, protocol as P
from ..client import DriveError, DriveTimeout
from . import theme
from .widgets import ComboBox, fold_on_single_click, make_button, set_prop
from .workers import run_async

COLUMNS = ["PARAMETER", "VALUE", "UNITS", "TYPE", "ADDRESS", "STATE", ""]
HIDDEN_COLUMNS = (3, 4, 5)      # type, address and state are not shown (the room goes to the names); they are in the tooltip
STATE_COLORS = {"ok": theme.EMERALD, "error": theme.RUBY, "warn": theme.AMBER, "": theme.MUTED}
NAME_COLORS = {"warn": theme.AMBER, "error": theme.RUBY}         # a name turns amber when edited, red when the drive said no
DIRTY_STYLE = f"background: #2A2410; border: 1px solid {theme.GOLD_LO};"
FILE_FORMAT = "sdr-parameters"
RISKY = {(0x3000, 0x02), (0x3000, 0x03)}        # CAN bit rate and node ID: a wrong value can make the drive unreachable
RAW_DTYPES = list(P.TYPES)      # for Raw Register Access - every wire type this protocol knows how to encode/decode


def is_mapping_slot(reg):
    """A Telemetry Mapping register: one slot of one broadcast message, holding the mapping code of the parameter it carries."""
    return reg.section.startswith("Telemetry Mapping")


def describe_code(code):
    """What a telemetry mapping code stands for, for a tooltip or a menu: "Current: Phase B (Ib) (0x5001:0x06)"."""
    code = int(code)
    if code == 0:
        return "unused (0)"
    index, sub = defs.parse_mapping_code(code)
    reg = defs.telemetry_catalog().get((index, sub))
    return f"{reg.name} ({reg.address})" if reg else f"no telemetry parameter at 0x{index:04X}:0x{sub:02X}"


def clipboard_code():
    """The telemetry mapping code on the clipboard, or None if it does not hold a whole number."""
    text = (QApplication.clipboard().text() or "").strip().replace(",", "").replace("_", "")
    return int(text) if text.isdigit() else None


def tooltip_html(reg, state="", kind="", current=None):
    """The parameter's address, type and state, and its description, as a wrapped rich-text tooltip (a plain tooltip
    would be one very long line). This is where the details that are not columns any more can be found. A telemetry
    parameter's tooltip carries its mapping code, a Telemetry Mapping slot's what it currently broadcasts (`current`) -
    user, 2026-09-26: "give option to user that he can copy telemetry code and paste it in telemetry mapping code"."""
    text = reg.desc or reg.name
    if len(text) > 900:
        text = text[:900].rsplit(" ", 1)[0] + " …"
    head = f"<b>{reg.address}</b> &nbsp;·&nbsp; {reg.dtype} &nbsp;·&nbsp; {'read / write' if reg.writable else 'read-only'}"
    if reg.units:
        head += f" &nbsp;·&nbsp; {html.escape(reg.units)}"
    lines = [head]
    if reg.power_cycle:
        lines.append("Applies only after a power cycle of the drive.")
    if reg.telemetry:
        lines.append(f"Telemetry mapping code: <b>{defs.mapping_code(*reg.key)}</b> &nbsp;—&nbsp; right-click the row to copy it, "
                     "then paste it into a Telemetry Mapping slot (Parameters Explorer) to make the drive broadcast this value.")
    elif is_mapping_slot(reg):
        lines.append("Holds a telemetry mapping code: the parameter this slot of the broadcast carries (0 = unused). "
                     "Right-click the row to paste a code copied from Telemetry Explorer, then Write it.")
        if current is not None:
            lines.append(f"Currently: <b>{html.escape(describe_code(current))}</b>")
    if state:
        lines.append(f"<span style='color:{STATE_COLORS.get(kind, theme.MUTED)}'><b>{html.escape(state)}</b></span>")
    return f"<table width='440'><tr><td>{'<br>'.join(lines)}<br><br>{html.escape(text)}</td></tr></table>"


def make_editor(reg, on_user_edit, on_submit=None):
    """-> (widget, getter, setter). The getter validates and raises ValueError; user edits call on_user_edit(); pressing
    Enter in a text box also calls on_submit(), if given - the caller's own Write / Apply action, so Enter is a shortcut
    for it rather than a second, different way to commit a value."""
    if reg.options:
        widget = ComboBox()
        for value, label in reg.options.items():
            widget.addItem(f"{value}" if label == str(value) else f"{value}  —  {label}", value)
        widget.setCurrentIndex(-1)
        widget.activated.connect(lambda _i=0: on_user_edit())

        def get():
            value = widget.currentData()
            if value is None:
                raise ValueError("choose a value")
            return value

        def set_(value):
            idx = widget.findData(int(value))
            if idx < 0:
                widget.addItem(f"{int(value)}  —  (not in the table)", int(value))
                idx = widget.count() - 1
            widget.setCurrentIndex(idx)
        return widget, get, set_

    if reg.dtype == "BOOL":
        widget = QCheckBox("enabled")
        widget.clicked.connect(lambda _c=False: on_user_edit())
        return widget, widget.isChecked, lambda v: widget.setChecked(bool(v))

    widget = QLineEdit()
    widget.setAlignment(Qt.AlignmentFlag.AlignRight)
    widget.textEdited.connect(lambda _t="": on_user_edit())
    if on_submit is not None:
        widget.returnPressed.connect(on_submit)

    def get():
        text = widget.text().strip()
        if not text:
            raise ValueError("empty")
        try:
            value = float(text) if P.is_float(reg.dtype) else int(text, 0)
        except ValueError:
            raise ValueError(f"'{text}' is not a valid number") from None
        P.encode_value(reg.dtype, value)                        # range check for the data type
        if reg.min is not None and value < reg.min:
            raise ValueError(f"must be at least {reg.min:g}")
        if reg.max is not None and value > reg.max:
            raise ValueError(f"must be at most {reg.max:g}")
        return value

    def set_(value):
        widget.setText(str(np.float32(P.wire_value(reg.dtype, value))) if P.is_float(reg.dtype) else str(int(value)))
    return widget, get, set_


class ParamRow:
    def __init__(self, reg, item, top):
        self.reg, self.item, self.top = reg, item, top
        self.dirty = False              # edited (or loaded from a file) and not yet written
        self.has_value = False
        self.last = None                # last value known to be in the drive
        self.search = f"{reg.name} {reg.section} {reg.address} {reg.units} {reg.desc}".lower()
        self.editor = self.get = self.set = self.read_btn = self.write_btn = None


class ParamsTab(QWidget):
    message = Signal(str, str)      # (text, level) for the window's status bar

    def __init__(self, session, telemetry=False, parent=None):
        """telemetry=False: the configuration parameters; telemetry=True: the (normally read-only) telemetry parameters.
        Whether a given row can be written follows THAT register's own access mark in the Device Reference (`reg.writable`),
        not which of the two this page is - checked directly against the PDF: today every telemetry parameter is marked
        read-only and every configuration parameter is read/write, but the row-level editor and Write button, and the
        page-level Write Changed / Save / Load / READ-ONLY badge, all follow the data, not that coincidence."""
        super().__init__(parent)
        self.s = session
        self.telemetry = telemetry
        self.any_writable = any(r.writable for r in defs.load_registers(telemetry))
        self.rows = []
        self._row_of_item = {}          # id(tree item) -> ParamRow, for the context menu
        self.busy = False
        self._cancel = None
        self._tops = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 14)
        outer.setSpacing(12)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.read_all_btn = make_button(QPushButton("READ ALL"), "primary" if self.any_writable else None)
        self.write_btn = QPushButton("WRITE CHANGED")
        self.save_btn = QPushButton("SAVE TO FILE…")
        self.load_btn = QPushButton("LOAD FROM FILE…")
        self.cancel_btn = QPushButton("CANCEL")
        self.cancel_btn.setVisible(False)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by name, section, address…")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMinimumWidth(280)
        if not self.any_writable:
            badge = QLabel("READ-ONLY")
            badge.setObjectName("readOnlyBadge")
            bar.addWidget(badge)
            bar.addSpacing(6)
        for w in (self.read_all_btn, self.write_btn, self.cancel_btn):
            bar.addWidget(w)
        bar.addSpacing(18)
        bar.addWidget(self.save_btn)
        bar.addWidget(self.load_btn)
        if not self.any_writable:
            for w in (self.write_btn, self.save_btn, self.load_btn):
                w.setVisible(False)                     # nothing on this page can be written
        bar.addStretch(1)
        bar.addWidget(self.filter)
        self.bar = QWidget()
        self.bar.setLayout(bar)
        bar.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.bar)

        self.tree = QTreeWidget()
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.setProperty("readonly", telemetry)
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setAlternatingRowColors(True)
        self.tree.setIndentation(18)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        fold_on_single_click(self.tree)      # a section folds/unfolds on one click, like Item Configuration's groups
        head = self.tree.header()
        head.setStretchLastSection(False)
        for col, width in enumerate((430, 200, 122, 80, 110, 300, 184 if self.any_writable else 110)):  # the last: Read (and Write) buttons
            head.resizeSection(col, width)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._build_rows()
        for column in HIDDEN_COLUMNS:
            self.tree.setColumnHidden(column, True)
        outer.addWidget(self.tree, 1)
        if not telemetry:
            outer.addWidget(self._raw_access_box())

        foot = QHBoxLayout()
        self.info = QLabel("Values reported by the drive (read-only), read once each time you press READ ALL or Read." if not self.any_writable
                           else "Connect to a drive, then press READ ALL.")
        self.info.setProperty("muted", "true")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        foot.addWidget(self.info, 1)
        foot.addWidget(self.progress)
        outer.addLayout(foot)

        self.read_all_btn.clicked.connect(lambda: self._read_rows(self.rows))
        self.write_btn.clicked.connect(self._write_changed)
        self.save_btn.clicked.connect(self._save_dialog)
        self.load_btn.clicked.connect(self._load_dialog)
        self.cancel_btn.clicked.connect(self._cancel_job)
        self.filter.textChanged.connect(self._apply_filter)
        session.connected.connect(lambda: self._set_connected(True))
        session.disconnected.connect(lambda: self._set_connected(False))
        self._set_connected(False)

    # ---- actions (also used by the ribbon) -------------------------------------------------
    def read_all(self):
        self._read_rows(self.rows)

    def write_changed(self):
        self._write_changed()

    def save_dialog(self):
        self._save_dialog()

    def load_dialog(self):
        self._load_dialog()

    # ---- construction ----------------------------------------------------------------------
    def _build_rows(self):
        tops = {}
        for reg in defs.load_registers(self.telemetry):
            top = tops.get(reg.section)
            if top is None:
                top = QTreeWidgetItem(self.tree, [""])
                top.setFirstColumnSpanned(True)
                top.setForeground(0, QBrush(QColor(theme.GOLD)))
                font = top.font(0)
                font.setBold(True)
                top.setFont(0, font)
                top.setSizeHint(0, QSize(0, 34))
                tops[reg.section] = top
                self._tops.append(top)
            item = QTreeWidgetItem(top, [reg.name + ("   ⟳" if reg.power_cycle else ""), "", reg.units, reg.dtype, reg.address, "", ""])
            item.setSizeHint(0, QSize(0, 38))
            item.setToolTip(0, tooltip_html(reg))
            if reg.power_cycle:
                item.setText(5, "needs a power cycle")
                item.setForeground(5, QBrush(QColor(theme.DIM)))
            item.setForeground(3, QBrush(QColor(theme.DIM)))
            item.setForeground(4, QBrush(QColor(theme.DIM)))
            item.setForeground(2, QBrush(QColor(theme.MUTED)))
            row = ParamRow(reg, item, top)
            self._row_of_item[id(item)] = row
            row.editor, row.get, row.set = make_editor(reg, lambda r=row: self._mark_edited(r),
                                                        on_submit=(lambda r=row: self._write_one(r)) if reg.writable else None)
            if not reg.writable:                                # this register's own access mark: nothing to edit or write
                if isinstance(row.editor, QLineEdit):
                    row.editor.setReadOnly(True)
                else:
                    row.editor.setEnabled(False)
            self.tree.setItemWidget(item, 1, row.editor)
            actions = QWidget()
            lay = QHBoxLayout(actions)
            lay.setContentsMargins(4, 2, 4, 2)
            lay.setSpacing(6)
            row.read_btn = make_button(QPushButton("Read"), size="small")
            row.write_btn = make_button(QPushButton("Write"), size="small")
            row.read_btn.clicked.connect(lambda _c=False, r=row: self._read_rows([r]))
            row.write_btn.clicked.connect(lambda _c=False, r=row: self._write_one(r))
            lay.addWidget(row.read_btn)
            lay.addWidget(row.write_btn)
            # Attach before changing visibility so Qt doesn't show each button as a standalone window.
            row.write_btn.setVisible(reg.writable)
            self.tree.setItemWidget(item, 6, actions)
            self.rows.append(row)
        for name, top in tops.items():
            top.setText(0, f"{name.replace('Telemetry - ', '').upper()}    ({top.childCount()})")
            top.setExpanded(name in ("Datasheet", "Control Laws", "Telemetry - System Status"))

    # ---- state helpers ---------------------------------------------------------------------
    def _say(self, text, level="info"):
        self.info.setText(text)
        self.message.emit(text, level)

    def _set_connected(self, connected):
        self.connected = connected
        self._refresh_buttons()

    def _refresh_buttons(self):
        can_io = self.connected and not self.busy
        self.read_all_btn.setEnabled(can_io)
        self.write_btn.setEnabled(can_io)
        self.tree.setEnabled(not self.busy)
        for row in self.rows:
            row.read_btn.setEnabled(can_io)
            row.write_btn.setEnabled(can_io and row.reg.writable)

    def _set_busy(self, busy):
        self.busy = busy
        self.cancel_btn.setVisible(busy)
        self.progress.setVisible(busy)
        self.save_btn.setEnabled(not busy)
        self.load_btn.setEnabled(not busy)
        self._refresh_buttons()

    def _set_state(self, row, text, kind=""):
        if row.reg.power_cycle and kind == "ok":
            text += "  ·  applies after a power cycle"
        row.item.setText(5, text)
        row.item.setForeground(5, QBrush(QColor(STATE_COLORS[kind])))
        row.item.setToolTip(0, tooltip_html(row.reg, text, kind,                 # the state is read from the tooltip ...
                                            current=row.last if is_mapping_slot(row.reg) else None))
        row.item.setForeground(0, QBrush(QColor(NAME_COLORS.get(kind, theme.TEXT))))     # ... and seen from the colour of the name

    def _set_dirty(self, row, dirty):
        row.dirty = dirty
        row.editor.setStyleSheet(DIRTY_STYLE if dirty else "")

    def _context_menu(self, pos):
        row = self._row_of_item.get(id(self.tree.itemAt(pos)))
        if row is not None:
            self.menu_for(row).exec(self.tree.viewport().mapToGlobal(pos))

    def menu_for(self, row):
        """The row's right-click menu: copy its address; a telemetry parameter's mapping code can be copied, a
        Telemetry Mapping slot can take a code from the clipboard (or be set unused) - the copy-and-paste way to change
        what the drive broadcasts, instead of typing a 7-digit code by hand (user, 2026-09-26)."""
        reg = row.reg
        menu = QMenu(self)
        if reg.telemetry:
            code = defs.mapping_code(*reg.key)
            action = menu.addAction(f"Copy telemetry mapping code  {code}")
            action.setToolTip("Paste it into a Telemetry Mapping slot on Parameters Explorer to make the drive broadcast this value.")
            action.triggered.connect(lambda _c=False: self._copy_code(reg, code))
        elif is_mapping_slot(reg) and reg.writable:
            code = clipboard_code()
            if code is None:
                action = menu.addAction("Paste mapping code from clipboard  (no code on the clipboard)")
                action.setEnabled(False)
            else:
                action = menu.addAction(f"Paste mapping code  {code}  =  {describe_code(code)}")
                action.triggered.connect(lambda _c=False, c=code: self._paste_code(row, c))
            clear = menu.addAction("Set this slot unused  (0)")
            clear.triggered.connect(lambda _c=False: self._paste_code(row, 0))
            if row.has_value and row.last is not None:
                copy = menu.addAction(f"Copy this slot's code  {int(row.last)}")
                copy.triggered.connect(lambda _c=False: QApplication.clipboard().setText(str(int(row.last))))
        address = menu.addAction(f"Copy address  {reg.address}")
        address.triggered.connect(lambda _c=False: QApplication.clipboard().setText(reg.address))
        return menu

    def _copy_code(self, reg, code):
        QApplication.clipboard().setText(str(code))
        self.message.emit(f"Copied {code} - the telemetry mapping code of {reg.name}. Paste it into a Telemetry Mapping "
                          f"slot on Parameters Explorer (right-click the slot), then Write it.", "ok")

    def _paste_code(self, row, code):
        row.set(code)
        self._mark_edited(row)
        self.message.emit(f"{row.reg.name}: now {code} = {describe_code(code)} - press Write (or Enter) to send it to the "
                          f"drive. The drive keeps it until a power cycle unless you Save to Flash.", "warn")

    def _mark_edited(self, row):
        row.has_value = True
        self._set_dirty(row, True)
        self._set_state(row, "edited — not written", "warn")

    @staticmethod
    def _as_stored(reg, value):
        return P.decode_value(reg.dtype, P.encode_value(reg.dtype, value))

    # ---- reading ---------------------------------------------------------------------------
    def _run_job(self, fn, total, on_progress, on_done, on_failed):
        self._set_busy(True)
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(0)
        self._done_count = 0
        self._cancel = run_async(fn, on_done=on_done, on_failed=on_failed, on_progress=on_progress, owner=self)

    def _cancel_job(self):
        if self._cancel:
            self._cancel.set()

    def _read_rows(self, rows):
        client = self.s.client
        if client is None or self.busy:
            return

        def job(emit, cancel):
            timeouts = 0
            for row in rows:
                if cancel.is_set():
                    break
                try:
                    emit((row, client.read(row.reg), None))
                    timeouts = 0
                except DriveTimeout as exc:
                    emit((row, None, exc))
                    timeouts += 1
                    if timeouts >= 3:
                        raise DriveTimeout("the drive is not answering — stopped after 3 timeouts") from None
                except DriveError as exc:
                    emit((row, None, exc))
            return len(rows)

        def progress(payload):
            row, value, exc = payload
            self._done_count += 1
            self.progress.setValue(self._done_count)
            if exc is not None:
                self._set_state(row, f"✗ {exc}", "error")
                return
            row.set(value)
            row.last, row.has_value = value, True
            self._set_dirty(row, False)
            self._set_state(row, "✓ read", "ok")

        def done(_n):
            self._set_busy(False)
            errors = sum(1 for r in rows if r.item.text(5).startswith("✗"))
            self._say(f"Read {self._done_count} parameter(s)" + (f" — {errors} failed." if errors else "."), "warn" if errors else "info")

        self._run_job(job, len(rows), progress, done, self._job_failed)

    def _job_failed(self, exc):
        self._set_busy(False)
        self._say(f"Stopped: {exc}", "error")

    # ---- writing ---------------------------------------------------------------------------
    def _confirm(self, text):
        return QMessageBox.question(self, "Write parameters", text) == QMessageBox.StandardButton.Yes

    def _write_changed(self):
        rows = [r for r in self.rows if r.dirty and r.reg.writable]
        if not rows:
            self._say("Nothing to write — edit a value first.")
            return
        client = self.s.client
        warning = "⚠  The motor is currently being commanded to run.\n\n" if client and client.commanded is not None else ""
        notes = ""
        power_cycle = [r.reg.name for r in rows if r.reg.power_cycle]
        if power_cycle:
            notes += "\n\nThese take effect only after a power cycle of the drive:\n  " + "\n  ".join(power_cycle)
        if any(r.reg.key in RISKY for r in rows):
            notes += "\n\n⚠  A wrong CAN bit rate or node ID can make the drive unreachable."
        if not self._confirm(f"{warning}Write {len(rows)} changed parameter(s) to node {self.s.node_id}?{notes}"):
            return
        self._write_rows(rows)

    def _write_one(self, row):
        if row.reg.power_cycle:
            extra = "\n\n⚠  A wrong value can make the drive unreachable." if row.reg.key in RISKY else ""
            if not self._confirm(f"{row.reg.name} takes effect only after a power cycle of the drive.{extra}\n\nWrite it now?"):
                return
        self._write_rows([row])

    def _write_rows(self, rows):
        client = self.s.client
        if client is None or self.busy:
            return
        items = []
        for row in rows:
            try:
                items.append((row, row.get()))
            except ValueError as exc:
                self._set_state(row, f"✗ {exc}", "error")
        if not items:
            self._say("No valid values to write.", "warn")
            return

        def job(emit, cancel):
            for row, value in items:
                if cancel.is_set():
                    break
                try:
                    emit((row, client.write(row.reg, value), None))
                except DriveTimeout as exc:
                    emit((row, None, exc))
                    raise DriveTimeout("the drive is not answering — stopped") from None
                except DriveError as exc:
                    emit((row, None, exc))
            return len(items)

        def progress(payload):
            row, stored, exc = payload
            self._done_count += 1
            self.progress.setValue(self._done_count)
            if exc is not None:
                self._set_state(row, f"✗ {exc}", "error")
                return
            row.set(stored)
            row.last = stored
            self._set_dirty(row, False)
            self._set_state(row, "✓ written and verified", "ok")

        def done(_n):
            self._set_busy(False)
            failed = sum(1 for r, _v in items if r.item.text(5).startswith("✗"))
            self._say(f"Wrote {self._done_count - failed} parameter(s)" + (f" — {failed} failed." if failed else "."), "warn" if failed else "ok")

        self._run_job(job, len(items), progress, done, self._job_failed)

    # ---- files -----------------------------------------------------------------------------
    def save_to(self, path):
        """Save every parameter that has a value to a JSON file. Returns the number saved."""
        params = []
        for row in self.rows:
            if row.has_value and row.reg.writable:
                r = row.reg
                params.append({"index": f"0x{r.index:04X}", "sub": f"0x{r.sub:02X}", "name": r.name, "value": row.get()})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"format": FILE_FORMAT, "version": 1, "device": "SDR-CB-60R", "params": params}, fh, indent=2)
        return len(params)

    def load_from(self, path):
        """Fill the editors from a file (nothing is sent to the drive). Returns (loaded, skipped)."""
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("format") != FILE_FORMAT:
            raise ValueError("this is not an SDR parameter file")
        by_key = {row.reg.key: row for row in self.rows}
        loaded = skipped = 0
        for entry in doc["params"]:
            row = by_key.get((int(entry["index"], 0), int(entry["sub"], 0)))
            if row is None:
                skipped += 1
                continue
            row.set(entry["value"])
            row.has_value = True
            same = row.last is not None and self._as_stored(row.reg, entry["value"]) == row.last
            self._set_dirty(row, not same)
            self._set_state(row, "loaded from file" if not same else "✓ same as drive", "warn" if not same else "ok")
            loaded += 1
        return loaded, skipped

    def _save_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save parameters", f"sdr_parameters_node{self.s.node_id or 1}.json",
                                              "Parameter files (*.json)")
        if not path:
            return
        try:
            n = self.save_to(path)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Save parameters", f"Could not save: {exc}")
            return
        self._say(f"Saved {n} parameter(s) to {path}", "ok")

    def _load_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load parameters", "", "Parameter files (*.json)")
        if not path:
            return
        try:
            loaded, skipped = self.load_from(path)
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, "Load parameters", f"Could not load: {exc}")
            return
        self._say(f"Loaded {loaded} value(s) from file ({skipped} unknown skipped). "
                          f"Review them, then press WRITE CHANGED.")

    # ---- raw register access (anything not in the table above, e.g. an undocumented or brand-new register) ------
    def _raw_access_box(self):
        box = QGroupBox("RAW REGISTER ACCESS  ·  by index : sub-index, for anything not in the table above")
        column = QVBoxLayout(box)
        column.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        column.addLayout(row)
        self.raw_index = QLineEdit()
        self.raw_index.setPlaceholderText("0x2000")
        self.raw_index.setFixedWidth(90)
        self.raw_sub = QLineEdit()
        self.raw_sub.setPlaceholderText("0x01")
        self.raw_sub.setFixedWidth(70)
        self.raw_type = ComboBox()
        self.raw_type.addItems(RAW_DTYPES)
        self.raw_type.setCurrentText("UINT32")
        self.raw_value = QLineEdit()
        self.raw_value.setPlaceholderText("value to write")
        self.raw_read_btn = QPushButton("Read")
        self.raw_write_btn = QPushButton("Write")
        self.raw_status = QLabel("Bypasses every check the named table above has: range, known meaning, "
                                 "whether the drive really considers it writable. Only for a register you "
                                 "already know.")
        self.raw_status.setObjectName("ctlStatus")
        self.raw_status.setWordWrap(True)
        for label, widget in (("Index", self.raw_index), ("Sub-Index", self.raw_sub), ("Type", self.raw_type),
                              ("Value", self.raw_value)):
            row.addWidget(QLabel(label))
            row.addWidget(widget)
        row.addWidget(self.raw_read_btn)
        row.addWidget(self.raw_write_btn)
        row.addStretch(1)
        # The note/status sits on its own full-width line UNDER the inputs, not beside them: beside them it got
        # only the leftover width, and in a narrow docked panel (user's recording, 2026-09-25) it wrapped into a
        # one-word-per-line column ~20 lines tall that ballooned this box and squeezed the tree above it.
        column.addWidget(self.raw_status)
        self.raw_read_btn.clicked.connect(self._raw_read)
        self.raw_write_btn.clicked.connect(self._raw_write)
        return box

    def _parse_raw_index_sub(self):
        return int(self.raw_index.text().strip(), 0), int(self.raw_sub.text().strip(), 0)

    def _set_raw_status(self, text, state=""):
        self.raw_status.setText(text)
        set_prop(self.raw_status, "state", state)

    def _raw_read(self):
        client = self.s.client
        if client is None:
            self._set_raw_status("Not connected.", "warn")
            return
        try:
            index, sub = self._parse_raw_index_sub()
        except ValueError:
            self._set_raw_status("Index and Sub-Index must be numbers, e.g. 0x2000 and 0x01.", "error")
            return
        dtype = self.raw_type.currentText()

        def job(_emit, _cancel):
            return client.read_raw(index, sub).value(dtype)

        def done(value):
            self.raw_value.setText(str(value))
            self._set_raw_status(f"0x{index:04X}:0x{sub:02X} ({dtype}) = {value}", "ok")

        def failed(exc):
            self._set_raw_status(f"0x{index:04X}:0x{sub:02X}: {exc}", "error")

        run_async(job, on_done=done, on_failed=failed, owner=self)

    def _raw_write(self):
        client = self.s.client
        if client is None:
            self._set_raw_status("Not connected.", "warn")
            return
        try:
            index, sub = self._parse_raw_index_sub()
        except ValueError:
            self._set_raw_status("Index and Sub-Index must be numbers, e.g. 0x2000 and 0x01.", "error")
            return
        dtype = self.raw_type.currentText()
        text = self.raw_value.text().strip()
        try:
            value = float(text) if P.is_float(dtype) else int(text, 0)
        except ValueError:
            self._set_raw_status(f"'{text}' is not a valid {dtype} value.", "error")
            return
        extra = "\n\n⚠  A wrong value at this address can make the drive unreachable." if (index, sub) in RISKY else ""
        if not self._confirm(f"Write {value!r} ({dtype}) to 0x{index:04X}:0x{sub:02X} on node {self.s.node_id}?\n\n"
                             f"This bypasses every check the named parameter table has - range, known meaning, "
                             f"power-cycle warnings, whether the drive actually considers it writable.{extra}"):
            return
        reg = defs.Register(section="Raw", name=f"0x{index:04X}:0x{sub:02X}", index=index, sub=sub, dtype=dtype)

        def job(_emit, _cancel):
            return client.write(reg, value)

        def done(stored):
            self._set_raw_status(f"Wrote 0x{index:04X}:0x{sub:02X} = {stored} (read back and verified)", "ok")

        def failed(exc):
            self._set_raw_status(f"0x{index:04X}:0x{sub:02X}: {exc}", "error")

        run_async(job, on_done=done, on_failed=failed, owner=self)

    # ---- filter ----------------------------------------------------------------------------
    def _apply_filter(self, text):
        query = text.strip().lower()
        visible = {}
        for row in self.rows:
            match = not query or query in row.search
            row.item.setHidden(not match)
            visible[row.top] = visible.get(row.top, 0) + match
        for top in self._tops:
            n = visible.get(top, 0)
            top.setHidden(n == 0)
            if query and n:
                top.setExpanded(True)
