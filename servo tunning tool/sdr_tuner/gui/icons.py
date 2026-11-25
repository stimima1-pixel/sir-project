"""Line icons drawn in code (gold strokes on a transparent background), so the tool needs no image files.

icon("plug") -> QIcon with a normal and a dimmed (disabled) picture. Every glyph is drawn on a 32 x 32 grid."""
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF

from . import theme

_ROUND = (Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _pen(p, colour, width=2.2):
    p.setPen(QPen(colour, width, *_ROUND))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _fill(p, colour):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)


def _poly(p, *points, close=False):
    poly = QPolygonF([QPointF(x, y) for x, y in points])
    p.drawPolygon(poly) if close else p.drawPolyline(poly)


def _plug(p, c, a):
    _pen(p, c)
    p.drawRoundedRect(QRectF(9, 12, 14, 10), 3, 3)
    p.drawLine(QPointF(13, 12), QPointF(13, 5))
    p.drawLine(QPointF(19, 12), QPointF(19, 5))
    p.drawLine(QPointF(16, 22), QPointF(16, 28))


def _unplug(p, c, a):
    p.save()
    p.translate(-3, -3)
    _plug(p, c, a)
    p.restore()
    _pen(p, a, 2.8)
    p.drawLine(QPointF(19, 19), QPointF(28, 28))
    p.drawLine(QPointF(28, 19), QPointF(19, 28))


def _save(p, c, a):
    _pen(p, c)
    p.drawRoundedRect(QRectF(6, 6, 20, 20), 2.5, 2.5)
    p.drawRect(QRectF(10, 6, 12, 8))
    p.drawRect(QRectF(11, 18, 10, 8))


def _open(p, c, a):
    _pen(p, c)
    _poly(p, (4, 25), (4, 8), (12, 8), (15, 11), (26, 11), (26, 16))
    _poly(p, (4, 25), (9, 15), (29, 15), (25, 25), close=True)


def _tray(p):
    _poly(p, (6, 20), (6, 27), (26, 27), (26, 20))


def _upload(p, c, a):
    _pen(p, c)
    p.drawLine(QPointF(16, 21), QPointF(16, 6))
    _poly(p, (10, 12), (16, 6), (22, 12))
    _tray(p)


def _download(p, c, a):
    _pen(p, c)
    p.drawLine(QPointF(16, 6), QPointF(16, 21))
    _poly(p, (10, 15), (16, 21), (22, 15))
    _tray(p)


def _gear(p, c, a):
    _pen(p, c, 2.0)
    p.save()
    p.translate(16, 16)
    p.drawEllipse(QPointF(0, 0), 7.5, 7.5)
    p.drawEllipse(QPointF(0, 0), 2.8, 2.8)
    for i in range(8):
        p.rotate(45)
        p.drawRoundedRect(QRectF(-2.2, -12.5, 4.4, 5), 1, 1)
    p.restore()


def _terminal(p, c, a):
    _pen(p, c)
    p.drawRoundedRect(QRectF(3.5, 6.5, 25, 19), 3, 3)
    _poly(p, (9, 12), (14, 16), (9, 20))
    p.drawLine(QPointF(17, 21), QPointF(23, 21))


def _code(p, c, a):
    _pen(p, c)
    _poly(p, (13, 8), (5, 16), (13, 24))
    _poly(p, (19, 8), (27, 16), (19, 24))
    _pen(p, a)
    p.drawLine(QPointF(18, 6), QPointF(14, 26))


def _chart(p, c, a):
    _pen(p, c)
    _poly(p, (5, 4), (5, 27), (28, 27))
    _pen(p, a)
    _poly(p, (9, 21), (14, 13), (19, 19), (25, 8))


def _motion(p, c, a):
    _pen(p, c)
    p.drawArc(QRectF(6, 6, 20, 20), 40 * 16, 280 * 16)
    p.setBrush(c)
    _poly(p, (26, 8), (26, 16), (18.5, 13), close=True)
    _fill(p, a)
    p.drawEllipse(QPointF(16, 16), 3.2, 3.2)


def _monitor(p, c, a):
    _pen(p, c)
    p.drawRoundedRect(QRectF(3.5, 5.5, 25, 17), 2.5, 2.5)
    p.drawLine(QPointF(16, 22.5), QPointF(16, 27))
    p.drawLine(QPointF(10, 27), QPointF(22, 27))
    _pen(p, a, 2.0)
    _poly(p, (7, 14), (11.5, 14), (14, 9), (18, 19), (20.5, 14), (25, 14))


def _sliders(p, c, a):
    _pen(p, c)
    for y, x in ((9, 21), (16, 11), (23, 18)):
        p.drawLine(QPointF(5, y), QPointF(27, y))
        p.save()
        _fill(p, a)
        p.drawEllipse(QPointF(x, y), 3.2, 3.2)
        p.restore()


def _help(p, c, a):
    _pen(p, c, 2.0)
    p.drawEllipse(QPointF(16, 16), 12, 12)
    font = QFont()
    font.setPixelSize(19)
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(4, 4, 24, 24), Qt.AlignmentFlag.AlignCenter, "?")


