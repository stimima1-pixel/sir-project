"""Pieces of the studio window around the pages: the page host (title bar + the page) and the bottom status bar."""
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from . import theme
from .icons import icon
from .widgets import set_prop

LEVEL_COLOURS = {"info": theme.SAPPHIRE, "warn": theme.AMBER, "error": theme.RUBY, "ok": theme.EMERALD}


class PageHost(QWidget):
    """The centre of the window: a header (icon, title, sub-title, the drive's name) and the page that is open."""

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()
        header.setObjectName("pageHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(14, 5, 14, 5)
        row.setSpacing(10)
        self.icon_label = QLabel()
        self.title = QLabel("")
        self.title.setObjectName("pageTitle")
        self.crumb = QLabel("")
        self.crumb.setObjectName("pageCrumb")
        self.target = QLabel("")
        self.target.setObjectName("pageTarget")
        row.addWidget(self.icon_label)
        row.addWidget(self.title)
        row.addWidget(self.crumb)
        row.addStretch(1)
        row.addWidget(self.target)
        outer.addWidget(header)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

    def show_page(self, widget, title, crumb="", glyph="gear"):
        if self.stack.indexOf(widget) < 0:
            self.stack.addWidget(widget)
        self.stack.setCurrentWidget(widget)
        self.icon_label.setPixmap(icon(glyph, 26).pixmap(QSize(26, 26)))
        self.title.setText(title)
        self.crumb.setText(crumb)
        self.crumb.setVisible(bool(crumb))

    def set_crumb(self, text):
        self.crumb.setText(text)
        self.crumb.setVisible(bool(text))

    def set_target(self, text):
        self.target.setText(text)

    def current_page(self):
        return self.stack.currentWidget()


class BottomBar(QFrame):
    """What the window is doing: the activity or page that is open, or the latest message; the connection state on the right."""

    def __init__(self, pill):
        super().__init__()
        self.setObjectName("bottomBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 3, 10, 3)
        row.setSpacing(8)
        self.bulb = QLabel()
        self.message = QLabel("")
        self.message.setObjectName("bottomMessage")
        row.addWidget(self.bulb)
        row.addWidget(self.message, 1)
        row.addWidget(pill)
        self.level = "info"
        self.set_message("", "info")

    def set_message(self, text, level="info"):
        self.level = level
        self.message.setText(text)
        self.bulb.setPixmap(icon("bulb", 18, LEVEL_COLOURS.get(level, theme.SAPPHIRE)).pixmap(QSize(18, 18)))
        set_prop(self.message, "level", level)
