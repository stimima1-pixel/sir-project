"""The Axes page's setup panel: Additional Axes, Kinematics Model and Tool Pose (Orientation IK) - the three
multi-axis groups that used to sit on Item Configuration (System Configuration). Moved here on 2026-09-26 (user:
"move three parameters - additional axis, kinematics model, tool pose - from system configuration to motion / axis
(on top) and show axis and other parameters below it"): they are about the set of axes and what moves them together,
which is the Motion activity's business, and the Axes page shows every connected axis right below them. The code is
the same as before, verbatim; only its home changed. Item Configuration keeps what is about ONE drive."""
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import kinematics
from .config_page import KINEMATICS_FILE_FILTER, KINEMATICS_SAMPLES_DIR, PropertyGroup
from .gcode_runner import GcodeRunner
from .session import USB
from .widgets import ComboBox, DoubleSpinBox, SpinBox, make_button


class AxesSetupPanel(QWidget):
    message = Signal(str, str)      # (text, level) for the window's status bar

    def __init__(self, session, drive_tab_for, connection_of, parent=None):
        """`connection_of() -> (channel, bit rate)` of the primary connection (Item Configuration's own Target
        Connection fields): an additional axis joins the SAME bus. `drive_tab_for` as everywhere: the runner's way
        to reach each axis's own Motion page."""
        super().__init__(parent)
        self.s = session
        self._connection_of = connection_of
        self.runner = GcodeRunner(session, drive_tab_for)     # for the Tool Pose group's own Orientation IK "GO"
        # two columns (user, 2026-09-26: "rearrange tool pose in red marked area" - the right half of the page):
        # Additional Axes over Kinematics Model on the left, Tool Pose beside them on the right
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        left, right = QVBoxLayout(), QVBoxLayout()
        for column in (left, right):
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(0)
        self.axes_group = self._build_axes_group(session)
        left.addWidget(self.axes_group)
        self.kinematics_group = self._build_kinematics_group(session)
        left.addWidget(self.kinematics_group)
        left.addStretch(1)
        self.pose_group = self._build_pose_group(session)
        right.addWidget(self.pose_group)
        right.addStretch(1)
        row.addLayout(left, 1)
        row.addLayout(right, 1)
        session.kinematics_changed.connect(self._refresh_kinematics_display)

    # ---- additional axes (real multi-axis - see the README's Multi-axis section) -----------------
    def _build_axes_group(self, session):
        """Adds a drive on the SAME CAN bus as the primary's Target Connection, alongside it - not instead of it - so one
        G-code program can coordinate several drives together (`Session.connect_axis`). The primary connection is
        untouched by anything here. The drive's own dedicated USB cable reaches exactly one drive, so this whole
        group is disabled outright on that interface - a shared CAN adapter (PCAN, Kvaser, CANable, SocketCAN,
        Vector) is needed to add more than one axis."""
        group = PropertyGroup("Additional Axes")
        group.add_note("Add another servo drive on the same CAN bus as the primary connection (Item Configuration's Target Connection), so this tool can "
                       "coordinate several axes together (a G-code program addressing more than one node ID). Needs "
                       "a shared CAN adapter - the drive's own USB cable reaches exactly one drive.")
        add_frame = QFrame()
        add_frame.setObjectName("gridRow")
        add_row = QHBoxLayout(add_frame)
        add_row.setContentsMargins(12, 6, 12, 8)
        add_row.setSpacing(10)
        self.new_axis_node = SpinBox()
        self.new_axis_node.setRange(1, 127)
        self.new_axis_node.setValue(2)
        self.new_axis_node.setToolTip("Node ID of the additional servo drive to add (1..127)")
        self.add_axis_btn = QPushButton("Add Axis")
        add_row.addWidget(QLabel("Node ID"))
        add_row.addWidget(self.new_axis_node)
        add_row.addWidget(self.add_axis_btn)
        add_row.addStretch(1)
        group.rows_layout.addWidget(add_frame)

        self.axes_list = QFrame()
        self.axes_list_layout = QVBoxLayout(self.axes_list)
        self.axes_list_layout.setContentsMargins(0, 0, 0, 0)
        self.axes_list_layout.setSpacing(0)
        group.rows_layout.addWidget(self.axes_list)
        self._axis_rows = {}       # node id -> the QFrame row shown for it

        self.add_axis_btn.clicked.connect(self._add_axis)
        session.connected.connect(self._sync_axes_group)
        session.disconnected.connect(self._sync_axes_group)
        session.axis_connected.connect(self._on_axis_connected)
        session.axis_disconnected.connect(self._on_axis_disconnected)
        session.axis_error.connect(self._on_axis_error)
        self._sync_axes_group()
        return group

    def _sync_axes_group(self):
        can_add = self.s.is_connected and self.s.interface != USB
        self.new_axis_node.setEnabled(can_add)
        self.add_axis_btn.setEnabled(can_add)
        if not self.s.is_connected:
            self.add_axis_btn.setToolTip("Connect the first drive before adding another axis.")
        elif self.s.interface == USB:
            self.add_axis_btn.setToolTip("The drive's own USB cable reaches exactly one drive.")
        else:
            self.add_axis_btn.setToolTip("")
        if not self.s.is_connected:
            for row in list(self._axis_rows.values()):
                self.axes_list_layout.removeWidget(row)
                row.setParent(None)
                row.deleteLater()
            self._axis_rows = {}

    def _add_axis(self):
        channel, bitrate = self._connection_of()                 # the primary connection's own, stripped like _connect
        self.s.connect_axis(self.s.interface, channel, bitrate, self.new_axis_node.value())   # does (the shared-bus hub is
                                                                                              # keyed by channel)

    def _on_axis_connected(self, node_id):
        """A plain name + Remove row - this axis's own full identity (Device Type, serials, Connection State,
        Product Activation, Networks) now lives on its own dedicated `AxisConfigurationPage` instead (2026-09-23:
        after two rounds - expanding a row in place, then making a tree click land on it - the user asked for the
        whole page, not a shared one with an inline detail block: "when drive 2 is selected then every single
        information showing must be about drive 2 only"). This list is now purely for adding/removing axes; see
        `main_window.py`'s `_on_axis_connected` for the actual per-axis config page."""
        row = QFrame()
        row.setObjectName("gridRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(12, 3, 12, 3)
        label = QLabel(f"Drive{node_id:02d} (SDR)")
        label.setObjectName("gridKey")
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(lambda: self.s.disconnect_axis(node_id))
        line.addWidget(label, 1)
        line.addWidget(remove_btn)
        self.axes_list_layout.addWidget(row)
        self._axis_rows[node_id] = row

    def _on_axis_disconnected(self, node_id):
        row = self._axis_rows.pop(node_id, None)
        if row is not None:
            self.axes_list_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()

    def _on_axis_error(self, node_id, message):
        self.message.emit(f"Axis {node_id}: {message}", "error")

    # ---- kinematics model (Inverse Kinematics for a G-code Txx - see the README's Multi-axis section) -----------
    def _build_kinematics_group(self, session):
        """Loads a .kinematics model (sdr_tuner.kinematics's own vendor CSV format) - what a G-code `Txx` line
        solves against (gcode_runner.py). Config-driven on purpose: this tool has no robot geometry of its own,
        so the model - and the raw-counts-per-unit conversion the drive itself needs (see kinematics.py's own
        docstring: the file's units are mm/degrees, this drive's own unit is a raw Work Zone Count) - both come
        from what is loaded here, never guessed."""
        group = PropertyGroup("Kinematics Model")
        group.add_note("Loads a .kinematics model (the vendor's own file format) - what a G-code Txx line solves "
                       "Inverse Kinematics against. \"Counts per unit\" converts the model's own units (degrees "
                       "for a rotary joint, mm for a linear one) into this drive's raw Work Zone Count - there is "
                       "no way to derive it automatically, so it defaults to 1.0 until you set it per axis.")
        row = QFrame()
        row.setObjectName("gridRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 6, 12, 8)
        row_layout.setSpacing(10)
        self.kinematics_open_btn = QPushButton("Open…")
        self.kinematics_samples = ComboBox()
        self.kinematics_samples.addItem("Samples…", None)
        for path in sorted(KINEMATICS_SAMPLES_DIR.glob("*.kinematics")):
            self.kinematics_samples.addItem(path.stem, str(path))
        self.kinematics_samples.setToolTip("Load one of the vendor's own bundled example models.")
        row_layout.addWidget(self.kinematics_open_btn)
        row_layout.addWidget(self.kinematics_samples)
        row_layout.addStretch(1)
        group.rows_layout.addWidget(row)

        self.kinematics_summary = QLabel("No model loaded.")
        self.kinematics_summary.setWordWrap(True)
        group.add_row("Model", self.kinematics_summary)

        self.kinematics_axes = QFrame()
        self.kinematics_axes_layout = QVBoxLayout(self.kinematics_axes)
        self.kinematics_axes_layout.setContentsMargins(0, 0, 0, 0)
        self.kinematics_axes_layout.setSpacing(0)
        group.rows_layout.addWidget(self.kinematics_axes)
        self._counts_per_unit_rows = {}     # axis -> DoubleSpinBox (what a test or the session reads)
        self._counts_per_unit_frames = []   # the row frames themselves - what a rebuild has to remove (a reload used to
                                            # remove only the spin boxes and leave the label rows behind, so loading
                                            # the same model three times showed nine "Axis N" labels: user, 2026-09-26)

        self.kinematics_open_btn.clicked.connect(self._open_kinematics_dialog)
        self.kinematics_samples.activated.connect(self._load_kinematics_sample)
        return group

    def _open_kinematics_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Kinematics Model", "", KINEMATICS_FILE_FILTER)
        if path:
            self._load_kinematics(path)

    def _load_kinematics_sample(self, index):
        path = self.kinematics_samples.itemData(index)
        self.kinematics_samples.setCurrentIndex(0)
        if path is not None:
            self._load_kinematics(path)

    def _load_kinematics(self, path):
        try:
            self.s.load_kinematics(path)
        except kinematics.KinematicsFileError as exc:
            self.message.emit(f"Could not load {Path(path).name}: {exc}", "error")

    def _refresh_kinematics_display(self):
        """Rebuilds this page's own kinematics-derived display (Model summary, counts-per-unit rows, Tool Pose's
        own Tool list) from whatever `session.kinematics_model` currently is - connected to `kinematics_changed`
        rather than called only from `_load_kinematics` above, so a model loaded from 3D Simulation's own
        Open…/Samples… (`sim3d.py`, 2026-09-24) - or any future second place a model can be loaded from - updates
        this page too, instead of leaving its summary/counts-per-unit/Tool list silently stale."""
        model = self.s.kinematics_model
        if model is None:
            self.kinematics_summary.setText("No model loaded.")
            self._rebuild_counts_per_unit([])
            self._rebuild_pose_tools({})
            return
        chains = model.chains()
        tools = ", ".join(f"T{n}" for n in sorted(chains))
        actuated = sum(1 for b in model.bodies.values() if b.axis is not None)
        name = Path(model.source).name
        self.kinematics_summary.setText(f"{name} - {len(model.bodies)} bodies, {actuated} axes, "
                                        f"{len(chains)} tool(s): {tools or 'none'}")
        self._rebuild_counts_per_unit(sorted({axis for chain in chains.values() for axis in chain.axes}))
        self._rebuild_pose_tools(chains)
        self.message.emit(f"Loaded kinematics model: {name}", "ok")

    def _rebuild_counts_per_unit(self, axes):
        for row in self._counts_per_unit_frames:
            self.kinematics_axes_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._counts_per_unit_rows, self._counts_per_unit_frames = {}, []
        for axis in axes:
            row = QFrame()
            row.setObjectName("gridRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(12, 3, 12, 3)
            label = QLabel(f"Axis {axis} (node {axis + 1}) - counts per unit")     # node ID = axis + 1 - see
                                                                                    # gcode_runner.py's own docstring
            label.setObjectName("gridKey")
            spin = DoubleSpinBox()
            spin.setRange(0.001, 1_000_000.0)
            spin.setDecimals(3)
            spin.setValue(self.s.counts_per_unit.get(axis, 1.0))
            spin.valueChanged.connect(lambda value, a=axis: self.s.counts_per_unit.__setitem__(a, value))
            line.addWidget(label, 1)
            line.addWidget(spin)
            self.kinematics_axes_layout.addWidget(row)
            self._counts_per_unit_rows[axis] = spin
            self._counts_per_unit_frames.append(row)

    # ---- tool pose (full-pose Inverse Kinematics - 2026-09-24) --------------------------------------------------
    def _build_pose_group(self, session):
        """A "Tool Pose" control for FULL-POSE (position + orientation together) Inverse Kinematics - the GUI's own
        way of giving `kinematics.solve_pose_ik` a target, since no G-code command can express an orientation at
        all (see kinematics.py's own module docstring: G-code's own move commands only ever carry X/Y/Z). Solves
        against the SAME loaded Kinematics Model above; GO dispatches the result through `GcodeRunner.move_to_pose`
        - the exact same multi-axis mechanism a G-code Txx move already uses (`_run_chain`/`_dispatch_value`) - and
        only when pressed, never automatically."""
        group = PropertyGroup("Tool Pose (Orientation IK)")
        group.add_note("Solves a FULL pose (position AND orientation together) against the Kinematics Model above, "
                       "and moves every joint of the chosen tool's own chain to match. Roll/Pitch/Yaw are degrees, "
                       "applied yaw then pitch then roll (the usual convention) - see kinematics.euler_to_matrix.")
        self.pose_tool = ComboBox()
        self.pose_tool.addItem("No model loaded", None)
        self.pose_tool.setEnabled(False)
        group.add_row("Tool", self.pose_tool)

        def spin():
            box = DoubleSpinBox()
            box.setRange(-1_000_000.0, 1_000_000.0)
            box.setDecimals(2)
            return box

        self.pose_x, self.pose_y, self.pose_z = spin(), spin(), spin()
        group.add_row("Target Position X (mm)", self.pose_x)
        group.add_row("Target Position Y (mm)", self.pose_y)
        group.add_row("Target Position Z (mm)", self.pose_z)
        self.pose_roll, self.pose_pitch, self.pose_yaw = spin(), spin(), spin()
        for box in (self.pose_roll, self.pose_pitch, self.pose_yaw):
            box.setRange(-360.0, 360.0)
        group.add_row("Target Roll (deg)", self.pose_roll)
        group.add_row("Target Pitch (deg)", self.pose_pitch)
        group.add_row("Target Yaw (deg)", self.pose_yaw)

        row = QFrame()
        row.setObjectName("gridRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 6, 12, 8)
        self.pose_go_btn = make_button(QPushButton("GO"), "primary")
        row_layout.addWidget(self.pose_go_btn)
        row_layout.addStretch(1)
        group.rows_layout.addWidget(row)

        self.pose_status = QLabel("")
        self.pose_status.setWordWrap(True)
        group.add_row("Status", self.pose_status)

        self.pose_go_btn.clicked.connect(self._solve_and_go_to_pose)
        return group

    def _rebuild_pose_tools(self, chains):
        """Refreshes the Tool combo whenever a (different) Kinematics Model loads - `chains` is that model's own
        {tool number: Chain}, exactly what `_load_kinematics` already computed."""
        self.pose_tool.clear()
        if not chains:
            self.pose_tool.addItem("No tools in this model", None)
            self.pose_tool.setEnabled(False)
            return
        for number in sorted(chains):
            self.pose_tool.addItem(f"T{number} - {chains[number].name}", number)
        self.pose_tool.setEnabled(True)

    def _solve_and_go_to_pose(self):
        def done(text, state):
            self.pose_status.setText(text)
            self.message.emit(text, state)

        tool = self.pose_tool.currentData()
        if tool is None:
            done("No kinematics model loaded - see the Kinematics Model group above.", "warn")
            return
        target_position = (self.pose_x.value(), self.pose_y.value(), self.pose_z.value())
        target_rotation = kinematics.euler_to_matrix(self.pose_roll.value(), self.pose_pitch.value(), self.pose_yaw.value())
        self.runner.move_to_pose(tool, target_position, target_rotation, self.pose_go_btn, done)

