"""Motion > Axes: one row per connected axis (node, online, position, speed, fault bits) - the
whole-fleet status view real multi-axis needs (see the README's Multi-axis section), reusing each axis's own
`client.telemetry_snapshot()`/`telemetry_age()` (already broadcast, never requested - the project's own standing
rule) and `defs.fault_names_text()` (the same helper `DriveTab`'s own Fault Bits card uses) rather than any new
client-side machinery. Not per-axis itself: a single table listing every axis, like the Terminal and Motion
Controller stay single, workspace-level tools that address whichever axes they need."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QFrame, QScrollArea)

from .. import defs
from . import theme
from .axes_setup import AxesSetupPanel
from .widgets import mono_font

REFRESH_MS = 200
STALE_AFTER = 1.0
COLUMNS = ("Axis", "Node", "Status", "Position (counts)", "Speed", "Fault Bits")
COL_AXIS, COL_NODE, COL_STATUS, COL_POSITION, COL_SPEED, COL_FAULTS = range(6)
# Each column's own data alignment - Node/Position/Speed are numeric (right), the rest are text (left). Every
# header label gets its OWN matching alignment below, not one blanket setDefaultAlignment(): this table mixes
# left- and right-aligned columns, and a single blanket alignment was caught live as a real bug on Multi Axis
# Motion's own table for exactly that reason (see the README) - headers would land at the opposite edge from their
# own column's data instead of sitting above it.
COLUMN_GAP = 120                                                             # the whitespace after every column's widest
                                                                             # text (header or cell) - the eye reads the
                                                                             # gap between texts, not the column edges
                                                                             # (user, 2026-09-26: equal widths looked
                                                                             # unequal beside a long header); Fault Bits
                                                                             # takes the rest
COLUMN_ALIGNMENT = {col: Qt.AlignmentFlag.AlignHCenter for col in range(6)}  # every value centred under its own header
                                                                             # (user, 2026-09-26: "1 should be in the
                                                                             # middle of node is written, 0 should be in
                                                                             # the middle of position is written")


def drive_label(node, primary_node_id, primary_name):
    """The display name for a connected axis's own node: the primary drive's own name (its editable Target Name
    field on Item Configuration, "Drive01" by default - see main_window.py's `_target_name()`, the one place that
    already resolves it) for the primary's own node, `f"Drive{node:02d}"` for the others - both with " (SDR)" (the navigator's own
    additional-axis naming, `navigator.py`'s `add_axis_item`) for any other - shared by this page and Plotter2DPage
    (`plotter_2d.py`), the two other places a connected axis's own name is shown, so a rename on Item Configuration
    shows up everywhere consistently instead of only in the navigator/pill/badges (2026-09-24: this page and the
    Plotter used to hardcode the literal word "Primary" here instead, never reflecting a renamed drive)."""
    name = primary_name if node == primary_node_id else f"Drive{node:02d}"
    return f"{name} (SDR)"                                                       # the primary too (user, 2026-09-26:
                                                                                 # "add to Drive 01 SDR") - every row
                                                                                 # named the way the navigator names it


class AxesStatusPage(QWidget):
    message = Signal(str, str)      # (text, level) for the window's status bar - the setup panel's own, forwarded

    def __init__(self, session, primary_name, drive_tab_for=None, connection_of=None):
        """The setup panel (Additional Axes, Kinematics Model, Tool Pose - gui/axes_setup.py) on top, the table of
        every connected axis below it (user, 2026-09-26). `drive_tab_for` / `connection_of` as the panel needs."""
        super().__init__()
        self.s = session
        self._primary_name = primary_name    # callable () -> str, e.g. main_window.py's _target_name
        self._fault_names = defs.load_telemetry()[1]
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)
        self.setup = AxesSetupPanel(session, drive_tab_for, connection_of or (lambda: ("", 0)))
        self.setup.message.connect(self.message)
        outer.addWidget(self.setup)

        note = QLabel("Every connected axis - the primary drive and any added in Additional Axes above - each row "
                      "from that axis's own telemetry broadcast, never requested.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        outer.addWidget(note)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setFont(mono_font(12))
        head = self.table.horizontalHeader()
        for col in (COL_AXIS, COL_NODE, COL_STATUS, COL_POSITION, COL_SPEED):
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)          # sized by _fit_columns: widest text + GAP
        head.setSectionResizeMode(COL_FAULTS, QHeaderView.ResizeMode.Stretch)    # the last column reaches the table's edge,
                                                                                 # so the header's gold rule does too (user,
                                                                                 # 2026-09-26: "make sure yellow line
                                                                                 # reaches till end"); its content is
                                                                                 # centred in that wider column
        head.setHighlightSections(False)
        for col, alignment in COLUMN_ALIGNMENT.items():
            self.table.horizontalHeaderItem(col).setTextAlignment(alignment | Qt.AlignmentFlag.AlignVCenter)
        self._fit_columns()
        self.table.setMinimumHeight(220)
        outer.addWidget(self.table, 1)
        scroll.setWidget(body)
        page.addWidget(scroll)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(REFRESH_MS)
        session.connected.connect(self._refresh)
        session.disconnected.connect(self._refresh)
        session.axis_connected.connect(lambda _node: self._refresh())
        session.axis_disconnected.connect(lambda _node: self._refresh())
        self._refresh()

    def _refresh(self):
        clients = self.s.all_clients()
        rows = sorted(clients.items())
        changed = self.table.rowCount() != len(rows)            # the columns are refitted only when a text changed
        if changed:
            self.table.setRowCount(len(rows))
        for i, (node, client) in enumerate(rows):
            snap = client.telemetry_snapshot()
            age = client.telemetry_age()
            online = age is not None and age < STALE_AFTER
            fault_bits = int(snap["fault_bits"]) if "fault_bits" in snap else None
            values = [
                drive_label(node, self.s.node_id, self._primary_name()),
                str(node),
                "Online" if online else ("Waiting for telemetry…" if age is None else f"Stale ({age:.0f} s)"),
                f"{snap['wz_count']:,.0f}" if "wz_count" in snap else "—",
                (f"{snap['speed_hz']:,.2f} Hz" if "speed_hz" in snap else          # whichever speed the drive actually
                 f"{snap['wz_speed']:,.2f} rps" if "wz_speed" in snap else "—"),   # broadcasts - the reference default
                                                                                     # layout sends speed_hz, never
                                                                                     # wz_speed, so this column read "—"
                                                                                     # for every simulated/factory drive
                (defs.fault_names_text(fault_bits, self._fault_names) or "No Faults") if fault_bits is not None else "—",
            ]
            colours = [None, None, theme.EMERALD if online else theme.AMBER, None, None,
                      theme.RUBY if fault_bits else None]
            for col, (value, colour) in enumerate(zip(values, colours)):
                item = self.table.item(i, col)
                if item is None:
                    item = QTableWidgetItem()
                    item.setTextAlignment(COLUMN_ALIGNMENT[col] | Qt.AlignmentFlag.AlignVCenter)
                    self.table.setItem(i, col, item)
                    changed = True
                if item.text() != value:
                    item.setText(value)
                    changed = True
                item.setForeground(QColor(colour) if colour else self.table.palette().text())
        if changed:
            self._fit_columns()

    def _fit_columns(self):
        """Every column but the last as wide as its widest text (its header or any cell) plus COLUMN_GAP - so the blank
        after each entry is the same whatever the entry's length; only a change of width is applied (cheap per tick)."""
        head = self.table.horizontalHeader()
        for col in (COL_AXIS, COL_NODE, COL_STATUS, COL_POSITION, COL_SPEED):
            width = max(head.sectionSizeHint(col), self.table.sizeHintForColumn(col)) + COLUMN_GAP
            if self.table.columnWidth(col) != width:
                self.table.setColumnWidth(col, width)
