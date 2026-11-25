"""Servo Drive's and Motion's shared dockable multi-pane workspace (Phase 3 of the EAS3 "Views" pass, see the README) -
a nested QMainWindow embedded inside PageHost's content area, opt-in via the ribbon's own Docked View toggle.
Reuses the exact same page instances main_window.py's own `self.tools`/`self._axis_pages` already hold - no new
page classes, just an additional surface to show several of them at once, resized/tabified/closed independently,
instead of PageHost's own single-page QStackedWidget swap. System Configuration and every other activity keep
using PageHost unchanged - main_window.py only ever routes a Servo Drive or Motion tool here, and only while
the toggle is on (see MainWindow.open_tool/_show_docked_tool). This class has no idea PageHost's own stack even
exists - it only ever holds whatever page widget it is handed; the caller is the one responsible for taking a
page OUT of the stack before showing it here, and putting it back after closing it here.

Every panel gets this module's own title bar (DockTitleBar) instead of Qt's default one, and with it this
module's own drag: tear a panel off by its title and the workspace shows DROP GUIDES (DockGuideOverlay) - an arrow
at each of its four edges, and a cluster of five over whichever other panel the cursor is on - so where a panel
will land is always visible before letting go (user, 2026-09-25, screen recording: with Qt's own drag there was
no visible way to choose a side - "if i move mouse towards right or left or top or bottom then i should get an
option where to set that dock")."""
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QDockWidget, QFrame, QHBoxLayout, QLabel, QMainWindow, QScrollArea,
                               QToolButton, QWidget)

from . import theme

OUTER, INNER, TAB = "outer", "inner", "tab"
LEFT, RIGHT, TOP, BOTTOM = "left", "right", "top", "bottom"
GUIDE = 40           # a drop guide's side, px
GUIDE_GAP = 6        # between the five guides of the inner cluster
EDGE_STRIP = 24      # this close to a workspace edge also counts as that edge's guide (a fast drag need not land on it)
FLOAT_SCALE = 0.7    # a panel torn off while dragging is at most this share of the workspace


class DockScroll(QScrollArea):
    """The scroll area every docked page sits in (DockWorkspace.show_tool). Reports a modest, uniform size hint on
    purpose: Qt sizes its dock areas from the docks' own hints at the moment a dock is inserted (probed 2026-09-25 -
    resizeDocks() only ever acts within one line of docks, never between areas), and a page's real hint is its
    whole minimum layout, so a dropped panel used to grab nearly everything and leave its neighbour a sliver.
    Equal hints split the space evenly instead."""

    def sizeHint(self):
        return QSize(480, 360)

    def minimumSizeHint(self):
        return QSize(220, 150)      # a panel squeezed by Qt's own between-area sizing stays readable (see _rebalance)


