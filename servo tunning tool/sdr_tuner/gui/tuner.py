"""Quick Tuning and Advanced Tuning: wizards built around the drive's own automatic configuration.

On the SDR drive the tuning is done by the drive itself. One command - Autoconfiguration (0xD0), or the advanced one (0xD1) that also
takes the General System Dynamics, or the brushed-motor one (0xD4) - makes it measure the motor's resistance, inductance, back-EMF
constant and inertia, detect the Hall sensors, write the Datasheet, compute the Control Laws and save everything to its flash. The motor
makes rapid moves and beeps when the procedure starts and when it ends.

While it runs the drive stops itself if it hears nothing on the bus for the heartbeat timeout (3.5 s by default). The reference says the
command is sent 'continuously'; this page does not repeat it (a second copy could start the procedure again) but sends a harmless read
request every quarter of a second (`client.set_heartbeat`).

The procedure takes anywhere from about half a minute to a couple of minutes, so nothing is assumed about its length: once the user
has started it this page reads the values the procedure measures (resistance, inductance,
back-EMF constant, inertia, viscous damping, Hall sensors) from `FIRST_POLL_S` after the command and then every `POLL_S` seconds (user,
2026-09-26: check once at START, wait 20 s, then every 4 s). When any of them is different from what it was before the start
(one is enough: on a repeat run on the same motor most values come out the same), the automatic tuning has completed successfully and the
reading STOPS. The page then waits for the drive to leave Autoconfiguration (Operation Mode, if the broadcast carries it) and reads the
Datasheet and the Control Laws once, to show what changed. Nothing is read before the START button
is pressed and confirmed, and nothing after the run: the bus stays quiet. Operation Mode 3 is only a hint on the way. NOT yet tried on a
real drive.

Both wizards look like the vendor's tuners: a page tree on the left, the page on the right, << >> and Revert / Apply / Errors under it.
The forms show what the drive holds and write only when the user presses Apply (after a confirmation); the run starts only when every
check has passed and the user has confirmed it; STOP / Esc ends it. On the real drive the Stop command works in the velocity, position
and other modes but NOT in the configuration mode; only Reset ends the automatic configuration. Reset also clears the drive's errors and
cuts the current supply to the motor. So that is what STOP sends
here, in the background, together with closing the connection or any other STOP in the window while a tuning runs."""
import time
from collections import namedtuple

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import defs, protocol as P
from ..client import DriveError, DriveTimeout
from . import theme
from .params_tab import DIRTY_STYLE, make_editor, tooltip_html
from .widgets import ComboBox, DoubleSpinBox, fold_on_single_click, make_button, set_prop
from .workers import run_async

MOTOR_TYPE, MAX_CURRENT, POLES = (0x2000, 0x01), (0x2000, 0x02), (0x2000, 0x03)
BRUSHED = 1                                     # Motor Type: 0 = brushless, 1 = brushed / voice coil
MODE_AUTOCONFIG = defs.OP_MODE_AUTOCONFIG       # Operation Mode: Autoconfiguration (the drive's Stop does nothing in it)
HEARTBEAT_TIMEOUT = (0x3000, 0x04)              # CANopen: heartbeat timeout: the drive stops itself after this long without a message
HEARTBEAT = 0.5                                 # seconds between the harmless requests that keep the drive going, if that is not known
TICK_MS = 200                                   # how often a run is looked at
NO_BUS_VOLTS = 5.0                              # below this the power stage is not supplied
FIRST_POLL_S = 20.0                             # the Datasheet is read once at START (the 'before' values), then not again before this
                                                # many seconds after the command - the procedure needs at least that long (user, 2026-09-26)
POLL_S = 4.0                                    # ... and from then on this often - it takes minutes, no need to check more often (and it
                                                # keeps the bus quieter); was 3 s from the start until 2026-09-26
START_TIMEOUT, RUN_LIMIT = 8.0, 900.0           # seconds: after this the page says the drive has not reported Autoconfiguration (only a hint),
                                                # and the longest a run may take without any change in the Datasheet
SETTLE_MIN = 0.5                                # seconds after the Datasheet changed before the results are read (longer while the drive says it works)
SETTLE_MAX = 10.0                               # ... but never more than this: the Datasheet change IS completion, this is only a courtesy
                                                 # wait for Control Laws/flash to follow - it must not turn into an open-ended second wait
NO_CHANGE_WAIT = 5.0                            # seconds after the drive left Autoconfiguration before the page remarks that the Datasheet has
                                                # not changed (only a remark: the run goes on - the drive may pass through other modes)
MEASURED_KEYS = ((0x2000, 0x04), (0x2000, 0x05), (0x2000, 0x06), (0x2000, 0x08), (0x2000, 0x07), (0x2000, 0x09))
# What the procedure measures or detects: phase resistance, inductance, back-EMF constant, inertia, viscous damping, Hall sensors.
# The two values it was given (maximum current, poles) are not among them: the drive may store those at once, before it has measured anything.
MIN_CHANGED = 1                                 # how many of them must differ from before the start: ONE. A repeat run on the same motor measures
                                                # nearly the same values again, so a couple of changed values (even one) is enough, never all of them
DYNAMICS_RANGE, DYNAMICS_DEFAULT = (1.5, 32.0), 3.16      # General System Dynamics: 1.5 - 15 nominal, up to 32 as a last resort
DYNAMICS_PRESETS = (("Direct drive (default)", 3.16), ("Gearbox or traction drive  (3.5 - 5)", 4.0),
                    ("High speed, low inductance  (5 - 15)", 8.0), ("Last resort  (12 - 32)", 16.0))

IDLE, PREPARING, RUNNING, FINISHING, READING, DONE, FAILED, STOPPED = ("idle", "preparing", "running", "finishing", "reading", "done",
                                                                       "failed", "stopped")
BUSY = (PREPARING, RUNNING, FINISHING, READING)
Check = namedtuple("Check", "level text")       # level: "ok", "warn" or "block"
SYMBOL = {"ok": ("✔", theme.EMERALD), "warn": ("⚠", theme.AMBER), "block": ("✖", theme.RUBY)}

MOTOR_ENCODER, SERVO_ENCODER, HALL_SENSORS = (0x2000, 0x0A), (0x2000, 0x0B), (0x2000, 0x09)
ENCODER_SECTIONS = {1: "Peripheral: Quadrature Encoder", 2: "Peripheral: SSI/BISS-C Encoder", 3: "Peripheral: SPI Encoder",
                    4: "Peripheral: PWM Encoder", 5: "Peripheral: RS485 Encoder", 6: "Peripheral: Dual Encoder"}     # Encoder type -> its settings
SENSORED = 1                                    # Hall Sensors: 0 = sensorless, 1 = sensored

QUICK_INTRO = ("Enter the motor's maximum continuous current and its number of rotor poles. The next page, Feedback Settings, is for "
               "the encoders, if the machine has any. The drive's automatic configuration then measures the motor and tunes itself - "
               "see Automatic Tuning.")
FEEDBACK_INTRO = ("Choose the encoder of the motor and the encoder on the load side: the settings of each encoder you choose appear "
                  "below it. The Hall sensors are detected by the automatic tuning.")
ADVANCED_INTRO = ("The motor and its transmission. The automatic configuration needs the maximum continuous current and the number "
                  "of rotor poles; the gear ratio and ball-screw lead are for the units of a servo.")


def _registers():
    return {r.key: r for r in defs.load_registers()}


def same_value(reg, a, b):
    """Are two values of a parameter the same as far as the drive can tell (the precision of its data type)?"""
    if a is None or b is None:
        return a is b
    if P.is_float(reg.dtype):
        return P.wire_value(reg.dtype, a) == P.wire_value(reg.dtype, b)     # FLOAT16: the nearest 1/256 on the wire
    return int(a) == int(b)


def show_value(reg, value):
    if value is None:
        return "—"
    if reg.options and int(value) in reg.options:
        return reg.options[int(value)]
    if P.is_float(reg.dtype):
        return f"{float(value):.6g}"
    return str(int(value))


def heartbeat_period(timeout):
    """How often to send a harmless request during a run: a quarter of the drive's own heartbeat timeout (3.5 s by default), never more
    often than 4 times a second and never less than once a second - enough to be heard, and the bus stays quiet."""
    return max(0.25, min(1.0, float(timeout) / 4)) if timeout else HEARTBEAT


def _duration(seconds):
    return f"{seconds / 60:.0f} minutes" if seconds >= 60 else f"{seconds:.0f} s"


def fault_names(bits):
    options = _registers()[(0x4000, 0x03)].options
    names = [options.get(1 << n, f"bit {n}") for n in range(16) if bits >> n & 1]
    return ", ".join(names) or "unknown"


def snapshot_registers():
    """The parameters the automatic configuration writes: the Datasheet and the Control Laws."""
    return [r for r in defs.load_registers() if r.section in ("Datasheet", "Control Laws")]


