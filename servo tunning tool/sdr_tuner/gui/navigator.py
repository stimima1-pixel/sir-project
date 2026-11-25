"""The left column of the studio window: the workspace tree, the list of tools of the current activity, and under them
the list of activities (System Configuration, Servo Drive, ...)."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QStackedWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .icons import icon
from .widgets import fold_on_single_click, set_prop

WIDTH = 262
COLLAPSED_WIDTH = 54


class Navigator(QFrame):
    activity_changed = Signal(str)      # activity key
    tool_chosen = Signal(str)           # tool key (the user clicked a tool of the current activity)
    drive_chosen = Signal()             # the primary drive's own item in the workspace tree was clicked
    axis_chosen = Signal(int)           # an ADDITIONAL axis's own item in the workspace tree was clicked - node id
                                         # (a flat item, exactly like the primary's own - see add_axis_item)

    def __init__(self):
        super().__init__()
        self.setObjectName("navigator")
        self.setFixedWidth(WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        head = QHBoxLayout()
        self.title = QLabel("")
        self.title.setObjectName("navTitle")
        self.collapse_btn = QToolButton()
        self.collapse_btn.setObjectName("navCollapse")
        self.collapse_btn.setIcon(icon("chevron_left", 22))      # drawn, like every other icon here - not a text glyph
        self.collapse_btn.setIconSize(QSize(22, 22))
        self.collapse_btn.setToolTip("Collapse or expand this panel")
        head.addWidget(self.title, 1)
        head.addWidget(self.collapse_btn)
        outer.addLayout(head)

        self.tree = QTreeWidget()
        self.tree.setObjectName("navTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tree.setIconSize(QSize(20, 20))
        fold_on_single_click(self.tree)      # the Workspace folder folds/unfolds on one click
        self.workspace = QTreeWidgetItem(self.tree, ['Workspace "Default"'])
        self.workspace.setIcon(0, icon("folder", 20))
        self.workspace.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.drive_item = QTreeWidgetItem(self.workspace, ["Drive01"])
        self.workspace.setExpanded(True)
        self.tree.setCurrentItem(self.drive_item)
        outer.addWidget(self.tree, 1)

        self.tools_stack = QStackedWidget()
        self.tools_stack.setObjectName("toolsStack")
        outer.addWidget(self.tools_stack, 3)
        outer.addStretch(0)      # collapsed (tree and tools hidden): keeps the toggle at the top and the activity
                                 # buttons at the bottom - without it Qt spread the leftover height between the
                                 # rows and the toggle floated mid-panel (2026-09-25); expanded, the tree's
                                 # and the tools' own stretch factors still take every spare pixel

        self.activity_box = QVBoxLayout()
        self.activity_box.setSpacing(2)
        outer.addLayout(self.activity_box)

        self._activities = {}       # key -> (button, tool list, title)
        self._axis_items = {}       # node id -> QTreeWidgetItem, for ADDITIONAL axes (connect_axis) - see add_axis_item
        self._collapsed = False
        self.collapse_btn.clicked.connect(self._toggle_collapsed)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.set_drive("Drive01", False)

    # ---- building ---------------------------------------------------------------------------
    def add_activity(self, key, title, glyph, tools=()):
        """tools: `(tool_key, title, glyph)` for a workspace-level tool whose own key never changes (one shared
        page regardless of which drive is selected - Terminal, Motion Controller, Axes), or `(tool_key, title,
        glyph, kind)` for one of the 7 PER-DRIVE tools (Quick/Advanced Tuning, Motion, Recorder, Parameters,
        Telemetry, Peripherals) - `kind` is a stable tag (`"motion"`, `"recorder"`, ...) `retarget_drive_tools()`
        later uses to re-point that row at a different drive's own key, when a different one becomes the drive
        this list is scoped to, without needing to rebuild the list itself."""
        listing = QListWidget()
        listing.setObjectName("toolList")
        listing.setIconSize(QSize(24, 24))
        listing.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        listing.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        listing.setSpacing(1)
        for entry in tools:
            tool_key, tool_title, tool_glyph = entry[:3]
            item = QListWidgetItem(icon(tool_glyph, 24), tool_title)
            item.setData(Qt.ItemDataRole.UserRole, tool_key)
            if len(entry) > 3:
                item.setData(Qt.ItemDataRole.UserRole + 1, entry[3])
            listing.addItem(item)
        listing.itemClicked.connect(lambda item: self.tool_chosen.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.tools_stack.addWidget(listing)
        button = QPushButton(title)
        button.setObjectName("activityButton")
        button.setIcon(icon(glyph, 24))
        button.setIconSize(QSize(24, 24))
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(title)
        button.clicked.connect(lambda _c=False, k=key: self.set_activity(k))
        self.activity_box.addWidget(button)
        self._activities[key] = (button, listing, title)

    # ---- state ------------------------------------------------------------------------------
    def set_activity(self, key):
        button, listing, title = self._activities[key]
        button.setChecked(True)
        self.title.setText(title)
        self.tools_stack.setCurrentWidget(listing)
        self.tools_stack.setVisible(listing.count() > 0 and not self._collapsed)
        self.activity_changed.emit(key)

    def activity_title(self, key):
        return self._activities[key][2]

    def current_activity(self):
        return next((k for k, (button, _, _) in self._activities.items() if button.isChecked()), None)

    def tool_keys(self, activity):
        listing = self._activities[activity][1]
        return [listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())]

    def select_tool(self, key):
        """Highlight a tool in the list of its activity without announcing it."""
        for _, listing, _ in self._activities.values():
            listing.blockSignals(True)
            listing.clearSelection()
            for i in range(listing.count()):
                if listing.item(i).data(Qt.ItemDataRole.UserRole) == key:
                    listing.setCurrentRow(i)
            listing.blockSignals(False)

    def set_drive(self, name, connected):
        self.drive_item.setText(0, f"{name} (SDR)")
        self.drive_item.setIcon(0, icon("plug" if connected else "unplug", 20))

    def retarget_drive_tools(self, kind_to_key):
        """Re-points every per-drive row of the Servo Drive activity's own tool list (tagged with a
        `kind` in `add_activity` - Quick/Advanced Tuning, Motion, Recorder, Parameters, Telemetry, Peripherals) at
        a DIFFERENT drive's own key - `kind_to_key`: `{"motion": "motion:2", ...}`, whatever the caller (which
        knows the real tool-key convention) wants each kind to mean right now. The workspace-level rows (Terminal,
        Motion Controller, Axes - no `kind` tag) are left alone: one shared page, regardless of which drive is
        currently selected. Used whenever a different drive - the primary, or an additional axis - becomes the
        one this list should describe, so the SAME list always shows "whichever drive is currently selected"'s
        own tools, instead of a fixed set."""
        _, listing, _ = self._activities["drive"]
        for i in range(listing.count()):
            item = listing.item(i)
            kind = item.data(Qt.ItemDataRole.UserRole + 1)
            if kind in kind_to_key:
                item.setData(Qt.ItemDataRole.UserRole, kind_to_key[kind])

    def add_axis_item(self, node_id, label=None):
        """A sibling of the primary drive item, for one ADDITIONAL connected axis (`Session.connect_axis` - real
        multi-axis, see the README's Multi-axis section) - a FLAT item, exactly like the primary drive's own (not an
        expandable subtree): clicking it selects this axis as the one the Servo Drive activity's own
        tool list is currently scoped to (`retarget_drive_tools`, called by whoever handles `axis_chosen`), then
        opens its Motion page - the same as clicking the primary drive's own item opens Item Configuration.
        Default label is `"Drive0N (SDR)"`, `N` = the real node ID (zero-padded like the primary's own default
        "Drive01") - `label` overrides it, for a caller with something more specific to call this axis. Inserted
        in ascending node-ID order among the other additional axes (never just appended after whichever one
        happened to connect last) - so axis 5 connected before axis 3 still ends up listed as 3, then 5."""
        item = QTreeWidgetItem([label or f"Drive{node_id:02d} (SDR)"])
        item.setIcon(0, icon("plug", 20))
        item.setData(0, Qt.ItemDataRole.UserRole, node_id)
        position = 1 + sum(1 for n in self._axis_items if n < node_id)   # 1 skips the primary's own item, index 0
        self.workspace.insertChild(position, item)
        self._axis_items[node_id] = item
        return item

    def remove_axis_item(self, node_id):
        item = self._axis_items.pop(node_id, None)
        if item is not None:
            self.workspace.removeChild(item)

    def set_axis_state(self, node_id, connected):
        item = self._axis_items.get(node_id)
        if item is not None:
            item.setIcon(0, icon("plug" if connected else "unplug", 20))

    def _tree_clicked(self, item, _column=0):
        if item is self.drive_item:
            self.drive_chosen.emit()
            return
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        if node_id is not None:
            self.axis_chosen.emit(node_id)

    def _toggle_collapsed(self):
        self._collapsed = not self._collapsed
        collapsed = self._collapsed
        self.setFixedWidth(COLLAPSED_WIDTH if collapsed else WIDTH)
        self.title.setVisible(not collapsed)
        self.tree.setVisible(not collapsed)
        self.collapse_btn.setIcon(icon("chevron_right" if collapsed else "chevron_left", 22))
        for key, (button, listing, title) in self._activities.items():
            button.setText("" if collapsed else title)
            set_prop(button, "collapsed", collapsed)     # theme: icon centred, no left padding (see theme.py)
        current = self.current_activity()
        listing = self._activities[current][1] if current else None
        self.tools_stack.setVisible(bool(listing and listing.count()) and not collapsed)
