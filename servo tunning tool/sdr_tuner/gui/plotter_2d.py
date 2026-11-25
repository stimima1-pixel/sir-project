"""Drive Setup and Motion > 2D Plotter: a LIVE trace of two connected axes' own Work Zone Count plotted against
each other, instead of each against time - the vendor's own "2D Plotter" feature (a separate thing from its
"Oscilloscope" tab, which is the same value-vs-time graphing this tool's own Recorder page already does). This is
what actually shows a genuine 2-axis interpolated move (a G02/G03 circle, say) as the physical shape it traces,
which two independent time-series charts cannot.

Workspace-level, like Terminal/Motion Controller/Axes - not per-axis, not a mode bolted onto the existing
`RecorderPage` (`recorder.py`) - it inherently needs to see two DIFFERENT connected axes at once, the same
reasoning that already keeps those three tools single and shared rather than duplicated per axis. Closest sibling
to build from is `axes_page.py`'s `AxesStatusPage`: same shape (`__init__(self, session, primary_name)`, a polling
`QTimer`, wired to `Session.connected`/`disconnected`/`axis_connected`/`axis_disconnected`, reading each client's
own `telemetry_snapshot()` - never requested, this project's own standing rule) and the same `drive_label()`
naming helper (`axes_page.py`) already used there and matching the navigator's own.

No Start/Stop/recording-session concept, unlike the Oscilloscope or this tool's own Recorder - just an always-on
trail, growing a new point whenever BOTH selected axes have a Work Zone Count in telemetry and it differs from the
last one plotted, reset only by Clear All. If a selected axis is not connected (or has not broadcast a Work Zone
Count yet), the trace simply stops growing - stays frozen exactly as far as it got - rather than clearing or
guessing, the same way `RecorderPage._on_disconnected` already freezes its own chart instead of clearing it."""
import csv
from datetime import datetime

import pyqtgraph as pg
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme
from .axes_page import drive_label
from .widgets import ComboBox, set_prop

REFRESH_MS = 100     # matches RecorderPage.REFRESH_MS - the same telemetry-polling cadence


PICK_PLACEHOLDER = "— pick an axis —"
ONE_AXIS_TEXT = ("Only one axis is connected. The 2D Plotter traces one axis's position against another's, so it needs "
                 "two - add a second drive under Motion → Axes → Additional Axes. To watch a single axis, use Motion - "
                 "Single Axis's live plot or the Recorder.")


