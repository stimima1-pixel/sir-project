"""Motion > Multi Axis Motion: move several connected axes without writing G-code - one row per
connected axis (built the same way axes_page.py is), each with its own Work Zone Count target and MOVE button,
plus a MOVE ALL that dispatches every row at once. Distinct from a G-code Txx line (inverse kinematics, solved
across one chain) and from each axis's own single-axis Motion page - this is raw, UNcoordinated per-axis
positioning, several at a time from one page, matching Elmo Application Studio III's own "Multi Axis Motion" tool
by name (UI reference only, not a protocol reference - see the README). Reuses the exact command and safety check
DriveTab's own MOVE TO already uses - feedback_gate.check() then client.hold_position() - not a new
implementation of either."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..client import DriveError
from . import feedback_gate, theme
from .axes_page import COLUMN_GAP, drive_label
from .widgets import SpinBox, make_button

REFRESH_MS = 200
POSITION_RANGE = (-2_000_000_000, 2_000_000_000)     # Work Zone Count is INT32 - matches drive_tab.py's own constant
COLUMNS = ("Axis", "Node", "Position (counts)", "Target", "Command", "Status")   # Command: the MOVE button (user, 2026-09-26)
COL_AXIS, COL_NODE, COL_POSITION, COL_TARGET, COL_MOVE, COL_STATUS = range(6)
TARGET_PX, MOVE_PX = 220, 96     # the Target field (the full +-2,000,000,000 counts with its suffix) and the MOVE button
ROW_HEIGHT = 32


class _Row:
    def __init__(self, node, position_item, target_spin, move_btn, status_item):
        self.node = node
        self.position_item, self.target_spin = position_item, target_spin
        self.move_btn, self.status_item = move_btn, status_item


class MultiAxisMotionPage(QWidget):
    def __init__(self, session, primary_name):
        super().__init__()
        self.s = session
        self._primary_name = primary_name      # callable () -> str, e.g. main_window.py's _target_name
        self._rows = {}                         # node -> _Row
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        note = QLabel("Every connected axis, each with its own Work Zone Count target. MOVE sends just that "
                      "axis's own Servo command (the same one its own Motion page's MOVE TO does) and needs a "
                      "Motor Encoder or Servo Encoder configured, same as there. MOVE ALL dispatches every row "
                      "at once - each axis still moves independently, not coordinated together; for that, use a "
                      "G-code Txx line instead.")
        note.setWordWrap(True)
        note.setObjectName("formNote")
        outer.addWidget(note)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.move_all_btn = make_button(QPushButton("MOVE ALL"), "primary")
        bar.addWidget(self.move_all_btn)
        bar.addStretch(1)
        outer.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setShowGrid(False)
        head = self.table.horizontalHeader()
        for column in (COL_AXIS, COL_NODE, COL_POSITION, COL_TARGET, COL_MOVE):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)    # sized by _fit_columns: widest + the gap
        head.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Stretch)   # the last column reaches the table's edge
                                                                                # (the header's gold rule with it); its
                                                                                # centred text sits further from MOVE,
                                                                                # which stays where it is (user, 2026-09-26)
        head.setHighlightSections(False)
        # Same arrangement as the Axes page (user, 2026-09-26: "can you do the same here as well and arrange it"):
        # everything left-aligned, each column its widest text plus the same gap - the eye reads the blank AFTER a
        # text, so equal column widths looked unequal beside a long header. The Target field and the MOVE button keep
        # their own widths (TARGET_PX / MOVE_PX) with the gap after them.
        for column in range(self.table.columnCount()):                         # every value centred under its own header
            self.table.horizontalHeaderItem(column).setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._fit_columns()
        outer.addWidget(self.table, 1)

        self.move_all_btn.clicked.connect(self._move_all)
        session.connected.connect(self._refresh_rows)
        session.disconnected.connect(self._refresh_rows)
        session.axis_connected.connect(lambda _node: self._refresh_rows())
        session.axis_disconnected.connect(lambda _node: self._refresh_rows())
        session.node_detected.connect(lambda _node: self._refresh_rows())   # `connected` fires BEFORE the probe - on the
                                                                            # USB cable the drive's real node is only found
                                                                            # by listening afterwards, re-keying all_clients()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)
        self._refresh_rows()

    # ---- rows: one per connected axis, rebuilt whenever what's connected changes ---------------
    def _refresh_rows(self):
        wanted = sorted(self.s.all_clients())
        if wanted == sorted(self._rows):
            return
        # A simple full rebuild, not an attempt to preserve a mid-typed target across it: unlike Plotter2D's own
        # axis-picker combos (a CHOICE OF axis, still meaningful if that axis reconnects, so worth preserving -
        # see plotter_2d.py's own docstring), a target NUMBER a user was typing for a row that just disconnected
        # has no "still meaningful" case to carry forward.
        self.table.setRowCount(0)
        self._rows = {}
        for node in wanted:
            self._add_row(node)
        self._fit_columns()

    def _add_row(self, node):
        i = self.table.rowCount()
        self.table.insertRow(i)
        self.table.setItem(i, COL_AXIS, QTableWidgetItem(drive_label(node, self.s.node_id, self._primary_name())))
        self.table.item(i, COL_AXIS).setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        node_item = QTableWidgetItem(str(node))
        node_item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(i, COL_NODE, node_item)
        position_item = QTableWidgetItem("—")
        position_item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(i, COL_POSITION, position_item)

        target_spin = SpinBox()
        target_spin.setRange(*POSITION_RANGE)
        target_spin.setKeyboardTracking(False)
        target_spin.setSuffix(" counts")
        target_spin.setAlignment(Qt.AlignmentFlag.AlignRight)          # matches drive_tab.py's own pos_target
        self.table.setCellWidget(i, COL_TARGET, self._with_gap(target_spin, TARGET_PX))

        move_btn = make_button(QPushButton("MOVE"), size="small")
        self.table.setCellWidget(i, COL_MOVE, self._with_gap(move_btn, MOVE_PX))

        status_item = QTableWidgetItem("")
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(i, COL_STATUS, status_item)

        row = _Row(node, position_item, target_spin, move_btn, status_item)
        self._rows[node] = row
        move_btn.clicked.connect(lambda _c=False, r=row: self._move_row(r))

    @staticmethod
    def _with_gap(widget, width):
        """A cell widget at its own width, with the column's gap after it (a cell widget otherwise fills the column)."""
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)                             # no vertical margin: the row height is fixed,
        lay.setSpacing(0)                                               # and a margin clipped the MOVE button's bottom
        widget.setFixedWidth(width)
        lay.addStretch(1)
        lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)         # centred in the column, like every value
        lay.addStretch(1)
        return box

    def _fit_columns(self):
        """Every column but Status as wide as its widest text (header or cell) plus COLUMN_GAP; the two widget columns
        their widget's width plus the gap. Only a change of width is applied (cheap per tick)."""
        head = self.table.horizontalHeader()
        for column in (COL_AXIS, COL_NODE, COL_POSITION):
            width = max(head.sectionSizeHint(column), self.table.sizeHintForColumn(column)) + COLUMN_GAP
            if self.table.columnWidth(column) != width:
                self.table.setColumnWidth(column, width)
        for column, px in ((COL_TARGET, TARGET_PX), (COL_MOVE, MOVE_PX)):
            if self.table.columnWidth(column) != px + COLUMN_GAP:
                self.table.setColumnWidth(column, px + COLUMN_GAP)

    # ---- moving - the exact same command and check DriveTab's own MOVE TO uses -----------------
    def _move_row(self, row):
        client = self.s.all_clients().get(row.node)
        if client is None:
            return
        target = row.target_spin.value()

        def go():
            try:
                client.hold_position(target)
            except (DriveError, ValueError) as exc:
                self._say(row, f"Move failed: {exc}", "error")
                return
            self._say(row, f"Moving to {target:,} counts  ·  sent every 100 ms", "ok")

        feedback_gate.check(
            client, row.move_btn,
            on_progress=lambda text, state: self._say(row, text, state),
            on_ok=go,
            on_warn=lambda text: self._say(row, text, "warn"),
            on_failed=lambda exc: self._say(row, f"Could not check Feedback Settings: {exc}", "error"),
        )

    def _move_all(self):
        for row in self._rows.values():
            self._move_row(row)

    def _say(self, row, text, state):
        row.status_item.setText(text)
        colour = {"ok": theme.EMERALD, "warn": theme.AMBER, "error": theme.RUBY}.get(state)
        row.status_item.setForeground(QColor(colour) if colour else self.table.palette().text())

    # ---- live refresh: position only - never a new request, same rule as every other page ------
    def _tick(self):
        clients = self.s.all_clients()
        changed = False
        for node, row in self._rows.items():
            client = clients.get(node)
            snap = client.telemetry_snapshot() if client is not None else {}
            text = f"{snap['wz_count']:,.0f}" if "wz_count" in snap else "—"
            if row.position_item.text() != text:
                row.position_item.setText(text)
                changed = True
        if changed:                                             # the columns are refitted only when a text changed
            self._fit_columns()