def read_registers(client, regs, cancel=None):
    """{key: value} of every register that can be read. Raises DriveTimeout when the drive does not answer at all."""
    values = {}
    for reg in regs:
        if cancel is not None and cancel.is_set():
            break
        try:
            values[reg.key] = client.read(reg)
        except DriveTimeout:
            raise
        except DriveError:
            pass                                    # one parameter that cannot be read does not spoil the rest
    return values


class Row:
    """One parameter on a form: its label, its editor, and what the drive holds."""

    def __init__(self, reg):
        self.reg = reg
        self.label = self.widget = self.get = self.set = self.units = None
        self.last = None            # the value the drive holds (None: not read yet)
        self.touched = False        # the user edited it, so an empty or wrong entry is an error rather than 'not read yet'
        self.refused = ""           # why the drive refused the last write
        self.hidden = False         # its group is not shown now, so it is left out of what counts as edited


class FormPage(QScrollArea):
    """A page of parameters, in groups. Shows what the drive holds; edits are marked and written by the tuner (Apply).

    A group is (heading, keys) or (heading, keys, condition). With a condition it is shown only while condition(form) is true,
    e.g. the settings of the encoder type chosen above it; heading may also be a function of the form. The rows of a group that
    is not shown are left out of what counts as edited or wrong."""
    changed = Signal()
    submitted = Signal()            # Enter was pressed in one of this page's boxes - the container's own Apply/Write, not a second action

    def __init__(self, title, groups, intro=""):
        super().__init__()
        self.title = title
        self.rows = {}
        self._groups = []
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.setWidget(body)
        grid = QGridLayout(body)
        grid.setContentsMargins(22, 14, 22, 18)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        grid.setColumnMinimumWidth(0, 360)
        grid.setColumnMinimumWidth(1, 250)
        grid.setColumnMinimumWidth(2, 110)
        grid.setColumnStretch(3, 1)
        line = 0
        if intro:
            note = QLabel(intro)
            note.setObjectName("formNote")
            note.setWordWrap(True)
            grid.addWidget(note, line, 0, 1, 4)
            line += 1
        regs = _registers()
        for heading, keys, *condition in groups:
            head = QLabel()
            head.setObjectName("groupTitle")
            grid.addWidget(head, line, 0, 1, 4)
            line += 1
            rows = []
            for key in keys:
                row = self._make_row(regs[key])
                grid.addWidget(row.label, line, 0)
                grid.addWidget(row.widget, line, 1)
                grid.addWidget(row.units, line, 2)
                rows.append(row)
                line += 1
            self._groups.append({"heading": heading, "condition": condition[0] if condition else None, "head": head, "rows": rows,
                                 "shown": None})
        grid.setRowStretch(line, 1)
        self._update_groups()

    def _make_row(self, reg):
        row = Row(reg)
        row.widget, row.get, row.set = make_editor(reg, lambda: self._edited(row),
                                                   on_submit=(lambda: self.submitted.emit()) if reg.writable else None)
        if not reg.writable:                                # this register's own access mark: nothing to edit or write -
            if isinstance(row.widget, QLineEdit):            # same treatment ParamsTab's own rows already get
                row.widget.setReadOnly(True)
            else:
                row.widget.setEnabled(False)
        row.widget.setMinimumWidth(240)
        row.label = QLabel(reg.name)
        row.label.setObjectName("formName")
        row.units = QLabel("" if reg.options or reg.dtype == "BOOL" else reg.units)
        row.units.setObjectName("formUnits")
        for w in (row.label, row.widget):
            w.setToolTip(tooltip_html(reg))
        self.rows[reg.key] = row
        return row

    # ---- groups that depend on a choice -----------------------------------------------------
    def selected(self, key):
        """The value in a parameter's editor, or None if it is empty or not valid (used to decide what to show)."""
        try:
            return self.rows[key].get()
        except ValueError:
            return None

    def _update_groups(self):
        for group in self._groups:
            heading, condition = group["heading"], group["condition"]
            show = True if condition is None else bool(condition(self))
            group["head"].setText((heading(self) if callable(heading) else heading).upper())
            if show == group["shown"]:
                continue
            group["shown"] = show
            for widget in [group["head"]] + [w for r in group["rows"] for w in (r.label, r.widget, r.units)]:
                widget.setVisible(show)
            for row in group["rows"]:
                row.hidden = not show
                self._paint(row)

    # ---- state ------------------------------------------------------------------------------
    def _edited(self, row):
        row.touched, row.refused = True, ""
        self._paint(row)
        self._update_groups()
        self.changed.emit()

    def _state(self, row):
        """-> ("", "") clean, ("dirty", "") edited, or ("error", why)."""
        if row.hidden:
            return "", ""
        if row.refused:
            return "error", row.refused
        if not row.touched and row.last is None:
            return "", ""                                   # nothing read yet, nothing typed
        try:
            value = row.get()
        except ValueError as exc:
            return ("error", str(exc)) if row.touched else ("", "")
        return ("dirty", "") if row.last is None or not same_value(row.reg, value, row.last) else ("", "")

    def _paint(self, row):
        kind, why = self._state(row)
        set_prop(row.label, "state", {"dirty": "warn", "error": "error"}.get(kind, ""))
        if not isinstance(row.widget, QCheckBox):
            row.widget.setStyleSheet({"dirty": DIRTY_STYLE, "error": f"border: 1px solid {theme.RUBY};"}.get(kind, ""))
        tip = tooltip_html(row.reg)
        row.widget.setToolTip(tip if not why else f"<b style='color:{theme.RUBY}'>{why}</b><br>{tip}")

    @property
    def is_dirty(self):
        return any(self._state(r)[0] == "dirty" for r in self.rows.values())

    def errors(self):
        return [f"{r.reg.name}: {why}" for r in self.rows.values() for kind, why in [self._state(r)] if kind == "error"]

    def dirty_count(self):
        return sum(1 for r in self.rows.values() if self._state(r)[0] == "dirty")

    def collect(self):
        """-> ([(row, value)] the edited values, [errors])."""
        items = []
        for row in self.rows.values():
            kind, why = self._state(row)
            if kind == "dirty":
                items.append((row, row.get()))
        return items, self.errors()

    def value(self, key):
        """The value in the editor; ValueError('<parameter>: <why>') if it is empty or wrong."""
        row = self.rows[key]
        try:
            return row.get()
        except ValueError as exc:
            raise ValueError(f"{row.reg.name}: {exc}") from None

    # ---- what the tuner does to it ----------------------------------------------------------
    def show_drive_value(self, key, value):
        row = self.rows[key]
        row.set(value)
        row.last, row.touched, row.refused = value, False, ""
        self._paint(row)
        self._update_groups()
        self.changed.emit()

    def refuse(self, key, why):
        row = self.rows[key]
        row.refused = why
        self._paint(row)
        self.changed.emit()

    def revert(self):
        for row in self.rows.values():
            if row.last is not None:
                row.set(row.last)
            row.touched, row.refused = False, ""
            self._paint(row)
        self._update_groups()
        self.changed.emit()


