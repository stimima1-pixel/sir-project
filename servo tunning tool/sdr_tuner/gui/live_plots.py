"""The LIVE TELEMETRY card: four stacked, time-linked plots (speed, position, current, DC bus) of what the drive
broadcasts, with the window length and a Freeze box - one on Motion - Single Axis (which also carries the Speed-in
chooser, since that one writes the drive's telemetry mapping) and one on Drive Modes (user, 2026-09-26: "show this
live telemetry in that new page as well - just make sure which page is opened telemetry will show in that page only
so software will not get much load"). A card only redraws while its own page is the one on screen (`update()` is
called from each page's tick behind `isVisible()`); the curves come from the motion page's knowledge of the drive's
layout (`build_curves`), so the two cards always show the same signals in the same unit. Nothing here asks the
drive for anything: it draws the client's own history of the broadcast."""
import time

import pyqtgraph as pg
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout

from . import theme
from .widgets import ComboBox, set_prop

PLOTS = [("Speed", "rpm", 10), ("Position", "", 100), ("Current", "A", 0.25), ("DC bus", "V", 1.0)]   # label, units, min y-span
SPEED_COLOURS = (theme.GOLD, theme.GOLD_HI, theme.SAPPHIRE, theme.ROSE)     # one curve per speed the drive broadcasts
CURVES = {      # telemetry key -> (plot row, colour, legend name, line width, opacity 0..255); speeds are added from the mapping
    "wz_count": (1, theme.SAPPHIRE, "Position", 2, 255),
    "ia": (2, theme.ROSE, "Phase A", 2, 255),
    "ib": (2, theme.EMERALD, "Phase B", 2, 255),
    "vbus": (3, theme.AMETHYST, "DC bus", 2, 255),
}
SPEED_UNITS = (("rpm", "rpm"), ("rps", "rps"), ("degs", "deg/sec"), ("hz", "Hz"))   # (key, label) of the speed the card can show
UNIT_LABELS = dict(SPEED_UNITS)
RPS_UNITS = ("rps", "rpm", "degs")                                    # the units taken from the drive's Work Zone: Speed (rps)
UNIT_MIN_SPAN = {"rpm": 10, "rps": 0.2, "degs": 60, "hz": 0.5}        # smallest y-range of the speed plot
STALE_AFTER = 1.0       # seconds without telemetry before it is flagged
WINDOWS = (10, 30, 60, 120)
SEPARATOR_PX = 2        # the black rule between two stacked plots (user, 2026-09-26: "give black line so user know where each
                        # graph is separated from each other")


def speed_shown(kind, unit):
    """(unit key, factor) a speed that the drive sends as `kind` (see defs.speed_kind) is shown with when the user chose
    `unit`. Nothing is converted through the pole count: Hz stays Hz, rps stays rps, and rpm is rps times 60."""
    if kind == "rps":
        return {"rpm": ("rpm", 60.0), "degs": ("degs", 360.0)}.get(unit, ("rps", 1.0))
    if kind == "rpm":
        return ("degs", 6.0) if unit == "degs" else ("rpm", 1.0)
    return "hz", 1.0                                                    # an electrical frequency


