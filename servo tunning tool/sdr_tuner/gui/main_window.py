"""The studio window: the ribbon on top, the workspace navigator on the left, the open page in the middle and a status
bar at the bottom - laid out like Elmo Application Studio III, in this tool's own black and gold."""
from collections import namedtuple

from PySide6.QtCore import QSettings, Qt, QThreadPool
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QVBoxLayout, QWidget)

from .. import APP_TITLE, export as export_module, usb
from .axes_page import AxesStatusPage
from .config_page import AxisConfigurationPage, ItemConfigurationPage
from .dock_workspace import DockWorkspace
from .workers import run_async
from .drive_modes import DriveModesPage
from .drive_tab import DriveTab
from .log_tab import LogTab
from .motion_controller import MotionControllerPage
from .motor_constants_dialog import MotorConstantsDialog
from .multi_axis_motion import MultiAxisMotionPage
from .navigator import Navigator
from .params_comparison_dialog import ParametersComparisonDialog
from .params_tab import ParamsTab
from .peripherals import PeripheralsPage
from .plotter_2d import Plotter2DPage
from .recorder import RecorderPage
from .ribbon import Ribbon
from .session import DEFAULT_CHANNEL, INTERFACES, SEARCH_PORTS, SIMULATOR, USB, Session
from .shell import BottomBar, PageHost
from .sim3d import Sim3DPage
from .status_monitor import StatusMonitorPage
from .tuner import TunerPage
from .widgets import set_prop

Tool = namedtuple("Tool", "activity title crumb glyph page ribbon")     # a page the user can open, and where it belongs
DOCKABLE_ACTIVITIES = ("drive", "motion")     # Servo Drive and Motion share ONE docked workspace (System
                                                # Configuration always stays a single page)
PRIMARY_TOOL_KEY = {"quick": "quick_tuning", "advanced": "advanced_tuning", "motion": "motion", "modes": "drive_modes",
                    "recorder": "recorder", "parameters": "parameters", "telemetry": "telemetry", "peripherals": "peripherals",
                    "config": "config"}
                    # an additional axis's own page "kind" -> the PRIMARY drive's own tool key of the same kind (not
                    # always identical - quick/advanced tuning's own keys carry a "_tuning" suffix the axis-scoped
                    # kind name does not) - used to fall back to the right primary page when an axis disconnects.
                    # "config" is not one of the Servo Drive list's own retargetable rows (Item
                    # Configuration is reached by clicking the drive's own tree item, not that shared list) - its
                    # entry here is only for _on_axis_disconnected's fallback; retarget_drive_tools itself ignores
                    # any kind that is not one of that list's own tagged rows, so the extra key is harmless there


