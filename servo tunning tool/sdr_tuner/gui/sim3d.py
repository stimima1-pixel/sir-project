"""Drive Setup and Motion > 3D Simulation: a live OpenGL render of the loaded kinematics model's own bodies, each
moved by `kinematics.KinematicModel.body_transforms()` - the vendor's own Servo-Drive-IDE.exe has exactly this
feature (a docked "3D Simulation" panel under its own Motion Controller / PLC activity - confirmed directly in its
binary's own strings, `Servo tunning tool-original/`, a sibling folder the user pointed at directly - see
the README for how this was found and scoped).

A body's own mesh comes from the SAME-STEM `.obj` file next to whichever `.kinematics` file is loaded (e.g.
`radar.obj.kinematics` -> `radar.obj`), split into one sub-mesh per body by `mesh.py`'s own parser - group names
match a `.kinematics` file's own `Name` column exactly (confirmed directly against every bundled vendor file, see
mesh.py's own docstring). A body with no matching `.obj` file at all (this tool's own minimal `bench_node7`
kinematics rig, or any future model with no bundled mesh) falls back to a small placeholder sphere at that body's
own Axis Origin Point, rather than being silently omitted - still useful for confirming a model's own STRUCTURE
even without real geometry.

**LIVE mode** (the first of the three modes originally scoped for this page - see the README; the other two,
SIMULATION and CURSOR, followed the same day and are documented further down): each connected axis's own real
Work Zone Count (never requested - the same standing telemetry rule every other page here follows) is converted to
its kinematics unit (degrees/mm) via `session.counts_per_unit`, the exact same conversion `gcode_runner.py`'s own
`_current_joint_value` already does the other way. A body whose own actuated ancestors are NOT all connected stays
at whatever pose those ancestors' last known (or home, if never connected) values give it - the same "partial data
shown honestly, not guessed or hidden" approach `plotter_2d.py`'s own single-axis limitation and `axes_page.py`
already establish.

**The vendor's own `.obj` files are authored in METRES, but every `.kinematics` file (and kinematics.py's own
"Axis Origin Point(mm)"/"Tool Tip(mm)" columns, so `body_transforms()`'s own translations too) is in MILLIMETRES**
- confirmed directly, not assumed: `robot-dog.obj.kinematics`'s own `leg-1-1` row gives its Axis Origin Point as
(-350, 57, 0) mm; that SAME body's own mesh group in `robot-dog.obj` has its vertices centred almost exactly on
(-0.3815, 0.0783, 0.0002) - (-350, 57, 0) mm converted to metres. Applying a millimetre-scale transform straight to
metre-scale vertices would be harmless at the HOME pose (identity either way) but would swing a moving body through
a wildly wrong, hugely oversized arc the instant its own joint actually moved (the rotation's own pivot point would
sit ~1000x farther from the mesh than the mesh's own extent) - `MESH_SCALE_MM_PER_M` below converts every parsed
mesh's own vertices to millimetres once, right after loading, so from that point on this page works in ONE
consistent unit throughout, matching kinematics.py exactly.

**A second mode, SIMULATION, added 2026-09-24** (`Live`/`Simulation` radio buttons, matching the vendor's own
`radioButtonPlcSimulationAxes`/`radioButtonPlcSimulationSimulation` naming, found the same way the whole page
itself was): reads `session.virtual_axes` (`gcode_sim.VirtualAxis`, written by Motion Controller's own SIMULATE
button) instead of real telemetry - no drive, real or simulated, connected at all. Both modes share the exact
same rendering (`body_transforms()`, the same GLMeshItem set) - only where a tick's own `joint_values` dict comes
from differs.

**A third mode, CURSOR, added 2026-09-24** (the vendor's own `radioButtonPlcSimulationCursor` - the last of the
three this page was originally scoped for): a slider + a precise numeric field per actuated axis, in a scrollable
panel next to the view - drag or type a joint value directly and watch the model pose update, with no G-code
program and no drive of any kind involved. Purely local to this page (`self._cursor_values`, never touches
`session` at all - unlike Live/Simulation, nothing else needs to read a manually-scrubbed pose), preserved across
a mode switch (only "Reset to Home" or loading a DIFFERENT model clears it) so flipping back to Cursor shows the
same pose you left. Each axis's own range comes from its `.kinematics` file's own `Axis Limits` column when given
(most of `robot-dog.obj`/`humanoid.obj`'s joints have real ones); a body with none (`radar.obj`/`ko-bot.obj`, and
this project's own `bench_node7` rig, all give a blank Limits column) falls back to a generic default
(`DEFAULT_ROTARY_RANGE`/`DEFAULT_LINEAR_RANGE` below) rather than refusing to show a control at all - a
deliberately generous, documented guess, not a physically meaningful limit."""
import time
from pathlib import Path