class LivePlots(QFrame):
    def __init__(self, with_speed_unit=True, parent=None):
        super().__init__(parent)
        self.setObjectName("plotcard")
        self.window_s = WINDOWS[1]
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(4)
        top = QHBoxLayout()
        title = QLabel("LIVE TELEMETRY")
        title.setStyleSheet(f"color: {theme.GOLD}; font-size: 11px; font-weight: 800;")
        self.live_tag = QLabel("NO DATA")
        self.live_tag.setObjectName("liveTag")
        self.live_tag.setToolTip("The values are shown as the drive sends them (its own telemetry broadcast, about 9 messages\n"
                                 "per second in total). The tool does not ask the drive for telemetry.")
        self.window_box = ComboBox()
        for seconds in WINDOWS:
            self.window_box.addItem(f"{seconds} s", seconds)
        self.window_box.setCurrentIndex(1)
        self.freeze = QCheckBox("Freeze")
        self.speed_unit = self.position_unit = None
        self.position_factor = 1.0
        if with_speed_unit:
            self.position_unit = ComboBox()                  # counts or degrees: a pure display choice, mirrored by
            self.position_unit.addItem("counts", "counts")   # the motion page's own TARGET POSITION chooser
            self.position_unit.addItem("deg", "deg")
            self.position_unit.setToolTip("The unit the position is shown in: Work Zone counts, or degrees from the drive's\n"
                                          "own Work Zone: Dimension (counts per turn), read once when deg is chosen.")
            self.speed_unit = ComboBox()
            for key, label in SPEED_UNITS:
                self.speed_unit.addItem(label, key)
            self.speed_unit.setToolTip("The unit the speed is shown in. It follows what the drive sends in its telemetry.\n"
                                       "Choosing rps or rpm makes the drive send Work Zone: Speed (RPS); rpm is that value times 60.\n"
                                       "Choosing Hz makes the drive send Speed (Electrical Frequency). This writes the drive's\n"
                                       "telemetry mapping, after you confirm.")
        top.addWidget(title)
        top.addSpacing(14)
        top.addWidget(self.live_tag)
        top.addStretch(1)
        if with_speed_unit:
            top.addWidget(QLabel("Position in"))
            top.addWidget(self.position_unit)
            top.addSpacing(16)
            top.addWidget(QLabel("Speed in"))
            top.addWidget(self.speed_unit)
            top.addSpacing(16)
        top.addWidget(QLabel("Window"))
        top.addWidget(self.window_box)
        top.addSpacing(10)
        top.addWidget(self.freeze)
        v.addLayout(top)

        self.plot_widgets, self.curves, self.curve_rows, self._legends = [], {}, {}, {}
        self.separators = []                                    # one black rule above every plot but the first shown
        for row, (label, units, min_span) in enumerate(PLOTS):
            if row:
                rule = QFrame()
                rule.setObjectName("plotSeparator")
                rule.setFixedHeight(SEPARATOR_PX)
                rule.setStyleSheet("QFrame#plotSeparator { background: #000000; border: 0; }")
                v.addWidget(rule)
                self.separators.append(rule)
            pw = pg.PlotWidget()
            pw.setMinimumHeight(64)
            theme.style_plot(pw, label, units, min_span)
            if row:
                pw.setXLink(self.plot_widgets[0])
            pw.getPlotItem().setLabel("bottom", "seconds", **{"color": theme.MUTED, "font-size": "10pt"})
            if row in (0, 2):                                       # speed (only shown when there are several) and current
                legend = pw.addLegend(offset=(-8, 4), labelTextColor=theme.MUTED)
                legend.setBrush(pg.mkBrush(theme.PANEL))
                self._legends[row] = legend
            v.addWidget(pw, 1)
            self.plot_widgets.append(pw)
        self.window_box.currentIndexChanged.connect(self._on_window_changed)
        self._on_window_changed()

    def _on_window_changed(self):
        self.window_s = self.window_box.currentData()
        for pw in self.plot_widgets:
            pw.setXRange(-self.window_s, 0, padding=0)

    def build_curves(self, card_keys, card_info, title_of):
        """One curve per plotted value that the drive broadcasts (a speed in whatever unit it comes); a plot with no
        curve is hidden. `card_info[key].speed` says which values are speeds; `title_of(key)` names a speed curve."""
        for key, curve in self.curves.items():
            self.plot_widgets[self.curve_rows[key]].removeItem(curve)
        self.curves, self.curve_rows = {}, {}
        specs, speeds = {}, 0
        for key in card_keys:
            if card_info[key].speed:
                specs[key] = (0, SPEED_COLOURS[speeds % len(SPEED_COLOURS)], title_of(key), 2, 255)
                speeds += 1
            elif key in CURVES:
                specs[key] = CURVES[key]
        for key, (row, color, name, width, opacity) in specs.items():
            kw = {"fillLevel": 0, "brush": pg.mkBrush(212, 175, 55, 34)} if row == 0 and not any(r == 0 for r in self.curve_rows.values()) else {}
            colour = QColor(color)
            colour.setAlpha(opacity)
            self.curves[key] = self.plot_widgets[row].plot(pen=pg.mkPen(colour, width=width), name=name, **kw)
            self.curves[key].setDownsampling(auto=True, method="peak")   # at 1000-2000 samples/s a curve holds up to
            self.curves[key].opts["autoDownsampleFactor"] = 1.0          # 20 000 points: drawn as each pixel column's
                                                                          # min and max (pyqtgraph's default keeps ~5
                                                                          # samples per pixel - 8000 of the 20 000)
            self.curve_rows[key] = row
        used = sorted(set(self.curve_rows.values()))
        for row, pw in enumerate(self.plot_widgets):
            pw.setVisible(row in used)
            if row:                                             # the rule above this plot: only between two shown plots
                self.separators[row - 1].setVisible(row in used and any(r < row for r in used))
            axis = pw.getPlotItem().getAxis("bottom")               # the time axis is drawn under the last plot only
            axis.setStyle(showValues=bool(used) and row == used[-1])
            axis.showLabel(bool(used) and row == used[-1])
            pw.setMinimumHeight(104 if used and row == used[-1] else 64)      # the last plot also holds the time axis
        self._legends[0].setVisible(speeds > 1)                     # name the curves only when there is more than one speed

    def set_speed_unit(self, unit):
        """The speed plot's own axis: label and smallest y-range for the unit the speeds are drawn in."""
        pw = self.plot_widgets[0].getPlotItem()
        pw.setLabel("left", "Speed", units=UNIT_LABELS[unit], **{"color": theme.MUTED, "font-size": "9pt"})
        pw.vb.setLimits(minYRange=UNIT_MIN_SPAN[unit])

    def set_position_unit(self, label, factor):
        """The position plot's own axis and the factor its counts are drawn with (1, or 360 / counts per turn)."""
        self.position_factor = factor
        pw = self.plot_widgets[1].getPlotItem()
        pw.setLabel("left", "Position", units=label, **{"color": theme.MUTED, "font-size": "9pt"})
        pw.vb.setLimits(minYRange=PLOTS[1][2] if label == "counts" else 1.0)
        if self.position_unit is not None and self.position_unit.currentData() != label:
            self.position_unit.setCurrentIndex(self.position_unit.findData(label))

    def set_live(self, age):
        """The tag beside the title, from the age of the last telemetry frame (None: nothing has arrived)."""
        if age is None:
            self.live_tag.setText("WAITING FOR TELEMETRY")
            set_prop(self.live_tag, "state", "warn")
            return
        live = age < STALE_AFTER
        self.live_tag.setText("● LIVE" if live else f"STALE  {age:.0f} s")
        set_prop(self.live_tag, "state", "ok" if live else "warn")

    def update(self, client, card_info, speed_unit):
        """Redraw every curve from the client's history - the caller does this only while this card is on screen."""
        if self.freeze.isChecked():
            return
        now = time.monotonic()
        for key, curve in self.curves.items():
            t, v = client.history_snapshot(key, self.window_s + 1)
            kind = card_info[key].speed
            if kind:                                            # a speed is drawn in the chosen unit: rps times 60 for rpm
                v = v * speed_shown(kind, speed_unit)[1]
            elif key == "wz_count" and self.position_factor != 1.0:   # the position in degrees, when chosen
                v = v * self.position_factor
            curve.setData(t - now, v)

    def clear(self):
        for curve in self.curves.values():
            curve.setData([], [])
        self.live_tag.setText("NO DATA")
        set_prop(self.live_tag, "state", "")
