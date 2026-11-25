"""Servo Drive > Advanced Tuning > Verification > Frequency Response: a Bode plot (gain and phase against frequency)
of one control loop of the drive - current, speed or servo - measured with the drive's OWN sine-wave test command,
one frequency at a time (a stepped sine; the arithmetic is in sdr_tuner/bode.py).

The drive has no frequency-response function; what it has (Device Reference pp. 89-91/95, and the README's notes of
2026-09-25) is a sine Wave Form for its four Testing commands, the loop's reference and response as telemetry, and a
telemetry rate of 1000 messages/s shared across the mapped messages. So a sweep: (1) remembers the drive's telemetry
frequency and its 12 mapping slots, then maps ONLY this loop's reference and response into telemetry message 0 (the
other two messages emptied - one message then gets the whole 1000 Hz, measured) and sets the frequency to 1000 Hz;
(2) for each frequency, runs the loop's Testing command with Wave Form 2 (sine) at Period = 1/f, waits a few cycles,
records a few more from the client's own telemetry history and fits both signals; (3) sends Stop and puts the mapping
and the frequency back EXACTLY as they were - on completion, on STOP / Esc, and when the connection is being closed.
While a sweep runs every other page sees only those two signals in the broadcast; they come back when it ends.

NOT tried on a real drive yet - see README's *Still to confirm*."""
import cmath
import csv
import math
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import bode, defs, protocol as P
from ..client import DriveError
from . import theme
from .tuner import MODE_AUTOCONFIG, NO_BUS_VOLTS, SYMBOL, TESTS, Check, card, fault_names, _registers
from .widgets import ComboBox, fit_table_height, make_button, set_prop, stack_pixmaps
from .workers import run_async

SINE = 2                                   # the Testing commands' Wave Form value for a sine wave
TELEMETRY_FREQUENCY = (0x3000, 0x05)
MAPPING_SLOTS = [(0x1A00 + message, slot) for message in range(3) for slot in range(1, 5)]
MEASUREMENT_HZ = 1000.0                    # fallback when the drive's timer frequency cannot be read: its usual tick
TIMER_ISR = (0x5000, 0xA9)                 # Timer ISR Frequency (telemetry): one TPDO per tick = the telemetry ceiling
FAST_HZ, NORMAL_HZ = 2000.0, 1000.0         # the receiving frequency sets that tick AT BOOT - measured 2026-09-25
LOOPS = {   # Testing command key -> (the loop's reference register, its response register): both FLOAT32/INT32, 8 bytes together
    "testing_electronic_speed_control_esc": ((0x4000, 0x21), (0x4000, 0x10)),    # Reference: Speed -> Speed (electrical Hz)
    "testing_field_oriented_control_foc": ((0x4000, 0x20), (0x4001, 0x03)),      # Reference: Current -> FOC: Iq Current
    "testing_iq_current_control": ((0x4000, 0x20), (0x4001, 0x03)),
    "testing_servo_control": ((0x4000, 0x23), (0x4000, 0x19)),                   # Reference: Work Zone Count -> Work Zone Count
}
MIN_SETTLE_S, MIN_MEASURE_S = 0.3, 0.5     # floors, so a fast frequency still gets a real settle and enough samples
METHODS = (("sine", "Stepped sine - one frequency at a time, fitted"),
           ("chirp", "Chirp - the drive's own frequency sweep, analysed by spectrum"))
QUALITY_FLOOR = bode.QUALITY_FLOOR         # a point whose fit explains less of the signal than this is drawn hollow/amber
NYQUIST_VIEW = 2.5                         # the Nyquist plot shows |L| up to this at least; the pass band's huge |L| stays off-screen
NYQUIST_VIEW_MAX = 25.0                    # ... but a loop that never comes inside grows the view until its nearest point shows
NYQUIST_MIN_PX = 260                       # the Nyquist square beside the Bode pair: as tall as the pair, but never
NYQUIST_WIDTH_SHARE = 0.4                  # ... wider than this share of the page, so the Bode plots keep their width
PLOT_MIN_PX, PLOT_MAX_PX = 190, 320        # a Bode plot's height: half the page each, so gain and phase are on screen together
CURSOR_COLOURS = {"A": theme.GOLD_HI, "B": theme.SAPPHIRE}     # the Recorder's own two cursors
CONTOUR_VIEW_T = 1.5                       # the full-contour plot's view for the closed loop T (|T| is about 1 in the pass band)
CONTOUR_VIEWS = (("closed", "Closed loop T - the response as measured"), ("open", "Open loop L = T / (1 - T)"))
CURSOR_ROWS = ("Frequency (Hz)", "Gain (dB)", "Phase (°)", "Quality", "Open loop |L|", "Open loop ∠L (°)",
               "Slope A→B (dB/decade)")


