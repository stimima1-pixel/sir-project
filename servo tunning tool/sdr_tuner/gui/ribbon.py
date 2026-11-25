"""The ribbon of the studio window: a row of tabs, under it groups of big icon buttons, and the round STOP button.

Some tabs are 'contextual': they are hidden until the tool they belong to is opened (like Drive Quick Tuner)."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QTabBar, QToolButton, QVBoxLayout,
                               QWidget)

from . import theme
from .icons import icon


class RibbonButton(QToolButton):
    """A big button: the icon above its text."""

    def __init__(self, text, glyph, slot=None, tip=""):
        super().__init__()
        self.setObjectName("ribbonButton")
        self.setText(text)
        self.setIcon(icon(glyph, 34))
        self.setIconSize(QSize(34, 34))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setAutoRaise(True)
        self.setMinimumWidth(64)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tip or text.replace("\n", " "))
        if slot is not None:
            self.clicked.connect(lambda _checked=False: slot())


class RibbonGroup(QFrame):
    """Buttons that belong together, with the group's name under them."""

    def __init__(self, title):
        super().__init__()
        self.setObjectName("ribbonGroup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 2)
        outer.setSpacing(1)
        self._row = QHBoxLayout()
        self._row.setSpacing(2)
        outer.addLayout(self._row, 1)
        caption = QLabel(title)
        caption.setObjectName("ribbonCaption")
        caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(caption)

    def add_button(self, text, glyph, slot=None, tip=""):
        button = RibbonButton(text, glyph, slot, tip)
        self._row.addWidget(button)
        return button


class RibbonPage(QWidget):
    def __init__(self):
        super().__init__()
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(4, 0, 4, 0)
        self._row.setSpacing(0)
        self._row.addStretch(1)
        self.groups = {}

    def add_group(self, title):
        group = RibbonGroup(title)
        self._row.insertWidget(self._row.count() - 1, group)
        self.groups[title] = group
        return group


class Ribbon(QFrame):
    page_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("ribbon")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("ribbonBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 0, 10, 0)
        row.setSpacing(0)
        brand = QLabel("STIMITA MOTION CONTROL")
        brand.setObjectName("brand")
        brand.setFont(theme.brand_font(15, spacing=2.6))
        self.tabs = QTabBar()
        self.tabs.setObjectName("ribbonTabs")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.collapse_btn = QToolButton()
        self.collapse_btn.setObjectName("ribbonCollapse")     # bigger than the plain "ribbonSmall" buttons (2026-09-23,
                                                                # user: "increase button size" - easy to miss at that size)
        self.collapse_btn.setIcon(icon("chevron_up", 26))     # a drawn icon, not a text glyph (2026-09-24, user
                                                                # circled it again - see icons.py's own _chevron_down
                                                                # docstring for why a bigger FONT wasn't enough)
        self.collapse_btn.setIconSize(QSize(26, 26))
        self.collapse_btn.setToolTip("Collapse or expand the ribbon")
        self.help_btn = QToolButton()
        self.help_btn.setObjectName("ribbonSmall")
        self.help_btn.setIcon(icon("help", 20))
        self.help_btn.setIconSize(QSize(20, 20))
        self.help_btn.setToolTip("Help")
        row.addWidget(brand)
        row.addSpacing(30)
        row.addWidget(self.tabs)
        row.addStretch(1)
        row.addWidget(self.collapse_btn)
        row.addWidget(self.help_btn)
        outer.addWidget(bar)

        self.body = QFrame()
        self.body.setObjectName("ribbonBody")
        body_row = QHBoxLayout(self.body)
        body_row.setContentsMargins(6, 4, 14, 4)
        body_row.setSpacing(10)
        self.stack = QStackedWidget()
        self.stop_button = QPushButton("STOP")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setFixedSize(78, 78)
        self.stop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_button.setToolTip("Stop the motor and reset the target to 0.   Shortcut: Esc")
        body_row.addWidget(self.stack, 1)
        body_row.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(self.body)
        self.body.setFixedHeight(108)

        self._keys = []
        self.tabs.currentChanged.connect(self._tab_changed)
        self.collapse_btn.clicked.connect(self._toggle_collapsed)

    # ---- pages ------------------------------------------------------------------------------
    def add_page(self, key, title, contextual=False):
        page = RibbonPage()
        self._keys.append(key)
        self.tabs.addTab(title)
        self.stack.addWidget(page)
        if contextual:
            self.tabs.setTabVisible(len(self._keys) - 1, False)
        return page

    def page(self, key):
        return self.stack.widget(self._keys.index(key))

    def current_key(self):
        return self._keys[self.tabs.currentIndex()] if self._keys else None

    def show_page(self, key):
        self.tabs.setCurrentIndex(self._keys.index(key))

    def set_page_visible(self, key, visible):
        """Show or hide a contextual tab. Hiding the tab that is showing moves to the first visible one."""
        index = self._keys.index(key)
        self.tabs.setTabVisible(index, visible)
        self.tabs.updateGeometry()                     # the row of tabs is wider or narrower now: make room before showing one
        if self.tabs.parentWidget() is not None and self.tabs.parentWidget().layout() is not None:
            self.tabs.parentWidget().layout().activate()
        if not visible and self.tabs.currentIndex() == index:
            self.tabs.setCurrentIndex(next(i for i in range(self.tabs.count()) if self.tabs.isTabVisible(i)))

    def tab_titles(self):
        return [self.tabs.tabText(i) for i in range(self.tabs.count()) if self.tabs.isTabVisible(i)]

    def _tab_changed(self, index):
        if 0 <= index < len(self._keys):
            self.stack.setCurrentIndex(index)
            self.page_changed.emit(self._keys[index])

    def _toggle_collapsed(self):
        collapsed = not self.body.isHidden()
        self.body.setVisible(not collapsed)
        self.collapse_btn.setIcon(icon("chevron_down" if collapsed else "chevron_up", 26))
