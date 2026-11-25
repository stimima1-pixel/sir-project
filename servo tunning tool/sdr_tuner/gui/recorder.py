"""Recorder: records the signals the drive broadcasts, shows them in two charts, measures between two cursors and exports the
recording to a CSV file.

Like the rest of the tool it only listens to the drive's telemetry broadcast (about 9 messages per second): nothing is requested
from the drive. A recording is a slice of the values the client has already received, so it also follows the speed unit chosen
on the Single Axis Motion page."""
import csv
import time
from collections import namedtuple
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QDialog, QFileDialog, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton, QRadioButton,
                               QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .icons import icon
from .widgets import ComboBox, DoubleSpinBox, fit_table_height, make_button, set_prop, stack_pixmaps

COLOURS = (theme.GOLD, theme.SAPPHIRE, theme.EMERALD, theme.ROSE, theme.AMETHYST, theme.AMBER, theme.GOLD_HI)
DURATIONS = (("Until stopped", 0), ("10 s", 10), ("30 s", 30), ("1 min", 60), ("5 min", 300), ("10 min", 600))
REFRESH_MS = 100
CURSOR_COLOURS = {"A": theme.GOLD_HI, "B": theme.SAPPHIRE}
MIN_WINDOW_S = 5.0          # the time axis of a running recording is at least this long

Trigger = namedtuple("Trigger", "source slope level")     # slope: "rising" or "falling"


class TriggerDialog(QDialog):
    """Matches Elmo Application Studio III's own Trigger Editor (checked directly: Source / Slope - Rising,
    Falling, Out of Window - / Delay%) - built the Rising/Falling core of it, the two fundamental edge types;
    Out of Window (a range-based condition) and Delay (pre-trigger buffering, which needs a fixed-size ring
    buffer this tool's own growing history does not have) are deliberately left out of this first cut."""

    def __init__(self, options, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trigger Editor")
        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.source = ComboBox()
        self.source.addItem("(No Trigger)", None)
        for key, label in options:
            self.source.addItem(label, key)
        form.addRow("Source:", self.source)
        self.level = DoubleSpinBox()
        self.level.setDecimals(4)
        self.level.setRange(-1_000_000.0, 1_000_000.0)
        form.addRow("Level:", self.level)
        outer.addLayout(form)

        slope_box = QGroupBox("Slope")
        slope_row = QHBoxLayout(slope_box)
        self.rising = QRadioButton("Rising edge")
        self.falling = QRadioButton("Falling edge")
        self.rising.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.rising)
        group.addButton(self.falling)
        slope_row.addWidget(self.rising)
        slope_row.addWidget(self.falling)
        outer.addWidget(slope_box)

        if current is not None:
            idx = self.source.findData(current.source)
            if idx >= 0:
                self.source.setCurrentIndex(idx)
            self.level.setValue(current.level)
            (self.rising if current.slope == "rising" else self.falling).setChecked(True)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        ok_btn = make_button(QPushButton("OK"), "primary")
        buttons.addWidget(cancel_btn)
        buttons.addWidget(ok_btn)
        outer.addLayout(buttons)
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

    def result_trigger(self):
        source = self.source.currentData()
        if source is None:
            return None
        return Trigger(source, "rising" if self.rising.isChecked() else "falling", self.level.value())


def value_at(times, values, at):
    """The last value received at or before `at` (a broadcast value holds until the next one), or None before the first."""
    i = int(np.searchsorted(times, at, side="right")) - 1
    return None if i < 0 else float(values[i])


def fmt(value):
    return "—" if value is None else f"{value:.5g}"


