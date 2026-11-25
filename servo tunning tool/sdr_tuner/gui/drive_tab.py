"""DRIVE tab (Motion - Single Axis): speed control (ESC, in rpm), position control, live readouts, fault LEDs and
scrolling telemetry plots - the two things an operator does every day. The drive's special modes (current/torque,
cyclic position, direct field control, Kickstart, Power Generation, brushed open loop, Virtual Brake) live on the
DRIVE MODES page (drive_modes.py) since 2026-09-26, bound to this page for the one-repeat-at-a-time bookkeeping.

Everything shown comes from the drive's own telemetry broadcast, and only what the drive broadcasts: the cards and the
curves are built from the telemetry mapping read at connect. This tab never asks the drive for telemetry."""
from collections import namedtuple

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .. import defs, protocol as P
from ..client import DriveError
from . import feedback_gate, theme
from .live_plots import (CURVES, LivePlots, PLOTS, RPS_UNITS, SPEED_COLOURS, SPEED_UNITS, STALE_AFTER,   # noqa: F401
                         UNIT_LABELS, UNIT_MIN_SPAN, speed_shown)                                        # (re-exported)
from .widgets import ComboBox, DoubleSpinBox, Led, Readout, Slider, SpinBox, make_button, set_prop
from .workers import run_async

CARD_ORDER = ("speed_hz", "wz_speed", "vbus", "op_mode", "fault_bits", "ia", "ib", "wz_count", "comm_mode", "wz_modulo")   # then any others
CARD_TITLES = {"speed_hz": "Speed", "vbus": "DC bus", "op_mode": "Operation mode", "fault_bits": "Fault bits", "ia": "Phase A",
               "ib": "Phase B", "wz_count": "Work zone count", "comm_mode": "Commutation", "wz_modulo": "Work zone modulo"}
CARD_FORMATS = {"vbus": "{:.2f}", "ia": "{:.3f}", "ib": "{:.3f}", "wz_count": "{:,.0f}", "wz_modulo": "{:,.0f}"}
CARD_COLUMNS = 4
LEDS_PER_ROW = 7
UNIT_FORMATS = {"rpm": "{:.1f}", "rps": "{:.3f}", "degs": "{:.1f}", "hz": "{:.2f}"}
UNIT_SOURCE = {"rpm": defs.SPEED_RPS, "rps": defs.SPEED_RPS, "degs": defs.SPEED_RPS,   # the drive's telemetry value each
               "hz": defs.SPEED_HZ}                                                     # is taken from
KIND_LABELS = {"electrical": "an electrical frequency (Hz)", "rps": "revolutions per second", "rpm": "revolutions per minute"}
POSITION_RANGE = (-2_000_000_000, 2_000_000_000)    # Work Zone Count is INT32; kept a little inside the type's own limit
POSITION_UNITS = (("counts", 0), ("deg", 2))         # the unit a position is typed and shown in (label, decimals); degrees need
WZ_DIMENSION = (0x4000, 0x1C)                        # the drive's Work Zone: Dimension - counts per turn - read once for them
CURRENT_TORQUE = (("current_control_field_oriented_control_foc", "Current (FOC)", "A"),
                  ("electronic_torque_control_etc", "Torque (ETC)", "N·m"))   # command key, label, unit
STATUS_LINES = 4    # lines of height reserved for every status label, up front - see DriveTab._status_label
Info = namedtuple("Info", "field options speed")   # a broadcast value: its TpdoField, {value: name} if enumerated, speed kind
OP_MODE_FAULT = 2       # Operation Mode 2 = Fault
REFRESH_MS = 100        # how often the screen is redrawn (the drive itself sends its telemetry at about 9 messages per second)
DEFAULT_LIMIT_RPM = 500.0
ENTRY_UNITS = (               # the unit the target speed and the limit are TYPED in (user, 2026-09-26: "enter velocity value in
    ("rpm", 1.0, 1, 10.0, 10),      # rpm or rps or degree per sec"): label, factor from rpm, decimals, single step, slider steps
    ("rps", 1.0 / 60.0, 3, 0.1, 1000),   # per unit - pure mechanical conversions (a turn is a turn), nothing through the
    ("deg/sec", 6.0, 1, 60.0, 10),  # pole count; the drive is always sent its own ESC RPM command
)
LAYOUT_UNKNOWN = ("The drive's telemetry layout could not be read, so the reference's default layout is assumed: "
                  "the values shown may be wrong.")


def speed_hint(kind, unit):
    """Tooltip of a speed card: what the drive sends, and how it becomes the unit shown."""
    text = f"The drive sends this speed as {KIND_LABELS[kind]}."
    if kind == "rps" and unit == "rpm":
        text += " The tool multiplies it by 60 to show rpm."
    return text


