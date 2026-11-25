"""Parameters Comparison - load two saved parameter snapshots (Parameters > Export > Save Snapshot writes them)
and see what differs, e.g. a drive's own settings before/after a tuning pass, or two drives' configurations
side by side. Matches Elmo Application Studio III's own Parameters Comparison tool (checked directly 2026-09-24:
File 1 / File 2 pickers, a Compare button, a table of differences) - ours is file-based like theirs rather than
a live drive-to-drive read, which would need two drives connected at once."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import export as export_module
from . import theme
from .widgets import make_button, mono_font, set_prop

COLUMNS = ("Name", "Address", "File 1", "File 2")


def _fmt(value):
    if value is None:
        return "(not in file)"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


class ParametersComparisonDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parameters Comparison")
        self.resize(760, 520)
        self._snap_a = self._snap_b = None

        outer = QVBoxLayout(self)
        self.path_a = self._file_row(outer, "File 1:")
        self.path_b = self._file_row(outer, "File 2:")

        controls = QHBoxLayout()
        self.compare_btn = make_button(QPushButton("Compare"), "primary")
        self.compare_btn.setEnabled(False)
        self.compare_btn.clicked.connect(self._compare)
        controls.addWidget(self.compare_btn)
        self.diffs_only = QCheckBox("Differences only")
        self.diffs_only.setChecked(True)
        self.diffs_only.toggled.connect(self._compare)
        controls.addWidget(self.diffs_only)
        controls.addStretch(1)
        self.summary = QLabel("")
        set_prop(self.summary, "muted", "true")
        controls.addWidget(self.summary)
        outer.addLayout(controls)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setFont(mono_font(12))
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setHighlightSections(False)
        outer.addWidget(self.table, 1)

        close_btn = make_button(QPushButton("Close"), "primary")
        close_btn.clicked.connect(self.accept)
        outer.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

    def _file_row(self, outer, label):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        path_edit = QLineEdit()
        path_edit.setReadOnly(True)
        row.addWidget(path_edit, 1)
        load_btn = QPushButton("Load…")
        load_btn.clicked.connect(lambda: self._load(path_edit))
        row.addWidget(load_btn)
        outer.addLayout(row)
        return path_edit

    def _load(self, path_edit):
        path, _ = QFileDialog.getOpenFileName(self, "Load Parameter Snapshot", "", "Snapshot files (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                snap = export_module.parse_snapshot(fh.read())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Load Snapshot", f"Could not load {path}: {exc}")
            return
        path_edit.setText(path)
        if path_edit is self.path_a:
            self._snap_a = snap
        else:
            self._snap_b = snap
        self.compare_btn.setEnabled(self._snap_a is not None and self._snap_b is not None)

    def _compare(self):
        if self._snap_a is None or self._snap_b is None:
            return
        all_rows = export_module.diff_snapshots(self._snap_a, self._snap_b)
        differing = sum(1 for r in all_rows if r.differs)
        self.summary.setText(f"{differing} of {len(all_rows)} parameter(s) differ")
        rows = [r for r in all_rows if r.differs] if self.diffs_only.isChecked() else all_rows
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            values = (r.name, f"0x{r.index:04X}:0x{r.sub:02X}", _fmt(r.value_a), _fmt(r.value_b))
            colour = QColor(theme.RUBY) if r.differs else self.table.palette().text()
            for col, text in enumerate(values):
                item = self.table.item(i, col)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(i, col, item)
                item.setText(text)
                item.setForeground(colour if col >= 2 else self.table.palette().text())