import math
import numpy as np
from PySide6.QtGui import QVector3D
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .. import kinematics, mesh
from . import gcode_sim, theme
from .config_page import KINEMATICS_FILE_FILTER, KINEMATICS_SAMPLES_DIR
from .gcode_runner import axis_to_node
from .widgets import ComboBox, DoubleSpinBox, Slider, set_prop

REFRESH_MS = 100        # matches Plotter2DPage/RecorderPage's own telemetry-polling cadence
MESH_COLOR = (0.80, 0.63, 0.20, 1.0)      # bronze/gold, matches theme.GOLD's own tone
PLACEHOLDER_COLOR = (0.55, 0.55, 0.58, 0.85)
PLACEHOLDER_RADIUS_MM = 15.0
MESH_SCALE_MM_PER_M = 1000.0      # the vendor's own .obj files are in metres - see the module docstring
CURSOR_PANEL_WIDTH = 320
DEFAULT_ROTARY_RANGE = (-180.0, 180.0)    # a body with no Axis Limits of its own - see the module docstring
DEFAULT_LINEAR_RANGE = (-500.0, 500.0)    # no bundled file actually uses a linear axis, but the format allows one


class Sim3DPage(QWidget):
    def __init__(self, session):
        super().__init__()
        self.s = session
        self._model = None                  # the kinematics.KinematicModel currently rendered, or None
        self._items = {}                     # body id -> GLMeshItem, one per body in self._model.bodies
        self._axes_used = []                 # sorted axis indices self._model's bodies actually depend on
        self._home_camera = None             # (distance, elevation, azimuth) - what Reset View restores
        self._cursor_values = {}             # axis -> manually-set value (Cursor mode's own pose, local to this page)
        self._cursor_rows = {}               # axis -> (Slider, DoubleSpinBox), rebuilt whenever the model changes

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.open_btn = QPushButton("Open…")
        self.open_btn.setToolTip("Load a .kinematics model - the same file this page always rendered, now "
                                 "loadable from here too instead of only Item Configuration's own Kinematics "
                                 "Model group.")
        self.samples = ComboBox()
        self.samples.addItem("Samples…", None)
        for path in sorted(KINEMATICS_SAMPLES_DIR.glob("*.kinematics")):
            self.samples.addItem(path.stem, str(path))
        self.samples.setToolTip("Load one of the vendor's own bundled example models.")
        bar.addWidget(self.open_btn)
        bar.addWidget(self.samples)
        bar.addSpacing(10)
        self.reset_view_btn = QPushButton("Reset View")
        self.live_radio = QRadioButton("Live")
        self.live_radio.setChecked(True)
        self.live_radio.setToolTip("Driven by each connected axis's own real telemetry.")
        self.sim_radio = QRadioButton("Simulation")
        self.sim_radio.setToolTip("Driven by Motion Controller's own SIMULATE button - no drive, real or "
                                  "simulated, needed at all.")
        self.cursor_radio = QRadioButton("Cursor")
        self.cursor_radio.setToolTip("Manually pose the model - drag or type each axis's own value. No drive, no "
                                     "G-code program, needed at all.")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.live_radio)
        self.mode_group.addButton(self.sim_radio)
        self.mode_group.addButton(self.cursor_radio)
        self.status = QLabel("")
        self.status.setObjectName("ctlStatus")
        bar.addWidget(self.reset_view_btn)
        bar.addSpacing(10)
        bar.addWidget(self.live_radio)
        bar.addWidget(self.sim_radio)
        bar.addWidget(self.cursor_radio)
        bar.addStretch(1)
        bar.addWidget(self.status)
        outer.addLayout(bar)

        self.hint = QLabel("Open… or Samples… loads a .kinematics model right here (the same one Item "
                           "Configuration's own Kinematics Model group shows and loads too - either page keeps "
                           "the other in sync), together with its matching .obj mesh if one is bundled alongside "
                           "it. \"Counts per unit\" per axis is set on Item Configuration only. Live: each "
                           "connected axis moves its own body from real telemetry - nothing is requested from "
                           "the drive. Simulation: driven by Motion Controller's own SIMULATE button - no drive, "
                           "real or simulated, needed at all. Cursor: pose the model by hand, with the sliders "
                           "on the right.")
        self.hint.setProperty("muted", "true")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        view_row = QHBoxLayout()
        view_row.setSpacing(10)
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor(theme.BG)
        self.grid = gl.GLGridItem()
        self.view.addItem(self.grid)
        view_row.addWidget(self.view, 1)

        self.cursor_container = QWidget()
        self.cursor_container.setFixedWidth(CURSOR_PANEL_WIDTH)
        self.cursor_container.setVisible(False)
        cursor_outer = QVBoxLayout(self.cursor_container)
        cursor_outer.setContentsMargins(0, 0, 0, 0)
        cursor_outer.setSpacing(6)
        self.cursor_reset_btn = QPushButton("Reset to Home")
        cursor_outer.addWidget(self.cursor_reset_btn)
        cursor_scroll = QScrollArea()
        cursor_scroll.setObjectName("cursorScroll")
        cursor_scroll.setWidgetResizable(True)
        cursor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        cursor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cursor_scroll.setStyleSheet("QScrollArea#cursorScroll { background: transparent; border: 0; }"
                                    "QScrollArea#cursorScroll > QWidget > QWidget { background: transparent; }")
        cursor_body = QWidget()
        self.cursor_rows_layout = QVBoxLayout(cursor_body)
        self.cursor_rows_layout.setSpacing(10)
        cursor_scroll.setWidget(cursor_body)
        cursor_outer.addWidget(cursor_scroll, 1)
        view_row.addWidget(self.cursor_container)
        outer.addLayout(view_row, 1)

        self.open_btn.clicked.connect(self._open_kinematics_dialog)
        self.samples.activated.connect(self._load_kinematics_sample)
        self.reset_view_btn.clicked.connect(self.reset_view)
        self.cursor_reset_btn.clicked.connect(self._reset_cursor_to_home)
        self.mode_group.buttonClicked.connect(self._on_mode_changed)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(REFRESH_MS)
        session.kinematics_changed.connect(self._reload_model)
        self._reload_model()

    # ---- loading ---------------------------------------------------------------------------------
    def _open_kinematics_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Kinematics Model", "", KINEMATICS_FILE_FILTER)
        if path:
            self._load_kinematics(path)

    def _load_kinematics_sample(self, index):
        path = self.samples.itemData(index)
        self.samples.setCurrentIndex(0)
        if path is not None:
            self._load_kinematics(path)

    def _load_kinematics(self, path):
        """Loads straight through `session.load_kinematics` - the same call Item Configuration's own Kinematics
        Model group makes, so either page loading a model keeps both in sync via `kinematics_changed`
        (`_reload_model` below re-renders this page; Item Configuration's own summary/counts-per-unit rows
        update the same way already). Counts-per-unit itself stays Item-Configuration-only - it is a per-axis
        value this page has no editor for, not something loading a model here needs to set."""
        try:
            self.s.load_kinematics(path)
        except kinematics.KinematicsFileError as exc:
            self._say(f"Could not load {Path(path).name}: {exc}", "error")

    def _reload_model(self):
        """Rebuilds the whole scene from whatever `session.kinematics_model` currently is (or clears it, if
        None) - called once at startup and again every time a model is loaded/cleared, from this page's own
        Open…/Samples… above or from Item Configuration's own Kinematics Model group."""
        for item in self._items.values():
            self.view.removeItem(item)
        self._items = {}
        self._model = self.s.kinematics_model
        if self._model is None:
            self._axes_used = []
            self._rebuild_cursor_panel()
            self._home_camera = None
            self._reset_grid()
            self._say("No model loaded - use Open… or Samples… above.", "warn")
            return

        mesh_groups = self._load_matching_mesh(self._model.source)
        real_mesh_count = 0
        for body in self._model.bodies.values():
            body_mesh = mesh_groups.get(body.name)
            if body_mesh is not None:
                item = gl.GLMeshItem(meshdata=gl.MeshData(vertexes=body_mesh.vertices, faces=body_mesh.faces),
                                     color=MESH_COLOR, smooth=True, shader="shaded", glOptions="opaque")
                real_mesh_count += 1
            else:
                sphere = gl.MeshData.sphere(rows=8, cols=8, radius=PLACEHOLDER_RADIUS_MM)
                sphere.setVertexes(sphere.vertexes() + np.asarray(body.origin, dtype=float))   # the body's origin
                item = gl.GLMeshItem(meshdata=sphere, color=PLACEHOLDER_COLOR, smooth=True,      # baked into the
                                     shader="shaded", glOptions="opaque")                        # vertices, like a
                # real mesh's own: `_render`'s setTransform REPLACES an item's transform, so an `item.translate(
                # *body.origin)` here was wiped on the first render - every placeholder sat at the model's origin
            self.view.addItem(item)
            self._items[body.id] = item

        self._axes_used = sorted({b.axis for b in self._model.bodies.values() if b.axis is not None})
        self._rebuild_cursor_panel()
        self._frame_camera(mesh_groups)
        name = Path(self._model.source).name
        placeholders = len(self._model.bodies) - real_mesh_count
        note = f", {placeholders} without a bundled mesh (shown as a marker)" if placeholders else ""
        self._say(f"Loaded {name}: {len(self._model.bodies)} body/bodies{note}.", "ok")

    # ---- Cursor mode: a slider + field per axis, local to this page only -----------------------------
    def _body_for_axis(self, axis):
        for body in self._model.bodies.values():
            if body.axis == axis:
                return body
        return None

    def _rebuild_cursor_panel(self):
        """Rebuilds every joint row from scratch - called whenever the loaded model changes (a different model
        means different axes, so old rows/values are meaningless, the same reasoning session.py's own
        counts_per_unit/virtual_axes reset already uses for the OTHER two modes)."""
        while self.cursor_rows_layout.count():
            item = self.cursor_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cursor_rows = {}
        self._cursor_values = {}
        if self._model is None:
            return
        for axis in self._axes_used:
            body = self._body_for_axis(axis)
            axis_type = (body.axis_type if body else None) or "rotary"
            lo, hi = (body.limits if body else (None, None))
            if lo is None or hi is None:
                lo, hi = DEFAULT_LINEAR_RANGE if axis_type == "linear" else DEFAULT_ROTARY_RANGE
            unit = "mm" if axis_type == "linear" else "°"

            row = QHBoxLayout()
            row.setSpacing(6)
            label = QLabel(body.name if body else f"Axis {axis}")
            label.setMinimumWidth(90)
            label.setWordWrap(True)
            spin = DoubleSpinBox()
            spin.setDecimals(1)
            spin.setRange(lo, hi)
            spin.setSuffix(f" {unit}")
            spin.setKeyboardTracking(False)
            spin.setFixedWidth(90)
            slider = Slider(Qt.Orientation.Horizontal)
            slider.setRange(round(lo * 10), round(hi * 10))
            row.addWidget(label)
            row.addWidget(slider, 1)
            row.addWidget(spin)
            container = QWidget()
            container.setLayout(row)
            self.cursor_rows_layout.addWidget(container)
            self._cursor_rows[axis] = (slider, spin)
            self._cursor_values[axis] = 0.0

            def from_slider(value, axis=axis, spin=spin):
                v = value / 10.0
                spin.blockSignals(True)
                spin.setValue(v)
                spin.blockSignals(False)
                self._cursor_values[axis] = v

            def from_spin(value, axis=axis, slider=slider):
                slider.blockSignals(True)
                slider.setValue(round(value * 10))
                slider.blockSignals(False)
                self._cursor_values[axis] = value

            slider.valueChanged.connect(from_slider)
            spin.valueChanged.connect(from_spin)
        self.cursor_rows_layout.addStretch(1)

    def _reset_cursor_to_home(self):
        for slider, spin in self._cursor_rows.values():
            spin.setValue(0.0)
            slider.setValue(0)     # covers the (already-0) case where setValue(0.0) above emits no signal

    def _load_matching_mesh(self, kinematics_source):
        """{body name: mesh.Mesh} from the .obj file with the SAME STEM as the loaded .kinematics file (e.g.
        radar.obj.kinematics -> radar.obj), or {} if no such file exists (this tool's own bench_node7 rig, or any
        future model with no bundled mesh) - reported via the placeholder markers above, not an error, since a
        kinematics model is fully usable (for IK, for this page's own placeholder view) with no mesh at all.
        Vertices are converted metres -> millimetres here, once, right after parsing - see the module docstring."""
        if kinematics_source in ("<string>",):
            return {}
        obj_path = Path(kinematics_source).with_suffix("")
        if not obj_path.exists():
            return {}
        try:
            groups = mesh.parse_obj_file(obj_path)
        except mesh.MeshFileError:
            return {}
        return {name: m._replace(vertices=m.vertices * MESH_SCALE_MM_PER_M) for name, m in groups.items()}

    # ---- camera -----------------------------------------------------------------------------------
    def _frame_camera(self, mesh_groups):
        """A starting camera distance from the loaded model's own bounding box (its real mesh vertices, already
        in mm - if any exist, else every body's own Axis Origin Point, mm too) - far enough to see the whole
        thing, not an arbitrary fixed distance that would swallow a small model or clip a large one. The floor
        (10 mm) only guards a degenerate single-point model against a zero-distance camera - it must NOT be
        anywhere near a plausible model's own real span, or it silently overrides the real framing instead
        (exactly the bug a metres-scale span - under 1 "unit" - walked straight into before the mm conversion
        above existed)."""
        points = [m.vertices for m in mesh_groups.values()]
        if not points:
            points = [np.array([b.origin for b in self._model.bodies.values()], dtype=float)]
        all_points = np.concatenate(points) if points else np.zeros((1, 3))
        lo, hi = all_points.min(axis=0), all_points.max(axis=0)
        centre = (lo + hi) / 2.0
        span = float(np.linalg.norm(hi - lo)) if len(all_points) else 200.0
        distance = max(span * 1.5, 10.0)
        # The camera looks AT the model's own centre - setCameraPosition(distance=...) alone left it aimed at the
        # origin, so a model whose box is off-centre (radar's is at (-56, 25, -115) mm) sat low and to one side
        # (2026-09-25, user: "model is also not showing in middle").
        self._home_camera = (QVector3D(*(float(v) for v in centre)), distance, 30.0, 45.0)
        self.reset_view()
        self._fit_grid(lo, hi, centre)

    def _fit_grid(self, lo, hi, centre):
        """The floor grid sized to the model and laid under it. pyqtgraph's default grid is 20 x 20 units of 1 -
        20 mm here - which is a speck once the camera stands ~700 mm back from a model, so the grid seemed to
        vanish the moment a model loaded (2026-09-25, user: "white base ... get vanished"). Cells are a round
        1/2/5 x 10^n step, about a tenth of the model's largest side; the grid spans ~2.5x the model and sits at
        its lowest point, centred under it."""
        extent = max(float((hi - lo).max()), 10.0)
        spacing = self._nice_step(extent / 10.0)
        cells = max(2, math.ceil(extent * 2.5 / (2 * spacing)))       # whole cells each side of the centre
        size = 2 * cells * spacing
        self.grid.setSize(x=size, y=size)
        self.grid.setSpacing(x=spacing, y=spacing)
        self.grid.resetTransform()
        self.grid.translate(float(centre[0]), float(centre[1]), float(lo[2]))

    def _reset_grid(self):
        self.grid.setSize(x=20, y=20)
        self.grid.setSpacing(x=1, y=1)
        self.grid.resetTransform()

    @staticmethod
    def _nice_step(x):
        """The smallest of 1, 2, 5 x 10^n that is >= x."""
        if x <= 0:
            return 1.0
        power = 10.0 ** math.floor(math.log10(x))
        for m in (1.0, 2.0, 5.0, 10.0):
            if m * power >= x - 1e-9:
                return m * power
        return 10.0 * power

    def reset_view(self):
        if self._home_camera is not None:
            centre, distance, elevation, azimuth = self._home_camera
            self.view.setCameraPosition(pos=centre, distance=distance, elevation=elevation, azimuth=azimuth)

    def _on_mode_changed(self, _btn):
        self.cursor_container.setVisible(self.cursor_radio.isChecked())
        self._tick()

    # ---- ticking: LIVE (real telemetry), SIMULATION (session.virtual_axes) or CURSOR (local) ----------
    def _tick(self):
        if self._model is None:
            return
        if self.sim_radio.isChecked():
            self._tick_simulation()
        elif self.cursor_radio.isChecked():
            self._tick_cursor()
        else:
            self._tick_live()

    def _render(self, joint_values):
        transforms = self._model.body_transforms(joint_values)
        for body_id, item in self._items.items():
            transform = transforms.get(body_id)
            if transform is not None:
                item.setTransform(pg.Transform3D(transform.tolist()))

    def _tick_live(self):
        clients = self.s.all_clients()
        joint_values = {}
        connected = 0
        for axis in self._axes_used:
            client = clients.get(axis_to_node(axis))
            if client is None:
                continue
            snap = client.telemetry_snapshot()
            if "wz_count" not in snap:
                continue
            cpu = self.s.counts_per_unit.get(axis, 1.0)
            joint_values[axis] = snap["wz_count"] / cpu if cpu else 0.0
            connected += 1
        self._render(joint_values)
        total = len(self._axes_used)
        if total == 0:
            self._say("This model has no actuated axes.", "warn")
        elif connected == 0:
            self._say(f"Live: 0 of {total} axis(es) connected - showing the home pose.", "warn")
        else:
            self._say(f"Live: {connected} of {total} axis(es) connected.", "ok")

    def _tick_simulation(self):
        now = time.monotonic()
        joint_values = {axis: gcode_sim.axis_value_now(state, now) for axis, state in self.s.virtual_axes.items()}
        self._render(joint_values)
        if not self.s.virtual_axes:
            self._say("Simulation: use Motion Controller's own SIMULATE button to preview a program here.", "warn")
        else:
            spinning = sum(1 for state in self.s.virtual_axes.values() if state.rate_per_sec != 0.0)
            note = f" ({spinning} spinning)" if spinning else ""
            self._say(f"Simulation: {len(self.s.virtual_axes)} axis(es) with a commanded value{note}.", "ok")

    def _tick_cursor(self):
        self._render(self._cursor_values)
        total = len(self._axes_used)
        if total == 0:
            self._say("This model has no actuated axes.", "warn")
        else:
            self._say(f"Cursor: manually posed ({total} axis(es)).", "ok")

    def _say(self, text, state):
        self.status.setText(text)
        set_prop(self.status, "state", state)