class Plotter2DPage(QWidget):
    message = Signal(str, str)      # (text, level) for the window's status bar - Save Image/Save Data outcomes

    def __init__(self, session, primary_name):
        super().__init__()
        self.s = session
        self._primary_name = primary_name    # callable () -> str, e.g. main_window.py's _target_name
        self._trace = []            # [(x, y), ...] Work Zone Count pairs, oldest first
        self._x_node = self._y_node = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.clear_btn = QPushButton("Clear All")
        self.save_image_btn = QPushButton("Save Image…")
        self.save_data_btn = QPushButton("Save Data…")
        self.x_combo = ComboBox()
        self.y_combo = ComboBox()
        self.status = QLabel("")
        self.status.setWordWrap(True)                      # a long note (one axis connected) must not be cut off
        self.status.setObjectName("ctlStatus")
        bar.addWidget(self.clear_btn)
        bar.addWidget(self.save_image_btn)
        bar.addWidget(self.save_data_btn)
        bar.addSpacing(10)
        bar.addWidget(QLabel("X Axis"))
        bar.addWidget(self.x_combo)
        bar.addWidget(QLabel("Y Axis"))
        bar.addWidget(self.y_combo)
        bar.addStretch(1)
        outer.addLayout(bar)
        outer.addWidget(self.status)                       # its own full-width line under the toolbar: the one-axis note
                                                           # is a sentence or two, not a word

        self.hint = QLabel("Traces two connected axes' own Work Zone Count against each other, live, as telemetry "
                           "arrives - e.g. a G02/G03 move across both draws the actual shape it interpolates. "
                           "Nothing is requested from the drive; pick two connected axes above to begin.")
        self.hint.setProperty("muted", "true")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        self.plot = pg.PlotWidget()
        theme.style_plot(self.plot, " ")
        item = self.plot.getPlotItem()
        item.setMouseEnabled(x=True, y=True)      # a trace is looked at: zoom with the wheel, pan by dragging
        item.setAspectLocked(True)                 # equal X/Y scale - a circle should actually look like one
        self.curve = self.plot.plot(pen=pg.mkPen(theme.GOLD, width=1.5), symbol="o", symbolSize=6,
                                    symbolBrush=pg.mkBrush(theme.GOLD), symbolPen=pg.mkPen(theme.GOLD_HI))
        outer.addWidget(self.plot, 1)

        self.clear_btn.clicked.connect(self.clear_all)
        self.save_image_btn.clicked.connect(self.save_image_dialog)
        self.save_data_btn.clicked.connect(self.save_data_dialog)
        self.x_combo.currentIndexChanged.connect(self._on_axis_picked)
        self.y_combo.currentIndexChanged.connect(self._on_axis_picked)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)
        session.connected.connect(self._refresh_axis_options)
        session.disconnected.connect(self._refresh_axis_options)
        session.axis_connected.connect(lambda _node: self._refresh_axis_options())
        session.axis_disconnected.connect(lambda _node: self._refresh_axis_options())
        session.node_detected.connect(self._on_node_detected)
        self._refresh_axis_options()
        self._say("Pick two connected axes to trace.", "")

    # ---- axis selection -----------------------------------------------------------------------
    def _on_node_detected(self, node):
        """The primary drive answered on a different node than the one selected - found by listening AFTER
        `connected` already fired (always so on the USB cable), which re-keys all_clients(). A combo still on the
        node the drive was supposed to be on follows it to the node it really is on (same drive, same trace); it
        used to stay on the stale node forever, tracing nothing, since nothing here listened for this."""
        connected = self.s.all_clients()
        for attr in ("_x_node", "_y_node"):
            if getattr(self, attr) is not None and getattr(self, attr) not in connected:
                setattr(self, attr, node)
        self._refresh_axis_options()

    def _refresh_axis_options(self):
        """Rebuilds the X/Y combos from whichever axes are actually connected right now - `drive_label()`'s own
        name for each (the primary's own, or `"DriveNN (SDR)"`), the same naming the navigator and the Axes status
        page already use. A combo's own
        CURRENT selection is kept even if that axis has just disconnected (its own label is simply carried over,
        not re-derived), so the choice - and the trace already drawn from it - is never silently reset out from
        under the user; only picking something else changes it. With nothing chosen yet (first run), defaults to
        the two lowest-numbered connected axes, if there are at least two."""
        connected = {node: drive_label(node, self.s.node_id, self._primary_name())
                    for node in self.s.all_clients()}
        nodes = sorted(connected)
        defaults = {"_x_node": nodes[0] if nodes else None, "_y_node": nodes[1] if len(nodes) > 1 else None}
        for combo, attr in ((self.x_combo, "_x_node"), (self.y_combo, "_y_node")):
            current = getattr(self, attr)
            if current is None:
                current = defaults[attr]
                setattr(self, attr, current)
            options = dict(connected)
            if current is not None and current not in options:
                options[current] = combo.currentText() or f"Drive{current:02d} (SDR)"
            combo.blockSignals(True)
            combo.clear()
            if current is None:                                 # nothing picked: say so, instead of showing the first
                combo.addItem(PICK_PLACEHOLDER, None)           # axis as if it were (a one-axis setup showed Y =
            for node in sorted(options):                        # Drive01 while tracing nothing - user, 2026-09-26)
                combo.addItem(options[node], node)
            if current is not None:
                index = combo.findData(current)
                if index >= 0:
                    combo.setCurrentIndex(index)
            else:
                combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._update_axis_labels()

    def _on_axis_picked(self, _index=None):
        self._x_node = self.x_combo.currentData()
        self._y_node = self.y_combo.currentData()
        self._update_axis_labels()

    def _update_axis_labels(self):
        item = self.plot.getPlotItem()
        x_name = self.x_combo.currentText() if self.x_combo.currentData() is not None else "X axis"
        y_name = self.y_combo.currentText() if self.y_combo.currentData() is not None else "Y axis"
        item.setLabel("bottom", f"{x_name} - Work Zone Count", units="counts",
                     **{"color": theme.MUTED, "font-size": "9pt"})
        item.setLabel("left", f"{y_name} - Work Zone Count", units="counts",
                     **{"color": theme.MUTED, "font-size": "9pt"})

    # ---- tracing --------------------------------------------------------------------------------
    def _tick(self):
        if self._x_node is None or self._y_node is None:
            if len(self.s.all_clients()) < 2:
                self._say(ONE_AXIS_TEXT, "warn")
            else:
                self._say("Pick two connected axes to trace.", "warn")
            return
        clients = self.s.all_clients()
        x_client, y_client = clients.get(self._x_node), clients.get(self._y_node)
        if x_client is None or y_client is None:
            stale = self.x_combo.currentText() if x_client is None else self.y_combo.currentText()
            self._say(f"{stale} is not connected - the trace is frozen where it left off.", "warn")
            return
        x_snap, y_snap = x_client.telemetry_snapshot(), y_client.telemetry_snapshot()
        if "wz_count" not in x_snap or "wz_count" not in y_snap:
            self._say("Waiting for both axes' own Work Zone Count in telemetry…", "warn")
            return
        point = (x_snap["wz_count"], y_snap["wz_count"])
        if not self._trace or self._trace[-1] != point:
            self._trace.append(point)
            self._redraw()
        self._say(f"{len(self._trace)} point(s) traced.", "ok")

    def _redraw(self):
        if self._trace:
            xs, ys = zip(*self._trace)
        else:
            xs, ys = [], []
        self.curve.setData(xs, ys)
        self.hint.setVisible(not self._trace)

    def clear_all(self):
        self._trace = []
        self._redraw()
        self._say("Cleared.", "")

    def _say(self, text, state):
        self.status.setText(text)
        set_prop(self.status, "state", state)

    # ---- export -------------------------------------------------------------------------------
    def save_image_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save 2D Plotter Image",
                                              f"plot_{datetime.now():%Y%m%d_%H%M%S}.png", "PNG images (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not self.plot.grab().save(path):
            self.message.emit(f"Could not save the image to {path}", "error")
            return
        self.message.emit(f"Saved image to {path}", "ok")

    def save_data_dialog(self):
        if not self._trace:
            self.message.emit("Nothing to save yet: trace something first.", "warn")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save 2D Plotter Data",
                                              f"plot_{datetime.now():%Y%m%d_%H%M%S}.csv", "CSV files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow([f"{self.x_combo.currentText()} [counts]", f"{self.y_combo.currentText()} [counts]"])
                for x, y in self._trace:
                    writer.writerow([f"{x:.9g}", f"{y:.9g}"])
        except OSError as exc:
            self.message.emit(f"Could not save the data: {exc}", "error")
            return
        self.message.emit(f"Saved {len(self._trace)} point(s) to {path}", "ok")