class DockTitleBar(QWidget):
    """A panel's own black+gold title bar (QDockWidget.setTitleBarWidget): the title, a float toggle, a close
    button, and the drag that moves the panel. Press and move past Qt's own start-drag distance and the panel
    tears off into a floating window that follows the cursor while the workspace shows its drop guides; release
    on a guide to dock it there, anywhere else to leave it floating where it is. Double-click floats/re-docks.
    A custom title bar is the documented way to take a dock's drag over: Qt runs its own drag only through its
    default title bar, and accepting the mouse events here keeps that one out entirely."""

    def __init__(self, dock, workspace):
        super().__init__(dock)
        self.setObjectName("dockTitle")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._dock, self._workspace = dock, workspace
        self._press = None          # global point of the left-button press, while it is held
        self._dragging = False
        self._grab_offset = QPoint()
        bar = QHBoxLayout(self)
        bar.setContentsMargins(8, 4, 4, 4)
        bar.setSpacing(4)
        self.label = QLabel(dock.windowTitle())
        self.label.setObjectName("dockTitleLabel")
        self.float_btn = QToolButton()
        self.float_btn.setText("❐")
        self.float_btn.setToolTip("Float this panel as its own window, or dock it back")
        self.close_btn = QToolButton()
        self.close_btn.setText("✕")
        self.close_btn.setToolTip("Close this panel (the sidebar reopens it)")
        for button in (self.float_btn, self.close_btn):
            button.setObjectName("dockTitleButton")
            button.setAutoRaise(True)
            button.setFixedSize(22, 20)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bar.addWidget(self.label, 1)
        bar.addWidget(self.float_btn)
        bar.addWidget(self.close_btn)
        dock.windowTitleChanged.connect(self.label.setText)
        self.float_btn.clicked.connect(lambda _c=False: dock.setFloating(not dock.isFloating()))
        self.close_btn.clicked.connect(lambda _c=False: dock.close())

    # ---- drag ---------------------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._press, self._dragging = event.globalPosition().toPoint(), False
        event.accept()

    def mouseMoveEvent(self, event):
        if self._press is None:
            return super().mouseMoveEvent(event)
        pos = event.globalPosition().toPoint()
        if not self._dragging:
            if (pos - self._press).manhattanLength() < QApplication.startDragDistance():
                return
            self._start_drag()
        self._dock.move(pos - self._grab_offset)
        self._workspace.guides.hover(self._workspace.mapFromGlobal(pos))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return super().mouseReleaseEvent(event)
        dragging, self._press, self._dragging = self._dragging, None, False
        if dragging:
            self.releaseMouse()
            workspace = self._workspace
            zone = workspace.guides.zone_at(workspace.mapFromGlobal(event.globalPosition().toPoint()))
            workspace.guides.end()
            if zone is not None:
                workspace.drop(self._dock, zone)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseDoubleClickEvent(event)
        self._press = None
        self._dock.setFloating(not self._dock.isFloating())
        event.accept()

    def _start_drag(self):
        """Tears the panel off (if it was docked) into a floating window sized to fit the workspace, keeps the
        cursor on the title bar at the spot it was pressed, grabs the mouse (the panel just moved to a new native
        window - Qt's own drag does the same) and raises the drop guides."""
        self._dragging = True
        dock, workspace = self._dock, self._workspace
        if not dock.isFloating():
            size = QSize(min(dock.width(), int(workspace.width() * FLOAT_SCALE)),
                         min(dock.height(), int(workspace.height() * FLOAT_SCALE))).expandedTo(QSize(320, 200))
            dock.setFloating(True)
            dock.resize(size)
        local = self.mapTo(dock, self.mapFromGlobal(self._press))
        self._grab_offset = QPoint(max(20, min(local.x(), dock.width() - 20)), max(0, local.y()))
        self.grabMouse()
        workspace.guides.begin(dock)