class MainWindow(QMainWindow):
    def __init__(self, startup_progress=None):
        super().__init__()
        report = startup_progress or (lambda *_: None)
        self.setWindowTitle(APP_TITLE)
        screen = QGuiApplication.primaryScreen()
        room = screen.availableGeometry() if screen is not None else None     # a laptop screen is shorter than 900 pixels
        self.resize(min(1360, room.width() - 40) if room else 1360, min(900, room.height() - 40) if room else 900)
        self.setMinimumSize(1120, 600)
        self.session = Session(self)

        report(0.14, "Preparing device configuration…")
        self.config_page = ItemConfigurationPage(self.session)
        self.iface, self.channel = self.config_page.iface, self.config_page.channel
        self.bitrate, self.node = self.config_page.bitrate, self.config_page.node
        report(0.32, "Building drive controls…")
        self.drive_tab = DriveTab(self.session)
        self.drive_modes_page = DriveModesPage(self.session, self.drive_tab)
        self.drive_tab.modes = self.drive_modes_page
        self.params_tab = ParamsTab(self.session)
        self.telemetry_tab = ParamsTab(self.session, telemetry=True)
        report(0.58, "Preparing monitoring tools…")
        self.peripherals_page = PeripheralsPage(self.session)
        self.log_tab = LogTab(self.session, self._drive_tab_for)
        self.motion_controller_page = MotionControllerPage(self.session, self._drive_tab_for)
        self.axes_page = AxesStatusPage(self.session, self._target_name, self._drive_tab_for,
                                        lambda: (self.channel.text().strip(), self.bitrate.currentData()))
        self.plotter_2d_page = Plotter2DPage(self.session, self._target_name)
        self.status_monitor_page = StatusMonitorPage(self.session, self._target_name)
        self.multi_axis_motion_page = MultiAxisMotionPage(self.session, self._target_name)
        self.sim3d_page = Sim3DPage(self.session)
        self.recorder_page = RecorderPage(self.session, self.drive_tab)
        self.quick_tuner = TunerPage(self.session)
        self.advanced_tuner = TunerPage(self.session, advanced=True)
        report(0.82, "Arranging the workspace…")
        self.pill = QLabel()
        self.pill.setObjectName("pill")
        self.pill.setMinimumWidth(190)
        self.pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tools = {
            "config": Tool("system", "Item Configuration", "", "gear", self.config_page, "system"),
            "quick_tuning": Tool("drive", "Quick Tuning", "", "wand", self.quick_tuner, "quick"),
            "advanced_tuning": Tool("drive", "Advanced Tuning", "", "wrench", self.advanced_tuner, "advanced"),
            "motion": Tool("drive", "Single Axis Motion", "", "motion", self.drive_tab, "tools"),
            "drive_modes": Tool("drive", "Drive Modes", "Torque, cyclic position, field, Kickstart, generator, brushed, brake",
                                "sliders", self.drive_modes_page, "tools"),
            "recorder": Tool("drive", "Recorder", "Chart #1 / Chart #2", "chart", self.recorder_page, "recording"),
            "parameters": Tool("drive", "Parameters Explorer", "Configuration", "list", self.params_tab, "parameters"),
            "telemetry": Tool("drive", "Telemetry Explorer", "Read-only values", "monitor", self.telemetry_tab, "parameters"),
            "terminal": Tool("motion", "Terminal", "CAN traffic", "terminal", self.log_tab, "tools"),
            "motion_controller": Tool("motion", "Motion Controller", "G-code programs", "code", self.motion_controller_page, "tools"),
            "sim3d": Tool("motion", "3D Simulation", "Live, from the connected axes' own telemetry", "cube",
                          self.sim3d_page, "tools"),
            "plotter_2d": Tool("motion", "2D Plotter", "Two axes, traced against each other", "scatter",
                               self.plotter_2d_page, "tools"),
            "peripherals": Tool("drive", "Peripherals", "RC PWM, GPIO, Thermistor, Inverter", "drive", self.peripherals_page, "tools"),
            "axes": Tool("motion", "Axes", "Every connected axis", "grid", self.axes_page, "tools"),
            "status_monitor": Tool("motion", "Status Monitor", "Pick any signal, on any axis", "monitor",
                                   self.status_monitor_page, "tools"),
            "multi_axis_motion": Tool("motion", "Multi Axis Motion", "Move several axes, no G-code", "grid",
                                      self.multi_axis_motion_page, "tools"),
        }
        self._last_tool = {"system": "config", "drive": "motion", "motion": "motion_controller"}
        self._current_key = None            # the tool key of whichever page is actually shown right now
        self.dock_workspace = DockWorkspace()      # Servo Drive's own dockable multi-pane surface -
                                                    # opt-in via the ribbon's own Docked View toggle, see
                                                    # open_tool()/_show_docked_tool(); every other activity keeps
                                                    # using self.host's own single-page QStackedWidget, untouched
        self._docked_mode = False
        self._interface_label = None        # the interface the channel field was last reset for - see _on_interface_changed
        self._axis_probe = {}               # node id -> the last `probed` result of that additional axis (True until known)
        self._scoped_axis = None            # the node id the Servo Drive list's per-drive rows currently point
                                             # at (retarget_drive_tools), None for the primary's own - so removing that
                                             # axis can point them back whether or not one of its pages is shown
        self._primary_pill = ("DISCONNECTED", "")   # the primary's own connection text/state, shown at the bottom
                                             # right whenever a primary/shared page is open - remembered separately
                                             # from what the pill is CURRENTLY showing, since an axis's own page
                                             # stays on ITS OWN text regardless of what the primary is doing
        self._axis_pages = {}       # node id -> {"motion": DriveTab, "parameters": ParamsTab, "telemetry": ParamsTab},
                                     # one set per ADDITIONAL axis (built as each one connects - see
                                     # _on_axis_connected; real multi-axis, the README's Multi-axis section)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.ribbon = Ribbon()
        self.navigator = Navigator()
        self.host = PageHost()
        self.bottom = BottomBar(self.pill)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.navigator)
        body.addWidget(self.host, 1)
        layout.addWidget(self.ribbon)
        layout.addLayout(body, 1)
        layout.addWidget(self.bottom)
        for tool in self.tools.values():
            self.host.stack.addWidget(tool.page)
        self.host.stack.addWidget(self.dock_workspace)

        self._build_ribbon()
        self._build_navigator()
        self.ribbon.stop_button.clicked.connect(self.emergency_stop)
        self.config_page.message.connect(self.show_status)
        self.axes_page.message.connect(self.show_status)
        self.peripherals_page.message.connect(self.show_status)
        self.motion_controller_page.message.connect(self.show_status)
        self.plotter_2d_page.message.connect(self.show_status)
        self.params_tab.message.connect(self.show_status)
        self.telemetry_tab.message.connect(self.show_status)
        self.recorder_page.message.connect(self.show_status)
        self.recorder_page.buttons_changed.connect(self._sync_recorder_buttons)
        self._sync_recorder_buttons()
        self.drive_tab.stop_requested.connect(self._stop_tuners)
        for tuner in self._tuners().values():
            tuner.message.connect(self.show_status)
            tuner.state_changed.connect(self._sync_tuner_buttons)
            tuner.page_changed.connect(lambda title, t=tuner: self._tuner_page_changed(t, title))
        self.advanced_tuner.steps.open_recorder.connect(lambda: self.open_tool("recorder"))
        self._sync_tuner_buttons()
        stop = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        stop.setContext(Qt.ShortcutContext.ApplicationShortcut)
        stop.activated.connect(self.emergency_stop)

        self.session.connected.connect(self._on_connected)
        self.session.probed.connect(self._on_probed)
        self.session.node_detected.connect(self._on_node_detected)
        self.session.disconnected.connect(self._on_disconnected)
        self.session.error.connect(self._on_error)
        self.session.link_lost.connect(self._on_link_lost)
        self.session.axis_connected.connect(self._on_axis_connected)
        self.session.axis_disconnected.connect(self._on_axis_disconnected)
        # axis_error is reported by config_page's own _on_axis_error (through its `message`, wired above) - a second
        # connection here used to write the very same "Axis N: ..." line to the status bar twice
        self.config_page.target_name.textChanged.connect(self._on_target_renamed)
        self.iface.currentIndexChanged.connect(self._on_interface_changed)
        self._on_interface_changed()
        self._load_settings()
        self._set_pill("DISCONNECTED", "")
        self._set_connection_buttons(False)
        report(0.96, "Opening the control workspace…")
        self.open_tool("config")

    # ---- ribbon and navigator ---------------------------------------------------------------
    def _build_ribbon(self):
        r = self.ribbon
        page = r.add_page("file", "File")
        group = page.add_group("Parameter File")
        group.add_button("Open…", "open", lambda: self._params_action("load_dialog"), "Load parameter values from a file")
        group.add_button("Save…", "save", lambda: self._params_action("save_dialog"), "Save the parameter values to a file")

        page = r.add_page("parameters", "Parameters")
        group = page.add_group("Drive")
        group.add_button("Upload", "upload", lambda: self._params_action("read_all"), "Read every configuration parameter from the drive")
        group.add_button("Download", "download", lambda: self._params_action("write_changed"), "Write the changed parameters to the drive")
        self.save_flash_btn = group.add_button("Save to\nFlash", "save", self._save_to_flash,
                                               "Make the drive's whole current configuration what it loads at power-up. A written "
                                               "parameter otherwise lives in RAM only and is lost at the next power cycle.")
        group = page.add_group("Telemetry")
        group.add_button("Read\nTelemetry", "monitor", lambda: self._telemetry_read(), "Read every telemetry parameter from the drive, once")
        group = page.add_group("Export")
        group.add_button("Export\nHTML", "export", lambda: self._export_dialog("html"),
                         "The whole object dictionary (configuration + telemetry) as a readable HTML report")
        group.add_button("Export\nEDS", "export", lambda: self._export_dialog("eds"),
                         "The whole object dictionary as a CANopen Electronic Data Sheet (.eds), for other CANopen tools")
        group.add_button("Save\nSnapshot", "export", lambda: self._export_dialog("snapshot"),
                         "Every parameter this session has a value for, as a file Parameters Comparison can load")

        page = r.add_page("system", "System Configuration")
        group = page.add_group("Device")
        self.connect_btn = group.add_button("Connect", "plug", self._connect, "Connect to the drive")
        self.disconnect_btn = group.add_button("Disconnect", "unplug", self._disconnect, "Disconnect from the drive (stops the motor first)")

        page = r.add_page("tools", "Floating Tools")
        group = page.add_group("Drive Tools")
        group.add_button("Terminal", "terminal", lambda: self.open_tool("terminal"), "Every frame on the bus, decoded")
        group.add_button("Motion\nController", "code", lambda: self.open_tool("motion_controller"),
                         "Write, run and save a short G-code program")
        group.add_button("Axes", "grid", lambda: self.open_tool("axes"), "Every connected axis, at a glance")
        group.add_button("Control\nParameters", "sliders", lambda: self.open_tool("parameters"), "Parameters Explorer")
        group.add_button("Drive Motion", "motion", lambda: self.open_tool("motion"), "Single Axis Motion")
        group.add_button("Drive Modes", "sliders", lambda: self.open_tool("drive_modes"),
                         "Current / torque, cyclic position, Direct Field Control, Kickstart, Power Generation, brushed, Virtual Brake")
        group.add_button("Telemetry", "monitor", lambda: self.open_tool("telemetry"), "Telemetry Explorer")
        group.add_button("Peripherals", "drive", lambda: self.open_tool("peripherals"),
                         "RC PWM / Electronic Gearing, GPIO, Thermistor, Inverter")
        group = page.add_group("Tuning")
        group.add_button("Quick\nTuning", "wand", lambda: self.open_tool("quick_tuning"),
                         "Motor settings, then the drive's automatic configuration")
        group.add_button("Advanced\nTuning", "wrench", lambda: self.open_tool("advanced_tuning"),
                         "Motor and feedback settings, the automatic configuration with its dynamics, and the results")
        group.add_button("Automatic\nTest", "wand", self._open_automatic_test,
                         "One button runs the checks and loop measurements; Beta tunes the loops round by round")
        group = page.add_group("Utilities")
        group.add_button("Recorder", "chart", lambda: self.open_tool("recorder"), "Record the signals the drive broadcasts and look at them")
        group.add_button("Motor\nConstants", "compare", lambda: self._open_motor_constants(),
                         "Convert between Ke, Kv and Kt - pure calculator, no drive needed")
        group.add_button("Compare\nParameters", "list", lambda: self._open_params_comparison(),
                         "Load two saved parameter snapshots and see what differs between them")
        group = page.add_group("Workspace")
        self.docked_view_btn = group.add_button(
            "Docked\nView", "grid", self._toggle_docked_mode,
            "Open several Servo Drive tools at once, side by side, instead of one page at a time")
        self.docked_view_btn.setCheckable(True)
        group.add_button("Reset\nLayout", "revert", self._reset_dock_layout,
                         "Close every open panel in the docked workspace and start fresh")

        page = r.add_page("recording", "Recording", contextual=True)             # while ANY Recorder is open -
                                                                                  # the primary's or an axis's own
        group = page.add_group("File")
        self.rec_save_btn = group.add_button("Save…", "save", lambda: self._current_recorder().save_dialog(),
                                             "Save the recording to a CSV file")
        self.rec_save_image_btn = group.add_button("Save\nImage…", "export", lambda: self._current_recorder().save_image_dialog(),
                                                    "Save both charts, exactly as shown, as a PNG image")
        group = page.add_group("Activation")
        self.rec_start_btn = group.add_button("Start", "record", lambda: self._current_recorder().start(),
                                              "Start recording the drive's telemetry broadcast")
        self.rec_stop_btn = group.add_button("Stop", "stop", lambda: self._current_recorder().stop(), "Stop recording")
        self.rec_clear_btn = group.add_button("Clear", "trash", lambda: self._current_recorder().clear(),
                                              "Throw the recording away")
        group = page.add_group("View")
        group.add_button("Fit", "fit", lambda: self._current_recorder().fit(), "Show the whole recording")
        self.rec_cursors_btn = group.add_button("Cursors", "cursors", None, "Two cursors to measure between")
        self.rec_cursors_btn.setCheckable(True)
        self.rec_cursors_btn.toggled.connect(lambda on: self._current_recorder().set_cursors(on))
        self.recorder_page.cursors_btn.toggled.connect(
            lambda on: self._on_recorder_cursors_toggled(self.recorder_page, on))
        self.tuner_buttons = {}
        for key, title in (("quick", "Drive Quick Tuner"), ("advanced", "Drive Advanced Tuner")):
            page = r.add_page(key, title, contextual=True)      # while ANY tuner of this key is open - primary's or an axis's own
            group = page.add_group("Device")
            buttons = {"load": group.add_button("Drive\nLoad", "upload", lambda k=key: self._current_tuner(k).load_all(),
                                                "Read every value of the tuner's pages from the drive")}
            group = page.add_group("Deployment")
            buttons["apply"] = group.add_button("Apply", "apply", lambda k=key: self._current_tuner(k).apply_page(),
                                                "Write the changed values of this page to the drive")
            buttons["revert"] = group.add_button("Revert", "revert", lambda k=key: self._current_tuner(k).revert_page(),
                                                 "Put back the values the drive holds, on this page")
            buttons["apply_all"] = group.add_button("Apply\nAll", "apply", lambda k=key: self._current_tuner(k).apply_all(),
                                                    "Write the changed values of every page to the drive")
            buttons["revert_all"] = group.add_button("Revert\nAll", "revert", lambda k=key: self._current_tuner(k).revert_all(),
                                                     "Put back the values the drive holds, on every page")
            self.tuner_buttons[key] = buttons
        r.show_page("system")

    def _tuners(self):
        """The PRIMARY drive's own two tuners - see `_current_tuner()` for whichever one (primary or an axis's
        own) is actually shown right now, which is what the ribbon's buttons must act on."""
        return {"quick": self.quick_tuner, "advanced": self.advanced_tuner}

    def _current_tuner(self, key):
        """Whichever TunerPage of this key (quick/advanced) is currently shown - the primary's own, or an
        additional axis's own - so the ONE ribbon tab per key always acts on the right one. Falls back to the
        primary tuner of that key if something else entirely is open (should not normally happen: that ribbon tab
        is hidden then anyway)."""
        page = self._focused_page()
        if isinstance(page, TunerPage) and page.advanced == (key == "advanced"):
            return page
        return self._tuners()[key]

    def _sync_tuner_buttons(self):
        for key in ("quick", "advanced"):
            tuner = self._current_tuner(key)
            b = self.tuner_buttons[key]
            b["load"].setEnabled(tuner.connected and not tuner.busy)
            b["apply"].setEnabled(tuner.can_apply())
            b["revert"].setEnabled(tuner.can_revert())
            b["apply_all"].setEnabled(tuner.can_apply(tuner.forms))
            b["revert_all"].setEnabled(tuner.can_revert(tuner.forms))

    def _open_automatic_test(self):
        """The ribbon's Automatic Test button: the page lives in Advanced Tuning, under Verification below Frequency
        Response (user, 2026-09-25: "put automatic test under advanced tunning below frequency response")."""
        self.open_tool("advanced_tuning")
        self.advanced_tuner.go_to(self.advanced_tuner.auto_test)

    def _tuner_page_changed(self, tuner, title):
        if self.host.current_page() is tuner:
            self.host.set_crumb(title)

    def emergency_stop(self):
        """STOP button and Esc: the motor stops, the target goes to 0 and a running automatic tuning ends (the
        motion page tells its own axis's tuners - see `_on_axis_connected`'s `stop_requested` wiring) - for the
        primary drive AND every additional axis (real multi-axis: a round STOP must not leave some axis, or some
        axis's own tuning run, running)."""
        # first, whatever G-code is in flight: the Motion Controller's program (it used to keep going - press the
        # round STOP during a G04 and the NEXT line moved the motor again once the dwell ended), the Terminal's own
        # single line, and a Tool Pose GO - each of these ends silently now, nothing sent after this point
        self.motion_controller_page._stop_program(stop_drives=False)     # the axes are stopped right below, once
        self.log_tab.runner.cancel()
        self.axes_page.setup.runner.cancel()
        self.drive_tab.emergency_stop()          # -> stop_requested -> the tuners, Automatic Test among their pages
        for pages in self._axis_pages.values():
            pages["motion"].emergency_stop()

    def _stop_tuners(self, tuners=None):
        """Ends whichever tuners are given (default: the PRIMARY drive's own two) - an additional axis's own
        Motion page wires its `stop_requested` to THIS with ITS OWN two tuners explicitly, so one axis's STOP
        never reaches a different axis's - or the primary's - own running tuning."""
        for tuner in (tuners if tuners is not None else self._tuners()).values():
            tuner.emergency()

    def _build_navigator(self):
        n = self.navigator
        n.add_activity("system", "System Configuration", "gear")
        # Three activities (2026-09-25, user: "we will make 2 option 1 as servo drive and 2 is motion instead of drive
        # setup and motion"): SERVO DRIVE holds one drive's own tools (the per-drive rows `retarget_drive_tools`
        # re-points at whichever drive is selected in the tree), MOTION everything that involves several axes at
        # once - G-code programs, the 2D/3D views, every axis's status, multi-axis moves, the Terminal (its G-code
        # console addresses any axis; its CAN trace shows every drive).
        n.add_activity("drive", "Servo Drive", "wrench", tools=[
            ("quick_tuning", "Quick Tuning", "wand", "quick"), ("advanced_tuning", "Advanced Tuning", "wrench", "advanced"),
            ("motion", "Motion - Single Axis", "motion", "motion"), ("drive_modes", "Drive Modes", "sliders", "modes"),
            ("recorder", "Recorder", "chart", "recorder"),
            ("parameters", "Parameters Explorer", "list", "parameters"), ("telemetry", "Telemetry Explorer", "monitor", "telemetry"),
            ("peripherals", "Peripherals", "drive", "peripherals")])
        n.add_activity("motion", "Motion", "motion", tools=[
            ("motion_controller", "Motion Controller", "code"), ("sim3d", "3D Simulation", "cube"),
            ("plotter_2d", "2D Plotter", "scatter"), ("multi_axis_motion", "Multi Axis Motion", "grid"),
            ("axes", "Axes", "grid"), ("status_monitor", "Status Monitor", "monitor"),
            ("terminal", "Terminal", "terminal")])
        n.activity_changed.connect(self._on_activity_changed)
        n.tool_chosen.connect(self.open_tool)
        n.drive_chosen.connect(self._on_drive_chosen)
        n.axis_chosen.connect(self._on_axis_selected)

    def open_tool(self, key):
        """Open a page: its activity, its place in the list, its ribbon tab and the page itself."""
        tool = self.tools[key]
        self.navigator.blockSignals(True)
        self.navigator.set_activity(tool.activity)
        self.navigator.blockSignals(False)
        self.navigator.select_tool(key)
        self._last_tool[tool.activity] = key
        self._current_key = key
        self._sync_drive_badges()
        docked = self._docked_mode and tool.activity in DOCKABLE_ACTIVITIES
        if docked:
            self._show_docked_tool(key, tool)
        else:
            self.host.show_page(tool.page, tool.title, tool.crumb, tool.glyph)
        if isinstance(tool.page, TunerPage):
            if not docked:                  # the outer crumb is the shared "Docked View" label while docked - a tuner's
                self.host.set_crumb(tool.page.current_title())     # own sub-page title belongs to its dock, not up there
            self._sync_tuner_buttons()      # switching TO a different tuner (primary or another axis's) - resync the ribbon
        is_recorder = isinstance(tool.page, RecorderPage)
        self.ribbon.set_page_visible("recording", is_recorder)
        for name in self._tuners():
            self.ribbon.set_page_visible(name, tool.ribbon == name)
        self.ribbon.show_page(tool.ribbon)
        self.show_status(f"{tool.title} Page")
        if is_recorder:      # switching TO a different Recorder (primary or another axis's) - resync the ribbon
            self._sync_recorder_buttons()
            self.rec_cursors_btn.blockSignals(True)
            self.rec_cursors_btn.setChecked(tool.page.cursors_btn.isChecked())
            self.rec_cursors_btn.blockSignals(False)

    # ---- Docked View: Servo Drive's own dockable multi-pane workspace ------------
    def _show_docked_tool(self, key, tool):
        """Takes this tool's own page OUT of PageHost's single-page stack (if it was there) and shows it as a
        dock instead - the SAME page instance either way, just a different surface. The outer PageHost header
        itself shows a fixed "Servo Drive / Docked View" title while docked, not whichever tool was
        just clicked - several panels can be open and focused at once, so a single outer crumb has nothing
        specific left to describe (each dock shows its own title on its own title bar instead)."""
        self.host.show_page(self.dock_workspace, self.navigator.activity_title(tool.activity), "Docked View",
                            "grid")                          # FIRST: removing the
        if self.host.stack.indexOf(tool.page) >= 0:          # current page from the stack would otherwise make the stack
            self.host.stack.removeWidget(tool.page)          # show its neighbour for an instant, whose showEvent then
        self.dock_workspace.show_tool(key, tool.title, tool.page)   # fires (a TunerPage's starts a Drive Load - real reads)

    def _focused_page(self):
        """Whichever page is really "current" right now. `self.host.current_page()` alone only means that in
        single-page mode - in docked mode it always returns `self.dock_workspace` itself, since several pages
        can be open there at once, so "current" instead means whichever tool key was most recently opened
        (`self._current_key`, the same thing `open_tool()` keeps updated on every click, docked or not). Used
        wherever the ribbon needs to know which tuner/recorder its contextual buttons should actually act on -
        `_current_tuner()`/`_current_recorder()` - so a docked axis's own panel gets the same correct ribbon
        sync a single-page one already did, instead of silently falling back to the primary drive's own."""
        tool = self.tools.get(self._current_key)
        if tool is not None and self._docked_mode and tool.activity in DOCKABLE_ACTIVITIES:
            return tool.page
        return self.host.current_page()

    def _toggle_docked_mode(self):
        self._docked_mode = self.docked_view_btn.isChecked()
        if not self._docked_mode:
            for key, page in self.dock_workspace.close_all().items():
                if self.host.stack.indexOf(page) < 0:
                    self.host.stack.addWidget(page)
        self.open_tool(self._current_key)     # refresh the visible surface for the new mode - a no-op while on
                                                # System Configuration, which never docks either way

    def _reset_dock_layout(self):
        for key, page in self.dock_workspace.close_all().items():
            if self.host.stack.indexOf(page) < 0:
                self.host.stack.addWidget(page)
        s = self._settings()
        s.remove("workspace/dock_keys")
        s.remove("workspace/dock_state")
        if self._docked_mode:
            self.open_tool(self._current_key)

    def _open_motor_constants(self):
        MotorConstantsDialog(self).exec()

    def _open_params_comparison(self):
        ParametersComparisonDialog(self).exec()

    def _export_entries(self):
        """Every configuration + telemetry register (417), each with whatever value this session's own two
        ParamsTab pages last knew (never re-requested here - the export reflects what has already been read,
        same as everywhere else in this tool)."""
        entries = []
        for tab in (self.params_tab, self.telemetry_tab):
            for row in tab.rows:
                entries.append(export_module.entry(row.reg, value=row.last, has_value=row.has_value))
        return entries

    def _export_dialog(self, kind):
        client = self.session.client
        node_id, poles = self.session.node_id, (client.poles if client else None)
        entries = self._export_entries()
        if kind == "html":
            caption, default, filt = "Export as HTML", "sdr_object_dictionary.html", "HTML files (*.html)"
            text = export_module.build_html_report(entries, node_id=node_id, poles=poles)
            count = len(entries)
        elif kind == "eds":
            caption, default, filt = "Export as EDS", f"{export_module.PRODUCT_NAME}.eds", "CANopen EDS files (*.eds)"
            text = export_module.build_eds(entries, node_id=node_id, poles=poles)
            count = len(entries)
        else:
            caption, default, filt = "Save Parameter Snapshot", "snapshot.json", "Snapshot files (*.json)"
            text = export_module.build_snapshot(entries, node_id=node_id, poles=poles)
            count = sum(1 for e in entries if e.has_value)      # only these actually make it into the snapshot
        path, _ = QFileDialog.getSaveFileName(self, caption, default, filt)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            QMessageBox.warning(self, caption, f"Could not save: {exc}")
            return
        verb = "Saved snapshot of" if kind == "snapshot" else "Exported"
        self.show_status(f"{verb} {count} parameter(s) to {path}", "ok")

    def _current_recorder(self):
        """Whichever RecorderPage is currently shown - the primary's own, or an additional axis's own - so the
        ONE "Recording" ribbon tab's buttons always act on the right one. Falls back to the primary recorder if
        something else entirely is open (should not normally happen: the tab is hidden then anyway)."""
        page = self._focused_page()
        return page if isinstance(page, RecorderPage) else self.recorder_page

    def _on_recorder_cursors_toggled(self, rec, on):
        """Keeps the ribbon's own Cursors button reflecting whichever RecorderPage's own toggle just changed -
        only if THAT page is the one currently shown (several axes' own Recorder pages each have their own
        `cursors_btn`, independent of each other and of the ribbon's single shared button)."""
        if self._current_recorder() is rec:
            self.rec_cursors_btn.blockSignals(True)
            self.rec_cursors_btn.setChecked(on)
            self.rec_cursors_btn.blockSignals(False)

    def _sync_recorder_buttons(self):
        rec = self._current_recorder()
        for ribbon_button, page_button in ((self.rec_start_btn, rec.start_btn), (self.rec_stop_btn, rec.stop_btn),
                                           (self.rec_clear_btn, rec.clear_btn), (self.rec_save_btn, rec.save_btn),
                                           (self.rec_save_image_btn, rec.save_image_btn)):
            ribbon_button.setEnabled(page_button.isEnabled())

    def _on_activity_changed(self, activity):
        key = self._last_tool[activity]
        self.open_tool(key)
        # Sync the tree to the drive this activity is really showing, not always the primary. System Configuration
        # shows either the primary's own Item Configuration ("config") or one specific axis's own ("config:N") - the
        # page's owner; Servo Drive shows whichever drive its per-drive rows are scoped to
        # (`_scoped_axis`) - even while a shared page like Terminal, which has no owner of its own, is the one open.
        # Two real mismatches this used to allow: navigate to an axis's Motion page, then click back to System
        # Configuration - `_last_tool["system"]` could still be the primary's own "config", which the tree's
        # leftover axis selection contradicted; and the reverse (only "system" used to be synced at all): select
        # Drive02 under Servo Drive, go to System Configuration (tree -> Drive01), come back - page
        # `motion:2`, badge "Drive02", tree still highlighting Drive01.
        node_id = self._current_owner() if activity == "system" else self._scoped_axis
        item = self.navigator._axis_items.get(node_id) if node_id is not None else self.navigator.drive_item
        if item is not None:
            self.navigator.tree.setCurrentItem(item)
        self.show_status(f"{self.navigator._activities[activity][2]} Activity")

    def _on_axis_connected(self, node_id):
        """Builds this additional axis's own Quick/Advanced Tuning, Motion, Recorder, Parameters, Telemetry and
        Peripherals pages RIGHT AWAY (not lazily on first click) - `AxisContext` fires `connected` and, once
        probed, `probed` immediately after `Session.axis_connected` itself, so a page built here in reaction to
        `axis_connected` is already subscribed in time to catch both (see `Session.connect_axis`'s own comment on
        this ordering) - a page built lazily on first click could otherwise miss both and show "Not connected" for
        an axis that has been connected all along.

        `DriveTab`/`ParamsTab`/`RecorderPage`/`PeripheralsPage`/`TunerPage` needed ZERO changes for this - each
        already only ever reads `self.s.client`/`.node_id`/`.is_connected` from its session (confirmed directly
        for every one of them, `TunerPage` included, despite carrying much more internal state than the others),
        so `AxisContext` works in their place unmodified. `RecorderPage` additionally takes a `drive_tab` for what
        to record - this axis's own Motion page, built first, right above.

        **Safety-critical wiring, not just page construction**: this axis's own Motion page's `stop_requested`
        (emitted by ITS OWN emergency_stop(), which the round STOP button already calls for every axis - see
        `emergency_stop()`) is wired to stop ONLY this axis's own two tuners, never the primary's or another
        axis's - `_stop_tuners()` takes an explicit tuners dict for exactly this. The Advanced tuner's own "Open
        in Recorder" (its Step Response Tests page) opens THIS axis's own Recorder, not the primary's."""
        self.navigator.add_axis_item(node_id)
        ctx = self.session.axes[node_id]
        motion = DriveTab(ctx)
        modes = DriveModesPage(ctx, motion)
        motion.modes = modes
        quick, advanced = TunerPage(ctx), TunerPage(ctx, advanced=True)
        pages = {"quick": quick, "advanced": advanced, "motion": motion, "modes": modes,
                 "recorder": RecorderPage(ctx, motion),
                 "parameters": ParamsTab(ctx), "telemetry": ParamsTab(ctx, telemetry=True),
                 "peripherals": PeripheralsPage(ctx),
                 "config": AxisConfigurationPage(ctx, self.channel.text(), self.bitrate.currentText())}
        self._axis_pages[node_id] = pages
        # "config" belongs to the System Configuration activity, not Servo Drive like the other 7 kinds
        # below - registered separately since it is the one page here the shared per-kind loop's own fixed
        # ("drive", ..., "tools") shape does not fit
        self.tools[self._axis_tool_key("config", node_id)] = Tool(
            "system", f"Drive{node_id:02d} (SDR) - Item Configuration", "", "gear", pages["config"], "system")
        self.host.stack.addWidget(pages["config"])
        pages["config"].message.connect(self.show_status)
        for kind, title, glyph, ribbon in (
                ("quick", "Quick Tuning", "wand", "quick"), ("advanced", "Advanced Tuning", "wrench", "advanced"),
                ("motion", "Motion", "motion", "tools"), ("modes", "Drive Modes", "sliders", "tools"),
                ("recorder", "Recorder", "chart", "tools"),
                ("parameters", "Parameters", "list", "tools"), ("telemetry", "Telemetry", "monitor", "tools"),
                ("peripherals", "Peripherals", "drive", "tools")):
            page = pages[kind]
            self.tools[self._axis_tool_key(kind, node_id)] = Tool(
                "drive", f"Drive{node_id:02d} (SDR) - {title}", "", glyph, page, ribbon)
            self.host.stack.addWidget(page)
            if hasattr(page, "message"):
                page.message.connect(self.show_status)
            if hasattr(page, "message"):
                page.message.connect(self.show_status)
        pages["recorder"].buttons_changed.connect(self._sync_recorder_buttons)
        pages["recorder"].cursors_btn.toggled.connect(lambda on, r=pages["recorder"]: self._on_recorder_cursors_toggled(r, on))
        for tuner in (quick, advanced):
            tuner.state_changed.connect(self._sync_tuner_buttons)
            tuner.page_changed.connect(lambda title, t=tuner: self._tuner_page_changed(t, title))
        advanced.steps.open_recorder.connect(lambda: self.open_tool(self._axis_tool_key("recorder", node_id)))
        motion.stop_requested.connect(lambda: self._stop_tuners({"quick": quick, "advanced": advanced}))
        # keep the bottom-right pill live while one of THIS axis's own pages happens to be the one shown - matches
        # how the primary's own probe result already updates it in place, not just at the moment a page is opened
        ctx.probed.connect(lambda ok, _msg, n=node_id: self._on_axis_probed(n, ok))

    def _on_axis_disconnected(self, node_id):
        self.navigator.remove_axis_item(node_id)
        self._axis_probe.pop(node_id, None)
        if self._scoped_axis == node_id:
            # the Servo Drive list's per-drive rows point at THIS axis's own tools: back to the primary's
            # own now, whether or not one of its pages is the one currently shown. It used to retarget only in that
            # case - leave the list on Terminal (or go to System Configuration), remove the axis, come back, click
            # Recorder: the row still held `recorder:2`, a key self.tools had just lost -> KeyError, nothing opened
            self.navigator.retarget_drive_tools(PRIMARY_TOOL_KEY)
            self._scoped_axis = None
        pages = self._axis_pages.pop(node_id, {})
        for kind, page in pages.items():
            axis_key = self._axis_tool_key(kind, node_id)
            self.tools.pop(axis_key, None)
            for activity, key in list(self._last_tool.items()):
                if key == axis_key:
                    # this axis's own key was some activity's own "reopen this if you come back" - not just the
                    # CURRENTLY shown page, but any activity that was never revisited since selecting this axis - a
                    # dangling reference to a key `self.tools` just lost, which would otherwise KeyError the moment
                    # that activity is next opened (2026-09-23: found while tracing a related report, not yet hit
                    # live, but a real crash risk once axes start disconnecting during normal use)
                    self._last_tool[activity] = PRIMARY_TOOL_KEY[kind]
            self.dock_workspace.close_tool(axis_key)   # detach if it happened to be docked - a harmless no-op
                                                        # otherwise; either way `page` must not be deleted while
                                                        # some QDockWidget still holds it
            if self._current_key == axis_key:
                # this axis's own tools were the ones the Servo Drive list was scoped to - back to the
                # primary's own, same as clicking its own tree item would, before falling back to its own page.
                # `_current_key` (not `self.host.current_page() is page`, the original check) also covers the
                # docked case correctly: a docked page is never actually `self.host.current_page()` itself, that
                # always reads `dock_workspace` while docked - but it's still exactly what `_current_key` names.
                self.navigator.retarget_drive_tools(PRIMARY_TOOL_KEY)
                self.open_tool(PRIMARY_TOOL_KEY[kind])   # fall back to the primary drive's own page of the same kind
            self.host.stack.removeWidget(page)
            page.deleteLater()

    def _on_drive_chosen(self):
        """The PRIMARY drive's own tree item was clicked - retargets the Servo Drive activity's own
        per-drive tool rows back to the primary's own pages first (in case an additional axis's own tools were
        showing there instead), then opens Item Configuration, exactly as this always has.

        Also resets `_last_tool["drive"]` to the primary's own Motion - not just the list of rows - so that
        clicking the Servo Drive ACTIVITY BUTTON itself (not a row in that list) afterward reopens the
        primary's own page too, instead of whatever axis was last actually visited there (2026-09-23, found while
        chasing the user's own report of Drive02 apparently showing Drive01's position: selecting a drive only ever
        retargeted the shared LIST, `_last_tool["drive"]` kept remembering the last drive whose page was actually
        opened directly - the two could disagree, and the activity button trusts only the latter)."""
        self.navigator.retarget_drive_tools(PRIMARY_TOOL_KEY)
        self._scoped_axis = None
        self._last_tool["drive"] = "motion"
        self.open_tool("config")

    def _on_axis_selected(self, node_id):
        """An additional axis's own tree item was clicked (built in `_on_axis_connected`) - a flat item, exactly
        like the primary's own. Retargets the Servo Drive activity's own per-drive tool rows (Quick/
        Advanced Tuning, Motion, Recorder, Parameters, Telemetry, Peripherals) at THIS axis's own pages, AND marks
        this axis's own Motion as what that activity's own button should reopen (`_last_tool["drive"]`) - the same
        real bug `_on_drive_chosen` had in the other direction (see its own docstring), just as reachable here:
        select an axis from System Configuration, then click the Servo Drive ACTIVITY BUTTON directly -
        without this, it would reopen whatever was last opened there directly, e.g. the primary's own Motion, even
        though the list itself now (correctly) points at this axis's tools. Then, whichever activity is CURRENTLY
        open (checked before `open_tool` below changes it) decides which of this axis's own pages appears right
        now: its own Item Configuration if System Configuration is open (2026-09-23, user: "when drive 2 is
        selected then every single information showing must be about drive 2 only" - its own General/Target
        Connection/Product Activation/Networks, not the primary's, and not an inline detail block on the primary's
        own page either), otherwise its own Motion page - the same as clicking the primary drive's own item opens
        Item Configuration or leaves you wherever you already were."""
        self.navigator.retarget_drive_tools({kind: self._axis_tool_key(kind, node_id)
                                             for kind in PRIMARY_TOOL_KEY if kind != "config"})
        self._scoped_axis = node_id
        self._last_tool["drive"] = self._axis_tool_key("motion", node_id)
        kind = "config" if self.navigator.current_activity() == "system" else "motion"
        self.open_tool(self._axis_tool_key(kind, node_id))

    @staticmethod
    def _axis_tool_key(kind, node_id):
        return f"{kind}:{node_id}"

    def _drive_tab_for(self, node_id):
        """The ONE DriveTab bound to a given axis's own connection - the primary drive's own page, or an additional
        axis's own Motion page (built in `_on_axis_connected`). Used by `GcodeRunner`/`MotionControllerPage` to
        reach whichever axis a G-code line actually names, without needing to know how pages are organised."""
        if node_id == self.session.node_id:
            return self.drive_tab
        return self._axis_pages.get(node_id, {}).get("motion")

    def show_status(self, text, level="info"):
        self.bottom.set_message(text, level)

    def status_text(self):
        return self.bottom.message.text()

    def _params_action(self, name):
        self.open_tool("parameters")
        getattr(self.params_tab, name)()

    def _telemetry_read(self):
        self.open_tool("telemetry")
        self.telemetry_tab.read_all()

    def _save_to_flash(self):
        """The drive's own store-to-flash request (client.save_to_flash - what the vendor IDE's button sends), for the
        drive whose page is shown: the primary, or the additional axis that owns the current page. Asked for first:
        it persists EVERYTHING currently in the drive's RAM, whatever was written to it and by whom."""
        owner = self._current_owner()
        ctx = self.session.axes.get(owner) if owner is not None else self.session
        client = ctx.client if ctx is not None else None
        if client is None or not ctx.is_connected:
            self.show_status("Save to Flash: not connected to a drive.", "warn")
            return
        name = self._target_name() if owner is None else f"Drive{owner:02d} (SDR)"
        if not self._confirm_flash(f"Save the whole current configuration of {name} (node {ctx.node_id}) to its flash?\n\n"
                                   "Everything the drive holds right now - every parameter written this session, the node ID, "
                                   "the CAN bit rate, the telemetry mapping and rates - becomes what it loads at power-up.\n\n"
                                   "This cannot be undone except by writing the old values back and saving again."):
            self.show_status("Save to Flash cancelled - nothing was sent.", "info")
            return

        def done(_ok):
            self.show_status(f"{name}: configuration saved to flash (node {ctx.node_id}).", "ok")

        def failed(exc):
            self.show_status(f"{name}: save to flash FAILED - {exc}", "error")

        self.show_status(f"{name}: saving the configuration to flash…", "warn")
        run_async(lambda emit, cancel: client.save_to_flash(), on_done=done, on_failed=failed)

    def _confirm_flash(self, text):
        return QMessageBox.question(self, "Save to Flash", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    # ---- the interface and the remembered connection settings -------------------------------
    def _on_interface_changed(self):
        label = self.iface.currentText()
        if label == SEARCH_PORTS:
            previous = self._interface_label or SIMULATOR
            ports = usb.find_serial_ports()
            if not ports:
                self.show_status("No serial ports were found.", "warn")
            else:
                port, accepted = (ports[0], True) if len(ports) == 1 else QInputDialog.getItem(
                    self, "Search available ports", "Select a serial port:", ports, 0, False)
                if accepted:
                    self.channel.setText(port)
                    self.channel.setCursorPosition(0)
                    self.show_status(f"Selected serial port {port}.", "ok")
            signals_blocked = self.iface.blockSignals(True)
            self.iface.setCurrentText(previous)
            self.iface.blockSignals(signals_blocked)
            return
        simulated, on_usb = label == SIMULATOR, label == USB
        changed, self._interface_label = label != self._interface_label, label
        self.channel.setEnabled(not simulated)
        self.bitrate.setEnabled(not (simulated or on_usb))      # the USB cable has a fixed link speed
        self.node.setEnabled(not on_usb)                        # the USB cable is a dedicated link: the node is found by listening
        self.node.setToolTip("Found automatically once connected - the USB cable reaches only this one drive" if on_usb
                             else "Node ID of the servo drive (1..127)")
        if on_usb:
            ports = usb.find_drive_ports()          # always re-scanned: the cable's port can change between plug-ins
            self.channel.setText(ports[0] if ports else "")
            self.channel.setCursorPosition(0)
            self.channel.setPlaceholderText("serial port, e.g. /dev/cu.usbmodem... or COM5" if not ports else "")
            self.channel.setToolTip("Serial port of the drive's USB cable" + (" (detected)" if ports else " (no drive found - plug it in)"))
        elif changed:
            # only when the interface itself changed: this also runs on every Disconnect (the unlock re-enables the
            # fields through here), where it used to overwrite a typed channel (PCAN_USBBUS2, /dev/ttyACM1...) with
            # the interface's default - so the very next Connect quietly opened the wrong channel
            self.channel.setText("" if simulated else DEFAULT_CHANNEL[INTERFACES[label]])
            self.channel.setPlaceholderText("virtual bus" if simulated else "")
            self.channel.setToolTip("")

    @staticmethod
    def _settings():
        return QSettings("STIMITA MOTION CONTROL", "SDR Servo Tuning Tool")

    def _load_settings(self):
        s = self._settings()
        idx = self.iface.findText(str(s.value("connection/interface", "")))
        if idx < 0 and usb.find_drive_ports():
            idx = self.iface.findText(USB)                      # first launch with the drive's cable plugged in
        if idx >= 0:
            self.iface.setCurrentIndex(idx)
        self._on_interface_changed()
        channel = str(s.value("connection/channel", ""))
        on_usb = self.iface.currentText() == USB
        if channel and self.channel.isEnabled() and not (on_usb and self.channel.text()):
            # a live-detected USB port (just set by _on_interface_changed) wins over a stale saved one - the OS can assign
            # a drive a different port name from one launch to the next, and this tool has no way to tell a stale saved
            # value from the plugged-in drive's own port other than trusting the live scan first
            self.channel.setText(channel)
        idx = self.bitrate.findData(int(s.value("connection/bitrate", 0)))
        if idx >= 0:
            self.bitrate.setCurrentIndex(idx)
        self.node.setValue(int(s.value("connection/node", 1)))
        name = str(s.value("workspace/target_name", "") or "")
        if name:
            self.config_page.target_name.setText(name)
        self._on_target_renamed()
        # Forget old saved docks. A floating QDockWidget can appear as an extra native window during startup.
        for key in ("workspace/docked_mode", "workspace/dock_keys", "workspace/dock_state"):
            s.remove(key)

    def _save_settings(self):
        s = self._settings()
        s.setValue("connection/interface", self.iface.currentText())
        s.setValue("connection/channel", self.channel.text().strip())
        s.setValue("connection/bitrate", self.bitrate.currentData())
        s.setValue("connection/node", self.node.value())
        s.setValue("workspace/target_name", self.config_page.target_name.text().strip())

    def _target_name(self):
        return self.config_page.target_name.text().strip() or "Drive01"

    def _on_target_renamed(self, _text=""):
        self.navigator.set_drive(self._target_name(), self.session.is_connected)
        self._sync_drive_badges()      # only touches the top-right badge if a primary/shared page is what is shown

    def _set_pill(self, text, state):
        """The PRIMARY's own connection text/state - always updates its own Item Configuration row, and the bottom
        right pill too, but ONLY when a primary or shared/workspace-level page is what is actually shown (2026-09-23,
        user: "i have selected drive 2 but why on bottom right side it shows node id 1" - the pill used to always
        show the primary, even while looking at an axis's own page, which does not describe it at all)."""
        self.config_page.state.setText(text)
        self._primary_pill = (text, state)
        if self._current_owner() is None:
            self._display_pill(text, state)

    def _display_pill(self, text, state):
        self.pill.setText(text)
        set_prop(self.pill, "state", state)

    def _current_owner(self):
        """The node id whose own page is currently shown, or None for the primary/a shared workspace-level page."""
        key = self._current_key
        return int(key.split(":")[1]) if key and ":" in key else None

    @staticmethod
    def _axis_pill_text(ctx, ok=True):
        if not ctx.is_connected:
            return "DISCONNECTED", ""
        if not ok:
            return f"NO RESPONSE  ·  NODE {ctx.node_id}", "warn"
        return (f"{'SIMULATED DRIVE' if ctx.is_simulated else 'ONLINE'}  ·  NODE {ctx.node_id}",
                "warn" if ctx.is_simulated else "ok")

    def _on_axis_probed(self, node_id, ok):
        """Remembers an additional axis's own probe result (as `_primary_pill` does for the primary) - the pill used
        to be recomputed with a default `ok=True` on every `open_tool()`/rename, so an axis that never answered
        showed "ONLINE · NODE N" while its own Item Configuration page said "NO RESPONSE"."""
        self._axis_probe[node_id] = ok
        if self._current_owner() == node_id:
            self._sync_drive_badges()

    def _sync_drive_badges(self):
        """Whichever page is now shown (`self._current_key`) decides what the top-right target name and the
        bottom-right pill say - the primary's own for a primary/shared page, or that specific axis's own name and
        live connection state (its last probe result, `_axis_probe`) for one of its own pages."""
        owner = self._current_owner()
        if owner is None:
            self.host.set_target(self._target_name())
            self._display_pill(*self._primary_pill)
            return
        ctx = self.session.axes.get(owner)
        if ctx is not None:
            self.host.set_target(f"Drive{owner:02d} (SDR)")
            self._display_pill(*self._axis_pill_text(ctx, self._axis_probe.get(owner, True)))

    # ---- connection ------------------------------------------------------------------------
    def connect_simulator(self):
        """`--sim`: select the built-in Simulator FIRST, then Connect. app.py used to press Connect alone, assuming the
        Simulator is the list's default - but _load_settings() restores whatever interface was saved last (found
        2026-09-25 with the real bench drive's own USB cable saved: `--sim` would have opened the real drive)."""
        self.iface.setCurrentIndex(self.iface.findText(SIMULATOR))
        self.connect_btn.click()

    def _connect(self):
        if self.session.is_connected:
            return
        channel = self.channel.text().strip()
        interface = self.iface.currentText()
        if interface != SIMULATOR and not channel:
            message = ("Select a serial port in Port / Channel before connecting." if interface == USB else
                       "Enter a port or channel in Port / Channel before connecting.")
            self.show_error(message)
            return
        self._set_pill("CONNECTING…", "warn")
        self.session.connect_drive(interface, channel, self.bitrate.currentData(), self.node.value())

    def _disconnect(self):
        if self.session.is_connected:
            self.session.disconnect_drive()

    def _set_connection_buttons(self, connected):
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)

    def _lock_connection_controls(self, locked):
        for w in (self.iface, self.channel, self.bitrate, self.node):
            w.setEnabled(not locked)
        if not locked:
            self._on_interface_changed()

    def _on_connected(self):
        self._save_settings()
        self._set_connection_buttons(True)
        self._lock_connection_controls(True)
        self._set_pill("CONNECTING…", "warn")
        self.navigator.set_drive(self._target_name(), True)

    def _on_probed(self, ok, message):
        node = self.session.node_id
        client = self.session.client
        self.config_page.show_device_info(client.device_info if client is not None and ok else None)
        if not ok:
            self._set_pill(f"NO RESPONSE  ·  NODE {node}", "warn")
        elif self.session.is_simulated:
            self._set_pill(f"SIMULATED DRIVE  ·  NODE {node}", "warn")
        else:
            self._set_pill(f"ONLINE  ·  NODE {node}", "ok")
        if ok:
            is_warning = "could NOT" in message or "WARNING" in message
            self.show_status(f"Node {node}: {message}", "warn" if is_warning else "info")
        else:
            self.show_status(f"Node {node} did not answer: {message}", "warn")

    def _on_node_detected(self, node):
        self.node.setValue(node)
        self._save_settings()                                   # remember the node the drive really uses
        self.show_status(f"The drive answers on node {node} - node selection updated.")

    def _on_disconnected(self):
        self._set_connection_buttons(False)
        self._lock_connection_controls(False)
        self._set_pill("DISCONNECTED", "")
        self.navigator.set_drive(self._target_name(), False)
        self.config_page.show_device_info(None)

    def _on_error(self, message):
        self._set_pill("DISCONNECTED", "")
        self.show_error(message)

    def _on_link_lost(self, message):
        self.session.disconnect_drive()
        self._set_pill("LINK LOST", "error")
        self.show_error(f"The CAN link was lost.\n\n{message}")

    def show_error(self, message):
        QMessageBox.critical(self, APP_TITLE, message)

    def closeEvent(self, event):
        self.session.disconnect_drive()         # also stops the motor
        QThreadPool.globalInstance().waitForDone(3000)
        super().closeEvent(event)