def _folder(p, c, a):
    _pen(p, c)
    _poly(p, (4, 25), (4, 7), (13, 7), (16, 10), (28, 10), (28, 25), close=True)
    _pen(p, a, 1.6)
    p.drawLine(QPointF(4, 14), QPointF(28, 14))


def _drive(p, c, a):
    _pen(p, c)
    p.drawRoundedRect(QRectF(9, 9, 14, 14), 2, 2)
    for v in (12.5, 16, 19.5):
        p.drawLine(QPointF(v, 9), QPointF(v, 4.5))
        p.drawLine(QPointF(v, 23), QPointF(v, 27.5))
        p.drawLine(QPointF(9, v), QPointF(4.5, v))
        p.drawLine(QPointF(23, v), QPointF(27.5, v))
    _fill(p, a)
    p.drawRect(QRectF(13, 13, 6, 6))


def _warning(p, c, a):
    _pen(p, a, 2.2)
    _poly(p, (16, 4.5), (29, 27), (3, 27), close=True)
    p.drawLine(QPointF(16, 12), QPointF(16, 20))
    _fill(p, a)
    p.drawEllipse(QPointF(16, 23.5), 1.5, 1.5)


def _apply(p, c, a):
    _pen(p, a, 3.0)
    _poly(p, (6, 17), (13, 24), (27, 8))


def _revert(p, c, a):
    _pen(p, c, 2.6)
    p.drawArc(QRectF(7, 8, 19, 17), 20 * 16, 240 * 16)
    p.setBrush(c)
    _poly(p, (5, 5), (5, 15), (14, 11), close=True)


def _export(p, c, a):
    _pen(p, c)
    _poly(p, (6, 13), (6, 27), (24, 27), (24, 19))
    p.drawLine(QPointF(13, 21), QPointF(27, 7))
    _poly(p, (19, 6), (28, 6), (28, 15))


def _import(p, c, a):
    _pen(p, c)
    _poly(p, (6, 13), (6, 27), (24, 27), (24, 19))
    p.drawLine(QPointF(27, 7), QPointF(15, 19))
    _poly(p, (14, 11), (14, 20), (23, 20))


def _play(p, c, a):
    _fill(p, c)
    _poly(p, (10, 6), (26, 16), (10, 26), close=True)


def _record(p, c, a):
    _fill(p, a)
    p.drawEllipse(QPointF(16, 16), 9, 9)


def _stop_square(p, c, a):
    _fill(p, c)
    p.drawRoundedRect(QRectF(8, 8, 16, 16), 2, 2)


def _trash(p, c, a):
    _pen(p, c)
    p.drawLine(QPointF(6, 9), QPointF(26, 9))
    _poly(p, (12, 9), (12, 5), (20, 5), (20, 9))
    _poly(p, (9, 9), (10.5, 27), (21.5, 27), (23, 9))
    p.drawLine(QPointF(14, 13), QPointF(14, 23))
    p.drawLine(QPointF(18, 13), QPointF(18, 23))


def _list(p, c, a):
    _pen(p, c)
    for y in (9, 16, 23):
        p.drawLine(QPointF(12, y), QPointF(27, y))
        _fill(p, a)
        p.drawEllipse(QPointF(6.5, y), 1.7, 1.7)
        _pen(p, c)


def _grid(p, c, a):
    """Four small squares: one connected axis (multi-axis - see the README's Multi-axis section), no existing glyph fit."""
    _pen(p, c)
    for x in (6.5, 17.5):
        for y in (6.5, 17.5):
            p.drawRoundedRect(QRectF(x, y, 8, 8), 1.6, 1.6)


def _scatter(p, c, a):
    """The same axes frame `_chart` uses, but a scattered run of small dots instead of a continuous zigzag line -
    the 2D Plotter's own icon (an X/Y trace of two axes against each other, not one signal over time), no
    existing glyph fit."""
    _pen(p, c)
    _poly(p, (5, 4), (5, 27), (28, 27))
    _fill(p, a)
    for x, y in ((9, 22), (13, 17), (17, 19), (21, 12), (24, 9)):
        p.drawEllipse(QPointF(x, y), 1.8, 1.8)


def _cube(p, c, a):
    """An isometric cube outline (3 rhombus faces meeting at a centre point) - the 3D Simulation page's own icon,
    no existing glyph fit."""
    _pen(p, c)
    top, ur, lr, bottom, ll, ul = (16, 6), (26, 11), (26, 21), (16, 26), (6, 21), (6, 11)
    center = QPointF(16, 16)
    _poly(p, top, ur, lr, bottom, ll, ul, close=True)
    for corner in (top, lr, ll):
        p.drawLine(center, QPointF(*corner))


def _wrench(p, c, a):
    _pen(p, c, 2.4)
    p.drawLine(QPointF(9, 27), QPointF(19, 17))
    p.drawArc(QRectF(15, 3, 13, 13), 200 * 16, 290 * 16)