class DockGuideOverlay(QWidget):
    """The drop guides shown over the workspace while a panel is being dragged: an arrow at the middle of each
    of the workspace's four edges (dock the panel along that whole side), and, while the cursor is over another
    panel, a cluster of five on it (left/right/above/below that panel, or the centre one to stack the two as
    tabs). The guide under the cursor lights up and a translucent preview shows the space the panel would take;
    a small ghost label names the panel being dragged. Mouse-transparent - the title bar keeps the mouse."""

    def __init__(self, workspace):
        # Its own always-on-top, translucent, click-through window over the workspace - NOT a child widget: the panel
        # being dragged is a floating window of its own, which would cover a child overlay (live, 2026-09-25: the
        # cluster of guides sat hidden behind the panel). Coordinates inside stay workspace-local: the window is
        # placed exactly over the workspace (begin()).
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._workspace = workspace
        self._dragged = self._hovered = self._zone = self._cursor = None
        self.hide()

    def begin(self, dragged):
        self._dragged, self._hovered, self._zone, self._cursor = dragged, None, None, None
        self.setGeometry(self.workspace_rect())
        self.show()
        self.raise_()

    def workspace_rect(self):
        ws = self._workspace
        return QRect(ws.mapToGlobal(QPoint(0, 0)), ws.size())

    def end(self):
        self._dragged = None
        self.hide()

    def hover(self, pos):
        """`pos` in workspace coordinates."""
        self._cursor = pos
        self._hovered = self._panel_under(pos)
        self._zone = self.zone_at(pos)
        self.update()

    def _panel_under(self, pos):
        for dock in self._workspace.docked_panels():
            if dock is not self._dragged and self._rect_of(dock).contains(pos):
                return dock
        return None

    def _rect_of(self, dock):
        return QRect(dock.mapTo(self._workspace, QPoint(0, 0)), dock.size())

    def guide_rects(self):
        """{zone: QRect} of every guide currently shown, in workspace coordinates. A zone is
        (OUTER, side), (INNER, side, panel) or (TAB, panel)."""
        area, g, margin = self.rect(), GUIDE, 12
        rects = {
            (OUTER, LEFT): QRect(margin, area.center().y() - g // 2, g, g),
            (OUTER, RIGHT): QRect(area.right() - margin - g, area.center().y() - g // 2, g, g),
            (OUTER, TOP): QRect(area.center().x() - g // 2, margin, g, g),
            (OUTER, BOTTOM): QRect(area.center().x() - g // 2, area.bottom() - margin - g, g, g),
        }
        target = self._hovered
        if target is not None:
            centre, step = self._rect_of(target).center(), g + GUIDE_GAP
            middle = QRect(centre.x() - g // 2, centre.y() - g // 2, g, g)
            rects[(TAB, target)] = middle
            rects[(INNER, LEFT, target)] = middle.translated(-step, 0)
            rects[(INNER, RIGHT, target)] = middle.translated(step, 0)
            rects[(INNER, TOP, target)] = middle.translated(0, -step)
            rects[(INNER, BOTTOM, target)] = middle.translated(0, step)
        return rects

    def zone_at(self, pos):
        """The zone a release at `pos` (workspace coordinates) would drop into, or None (leave it floating)."""
        rects = self.guide_rects()
        for zone, rect in rects.items():
            if zone[0] != OUTER and rect.contains(pos):
                return zone
        for zone, rect in rects.items():
            if zone[0] == OUTER and rect.contains(pos):
                return zone
        area = self.rect()
        if not area.contains(pos):
            return None
        if pos.x() < EDGE_STRIP:
            return (OUTER, LEFT)
        if pos.x() > area.right() - EDGE_STRIP:
            return (OUTER, RIGHT)
        if pos.y() < EDGE_STRIP:
            return (OUTER, TOP)
        if pos.y() > area.bottom() - EDGE_STRIP:
            return (OUTER, BOTTOM)
        return None

    def preview_rect(self, zone):
        base = self.rect() if zone[0] == OUTER else self._rect_of(zone[-1])
        if zone[0] == TAB:
            return base
        side = zone[1]
        if side == LEFT:
            return QRect(base.left(), base.top(), base.width() // 2, base.height())
        if side == RIGHT:
            return QRect(base.center().x(), base.top(), base.width() - base.width() // 2, base.height())
        if side == TOP:
            return QRect(base.left(), base.top(), base.width(), base.height() // 2)
        return QRect(base.left(), base.center().y(), base.width(), base.height() - base.height() // 2)

    def paintEvent(self, _event):
        if self._dragged is None:
            return
        gold, gold_hi, dark = QColor(theme.GOLD), QColor(theme.GOLD_HI), QColor(20, 22, 27, 230)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._zone is not None:
            preview = self.preview_rect(self._zone)
            wash = QColor(gold)
            wash.setAlpha(70)
            p.fillRect(preview, wash)
            p.setPen(QPen(gold, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(preview.adjusted(1, 1, -1, -1))
        for zone, rect in self.guide_rects().items():
            active = zone == self._zone
            p.setPen(QPen(gold_hi if active else gold, 1.5))
            p.setBrush(gold if active else dark)
            p.drawRoundedRect(QRectF(rect), 5, 5)
            self._glyph(p, rect, zone, QColor(theme.BG) if active else gold)
        if self._cursor is not None:
            text = self._dragged.windowTitle()
            font = p.font()
            font.setBold(True)
            p.setFont(font)
            metrics = p.fontMetrics()
            w, h = metrics.horizontalAdvance(text) + 20, metrics.height() + 10
            box = QRect(min(self._cursor.x() + 18, self.width() - w - 4), min(self._cursor.y() + 18, self.height() - h - 4), w, h)
            p.setPen(QPen(gold, 1))
            p.setBrush(dark)
            p.drawRoundedRect(QRectF(box), 5, 5)
            p.setPen(gold_hi)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        p.end()

    @staticmethod
    def _glyph(p, rect, zone, colour):
        c, s = QPointF(rect.center()), rect.width() * 0.22
        if zone[0] == TAB:                      # a small window with a tab strip = "stack as a tab"
            p.setPen(QPen(colour, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(c.x() - s, c.y() - s * 0.8, 2 * s, 1.7 * s))
            p.drawLine(QPointF(c.x() - s, c.y() - s * 0.8 + 5), QPointF(c.x() + s, c.y() - s * 0.8 + 5))
            return
        tip = {LEFT: (c.x() - s, c.y()), RIGHT: (c.x() + s, c.y()), TOP: (c.x(), c.y() - s), BOTTOM: (c.x(), c.y() + s)}[zone[1]]
        base = {LEFT: ((c.x() + s * 0.6, c.y() - s), (c.x() + s * 0.6, c.y() + s)),
                RIGHT: ((c.x() - s * 0.6, c.y() - s), (c.x() - s * 0.6, c.y() + s)),
                TOP: ((c.x() - s, c.y() + s * 0.6), (c.x() + s, c.y() + s * 0.6)),
                BOTTOM: ((c.x() - s, c.y() - s * 0.6), (c.x() + s, c.y() - s * 0.6))}[zone[1]]
        path = QPainterPath(QPointF(*tip))
        path.lineTo(QPointF(*base[0]))
        path.lineTo(QPointF(*base[1]))
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)
        p.drawPath(path)


class DockWorkspace(QMainWindow):
    def __init__(self):
        super().__init__()
        # A QMainWindow needs a central widget, but any leftover space goes to it, not to the docks: on a maximised
        # window (2026-09-25, user's screenshot) the two panels sat on the right and the left half of the
        # workspace was blank. A zero-size one keeps Qt happy and hands the dock areas every pixel instead.
        centre = QWidget()
        centre.setFixedSize(0, 0)
        self.setCentralWidget(centre)
        # Nested + tabbed, but NOT animated: drop() re-docks a floating panel (setFloating(False)) and moves it in
        # the same call, and Qt's animated re-dock only lands the panel when its animation ends - the move
        # then raced it and the panel stayed where it was (caught by the drag test, 2026-09-25).
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks)
        self._docks = {}       # tool key -> QDockWidget
        self.guides = DockGuideOverlay(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.guides.isVisible():
            self.guides.setGeometry(self.guides.workspace_rect())

    def open_keys(self):
        return list(self._docks)

    def page_of(self, key):
        """The page widget shown in this key's dock (inside the dock's own scroll area), or None."""
        dock = self._docks.get(key)
        return dock.widget().widget() if dock is not None else None

    def docked_panels(self):
        """The panels currently docked and showing (not floating, not a hidden tab)."""
        return [dock for dock in self._docks.values() if dock.isVisible() and not dock.isFloating()]

    def show_tool(self, key, title, widget):
        """Adds this tool's own page as a new dock (first time) or just re-titles/raises it (already open) - the
        same page widget every time. The dock gets this module's own title bar (title, float, close, drag with
        drop guides) in place of Qt's default one."""
        dock = self._docks.get(key)
        if dock is None:
            dock = QDockWidget(title)
            dock.setObjectName(key)     # saveState()/restoreState() key off this - must be stable and unique
            # The page sits inside a scroll area, not directly in the dock: a dock can never be smaller than its
            # widget's minimum size, and three stacked pages' minimums (596 + 351 + 161 px) do not fit a 725 px
            # workspace - Qt then clamps each dock to its own minimum and they OVERLAP, one page painting through
            # the next (seen live 2026-09-25). Behind a scroll area the dock shrinks freely and the page scrolls.
            scroll = DockScroll()
            scroll.setObjectName("dockScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(widget)
            dock.setWidget(scroll)
            dock.setTitleBarWidget(DockTitleBar(dock, self))
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            self._docks[key] = dock
        else:
            dock.setWindowTitle(title)
        dock.setVisible(True)
        dock.raise_()
        return dock

    def drop(self, dock, zone):
        """Docks `dock` where a drop guide says (DockGuideOverlay.zone_at): (OUTER, side) along that whole edge of
        the workspace, (INNER, side, panel) beside that panel, (TAB, panel) stacked as a tab with it - then
        re-balances every line of docks (_rebalance)."""
        horizontal, vertical = Qt.Orientation.Horizontal, Qt.Orientation.Vertical
        if dock.isFloating():
            dock.setFloating(False)
        kind = zone[0]
        if kind == OUTER:
            corner = Qt.Corner
            area, orientation, corners = {
                LEFT: (Qt.DockWidgetArea.LeftDockWidgetArea, horizontal, (corner.TopLeftCorner, corner.BottomLeftCorner)),
                RIGHT: (Qt.DockWidgetArea.RightDockWidgetArea, horizontal, (corner.TopRightCorner, corner.BottomRightCorner)),
                TOP: (Qt.DockWidgetArea.TopDockWidgetArea, vertical, (corner.TopLeftCorner, corner.TopRightCorner)),
                BOTTOM: (Qt.DockWidgetArea.BottomDockWidgetArea, vertical, (corner.BottomLeftCorner, corner.BottomRightCorner)),
            }[zone[1]]
            # An edge guide means the WHOLE edge: the side just dropped on takes its two corners, so the newest
            # edge drop is always the outermost (as in Visual Studio), whatever areas the other panels sit in -
            # by Qt's default the top/bottom areas own every corner, and live (2026-09-25) a panel dropped on the
            # left guide landed UNDER a panel sitting in the top area, full width, as if nothing had happened.
            # Corner ownership is part of saveState(), so a saved arrangement comes back as it was.
            for each in corners:
                self.setCorner(each, area)
            self.removeDockWidget(dock)
            self.addDockWidget(area, dock, orientation)
            dock.show()
        elif kind == INNER:
            side, target = zone[1], zone[2]
            orientation = horizontal if side in (LEFT, RIGHT) else vertical
            self.removeDockWidget(dock)
            self.splitDockWidget(target, dock, orientation)      # Qt only ever splits "after" (right / below)...
            if side in (LEFT, TOP):                               # ...so for "before", split once more the other way
                self.removeDockWidget(target)
                self.splitDockWidget(dock, target, orientation)
                target.show()
            dock.show()
        else:
            target = zone[1]
            self.removeDockWidget(dock)
            self.tabifyDockWidget(target, dock)
            dock.show()
        self._rebalance()
        dock.raise_()

    def _rebalance(self):
        """Equal weights for every docked panel in both orientations, so each line of docks shares its span evenly
        after a drop - without this the moved panel kept whatever size it last had (a 79 px strip left over from a
        tall stack, say) and Qt's own placement gave a neighbour a 14 px sliver. Sizes BETWEEN dock areas (a panel
        on the left edge beside one on the top edge) are Qt's own, from the size hints (DockScroll) - the separator
        between them still drags."""
        docks = self.docked_panels()
        if not docks:
            return
        n = len(docks)
        self.resizeDocks(docks, [max(1, self.width() // n)] * n, Qt.Orientation.Horizontal)
        self.resizeDocks(docks, [max(1, self.height() // n)] * n, Qt.Orientation.Vertical)

    def close_tool(self, key):
        """Removes and returns this key's own page widget, still fully alive, just detached - or None if it
        wasn't open. The caller decides where the widget goes next (back to PageHost's own stack, or nowhere at
        all if the axis it belonged to just disconnected)."""
        dock = self._docks.pop(key, None)
        if dock is None:
            return None
        scroll = dock.widget()
        widget = scroll.takeWidget()     # detach the page (still alive, unparented) before the dock and its
        dock.setWidget(None)             # scroll area are destroyed
        scroll.deleteLater()
        self.removeDockWidget(dock)
        dock.deleteLater()
        return widget

    def close_all(self):
        return {key: self.close_tool(key) for key in list(self._docks)}