class Chart(QFrame):
    """A title, a menu of the signals to draw, and the plot."""
    selection_changed = Signal()

    def __init__(self, title):
        super().__init__()
        self.setObjectName("plotcard")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(4)
        head = QHBoxLayout()
        self.title = QLabel(title.upper())
        self.title.setStyleSheet(f"color: {theme.GOLD}; font-size: 11px; font-weight: 800;")
        self.signals_btn = QToolButton()
        self.signals_btn.setObjectName("chartMenu")
        self.signals_btn.setText("Signals  ▾")
        self.signals_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.signals_btn.setToolTip("Choose the signals drawn in this chart")
        self.menu = QMenu(self.signals_btn)
        self.signals_btn.setMenu(self.menu)
        head.addWidget(self.title)
        head.addSpacing(10)
        head.addWidget(self.signals_btn)
        head.addStretch(1)
        v.addLayout(head)
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(90)
        theme.style_plot(self.plot, " ")
        item = self.plot.getPlotItem()
        item.setMouseEnabled(x=True, y=True)                      # a recording is looked at: zoom with the wheel, pan by dragging
        item.setLabel("bottom", "Time (sec)", **{"color": theme.MUTED, "font-size": "9pt"})
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.legend = self.plot.addLegend(offset=(-8, 4), labelTextColor=theme.MUTED)
        self.legend.setBrush(pg.mkBrush(theme.PANEL))
        v.addWidget(self.plot, 1)
        self.actions = {}           # key -> checkable menu entry
        self.curves = {}            # key -> curve being drawn

    def set_signals(self, options, defaults):
        """options: [(key, menu text)]. Keeps what was ticked and is still offered; otherwise ticks `defaults`."""
        ticked = {k for k, a in self.actions.items() if a.isChecked()} & {k for k, _ in options}
        if not ticked:
            ticked = {k for k in defaults if k in dict(options)}
        self.menu.clear()
        self.actions = {}
        for key, text in options:
            action = QAction(text, self.menu)
            action.setCheckable(True)
            action.setChecked(key in ticked)
            action.toggled.connect(lambda _on: self.selection_changed.emit())
            self.menu.addAction(action)
            self.actions[key] = action
        self.clear_curves()

    def checked_keys(self):
        return [k for k, a in self.actions.items() if a.isChecked()]

    def clear_curves(self):
        for curve in self.curves.values():
            self.plot.removeItem(curve)
        self.curves = {}

    def show_data(self, data, info, colour_of):
        """data: {key: (times, raw values)}; info: key -> (title, unit, factor); colour_of: key -> colour."""
        wanted = self.checked_keys()
        for key in [k for k in self.curves if k not in wanted]:
            self.plot.removeItem(self.curves.pop(key))
        for key in wanted:
            title, unit, factor = info[key]
            if key not in self.curves:
                name = f"{title} ({unit})" if unit else title
                self.curves[key] = self.plot.plot(pen=pg.mkPen(colour_of(key), width=2), name=name)
                self.curves[key].setDownsampling(auto=True, method="peak")   # a long fast recording: each pixel
                self.curves[key].opts["autoDownsampleFactor"] = 1.0          # column's min and max (as live_plots)
            t, v = data.get(key, (np.array([]), np.array([])))
            self.curves[key].setData(t, v * factor)
        units = {info[k][1] for k in wanted if info[k][1]}
        self.plot.getPlotItem().setLabel("left", units.pop() if len(units) == 1 else " ",
                                         **{"color": theme.MUTED, "font-size": "9pt"})