def _wand(p, c, a):
    _pen(p, c)
    p.drawLine(QPointF(6, 26), QPointF(20, 12))
    _pen(p, a, 2.0)
    for (x, y) in ((24, 6), (27, 13), (17, 5)):
        p.drawLine(QPointF(x - 2, y), QPointF(x + 2, y))
        p.drawLine(QPointF(x, y - 2), QPointF(x, y + 2))


def _compare(p, c, a):
    _pen(p, c)
    p.drawRoundedRect(QRectF(4, 6, 10, 20), 1.5, 1.5)
    p.drawRoundedRect(QRectF(18, 6, 10, 20), 1.5, 1.5)
    _pen(p, a, 2.0)
    p.drawLine(QPointF(14.5, 16), QPointF(17.5, 16))


def _fit(p, c, a):
    _pen(p, c)
    for x, y, dx, dy in ((5, 5, 1, 1), (27, 5, -1, 1), (5, 27, 1, -1), (27, 27, -1, -1)):
        _poly(p, (x, y + 7 * dy), (x, y), (x + 7 * dx, y))
    _pen(p, a, 2.0)
    p.drawRect(QRectF(12, 12, 8, 8))


def _cursors(p, c, a):
    _pen(p, c)
    pen = QPen(c, 2.0, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(10, 6), QPointF(10, 27))
    p.drawLine(QPointF(22, 6), QPointF(22, 27))
    _fill(p, a)
    p.drawEllipse(QPointF(10, 6), 3, 3)
    p.drawEllipse(QPointF(22, 6), 3, 3)
    _pen(p, a, 1.6)
    p.drawLine(QPointF(12, 16), QPointF(20, 16))


def _bulb(p, c, a):
    _fill(p, c)
    p.drawEllipse(QPointF(16, 14), 7, 7)
    p.drawRect(QRectF(12.5, 21, 7, 4))


def _bug(p, c, a):
    _warning(p, c, a)


def _chevron_down(p, c, a):
    """A bold, open "v" chevron - the ribbon's own collapse/expand toggle (`ribbon.py`). Replaces a plain text
    glyph ("▾"/"▴") which, even at a large font-size, still looked small next to Help's own drawn icon (2026-09-24,
    user: circled it a second time - the font-size bump alone (2026-09-23) wasn't enough, since a triangle
    CHARACTER only fills a fraction of its own em-box; a properly drawn icon, sized and weighted the same way
    every other icon here is, does not have that problem)."""
    _pen(p, c, 3.4)
    _poly(p, (7, 12), (16, 21), (25, 12))


def _chevron_up(p, c, a):
    _pen(p, c, 3.4)
    _poly(p, (7, 20), (16, 11), (25, 20))


def _chevron_left(p, c, a):
    """The navigator's own collapse/expand toggle (`navigator.py`) - the same drawn-icon treatment the ribbon's
    toggle got (2026-09-25, user: "logo mismatched when collapse" - the plain "«"/"»" text glyph looked nothing
    like the drawn gold icons in the buttons right under it once the panel was collapsed)."""
    _pen(p, c, 3.4)
    _poly(p, (20, 7), (11, 16), (20, 25))


def _chevron_right(p, c, a):
    _pen(p, c, 3.4)
    _poly(p, (12, 7), (21, 16), (12, 25))


GLYPHS = {
    "plug": _plug, "unplug": _unplug, "save": _save, "open": _open, "upload": _upload, "download": _download,
    "gear": _gear, "terminal": _terminal, "chart": _chart, "motion": _motion, "monitor": _monitor, "sliders": _sliders,
    "help": _help, "folder": _folder, "drive": _drive, "warning": _warning, "apply": _apply, "revert": _revert,
    "export": _export, "import": _import, "play": _play, "record": _record, "stop": _stop_square, "trash": _trash,
    "list": _list, "wrench": _wrench, "wand": _wand, "compare": _compare, "bulb": _bulb, "fault": _bug,
    "fit": _fit, "cursors": _cursors, "code": _code, "grid": _grid, "scatter": _scatter, "cube": _cube,
    "chevron_down": _chevron_down, "chevron_up": _chevron_up, "chevron_left": _chevron_left,
    "chevron_right": _chevron_right,
}


@lru_cache(maxsize=None)
def icon(name, size=32, colour=None, accent=None):
    """A QIcon of the glyph `name` (see GLYPHS). colour: strokes (gold), accent: highlights (ivory / red for x and warnings)."""
    draw = GLYPHS[name]
    qicon = QIcon()
    for mode, main, second in ((QIcon.Mode.Normal, colour or theme.GOLD, accent or (theme.RUBY if name in ("unplug", "warning", "fault", "record")
                                                                                     else theme.GOLD_HI)),
                               (QIcon.Mode.Disabled, theme.DIM, theme.DIM)):
        scale = 2
        pix = QPixmap(size * scale, size * scale)
        pix.setDevicePixelRatio(scale)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 32.0, size / 32.0)
        draw(p, QColor(main), QColor(second))
        p.end()
        qicon.addPixmap(pix, mode)
    return qicon
