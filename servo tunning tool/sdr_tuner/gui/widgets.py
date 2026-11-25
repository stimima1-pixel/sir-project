"""Small reusable widgets."""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from .theme import BG
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QSlider, QSpinBox, QVBoxLayout,
                               QWidget)

from . import theme
from .theme import mono_font


def fold_on_single_click(tree):
    """Fold/unfold a tree's parent items with ONE click on the item itself, not only on its arrow or with a
    double-click - the same one-click behaviour Item Configuration's own group headers have (user, 2026-09-25:
    "make sure every where fold and unfold should work with single mouse click"). A click on the branch arrow does
    not emit itemClicked, so the arrow keeps working without toggling twice; a leaf item is left alone."""
    tree.setExpandsOnDoubleClick(False)
    tree.itemClicked.connect(lambda item, _column=0: item.setExpanded(not item.isExpanded()) if item.childCount() else None)


def set_prop(widget, name, value):
    """Set a dynamic property and re-apply the style sheet (only when it changed)."""
    value = str(value).lower() if isinstance(value, bool) else value
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def make_button(button, role=None, size=None):
    if role:
        button.setProperty("role", role)
    if size:
        button.setProperty("size", size)
    return button


MIN_PX = 10
_METRICS = {}           # px -> QFontMetrics of the monospace font: Readout._fit_text tries up to 16 sizes per new text


def _mono_metrics(px):
    metrics = _METRICS.get(px)
    if metrics is None:
        metrics = _METRICS[px] = QFontMetrics(mono_font(px))
    return metrics
CARD_MARGINS = 32       # left + right margin inside a card


class Readout(QFrame):
    """A titled big-number display. With fit=True the text is shrunk (down to MIN_PX) until it fits in `lines` line(s)
    of the card, so a long mode name never makes the card - and the row of cards - taller."""

    def __init__(self, title, unit="", size=26, fit=False, lines=1):
        super().__init__()
        self.setObjectName("readout")
        self._max_px, self._px, self._fit, self._lines, self._fitted = size, None, fit, lines, None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(CARD_MARGINS // 2, 11, CARD_MARGINS // 2, 12)
        layout.setSpacing(3)
        self.title = QLabel(title.upper())
        self.title.setObjectName("readoutTitle")
        self.value = QLabel("—")
        self.value.setObjectName("readoutValue")
        self.value.setWordWrap(lines > 1)
        self._apply_px(size)
        if lines > 1:                                           # hold the row height steady whatever the text
            self.value.setMinimumHeight(lines * QFontMetrics(mono_font(MIN_PX + 2)).lineSpacing())
        self.unit = QLabel(unit)
        self.unit.setObjectName("readoutUnit")
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.value, 1 if lines > 1 else 0)        # a wrapping label needs the whole card width
        row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        if lines == 1:
            row.addStretch(1)
        layout.addWidget(self.title)
        layout.addLayout(row)

    @property
    def font_px(self):
        return self._px

    def _apply_px(self, px):
        if px != self._px:                                      # the style sheet's own font-size would win over setFont()
            self._px = px
            self.value.setFont(mono_font(px))                   # the monospace family ...
            self.value.setStyleSheet(f"font-size: {px}px;")     # ... and the size, which the application style sheet would override

    def _fit_text(self, text):
        width = max(0.96 * (self.width() - CARD_MARGINS - self.unit.sizeHint().width() - 6), 40)   # bold text is a bit wider
        wrap = Qt.TextFlag.TextWordWrap if self._lines > 1 else 0
        for px in range(self._max_px, MIN_PX - 1, -1):
            metrics = _mono_metrics(px)
            box = metrics.boundingRect(0, 0, int(width), 100000, wrap, text)
            if box.height() <= self._lines * metrics.lineSpacing() + 1 and (self._lines > 1 or box.width() <= width):
                break
        self._apply_px(px)

    def set(self, text, state=""):
        if self._fit and self._fitted != (text, self.width()):
            self._fitted = (text, self.width())
            self._fit_text(text)
        self.value.setText(text)
        set_prop(self.value, "state", state)

    def clear(self):
        self.set("—")


