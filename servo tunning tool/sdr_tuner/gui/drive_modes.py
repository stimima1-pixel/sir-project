"""DRIVE MODES page: the drive's special control modes, one card each, moved off Motion - Single Axis so that page
keeps only what an operator uses every day - run at a speed, move to a position (user, 2026-09-26: "keep velocity and
position control in motion single axis, rest of the command parameters like kickstart or torque control, direct field
control etc ... move somewhere else so we can keep motion single axis simple to operate with limited functions").

Here: Current / Torque Control (FOC / ETC), Position Control · Cyclic (Servo: Modulo, Servo: Turns and Modulo), Direct
Field Control (Rotation / Electrical Position), Kickstart, Power Generation, Brushed: Open Loop Control and Virtual
Brake - every one a continuous command the drive repeats every 100 ms through the client's ONE repeat slot, so
starting a mode here takes over from whatever Motion - Single Axis was sending and vice versa. The two pages keep each
other honest about it: this page tells its motion page when a mode starts (`DriveTab.external_repeat_started`), and
the motion page's `repeat_changed` signal clears this page's own "· sent every 100 ms" note when RUN / STOP / OFF /
RESET / ENABLE / a move / a G-code line takes the slot back. A small readout row (Operation mode, the speed, phase
currents, DC bus, fault bits - the SAME telemetry the motion page shows, formatted by it, nothing extra requested)
shows a mode's effect without switching pages, and STOP ends it. Per drive, like every other Servo Drive page: bound
to a Session or an AxisContext, and to that drive's own DriveTab."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import protocol as P
from ..client import DriveError
from . import feedback_gate
from .drive_tab import CARD_TITLES, CURRENT_TORQUE, DriveTab, POSITION_RANGE, REFRESH_MS
from .live_plots import LivePlots
from .widgets import ComboBox, DoubleSpinBox, Readout, SpinBox, make_button, set_prop

READOUT_KEYS = ("op_mode", "speed_hz", "wz_speed", "ia", "ib", "vbus", "fault_bits")   # shown when the drive broadcasts them
TITLES = {**CARD_TITLES, "wz_speed": "Work zone speed"}   # until a layout is known; then the motion page's own names


class DriveModesPage(QWidget):
    def __init__(self, session, motion, parent=None):
        super().__init__(parent)
        self.s = session
        self.motion = motion                    # this drive's own Motion - Single Axis page
        self._repeat = None                     # (label, text) of the ONE "· sent every 100 ms" note this page shows
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._current_card())
        left.addWidget(self._cyclic_position_card())
        left.addWidget(self._direct_field_card())
        left.addWidget(self._kickstart_card())
        left.addWidget(self._power_generation_card())
        left.addWidget(self._brushed_card())
        left.addWidget(self._virtual_brake_card())
        left.addStretch(1)
        left.setContentsMargins(0, 0, 8, 0)
        left_box = QWidget()
        left_box.setLayout(left)
        left_scroll = QScrollArea()
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
        right.addWidget(self._readout_card())
        self.plots = LivePlots(with_speed_unit=False)           # the same live plots as the motion page, drawn only here
        right.addWidget(self.plots, 1)                          # while this page is the one on screen
        outer.addWidget(left_scroll)
        outer.addLayout(right, 1)

        session.connected.connect(lambda: self._set_controls(True))
        session.probed.connect(lambda _ok, _message: self._set_controls(True))
        session.disconnected.connect(self._on_disconnected)
        motion.repeat_changed.connect(self._clear_repeat_status)     # the motion page took the repeat slot (or ended it)
        motion.layout_changed.connect(self._sync_readouts)
        motion.layout_changed.connect(self._sync_plots)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)
        self._on_disconnected()
        self._sync_plots()

    # ---- construction ----------------------------------------------------------------------
    _muted = staticmethod(DriveTab._muted)
    _status_label = staticmethod(DriveTab._status_label)

    def _readout_card(self):
        box = QGroupBox("THIS DRIVE NOW")
        column = QVBoxLayout(box)
        column.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.readouts = {}
        for key in READOUT_KEYS:
            card = Readout(TITLES.get(key, key), "")
            row.addWidget(card)
            self.readouts[key] = card
        column.addLayout(row)
        self.stop_btn = make_button(QPushButton("STOP"), "danger")
        self.stop_btn.setToolTip("Stop the motor: ends whichever mode is being sent (the same STOP as Motion - Single "
                                 "Axis and the ribbon's round button).   Shortcut: Esc")
        self.stop_btn.setFixedHeight(40)
        self.stop_btn.setStyleSheet("padding: 4px 10px; font-size: 14px;")
        self.stop_btn.clicked.connect(self.motion.emergency_stop)
        note = QLabel("The same telemetry Motion - Single Axis shows, so a mode's effect is visible here. Every mode "
                      "below is one continuous command: starting one ends whatever was being sent before, on either page.")
        note.setWordWrap(True)
        set_prop(note, "muted", "true")
        column.addWidget(self.stop_btn)
        column.addWidget(note)
        return box

    def _cyclic_position_card(self):
        """Servo: Modulo and Servo: Turns and Modulo - two more ways to give the drive a target position, beside
        Motion - Single Axis's MOVE TO/BY (the plain Servo command, a single multi-turn Work Zone Count). Modulo is
        a genuinely different kind of target: a single-turn CYCLIC one, for a rotary actuator meant to keep spinning
        past its own Work Zone Count range (a continuously-rotating turret) rather than being held to one absolute
        multi-turn count - with a DIRECTION choice a plain Servo target has no equivalent of. Turns and Modulo is the
        same KIND of multi-turn target MOVE TO/BY sends, split into a single-turn part and a whole-turns part."""
        box = QGroupBox("POSITION CONTROL  ·  CYCLIC")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        self.cyclic_mode = ComboBox()
        self.cyclic_mode.addItem("Modulo (cyclic)")
        self.cyclic_mode.addItem("Turns + Modulo (split)")
        self.cyclic_modulo = SpinBox()
        self.cyclic_modulo.setRange(*POSITION_RANGE)
        self.cyclic_modulo.setKeyboardTracking(False)
        self.cyclic_modulo.setSuffix(" counts")
        self.cyclic_modulo.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.cyclic_direction = ComboBox()      # index == the drive's own Direction enum value - no translation needed
        for label in ("Normal (same route as Servo)", "Negative Direction Only", "Positive Direction Only", "Shortest Cyclic Route"):
            self.cyclic_direction.addItem(label)
        self.cyclic_direction.setCurrentIndex(3)   # Shortest Cyclic Route - the sensible default for a cyclic target
        self.cyclic_turns = SpinBox()
        self.cyclic_turns.setRange(-32768, 32767)
        self.cyclic_turns.setKeyboardTracking(False)
        self.cyclic_turns.setSuffix(" turns")
        self.cyclic_turns.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.cyclic_move_btn = QPushButton("MOVE")
        self.cyclic_move_btn.setToolTip("Move to this cyclic target and hold it there, repeated every 100 ms until "
                                        "STOP / OFF / RESET or a new command. Needs a Motor Encoder or Servo "
                                        "Encoder configured in Feedback Settings, like MOVE TO/BY on Motion - Single Axis.")
        self.cyclic_move_btn.setFixedHeight(32)
        self.cyclic_move_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cyclic_status = self._status_label()
        grid.addWidget(self._muted("MODE"), 0, 0, 1, 2)
        grid.addWidget(self.cyclic_mode, 1, 0, 1, 2)
        grid.addWidget(self._muted("TARGET: WORK ZONE MODULO"), 2, 0, 1, 2)
        grid.addWidget(self.cyclic_modulo, 3, 0, 1, 2)
        self.cyclic_direction_label = self._muted("DIRECTION")
        grid.addWidget(self.cyclic_direction_label, 4, 0, 1, 2)
        grid.addWidget(self.cyclic_direction, 5, 0, 1, 2)
        self.cyclic_turns_label = self._muted("TARGET: WORK ZONE TURNS")
        grid.addWidget(self.cyclic_turns_label, 4, 0, 1, 2)
        grid.addWidget(self.cyclic_turns, 5, 0, 1, 2)
        grid.addWidget(self.cyclic_move_btn, 6, 0, 1, 2)
        grid.addWidget(self.cyclic_status, 7, 0, 1, 2)
        self.cyclic_mode.currentIndexChanged.connect(self._sync_cyclic_mode)
        self.cyclic_move_btn.clicked.connect(self._move_cyclic)
        self._sync_cyclic_mode()
        return box

    def _sync_cyclic_mode(self, _index=0):
        modulo_mode = self.cyclic_mode.currentIndex() == 0
        self.cyclic_direction_label.setVisible(modulo_mode)
        self.cyclic_direction.setVisible(modulo_mode)
        self.cyclic_turns_label.setVisible(not modulo_mode)
        self.cyclic_turns.setVisible(not modulo_mode)

    def _current_card(self):
        """A steady current (the drive's FOC current-control command) or torque (Electronic Torque Control), whichever
        MODE is chosen - both continuous, sharing the same repeat slot as Speed Control / Enable / Move."""
        box = QGroupBox("CURRENT / TORQUE CONTROL")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        self.ct_mode = ComboBox()
        for _key, mode_label, _unit in CURRENT_TORQUE:
            self.ct_mode.addItem(mode_label)
        self.ct_target = DoubleSpinBox()
        self.ct_target.setDecimals(3)
        self.ct_target.setRange(-1000.0, 1000.0)
        self.ct_target.setKeyboardTracking(False)
        self.ct_target.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.ct_run_btn = make_button(QPushButton("RUN"), "primary")
        self.ct_run_btn.setToolTip("Command this steady current or torque (whichever MODE is chosen), repeated every "
                                   "100 ms until STOP / OFF / RESET or a new command.")
        self.ct_run_btn.setFixedHeight(32)
        self.ct_run_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.ct_status = self._status_label()
        grid.addWidget(self._muted("MODE"), 0, 0, 1, 2)
        grid.addWidget(self.ct_mode, 1, 0, 1, 2)
        grid.addWidget(self._muted("TARGET"), 2, 0, 1, 2)
        grid.addWidget(self.ct_target, 3, 0, 1, 2)
        grid.addWidget(self.ct_run_btn, 4, 0, 1, 2)
        grid.addWidget(self.ct_status, 5, 0, 1, 2)
        self.ct_mode.currentIndexChanged.connect(self._sync_ct_unit)
        self.ct_run_btn.clicked.connect(self._run_current_torque)
        self._sync_ct_unit()
        return box

    def _sync_ct_unit(self, _index=0):
        unit = CURRENT_TORQUE[self.ct_mode.currentIndex()][2]
        self.ct_target.setSuffix(f" {unit}")

    def _direct_field_card(self):
        """Direct Field Control: Rotation / Electrical Position - an OPEN-LOOP alternative to closed-loop commutation:
        a chosen VOLTAGE directly drives the stator coils to create a magnetic field, rotating at a chosen Speed
        (electrical Hz) or held static at a chosen Electrical Position (rad). The reference warns even 0.2-0.3 V can
        burn a low-resistance motor - a standing warning on the card, not a tooltip; on the bench motor 0.5 V draws
        half its current limit (see the manual). No Feedback Settings check: this mode never touches Work Zone Count."""
        box = QGroupBox("DIRECT FIELD CONTROL")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        warning = QLabel("Open-loop: a wrong Voltage can burn the motor. Start around 0.1 V and raise it "
                         "gradually while watching Phase A/B current against Maximum Continuous Current.")
        warning.setWordWrap(True)
        set_prop(warning, "muted", "true")
        self.dfc_mode = ComboBox()
        self.dfc_mode.addItem("Rotation (Hz)")
        self.dfc_mode.addItem("Electrical Position (rad)")
        self.dfc_voltage = DoubleSpinBox()
        self.dfc_voltage.setDecimals(3)
        self.dfc_voltage.setRange(0.0, 50.0)
        self.dfc_voltage.setSuffix(" V")
        self.dfc_voltage.setKeyboardTracking(False)
        self.dfc_voltage.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.dfc_target = DoubleSpinBox()
        self.dfc_target.setDecimals(3)
        self.dfc_target.setRange(-10000.0, 10000.0)
        self.dfc_target.setKeyboardTracking(False)
        self.dfc_target.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.dfc_run_btn = make_button(QPushButton("RUN"), "primary")
        self.dfc_run_btn.setToolTip("Command this open-loop field (whichever MODE is chosen), repeated every 100 "
                                    "ms until STOP / OFF / RESET or a new command. No Feedback Settings check "
                                    "needed - this mode never touches Work Zone Count.")
        self.dfc_run_btn.setFixedHeight(32)
        self.dfc_run_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.dfc_status = self._status_label()
        grid.addWidget(warning, 0, 0, 1, 2)
        grid.addWidget(self._muted("MODE"), 1, 0, 1, 2)
        grid.addWidget(self.dfc_mode, 2, 0, 1, 2)
        grid.addWidget(self._muted("VOLTAGE"), 3, 0, 1, 2)
        grid.addWidget(self.dfc_voltage, 4, 0, 1, 2)
        self.dfc_target_label = self._muted("SPEED (ELECTRICAL FREQUENCY)")
        grid.addWidget(self.dfc_target_label, 5, 0, 1, 2)
        grid.addWidget(self.dfc_target, 6, 0, 1, 2)
        grid.addWidget(self.dfc_run_btn, 7, 0, 1, 2)
        grid.addWidget(self.dfc_status, 8, 0, 1, 2)
        self.dfc_mode.currentIndexChanged.connect(self._sync_dfc_unit)
        self.dfc_run_btn.clicked.connect(self._run_direct_field)
        self._sync_dfc_unit()
        return box

    def _sync_dfc_unit(self, _index=0):
        rotation = self.dfc_mode.currentIndex() == 0
        self.dfc_target_label.setText("SPEED (ELECTRICAL FREQUENCY)" if rotation else "ELECTRICAL POSITION")
        self.dfc_target.setSuffix(" Hz" if rotation else " rad")

    def _kickstart_card(self):
        """Kickstart - an alternate startup acceleration technique for a SENSORLESS brushless motor: gets it moving
        toward a target speed without knowing the rotor's position, using a control law tuned via Control Laws.
        Always in electrical Hz - never folded into Speed Control's rpm target (no silent pole-count conversion)."""
        box = QGroupBox("KICKSTART")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        hint = QLabel("An alternate startup technique for a sensorless brushless motor - accelerates it toward "
                      "a target speed without needing to know the rotor's position. Tuned via Control Laws.")
        hint.setWordWrap(True)
        set_prop(hint, "muted", "true")
        self.kick_target = DoubleSpinBox()
        self.kick_target.setDecimals(2)
        self.kick_target.setRange(-100000.0, 100000.0)
        self.kick_target.setSuffix(" Hz")
        self.kick_target.setKeyboardTracking(False)
        self.kick_target.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.kick_run_btn = make_button(QPushButton("RUN"), "primary")
        self.kick_run_btn.setToolTip("Command this target speed via Kickstart's own acceleration technique, "
                                     "repeated every 100 ms until STOP / OFF / RESET or a new command.")
        self.kick_run_btn.setFixedHeight(32)
        self.kick_run_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.kick_status = self._status_label()
        grid.addWidget(hint, 0, 0, 1, 2)
        grid.addWidget(self._muted("SPEED (ELECTRICAL FREQUENCY)"), 1, 0, 1, 2)
        grid.addWidget(self.kick_target, 2, 0, 1, 2)
        grid.addWidget(self.kick_run_btn, 3, 0, 1, 2)
        grid.addWidget(self.kick_status, 4, 0, 1, 2)
        self.kick_run_btn.clicked.connect(self._run_kickstart)
        return box

    def _power_generation_card(self):
        """Power Generation - the drive as a generator's electronic control unit, producing a target output DC voltage
        (enough to charge a battery through a BMS, if one is used): nothing drives the motor, it converts power FROM
        an externally-spinning shaft."""
        box = QGroupBox("POWER GENERATION")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        hint = QLabel("Turns the drive into a generator's control unit, producing this output DC voltage - "
                      "enough to charge a battery through a BMS, if one is used.")
        hint.setWordWrap(True)
        set_prop(hint, "muted", "true")
        self.gen_target = DoubleSpinBox()
        self.gen_target.setDecimals(2)
        self.gen_target.setRange(0.0, 1000.0)
        self.gen_target.setSuffix(" V")
        self.gen_target.setKeyboardTracking(False)
        self.gen_target.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.gen_run_btn = make_button(QPushButton("RUN"), "primary")
        self.gen_run_btn.setToolTip("Command this output voltage via Power Generation, repeated every 100 ms "
                                    "until STOP / OFF / RESET or a new command.")
        self.gen_run_btn.setFixedHeight(32)
        self.gen_run_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.gen_status = self._status_label()
        grid.addWidget(hint, 0, 0, 1, 2)
        grid.addWidget(self._muted("VOLTAGE"), 1, 0, 1, 2)
        grid.addWidget(self.gen_target, 2, 0, 1, 2)
        grid.addWidget(self.gen_run_btn, 3, 0, 1, 2)
        grid.addWidget(self.gen_status, 4, 0, 1, 2)
        self.gen_run_btn.clicked.connect(self._run_power_generation)
        return box

    def _brushed_card(self):
        """Brushed: Open Loop Control - direct open-loop voltage on 1-2 brushed motors or solenoids, independently
        (#1 on terminals A/B, #2 on C/B, sharing B) - for a drive whose Motor Type (Datasheet) is Brushed."""
        box = QGroupBox("BRUSHED: OPEN LOOP CONTROL")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        hint = QLabel("Direct open-loop voltage on 1-2 brushed motors/solenoids, independently. #1 on terminals "
                      "A/B, #2 (if present) on C/B - they share terminal B.")
        hint.setWordWrap(True)
        set_prop(hint, "muted", "true")
        self.brushed_v1 = DoubleSpinBox()
        self.brushed_v1.setDecimals(3)
        self.brushed_v1.setRange(-1000.0, 1000.0)
        self.brushed_v1.setSuffix(" V")
        self.brushed_v1.setKeyboardTracking(False)
        self.brushed_v1.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.brushed_v2 = DoubleSpinBox()
        self.brushed_v2.setDecimals(3)
        self.brushed_v2.setRange(-1000.0, 1000.0)
        self.brushed_v2.setSuffix(" V")
        self.brushed_v2.setKeyboardTracking(False)
        self.brushed_v2.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.brushed_run_btn = make_button(QPushButton("RUN"), "primary")
        self.brushed_run_btn.setToolTip("Command these channel voltages, repeated every 100 ms until STOP / OFF "
                                        "/ RESET or a new command.")
        self.brushed_run_btn.setFixedHeight(32)
        self.brushed_run_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.brushed_status = self._status_label()
        grid.addWidget(hint, 0, 0, 1, 2)
        grid.addWidget(self._muted("VOLTAGE: CHANNEL #1"), 1, 0, 1, 2)
        grid.addWidget(self.brushed_v1, 2, 0, 1, 2)
        grid.addWidget(self._muted("VOLTAGE: CHANNEL #2"), 3, 0, 1, 2)
        grid.addWidget(self.brushed_v2, 4, 0, 1, 2)
        grid.addWidget(self.brushed_run_btn, 5, 0, 1, 2)
        grid.addWidget(self.brushed_status, 6, 0, 1, 2)
        self.brushed_run_btn.clicked.connect(self._run_brushed)
        return box

    def _virtual_brake_card(self):
        """VIRTUAL BRAKE: resist unwanted motion using the motor itself, without a physical braking device - no
        Feedback Settings check, unlike ENABLE/MOVE TO/BY: the drive still does something meaningful (a statically
        held field) with no Motor/Servo Encoder, just less efficiently."""
        box = QGroupBox("VIRTUAL BRAKE")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        hint = QLabel("The motor itself resists unwanted motion from external forces, like a brake with no braking "
                      "device - no current while nothing tries to move the shaft. Best with Hall sensors or a Motor "
                      "Encoder; without either the drive holds a static field instead, which can heat a sensorless "
                      "motor if left on.")
        hint.setWordWrap(True)
        set_prop(hint, "muted", "true")
        self.vbrake_btn = QPushButton("ENGAGE VIRTUAL BRAKE")
        self.vbrake_btn.setToolTip("Repeated every 100 ms until STOP / OFF / RESET or a new command. No Feedback "
                                   "Settings check needed.")
        self.vbrake_btn.setFixedHeight(32)
        self.vbrake_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.vbrake_status = self._status_label()
        grid.addWidget(hint, 0, 0, 1, 2)
        grid.addWidget(self.vbrake_btn, 1, 0, 1, 2)
        grid.addWidget(self.vbrake_status, 2, 0, 1, 2)
        self.vbrake_btn.clicked.connect(self._engage_virtual_brake)
        return box

    # ---- state ------------------------------------------------------------------------------
    def _set_controls(self, connected):
        for w in (self.cyclic_mode, self.cyclic_modulo, self.cyclic_direction, self.cyclic_turns, self.ct_mode, self.ct_target,
                  self.dfc_mode, self.dfc_voltage, self.dfc_target, self.kick_target, self.gen_target,
                  self.brushed_v1, self.brushed_v2, self.stop_btn):
            w.setEnabled(connected)
        client = self.s.client
        for btn, key in ((self.cyclic_move_btn, "servo_modulo"),
                         (self.ct_run_btn, "current_control_field_oriented_control_foc"),
                         (self.dfc_run_btn, "direct_field_control_rotation"),
                         (self.vbrake_btn, "virtual_brake"),
                         (self.kick_run_btn, "kickstart"),
                         (self.gen_run_btn, "power_generation"),
                         (self.brushed_run_btn, "brushed_open_loop_control_1_2_motors")):
            btn.setEnabled(bool(connected and client and client.commands[key].defined))
        self._sync_readouts()

    def _on_disconnected(self):
        self._set_controls(False)
        self._repeat = None
        for label in self._card_status_labels():
            self._status("", "", label)
        for card in self.readouts.values():
            card.clear()
        self.plots.clear()

    def _card_status_labels(self):
        return (self.cyclic_status, self.ct_status, self.dfc_status, self.kick_status, self.gen_status, self.brushed_status,
                self.vbrake_status)

    def _status(self, text, state, label):
        label.setText(text)
        set_prop(label, "state", state)

    def _repeat_status(self, text, label):
        """A mode started: it now owns the client's one repeat slot. The motion page is told first (it clears its own
        note and its RUN/hold bookkeeping - and, through `repeat_changed`, this page's previous note)."""
        self.motion.external_repeat_started(text.split("  ·")[0])
        self._status(text, "ok", label)
        self._repeat = (label, text)

    def _clear_repeat_status(self):
        if self._repeat is not None:
            label, text = self._repeat
            if label.text() == text:
                self._status("", "", label)
            self._repeat = None

    def mirror_torque(self, value):
        """A G-code torque line commanded this drive (gcode_runner -> DriveTab.note_gcode -> here): show it the way
        this page's own RUN would have."""
        self.ct_mode.setCurrentIndex(1)                             # CURRENT_TORQUE[1] = Torque (ETC)
        self.ct_target.blockSignals(True)
        self.ct_target.setValue(value)
        self.ct_target.blockSignals(False)
        self._status(f"Commanding {value:.3f} N·m (Torque (ETC)) {DriveTab.GCODE_NOTE}  ·  sent every 100 ms", "ok",
                     self.ct_status)
        self._repeat = (self.ct_status, self.ct_status.text())
        self.motion._status(f"Commanding {value:.3f} N·m (Torque (ETC)) {DriveTab.GCODE_NOTE}  -  see Drive Modes.", "ok")

    # ---- the modes --------------------------------------------------------------------------
    def _with_feedback_check(self, button, on_ok, label):
        """Before a Work Zone target (the cyclic moves): the same Feedback Settings check as ENABLE / MOVE TO / BY."""
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

    def _move_cyclic(self):
        client = self.s.client
        if client is None:
            return
        modulo_mode = self.cyclic_mode.currentIndex() == 0
        target = self.cyclic_modulo.value()
        if modulo_mode:
            direction = self.cyclic_direction.currentIndex()
            def go():
                try:
                    client.hold_modulo_position(target, direction)
                except (DriveError, ValueError) as exc:
                    self._status(f"Move failed: {exc}", "error", self.cyclic_status)
                    return
                self._repeat_status(f"Moving to modulo {target:,.0f} counts ({self.cyclic_direction.currentText()})  ·  "
                                    f"sent every 100 ms", self.cyclic_status)
        else:
            turns = self.cyclic_turns.value()
            def go():
                try:
                    client.hold_turns_and_modulo_position(target, turns)
                except (DriveError, ValueError) as exc:
                    self._status(f"Move failed: {exc}", "error", self.cyclic_status)
                    return
                self._repeat_status(f"Moving to {turns:,.0f} turns + modulo {target:,.0f} counts  ·  sent every 100 ms",
                                    self.cyclic_status)
        self._with_feedback_check(self.cyclic_move_btn, go, self.cyclic_status)

    def _run_current_torque(self):
        client = self.s.client
        if client is None:
            return
        key, mode_label, unit = CURRENT_TORQUE[self.ct_mode.currentIndex()]
        value = self.ct_target.value()
        try:
            client.set_current(value) if key == "current_control_field_oriented_control_foc" else client.set_torque(value)
        except (DriveError, ValueError) as exc:
            self._status(f"Command failed: {exc}", "error", self.ct_status)
            return
        self._repeat_status(f"Commanding {value:.3f} {unit} ({mode_label})  ·  sent every 100 ms", self.ct_status)

    def _run_direct_field(self):
        client = self.s.client
        if client is None:
            return
        rotation = self.dfc_mode.currentIndex() == 0
        voltage = self.dfc_voltage.value()
        target = self.dfc_target.value()
        try:
            client.set_direct_field_rotation(voltage, target) if rotation else client.set_direct_field_position(voltage, target)
        except (DriveError, ValueError) as exc:
            self._status(f"Command failed: {exc}", "error", self.dfc_status)
            return
        unit, verb = ("Hz", "Rotating field at") if rotation else ("rad", "Holding field at")
        sent = P.wire_value("FLOAT16", voltage)               # the Voltage travels in 1/256 V steps: say what the drive got
        self._repeat_status(f"{verb} {target:.3f} {unit}, {sent:.4f} V  ·  sent every 100 ms", self.dfc_status)

    def _run_kickstart(self):
        client = self.s.client
        if client is None:
            return
        target = self.kick_target.value()
        try:
            client.kickstart(target)
        except (DriveError, ValueError) as exc:
            self._status(f"Command failed: {exc}", "error", self.kick_status)
            return
        self._repeat_status(f"Kickstarting to {target:.2f} Hz  ·  sent every 100 ms", self.kick_status)

    def _run_power_generation(self):
        client = self.s.client
        if client is None:
            return
        target = self.gen_target.value()
        try:
            client.generate_power(target)
        except (DriveError, ValueError) as exc:
            self._status(f"Command failed: {exc}", "error", self.gen_status)
            return
        self._repeat_status(f"Generating {target:.2f} V  ·  sent every 100 ms", self.gen_status)

    def _run_brushed(self):
        client = self.s.client
        if client is None:
            return
        v1, v2 = self.brushed_v1.value(), self.brushed_v2.value()
        try:
            client.brushed_open_loop(v1, v2)
        except (DriveError, ValueError) as exc:
            self._status(f"Command failed: {exc}", "error", self.brushed_status)
            return
        s1, s2 = P.wire_value("FLOAT16", v1), P.wire_value("FLOAT16", v2)     # 1/256 V steps on the wire
        self._repeat_status(f"Commanding {s1:.4f} V / {s2:.4f} V  ·  sent every 100 ms", self.brushed_status)

    def _engage_virtual_brake(self):
        client = self.s.client
        if client is None:
            return
        try:
            client.engage_virtual_brake()
        except DriveError as exc:
            self._status(f"Virtual Brake failed: {exc}", "error", self.vbrake_status)
            return
        self._repeat_status("Virtual Brake engaged, resisting motion  ·  sent every 100 ms", self.vbrake_status)

    # ---- the readouts -----------------------------------------------------------------------
    def _sync_readouts(self):
        """Show the readouts the drive broadcasts (the motion page knows the layout), hide the rest."""
        keys = set(self.motion.cards)
        for key, card in self.readouts.items():
            card.setVisible(key in keys)
            if key in keys:
                card.title.setText(self.motion._title(key).upper())
                card.unit.setText(self.motion.cards[key].unit.text())      # the unit the motion page shows it in

    def _sync_plots(self):
        """The same curves, in the same unit, as the motion page's own card - built from its knowledge of the layout."""
        if self.motion._card_keys is None:
            return
        self.plots.build_curves(self.motion._card_keys, self.motion._card_info, self.motion._title)
        self.plots.set_speed_unit(self.motion.plot_unit)
        self.plots.set_position_unit(self.motion.position_label, self.motion.position_factor)

    def _tick(self):
        client = self.s.client
        if client is None or not self.isVisible():
            return
        age = client.telemetry_age()
        self.plots.set_live(age)
        snap = client.telemetry_snapshot()
        for key, card in self.readouts.items():
            if key in snap and key in self.motion.cards:
                card.set(*self.motion._card_text(client, key, snap[key]))
        if age is not None:
            self.plots.update(client, self.motion._card_info, self.motion._speed_unit)