class RecorderPage(QWidget):
    state_changed = Signal(bool)            # True while recording
    buttons_changed = Signal()              # which of Start / Stop / Clear / Save can be used changed
    message = Signal(str, str)              # (text, level) for the window's status bar

    def __init__(self, session, drive_tab):
        super().__init__()
        self.s, self.drive = session, drive_tab
        self._recording = False
        self._t0 = self._t1 = None
        self._frozen = {}
        self._rec_keys, self._rec_info = [], {}     # the signals of the recording, fixed when it starts, and how they were named
        self._rec_client = None                     # the client it is recorded from (still there when the drive is disconnected)
        self._keys_seen = None
        self._syncing = False
        self.trigger = None                          # a Trigger, or None (Start begins recording immediately)
        self._armed = False                           # waiting for self.trigger to fire - no samples kept yet
        self._arm_last = None                         # the trigger source's own last-seen value, to detect a crossing
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.start_btn = make_button(QPushButton("  Start"), "primary")
        self.start_btn.setIcon(icon("record", 20))
        self.stop_btn = QPushButton("  Stop")
        self.stop_btn.setIcon(icon("stop", 20))
        self.clear_btn = QPushButton("  Clear")
        self.clear_btn.setIcon(icon("trash", 20))
        self.fit_btn = QPushButton("  Fit")
        self.fit_btn.setIcon(icon("fit", 20))
        self.cursors_btn = QPushButton("  Cursors")
        self.cursors_btn.setIcon(icon("cursors", 20))
        self.cursors_btn.setCheckable(True)
        self.save_btn = QPushButton("  Save…")
        self.save_btn.setIcon(icon("save", 20))
        self.save_image_btn = QPushButton("  Save Image…")
        self.save_image_btn.setIcon(icon("export", 20))
        self.trigger_btn = QPushButton("  Trigger…")
        self.trigger_btn.setIcon(icon("compare", 20))
        self.duration = ComboBox()
        for label, seconds in DURATIONS:
            self.duration.addItem(label, seconds)
        self.status = QLabel("")
        self.status.setObjectName("ctlStatus")
        for b in (self.start_btn, self.stop_btn, self.clear_btn, self.fit_btn, self.cursors_btn, self.save_btn,
                 self.save_image_btn, self.trigger_btn):
            b.setIconSize(QSize(20, 20))
            bar.addWidget(b)
        bar.addSpacing(10)
        bar.addWidget(QLabel("Record for"))
        bar.addWidget(self.duration)
        bar.addStretch(1)
        bar.addWidget(self.status)
        outer.addLayout(bar)

        self.hint = QLabel("Press Start to record the values the drive broadcasts (about 9 messages per second). "
                           "Nothing is requested from the drive.")
        self.hint.setProperty("muted", "true")
        outer.addWidget(self.hint)
        self.charts = [Chart("Chart #1"), Chart("Chart #2")]
        for chart in self.charts:
            outer.addWidget(chart, 1)
            chart.selection_changed.connect(self._refresh)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["SIGNAL", "A", "B", "B − A"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setVisible(False)
        outer.addWidget(self.table)

        self._cursors = {}          # "A" / "B" -> one line in each chart (they move together)
        for name in CURSOR_COLOURS:
            lines = []
            for chart in self.charts:
                line = pg.InfiniteLine(pos=0, angle=90, movable=True, pen=pg.mkPen(CURSOR_COLOURS[name], width=1.5, style=Qt.PenStyle.DashLine),
                                       label=name, labelOpts={"position": 0.92, "color": CURSOR_COLOURS[name]})
                line.setVisible(False)
                line.sigPositionChanged.connect(lambda l, n=name: self._cursor_moved(n, l))
                chart.plot.addItem(line)
                lines.append(line)
            self._cursors[name] = lines

        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(lambda _checked=False: self.stop())
        self.clear_btn.clicked.connect(self.clear)
        self.trigger_btn.clicked.connect(self.edit_trigger)
        self.fit_btn.clicked.connect(self.fit)
        self.cursors_btn.toggled.connect(self.set_cursors)
        self.save_btn.clicked.connect(self.save_dialog)
        self.save_image_btn.clicked.connect(self.save_image_dialog)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        session.disconnected.connect(self._on_disconnected)
        drive_tab.layout_changed.connect(self._refresh_signals)
        self._refresh_signals()
        self._set_buttons()
        self._refresh()

    # ---- what can be recorded -----------------------------------------------------------------
    def _refresh_signals(self):
        keys = self.drive.signal_keys()
        changed = self._keys_seen is not None and keys != self._keys_seen
        self._keys_seen = keys
        if changed and self._recording:              # a recording has one set of signals from start to end
            self.stop(" (the drive's telemetry layout changed)")
        info = self._info()
        options = [(key, f"{info[key][0]} ({info[key][1]})" if info[key][1] else info[key][0]) for key in self._all_keys()]
        speed = next((k for k in keys if self.drive.is_speed(k)), None)
        second = "ia" if "ia" in keys else "vbus" if "vbus" in keys else None
        for chart, defaults in zip(self.charts, ([speed], [second])):
            chart.set_signals(options, [d for d in defaults if d])
        self._refresh()

    def _all_keys(self):
        """The signals the drive broadcasts now, then those of the recording that it does not broadcast any more."""
        keys = self.drive.signal_keys()
        return keys + [k for k in self._rec_keys if k not in keys]

    def _info(self):
        """key -> (title, unit, factor), as the signals are shown now (the recording's own naming for a signal that is gone)."""
        return {**self._rec_info, **{key: self.drive.signal_display(key) for key in self.drive.signal_keys()}}

    def _colour_of(self, key):
        keys = self._all_keys()
        return COLOURS[keys.index(key) % len(COLOURS)] if key in keys else theme.GOLD

    # ---- recording ------------------------------------------------------------------------------
    @property
    def recording(self):
        return self._recording

    def edit_trigger(self):
        options = [(key, self._info()[key][0]) for key in self._all_keys()]
        dlg = TriggerDialog(options, self.trigger, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.trigger = dlg.result_trigger()
        self.message.emit(f"Trigger set: {self._trigger_text()}." if self.trigger else "Trigger cleared.", "ok")

    def _trigger_text(self):
        t = self.trigger
        title, unit, _factor = self._info().get(t.source, (t.source, "", 1.0))
        return f"{title} {t.slope} through {t.level:g}{' ' + unit if unit else ''}"     # the unit the level is in

    def start(self):
        if self.s.client is None:
            self.message.emit("Connect to a drive first: the recorder records what the drive broadcasts.", "warn")
            return
        self.clear()
        self._rec_keys = self.drive.signal_keys()
        self._rec_info = self._info()
        self._rec_client = self.s.client
        if self.trigger is not None:
            self._armed = True
            self._arm_last = self._trigger_value()
            self._timer.start(REFRESH_MS)
            self._set_buttons()
            self.state_changed.emit(True)
            self._refresh()
            return
        self._t0 = time.monotonic()
        self._recording = True
        self._timer.start(REFRESH_MS)
        self._set_buttons()
        self._refresh()
        self.state_changed.emit(True)
        self.message.emit("Recording…", "info")

    def _trigger_value(self):
        """The trigger source's current value in the unit the chart, cursors and CSV all show it in (rpm, not the
        raw rps the drive sends) - the level is typed in that unit. Comparing the raw value used to make a Speed
        level of 100, with the motor visibly at 200 rpm on the chart, never fire (200 rpm is 3.3 rps)."""
        if not self._rec_client:
            return None
        value = self._rec_client.telemetry_snapshot().get(self.trigger.source)
        if value is None:
            return None
        return value * self._info().get(self.trigger.source, (None, None, 1.0))[2]

    def _check_trigger(self):
        """Called every tick while armed instead of the normal chart refresh - nothing has been recorded yet, so
        there is nothing to plot. Compares the trigger source's own last-seen value against its current one to
        detect a crossing (never requested on its own - telemetry_snapshot() is whatever already arrived)."""
        value = self._trigger_value()
        if value is not None and self._arm_last is not None:
            crossed = (self._arm_last < self.trigger.level <= value) if self.trigger.slope == "rising" \
                else (self._arm_last > self.trigger.level >= value)
            if crossed:
                self._armed = False
                self._t0 = time.monotonic()
                self._recording = True
                self._set_buttons()
                self.message.emit(f"Triggered: {self._trigger_text()}.", "ok")
                return
        if value is not None:
            self._arm_last = value
        self._say(f"ARMED   ·   waiting for {self._trigger_text()}…", "warn")

    def stop(self, reason=""):
        if self._armed:
            self._armed = False
            self._timer.stop()
            self._set_buttons()
            self.state_changed.emit(False)
            self._say("READY", "")
            self.message.emit("Trigger cancelled - it never fired.", "warn")
            return
        if not self._recording:
            return
        self._t1 = time.monotonic()
        self._frozen = self._capture(self._t1)
        self._recording = False
        self._timer.stop()
        self._set_buttons()
        self._refresh()
        self.state_changed.emit(False)
        n = max((len(t) for t, _ in self._frozen.values()), default=0)
        self.message.emit(f"Recording stopped{reason}: {n} samples in {self._t1 - self._t0:.1f} s.", "ok")

    def clear(self):
        self.stop()
        self._t0 = self._t1 = None
        self._frozen = {}
        self._rec_keys, self._rec_info, self._rec_client = [], {}, None
        self._set_buttons()
        self._refresh_signals()

    def _on_disconnected(self):
        self.stop(" (the drive was disconnected)")

    def _capture(self, end=None):
        """{key: (seconds since the start, raw values)} of everything the drive broadcast since the recording began."""
        client = self._rec_client
        out = {}
        if client is None or self._t0 is None:
            return out
        for key in self._rec_keys:
            t, v = client.history_snapshot(key)
            mask = t >= self._t0
            if end is not None:
                mask &= t <= end
            if mask.any():
                out[key] = (t[mask] - self._t0, v[mask])
        return out

    def data(self):
        return self._capture() if self._recording else self._frozen

    def elapsed(self):
        if self._t0 is None:
            return 0.0
        return (time.monotonic() if self._recording else self._t1) - self._t0

    # ---- showing ------------------------------------------------------------------------------
    def _set_buttons(self):
        busy = self._recording or self._armed
        self.start_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self.clear_btn.setEnabled(bool(busy or self._frozen))
        self.save_btn.setEnabled(bool(not busy and self._frozen))
        self.save_image_btn.setEnabled(bool(not busy and self._frozen))
        self.duration.setEnabled(not busy)
        self.trigger_btn.setEnabled(not busy)
        self.buttons_changed.emit()

    def _refresh(self):
        if self._armed:
            self._check_trigger()
            return
        elapsed = self.elapsed()
        limit = self.duration.currentData()
        if self._recording and limit and elapsed >= limit:
            self.stop(" (time is up)")
            return
        data = self.data()
        info = self._info()
        for chart in self.charts:
            chart.show_data(data, info, self._colour_of)
            if self._recording:
                chart.plot.setXRange(0, max(elapsed, MIN_WINDOW_S), padding=0)
        samples = max((len(t) for t, _ in data.values()), default=0)
        if self._recording:
            self._say(f"●  RECORDING   {elapsed:.1f} s   ·   {samples} samples", "ok")
        elif self._frozen:
            self._say(f"STOPPED   {elapsed:.1f} s   ·   {samples} samples", "")
        else:
            self._say("READY", "")
        self.hint.setVisible(not data and not self._recording)
        self._update_table(data, info)

    def _say(self, text, state):
        self.status.setText(text)
        set_prop(self.status, "state", state)

    def fit(self):
        for chart in self.charts:
            chart.plot.getPlotItem().autoRange()
            chart.plot.setXRange(0, max(self._span(), 1.0), padding=0.02)

    def _span(self):
        data = self.data()
        return max([t[-1] for t, _ in data.values() if len(t)] + [self.elapsed()], default=0.0)

    # ---- cursors ------------------------------------------------------------------------------
    def set_cursors(self, on):
        self.cursors_btn.blockSignals(True)
        self.cursors_btn.setChecked(on)
        self.cursors_btn.blockSignals(False)
        span = max(self._span(), 1.0)
        for name, position in (("A", 0.25 * span), ("B", 0.75 * span)):
            for line in self._cursors[name]:
                line.setVisible(on)
                if on:
                    self._syncing = True
                    line.setValue(position)
                    self._syncing = False
        self.table.setVisible(on)
        self._update_table()

    def cursor_positions(self):
        return {name: lines[0].value() for name, lines in self._cursors.items()}

    def set_cursor(self, name, seconds):
        for line in self._cursors[name]:
            line.setValue(seconds)

    def _cursor_moved(self, name, line):
        if self._syncing:
            return
        self._syncing = True
        for other in self._cursors[name]:
            if other is not line:
                other.setValue(line.value())
        self._syncing = False
        self._update_table()

    def _update_table(self, data=None, info=None):
        if not self.table.isVisible() and not self.cursors_btn.isChecked():
            return
        data = self.data() if data is None else data
        info = self._info() if info is None else info
        keys = []
        for chart in self.charts:
            keys += [k for k in chart.checked_keys() if k not in keys]
        a, b = (self._cursors[n][0].value() for n in ("A", "B"))
        self.table.setRowCount(len(keys))
        fit_table_height(self.table, rows=len(keys))
        for row, key in enumerate(keys):
            title, unit, factor = info[key]
            t, v = data.get(key, (np.array([]), np.array([])))
            va, vb = value_at(t, v, a), value_at(t, v, b)
            va, vb = (None if x is None else x * factor for x in (va, vb))
            cells = [f"{title} ({unit})" if unit else title, fmt(va), fmt(vb), fmt(None if va is None or vb is None else vb - va)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
        self.table.setHorizontalHeaderLabels(["SIGNAL", f"A  ({a:.2f} s)", f"B  ({b:.2f} s)", f"B − A  ({b - a:.2f} s)"])

    # ---- export -------------------------------------------------------------------------------
    def export_csv(self, path):
        """Write the recording as CSV: one row whenever any signal was received, a signal keeps its last value in between
        (empty before its first). Speeds are in the unit shown on the Single Axis Motion page. Returns the number of rows."""
        data = self.data()
        info = self._info()
        keys = [k for k in self._rec_keys if k in data]
        if not keys:
            raise ValueError("nothing has been recorded")
        times = np.unique(np.concatenate([data[k][0] for k in keys]))
        columns = []
        for key in keys:
            t, v = data[key]
            index = np.searchsorted(t, times, side="right") - 1
            factor = info[key][2]
            columns.append([("" if i < 0 else f"{v[i] * factor:.9g}") for i in index])
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["time [s]"] + [f"{info[k][0]} [{info[k][1]}]" if info[k][1] else info[k][0] for k in keys])
            for i, t in enumerate(times):
                writer.writerow([f"{t:.6f}"] + [column[i] for column in columns])
        return len(times)

    def save_dialog(self):
        if not self._frozen:
            self.message.emit("Nothing to save yet: record something first.", "warn")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save recording", f"recording_{datetime.now():%Y%m%d_%H%M%S}.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            rows = self.export_csv(path)
        except (ValueError, OSError) as exc:
            self.message.emit(f"Could not save the recording: {exc}", "error")
            return
        self.message.emit(f"Saved {rows} rows to {path}", "ok")

    def save_image_dialog(self):
        """Both charts, stacked exactly as shown, as one PNG - same `widget.grab()` approach `plotter_2d.py`'s own
        Save Image already uses, just composing the two charts into one image since this page has two."""
        if not self._frozen:
            self.message.emit("Nothing to save yet: record something first.", "warn")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Recorder Image",
                                              f"recording_{datetime.now():%Y%m%d_%H%M%S}.png", "PNG images (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        combined = stack_pixmaps([chart.grab() for chart in self.charts])
        if not combined.save(path):
            self.message.emit(f"Could not save the image to {path}", "error")
            return
        self.message.emit(f"Saved image to {path}", "ok")