class Led(QLabel):
    def __init__(self):
        super().__init__()
        self.setObjectName("led")

    def set_on(self, on):
        set_prop(self, "on", bool(on))


class Gauge(QWidget):
    """A simple horizontal fill bar for a value inside [minimum, maximum] - Status Monitor's own optional per-row
    visual (EAS3's own "Gauge" column). Only meaningful for a register whose own min AND max are both known from
    the Device Reference; one with neither (most enums/bools) has nothing to scale a fill against, so callers
    never build one for those rows rather than this widget guessing a range."""

    def __init__(self, minimum, maximum):
        super().__init__()
        self.setObjectName("gauge")
        self.setFixedHeight(14)
        self.setMinimumWidth(80)
        self._min, self._max, self._value = float(minimum), float(maximum), None

    def set_range(self, minimum, maximum):
        self._min, self._max = float(minimum), float(maximum)
        self.update()

    def set_value(self, value):
        self._value = None if value is None else float(value)
        self.update()

    @property
    def value(self):
        return self._value

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(theme.LINE), 1))
        painter.setBrush(QColor(theme.INPUT))
        painter.drawRoundedRect(rect, 3, 3)
        if self._value is not None and self._max > self._min:
            frac = max(0.0, min(1.0, (self._value - self._min) / (self._max - self._min)))
            fill = rect.adjusted(1, 1, -1, -1)
            fill.setWidth(max(0, int(fill.width() * frac)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.GOLD))
            painter.drawRoundedRect(fill, 2, 2)
        painter.end()


class NoWheel:
    """Mixed into a spin box / combo box / slider so scrolling the mouse over it - almost every one of these sits in a
    scrollable form - scrolls the page underneath instead of silently nudging or cycling its value (a user reported
    this live: scrolling a long page changed a MOVE TO target from 0 to -3 counts, just by passing the mouse over it).
    The value can still be typed, or changed with the arrows / steppers / a click; only the wheel is given up, and an
    ignored event is passed to the parent, which is exactly what should scroll instead."""

    def wheelEvent(self, event):
        event.ignore()


class SpinBox(NoWheel, QSpinBox):
    pass


class DoubleSpinBox(NoWheel, QDoubleSpinBox):
    pass


class ComboBox(NoWheel, QComboBox):
    pass


class Slider(NoWheel, QSlider):
    pass


def stack_pixmaps(pixmaps):
    """One pixmap with `pixmaps` stacked top to bottom, exactly as grabbed. On a HiDPI screen a grab carries a
    devicePixelRatio (2 device pixels per logical one): painting it at its device-pixel height leaves a blank strip
    under each one - seen 2026-09-25 in a real Bode PNG, the two plots with a plot-sized gap between them. The result
    keeps that ratio and stacks in logical pixels; its own width/height stay in device pixels, as the PNG is saved."""
    ratio = max(p.devicePixelRatio() for p in pixmaps)
    image = QPixmap(max(p.width() for p in pixmaps), sum(p.height() for p in pixmaps))
    image.setDevicePixelRatio(ratio)
    image.fill(QColor(BG))                                   # not transparent: viewers show that as white beside a narrower plot
    painter = QPainter(image)
    y = 0.0
    for p in pixmaps:
        painter.drawPixmap(QPointF(0.0, y), p)
        y += p.height() / p.devicePixelRatio()
    painter.end()
    return image


def fit_table_height(table, rows=None):
    """A read-out table shows every row with no scroll bar: its height is the header's own height plus the rows plus
    the frame, MEASURED - the old "28 + 24 n + 4" guess left both cursor tables (Bode, Recorder) 7 px short on the real
    screen, where the theme's header is 37 px, so their last row hid behind a scroll bar and the right-hand column was
    clipped (user, 2026-09-25, the Bode cursor table's Slope row: "some data ... is missing now")."""
    rows = max(table.rowCount(), 1) if rows is None else max(rows, 1)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setFixedHeight(table.horizontalHeader().sizeHint().height() + rows * table.verticalHeader().defaultSectionSize()
                         + 2 * table.frameWidth())