class ChangesTable(QTableWidget):
    """What a run changed in the drive: parameter, value before, value after."""

    def __init__(self):
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["PARAMETER", "BEFORE", "AFTER", "UNITS"])
        self.verticalHeader().hide()
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setAlternatingRowColors(True)
        head = self.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.setMinimumHeight(170)

    def show_changes(self, changes):
        self.setRowCount(0)
        for reg, before, after in changes or ():
            line = self.rowCount()
            self.insertRow(line)
            for column, text in enumerate((reg.name, show_value(reg, before), show_value(reg, after), reg.units)):
                item = QTableWidgetItem(text)
                if column in (1, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(line, column, item)
            self.item(line, 2).setForeground(QBrush(QColor(theme.GOLD_HI)))


def card(title):
    """-> (frame, layout) a titled panel."""
    frame = QFrame()
    frame.setObjectName("tunerCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 14)
    layout.setSpacing(6)
    head = QLabel(title.upper())
    head.setObjectName("cardTitle")
    layout.addWidget(head)
    return frame, layout


class AutoTuningPage(QScrollArea):
    """Runs the drive's automatic configuration: checks first, a confirmation, then the single command while it watches the drive."""
    state_changed = Signal(str)
    finished = Signal(object)               # [(register, value before, value after)] the run changed
    results_read = Signal(object)           # {register key: value}: what the drive holds after the run (the pages show it)
    message = Signal(str, str)              # (text, level) for the window's status bar

    def __init__(self, session, motor, advanced=False):
        super().__init__()
        self.title = "Automatic Tuning"
        self.s, self.motor, self.advanced = session, motor, advanced
        self.state = IDLE
        self.changes = None                 # what the last completed run changed, or None if there has not been one
        self.last_error = ""
        self._token = 0                     # a run's number: results of an older run (or one that was stopped) are ignored
        self._before, self._t0, self._seen, self._key = {}, 0.0, False, ""
        self._left_at = None                # when the drive left the automatic configuration (Operation Mode)
        self._polls, self._polling, self._next_poll = 0, False, 0.0        # the Datasheet readings of a run
        self._detected_at, self._detected_keys = 0.0, []                   # when the Datasheet changed, and which values
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(22, 14, 22, 18)
        col.setSpacing(12)

        text = ("The drive measures the motor and tunes itself. It measures the phase resistance, the inductance, the back-EMF constant and "
                "the inertia, detects the Hall sensors, writes the Datasheet, computes the Control Laws and saves them to its flash.")
        if advanced:
            text += (" The advanced procedure also takes the General System Dynamics: the higher it is, the more aggressive the control "
                     "laws it computes.")
        note = QLabel(text + "\n\nThe motor makes rapid moves and beeps when the procedure starts and when it ends. Keep the shaft free "
                     "and everybody clear of it, and keep a hand on STOP (or Esc).\n\nIt takes anywhere from about half a minute to a couple of "
                     "minutes, so the time is not assumed: the values it measures are read from the Datasheet once at the start, then "
                     f"from {FIRST_POLL_S:g} s after the command every {POLL_S:g} s, "
                     "and the tuning is complete as soon as they change.")
        note.setObjectName("formNote")
        note.setWordWrap(True)
        col.addWidget(note)

        box, lay = card("Sent to the drive")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self._input_labels = {}
        for line, (key, name) in enumerate(((MOTOR_TYPE, "Motor type"), (MAX_CURRENT, "Maximum continuous current (line-to-line)"),
                                            (POLES, "Poles number (rotor poles)"))):
            grid.addWidget(QLabel(name), line, 0)
            value = QLabel("—")
            value.setObjectName("formName")
            grid.addWidget(value, line, 1)
            self._input_labels[key] = value
        grid.setColumnStretch(2, 1)
        self.dynamics = DoubleSpinBox()
        self.presets = ComboBox()
        self.dynamics_note = QLabel("")
        self.dynamics_note.setObjectName("formNote")
        if advanced:
            self.dynamics.setRange(*DYNAMICS_RANGE)
            self.dynamics.setDecimals(2)
            self.dynamics.setSingleStep(0.1)
            self.dynamics.setValue(DYNAMICS_DEFAULT)
            self.dynamics.setFixedWidth(96)
            self.dynamics.setToolTip("General System Dynamics: 1.5 - 15 is the normal range; 12 - 32 only as a last resort")
            for name, value in DYNAMICS_PRESETS:
                self.presets.addItem(name, value)
            self.presets.setMinimumWidth(280)
            self.presets.activated.connect(lambda _i=0: self.dynamics.setValue(self.presets.currentData()))
            self.dynamics.valueChanged.connect(self._dynamics_changed)
            grid.addWidget(QLabel("General system dynamics"), 3, 0)
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            row.addWidget(self.dynamics)
            row.addWidget(self.presets)
            row.addStretch(1)
            grid.addWidget(holder, 3, 1, 1, 2)
        lay.addLayout(grid)
        if advanced:
            lay.addWidget(self.dynamics_note)
            self._dynamics_changed()
        col.addWidget(box)

        box, lay = card("Before it starts")
        self.check_list = QLabel("")
        self.check_list.setObjectName("checkList")
        self.check_list.setTextFormat(Qt.TextFormat.RichText)
        self.check_list.setWordWrap(True)
        lay.addWidget(self.check_list)
        col.addWidget(box)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.start_btn = make_button(QPushButton("START AUTOMATIC TUNING"), "primary")
        self.stop_btn = make_button(QPushButton("STOP"), "danger")
        self.stop_btn.setToolTip("End the procedure: the drive is sent a Reset, which stops it, clears the drive's errors and cuts the "
                                 "current supply to the motor.   Shortcut: Esc")
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        col.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("bigStatus")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        col.addWidget(self.status)
        col.addWidget(self.progress)

        box, lay = card("What the procedure changed in the drive")
        self.table = ChangesTable()
        self.table_note = QLabel("Nothing has been run yet.")
        self.table_note.setObjectName("formNote")
        lay.addWidget(self.table_note)
        lay.addWidget(self.table)
        col.addWidget(box)
        col.addStretch(1)

        self.start_btn.clicked.connect(lambda _c=False: self.start())
        self.stop_btn.clicked.connect(lambda _c=False: self.stop())
        self.motor.changed.connect(self.refresh)
        self._monitor = QTimer(self)                # looks at a run
        self._monitor.setInterval(TICK_MS)
        self._monitor.timeout.connect(self._tick)
        self._live = QTimer(self)                   # refreshes the checks while the page is open
        self._live.setInterval(500)
        self._live.timeout.connect(self.refresh)
        self.refresh()

    # ---- what is sent ----------------------------------------------------------------------
    def _dynamics_changed(self, _value=0.0):
        value = self.dynamics.value()
        nominal = value <= 15.0
        self.dynamics_note.setText("" if nominal else "Above 15 is a last resort: use it only if the normal range does not give a "
                                                       "stable system.")
        self.presets.blockSignals(True)
        self.presets.setCurrentIndex(next((i for i, (_n, v) in enumerate(DYNAMICS_PRESETS) if abs(v - value) < 1e-9), -1))
        self.presets.blockSignals(False)

    def command(self):
        """-> (command key, values) from the motor settings; ValueError says what is wrong."""
        kind, current, poles = self.motor.value(MOTOR_TYPE), self.motor.value(MAX_CURRENT), self.motor.value(POLES)
        if not current > 0:
            raise ValueError("Maximum continuous current must be more than 0 A.")
        if poles < 2 or poles % 2:
            raise ValueError("Poles number must be an even number, at least 2.")
        if kind == BRUSHED:
            key, values = "autoconfiguration_brushed_motor", (current, poles)
        elif self.advanced:
            key, values = "autoconfiguration_advanced_brushless_motor", (current, poles, round(self.dynamics.value(), 2))
        else:
            key, values = "autoconfiguration_brushless_motor", (current, poles)
        for param, value in zip(defs.load_commands()[key].params, values):
            try:
                P.encode_value(param.dtype, value)
            except ValueError as exc:
                raise ValueError(f"{param.name}: {exc}") from None
        return key, values

    def checks(self):
        """[Check]: what stands in the way of a run ('block'), what deserves a second look ('warn'), what is fine ('ok')."""
        client = self.s.client
        if client is None:
            return [Check("block", "Not connected to a drive.")]
        out = []
        try:
            self.command()
            out.append(Check("ok", "The motor data are valid."))
        except ValueError as exc:
            out.append(Check("block", str(exc)))
        snap, age = client.telemetry_snapshot(), client.telemetry_age()
        if not snap or age is None or age > 2.0:
            out.append(Check("block", "No telemetry is arriving from the drive. Its broadcast is needed to watch the procedure."))
            return out
        mode = snap.get("op_mode")
        if mode is None:
            out.append(Check("warn", "Operation Mode is not in the drive's telemetry broadcast: it could not be checked whether the "
                                     "drive is already running the automatic configuration."))
        elif int(mode) == MODE_AUTOCONFIG:
            out.append(Check("ok", "The drive is running the automatic configuration.") if self.busy
                       else Check("block", "The drive is already running the automatic configuration."))
        faults = snap.get("fault_bits")
        if faults is None:
            out.append(Check("warn", "Fault Bits are not in the telemetry broadcast: the drive could not be checked for faults."))
        elif int(faults):
            out.append(Check("block", f"The drive reports a fault: {self.fault_names(int(faults))}. Clear it first (RESET on Single Axis Motion)."))
        else:
            out.append(Check("ok", "The drive reports no fault."))
        vbus = snap.get("vbus")
        if vbus is None:
            out.append(Check("warn", "The DC bus voltage is not in the telemetry broadcast: the motor supply could not be checked."))
        elif float(vbus) < NO_BUS_VOLTS:
            out.append(Check("block", f"The DC bus is at {float(vbus):.1f} V: the power stage is not supplied, the motor cannot be measured."))
        else:
            out.append(Check("ok", f"The DC bus is at {float(vbus):.1f} V."))
        if client.commanded is not None:
            out.append(Check("block", "A speed command is running. Press STOP first."))
        return out

    fault_names = staticmethod(fault_names)

    # ---- the page ---------------------------------------------------------------------------
    @property
    def busy(self):
        return self.state in BUSY

    def showEvent(self, event):
        super().showEvent(event)
        self._live.start()
        self.refresh()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._live.stop()

    def refresh(self):
        """Shows the motor data that will be sent and the checks, and enables the buttons."""
        for key, label in self._input_labels.items():
            try:
                value = self.motor.value(key)
                reg = self.motor.rows[key].reg
                label.setText(show_value(reg, value) + (f"  {reg.units}" if reg.units and not reg.options else ""))
                set_prop(label, "state", "")
            except ValueError as exc:
                label.setText(f"{exc}" if self.motor.rows[key].touched else "not read from the drive yet")
                set_prop(label, "state", "error" if self.motor.rows[key].touched else "")
        if self.advanced:
            brushed = self._is_brushed()
            self.dynamics.setEnabled(not brushed and not self.busy)
            self.presets.setEnabled(not brushed and not self.busy)
            if brushed:
                self.dynamics_note.setText("Not used for a brushed motor.")
            else:
                self._dynamics_changed()
        checks = self.checks()
        lines = []
        for level, text in checks:
            mark, colour = SYMBOL[level]
            lines.append(f"<span style='color:{colour}'>{mark}</span>&nbsp; {text}")
        html_text = "<br>".join(lines)
        if self.check_list.text() != html_text:
            self.check_list.setText(html_text)
        blocked = any(c.level == "block" for c in checks)
        self.start_btn.setEnabled(not self.busy and not blocked)
        self.stop_btn.setEnabled(self.busy)
        self.progress.setVisible(self.busy)
        if self.state == IDLE:
            first = next((c.text for c in checks if c.level == "block"), "")
            self._set_status(first or "Ready to start.", "warn" if first else "ok")

    def _is_brushed(self):
        try:
            return self.motor.value(MOTOR_TYPE) == BRUSHED
        except ValueError:
            return False

    def _set_status(self, text, level=""):
        self.status.setText(text)
        set_prop(self.status, "state", level)

    def _say(self, text, level="info"):
        self.message.emit(text, level)

    def _set_state(self, state):
        self.state = state
        self.refresh()
        self.state_changed.emit(state)

    # ---- a run ------------------------------------------------------------------------------
    def _confirm(self, text):
        return QMessageBox.question(self, "Automatic Tuning", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def confirmation_text(self, key, values, warnings):
        cmd = defs.load_commands()[key]
        sent = "\n".join(f"    {p.name}: {v:g}" for p, v in zip(cmd.params, values))
        extra = "".join(f"\n⚠  {w}" for w in warnings)
        return (f"Run '{cmd.name}' on node {self.s.node_id}?\n\n{sent}\n\n"
                "•  The motor makes rapid moves and beeps when the procedure starts and when it ends. Keep the shaft free and everybody clear.\n"
                "•  The Datasheet and the Control Laws in the drive are overwritten and saved to its flash.\n"
                f"•  It takes from about half a minute to a couple of minutes. The Datasheet is read once now, then from {FIRST_POLL_S:g} s "
                f"after the start every {POLL_S:g} s; when it changes, the tuning is complete.\n"
                "•  Press STOP (or Esc) at any time to end it: the drive is sent a Reset, which clears its errors and cuts the current supply "
                "to the motor." + (f"\n{extra}" if extra else ""))

    def start(self):
        client = self.s.client
        if client is None or self.busy:
            return
        checks = self.checks()
        blocks = [c.text for c in checks if c.level == "block"]
        if blocks:
            self._say(f"Cannot start: {blocks[0]}", "warn")
            self.refresh()
            return
        key, values = self.command()
        if not self._confirm(self.confirmation_text(key, values, [c.text for c in checks if c.level == "warn"])):
            self._say("Automatic tuning cancelled - nothing was sent.", "info")
            return
        self._token += 1
        token = self._token
        self._key, self.last_error = key, ""
        self._set_state(PREPARING)
        self._set_status("Reading the drive's present values…")
        regs = snapshot_registers() + [_registers()[HEARTBEAT_TIMEOUT]]
        run_async(lambda emit, cancel: read_registers(client, regs, cancel),
                  on_done=lambda before: self._prepared(token, client, key, values, before),
                  on_failed=lambda exc: self._fail(token, f"the drive could not be read: {exc}", reset_drive=False), owner=self)

    def _prepared(self, token, client, key, values, before):
        if token != self._token or self.state != PREPARING:
            return                                          # STOP was pressed meanwhile
        if client is not self.s.client:
            return self._fail(token, "the connection was closed", reset_drive=False)
        if not before:
            return self._fail(token, "no parameter could be read from the drive", reset_drive=False)
        try:
            client.send_command(key, *values)
        except (DriveError, ValueError) as exc:
            return self._fail(token, f"the command could not be sent: {exc}", reset_drive=False)
        client.set_heartbeat(heartbeat_period(before.get(HEARTBEAT_TIMEOUT)))
        now = time.monotonic()
        self._before, self._t0, self._seen, self._left_at = before, now, False, None
        self._polls, self._polling, self._next_poll, self._detected_keys = 0, False, now + FIRST_POLL_S, []
        self._set_state(RUNNING)
        self._set_status(self._progress_text(False, 0.0), "warn")
        self._say("Automatic tuning: command sent to the drive.", "warn")
        self._monitor.start()

    def _tick(self):
        client = self.s.client
        if self.state not in (RUNNING, FINISHING) or client is None:
            self._monitor.stop()
            return
        now = time.monotonic()
        elapsed = now - self._t0
        snap = client.telemetry_snapshot()
        mode, faults = snap.get("op_mode"), int(snap.get("fault_bits") or 0)
        in_mode = mode is not None and int(mode) == MODE_AUTOCONFIG
        if in_mode:
            self._seen, self._left_at = True, None
        elif self._seen and self._left_at is None:
            self._left_at = now                                     # the drive has left the automatic configuration
        if self.state == FINISHING:                                 # the Datasheet already changed: the tuning IS complete; this is only
            waited = now - self._detected_at                        # a short, capped courtesy wait for Control Laws/flash to follow
            if in_mode and waited < SETTLE_MAX:                      # it still says it is working: give it a little longer, but not forever
                self._set_status(self._finishing_text(waited, working=True), "ok")
            elif waited >= SETTLE_MIN:
                self._completed(elapsed)
            return
        if faults and not in_mode:
            return self._fail(self._token, f"the drive reported a fault: {self.fault_names(faults)}", reset_drive=False)
        if not self._polling and now >= self._next_poll:
            self._poll(client, self._token, now)
        if elapsed > RUN_LIMIT:                                     # only the Datasheet says it is over; this is a ceiling, far above a real run
            return self._fail(self._token, f"the Datasheet did not change within {_duration(RUN_LIMIT)} - stopped", reset_drive=True)
        self._set_status(self._progress_text(in_mode, elapsed, mode is not None, 0.0 if self._left_at is None else now - self._left_at), "warn")

    def _progress_text(self, in_mode, elapsed, mode_known=True, left_for=0.0):
        # no "Datasheet checked N times" any more (user, 2026-09-26: "we don't need to show how many times you checked")
        if in_mode:
            head = "Running: the drive is measuring the motor…"
        elif self._seen and left_for >= NO_CHANGE_WAIT:
            head = "The drive has left the automatic configuration, but the Datasheet has not changed yet. Still watching - press STOP to end it."
        elif self._seen:
            head = "The drive has left the automatic configuration. Waiting for the Datasheet to change…"
        elif mode_known and elapsed > START_TIMEOUT:
            head = "The drive has not reported the automatic configuration yet. Still watching the Datasheet…"
        elif elapsed > START_TIMEOUT:
            head = "Waiting for the Datasheet to change…"
        else:
            head = "Command sent. Waiting for the drive to start…"
        return f"{head}  {elapsed:.0f} s"

    def _finishing_text(self, waited, working=False):
        head = (f"The Datasheet has been updated ({len(self._detected_keys)} of {len(MEASURED_KEYS)} measured values changed): "
                "the automatic tuning is complete.")
        return f"{head} " + (f"The drive is still finishing (control laws, flash)…  {waited:.0f} s" if working else "Waiting for the drive to finish…")

    def _poll(self, client, token, now):
        """Reads the values of the Datasheet that the procedure measures, in a worker, every POLL_S (the first FIRST_POLL_S after
        the command) - only while a run that the user started is going. A drive that does not answer while it works is asked again."""
        self._polling, self._next_poll = True, now + POLL_S
        regs = [_registers()[k] for k in MEASURED_KEYS]

        def job(emit, cancel):
            try:
                return read_registers(client, regs, cancel)
            except DriveTimeout:
                return None

        run_async(job, on_done=lambda values: self._polled(token, values), on_failed=lambda exc: self._polled(token, None), owner=self)

    def _polled(self, token, values):
        if token != self._token:
            return                                                  # a reading of an older run, or of a run that was stopped
        self._polling = False
        if self.state != RUNNING or not values:
            return
        self._polls += 1
        regs = _registers()
        changed = [k for k in MEASURED_KEYS if k in values and k in self._before
                   and not same_value(regs[k], self._before[k], values[k])]
        if len(changed) >= MIN_CHANGED:
            self._detected_at, self._detected_keys = time.monotonic(), changed
            self._set_state(FINISHING)
            self._set_status(self._finishing_text(0.0), "ok")
            self._say(f"Automatic tuning: the Datasheet has been updated ({len(changed)} of {len(MEASURED_KEYS)} measured values changed).", "ok")

    def _completed(self, elapsed):
        client = self.s.client
        self._monitor.stop()
        client.set_heartbeat(None)                                  # from here the drive is not asked for anything but the final values
        token = self._token
        self._set_state(READING)
        self._set_status("The drive has finished. Reading the new values…")
        run_async(lambda emit, cancel: read_registers(client, snapshot_registers(), cancel),
                  on_done=lambda after: self._read_back(token, elapsed, after),
                  on_failed=lambda exc: self._fail(token, f"the drive finished, but the new values could not be read: {exc}", reset_drive=False), owner=self)

    def _read_back(self, token, elapsed, after):
        if token != self._token or self.state != READING:
            return
        client = self.s.client
        faults = 0
        if client is not None:
            faults = int(client.telemetry_snapshot().get("fault_bits") or 0)
        regs = {r.key: r for r in snapshot_registers()}
        self.changes = [(regs[k], self._before.get(k), v) for k, v in after.items()
                        if k in self._before and not same_value(regs[k], self._before[k], v)]
        self.table.show_changes(self.changes)
        n = len(self.changes)
        self.table_note.setText(f"{n} value(s) changed. The drive saves them to its flash by itself." if n
                                else "The procedure ended but no value in the Datasheet or the Control Laws changed.")
        text, level = "Auto tuning completed successfully.", "ok"          # the table below already lists what changed
        self._say(f"Automatic tuning finished: {n} value(s) changed in the drive.", "ok")
        if faults:
            text, level = f"{text} The drive now reports a fault: {self.fault_names(faults)}.", "warn"
        self._set_status(text, level)
        self._set_state(DONE)
        self.results_read.emit(dict(after))
        self.finished.emit(self.changes)

    def _fail(self, token, why, reset_drive):
        if token != self._token:
            return
        self._token += 1
        self._monitor.stop()
        client = self.s.client
        if client is not None:
            client.set_heartbeat(None)
            if reset_drive:
                self._reset_drive()
        self.last_error = why
        self._set_state(FAILED)
        self._set_status(f"Automatic tuning failed: {why}.", "error")
        self._say(f"Automatic tuning failed: {why}.", "error")

    def _reset_drive(self):
        """Sends the drive's Reset: it ends the automatic configuration (Stop does not), clears the drive's errors and cuts the current
        supply to the motor. One frame, sent at once from the window's thread. True if it could be sent."""
        client = self.s.client
        if client is None:
            return False
        try:
            client.reset()
        except DriveError:
            return False
        return True

    def stop(self, why="", send_reset=True):
        """Ends the run: the drive is sent a Reset (once the command has gone out) and the page stops watching. The values the drive holds
        are then whatever it had written."""
        if not self.busy:
            return
        running = self.state != PREPARING                           # before the command has been sent nothing is going on in the drive
        self._token += 1
        self._monitor.stop()
        client = self.s.client
        reset_sent = False
        if client is not None:
            client.set_heartbeat(None)
            if send_reset and running:
                reset_sent = self._reset_drive()
        self._set_state(STOPPED)
        parts = [f"Stopped: {why}." if why else "Stopped."]
        if reset_sent:
            parts.append("A Reset was sent to the drive: it ends the automatic configuration, clears the drive's errors and cuts the "
                         "current supply to the motor.")
        elif send_reset and running:
            parts.append("The Reset could NOT be sent - press RESET on the Single Axis Motion page.")
        if running:
            parts.append("The Datasheet and the Control Laws may not have been updated - load them from the drive before using the motor.")
        self._set_status(" ".join(parts), "warn" if reset_sent or not (send_reset and running) else "error")
        self._say("Automatic tuning stopped" + (" - Reset sent to the drive." if reset_sent else "."), "warn")

    def emergency(self):
        """STOP / Esc, from anywhere in the window."""
        self.stop()

    def connection_closing(self):
        """The connection is about to be closed: end the run while the drive can still be told."""
        self.stop("the connection to the drive is being closed")

    def connection_closed(self):
        """The link to the drive is gone (nothing can be sent any more)."""
        if self.busy:
            self.stop("the connection to the drive was closed", send_reset=False)
        self.refresh()


TESTS = (   # command key -> (label, amplitude name, amplitude units, telemetry key to watch, Operation Mode while it runs)
    ("testing_electronic_speed_control_esc", "Speed (Electronic Speed Control)", "Amplitude: Speed", "Hz", "speed_hz", 16),
    ("testing_field_oriented_control_foc", "Current (Field Oriented Control)", "Amplitude: Iq current", "A", "ia", 15),
    ("testing_iq_current_control", "Iq Current", "Amplitude: Iq current", "A", "ia", 20),
    ("testing_servo_control", "Servo (position)", "Amplitude: Work Zone Counts", "counts", "wz_count", 17),
)


WAVE_FORMS = ("0 - Square Wave", "1 - Square Wave, Positive only", "2 - Sine Wave",
              "3 - Frequency Sweep (1/Period up to ~450 Hz over 10 Periods, repeating)",
              "4 - Frequency Sweep with Linear Amplitude Decay", "5 - Frequency Sweep with Quadratic Amplitude Decay")
# Wave form 3 measured on the bench drive (2026-09-25, Iq loop, Period 0.1 and 1 s): starts at 1/Period, rises faster
# than exponentially and reaches a few hundred Hz only in the last few percent of a cycle of 10 x Period, then
# starts over; the amplitude stays constant. Forms 4/5 are assumed to sweep the same way (not measured).
# The drive's own Wave Form values for its four Testing commands (0xB0 FOC, 0xB4 ESC, 0xB8 Servo, 0xBC Iq) - Device
# Reference pp. 89-91/95. The field used to be a bare 0-5 number with a tooltip claiming the reference does not say
# what each draws; it does (found 2026-09-25 while checking whether the drive can support a Bode plot: 2 and 3-5 are
# exactly the sine / swept-sine excitation such a measurement needs).


class StepResponsePage(QScrollArea):
    """Runs one of the drive's built-in step-response tests: a square wave into the current, speed or servo loop, so the
    result can be watched (best in the Recorder) to check the automatic tuning or to fine-tune a gain or a notch filter by
    hand. The test is continuous like the speed command - re-sent automatically until STOP - and, like the speed command,
    starting one replaces whatever the drive was already being sent.

    NOT tried on a real drive: this assumes Stop ends a running test (unlike the automatic configuration's own
    'configuration' mode, where only Reset does)."""
    message = Signal(str, str)      # (text, level) for the window's status bar
    open_recorder = Signal()        # 'Open in Recorder' was pressed

    def __init__(self, session):
        super().__init__()
        self.title = "Step Response Tests"
        self.s = session
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(22, 14, 22, 18)
        col.setSpacing(12)

        note = QLabel("The drive drives a square wave into the loop you choose, reversing sign every Period. Watch the response on the "
                      "Recorder - current for the current-loop tests, speed for the speed loop, Work Zone Count for the servo loop - to "
                      "check the automatic tuning, or to fine-tune a gain or a notch filter by hand. It runs until you press STOP; "
                      "starting a test replaces any speed command or other test already running.")
        note.setObjectName("formNote")
        note.setWordWrap(True)
        col.addWidget(note)

        box, lay = card("Test")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        self.kind = ComboBox()
        for key, label, _amp_name, _units, _watch, _mode in TESTS:
            self.kind.addItem(label, key)
        self.period = QLineEdit("0.5")
        self.period.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.amplitude = QLineEdit("")
        self.amplitude.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.wave_form = ComboBox()
        for label in WAVE_FORMS:                 # index = the drive's own Wave Form value (0-5)
            self.wave_form.addItem(label)
        self.wave_form.setToolTip("The reference profile the test drives the loop with (Device Reference, the four "
                                  "Testing commands): a square wave reverses every Period; a sine wave is the basis "
                                  "of a frequency-response (Bode) measurement at 1/Period Hz; the sweeps rise in "
                                  "frequency, optionally with the amplitude decaying - their frequency range is not "
                                  "documented.")
        self.amplitude_label = QLabel("")
        self.amplitude_units = QLabel("")
        grid.addWidget(QLabel("Test"), 0, 0)
        grid.addWidget(self.kind, 0, 1, 1, 2)
        grid.addWidget(QLabel("Period"), 1, 0)
        grid.addWidget(self.period, 1, 1)
        grid.addWidget(QLabel("sec"), 1, 2)
        grid.addWidget(self.amplitude_label, 2, 0)
        grid.addWidget(self.amplitude, 2, 1)
        grid.addWidget(self.amplitude_units, 2, 2)
        grid.addWidget(QLabel("Wave Form"), 3, 0)
        grid.addWidget(self.wave_form, 3, 1, 1, 2)
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)
        col.addWidget(box)

        box, lay = card("Before it starts")
        self.check_list = QLabel("")
        self.check_list.setObjectName("checkList")
        self.check_list.setTextFormat(Qt.TextFormat.RichText)
        self.check_list.setWordWrap(True)
        lay.addWidget(self.check_list)
        col.addWidget(box)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.start_btn = make_button(QPushButton("START TEST"), "primary")
        self.stop_btn = make_button(QPushButton("STOP"), "danger")
        self.stop_btn.setToolTip("End the test.   Shortcut: Esc")
        self.recorder_btn = QPushButton("Open in Recorder")
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.recorder_btn)
        row.addStretch(1)
        col.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("bigStatus")
        self.status.setWordWrap(True)
        col.addWidget(self.status)

        box, lay = card("Live")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.live_watch = QLabel("")
        self.live_op_mode = QLabel("")
        grid.addWidget(QLabel("Watching"), 0, 0)
        grid.addWidget(self.live_watch, 0, 1)
        grid.addWidget(QLabel("Operation Mode"), 1, 0)
        grid.addWidget(self.live_op_mode, 1, 1)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)
        col.addWidget(box)
        col.addStretch(1)

        self.kind.currentIndexChanged.connect(self._kind_changed)
        for field in (self.period, self.amplitude):
            field.textEdited.connect(lambda _t="": self.refresh())
        self.wave_form.currentIndexChanged.connect(lambda _i=0: self.refresh())
        self.start_btn.clicked.connect(lambda _c=False: self.start())
        self.stop_btn.clicked.connect(lambda _c=False: self.stop())
        self.recorder_btn.clicked.connect(lambda _c=False: self.open_recorder.emit())
        self._kind_changed()
        self._live = QTimer(self)
        self._live.setInterval(400)
        self._live.timeout.connect(self.refresh)
        self.refresh()

    def _test(self):
        return TESTS[self.kind.currentIndex()]

    def _kind_changed(self, _index=0):
        _key, _label, amp_name, units, _watch, _mode = self._test()
        self.amplitude_label.setText(amp_name)
        self.amplitude_units.setText(units)
        self.refresh()

    # ---- the page ---------------------------------------------------------------------------
    @property
    def running(self):
        client = self.s.client
        return client is not None and client.commanded is not None and client.commanded[0] == self._test()[0]

    def showEvent(self, event):
        super().showEvent(event)
        self._live.start()
        self.refresh()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._live.stop()

    def values(self):
        """-> (period, amplitude, wave_form); ValueError says what is wrong."""
        try:
            period = float(self.period.text().strip())
        except ValueError:
            raise ValueError("Period must be a number") from None
        if not period > 0:
            raise ValueError("Period must be more than 0 s")
        try:
            amplitude = float(self.amplitude.text().strip())
        except ValueError:
            raise ValueError(f"{self._test()[2]} must be a number") from None
        wave_form = self.wave_form.currentIndex()          # the combo's order IS the drive's own 0-5 numbering
        key = self._test()[0]
        for param, value in zip(defs.load_commands()[key].params, (period, amplitude, wave_form)):
            try:
                P.encode_value(param.dtype, value)
            except ValueError as exc:
                raise ValueError(f"{param.name}: {exc}") from None
        return period, amplitude, wave_form

    def checks(self):
        client = self.s.client
        if client is None:
            return [Check("block", "Not connected to a drive.")]
        out = []
        try:
            self.values()
            out.append(Check("ok", "The test's values are valid."))
        except ValueError as exc:
            out.append(Check("block", str(exc)))
        snap, age = client.telemetry_snapshot(), client.telemetry_age()
        if not snap or age is None or age > 2.0:
            out.append(Check("block", "No telemetry is arriving from the drive."))
            return out
        mode = snap.get("op_mode")
        if mode is not None and int(mode) == MODE_AUTOCONFIG:
            out.append(Check("block", "The drive is running the automatic configuration."))
        faults = snap.get("fault_bits")
        if faults is None:
            out.append(Check("warn", "Fault Bits are not in the telemetry broadcast: the drive could not be checked for faults."))
        elif int(faults):
            out.append(Check("block", f"The drive reports a fault: {fault_names(int(faults))}. Clear it first (RESET on Single Axis Motion)."))
        else:
            out.append(Check("ok", "The drive reports no fault."))
        vbus = snap.get("vbus")
        if vbus is not None and float(vbus) < NO_BUS_VOLTS:
            out.append(Check("block", f"The DC bus is at {float(vbus):.1f} V: the power stage is not supplied."))
        if client.commanded is not None and not self.running:
            name = defs.load_commands()[client.commanded[0]].name
            out.append(Check("warn", f"'{name}' is running: starting this test will replace it."))
        return out

    def refresh(self):
        checks = self.checks()
        lines = [f"<span style='color:{SYMBOL[level][1]}'>{SYMBOL[level][0]}</span>&nbsp; {text}" for level, text in checks]
        html_text = "<br>".join(lines)
        if self.check_list.text() != html_text:
            self.check_list.setText(html_text)
        running = self.running
        self.start_btn.setEnabled(not running and not any(c.level == "block" for c in checks))
        self.stop_btn.setEnabled(running)
        self.kind.setEnabled(not running)
        client = self.s.client
        _key, _label, _amp_name, units, watch, mode_value = self._test()
        if client is not None:
            snap = client.telemetry_snapshot()
            value = snap.get(watch)
            self.live_watch.setText("—" if value is None else f"{value:.4g} {units if watch != 'wz_count' else 'counts'}")
            live_mode = snap.get("op_mode")
            names = _registers()[(0x4000, 0x02)].options
            self.live_op_mode.setText("—" if live_mode is None else names.get(int(live_mode), str(int(live_mode))))
            set_prop(self.live_op_mode, "state", "ok" if running and live_mode is not None and int(live_mode) == mode_value else "")
        else:
            self.live_watch.setText("—")
            self.live_op_mode.setText("—")
        if running:
            self._set_status(f"Running '{self._test()[1]}' - watch the response on the Recorder. Press STOP to end it.", "ok")
        else:
            first = next((c.text for c in checks if c.level == "block"), "")
            self._set_status(first or "Ready to start.", "warn" if first else "ok")

    def _set_status(self, text, level=""):
        self.status.setText(text)
        set_prop(self.status, "state", level)

    def _confirm(self, text):
        return QMessageBox.question(self, "Step Response Test", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def start(self):
        client = self.s.client
        if client is None:
            return
        checks = self.checks()
        blocks = [c.text for c in checks if c.level == "block"]
        if blocks:
            self.message.emit(f"Cannot start: {blocks[0]}", "warn")
            self.refresh()
            return
        try:
            period, amplitude, wave_form = self.values()
        except ValueError as exc:
            self.message.emit(f"Cannot start: {exc}", "warn")
            self.refresh()
            return
        key, label, amp_name, units, _watch, _mode = self._test()
        warnings = "".join(f"\n⚠  {c.text}" for c in checks if c.level == "warn")
        if not self._confirm(f"Run '{label}' on node {self.s.node_id}?\n\n    Period: {period:g} sec\n    {amp_name}: {amplitude:g} {units}\n"
                             f"    Wave Form: {wave_form}\n\n•  The motor moves continuously while the test runs.\n"
                             "•  Press STOP (or Esc) at any time to end it." + (f"\n{warnings}" if warnings else "")):
            self._set_status("Test cancelled - nothing was sent.", "info")
            self.message.emit("Test cancelled - nothing was sent.", "info")
            return
        try:
            client.run_test(key, period, amplitude, wave_form)
        except (DriveError, ValueError) as exc:
            self.message.emit(f"Could not start: {exc}", "error")
            return
        self.message.emit(f"Started '{label}'.", "warn")
        self.refresh()

    def stop(self):
        client = self.s.client
        if client is None or not self.running:
            return
        try:
            client.stop()
        except DriveError as exc:
            self.message.emit(f"STOP failed: {exc}", "error")
            return
        self.message.emit("Test stopped.", "warn")
        self.refresh()

    def emergency(self):
        """STOP / Esc, from anywhere in the window."""
        if self.running:
            self.stop()

    def connection_closing(self):
        self.emergency()

    def connection_closed(self):
        self.refresh()


class SummaryPage(QScrollArea):
    """Recommended actions before leaving the Advanced tuner."""

    def __init__(self, tuner):
        super().__init__()
        self.title = "Summary"
        self.tuner = tuner
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(22, 14, 22, 18)
        col.setSpacing(12)
        box, lay = card("Recommended actions before leaving the tuner")
        self.list = QLabel("")
        self.list.setObjectName("checkList")
        self.list.setTextFormat(Qt.TextFormat.RichText)
        self.list.setWordWrap(True)
        lay.addWidget(self.list)
        col.addWidget(box)
        box, lay = card("Changed by the last automatic tuning")
        self.table = ChangesTable()
        lay.addWidget(self.table)
        col.addWidget(box)
        col.addStretch(1)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        auto = self.tuner.auto
        lines = []
        if auto.changes is None:
            lines.append(("warn", "The automatic tuning has not been run in this session."))
        else:
            lines.append(("ok", f"The automatic tuning completed successfully: the Datasheet was updated and {len(auto.changes)} value(s) changed."))
        unapplied = [f.title for f in self.tuner.forms if f.is_dirty]
        lines.append(("warn", "Changes not applied yet on: " + ", ".join(unapplied) + ".") if unapplied
                     else ("ok", "Every value on the pages has been applied (or reverted)."))
        text = "<br>".join(f"<span style='color:{SYMBOL[level][1]}'>{SYMBOL[level][0]}</span>&nbsp; {t}" for level, t in lines)
        text += ("<br><br>•&nbsp; Try the motor at a low speed in Single Axis Motion and watch the current in the Recorder.<br>"
                 "•&nbsp; Save the parameters to a file (File, Save…) so that this tuning can be restored.")
        self.list.setText(text)
        self.table.show_changes(auto.changes)


class TunerPage(QWidget):
    """The wizard: a page tree, the pages, << >> and Revert / Apply / Errors."""
    message = Signal(str, str)      # (text, level) for the window's status bar
    page_changed = Signal(str)      # the title of the page now shown (the window's breadcrumb)
    state_changed = Signal()        # buttons may need refreshing (the window's ribbon)

    def __init__(self, session, advanced=False):
        super().__init__()
        self.s, self.advanced = session, advanced
        self.forms, self.pages = [], []         # forms: the parameter pages; pages: every page in order
        self._io, self._loaded = False, False
        self._items = []

        if advanced:
            self.motor = FormPage("Motor Settings", [("Motor", [MOTOR_TYPE, MAX_CURRENT, POLES]),
                                                     ("Transmission", [(0x2000, 0x0C), (0x2000, 0x0D)])], ADVANCED_INTRO)
        else:
            self.motor = FormPage("Motor Settings", [("Motor", [MOTOR_TYPE, MAX_CURRENT, POLES])], QUICK_INTRO)
        self.feedback = FormPage("Feedback Settings", self._feedback_groups(), FEEDBACK_INTRO)
        self.auto = AutoTuningPage(session, self.motor, advanced)
        layout = [("Motor and Feedback", [self.motor, self.feedback]), self.auto]
        if advanced:
            self.data = FormPage("Measured Motor Data", [("Measured by the automatic tuning", [
                (0x2000, 0x04), (0x2000, 0x05), (0x2000, 0x06), (0x2000, 0x08), (0x2000, 0x07)])],
                "The drive writes these when the automatic tuning ends. They can be corrected by hand.")
            self.laws = FormPage("Control Laws", [
                ("Speed filter", [(0x2004, 0x73), (0x2002, 0x02)]),
                ("Field oriented control (current loop)", [(0x2002, 0x12), (0x2002, 0x13), (0x2002, 0x14),
                                                            (0x2004, 0x42), (0x2004, 0x43)]),
                ("Electronic speed control", [(0x2002, 0x22), (0x2002, 0x23), (0x2004, 0x52)]),
                ("Servo", [(0x2002, 0x32), (0x2004, 0x74), (0x2002, 0x33), (0x2002, 0x38), (0x2002, 0x37), (0x2002, 0x34),
                          (0x2002, 0x36)]),
                ("Micro-speeding and sensorless", [(0x2002, 0x72), (0x2002, 0x54), (0x2002, 0x52), (0x2002, 0x53),
                                                   (0x2002, 0x62), (0x2002, 0x63)])],
                "The drive computes the gains when the automatic tuning ends; change them only if you know what they do. The features "
                "next to a gain turn the control it belongs to on or off.")
            self.filters = FormPage("Filters", [
                ("Notch filter 0: output torque", [(0x2014, 0x02), (0x2014, 0x03), (0x2014, 0x04)]),
                ("Notch filter 1: speed filter", [(0x2015, 0x02), (0x2015, 0x03), (0x2015, 0x04)])],
                "A notch filter removes one frequency from the control loop, typically a mechanical resonance. Center it on the "
                "resonance the Recorder shows and set the bandwidth around it.")
            self.application = FormPage("Application Settings", [
                ("Acceleration / deceleration", [(0x2048, 0x04), (0x2048, 0x05), (0x2048, 0x06), (0x2048, 0x03)]),
                ("Work zone", [(0x2034, 0xB1), (0x2034, 0xB2), (0x2034, 0xB3), (0x2034, 0xB0), (0x2034, 0xC1), (0x2034, 0xC2)]),
                ("Starting techniques", [(0x2004, 0x02), (0x2004, 0x03), (0x2004, 0x62), (0x2004, 0x44)]),
                ("Power generation (starter-generator)", [(0x2049, 0x03), (0x2049, 0x04)])],
                "Settings of the application built on the motor: how it accelerates and decelerates, the travel it is allowed "
                "(Work Zone), how it starts (Kickstart, Micro-Speeding, Gimbal Control) and, for a starter-generator, how it "
                "regulates while generating power.")
            self.limits = FormPage("Limits and Protections", [
                ("Faults", [(0x2044, 0x02), (0x2044, 0x04)]),
                ("Electromagnetic brake", [(0x2025, 0x12), (0x2025, 0x10), (0x2025, 0x11), (0x2025, 0x03), (0x2025, 0x04),
                                          (0x2025, 0x07)]),
                ("Virtual brake", [(0x2024, 0x10), (0x2024, 0x03), (0x2024, 0x04), (0x2024, 0x05)])],
                "What protects the motor and the mechanism: which faults are reported, the current limit factor, and the "
                "electromagnetic and virtual brakes.")
            self.steps = StepResponsePage(session)
            from .bode_page import FrequencyResponsePage      # here, not at the top: bode_page imports this module's helpers
            self.bode = FrequencyResponsePage(session)
            from .autotest_page import AutomaticTestPage      # same reason (it imports card from here)
            self.auto_test = AutomaticTestPage(session)       # under Verification, below Frequency Response (user, 2026-09-25)
            self.summary = SummaryPage(self)
            layout += [("Results", [self.data, self.laws, self.filters]),
                      ("Application", [self.application, self.limits]),
                      ("Verification", [self.steps, self.bode, self.auto_test]), self.summary]

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        left = QVBoxLayout()
        left.setContentsMargins(8, 10, 6, 8)
        self.tree = QTreeWidget()
        self.tree.setObjectName("pageTree")
        self.tree.setHeaderHidden(True)
        self.tree.setFixedWidth(232)
        self.tree.setIndentation(16)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        fold_on_single_click(self.tree)      # a heading folds/unfolds on one click
        left.addWidget(self.tree)
        outer.addLayout(left)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        bar = QFrame()
        bar.setObjectName("tunerBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(8)
        self.prev_btn, self.next_btn = QPushButton("<<"), QPushButton(">>")
        self.revert_btn, self.apply_btn = QPushButton("REVERT"), make_button(QPushButton("APPLY"), "primary")
        self.errors_btn = QPushButton("ERRORS…")
        self.prev_btn.setToolTip("Previous page")
        self.next_btn.setToolTip("Next page")
        self.revert_btn.setToolTip("Put back the values the drive holds, on this page")
        self.apply_btn.setToolTip("Write the changed values of this page to the drive")
        self.errors_btn.setToolTip("List the values that are not valid")
        for w in (self.prev_btn, self.next_btn):
            w.setFixedWidth(64)
        row.addWidget(self.prev_btn)
        row.addWidget(self.next_btn)
        row.addStretch(1)
        row.addWidget(self.revert_btn)
        row.addWidget(self.apply_btn)
        row.addWidget(self.errors_btn)
        right.addWidget(bar)
        outer.addLayout(right, 1)

        for entry in layout:
            if isinstance(entry, tuple):                    # a heading with pages under it
                heading, pages = entry
                group = QTreeWidgetItem(self.tree, [heading])
                group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                for page in pages:
                    self._add_page(page, group)
            else:
                self._add_page(entry, self.tree)
        self.tree.expandAll()

        self.tree.currentItemChanged.connect(self._tree_changed)
        self.prev_btn.clicked.connect(lambda _c=False: self.step(-1))
        self.next_btn.clicked.connect(lambda _c=False: self.step(1))
        self.revert_btn.clicked.connect(lambda _c=False: self.revert_page())
        self.apply_btn.clicked.connect(lambda _c=False: self.apply_page())
        self.errors_btn.clicked.connect(lambda _c=False: self.show_errors())
        self.auto.state_changed.connect(lambda _s: self._sync())
        self.auto.message.connect(self.message)
        self.auto.results_read.connect(self._show_results)
        session.probed.connect(self._probed)
        session.disconnecting.connect(self.auto.connection_closing)
        session.disconnected.connect(self._disconnected)
        if advanced:
            self.steps.message.connect(self.message)
            session.disconnecting.connect(self.steps.connection_closing)
            self.bode.message.connect(self.message)
            session.disconnecting.connect(self.bode.connection_closing)
            self.auto_test.message.connect(self.message)      # it watches the session's own signals itself
        self.show_page(0)

    @staticmethod
    def _feedback_groups():
        """The selectors, then the settings of whichever encoders are chosen (and of the Hall sensors when the motor has them)."""
        groups = [("Sensors", [HALL_SENSORS, MOTOR_ENCODER, SERVO_ENCODER, (0x2004, 0x72)])]
        regs = defs.load_registers()

        def uses(kind):
            return lambda f: kind in (f.selected(MOTOR_ENCODER), f.selected(SERVO_ENCODER))

        def heading(kind, name):
            def text(f):
                roles = [role for role, key in (("motor encoder", MOTOR_ENCODER), ("servo encoder", SERVO_ENCODER)) if f.selected(key) == kind]
                return f"{name} settings" + (f"  ({' and '.join(roles)})" if roles else "")
            return text

        for kind, section in ENCODER_SECTIONS.items():
            keys = [r.key for r in regs if r.section == section]
            groups.append((heading(kind, section.split(": ", 1)[1]), keys, uses(kind)))
        hall = [r.key for r in regs if r.section == "Peripheral: Hall Sensors"]
        groups.append(("Hall sensors settings", hall, lambda f: f.selected(HALL_SENSORS) == SENSORED))
        return groups

    # ---- pages ------------------------------------------------------------------------------
    def _add_page(self, page, parent):
        item = QTreeWidgetItem(parent, [page.title])
        item.setData(0, Qt.ItemDataRole.UserRole, len(self.pages))
        self._items.append(item)
        self.pages.append(page)
        self.stack.addWidget(page)
        if isinstance(page, FormPage):
            self.forms.append(page)
            page.changed.connect(self._sync)
            page.submitted.connect(self.apply_page)      # Enter in a box applies THIS page's changes, like clicking Apply

    @property
    def index(self):
        return self.stack.currentIndex()

    def current_title(self):
        return self._items[self.index].text(0).rstrip(" ●")

    def current_form(self):
        page = self.pages[self.index]
        return page if isinstance(page, FormPage) else None

    def show_page(self, index):
        index = max(0, min(index, len(self.pages) - 1))
        self.stack.setCurrentIndex(index)
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(self._items[index])
        self.tree.blockSignals(False)
        self._sync()
        self.page_changed.emit(self.current_title())
        form = self.current_form()
        if form is not None and form.errors():
            self.message.emit("One or more invalid parameters on page", "warn")

    def step(self, direction):
        if not self.auto.busy:
            self.show_page(self.index + direction)

    def _tree_changed(self, item, _previous=None):
        if item is None:
            return
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is None and item.childCount():                       # a group heading: go to its first page
            page = item.child(0).data(0, Qt.ItemDataRole.UserRole)
        if page is not None:
            self.show_page(int(page))

    def go_to(self, page):
        self.show_page(self.pages.index(page))

    # ---- state ------------------------------------------------------------------------------
    @property
    def busy(self):
        return self._io or self.auto.busy

    @property
    def connected(self):
        return self.s.is_connected

    def can_apply(self, forms=None):
        forms = [self.current_form()] if forms is None else forms
        return self.connected and not self.busy and any(f is not None and f.is_dirty for f in forms)

    def can_revert(self, forms=None):
        forms = [self.current_form()] if forms is None else forms
        return not self.busy and any(f is not None and f.is_dirty for f in forms)

    def _sync(self):
        self.apply_btn.setEnabled(self.can_apply())
        self.revert_btn.setEnabled(self.can_revert())
        self.prev_btn.setEnabled(self.index > 0 and not self.auto.busy)
        self.next_btn.setEnabled(self.index < len(self.pages) - 1 and not self.auto.busy)
        self.tree.setEnabled(not self.auto.busy)
        n = len(self.errors())
        self.errors_btn.setText(f"ERRORS… ({n})" if n else "ERRORS…")
        for item, page in zip(self._items, self.pages):
            dirty = isinstance(page, FormPage) and page.is_dirty
            bad = isinstance(page, FormPage) and bool(page.errors())
            item.setText(0, item.text(0).rstrip(" ●") + (" ●" if dirty else ""))
            item.setForeground(0, QBrush(QColor(theme.RUBY if bad else theme.AMBER if dirty else theme.TEXT)))
        self.state_changed.emit()

    def errors(self):
        return [e for f in self.forms for e in f.errors()]

    def show_errors(self):
        errors = self.errors()
        if errors:
            QMessageBox.warning(self, "Errors", "These values are not valid:\n\n" + "\n".join(errors))
        else:
            QMessageBox.information(self, "Errors", "No errors: every value is valid.")

    # ---- reading and writing ----------------------------------------------------------------
    def _confirm(self, text):
        return QMessageBox.question(self, "Write parameters", text) == QMessageBox.StandardButton.Yes

    def _set_io(self, busy):
        self._io = busy
        self._sync()

    def load_all(self):
        """Read every parameter of every page from the drive (the vendor tuner's 'Drive Load')."""
        client = self.s.client
        if client is None:
            self.message.emit("Connect to a drive first.", "warn")
            return
        if self.busy:
            return
        where = {row.reg.key: form for form in self.forms for row in form.rows.values()}
        regs = [form.rows[key].reg for key, form in where.items()]

        def job(emit, cancel):
            for reg in regs:
                if cancel.is_set():
                    break
                try:
                    emit((reg.key, client.read(reg), None))
                except DriveTimeout as exc:
                    emit((reg.key, None, exc))
                    raise DriveTimeout("the drive is not answering - stopped") from None
                except DriveError as exc:
                    emit((reg.key, None, exc))
            return len(regs)

        failed = []

        def progress(payload):
            key, value, exc = payload
            if exc is not None:
                failed.append(key)
            else:
                where[key].show_drive_value(key, value)

        def done(n):
            self._loaded = True
            self._set_io(False)
            self.message.emit(f"Read {n - len(failed)} parameter(s) from the drive" + (f" - {len(failed)} could not be read." if failed else "."),
                              "warn" if failed else "info")

        def failed_job(exc):
            self._set_io(False)
            self.message.emit(f"Stopped: {exc}", "error")

        self._set_io(True)
        run_async(job, on_done=done, on_failed=failed_job, on_progress=progress, owner=self)

    def apply_page(self):
        form = self.current_form()
        if form is not None:
            self._apply([form])

    def apply_all(self):
        self._apply(self.forms)

    def revert_page(self):
        form = self.current_form()
        if form is not None and not self.busy:
            form.revert()

    def revert_all(self):
        if not self.busy:
            for form in self.forms:
                form.revert()

    def _apply(self, forms):
        client = self.s.client
        if client is None:
            self.message.emit("Connect to a drive first.", "warn")
            return
        if self.busy:
            return
        items, errors = [], []
        for form in forms:
            got, bad = form.collect()
            items += [(form, row, value) for row, value in got]
            errors += bad
        if errors:
            self.message.emit(f"Cannot apply: {errors[0]}" + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""), "error")
            return
        if not items:
            self.message.emit("Nothing to apply: no value was changed.", "info")
            return
        warning = "⚠  The motor is currently being commanded to run.\n\n" if client.commanded is not None else ""
        lines = [f"    {row.reg.name}: {show_value(row.reg, row.last)}  →  {show_value(row.reg, value)}" for _f, row, value in items[:10]]
        if len(items) > 10:
            lines.append(f"    … and {len(items) - 10} more")
        if not self._confirm(f"{warning}Write {len(items)} changed parameter(s) to node {self.s.node_id}?\n\n" + "\n".join(lines)):
            return

        def job(emit, cancel):
            for form, row, value in items:
                if cancel.is_set():
                    break
                try:
                    emit((form, row, client.write(row.reg, value), None))
                except DriveTimeout as exc:
                    emit((form, row, None, exc))
                    raise DriveTimeout("the drive is not answering - stopped") from None
                except (DriveError, ValueError) as exc:
                    emit((form, row, None, exc))
            return len(items)

        failed = []

        def progress(payload):
            form, row, stored, exc = payload
            if exc is not None:
                failed.append(row)
                form.refuse(row.reg.key, str(exc))
            else:
                form.show_drive_value(row.reg.key, stored)

        def done(n):
            self._set_io(False)
            self.message.emit(f"Applied {n - len(failed)} parameter(s)" + (f" - {len(failed)} refused by the drive." if failed else "."),
                              "warn" if failed else "ok")

        def failed_job(exc):
            self._set_io(False)
            self.message.emit(f"Stopped: {exc}", "error")

        self._set_io(True)
        run_async(job, on_done=done, on_failed=failed_job, on_progress=progress, owner=self)

    # ---- the drive and the window -----------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded and self.connected and not self.busy:
            self.load_all()

    def _probed(self, ok, _message=""):
        self._loaded = False
        if ok and self.isVisible():
            self.load_all()
        self._sync()

    def _disconnected(self):
        self._loaded = False
        self.auto.connection_closed()
        if self.advanced:
            self.steps.connection_closed()
            self.bode.connection_closed()
        self._sync()

    def _show_results(self, values):
        """The drive changed its Datasheet and Control Laws: the pages show what the run has just read back (no second reading)."""
        where = {key: form for form in self.forms for key in form.rows}
        for key, value in values.items():
            if key in where:
                where[key].show_drive_value(key, value)

    def emergency(self):
        """STOP / Esc."""
        self.auto.emergency()
        if self.advanced:
            self.steps.emergency()
            self.bode.emergency()
            self.auto_test.emergency()