class DriveTab(QWidget):
    layout_changed = Signal()       # the signals the drive broadcasts, or the unit the speed is shown in, changed
    stop_requested = Signal()       # STOP was pressed (the window tells a running automatic tuning)
    repeat_changed = Signal()       # this page took the client's one repeat slot, or ended it (Drive Modes listens)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.s = session
        self._running = False
        self._holding = False               # a position is being held (ENABLE)
        self._catalog = defs.telemetry_catalog()
        self._default_tpdos, self._fault_names = defs.load_telemetry()   # {bit: name}
        self.cards = {}                     # telemetry key -> Readout: one card per value the drive broadcasts
        self._card_info = {}                # telemetry key -> Info
        self._card_keys = None
        self._speed_unit = "rpm"            # the unit the speed is shown in
        self.plot_unit = "rpm"              # the unit the speed PLOT is drawn in (the first speed's own, see _apply_speed_unit)
        self._pending_unit = None           # the unit chosen while the drive's telemetry is being changed to match
        self._rps_unit = "rpm"              # rps, rpm or deg/sec, whichever was chosen last for an rps-sending drive - kept
                                             # across a disconnect (which shows the default Hz layout meanwhile)
        self._repeat = None                 # (label, text) of the ONE "· sent every 100 ms" status currently true -
                                             # the drive repeats a single continuous command, see _repeat_status
        self.modes = None                   # this drive's own DriveModesPage, once the window has built it
        self._mapping_regs = {r.key: r for r in defs.load_registers(False) if r.section.startswith("Telemetry Mapping")}
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._mode_row())
        self.velocity_box = self._control_card()
        self.position_box = self._position_card()
        left.addWidget(self.velocity_box)
        left.addWidget(self.position_box)
        left.addWidget(self._drive_card())
        left.addWidget(self._fault_card())
        left.addStretch(1)
        left.setContentsMargins(0, 0, 8, 0)
        left_box = QWidget()
        left_box.setLayout(left)
        # Parent the cards first; showing a parentless widget briefly opens it as a separate window.
        self._sync_control_mode()
        left_scroll = QScrollArea()                          # a short window scrolls this column; nothing is squeezed over anything
        left_scroll.setObjectName("leftScroll")
        left_scroll.setWidget(left_box)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setFixedWidth(356)
        left_scroll.setStyleSheet("QScrollArea#leftScroll { background: transparent; border: 0; }"
                                  "QScrollArea#leftScroll > QWidget > QWidget { background: transparent; }")
        right = QVBoxLayout()
        right.setSpacing(14)
        self.mapping_banner = self._mapping_overflow_banner()
        right.addWidget(self.mapping_banner)
        right.addLayout(self._readouts())
        right.addWidget(self._plots_card(), 1)
        outer.addWidget(left_scroll)
        outer.addLayout(right, 1)

        session.connected.connect(self._on_connected)
        session.probed.connect(self._on_probed)
        session.mapping_changed.connect(self._on_mapping_changed)
        session.disconnected.connect(self._on_disconnected)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)
        self._on_disconnected()

    # ---- construction ----------------------------------------------------------------------
    def _mode_row(self):
        """CONTROL MODE: Velocity or Position - only one can be in use at a time (the drive repeats one command), so
        only the chosen card is shown (user, 2026-09-26: "give drop down from which user can select position mode or
        velocity mode instead of two you are showing at the same time"). STOP / OFF / RESET / ENABLE / DISABLE are
        mode-independent and stay on the DRIVE card below either."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self.control_mode = ComboBox()
        self.control_mode.addItem("Velocity  ·  speed (ESC)", "velocity")
        self.control_mode.addItem("Position  ·  servo", "position")
        self.control_mode.setToolTip("Which control card is shown. Only one continuous command runs at a time, so a "
                                     "speed and a position target are never both in use.")
        h.addWidget(self._muted("CONTROL MODE"))
        h.addWidget(self.control_mode, 1)
        self.control_mode.currentIndexChanged.connect(self._sync_control_mode)
        return row

    def _sync_control_mode(self, _index=0):
        position = self.control_mode.currentData() == "position"
        self.velocity_box.setVisible(not position)
        self.position_box.setVisible(position)

    def _control_card(self):
        box = QGroupBox("SPEED CONTROL  ·  ESC")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        self.target = DoubleSpinBox()
        self.target.setDecimals(1)
        self.target.setKeyboardTracking(False)
        self.target.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.target.setFont(theme.mono_font(20))
        self.target.setFixedHeight(42)
        self.entry_unit = ComboBox()                     # the unit the target and the limit are typed in
        for label, factor, decimals, step, steps in ENTRY_UNITS:
            self.entry_unit.addItem(label, label)
        self.entry_unit.setToolTip("The unit you type the target speed and the speed limit in: rpm, rps (turns per second) or "
                                   "degrees per second. A pure conversion - the drive is always sent the same rpm command.")
        self._entry = ENTRY_UNITS[0]
        self._limit_rpm = DEFAULT_LIMIT_RPM              # the limit exactly, in rpm: the shown value is rounded to its decimals
        self.slider = Slider(Qt.Orientation.Horizontal)
        self.limit = DoubleSpinBox()
        self.limit.setRange(1.0, 20000.0)
        self.limit.setDecimals(0)
        self.limit.setValue(DEFAULT_LIMIT_RPM)
        self.limit.setSuffix(" rpm")
        self.limit.setKeyboardTracking(False)
        self.limit.setToolTip("Safety clamp: the target speed can never exceed this value.")
        self.run_btn = make_button(QPushButton("RUN"), "primary")
        self.run_btn.setToolTip("Send the target speed to the drive and keep sending it.")
        self.run_btn.setFixedHeight(40)
        self.run_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.run_btn.setStyleSheet("padding: 4px 10px; font-size: 14px;")
        grid.addWidget(self._muted("TARGET SPEED"), 0, 0)
        grid.addWidget(self.entry_unit, 0, 1)
        grid.addWidget(self.target, 1, 0, 1, 2)
        grid.addWidget(self.slider, 2, 0, 1, 2)
        grid.addWidget(self._muted("SPEED LIMIT"), 3, 0)
        grid.addWidget(self.limit, 3, 1)
        grid.addWidget(self.run_btn, 4, 0, 1, 2)
        self.target.valueChanged.connect(self._from_spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.limit.valueChanged.connect(self._on_limit_edited)
        self.entry_unit.currentIndexChanged.connect(self._on_entry_unit_changed)
        self.run_btn.clicked.connect(self._run)
        self._sync_ranges(0.0)
        return box

    # ---- the unit the speed is typed in ------------------------------------------------------
    def _to_rpm(self, shown):
        return shown / self._entry[1]

    def _from_rpm(self, rpm):
        return rpm * self._entry[1]

    def limit_rpm(self):
        return self._limit_rpm

    def _on_limit_edited(self, _value=0.0):
        self._limit_rpm = self._to_rpm(self.limit.value())
        self._sync_ranges()
        self._apply_if_running()

    def _on_entry_unit_changed(self, _index=0):
        """Convert the target and the limit to the newly chosen unit: the values keep their meaning, only the numbers
        change (the limit from its exact rpm, not from the rounded number shown). Nothing is sent - a running command
        keeps its rpm."""
        rpm_target, rpm_limit = self._to_rpm(self.target.value()), self._limit_rpm
        self._entry = ENTRY_UNITS[self.entry_unit.currentIndex()]
        label, factor, decimals, step, _steps = self._entry
        self.limit.blockSignals(True)
        self.limit.setDecimals(0 if label == "rpm" else 2 if label == "rps" else 0)
        self.limit.setRange(1.0 * factor, 20000.0 * factor)
        self.limit.setSingleStep(step)
        self.limit.setSuffix(f" {label}")
        self.limit.setValue(self._from_rpm(rpm_limit))
        self.limit.blockSignals(False)
        self.target.setDecimals(decimals)
        self._sync_ranges(self._from_rpm(rpm_target))

    def _drive_card(self):
        """The mode-independent buttons - STOP, OFF, RESET, ENABLE, DISABLE - and the drive's own status line: shown
        whichever control mode is chosen."""
        box = QGroupBox("DRIVE")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        self.stop_btn = make_button(QPushButton("STOP"), "danger")
        self.stop_btn.setToolTip("Stop the motor and reset the target to 0.   Shortcut: Esc")
        self.stop_btn.setFixedHeight(40)
        self.stop_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.stop_btn.setStyleSheet("padding: 4px 10px; font-size: 14px;")
        self.off_btn = QPushButton("OFF")
        self.off_btn.setToolTip("Power off the inverter: the motor is no longer driven.")
        self.reset_btn = QPushButton("RESET")
        self.reset_btn.setToolTip("Clear the latched fault bits, power off the motor, reset the inverter circuitry and the\n"
                                  "work-zone position. The drive ignores every other command until it gets a Reset after a fault.")
        self.enable_btn = QPushButton("ENABLE")
        self.enable_btn.setToolTip("Hold the motor at its current position: sends the drive's Servo command (Servo Control) "
                                   "targeting its current Work Zone Count, and keeps sending it. Needs a Motor Encoder or "
                                   "Servo Encoder configured in Feedback Settings - without one, Work Zone Count does not "
                                   "track a real position to hold.")
        self.disable_btn = QPushButton("DISABLE")
        self.disable_btn.setToolTip("End the position hold and power off the inverter (same as OFF).")
        self.status = self._status_label("Not connected")
        for small in (self.off_btn, self.reset_btn, self.enable_btn, self.disable_btn):
            small.setFixedHeight(32)
            small.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        second_row, third_row = QHBoxLayout(), QHBoxLayout()
        second_row.setSpacing(8)
        third_row.setSpacing(8)
        second_row.addWidget(self.off_btn)
        second_row.addWidget(self.reset_btn)
        third_row.addWidget(self.enable_btn)
        third_row.addWidget(self.disable_btn)
        grid.addWidget(self.stop_btn, 0, 0, 1, 2)
        grid.addLayout(second_row, 1, 0, 1, 2)
        grid.addLayout(third_row, 2, 0, 1, 2)
        grid.addWidget(self.status, 3, 0, 1, 2)
        self.stop_btn.clicked.connect(self.emergency_stop)
        self.off_btn.clicked.connect(lambda: self._simple_command("off"))
        self.reset_btn.clicked.connect(lambda: self._simple_command("reset"))
        self.enable_btn.clicked.connect(self._enable)
        self.disable_btn.clicked.connect(lambda: self._simple_command("off"))
        return box

    def _position_card(self):
        """MOVE TO / MOVE BY: the drive's plain 'servo' command (Servo Control) with a chosen Work Zone Count target
        instead of Enable's current-position one; SET HOME HERE: the drive's Reset Work Zone command. Stopping either
        a move or a speed command uses the existing STOP / OFF / RESET above - there is no separate Stop here."""
        box = QGroupBox("POSITION CONTROL  ·  SERVO")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        self._pos_unit_key = "counts"
        self._counts_per_turn = None                    # the drive's Work Zone: Dimension, once read (deg needs it)
        self.pos_unit = ComboBox()                      # counts or deg - mirrored by the plot card's own Position-in chooser
        for label, _decimals in POSITION_UNITS:
            self.pos_unit.addItem(label, label)
        self.pos_unit.setToolTip("The unit positions are typed and shown in: Work Zone counts, or degrees from the drive's "
                                 "own Work Zone: Dimension (counts per turn), read once when deg is chosen.")
        self.pos_target = DoubleSpinBox()
        self.pos_target.setDecimals(0)
        self.pos_target.setRange(*POSITION_RANGE)
        self.pos_target.setKeyboardTracking(False)
        self.pos_target.setSuffix(" counts")
        self.pos_target.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.move_btn = QPushButton("MOVE TO")
        self.move_btn.setToolTip("Move to this Work Zone Count and hold it there (the drive's Servo command), repeated "
                                 "every 100 ms until STOP / OFF / RESET or a new command. Needs a Motor Encoder or "
                                 "Servo Encoder configured in Feedback Settings.")
        self.pos_delta = DoubleSpinBox()
        self.pos_delta.setDecimals(0)
        self.pos_delta.setRange(*POSITION_RANGE)
        self.pos_delta.setKeyboardTracking(False)
        self.pos_delta.setSuffix(" counts")
        self.pos_delta.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.move_by_btn = QPushButton("MOVE BY")
        self.move_by_btn.setToolTip("Move by this many counts from the drive's current Work Zone Count (from the "
                                    "broadcast, never requested) and hold there. Same command and needs as MOVE TO.")
        self.home_btn = QPushButton("SET HOME HERE")
        self.home_btn.setToolTip("Zero the Work Zone Count at wherever the motor is right now (the drive's Reset Work "
                                 "Zone command). This drive has no automatic homing sequence: jog to the reference "
                                 "point with Speed Control, then press this. Sends a Stop first.")
        for w in (self.move_btn, self.move_by_btn, self.home_btn):
            w.setFixedHeight(32)
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.pos_status = self._status_label()
        grid.addWidget(self._muted("TARGET POSITION"), 0, 0)
        grid.addWidget(self.pos_unit, 0, 1)
        grid.addWidget(self.pos_target, 1, 0, 1, 2)
        grid.addWidget(self.move_btn, 2, 0, 1, 2)
        grid.addWidget(self._muted("MOVE BY (RELATIVE)"), 3, 0, 1, 2)
        grid.addWidget(self.pos_delta, 4, 0, 1, 2)
        grid.addWidget(self.move_by_btn, 5, 0, 1, 2)
        grid.addWidget(self.home_btn, 6, 0, 1, 2)
        grid.addWidget(self.pos_status, 7, 0, 1, 2)
        self.move_btn.clicked.connect(self._move_absolute)
        self.move_by_btn.clicked.connect(self._move_relative)
        self.home_btn.clicked.connect(self._set_home_here)
        self.pos_unit.activated.connect(lambda i: self._choose_pos_unit(self.pos_unit.itemData(i)))
        return box

    # ---- the unit a position is typed and shown in ------------------------------------------
    @property
    def position_label(self):
        return self._pos_unit_key

    @property
    def position_factor(self):
        """What a Work Zone count is multiplied by to show it: 1, or 360 / counts per turn."""
        return 1.0 if self._pos_unit_key == "counts" or not self._counts_per_turn else 360.0 / self._counts_per_turn

    def _pos_counts(self, shown):
        return shown / self.position_factor

    def _pos_shown(self, counts):
        return counts * self.position_factor

    def _pos_text(self, counts):
        """A position for a status line: the counts, plus the degrees when that is the unit shown."""
        if self._pos_unit_key == "counts":
            return f"{counts:,.0f} counts"
        return f"{self._pos_shown(counts):,.2f} deg ({counts:,.0f} counts)"

    def _choose_pos_unit(self, key):
        """From either chooser (the card's or the plot's). Degrees need the drive's Work Zone: Dimension - read once,
        on this choice only (never polled); without a drive, or if the drive says 0, the choice goes back to counts."""
        if key == self._pos_unit_key:
            return
        if key == "counts" or self._counts_per_turn:
            self._apply_pos_unit(key)
            return
        client = self.s.client
        if client is None:
            self._status("Degrees need the drive's Work Zone: Dimension (counts per turn): connect first.", "warn", self.pos_status)
            self._apply_pos_unit("counts")
            return
        reg = defs.telemetry_catalog()[WZ_DIMENSION]
        self._status("Reading the drive's Work Zone: Dimension…", "warn", self.pos_status)
        run_async(lambda emit, cancel: client.read(reg), on_done=self._counts_per_turn_read, on_failed=self._counts_per_turn_failed, owner=self)

    def _counts_per_turn_read(self, value):
        if not value or int(value) <= 0:
            self._status("The drive reports a Work Zone: Dimension of 0 - no turn to divide into degrees.", "warn", self.pos_status)
            self._apply_pos_unit("counts")
            return
        self._counts_per_turn = int(value)
        self._apply_pos_unit("deg")
        self._status(f"1 turn = {self._counts_per_turn:,} counts (the drive's Work Zone: Dimension).", "ok", self.pos_status)

    def _counts_per_turn_failed(self, exc):
        self._status(f"Could not read the drive's Work Zone: Dimension: {exc}", "error", self.pos_status)
        self._apply_pos_unit("counts")

    def _apply_pos_unit(self, key):
        """Convert the target / move-by fields in place, relabel the position cards and both plot cards, sync the two
        choosers. Nothing is sent."""
        counts_target, counts_delta = self._pos_counts(self.pos_target.value()), self._pos_counts(self.pos_delta.value())
        self._pos_unit_key = key
        decimals = dict(POSITION_UNITS)[key]
        factor = self.position_factor
        for box, counts in ((self.pos_target, counts_target), (self.pos_delta, counts_delta)):
            box.blockSignals(True)
            box.setDecimals(decimals)
            box.setRange(POSITION_RANGE[0] * factor, POSITION_RANGE[1] * factor)
            box.setSingleStep(1.0 if key == "deg" else 100.0)
            box.setSuffix(f" {key}")
            box.setValue(self._pos_shown(counts))
            box.blockSignals(False)
        for combo in (self.pos_unit, self.plots.position_unit):
            if combo is not None and combo.currentData() != key:
                combo.blockSignals(True)
                combo.setCurrentIndex(combo.findData(key))
                combo.blockSignals(False)
        for card_key in ("wz_count", "wz_modulo"):
            if card_key in self.cards:
                self.cards[card_key].unit.setText(key)
        self.plots.set_position_unit(key, factor)
        self.layout_changed.emit()                      # Drive Modes' own plot card follows

    @staticmethod
    def _muted(text):
        label = QLabel(text)
        label.setProperty("muted", "true")
        label.setStyleSheet("font-size: 10px; font-weight: 800;")
        return label

    @staticmethod
    def _status_label(text=""):
        """A `ctlStatus` label, sized up front for STATUS_LINES - a user found this live, pressing MOVE BY: a brief
        message ("Checking Feedback Settings…") is much shorter than the one that follows it, and without a fixed
        minimum height the label - and so its card, and so the whole scrollable column - visibly shrank and grew back
        within a couple of frames ("some background is coming for a very short period of time"). Reserving room for
        the longest realistic message up front means no message, however short, ever changes the card's height."""
        label = QLabel(text)
        label.setObjectName("ctlStatus")
        label.setWordWrap(True)
        label.setMinimumHeight(STATUS_LINES * 17)     # ~17 px per line at the 12 px #ctlStatus font (theme.py)
        return label

    def _mapping_overflow_banner(self):
        """A persistent notice for when the drive's OWN telemetry mapping does not fit an 8-byte CAN message (see
        client.py's load_tpdo_mapping docstring) - e.g. this can happen after remapping the DC-bus slot to its FLOAT32
        register (README, 2026-09-22) leaves no room left for Work Zone: Speed. session.py's connect-time
        status message already said this once, but that status bar is a single line the very next page navigation
        overwrites - a user connecting then immediately clicking around loses it with no way to tell why a value they
        expect (e.g. Speed) never got a card. This banner instead stays up for as long as the condition holds."""
        label = QLabel()
        label.setObjectName("ctlStatus")
        label.setWordWrap(True)
        label.setVisible(False)
        return label

    def _update_mapping_banner(self):
        client = self.s.client
        overflow = client.tpdo_overflow if client is not None else []
        if not overflow:
            self.mapping_banner.setVisible(False)
            return
        worst = overflow[0]
        more = f" (+{len(overflow) - 1} more)" if len(overflow) > 1 else ""
        self.mapping_banner.setText(f"WARNING - this drive's own telemetry mapping does not fit an 8-byte message: "
                                    f"{worst[2]}{more}. The missing value(s) show no card or curve below.")
        set_prop(self.mapping_banner, "state", "warn")
        self.mapping_banner.setVisible(True)

    def _fault_card(self):
        box = QGroupBox("FAULT BITS")
        v = QVBoxLayout(box)
        v.setSpacing(8)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(8)
        self.status_led = Led()                     # an at-a-glance health light, matching EAS3's own Motion page -
        self.status_led.setToolTip("Grey: no faults.   Red: the drive reports at least one fault.")
        summary_row.addWidget(self.status_led)
        self.fault_summary = QLabel("—")
        self.fault_summary.setObjectName("faultSummary")
        summary_row.addWidget(self.fault_summary, 1)
        v.addLayout(summary_row)
        self.fault_names = QLabel("")
        self.fault_names.setWordWrap(True)
        self.fault_names.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        v.addWidget(self.fault_names)
        grid = QGridLayout()
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(8)
        self._leds = []
        self._led_bits = sorted(self._fault_names)                # only the fault bits this drive reports have a lamp
        for i, bit in enumerate(self._led_bits):
            led = Led()
            led.setToolTip(f"Bit {bit}: {self._fault_names[bit]}")
            label = QLabel(str(bit))
            label.setProperty("muted", "true")
            cell = QHBoxLayout()
            cell.setSpacing(3)
            cell.addWidget(led)
            cell.addWidget(label)
            cell.addStretch(1)
            grid.addLayout(cell, i // LEDS_PER_ROW, i % LEDS_PER_ROW)
            self._leds.append(led)
        self.led_for_bit = dict(zip(self._led_bits, self._leds))
        v.addLayout(grid)
        return box

    def _readouts(self):
        self._grid = QGridLayout()
        self._grid.setSpacing(14)
        for col in range(CARD_COLUMNS):
            self._grid.setColumnStretch(col, 1)
        return self._grid

    def _apply_broadcast(self, tpdos):
        """Show a card (and a curve) for each value in the drive's telemetry messages, and for nothing else. What a value
        is - and so its unit and how it becomes rpm, if it is a speed - follows from its mapping code, i.e. its parameter."""
        fields = {}
        for tpdo in tpdos:
            for f in tpdo.fields:
                fields.setdefault(f.key, f)
        order = [k for k in CARD_ORDER if k in fields] + [k for k in fields if k not in CARD_ORDER]
        if order == self._card_keys:
            return
        self._card_keys = order
        for card in self.cards.values():
            self._grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.cards, self._card_info = {}, {}
        for key in order:
            f = fields[key]
            reg = self._catalog.get((f.index, f.sub))
            options = reg.options if reg is not None and key != "fault_bits" and f.dtype != "BOOL" else {}
            self._card_info[key] = Info(f, options, defs.speed_kind(f.name, f.units))
        for i, key in enumerate(order):
            self.cards[key] = card = self._new_card(key, self._card_info[key])
            self._grid.addWidget(card, i // CARD_COLUMNS, i % CARD_COLUMNS)
        self._build_curves()
        self._unit_from_mapping()
        self._apply_speed_unit()

    def _unit_from_mapping(self):
        """The unit chosen always matches what the drive sends: a drive that sends Hz is shown in Hz, one that sends rps in
        rps or rpm (whichever was chosen last, rpm at first)."""
        kinds = {info.speed for info in self._card_info.values() if info.speed}
        if "rps" in kinds:
            wanted = self._pending_unit or self._rps_unit    # not _speed_unit: that reads "hz" after a disconnect (the
                                                             # default layout shown meanwhile), which used to drop a
                                                             # chosen rps back to rpm on every reconnect
            self._speed_unit = wanted if wanted in RPS_UNITS else "rpm"
        elif "electrical" in kinds:
            self._speed_unit = "hz"

    def signal_keys(self):
        """The telemetry keys the drive broadcasts, in display order (what the recorder can record)."""
        return list(self._card_keys or [])

    def signal_display(self, key):
        """-> (title, unit label, factor) of a broadcast signal as it is shown: multiply the value the drive sends by the factor."""
        info = self._card_info[key]
        if info.speed:
            unit, factor = speed_shown(info.speed, self._speed_unit)
            return self._title(key), UNIT_LABELS[unit], factor
        return self._title(key), info.field.units, 1.0

    def is_speed(self, key):
        return bool(self._card_info[key].speed)

    def _title(self, key):
        """A speed is simply 'Speed' when it is the only one; several speeds keep the names of their parameters."""
        info = self._card_info[key]
        if info.speed and sum(1 for i in self._card_info.values() if i.speed) == 1:
            return "Speed"
        return CARD_TITLES.get(key, info.field.name)

    def _new_card(self, key, info):
        title = self._title(key)
        if info.speed:
            return Readout(title, UNIT_LABELS[speed_shown(info.speed, self._speed_unit)[0]], fit=True)
        if key == "fault_bits":
            return Readout(title, size=20, fit=True, lines=2)   # names, not a hex number - can run to a few words
        if info.options:
            return Readout(title, size=20, fit=True, lines=2)
        if key in ("wz_count", "wz_modulo"):
            return Readout(title, self._pos_unit_key, fit=True)
        return Readout(title, info.field.units, fit=True)

    def _card_text(self, client, key, value):
        """-> (text, state) for a value of the broadcast."""
        info = self._card_info[key]
        field = info.field
        if info.speed:
            unit, factor = speed_shown(info.speed, self._speed_unit)
            return UNIT_FORMATS[unit].format(value * factor), ""
        if key == "fault_bits":
            bits = int(value)
            if not bits:
                return "No Faults", ""
            return defs.fault_names_text(bits, self._fault_names), "fault"
        if info.options:
            return info.options.get(int(value), f"mode {int(value)}"), "fault" if key == "op_mode" and int(value) == OP_MODE_FAULT else ""
        if key in ("wz_count", "wz_modulo") and self._pos_unit_key == "deg":
            return f"{value * self.position_factor:,.2f}", ""
        if key in CARD_FORMATS:
            return CARD_FORMATS[key].format(value), ""
        if field.dtype == "BOOL":
            return ("yes" if value else "no"), ""
        if P.is_float(field.dtype):
            return (f"{value:,.3f}" if abs(value) < 1000 else f"{value:,.1f}"), ""
        return f"{int(value):,}", ""

    def _plots_card(self):
        """The LIVE TELEMETRY card (live_plots.LivePlots) with the Speed-in chooser - the one control there that writes
        the drive (its telemetry mapping), so it lives on this page only; Drive Modes' own card follows this one."""
        self.plots = LivePlots(with_speed_unit=True)
        self.live_tag, self.window_box, self.freeze, self.speed_unit = (self.plots.live_tag, self.plots.window_box,
                                                                       self.plots.freeze, self.plots.speed_unit)
        self.speed_unit.activated.connect(self._on_speed_unit_chosen)          # a choice made by the user, not a program change
        self.plots.position_unit.activated.connect(lambda i: self._choose_pos_unit(self.plots.position_unit.itemData(i)))
        return self.plots

    @property
    def _curves(self):
        return self.plots.curves

    @property
    def _curve_rows(self):
        return self.plots.curve_rows

    @property
    def _plot_widgets(self):
        return self.plots.plot_widgets

    @property
    def _window_s(self):
        return self.plots.window_s

    @property
    def _legends(self):
        return self.plots._legends

    def _build_curves(self):
        """One curve per plotted value that the drive broadcasts - see LivePlots.build_curves."""
        self.plots.build_curves(self._card_keys, self._card_info, self._title)

    # ---- session events --------------------------------------------------------------------
    def _set_controls(self, connected):
        for w in (self.target, self.slider, self.limit, self.run_btn, self.stop_btn, self.pos_target, self.pos_delta):
            w.setEnabled(connected)
        client = self.s.client
        for btn, key in ((self.off_btn, "off"), (self.reset_btn, "reset"), (self.disable_btn, "off"), (self.enable_btn, "servo"),
                         (self.move_btn, "servo"), (self.move_by_btn, "servo"), (self.home_btn, "reset_work_zone")):
            btn.setEnabled(bool(connected and client and client.commands[key].defined))

    def _on_connected(self):
        self._set_controls(True)
        self._status("Connected — checking the drive…", "warn")

    def _on_probed(self, ok, message):
        client = self.s.client
        if client is None:
            return
        self._apply_broadcast(client.tpdos)                 # the drive's own telemetry mapping, as read at connect
        self._update_mapping_banner()
        if not ok:
            self._status("The drive did not answer. Check the node ID, bit rate and wiring.", "warn")
        elif not client.mapping_from_drive:
            self._status(LAYOUT_UNKNOWN, "warn")
        else:
            self._status("Ready. Pick a control mode, set a target and press RUN or MOVE.", "ok")

    # ---- the unit the speed is shown in ---------------------------------------------------------
    def _apply_speed_unit(self):
        """Put the chosen speed unit on the speed cards and the speed plot."""
        shown = []
        for key, info in self._card_info.items():
            if info.speed:
                unit = speed_shown(info.speed, self._speed_unit)[0]
                shown.append(unit)
                self.cards[key].unit.setText(UNIT_LABELS[unit])
                self.cards[key].setToolTip(speed_hint(info.speed, self._speed_unit))
        self.plot_unit = shown[0] if shown else self._speed_unit        # the plot is in the unit of the first speed
        self.plots.set_speed_unit(self.plot_unit)
        if self.speed_unit.currentData() != self._speed_unit:
            self.speed_unit.setCurrentIndex(self.speed_unit.findData(self._speed_unit))
        self.layout_changed.emit()

    def _confirm(self, text):
        return QMessageBox.question(self, "Change the drive's telemetry", text) == QMessageBox.StandardButton.Yes

    def _speed_slot(self, client):
        """(mapping register, code it holds) of the slot of the drive's telemetry mapping that carries a speed which
        the unit choice can switch (Speed in Hz, or Work Zone: Speed in RPS); None if there is no such slot."""
        wanted = {defs.mapping_code(*defs.SPEED_HZ), defs.mapping_code(*defs.SPEED_RPS)}
        for key, code in sorted(client.mapping_codes.items()):
            if code in wanted and key in self._mapping_regs:
                return self._mapping_regs[key], code
        return None

    def _on_speed_unit_chosen(self, _index=0):
        unit = self.speed_unit.currentData()
        if unit == self._speed_unit:
            return
        client = self.s.client
        slot = self._speed_slot(client) if client is not None else None
        need = defs.mapping_code(*UNIT_SOURCE[unit])
        if slot is None or slot[1] == need:
            # not connected, no speed slot to switch, or the drive already sends the right value: only the display changes
            self._speed_unit = unit
            if unit in RPS_UNITS:
                self._rps_unit = unit
            self._apply_speed_unit()
            if client is not None and slot is None:
                self._status("The drive's telemetry has no Speed / Work Zone: Speed slot to switch: only the display unit changed.", "warn")
            return
        reg, have = slot
        names = {defs.mapping_code(*defs.SPEED_HZ): "Speed (Electrical Frequency), Hz", defs.mapping_code(*defs.SPEED_RPS): "Work Zone: Speed, RPS"}
        if not self._confirm(f"Change the drive's telemetry so that it sends\n    {names[need]}\ninstead of\n    {names[have]}?\n\n"
                             f"This writes the drive's telemetry mapping ({reg.address} = {need}). "
                             f"Anything else that reads this drive's telemetry will see the change too."):
            self.speed_unit.setCurrentIndex(self.speed_unit.findData(self._speed_unit))
            return
        self.speed_unit.setEnabled(False)
        self._pending_unit = unit
        self._status(f"Changing the drive's telemetry to {names[need]} …", "warn")
        run_async(lambda emit, cancel: client.write(reg, need),
                  on_done=lambda _stored: self._speed_source_written(unit, names[need]),
                  on_failed=self._speed_source_failed, owner=self)

    def _speed_source_written(self, unit, name):
        self.speed_unit.setEnabled(True)
        self._pending_unit = None
        self._speed_unit = unit
        if unit in RPS_UNITS:
            self._rps_unit = unit
        self._apply_speed_unit()
        self._status(f"The drive now sends {name}; the speed is shown in {UNIT_LABELS[unit]}.", "ok")

    def _speed_source_failed(self, exc):
        self.speed_unit.setEnabled(True)
        self._pending_unit = None
        self.speed_unit.setCurrentIndex(self.speed_unit.findData(self._speed_unit))
        self._status(f"The drive's telemetry was not changed: {exc}", "error")

    def _on_mapping_changed(self):
        """The drive's telemetry layout was read again (someone changed it, e.g. Speed in Hz -> speed in RPS)."""
        client = self.s.client
        if client is None:
            return
        self._apply_broadcast(client.tpdos)
        self._update_mapping_banner()
        if not client.mapping_from_drive:
            self._status(LAYOUT_UNKNOWN, "warn")

    def _on_disconnected(self):
        self._running = self._holding = False
        self._set_controls(False)
        self._repeat = None                              # every card's own status, not just Position/Current: a
        for label in self._card_status_labels():         # "Kickstarting … sent every 100 ms" used to survive the
            self._status("", "", label)                  # disconnect in green
        self.target.blockSignals(True)
        self.slider.blockSignals(True)
        self.target.setValue(0.0)
        self.slider.setValue(0)
        self.target.blockSignals(False)
        self.slider.blockSignals(False)
        self._apply_broadcast(self._default_tpdos)          # until a drive is connected: the default layout, empty
        self.mapping_banner.setVisible(False)
        for card in self.cards.values():
            card.clear()
        for led in self._leds:
            led.set_on(False)
        self.status_led.set_on(False)
        self.fault_summary.setText("—")
        self.fault_names.setText("")
        set_prop(self.fault_summary, "state", "")
        self.plots.clear()
        self._counts_per_turn = None                     # another connect may be another drive: degrees are re-read
        if self._pos_unit_key != "counts":
            self._apply_pos_unit("counts")
        self._status("Not connected", "")

    # ---- control ---------------------------------------------------------------------------
    def _card_status_labels(self):
        return (self.pos_status,)

    def external_repeat_started(self, text=""):
        """The Drive Modes page started one of its modes: it owns the client's one repeat slot now - this page's own
        "· sent every 100 ms" note goes, RUN/hold bookkeeping reads "a hold-type command is being sent", and Speed
        Control's status says what is running and where (it used to keep "Ready. Set a target speed…" meanwhile)."""
        self._running, self._holding = False, True
        self._clear_repeat_status()
        if text:
            self._status(f"{text}  -  see Drive Modes.", "ok")

    def _repeat_status(self, text, label=None):
        """A "· sent every 100 ms" status. The drive repeats ONE continuous command at a time, so whichever card
        described the previous one is cleared first - only if it still shows that very text (an error or another
        message written there since stays). Without this, Kickstart RUN then STOP left the Kickstart card reading
        "Kickstarting to 15.00 Hz · sent every 100 ms" in green while nothing was being sent any more - the same
        for every card after any takeover of the single repeat slot, and all of them after a disconnect."""
        self._clear_repeat_status()
        if callable(label):                  # gcode_runner's own callback, not one of this page's labels: nothing to track
            self._status(text, "ok", label)
            return
        label = label or self.status
        self._status(text, "ok", label)
        self._repeat = (label, text)
        self.repeat_changed.emit()

    def _clear_repeat_status(self):
        if self._repeat is not None:
            label, text = self._repeat
            if label.text() == text:
                self._status("", "", label)
            self._repeat = None
        self.repeat_changed.emit()

    def _status(self, text, state, label=None):
        """Feedback for a command goes under the card the button that sent it lives in - `label` defaults to Speed
        Control's own (RUN/STOP/OFF/RESET/ENABLE/DISABLE all live there), Position Control and Current/Torque Control
        pass their own (`self.pos_status`, `self.ct_status`) so a command's result never appears under a different
        card (a user found this live: a Position Control error was showing under Speed Control). `label` may also be
        a plain `callable(text, state)` instead of a QLabel - `gcode_runner.py` uses this to receive the result of an
        action normally destined for one of this page's own labels (Enable/Move To/By's Feedback Settings check and
        its eventual result) as a callback instead, without this page needing to know or care."""
        label = label or self.status
        if callable(label):
            label(text, state)
            return
        label.setText(text)
        set_prop(label, "state", state)

    def _sync_ranges(self, value=None):
        """Re-fit spin box and slider to the speed limit; optionally set a new target value."""
        limit = self.limit.value()
        label, _factor, _decimals, step, steps = self._entry
        if value is None:
            value = self.target.value()
        self.target.blockSignals(True)
        self.slider.blockSignals(True)
        self.target.setRange(-limit, limit)
        self.target.setSingleStep(step)
        self.target.setSuffix(f" {label}")
        self.target.setValue(value)
        self.slider.setRange(-round(limit * steps), round(limit * steps))
        self.slider.setValue(round(self.target.value() * steps))
        self.target.blockSignals(False)
        self.slider.blockSignals(False)

    def _from_spin(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(round(value * self._entry[4]))
        self.slider.blockSignals(False)
        self._apply_if_running()

    def _from_slider(self, value):
        self.target.blockSignals(True)
        self.target.setValue(value / self._entry[4])
        self.target.blockSignals(False)
        self._apply_if_running()

    def _apply_if_running(self):
        if self._running:
            self._run()

    def _run(self):
        client = self.s.client
        if client is None:
            return
        shown = self.target.value()
        rpm = self._to_rpm(shown)
        try:
            client.set_speed_rpm(rpm)
        except (DriveError, ValueError) as exc:
            self._running = False
            self._status(f"Command failed: {exc}", "error")
            return
        self._running, self._holding = True, False
        label, _f, decimals, _s, _n = self._entry
        typed = "" if label == "rpm" else f"{shown:.{decimals}f} {label} = "     # what was typed, and what the drive gets
        self._repeat_status(f"Commanding {typed}{rpm:.1f} rpm  ·  sent every 100 ms")

    def _enable(self):
        """ENABLE: hold the motor at its current position - the drive's plain 'servo' command (Servo Control), targeting
        its current Work Zone Count from the broadcast (never requested). Feedback goes under Speed Control, where the
        ENABLE button itself lives."""
        client = self.s.client
        if client is None:
            return
        if "wz_count" not in client.telemetry_snapshot():
            self._status("Enable needs the drive's Work Zone Count in its telemetry - none has arrived yet, or it "
                         "isn't currently broadcast.", "warn")
            return
        self._with_feedback_check(self.enable_btn, self._hold_at_current_position, self.status)

    def _hold_at_current_position(self):
        client = self.s.client
        snap = client.telemetry_snapshot() if client else {}
        if client is None or "wz_count" not in snap:
            self._status("Enable needs the drive's Work Zone Count in its telemetry - it stopped arriving.", "warn")
            return
        self._go_to_position(snap["wz_count"], "Holding position", self.status)

    def _move_absolute(self):
        """MOVE TO: move to (and hold) an arbitrary Work Zone Count typed by the user - the same 'servo' command as
        Enable, just with a chosen target instead of the current position. Feedback goes under Position Control, where
        MOVE TO itself lives - not under Speed Control, even though the two share the same underlying check."""
        client = self.s.client
        if client is None:
            return
        target = round(self._pos_counts(self.pos_target.value()))
        self._with_feedback_check(self.move_btn, lambda: self._go_to_position(target, "Moving to", self.pos_status),
                                  self.pos_status)

    def _move_relative(self):
        """MOVE BY: move by a chosen number of counts from the drive's current Work Zone Count (from the broadcast,
        never requested), then hold there. Feedback goes under Position Control."""
        client = self.s.client
        if client is None:
            return
        snap = client.telemetry_snapshot()
        if "wz_count" not in snap:
            self._status("Move needs the drive's Work Zone Count in its telemetry - none has arrived yet, or it "
                         "isn't currently broadcast.", "warn", self.pos_status)
            return
        target = snap["wz_count"] + round(self._pos_counts(self.pos_delta.value()))
        self._with_feedback_check(self.move_by_btn, lambda: self._go_to_position(target, "Moving to", self.pos_status),
                                  self.pos_status)

    def _go_to_position(self, target, verb, label):
        client = self.s.client
        if client is None:
            self._status("Not connected - nothing was sent.", "warn", label)   # said, not silent: gcode_runner waits
            return                                                             # on this label's own callback
        try:
            client.hold_position(target)
        except (DriveError, ValueError) as exc:
            self._status(f"Move failed: {exc}", "error", label)
            return
        self._running, self._holding = False, True
        self._report_move(f"{verb} {self._pos_text(target)}", label, target, on_speed_card=verb == "Holding position")

    def _report_move(self, text, label, target, on_speed_card=False):
        """The "· sent every 100 ms" note for a position command. `label` is one of this page's own labels when its
        own MOVE TO/BY/ENABLE sent it; a plain callback when a G-code line did (gcode_runner's `_go`) - then the
        callback gets the text AND the page mirrors the move itself (Position Control's target field and status,
        or Speed Control's status for a hold, where ENABLE lives), tagged "(G-code)", so a program's move shows here
        exactly like a MOVE TO would (2026-09-25, user: no update on this page while a program ran)."""
        if not callable(label):
            self._repeat_status(f"{text}  ·  sent every 100 ms", label)
            return
        label(f"{text}  ·  sent every 100 ms", "ok")
        if not on_speed_card:
            self.pos_target.blockSignals(True)
            self.pos_target.setValue(self._pos_shown(target))
            self.pos_target.blockSignals(False)
        self._repeat_status(f"{text} {self.GCODE_NOTE}  ·  sent every 100 ms",
                            self.status if on_speed_card else self.pos_status)

    def go_to_position_linear(self, target, feed_rate, label):
        """Like `_go_to_position`, but the drive's Servo: Linear Interpolation command (a target plus a feed rate) -
        used by the G-code console's G01. Not used by any control on this page itself (there is no Feed Rate field
        here), so it is public, unlike the rest of this page's own action methods."""
        client = self.s.client
        if client is None:
            self._status("Not connected - nothing was sent.", "warn", label)   # same as _go_to_position: never silent
            return
        try:
            client.move_linear(target, feed_rate)
        except (DriveError, ValueError) as exc:
            self._status(f"Move failed: {exc}", "error", label)
            return
        self._running, self._holding = False, True
        self._report_move(f"Moving to {self._pos_text(target)} at {feed_rate:,.0f} counts/sec", label, target)

    GCODE_NOTE = "(G-code)"

    def note_gcode(self, started, *, kind=None, value=None, text=None):
        """A G-code line (Terminal console or a Motion Controller program) just commanded THIS axis directly through
        the client - keep this page honest about it. Always: the RUN/target bookkeeping (`started=True` after
        Move/ESC/Torque/Capture, `False` after Reset/Off/Stop/Release, so touching the target/limit boxes here later
        never silently re-applies a stale RUN over what the G-code layer did). With `kind`, also MIRROR it the way
        this page's own buttons would have shown it (2026-09-25, user: "when i run G-code through motion controller
        then i should get update in servo drive as well" - the live cards followed the telemetry, but the target
        field still read 0 rpm and the status "Ready. Set a target speed and press RUN."): a speed command sets the
        target field/slider and Speed Control's own "Commanding ... · sent every 100 ms" note; a torque command
        picks Torque on the Current/Torque card and sets its target; Stop/Off/Reset zero the target field and show
        "Stop sent. (G-code)" under Speed Control, as this page's own STOP does. Position moves are mirrored by
        `_go_to_position`/`go_to_position_linear` themselves (the runner calls those directly)."""
        self._running, self._holding = False, started
        if kind is None:
            return
        note = self.GCODE_NOTE
        if kind == "esc_rpm":
            limit = self.limit_rpm()
            if abs(value) <= limit:
                self._set_speed_target_quietly(value)
                self._repeat_status(f"Commanding {value:.1f} rpm {note}  ·  sent every 100 ms")
            else:                                  # this page's own Speed Limit only caps its own RUN - say so, never
                self._repeat_status(f"Commanding {value:.1f} rpm {note} - above the {limit:g} rpm speed limit set "
                                    f"here  ·  sent every 100 ms")    # clamp the field to a value the drive was not sent
        elif kind == "torque":                     # the Current / Torque card lives on Drive Modes: mirrored there
            self._clear_repeat_status()
            if self.modes is not None:
                self.modes.mirror_torque(value)
        else:                                      # stop / off / reset / release
            self._clear_repeat_status()
            if kind in ("stop", "off", "reset"):
                self._set_speed_target_quietly(0.0)
            self._status(f"{text} {note}", "warn")

    def _set_speed_target_quietly(self, rpm):
        """Target field + slider (in the unit chosen for typing), without `_apply_if_running` re-sending anything."""
        self.target.blockSignals(True)
        self.slider.blockSignals(True)
        self.target.setValue(self._from_rpm(rpm))
        self.slider.setValue(round(self.target.value() * self._entry[4]))
        self.target.blockSignals(False)
        self.slider.blockSignals(False)

    def _set_home_here(self):
        """Feedback goes under Position Control, where SET HOME HERE itself lives."""
        client = self.s.client
        if client is None:
            return
        try:
            client.reset_work_zone()
        except DriveError as exc:
            self._status(f"Set Home Here failed: {exc}", "error", self.pos_status)
            return
        self._running = self._holding = False
        self._status("Home set here: Work Zone Count is now 0. A Stop was sent too.", "ok", self.pos_status)

    def _with_feedback_check(self, button, on_ok, label):
        """Needed before commanding a Work Zone Count target (Enable, Move To/By) - see feedback_gate.check() for the
        check itself (factored out so GcodeRunner, and eventually per-axis dispatch, can reuse it without going
        through this page's own button/label). Feedback goes under `label` (the card `button` itself lives in),
        never under a different card even though Enable and Move To/By share this same check."""
        client = self.s.client
        if client is None:
            return
        feedback_gate.check(
            client, button,
            on_progress=lambda text, state: self._status(text, state, label),
            on_ok=on_ok,
            on_warn=lambda text: self._status(text, "warn", label),
            on_failed=lambda exc: self._status(f"Could not check Feedback Settings: {exc}", "error", label),
        )

    def emergency_stop(self):
        self.stop_requested.emit()              # a running automatic tuning is ended too (with a Reset: Stop does not end it)
        client = self.s.client
        if client is None:
            return
        self._running = self._holding = False
        self._set_speed_target_quietly(0.0)
        mode = client.telemetry_snapshot().get("op_mode")
        in_configuration = mode is not None and int(mode) == defs.OP_MODE_AUTOCONFIG
        reset = False
        try:
            client.stop()
            if in_configuration and (client.reset_age is None or client.reset_age > 1.0):      # not if a tuner has just sent one
                client.reset()                                          # Stop does not work in the configuration mode; Reset ends it
                reset = True
        except DriveError as exc:
            self._status(f"STOP failed: {exc}", "error")
            return
        self._clear_repeat_status()          # whichever card said "· sent every 100 ms": nothing is being sent any more
        self._status("STOP sent." + (" The drive is in the automatic configuration, where Stop has no effect: a Reset was sent too "
                                     "(it cuts the current supply to the motor and clears the drive's errors)." if reset else ""), "warn")

    def _simple_command(self, key):
        client = self.s.client
        if client is None:
            return
        self._running = self._holding = False
        try:
            client.off() if key == "off" else client.reset()
        except DriveError as exc:
            self._status(str(exc), "error")
            return
        self._clear_repeat_status()          # OFF/RESET end the repeated command too
        self._status(f"{key.upper()} sent.", "ok")

    # ---- live display ----------------------------------------------------------------------
    def _tick(self):
        client = self.s.client
        if client is None:
            return
        age = client.telemetry_age()
        self.plots.set_live(age)
        if age is None:
            return

        snap = client.telemetry_snapshot()       # may be partial while the first messages arrive
        for key, card in self.cards.items():
            if key in snap:
                card.set(*self._card_text(client, key, snap[key]))
        if "fault_bits" in snap:
            bits = int(snap["fault_bits"])
            for bit, led in self.led_for_bit.items():
                led.set_on(bits >> bit & 1)
            self.status_led.set_on(bool(bits))
            self.fault_summary.setText("NO FAULTS" if not bits else f"FAULT  0x{bits:04X}")
            set_prop(self.fault_summary, "state", "fault" if bits else "")
            self.fault_names.setText(defs.fault_names_text(bits, self._fault_names))
            if bits and self._running:                          # the drive ignores everything but Reset while faulted
                self._status("The drive reports a fault: it ignores speed commands until RESET.", "error")
        if self.isVisible():                                    # only the page on screen redraws its plots (Drive
            self.plots.update(client, self._card_info, self._speed_unit)   # Modes has its own card, same rule)