class FrequencyResponsePage(QScrollArea):
    message = Signal(str, str)      # (text, level) for the window's status bar

    def __init__(self, session):
        super().__init__()
        self.title = "Frequency Response"
        self.s = session
        self.points = []                # bode.BodePoint, in sweep order
        self._phase = "idle"            # idle | switching | settle | measure | restoring
        self._freqs, self._index, self._loop = [], 0, None
        self._original = None           # {"frequency": Hz, "mapping": {slot key: code}} - the drive's own, to put back
        self._chirp = None              # (period, cycles) while a chirp measurement runs, None for a stepped sine
        self._reported = False          # a sweep's outcome is on the status line (refresh must not replace it with "Ready")
        self._measure_s = 0.0
        self._sample_hz = None          # the telemetry rate the last sweep really used (the drive's timer frequency)
        self.timer_hz = None            # Timer ISR Frequency as last read from the drive (None: not read / not connected)
        self._config = {r.key: r for r in defs.load_registers(False)}
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(22, 14, 22, 18)
        col.setSpacing(12)

        note = QLabel("Measures the frequency response - a Bode plot of gain (dB) and phase (°) - of one control loop, using the "
                      "drive's own sine-wave test one frequency at a time. For the length of the sweep the drive's telemetry is "
                      "switched to carry only this loop's reference and response (message 0, at the drive's own timer rate); the original "
                      "layout and rate are put back when the sweep ends or is stopped. The motor moves continuously while it "
                      "runs. Read the -3 dB bandwidth and the phase lag from the result, or spot a resonance for the notch filters.")
        note.setObjectName("formNote")
        note.setWordWrap(True)
        col.addWidget(note)

        box, lay = card("Sweep")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        self.kind = ComboBox()
        for key, label, _amp_name, _units, _watch, _mode in TESTS:
            self.kind.addItem(label, key)
        self.amplitude = QLineEdit("")
        self.amplitude_label, self.amplitude_units = QLabel(""), QLabel("")
        self.f_start, self.f_stop, self.n_points = QLineEdit("0.5"), QLineEdit("50"), QLineEdit("12")
        self.settle_cycles, self.measure_cycles = QLineEdit("3"), QLineEdit("5")
        for field in (self.amplitude, self.f_start, self.f_stop, self.n_points, self.settle_cycles, self.measure_cycles):
            field.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.method = ComboBox()
        for key, label in METHODS:
            self.method.addItem(label, key)
        self.chirp_period, self.chirp_cycles = QLineEdit("1"), QLineEdit("2")
        for field in (self.chirp_period, self.chirp_cycles):
            field.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.chirp_note = QLabel("")
        self.chirp_note.setObjectName("formNote")
        self.chirp_note.setWordWrap(True)
        grid.addWidget(QLabel("Loop"), 0, 0)
        grid.addWidget(self.kind, 0, 1, 1, 3)
        grid.addWidget(QLabel("Method"), 1, 0)
        grid.addWidget(self.method, 1, 1, 1, 3)
        grid.addWidget(self.amplitude_label, 2, 0)
        grid.addWidget(self.amplitude, 2, 1)
        grid.addWidget(self.amplitude_units, 2, 2)
        self._sine_widgets, self._chirp_widgets = [], []
        for row, name, field, unit in ((3, "From", self.f_start, "Hz"), (4, "To", self.f_stop, "Hz"),
                                       (5, "Points", self.n_points, "(log-spaced)"),
                                       (6, "Settle", self.settle_cycles, "cycles before measuring"),
                                       (7, "Measure", self.measure_cycles, "cycles per point")):
            name_label, unit_label = QLabel(name), QLabel(unit)
            grid.addWidget(name_label, row, 0)
            grid.addWidget(field, row, 1)
            grid.addWidget(unit_label, row, 2)
            self._sine_widgets += [name_label, field, unit_label]
        for row, name, field, unit in ((8, "Period", self.chirp_period, "s  (the sweep starts at 1/Period)"),
                                       (9, "Cycles", self.chirp_cycles, "recorded, of 10 × Period each")):
            name_label, unit_label = QLabel(name), QLabel(unit)
            grid.addWidget(name_label, row, 0)
            grid.addWidget(field, row, 1)
            grid.addWidget(unit_label, row, 2)
            self._chirp_widgets += [name_label, field, unit_label]
        grid.addWidget(self.chirp_note, 10, 0, 1, 4)
        self._chirp_widgets.append(self.chirp_note)
        grid.setColumnStretch(4, 1)
        lay.addLayout(grid)
        col.addWidget(box)

        box, lay = card("Telemetry rate (the drive's timer)")
        self.rate_note = QLabel("")
        self.rate_note.setObjectName("formNote")
        self.rate_note.setWordWrap(True)
        lay.addWidget(self.rate_note)
        rate_row = QHBoxLayout()
        rate_row.setSpacing(10)
        self.prepare_fast_btn = QPushButton(f"PREPARE {FAST_HZ:g} Hz")
        self.prepare_fast_btn.setToolTip(f"Write receiving frequency {FAST_HZ:g} Hz and save the drive's configuration to flash; "
                                         "after a power cycle the drive's timer runs at that rate and a sweep samples twice as fast.")
        self.restore_rate_btn = QPushButton(f"BACK TO {NORMAL_HZ:g} Hz")
        self.restore_rate_btn.setToolTip(f"Write receiving frequency {NORMAL_HZ:g} Hz and save to flash - the drive's normal rate "
                                         "after the next power cycle.")
        rate_row.addWidget(self.prepare_fast_btn)
        rate_row.addWidget(self.restore_rate_btn)
        rate_row.addStretch(1)
        lay.addLayout(rate_row)
        self.rate_instruction = QLabel("")
        self.rate_instruction.setObjectName("bigStatus")
        self.rate_instruction.setWordWrap(True)
        self.rate_instruction.setVisible(False)
        lay.addWidget(self.rate_instruction)
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
        self.start_btn = make_button(QPushButton("START SWEEP"), "primary")
        self.stop_btn = make_button(QPushButton("STOP"), "danger")
        self.stop_btn.setToolTip("End the sweep: stop the drive and put its telemetry back.   Shortcut: Esc")
        self.save_csv_btn = QPushButton("Save Data…")
        self.save_image_btn = QPushButton("Save Image…")
        self.cursors_btn = QPushButton("CURSORS")
        self.cursors_btn.setCheckable(True)
        self.cursors_btn.setToolTip("Two frequency cursors, A and B, shared by the gain and phase plots: drag one on either plot and "
                                    "it moves on both, snapping to the measured points; the table under the plots reads them and "
                                    "the slope between them, and the Nyquist plot marks the same two points.")
        for b in (self.start_btn, self.stop_btn, self.save_csv_btn, self.save_image_btn, self.cursors_btn):
            row.addWidget(b)
        row.addStretch(1)
        col.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("bigStatus")
        self.status.setWordWrap(True)
        col.addWidget(self.status)

        self.plots = QWidget()
        plots = QHBoxLayout(self.plots)                  # the Bode pair stacked on the left, the Nyquist square beside
        plots.setContentsMargins(0, 0, 0, 0)             # it on the right: all three plots on screen together (_fit_plots)
        plots.setSpacing(12)
        self.gain_plot, self.gain_curve, self.gain_warn = self._make_plot("Gain", "dB", theme.GOLD)
        self.phase_plot, self.phase_curve, self.phase_warn = self._make_plot("Phase", "°", theme.SAPPHIRE)
        self.nyquist_plot, self.nyquist_curve, self.nyquist_warn, self.nyquist_cross = self._make_nyquist()
        # small enough to fit one screen, so they can be looked into: Ctrl/Cmd + wheel or a pinch zooms, a drag pans,
        # a double-click resets; the plain wheel still scrolls the page (theme._PlotMouse)
        theme.enable_zoom(self.gain_plot, reset=lambda: self.gain_plot.getPlotItem().enableAutoRange())
        theme.enable_zoom(self.phase_plot, reset=lambda: self.phase_plot.getPlotItem().enableAutoRange())
        theme.enable_zoom(self.nyquist_plot, reset=self._fit_nyquist_view)
        self.bode_col = QVBoxLayout()
        self.bode_col.setSpacing(8)
        self.bode_col.addWidget(self.gain_plot)
        self.bode_col.addWidget(self.phase_plot)
        plots.addLayout(self.bode_col, 1)
        plots.addWidget(self.nyquist_plot, 0, Qt.AlignmentFlag.AlignVCenter)    # a square panel: the aspect lock would
        col.addWidget(self.plots)                        # otherwise stretch the view sideways, the unit circle small in a corner
        self.zoom_hint = QLabel("Zoom a plot with Cmd/Ctrl + scroll or a trackpad pinch, drag it to pan, double-click it to "
                                "reset its view; the plain scroll wheel scrolls the page.")
        self.zoom_hint.setObjectName("formNote")
        self.zoom_hint.setWordWrap(True)
        col.addWidget(self.zoom_hint)
        # CURSORS (user, 2026-09-25: "cursor for more analysis in gain and phase both graph and sync"): A and B, one
        # InfiniteLine per Bode plot each, in the Recorder's colours; positions are log10(f) (the plots are in log mode)
        self._cursors = {}              # "A"/"B" -> (line on the gain plot, line on the phase plot)
        self._cursor_hz = {}            # "A"/"B" -> the frequency a cursor was last put at (kept across sweeps)
        for name, colour in CURSOR_COLOURS.items():
            lines = []
            for plot in (self.gain_plot, self.phase_plot):
                line = pg.InfiniteLine(pos=0, angle=90, movable=True, pen=pg.mkPen(colour, width=1.5, style=Qt.PenStyle.DashLine),
                                       hoverPen=pg.mkPen(colour, width=3), label=name, labelOpts={"position": 0.92, "color": colour})
                line.setVisible(False)
                plot.addItem(line)
                line.sigDragged.connect(lambda l, n=name: self._cursor_dragged(n, l))
                line.sigPositionChangeFinished.connect(lambda l, n=name: self._cursor_released(n, l))
                lines.append(line)
            self._cursors[name] = tuple(lines)
        self.nyquist_marks, self.nyquist_labels = {}, {}
        for name, colour in CURSOR_COLOURS.items():
            self.nyquist_marks[name] = self.nyquist_plot.plot(pen=None, symbol="s" if name == "A" else "t", symbolSize=13,
                                                              symbolBrush=None, symbolPen=pg.mkPen(colour, width=2))
            label = pg.TextItem(name, color=colour, anchor=(-0.4, 1.3))
            label.setVisible(False)
            self.nyquist_plot.addItem(label)
            self.nyquist_labels[name] = label
        self.cursor_table = QTableWidget(len(CURSOR_ROWS), 4)
        self.cursor_table.setHorizontalHeaderLabels(["", "A", "B", "B − A"])
        self.cursor_table.verticalHeader().setVisible(False)
        self.cursor_table.verticalHeader().setDefaultSectionSize(24)
        self.cursor_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cursor_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.cursor_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cursor_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.cursor_table.setColumnWidth(column, 170)
            self.cursor_table.horizontalHeaderItem(column).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for r, name in enumerate(CURSOR_ROWS):
            for c in range(4):
                item = QTableWidgetItem(name if c == 0 else "")
                if c:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.cursor_table.setItem(r, c, item)
        fit_table_height(self.cursor_table)
        self.cursor_table.setVisible(False)
        col.addWidget(self.cursor_table)
        self.cursors_btn.toggled.connect(self.set_cursors)
        self.results = QLabel("")
        self.results.setObjectName("formNote")
        self.results.setWordWrap(True)
        col.addWidget(self.results)
        # A second Nyquist plot below, the textbook way (user, 2026-09-25, a picture of G = 1/(s^2+s+1)'s full contour:
        # "this is nyquist plot while our looks different i am confused" -> "add both the mirror half and the closed loop
        # T toggle but don't remove current one just add both in different graph below"): the measured half and its
        # mirror image (negative frequencies, the complex conjugate), for the closed loop T as measured - which starts
        # near (1, 0) and heads for the origin, the familiar shape - or for the open loop L the plot above reads from.
        box, lay = card("Nyquist - the full contour")
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("Show"))
        self.contour_view = ComboBox()
        for key, label in CONTOUR_VIEWS:
            self.contour_view.addItem(label, key)
        view_row.addWidget(self.contour_view)
        view_row.addStretch(1)
        lay.addLayout(view_row)
        (self.contour_plot, self.contour_curve, self.contour_mirror, self.contour_warn,
         self.contour_minus_one) = self._make_contour()
        theme.enable_zoom(self.contour_plot, reset=self._fit_contour_view)
        plot_row = QHBoxLayout()
        plot_row.addWidget(self.contour_plot)
        plot_row.addStretch(1)
        lay.addLayout(plot_row)
        self.contour_marks, self.contour_labels = {}, {}
        for name, colour in CURSOR_COLOURS.items():
            self.contour_marks[name] = self.contour_plot.plot(pen=None, symbol="s" if name == "A" else "t", symbolSize=13,
                                                              symbolBrush=None, symbolPen=pg.mkPen(colour, width=2))
            label = pg.TextItem(name, color=colour, anchor=(-0.4, 1.3))
            label.setVisible(False)
            self.contour_plot.addItem(label)
            self.contour_labels[name] = label
        self.contour_note = QLabel("The measured half (0 ≤ ω, gold, one point per frequency) and its mirror image for negative "
                                   "frequencies (dashed blue, the complex conjugate) - the full contour a textbook draws. The "
                                   "closed loop T starts near (1, 0), where the loop tracks its reference perfectly, and heads for "
                                   "the origin as its gain falls; the open loop L = T / (1 − T) is what the stability margins are "
                                   "read from (the plot beside the Bode plots), so the −1 point is marked in that view only. Both "
                                   "begin and end where the measurement does: nothing below the lowest or above the highest "
                                   "frequency is drawn, so a curve rarely closes on the origin the way a formula's does.")
        self.contour_note.setObjectName("formNote")
        self.contour_note.setWordWrap(True)
        lay.addWidget(self.contour_note)
        col.addWidget(box)
        self.contour_view.currentIndexChanged.connect(lambda _i=0: self._redraw_contour())
        col.addStretch(1)

        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.method.currentIndexChanged.connect(self._method_changed)
        for field in (self.amplitude, self.f_start, self.f_stop, self.n_points, self.settle_cycles, self.measure_cycles,
                      self.chirp_period, self.chirp_cycles):
            field.textEdited.connect(lambda _t="": self.refresh())
        self.start_btn.clicked.connect(lambda _c=False: self.start())
        self.stop_btn.clicked.connect(lambda _c=False: self.stop())
        self.save_csv_btn.clicked.connect(lambda _c=False: self.save_csv_dialog())
        self.save_image_btn.clicked.connect(lambda _c=False: self.save_image_dialog())
        self.prepare_fast_btn.clicked.connect(lambda _c=False: self.set_rate(FAST_HZ))
        self.restore_rate_btn.clicked.connect(lambda _c=False: self.set_rate(NORMAL_HZ))
        # After a reconnect (the power cycle) re-read the timer - but only while this page is on screen: connecting
        # itself must read nothing beyond the pole count, the mapping and the identity (the standing rule, pinned by
        # test_telemetry_shows_the_drive_broadcast_and_sends_no_requests); showEvent reads it when the page opens.
        session.probed.connect(lambda _ok=True, _msg="": self.refresh_timer() if self.isVisible() else None)
        session.disconnected.connect(self._timer_lost)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)     # one slot, dispatching on the phase (settle -> measure)
        self._live = QTimer(self)
        self._live.setInterval(400)
        self._live.timeout.connect(self.refresh)
        self._kind_changed()
        self._method_changed()

    def chirp_mode(self):
        return self.method.currentData() == "chirp"

    def _method_changed(self, _index=0):
        chirp = self.chirp_mode()
        for w in self._sine_widgets:
            w.setVisible(not chirp)
        for w in self._chirp_widgets:
            w.setVisible(chirp)
        self.refresh()

    @staticmethod
    def _chirp_seconds(period, cycles):
        """The recording: the asked cycles plus 8 % - a cycle measured 10.0-10.5 x Period on the bench drive, and its
        last few percent are the only part with the high frequencies, so cutting one short loses exactly them."""
        return cycles * bode.CHIRP_CYCLE * period * 1.08

    @staticmethod
    def _make_nyquist():
        """The open loop L = T / (1 - T) in the complex plane: the unit circle dashed in ruby, the -1 point in ruby, the gain
        crossover (|L| = 1, where the phase margin is read) as a sapphire diamond; the view is fixed to +-NYQUIST_VIEW so
        the pass band's huge |L| (mostly measurement noise, see bode.open_loop) never squashes the part that matters."""
        plot = pg.PlotWidget()
        plot.setFixedSize(NYQUIST_MIN_PX, NYQUIST_MIN_PX)      # _fit_plots sizes it to the Bode pair beside it
        theme.style_plot(plot, "Nyquist - open loop L = T / (1 - T)", "")
        item = plot.getPlotItem()
        item.setAspectLocked(True)
        item.setLabel("bottom", "Re L")
        item.setLabel("left", "Im L")
        angles = np.linspace(0.0, 2.0 * math.pi, 181)
        plot.plot(np.cos(angles), np.sin(angles),           # the unit circle in ruby, like the -1 point: the hairline
                  pen=pg.mkPen(theme.RUBY, width=1.5, style=Qt.PenStyle.DashLine))   # colour was barely visible (user)
        plot.plot([-1.0], [0.0], pen=None, symbol="+", symbolSize=16, symbolPen=pg.mkPen(theme.RUBY, width=2))
        curve = plot.plot(pen=pg.mkPen(theme.GOLD, width=2), symbol="o", symbolSize=6, symbolBrush=theme.GOLD, symbolPen=None)
        warn = plot.plot(pen=None, symbol="o", symbolSize=10, symbolBrush=None, symbolPen=pg.mkPen(theme.AMBER, width=2))
        cross = plot.plot(pen=None, symbol="d", symbolSize=13, symbolBrush=theme.SAPPHIRE, symbolPen=None)
        item.disableAutoRange()
        item.setXRange(-NYQUIST_VIEW, NYQUIST_VIEW, padding=0)
        item.setYRange(-NYQUIST_VIEW, NYQUIST_VIEW, padding=0)
        return plot, curve, warn, cross

    @staticmethod
    def _make_contour():
        """The full-contour Nyquist plot: the measured half with symbols, its mirror dashed, the unit circle, the -1 point
        (shown for the open loop only), a legend naming the two halves; aspect-locked, the view set by _fit_contour_view."""
        plot = pg.PlotWidget()
        plot.setFixedSize(NYQUIST_MIN_PX, NYQUIST_MIN_PX)      # _fit_plots sizes it like the Nyquist plot above
        theme.style_plot(plot, "Nyquist - full contour", "")
        item = plot.getPlotItem()
        item.setAspectLocked(True)
        item.setLabel("bottom", "Re")
        item.setLabel("left", "Im")
        item.addLegend(offset=(-8, 8), labelTextColor=theme.TEXT, brush=pg.mkBrush(theme.PANEL), pen=pg.mkPen(theme.LINE))
        angles = np.linspace(0.0, 2.0 * math.pi, 181)
        plot.plot(np.cos(angles), np.sin(angles), pen=pg.mkPen(theme.RUBY, width=1.5, style=Qt.PenStyle.DashLine))
        minus_one = plot.plot([-1.0], [0.0], pen=None, symbol="+", symbolSize=16, symbolPen=pg.mkPen(theme.RUBY, width=2))
        curve = plot.plot(pen=pg.mkPen(theme.GOLD, width=2), symbol="o", symbolSize=6, symbolBrush=theme.GOLD, symbolPen=None,
                          name="0 ≤ ω : the measured half")
        mirror = plot.plot(pen=pg.mkPen(theme.SAPPHIRE, width=1.5, style=Qt.PenStyle.DashLine),
                           name="ω &lt; 0 : its mirror image")           # the legend is HTML: a bare "<" ate the label
        warn = plot.plot(pen=None, symbol="o", symbolSize=10, symbolBrush=None, symbolPen=pg.mkPen(theme.AMBER, width=2))
        item.disableAutoRange()
        item.setXRange(-CONTOUR_VIEW_T, CONTOUR_VIEW_T, padding=0)
        item.setYRange(-CONTOUR_VIEW_T, CONTOUR_VIEW_T, padding=0)
        return plot, curve, mirror, warn, minus_one

    @staticmethod
    def _make_plot(label, units, colour):
        plot = pg.PlotWidget()
        plot.setFixedHeight(PLOT_MIN_PX)           # _fit_plots grows it to half the page once the page has a size
        theme.style_plot(plot, label, units)
        item = plot.getPlotItem()
        item.setLogMode(x=True, y=False)
        item.setLabel("bottom", "Frequency", units="Hz")
        curve = plot.plot(pen=pg.mkPen(colour, width=2), symbol="o", symbolSize=7, symbolBrush=colour, symbolPen=None)
        warn = plot.plot(pen=None, symbol="o", symbolSize=11, symbolBrush=None, symbolPen=pg.mkPen(theme.AMBER, width=2))
        return plot, curve, warn

    # ---- state ------------------------------------------------------------------------------
    @property
    def running(self):
        return self._phase != "idle"

    def _test(self):
        return TESTS[self.kind.currentIndex()]

    def _kind_changed(self, _index=0):
        _key, _label, amp_name, units, _watch, _mode = self._test()
        self.amplitude_label.setText(amp_name)
        self.amplitude_units.setText(units)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._live.start()
        self.refresh()
        if self.timer_hz is None:
            self.refresh_timer()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_plots()

    def _fit_plots(self):
        """All three plots fit the page at once (user, 2026-09-25, the gain plot alone filling the screen: "make size of
        both graph small so it can fit in one screen so user can see both graph at the same time without scroll up or
        down", then "nyquist plot is not visible in screen"): each Bode plot gets half the viewport's height, between
        PLOT_MIN_PX and PLOT_MAX_PX, and the Nyquist square beside the pair is as tall as it, capped at
        NYQUIST_WIDTH_SHARE of the page's width so the Bode plots keep their room. Without a fixed size a PlotWidget's
        size hint is its own current size, so the plots simply kept whatever the first layout pass gave them."""
        viewport = self.viewport()
        gap = self.bode_col.spacing()
        each = max(PLOT_MIN_PX, min(PLOT_MAX_PX, (viewport.height() - gap) // 2))
        for plot in (self.gain_plot, self.phase_plot):
            if plot.minimumHeight() != each or plot.maximumHeight() != each:
                plot.setFixedHeight(each)
        side = max(NYQUIST_MIN_PX, min(2 * each + gap, int(viewport.width() * NYQUIST_WIDTH_SHARE)))
        for plot in (self.nyquist_plot, self.contour_plot):
            if plot.minimumWidth() != side or plot.maximumWidth() != side:
                plot.setFixedSize(side, side)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._live.stop()

    def values(self):
        """-> (amplitude, frequencies, settle_cycles, measure_cycles); ValueError says what is wrong."""
        def number(field, name, whole=False):
            try:
                return int(field.text().strip(), 0) if whole else float(field.text().strip())
            except ValueError:
                raise ValueError(f"{name} must be a {'whole ' if whole else ''}number") from None
        amplitude = number(self.amplitude, self._test()[2])
        if not amplitude > 0:
            raise ValueError(f"{self._test()[2]} must be more than 0")
        key = self._test()[0]

        def encodable(*values):
            for param, value in zip(defs.load_commands()[key].params, values):
                try:
                    P.encode_value(param.dtype, value)
                except ValueError as exc:
                    raise ValueError(f"{param.name}: {exc}") from None

        if self.chirp_mode():
            period, cycles = number(self.chirp_period, "Period"), number(self.chirp_cycles, "Cycles", True)
            if not period > 0:
                raise ValueError("Period must be more than 0")
            if cycles < 1:
                raise ValueError("Cycles needs at least 1")
            encodable(period, amplitude, bode.CHIRP)
            period = P.wire_value("FLOAT16", period)                    # the drive counts the Period in 1/256 s
            if period <= 0.0:
                raise ValueError("Period must be at least 1/256 s")
            return amplitude, period, cycles
        freqs = bode.frequencies(number(self.f_start, "From"), number(self.f_stop, "To"), number(self.n_points, "Points", True))
        settle, measure = number(self.settle_cycles, "Settle", True), number(self.measure_cycles, "Measure", True)
        if settle < 1 or measure < 2:
            raise ValueError("Settle needs at least 1 cycle and Measure at least 2")
        encodable(1.0 / freqs[0], amplitude, SINE)
        return amplitude, freqs, settle, measure

    def _durations(self, freqs, settle, measure):
        return [(max(settle / f, MIN_SETTLE_S), max(measure / f, MIN_MEASURE_S)) for f in freqs]

    def checks(self):
        client = self.s.client
        if client is None:
            return [Check("block", "Not connected to a drive.")]
        out = []
        try:
            if self.chirp_mode():
                _amp, period, cycles = self.values()
                total = self._chirp_seconds(period, cycles)
                out.append(Check("ok", f"The chirp's values are valid: from {1.0 / period:.3g} Hz, {cycles} cycle(s) of "
                                       f"{bode.CHIRP_CYCLE * period:.3g} s - about {total:.0f} s."))
            else:
                _amp, freqs, settle, measure = self.values()
                total = sum(a + b for a, b in self._durations(freqs, settle, measure))
                out.append(Check("ok", f"The sweep's values are valid: {len(freqs)} frequencies, about {total:.0f} s."))
        except ValueError as exc:
            out.append(Check("block", str(exc)))
        snap, age = client.telemetry_snapshot(), client.telemetry_age()
        if not snap or age is None or age > 2.0:
            out.append(Check("block", "No telemetry is arriving from the drive."))
            return out
        if not client.mapping_from_drive:
            out.append(Check("block", "The drive's telemetry mapping could not be read, so it cannot be switched and put back."))
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
            out.append(Check("warn", f"'{name}' is running: starting the sweep will replace it."))
        return out

    def refresh(self):
        checks = self.checks()
        html_text = "<br>".join(f"<span style='color:{SYMBOL[level][1]}'>{SYMBOL[level][0]}</span>&nbsp; {text}"
                                for level, text in checks)
        if self.check_list.text() != html_text:
            self.check_list.setText(html_text)
        running = self.running
        self.start_btn.setEnabled(not running and not any(c.level == "block" for c in checks))
        self.stop_btn.setEnabled(running)
        for w in (self.kind, self.method, self.amplitude, self.f_start, self.f_stop, self.n_points, self.settle_cycles,
                  self.measure_cycles, self.chirp_period, self.chirp_cycles):
            w.setEnabled(not running)
        self.chirp_note.setText(self._chirp_text())
        self.save_csv_btn.setEnabled(not running and bool(self.points))
        self.save_image_btn.setEnabled(not running and bool(self.points))
        connected = self.s.client is not None
        self.prepare_fast_btn.setEnabled(connected and not running and self.timer_hz != FAST_HZ)
        self.restore_rate_btn.setEnabled(connected and not running and self.timer_hz != NORMAL_HZ)
        self.rate_note.setText(self._rate_text())
        if not running:
            first = next((c.text for c in checks if c.level == "block"), "")
            if first:
                self._set_status(first, "warn")
            elif not self.points and not self._reported:
                self._set_status("Ready to start.", "ok")

    def _set_status(self, text, level=""):
        self.status.setText(text)
        set_prop(self.status, "state", level)

    def _chirp_text(self):
        try:
            period = P.wire_value("FLOAT16", float(self.chirp_period.text().strip()))
            cycles = int(self.chirp_cycles.text().strip(), 0)
        except ValueError:
            return "The drive's own sweep starts at 1/Period and rises to a few hundred hertz over 10 × Period, then repeats."
        if period <= 0.0 or cycles < 1:
            return "The drive's own sweep starts at 1/Period and rises to a few hundred hertz over 10 × Period, then repeats."
        return (f"The drive's own sweep starts at {1.0 / period:.3g} Hz and rises, faster and faster, to a few hundred hertz "
                f"at the end of each {bode.CHIRP_CYCLE * period:.3g} s cycle; {cycles} cycle(s) = {self._chirp_seconds(period, cycles):.0f} s "
                f"recorded, then analysed by spectrum (Welch) into points averaged 12 per octave, each point's quality being "
                f"the coherence there. A long Period puts more energy at the high frequencies (4 s or more for a resonance "
                f"search); a short one sweeps quickly. Nothing above about {bode.CHIRP_TOP_HZ:g} Hz is reached.")

    # ---- the drive's telemetry rate (its timer interrupt) ---------------------------------------
    def _rate_text(self):
        if self.s.client is None:
            return "Connect to a drive to see its telemetry rate."
        if self.timer_hz is None:
            return "Reading the drive's timer frequency…"
        return (f"The drive's timer runs at {self.timer_hz:g} Hz, so its telemetry - and a sweep's sampling - is at most "
                f"{self.timer_hz:g} messages/s. " +
                (f"PREPARE {FAST_HZ:g} Hz doubles that for the sweeps: it writes the receiving frequency, saves the drive's "
                 f"configuration to flash and asks you for a power cycle; BACK TO {NORMAL_HZ:g} Hz undoes it the same way when "
                 "you are done." if self.timer_hz < FAST_HZ else
                 f"Sweeps sample at {self.timer_hz:g} Hz now. When you are done, BACK TO {NORMAL_HZ:g} Hz puts the drive back "
                 "to its normal rate (write, save to flash, power cycle)."))

    def refresh_timer(self):
        """Reads Timer ISR Frequency once (an SDO read, in the background) - at first show and after every reconnect."""
        client = self.s.client
        if client is None:
            self.timer_hz = None
            self.refresh()
            return

        def done(value):
            if self.s.client is client:
                self.timer_hz = float(value)
                if self.rate_instruction.isVisible() and self._expected_hz == self.timer_hz:
                    self.rate_instruction.setVisible(False)      # the power cycle happened: the instruction is done
                self.refresh()

        run_async(lambda emit, cancel: client.read_raw(*TIMER_ISR).value("FLOAT32"), on_done=done,
                  on_failed=lambda exc: self._timer_lost(), owner=self)

    def _timer_lost(self):
        self.timer_hz = None
        self.refresh()

    _expected_hz = None

    def set_rate(self, hz):
        """PREPARE / BACK TO: writes CANopen: receiving frequency (the timer's rate at the next boot), saves the drive's
        whole configuration to flash (client.save_to_flash - the vendor IDE's own request) and tells the user to
        power-cycle and reconnect. Both steps are asked for first: the save persists everything in the drive's RAM."""
        client = self.s.client
        if client is None or self.running:
            return
        if not self._confirm_rate(f"Set the drive's telemetry rate to {hz:g} Hz (node {self.s.node_id})?\n\n"
                                  f"•  'CANopen: receiving frequency' is written to {hz:g} Hz.\n"
                                  "•  The drive's WHOLE current configuration is then saved to flash (everything in its RAM "
                                  "becomes what it loads at power-up).\n"
                                  "•  You then power-cycle the drive and reconnect; the new rate applies from that boot."):
            self.message.emit("Telemetry rate change cancelled - nothing was sent.", "info")
            return
        reg = self._config[(0x3000, 0x06)]
        self._expected_hz = hz
        self.prepare_fast_btn.setEnabled(False)
        self.restore_rate_btn.setEnabled(False)

        def job(emit, cancel):
            client.write(reg, hz)
            client.save_to_flash()

        def done(_result):
            self.rate_instruction.setText(f"Receiving frequency {hz:g} Hz written and saved to flash. Now POWER-CYCLE the drive, "
                                          "then Disconnect and Connect again - the timer will run at "
                                          f"{hz:g} Hz from that boot.")
            set_prop(self.rate_instruction, "state", "warn")
            self.rate_instruction.setVisible(True)
            self.message.emit(f"Receiving frequency {hz:g} Hz saved to flash - power-cycle the drive, then reconnect.", "warn")
            self.refresh()

        def failed(exc):
            self.rate_instruction.setText(f"Could not set the telemetry rate: {exc}")
            set_prop(self.rate_instruction, "state", "error")
            self.rate_instruction.setVisible(True)
            self.message.emit(f"Could not set the telemetry rate: {exc}", "error")
            self.refresh()

        run_async(job, on_done=done, on_failed=failed, owner=self)

    def _confirm_rate(self, text):
        return QMessageBox.question(self, "Telemetry rate", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _confirm(self, text):
        return QMessageBox.question(self, "Frequency Response", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    # ---- the sweep ----------------------------------------------------------------------------
    def start(self):
        client = self.s.client
        if client is None or self.running:
            return
        checks = self.checks()
        blocks = [c.text for c in checks if c.level == "block"]
        if blocks:
            self.message.emit(f"Cannot start: {blocks[0]}", "warn")
            self.refresh()
            return
        values = self.values()
        key, label, amp_name, units, _watch, _mode = self._test()
        if self.chirp_mode():
            amplitude, period, cycles = values
            freqs, settle, measure = [], 0, 0
            total = self._chirp_seconds(period, cycles)
            plan = (f"    Chirp: the drive's own sweep from {1.0 / period:.3g} Hz up to a few hundred Hz, {cycles} cycle(s) of "
                    f"{bode.CHIRP_CYCLE * period:.3g} s (about {total:.0f} s), analysed by spectrum")
        else:
            amplitude, freqs, settle, measure = values
            total = sum(a + b for a, b in self._durations(freqs, settle, measure))
            plan = f"    Frequencies: {freqs[0]:.3g} - {freqs[-1]:.3g} Hz, {len(freqs)} points (about {total:.0f} s)"
        warnings = "".join(f"\n⚠  {c.text}" for c in checks if c.level == "warn")
        if not self._confirm(f"Measure the frequency response of '{label}' on node {self.s.node_id}?\n\n"
                             f"    {amp_name}: {amplitude:g} {units}\n"
                             f"{plan}\n\n"
                             "•  The motor moves continuously - a sine wave into the loop - for the whole sweep.\n"
                             "•  The drive's telemetry is switched to carry only this loop's reference and response at "
                             f"{self.timer_hz or MEASUREMENT_HZ:g} messages/s (its timer rate), and put back exactly as it was "
                             "when the sweep ends or is stopped.\n"
                             "•  Press STOP (or Esc) at any time to end it." + (f"\n{warnings}" if warnings else "")):
            self._set_status("Sweep cancelled - nothing was sent.", "info")
            self.message.emit("Sweep cancelled - nothing was sent.", "info")
            return
        self._loop = (key, amplitude, LOOPS[key])
        self._freqs, self._settle, self._measure = freqs, settle, measure
        self._chirp = (period, cycles) if self.chirp_mode() else None
        self._index, self.points, self._original, self._reported = 0, [], None, False
        self._reset_views()
        self._redraw()
        self._phase = "switching"
        self._set_status("Switching the drive's telemetry to this loop's reference and response…", "warn")
        self.message.emit(f"Frequency response of '{label}': switching the telemetry…", "warn")
        self.refresh()
        ref_reg, resp_reg = LOOPS[key]
        run_async(lambda emit, cancel: self._switch_job(client, ref_reg, resp_reg, emit),
                  on_done=self._switched, on_failed=self._switch_failed, on_progress=self._remember_original, owner=self)

    def _switch_job(self, client, ref_reg, resp_reg, emit):
        """Worker thread: read the drive's own telemetry frequency and 12 mapping slots (reported through `emit` BEFORE
        anything is written, so a failure below can still be undone), then map only the reference and the response
        into message 0, empty messages 1 and 2, and set the frequency to 1000 Hz."""
        regs = self._config
        original = {"frequency": client.read(regs[TELEMETRY_FREQUENCY]),
                    "mapping": {key: client.read(regs[key]) for key in MAPPING_SLOTS}}
        emit(original)
        try:
            rate = float(client.read_raw(*TIMER_ISR).value("FLOAT32"))    # one TPDO per timer tick: ask for exactly that
        except DriveError:
            rate = MEASUREMENT_HZ
        self._sample_hz = rate
        for key, code in self._measurement_mapping(ref_reg, resp_reg).items():
            if original["mapping"][key] != code:
                client.write(regs[key], code)
        if original["frequency"] != rate:
            client.write(regs[TELEMETRY_FREQUENCY], rate)
        return original

    @staticmethod
    def _measurement_mapping(ref_reg, resp_reg):
        wanted = {key: 0 for key in MAPPING_SLOTS}
        wanted[(0x1A00, 1)] = defs.mapping_code(*ref_reg)
        wanted[(0x1A00, 2)] = defs.mapping_code(*resp_reg)
        return wanted

    def _remember_original(self, original):
        self._original = original

    def _switch_failed(self, exc):
        self.message.emit(f"Could not switch the drive's telemetry: {exc}", "error")
        self._set_status(f"Could not switch the drive's telemetry: {exc}", "error")
        self._finish(stopped=True)

    def _switched(self, original):
        self._original = original
        if self._phase != "switching":          # STOP came while the switch was running
            return
        if self._chirp is not None:
            self._start_chirp()
        else:
            self._start_point()

    def _start_chirp(self):
        """The drive's own frequency sweep, recorded for the asked number of cycles, then analysed by spectrum."""
        client = self.s.client
        if client is None or self._phase == "idle":
            return
        key, amplitude, _regs = self._loop
        period, cycles = self._chirp
        try:
            client.run_test(key, period, amplitude, bode.CHIRP)
        except (DriveError, ValueError) as exc:
            self.message.emit(f"Could not command the sweep: {exc}", "error")
            self._set_status(f"Could not command the sweep: {exc}", "error")
            self._finish(stopped=True)
            return
        self._chirp_s = self._chirp_seconds(period, cycles)
        self._phase = "chirp"
        self._set_status(f"Chirp: recording {cycles} cycle(s) of the drive's sweep from {1.0 / period:.3g} Hz - "
                         f"{self._chirp_s:.0f} s…", "warn")
        self._timer.start(int((self._chirp_s + 0.3) * 1000))

    def _chirp_done(self):
        if self._phase != "chirp":
            return
        client = self.s.client
        if client is None:
            return
        _key, _amp, (ref_reg, resp_reg) = self._loop
        period, _cycles = self._chirp
        t_ref, ref = client.history_snapshot(defs.TELEMETRY_KEYS[ref_reg], seconds=self._chirp_s)
        t_res, res = client.history_snapshot(defs.TELEMETRY_KEYS[resp_reg], seconds=self._chirp_s)
        t, ref, res = bode.align(t_ref, ref, t_res, res)
        try:
            if len(t) < bode.MIN_SAMPLES or t[-1] <= t[0]:
                raise ValueError("no telemetry samples arrived - is the drive broadcasting?")
            fs = (len(t) - 1) / float(t[-1] - t[0])               # the rate the frames really came at
            self.points = bode.chirp_response(ref, res, fs, 1.0 / period, min(bode.CHIRP_TOP_HZ, 0.45 * fs))
        except ValueError as exc:
            self.message.emit(f"Chirp: {exc} - sweep stopped.", "error")
            self._set_status(f"Chirp: {exc} - sweep stopped.", "error")
            self._finish(stopped=True)
            return
        self._redraw()
        self._finish(stopped=False)

    def _start_point(self):
        client = self.s.client
        if client is None or self._phase == "idle":
            return
        if self._index >= len(self._freqs):
            self._finish(stopped=False)
            return
        key, amplitude, _regs = self._loop
        f = self._freqs[self._index]
        try:
            if self._index > 0:
                client.stop()           # a fresh start for every point: the drive's sine generator drifts off the
            client.run_test(key, 1.0 / f, amplitude, SINE)    # asked frequency the longer one test runs (bode.refine_frequency)
        except (DriveError, ValueError) as exc:
            self.message.emit(f"Could not command the test at {f:.3g} Hz: {exc}", "error")
            self._set_status(f"Could not command the test at {f:.3g} Hz: {exc}", "error")
            self._finish(stopped=True)
            return
        settle_s, self._measure_s = self._durations([f], self._settle, self._measure)[0]
        self._phase = "settle"
        self._set_status(f"Point {self._index + 1} of {len(self._freqs)}: {f:.3g} Hz - settling…", "warn")
        self._timer.start(int(settle_s * 1000))

    def _on_timer(self):
        if self._phase == "settle":
            self._settled()
        elif self._phase == "measure":
            self._measured()
        elif self._phase == "chirp":
            self._chirp_done()

    def _settled(self):
        self._phase = "measure"
        f = self._freqs[self._index]
        self._set_status(f"Point {self._index + 1} of {len(self._freqs)}: {f:.3g} Hz - measuring…", "warn")
        self._timer.start(int(self._measure_s * 1000))

    def _measured(self):
        if self._phase != "measure":
            return
        client = self.s.client
        if client is None:
            return
        f = self._freqs[self._index]
        _key, _amp, (ref_reg, resp_reg) = self._loop
        t_ref, ref = client.history_snapshot(defs.TELEMETRY_KEYS[ref_reg], seconds=self._measure_s)
        t_res, res = client.history_snapshot(defs.TELEMETRY_KEYS[resp_reg], seconds=self._measure_s)
        t, ref, res = bode.align(t_ref, ref, t_res, res)
        if len(t) < bode.MIN_SAMPLES:
            self.message.emit(f"No telemetry samples arrived for {f:.3g} Hz - is the drive broadcasting? Sweep stopped.", "error")
            self._set_status(f"No telemetry samples arrived for {f:.3g} Hz - sweep stopped.", "error")
            self._finish(stopped=True)
            return
        try:
            point = bode.response_point(t, ref, res, f)     # fitted at the frequency the reference really shows
        except ValueError as exc:
            self.message.emit(f"{f:.3g} Hz: {exc} - sweep stopped.", "error")
            self._set_status(f"{f:.3g} Hz: {exc} - sweep stopped.", "error")
            self._finish(stopped=True)
            return
        self.points.append(point)
        self._redraw()
        self._index += 1
        self._start_point()

    def _finish(self, stopped):
        """Ends the sweep: the drive stopped, its telemetry put back (in the background), the page idle."""
        self._timer.stop()
        client = self.s.client
        if client is not None and client.commanded is not None and client.commanded[0] in LOOPS:
            try:
                client.stop()
            except DriveError as exc:
                self.message.emit(f"STOP failed: {exc}", "error")
        n = len(self.points)
        if self._original is not None and client is not None:
            self._phase = "restoring"
            self._set_status("Putting the drive's telemetry back…", "warn")
            original, self._original = self._original, None
            run_async(lambda emit, cancel: self._restore_job(client, original),
                      on_done=lambda failures: self._restored(failures, stopped, n),
                      on_failed=lambda exc: self._restored([str(exc)], stopped, n), owner=self)
        else:
            self._phase = "idle"
            self._report(stopped, n, [])
        self.refresh()

    def _restore_job(self, client, original):
        regs, failures = self._config, []
        _key, _amp, (ref_reg, resp_reg) = self._loop
        wanted = self._measurement_mapping(ref_reg, resp_reg)
        for key, code in original["mapping"].items():
            if code != wanted[key]:
                try:
                    client.write(regs[key], code)
                except (DriveError, ValueError) as exc:
                    failures.append(f"0x{key[0]:04X}:0x{key[1]:02X} = {code} ({exc})")
        if original["frequency"] != (self._sample_hz or MEASUREMENT_HZ):
            try:
                client.write(regs[TELEMETRY_FREQUENCY], original["frequency"])
            except (DriveError, ValueError) as exc:
                failures.append(f"CANopen: telemetry frequency = {original['frequency']:g} ({exc})")
        return failures

    def _restored(self, failures, stopped, n):
        self._phase = "idle"
        self._report(stopped, n, failures)
        self.refresh()

    def _report(self, stopped, n, failures):
        self._reported = True
        if failures:
            text = ("The drive's telemetry could NOT be fully put back - set these by hand in the Parameters Explorer: "
                    + "; ".join(failures))
            self._set_status(text, "error")
            self.message.emit(text, "error")
            return
        summary = self._summary()
        if stopped:
            self._set_status(f"Sweep stopped after {n} point(s). The drive is stopped and its telemetry is back as it was."
                             + (f" {summary}" if n else ""), "warn")
            self.message.emit(f"Frequency response sweep stopped after {n} point(s).", "warn")
        else:
            rate = f" Sampled at {self._sample_hz:g} Hz." if self._sample_hz else ""
            self._set_status(f"Sweep complete: {n} points.{rate} The drive is stopped and its telemetry is back as it was. {summary}", "ok")
            self.message.emit(f"Frequency response sweep complete: {n} points.", "ok")

    def _summary(self):
        pts = self.points
        if len(pts) < 2:
            return ""
        parts = []
        good = [p for p in pts if p.quality >= QUALITY_FLOOR]     # bandwidth and resonance from the trustworthy points only:
        pts = good if len(good) >= 3 else pts                     # a hollow point is noise, not a peak (the first real sweep
        bw = bode.bandwidth(pts)                                  # reported "+63 dB at 13 Hz" from exactly such a point)
        if bw is None:
            parts.append(f"No -3 dB point within {pts[0].frequency:.3g} - {pts[-1].frequency:.3g} Hz "
                         f"(gain stays within 3 dB of {pts[0].gain_db:+.1f} dB).")
        else:
            phase = bode.phase_at(pts, bw)
            parts.append(f"-3 dB bandwidth: {bw:.3g} Hz" + (f", phase {phase:+.0f}° there." if phase is not None else "."))
        peak = bode.resonance(pts)
        if peak is not None:
            parts.append(f"Peak of {peak.gain_db:+.1f} dB at {peak.frequency:.3g} Hz - a resonance the notch filters can target.")
        m = bode.margins(self.points)
        if m.phase_margin_deg is not None:
            parts.append(f"Nyquist: phase margin {m.phase_margin_deg:.0f}° at the {m.gain_crossover_hz:.3g} Hz gain crossover"
                         + (f", gain margin {m.gain_margin_db:.1f} dB at {m.phase_crossover_hz:.3g} Hz." if m.gain_margin_db is not None
                            else " - the open loop's phase never reaches -180° within the range, so there is no gain margin to read."))
        elif len(pts) >= 2:
            parts.append("Nyquist: the open loop stays above 1 within the measured range - no gain crossover to read a phase margin at.")
        low_quality = [p for p in self.points if p.quality < QUALITY_FLOOR]
        if low_quality:
            parts.append(f"{len(low_quality)} point(s) drawn hollow: the response there did not follow the reference cleanly "
                         "(noise, saturation, a limit, or too little sweep energy) - left out of the bandwidth and peak above.")
        return " ".join(parts)

    def _redraw(self):
        pts = self.points
        f = [p.frequency for p in pts]
        self.gain_curve.setData(f, [p.gain_db for p in pts])
        self.phase_curve.setData(f, [p.phase_deg for p in pts])
        weak = [p for p in pts if p.quality < QUALITY_FLOOR]
        self.gain_warn.setData([p.frequency for p in weak], [p.gain_db for p in weak])
        self.phase_warn.setData([p.frequency for p in weak], [p.phase_deg for p in weak])
        self._set_ticks(f)
        ordered = bode.open_loop(sorted(pts, key=lambda p: p.frequency))
        # one line through EVERY point in frequency order, like the gain and phase plots - the path is what makes the
        # curve readable (user, 2026-09-25: "there should be line joining all points"); the hollow rings still mark
        # the points not to trust
        self.nyquist_curve.setData([l.real for _f, l, _q in ordered], [l.imag for _f, l, _q in ordered])
        poor = [(l.real, l.imag) for _f, l, q in ordered if q < QUALITY_FLOOR]
        self.nyquist_warn.setData([x for x, _y in poor], [y for _x, y in poor])
        self._fit_nyquist_view()
        m = bode.margins(pts)
        if m.phase_margin_deg is not None:                       # |L| = 1 at the phase-margin angle
            angle = math.radians(m.phase_margin_deg - 180.0)
            self.nyquist_cross.setData([math.cos(angle)], [math.sin(angle)])
        else:
            self.nyquist_cross.setData([], [])
        self.results.setText(self._summary())
        self._redraw_contour()
        self._place_cursors()

    # ---- cursors ---------------------------------------------------------------------------------
    def _sorted_points(self):
        return sorted(self.points, key=lambda p: p.frequency)

    def _cursor_index(self, name, pts):
        """Where a cursor sits among the points sorted by frequency: nearest (in log f) to the frequency it was last
        put at, or a third / two thirds of the way along when it has never been moved."""
        hz = self._cursor_hz.get(name)
        if hz is None:
            return len(pts) // 3 if name == "A" else (2 * len(pts)) // 3
        return min(range(len(pts)), key=lambda i: abs(math.log10(pts[i].frequency) - math.log10(hz)))

    def cursor_points(self):
        """{'A': BodePoint or None, 'B': ...} - the measured point each cursor sits on (None while the cursors are off)."""
        pts = self._sorted_points()
        on = self.cursors_btn.isChecked() and bool(pts)
        return {name: pts[self._cursor_index(name, pts)] if on else None for name in CURSOR_COLOURS}

    def set_cursors(self, on):
        """CURSORS: two frequency cursors, A and B, shared by the gain and phase plots (dragging one on either plot moves
        it on both), snapping to the measured points; the table under the plots reads them and the slope between."""
        if self.cursors_btn.isChecked() != on:
            self.cursors_btn.blockSignals(True)
            self.cursors_btn.setChecked(on)
            self.cursors_btn.blockSignals(False)
        self._place_cursors()

    def _place_cursors(self):
        pts = self._sorted_points()
        on = self.cursors_btn.isChecked() and bool(pts)
        for name, lines in self._cursors.items():
            if on:
                self._set_cursor_lines(name, math.log10(pts[self._cursor_index(name, pts)].frequency))
            for line in lines:
                line.setVisible(on)
        self.cursor_table.setVisible(self.cursors_btn.isChecked())
        self._read_cursors()

    def _set_cursor_lines(self, name, log_f):
        for line in self._cursors[name]:
            line.blockSignals(True)
            line.setValue(log_f)
            line.blockSignals(False)

    def _cursor_dragged(self, name, line):
        """While a cursor is dragged on one plot its twin follows on the other; the readout tracks the nearest point."""
        for other in self._cursors[name]:
            if other is not line:
                other.blockSignals(True)
                other.setValue(line.value())
                other.blockSignals(False)
        pts = self._sorted_points()
        if pts:
            self._cursor_hz[name] = 10.0 ** line.value()
        self._read_cursors()

    def _cursor_released(self, name, line):
        """Let go: both lines snap to the measured point nearest the drop - the response exists only there."""
        pts = self._sorted_points()
        if not pts:
            return
        self._cursor_hz[name] = 10.0 ** line.value()
        point = pts[self._cursor_index(name, pts)]
        self._cursor_hz[name] = point.frequency
        self._set_cursor_lines(name, math.log10(point.frequency))
        self._read_cursors()

    def _read_cursors(self):
        """The table: each cursor's point (gain, phase, quality, the open loop behind it) and, between them, the
        differences and the gain slope in dB/decade (-20 for a first-order roll-off, -40 for a second); the Nyquist
        plot marks the same two points."""
        at = self.cursor_points()
        ol = {f: l for f, l, _q in bode.open_loop(self.points)}

        def cell(r, c, text):
            self.cursor_table.item(r, c).setText(text)

        def angle(l):
            return math.degrees(cmath.phase(l))

        for c, name in ((1, "A"), (2, "B")):
            p = at[name]
            l = ol.get(p.frequency) if p is not None else None
            cell(0, c, f"{p.frequency:.4g}" if p else "")
            cell(1, c, f"{p.gain_db:+.2f}" if p else "")
            cell(2, c, f"{p.phase_deg:+.1f}" if p else "")
            cell(3, c, f"{p.quality:.2f}" if p else "")
            cell(4, c, f"{abs(l):.3g}" if l is not None else "")
            cell(5, c, f"{angle(l):+.1f}" if l is not None else "")
            cell(6, c, "")
        a, b = at["A"], at["B"]
        la, lb = (ol.get(a.frequency) if a else None), (ol.get(b.frequency) if b else None)
        both = a is not None and b is not None and a.frequency != b.frequency
        decades = math.log10(b.frequency / a.frequency) if both else 0.0
        cell(0, 3, f"{b.frequency - a.frequency:+.4g}  ({decades:+.2f} decades)" if both else "")
        cell(1, 3, f"{b.gain_db - a.gain_db:+.2f}" if both else "")
        cell(2, 3, f"{b.phase_deg - a.phase_deg:+.1f}" if both else "")
        cell(3, 3, "")
        cell(4, 3, f"{abs(lb) - abs(la):+.3g}" if both and la is not None and lb is not None else "")
        cell(5, 3, f"{angle(lb) - angle(la):+.1f}" if both and la is not None and lb is not None else "")
        cell(6, 3, f"{(b.gain_db - a.gain_db) / decades:+.1f}" if both else "")
        contour = {f: v for f, v, _q in self._contour_values()}
        for name in CURSOR_COLOURS:
            p = at[name]
            for values, marks, labels in ((ol, self.nyquist_marks, self.nyquist_labels),
                                          (contour, self.contour_marks, self.contour_labels)):
                v = values.get(p.frequency) if p is not None else None
                if v is not None:
                    marks[name].setData([v.real], [v.imag])
                    labels[name].setPos(v.real, v.imag)
                else:
                    marks[name].setData([], [])
                labels[name].setVisible(v is not None)

    def _fit_nyquist_view(self):
        """The view: +-NYQUIST_VIEW, unless the curve never comes that close - then wide enough that its nearest
        trustworthy point is on screen (a loop whose crossover lies beyond the measured range still shows WHERE it
        ends; the bench drive at Kp 2.25 had |L| = 2.5 at 256 Hz and drew one point on the edge). Also the Nyquist
        plot's double-click reset (theme.enable_zoom)."""
        view = self._open_loop_view(bode.open_loop(self._sorted_points()))
        item = self.nyquist_plot.getPlotItem()
        item.setXRange(-view, view, padding=0)
        item.setYRange(-view, view, padding=0)

    @staticmethod
    def _open_loop_view(values):
        near = [abs(l) for _f, l, q in values if q >= QUALITY_FLOOR] or [abs(l) for _f, l, _q in values]
        return min(NYQUIST_VIEW_MAX, max(NYQUIST_VIEW, 1.3 * min(near))) if near else NYQUIST_VIEW

    # ---- the full-contour plot -------------------------------------------------------------------
    def _contour_values(self):
        """[(frequency, value, quality)] for the full-contour plot: the closed loop T as measured, or the open loop L."""
        pts = self._sorted_points()
        if self.contour_view.currentData() == "closed":
            return [(p.frequency, cmath.rect(10.0 ** (p.gain_db / 20.0), math.radians(p.phase_deg)), p.quality) for p in pts]
        return bode.open_loop(pts)

    def _redraw_contour(self):
        values = self._contour_values()
        closed = self.contour_view.currentData() == "closed"
        xs, ys = [v.real for _f, v, _q in values], [v.imag for _f, v, _q in values]
        self.contour_curve.setData(xs, ys)
        self.contour_mirror.setData(xs, [-y for y in ys])                    # negative frequencies: the conjugate
        poor = [(v.real, v.imag) for _f, v, q in values if q < QUALITY_FLOOR]
        self.contour_warn.setData([x for x, _y in poor], [y for _x, y in poor])
        self.contour_minus_one.setVisible(not closed)                        # -1 means something for L only
        self._fit_contour_view()
        self._read_cursors()

    def _fit_contour_view(self):
        """+-CONTOUR_VIEW_T for the closed loop (grown if T ever peaks past it); the open loop's own adaptive view."""
        values = self._contour_values()
        if self.contour_view.currentData() == "closed":
            view = max(CONTOUR_VIEW_T, 1.2 * max([abs(v) for _f, v, _q in values], default=0.0))
        else:
            view = self._open_loop_view(values)
        item = self.contour_plot.getPlotItem()
        item.setXRange(-view, view, padding=0)
        item.setYRange(-view, view, padding=0)

    def _reset_views(self):
        """A zoom left from looking at the last sweep must not hide the next one's points."""
        for plot in (self.gain_plot, self.phase_plot):
            plot.getPlotItem().enableAutoRange()
        self._fit_nyquist_view()
        self._fit_contour_view()

    def _set_ticks(self, f):
        """1-2-5 ticks per decade across the measured range: pyqtgraph's own log-axis labels crowd into one another
        above 30 Hz (seen on the first real plot); with no points the axis is left to pyqtgraph."""
        ticks = None
        if f:
            lo, hi = math.floor(math.log10(min(f))), math.ceil(math.log10(max(f)))
            majors = [m * 10 ** d for d in range(lo, hi + 1) for m in (1, 2, 5) if min(f) / 1.5 <= m * 10 ** d <= max(f) * 1.5]
            ticks = [[(math.log10(v), f"{v:g}") for v in majors], []]        # log mode: tick positions are log10(x)
        for plot in (self.gain_plot, self.phase_plot):
            plot.getPlotItem().getAxis("bottom").setTicks(ticks)

    # ---- ending it early -------------------------------------------------------------------------
    def stop(self):
        """STOP: the drive stopped, the telemetry put back, whatever points were measured kept."""
        if not self.running:
            return
        self._finish(stopped=True)

    def emergency(self):
        """STOP / Esc, from anywhere in the window."""
        self.stop()

    def connection_closing(self):
        """The connection is about to be closed: put the telemetry back NOW, synchronously, while the drive can still
        be told (the background restore of stop() would run after the port is gone)."""
        if not self.running:
            return
        self._timer.stop()
        client = self.s.client
        original, self._original = self._original, None
        failures = []
        if client is not None:
            try:
                if client.commanded is not None and client.commanded[0] in LOOPS:
                    client.stop()
                if original is not None:
                    failures = self._restore_job(client, original)
            except DriveError as exc:
                failures.append(str(exc))
        self._phase = "idle"
        self._report(True, len(self.points), failures)
        self.refresh()

    def connection_closed(self):
        self.refresh()

    # ---- export --------------------------------------------------------------------------------
    def save_csv_dialog(self):
        if not self.points:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save frequency response", f"bode_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        marks = {id(p): name for name, p in self.cursor_points().items() if p is not None}     # "" for every row while off
        rows = list(bode.csv_rows(self.points))
        rows[0] += ("cursor",)
        for i, p in enumerate(self.points, 1):
            rows[i] += (marks.get(id(p), ""),)
        with open(path, "w", newline="") as fh:
            csv.writer(fh).writerows(rows)
        self.message.emit(f"Saved {len(self.points)} points to {path}", "ok")

    def save_image_dialog(self):
        if not self.points:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Bode plot image", f"bode_{time.strftime('%Y%m%d_%H%M%S')}.png",
                                              "PNG image (*.png)")
        if not path:
            return
        stack_pixmaps([self.gain_plot.grab(), self.phase_plot.grab(), self.nyquist_plot.grab(), self.contour_plot.grab()]).save(path)
        self.message.emit(f"Saved the Bode plot to {path}", "ok")
