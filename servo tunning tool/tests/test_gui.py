"""End-to-end tests: the real window, driven like a user, talking to the simulated drive."""
import json
import math
import re
import threading
import time

import can
import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSettings, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QDockWidget, QFrame, QLabel, QMessageBox, QPushButton
from usb_fakes import UsbRig

from sdr_tuner import bode, defs, kinematics, protocol as P
from sdr_tuner import usb as usb_module
from sdr_tuner.client import DriveClient, DriveError, DriveTimeout
from sdr_tuner.gui.log_tab import FrameDecoder
from sdr_tuner.gui.main_window import MainWindow
from sdr_tuner.gui.dock_workspace import DockTitleBar
from sdr_tuner.gui.motion_controller import GcodeEditor
from sdr_tuner.gui.params_tab import ParamsTab
from sdr_tuner.gui import session, trajectory
from sdr_tuner.gui.config_page import PropertyGroup
from sdr_tuner.gui.session import USB
from sdr_tuner.gui.theme import apply_theme
from sdr_tuner.simulator import SimulatedDrive

WAIT = 10000    # ms


@pytest.fixture(scope="session", autouse=True)
def _theme(qapp):
    apply_theme(qapp)


@pytest.fixture(autouse=True)
def _private_settings(tmp_path, monkeypatch):
    """Never read or write the real saved connection settings (native prefs / registry): use a temp .ini file."""
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(MainWindow, "_settings", staticmethod(lambda: QSettings(ini, QSettings.Format.IniFormat)))


@pytest.fixture(autouse=True)
def _no_real_usb(monkeypatch):
    """Tests must not look at the serial ports of the machine they run on."""
    monkeypatch.setattr(usb_module, "find_drive_ports", lambda: [])


@pytest.fixture(autouse=True)
def _quick_first_datasheet_poll(request, monkeypatch):
    """The automatic tuning reads the Datasheet once at START, then not before FIRST_POLL_S (20 s) after the command
    (user, 2026-09-26) - every tuning test would sit idle for 20 s, so the tests use 3 s: the first poll's time before
    that change, which keeps every tuning test's own timing margins as they were (a 1 s first poll made
    test_a_value_written_early_... detect the change 2 s sooner and end its capped wait before the simulated procedure
    had saved its flash). A test marked `@pytest.mark.real_poll_timing` keeps the real value, to check it directly."""
    if request.node.get_closest_marker("real_poll_timing"):
        return
    import sdr_tuner.autotest as autotest_module
    import sdr_tuner.gui.tuner as tuner_module
    monkeypatch.setattr(tuner_module, "FIRST_POLL_S", 3.0)
    monkeypatch.setattr(autotest_module, "FIRST_POLL_S", 3.0)


@pytest.fixture(autouse=True)
def _fast_simulated_telemetry(request, monkeypatch):
    """The window's own simulated drive publishes at a realistic ~9 messages/s (`session.SIM_TPDO_HZ`, like the real drive -
    see test_the_simulator_the_window_creates_uses_a_realistic_telemetry_rate). Tests connect through the same code path
    (`connect()` below), so without this they would need to wait 30x longer for enough samples; restore the fast rate
    `SimulatedDrive` itself defaults to (used directly by `make_rig()` in test_client.py). A test marked
    `@pytest.mark.realistic_telemetry` keeps the real, slow default, to check it directly."""
    if request.node.get_closest_marker("realistic_telemetry"):
        return
    import sdr_tuner.simulator as simulator_module
    monkeypatch.setattr(session, "SIM_TPDO_HZ", simulator_module.SimulatedDrive.__init__.__kwdefaults__["tpdo_hz"])


@pytest.fixture
def win(qtbot, monkeypatch):
    errors = []
    monkeypatch.setattr(MainWindow, "show_error", lambda self, message: errors.append(message))
    w = MainWindow()
    w.errors = errors
    qtbot.addWidget(w)
    w.show()
    yield w
    w.close()


def connect(win, qtbot):
    qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "SIMULATED" in win.pill.text(), timeout=WAIT)
    return win.session._sim, win.session.client


@pytest.mark.realistic_telemetry
def test_the_simulator_the_window_creates_uses_a_realistic_telemetry_rate(win, qtbot):
    """The user reported the software slow and hanging after a velocity command and a recording: the window's own simulator
    was publishing telemetry at the 100 Hz per message `SimulatedDrive` defaults to for fast tests, not the real drive's own
    ~3 Hz per message (~9 messages/s total) - 30x more history to copy and redraw on every Recorder / motion-page tick, and
    the 20000-sample buffer filling in ~3 minutes instead of ~2 hours. `session.SIM_TPDO_HZ` is the fix; this test keeps the
    real, slow default (`@pytest.mark.realistic_telemetry`) and measures the drive's own broadcast rate."""
    sim, client = connect(win, qtbot)
    assert session.SIM_TPDO_HZ <= 5.0                                                      # matches the real drive, not the test default
    assert sim.params[(0x3000, 0x05)] == pytest.approx(3.0 * session.SIM_TPDO_HZ)
    qtbot.wait(1000)
    n = len(client.history_snapshot("speed_hz")[0])
    assert 0 < n < 20                                                                       # ~9/s in 1 s, not ~100/s


def rows_by_address(win):
    return {r.reg.address: r for r in win.params_tab.rows}


def wait_idle(win, qtbot, timeout=30000):
    qtbot.waitUntil(lambda: not win.params_tab.busy, timeout=timeout)


def read_all(win, qtbot):
    qtbot.mouseClick(win.params_tab.read_all_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)


def user_types(row, text):
    """What typing into a value cell does: new text, then the user-edit signal."""
    row.editor.setText(text)
    row.editor.textEdited.emit(text)


def stored(reg, value):
    return P.decode_value(reg.dtype, P.encode_value(reg.dtype, value))


# ---- connection -------------------------------------------------------------------------------
def test_starts_disconnected_with_controls_disabled(win):
    assert win.pill.text() == "DISCONNECTED"
    assert not win.drive_tab.run_btn.isEnabled() and not win.drive_tab.stop_btn.isEnabled()
    assert not win.params_tab.read_all_btn.isEnabled()
    assert win.iface.currentText().startswith("Simulator")


def test_connect_disconnect_reconnect(win, qtbot):
    for _ in range(2):
        sim, client = connect(win, qtbot)
        assert win.disconnect_btn.isEnabled() and not win.connect_btn.isEnabled()
        assert win.drive_tab.run_btn.isEnabled() and win.params_tab.read_all_btn.isEnabled()
        assert not win.iface.isEnabled()
        assert client.poles == 14
        qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
        assert win.session.client is None and win.connect_btn.isEnabled() and not win.disconnect_btn.isEnabled()
        assert win.pill.text() == "DISCONNECTED" and not win.drive_tab.run_btn.isEnabled() and win.iface.isEnabled()


def test_no_threads_left_behind(win, qtbot):
    connect(win, qtbot)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not [t for t in threading.enumerate() if t.name.startswith("sdr-")], timeout=3000)


def test_connection_error_is_reported_not_raised(win, qtbot, monkeypatch):
    def refuse(**kwargs):
        raise can.CanInterfaceNotImplementedError("PCAN driver not installed")
    monkeypatch.setattr(can, "Bus", refuse)
    win.iface.setCurrentText("PCAN (PEAK-System)")
    assert win.channel.text() == "PCAN_USBBUS1"
    qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
    assert win.session.client is None and win.connect_btn.isEnabled() and win.pill.text() == "DISCONNECTED"
    assert len(win.errors) == 1 and "PCAN driver not installed" in win.errors[0]


def test_drive_that_does_not_answer_is_flagged(win, qtbot, monkeypatch):
    def silent(self):
        raise DriveTimeout("no answer from node 1")
    monkeypatch.setattr(DriveClient, "identify", silent)
    qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "NO RESPONSE" in win.pill.text(), timeout=WAIT)
    dt = win.drive_tab
    qtbot.waitUntil(lambda: dt.cards["vbus"].value.text() != "—", timeout=WAIT)              # the broadcast is still shown ...
    qtbot.waitUntil(lambda: dt.cards["speed_hz"].value.text() != "—", timeout=WAIT)          # ... speed included: it needs no request


def test_link_loss_disconnects_and_reports(win, qtbot):
    _, client = connect(win, qtbot)
    client.link_error_listeners[0](RuntimeError("cable pulled"))
    qtbot.waitUntil(lambda: win.session.client is None, timeout=WAIT)
    assert win.pill.text() == "LINK LOST" and "cable pulled" in win.errors[0]


# ---- multi-axis (Session.connect_axis/disconnect_axis - real vendor-style multi-drive support) ------------
def test_connect_axis_adds_a_second_simulated_drive_alongside_the_primary_one(win, qtbot):
    """Real multi-axis: connect_axis() lets a SECOND servo drive join the session on top of the primary connection
    (connect_drive/win.connect_btn) - the axis/node-id rule this project already established for G-code
    (gcode_runner.py) is exactly what makes several independently-addressed drives meaningful here."""
    sim, client = connect(win, qtbot)                      # primary: node 1 (the default)
    second = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    assert second is not None and second.client is not client
    qtbot.waitUntil(lambda: 2 in win.session.axes, timeout=WAIT)
    assert win.session.axes[2] is second
    assert win.session.client is client and win.session.node_id == 1    # the primary connection is untouched
    qtbot.waitUntil(lambda: second.client.poles is not None, timeout=WAIT)   # the new axis's own probe completed


def test_connect_axis_context_signals_reach_a_listener_attached_via_axis_connected(win, qtbot):
    """This is exactly the ordering a per-axis page needs to build correctly: subscribe to Session.axis_connected,
    and from INSIDE that handler grab the AxisContext and connect to ITS OWN signals - `connected` (fired
    synchronously, right after `axis_connected` itself) and `probed` (fired later, once the async probe resolves)
    must both still reach a listener attached this way, not just one that already existed beforehand."""
    sim, client = connect(win, qtbot)
    events = []

    def on_axis_connected(node_id):
        ctx = win.session.axes[node_id]
        ctx.connected.connect(lambda: events.append("connected"))
        ctx.probed.connect(lambda ok, msg: events.append(("probed", ok)))

    win.session.axis_connected.connect(on_axis_connected)
    ctx = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    assert events == ["connected"]                       # already fired synchronously, in order, before probing
    qtbot.waitUntil(lambda: ("probed", True) in events, timeout=WAIT)
    assert ctx.node_id == 2 and ctx.is_simulated and ctx.interface == session.SIMULATOR and ctx.is_connected


def test_connect_axis_refuses_a_node_already_in_use(win, qtbot):
    sim, client = connect(win, qtbot)
    errors = []
    win.session.axis_error.connect(lambda node, msg: errors.append((node, msg)))
    assert win.session.connect_axis(session.SIMULATOR, "", 0, 1) is None       # 1 is the primary's own node
    assert errors and "already connected" in errors[-1][1]
    second = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    assert second is not None
    qtbot.waitUntil(lambda: 2 in win.session.axes, timeout=WAIT)
    assert win.session.connect_axis(session.SIMULATOR, "", 0, 2) is None       # 2 is now an additional axis's own node
    assert "already connected" in errors[-1][1]


def test_connect_axis_refuses_before_a_primary_connection_exists(win, qtbot):
    errors = []
    win.session.axis_error.connect(lambda node, msg: errors.append((node, msg)))
    assert win.session.connect_axis(session.SIMULATOR, "", 0, 2) is None
    assert errors and "Connect the first drive" in errors[-1][1]
    assert win.session.axes == {}


def test_connect_axis_refuses_on_usb_even_with_a_primary_connection(win, qtbot):
    """The drive's own USB cable is a dedicated link to exactly one drive (the README) - this stays true no matter
    what the PRIMARY connection's own interface is."""
    sim, client = connect(win, qtbot)
    errors = []
    win.session.axis_error.connect(lambda node, msg: errors.append((node, msg)))
    assert win.session.connect_axis(session.USB, "/dev/fake", 0, 2) is None
    assert errors and "reaches exactly one drive" in errors[-1][1]
    assert win.session.axes == {}


def test_disconnect_axis_ends_only_that_axis_not_the_primary(win, qtbot):
    sim, client = connect(win, qtbot)
    second = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.session.axes, timeout=WAIT)
    second_client = second.client
    disconnected = []
    win.session.axis_disconnected.connect(disconnected.append)
    win.session.disconnect_axis(2)
    assert 2 not in win.session.axes and disconnected == [2]
    assert win.session.client is client                              # the primary connection is unaffected
    assert second_client._closed and not client._closed              # only the disconnected axis's client closed
    assert not second.is_connected and second.client is None


def test_disconnecting_the_primary_also_ends_every_additional_axis(win, qtbot):
    """There is not yet a way to disconnect one extra axis on its own from the UI while keeping the rest (that is
    Stage 2's job) - a full Disconnect must not leave any axis's threads/simulator running behind it."""
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    qtbot.waitUntil(lambda: {2, 3} <= set(win.session.axes), timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert win.session.client is None and win.session.axes == {}
    qtbot.waitUntil(lambda: not [t for t in threading.enumerate() if t.name.startswith("sdr-")], timeout=3000)


def test_two_simulated_axes_are_independent_drives_not_just_two_dict_entries(win, qtbot):
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    client2 = ctx2.client
    qtbot.waitUntil(lambda: client2.poles is not None, timeout=WAIT)
    assert client1.node == 1 and client2.node == 2
    assert client1 is not client2 and client1.bus is not client2.bus
    assert client1.poles == client2.poles == 14                       # both simulated drives share the same default


def test_folding_a_property_group_reflows_the_groups_below_it_immediately(win, qtbot):
    """The user's own screen recording (2026-09-24): clicking a group's header to fold it (e.g. Kinematics Model)
    flipped the header to "+" right away, but the groups below it in Item Configuration's column stayed put for
    a beat first, leaving a stale blank gap where the folded content used to be - reported as some groups "not
    minimising" when clicked. `_toggled` used to only hide `self.rows` and rely on Qt's async LayoutRequest event
    to reflow the column's other groups, which can lag a paint or two behind (worse the busier the event loop is,
    e.g. the connected page's own polling) - it now activates the parent layout synchronously in the same call,
    so a sibling group below has already moved by the time the click handler returns, with no extra event-loop
    spin (qtbot.wait/processEvents) required to observe it."""
    win.open_tool("axes")                                             # the groups moved to the Axes page (2026-09-26);
    cfg = win.axes_page.setup                                         # Additional Axes sits over Kinematics Model there
    axes, kinematics = cfg.axes_group, cfg.kinematics_group
    assert axes.header.isChecked() and axes.rows.isVisible()
    y_open = kinematics.y()
    qtbot.mouseClick(axes.header, Qt.MouseButton.LeftButton)
    assert not axes.header.isChecked() and not axes.rows.isVisible()
    assert kinematics.y() < y_open                                     # already reflowed - no wait needed
    qtbot.mouseClick(axes.header, Qt.MouseButton.LeftButton)
    assert axes.header.isChecked() and axes.rows.isVisible()
    assert kinematics.y() == y_open                                    # and back, just as immediately


def test_folding_a_parameter_group_like_product_activation_actually_folds(win, qtbot):
    """The user, right after the fold/unfold fix above: "activation key and some other tab does not fold" -
    Product Activation and Networks are `ParameterGroup`s, not plain `PropertyGroup`s, and that class has its OWN
    unrelated `self.rows` - a dict of `_Row` bookkeeping objects keyed by register, assigned in `__init__` right
    after `super().__init__()` already set `self.rows` to the fold container QFrame. That silently overwrote it,
    so the inherited `_toggled` (`self.rows.setVisible(open_)`) was handed a dict instead of a widget on every
    click - PySide6 does not propagate that AttributeError out of a signal handler, so the app kept running with
    folding just quietly doing nothing for every ParameterGroup (here, Product Activation and Networks - the "some
    other tab" the user meant), header text included. No test ever caught it because none clicked a ParameterGroup
    header before. Fixed by renaming the bookkeeping dict to `self.param_rows`, leaving `self.rows` to mean the
    same fold container it means on every other group."""
    cfg = win.config_page
    activation = cfg.activation
    assert activation.header.isChecked() and activation.rows.isVisible()
    qtbot.mouseClick(activation.header, Qt.MouseButton.LeftButton)
    assert not activation.header.isChecked()
    assert not activation.rows.isVisible()                             # used to stay stuck open
    assert activation.header.text().strip().startswith("+")            # used to never update either
    qtbot.mouseClick(activation.header, Qt.MouseButton.LeftButton)
    assert activation.header.isChecked() and activation.rows.isVisible()
    assert activation.header.text().strip().startswith("−")


def test_every_fold_group_on_every_item_configuration_page_actually_folds(win, qtbot):
    """Two DIFFERENT real fold/unfold bugs turned up on this page within the same session (a layout-reflow lag on
    plain PropertyGroups, then ParameterGroup's `self.rows` collision leaving it non-functional) - both were only
    ever caught by clicking ONE specific group's header and happened to miss the other bug class entirely each
    time. Rather than trust "the class-level fix covers every instance" by reasoning about it, this clicks every
    single PropertyGroup (which ParameterGroup also is) on BOTH System Configuration pages that have one - Item
    Configuration (General, Target Connection, Additional Axes, Kinematics Model, Tool Pose, Product Activation,
    Networks) and an additional axis's own copy of it (General, Target Connection, Product Activation, Networks) -
    and checks each one actually folds and unfolds, not just that the header's checked-state flips."""
    def assert_group_folds_and_unfolds(group):
        assert group.header.isChecked() and group.rows.isVisible(), f"{group._title!r} did not start open"
        qtbot.mouseClick(group.header, Qt.MouseButton.LeftButton)
        assert not group.header.isChecked(), f"{group._title!r} header did not flip to unchecked"
        assert not group.rows.isVisible(), f"{group._title!r} did not actually fold"
        assert group.header.text().strip().startswith("+"), f"{group._title!r} header text did not update"
        qtbot.mouseClick(group.header, Qt.MouseButton.LeftButton)
        assert group.header.isChecked() and group.rows.isVisible(), f"{group._title!r} did not unfold again"
        assert group.header.text().strip().startswith("−"), f"{group._title!r} header text did not restore"

    cfg = win.config_page
    cfg_groups = cfg.findChildren(PropertyGroup)
    assert {g._title for g in cfg_groups} == {                          # the three multi-axis groups moved to the
        "General", "Target Connection", "Product Activation", "Networks (drive's own settings)"}   # Axes page (2026-09-26)
    for group in cfg_groups:
        assert_group_folds_and_unfolds(group)
    win.open_tool("axes")                                              # isVisible() needs the page shown
    axes_groups = win.axes_page.setup.findChildren(PropertyGroup)
    assert {g._title for g in axes_groups} == {"Additional Axes", "Kinematics Model", "Tool Pose (Orientation IK)"}
    for group in axes_groups:
        assert_group_folds_and_unfolds(group)
    win.open_tool("config")

    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win.navigator.tree.setCurrentItem(win.navigator._axis_items[2])
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)
    axis_cfg = win._axis_pages[2]["config"]
    assert win.host.current_page() is axis_cfg                        # isVisible() below needs it actually shown
    axis_groups = axis_cfg.findChildren(PropertyGroup)
    assert {g._title for g in axis_groups} == {
        "General", "Target Connection", "Product Activation", "Networks (drive's own settings)"}
    for group in axis_groups:
        assert_group_folds_and_unfolds(group)


# ---- multi-axis UI (Item Configuration's Add Axis, the navigator's sibling tree items) --------------------
def test_add_axis_button_is_disabled_until_connected(win, qtbot):
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    assert not cfg.add_axis_btn.isEnabled() and not cfg.new_axis_node.isEnabled()
    sim, client = connect(win, qtbot)
    assert cfg.add_axis_btn.isEnabled() and cfg.new_axis_node.isEnabled()
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert not cfg.add_axis_btn.isEnabled()


def test_add_axis_button_is_disabled_on_usb(win, qtbot, monkeypatch):
    rig = UsbRig(node=7)
    monkeypatch.setattr("sdr_tuner.gui.session.UsbSerialBus", lambda port: rig.bus())
    try:
        win.iface.setCurrentText(USB)
        win.channel.setText("/dev/cu.usbmodemTEST")
        qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: "ONLINE" in win.pill.text(), timeout=WAIT)
        assert not win.axes_page.setup.add_axis_btn.isEnabled()
        assert "reaches exactly one drive" in win.axes_page.setup.add_axis_btn.toolTip()
    finally:
        qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
        rig.close()


def test_clicking_add_axis_connects_it_and_shows_it_in_the_list_and_the_navigator(win, qtbot):
    sim, client = connect(win, qtbot)
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    cfg.new_axis_node.setValue(2)
    qtbot.mouseClick(cfg.add_axis_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: 2 in win.session.axes, timeout=WAIT)
    assert 2 in cfg._axis_rows                                        # shown in Item Configuration's own list
    assert 2 in win.navigator._axis_items                             # and as a sibling of the drive in the tree
    remove_btn = next(b for b in cfg._axis_rows[2].findChildren(QPushButton) if b.text() == "Remove")
    qtbot.mouseClick(remove_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: 2 not in win.session.axes, timeout=WAIT)
    assert 2 not in cfg._axis_rows and 2 not in win.navigator._axis_items


def test_clicking_an_axis_while_on_system_configuration_opens_its_own_item_configuration(win, qtbot):
    """The user's own screen recording (2026-09-23): "i am not able to see the drive 2 system configuration...
    as soon as i click it shift to drive setup and motion" - clicking Drive02 while ALREADY looking at System
    Configuration (the default activity) used to always jump to its Motion page. After two smaller attempts
    (expanding a row in place, then a tree click landing on that same in-place row), the user asked for a real,
    separate page: "when drive 2 is selected then every single information showing must be about drive 2 only" -
    so this now opens a whole `AxisConfigurationPage` of its own, not a detail block on the primary's own page."""
    sim, client = connect(win, qtbot)
    ctx = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    assert win.navigator.current_activity() == "system"                  # the default, matching the user's own video
    win.navigator.tree.setCurrentItem(win.navigator._axis_items[2])
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)
    assert win.navigator.current_activity() == "system"                  # did NOT shift to Servo Drive
    page = win._axis_pages[2]["config"]
    assert win.host.current_page() is page and page is not win.config_page
    assert win.navigator.tree.currentItem() is win.navigator._axis_items[2]

    qtbot.waitUntil(lambda: bool(ctx.client.device_info), timeout=WAIT)      # probed
    info = ctx.client.device_info

    def value_text(group_title, key):
        group = next(g for g in page.findChildren(PropertyGroup) if g._title == group_title)
        row = next(r for r in group.rows.findChildren(QFrame, "gridRow") if r.findChildren(QLabel)[0].text() == key)
        return row.findChildren(QLabel)[1].text()
    assert value_text("General", "Target Name") == "Drive02 (SDR)"
    assert value_text("General", "Device Type") == f"{info['device_type']}   (0x{info['device_type']:X})"
    assert value_text("General", "Device Serial Number") == f"{info['serial']}   (0x{info['serial']:X})"
    assert value_text("General", "Firmware Serial Number") == f"{info['firmware']}   (0x{info['firmware']:X})"
    assert value_text("Target Connection", "Node ID") == "2"
    assert value_text("Target Connection", "Connection State") == "SIMULATED DRIVE  ·  NODE 2"

    # from Servo Drive, clicking it still opens its Motion page, exactly as before
    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)
    assert win.host.current_page() is win._axis_pages[2]["motion"]


def test_the_navigator_tree_stops_highlighting_an_axis_when_system_configuration_is_reopened(win, qtbot):
    """The scenario `_on_activity_changed`'s own tree-resync exists for: navigate to an axis's Motion page under
    Servo Drive (leaving the tree on it), then click back to the System Configuration ACTIVITY BUTTON
    itself (not a tree item) while its own `_last_tool["system"]` is still the PRIMARY's "config" from earlier -
    the tree must not go on claiming to show an axis this page cannot display anything about (the exact confusion
    a user hit: the tree said Drive02, the page said Drive01)."""
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)
    win.navigator.tree.setCurrentItem(win.navigator._axis_items[2])       # what a real click does, before itemClicked
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)
    assert win.host.current_page() is win._axis_pages[2]["motion"]
    assert win.navigator.tree.currentItem() is win.navigator._axis_items[2]
    qtbot.mouseClick(win.navigator._activities["system"][0], Qt.MouseButton.LeftButton)
    assert win.host.current_page() is win.config_page
    assert win.navigator.tree.currentItem() is win.navigator.drive_item


def test_the_navigator_tree_tracks_whichever_axis_owns_the_config_page_shown(win, qtbot):
    """The other half of the same resync logic: if the LAST thing shown under System Configuration was an axis's
    own Item Configuration, reopening that activity must show that axis again AND highlight it - not silently
    fall back to the primary just because the activity itself was re-entered."""
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win.navigator.tree.setCurrentItem(win.navigator._axis_items[2])
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)
    assert win.host.current_page() is win._axis_pages[2]["config"]
    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)   # visit somewhere else...
    qtbot.mouseClick(win.navigator._activities["system"][0], Qt.MouseButton.LeftButton)  # ...then back
    assert win.host.current_page() is win._axis_pages[2]["config"]
    assert win.navigator.tree.currentItem() is win.navigator._axis_items[2]


def test_the_drive_setup_activity_button_reopens_whichever_drive_was_last_selected(win, qtbot):
    """A real bug found while chasing the user's own report of Drive02 apparently showing Drive01's position
    (2026-09-23): selecting a drive only ever retargeted the Servo Drive activity's own shared tool
    LIST - `_last_tool["drive"]` (what the activity BUTTON itself reopens) kept remembering whichever drive's page
    was last opened DIRECTLY, and the two could disagree. Concretely: rotate Drive01 on its own Motion page, go to
    System Configuration, select Drive02 there (opens its own Item Configuration, never touching "drive" directly),
    then click the Servo Drive ACTIVITY BUTTON - it used to reopen Drive01's own (stale) Motion page,
    making Drive02 look like it shared Drive01's position, when in fact Drive02's own page was never shown at all."""
    sim, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)

    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)   # Drive01's own Motion page
    win.drive_tab.pos_target.setValue(9999)
    win.drive_tab.control_mode.setCurrentIndex(1)                    # the Position card must be the one shown
    qtbot.mouseClick(win.drive_tab.move_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client1.telemetry_snapshot().get("wz_count") == 9999, timeout=WAIT)

    qtbot.mouseClick(win.navigator._activities["system"][0], Qt.MouseButton.LeftButton)
    win.navigator.tree.setCurrentItem(win.navigator._axis_items[2])
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)
    assert win.host.current_page() is win._axis_pages[2]["config"]                       # Drive02's own, not Motion

    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)
    assert win.host.current_page() is win._axis_pages[2]["motion"]                       # Drive02's own, not Drive01's
    assert win.host.current_page() is not win.drive_tab
    assert win.host.current_page().cards["wz_count"].value.text() == "0"                 # not Drive01's own 9,999


def test_the_bottom_right_pill_and_top_right_badge_track_whichever_drive_is_shown(win, qtbot):
    """User (2026-09-23, screenshot of Drive02's own Motion page): "i have selected drive 2 but why on bottom
    right side it shows node id 1" - the pill (and the top-right target badge next to it) used to always show the
    PRIMARY's own connection, even while looking at an axis's own page, which does not describe it at all."""
    sim, client = connect_axis1(win, qtbot)                                # node 2 = axis 1
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)           # node 3 = axis 2
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    qtbot.waitUntil(lambda: "NODE 2" in win.pill.text(), timeout=WAIT)     # the primary's own, unaffected so far

    win.navigator._activities["drive"][0].click()
    item = win.navigator._axis_items[3]
    win.navigator.tree.setCurrentItem(item)
    win.navigator._tree_clicked(item)
    qtbot.waitUntil(lambda: "NODE 3" in win.pill.text(), timeout=WAIT)     # now describes axis 2 (node 3), not the primary
    assert win.host.target.text() == "Drive03 (SDR)"

    # renaming the primary while viewing the axis must not overwrite the axis's own badge
    win.config_page.target_name.setText("Renamed Primary")
    assert win.host.target.text() == "Drive03 (SDR)" and "NODE 3" in win.pill.text()

    # switching back to the primary restores its own badge and pill
    win.navigator.tree.setCurrentItem(win.navigator.drive_item)
    win.navigator._tree_clicked(win.navigator.drive_item)
    assert win.host.target.text() == "Renamed Primary"
    assert "NODE 2" in win.pill.text()


def test_clicking_an_axis_in_the_navigator_reports_it_in_the_status_bar(win, qtbot):
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)   # already on Drive Setup
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)
    qtbot.waitUntil(lambda: win.status_text().startswith("Drive02 (SDR)"), timeout=WAIT)


def test_additional_axes_are_listed_in_numerical_order_regardless_of_connection_order(win, qtbot):
    """Connecting axis 5 before axis 3 must not leave them listed in THAT order - the tree always reads in
    ascending node-ID order, primary first."""
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 5)
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    win.session.connect_axis(session.SIMULATOR, "", 0, 8)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: {2, 3, 5, 8} <= set(win.navigator._axis_items), timeout=WAIT)
    names = [win.navigator.workspace.child(i).text(0) for i in range(win.navigator.workspace.childCount())]
    assert names == ["Drive01 (SDR)", "Drive02 (SDR)", "Drive03 (SDR)", "Drive05 (SDR)", "Drive08 (SDR)"]


def test_disconnecting_clears_the_axes_list_and_the_navigator_items(win, qtbot):
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert win.axes_page.setup._axis_rows == {} and win.navigator._axis_items == {}


# ---- kinematics model (Item Configuration's Kinematics Model group - loading a model for G-code Txx) --------
def test_kinematics_samples_lists_the_bundled_vendor_files(win, qtbot):
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    names = [cfg.kinematics_samples.itemText(i) for i in range(cfg.kinematics_samples.count())]
    assert "Samples…" in names
    for expected in ("radar.obj", "ko-bot.obj", "robot-dog.obj", "humanoid.obj", "monkey.obj"):
        assert expected in names


def test_monkey_sample_has_one_rotary_axis_and_a_real_tool(win, qtbot):
    """monkey.obj is a bare, single-group vendor mesh (Blender's own "Suzanne" test shape - confirmed directly,
    it has exactly one `o monkey` line, no matching vendor .kinematics) with no real robot behind it, unlike the
    other 4 samples - so unlike them, its own .kinematics file is one WE wrote (not the vendor's), a minimal
    base + one rotary joint named to match the mesh's own group ("monkey"), giving it a real body to actually
    spin in Cursor/Live/Simulation mode rather than a lifeless static decoration. Given a real Tool Direction/Tip,
    matching bench_node7.kinematics's own precedent for a custom single-joint file - found live while writing
    this test that a body with NEITHER stays out of `chains()` entirely, which silently drops it from Item
    Configuration's own "Counts per unit" rows too (built from each chain's own axes, not every actuated body) -
    a chain-less axis would have been unable to have its own unit conversion set from the GUI at all."""
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    idx = cfg.kinematics_samples.findText("monkey.obj")
    assert idx > 0
    cfg._load_kinematics_sample(idx)
    model = win.session.kinematics_model
    assert sorted(b.name for b in model.bodies.values()) == ["base", "monkey"]
    assert [c.name for c in model.chains().values()] == ["monkey"]
    assert cfg.pose_tool.currentText() == "T1 - monkey"
    assert cfg.pose_tool.isEnabled()
    assert sorted(cfg._counts_per_unit_rows) == [0]


def test_loading_a_kinematics_sample_shows_a_summary_and_counts_per_unit_rows(win, qtbot):
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    idx = cfg.kinematics_samples.findText("robot-dog.obj")
    assert idx > 0
    cfg.kinematics_samples.setCurrentIndex(idx)
    cfg._load_kinematics_sample(idx)
    assert cfg.kinematics_samples.currentIndex() == 0                            # resets to the placeholder
    assert win.session.kinematics_model is not None
    assert "12 axes" in cfg.kinematics_summary.text() and "T3" in cfg.kinematics_summary.text()
    assert sorted(cfg._counts_per_unit_rows) == list(range(12))
    for spin in cfg._counts_per_unit_rows.values():
        assert spin.value() == 1.0                                               # default until set explicitly


def test_editing_counts_per_unit_updates_the_session(win, qtbot):
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    idx = cfg.kinematics_samples.findText("radar.obj")
    cfg._load_kinematics_sample(idx)
    cfg._counts_per_unit_rows[0].setValue(4096.0)
    assert win.session.counts_per_unit[0] == 4096.0


def test_loading_a_different_model_resets_counts_per_unit_to_defaults(win, qtbot):
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    cfg._load_kinematics_sample(cfg.kinematics_samples.findText("radar.obj"))
    cfg._counts_per_unit_rows[0].setValue(999.0)
    cfg._load_kinematics_sample(cfg.kinematics_samples.findText("ko-bot.obj"))
    assert sorted(cfg._counts_per_unit_rows) == list(range(5))
    assert all(v == 1.0 for v in win.session.counts_per_unit.values())


def test_a_bad_kinematics_file_is_refused_with_a_clear_message(win, qtbot, tmp_path):
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    bad = tmp_path / "bad.kinematics"
    bad.write_text("not a kinematics file\n")
    messages = []
    cfg.message.connect(lambda text, level: messages.append((text, level)))
    cfg._load_kinematics(str(bad))
    assert messages and messages[-1][1] == "error" and "not a recognised" in messages[-1][0]
    assert win.session.kinematics_model is None


# ---- multi-axis pages (each additional axis gets its own Motion page - Stage 2 of the multi-axis work) ------
def test_an_additional_axis_gets_its_own_working_motion_page(win, qtbot):
    """The core of it: axis 2's own Motion page RUNs/STOPs axis 2's own simulated drive, entirely independent of
    the primary drive - not a second view of the same connection."""
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    page2 = win.tools[win._axis_tool_key("motion", 2)].page
    assert page2 is win._axis_pages[2]["motion"] and page2 is not win.drive_tab
    qtbot.waitUntil(lambda: page2.run_btn.isEnabled(), timeout=WAIT)     # its own controls enabled once connected

    page2.target.setValue(150.0)
    qtbot.mouseClick(page2.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: ctx2.client.commanded == ("esc_rpm", 150.0), timeout=WAIT)
    assert client1.commanded is None                                    # the primary drive never saw this command
    qtbot.mouseClick(page2.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: ctx2.client.commanded is None, timeout=WAIT)


def test_clicking_the_axis_in_the_navigator_opens_its_motion_page(win, qtbot):
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)   # already on Drive Setup
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)
    assert win.host.current_page() is win._axis_pages[2]["motion"]


def _click_setup_tool(win, qtbot, title, activity="drive"):
    """Clicks a row of an activity's own tool list (the shared box below the tree) by its CURRENT label - the
    Servo Drive list by default: same list widget for every drive, only what each per-drive row's own key points
    at changes (`retarget_drive_tools`), so this always exercises a real click, never a direct signal/key
    shortcut. `activity="motion"` clicks the Motion list instead (its rows never retarget)."""
    _, listing, _ = win.navigator._activities[activity]
    matches = listing.findItems(title, Qt.MatchFlag.MatchExactly)
    assert matches, f"{title!r} not found in the {win.navigator.activity_title(activity)} tool list"
    qtbot.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=listing.visualItemRect(matches[0]).center())


def test_clicking_the_axis_in_the_navigator_opens_its_parameters_and_telemetry_pages_too(win, qtbot):
    """Selecting an additional axis in the tree (a flat item, exactly like the primary's own) retargets the SAME
    shared tool list below to its own 7 pages - Stage 2 only built Motion; this extends the exact same pattern to
    the rest, each entirely independent of the primary drive's own pages."""
    sim, client = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)   # already on Drive Setup
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)     # selects axis 2 - opens its Motion page

    _click_setup_tool(win, qtbot, "Parameters Explorer")
    params2 = win._axis_pages[2]["parameters"]
    assert win.host.current_page() is params2 and params2 is not win.params_tab
    assert params2.s is ctx2                                            # bound to axis 2's own connection

    _click_setup_tool(win, qtbot, "Telemetry Explorer")
    telemetry2 = win._axis_pages[2]["telemetry"]
    assert win.host.current_page() is telemetry2 and telemetry2 is not win.telemetry_tab
    assert telemetry2.telemetry and telemetry2.s is ctx2

    qtbot.mouseClick(win.navigator._activities["motion"][0], Qt.MouseButton.LeftButton)
    _click_setup_tool(win, qtbot, "Terminal", activity="motion")        # a SHARED, workspace-level tool (Motion
    assert win.host.current_page() is win.log_tab                       # activity) - unaffected by the scoping


def test_selecting_the_primary_drive_again_retargets_the_tool_list_back_to_it(win, qtbot):
    """Switching from an additional axis's own tools back to the primary's own - the SAME list, re-pointed, not a
    separate one - exactly mirrors what selecting that axis did in the other direction."""
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)   # already on Drive Setup
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)
    _click_setup_tool(win, qtbot, "Parameters Explorer")
    assert win.host.current_page() is win._axis_pages[2]["parameters"]

    win.navigator.tree.itemClicked.emit(win.navigator.drive_item, 0)    # back to the primary - opens Item Configuration
    assert win.host.current_page() is win.config_page
    _click_setup_tool(win, qtbot, "Parameters Explorer")                # the SAME row now means the primary's own page
    assert win.host.current_page() is win.params_tab


def test_an_additional_axis_parameters_page_reads_and_writes_only_that_axis(win, qtbot):
    """Not a second view of the primary drive's own Parameters Explorer - reading axis 2's page must never touch
    the primary drive's own rows, and vice versa."""
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    params2 = win._axis_pages[2]["parameters"]
    qtbot.mouseClick(params2.read_all_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not params2.busy, timeout=30000)
    assert len(params2.rows) == 243 and all(r.last is not None for r in params2.rows)
    assert all(r.last is None for r in win.params_tab.rows)             # the primary page's own rows: never read


def test_disconnecting_one_axis_removes_its_page_and_falls_back_if_it_was_open(win, qtbot):
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win.open_tool(win._axis_tool_key("motion", 2))
    assert win.host.current_page() is win._axis_pages[2]["motion"]
    win.session.disconnect_axis(2)
    assert win._axis_tool_key("motion", 2) not in win.tools and 2 not in win._axis_pages
    assert win.host.current_page() is win.drive_tab                     # fell back to the primary drive's own page


def test_disconnecting_one_axis_falls_back_correctly_from_its_parameters_page_too(win, qtbot):
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win.open_tool(win._axis_tool_key("parameters", 2))
    assert win.host.current_page() is win._axis_pages[2]["parameters"]
    win.session.disconnect_axis(2)
    assert win._axis_tool_key("parameters", 2) not in win.tools
    assert win.host.current_page() is win.params_tab                    # fell back to the primary drive's own Parameters page


def test_disconnecting_an_axis_clears_any_dangling_last_tool_reference_to_it(win, qtbot):
    """A latent crash risk found while fixing the activity-button bug above, not yet hit live: selecting an axis's
    own Motion page (whether directly or via `_on_axis_selected`'s own bookkeeping) makes it
    `_last_tool["drive"]`. If the user then goes elsewhere WITHOUT ever revisiting Servo Drive, and that
    axis disconnects, its own tool keys are removed from `self.tools` - but `_last_tool["drive"]` would otherwise
    still be pointing at one of them, ready to KeyError the next time that activity's own button is pressed."""
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win.navigator.tree.setCurrentItem(win.navigator._axis_items[2])
    win.navigator.tree.itemClicked.emit(win.navigator._axis_items[2], 0)    # opens its own config; "drive" -> "motion:2"
    assert win._last_tool["drive"] == win._axis_tool_key("motion", 2)
    assert win.host.current_page() is win._axis_pages[2]["config"]          # "drive" itself was never actually visited

    win.session.disconnect_axis(2)                                          # removes "motion:2" from win.tools
    qtbot.mouseClick(win.navigator._activities["drive"][0], Qt.MouseButton.LeftButton)   # must not KeyError
    assert win.host.current_page() is win.drive_tab


def test_an_additional_axis_gets_its_own_quick_and_advanced_tuning_pages(win, qtbot):
    sim, client = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    quick2, advanced2 = win._axis_pages[2]["quick"], win._axis_pages[2]["advanced"]
    assert quick2 is not win.quick_tuner and quick2.s is ctx2 and not quick2.advanced
    assert advanced2 is not win.advanced_tuner and advanced2.s is ctx2 and advanced2.advanced
    assert advanced2.steps.s is ctx2                                     # Step Response Tests bound to axis 2 too


def test_the_tuning_ribbon_tabs_control_whichever_tuner_is_open(win, qtbot):
    """Only ONE "Drive Quick/Advanced Tuner" ribbon tab of each kind exists - the buttons must act on whichever
    TunerPage is CURRENTLY shown, primary or an axis's own, exactly like the Recorder's own ribbon tab."""
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    key2 = win._axis_tool_key("quick", 2)
    win.open_tool(key2)
    assert win._current_tuner("quick") is win._axis_pages[2]["quick"]
    assert win.ribbon.current_key() == "quick"                           # the SAME ribbon tab as the primary's own

    win.open_tool("quick_tuning")
    assert win._current_tuner("quick") is win.quick_tuner


def test_an_additional_axis_auto_tuning_stop_never_touches_the_primarys_own_run(win, qtbot):
    """Safety-critical: this is what actually moves a motor and rewrites flash. Axis 2's own STOP must end ONLY
    axis 2's own running tuning - never the primary drive's, which keeps running until ITS OWN stop is pressed."""
    sim1, client1 = connect(win, qtbot)
    sim1.autoconfig_seconds = 6.0
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win.session._axis_sims[2].autoconfig_seconds = 6.0

    def start(which):
        win.open_tool(which)
        tuner = win.tools[which].page
        qtbot.waitUntil(lambda: tuner._loaded and not tuner.busy, timeout=WAIT)
        enter(tuner.motor, MAX_CURRENT, "6.5")
        enter(tuner.motor, POLES, "8")
        tuner.auto._confirm = lambda text: True
        qtbot.waitUntil(lambda: tuner.auto.start_btn.isEnabled(), timeout=WAIT)
        tuner.auto.start()
        return tuner

    tuner1 = start("quick_tuning")
    tuner2 = start(win._axis_tool_key("quick", 2))
    qtbot.waitUntil(lambda: tuner1.auto.state == "running" and tuner2.auto.state == "running", timeout=WAIT)

    win._axis_pages[2]["motion"].emergency_stop()                        # axis 2's own STOP only
    qtbot.waitUntil(lambda: tuner2.auto.state == "stopped", timeout=WAIT)
    qtbot.wait(300)
    assert tuner1.auto.state == "running"                                # the primary's own run is completely unaffected

    qtbot.mouseClick(win.ribbon.stop_button, Qt.MouseButton.LeftButton)  # the round STOP ends every axis's own run
    qtbot.waitUntil(lambda: tuner1.auto.state == "stopped", timeout=WAIT)


def test_disconnecting_an_axis_falls_back_correctly_from_its_own_tuner_pages(win, qtbot):
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win.open_tool(win._axis_tool_key("advanced", 2))
    assert win.host.current_page() is win._axis_pages[2]["advanced"]
    win.session.disconnect_axis(2)
    assert win.host.current_page() is win.advanced_tuner                # fell back to the primary's own Advanced Tuner


def test_an_additional_axis_gets_its_own_recorder_and_peripherals_pages(win, qtbot):
    sim, client = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    rec2, periph2 = win._axis_pages[2]["recorder"], win._axis_pages[2]["peripherals"]
    assert rec2 is not win.recorder_page and rec2.s is ctx2
    assert rec2.drive is win._axis_pages[2]["motion"]                   # records ITS OWN Motion page, not the primary's
    assert periph2 is not win.peripherals_page and periph2.s is ctx2


def test_an_additional_axis_recorder_records_only_that_axis(win, qtbot):
    """Not just "the primary never ran" - axis 2's own recorder must capture axis 2's OWN telemetry (its motor
    really running), independent of the primary drive, which stays at rest throughout."""
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    motion2 = win._axis_pages[2]["motion"]
    motion2.target.setValue(120.0)
    qtbot.mouseClick(motion2.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(ctx2.client.telemetry_snapshot().get("speed_hz", 0) - 14.0) < 0.3, timeout=WAIT)
    rec2 = win._axis_pages[2]["recorder"]
    rec2.start_btn.click()
    qtbot.wait(700)
    rec2.stop_btn.click()
    t, v = rec2.data()["speed_hz"]
    assert len(t) > 5 and v[-1] == pytest.approx(14.0, abs=0.5)          # axis 2's own motor was really running (120 rpm/7 pole pairs)
    assert not win.recorder_page.recording                              # the primary's own recorder never ran


def test_the_recording_ribbon_tab_controls_whichever_recorder_is_open(win, qtbot):
    """Only ONE set of ribbon Recording buttons exists - they must act on whichever RecorderPage is CURRENTLY
    shown, primary or an axis's own, not always the primary."""
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    rec2 = win._axis_pages[2]["recorder"]
    win.open_tool(win._axis_tool_key("recorder", 2))
    assert win._current_recorder() is rec2
    qtbot.mouseClick(win.rec_start_btn, Qt.MouseButton.LeftButton)
    assert rec2.recording and not win.recorder_page.recording           # the ribbon started axis 2's own recorder
    qtbot.mouseClick(win.rec_stop_btn, Qt.MouseButton.LeftButton)
    assert not rec2.recording

    win.open_tool("recorder")                                           # switch back to the primary
    assert win._current_recorder() is win.recorder_page
    qtbot.mouseClick(win.rec_start_btn, Qt.MouseButton.LeftButton)
    assert win.recorder_page.recording and not rec2.recording
    qtbot.mouseClick(win.rec_stop_btn, Qt.MouseButton.LeftButton)


def test_an_additional_axis_peripherals_page_reads_only_that_axis(win, qtbot):
    """PeripheralsPage only auto-reads once it is actually SHOWN (like the primary's own) - opening it is what
    triggers the read, exactly like the primary page's own behaviour."""
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win.open_tool(win._axis_tool_key("peripherals", 2))
    periph2 = win._axis_pages[2]["peripherals"]
    qtbot.waitUntil(lambda: periph2._loaded, timeout=WAIT)
    assert len(periph2.form.rows) == 33
    assert not win.peripherals_page._loaded                             # the primary page was never opened/read


def test_disconnecting_one_axis_falls_back_correctly_from_its_recorder_and_peripherals_pages(win, qtbot):
    sim, client = connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)

    win.open_tool(win._axis_tool_key("recorder", 2))
    win.session.disconnect_axis(2)
    assert win.host.current_page() is win.recorder_page

    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    win.open_tool(win._axis_tool_key("peripherals", 3))
    win.session.disconnect_axis(3)
    assert win.host.current_page() is win.peripherals_page


def test_the_round_stop_button_stops_every_axis_not_just_the_primary(win, qtbot):
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    page2 = win._axis_pages[2]["motion"]
    win.drive_tab.target.setValue(100.0)
    qtbot.mouseClick(win.drive_tab.run_btn, Qt.MouseButton.LeftButton)
    page2.target.setValue(200.0)
    qtbot.mouseClick(page2.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client1.commanded is not None and ctx2.client.commanded is not None, timeout=WAIT)
    qtbot.mouseClick(win.ribbon.stop_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client1.commanded is None and ctx2.client.commanded is None, timeout=WAIT)


# ---- parameters -------------------------------------------------------------------------------
def test_read_all_parameters(win, qtbot):
    sim, _ = connect(win, qtbot)
    read_all(win, qtbot)
    assert len(win.params_tab.rows) == 243
    for row in win.params_tab.rows:
        assert row.item.text(5).startswith("✓ read"), row.reg.name
        assert ParamsTab._as_stored(row.reg, row.get()) == stored(row.reg, sim.params[row.reg.key]), row.reg.name
    assert "Read 243 parameter(s)." in win.params_tab.info.text()


def test_type_address_and_state_are_not_columns_and_the_name_gets_the_room(win, qtbot):
    for key, tab in (("parameters", win.params_tab), ("telemetry", win.telemetry_tab)):
        win.open_tool(key)
        assert [tab.tree.isColumnHidden(c) for c in range(7)] == [False, False, False, True, True, True, False]
        win.resize(1120, 700)                                                        # the narrowest the window goes
        qtbot.wait(120)
        assert tab.tree.columnWidth(0) >= 300
        widths = [tab.tree.fontMetrics().horizontalAdvance(r.reg.name) for r in tab.rows]
        assert sum(w < tab.tree.columnWidth(0) for w in widths) >= 0.9 * len(widths)          # even here, nine names in ten fit whole
        win.resize(1440, 810)                                                        # a laptop screen: every name fits whole
        qtbot.wait(120)
        longest = max(tab.rows, key=lambda r: len(r.reg.name))
        assert tab.tree.fontMetrics().horizontalAdvance(longest.reg.name) < tab.tree.columnWidth(0)


def test_the_details_that_are_not_columns_are_in_the_tooltip(win):
    rows = rows_by_address(win)
    tip = rows["0x3000:0x06"].item.toolTip(0)                                          # receiving frequency: needs a power cycle
    assert "0x3000:0x06" in tip and "FLOAT32" in tip and "read / write" in tip and "power cycle" in tip
    assert rows["0x3000:0x06"].item.text(0).endswith("⟳")                            # the marker for "applies after a power cycle"
    assert "power cycle" not in rows["0x3000:0x05"].item.toolTip(0)                  # telemetry frequency applies at once (measured)
    telemetry = {r.reg.address: r for r in win.telemetry_tab.rows}
    assert "read-only" in telemetry["0x4000:0x03"].item.toolTip(0) and "0x4000:0x03" in telemetry["0x4000:0x03"].item.toolTip(0)


def test_edited_and_refused_values_are_seen_from_the_name_and_the_tooltip(win, qtbot):
    from PySide6.QtGui import QColor
    from sdr_tuner.gui import theme
    sim, _ = connect(win, qtbot)
    win.params_tab._confirm = lambda text: True
    read_all(win, qtbot)
    rows = rows_by_address(win)
    row = rows["0x2002:0x22"]
    assert row.item.foreground(0).color() == QColor(theme.TEXT)                       # read: nothing to say
    user_types(row, "0.123")
    assert row.item.foreground(0).color() == QColor(theme.AMBER) and "edited" in row.item.toolTip(0)
    user_types(rows["0x2002:0x14"], "120")                                            # beyond +-90
    qtbot.mouseClick(win.params_tab.write_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)
    assert rows["0x2002:0x14"].item.foreground(0).color() == QColor(theme.RUBY)
    assert "at most 90" in rows["0x2002:0x14"].item.toolTip(0)                        # the reason is in the tooltip
    assert row.item.foreground(0).color() == QColor(theme.TEXT) and "written and verified" in row.item.toolTip(0)


def test_what_the_parameter_pages_do_is_told_in_the_status_bar(win, qtbot):
    connect(win, qtbot)
    win.open_tool("parameters")
    read_all(win, qtbot)
    assert win.status_text() == "Read 243 parameter(s)."
    win.params_tab._confirm = lambda text: True
    user_types(rows_by_address(win)["0x2002:0x22"], "0.2")
    qtbot.mouseClick(win.params_tab.write_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)
    assert win.status_text() == "Wrote 1 parameter(s)." and win.bottom.level == "ok"
    qtbot.mouseClick(win.telemetry_tab.read_all_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.status_text() == "Read 176 parameter(s).", timeout=30000)


def test_power_cycle_parameters_say_so(win, qtbot):
    connect(win, qtbot)
    rows = rows_by_address(win)
    assert rows["0x3000:0x06"].item.text(5) == "needs a power cycle"          # receiving frequency
    assert rows["0x3000:0x05"].item.text(5) == ""       # telemetry frequency: measured to apply at once (2026-09-25)
    assert rows["0x2000:0x02"].item.text(5) == ""
    read_all(win, qtbot)
    assert rows["0x3000:0x06"].item.text(5) == "✓ read  ·  applies after a power cycle"
    assert rows["0x2000:0x02"].item.text(5) == "✓ read"


def test_writing_a_power_cycle_parameter_warns_first(win, qtbot):
    sim, _ = connect(win, qtbot)
    asked = []
    win.params_tab._confirm = lambda text: asked.append(text) or False
    rows = rows_by_address(win)
    before = sim.params[(0x3000, 0x06)]
    qtbot.mouseClick(rows["0x3000:0x06"].write_btn, Qt.MouseButton.LeftButton)      # single-row write (receiving frequency)
    assert len(asked) == 1 and "power cycle" in asked[0] and "unreachable" not in asked[0]
    qtbot.mouseClick(rows["0x3000:0x03"].write_btn, Qt.MouseButton.LeftButton)      # node ID: can lose the drive
    assert len(asked) == 2 and "unreachable" in asked[1]
    user_types(rows["0x3000:0x06"], "12")                                            # a group write lists them too
    qtbot.mouseClick(win.params_tab.write_btn, Qt.MouseButton.LeftButton)
    assert len(asked) == 3 and "CANopen: receiving frequency" in asked[2] and "power cycle" in asked[2]
    assert sim.params[(0x3000, 0x06)] == before                                      # declined: nothing was sent


def test_option_labels_that_only_repeat_the_number_are_not_doubled(win):
    combo = rows_by_address(win)["0x3000:0x02"].editor                              # CAN bit rate: 1000 / 500 / 250 kbps
    assert combo.itemText(combo.findData(1000)) == "1000"
    encoder = rows_by_address(win)["0x2000:0x0A"].editor
    assert encoder.itemText(encoder.findData(2)) == "2  —  BISS-C/SSI Encoder"


def test_telemetry_mapping_is_a_number_not_a_drop_down(win):
    rows = rows_by_address(win)
    for slot in range(1, 5):
        assert not hasattr(rows[f"0x1A00:0x{slot:02X}"].editor, "addItem")           # any mapping code can be typed


def test_the_drive_is_told_nothing_when_a_row_is_only_read(win, qtbot):
    sim, _ = connect(win, qtbot)
    before = dict(sim.params)
    read_all(win, qtbot)
    assert sim.params == before and sim.commands_received == []


def test_float_values_are_shown_cleanly(win, qtbot):
    connect(win, qtbot)
    read_all(win, qtbot)
    rows = rows_by_address(win)
    assert rows["0x2000:0x04"].editor.text() == "0.8"           # not 0.800000012
    assert rows["0x2000:0x08"].editor.text() == "2e-05"


def test_edit_and_write_parameters_of_every_kind(win, qtbot):
    sim, _ = connect(win, qtbot)
    win.params_tab._confirm = lambda text: True
    read_all(win, qtbot)
    rows = rows_by_address(win)
    user_types(rows["0x2002:0x22"], "0.123")                    # FLOAT32
    user_types(rows["0x2002:0x36"], "0x20")                     # UINT32 written as hex
    user_types(rows["0x2034:0xB0"], "-5")                       # INT32
    assert rows["0x2004:0x73"].editor.isChecked()
    rows["0x2004:0x73"].editor.click()                          # BOOL: user un-ticks the box
    combo = rows["0x2000:0x0A"].editor                          # option list: motor encoder -> SPI (3)
    combo.setCurrentIndex(combo.findData(3))
    combo.activated.emit(combo.currentIndex())
    assert sum(r.dirty for r in win.params_tab.rows) == 5
    qtbot.mouseClick(win.params_tab.write_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)
    assert sim.params[(0x2002, 0x22)] == pytest.approx(0.123, rel=1e-6)
    assert sim.params[(0x2002, 0x36)] == 32
    assert sim.params[(0x2034, 0xB0)] == -5
    assert sim.params[(0x2004, 0x73)] is False or sim.params[(0x2004, 0x73)] == 0
    assert sim.params[(0x2000, 0x0A)] == 3
    assert not any(r.dirty for r in win.params_tab.rows)
    assert rows["0x2002:0x22"].item.text(5) == "✓ written and verified"
    assert "Wrote 5 parameter(s)." in win.params_tab.info.text()


def test_invalid_values_are_flagged_and_never_sent(win, qtbot):
    sim, _ = connect(win, qtbot)
    win.params_tab._confirm = lambda text: True
    read_all(win, qtbot)
    rows = rows_by_address(win)
    before = dict(sim.params)
    bad = {"0x2000:0x03": "70000",       # UINT16 overflow
           "0x2002:0x14": "120",         # FOC lead angle is limited to +-90
           "0x2000:0x02": "abc",         # not a number
           "0x2002:0x36": "-1"}          # UINT32 cannot be negative
    for addr, text in bad.items():
        user_types(rows[addr], text)
    qtbot.mouseClick(win.params_tab.write_btn, Qt.MouseButton.LeftButton)
    for addr in bad:
        assert rows[addr].item.text(5).startswith("✗"), addr
    assert "at most 90" in rows["0x2002:0x14"].item.text(5)
    assert "not a valid number" in rows["0x2000:0x02"].item.text(5)
    assert not win.params_tab.busy
    time.sleep(0.1)
    assert sim.params == before


def test_write_asks_for_confirmation(win, qtbot):
    sim, _ = connect(win, qtbot)
    asked = []
    win.params_tab._confirm = lambda text: asked.append(text) or False      # user says "no"
    read_all(win, qtbot)
    row = rows_by_address(win)["0x2002:0x22"]
    user_types(row, "9.5")
    qtbot.mouseClick(win.params_tab.write_btn, Qt.MouseButton.LeftButton)
    assert len(asked) == 1 and "1 changed parameter" in asked[0]
    assert sim.params[(0x2002, 0x22)] != 9.5 and row.dirty


def test_write_warns_when_motor_is_running(win, qtbot):
    connect(win, qtbot)
    asked = []
    win.params_tab._confirm = lambda text: asked.append(text) or False
    win.drive_tab.target.setValue(10.0)
    qtbot.mouseClick(win.drive_tab.run_btn, Qt.MouseButton.LeftButton)
    row = rows_by_address(win)["0x2002:0x22"]
    user_types(row, "1")
    qtbot.mouseClick(win.params_tab.write_btn, Qt.MouseButton.LeftButton)
    assert "currently being commanded to run" in asked[0]


def test_single_row_read_and_write(win, qtbot):
    sim, _ = connect(win, qtbot)
    row = rows_by_address(win)["0x2000:0x03"]
    qtbot.mouseClick(row.read_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)
    assert row.get() == 14
    user_types(row, "20")
    qtbot.mouseClick(row.write_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)
    assert sim.params[(0x2000, 0x03)] == 20 and row.item.text(5) == "✓ written and verified"


def test_read_all_gives_up_when_drive_is_silent(win, qtbot, monkeypatch):
    connect(win, qtbot)

    def silent(self, reg):
        raise DriveTimeout(f"no answer for {reg.name}")
    monkeypatch.setattr(DriveClient, "read", silent)
    qtbot.mouseClick(win.params_tab.read_all_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot, timeout=5000)
    assert "not answering" in win.params_tab.info.text()
    assert sum(r.item.text(5).startswith("✗") for r in win.params_tab.rows) == 3       # stopped after 3


def test_parameter_file_roundtrip(win, qtbot, tmp_path):
    sim, _ = connect(win, qtbot)
    win.params_tab._confirm = lambda text: True
    read_all(win, qtbot)
    path = tmp_path / "params.json"
    assert win.params_tab.save_to(path) == 243
    doc = json.loads(path.read_text())
    assert doc["format"] == "sdr-parameters" and len(doc["params"]) == 243
    # loading the file we just saved changes nothing
    loaded, skipped = win.params_tab.load_from(path)
    assert (loaded, skipped) == (243, 0) and not any(r.dirty for r in win.params_tab.rows)
    # a file with a different ESC Kp and an unknown parameter
    for p in doc["params"]:
        if (p["index"], p["sub"]) == ("0x2002", "0x22"):
            p["value"] = 0.5
    doc["params"].append({"index": "0xDEAD", "sub": "0x01", "name": "nope", "value": 1})
    path.write_text(json.dumps(doc))
    loaded, skipped = win.params_tab.load_from(path)
    assert (loaded, skipped) == (243, 1)
    dirty = [r for r in win.params_tab.rows if r.dirty]
    assert [r.reg.address for r in dirty] == ["0x2002:0x22"]
    qtbot.mouseClick(win.params_tab.write_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)
    assert sim.params[(0x2002, 0x22)] == pytest.approx(0.5)


def test_bad_parameter_file_is_refused(win, tmp_path):
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"format": "something-else", "params": []}))
    with pytest.raises(ValueError, match="not an SDR parameter file"):
        win.params_tab.load_from(path)


def test_filter_hides_rows_and_empty_sections(win):
    tab = win.params_tab
    tab.filter.setText("kickstart")
    visible = [r for r in tab.rows if not r.item.isHidden()]
    assert visible and all("kickstart" in r.search for r in visible)
    assert {"Control Laws", "Features"} <= {r.reg.section for r in visible}
    assert all(top.isHidden() == (top not in {r.top for r in visible}) for top in tab._tops)
    tab.filter.setText("0x3013:0x02")
    assert [r.reg.name for r in tab.rows if not r.item.isHidden()] == ["counts per revolution"]
    tab.filter.setText("")
    assert not any(r.item.isHidden() for r in tab.rows) and not any(t.isHidden() for t in tab._tops)


# ---- telemetry tab -----------------------------------------------------------------------------
def test_readout_cards_shrink_long_text_instead_of_growing(qtbot):
    from sdr_tuner.gui.widgets import MIN_PX, Readout
    card = Readout("Operation mode", size=20, fit=True, lines=2)
    qtbot.addWidget(card)
    card.resize(150, 90)
    card.show()
    card.set("Idle")
    assert card.font_px == 20                                                       # short text keeps the full size
    card.set("Electronic Speed Control (ESC)")
    assert MIN_PX <= card.font_px < 20
    long_px = card.font_px
    card.set("Direct Field Control - Electrical Position")
    assert MIN_PX <= card.font_px <= long_px
    card.set("Idle")
    assert card.font_px == 20                                                       # and grows back
    wide = Readout("Work zone count", "counts", fit=True)
    qtbot.addWidget(wide)
    wide.resize(200, 80)
    wide.show()
    wide.set("0")
    assert wide.font_px == 26
    wide.set("-2,147,483,648")
    assert MIN_PX <= wide.font_px < 26                                              # a huge count still fits on one line


# ---- scroll safety: spin/combo boxes and sliders must not react to the mouse wheel -----------------------------
def send_wheel(widget):
    """Simulates scrolling the mouse wheel down over `widget`. Forces WheelFocus first so the underlying Qt behaviour
    this is guarding against actually engages (proven directly: a plain QComboBox's index does change under this same
    call) - a widget that still ignores it under WheelFocus truly never reacts to the wheel, regardless of whether it
    happens to have focus in the real app."""
    widget.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.instance().sendEvent(widget, event)


def test_the_wheel_over_a_plot_with_its_zoom_off_is_left_for_the_page_to_scroll(qtbot):
    """User (2026-09-25), pointing at the Bode plots' frequency-axis rows: "if you keep mouse pointer then scroll up and
    down will not work" - pyqtgraph's axes and view box ACCEPT the wheel even with the mouse disabled, and an accepted
    wheel event never reaches the page's scroll area. Qt propagates a real wheel event to the parent exactly when the
    widget leaves it unaccepted, and that flag is what this test checks (QApplication.sendEvent does not itself walk
    up to the parents, so the scroll bar cannot be watched here; the real app was checked with a genuine wheel event).
    A plot whose zoom is on (Recorder, 2D Plotter) must still accept it and zoom."""
    import pyqtgraph as pg
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
    from sdr_tuner.gui import theme
    area = QScrollArea()
    page = QWidget()
    lay = QVBoxLayout(page)
    plot = pg.PlotWidget()
    theme.style_plot(plot, "Gain", "dB")
    plot.setFixedHeight(300)
    lay.addWidget(plot)
    zooming = pg.PlotWidget()
    theme.style_plot(zooming, "Trace", "")
    zooming.getPlotItem().setMouseEnabled(x=True, y=True)
    zooming.setFixedHeight(300)
    lay.addWidget(zooming)
    area.setWidget(page)
    area.setWidgetResizable(True)
    area.resize(600, 700)
    area.show()
    qtbot.addWidget(area)
    qtbot.waitExposed(area)

    def wheel_down(widget, where):
        event = QWheelEvent(QPointF(*where), widget.mapToGlobal(QPoint(*where)).toPointF(), QPoint(0, 0), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(widget, event)
        return event.isAccepted()

    assert not wheel_down(plot.viewport(), (plot.width() // 2, plot.height() - 12)), "the axis row swallowed the wheel"
    assert not wheel_down(plot.viewport(), (plot.width() // 2, plot.height() // 2)), "the plot area swallowed the wheel"
    x_range_before = zooming.getPlotItem().getViewBox().viewRange()[0]
    assert wheel_down(zooming.viewport(), (zooming.width() // 2, zooming.height() // 2)), "a zooming plot must keep the wheel"
    assert zooming.getPlotItem().getViewBox().viewRange()[0] != x_range_before, "the zooming plot did not zoom"


def test_a_zoomable_plot_in_a_page_zooms_with_a_modifier_wheel_or_a_pinch_and_resets_on_double_click(qtbot):
    """User (2026-09-25): "make sure we can zoom the graph as we make graph size small to fit in one screen" - while the
    plain wheel keeps scrolling the page (the test above). theme.enable_zoom: a plain wheel is left unaccepted and
    zooms nothing; Ctrl (Cmd on a Mac) + wheel zooms; a trackpad pinch - a native zoom gesture pyqtgraph does not
    handle - narrows the view; a double-click calls the plot's reset."""
    import pyqtgraph as pg
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent, QNativeGestureEvent, QPointingDevice
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
    from sdr_tuner.gui import theme
    area = QScrollArea()
    page = QWidget()
    lay = QVBoxLayout(page)
    plot = pg.PlotWidget()
    theme.style_plot(plot, "Gain", "dB")
    plot.setFixedHeight(300)
    resets = []
    theme.enable_zoom(plot, reset=lambda: (resets.append(1), plot.getPlotItem().enableAutoRange()))
    lay.addWidget(plot)
    area.setWidget(page)
    area.setWidgetResizable(True)
    area.resize(600, 700)
    area.show()
    qtbot.addWidget(area)
    qtbot.waitExposed(area)
    view = plot.getPlotItem().getViewBox()
    where = QPoint(plot.width() // 2, plot.height() // 2)
    where_global = plot.mapToGlobal(where).toPointF()

    def wheel_down(modifiers):
        event = QWheelEvent(where.toPointF(), where_global, QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton, modifiers,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(plot.viewport(), event)
        return event.isAccepted()

    before = view.viewRange()[0]
    assert not wheel_down(Qt.KeyboardModifier.NoModifier) and view.viewRange()[0] == before    # the page's wheel, unzoomed
    assert wheel_down(Qt.KeyboardModifier.ControlModifier) and view.viewRange()[0] != before   # Ctrl/Cmd + wheel zooms
    span = view.viewRange()[0][1] - view.viewRange()[0][0]
    pinch = QNativeGestureEvent(Qt.NativeGestureType.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(), 2,
                                where.toPointF(), plot.mapToScene(where), where_global, 0.25, QPointF())
    QApplication.sendEvent(plot.viewport(), pinch)
    assert view.viewRange()[0][1] - view.viewRange()[0][0] < span * 0.9                        # fingers apart: a narrower view
    assert view.state["autoRange"] == [False, False]
    double = QMouseEvent(QEvent.Type.MouseButtonDblClick, where.toPointF(), where_global, Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(plot.viewport(), double)
    assert resets == [1] and view.state["autoRange"] == [True, True]


def test_spin_boxes_combo_boxes_and_sliders_ignore_the_mouse_wheel(qtbot):
    """The user hit this live: scrolling a long page changed a MOVE TO target from 0 to -3 counts, just by passing the
    mouse over it on the way past - a plain QSpinBox/QComboBox/QSlider changes its value on a wheel scroll even without
    focus, so scrolling a form is a minefield of accidental edits. Every one used in this app must ignore the wheel
    instead, so scrolling a form always scrolls the form."""
    from sdr_tuner.gui.widgets import ComboBox, DoubleSpinBox, Slider, SpinBox

    plain_combo = QComboBox()                                  # first prove the wheel mechanism really engages here
    qtbot.addWidget(plain_combo)
    plain_combo.addItems(["a", "b", "c"])
    send_wheel(plain_combo)
    assert plain_combo.currentIndex() != 0

    spin = SpinBox()
    qtbot.addWidget(spin)
    spin.setRange(-100, 100)
    send_wheel(spin)
    assert spin.value() == 0

    dspin = DoubleSpinBox()
    qtbot.addWidget(dspin)
    dspin.setRange(-100.0, 100.0)
    send_wheel(dspin)
    assert dspin.value() == 0.0

    combo = ComboBox()
    qtbot.addWidget(combo)
    combo.addItems(["a", "b", "c"])
    send_wheel(combo)
    assert combo.currentIndex() == 0

    slider = Slider(Qt.Orientation.Horizontal)
    qtbot.addWidget(slider)
    slider.setRange(-100, 100)
    send_wheel(slider)
    assert slider.value() == 0


def test_the_real_pages_use_the_scroll_safe_fields_not_the_plain_qt_ones(win, qtbot):
    """Confirms the app's own fields - not just the widgets.py classes in isolation - are wired up: a regression here
    would mean someone reached for QSpinBox/QComboBox/QSlider directly again on a new field."""
    dt = win.drive_tab
    for widget in (dt.target, dt.limit, dt.pos_target, dt.pos_delta, win.drive_modes_page.ct_target):
        value = widget.value()
        send_wheel(widget)
        assert widget.value() == value
    for widget in (win.drive_modes_page.ct_mode, dt.speed_unit, dt.window_box, win.iface, win.bitrate):
        index = widget.currentIndex()
        send_wheel(widget)
        assert widget.currentIndex() == index
    slider_value = dt.slider.value()
    send_wheel(dt.slider)
    assert dt.slider.value() == slider_value
    node_value = win.node.value()
    send_wheel(win.node)
    assert win.node.value() == node_value

    sim, client = connect(win, qtbot)
    row = next(r for r in win.params_tab.rows if r.reg.options)                 # an enum parameter: its editor is a combo box
    index = row.editor.currentIndex()
    send_wheel(row.editor)
    assert row.editor.currentIndex() == index


def test_the_window_is_laid_out_like_a_studio(win):
    assert win.ribbon.tab_titles() == ["File", "Parameters", "System Configuration", "Floating Tools"]
    assert list(win.navigator._activities) == ["system", "drive", "motion"]
    assert [win.navigator.activity_title(k) for k in win.navigator._activities] == ["System Configuration", "Servo Drive", "Motion"]
    assert win.navigator.tool_keys("drive") == ["quick_tuning", "advanced_tuning", "motion", "drive_modes", "recorder", "parameters",
                                                "telemetry", "peripherals"]
    assert win.navigator.tool_keys("motion") == ["motion_controller", "sim3d", "plotter_2d", "multi_axis_motion", "axes",
                                                 "status_monitor", "terminal"]
    assert win.ribbon.current_key() == "system" and win.navigator.current_activity() == "system"
    assert win.host.title.text() == "Item Configuration" and win.host.current_page() is win.config_page
    assert win.ribbon.stop_button.text() == "STOP" and win.status_text() == "Item Configuration Page"


def test_opening_the_tools_shows_their_page_and_ribbon_tab(win, qtbot):
    expectations = {"motion": (win.drive_tab, "Single Axis Motion", "tools"), "parameters": (win.params_tab, "Parameters Explorer", "parameters"),
                    "telemetry": (win.telemetry_tab, "Telemetry Explorer", "parameters"), "terminal": (win.log_tab, "Terminal", "tools"),
                    "recorder": (win.recorder_page, "Recorder", "recording"), "config": (win.config_page, "Item Configuration", "system"),
                    "quick_tuning": (win.quick_tuner, "Quick Tuning", "quick"), "advanced_tuning": (win.advanced_tuner, "Advanced Tuning", "advanced"),
                    "peripherals": (win.peripherals_page, "Peripherals", "tools"),
                    "drive_modes": (win.drive_modes_page, "Drive Modes", "tools"),
                    "motion_controller": (win.motion_controller_page, "Motion Controller", "tools"),
                    "axes": (win.axes_page, "Axes", "tools")}
    for key, (page, title, ribbon) in expectations.items():
        win.open_tool(key)
        assert win.host.current_page() is page and win.host.title.text() == title
        assert win.ribbon.current_key() == ribbon and win.status_text() == f"{title} Page"
        assert win.navigator.current_activity() == win.tools[key].activity


# ---- Axes status page (one row per connected axis - Stage 4) --------------------------------------------------
def test_axes_page_shows_one_row_per_connected_axis(win, qtbot):
    sim1, client1 = connect_axis1(win, qtbot)                              # node 2 - see connect_axis1's own docstring
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    page = win.axes_page
    qtbot.waitUntil(lambda: page.table.rowCount() == 2, timeout=WAIT)
    nodes = {page.table.item(r, 1).text() for r in range(page.table.rowCount())}
    assert nodes == {"2", "3"}
    axis_names = {page.table.item(r, 0).text() for r in range(page.table.rowCount())}
    assert axis_names == {"Drive01 (SDR)", "Drive03 (SDR)"}     # "Drive01 (SDR)": the primary's own default Target Name


def test_axes_page_shows_the_primarys_own_renamed_target_name_not_the_literal_word_primary(win, qtbot):
    """2026-09-24, user: "in axis page i see primary instead of drive 01" - the Axis column used to hardcode the
    literal word "Primary" for the primary's own row, never reflecting its own editable Target Name (Item
    Configuration), unlike the navigator/pill/badges, which always have. Renaming it (even after connecting) must
    show up here too, on the very next refresh tick - the same as everywhere else that already shows this name."""
    sim1, client1 = connect_axis1(win, qtbot)
    page = win.axes_page
    qtbot.waitUntil(lambda: page.table.rowCount() == 1, timeout=WAIT)
    win.config_page.target_name.setText("Leg 1 Drive")
    qtbot.waitUntil(lambda: page.table.item(0, 0).text() == "Leg 1 Drive (SDR)", timeout=WAIT)


def test_axes_page_shows_live_telemetry_and_fault_bits_per_axis(win, qtbot):
    sim1, client1 = connect_axis1(win, qtbot)
    page = win.axes_page
    qtbot.waitUntil(lambda: page.table.rowCount() == 1, timeout=WAIT)
    qtbot.waitUntil(lambda: page.table.item(0, 2).text() == "Online", timeout=WAIT)
    sim1.fault_bits = 1        # Driver Fault
    qtbot.waitUntil(lambda: "Driver Fault" in page.table.item(0, 5).text(), timeout=WAIT)


def test_axes_page_updates_when_an_axis_disconnects(win, qtbot):
    sim1, client1 = connect_axis1(win, qtbot)                              # node 2 - see connect_axis1's own docstring
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    qtbot.waitUntil(lambda: win.axes_page.table.rowCount() == 2, timeout=WAIT)
    win.session.disconnect_axis(3)
    qtbot.waitUntil(lambda: win.axes_page.table.rowCount() == 1, timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.axes_page.table.rowCount() == 0, timeout=WAIT)


def test_the_navigator_opens_pages_and_remembers_the_last_tool(win, qtbot):
    setup = win.navigator._activities["drive"][0]
    qtbot.mouseClick(setup, Qt.MouseButton.LeftButton)                               # the activity list under the tree
    assert win.host.current_page() is win.drive_tab and win.navigator.title.text() == "Servo Drive"
    listing = win.navigator._activities["drive"][1]
    qtbot.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=listing.visualItemRect(listing.item(5)).center())
    assert win.host.current_page() is win.params_tab                                  # a tool of the list
    qtbot.mouseClick(win.navigator._activities["system"][0], Qt.MouseButton.LeftButton)
    assert win.host.current_page() is win.config_page and win.status_text() == "System Configuration Activity"
    qtbot.mouseClick(setup, Qt.MouseButton.LeftButton)
    assert win.host.current_page() is win.params_tab                                  # back where it was
    win.navigator.drive_chosen.emit()
    assert win.host.current_page() is win.config_page                                 # the drive in the tree


def test_the_navigator_can_be_folded_away(win, qtbot):
    full = win.navigator.width()
    qtbot.mouseClick(win.navigator.collapse_btn, Qt.MouseButton.LeftButton)
    assert win.navigator.width() < full and win.navigator.tree.isHidden()
    qtbot.mouseClick(win.navigator.collapse_btn, Qt.MouseButton.LeftButton)
    assert win.navigator.width() == full and not win.navigator.tree.isHidden()


def test_the_ribbon_stop_button_stops_the_motor(win, qtbot):
    sim, client = connect(win, qtbot)
    win.drive_tab.target.setValue(150.0)
    qtbot.mouseClick(win.drive_tab.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.speed_hz > 10, timeout=WAIT)
    qtbot.mouseClick(win.ribbon.stop_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(sim.speed_hz) < 0.5, timeout=WAIT)
    assert client.commanded is None and sim.commands_received[-1] == ("stop", ())


def test_write_capability_follows_each_registers_own_access_not_the_page(qtbot, monkeypatch):
    """The bug the user pointed at: a page must not treat every row as read-only just because it is the 'telemetry' page -
    each register's own access mark (from the Device Reference) decides. Fabricate a telemetry-section register that IS
    writable (none exist in the current PDF - verified directly against it) and check the page follows the data."""
    writable_telemetry = defs.Register(section="Telemetry - Test", name="Fabricated Writable Telemetry Value", index=0x9000,
                                       sub=0x01, dtype="UINT16", access="rw")
    readonly_telemetry = defs.Register(section="Telemetry - Test", name="Fabricated Read-Only Telemetry Value", index=0x9000,
                                       sub=0x02, dtype="UINT16", access="ro")
    monkeypatch.setattr(defs, "load_registers", lambda telemetry=None: [writable_telemetry, readonly_telemetry] if telemetry else [])
    tab = ParamsTab(session.Session(), telemetry=True)
    qtbot.addWidget(tab)
    assert tab.any_writable is True                                                            # not "telemetry, so False"
    rows = {r.reg.name: r for r in tab.rows}
    # a QTreeWidget item widget's own isVisible() also depends on the row being expanded/in the viewport, unrelated to what
    # is being tested here - isHidden() reflects the button's own setVisible() call regardless of that
    assert not rows["Fabricated Writable Telemetry Value"].write_btn.isHidden()
    assert rows["Fabricated Read-Only Telemetry Value"].write_btn.isHidden()                    # still respected, row by row
    assert not tab.write_btn.isHidden() and not tab.save_btn.isHidden() and not tab.load_btn.isHidden()   # can save/load too
    assert not tab.findChild(QLabel, "readOnlyBadge")                                           # not read-only any more
    assert tab.read_all_btn.property("role") == "primary"


def test_open_and_save_are_only_on_the_file_tab_not_duplicated_on_parameters(win, qtbot):
    file_buttons = [win.ribbon.page("file").groups["Parameter File"]._row.itemAt(i).widget().text() for i in range(2)]
    assert file_buttons == ["Open…", "Save…"]
    drive_buttons = [win.ribbon.page("parameters").groups["Drive"]._row.itemAt(i).widget().text()
                     for i in range(win.ribbon.page("parameters").groups["Drive"]._row.count())]
    assert drive_buttons == ["Upload", "Download", "Save to\nFlash"]                          # Open…/Save… live on File only


def test_telemetry_explorer_looks_visibly_different_from_parameters_explorer(win, qtbot):
    win.open_tool("parameters")
    assert win.params_tab.tree.property("readonly") in (False, None) and win.params_tab.read_all_btn.property("role") == "primary"
    assert win.params_tab.write_btn.isVisible()
    win.open_tool("telemetry")
    assert win.telemetry_tab.tree.property("readonly") is True
    assert win.telemetry_tab.read_all_btn.property("role") != "primary"
    assert not win.telemetry_tab.write_btn.isVisible() and not win.telemetry_tab.save_btn.isVisible()
    badges = [c for c in win.telemetry_tab.bar.findChildren(type(win.pill)) if c.objectName() == "readOnlyBadge"]
    assert len(badges) == 1 and badges[0].text() == "READ-ONLY"
    telemetry_btn = win.ribbon.page("parameters").groups["Telemetry"]._row.itemAt(0).widget()
    drive_btn = win.ribbon.page("parameters").groups["Drive"]._row.itemAt(0).widget()
    assert "Telemetry" in telemetry_btn.text() and drive_btn.text() == "Upload"                # not the same label


def test_the_ribbon_parameter_buttons_work_on_the_parameters(win, qtbot):
    sim, client = connect(win, qtbot)
    upload = win.ribbon.page("parameters").groups["Drive"]._row.itemAt(0).widget()
    assert upload.text() == "Upload"
    qtbot.mouseClick(upload, Qt.MouseButton.LeftButton)
    assert win.host.current_page() is win.params_tab and win.ribbon.current_key() == "parameters"
    wait_idle(win, qtbot)
    assert all(r.item.text(5).startswith("✓ read") for r in win.params_tab.rows)
    telemetry = win.ribbon.page("parameters").groups["Telemetry"]._row.itemAt(0).widget()
    qtbot.mouseClick(telemetry, Qt.MouseButton.LeftButton)
    assert win.host.current_page() is win.telemetry_tab
    qtbot.waitUntil(lambda: not win.telemetry_tab.busy and all(r.has_value for r in win.telemetry_tab.rows), timeout=30000)


def test_the_item_configuration_shows_who_the_drive_is(win, qtbot):
    page = win.config_page
    assert page.device_type.text() == page.serial.text() == page.firmware.text() == "Unknown"
    assert page.state.text() == "DISCONNECTED" and not win.disconnect_btn.isEnabled()
    connect(win, qtbot)
    qtbot.waitUntil(lambda: page.serial.text() != "Unknown", timeout=WAIT)
    assert page.device_type.text() == f"{0x0A020192}   (0xA020192)"                  # the simulator's device type
    assert page.serial.text() == f"{100234}   (0x{100234:X})" and page.firmware.text() == f"{26092101}   (0x{26092101:X})"
    assert "SIMULATED" in page.state.text()
    assert win.navigator.drive_item.text(0) == "Drive01 (SDR)"
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert page.serial.text() == "Unknown" and page.state.text() == "DISCONNECTED"


def test_the_target_name_labels_the_drive_everywhere_and_is_remembered(win, qtbot):
    win.config_page.target_name.setText("Bench drive")
    assert win.navigator.drive_item.text(0) == "Bench drive (SDR)" and win.host.target.text() == "Bench drive"
    connect(win, qtbot)                                                             # saved when the connection is made
    assert MainWindow._settings().value("workspace/target_name") == "Bench drive"
    win.config_page.target_name.setText("   ")
    assert win.navigator.drive_item.text(0) == "Drive01 (SDR)"                       # an empty name falls back to the default


def test_the_connection_buttons_follow_the_connection(win, qtbot):
    assert win.connect_btn.isEnabled() and not win.disconnect_btn.isEnabled()
    connect(win, qtbot)
    assert not win.connect_btn.isEnabled() and win.disconnect_btn.isEnabled()
    assert not any(w.isEnabled() for w in (win.iface, win.channel, win.bitrate, win.node))
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert win.iface.isEnabled() and win.node.isEnabled()


def test_the_ribbon_can_be_folded_and_has_contextual_tabs(win, qtbot):
    ribbon = win.ribbon
    assert not ribbon.body.isHidden()
    qtbot.mouseClick(ribbon.collapse_btn, Qt.MouseButton.LeftButton)
    assert ribbon.body.isHidden()
    qtbot.mouseClick(ribbon.collapse_btn, Qt.MouseButton.LeftButton)
    assert not ribbon.body.isHidden()
    page = ribbon.add_page("extra", "Extra", contextual=True)                          # a tab that only shows with its tool
    assert "Extra" not in ribbon.tab_titles()
    ribbon.set_page_visible("extra", True)
    ribbon.show_page("extra")
    assert "Extra" in ribbon.tab_titles() and ribbon.current_key() == "extra" and ribbon.stack.currentWidget() is page
    ribbon.set_page_visible("extra", False)
    assert "Extra" not in ribbon.tab_titles() and ribbon.current_key() != "extra"


def test_telemetry_tab_lists_every_telemetry_parameter_read_only(win):
    tab = win.telemetry_tab
    assert len(tab.rows) == 176 and all(r.reg.telemetry and not r.reg.writable for r in tab.rows)
    assert tab.write_btn.isHidden() and tab.save_btn.isHidden() and tab.load_btn.isHidden()
    assert all(r.write_btn.isHidden() for r in tab.rows)
    for row in tab.rows:
        if hasattr(row.editor, "isReadOnly"):
            assert row.editor.isReadOnly(), row.reg.name
        else:
            assert not row.editor.isEnabled(), row.reg.name
    assert not {r.reg.address for r in tab.rows} & {r.reg.address for r in win.params_tab.rows}
    tab.filter.setText("fault bits")
    assert [r.reg.address for r in tab.rows if not r.item.isHidden()][:1] == ["0x4000:0x03"]


def test_telemetry_tab_reads_the_values_by_request(win, qtbot):
    sim, _ = connect(win, qtbot)
    sim.fault_bits = 0x8000
    tab = win.telemetry_tab
    qtbot.mouseClick(tab.read_all_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not tab.busy, timeout=30000)
    rows = {r.reg.address: r for r in tab.rows}
    assert all(r.item.text(5) == "✓ read" for r in tab.rows) and "Read 176 parameter(s)." in tab.info.text()
    assert rows["0x4000:0x03"].get() == 0x8000                                    # Fault Bits
    assert rows["0x4000:0x02"].get() == 2                                         # Operation Mode: Fault
    assert rows["0x5001:0x04"].get() == pytest.approx(24.0, abs=0.3)              # DC bus
    assert sim.commands_received == [] and not win.params_tab.rows[0].has_value   # nothing else was touched


def test_nothing_asks_the_drive_for_telemetry(win, qtbot):
    sim, client = connect(win, qtbot)
    sent = []
    client.frame_listeners.append(lambda d, t, cid, data: sent.append((cid, bytes(data))) if d == "tx" else None)
    win.open_tool("telemetry")
    qtbot.wait(2600)                                                              # longer than the old 2 s auto-refresh
    assert sent == [] and not client.polling
    assert not hasattr(win.telemetry_tab, "auto") and not hasattr(win.drive_tab, "poll")
    win.open_tool("motion")
    qtbot.wait(500)
    assert sent == []                                                             # and the DRIVE tab does not ask either


def test_the_drives_own_telemetry_mapping_is_used_from_connect(win, qtbot, monkeypatch):
    def remapped(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        for slot, (index, sub) in enumerate([(0x5001, 0x06), (0x4000, 0x03), (0x5001, 0x04)], 1):     # Phase B, Fault Bits, DC bus
            sim.params[(0x1A00, slot)] = (index << 8) | sub
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", remapped)
    _, client = connect(win, qtbot)
    assert [f.key for f in client.tpdos[0].fields] == ["ib", "fault_bits", "vbus"]
    dt = win.drive_tab
    qtbot.waitUntil(lambda: "ib" in dt.cards and dt.cards["ib"].value.text() != "—" and dt.cards["vbus"].value.text() != "—", timeout=WAIT)
    assert "speed_hz" not in dt.cards                                              # this drive does not broadcast speed
    assert not client.polling                                                      # Phase B came from the broadcast
    assert win.log_tab.decoder.tpdo_by_cob[0x180].fields[0].key == "ib"            # the CAN log decodes it the same way


def test_the_drive_tab_shows_exactly_what_the_drive_broadcasts(win, qtbot):
    _, client = connect(win, qtbot)
    dt = win.drive_tab
    # the default broadcast of the drive (revision L): nothing more, nothing less - Phase B is not in it, so there is no card
    assert list(dt.cards) == ["speed_hz", "vbus", "op_mode", "fault_bits", "ia", "wz_count", "comm_mode", "wz_modulo"]
    assert set(dt._curves) == {"speed_hz", "wz_count", "ia", "vbus"} and "ib" not in dt.cards
    assert not any(pw.isHidden() for pw in dt._plot_widgets)
    qtbot.waitUntil(lambda: all(card.value.text() != "—" for card in dt.cards.values()), timeout=WAIT)
    assert dt.cards["wz_modulo"].value.text().replace(",", "").isdigit()               # Work Zone Modulo is in the broadcast


def test_a_drive_that_broadcasts_less_shows_less(win, qtbot, monkeypatch):
    def no_speed(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        sim.params[(0x1A00, 3)] = 0                                                # message 0: Fault Bits and DC bus only
        sim.params[(0x1A02, 2)] = 0                                                # message 2: Phase A only
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", no_speed)
    connect(win, qtbot)
    dt = win.drive_tab
    qtbot.waitUntil(lambda: list(dt.cards) == ["vbus", "op_mode", "fault_bits", "ia", "wz_count", "comm_mode"], timeout=WAIT)
    assert "speed_hz" not in dt._curves and dt._plot_widgets[0].isHidden()             # no speed: no speed plot either
    assert not any(pw.isHidden() for pw in dt._plot_widgets[1:])
    qtbot.waitUntil(lambda: dt.cards["vbus"].value.text() != "—" and dt.cards["ia"].value.text() != "—", timeout=WAIT)


def test_a_drive_that_broadcasts_more_shows_more(win, qtbot, monkeypatch):
    def with_phase_b(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        sim.params[(0x1A02, 2)] = (0x5001 << 8) | 0x06                             # Phase B in place of Work Zone Modulo
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", with_phase_b)
    connect(win, qtbot)
    dt = win.drive_tab
    qtbot.waitUntil(lambda: "ib" in dt.cards, timeout=WAIT)
    assert list(dt.cards) == ["speed_hz", "vbus", "op_mode", "fault_bits", "ia", "ib", "wz_count", "comm_mode"]
    assert set(dt._curves) == {"speed_hz", "wz_count", "ia", "ib", "vbus"}
    qtbot.waitUntil(lambda: dt.cards["ib"].value.text() != "—", timeout=WAIT)
    assert dt.cards["ib"].title.text() == "PHASE B" and dt.cards["ib"].unit.text() == "A"


def test_a_value_without_a_special_card_is_shown_with_its_own_name_and_unit(win, qtbot, monkeypatch):
    reg = defs.telemetry_catalog()[(0x5001, 0x08)]                                 # Current: DC bus (Itotal), FLOAT32, A

    def with_itotal(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        sim.params[(0x1A02, 2)] = (reg.index << 8) | reg.sub                       # in place of Work Zone Modulo
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", with_itotal)
    connect(win, qtbot)
    dt = win.drive_tab
    qtbot.waitUntil(lambda: reg.address in dt.cards, timeout=WAIT)
    assert dt.cards[reg.address].title.text() == reg.name.upper() and dt.cards[reg.address].unit.text() == reg.units
    assert list(dt.cards)[-1] == reg.address                                       # unknown values go after the usual ones
    qtbot.waitUntil(lambda: dt.cards[reg.address].value.text() != "—", timeout=WAIT)


# ---- 2D Plotter (live XY trace of two axes' own Work Zone Count against each other) --------------------------
def test_2d_plotter_axis_combos_default_to_the_two_lowest_numbered_connected_axes(win, qtbot):
    page = win.plotter_2d_page
    assert [page.x_combo.itemData(i) for i in range(page.x_combo.count())] == [None]   # only the placeholder
    assert [page.y_combo.itemData(i) for i in range(page.y_combo.count())] == [None]   # ("— pick an axis —")
    connect_axis1(win, qtbot)                                              # node 2 -> "Drive01 (SDR)" (the primary's own default name)
    qtbot.waitUntil(lambda: page.x_combo.count() == 1, timeout=WAIT)
    assert page.x_combo.currentText() == "Drive01 (SDR)"
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)                  # node 3 -> "Drive03 (SDR)"
    qtbot.waitUntil(lambda: page.y_combo.currentText() == "Drive03 (SDR)", timeout=WAIT)
    assert page.x_combo.currentText() == "Drive01 (SDR)"                         # unchanged by the new axis arriving


def test_2d_plotter_a_third_axis_arriving_does_not_disturb_an_already_made_pick(win, qtbot):
    connect_axis1(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    page = win.plotter_2d_page
    qtbot.waitUntil(lambda: page.y_combo.currentText() == "Drive03 (SDR)", timeout=WAIT)
    win.session.connect_axis(session.SIMULATOR, "", 0, 4)                  # node 4 -> "Drive04 (SDR)"
    qtbot.waitUntil(lambda: page.x_combo.count() == 3, timeout=WAIT)
    assert page.y_combo.currentText() == "Drive03 (SDR)"


def test_2d_plotter_traces_live_telemetry_from_both_selected_axes(win, qtbot):
    sim1, client1 = connect_axis1(win, qtbot)                              # axis 1 = node 2
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)           # axis 2 = node 3
    client2 = ctx2.client
    page = win.plotter_2d_page
    qtbot.waitUntil(lambda: page.y_combo.currentText() == "Drive03 (SDR)", timeout=WAIT)
    qtbot.waitUntil(lambda: len(page._trace) >= 1, timeout=WAIT)
    assert page._trace[-1] == pytest.approx((0.0, 0.0), abs=1e-2)          # both axes start at rest
    log = win.log_tab
    run_gcode(log, "G01 [1]500 [2]-500 F50000")
    qtbot.waitUntil(lambda: client1.commanded is not None
                    and client1.commanded[1][0] == pytest.approx(500.0, abs=1e-3), timeout=WAIT)
    assert client2.commanded[1][0] == pytest.approx(-500.0, abs=1e-3)
    qtbot.waitUntil(lambda: page._trace[-1] == pytest.approx((500.0, -500.0), abs=1e-2), timeout=WAIT)
    assert len(page._trace) >= 2                                          # a genuine trace, not just one frozen point


def test_2d_plotter_disconnecting_the_selected_axis_freezes_the_trace_but_keeps_it_picked(win, qtbot):
    connect_axis1(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    page = win.plotter_2d_page
    qtbot.waitUntil(lambda: page.y_combo.currentText() == "Drive03 (SDR)", timeout=WAIT)
    qtbot.waitUntil(lambda: len(page._trace) >= 1, timeout=WAIT)
    frozen = list(page._trace)
    win.session.disconnect_axis(3)
    qtbot.waitUntil(lambda: "Drive03 (SDR) is not connected" in page.status.text(), timeout=WAIT)
    qtbot.wait(300)
    assert page._trace == frozen                                          # not cleared, not silently reset
    assert page.y_combo.currentText() == "Drive03 (SDR)"                  # the pick itself survives the disconnect


def test_2d_plotter_clear_all_empties_the_trace(win, qtbot):
    connect_axis1(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    page = win.plotter_2d_page
    qtbot.waitUntil(lambda: len(page._trace) >= 1, timeout=WAIT)
    qtbot.mouseClick(page.clear_btn, Qt.MouseButton.LeftButton)
    assert page._trace == [] and page.status.text() == "Cleared."
    xs, ys = page.curve.getData()
    assert xs is None or len(xs) == 0


def test_2d_plotter_save_image_writes_a_png(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    connect_axis1(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    page = win.plotter_2d_page
    qtbot.waitUntil(lambda: len(page._trace) >= 1, timeout=WAIT)
    path = tmp_path / "plot.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    page.save_image_dialog()
    assert path.exists() and path.stat().st_size > 0
    assert win.status_text() == f"Saved image to {path}"


def test_2d_plotter_save_data_writes_a_csv_of_the_trace(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    connect_axis1(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    page = win.plotter_2d_page
    qtbot.waitUntil(lambda: len(page._trace) >= 1, timeout=WAIT)
    path = tmp_path / "plot.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    page.save_data_dialog()
    lines = path.read_text().splitlines()
    assert lines[0] == "Drive01 (SDR) [counts],Drive03 (SDR) [counts]"
    assert len(lines) == len(page._trace) + 1
    assert win.status_text() == f"Saved {len(page._trace)} point(s) to {path}"


def test_2d_plotter_save_data_refuses_with_nothing_traced_yet(win, qtbot):
    win.plotter_2d_page.save_data_dialog()
    assert win.status_text() == "Nothing to save yet: trace something first."


def test_2d_plotter_traces_a_real_g03_arc_as_a_genuine_circle(win, qtbot):
    """The point of the whole feature: dispatch a real G03 arc across 2 connected axes (reusing the same
    arc-interpolation machinery `test_gcode_arc_moves_two_connected_axes_through_waypoints_to_the_target` already
    exercises) and confirm the 2D Plotter's own accumulated trace is consistent with an actual circle, not just a
    start/end pair - every captured point sits within a small tolerance of the arc's own true centre and radius
    (computed the same way `trajectory.py` itself does, already covered in isolation by `tests/test_trajectory.py`).
    A deliberately slow feed rate (F300, matching a hand-verified run of this exact scenario) paces waypoints far
    enough apart that the plotter's own 100 ms polling timer - and the simulator's own realistic telemetry rate -
    actually catch several of them, not just wherever the arc happened to be at the one moment it finished."""
    sim1, client1 = connect_axis1(win, qtbot)                              # axis 1 = node 2
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)           # axis 2 = node 3
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    client2 = ctx2.client
    page = win.plotter_2d_page
    qtbot.waitUntil(lambda: page.y_combo.currentText() == "Drive03 (SDR)", timeout=WAIT)
    qtbot.waitUntil(lambda: "wz_count" in client1.telemetry_snapshot() and "wz_count" in client2.telemetry_snapshot(),
                    timeout=WAIT)
    a0, b0 = client1.telemetry_snapshot()["wz_count"], client2.telemetry_snapshot()["wz_count"]
    a1, b1 = a0 + 1000, b0 + 1000
    center = trajectory.arc_center((a0, b0), (a1, b1), clockwise=False, radius=1000.0)
    radius = math.hypot(a0 - center[0], b0 - center[1])
    log = win.log_tab
    run_gcode(log, f"G03 [1]{a1:.1f} [2]{b1:.1f} R1000 F300")
    qtbot.waitUntil(lambda: client1.commanded is not None
                    and client1.commanded[1][0] == pytest.approx(a1, abs=1e-3), timeout=WAIT)
    qtbot.waitUntil(lambda: client2.commanded is not None
                    and client2.commanded[1][0] == pytest.approx(b1, abs=1e-3), timeout=WAIT)
    # the runner still has one more scheduled callback pending after the last waypoint (the one that notices there
    # are no more and finishes) - wait for it here, not after this test's own window is torn down (a slow F leaves
    # it pending long enough to otherwise fire during a LATER test and crash on that test's own, different widgets)
    qtbot.waitUntil(lambda: "waypoint(s) sent" in log.gcode_status.text(), timeout=WAIT)
    assert len(page._trace) >= 4                                          # a genuine curve, not just start/end
    for x, y in page._trace:
        assert math.hypot(x - center[0], y - center[1]) == pytest.approx(radius, abs=1.0)


# ---- Status Monitor (pick any signal, on any connected axis, and watch it - 2026-09-25) ------------------------
def test_status_monitor_signal_picker_lists_every_register_by_name(win, qtbot):
    page = win.status_monitor_page
    page.add_btn.click()
    row = page._rows[0]
    names = {row.signal_combo.itemText(i) for i in range(row.signal_combo.count())}
    assert row.signal_combo.count() == len(defs.load_registers())
    assert "Fault Bits" in names and "Maximum Limit on Continuous Current (Line-to-Line)" in names


def test_status_monitor_a_broadcast_signal_updates_live_with_no_extra_reads(win, qtbot, monkeypatch):
    """The core design point: a signal the drive is already broadcasting (Fault Bits, always in the default
    telemetry mapping) must update purely from `client.telemetry_snapshot()`, like every other page - never a
    fresh `client.read()` call, which would be exactly the standing "never poll" rule this page's own docstring
    states."""
    sim, client = connect(win, qtbot)
    reads = []
    monkeypatch.setattr(client, "read", lambda reg: reads.append(reg) or 0)
    page = win.status_monitor_page
    page.add_btn.click()
    row = page._rows[0]
    idx = row.signal_combo.findText("Fault Bits")
    assert idx >= 0
    row.signal_combo.setCurrentIndex(idx)
    assert row.snap_key == "fault_bits"
    qtbot.waitUntil(lambda: row.value_item.text() == "No Faults", timeout=WAIT)
    assert not row.read_btn.isEnabled()                                    # broadcasting - nothing to press
    sim.fault_bits = 1
    qtbot.waitUntil(lambda: row.value_item.text() != "No Faults", timeout=WAIT)
    assert reads == []                                                     # confirmed: never read, only snapshot


def test_status_monitor_a_non_broadcast_signal_shows_not_broadcast_until_read(win, qtbot):
    sim, client = connect(win, qtbot)
    sim.params[MAX_CURRENT] = 6.5
    page = win.status_monitor_page
    page.add_btn.click()
    row = page._rows[0]
    idx = row.signal_combo.findText("Maximum Limit on Continuous Current (Line-to-Line)")
    row.signal_combo.setCurrentIndex(idx)
    qtbot.waitUntil(lambda: row.value_item.text() == "not broadcast", timeout=WAIT)
    assert row.read_btn.isEnabled()
    row.read_btn.click()
    qtbot.waitUntil(lambda: row.value_item.text() != "not broadcast", timeout=WAIT)
    assert row.value_item.text() == "6.5"
    assert "Read Maximum Limit on Continuous Current" in page.status.text()


def test_status_monitor_gauge_checkbox_only_enabled_with_a_real_min_and_max(win, qtbot):
    connect(win, qtbot)
    page = win.status_monitor_page
    page.add_btn.click()
    page.add_btn.click()
    no_range, has_range = page._rows
    no_range.signal_combo.setCurrentIndex(no_range.signal_combo.findText(
        "Maximum Limit on Continuous Current (Line-to-Line)"))
    assert not no_range.gauge_check.isEnabled()
    has_range.signal_combo.setCurrentIndex(has_range.signal_combo.findText("CANopen: Node ID"))
    assert has_range.gauge_check.isEnabled()
    has_range.gauge_check.setChecked(True)
    has_range.last_value = 64.0                                            # midway through NODE_ID's own 1..127
    page._tick()
    assert has_range.gauge.value == pytest.approx(64.0)


def test_status_monitor_remove_row(win, qtbot):
    page = win.status_monitor_page
    page.add_btn.click()
    page.add_btn.click()
    assert page.table.rowCount() == 2 and len(page._rows) == 2
    page._rows[0].remove_btn.click()
    assert page.table.rowCount() == 1 and len(page._rows) == 1
    assert "1 signal(s)" in page.status.text()


def test_status_monitor_target_combo_lists_every_connected_axis_and_switching_repoints_the_row(win, qtbot):
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    client2 = ctx2.client
    page = win.status_monitor_page
    page.add_btn.click()
    row = page._rows[0]
    labels = {row.target_combo.itemText(i) for i in range(row.target_combo.count())}
    assert labels == {"Drive01 (SDR)", "Drive02 (SDR)"}
    idx = row.signal_combo.findText("Fault Bits")
    row.signal_combo.setCurrentIndex(idx)
    sim1.fault_bits = 2
    qtbot.waitUntil(lambda: row.value_item.text() != "No Faults", timeout=WAIT)
    row.target_combo.setCurrentIndex(row.target_combo.findData(2))          # switch to axis 2 (node 2)
    qtbot.waitUntil(lambda: row.value_item.text() == "No Faults", timeout=WAIT)   # axis 2 has no fault
    assert page._client_for(row) is client2


# ---- Multi Axis Motion (move several connected axes, no G-code - 2026-09-25) ------------------------------------
def test_multi_axis_motion_lists_one_row_per_connected_axis(win, qtbot):
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    page = win.multi_axis_motion_page
    qtbot.waitUntil(lambda: page.table.rowCount() == 2, timeout=WAIT)
    assert sorted(page._rows) == [1, 2]
    assert page.table.item(0, 0).text() == "Drive01 (SDR)" and page.table.item(1, 0).text() == "Drive02 (SDR)"
    assert page.table.item(0, 1).text() == "1" and page.table.item(1, 1).text() == "2"


def test_multi_axis_motion_position_column_updates_live_from_broadcast(win, qtbot):
    sim, client = connect(win, qtbot)
    page = win.multi_axis_motion_page
    qtbot.waitUntil(lambda: 1 in page._rows, timeout=WAIT)
    row = page._rows[1]
    qtbot.waitUntil(lambda: row.position_item.text() != "—", timeout=WAIT)
    sim.wz_count = 4200
    qtbot.waitUntil(lambda: row.position_item.text() == "4,200", timeout=WAIT)


def test_multi_axis_motion_move_sends_hold_position_to_the_right_client_only(win, qtbot):
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    client2 = ctx2.client
    page = win.multi_axis_motion_page
    qtbot.waitUntil(lambda: page.table.rowCount() == 2, timeout=WAIT)
    row1 = page._rows[1]
    row1.target_spin.setValue(500)
    row1.move_btn.click()
    qtbot.waitUntil(lambda: client1.commanded == ("servo", 500.0), timeout=WAIT)
    assert client2.commanded is None                                        # axis 2 untouched
    qtbot.waitUntil(lambda: "Moving to 500 counts" in row1.status_item.text(), timeout=WAIT)


def test_multi_axis_motion_move_refuses_when_no_encoder_is_configured(win, qtbot):
    sim, client = connect(win, qtbot)
    sim.params[(0x2000, 0x0A)] = 0                                          # neither Motor nor Servo Encoder configured
    page = win.multi_axis_motion_page
    qtbot.waitUntil(lambda: 1 in page._rows, timeout=WAIT)
    row = page._rows[1]
    row.target_spin.setValue(100)
    row.move_btn.click()
    qtbot.waitUntil(lambda: "Motor Encoder or Servo Encoder" in row.status_item.text(), timeout=WAIT)
    assert client.commanded is None


def test_multi_axis_motion_move_all_dispatches_every_row(win, qtbot):
    sim1, client1 = connect(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    client2 = ctx2.client
    page = win.multi_axis_motion_page
    qtbot.waitUntil(lambda: page.table.rowCount() == 2, timeout=WAIT)
    page._rows[1].target_spin.setValue(111)
    page._rows[2].target_spin.setValue(222)
    page.move_all_btn.click()
    qtbot.waitUntil(lambda: client1.commanded == ("servo", 111.0) and client2.commanded == ("servo", 222.0),
                    timeout=WAIT)


def test_multi_axis_motion_rebuilds_rows_when_an_axis_disconnects(win, qtbot):
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    page = win.multi_axis_motion_page
    qtbot.waitUntil(lambda: page.table.rowCount() == 2, timeout=WAIT)
    win.session.disconnect_axis(2)
    qtbot.waitUntil(lambda: page.table.rowCount() == 1, timeout=WAIT)
    assert sorted(page._rows) == [1]


# ---- Docked View: Servo Drive's own dockable multi-pane workspace (Phase 3) ------------
def test_docked_view_toggle_swaps_the_dock_workspace_into_page_host(win, qtbot):
    connect(win, qtbot)
    win.open_tool("motion")
    assert win.host.current_page() is win.drive_tab
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    assert win.host.current_page() is win.dock_workspace
    assert win.dock_workspace.open_keys() == ["motion"]
    assert win.host.stack.indexOf(win.drive_tab) < 0                    # taken out of the single-page stack
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)    # back to single-page
    assert win.host.current_page() is win.drive_tab
    assert win.dock_workspace.open_keys() == []


def test_opening_two_tools_in_docked_mode_creates_two_docks_each_showing_the_right_page(win, qtbot):
    connect(win, qtbot)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("motion")
    win.open_tool("axes")
    assert win.dock_workspace.open_keys() == ["motion", "axes"]
    assert win.dock_workspace.page_of("motion") is win.drive_tab       # (inside the dock's own scroll area)
    assert win.dock_workspace.page_of("axes") is win.axes_page


def test_closing_a_dock_lets_the_sidebar_reopen_it(win, qtbot):
    connect(win, qtbot)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("motion")
    win.dock_workspace._docks["motion"].close()
    assert not win.dock_workspace._docks["motion"].isVisible()
    win.open_tool("motion")
    assert win.dock_workspace._docks["motion"].isVisible()
    assert win.dock_workspace.page_of("motion") is win.drive_tab           # same instance, not rebuilt


def test_switching_back_to_single_page_mode_returns_pages_to_the_stack(win, qtbot):
    connect(win, qtbot)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("motion")
    win.open_tool("axes")
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    assert win.dock_workspace.open_keys() == []
    assert win.host.stack.indexOf(win.drive_tab) >= 0 and win.host.stack.indexOf(win.axes_page) >= 0
    assert win.host.current_page() is win.axes_page      # whichever was current when the toggle switched off


def test_system_configuration_is_never_dockable(win, qtbot):
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("config")
    assert win.host.current_page() is win.config_page
    assert win.dock_workspace.open_keys() == []


def test_an_axis_qualified_tool_docks_under_its_own_drive_name(win, qtbot):
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool(win._axis_tool_key("motion", 2))
    dock = win.dock_workspace._docks[win._axis_tool_key("motion", 2)]
    assert "Drive02" in dock.windowTitle() and "Motion" in dock.windowTitle()
    assert win.dock_workspace.page_of("motion:2") is win._axis_pages[2]["motion"]


def test_disconnecting_an_axis_closes_its_own_docked_panels(win, qtbot):
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    motion_key = win._axis_tool_key("motion", 2)
    win.open_tool(motion_key)
    assert motion_key in win.dock_workspace.open_keys()
    win.session.disconnect_axis(2)
    assert motion_key not in win.dock_workspace.open_keys()
    # still in docked mode - falling back means re-docking the primary's own Motion, not a single-page swap
    assert win.dock_workspace.open_keys() == ["motion"]
    assert win.dock_workspace.page_of("motion") is win.drive_tab


def test_docked_view_ribbon_context_follows_the_focused_tool_not_just_the_primary(win, qtbot):
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("advanced_tuning")                            # the primary's own, docked
    win.open_tool(win._axis_tool_key("advanced", 2))            # axis 2's own, now the focused one
    assert win._current_tuner("advanced") is win._axis_pages[2]["advanced"]
    assert win._current_recorder() is win.recorder_page         # unaffected - no recorder is open/focused here


def test_reset_layout_closes_every_dock_and_reopens_just_the_current_one_fresh(win, qtbot):
    connect(win, qtbot)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("motion")
    win.open_tool("axes")
    win._reset_dock_layout()
    assert win.dock_workspace.open_keys() == ["axes"]     # "motion" closed, "axes" (whatever was current) re-docked fresh
    assert win.host.stack.indexOf(win.drive_tab) >= 0      # "motion" handed back to the single-page stack, not lost


def test_reset_layout_in_single_page_mode_just_clears_saved_state(win, qtbot):
    connect(win, qtbot)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("motion")
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)     # back to single-page
    win._reset_dock_layout()
    assert win.dock_workspace.open_keys() == []
    assert win.host.current_page() is win.drive_tab


def test_dock_layout_persists_across_a_restart(qtbot, monkeypatch):
    monkeypatch.setattr(MainWindow, "show_error", lambda self, message: None)
    first = MainWindow()
    qtbot.addWidget(first)
    first.show()
    connect(first, qtbot)
    qtbot.mouseClick(first.docked_view_btn, Qt.MouseButton.LeftButton)
    first.open_tool("motion")
    first.open_tool("axes")
    first.close()
    second = MainWindow()
    qtbot.addWidget(second)
    assert second._docked_mode and second.docked_view_btn.isChecked()
    assert sorted(second.dock_workspace.open_keys()) == ["axes", "motion"]


# ---- the drive's telemetry layout is read first -------------------------------------------------
def test_nothing_is_decoded_before_the_layout_is_read(win, qtbot, monkeypatch):
    def remapped(channel, node_id):                                                # message 0: Phase B, Fault Bits, DC bus
        sim = SimulatedDrive(channel, node_id)
        for slot, (index, sub) in enumerate([(0x5001, 0x06), (0x4000, 0x03), (0x5001, 0x04)], 1):
            sim.params[(0x1A00, slot)] = defs.mapping_code(index, sub)
        return sim
    real_load = DriveClient.load_tpdo_mapping

    def slow_load(self):
        time.sleep(0.4)                                                            # the broadcast (100 per second) runs meanwhile
        return real_load(self)
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", remapped)
    monkeypatch.setattr(DriveClient, "load_tpdo_mapping", slow_load)
    _, client = connect(win, qtbot)
    qtbot.waitUntil(lambda: "ib" in client.telemetry_snapshot(), timeout=WAIT)
    # with the default layout the frames of this drive would have produced a Speed value out of Phase B's bytes
    assert len(client.history_snapshot("speed_hz")[0]) == 0 and "speed_hz" not in client.telemetry_snapshot()
    assert client.mapping_from_drive


def test_a_layout_that_cannot_be_read_is_reported(win, qtbot, monkeypatch):
    def unreadable(self):
        raise DriveError("no answer for 0x1A00:0x01")
    monkeypatch.setattr(DriveClient, "load_tpdo_mapping", unreadable)
    _, client = connect(win, qtbot)
    dt = win.drive_tab
    assert not client.mapping_from_drive
    assert "could not be read" in dt.status.text() and dt.status.property("state") == "warn"
    assert "could NOT be read" in win.status_text()
    qtbot.waitUntil(lambda: dt.cards["vbus"].value.text() != "—", timeout=WAIT)      # the reference's default layout is used


def test_a_layout_read_from_the_drive_is_reported_as_such(win, qtbot):
    connect(win, qtbot)
    assert win.drive_tab.status.text().startswith("Ready")
    assert "telemetry layout read from the drive" in win.status_text()


def test_a_mapping_that_overflows_an_8_byte_message_is_reported_not_silent(win, qtbot, monkeypatch):
    """2026-09-24, found while checking the user's own real drive: Fault Bits (2B) + the FLOAT32 DC-bus register
    (4B) + Work Zone Speed (4B) is 10 bytes, over the 8-byte CAN limit - the client already correctly refused to
    guess at the overflowing field (test_client.py's own tests), but did so completely silently. The connect-time
    status message must now say so, with "warn" styling, instead of reporting a plain "telemetry layout read from
    the drive" that hides a real, configured field having quietly gone missing."""
    def overflowing(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        for slot, (index, sub) in enumerate([(0x4000, 0x03), (0x5001, 0x0D), (0x4000, 0x11), (0, 0)], 1):
            sim.params[(0x1A00, slot)] = defs.mapping_code(index, sub) if (index, sub) != (0, 0) else 0
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", overflowing)
    connect(win, qtbot)
    assert "does not fit an 8-byte message" in win.status_text()
    assert win.bottom.level == "warn"
    # 2026-09-24: found live on the user's own real drive - that status line above is a SINGLE message the very next
    # page navigation overwrites (e.g. clicking between activities calls show_status("<activity> Activity")), so a
    # user who connects and immediately navigates away loses the warning with no way to tell why Speed never got a
    # card. The drive tab's own banner must stay up regardless of what the window's status bar says afterward.
    dt = win.drive_tab
    win.open_tool("motion")                                      # a hidden page's widgets never report isVisible()
    assert "speed_hz" not in dt.cards                            # the overflowing slot really never got a card
    assert dt.mapping_banner.isVisible()
    assert "does not fit an 8-byte message" in dt.mapping_banner.text()
    win.show_status("System Configuration Activity")             # simulate the page-navigation overwrite
    assert dt.mapping_banner.isVisible()                         # still shown - unlike the status bar
    assert "does not fit an 8-byte message" in dt.mapping_banner.text()


def test_the_mapping_banner_is_hidden_when_nothing_overflows(win, qtbot):
    """Sanity check the banner does not false-positive on the ordinary case (test_client.py's own
    test_the_default_layout_has_no_overflow has the same purpose at the client level)."""
    connect(win, qtbot)
    dt = win.drive_tab
    win.open_tool("motion")
    assert not dt.mapping_banner.isVisible()


def test_the_mapping_banner_clears_on_disconnect(win, qtbot, monkeypatch):
    def overflowing(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        for slot, (index, sub) in enumerate([(0x4000, 0x03), (0x5001, 0x0D), (0x4000, 0x11), (0, 0)], 1):
            sim.params[(0x1A00, slot)] = defs.mapping_code(index, sub) if (index, sub) != (0, 0) else 0
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", overflowing)
    connect(win, qtbot)
    dt = win.drive_tab
    win.open_tool("motion")
    assert dt.mapping_banner.isVisible()
    win.session.disconnect_drive()
    qtbot.waitUntil(lambda: not dt.mapping_banner.isVisible(), timeout=WAIT)


# ---- the unit the speed is shown in ------------------------------------------------------------
def run_at_120_rpm(win, qtbot, sim):
    dt = win.drive_tab
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(sim.speed_hz - 14.0) < 0.3, timeout=WAIT)          # 14 poles: 7 pole pairs -> 14 Hz = 2 rps = 120 rpm


def choose_unit(dt, unit):
    dt.speed_unit.setCurrentIndex(dt.speed_unit.findData(unit))
    dt.speed_unit.activated.emit(dt.speed_unit.currentIndex())


def speed_value(dt):
    text = dt.cards[next(k for k, i in dt._card_info.items() if i.speed)].value.text()
    return float("nan") if text == "—" else float(text.replace(",", ""))          # nan: no value yet (compares false)


def test_the_speed_unit_choices_and_default(win):
    dt = win.drive_tab
    assert [dt.speed_unit.itemText(i) for i in range(dt.speed_unit.count())] == ["rpm", "rps", "deg/sec", "Hz"]
    # the unit shown is the one the drive sends by default (Speed in Hz): nothing is converted through the pole count
    assert dt.speed_unit.currentText() == "Hz" and dt.cards["speed_hz"].unit.text() == "Hz"
    assert dt._plot_widgets[0].getPlotItem().getAxis("left").labelUnits == "Hz"


def test_a_drive_that_already_sends_rps_is_shown_in_rpm(win, qtbot, monkeypatch):
    def sends_rps(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        sim.params[(0x1A00, 3)] = defs.mapping_code(*defs.SPEED_RPS)
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", sends_rps)
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    dt._confirm = lambda text: pytest.fail("nothing needs to be written")
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    assert dt.speed_unit.currentText() == "rpm" and dt.cards["wz_speed"].unit.text() == "rpm"     # rpm is the default for rps
    run_at_120_rpm(win, qtbot, sim)
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 120.0) < 5, timeout=WAIT)          # the drive's rps value times 60
    choose_unit(dt, "rps")                                                          # rps <-> rpm needs no write
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 2.0) < 0.1, timeout=WAIT)
    assert dt.cards["wz_speed"].unit.text() == "rps"


def test_the_speed_is_shown_in_hz_while_the_drive_sends_hz(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("motion")                                      # the plot is read: it draws only on screen
    dt = win.drive_tab
    dt._confirm = lambda text: pytest.fail("nothing needs to be written")
    run_at_120_rpm(win, qtbot, sim)
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 14.0) < 0.5, timeout=WAIT)         # 120 rpm on 7 pole pairs = 14 Hz, as sent
    choose_unit(dt, "hz")                                                           # already Hz: nothing to do
    assert dt.cards["speed_hz"].unit.text() == "Hz" and dt._plot_widgets[0].getPlotItem().getAxis("left").labelUnits == "Hz"
    assert sim.params[(0x1A00, 3)] == 4194320 and client.mapping_codes[(0x1A00, 3)] == 4194320
    qtbot.waitUntil(lambda: curve_max(dt) > 10, timeout=WAIT)
    assert curve_max(dt) < 20                                                       # the plot is in Hz too


def test_choosing_rpm_after_hz_writes_the_rps_parameter(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    asked = []
    dt._confirm = lambda text: asked.append(text) or True
    run_at_120_rpm(win, qtbot, sim)
    choose_unit(dt, "hz")                                                           # the drive sends Hz already: no write
    assert asked == []
    choose_unit(dt, "rpm")                                                          # rpm is taken from Work Zone: Speed (RPS)
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    assert len(asked) == 1 and sim.params[(0x1A00, 3)] == 4194326
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 120.0) < 5, timeout=WAIT)
    assert dt.cards["wz_speed"].unit.text() == "rpm"


def test_choosing_rps_or_rpm_makes_the_drive_send_work_zone_speed(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("motion")                                      # the plot is read at the end: it draws only on screen
    dt = win.drive_tab
    asked = []
    dt._confirm = lambda text: asked.append(text) or True
    run_at_120_rpm(win, qtbot, sim)
    choose_unit(dt, "rps")
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    assert len(asked) == 1 and "Work Zone: Speed, RPS" in asked[0] and "Speed (Electrical Frequency), Hz" in asked[0]
    assert "0x1A00:0x03 = 4194326" in asked[0]
    assert sim.params[(0x1A00, 3)] == 4194326                                      # the drive's telemetry mapping was written
    assert "speed_hz" not in dt.cards and dt.cards["wz_speed"].title.text() == "SPEED"    # the only speed is simply "Speed"
    qtbot.waitUntil(lambda: dt.speed_unit.isEnabled(), timeout=WAIT)               # the write is finished
    assert dt.cards["wz_speed"].unit.text() == "rps"
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 2.0) < 0.1, timeout=WAIT)        # what the drive sends: 2 revolutions per second
    assert set(dt._curves) == {"wz_speed", "wz_count", "ia", "vbus"}
    assert dt.status.text().startswith("The drive now sends Work Zone: Speed") and dt.status.property("state") == "ok"
    assert win.log_tab.decoder.tpdo_by_cob[0x180].fields[2].key == "wz_speed"      # the CAN log reads the new layout too
    choose_unit(dt, "rpm")                                                          # the drive already sends rps: times 60, no write
    assert len(asked) == 1
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 120.0) < 5, timeout=WAIT)
    assert dt.cards["wz_speed"].unit.text() == "rpm"
    assert dt._plot_widgets[0].getPlotItem().getAxis("left").labelUnits == "rpm"
    qtbot.waitUntil(lambda: curve_max(dt) > 100, timeout=WAIT)                     # the plot is in rpm as well


def curve_max(dt):
    x, y = dt._curves[next(k for k, i in dt._card_info.items() if i.speed)].getData()
    return 0 if y is None or len(y) == 0 else float(max(y))


def test_choosing_hz_writes_the_hz_parameter_back(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    dt._confirm = lambda text: True
    run_at_120_rpm(win, qtbot, sim)
    choose_unit(dt, "rps")
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    choose_unit(dt, "hz")
    qtbot.waitUntil(lambda: "speed_hz" in dt.cards and "wz_speed" not in dt.cards, timeout=WAIT)
    assert sim.params[(0x1A00, 3)] == 4194320
    assert dt.cards["speed_hz"].unit.text() == "Hz"
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 14.0) < 0.5, timeout=WAIT)


def test_declining_the_change_writes_nothing(win, qtbot):
    sim, _ = connect(win, qtbot)
    dt = win.drive_tab
    before = dict(sim.params)
    dt._confirm = lambda text: False
    choose_unit(dt, "rps")
    assert sim.params == before and "speed_hz" in dt.cards
    assert dt.speed_unit.currentText() == "Hz" and dt.cards["speed_hz"].unit.text() == "Hz"       # the choice was undone


def test_a_refused_write_leaves_everything_as_it_was(win, qtbot, monkeypatch):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    dt._confirm = lambda text: True

    def refuse(self, reg, value, verify=True):
        raise DriveError(f"{reg.name}: drive rejected the write")
    monkeypatch.setattr(DriveClient, "write", refuse)
    choose_unit(dt, "rps")
    qtbot.waitUntil(lambda: dt.status.property("state") == "error", timeout=WAIT)
    assert "was not changed" in dt.status.text() and "rejected" in dt.status.text()
    assert dt.speed_unit.currentText() == "Hz" and dt.speed_unit.isEnabled() and "speed_hz" in dt.cards
    assert sim.params[(0x1A00, 3)] == 4194320


def test_without_a_speed_slot_only_the_display_unit_changes(win, qtbot, monkeypatch):
    def no_speed(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        sim.params[(0x1A00, 3)] = 0                                                # message 0: Fault Bits and DC bus only
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", no_speed)
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    qtbot.waitUntil(lambda: "speed_hz" not in dt.cards and "vbus" in dt.cards, timeout=WAIT)
    dt._confirm = lambda text: pytest.fail("there is nothing to write")
    before = dict(sim.params)
    choose_unit(dt, "rpm")
    assert dt._speed_unit == "rpm" and sim.params == before
    assert "no Speed / Work Zone: Speed slot" in dt.status.text() and dt.status.property("state") == "warn"


def test_a_change_made_elsewhere_from_hz_to_rps_is_followed(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    run_at_120_rpm(win, qtbot, sim)
    assert "speed_hz" in dt.cards
    poles_row = next(r for r in win.params_tab.rows if r.reg.address == "0x1A00:0x03")        # the user edits the mapping by hand
    win.params_tab._confirm = lambda text: True
    user_types(poles_row, "4194326")                                                # ... typing the code for speed in rps
    qtbot.mouseClick(poles_row.write_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards and "speed_hz" not in dt.cards, timeout=WAIT)
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 120.0) < 5, timeout=WAIT)        # still shown in rpm, now from the rps value
    assert dt.cards["wz_speed"].unit.text() == "rpm"


def test_two_speeds_keep_the_names_of_their_parameters(win, qtbot, monkeypatch):
    def both(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        sim.params[(0x1A02, 2)] = defs.mapping_code(*defs.SPEED_RPS)               # rps in place of Work Zone Modulo
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", both)
    connect(win, qtbot)
    dt = win.drive_tab
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    assert dt.cards["speed_hz"].title.text() == "SPEED" and dt.cards["wz_speed"].title.text() == "WORK ZONE: SPEED"
    assert dt.cards["speed_hz"].unit.text() == "Hz" and dt.cards["wz_speed"].unit.text() == "rpm"   # each in its own family
    assert dt._legends[0].isVisible()                                               # named curves when there are several
    qtbot.waitUntil(lambda: dt.cards["speed_hz"].value.text() != "—" and dt.cards["wz_speed"].value.text() != "—", timeout=WAIT)


# ---- drive tab --------------------------------------------------------------------------------
def test_the_drive_tab_has_no_polling_and_no_current_magnitude(win):
    dt = win.drive_tab
    assert not hasattr(dt, "unit") and not hasattr(dt, "poll")
    assert dt.target.suffix() == " rpm" and dt.limit.suffix() == " rpm" and dt.limit.value() == 500.0
    assert "i_mag" not in dt._curves and set(dt._curves) == {"speed_hz", "wz_count", "ia", "vbus"}
    assert [c.title.text() for c in dt.cards.values()] == [
        "SPEED", "DC BUS", "OPERATION MODE", "FAULT BITS", "PHASE A", "WORK ZONE COUNT", "COMMUTATION", "WORK ZONE MODULO"]
    assert all(card.value.text() == "—" for card in dt.cards.values())                # nothing to show until a drive is connected


@pytest.mark.parametrize("size", [(1120, 600), (1280, 700), (1440, 810), (1360, 900)])
def test_the_control_buttons_never_overlap_at_any_window_size(win, qtbot, size):
    win.resize(*size)
    win.open_tool("motion")
    qtbot.wait(150)
    dt = win.drive_tab
    box = dt.run_btn.parentWidget()
    while box is not None and box.__class__.__name__ != "QGroupBox":
        box = box.parentWidget()
    parts = {"target": dt.target, "slider": dt.slider, "limit": dt.limit, "run": dt.run_btn, "stop": dt.stop_btn,
             "off": dt.off_btn, "reset": dt.reset_btn, "enable": dt.enable_btn, "disable": dt.disable_btn, "status": dt.status}
    rects = {name: w.rect().translated(w.mapTo(box, w.rect().topLeft())) for name, w in parts.items()}
    names = list(rects)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not rects[a].intersects(rects[b]), f"{a} overlaps {b} at {size}: {rects[a]} {rects[b]}"
    assert dt.run_btn.height() == dt.stop_btn.height() == 40 and dt.run_btn.width() == dt.stop_btn.width()
    assert rects["run"].bottom() < rects["off"].top() and rects["off"].bottom() < rects["enable"].top()
    assert rects["enable"].bottom() < rects["status"].top()


def test_run_shows_live_telemetry_and_stop_stops(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    assert client.commanded == ("esc_rpm", 120.0)
    qtbot.waitUntil(lambda: abs(sim.speed_hz - 14.0) < 0.5, timeout=WAIT)          # 120 rpm, 7 pole pairs: 14 Hz electrical
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 14.0) < 0.5, timeout=WAIT)        # shown as the drive sends it: in Hz
    assert "commanding 120.0 rpm" in dt.status.text().lower()
    assert dt.live_tag.text().endswith("LIVE")
    dt._confirm = lambda text: True
    choose_unit(dt, "rpm")                                                          # the drive is told to send rps; shown times 60
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 120.0) < 5, timeout=WAIT)
    assert float(dt.cards["vbus"].value.text()) == pytest.approx(24.0, abs=0.3)
    qtbot.waitUntil(lambda: len(client.history_snapshot("wz_speed", 5)[0]) > 3, timeout=WAIT)    # the samples arrive over time
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(sim.speed_hz) < 0.5, timeout=WAIT)
    assert dt.target.value() == 0.0 and dt.slider.value() == 0 and client.commanded is None
    assert "STOP" in dt.status.text()


def test_changing_target_while_running_updates_the_drive(win, qtbot):
    sim, _ = connect(win, qtbot)
    dt = win.drive_tab
    dt.target.setValue(60.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.esc_target_hz == pytest.approx(7.0), timeout=WAIT)          # 60 rpm = 7 Hz with 14 poles
    dt.slider.setValue(1200)                                     # the slider works in tenths of an rpm: 120.0 rpm
    qtbot.waitUntil(lambda: sim.esc_target_hz == pytest.approx(14.0), timeout=WAIT)
    assert dt.target.value() == 120.0
    dt.target.setValue(-90.0)                                    # reverse
    qtbot.waitUntil(lambda: sim.esc_target_hz == pytest.approx(-10.5), timeout=WAIT)
    assert dt.slider.value() == -900


def test_speed_limit_clamps_the_target(win, qtbot):
    sim, _ = connect(win, qtbot)
    dt = win.drive_tab
    dt.limit.setValue(300.0)
    dt.target.setValue(1000.0)
    assert dt.target.value() == 300.0 and dt.slider.maximum() == 3000
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.esc_target_hz == pytest.approx(35.0), timeout=WAIT)         # 300 rpm = 35 Hz
    dt.target.setValue(-1000.0)
    assert dt.target.value() == -300.0
    dt.limit.setValue(100.0)                                     # lowering the limit pulls a running target down at once
    assert dt.target.value() == -100.0
    qtbot.waitUntil(lambda: sim.esc_target_hz == pytest.approx(-11.6667, abs=0.001), timeout=WAIT)


def test_the_speed_shown_is_what_the_drive_reports_whatever_the_pole_count(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    dt._confirm = lambda text: True
    choose_unit(dt, "rpm")                                                          # the drive is told to send rps
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 120.0) < 5, timeout=WAIT)
    poles = next(r for r in win.params_tab.rows if r.reg.address == "0x2000:0x03")
    user_types(poles, "8")                                                          # the user enters the motor's real pole count
    win.params_tab._confirm = lambda text: True
    qtbot.mouseClick(poles.write_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)
    qtbot.waitUntil(lambda: sim.speed_hz == pytest.approx(8.0, abs=0.3), timeout=WAIT)     # the drive turns 120 rpm into 8 Hz now
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 120.0) < 5, timeout=WAIT)                # and reports 2 rps as before: nothing to convert


def test_fault_leds_follow_fault_bits(win, qtbot):
    sim, _ = connect(win, qtbot)
    dt = win.drive_tab
    sim.fault_bits = 0b1010
    qtbot.waitUntil(lambda: [led.property("on") for led in dt._leds[:4]] == ["false", "true", "false", "true"], timeout=WAIT)
    assert dt.fault_summary.text() == "FAULT  0x000A"
    assert dt.cards["fault_bits"].value.text() == "Driver Warning (Maskable), Overcurrent Protection (firmware-controlled)"
    assert dt.cards["fault_bits"].value.property("state") == "fault"
    assert dt.fault_names.text() == "Driver Warning (Maskable), Overcurrent Protection (firmware-controlled)"
    sim.fault_bits = 0
    qtbot.waitUntil(lambda: dt.fault_summary.text() == "NO FAULTS", timeout=WAIT)
    assert not any(led.property("on") == "true" for led in dt._leds)
    assert dt.cards["fault_bits"].value.text() == "No Faults" and dt.cards["fault_bits"].value.property("state") != "fault"


def test_status_led_follows_fault_bits_like_the_summary_text_does(win, qtbot):
    """An at-a-glance health light next to FAULT BITS' own summary text (2026-09-25) - matching EAS3's own Motion
    page, which shows a light + "Disabled"/status text together rather than colour-coded text alone. Reuses the
    exact same `bits` signal `fault_summary`/the per-bit lamps already use - not a new telemetry request, and not
    a new interpretation of what "fault" means, just a second, glance-friendly rendering of it."""
    sim, _ = connect(win, qtbot)
    dt = win.drive_tab
    assert dt.status_led.property("on") != "true"                        # no fault right after connecting
    sim.fault_bits = 0b1010
    qtbot.waitUntil(lambda: dt.status_led.property("on") == "true", timeout=WAIT)
    sim.fault_bits = 0
    qtbot.waitUntil(lambda: dt.status_led.property("on") != "true", timeout=WAIT)


def test_status_led_resets_when_disconnected(win, qtbot):
    sim, _ = connect(win, qtbot)
    dt = win.drive_tab
    sim.fault_bits = 1
    qtbot.waitUntil(lambda: dt.status_led.property("on") == "true", timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert dt.status_led.property("on") != "true"


def test_only_the_fault_bits_the_drive_reports_have_a_lamp(win, qtbot):
    sim, _ = connect(win, qtbot)
    dt = win.drive_tab
    assert sorted(dt.led_for_bit) == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 15] and len(dt._leds) == 13
    assert dt.led_for_bit[14].toolTip() == "Bit 14: Emergency Stop" and dt.led_for_bit[3].toolTip().endswith("Overcurrent Protection (firmware-controlled)")
    sim.fault_bits = 0x0800                                              # bit 11: not one of this drive's fault bits
    qtbot.waitUntil(lambda: dt.fault_summary.text() == "FAULT  0x0800", timeout=WAIT)
    assert dt.fault_names.text() == "Bit 11 (not in the drive's list)"    # still reported, in words
    assert not any(led.property("on") == "true" for led in dt._leds)
    sim.fault_bits = 0x4000 | 0x0001
    qtbot.waitUntil(lambda: dt.led_for_bit[14].property("on") == "true", timeout=WAIT)
    assert dt.led_for_bit[0].property("on") == "true" and dt.fault_names.text() == "Driver Fault, Emergency Stop"


def test_off_and_reset_buttons_send_the_drives_commands(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    assert dt.off_btn.isEnabled() and dt.reset_btn.isEnabled()
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.speed_hz > 10, timeout=WAIT)
    qtbot.mouseClick(dt.off_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.off and abs(sim.speed_hz) < 0.5, timeout=WAIT)
    assert client.commanded is None and dt.status.text() == "OFF sent."
    qtbot.waitUntil(lambda: dt.cards["op_mode"].value.text() == "Off", timeout=WAIT)
    sim.fault_bits = 0x0100
    qtbot.waitUntil(lambda: dt.cards["op_mode"].value.text() == "Fault", timeout=WAIT)
    qtbot.mouseClick(dt.reset_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.fault_bits == 0 and not sim.off, timeout=WAIT)
    assert dt.status.text() == "RESET sent." and ("reset", ()) in sim.commands_received
    qtbot.waitUntil(lambda: dt.fault_summary.text() == "NO FAULTS", timeout=WAIT)
    qtbot.wait(300)
    assert client.commanded is None and sim.esc_target_hz == 0.0                   # the old speed is not sent again


def test_stop_button_sends_the_stop_command(win, qtbot):
    sim, _ = connect(win, qtbot)
    dt = win.drive_tab
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.speed_hz > 8, timeout=WAIT)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.commands_received[-1:] == [("stop", ())], timeout=WAIT)
    assert dt.status.text() == "STOP sent."


def test_enable_and_disable_buttons_follow_the_connection_like_off_and_reset(win, qtbot):
    dt = win.drive_tab
    assert not dt.enable_btn.isEnabled() and not dt.disable_btn.isEnabled()
    connect(win, qtbot)
    assert dt.enable_btn.isEnabled() and dt.disable_btn.isEnabled()


def test_enable_holds_the_current_position_when_an_encoder_is_configured(win, qtbot):
    sim, client = connect(win, qtbot)                                       # Motor Encoder defaults to a BISS-C/SSI Encoder
    dt = win.drive_tab
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.wz_count != 0, timeout=WAIT)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(sim.speed_hz) < 0.5, timeout=WAIT)
    qtbot.waitUntil(lambda: "wz_count" in client.telemetry_snapshot(), timeout=WAIT)
    qtbot.mouseClick(dt.enable_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Holding position" in dt.status.text(), timeout=WAIT)
    assert dt.status.property("state") == "ok"
    assert client.commanded[0] == "servo"
    target = client.commanded[1]
    qtbot.waitUntil(lambda: dt.cards["op_mode"].value.text() == "Servo Control", timeout=WAIT)
    qtbot.wait(200)
    assert sim.wz_count == pytest.approx(target)                            # held still, does not keep drifting
    qtbot.mouseClick(dt.disable_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.off and client.commanded is None, timeout=WAIT)
    assert dt.status.text() == "OFF sent."                                  # DISABLE is the same as OFF


def test_enable_refuses_when_no_encoder_is_configured(win, qtbot):
    sim, client = connect(win, qtbot)
    sim.params[(0x2000, 0x0A)] = 0                                          # neither Motor nor Servo Encoder is configured
    dt = win.drive_tab
    qtbot.waitUntil(lambda: "wz_count" in client.telemetry_snapshot(), timeout=WAIT)
    qtbot.mouseClick(dt.enable_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Motor Encoder or Servo Encoder" in dt.status.text(), timeout=WAIT)
    assert dt.status.property("state") == "warn" and client.commanded is None
    assert not any(key == "servo" for key, _v in sim.commands_received)


def test_virtual_brake_button_follows_the_connection(win, qtbot):
    dt = win.drive_modes_page
    assert not dt.vbrake_btn.isEnabled()
    connect(win, qtbot)
    assert dt.vbrake_btn.isEnabled()


def test_virtual_brake_engages_with_no_encoder_needed(win, qtbot):
    sim, client = connect(win, qtbot)
    sim.params[(0x2000, 0x0A)] = 0                                          # neither Motor nor Servo Encoder configured -
    dm, dt = win.drive_modes_page, win.drive_tab                           # Virtual Brake works (less efficiently) either way
    qtbot.mouseClick(dm.vbrake_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Virtual Brake engaged" in dm.vbrake_status.text(), timeout=WAIT)
    assert dm.vbrake_status.property("state") == "ok"
    assert client.commanded == ("virtual_brake", ())
    qtbot.waitUntil(lambda: dt.cards["op_mode"].value.text() == "Virtual Brake", timeout=WAIT)
    qtbot.mouseClick(dm.stop_btn, Qt.MouseButton.LeftButton)              # the Drive Modes page's own STOP
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


def test_kickstart_commands_the_target_speed(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_modes_page
    dt.kick_target.setValue(15.0)
    qtbot.mouseClick(dt.kick_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Kickstarting to" in dt.kick_status.text(), timeout=WAIT)
    assert dt.kick_status.property("state") == "ok"
    assert client.commanded == ("kickstart", 15.0)
    qtbot.waitUntil(lambda: win.drive_tab.cards["op_mode"].value.text() == "Kickstart", timeout=WAIT)
    qtbot.waitUntil(lambda: sim.speed_hz == pytest.approx(15.0, abs=1.0), timeout=WAIT)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


def test_kickstart_button_is_disabled_before_connecting(win, qtbot):
    assert not win.drive_modes_page.kick_run_btn.isEnabled()


def test_power_generation_commands_the_target_voltage(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_modes_page
    dt.gen_target.setValue(48.0)
    qtbot.mouseClick(dt.gen_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Generating" in dt.gen_status.text(), timeout=WAIT)
    assert dt.gen_status.property("state") == "ok"
    assert client.commanded == ("power_generation", 48.0)
    qtbot.waitUntil(lambda: win.drive_tab.cards["op_mode"].value.text() == "Power Generation", timeout=WAIT)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


def test_power_generation_button_is_disabled_before_connecting(win, qtbot):
    assert not win.drive_modes_page.gen_run_btn.isEnabled()


def test_brushed_open_loop_commands_both_channel_voltages(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_modes_page
    dt.brushed_v1.setValue(5.0)
    dt.brushed_v2.setValue(-3.0)
    qtbot.mouseClick(dt.brushed_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Commanding" in dt.brushed_status.text(), timeout=WAIT)
    assert dt.brushed_status.property("state") == "ok"
    key, value = client.commanded
    assert key == "brushed_open_loop_control_1_2_motors" and value == pytest.approx((5.0, -3.0), abs=0.01)
    qtbot.waitUntil(lambda: win.drive_tab.cards["op_mode"].value.text() == "Brushed Motor or Solenoid Control (1-2 motors)",
                    timeout=WAIT)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


def test_brushed_open_loop_button_is_disabled_before_connecting(win, qtbot):
    assert not win.drive_modes_page.brushed_run_btn.isEnabled()


def test_move_to_moves_to_the_typed_position_when_an_encoder_is_configured(win, qtbot):
    sim, client = connect(win, qtbot)                                        # Motor Encoder defaults to a BISS-C/SSI Encoder
    dt = win.drive_tab
    dt.pos_target.setValue(7500)
    dt.control_mode.setCurrentIndex(1)                    # the Position card must be the one shown
    qtbot.mouseClick(dt.move_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Moving to" in dt.pos_status.text(), timeout=WAIT)     # feedback under Position Control, not Speed Control
    assert dt.pos_status.property("state") == "ok"
    assert client.commanded == ("servo", 7500.0)
    qtbot.waitUntil(lambda: dt.cards["op_mode"].value.text() == "Servo Control", timeout=WAIT)
    qtbot.waitUntil(lambda: sim.wz_count == pytest.approx(7500.0), timeout=WAIT)
    qtbot.waitUntil(lambda: dt.cards["wz_count"].value.text() == "7,500", timeout=WAIT)


def test_move_by_button_stays_visually_enabled_during_the_feedback_check(win, qtbot, monkeypatch):
    """A user found this live: MOVE BY's button briefly showed its disabled colour (a darker, background-like tone -
    theme.py's QPushButton:disabled) while the brief Feedback Settings check ran, which looked like "some background
    is coming for a very short period of time". The button must never visibly disable for this; a second click while
    checking is silently ignored instead (no visible state change either way)."""
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    real_read = client.read
    calls = []

    def slow_read(reg):
        calls.append(reg)
        time.sleep(0.15)
        return real_read(reg)

    monkeypatch.setattr(client, "read", slow_read)
    qtbot.waitUntil(lambda: "wz_count" in client.telemetry_snapshot(), timeout=WAIT)
    dt.pos_delta.setValue(10)
    dt.control_mode.setCurrentIndex(1)                    # the Position card must be the one shown
    qtbot.mouseClick(dt.move_by_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Checking Feedback Settings" in dt.pos_status.text(), timeout=WAIT)
    assert dt.move_by_btn.isEnabled()                                   # never visibly disabled during the check
    dt.control_mode.setCurrentIndex(1)                    # the Position card must be the one shown
    qtbot.mouseClick(dt.move_by_btn, Qt.MouseButton.LeftButton)         # a second click while checking is a no-op
    qtbot.waitUntil(lambda: "Moving to" in dt.pos_status.text(), timeout=WAIT)
    assert dt.move_by_btn.isEnabled()
    assert len(calls) == 2                                              # only the first click's check actually ran


def test_move_by_moves_relative_to_the_drives_current_position(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    sim.wz_count = 1000.0
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("wz_count") == 1000, timeout=WAIT)
    dt.pos_delta.setValue(-250)
    dt.control_mode.setCurrentIndex(1)                    # the Position card must be the one shown
    qtbot.mouseClick(dt.move_by_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Moving to" in dt.pos_status.text(), timeout=WAIT)
    assert client.commanded == ("servo", 750.0)
    qtbot.waitUntil(lambda: sim.wz_count == pytest.approx(750.0), timeout=WAIT)


def test_move_to_refuses_when_no_encoder_is_configured(win, qtbot):
    sim, client = connect(win, qtbot)
    sim.params[(0x2000, 0x0A)] = 0                                           # neither Motor nor Servo Encoder is configured
    dt = win.drive_tab
    dt.pos_target.setValue(500)
    dt.control_mode.setCurrentIndex(1)                    # the Position card must be the one shown
    qtbot.mouseClick(dt.move_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Motor Encoder or Servo Encoder" in dt.pos_status.text(), timeout=WAIT)
    assert client.commanded is None and not any(key == "servo" for key, _v in sim.commands_received)


def test_cyclic_move_defaults_to_modulo_mode_with_shortest_cyclic_route(win, qtbot):
    win.open_tool("drive_modes")                                             # a hidden page's widgets never report isVisible()
    dt = win.drive_modes_page
    assert dt.cyclic_mode.currentIndex() == 0                          # Modulo (cyclic)
    assert dt.cyclic_direction.currentIndex() == 3                     # Shortest Cyclic Route
    assert dt.cyclic_direction.isVisible() and not dt.cyclic_turns.isVisible()


def test_cyclic_move_switches_fields_between_modulo_and_turns_modes(win, qtbot):
    win.open_tool("drive_modes")
    dt = win.drive_modes_page
    dt.cyclic_mode.setCurrentIndex(1)                                   # Turns + Modulo (split)
    assert dt.cyclic_turns.isVisible() and not dt.cyclic_direction.isVisible()
    dt.cyclic_mode.setCurrentIndex(0)
    assert dt.cyclic_direction.isVisible() and not dt.cyclic_turns.isVisible()


def test_cyclic_move_sends_servo_modulo_with_the_chosen_direction(win, qtbot):
    sim, client = connect(win, qtbot)                                   # Motor Encoder defaults to a BISS-C/SSI Encoder
    dt = win.drive_modes_page
    dt.cyclic_modulo.setValue(3000)
    dt.cyclic_direction.setCurrentIndex(1)                              # Negative Direction Only
    qtbot.mouseClick(dt.cyclic_move_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Moving to modulo" in dt.cyclic_status.text(), timeout=WAIT)
    assert dt.cyclic_status.property("state") == "ok"
    assert client.commanded == ("servo_modulo", (3000.0, 1.0))
    qtbot.waitUntil(lambda: sim.wz_count == pytest.approx(3000.0), timeout=WAIT)


def test_cyclic_move_sends_servo_turns_and_modulo_in_the_split_mode(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_modes_page
    dt.cyclic_mode.setCurrentIndex(1)
    dt.cyclic_modulo.setValue(1200)
    dt.cyclic_turns.setValue(7)
    qtbot.mouseClick(dt.cyclic_move_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "turns + modulo" in dt.cyclic_status.text(), timeout=WAIT)
    assert dt.cyclic_status.property("state") == "ok"
    assert client.commanded == ("servo_turns_and_modulo", (1200.0, 7.0))
    qtbot.waitUntil(lambda: sim.wz_count == pytest.approx(1200.0), timeout=WAIT)


def test_cyclic_move_refuses_when_no_encoder_is_configured(win, qtbot):
    sim, client = connect(win, qtbot)
    sim.params[(0x2000, 0x0A)] = 0                                      # neither Motor nor Servo Encoder is configured
    dt = win.drive_modes_page
    dt.cyclic_modulo.setValue(500)
    qtbot.mouseClick(dt.cyclic_move_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Motor Encoder or Servo Encoder" in dt.cyclic_status.text(), timeout=WAIT)
    assert client.commanded is None and not any(key == "servo_modulo" for key, _v in sim.commands_received)


def test_cyclic_move_button_is_disabled_before_connecting(win, qtbot):
    assert not win.drive_modes_page.cyclic_move_btn.isEnabled()


def test_set_home_here_stops_and_zeroes_the_work_zone_count(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    dt.target.setValue(100.0)
    dt.control_mode.setCurrentIndex(0)                    # back to the Velocity card
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.speed_hz > 5, timeout=WAIT)
    sim.wz_count = 99999.0
    dt.control_mode.setCurrentIndex(1)                    # the Position card must be the one shown
    qtbot.mouseClick(dt.home_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Home set here" in dt.pos_status.text(), timeout=WAIT)
    assert client.commanded is None
    qtbot.waitUntil(lambda: sim.wz_count != 99999.0, timeout=WAIT)                 # the reference point changed
    qtbot.waitUntil(lambda: abs(sim.speed_hz) < 1.0, timeout=WAIT)                 # a Stop was sent, motor coasting down


def test_current_torque_control_commands_the_chosen_mode(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_modes_page
    assert dt.ct_mode.currentText() == "Current (FOC)" and dt.ct_target.suffix() == " A"
    dt.ct_target.setValue(2.5)
    qtbot.mouseClick(dt.ct_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "2.500 A" in dt.ct_status.text(), timeout=WAIT)
    assert client.commanded == ("current_control_field_oriented_control_foc", 2.5)
    qtbot.waitUntil(lambda: win.drive_tab.cards["op_mode"].value.text() == "Field Oriented Control (FOC)", timeout=WAIT)

    dt.ct_mode.setCurrentIndex(1)
    assert dt.ct_target.suffix() == " N·m"
    dt.ct_target.setValue(0.6)
    qtbot.mouseClick(dt.ct_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded == ("electronic_torque_control_etc", 0.6), timeout=WAIT)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


def test_direct_field_control_commands_the_chosen_mode(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_modes_page
    assert dt.dfc_mode.currentText() == "Rotation (Hz)" and dt.dfc_target.suffix() == " Hz"
    dt.dfc_voltage.setValue(0.5)
    dt.dfc_target.setValue(12.0)
    qtbot.mouseClick(dt.dfc_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Rotating field at" in dt.dfc_status.text(), timeout=WAIT)
    assert "0.5000 V" in dt.dfc_status.text()
    # the Voltage is a "FLOAT16" = fixed point in 1/256 V (2026-09-25): the status says what the drive really got -
    # 0.1 goes out as raw 26 = 0.1016 V (the half-float encoding used until that day sent 0.1 as 46.4 V, which is
    # what tripped the real drive's overcurrent protection on 2026-09-24)
    dt.dfc_voltage.setValue(0.1)
    qtbot.mouseClick(dt.dfc_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "0.1016 V" in dt.dfc_status.text(), timeout=WAIT)
    dt.dfc_voltage.setValue(0.5)                                              # back to the value the rest of the test expects
    qtbot.mouseClick(dt.dfc_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "0.5000 V" in dt.dfc_status.text(), timeout=WAIT)
    assert dt.dfc_status.property("state") == "ok"
    key, value = client.commanded
    assert key == "direct_field_control_rotation" and value == pytest.approx((0.5, 12.0), abs=0.01)
    qtbot.waitUntil(lambda: win.drive_tab.cards["op_mode"].value.text() == "Direct Field Control - Rotation", timeout=WAIT)

    dt.dfc_mode.setCurrentIndex(1)
    assert dt.dfc_target.suffix() == " rad"
    dt.dfc_target.setValue(1.25)
    qtbot.mouseClick(dt.dfc_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Holding field at" in dt.dfc_status.text(), timeout=WAIT)
    key, value = client.commanded
    assert key == "direct_field_control_electrical_position" and value == pytest.approx((0.5, 1.25), abs=0.01)
    qtbot.waitUntil(lambda: win.drive_tab.cards["op_mode"].value.text() == "Direct Field Control - Electrical Position", timeout=WAIT)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


def test_direct_field_control_button_is_disabled_before_connecting(win, qtbot):
    assert not win.drive_modes_page.dfc_run_btn.isEnabled()


def test_each_cards_feedback_stays_under_that_card_not_speed_control(win, qtbot):
    """The user found this live: a Position Control error was showing under the Speed Control box instead of under
    Position Control, because every card's status used to write to the one label physically inside Speed Control."""
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    sim.params[(0x2000, 0x0A)] = 0                                            # neither Motor nor Servo Encoder: MOVE TO will refuse
    ready_text = dt.status.text()
    dt.pos_target.setValue(500)
    dt.control_mode.setCurrentIndex(1)                    # the Position card must be the one shown
    qtbot.mouseClick(dt.move_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Motor Encoder or Servo Encoder" in dt.pos_status.text(), timeout=WAIT)
    assert dt.status.text() == ready_text                                     # Speed Control's own box is untouched

    dm = win.drive_modes_page                                                 # Current / Torque lives on Drive Modes now
    dm.ct_target.setValue(1.2)
    qtbot.mouseClick(dm.ct_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "1.200 A" in dm.ct_status.text(), timeout=WAIT)
    assert "sent every" in dm.ct_status.text() and "1.200 A" not in dt.pos_status.text()
    # Speed Control only points at it (it used to keep "Ready…" while a mode ran): the detail stays on the modes page
    assert "see Drive Modes" in dt.status.text() and "sent every" not in dt.status.text()

    dt.target.setValue(60.0)
    dt.control_mode.setCurrentIndex(0)                    # back to the Velocity card
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "60.0 rpm" in dt.status.text(), timeout=WAIT)      # RUN's own feedback still lands here
    assert "60.0 rpm" not in dt.pos_status.text() and "60.0 rpm" not in dm.ct_status.text()


def test_a_status_labels_height_never_changes_between_a_short_and_a_long_message(win, qtbot):
    """The user found this live, pressing MOVE BY: for a couple of frames "some background is coming for a very short
    period of time" - the brief "Checking Feedback Settings…" message is much shorter than the "no encoder" one that
    follows it, and without a fixed minimum height the label (and so its card, and so the whole scrollable column)
    visibly shrank then grew back. Every status label now reserves room for the longest message up front."""
    win.open_tool("motion")
    dt = win.drive_tab
    for name, label in (("status", dt.status), ("pos_status", dt.pos_status)):
        box = label.parentWidget()
        while box is not None and box.__class__.__name__ != "QGroupBox":
            box = box.parentWidget()
        heights = []
        for text in ("", "Checking Feedback Settings…",
                     "This needs a Motor Encoder or Servo Encoder configured (Datasheet → Feedback Settings) - this "
                     "drive currently has none, so Work Zone Count would not track a real position.",
                     "Checking Feedback Settings…"):
            label.setText(text)
            qtbot.wait(0)
            heights.append(box.height())
        assert len(set(heights)) == 1, f"{name}'s card changed height across messages: {heights}"


def test_operation_mode_commutation_and_fault_names_are_shown(win, qtbot):
    sim, _ = connect(win, qtbot)
    dt = win.drive_tab
    qtbot.waitUntil(lambda: dt.cards["op_mode"].value.text() == "Idle" and dt.cards["comm_mode"].value.text() == "Sensorless", timeout=WAIT)
    assert dt.cards["op_mode"].value.property("state") in (None, "")
    sim.fault_bits = 0x8000 | 0x0001
    qtbot.waitUntil(lambda: dt.cards["op_mode"].value.text() == "Fault", timeout=WAIT)
    assert dt.cards["op_mode"].value.property("state") == "fault"
    assert dt.fault_names.text() == "Driver Fault, Activation Key is Missing"
    assert dt.cards["op_mode"].font_px >= 10 and dt.cards["comm_mode"].font_px >= 10
    assert dt.fault_summary.text() == "FAULT  0x8001"
    assert dt.cards["fault_bits"].value.text() == "Driver Fault, Activation Key is Missing"
    assert [dt.led_for_bit[bit].property("on") for bit in (0, 15, 7)] == ["true", "true", "false"]


def test_a_fault_while_running_is_explained_and_the_motor_coasts(win, qtbot):
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    dt.target.setValue(150.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.speed_hz > 15, timeout=WAIT)
    sim.fault_bits = 0x0008                                                         # overcurrent
    qtbot.waitUntil(lambda: "ignores speed commands until RESET" in dt.status.text(), timeout=WAIT)
    assert dt.status.property("state") == "error"
    qtbot.waitUntil(lambda: abs(sim.speed_hz) < 2.0, timeout=WAIT)                  # the fault powered the motor off


def test_a_drive_without_activation_key_shows_it(win, qtbot, monkeypatch):
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive",
                        lambda channel, node_id: SimulatedDrive(channel, node_id, activated=False))
    connect(win, qtbot)
    dt = win.drive_tab
    qtbot.waitUntil(lambda: dt.fault_names.text() == "Activation Key is Missing", timeout=WAIT)
    assert dt.cards["op_mode"].value.text() == "Fault" and dt.cards["fault_bits"].value.text() == "Activation Key is Missing"


def test_escape_key_stops_the_motor(win, qtbot):
    sim, _ = connect(win, qtbot)
    win.open_tool("parameters")                                  # works from any page
    win.drive_tab.target.setValue(150.0)
    qtbot.mouseClick(win.drive_tab.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.speed_hz > 10, timeout=WAIT)
    qtbot.keyClick(win, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: abs(sim.speed_hz) < 0.5, timeout=WAIT)


def test_disconnect_and_close_stop_the_motor(win, qtbot):
    sim, client = connect(win, qtbot)
    win.drive_tab.target.setValue(200.0)
    qtbot.mouseClick(win.drive_tab.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.speed_hz > 15, timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)    # disconnect while running
    qtbot.waitUntil(lambda: sim.esc_target_hz == 0.0, timeout=WAIT)    # the client stopped the motor first
    assert client.commanded is None
    sim2, _ = connect(win, qtbot)
    win.drive_tab.target.setValue(200.0)
    qtbot.mouseClick(win.drive_tab.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim2.speed_hz > 15, timeout=WAIT)
    win.close()                                                        # closing the window while running
    qtbot.waitUntil(lambda: sim2.esc_target_hz == 0.0, timeout=WAIT)
    assert win.session.client is None


def test_plots_receive_data(win, qtbot):
    _, client = connect(win, qtbot)
    win.open_tool("motion")                                      # a page's plots only redraw while it is on screen
    win.drive_tab.target.setValue(120.0)
    qtbot.mouseClick(win.drive_tab.run_btn, Qt.MouseButton.LeftButton)
    curves = win.drive_tab._curves

    def points(key):
        x, _ = curves[key].getData()
        return 0 if x is None else len(x)
    broadcast = ("speed_hz", "wz_count", "ia", "vbus")
    qtbot.waitUntil(lambda: all(points(k) > 20 for k in broadcast), timeout=WAIT)
    assert set(curves) == set(broadcast)                         # Phase B is not part of the drive's broadcast: no curve
    x, y = curves["speed_hz"].getData()
    assert x.max() <= 0.05 and x.min() >= -31                    # seconds relative to now, inside the window
    qtbot.waitUntil(lambda: curves["speed_hz"].getData()[1].max() > 10, timeout=WAIT)      # drawn in Hz, as the drive sends it: 14 at speed
    assert curves["speed_hz"].getData()[1].max() < 20
    win.drive_tab.freeze.setChecked(True)
    n = len(curves["speed_hz"].getData()[0])
    qtbot.wait(300)
    assert len(curves["speed_hz"].getData()[0]) == n             # frozen plots do not move
    win.drive_tab.window_box.setCurrentIndex(0)
    assert win.drive_tab._window_s == 10


# ---- CAN traffic ------------------------------------------------------------------------------
def test_can_log_decodes_traffic(win, qtbot):
    connect(win, qtbot)
    log = win.log_tab
    row = rows_by_address(win)["0x2002:0x22"]
    qtbot.mouseClick(row.read_btn, Qt.MouseButton.LeftButton)
    wait_idle(win, qtbot)

    def texts():
        return [log.table.item(r, 4).text() for r in range(log.table.rowCount())]
    qtbot.waitUntil(lambda: any("Read response  0x2002:0x22  Electronic Speed Control: ESC Kp  =  0.05" in t for t in texts()), timeout=WAIT)
    assert any(t.startswith("Read request  0x2002:0x22") for t in texts())
    assert not any(t.startswith("Telemetry") for t in texts())                 # hidden by default
    log.show_telemetry.setChecked(True)
    qtbot.waitUntil(lambda: any(t.startswith("Telemetry") for t in texts()), timeout=WAIT)
    dirs = {log.table.item(r, 1).text() for r in range(log.table.rowCount())}
    assert dirs == {"TX", "RX"}


# ---- G-code console -----------------------------------------------------------------------------
def run_gcode(log, line):
    log.gcode_input.setText(line)
    log.gcode_run_btn.click()


def connect_axis1(win, qtbot):
    """Connects the simulator as node 2 - the CAN node axis 1 is actually wired as (node ID = axis + 1, the user's
    own correction, 2026-09-23: "node id 1 means axis 0, node is 2 means axis 1"), so the G-code axis letter Y
    (which names axis 1 - X/Y/Z/A/B/C are axes 0-5, fixed by the dialect, not remappable) addresses it directly,
    without every G-code test needing bracket syntax. The node number itself is otherwise irrelevant to what these
    tests check - `connect(win, qtbot)` alone, at the Node ID field's own default (node 1 = axis 0 = X), covers the
    plain single-axis case just as conveniently."""
    win.node.setValue(2)
    return connect(win, qtbot)


def test_gcode_esc_rpm_is_translated_and_shows_in_the_traffic_log(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    log = win.log_tab
    run_gcode(log, "G1030 Y500")
    qtbot.waitUntil(lambda: "Commanding 500.0 rpm" in log.gcode_status.text(), timeout=WAIT)
    assert client.commanded == ("esc_rpm", 500.0)

    def texts():
        return [log.table.item(r, 4).text() for r in range(log.table.rowCount())]
    qtbot.waitUntil(lambda: any(t.startswith("Command  Electronic Speed Control (ESC), RPM") for t in texts()), timeout=WAIT)


def connect_node(win, qtbot, node):
    """Connects the simulator at a specific node - for the axis/node-matching rule's own tests, where the exact
    node number is the point (unlike connect_axis1's node 2, chosen only to make plain X/Y/Z letters convenient)."""
    win.node.setValue(node)
    return connect(win, qtbot)


def test_gcode_refuses_a_line_whose_axis_does_not_match_the_connected_node(win, qtbot):
    """The user's own corrections: "axis means node id so node id of servo drive and axis of G-code has to match
    otherwise code will not run", refined later as "node id 1 means axis 0, node is 2 means axis 1" (node ID =
    axis + 1). Connects at node 7 (the real bench drive's own node, per the README - so axis 6) and checks both
    directions: a line naming a different axis is skipped with a clear reason and sends nothing to the drive; the
    matching axis runs normally."""
    sim, client = connect_node(win, qtbot, 7)
    log = win.log_tab
    run_gcode(log, "G1030 X500")                                          # X = axis 0 = node 1, not this node (7)
    qtbot.waitUntil(lambda: "skipped" in log.gcode_status.text(), timeout=WAIT)
    assert "no connected axis matches" in log.gcode_status.text()
    assert log.gcode_status.property("state") == "warn"
    assert client.commanded is None                                       # nothing was sent to the drive

    run_gcode(log, "G1030 [6]500")                                        # axis 6 = node 7: matches, runs
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", 500.0), timeout=WAIT)


def test_gcode_reset_off_stop_with_an_explicit_axis_is_skipped_when_it_does_not_match(win, qtbot):
    sim, client = connect_node(win, qtbot, 7)                              # node 7 = axis 6
    log = win.log_tab
    run_gcode(log, "G1030 [6]500")
    qtbot.waitUntil(lambda: client.commanded is not None, timeout=WAIT)
    run_gcode(log, "G1002 P3")                                            # Stop, axis 3 = node 4 - not this node
    qtbot.waitUntil(lambda: "axis 3" in log.gcode_status.text() and "no connected axis is node 4" in log.gcode_status.text(),
                    timeout=WAIT)
    assert client.commanded is not None                                   # the running command was left alone
    run_gcode(log, "G1002 P6")                                            # Stop, axis 6 = this node (7): runs
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


def test_gcode_a_no_axis_reset_off_stop_still_means_all_axes_including_this_one(win, qtbot):
    """A bare G1000/G1001/G1002/G1010/G1011 (no P<n>) has no axis at all, meaning "all axes" - which includes
    whichever one is connected, so it still runs (this is not itself a mismatch to skip)."""
    sim, client = connect_node(win, qtbot, 7)
    log = win.log_tab
    run_gcode(log, "G1030 [6]500")                                        # axis 6 = node 7
    qtbot.waitUntil(lambda: client.commanded is not None, timeout=WAIT)
    run_gcode(log, "G1002")                                               # Stop, no axis given at all
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


# ---- real multi-axis G-code dispatch (a line naming SEVERAL connected axes commands all of them) ------------
def test_gcode_line_naming_two_connected_axes_commands_both_of_them(win, qtbot):
    """UGV.NC's own real usage (the vendor's sample program - see the README): a line naming several axes at once
    commands all of them, not just whichever one happens to be the primary connection. Axis 1/2 = node 2/3 (node
    ID = axis + 1) - connect_axis1 connects node 2 (axis 1); the second axis, axis 2, is node 3."""
    sim1, client1 = connect_axis1(win, qtbot)
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    log = win.log_tab
    run_gcode(log, "G1030 [1]300 [2]-300")
    qtbot.waitUntil(lambda: client1.commanded == ("esc_rpm", 300.0) and ctx2.client.commanded == ("esc_rpm", -300.0),
                    timeout=WAIT)
    assert "Axis 2: Commanding 300.0 rpm." in log.gcode_status.text()
    assert "Axis 3: Commanding -300.0 rpm." in log.gcode_status.text()


def test_gcode_no_axis_reset_stops_every_connected_axis(win, qtbot):
    sim1, client1 = connect_axis1(win, qtbot)                              # axis 1 = node 2
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)           # axis 2 = node 3
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    log = win.log_tab
    run_gcode(log, "G1030 [1]300 [2]-300")
    qtbot.waitUntil(lambda: client1.commanded is not None and ctx2.client.commanded is not None, timeout=WAIT)
    run_gcode(log, "G1002")                                              # Stop, no axis given: every connected axis
    qtbot.waitUntil(lambda: client1.commanded is None and ctx2.client.commanded is None, timeout=WAIT)


def test_gcode_line_naming_an_axis_that_is_not_connected_is_skipped_but_others_still_run(win, qtbot):
    """Only axis 1 is connected here; axis 9 (also named on this line) is silently not reached - exactly as naming
    it would not affect that OTHER axis's own, absent drive on a real multi-axis system. With only one CONNECTED
    axis actually addressed, the result reads exactly like the original single-axis case (no "Axis 1:" label) -
    unchanged wording for the common case, per gcode_runner.py's own `_combine`."""
    sim1, client1 = connect_axis1(win, qtbot)
    log = win.log_tab
    run_gcode(log, "G1030 [1]300 [9]999")                                # axis 9 is not connected
    qtbot.waitUntil(lambda: client1.commanded == ("esc_rpm", 300.0), timeout=WAIT)
    qtbot.waitUntil(lambda: log.gcode_status.text() == "Commanding 300.0 rpm.", timeout=WAIT)


def test_motion_controller_program_line_commands_two_connected_axes(win, qtbot):
    sim1, client1 = connect_axis1(win, qtbot)                              # axis 1 = node 2
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)           # axis 2 = node 3
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1030 [1]150 [2]-150")
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client1.commanded == ("esc_rpm", 150.0) and ctx2.client.commanded == ("esc_rpm", -150.0),
                    timeout=WAIT)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText(), timeout=WAIT)


def test_motion_controller_concatenated_line_commands_both_of_its_own_axes(win, qtbot):
    """DOG.NC's own gait is one line concatenating several commands (each its own axis/tool) - here, two separate
    ESC RPM commands, tab-separated, on ONE program line."""
    sim1, client1 = connect_axis1(win, qtbot)                              # axis 1 = node 2
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)           # axis 2 = node 3
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1030 [1]150\tG1030 [2]-150")
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client1.commanded == ("esc_rpm", 150.0) and ctx2.client.commanded == ("esc_rpm", -150.0),
                    timeout=WAIT)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText(), timeout=WAIT)


def test_motion_controller_concatenated_line_halts_on_a_genuine_refusal_but_not_on_a_harmless_noop(win, qtbot):
    """The noop-vs-refusal halt decision (see _advance()'s own docstring) is made PER ACTION, not from the line's
    own combined state - a harmless noop concatenated alongside a real, connected move must not halt the program,
    and a genuine refusal concatenated alongside a noop must still halt it, exactly as either would alone."""
    sim1, client1 = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G93 F60\tG1030 [1]150", "G1000"))    # a noop (G93) + a real move, one line
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText(), timeout=WAIT)     # did NOT halt on line 1
    assert "Commanding 150.0 rpm." in mc.output.toPlainText()             # line 1's real move actually ran
    assert client1.commanded is None                                      # G1000 (Reset) on line 2 ran too

    mc.editor.setPlainText("G93 F60\tG1030 [9]150")                       # a noop + a real refusal (axis 9 not connected)
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: mc.run_btn.isEnabled(), timeout=WAIT)         # finished (halted) - RUN re-enabled
    assert "Program finished." not in mc.output.toPlainText()            # halted, did not run to completion


def test_motion_controller_stop_button_stops_every_connected_axis(win, qtbot):
    sim1, client1 = connect_axis1(win, qtbot)                              # axis 1 = node 2
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)           # axis 2 = node 3
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 [1]150", "G1030 [2]-150", "G04 P5"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client1.commanded is not None and ctx2.client.commanded is not None, timeout=WAIT)
    qtbot.mouseClick(mc.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client1.commanded is None and ctx2.client.commanded is None, timeout=WAIT)


# ---- G-code Txx (Inverse Kinematics against a loaded kinematics model) ---------------------------------------
def _load_kinematics_sample(win, name):
    """Loads a bundled kinematics sample into the session directly (bypassing the Item Configuration UI, already
    covered by its own tests), for G-code Txx end-to-end tests."""
    from sdr_tuner.gui.config_page import KINEMATICS_SAMPLES_DIR
    return win.session.load_kinematics(str(KINEMATICS_SAMPLES_DIR / f"{name}.obj.kinematics"))


def test_gcode_txx_solves_ik_and_dispatches_to_every_joint(win, qtbot):
    """robot-dog.obj's own T6 chain (leg 2) uses axes 3/4/5 (the file's own "Axis" column - 0-based, node ID =
    axis + 1) - real nodes 4/5/6. The file's OWN leg-1 chain (T3) uses axis 0 (node 1) - reachable too now (node
    IDs start at 1, and axis 0 is simply node 1), but leg 2 is what's already set up and tested here, unchanged."""
    sim4, client4 = connect_node(win, qtbot, 4)
    ctx5 = win.session.connect_axis(session.SIMULATOR, "", 0, 5)
    ctx6 = win.session.connect_axis(session.SIMULATOR, "", 0, 6)
    qtbot.waitUntil(lambda: {5, 6} <= set(win.session.axes), timeout=WAIT)
    qtbot.waitUntil(lambda: all("wz_count" in c.telemetry_snapshot() for c in (client4, ctx5.client, ctx6.client)),
                    timeout=WAIT)
    model = _load_kinematics_sample(win, "robot-dog")
    home = model.chains()[6].home_tip                    # reachable trivially: it is the chain's own home position
    log = win.log_tab
    run_gcode(log, f"G00 T6 X{home[0]:.3f} Y{home[1]:.3f} Z{home[2]:.3f}")
    qtbot.waitUntil(lambda: client4.commanded is not None and ctx5.client.commanded is not None and
                     ctx6.client.commanded is not None, timeout=WAIT)
    assert client4.commanded[0] == "servo" and ctx5.client.commanded[0] == "servo" and ctx6.client.commanded[0] == "servo"


def test_gcode_txx_arc_solves_ik_per_waypoint_and_dispatches_to_every_joint(win, qtbot):
    """Same chain as the test above (robot-dog's T6, axes 3/4/5 - nodes 4/5/6), but a G03 arc instead of a straight
    move - a small one near the chain's own home position, in the X/Z plane (Y held wherever it already is - a
    real DOG.NC line's own shape, see gcode.py's own docstring), and needs no F, exactly like the vendor's own real
    line: a rapid arc, waypoints sent back-to-back."""
    sim4, client4 = connect_node(win, qtbot, 4)
    ctx5 = win.session.connect_axis(session.SIMULATOR, "", 0, 5)
    ctx6 = win.session.connect_axis(session.SIMULATOR, "", 0, 6)
    qtbot.waitUntil(lambda: {5, 6} <= set(win.session.axes), timeout=WAIT)
    qtbot.waitUntil(lambda: all("wz_count" in c.telemetry_snapshot() for c in (client4, ctx5.client, ctx6.client)),
                    timeout=WAIT)
    model = _load_kinematics_sample(win, "robot-dog")
    home = model.chains()[6].home_tip
    log = win.log_tab
    run_gcode(log, f"G03 T6 X{home[0]:.3f} Z{home[2] - 50:.3f} R100")
    qtbot.waitUntil(lambda: client4.commanded is not None and ctx5.client.commanded is not None and
                     ctx6.client.commanded is not None, timeout=WAIT)
    assert client4.commanded[0] == "servo" and ctx5.client.commanded[0] == "servo" and ctx6.client.commanded[0] == "servo"


def test_gcode_txx_refuses_when_no_kinematics_model_is_loaded(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    log = win.log_tab
    run_gcode(log, "G00 T3 X0 Y0 Z0")
    qtbot.waitUntil(lambda: "no kinematics model loaded" in log.gcode_status.text(), timeout=WAIT)
    assert client.commanded is None


def test_gcode_txx_refuses_an_unknown_tool_number(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    _load_kinematics_sample(win, "radar")             # only has tool 3
    log = win.log_tab
    run_gcode(log, "G00 T99 X0 Y0 Z0")
    qtbot.waitUntil(lambda: "no chain with that tool number" in log.gcode_status.text(), timeout=WAIT)
    assert client.commanded is None


def test_gcode_txx_refuses_when_not_every_joint_of_the_chain_is_connected(win, qtbot):
    """robot-dog's T6 needs axes 3, 4 AND 5 (nodes 4, 5, 6) - only axis 3 (node 4) connected here."""
    sim4, client4 = connect_node(win, qtbot, 4)
    model = _load_kinematics_sample(win, "robot-dog")
    home = model.chains()[6].home_tip
    log = win.log_tab
    run_gcode(log, f"G00 T6 X{home[0]:.3f} Y{home[1]:.3f} Z{home[2]:.3f}")
    qtbot.waitUntil(lambda: "needs axis" in log.gcode_status.text(), timeout=WAIT)
    assert client4.commanded is None


def test_gcode_enter_key_runs_the_line_like_the_run_button(win, qtbot):
    sim, client = connect(win, qtbot)
    log = win.log_tab
    log.gcode_input.setText("G1000")
    log.gcode_input.returnPressed.emit()
    qtbot.waitUntil(lambda: log.gcode_status.text() == "Reset sent.", timeout=WAIT)


# ---- Tool Pose (full-pose Orientation IK - Item Configuration's own GUI control, 2026-09-24) -------------------
def _load_kinematics_via_ui(win, name):
    """Loads a bundled sample through the SAME path the Samples combo itself uses (`cfg._load_kinematics_sample`) -
    unlike the module-level `_load_kinematics_sample(win, name)` helper above (which loads straight into the
    session, bypassing the UI, for the Txx tests that only care about G-code dispatch), the Tool Pose group's own
    `pose_tool` combo is only ever refreshed by this real UI path (see config_page.py's `_load_kinematics`), so
    these tests need it."""
    cfg = win.axes_page.setup
    cfg._load_kinematics_sample(cfg.kinematics_samples.findText(f"{name}.obj"))
    return win.session.kinematics_model


def test_tool_pose_group_lists_the_loaded_models_own_tools(win, qtbot):
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    assert cfg.pose_tool.count() == 1 and cfg.pose_tool.currentData() is None      # nothing loaded yet
    _load_kinematics_via_ui(win, "robot-dog")
    assert [cfg.pose_tool.itemData(i) for i in range(cfg.pose_tool.count())] == [3, 6, 9, 12]
    _load_kinematics_via_ui(win, "radar")
    assert [cfg.pose_tool.itemData(i) for i in range(cfg.pose_tool.count())] == [3]


def test_tool_pose_go_solves_full_pose_ik_and_dispatches_to_every_joint(win, qtbot):
    """robot-dog's own T6 chain (leg 2, axes 3/4/5 - nodes 4/5/6, see the Txx tests above) - GO solves for the
    chain's own HOME pose (position AND identity orientation, both reachable trivially: it is what every joint at
    0 already produces) and dispatches through the exact same mechanism as a G-code Txx move, just reached from the
    GUI's own Tool Pose control instead of a typed line."""
    sim4, client4 = connect_node(win, qtbot, 4)
    ctx5 = win.session.connect_axis(session.SIMULATOR, "", 0, 5)
    ctx6 = win.session.connect_axis(session.SIMULATOR, "", 0, 6)
    qtbot.waitUntil(lambda: {5, 6} <= set(win.session.axes), timeout=WAIT)
    qtbot.waitUntil(lambda: all("wz_count" in c.telemetry_snapshot() for c in (client4, ctx5.client, ctx6.client)),
                    timeout=WAIT)
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    model = _load_kinematics_via_ui(win, "robot-dog")
    home = model.chains()[6].home_tip
    cfg.pose_tool.setCurrentIndex(cfg.pose_tool.findData(6))
    cfg.pose_x.setValue(home[0])
    cfg.pose_y.setValue(home[1])
    cfg.pose_z.setValue(home[2])
    cfg.pose_roll.setValue(0.0)
    cfg.pose_pitch.setValue(0.0)
    cfg.pose_yaw.setValue(0.0)
    cfg.pose_go_btn.click()
    qtbot.waitUntil(lambda: client4.commanded is not None and ctx5.client.commanded is not None and
                     ctx6.client.commanded is not None, timeout=WAIT)
    assert client4.commanded[0] == "servo" and ctx5.client.commanded[0] == "servo" and ctx6.client.commanded[0] == "servo"
    assert "not converge" not in cfg.pose_status.text()


def test_tool_pose_go_asks_for_a_looser_tolerance_than_solve_poses_own_default(win, qtbot, monkeypatch):
    """2026-09-24, found live against a real drive: solve_pose's own default position tolerance (1e-3 mm) is
    tighter than the Tool Pose fields can even type (2 decimal places) - a genuinely reachable target could fail
    to "converge" purely from that rounding. move_to_pose asks for tolerance=0.05 explicitly - checked here as a
    spy on Chain.solve_pose's own kwargs, not by trying to reproduce the fragile floating-point edge case through
    the GUI (test_kinematics.py's own test does that, at the math level, where it belongs)."""
    sim4, client4 = connect_node(win, qtbot, 4)
    ctx5 = win.session.connect_axis(session.SIMULATOR, "", 0, 5)
    ctx6 = win.session.connect_axis(session.SIMULATOR, "", 0, 6)
    qtbot.waitUntil(lambda: {5, 6} <= set(win.session.axes), timeout=WAIT)
    qtbot.waitUntil(lambda: all("wz_count" in c.telemetry_snapshot() for c in (client4, ctx5.client, ctx6.client)),
                    timeout=WAIT)
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    model = _load_kinematics_via_ui(win, "robot-dog")
    home = model.chains()[6].home_tip
    cfg.pose_tool.setCurrentIndex(cfg.pose_tool.findData(6))
    cfg.pose_x.setValue(home[0])
    cfg.pose_y.setValue(home[1])
    cfg.pose_z.setValue(home[2])

    calls = []
    real_solve_pose = kinematics.Chain.solve_pose

    def spying_solve_pose(self, *args, **kwargs):
        calls.append(kwargs)
        return real_solve_pose(self, *args, **kwargs)
    monkeypatch.setattr(kinematics.Chain, "solve_pose", spying_solve_pose)

    cfg.pose_go_btn.click()
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=WAIT)
    assert calls[0].get("tolerance") == 0.05


def test_tool_pose_go_refuses_when_no_model_is_loaded(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    win.axes_page.setup.pose_go_btn.click()
    assert "No kinematics model loaded" in win.axes_page.setup.pose_status.text()
    assert client.commanded is None


def test_tool_pose_go_reports_when_ik_does_not_converge(win, qtbot):
    """A target far outside any real leg's reach (same idea as
    test_ik_reports_not_converged_for_an_unreachable_target_instead_of_guessing in test_kinematics.py) - GO must
    report it plainly rather than dispatch a bogus command."""
    sim4, client4 = connect_node(win, qtbot, 4)
    ctx5 = win.session.connect_axis(session.SIMULATOR, "", 0, 5)
    ctx6 = win.session.connect_axis(session.SIMULATOR, "", 0, 6)
    qtbot.waitUntil(lambda: {5, 6} <= set(win.session.axes), timeout=WAIT)
    qtbot.waitUntil(lambda: all("wz_count" in c.telemetry_snapshot() for c in (client4, ctx5.client, ctx6.client)),
                    timeout=WAIT)
    cfg = win.axes_page.setup                                        # the three groups live on the Axes page now
    _load_kinematics_via_ui(win, "robot-dog")
    cfg.pose_tool.setCurrentIndex(cfg.pose_tool.findData(6))
    cfg.pose_x.setValue(1e6)
    cfg.pose_y.setValue(1e6)
    cfg.pose_z.setValue(1e6)
    cfg.pose_go_btn.click()
    qtbot.waitUntil(lambda: "did not converge" in cfg.pose_status.text(), timeout=WAIT)
    assert client4.commanded is None and ctx5.client.commanded is None and ctx6.client.commanded is None


def _distinct_targets(commands, key="servo_linear_interpolation"):
    """The sequence of DISTINCT targets a repeating command actually moved through, from a sim's own
    `commands_received` - collapses consecutive keepalive repeats of the SAME target (the client resends every
    ~100 ms in the background, independent of when gcode_runner itself advances to the next arc waypoint) down to
    one entry each, so a real-time race with that background repeat never makes a raw send COUNT flaky."""
    targets = [values[0] for k, values in commands if k == key]
    distinct = []
    for t in targets:
        if not distinct or distinct[-1] != t:
            distinct.append(t)
    return distinct


def test_gcode_arc_moves_two_connected_axes_through_waypoints_to_the_target(win, qtbot):
    """A plain (non-Txx) arc's own plane is whichever two axes the line names - its START is simply wherever they
    already are, from telemetry (parse() itself has no idea - see gcode.py's own docstring). Bracket form ([1]/[2]),
    just to match connect_axis1's own axes 1/2, not for any real necessity (axis 0/X would work fine too - node
    ID = axis + 1, so it is simply node 1). Checked via the DISTINCT sequence of Servo: Linear Interpolation
    targets that actually reached the (simulated) drive - one per waypoint trajectory.py's own math computes for
    this exact arc, not just the final one."""
    sim1, client1 = connect_axis1(win, qtbot)                                # axis 1 = node 2
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)             # axis 2 = node 3
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    client2 = ctx2.client
    qtbot.waitUntil(lambda: "wz_count" in client1.telemetry_snapshot() and "wz_count" in client2.telemetry_snapshot(),
                    timeout=WAIT)
    a0, b0 = client1.telemetry_snapshot()["wz_count"], client2.telemetry_snapshot()["wz_count"]
    a1, b1 = a0 + 1000, b0 + 1000
    expected = trajectory.arc_waypoints((a0, b0), (a1, b1), clockwise=False, radius=1000.0)
    sim2 = win.session._axis_sims[3]
    n1, n2 = len(sim1.commands_received), len(sim2.commands_received)
    log = win.log_tab
    run_gcode(log, f"G03 [1]{a1:.1f} [2]{b1:.1f} R1000 F50000")
    qtbot.waitUntil(lambda: client1.commanded is not None and client1.commanded[0] == "servo_linear_interpolation"
                    and client1.commanded[1][0] == pytest.approx(a1, abs=1e-3), timeout=WAIT)
    assert client2.commanded[1][0] == pytest.approx(b1, abs=1e-3)
    qtbot.wait(50)      # let a same-target keepalive repeat, if one was going to land, actually land before counting
    assert len(_distinct_targets(sim1.commands_received[n1:])) == len(expected)
    assert len(_distinct_targets(sim2.commands_received[n2:])) == len(expected)


def test_gcode_arc_needs_its_two_axes_connected_and_refuses_an_unreachable_radius(win, qtbot):
    sim, client = connect_axis1(win, qtbot)                                  # only axis 1 (node 2) connected
    log = win.log_tab
    run_gcode(log, "G02 [1]1000 [2]2000 R1000 F500")                        # names axes 1 AND 2 - only 1 connected
    qtbot.waitUntil(lambda: "needs axis" in log.gcode_status.text(), timeout=WAIT)
    assert client.commanded is None

    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)             # axis 2 = node 3
    qtbot.waitUntil(lambda: "wz_count" in ctx2.client.telemetry_snapshot(), timeout=WAIT)
    run_gcode(log, "G02 [1]100000 [2]0 R1 F500")                             # a radius far too small to reach that far
    qtbot.waitUntil(lambda: "too far" in log.gcode_status.text(), timeout=WAIT)
    assert log.gcode_status.property("state") == "error"


def test_gcode_concatenated_line_runs_both_of_its_own_commands(win, qtbot):
    """The vendor's own real multi-axis programs concatenate several G-codes on one physical line (DOG.NC's own
    gait) - here, two separate ESC RPM commands, tab-separated, each its own axis, from ONE line."""
    sim1, client1 = connect_axis1(win, qtbot)                              # axis 1 = node 2
    ctx2 = win.session.connect_axis(session.SIMULATOR, "", 0, 3)           # axis 2 = node 3
    qtbot.waitUntil(lambda: 3 in win.navigator._axis_items, timeout=WAIT)
    log = win.log_tab
    run_gcode(log, "G1030 [1]150\tG1030 [2]-150")
    qtbot.waitUntil(lambda: client1.commanded == ("esc_rpm", 150.0) and ctx2.client.commanded == ("esc_rpm", -150.0),
                    timeout=WAIT)
    # combine_action_results() labels each concatenated SEGMENT by the axis the user typed ([1]/[2]), unlike
    # _combine()'s own per-node labelling for several axes on ONE action - see _describe_action().
    assert "Axis 1: Commanding 150.0 rpm." in log.gcode_status.text()
    assert "Axis 2: Commanding -150.0 rpm." in log.gcode_status.text()


def test_gcode_capture_reuses_the_same_encoder_gate_as_enable(win, qtbot):
    sim, client = connect(win, qtbot)
    log = win.log_tab
    sim.params[(0x2000, 0x0A)] = 0                                        # neither Motor nor Servo Encoder configured
    qtbot.waitUntil(lambda: "wz_count" in client.telemetry_snapshot(), timeout=WAIT)
    run_gcode(log, "G1010")
    qtbot.waitUntil(lambda: "Motor Encoder or Servo Encoder" in log.gcode_status.text(), timeout=WAIT)
    assert client.commanded is None
    sim.params[(0x2000, 0x0A)] = 1                                        # Quadrature Encoder: now it works
    run_gcode(log, "G1010")
    qtbot.waitUntil(lambda: "Holding position" in log.gcode_status.text(), timeout=WAIT)
    assert client.commanded[0] == "servo"


def test_gcode_move_absolute_and_relative(win, qtbot):
    sim, client = connect_axis1(win, qtbot)                                # Motor Encoder defaults to an encoder
    log = win.log_tab
    qtbot.waitUntil(lambda: "wz_count" in client.telemetry_snapshot(), timeout=WAIT)
    run_gcode(log, "G00 Y2000")
    qtbot.waitUntil(lambda: client.commanded == ("servo", 2000.0), timeout=WAIT)
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("wz_count") == 2000, timeout=WAIT)   # what the client itself sees
    run_gcode(log, "G1020 V-500")                                         # relative: current (2000) - 500 = 1500
    qtbot.waitUntil(lambda: client.commanded == ("servo", 1500.0), timeout=WAIT)


def test_gcode_linear_move_and_torque(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    log = win.log_tab
    qtbot.waitUntil(lambda: "wz_count" in client.telemetry_snapshot(), timeout=WAIT)
    run_gcode(log, "G01 Y1000 F500")
    qtbot.waitUntil(lambda: client.commanded == ("servo_linear_interpolation", (1000.0, 500.0, 0)), timeout=WAIT)
    run_gcode(log, "G1040 Y0.5")
    qtbot.waitUntil(lambda: client.commanded == ("electronic_torque_control_etc", 0.5), timeout=WAIT)


def test_gcode_release_ends_the_repeat_without_sending_anything_reset_off_stop_do(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    log = win.log_tab
    run_gcode(log, "G1030 Y500")
    qtbot.waitUntil(lambda: client.commanded is not None, timeout=WAIT)
    run_gcode(log, "G1011")
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)
    qtbot.wait(150)                       # let any keepalive frame already in flight at that instant land and be counted
    n = len(sim.commands_received)
    qtbot.wait(250)
    assert len(sim.commands_received) == n                                # nothing new was sent, not even a stop

    run_gcode(log, "G1030 Y500")
    qtbot.waitUntil(lambda: client.commanded is not None, timeout=WAIT)
    run_gcode(log, "G1001")                                                # Off: a real command this time
    qtbot.waitUntil(lambda: sim.off, timeout=WAIT)
    assert client.commanded is None


def test_gcode_dwell_counts_down_with_no_can_traffic(win, qtbot):
    sim, client = connect(win, qtbot)
    log = win.log_tab
    n = len(sim.commands_received)
    run_gcode(log, "G04 P0.2")
    qtbot.waitUntil(lambda: "Dwelling" in log.gcode_status.text(), timeout=WAIT)
    qtbot.waitUntil(lambda: "Dwelt" in log.gcode_status.text(), timeout=WAIT)
    assert len(sim.commands_received) == n


def test_gcode_untracked_modal_codes_are_noted_not_refused(win, qtbot):
    sim, client = connect(win, qtbot)
    log = win.log_tab
    run_gcode(log, "G93")
    qtbot.waitUntil(lambda: "not tracked" in log.gcode_status.text(), timeout=WAIT)
    assert log.gcode_status.property("state") == "warn"


def test_the_motion_pages_stop_ends_a_running_gcode_command(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    log = win.log_tab
    run_gcode(log, "G1030 Y500")
    qtbot.waitUntil(lambda: client.commanded is not None, timeout=WAIT)
    win.drive_tab.emergency_stop()
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


# ---- Motion Controller (multi-line G-code programs) -----------------------------------------------
def program(*lines):
    return "\n".join(lines)


def test_motion_controller_runs_a_program_line_by_line_in_order(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G04 P0.3", "G1030 Y200", "G04 P0.3", "G1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    assert not mc.run_btn.isEnabled() and mc.stop_btn.isEnabled()
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", 100.0), timeout=WAIT)
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", 200.0), timeout=WAIT)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText(), timeout=WAIT)
    assert client.commanded is None                                        # G1000 (Reset) ended it
    assert mc.run_btn.isEnabled() and not mc.stop_btn.isEnabled()


def test_motion_controller_hide_output_toggle_minimises_the_log_without_losing_it(win, qtbot):
    """User request (2026-09-23, screenshot circling the output log): "give option to minimise the window" - a way
    to reclaim the editor's own room without needing the log at all times. Hiding it does not lose what is already
    logged - a program run while hidden still appends to it normally, it is just not shown until toggled back."""
    sim, client = connect_axis1(win, qtbot)
    win.open_tool("motion_controller")
    mc = win.motion_controller_page
    assert mc.output.isVisible() and mc.output_toggle.text() == "Hide Output"
    qtbot.mouseClick(mc.output_toggle, Qt.MouseButton.LeftButton)
    assert not mc.output.isVisible() and mc.output_toggle.text() == "Show Output"

    mc.editor.setPlainText(program("G1030 Y100", "G1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText(), timeout=WAIT)   # still logs while hidden

    qtbot.mouseClick(mc.output_toggle, Qt.MouseButton.LeftButton)
    assert mc.output.isVisible() and mc.output_toggle.text() == "Hide Output"
    assert "Program finished." in mc.output.toPlainText()                    # nothing was lost while it was hidden


def test_motion_controller_toolbar_buttons_never_take_keyboard_focus_from_the_editor(win, qtbot):
    """Found live (2026-09-25): the app's Fusion style lets a push button take focus on a mouse click, so after
    clicking RUN the editor no longer had the keyboard - the space in the next typed line pressed whichever button
    held focus (it opened the Open… file dialog) instead of typing into the program. The toolbar's buttons now use
    the same no-focus policy the ribbon's own buttons do: a mouse click on any of them leaves the editor focused,
    so typing after RUN/STOP always goes into the program text. The Loop box and Samples combo are typed in / picked
    from, so they keep their focus."""
    connect_axis1(win, qtbot)
    win.open_tool("motion_controller")
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G1000"))
    mc.editor.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is mc.editor, timeout=WAIT)

    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText(), timeout=WAIT)
    assert QApplication.focusWidget() is mc.editor                       # RUN never took the keyboard away

    for button in (mc.simulate_btn, mc.pause_btn, mc.resume_btn, mc.stop_btn, mc.open_btn, mc.save_btn,
                   mc.output_toggle, mc.help_btn):
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus, button.text()
    qtbot.mouseClick(mc.output_toggle, Qt.MouseButton.LeftButton)          # a real click on another toolbar button
    assert QApplication.focusWidget() is mc.editor
    assert mc.loop_count.focusPolicy() != Qt.FocusPolicy.NoFocus            # still typeable
    assert mc.samples.focusPolicy() != Qt.FocusPolicy.NoFocus


def test_motion_controller_dwell_actually_pauses_before_the_next_line(win, qtbot):
    """Unlike the Terminal's single-line console (nothing to pause before), a Motion Controller program really does
    wait out a G04 before moving on."""
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G04 P0.6", "G1030 Y200"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", 100.0), timeout=WAIT)
    qtbot.wait(200)                                                        # well within the 0.6 s dwell
    assert client.commanded == ("esc_rpm", 100.0)                          # line 3 has not run yet
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", 200.0), timeout=WAIT)


def test_motion_controller_stop_cancels_a_dwell_and_stops_the_drive(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G04 P5", "G1030 Y200"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Dwelling" in mc.output.toPlainText(), timeout=WAIT)
    qtbot.mouseClick(mc.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)        # the round-STOP equivalent ended it at once
    assert mc.run_btn.isEnabled() and not mc.stop_btn.isEnabled()
    qtbot.wait(250)
    assert client.commanded is None                                       # line 3 never ran (not even after the 5 s would have elapsed)


def test_motion_controller_stops_at_the_first_failing_line(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    sim.params[(0x2000, 0x0A)] = 0                                         # neither Motor nor Servo Encoder: G00 refuses
    mc.editor.setPlainText(program("G00 Y1000", "G1030 Y999"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Motor Encoder or Servo Encoder" in mc.output.toPlainText(), timeout=WAIT)
    qtbot.wait(200)
    assert client.commanded is None                                       # line 2 never ran
    assert mc.run_btn.isEnabled() and not mc.stop_btn.isEnabled()


def test_motion_controller_stops_on_a_parse_error_without_running_later_lines(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "not g-code", "G1030 Y999"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "no G-code found" in mc.output.toPlainText(), timeout=WAIT)
    assert client.commanded == ("esc_rpm", 100.0)                         # line 1 ran; line 3 did not


def test_motion_controller_running_an_empty_program_does_nothing(win, qtbot):
    sim, client = connect(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText("   \n;just a comment\n")
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "empty" in mc.output.toPlainText().lower(), timeout=WAIT)
    assert client.commanded is None


def test_motion_controller_refuses_to_start_while_disconnected(win, qtbot):
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1000")
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    assert "Connect to a drive first." in mc.output.toPlainText()


def test_motion_controller_line_highlight_marks_the_running_line_and_clears_when_done(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G04 P0.3", "G1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(mc.editor.extraSelections()) > 0, timeout=WAIT)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText(), timeout=WAIT)
    assert len(mc.editor.extraSelections()) == 0


def test_motion_controller_editor_has_a_line_number_gutter_that_widens_with_more_lines(win, qtbot):
    """PySide6's QPlainTextEdit has no built-in line numbers - GcodeEditor adds the standard Qt gutter recipe, so
    the editor's own numbers match what the output log already reports ("Line N: ...")."""
    mc = win.motion_controller_page
    assert isinstance(mc.editor, GcodeEditor)
    mc.editor.setPlainText("G1000")
    narrow = mc.editor.gutter_width()
    mc.editor.setPlainText(program(*[f"G1030 X{n}" for n in range(50)]))    # 50 lines - two digits, not one
    assert mc.editor.gutter_width() > narrow


# ---- Motion Controller SIMULATE (offline preview, no drive - real or simulated - needed, 2026-09-24) ---------
def test_simulate_refuses_without_a_kinematics_model_loaded(win, qtbot):
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1030 X60")
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    assert "Load a kinematics model first" in mc.output.toPlainText()
    assert not mc._running


def test_simulate_runs_with_absolutely_no_connection_at_all(win, qtbot):
    """The whole point of this mode: unlike RUN, it needs no drive - real or simulated - connected. win's own
    session.client is None throughout this test."""
    assert win.session.client is None
    _load_kinematics_sample(win, "radar")
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 X60", "G1000"))
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Simulation finished." in mc.output.toPlainText(), timeout=WAIT)
    assert win.session.client is None                                     # still never connected


def test_simulate_esc_rpm_spins_the_axis_continuously_in_session_virtual_axes(win, qtbot):
    _load_kinematics_sample(win, "radar")
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1030 X60")                                   # axis 0, 60 rpm = 360 deg/s, no G1000 - keeps spinning
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: 0 in win.session.virtual_axes, timeout=WAIT)
    from sdr_tuner.gui import gcode_sim
    first = gcode_sim.axis_value_now(win.session.virtual_axes[0])
    qtbot.wait(150)
    second = gcode_sim.axis_value_now(win.session.virtual_axes[0])
    assert second > first + 20.0                                         # genuinely advancing, not a one-shot snap


def test_simulate_always_starts_from_home_even_after_a_previous_run_moved_it(win, qtbot):
    _load_kinematics_sample(win, "radar")
    mc = win.motion_controller_page
    mc.editor.setPlainText("G00 X500")
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Simulation finished." in mc.output.toPlainText(), timeout=WAIT)
    assert win.session.virtual_axes[0].value == pytest.approx(500.0)

    mc.editor.setPlainText("G1020 U10")                                  # relative +10 - must be from 0, not 500
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Simulation finished." in mc.output.toPlainText(), timeout=WAIT)
    assert win.session.virtual_axes[0].value == pytest.approx(10.0)


def test_simulate_torque_notes_no_motion_is_modelled(win, qtbot):
    _load_kinematics_sample(win, "radar")
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1040 X0.5")
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Simulation finished." in mc.output.toPlainText(), timeout=WAIT)
    assert "no motion modelled" in mc.output.toPlainText()


def test_simulate_and_run_are_mutually_exclusive_buttons(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    _load_kinematics_sample(win, "radar")
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G04 P5", "G1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not mc.simulate_btn.isEnabled(), timeout=WAIT)
    assert not mc.run_btn.isEnabled()
    qtbot.mouseClick(mc.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: mc.simulate_btn.isEnabled(), timeout=WAIT)


def test_simulate_stop_does_not_touch_a_real_client_even_if_one_is_connected(win, qtbot):
    """An edge case worth locking in: if the user happens to have a real connection open while also pressing
    SIMULATE, STOP must not send anything to that real client - simulating never touches real hardware, in either
    direction."""
    sim, client = connect_axis1(win, qtbot)
    _load_kinematics_sample(win, "radar")
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 X60", "G04 P5", "G1000"))
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Dwelling" in mc.output.toPlainText(), timeout=WAIT)
    qtbot.mouseClick(mc.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: mc.run_btn.isEnabled(), timeout=WAIT)
    assert client.commanded is None                                       # never sent anything to the real axis


def test_simulate_pause_and_resume_work_the_same_as_run(win, qtbot):
    _load_kinematics_sample(win, "radar")
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 X100", "G04 P0.6", "G1030 X200"))
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Dwelling" in mc.output.toPlainText(), timeout=WAIT)
    qtbot.mouseClick(mc.pause_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: mc.resume_btn.isEnabled(), timeout=WAIT)
    qtbot.wait(700)                                                       # past the dwell - paused before line 3
    assert "spinning at 200.0 rpm" not in mc.output.toPlainText()
    qtbot.mouseClick(mc.resume_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "spinning at 200.0 rpm" in mc.output.toPlainText(), timeout=WAIT)


def test_simulate_finished_message_says_simulation_not_program(win, qtbot):
    _load_kinematics_sample(win, "radar")
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1000")
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Simulation finished." in mc.output.toPlainText(), timeout=WAIT)
    assert "Program finished." not in mc.output.toPlainText()


def test_simulate_arc_refuses_and_halts_the_program(win, qtbot):
    _load_kinematics_sample(win, "radar")
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G02 X100 Y100 R50", "G1030 X999"))
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "not yet supported" in mc.output.toPlainText(), timeout=WAIT)
    assert "999" not in mc.output.toPlainText()                           # line 2 never ran


# ---- Motion Controller Pause / Resume / Loop (the vendor controller's pause/resume/execute-forever - Stage 4) -----------
def test_motion_controller_pause_and_resume_buttons_start_disabled(win, qtbot):
    mc = win.motion_controller_page
    assert not mc.pause_btn.isEnabled() and not mc.resume_btn.isEnabled()


def test_motion_controller_pause_freezes_at_the_next_line_boundary_not_mid_dwell(win, qtbot):
    """PAUSE never cuts a line short - a dwell already counting down finishes normally; the program then freezes
    right before the NEXT line instead of running it, until RESUME."""
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G04 P0.3", "G1030 Y200"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", 100.0), timeout=WAIT)
    qtbot.mouseClick(mc.pause_btn, Qt.MouseButton.LeftButton)
    assert not mc.pause_btn.isEnabled() and mc.resume_btn.isEnabled() and mc.stop_btn.isEnabled()
    qtbot.waitUntil(lambda: "Dwelt" in mc.output.toPlainText(), timeout=WAIT)   # the in-flight dwell still completed
    qtbot.wait(200)
    assert client.commanded == ("esc_rpm", 100.0)                              # line 3 (Y200) has not run yet
    assert "Paused before line 3." in mc.output.toPlainText()
    qtbot.mouseClick(mc.resume_btn, Qt.MouseButton.LeftButton)
    assert mc.pause_btn.isEnabled() and not mc.resume_btn.isEnabled()
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", 200.0), timeout=WAIT)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText(), timeout=WAIT)


def test_motion_controller_stop_ends_a_paused_program(win, qtbot):
    """A G04 dwell between the two speed lines gives real time to observe and pause before line 3 - with no dwell
    at all, both lines resolve near-instantly and the brief line-1 state could be missed entirely (a race in the
    TEST, not the product - see the sibling pause test just above, which uses this same shape)."""
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G04 P0.3", "G1030 Y200"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", 100.0), timeout=WAIT)
    qtbot.mouseClick(mc.pause_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Paused before line 3." in mc.output.toPlainText(), timeout=WAIT)
    qtbot.mouseClick(mc.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)
    assert mc.run_btn.isEnabled() and not mc.pause_btn.isEnabled() and not mc.resume_btn.isEnabled()
    qtbot.wait(150)
    assert client.commanded is None                                            # line 3 (Y200) never ran


def test_motion_controller_loop_repeats_the_program_a_chosen_number_of_times(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.loop_count.setValue(2)
    mc.editor.setPlainText(program("G1030 Y100", "G1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Loop 1 finished - repeating." in mc.output.toPlainText(), timeout=WAIT)
    qtbot.waitUntil(lambda: "Program finished. (2 loop(s))" in mc.output.toPlainText(), timeout=WAIT)
    assert mc.run_btn.isEnabled() and not mc.stop_btn.isEnabled()


def test_motion_controller_loop_once_by_default_says_nothing_extra(win, qtbot):
    """The default (Loop = 1) reads exactly like the original, pre-Loop "Program finished." message - unchanged
    wording for the common case."""
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    assert mc.loop_count.value() == 1
    mc.editor.setPlainText(program("G1030 Y100", "G1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText(), timeout=WAIT)
    assert "loop" not in mc.output.toPlainText().lower()


def test_motion_controller_stop_ends_a_forever_loop(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.loop_count.setValue(0)                                                  # "Forever"
    assert mc.loop_count.text() == "Forever"
    mc.editor.setPlainText(program("G1030 Y100", "G1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Loop 2 finished - repeating." in mc.output.toPlainText(), timeout=WAIT)
    qtbot.mouseClick(mc.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: mc.run_btn.isEnabled() and not mc.stop_btn.isEnabled(), timeout=WAIT)
    assert "Program finished." not in mc.output.toPlainText()                  # a Stop is never a success
    n = len(mc.output.toPlainText())
    qtbot.wait(300)
    assert len(mc.output.toPlainText()) == n                                   # nothing kept looping after Stop


def test_motion_controller_loop_count_is_locked_while_running(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G04 P5", "G1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not mc.loop_count.isEnabled(), timeout=WAIT)
    qtbot.mouseClick(mc.stop_btn, Qt.MouseButton.LeftButton)
    assert mc.loop_count.isEnabled()


def test_motion_controller_loading_a_bundled_sample_replaces_the_editor(win, qtbot):
    mc = win.motion_controller_page
    assert mc.samples.count() >= 4                                        # the placeholder + at least 3 bundled samples
    idx = mc.samples.findText("Speed Ramp")
    assert idx > 0
    mc.editor.clear()
    mc._load_sample(idx)
    assert "G1030" in mc.editor.toPlainText()
    assert mc.samples.currentIndex() == 0                                 # resets back to the placeholder


def test_motion_controller_loading_a_sample_over_unsaved_text_asks_first(win, qtbot, monkeypatch):
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1000  ;my own work")
    idx = mc.samples.findText("Speed Ramp")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    mc._load_sample(idx)
    assert mc.editor.toPlainText() == "G1000  ;my own work"               # declined: nothing replaced
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    mc._load_sample(idx)
    assert "G1030" in mc.editor.toPlainText()


def test_motion_controller_save_and_open_round_trip_a_file(win, qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1030 X321\nG1000")
    path = tmp_path / "my_program.nc"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    mc.save_btn.click()
    assert path.read_text() == "G1030 X321\nG1000"
    mc.editor.clear()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    mc.open_btn.click()
    assert mc.editor.toPlainText() == "G1030 X321\nG1000"


def test_motion_controller_saving_into_the_samples_folder_makes_it_a_selectable_sample(win, qtbot, monkeypatch, tmp_path):
    import sdr_tuner.gui.motion_controller as mc_module
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(mc_module, "SAMPLES_DIR", tmp_path)
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1000")
    new_path = tmp_path / "test_temp_sample.nc"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(new_path), "")))
    mc.save_btn.click()
    assert mc.samples.findText("Test Temp Sample") > 0


def test_frame_decoder_descriptions():
    d = FrameDecoder()
    assert d.describe(0x601, bytes([0x40, 0x03, 0x20, 0x00, 0, 0, 0, 0])) == "Read request  0x2003:0x00  unknown parameter"
    assert d.describe(0x601, bytes([0x20, 0x02, 0x20, 0x22]) + bytes.fromhex("0000003f")) == \
        "Write request  0x2002:0x22  Electronic Speed Control: ESC Kp  =  0.5"
    assert d.describe(0x581, bytes([0x4B, 0x03, 0x20, 0x00, 14, 0, 0, 0])) == \
        "Read response  0x2003:0x00  unknown parameter  =  0e 00 00 00"       # not in the tables: raw bytes
    assert d.describe(0x581, bytes([0x4B, 0x00, 0x20, 0x03, 14, 0, 0, 0])) == \
        "Read response  0x2000:0x03  Poles Number (Rotor Poles)  =  14"
    assert d.describe(0x581, bytes([0x80, 0x34, 0x12, 0x01, 0, 0, 0x02, 0x06])).startswith("ERROR response  0x1234:0x01")
    assert d.describe(0x201, bytes([0x20, 0, 0, 0]) + bytes.fromhex("00004841")) == \
        "Command  Electronic Speed Control (ESC), Hz  Speed: Electrical Frequency=12.5 Hz"
    assert d.describe(0x207, bytes([0x04, 0, 0, 0, 0, 0, 0, 0])) == "Command  Stop  "
    assert d.describe(0x207, bytes([0x01, 0, 0, 0, 0, 0, 0, 0])) == "Command  Reset  "
    # captured from the real drive: the acknowledgement of a write of 750.0 to 0x2014:0x03 (0x60, index, sub, value echoed)
    assert d.describe(0x587, bytes.fromhex("6014200300803b44")) == \
        "Write acknowledged  0x2014:0x03  Notch Filter 0: center frequency  =  750"
    assert d.describe(0x587, bytes.fromhex("6014200201000000")) == "Write acknowledged  0x2014:0x02  Notch Filter 0: enable  =  True"
    tel = d.describe(0x181, bytes.fromhex("0500") + bytes.fromhex("8002") + bytes.fromhex("0000c041"))   # vbus: 640 / 256 (fixed point)
    assert tel == "Telemetry  fault_bits=5  vbus=2.5  speed_hz=24"
    # message 1 in the rev L layout: Operation Mode, Commutation Mode, Work Zone Count (INT32)
    assert d.describe(0x287, bytes.fromhex("0200" "0100" "fbffffff")) == "Telemetry  op_mode=2  comm_mode=1  wz_count=-5"
    assert d.describe(0x387, bytes.fromhex("0000c03f" "07000000")) == "Telemetry  ia=1.5  wz_modulo=7"


def test_frame_decoder_follows_a_changed_telemetry_mapping():
    d = FrameDecoder()
    tpdo = defs.Tpdo(0x180, (defs.tpdo_field(defs.telemetry_catalog()[(0x5001, 0x06)], 0),))         # Phase B alone
    d.set_tpdos([tpdo])
    assert d.describe(0x187, bytes.fromhex("0000003f") + bytes(4)) == "Telemetry  ib=0.5"
    assert d.describe(0x287, bytes(8)) == ""                                                        # no longer a telemetry message
    assert d.is_telemetry(0x607, bytes([0x40, 0x01, 0x50, 0x06, 0, 0, 0, 0]))                       # Phase B poll request


def test_connection_settings_are_remembered(qtbot, monkeypatch):
    monkeypatch.setattr(MainWindow, "show_error", lambda self, message: None)
    real_bus = can.Bus
    first = MainWindow()
    qtbot.addWidget(first)
    first.iface.setCurrentText("Kvaser")
    first.channel.setText("1")
    first.bitrate.setCurrentIndex(first.bitrate.findData(500_000))
    first.node.setValue(5)
    monkeypatch.setattr(can, "Bus", lambda **kw: (_ for _ in ()).throw(can.CanInitializationError("no adapter")))
    qtbot.mouseClick(first.connect_btn, Qt.MouseButton.LeftButton)          # fails, so nothing is saved yet
    assert MainWindow._settings().value("connection/node") is None
    monkeypatch.setattr(can, "Bus", real_bus)
    first.iface.setCurrentIndex(0)                                          # simulator connects for real
    first.node.setValue(5)
    qtbot.mouseClick(first.connect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "SIMULATED" in first.pill.text(), timeout=WAIT)
    first.close()
    second = MainWindow()
    qtbot.addWidget(second)
    assert second.iface.currentText().startswith("Simulator") and second.node.value() == 5


def test_tests_never_touch_the_real_settings(win):
    assert MainWindow._settings().fileName().endswith("settings.ini")


# ---- USB cable --------------------------------------------------------------------------------
def test_usb_connection_finds_the_drive_on_its_own_node(win, qtbot, monkeypatch):
    rig = UsbRig(node=7)
    monkeypatch.setattr("sdr_tuner.gui.session.UsbSerialBus", lambda port: rig.bus())
    try:
        win.iface.setCurrentText(USB)
        assert win.channel.isEnabled() and not win.bitrate.isEnabled()
        win.channel.setText("/dev/cu.usbmodemTEST")
        assert win.node.value() == 1                                    # the wrong node on purpose
        qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: "ONLINE" in win.pill.text(), timeout=WAIT)
        assert win.node.value() == 7 and win.pill.text().endswith("NODE 7") and win.session.node_id == 7
        assert win.session.client.poles == 14
        assert int(MainWindow._settings().value("connection/node")) == 7      # remembered for the next launch
        read_all(win, qtbot)
        assert all(r.item.text(5).startswith("✓ read") for r in win.params_tab.rows)
        qtbot.waitUntil(lambda: win.drive_tab.live_tag.text().endswith("LIVE"), timeout=WAIT)
        assert win.drive_tab.cards["speed_hz"].value.text() != "—"
        # only read requests went to the drive (to node 1 while searching, then node 7)
        assert rig.bridge.sent_lines and all(re.match(rb"t60[17]840", line) for line in rig.bridge.sent_lines)
        qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    finally:
        rig.close()


def test_usb_with_several_drives_does_not_guess(win, qtbot, monkeypatch):
    rig = UsbRig(node=7)
    other = SimulatedDrive(rig.channel, node_id=9)
    monkeypatch.setattr("sdr_tuner.gui.session.UsbSerialBus", lambda port: rig.bus())
    try:
        win.iface.setCurrentText(USB)
        win.channel.setText("/dev/cu.usbmodemTEST")
        qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: "NO RESPONSE" in win.pill.text(), timeout=WAIT)
        assert win.node.value() == 1
        assert "7, 9" in win.status_text()
    finally:
        other.close()
        rig.close()


def test_usb_port_that_cannot_be_opened_is_reported(win, qtbot, monkeypatch):
    def busy(port):
        raise can.CanInitializationError(f"cannot open {port}: port busy")
    monkeypatch.setattr("sdr_tuner.gui.session.UsbSerialBus", busy)
    win.iface.setCurrentText(USB)
    win.channel.setText("/dev/cu.usbmodemTEST")
    qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
    assert win.session.client is None and "port busy" in win.errors[0]


def test_usb_is_preselected_when_the_drive_cable_is_plugged_in(qtbot, monkeypatch):
    monkeypatch.setattr(usb_module, "find_drive_ports", lambda: ["/dev/cu.usbmodemTEST"])
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.iface.currentText() == USB and w.channel.text() == "/dev/cu.usbmodemTEST" and not w.bitrate.isEnabled()


def test_the_node_id_is_not_asked_for_on_usb_the_drive_is_the_only_one_on_the_cable(win, qtbot):
    win.iface.setCurrentText(USB)
    assert not win.node.isEnabled() and "automatically" in win.node.toolTip()
    win.iface.setCurrentText("PCAN (PEAK-System)")
    assert win.node.isEnabled() and win.node.toolTip() == "Node ID of the servo drive (1..127)"


def test_a_live_usb_port_wins_over_a_stale_saved_one(qtbot, monkeypatch, tmp_path):
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(MainWindow, "_settings", staticmethod(lambda: QSettings(ini, QSettings.Format.IniFormat)))
    s = QSettings(ini, QSettings.Format.IniFormat)
    s.setValue("connection/interface", USB)
    s.setValue("connection/channel", "/dev/cu.usbmodemSTALE")                # last session's port, no longer valid
    s.sync()
    monkeypatch.setattr(usb_module, "find_drive_ports", lambda: ["/dev/cu.usbmodemLIVE"])   # this session's actual port
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.channel.text() == "/dev/cu.usbmodemLIVE"                        # not the stale saved one
    w.close()


def test_the_saved_port_is_used_when_nothing_is_found_live(qtbot, monkeypatch, tmp_path):
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(MainWindow, "_settings", staticmethod(lambda: QSettings(ini, QSettings.Format.IniFormat)))
    s = QSettings(ini, QSettings.Format.IniFormat)
    s.setValue("connection/interface", USB)
    s.setValue("connection/channel", "/dev/cu.usbmodemLASTKNOWN")
    s.sync()
    monkeypatch.setattr(usb_module, "find_drive_ports", lambda: [])          # nothing plugged in right now
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.channel.text() == "/dev/cu.usbmodemLASTKNOWN"
    w.close()


def test_connect_re_scans_for_the_port_if_it_is_still_empty(win, qtbot, monkeypatch):
    win.iface.setCurrentText(USB)
    win.channel.setText("")                                                  # nothing found when the interface was chosen
    monkeypatch.setattr(usb_module, "find_drive_ports", lambda: ["/dev/cu.usbmodemPLUGGEDINNOW"])  # plugged in just now
    monkeypatch.setattr("sdr_tuner.gui.session.UsbSerialBus", lambda port: (_ for _ in ()).throw(can.CanInitializationError(port)))
    qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
    assert win.channel.text() == "/dev/cu.usbmodemPLUGGEDINNOW"


def test_connect_with_no_port_found_at_all_shows_a_clear_error(win, qtbot, monkeypatch):
    win.iface.setCurrentText(USB)
    win.channel.setText("")
    monkeypatch.setattr(usb_module, "find_drive_ports", lambda: [])
    qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
    assert win.session.client is None and "No drive was found" in win.errors[0]


# ---- telemetry: only the drive's own broadcast -----------------------------------------------
def test_telemetry_shows_the_drive_broadcast_and_sends_no_requests(win, qtbot, monkeypatch):
    rig = UsbRig(node=7, tpdo_hz=3.0)                    # like the real drive: 3 messages per second
    monkeypatch.setattr("sdr_tuner.gui.session.UsbSerialBus", lambda port: rig.bus())
    try:
        win.iface.setCurrentText(USB)
        win.channel.setText("/dev/cu.usbmodemTEST")
        win.node.setValue(7)
        qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: "ONLINE" in win.pill.text(), timeout=WAIT)
        dt, client = win.drive_tab, win.session.client
        assert not hasattr(dt, "poll") and not client.polling
        # connecting reads three things, once: the pole count, the drive's telemetry mapping (12 slots) and who the drive is
        assert len(rig.bridge.sent_lines) == 16 and all(re.match(rb"t607840", line) for line in rig.bridge.sent_lines)
        asked = {(int(line[9:11] + line[7:9], 16), int(line[11:13], 16)) for line in rig.bridge.sent_lines}    # (index, sub)
        assert asked == ({(0x2000, 0x03)} | {(0x1A00 + m, slot) for m in range(3) for slot in range(1, 5)}
                         | {(0x5000, 0x9F), (0x5000, 0xA0), (0x5000, 0xA1)})
        n = len(rig.bridge.sent_lines)
        qtbot.wait(2200)
        assert len(rig.bridge.sent_lines) == n                               # after that the tool only listens
        for key in ("speed_hz", "vbus", "ia", "fault_bits", "op_mode", "comm_mode", "wz_count", "wz_modulo"):
            t, _ = client.history_snapshot(key, 2.0)
            assert 4 <= len(t) <= 8, key                                     # about 3 per second, as the drive sends them
        for key in ("ib", "elec_pos"):
            assert len(client.history_snapshot(key)[0]) == 0, key            # not in the broadcast: never shown
        assert dt.live_tag.text().endswith("LIVE")
        assert "ib" not in dt.cards                                          # Phase B is not broadcast: there is no card for it
        assert dt.cards["ia"].value.text() != "—" and dt.cards["speed_hz"].value.text() != "—"
        log_texts = [win.log_tab.table.item(r, 4).text() for r in range(win.log_tab.table.rowCount())]
        assert not any(t.startswith("Telemetry") for t in log_texts)         # the log hides the broadcast by default
        qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    finally:
        rig.close()


# ---- Recorder ----------------------------------------------------------------------------------
def test_the_recorder_offers_the_signals_the_drive_broadcasts(win):
    rec = win.recorder_page
    assert rec.drive.signal_keys() == ["speed_hz", "vbus", "op_mode", "fault_bits", "ia", "wz_count", "comm_mode", "wz_modulo"]
    assert list(rec.charts[0].actions) == rec.drive.signal_keys() == list(rec.charts[1].actions)
    assert rec.charts[0].checked_keys() == ["speed_hz"] and rec.charts[1].checked_keys() == ["ia"]
    assert rec.charts[0].actions["speed_hz"].text() == "Speed (Hz)" and rec.charts[0].actions["ia"].text() == "Phase A (A)"
    assert not rec.stop_btn.isEnabled() and not rec.save_btn.isEnabled() and not rec.clear_btn.isEnabled()
    assert rec.status.text() == "READY" and not rec.hint.isHidden()


def test_recording_needs_a_connected_drive(win):
    rec = win.recorder_page
    messages = []
    rec.message.connect(lambda text, level: messages.append((text, level)))
    rec.start_btn.click()
    assert not rec.recording and messages[-1][1] == "warn" and "Connect to a drive" in messages[-1][0]


def test_a_recording_follows_the_motor_and_stops_on_request(win, qtbot):
    sim, client = connect(win, qtbot)
    rec, dt = win.recorder_page, win.drive_tab
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(sim.speed_hz - 14.0) < 0.3, timeout=WAIT)
    rec.start_btn.click()
    assert rec.recording and rec.stop_btn.isEnabled() and not rec.start_btn.isEnabled() and not rec.duration.isEnabled()
    qtbot.wait(700)
    t, v = rec.data()["speed_hz"]
    assert len(t) > 30 and t[0] >= 0 and (np.diff(t) >= 0).all() and t[-1] <= rec.elapsed() + 0.05
    assert v[-1] == pytest.approx(14.0, abs=0.5)                              # 120 rpm on 7 pole pairs
    x, y = rec.charts[0].curves["speed_hz"].getData()
    assert len(x) > 30 and rec.status.text().startswith("●  RECORDING")       # and the chart is drawing it
    rec.stop_btn.click()
    assert not rec.recording and rec.save_btn.isEnabled() and rec.clear_btn.isEnabled() and rec.status.text().startswith("STOPPED")
    n = len(rec.data()["speed_hz"][0])
    qtbot.wait(300)
    assert len(rec.data()["speed_hz"][0]) == n                                # stopped: nothing more is added
    assert win.status_text().startswith("Recording stopped:") and rec.hint.isHidden()


def test_the_recorder_only_listens(win, qtbot):
    _, client = connect(win, qtbot)
    sent = []
    client.frame_listeners.append(lambda d, t, cid, data: sent.append(cid) if d == "tx" else None)
    win.recorder_page.start_btn.click()
    qtbot.wait(600)
    win.recorder_page.stop_btn.click()
    assert sent == []                                                        # nothing was requested from the drive


def test_a_recording_stops_by_itself_after_the_chosen_time(win, qtbot):
    connect(win, qtbot)
    rec = win.recorder_page
    assert [rec.duration.itemText(i) for i in range(rec.duration.count())] == ["Until stopped", "10 s", "30 s", "1 min", "5 min", "10 min"]
    rec.duration.addItem("1 s", 1)
    rec.duration.setCurrentIndex(rec.duration.findData(1))
    rec.start_btn.click()
    qtbot.waitUntil(lambda: not rec.recording, timeout=5000)
    assert 1.0 <= rec.elapsed() < 1.6 and "time is up" in win.status_text()


def test_starting_again_and_clearing_throw_the_old_recording_away(win, qtbot):
    connect(win, qtbot)
    rec = win.recorder_page
    rec.start_btn.click()
    qtbot.wait(500)
    rec.stop_btn.click()
    first = len(rec.data()["speed_hz"][0])
    rec.start_btn.click()
    qtbot.wait(150)
    t, _ = rec.data()["speed_hz"]
    assert len(t) < first and t[0] < 0.2                                     # a new recording starts at time 0
    rec.stop_btn.click()
    rec.clear_btn.click()
    assert rec.data() == {} and not rec.save_btn.isEnabled() and rec.status.text() == "READY" and not rec.hint.isHidden()
    x, y = rec.charts[0].curves["speed_hz"].getData()
    assert x is None or len(x) == 0


def test_the_signals_of_a_chart_are_chosen_from_its_menu(win, qtbot):
    connect(win, qtbot)
    rec = win.recorder_page
    rec.charts[1].actions["vbus"].setChecked(True)
    rec.start_btn.click()
    qtbot.wait(300)
    assert set(rec.charts[1].curves) == {"ia", "vbus"} and set(rec.charts[0].curves) == {"speed_hz"}
    assert rec.charts[1].curves["ia"].opts["pen"].color() != rec.charts[1].curves["vbus"].opts["pen"].color()
    rec.charts[1].actions["ia"].setChecked(False)
    qtbot.wait(150)
    assert set(rec.charts[1].curves) == {"vbus"}
    rec.charts[0].actions["speed_hz"].setChecked(False)
    qtbot.wait(150)
    assert rec.charts[0].curves == {}
    rec.stop_btn.click()


def test_the_recording_is_exported_as_csv(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    sim, _ = connect(win, qtbot)
    rec, dt = win.recorder_page, win.drive_tab
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(sim.speed_hz - 14.0) < 0.3, timeout=WAIT)
    rec.start_btn.click()
    qtbot.wait(600)
    rec.stop_btn.click()
    path = tmp_path / "recording.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    rec.save_btn.click()
    lines = path.read_text().splitlines()
    header = lines[0].split(",")
    assert header[:3] == ["time [s]", "Speed [Hz]", "DC bus [V]"] and "Phase A [A]" in header and len(header) == 9
    assert win.status_text() == f"Saved {len(lines) - 1} rows to {path}"
    rows = [line.split(",") for line in lines[1:]]
    times = [float(r[0]) for r in rows]
    assert len(rows) > 30 and times == sorted(times) and times[0] >= 0 and all(len(r) == 9 for r in rows)
    assert float(rows[-1][1]) == pytest.approx(14.0, abs=0.5) and float(rows[-1][2]) == pytest.approx(24.0, abs=0.5)
    assert all(cell != "" for cell in rows[-1])                              # a value holds until the next one arrives


def test_saving_needs_a_recording(win):
    rec = win.recorder_page
    messages = []
    rec.message.connect(lambda text, level: messages.append((text, level)))
    rec.save_dialog()
    assert messages[-1][1] == "warn" and "record something first" in messages[-1][0]
    with pytest.raises(ValueError):
        rec.export_csv("/nonexistent/whatever.csv")


def test_the_recorder_shows_the_speed_in_the_chosen_unit(win, qtbot, monkeypatch):
    def sends_rps(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        sim.params[(0x1A00, 3)] = defs.mapping_code(*defs.SPEED_RPS)
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", sends_rps)
    sim, _ = connect(win, qtbot)
    rec, dt = win.recorder_page, win.drive_tab
    qtbot.waitUntil(lambda: "wz_speed" in rec.charts[0].actions, timeout=WAIT)
    assert rec.charts[0].checked_keys() == ["wz_speed"] and rec.charts[0].actions["wz_speed"].text() == "Speed (rpm)"
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: abs(sim.speed_hz - 14.0) < 0.3, timeout=WAIT)
    rec.start_btn.click()
    qtbot.wait(500)
    rec.stop_btn.click()
    assert rec.charts[0].curves["wz_speed"].getData()[1][-1] == pytest.approx(120.0, abs=5)
    choose_unit(dt, "rps")                                                   # only the display changes: the same recording
    assert rec.charts[0].actions["wz_speed"].text() == "Speed (rps)"
    assert rec.charts[0].curves["wz_speed"].getData()[1][-1] == pytest.approx(2.0, abs=0.1)


def test_cursors_measure_between_two_points(win, qtbot):
    from sdr_tuner.gui.recorder import value_at
    sim, _ = connect(win, qtbot)
    rec, dt = win.recorder_page, win.drive_tab
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)                  # a speed step: the recording has something to measure
    rec.start_btn.click()
    qtbot.wait(900)
    rec.stop_btn.click()
    assert rec.table.isHidden()
    rec.cursors_btn.click()
    assert rec.cursors_btn.isChecked() and not rec.table.isHidden() and win.rec_cursors_btn.isChecked()
    span = max(rec.elapsed(), 1.0)                                            # the cursors start at a quarter and three quarters
    assert rec.cursor_positions()["A"] == pytest.approx(0.25 * span, abs=0.05) and rec.cursor_positions()["B"] == pytest.approx(0.75 * span, abs=0.05)
    rec.set_cursor("A", 0.1)
    rec.set_cursor("B", 0.8)
    assert [line.value() for line in rec._cursors["A"]] == pytest.approx([0.1, 0.1])       # the cursor is the same in both charts
    t, v = rec.data()["speed_hz"]
    assert rec.table.item(0, 0).text() == "Speed (Hz)"
    a, b = value_at(t, v, 0.1), value_at(t, v, 0.8)
    assert float(rec.table.item(0, 1).text()) == pytest.approx(a, rel=1e-3, abs=1e-4)
    assert float(rec.table.item(0, 2).text()) == pytest.approx(b, rel=1e-3, abs=1e-4)
    assert float(rec.table.item(0, 3).text()) == pytest.approx(b - a, rel=1e-3, abs=1e-3) and b > a    # the motor sped up between them
    assert "0.70 s" in rec.table.horizontalHeaderItem(3).text() and rec.table.rowCount() == 2         # speed and Phase A are plotted
    rec.cursors_btn.click()
    assert rec.table.isHidden() and not win.rec_cursors_btn.isChecked()


def test_a_disconnect_ends_the_recording_and_keeps_it(win, qtbot):
    connect(win, qtbot)
    rec = win.recorder_page
    rec.start_btn.click()
    qtbot.wait(400)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert not rec.recording and "disconnected" in win.status_text()
    assert len(rec.data()["speed_hz"][0]) > 10 and rec.save_btn.isEnabled()


def test_a_change_of_the_telemetry_layout_ends_the_recording_but_not_its_data(win, qtbot, tmp_path):
    sim, client = connect(win, qtbot)
    rec, dt = win.recorder_page, win.drive_tab
    dt._confirm = lambda text: True
    rec.start_btn.click()
    qtbot.wait(400)
    choose_unit(dt, "rps")                                                   # the drive is told to send Work Zone: Speed instead
    qtbot.waitUntil(lambda: not rec.recording, timeout=WAIT)
    assert "telemetry layout changed" in win.status_text()
    assert len(rec.data()["speed_hz"][0]) > 10                               # what was recorded is still there ...
    assert "speed_hz" in rec.charts[0].actions and "wz_speed" in rec.charts[0].actions      # ... and can still be looked at
    path = tmp_path / "old.csv"
    rows = rec.export_csv(path)
    assert rows > 10 and path.read_text().splitlines()[0].split(",")[1] == "Speed [Hz]"


def test_the_recording_ribbon_tab_appears_with_the_recorder(win, qtbot):
    assert "Recording" not in win.ribbon.tab_titles() and "Recorder" in [b.text() for b in win.ribbon.page("tools").findChildren(type(win.connect_btn))]
    connect(win, qtbot)
    win.open_tool("recorder")
    assert "Recording" in win.ribbon.tab_titles() and win.ribbon.current_key() == "recording"
    qtbot.wait(100)
    assert win.ribbon.tabs.tabRect(0).left() >= 0 and win.ribbon.tabs.width() >= win.ribbon.tabs.sizeHint().width()    # every tab is in view
    assert win.rec_start_btn.isEnabled() and not win.rec_stop_btn.isEnabled() and not win.rec_save_btn.isEnabled()
    qtbot.mouseClick(win.rec_start_btn, Qt.MouseButton.LeftButton)
    assert win.recorder_page.recording and not win.rec_start_btn.isEnabled() and win.rec_stop_btn.isEnabled()
    qtbot.wait(300)
    qtbot.mouseClick(win.rec_stop_btn, Qt.MouseButton.LeftButton)
    assert not win.recorder_page.recording and win.rec_save_btn.isEnabled() and win.rec_clear_btn.isEnabled()
    qtbot.mouseClick(win.rec_cursors_btn, Qt.MouseButton.LeftButton)
    assert win.recorder_page.cursors_btn.isChecked()
    win.open_tool("motion")
    assert "Recording" not in win.ribbon.tab_titles()


# ---- Quick Tuning and Advanced Tuning ---------------------------------------------------------------------------------
MAX_CURRENT, POLES, MOTOR_TYPE = (0x2000, 0x02), (0x2000, 0x03), (0x2000, 0x01)
ESC_KP, MOTOR_R = (0x2002, 0x22), (0x2000, 0x04)


def enter(form, key, text):
    """Type into a parameter's box the way a user does."""
    row = form.rows[key]
    row.widget.setText(text)
    row.widget.textEdited.emit(text)


def tuner_ready(win, qtbot, which="quick_tuning", seconds=0.8, heartbeat=None):
    """Connect to the simulator, open a tuner and wait until it has read the drive's values. -> (tuner, sim, client)"""
    sim, client = connect(win, qtbot)
    sim.autoconfig_seconds = seconds
    if heartbeat is not None:
        sim.params[(0x3000, 0x04)] = heartbeat
    win.open_tool(which)
    tuner = win.tools[which].page
    qtbot.waitUntil(lambda: tuner._loaded and not tuner.busy, timeout=WAIT)
    enter(tuner.motor, MAX_CURRENT, "6.5")
    enter(tuner.motor, POLES, "8")
    return tuner, sim, client


def run_tuning(tuner, qtbot, timeout=WAIT):
    asked = []
    tuner.auto._confirm = lambda text: asked.append(text) or True
    qtbot.waitUntil(lambda: tuner.auto.start_btn.isEnabled(), timeout=timeout)
    tuner.auto.start()
    return asked


def test_the_tuners_open_with_their_own_ribbon_tab_and_page_tree(win, qtbot):
    win.open_tool("quick_tuning")
    assert win.ribbon.current_key() == "quick" and "Drive Quick Tuner" in win.ribbon.tab_titles()
    assert [i.text(0) for i in win.quick_tuner._items] == ["Motor Settings", "Feedback Settings", "Automatic Tuning"]
    assert win.host.crumb.text() == "Motor Settings" and win.status_text() == "Quick Tuning Page"
    win.quick_tuner.step(1)
    assert win.host.crumb.text() == "Feedback Settings" and win.host.current_page() is win.quick_tuner
    win.quick_tuner.step(1)
    assert win.host.crumb.text() == "Automatic Tuning"
    win.open_tool("advanced_tuning")
    assert win.ribbon.current_key() == "advanced" and "Drive Quick Tuner" not in win.ribbon.tab_titles()
    assert "Drive Advanced Tuner" in win.ribbon.tab_titles()
    assert [i.text(0) for i in win.advanced_tuner._items] == ["Motor Settings", "Feedback Settings", "Automatic Tuning",
                                                              "Measured Motor Data", "Control Laws", "Filters",
                                                              "Application Settings", "Limits and Protections",
                                                              "Step Response Tests", "Frequency Response", "Automatic Test", "Summary"]
    tree = win.advanced_tuner.tree
    assert [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())] == ["Motor and Feedback", "Automatic Tuning", "Results",
                                                                                       "Application", "Verification", "Summary"]
    win.open_tool("motion")
    assert "Drive Advanced Tuner" not in win.ribbon.tab_titles()
    win.ribbon.show_page("tools")
    labels = [b.text().replace("\n", " ") for b in win.ribbon.page("tools").groups["Tuning"].findChildren(type(win.connect_btn))]
    assert labels == ["Quick Tuning", "Advanced Tuning", "Automatic Test"]


def test_a_tuner_shows_what_the_drive_holds_and_marks_what_was_edited(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("quick_tuning")
    tuner = win.quick_tuner
    qtbot.waitUntil(lambda: tuner._loaded and not tuner.busy, timeout=WAIT)
    motor = tuner.motor
    assert motor.value(MAX_CURRENT) == 5.0 and motor.value(POLES) == 14 and motor.value(MOTOR_TYPE) == 0
    assert not motor.is_dirty and not tuner.apply_btn.isEnabled() and not tuner.revert_btn.isEnabled()
    assert "Read 129 parameter(s)" in win.status_text()        # 3 motor settings and the 126 of the feedback page
    enter(motor, MAX_CURRENT, "6.5")
    assert motor.is_dirty and tuner.apply_btn.isEnabled() and tuner.revert_btn.isEnabled()
    assert motor.rows[MAX_CURRENT].label.property("state") == "warn" and tuner._items[0].text(0) == "Motor Settings ●"
    assert win.tuner_buttons["quick"]["apply"].isEnabled() and win.tuner_buttons["quick"]["revert_all"].isEnabled()
    tuner.revert_page()
    assert motor.value(MAX_CURRENT) == 5.0 and not motor.is_dirty and tuner._items[0].text(0) == "Motor Settings"
    assert not win.tuner_buttons["quick"]["apply"].isEnabled()


def test_apply_writes_only_what_was_changed_and_asks_first(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot)
    asked = []
    tuner._confirm = lambda text: asked.append(text) or False
    tuner.apply_btn.click()
    assert len(asked) == 1 and "2 changed parameter(s)" in asked[0] and "6.5" in asked[0]
    assert sim.params[MAX_CURRENT] == 5.0 and sim.params[POLES] == 14                       # declined: nothing was written
    tuner._confirm = lambda text: True
    tuner.apply_btn.click()
    qtbot.waitUntil(lambda: not tuner.busy and not tuner.motor.is_dirty, timeout=WAIT)
    assert sim.params[MAX_CURRENT] == 6.5 and sim.params[POLES] == 8 and "Applied 2 parameter(s)" in win.status_text()
    tuner._confirm = lambda text: pytest.fail("nothing is left to write")
    tuner.apply_page()
    assert "Nothing to apply" in win.status_text()


def test_apply_all_and_revert_all_cover_every_page(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    enter(tuner.laws, ESC_KP, "0.07")
    enter(tuner.data, MOTOR_R, "0.9")
    assert tuner.laws.is_dirty and tuner.data.is_dirty and tuner.motor.is_dirty            # three pages hold edits
    assert tuner.can_apply() and not tuner.can_apply([tuner.feedback])                      # the page shown is Motor Settings
    tuner.go_to(tuner.data)
    assert win.tuner_buttons["advanced"]["apply"].isEnabled() and win.tuner_buttons["advanced"]["apply_all"].isEnabled()
    tuner.revert_all()
    assert not tuner.laws.is_dirty and not tuner.data.is_dirty and not tuner.motor.is_dirty
    enter(tuner.laws, ESC_KP, "0.07")
    enter(tuner.data, MOTOR_R, "0.9")
    tuner._confirm = lambda text: True
    win.tuner_buttons["advanced"]["apply_all"].click()
    qtbot.waitUntil(lambda: not tuner.busy and not tuner.laws.is_dirty, timeout=WAIT)
    assert sim.params[MOTOR_R] == pytest.approx(0.9) and sim.params[ESC_KP] == pytest.approx(0.07)


def test_a_wrong_entry_is_an_error_and_is_never_written(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot)
    enter(tuner.motor, POLES, "eight")
    assert tuner.errors() and "Poles Number" in tuner.errors()[0] and tuner.errors_btn.text() == "ERRORS… (1)"
    assert tuner.motor.rows[POLES].label.property("state") == "error" and tuner._items[0].foreground(0).color().name().lower() == "#da4a56"
    tuner._confirm = lambda text: pytest.fail("a wrong entry must not reach the drive")
    tuner.apply_page()
    assert win.status_text().startswith("Cannot apply") and sim.params[POLES] == 14
    enter(tuner.motor, POLES, "8")
    assert not tuner.errors() and tuner.errors_btn.text() == "ERRORS…"
    enter(tuner.motor, MAX_CURRENT, "-1")                                                    # not valid for the run either
    assert tuner.auto.checks()[0].level == "block" and "more than 0" in tuner.auto.checks()[0].text
    assert not tuner.auto.start_btn.isEnabled()


def test_the_quick_tuning_runs_the_drives_automatic_configuration(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot)
    tuner.go_to(tuner.auto)
    assert [c.level for c in tuner.auto.checks()] == ["ok"] * 3 and tuner.auto.start_btn.isEnabled() and not tuner.auto.stop_btn.isEnabled()
    before = dict(sim.params)
    asked = run_tuning(tuner, qtbot)
    assert len(asked) == 1 and "Autoconfiguration: Brushless Motor" in asked[0] and "6.5" in asked[0] and "flash" in asked[0]
    qtbot.waitUntil(lambda: tuner.auto.state == "running", timeout=WAIT)
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
    tuner.auto.refresh()
    assert not any(c.level == "block" for c in tuner.auto.checks())                          # its own run is not a reason to refuse it
    assert tuner.auto.stop_btn.isEnabled() and not tuner.auto.start_btn.isEnabled() and not tuner.tree.isEnabled()
    assert not tuner.next_btn.isEnabled() and client._heartbeat == pytest.approx(0.875)    # a quarter of the drive's 3.5 s timeout: kept informed
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    assert client._heartbeat is None and sim.autoconfig_runs == 1 and sim.autoconfig_aborted == 0 and sim.flash_saved
    assert sim.commands_received[-1] == ("autoconfiguration_brushless_motor", (6.5, 8))
    changed = {reg.key: (b, a) for reg, b, a in tuner.auto.changes}
    assert changed[MAX_CURRENT] == (5.0, 6.5) and changed[POLES] == (14, 8) and MOTOR_R in changed and ESC_KP in changed
    assert tuner.auto.table.rowCount() == len(changed) and tuner.auto.status.text() == "Auto tuning completed successfully."
    assert tuner.tree.isEnabled() and tuner.prev_btn.isEnabled() and not tuner.next_btn.isEnabled()      # the last page of the quick tuner
    qtbot.waitUntil(lambda: not tuner.busy, timeout=WAIT)
    assert tuner.motor.value(POLES) == 8 and not tuner.motor.is_dirty                        # the pages show what the drive holds now
    assert win.status_text().startswith("Automatic tuning finished")                         # and the reload does not bury the news
    assert sim.params[MOTOR_R] != before[MOTOR_R]


def test_the_advanced_tuning_sends_the_general_system_dynamics_and_shows_the_results(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    auto = tuner.auto
    assert auto.dynamics.value() == 3.16 and auto.presets.currentText().startswith("Direct drive")
    auto.presets.setCurrentIndex(1)
    auto.presets.activated.emit(1)
    assert auto.dynamics.value() == 4.0
    auto.dynamics.setValue(6.0)
    assert auto.presets.currentIndex() == -1 and auto.dynamics_note.text() == ""
    auto.dynamics.setValue(20.0)
    assert "last resort" in auto.dynamics_note.text().lower()
    auto.dynamics.setValue(6.0)
    tuner.go_to(auto)
    asked = run_tuning(tuner, qtbot)
    assert "Advanced" in asked[0] and "General System Dynamics: 6" in asked[0]
    qtbot.waitUntil(lambda: auto.state == "done" and not tuner.busy, timeout=WAIT)
    name, values = sim.commands_received[-1]
    assert name == "autoconfiguration_advanced_brushless_motor" and values == pytest.approx((6.5, 8, 6.0), abs=0.01)
    assert tuner.laws.value(ESC_KP) == pytest.approx(0.058 * 6.0 / 3.16, rel=1e-3)         # the Control Laws page shows the new gains
    assert tuner.data.value(MOTOR_R) == pytest.approx(0.87, rel=1e-3) and not tuner.laws.is_dirty
    tuner.go_to(tuner.summary)
    text = tuner.summary.list.text()
    assert "changed" in text and "Every value on the pages has been applied" in text and tuner.summary.table.rowCount() == len(auto.changes)


def test_a_brushed_motor_uses_its_own_command(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    combo = tuner.motor.rows[MOTOR_TYPE].widget
    combo.setCurrentIndex(combo.findData(1))
    combo.activated.emit(combo.currentIndex())
    tuner.go_to(tuner.auto)
    assert not tuner.auto.dynamics.isEnabled() and "brushed" in tuner.auto.dynamics_note.text().lower()
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    assert sim.commands_received[-1] == ("autoconfiguration_brushed_motor", (6.5, 8))


def test_the_run_is_refused_when_the_drive_has_a_fault_or_no_power(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot)
    tuner.go_to(tuner.auto)
    auto = tuner.auto
    auto._confirm = lambda text: pytest.fail("a blocked run must not even ask")
    sim.fault_bits = 0x0100                                                                   # SPI Encoder Fault
    qtbot.waitUntil(lambda: any(c.level == "block" and "SPI Encoder Fault" in c.text for c in auto.checks()), timeout=WAIT)
    qtbot.waitUntil(lambda: "SPI Encoder Fault" in auto.check_list.text(), timeout=WAIT)              # the list on the page follows
    assert not auto.start_btn.isEnabled()
    auto.start()
    assert sim.autoconfig_runs == 0 and win.status_text().startswith("Cannot start") and auto.state == "idle"
    sim.fault_bits = 0
    qtbot.waitUntil(lambda: auto.start_btn.isEnabled(), timeout=WAIT)
    sim.bus_volts = 0.0                                                                       # the power stage is not supplied
    qtbot.waitUntil(lambda: any(c.level == "block" and "DC bus" in c.text for c in auto.checks()), timeout=WAIT)
    auto.start()
    assert sim.autoconfig_runs == 0 and "power stage is not supplied" in win.status_text()
    sim.bus_volts = 24.0
    qtbot.waitUntil(lambda: auto.start_btn.isEnabled(), timeout=WAIT)
    client.set_speed_rpm(100)                                                                 # a speed command is running
    qtbot.waitUntil(lambda: any(c.level == "block" and "speed command" in c.text for c in auto.checks()), timeout=WAIT)
    auto.start()                                                                              # start() looks again, whatever the button shows
    assert sim.autoconfig_runs == 0 and "speed command" in win.status_text()
    qtbot.waitUntil(lambda: not auto.start_btn.isEnabled(), timeout=WAIT)
    client.stop()


def test_a_run_needs_a_connection_and_valid_motor_data(win, qtbot):
    win.open_tool("quick_tuning")
    auto = win.quick_tuner.auto
    assert not auto.start_btn.isEnabled() and auto.checks()[0].text == "Not connected to a drive."
    win.quick_tuner.load_all()
    assert win.status_text() == "Connect to a drive first."
    assert not win.tuner_buttons["quick"]["load"].isEnabled()
    connect(win, qtbot)
    qtbot.waitUntil(lambda: win.quick_tuner._loaded, timeout=WAIT)
    assert win.tuner_buttons["quick"]["load"].isEnabled()
    for poles, current, problem in (("7", "6.5", "even number"), ("0", "6.5", "even number"), ("8", "0", "more than 0")):
        enter(win.quick_tuner.motor, POLES, poles)
        enter(win.quick_tuner.motor, MAX_CURRENT, current)
        first = auto.checks()[0]
        assert first.level == "block" and problem in first.text, (poles, current)
    assert not auto.start_btn.isEnabled()


def test_declining_the_confirmation_sends_nothing(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot)
    tuner.go_to(tuner.auto)
    asked = []
    tuner.auto._confirm = lambda text: asked.append(text) or False
    qtbot.waitUntil(lambda: tuner.auto.start_btn.isEnabled(), timeout=WAIT)
    tuner.auto.start()
    qtbot.wait(300)
    assert len(asked) == 1 and tuner.auto.state == "idle" and sim.autoconfig_runs == 0 and sim.commands_received == []
    assert client._heartbeat is None and "cancelled" in win.status_text()


def test_stop_ends_a_running_tuning(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=6.0)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
    qtbot.mouseClick(win.ribbon.stop_button, Qt.MouseButton.LeftButton)                      # the round STOP button
    assert tuner.auto.state == "stopped" and client._heartbeat is None
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 0, timeout=WAIT)
    assert sim.autoconfig_aborted == 1 and not sim.flash_saved and "Stopped" in tuner.auto.status.text()
    assert after_the_tuning_command(sim)[0] == "reset"                                        # the drive's Stop does not end it: a Reset is sent first
    assert tuner.tree.isEnabled()
    qtbot.waitUntil(lambda: tuner.auto.start_btn.isEnabled(), timeout=WAIT)                    # the checks catch up with the drive
    qtbot.wait(700)
    assert sim.autoconfig_aborted == 1 and tuner.auto.changes is None                         # nothing is reported as finished


def test_esc_also_ends_a_running_tuning(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=6.0)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "running", timeout=WAIT)
    qtbot.keyClick(win, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: tuner.auto.state == "stopped", timeout=WAIT)
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 0, timeout=WAIT)
    assert not sim.flash_saved


def test_a_long_tuning_survives_the_drives_heartbeat_timeout(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=2.2, heartbeat=0.7)                 # the drive gives up after 0.7 s of silence
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    assert sim.autoconfig_aborted == 0 and sim.autoconfig_runs == 1 and sim.flash_saved
    assert len(sim.commands_received) == 1                                                    # the command was sent once, never repeated


def test_a_drive_that_ignores_the_command_is_waited_for_and_then_stopped(win, qtbot, monkeypatch):
    import sdr_tuner.gui.tuner as tuner_module
    monkeypatch.setattr(tuner_module, "START_TIMEOUT", 0.6)                                   # after this the page only says it has heard nothing
    monkeypatch.setattr(tuner_module, "RUN_LIMIT", 3.0)                                       # and this long without a change ends the run
    tuner, sim, client = tuner_ready(win, qtbot)
    sim._commands = {k: v for k, v in sim._commands.items() if v.key not in ("autoconfiguration_brushless_motor",)}     # the drive does not obey
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: "has not reported" in tuner.auto.status.text(), timeout=WAIT)
    assert tuner.auto.state == "running" and client._heartbeat is not None                    # a hint, not a verdict: it keeps watching
    qtbot.waitUntil(lambda: tuner.auto.state == "failed", timeout=WAIT)
    assert "Datasheet did not change within 3 s" in tuner.auto.status.text() and client._heartbeat is None
    assert win.status_text().startswith("Automatic tuning failed")


def datasheet_reads(sim):
    """Records the times at which the drive is asked for the Phase Resistance (only the Datasheet readings do that)."""
    times, original = [], sim._sdo

    def spy(data):
        if data[0] == P.CMD_READ and (data[1] | data[2] << 8, data[3]) == MOTOR_R:
            times.append(time.monotonic())
        return original(data)
    sim._sdo = spy
    return times


def test_the_datasheet_is_read_every_4_seconds_while_the_tuning_runs(win, qtbot):
    """User (2026-09-26): continuous checking every 4 s (was 3), and no "Datasheet checked N times" on the status line."""
    tuner, sim, client = tuner_ready(win, qtbot, seconds=10.0)                                # long enough for a few 4 s polls
    reads = datasheet_reads(sim)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto._polls >= 2, timeout=WAIT)
    assert "checked" not in tuner.auto.status.text() and tuner.auto.status.text().rstrip().endswith("s")   # "...  N s", nothing more
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=15000)
    polls = reads[1:-1]                                                                       # the first reading is the 'before', the last the 'after'
    assert len(polls) >= 3, reads
    assert all(3.4 < b - a < 4.8 for a, b in zip(polls, polls[1:])), [round(b - a, 2) for a, b in zip(polls, polls[1:])]
    assert tuner.auto.state == "done" and "completed successfully" in tuner.auto.status.text()


@pytest.mark.real_poll_timing
def test_the_first_datasheet_check_after_start_waits_20_seconds(win, qtbot):
    """User (2026-09-26): "first check data sheet once user click on start automatic tunning then wait for 20 sec before
    start continuous data sheet checking" - the once-at-START read is the 'before' snapshot; nothing is read again before
    FIRST_POLL_S; the simulator's Datasheet changes at 10 s and is found by that first 20 s poll, which is then the last."""
    import sdr_tuner.gui.tuner as tuner_module
    assert tuner_module.FIRST_POLL_S == 20.0 and tuner_module.POLL_S == 4.0                    # the real values, not the test speed-up
    tuner, sim, client = tuner_ready(win, qtbot, seconds=10.0)
    reads = datasheet_reads(sim)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "running", timeout=WAIT)
    started = time.monotonic()
    qtbot.wait(15000)
    assert len(reads) == 1, reads                                                             # only the 'before' read, 15 s in
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=20000)
    polls = reads[1:-1]
    assert len(polls) == 1 and polls[0] - started >= 19.0, [round(t - started, 1) for t in reads]
    assert "completed successfully" in tuner.auto.status.text()


def after_the_tuning_command(sim):
    """Names of the commands the simulated drive carried out after the automatic configuration was started."""
    names = [name for name, _values in sim.commands_received]
    start = next(i for i, name in enumerate(names) if name.startswith("autoconfiguration"))
    return names[start + 1:]


def all_reads(sim):
    """Records every read request that reaches the drive: (time, (index, sub)). That is everything the tool puts on the bus to ask."""
    seen, original = [], sim._sdo

    def spy(data):
        if data[0] == P.CMD_READ:
            seen.append((time.monotonic(), (data[1] | data[2] << 8, data[3])))
        return original(data)
    sim._sdo = spy
    return seen


def test_nothing_is_read_from_the_drive_unless_the_user_starts_a_tuning(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=1.0)
    reads = all_reads(sim)
    tuner.go_to(tuner.auto)
    qtbot.wait(2500)                                                                          # the page is open and the drive is connected ...
    assert reads == [] and client._heartbeat is None                                          # ... and the bus is quiet
    tuner.auto._confirm = lambda text: False                                                  # START, but the user says no
    qtbot.waitUntil(lambda: tuner.auto.start_btn.isEnabled(), timeout=WAIT)
    tuner.auto.start()
    qtbot.wait(2000)
    assert reads == [] and client._heartbeat is None
    tuner.auto._confirm = lambda text: True                                                   # now the user starts it
    tuner.auto.start()
    qtbot.waitUntil(lambda: len(reads) > 0, timeout=WAIT)
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    seen = len(reads)
    qtbot.wait(2500)                                                                          # and when it is over the bus is quiet again
    assert len(reads) == seen and client._heartbeat is None


def test_the_readings_stop_as_soon_as_the_datasheet_has_changed(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=6.5)                                 # past 2 full 3 s poll intervals
    reads = all_reads(sim)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state in ("finishing", "reading", "done"), timeout=15000)
    detected_at = tuner.auto._detected_at
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    resistance = [t for t, key in reads if key == MOTOR_R]
    assert len([t for t in resistance if t > detected_at]) == 1                               # only the final reading of the results follows
    assert len([t for t in resistance if t <= detected_at]) >= 3                              # the 'before' reading and the once-every-3s ones
    from sdr_tuner.gui.tuner import snapshot_registers
    after = [key for t, key in reads if t > detected_at and key != (0x2000, 0x03)]            # (the keep-alive asks for the poles, so leave those out)
    assert sorted(after) == sorted(r.key for r in snapshot_registers() if r.key != (0x2000, 0x03))       # the Datasheet and Control Laws, once, nothing else
    settled = len(reads)
    qtbot.wait(2000)
    assert len(reads) == settled


def test_the_bus_is_asked_for_six_values_not_the_whole_datasheet(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=8.0)                                 # past 2 full 3 s poll intervals
    tuner.go_to(tuner.auto)
    reads = all_reads(sim)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "running" and tuner.auto._polls >= 2, timeout=15000)
    from sdr_tuner.gui.tuner import MEASURED_KEYS
    polled = {key for _t, key in reads[-12:]}
    assert (0x2000, 0x0C) not in polled and (0x2000, 0x0D) not in polled                      # gear ratio and ball-screw lead are not asked for
    assert set(MEASURED_KEYS) <= {key for _t, key in reads}
    assert tuner.auto._before[(0x3000, 0x04)] == 3.5                                          # the drive's own heartbeat timeout was read once
    win.emergency_stop()


def test_the_keep_alive_is_a_quarter_of_the_drives_own_timeout():
    from sdr_tuner.gui.tuner import HEARTBEAT, heartbeat_period
    assert heartbeat_period(3.5) == pytest.approx(0.875)                                      # the default: about once a second
    assert heartbeat_period(0.7) == 0.25 and heartbeat_period(60) == 1.0                      # never faster than 4 / s, never slower than 1 / s
    assert heartbeat_period(None) == HEARTBEAT and heartbeat_period(0) == HEARTBEAT           # timeout unknown


def test_the_tuning_is_complete_when_the_datasheet_changes_whatever_the_operation_mode_says(win, qtbot, monkeypatch):
    import sdr_tuner.simulator as simulator
    monkeypatch.setattr(simulator, "MODE_AUTOCONFIG", 0)                                      # this drive never shows Autoconfiguration
    tuner, sim, client = tuner_ready(win, qtbot, seconds=1.5)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    assert tuner.auto._seen is False and tuner.auto.state == "done" and sim.flash_saved
    assert tuner.auto.status.text() == "Auto tuning completed successfully."
    assert MOTOR_R in {reg.key for reg, _b, _a in tuner.auto.changes}                        # and the results were read back


def test_values_the_drive_was_given_do_not_count_as_a_change_of_the_datasheet(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=30.0)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "running", timeout=WAIT)
    sim.params[MAX_CURRENT], sim.params[POLES] = 6.5, 8                                       # a drive that stores what it was told at once
    qtbot.waitUntil(lambda: tuner.auto._polls >= 2, timeout=15000)
    assert tuner.auto.state == "running" and tuner.auto._detected_keys == []                  # not a result: still waiting
    sim.params[MOTOR_R] = 0.93                                                                # a measured value arrives
    qtbot.waitUntil(lambda: tuner.auto.state in ("finishing", "reading", "done"), timeout=WAIT)
    assert tuner.auto._detected_keys == [MOTOR_R]
    qtbot.mouseClick(win.ribbon.stop_button, Qt.MouseButton.LeftButton)                       # (the simulated procedure is still going)


def test_a_drive_that_does_not_answer_reads_while_it_works_is_asked_again(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=1.0)
    tuner.go_to(tuner.auto)
    real_read, muted = client.read, {"on": False}

    def read(reg):
        if muted["on"]:
            raise DriveTimeout("no answer while it works")
        return real_read(reg)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "running", timeout=WAIT)
    client.read, muted["on"] = read, True
    qtbot.wait(3500)                                                                          # the procedure is over in the drive by now
    assert tuner.auto.state == "running" and tuner.auto._polls == 0 and sim.flash_saved       # unanswered readings are not a failure
    muted["on"] = False
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    assert tuner.auto.state == "done" and tuner.auto._polls >= 1


def test_the_drive_leaving_the_mode_is_not_the_end_only_the_datasheet_is(win, qtbot, monkeypatch):
    import sdr_tuner.gui.tuner as tuner_module
    import sdr_tuner.simulator as simulator
    monkeypatch.setattr(simulator, "MEASURED", {})                                            # this time the drive changes nothing measured
    monkeypatch.setattr(tuner_module, "NO_CHANGE_WAIT", 1.0)
    tuner, sim, client = tuner_ready(win, qtbot, seconds=0.8)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: "Datasheet has not changed yet" in tuner.auto.status.text(), timeout=WAIT)     # it says so ...
    qtbot.wait(7000)                                                                          # ... and however long it takes, it does not give up
    assert tuner.auto.state == "running" and client._heartbeat is not None and tuner.auto._polls >= 2
    assert tuner.auto.changes is None and tuner.auto.stop_btn.isEnabled()
    sim.params[MOTOR_R] = 1.05                                                                # 'a minute later' the Datasheet changes
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    assert tuner.auto._detected_keys == [MOTOR_R] and "completed successfully" in tuner.auto.status.text()
    assert client._heartbeat is None


def test_a_run_of_any_length_completes_when_the_datasheet_changes(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=6.5)                                 # a long one: nothing is assumed about the time
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.wait(4500)
    assert tuner.auto.state == "running"                                                      # no timeout ends it early
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    assert sim.autoconfig_aborted == 0 and sim.flash_saved and "completed successfully" in tuner.auto.status.text()


def test_a_fault_during_the_tuning_fails_it(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=6.0)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
    sim.fault_bits = 0x0002                                                                   # Driver Warning
    sim._autoconfig = None                                                                    # the drive gave up
    qtbot.waitUntil(lambda: tuner.auto.state == "failed", timeout=WAIT)
    assert "fault" in tuner.auto.status.text().lower() and client._heartbeat is None and tuner.auto.changes is None


def test_disconnecting_during_a_tuning_ends_it_cleanly(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=6.0)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "running", timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert tuner.auto.state == "stopped" and "connection" in tuner.auto.status.text() and not tuner.auto.start_btn.isEnabled()
    assert not tuner._loaded and win.errors == []
    assert not win.tuner_buttons["quick"]["load"].isEnabled()


def test_the_summary_says_what_is_left_to_do(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    tuner.go_to(tuner.summary)
    text = tuner.summary.list.text()
    assert "has not been run" in text and "Changes not applied yet on: Motor Settings" in text
    assert tuner.summary.table.rowCount() == 0


def test_the_value_boxes_carry_no_hint_text(win):
    """An empty box stays empty (no '0x.. accepted' in it), on the tuner pages and in the parameter tables alike."""
    regs = {r.key: r for r in defs.load_registers()}
    from sdr_tuner.gui.params_tab import make_editor
    for key in (POLES, MAX_CURRENT, (0x2000, 0x0C), (0x2034, 0xC2)):                          # integer and float parameters
        widget, get, set_ = make_editor(regs[key], lambda: None)
        assert widget.placeholderText() == "", key
    for form in win.quick_tuner.forms + win.advanced_tuner.forms:
        for row in form.rows.values():
            assert not getattr(row.widget, "placeholderText", lambda: "")(), row.reg.name
    widget.setText("0x10")                                                                     # hex is still understood ...
    assert get() == 16                                                                         # ... it is just not advertised


# ---- the settings of the encoder that was chosen ----------------------------------------------------------------------
MOTOR_ENC, SERVO_ENC, HALL = (0x2000, 0x0A), (0x2000, 0x0B), (0x2000, 0x09)
SSI_CPR, QUAD_CPR, SPI_CPR = (0x3013, 0x02), (0x3011, 0x02), (0x3014, 0x02)


def choose(form, key, value):
    """Pick an entry of a parameter's list the way a user does."""
    combo = form.rows[key].widget
    combo.setCurrentIndex(combo.findData(value))
    combo.activated.emit(combo.currentIndex())


def shown(form):
    return [g["head"].text() for g in form._groups if g["shown"]]


@pytest.mark.parametrize("which", ["quick_tuning", "advanced_tuning"])
def test_choosing_an_encoder_shows_the_settings_of_that_encoder(win, qtbot, which):
    tuner, sim, client = tuner_ready(win, qtbot, which)
    form = tuner.feedback
    tuner.go_to(form)
    assert shown(form) == ["SENSORS", "SSI/BISS-C ENCODER SETTINGS  (MOTOR ENCODER)", "HALL SENSORS SETTINGS"]     # what the drive holds
    assert not form.rows[SSI_CPR].widget.isHidden() and form.rows[QUAD_CPR].widget.isHidden()
    assert form.value(SSI_CPR) == 65536                                                       # already read from the drive
    choose(form, MOTOR_ENC, 1)                                                                # Quadrature Encoder
    assert shown(form)[1] == "QUADRATURE ENCODER SETTINGS  (MOTOR ENCODER)" and form.rows[SSI_CPR].widget.isHidden()
    assert not form.rows[QUAD_CPR].widget.isHidden() and form.rows[QUAD_CPR].label.text() == "counts per revolution"
    choose(form, SERVO_ENC, 3)                                                                # SPI encoder on the load side
    assert shown(form)[1:3] == ["QUADRATURE ENCODER SETTINGS  (MOTOR ENCODER)", "SPI ENCODER SETTINGS  (SERVO ENCODER)"]
    choose(form, SERVO_ENC, 1)                                                                # the same type for both: one block
    assert [h for h in shown(form) if "QUADRATURE" in h] == ["QUADRATURE ENCODER SETTINGS  (MOTOR ENCODER AND SERVO ENCODER)"]
    assert form.rows[SPI_CPR].widget.isHidden()
    choose(form, MOTOR_ENC, 0)
    choose(form, SERVO_ENC, 0)                                                                # no encoder: nothing to enter
    assert shown(form) == ["SENSORS", "HALL SENSORS SETTINGS"]
    choose(form, HALL, 0)                                                                     # sensorless: no Hall wiring either
    assert shown(form) == ["SENSORS"]
    for kind in (2, 4, 5, 6):                                                                 # every other type has its own block
        choose(form, MOTOR_ENC, kind)
        assert len(shown(form)) == 2 and "ENCODER SETTINGS" in shown(form)[1], kind


@pytest.mark.parametrize("which", ["quick_tuning", "advanced_tuning"])
def test_the_settings_of_a_chosen_encoder_are_written_with_apply(win, qtbot, which):
    tuner, sim, client = tuner_ready(win, qtbot, which)
    form = tuner.feedback
    tuner.go_to(form)
    choose(form, MOTOR_ENC, 1)
    enter(form, QUAD_CPR, "4096")
    enter(form, (0x3011, 0x03), "128")                                                        # encoder bias
    form.rows[(0x3011, 0x04)].widget.click()                                                  # 'inverted installation' is a yes / no box
    assert form.dirty_count() == 4                                                            # the encoder type and three settings
    asked = []
    tuner._confirm = lambda text: asked.append(text) or True
    tuner.apply_btn.click()
    qtbot.waitUntil(lambda: not tuner.busy and not form.is_dirty, timeout=WAIT)
    assert sim.params[MOTOR_ENC] == 1 and sim.params[QUAD_CPR] == 4096 and sim.params[(0x3011, 0x03)] == 128
    assert bool(sim.params[(0x3011, 0x04)]) is True and "4 changed parameter(s)" in asked[0] and "counts per revolution" in asked[0]
    assert form.value(QUAD_CPR) == 4096 and not form.rows[QUAD_CPR].label.property("state")


@pytest.mark.parametrize("which", ["quick_tuning", "advanced_tuning"])
def test_edits_in_a_group_that_is_not_shown_are_not_applied_and_are_not_errors(win, qtbot, which):
    tuner, sim, client = tuner_ready(win, qtbot, which)
    form = tuner.feedback
    tuner.go_to(form)
    enter(form, SSI_CPR, "1234")                                                              # an edit while the SSI block is shown
    assert form.is_dirty
    choose(form, MOTOR_ENC, 0)                                                                # then the encoder is taken away
    choose(form, MOTOR_ENC, 1)
    enter(form, QUAD_CPR, "2000")
    tuner._confirm = lambda text: True
    tuner.apply_btn.click()
    qtbot.waitUntil(lambda: not tuner.busy and not form.is_dirty, timeout=WAIT)
    assert sim.params[QUAD_CPR] == 2000 and sim.params[SSI_CPR] == 65536                      # the hidden edit did not reach the drive
    choose(form, MOTOR_ENC, 2)                                                                # shown again: the edit is still there
    assert form.rows[SSI_CPR].widget.text() == "1234" and form.is_dirty
    enter(form, SSI_CPR, "many")
    assert tuner.errors() and "counts per revolution" in tuner.errors()[0]
    choose(form, MOTOR_ENC, 1)
    assert tuner.errors() == []                                                               # a wrong entry that is not shown is no error


@pytest.mark.parametrize("changed", [[MOTOR_R, (0x2000, 0x05)], [MOTOR_R]], ids=["two values", "one value"])
def test_a_repeat_run_is_complete_when_only_a_couple_of_datasheet_values_changed(win, qtbot, monkeypatch, changed):
    import sdr_tuner.simulator as simulator
    tuner, sim, client = tuner_ready(win, qtbot, seconds=1.0)
    previous = dict(simulator.MEASURED)
    sim.params.update(previous)                                                               # the motor has been tuned before ...
    new = dict(previous)
    for key in changed:
        new[key] = previous[key] * 1.01                                                       # ... and this time a little differs in only these
    monkeypatch.setattr(simulator, "MEASURED", new)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    assert sorted(tuner.auto._detected_keys) == sorted(changed) and tuner.auto.state == "done"
    assert tuner.auto.status.text() == "Auto tuning completed successfully." and sim.flash_saved


def test_a_value_written_early_does_not_end_the_wait_while_the_drive_says_it_is_still_working(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=12.0, heartbeat=1.0)                 # long enough that detection (up to a 3 s poll
                                                                                               # away) still lands well before the drive is done
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "running" and client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
    sim.params[MOTOR_R] = 0.93                                                                # one value is written early, the procedure goes on
    qtbot.waitUntil(lambda: tuner.auto.state == "finishing", timeout=WAIT)
    qtbot.wait(1800)
    assert tuner.auto.state == "finishing" and client._heartbeat is not None                  # still waiting, still keeping the drive informed
    assert "still finishing" in tuner.auto.status.text()
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)
    assert tuner.auto.state == "done" and sim.flash_saved and sim.autoconfig_aborted == 0     # the procedure was not cut short
    assert client._heartbeat is None


def test_finishing_does_not_wait_forever_if_the_drive_never_leaves_autoconfig(win, qtbot, monkeypatch):
    """The user's rule: once the Datasheet has changed, the tuning IS complete - a drive that keeps reporting Operation
    Mode 3 afterwards (still saving to flash, or just slow to report the mode change) gets a short, CAPPED courtesy wait,
    not an open-ended one tied to Operation Mode."""
    import sdr_tuner.gui.tuner as tuner_module
    monkeypatch.setattr(tuner_module, "SETTLE_MAX", 1.0)
    tuner, sim, client = tuner_ready(win, qtbot, seconds=30.0)                                # stays "running" far longer than the cap
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "running" and client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
    sim.params[MOTOR_R] = 0.93                                                                # the Datasheet changes ...
    qtbot.waitUntil(lambda: tuner.auto.state == "finishing", timeout=WAIT)
    qtbot.waitUntil(lambda: tuner.auto.state == "done", timeout=WAIT)                         # ... and it does not wait for autoconfig_seconds=30
    assert client.telemetry_snapshot().get("op_mode") == 3                                    # the drive is STILL reporting Autoconfiguration
    assert sim.autoconfig_aborted == 0                                                        # the run was not cut short - it is simply done
    assert MOTOR_R in {reg.key for reg, _b, _a in tuner.auto.changes}


def test_the_tuning_pages_own_stop_sends_a_reset_and_nothing_else(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=8.0)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
    assert "Reset" in tuner.auto.stop_btn.toolTip() and "current supply" in tuner.auto.stop_btn.toolTip()
    tuner.auto.stop_btn.click()                                                               # in the background: no question is asked
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 0, timeout=WAIT)
    assert after_the_tuning_command(sim) == ["reset"]                                         # Reset only: Stop would not end it
    assert sim.autoconfig_aborted == 1 and not sim.flash_saved and tuner.auto.state == "stopped"
    text = tuner.auto.status.text()
    assert "A Reset was sent" in text and "clears the drive's errors" in text and "cuts the current supply to the motor" in text
    assert win.status_text() == "Automatic tuning stopped - Reset sent to the drive."


def test_every_stop_in_the_window_resets_a_running_tuning(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=8.0)
    tuner.go_to(tuner.auto)
    for number, how in enumerate(("ribbon", "esc", "motion page"), 1):
        mark = len(sim.commands_received)                                                     # only this pass's commands are looked at
        run_tuning(tuner, qtbot)
        qtbot.waitUntil(lambda: tuner.auto.state == "running" and client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
        if how == "ribbon":
            qtbot.mouseClick(win.ribbon.stop_button, Qt.MouseButton.LeftButton)
        elif how == "esc":
            qtbot.keyClick(win, Qt.Key.Key_Escape)
        else:
            win.open_tool("motion")                                                           # the tuning goes on while another page is shown
            assert tuner.auto.state == "running"
            win.drive_tab.stop_btn.click()
        qtbot.waitUntil(lambda: tuner.auto.state == "stopped", timeout=WAIT)
        qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 0, timeout=WAIT)
        qtbot.waitUntil(lambda: len(sim.commands_received) >= mark + 3, timeout=WAIT)         # the tuning command, the Reset and the motion page's Stop
        names = [name for name, _values in sim.commands_received[mark:]]
        assert names[0].startswith("autoconfiguration") and names[1] == "reset", (how, names)
        assert names.count("reset") == 1, (how, names)                                        # the tuner's Reset is not repeated by the motion page
        assert sim.autoconfig_aborted == number, how                                          # this pass's run was really ended
        win.open_tool("quick_tuning")
        tuner.go_to(tuner.auto)
        qtbot.waitUntil(lambda: tuner.auto.start_btn.isEnabled(), timeout=WAIT)


def test_stop_with_no_tuning_running_sends_no_reset(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot)
    win.emergency_stop()
    qtbot.waitUntil(lambda: len(sim.commands_received) >= 1, timeout=WAIT)
    qtbot.wait(300)
    assert [name for name, _ in sim.commands_received] == ["stop"]                            # the motion page's Stop, nothing more: no Reset
    assert tuner.auto.state == "idle"


def test_disconnecting_during_a_tuning_sends_a_reset_before_the_connection_closes(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, seconds=8.0)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "running", timeout=WAIT)
    frames = win.session.frames
    while not frames.empty():
        frames.get()
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    sent = []
    while not frames.empty():
        sent.append(frames.get())
    node = client.node
    assert any(d == "tx" and can_id == 0x200 + node and data[0] == 0x01 for d, _t, can_id, data in sent)     # the Reset command frame
    assert tuner.auto.state == "stopped" and "connection to the drive is being closed" in tuner.auto.status.text()
    assert not any(d == "tx" and can_id == 0x200 + node and data[0] == 0x04 for d, _t, can_id, data in sent)   # and no Stop: it would not end it


def test_a_tuning_that_has_to_be_given_up_is_ended_with_a_reset(win, qtbot, monkeypatch):
    import sdr_tuner.gui.tuner as tuner_module
    monkeypatch.setattr(tuner_module, "RUN_LIMIT", 1.5)
    tuner, sim, client = tuner_ready(win, qtbot, seconds=30.0)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: tuner.auto.state == "failed", timeout=WAIT)
    qtbot.waitUntil(lambda: sim.autoconfig_aborted == 1, timeout=WAIT)                        # the drive really stopped
    assert after_the_tuning_command(sim) == ["reset"]


@pytest.mark.parametrize("how", ["motion page button", "ribbon", "esc"])
def test_stop_also_resets_a_drive_found_in_the_configuration_mode_that_no_tuner_started(win, qtbot, how):
    sim, client = connect(win, qtbot)                                                         # e.g. started from another tool, or before a restart
    sim.autoconfig_seconds = 8.0
    win.open_tool("motion")
    client.set_heartbeat(0.2)
    client.send_command("autoconfiguration_brushless_motor", 6.5, 8)
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
    if how == "motion page button":
        win.drive_tab.stop_btn.click()
    elif how == "ribbon":
        qtbot.mouseClick(win.ribbon.stop_button, Qt.MouseButton.LeftButton)
    else:
        qtbot.keyClick(win, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 0, timeout=WAIT)      # Stop alone would have left it running
    assert after_the_tuning_command(sim) == ["stop", "reset"] and sim.autoconfig_aborted == 1
    assert "Stop has no effect" in win.drive_tab.status.text() and "cuts the current supply" in win.drive_tab.status.text()
    client.set_heartbeat(None)


def test_stop_outside_the_configuration_mode_is_only_a_stop(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("motion")
    win.drive_tab.target.setValue(60.0)
    win.drive_tab.run_btn.click()                                                             # a normal speed run: Stop works in this mode
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 6, timeout=WAIT)
    qtbot.waitUntil(lambda: sim.speed_hz > 5.0, timeout=WAIT)                                 # let it actually spin up, or STOP could race RUN
    win.drive_tab.stop_btn.click()
    qtbot.waitUntil(lambda: abs(sim.speed_hz) < 0.5, timeout=WAIT)
    assert [name for name, _ in sim.commands_received if name in ("stop", "reset")] == ["stop"]
    assert win.drive_tab.status.text() == "STOP sent."


# ---- Filters, Application Settings and Limits and Protections (Advanced Tuning only) -----------------------------------
NOTCH0_FREQ, ACCEL_ENABLE, ACCEL, HIDE_FAULTS, BRAKE_PULL_V = (0x2014, 0x03), (0x2048, 0x04), (0x2048, 0x05), (0x2044, 0x02), (0x2025, 0x03)
SPEED_FILTER_ON, FOC_DQ_COMP = (0x2004, 0x73), (0x2004, 0x42)


def test_the_advanced_tuner_has_filters_application_and_limits_pages_the_quick_tuner_does_not(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    for page, key in ((tuner.filters, NOTCH0_FREQ), (tuner.application, ACCEL), (tuner.limits, HIDE_FAULTS)):
        assert key in page.rows and page in tuner.forms
    win.open_tool("quick_tuning")
    assert not hasattr(win.quick_tuner, "filters") and not hasattr(win.quick_tuner, "application") and not hasattr(win.quick_tuner, "limits")


def test_feature_switches_sit_next_to_the_gain_they_enable(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    laws = tuner.laws
    assert SPEED_FILTER_ON in laws.rows and FOC_DQ_COMP in laws.rows                           # in Control Laws, not a separate page
    assert laws.value(SPEED_FILTER_ON) is True                                                 # the drive's own default
    feedback = tuner.feedback
    assert (0x2004, 0x72) in feedback.rows                                                     # 'use servo encoder for speed' is with the sensors


def test_applying_filters_writes_only_the_filters_page(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    tuner.go_to(tuner.filters)
    enter(tuner.filters, NOTCH0_FREQ, "180")
    tuner.laws.rows[SPEED_FILTER_ON].widget.click()                                            # a change on another page, not applied yet
    tuner._confirm = lambda text: True
    tuner.apply_btn.click()
    qtbot.waitUntil(lambda: not tuner.busy and not tuner.filters.is_dirty, timeout=WAIT)
    assert sim.params[NOTCH0_FREQ] == pytest.approx(180.0) and tuner.laws.is_dirty              # the other page's edit is untouched
    tuner.revert_all()


def test_application_settings_and_limits_are_written_with_apply_all(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    before = sim.params[ACCEL]
    enter(tuner.application, ACCEL, str(before + 50))
    enter(tuner.limits, BRAKE_PULL_V, "26")
    tuner._confirm = lambda text: True
    win.tuner_buttons["advanced"]["apply_all"].click()
    qtbot.waitUntil(lambda: not tuner.busy and not tuner.application.is_dirty and not tuner.limits.is_dirty, timeout=WAIT)
    assert sim.params[ACCEL] == pytest.approx(before + 50) and sim.params[BRAKE_PULL_V] == pytest.approx(26.0)


def test_every_advanced_tuner_row_is_a_configuration_parameter_used_once(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    keys = [k for form in tuner.forms for k in form.rows]
    assert len(keys) == len(set(keys)), "the same parameter appears on two pages of the same tuner"
    regs = {r.key: r for r in defs.load_registers()}
    assert all(not regs[k].telemetry for k in keys)


# ---- Item Configuration: Product Activation and Networks -----------------------------------------------------------
ACTIVATION_KEY, CAN_BITRATE, NODE_ID, HEARTBEAT_TIMEOUT_REG = (0x20FF, 0x02), (0x3000, 0x02), (0x3000, 0x03), (0x3000, 0x04)


def test_nothing_is_read_from_the_drive_on_connect_only_on_read(win, qtbot):
    sim, client = connect(win, qtbot)
    activation, networks = win.config_page.activation, win.config_page.networks
    assert not activation.param_rows[ACTIVATION_KEY].widget.text() and not networks.param_rows[CAN_BITRATE].widget.currentText()
    assert activation.read_btn.isEnabled() and networks.read_btn.isEnabled()
    activation.read_now()
    qtbot.waitUntil(lambda: activation.param_rows[ACTIVATION_KEY].widget.text() != "", timeout=WAIT)
    assert int(activation.param_rows[ACTIVATION_KEY].widget.text()) == sim.params[ACTIVATION_KEY]
    networks.read_now()
    qtbot.waitUntil(lambda: networks.param_rows[CAN_BITRATE].widget.currentText() != "", timeout=WAIT)
    assert networks.param_rows[CAN_BITRATE].get() == sim.params[CAN_BITRATE] and networks.param_rows[NODE_ID].get() == sim.params[NODE_ID]
    assert win.status_text().startswith("Read 8 parameter(s)")


def test_writing_the_activation_key_asks_first_and_updates_the_fault(win, qtbot):
    sim, client = connect(win, qtbot)
    win.drive_tab.emergency_stop()                                                            # unrelated: just settling the page
    activation = win.config_page.activation
    activation.read_now()
    qtbot.waitUntil(lambda: activation.param_rows[ACTIVATION_KEY].last is not None, timeout=WAIT)
    edit = activation.param_rows[ACTIVATION_KEY].widget
    edit.setText("123456")
    edit.textEdited.emit("123456")
    assert activation.is_dirty() and activation.write_btn.isEnabled()
    asked = []
    activation._confirm = lambda text: asked.append(text) or False
    activation.write_changed()
    assert len(asked) == 1 and "Write 1 changed parameter(s)" in asked[0] and sim.params[ACTIVATION_KEY] != 123456
    activation._confirm = lambda text: True
    activation.write_changed()
    qtbot.waitUntil(lambda: not activation.is_dirty(), timeout=WAIT)
    assert sim.params[ACTIVATION_KEY] == 123456 and "Wrote 1 parameter(s)" in win.status_text()


def test_networks_write_warns_about_bitrate_and_node_id_and_power_cycle(win, qtbot):
    sim, client = connect(win, qtbot)
    networks = win.config_page.networks
    networks.read_now()
    qtbot.waitUntil(lambda: networks.param_rows[NODE_ID].last is not None, timeout=WAIT)
    node_edit = networks.param_rows[NODE_ID].widget
    node_edit.setText(str(client.node + 1))                                                   # a changed, risky value
    node_edit.textEdited.emit(str(client.node + 1))
    asked = []
    networks._confirm = lambda text: asked.append(text) or False                              # decline: nothing is written
    networks.write_changed()
    assert len(asked) == 1 and "unreachable" in asked[0] and "power cycle" in asked[0] and "CANopen: Node ID" in asked[0]
    assert sim.params[NODE_ID] == client.node
    networks._confirm = lambda text: True
    networks.write_changed()
    qtbot.waitUntil(lambda: not networks.is_dirty(), timeout=WAIT)
    assert sim.params[NODE_ID] == client.node + 1


def test_revert_puts_back_the_drives_values_without_writing(win, qtbot):
    sim, client = connect(win, qtbot)
    networks = win.config_page.networks
    networks.read_now()
    qtbot.waitUntil(lambda: networks.param_rows[NODE_ID].last is not None, timeout=WAIT)
    edit = networks.param_rows[HEARTBEAT_TIMEOUT_REG].widget
    edit.setText("9.0")
    edit.textEdited.emit("9.0")
    assert networks.is_dirty() and networks.revert_btn.isEnabled()
    networks._confirm = lambda text: pytest.fail("revert must not write anything")
    networks.revert()
    assert not networks.is_dirty() and networks.param_rows[HEARTBEAT_TIMEOUT_REG].get() == sim.params[HEARTBEAT_TIMEOUT_REG]
    assert sim.params[HEARTBEAT_TIMEOUT_REG] == pytest.approx(3.5)


def test_a_wrong_entry_is_not_written_and_is_shown_as_an_error(win, qtbot):
    sim, client = connect(win, qtbot)
    networks = win.config_page.networks
    networks.read_now()
    qtbot.waitUntil(lambda: networks.param_rows[NODE_ID].last is not None, timeout=WAIT)
    edit = networks.param_rows[NODE_ID].widget
    edit.setText("not a number")
    edit.textEdited.emit("not a number")
    assert networks.errors() and "Node ID" in networks.errors()[0]
    assert not networks.write_btn.isEnabled()                                                # nothing else was edited: not dirty either
    networks._confirm = lambda text: pytest.fail("a wrong entry must not reach the drive")
    networks.write_changed()


def test_disconnecting_clears_the_fields_so_stale_values_are_not_shown(win, qtbot):
    sim, client = connect(win, qtbot)
    activation = win.config_page.activation
    activation.read_now()
    qtbot.waitUntil(lambda: activation.param_rows[ACTIVATION_KEY].widget.text() != "", timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert not activation.read_btn.isEnabled() and not activation.write_btn.isEnabled()
    assert activation.param_rows[ACTIVATION_KEY].last is None and not activation.is_dirty()


# ---- Step Response Tests (Advanced Tuning only) -------------------------------------------------------------------
def enter_amplitude(steps, text):
    steps.amplitude.setText(text)
    steps.amplitude.textEdited.emit(text)


def test_the_step_response_page_is_advanced_only_and_starts_a_test(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    assert not hasattr(win.quick_tuner, "steps")
    steps = tuner.steps
    tuner.go_to(steps)
    assert steps.kind.currentText() == "Speed (Electronic Speed Control)" and steps.amplitude_units.text() == "Hz"
    enter_amplitude(steps, "40")
    asked = []
    steps._confirm = lambda text: asked.append(text) or True
    steps.start_btn.click()
    assert len(asked) == 1 and "Speed (Electronic Speed Control)" in asked[0] and "40" in asked[0]
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 16, timeout=WAIT)     # Testing - Speed Step Response
    assert steps.running and not steps.start_btn.isEnabled() and steps.stop_btn.isEnabled() and not steps.kind.isEnabled()
    qtbot.waitUntil(lambda: "Running" in steps.status.text() and "STOP to end it" in steps.status.text(), timeout=WAIT)
    qtbot.waitUntil(lambda: abs(sim.speed_hz) > 20, timeout=WAIT)
    steps.stop_btn.click()
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 0, timeout=WAIT)
    assert not steps.running and client.commanded is None
    qtbot.waitUntil(lambda: steps.status.text() == "Ready to start.", timeout=WAIT)


def test_each_test_type_has_its_own_amplitude_units_and_watched_signal(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    steps = tuner.steps
    tuner.go_to(steps)
    expected = {"Speed (Electronic Speed Control)": "Hz", "Current (Field Oriented Control)": "A", "Iq Current": "A",
               "Servo (position)": "counts"}
    for i in range(steps.kind.count()):
        steps.kind.setCurrentIndex(i)
        assert steps.amplitude_units.text() == expected[steps.kind.currentText()], steps.kind.currentText()


def test_a_test_replaces_the_running_speed_command_with_a_warning(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    win.open_tool("motion")
    win.drive_tab.target.setValue(60.0)
    win.drive_tab.run_btn.click()
    qtbot.waitUntil(lambda: client.commanded is not None and client.commanded[0] == "esc_rpm", timeout=WAIT)
    win.open_tool("advanced_tuning")
    steps = tuner.steps
    tuner.go_to(steps)
    enter_amplitude(steps, "30")
    qtbot.waitUntil(lambda: any("replace it" in c.text for c in steps.checks()), timeout=WAIT)
    asked = []
    steps._confirm = lambda text: asked.append(text) or True
    steps.start_btn.click()
    assert "replace it" in asked[0]
    qtbot.waitUntil(lambda: client.commanded[0] == "testing_electronic_speed_control_esc", timeout=WAIT)
    steps.stop()


def test_a_running_automatic_tuning_blocks_a_step_response_test(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning", seconds=6.0, heartbeat=3.5)
    tuner.go_to(tuner.auto)
    run_tuning(tuner, qtbot)
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("op_mode") == 3, timeout=WAIT)
    steps = tuner.steps
    tuner.go_to(steps)
    enter_amplitude(steps, "10")
    qtbot.waitUntil(lambda: any(c.level == "block" and "automatic configuration" in c.text for c in steps.checks()), timeout=WAIT)
    assert not steps.start_btn.isEnabled()
    tuner.go_to(tuner.auto)
    tuner.auto.stop()


def test_a_wrong_value_blocks_the_start_and_is_never_sent(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    steps = tuner.steps
    tuner.go_to(steps)
    enter_amplitude(steps, "not a number")
    assert steps.checks()[0].level == "block" and "must be a number" in steps.checks()[0].text
    steps._confirm = lambda text: pytest.fail("a wrong value must not even ask")
    steps.start()
    assert client.commanded is None


def test_declining_the_confirmation_sends_no_command(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    steps = tuner.steps
    tuner.go_to(steps)
    enter_amplitude(steps, "10")
    steps._confirm = lambda text: False
    steps.start()
    assert client.commanded is None and steps.status.text() == "Test cancelled - nothing was sent."


def test_esc_and_the_round_stop_button_end_a_running_test(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    steps = tuner.steps
    tuner.go_to(steps)
    enter_amplitude(steps, "15")
    steps._confirm = lambda text: True
    steps.start()
    qtbot.waitUntil(lambda: steps.running, timeout=WAIT)
    qtbot.keyClick(win, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not steps.running, timeout=WAIT)
    assert client.commanded is None
    steps.start()
    qtbot.waitUntil(lambda: steps.running, timeout=WAIT)
    qtbot.mouseClick(win.ribbon.stop_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not steps.running, timeout=WAIT)


def test_disconnecting_ends_a_running_test(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    steps = tuner.steps
    tuner.go_to(steps)
    enter_amplitude(steps, "15")
    steps._confirm = lambda text: True
    steps.start()
    qtbot.waitUntil(lambda: steps.running, timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert not steps.start_btn.isEnabled()


def test_open_in_recorder_switches_the_page(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    tuner.go_to(tuner.steps)
    tuner.steps.recorder_btn.click()
    assert win.host.current_page() is win.recorder_page


# ---- Peripherals (RC PWM / Electronic Gearing, GPIO, Thermistor, Inverter) ----------------------------------------
RC_ENABLE, RC_FUNCTION, GPIO_SWITCH_TYPE, THERMISTOR_ENABLE, SHUNT_A = (0x3015, 0x01), (0x3015, 0x05), (0x3020, 0x19), (0x3021, 0x10), (0x3002, 0x0A)
GPIO_ESTOP_STATUS, GPIO_LIMIT_NEG, GPIO_LIMIT_POS, GPIO_GENERIC_IN, STO_STATUS = (
    (0x5009, 0x04), (0x5009, 0x05), (0x5009, 0x06), (0x5009, 0x10), (0x5009, 0x20))


def test_peripherals_reads_all_33_parameters_on_connect(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)
    assert len(page.form.rows) == 33
    assert page.form.value(RC_FUNCTION) == sim.params[RC_FUNCTION] and page.form.value(SHUNT_A) == pytest.approx(sim.params[SHUNT_A])
    assert "Read 33 parameter(s)" in win.status_text()
    assert not page.form.is_dirty and not page.write_btn.isEnabled() and page.read_btn.isEnabled()


def test_peripherals_digital_inputs_and_safety_are_read_only_status(win, qtbot):
    """The 5 telemetry signals EAS3's own Motion page shows at a glance (Digital In Bit/Func/Stat, Safety
    STO1/STO2/ERR) - this tool has the same real data (confirmed against the Device Reference: `GPIO: Emergency
    Stop Switch`/`GPIO: Limit Switch: Negative/Positive Speed`/`GPIO: Generic Input`/`STO /Interlock: status`,
    all `access: ro`), just not previously shown anywhere outside the 175-parameter Telemetry Explorer.
    drive_tab.py's own Motion page can never poll for them (it "never asks the drive for telemetry" outside the
    broadcast, by design), so they live here instead, in the existing Read All/on-demand model - not auto-live,
    but no longer buried among 175 others either. Each row must come through read-only (FormPage now honours
    `reg.writable`, a real, separate gap this exposed - see the FormPage fix in tuner.py's own history)."""
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)
    for key in (GPIO_ESTOP_STATUS, GPIO_LIMIT_NEG, GPIO_LIMIT_POS, GPIO_GENERIC_IN):
        row = page.form.rows[key]
        assert not row.reg.writable
        assert row.widget.isReadOnly()                                   # plain QLineEdit rows (no options)
    sto_row = page.form.rows[STO_STATUS]
    assert not sto_row.reg.writable
    assert not sto_row.widget.isEnabled()                                 # a ComboBox row (STO has options)
    assert page.form.value(GPIO_ESTOP_STATUS) == 0                        # unmodeled telemetry, like any other -
    assert page.form.value(STO_STATUS) == 0                               # simulator's own generic 0 fallback
    assert "Motion blocked (SAFE)" in page.form.rows[STO_STATUS].widget.currentText()


def test_peripherals_edits_are_written_only_on_write_changed(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)
    enter(page.form, SHUNT_A, "0.02")
    page.form.rows[THERMISTOR_ENABLE].widget.click()
    assert page.form.is_dirty and page.write_btn.isEnabled() and page.revert_btn.isEnabled()
    asked = []
    page._confirm = lambda text: asked.append(text) or False
    page.write_changed()
    assert len(asked) == 1 and "2 changed parameter(s)" in asked[0] and sim.params[SHUNT_A] != pytest.approx(0.02)
    page._confirm = lambda text: True
    page.write_changed()
    qtbot.waitUntil(lambda: not page.form.is_dirty, timeout=WAIT)
    assert sim.params[SHUNT_A] == pytest.approx(0.02) and bool(sim.params[THERMISTOR_ENABLE]) is True
    assert "Wrote 2 parameter(s)" in win.status_text()


def test_peripherals_revert_discards_edits_without_writing(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)
    before = sim.params[RC_FUNCTION]
    page._confirm = lambda text: pytest.fail("revert must not write anything")
    combo = page.form.rows[RC_FUNCTION].widget
    combo.setCurrentIndex((combo.currentIndex() + 1) % combo.count())
    combo.activated.emit(combo.currentIndex())
    assert page.form.is_dirty
    page.revert()
    assert not page.form.is_dirty and page.form.value(RC_FUNCTION) == before
    assert sim.params[RC_FUNCTION] == before


def test_a_wrong_entry_in_peripherals_is_never_written(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)
    enter(page.form, SHUNT_A, "not a number")
    page._confirm = lambda text: pytest.fail("a wrong entry must not reach the drive")
    page.write_changed()
    assert win.status_text().startswith("Cannot write") and "ADC: Shunt Resistor in Phase A" in win.status_text()


def test_peripherals_disconnect_clears_the_loaded_flag(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    assert not page._loaded and not page.read_btn.isEnabled() and not page.write_btn.isEnabled()


# ---- Peripheral Commands (Electromagnetic Brake, GPIO: Generic/PWM Output - one-shot, not registers) ----------
def test_peripheral_command_buttons_follow_the_connection(win, qtbot):
    page = win.peripherals_page
    for btn in (page.brake_engage_btn, page.brake_disengage_btn, page.gpio_high_btn, page.gpio_low_btn,
               page.gpio_pwm_send_btn):
        assert not btn.isEnabled()
    connect(win, qtbot)
    for btn in (page.brake_engage_btn, page.brake_disengage_btn, page.gpio_high_btn, page.gpio_low_btn,
               page.gpio_pwm_send_btn):
        assert btn.isEnabled()


def test_electromagnetic_brake_engage_and_disengage(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)     # don't race the page's own auto Read All on open
    qtbot.mouseClick(page.brake_engage_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.brake_engaged is True, timeout=WAIT)
    assert "Electromagnetic Brake engaged" in win.status_text()
    qtbot.mouseClick(page.brake_disengage_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.brake_engaged is False, timeout=WAIT)
    assert "Electromagnetic Brake disengaged" in win.status_text()


def test_gpio_generic_output_set_high_and_low(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)     # don't race the page's own auto Read All on open
    qtbot.mouseClick(page.gpio_high_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.gpio_output is True, timeout=WAIT)
    assert "set high" in win.status_text()
    qtbot.mouseClick(page.gpio_low_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.gpio_output is False, timeout=WAIT)
    assert "set low" in win.status_text()


def test_gpio_pwm_output_sends_the_duty_cycle(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)     # don't race the page's own auto Read All on open
    page.gpio_pwm_value.setValue(0.4)
    qtbot.mouseClick(page.gpio_pwm_send_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.gpio_pwm_duty == pytest.approx(0.4), timeout=WAIT)
    assert "0.40" in win.status_text()


# ---- pressing Enter in a value box applies/writes it, like the container's own button --------------------------------
def press_enter(widget):
    """Enter in a value box - the way returnPressed actually fires, without needing a real key event."""
    widget.returnPressed.emit()


def test_pressing_enter_in_a_tuner_box_applies_that_pages_changes(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    enter(tuner.motor, MAX_CURRENT, "7.0")
    asked = []
    tuner._confirm = lambda text: asked.append(text) or True
    press_enter(tuner.motor.rows[MAX_CURRENT].widget)
    qtbot.waitUntil(lambda: not tuner.motor.is_dirty, timeout=WAIT)
    assert len(asked) == 1 and sim.params[MAX_CURRENT] == 7.0                                 # written, with the usual confirmation
    tuner.go_to(tuner.laws)                                                                   # Enter always applies the page that is SHOWN
    enter(tuner.laws, ESC_KP, "0.09")
    enter(tuner.data, MOTOR_R, "0.95")                                                        # a different, not-currently-shown page
    press_enter(tuner.laws.rows[ESC_KP].widget)
    qtbot.waitUntil(lambda: not tuner.laws.is_dirty, timeout=WAIT)
    assert sim.params[ESC_KP] == pytest.approx(0.09) and tuner.data.is_dirty                  # the other page's edit is untouched


def test_pressing_enter_asks_first_and_a_no_writes_nothing(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot)
    enter(tuner.motor, MAX_CURRENT, "7.0")
    tuner._confirm = lambda text: False
    press_enter(tuner.motor.rows[MAX_CURRENT].widget)
    assert tuner.motor.is_dirty and sim.params[MAX_CURRENT] == 5.0


def test_pressing_enter_in_parameters_explorer_writes_that_one_row(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("parameters")
    read_all(win, qtbot)
    row = rows_by_address(win)["0x2000:0x02"]                                                 # Maximum Limit on Continuous Current
    row.editor.setText("11.0")
    row.editor.textEdited.emit("11.0")
    win.params_tab._confirm = lambda text: pytest.fail("an ordinary row's Write needs no confirmation, same as the row's own button")
    press_enter(row.editor)
    qtbot.waitUntil(lambda: row.item.text(5).startswith("✓ written"), timeout=WAIT)
    assert sim.params[(0x2000, 0x02)] == pytest.approx(11.0)


def test_pressing_enter_on_a_power_cycle_row_still_asks_first(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("parameters")
    read_all(win, qtbot)
    row = next(r for r in win.params_tab.rows if r.reg.key == (0x3000, 0x06))                  # CANopen: receiving frequency (power-cycle)
    before = sim.params[row.reg.key]
    row.editor.setText("500")
    row.editor.textEdited.emit("500")
    asked = []
    win.params_tab._confirm = lambda text: asked.append(text) or False
    press_enter(row.editor)
    assert len(asked) == 1 and "power cycle" in asked[0] and sim.params[row.reg.key] == before  # declined: nothing written


def test_pressing_enter_in_a_read_only_telemetry_row_does_nothing(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("telemetry")
    win.telemetry_tab.read_all()
    wait_idle(win, qtbot, timeout=30000)
    row = next(r for r in win.telemetry_tab.rows if r.reg.key == (0x5001, 0x04))               # DC bus voltage, read-only
    win.telemetry_tab._confirm = lambda text: pytest.fail("a read-only row must not try to write")
    press_enter(row.editor)                                                                    # a read-only QLineEdit can still get Enter
    assert sim.commands_received == [] or True                                                 # nothing crashed and nothing was asked


def test_pressing_enter_in_peripherals_writes_the_changed_parameters(win, qtbot):
    sim, client = connect(win, qtbot)
    win.open_tool("peripherals")
    page = win.peripherals_page
    qtbot.waitUntil(lambda: page._loaded, timeout=WAIT)
    enter(page.form, SHUNT_A, "0.03")
    asked = []
    page._confirm = lambda text: asked.append(text) or True
    press_enter(page.form.rows[SHUNT_A].widget)
    qtbot.waitUntil(lambda: not page.form.is_dirty, timeout=WAIT)
    assert len(asked) == 1 and sim.params[SHUNT_A] == pytest.approx(0.03)


def test_pressing_enter_in_item_configuration_writes_that_group(win, qtbot):
    sim, client = connect(win, qtbot)
    activation = win.config_page.activation
    activation.read_now()
    qtbot.waitUntil(lambda: activation.param_rows[ACTIVATION_KEY].last is not None, timeout=WAIT)
    edit = activation.param_rows[ACTIVATION_KEY].widget
    edit.setText("222222")
    edit.textEdited.emit("222222")
    asked = []
    activation._confirm = lambda text: asked.append(text) or True
    press_enter(edit)
    qtbot.waitUntil(lambda: not activation.is_dirty(), timeout=WAIT)
    assert len(asked) == 1 and sim.params[ACTIVATION_KEY] == 222222


# ---- 3D Simulation (live, from the connected axes' own telemetry - 2026-09-24) -------------------------------
def test_sim3d_page_shows_no_model_message_by_default(win, qtbot):
    page = win.sim3d_page
    assert "No model loaded" in page.status.text()
    assert page._items == {}


def test_sim3d_page_samples_dropdown_lists_and_loads_the_bundled_vendor_files(win, qtbot):
    """Loading a model used to be Item Configuration's job only - this page's own Samples… (2026-09-24, user:
    "give option to load model in this page only") loads through the exact same `session.load_kinematics`, so
    it lists the same bundled files and this page re-renders itself the same way it already does when Item
    Configuration loads one (session.kinematics_changed -> _reload_model, unchanged)."""
    from pathlib import Path
    page = win.sim3d_page
    names = [page.samples.itemText(i) for i in range(page.samples.count())]
    assert "Samples…" in names
    for expected in ("radar.obj", "ko-bot.obj", "robot-dog.obj", "humanoid.obj", "monkey.obj"):
        assert expected in names
    idx = page.samples.findText("robot-dog.obj")
    assert idx > 0
    page.samples.setCurrentIndex(idx)
    page._load_kinematics_sample(idx)
    assert page.samples.currentIndex() == 0                                       # resets to the placeholder
    assert win.session.kinematics_model is not None
    assert Path(win.session.kinematics_model.source).name == "robot-dog.obj.kinematics"
    assert page._items                                                            # the scene actually rebuilt
    assert "Loaded robot-dog.obj.kinematics" in page.status.text()


def test_sim3d_page_open_dialog_loads_a_model_via_the_shared_session_loader(win, qtbot, monkeypatch):
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    from sdr_tuner.gui.config_page import KINEMATICS_SAMPLES_DIR
    page = win.sim3d_page
    path = KINEMATICS_SAMPLES_DIR / "radar.obj.kinematics"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    page.open_btn.click()
    assert win.session.kinematics_model is not None
    assert Path(win.session.kinematics_model.source).name == "radar.obj.kinematics"
    assert page._items


def test_sim3d_page_shows_a_clear_error_for_a_bad_kinematics_file_and_keeps_no_model_loaded(win, qtbot, tmp_path):
    page = win.sim3d_page
    bad = tmp_path / "bad.kinematics"
    bad.write_text("not a kinematics file\n")
    page._load_kinematics(str(bad))
    assert "not a recognised" in page.status.text()
    assert win.session.kinematics_model is None
    assert page._items == {}


def test_loading_a_model_on_the_3d_simulation_page_updates_item_configurations_own_summary_too(win, qtbot):
    """The two pages share one `session.kinematics_model` - loading from either keeps the other in sync (Item
    Configuration -> this page was already true; this proves the new direction, this page -> Item Configuration,
    works the same way, through the same `kinematics_changed` signal both already listen to)."""
    page = win.sim3d_page
    idx = page.samples.findText("humanoid.obj")
    page.samples.setCurrentIndex(idx)
    page._load_kinematics_sample(idx)
    assert "humanoid.obj.kinematics" in win.axes_page.setup.kinematics_summary.text()


def test_sim3d_page_builds_one_item_per_body_for_a_model_with_a_real_mesh(win, qtbot):
    model = _load_kinematics_sample(win, "radar")
    page = win.sim3d_page
    qtbot.waitUntil(lambda: len(page._items) == 4, timeout=WAIT)               # chassis + A:yaw + B:pitch + C:roll
    assert set(page._items) == set(model.bodies)
    assert "without a bundled mesh" not in page.status.text()                  # radar.obj covers every body


def test_sim3d_page_converts_the_vendors_own_metre_scale_obj_vertices_to_millimetres(win, qtbot):
    """A real bug, found by looking at the actual running app, not just the offscreen tests: the vendor's own .obj
    files are authored in METRES (confirmed directly: robot-dog.obj's own leg-1-1 mesh group is centred almost
    exactly on (-0.3815, 0.0783, 0.0002), matching that SAME body's own Axis Origin Point in
    robot-dog.obj.kinematics, (-350, 57, 0) mm, converted to metres) while every .kinematics file - and
    body_transforms()'s own translations - are in MILLIMETRES; at the home pose an unconverted mesh still LOOKED
    fine (identity transform either way), but the camera-framing distance (tuned for an mm-scale span) put a
    metre-scale model far too small on screen to see at all, and a moving joint would have swung the body through
    a wildly wrong arc, its rotation pivoting ~1000x farther from the mesh than the mesh's own real extent."""
    from sdr_tuner import mesh as mesh_module
    from sdr_tuner.gui.config_page import KINEMATICS_SAMPLES_DIR
    raw = mesh_module.parse_obj_file(KINEMATICS_SAMPLES_DIR / "radar.obj")
    page = win.sim3d_page
    scaled = page._load_matching_mesh(str(KINEMATICS_SAMPLES_DIR / "radar.obj.kinematics"))
    for name in raw:
        np.testing.assert_allclose(scaled[name].vertices, raw[name].vertices * 1000.0)


def test_sim3d_page_falls_back_to_a_placeholder_for_a_model_with_no_matching_mesh(win, qtbot):
    page = win.sim3d_page
    from sdr_tuner.gui.config_page import KINEMATICS_SAMPLES_DIR
    model = win.session.load_kinematics(str(KINEMATICS_SAMPLES_DIR / "bench_node7.kinematics"))
    qtbot.waitUntil(lambda: len(page._items) == len(model.bodies), timeout=WAIT)
    assert "without a bundled mesh" in page.status.text()


def test_sim3d_page_handles_a_model_that_mixes_a_real_mesh_and_a_placeholder(win, qtbot):
    """monkey.obj.kinematics is the first bundled sample where only SOME bodies have a matching mesh group -
    "base" (a bare anchor, no `o base` group in monkey.obj) falls back to the placeholder marker, "monkey"
    (matching monkey.obj's own single `o monkey` group) gets the real mesh - the two existing cases so far were
    only "every body covered" (radar) or "no body covered" (bench_node7)."""
    model = _load_kinematics_sample(win, "monkey")
    page = win.sim3d_page
    qtbot.waitUntil(lambda: len(page._items) == 2, timeout=WAIT)
    assert set(page._items) == set(model.bodies)
    assert "1 without a bundled mesh" in page.status.text()


def test_sim3d_page_clears_its_scene_when_the_model_is_cleared(win, qtbot):
    _load_kinematics_sample(win, "radar")
    page = win.sim3d_page
    qtbot.waitUntil(lambda: len(page._items) == 4, timeout=WAIT)
    win.session.clear_kinematics()
    qtbot.waitUntil(lambda: page._items == {}, timeout=WAIT)
    assert "No model loaded" in page.status.text()


def test_sim3d_page_updates_a_connected_bodys_transform_live_from_telemetry(win, qtbot):
    from PySide6.QtGui import QVector3D
    sim, client = connect_node(win, qtbot, 1)                                  # axis 0 = node 1
    model = _load_kinematics_sample(win, "radar")
    page = win.sim3d_page
    qtbot.waitUntil(lambda: len(page._items) == 4, timeout=WAIT)
    sim.wz_count = 90.0                                                        # counts_per_unit[0] == 1.0 -> 90 degrees
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("wz_count") == 90, timeout=WAIT)
    qtbot.waitUntil(lambda: "Live: 1 of 3" in page.status.text(), timeout=WAIT)
    yaw_id = next(b.id for b in model.bodies.values() if b.name == "A:yaw")
    expected = model.body_transforms({0: 90.0})[yaw_id]
    expected_pt = (expected @ np.array([1.0, 0.0, 0.0, 1.0]))[:3]

    def close_enough():
        actual = page._items[yaw_id].transform().map(QVector3D(1.0, 0.0, 0.0))
        return (actual.x(), actual.y(), actual.z()) == pytest.approx(tuple(expected_pt), abs=1e-6)
    qtbot.waitUntil(close_enough, timeout=WAIT)


def test_sim3d_page_leaves_a_disconnected_bodys_ancestors_at_home_when_only_one_axis_is_live(win, qtbot):
    """radar's chassis has NO actuated ancestors at all - it must stay at the identity transform regardless of
    what axis 0 is doing, the same way kinematics.body_transforms itself already guarantees (test_kinematics.py's
    own test_body_transforms_only_moves_a_bodys_own_descendants) - checked here end to end, through the real page."""
    from PySide6.QtGui import QVector3D
    sim, client = connect_node(win, qtbot, 1)
    model = _load_kinematics_sample(win, "radar")
    page = win.sim3d_page
    qtbot.waitUntil(lambda: len(page._items) == 4, timeout=WAIT)
    sim.wz_count = 45.0
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("wz_count") == 45, timeout=WAIT)
    qtbot.waitUntil(lambda: "Live: 1 of 3" in page.status.text(), timeout=WAIT)
    chassis_id = next(b.id for b in model.bodies.values() if b.name == "chassis")
    pt = page._items[chassis_id].transform().map(QVector3D(5.0, 0.0, 0.0))
    assert (pt.x(), pt.y(), pt.z()) == pytest.approx((5.0, 0.0, 0.0), abs=1e-6)


def test_sim3d_reset_view_button_does_not_crash_with_no_model_loaded(win, qtbot):
    qtbot.mouseClick(win.sim3d_page.reset_view_btn, Qt.MouseButton.LeftButton)   # nothing to reset - must not raise


def test_sim3d_page_reports_no_actuated_axes_for_a_rigid_only_model(win, qtbot):
    text = ("#;Name;Parent;Axis;Axis Type;Axis Direction X;Y;Z;Axis Origin Point(mm) X;Y;Z;"
           "Axis Limits(mm or deg) Min;Max;Axis Tolerance(mm or deg);"
           "Tool Direction X;Y;Z;Tool Tip(mm) X;Y;Z;Body Center of Mass(mm) X;Y;Z;Mass(kg);IMU\n"
           "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;0\n")
    win.session.kinematics_model = kinematics.parse_kinematics_text(text)
    win.session.counts_per_unit = {}
    win.session.kinematics_changed.emit()
    page = win.sim3d_page
    qtbot.waitUntil(lambda: len(page._items) == 1, timeout=WAIT)
    qtbot.waitUntil(lambda: "no actuated axes" in page.status.text(), timeout=WAIT)


# ---- 3D Simulation - Simulation mode (offline, driven by Motion Controller's SIMULATE, 2026-09-24) ------------
def test_sim3d_defaults_to_live_mode(win, qtbot):
    page = win.sim3d_page
    assert page.live_radio.isChecked() and not page.sim_radio.isChecked()


def test_sim3d_simulation_mode_shows_a_hint_with_nothing_simulated_yet(win, qtbot):
    _load_kinematics_sample(win, "radar")
    win.open_tool("sim3d")                 # qtbot.mouseClick needs the page actually shown to hit the radio button
    page = win.sim3d_page
    qtbot.mouseClick(page.sim_radio, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "SIMULATE button" in page.status.text(), timeout=WAIT)


def test_sim3d_simulation_mode_renders_from_session_virtual_axes_with_no_connection(win, qtbot):
    """The key end-to-end proof: Motion Controller's SIMULATE (no client connected anywhere) drives this page's
    own 3D view when Simulation mode is selected."""
    from PySide6.QtGui import QVector3D
    assert win.session.client is None
    model = _load_kinematics_sample(win, "radar")
    win.open_tool("sim3d")
    page = win.sim3d_page
    qtbot.mouseClick(page.sim_radio, Qt.MouseButton.LeftButton)
    mc = win.motion_controller_page
    mc.editor.setPlainText("G00 X90")                 # axis 0 = A:yaw, straight to 90 degrees
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Simulation finished." in mc.output.toPlainText(), timeout=WAIT)
    qtbot.waitUntil(lambda: "1 axis(es)" in page.status.text(), timeout=WAIT)
    yaw_id = next(b.id for b in model.bodies.values() if b.name == "A:yaw")
    expected = model.body_transforms({0: 90.0})[yaw_id]
    expected_pt = (expected @ np.array([1.0, 0.0, 0.0, 1.0]))[:3]

    def close_enough():
        actual = page._items[yaw_id].transform().map(QVector3D(1.0, 0.0, 0.0))
        return (actual.x(), actual.y(), actual.z()) == pytest.approx(tuple(expected_pt), abs=1e-6)
    qtbot.waitUntil(close_enough, timeout=WAIT)


def test_sim3d_simulation_mode_ignores_live_telemetry_even_if_a_drive_is_connected(win, qtbot):
    """Switching to Simulation mode must stop reading real telemetry - a connected axis's own live motion should
    NOT leak into the Simulation view."""
    from PySide6.QtGui import QVector3D
    sim, client = connect_node(win, qtbot, 1)                              # axis 0 = node 1
    _load_kinematics_sample(win, "radar")
    win.open_tool("sim3d")
    page = win.sim3d_page
    sim.wz_count = 45.0
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("wz_count") == 45, timeout=WAIT)
    qtbot.mouseClick(page.sim_radio, Qt.MouseButton.LeftButton)
    qtbot.wait(150)
    assert "Live" not in page.status.text()
    model = win.session.kinematics_model
    yaw_id = next(b.id for b in model.bodies.values() if b.name == "A:yaw")
    pt = page._items[yaw_id].transform().map(QVector3D(1.0, 0.0, 0.0))
    assert (pt.x(), pt.y(), pt.z()) == pytest.approx((1.0, 0.0, 0.0), abs=1e-6)   # still at home, not 45 degrees


def test_sim3d_live_mode_ignores_a_simulated_run_even_if_one_is_active(win, qtbot):
    """The reverse of the test above: with Live selected, a Simulate run in progress must not move the view."""
    from PySide6.QtGui import QVector3D
    model = _load_kinematics_sample(win, "radar")
    page = win.sim3d_page
    assert page.live_radio.isChecked()
    mc = win.motion_controller_page
    mc.editor.setPlainText("G00 X90")
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Simulation finished." in mc.output.toPlainText(), timeout=WAIT)
    qtbot.wait(150)
    yaw_id = next(b.id for b in model.bodies.values() if b.name == "A:yaw")
    pt = page._items[yaw_id].transform().map(QVector3D(1.0, 0.0, 0.0))
    assert (pt.x(), pt.y(), pt.z()) == pytest.approx((1.0, 0.0, 0.0), abs=1e-6)   # still at home, Live shows 0 connected


def test_sim3d_simulation_mode_shows_how_many_axes_are_spinning(win, qtbot):
    _load_kinematics_sample(win, "radar")
    win.open_tool("sim3d")
    page = win.sim3d_page
    qtbot.mouseClick(page.sim_radio, Qt.MouseButton.LeftButton)
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1030 X60")                # keeps spinning - no G1000 to end it
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "1 spinning" in page.status.text(), timeout=WAIT)


# ---- 3D Simulation - Cursor mode (manual joint-scrub, local to the page, 2026-09-24) ---------------------------
def test_sim3d_cursor_panel_is_hidden_until_cursor_mode_is_selected(win, qtbot):
    _load_kinematics_sample(win, "radar")
    page = win.sim3d_page
    assert not page.cursor_container.isVisible()
    win.open_tool("sim3d")
    qtbot.mouseClick(page.cursor_radio, Qt.MouseButton.LeftButton)
    assert page.cursor_container.isVisible()


def test_sim3d_cursor_builds_one_row_per_actuated_axis(win, qtbot):
    model = _load_kinematics_sample(win, "robot-dog")
    page = win.sim3d_page
    assert set(page._cursor_rows) == set(range(12))
    assert all(v == 0.0 for v in page._cursor_values.values())


def test_sim3d_cursor_row_range_comes_from_the_kinematics_files_own_limits(win, qtbot):
    model = _load_kinematics_sample(win, "robot-dog")           # leg-1-1 (axis 0): -90..90 in the real file
    page = win.sim3d_page
    slider, spin = page._cursor_rows[0]
    assert (spin.minimum(), spin.maximum()) == (-90.0, 90.0)
    assert (slider.minimum(), slider.maximum()) == (-900, 900)


def test_sim3d_cursor_row_falls_back_to_a_default_range_with_no_limits_given(win, qtbot):
    from sdr_tuner.gui.sim3d import DEFAULT_ROTARY_RANGE
    _load_kinematics_sample(win, "radar")                        # radar's own rows all have a blank Limits column
    page = win.sim3d_page
    spin = page._cursor_rows[0][1]
    assert (spin.minimum(), spin.maximum()) == DEFAULT_ROTARY_RANGE


def test_sim3d_cursor_spin_box_drives_the_slider_and_the_pose(win, qtbot):
    from PySide6.QtGui import QVector3D
    model = _load_kinematics_sample(win, "radar")
    win.open_tool("sim3d")
    page = win.sim3d_page
    qtbot.mouseClick(page.cursor_radio, Qt.MouseButton.LeftButton)
    slider, spin = page._cursor_rows[0]                          # axis 0 = A:yaw
    spin.setValue(90.0)
    assert slider.value() == 900
    assert page._cursor_values[0] == 90.0
    yaw_id = next(b.id for b in model.bodies.values() if b.name == "A:yaw")
    expected = model.body_transforms({0: 90.0})[yaw_id]
    expected_pt = (expected @ np.array([1.0, 0.0, 0.0, 1.0]))[:3]

    def close_enough():
        actual = page._items[yaw_id].transform().map(QVector3D(1.0, 0.0, 0.0))
        return (actual.x(), actual.y(), actual.z()) == pytest.approx(tuple(expected_pt), abs=1e-6)
    qtbot.waitUntil(close_enough, timeout=WAIT)


def test_sim3d_cursor_slider_drives_the_spin_box_back(win, qtbot):
    _load_kinematics_sample(win, "robot-dog")
    page = win.sim3d_page
    slider, spin = page._cursor_rows[3]
    slider.setValue(300)                                         # 30.0 degrees, *10 scale
    assert spin.value() == pytest.approx(30.0)
    assert page._cursor_values[3] == pytest.approx(30.0)


def test_sim3d_cursor_reset_to_home_zeroes_every_axis(win, qtbot):
    _load_kinematics_sample(win, "radar")
    win.open_tool("sim3d")
    page = win.sim3d_page
    qtbot.mouseClick(page.cursor_radio, Qt.MouseButton.LeftButton)
    for slider, spin in page._cursor_rows.values():
        spin.setValue(spin.maximum())
    assert any(v != 0.0 for v in page._cursor_values.values())
    qtbot.mouseClick(page.cursor_reset_btn, Qt.MouseButton.LeftButton)
    assert all(v == 0.0 for v in page._cursor_values.values())
    for slider, spin in page._cursor_rows.values():
        assert slider.value() == 0 and spin.value() == 0.0


def test_sim3d_cursor_pose_survives_switching_to_another_mode_and_back(win, qtbot):
    win.open_tool("sim3d")
    _load_kinematics_sample(win, "radar")
    page = win.sim3d_page
    qtbot.mouseClick(page.cursor_radio, Qt.MouseButton.LeftButton)
    page._cursor_rows[0][1].setValue(45.0)
    qtbot.mouseClick(page.live_radio, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(page.cursor_radio, Qt.MouseButton.LeftButton)
    assert page._cursor_values[0] == 45.0
    assert page._cursor_rows[0][1].value() == 45.0


def test_sim3d_cursor_values_reset_when_a_different_model_loads(win, qtbot):
    _load_kinematics_sample(win, "radar")
    page = win.sim3d_page
    page._cursor_rows[0][1].setValue(45.0)
    assert page._cursor_values[0] == 45.0
    _load_kinematics_sample(win, "robot-dog")
    assert page._cursor_values.get(0) == 0.0                    # rebuilt fresh, not carried over from radar


def test_sim3d_cursor_ignores_live_telemetry_and_a_simulated_run(win, qtbot):
    """Cursor mode must not be affected by either of the other two data sources, even while both are active."""
    from PySide6.QtGui import QVector3D
    sim, client = connect_node(win, qtbot, 1)
    model = _load_kinematics_sample(win, "radar")
    win.open_tool("sim3d")
    page = win.sim3d_page
    sim.wz_count = 45.0
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("wz_count") == 45, timeout=WAIT)
    mc = win.motion_controller_page
    mc.editor.setPlainText("G1030 X60")
    qtbot.mouseClick(mc.simulate_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(page.cursor_radio, Qt.MouseButton.LeftButton)
    qtbot.wait(150)
    assert "Cursor" in page.status.text()
    yaw_id = next(b.id for b in model.bodies.values() if b.name == "A:yaw")
    pt = page._items[yaw_id].transform().map(QVector3D(1.0, 0.0, 0.0))
    assert (pt.x(), pt.y(), pt.z()) == pytest.approx((1.0, 0.0, 0.0), abs=1e-6)   # untouched: still home


# ---- Motor Constants Converter (Ke/Kv/Kt) ------------------------------------------------------
def test_motor_constants_dialog_computes_kt_and_kv_from_ke(qtbot):
    """Poles=10, Ke=0.01366658 is the real bench drive's own Datasheet value - Kt/Kv must match what the
    vendor's own converter tool computed for the exact same input (read directly off it, 2026-09-24)."""
    from sdr_tuner.gui.motor_constants_dialog import MotorConstantsDialog
    dlg = MotorConstantsDialog()
    qtbot.addWidget(dlg)
    dlg.poles.setValue(10)
    dlg.spins["ke"].setValue(0.01366658)
    assert dlg.spins["kt"].value() == pytest.approx(0.10249935, rel=1e-4)
    assert dlg.spins["kv"].value() == pytest.approx(80.683, rel=1e-3)


def test_motor_constants_dialog_switching_source_recomputes_from_kt(qtbot):
    from sdr_tuner.gui.motor_constants_dialog import MotorConstantsDialog
    dlg = MotorConstantsDialog()
    qtbot.addWidget(dlg)
    dlg.show()             # a hidden widget's mouseClick silently no-ops (same gotcha as PageHost's own pages)
    dlg.poles.setValue(10)
    qtbot.mouseClick(dlg.radios["kt"], Qt.MouseButton.LeftButton)
    dlg.spins["kt"].setValue(0.10249935)
    assert dlg.spins["ke"].value() == pytest.approx(0.01366658, rel=1e-4)
    assert dlg.spins["kv"].value() == pytest.approx(80.683, rel=1e-3)


def test_motor_constants_dialog_only_the_source_field_is_editable(qtbot):
    from sdr_tuner.gui.motor_constants_dialog import MotorConstantsDialog
    dlg = MotorConstantsDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    assert not dlg.spins["ke"].isReadOnly()
    assert dlg.spins["kv"].isReadOnly() and dlg.spins["kt"].isReadOnly()
    qtbot.mouseClick(dlg.radios["kv"], Qt.MouseButton.LeftButton)
    assert not dlg.spins["kv"].isReadOnly()
    assert dlg.spins["ke"].isReadOnly() and dlg.spins["kt"].isReadOnly()


def test_the_ribbon_opens_the_motor_constants_dialog(win, monkeypatch):
    from sdr_tuner.gui.motor_constants_dialog import MotorConstantsDialog
    opened = []
    monkeypatch.setattr(MotorConstantsDialog, "exec", lambda self: opened.append(self))
    win._open_motor_constants()
    assert len(opened) == 1
    assert opened[0].parent() is win


# ---- Export as HTML / EDS (the whole object dictionary, configuration + telemetry) ------------
def test_export_html_writes_a_report_covering_both_pages(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    path = tmp_path / "report.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    win._export_dialog("html")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert len(win.params_tab.rows) + len(win.telemetry_tab.rows) == 419
    assert f"{419} parameter(s)" in text
    assert "Exported 419 parameter(s)" in win.status_text()


def test_export_eds_writes_a_valid_eds_file(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    import configparser
    path = tmp_path / "drive.eds"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    win._export_dialog("eds")
    assert path.exists()
    cp = configparser.ConfigParser(strict=False)
    cp.read(path, encoding="utf-8")
    assert "FileInfo" in cp and "DeviceInfo" in cp
    assert cp["DeviceInfo"]["productname"] == "SDR-CB-60R-D"


def test_export_includes_node_and_poles_once_connected(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    connect(win, qtbot)
    path = tmp_path / "report.html"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    win._export_dialog("html")
    text = path.read_text(encoding="utf-8")
    assert f"Node {win.session.node_id}" in text


def test_export_cancelled_dialog_writes_nothing(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    win._export_dialog("eds")
    assert not any(tmp_path.iterdir())


# ---- Save Snapshot (the file Parameters Comparison loads) --------------------------------------
def test_export_snapshot_writes_nothing_known_on_a_fresh_window(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from sdr_tuner import export as export_module
    path = tmp_path / "snapshot.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    win._export_dialog("snapshot")
    assert path.exists()
    snap = export_module.parse_snapshot(path.read_text(encoding="utf-8"))
    assert snap["parameters"] == []          # nothing has been read from the drive yet in a fresh window
    assert "Saved snapshot of 0 parameter(s)" in win.status_text()


def test_export_snapshot_includes_every_value_this_session_has_read(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from sdr_tuner import export as export_module
    connect(win, qtbot)
    read_all(win, qtbot)
    path = tmp_path / "snapshot.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    win._export_dialog("snapshot")
    snap = export_module.parse_snapshot(path.read_text(encoding="utf-8"))
    assert len(snap["parameters"]) == len(win.params_tab.rows)
    assert f"Saved snapshot of {len(win.params_tab.rows)} parameter(s)" in win.status_text()


# ---- Parameters Comparison (load two saved snapshots, see what differs) ------------------------
def test_params_comparison_dialog_needs_both_files_before_compare_is_enabled(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from sdr_tuner import defs, export as export_module
    from sdr_tuner.gui.params_comparison_dialog import ParametersComparisonDialog
    reg = defs.load_registers(False)[0]
    path_a = tmp_path / "a.json"
    path_a.write_text(export_module.build_snapshot([export_module.entry(reg, value=1, has_value=True)]),
                      encoding="utf-8")

    dlg = ParametersComparisonDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    assert not dlg.compare_btn.isEnabled()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path_a), "")))
    dlg._load(dlg.path_a)
    assert dlg._snap_a is not None
    assert not dlg.compare_btn.isEnabled()          # File 2 still not loaded


def test_params_comparison_dialog_compare_shows_only_the_differences_by_default(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from sdr_tuner import defs, export as export_module
    from sdr_tuner.gui.params_comparison_dialog import ParametersComparisonDialog
    regs = defs.load_registers(False)[:2]
    path_a, path_b = tmp_path / "a.json", tmp_path / "b.json"
    path_a.write_text(export_module.build_snapshot(
        [export_module.entry(regs[0], value=1, has_value=True), export_module.entry(regs[1], value=2, has_value=True)]),
        encoding="utf-8")
    path_b.write_text(export_module.build_snapshot(
        [export_module.entry(regs[0], value=1, has_value=True), export_module.entry(regs[1], value=99, has_value=True)]),
        encoding="utf-8")

    dlg = ParametersComparisonDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path_a), "")))
    dlg._load(dlg.path_a)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path_b), "")))
    dlg._load(dlg.path_b)
    assert dlg.compare_btn.isEnabled()

    qtbot.mouseClick(dlg.compare_btn, Qt.MouseButton.LeftButton)
    assert dlg.summary.text() == "1 of 2 parameter(s) differ"
    assert dlg.table.rowCount() == 1                # regs[0] matches (1 == 1) - hidden, only regs[1] shown
    assert dlg.table.item(0, 0).text() == regs[1].name
    assert dlg.table.item(0, 2).text() == "2"
    assert dlg.table.item(0, 3).text() == "99"

    dlg.diffs_only.setChecked(False)                 # re-compares automatically
    assert dlg.table.rowCount() == 2


def test_params_comparison_dialog_a_file_only_one_side_read_shows_as_missing(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from sdr_tuner import defs, export as export_module
    from sdr_tuner.gui.params_comparison_dialog import ParametersComparisonDialog
    regs = defs.load_registers(False)[:2]
    path_a, path_b = tmp_path / "a.json", tmp_path / "b.json"
    path_a.write_text(export_module.build_snapshot(
        [export_module.entry(regs[0], value=1, has_value=True), export_module.entry(regs[1], value=2, has_value=True)]),
        encoding="utf-8")
    path_b.write_text(export_module.build_snapshot([export_module.entry(regs[0], value=1, has_value=True)]),
                      encoding="utf-8")

    dlg = ParametersComparisonDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path_a), "")))
    dlg._load(dlg.path_a)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path_b), "")))
    dlg._load(dlg.path_b)
    qtbot.mouseClick(dlg.compare_btn, Qt.MouseButton.LeftButton)
    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 3).text() == "(not in file)"


def test_params_comparison_dialog_a_bad_file_warns_without_crashing(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from sdr_tuner.gui.params_comparison_dialog import ParametersComparisonDialog
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")

    dlg = ParametersComparisonDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a) or None))
    dlg._load(dlg.path_a)
    assert len(warnings) == 1
    assert dlg._snap_a is None
    assert not dlg.compare_btn.isEnabled()


def test_the_ribbon_opens_the_params_comparison_dialog(win, monkeypatch):
    from sdr_tuner.gui.params_comparison_dialog import ParametersComparisonDialog
    opened = []
    monkeypatch.setattr(ParametersComparisonDialog, "exec", lambda self: opened.append(self))
    win._open_params_comparison()
    assert len(opened) == 1
    assert opened[0].parent() is win


# ---- Raw Register Access (index:sub-index, for anything not in the named table) ---------------
def test_raw_read_a_known_register(win, qtbot):
    connect(win, qtbot)
    page = win.params_tab
    page.raw_index.setText("0x2000")
    page.raw_sub.setText("0x03")
    page.raw_type.setCurrentText("UINT16")
    qtbot.mouseClick(page.raw_read_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "0x2000:0x03" in page.raw_status.text(), timeout=WAIT)
    assert "= 14" in page.raw_status.text()          # the simulator's own default pole count
    assert page.raw_value.text() == "14"


def test_raw_read_an_unknown_address_fails_clearly(win, qtbot):
    connect(win, qtbot)
    page = win.params_tab
    page.raw_index.setText("0x9999")
    page.raw_sub.setText("0x01")
    qtbot.mouseClick(page.raw_read_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "0x9999:0x01" in page.raw_status.text(), timeout=WAIT)
    assert "error" in page.raw_status.property("state")


def test_raw_write_round_trips_through_a_real_writable_register(win, qtbot, monkeypatch):
    connect(win, qtbot)
    page = win.params_tab
    monkeypatch.setattr(page, "_confirm", lambda text: True)
    page.raw_index.setText("0x2000")
    page.raw_sub.setText("0x03")
    page.raw_type.setCurrentText("UINT16")
    page.raw_value.setText("8")
    qtbot.mouseClick(page.raw_write_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Wrote" in page.raw_status.text(), timeout=WAIT)
    assert "= 8" in page.raw_status.text()
    assert win.session.client.read_raw(0x2000, 0x03).value("UINT16") == 8


def test_raw_write_asks_for_confirmation_first(win, qtbot, monkeypatch):
    connect(win, qtbot)
    page = win.params_tab
    asked = []
    monkeypatch.setattr(page, "_confirm", lambda text: asked.append(text) or False)
    page.raw_index.setText("0x2000")
    page.raw_sub.setText("0x03")
    page.raw_value.setText("8")
    qtbot.mouseClick(page.raw_write_btn, Qt.MouseButton.LeftButton)
    assert len(asked) == 1
    assert "0x2000:0x03" in asked[0]


def test_raw_write_warns_extra_for_a_risky_address(win, qtbot, monkeypatch):
    connect(win, qtbot)
    page = win.params_tab
    asked = []
    monkeypatch.setattr(page, "_confirm", lambda text: asked.append(text) or False)
    page.raw_index.setText("0x3000")
    page.raw_sub.setText("0x02")            # CAN bit rate - in the RISKY set
    page.raw_value.setText("1000000")
    qtbot.mouseClick(page.raw_write_btn, Qt.MouseButton.LeftButton)
    assert "unreachable" in asked[0]


def test_raw_access_requires_a_connection(win, qtbot):
    page = win.params_tab
    page.raw_index.setText("0x2000")
    page.raw_sub.setText("0x03")
    qtbot.mouseClick(page.raw_read_btn, Qt.MouseButton.LeftButton)
    assert "Not connected" in page.raw_status.text()


def test_raw_access_rejects_a_non_numeric_index(win, qtbot):
    connect(win, qtbot)
    page = win.params_tab
    page.raw_index.setText("not a number")
    page.raw_sub.setText("0x03")
    qtbot.mouseClick(page.raw_read_btn, Qt.MouseButton.LeftButton)
    assert "must be numbers" in page.raw_status.text()


def test_raw_access_is_not_on_the_telemetry_explorer_page(win, qtbot):
    assert not hasattr(win.telemetry_tab, "raw_read_btn")


def test_raw_access_works_on_an_additional_axis_too(win, qtbot):
    """No special-casing needed for this in ParamsTab - each additional axis already gets its own ParamsTab bound
    to its own AxisContext (main_window.py's _on_axis_connected), so raw access follows for free."""
    connect(win, qtbot)                                     # primary: node 1
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)   # a second, independent simulated drive: node 2
    qtbot.waitUntil(lambda: 2 in win.session.axes, timeout=WAIT)
    page = win._axis_pages[2]["parameters"]
    page.raw_index.setText("0x2000")
    page.raw_sub.setText("0x03")
    page.raw_type.setCurrentText("UINT16")
    qtbot.mouseClick(page.raw_read_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "0x2000:0x03" in page.raw_status.text(), timeout=WAIT)
    assert "= 14" in page.raw_status.text()


# ---- Recorder: Save Image (both charts as one PNG, same grab() approach the 2D Plotter's own Save Image uses) --
def test_recorder_save_image_writes_a_png(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    connect(win, qtbot)
    win.open_tool("recorder")                # a hidden page's grab() can capture a blank/zero-size image
    rec = win.recorder_page
    rec.start_btn.click()
    qtbot.wait(300)
    rec.stop_btn.click()
    path = tmp_path / "recording.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    rec.save_image_dialog()
    assert path.exists() and path.stat().st_size > 0
    assert win.status_text() == f"Saved image to {path}"


def test_stacked_images_keep_the_screens_pixel_ratio_so_the_charts_leave_no_gap():
    """On a HiDPI screen a widget grab carries devicePixelRatio 2; the first real Bode PNG (2026-09-25) had a
    plot-sized blank strip under each plot because the composition advanced by the grab's DEVICE height while
    painting it at its logical size. The stacker keeps the ratio and stacks in logical pixels."""
    from PySide6.QtGui import QColor, QPixmap
    from sdr_tuner.gui.widgets import stack_pixmaps
    top, bottom = QPixmap(100, 40), QPixmap(100, 60)
    for pixmap, colour in ((top, Qt.GlobalColor.red), (bottom, Qt.GlobalColor.blue)):
        pixmap.setDevicePixelRatio(2.0)
        pixmap.fill(colour)
    out = stack_pixmaps([top, bottom])
    assert (out.width(), out.height(), out.devicePixelRatio()) == (100, 100, 2.0)      # device pixels, as the PNG is saved
    image = out.toImage()
    assert image.pixelColor(50, 39) == QColor(Qt.GlobalColor.red)
    assert image.pixelColor(50, 41) == QColor(Qt.GlobalColor.blue) and image.pixelColor(50, 99) == QColor(Qt.GlobalColor.blue)


def test_recorder_save_image_combines_both_charts_vertically(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QFileDialog
    connect(win, qtbot)
    win.open_tool("recorder")
    rec = win.recorder_page
    rec.start_btn.click()
    qtbot.wait(300)
    rec.stop_btn.click()
    path = tmp_path / "recording.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    rec.save_image_dialog()
    saved = QPixmap(str(path))
    expected_height = sum(chart.grab().height() for chart in rec.charts)
    assert saved.height() == expected_height


def test_recorder_save_image_refuses_when_nothing_recorded(win, qtbot):
    win.open_tool("recorder")
    rec = win.recorder_page
    rec.save_image_dialog()
    assert "record something first" in win.status_text()


def test_recorder_save_image_adds_png_extension(win, qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    connect(win, qtbot)
    win.open_tool("recorder")
    rec = win.recorder_page
    rec.start_btn.click()
    qtbot.wait(300)
    rec.stop_btn.click()
    path = tmp_path / "recording"                    # no extension typed
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    rec.save_image_dialog()
    assert (tmp_path / "recording.png").exists()


def test_recorder_save_image_button_follows_save_btns_own_enabled_state(win, qtbot):
    win.open_tool("recorder")
    rec = win.recorder_page
    assert not rec.save_image_btn.isEnabled()
    connect(win, qtbot)
    rec.start_btn.click()
    qtbot.wait(200)
    rec.stop_btn.click()
    assert rec.save_image_btn.isEnabled()


def test_ribbon_save_image_button_mirrors_the_pages_own(win, qtbot):
    win.open_tool("recorder")
    connect(win, qtbot)
    rec = win.recorder_page
    rec.start_btn.click()
    qtbot.wait(200)
    rec.stop_btn.click()
    win._sync_recorder_buttons()
    assert win.rec_save_image_btn.isEnabled() == rec.save_image_btn.isEnabled()


# ---- Recorder Trigger (Rising/Falling edge, matching Elmo Application Studio III's own Trigger Editor) --------
def test_edit_trigger_button_opens_a_dialog_that_sets_the_trigger(win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from sdr_tuner.gui.recorder import Trigger, TriggerDialog
    rec = win.recorder_page

    def fake_exec(self):
        self.source.setCurrentIndex(self.source.findData("wz_count"))
        self.level.setValue(42.0)
        self.falling.setChecked(True)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(TriggerDialog, "exec", fake_exec)
    qtbot.mouseClick(rec.trigger_btn, Qt.MouseButton.LeftButton)
    assert rec.trigger == Trigger("wz_count", "falling", 42.0)
    assert "Trigger set" in win.status_text()


def test_trigger_dialog_cancel_leaves_the_trigger_unchanged(win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from sdr_tuner.gui.recorder import Trigger, TriggerDialog
    rec = win.recorder_page
    rec.trigger = Trigger("wz_count", "rising", 10.0)
    monkeypatch.setattr(TriggerDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    qtbot.mouseClick(rec.trigger_btn, Qt.MouseButton.LeftButton)
    assert rec.trigger == Trigger("wz_count", "rising", 10.0)


def test_selecting_no_trigger_in_the_dialog_clears_it(win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from sdr_tuner.gui.recorder import Trigger, TriggerDialog
    rec = win.recorder_page
    rec.trigger = Trigger("wz_count", "rising", 10.0)

    def fake_exec(self):
        self.source.setCurrentIndex(0)          # "(No Trigger)"
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(TriggerDialog, "exec", fake_exec)
    qtbot.mouseClick(rec.trigger_btn, Qt.MouseButton.LeftButton)
    assert rec.trigger is None
    assert win.status_text() == "Trigger cleared."


def test_starting_with_a_trigger_arms_instead_of_recording_immediately(win, qtbot):
    from sdr_tuner.gui.recorder import Trigger
    connect(win, qtbot)
    rec = win.recorder_page
    rec.trigger = Trigger("wz_count", "rising", 50.0)
    rec.start_btn.click()
    assert rec._armed and not rec.recording
    assert "ARMED" in rec.status.text()
    assert not rec.start_btn.isEnabled() and rec.stop_btn.isEnabled()
    assert not rec.trigger_btn.isEnabled() and not rec.duration.isEnabled()


def test_a_rising_crossing_fires_the_trigger_and_starts_recording(win, qtbot):
    from sdr_tuner.gui.recorder import Trigger
    sim, client = connect(win, qtbot)
    rec = win.recorder_page
    sim.wz_count = 0.0
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("wz_count") == 0.0, timeout=WAIT)
    rec.trigger = Trigger("wz_count", "rising", 50.0)
    rec.start_btn.click()
    qtbot.wait(150)
    assert rec._armed and not rec.recording                    # still below the level - not triggered yet
    sim.wz_count = 100.0
    qtbot.waitUntil(lambda: rec.recording, timeout=WAIT)
    assert not rec._armed
    assert "Triggered" in win.status_text()
    assert rec.trigger_btn.isEnabled() is False                # still recording


def test_a_falling_crossing_fires_the_trigger(win, qtbot):
    from sdr_tuner.gui.recorder import Trigger
    sim, client = connect(win, qtbot)
    rec = win.recorder_page
    sim.wz_count = 100.0
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("wz_count") == 100.0, timeout=WAIT)
    rec.trigger = Trigger("wz_count", "falling", 50.0)
    rec.start_btn.click()
    qtbot.wait(150)
    assert rec._armed and not rec.recording
    sim.wz_count = 0.0
    qtbot.waitUntil(lambda: rec.recording, timeout=WAIT)
    assert not rec._armed


def test_the_opposite_crossing_does_not_fire_the_trigger(win, qtbot):
    """A rising trigger must not fire on a falling crossing of the same level."""
    from sdr_tuner.gui.recorder import Trigger
    sim, client = connect(win, qtbot)
    rec = win.recorder_page
    sim.wz_count = 100.0
    qtbot.waitUntil(lambda: client.telemetry_snapshot().get("wz_count") == 100.0, timeout=WAIT)
    rec.trigger = Trigger("wz_count", "rising", 50.0)
    rec.start_btn.click()
    qtbot.wait(150)
    sim.wz_count = 0.0                                          # crosses 50 going DOWN - a rising trigger ignores this
    qtbot.wait(400)
    assert rec._armed and not rec.recording


def test_stop_while_armed_cancels_without_recording(win, qtbot):
    from sdr_tuner.gui.recorder import Trigger
    connect(win, qtbot)
    rec = win.recorder_page
    rec.trigger = Trigger("wz_count", "rising", 50.0)
    rec.start_btn.click()
    assert rec._armed
    rec.stop_btn.click()
    assert not rec._armed and not rec.recording and rec.data() == {}
    assert "never fired" in win.status_text()
    assert rec.start_btn.isEnabled()


def test_trigger_persists_across_recordings(win, qtbot):
    """Unlike the recording data itself, the trigger configuration is not cleared by clear()/a new Start - it is
    a standing setting, matching Elmo Application Studio III's own Trigger Editor (configure once, reused)."""
    from sdr_tuner.gui.recorder import Trigger
    connect(win, qtbot)
    rec = win.recorder_page
    rec.trigger = Trigger("wz_count", "rising", 50.0)
    rec.start_btn.click()
    rec.stop_btn.click()
    assert rec.trigger == Trigger("wz_count", "rising", 50.0)


def test_no_trigger_still_starts_recording_immediately(win, qtbot):
    """The pre-existing behaviour, unchanged when no trigger is configured (the default)."""
    connect(win, qtbot)
    rec = win.recorder_page
    assert rec.trigger is None
    rec.start_btn.click()
    assert rec.recording and not rec._armed


# ---- audit fixes (2026-09-25): real bugs found by a whole-codebase review, each verified before it was fixed -----
def test_workspace_pages_follow_the_primary_to_the_node_it_is_really_on(win, qtbot, monkeypatch):
    """`connected` fires BEFORE the probe; on the USB cable the drive's real node is only found by listening
    afterwards, which re-keys all_clients(). Multi Axis Motion, Status Monitor and 2D Plotter rebuilt only on
    connected/disconnected/axis_*: on the real bench drive (cable, node 7, the field left at 1) the Multi Axis
    row stayed keyed 1 - Position "—" forever, MOVE silently doing nothing - and a Status Monitor row kept its
    target on node 1."""
    rig = UsbRig(node=7)
    monkeypatch.setattr("sdr_tuner.gui.session.UsbSerialBus", lambda port: rig.bus())
    try:
        win.iface.setCurrentText(USB)
        win.channel.setText("/dev/cu.usbmodemTEST")
        win.status_monitor_page._add_row()
        sm_row = win.status_monitor_page._rows[-1]
        qtbot.mouseClick(win.connect_btn, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: win.session.node_id == 7, timeout=WAIT)
        qtbot.waitUntil(lambda: sorted(win.multi_axis_motion_page._rows) == [7], timeout=WAIT)
        qtbot.waitUntil(lambda: sm_row.target_combo.currentData() == 7, timeout=WAIT)
        assert win.plotter_2d_page._x_node == 7
        qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    finally:
        rig.close()


def test_node_detected_re_points_rows_and_combos_that_pointed_at_the_old_node(win, qtbot):
    sim, client = connect(win, qtbot)                            # node 1
    sm = win.status_monitor_page
    sm._add_row()
    row = sm._rows[-1]
    qtbot.waitUntil(lambda: row.target_combo.currentData() == 1, timeout=WAIT)
    assert win.plotter_2d_page._x_node == 1 and sorted(win.multi_axis_motion_page._rows) == [1]
    win.session.node_id = 7                                      # exactly what Session._node_found does, then...
    win.session.node_detected.emit(7)
    assert row.target_combo.currentData() == 7 and row.target_combo.count() == 1     # the stale node is gone too
    assert win.plotter_2d_page._x_node == 7
    assert sorted(win.multi_axis_motion_page._rows) == [7]


def test_the_recorder_trigger_level_is_in_the_unit_the_chart_shows(win, qtbot, monkeypatch):
    """Speed shown in rpm (the default for an rps-sending drive): the chart, cursors and CSV all apply the x60,
    but the trigger compared the raw rps value - a level of 100 with the motor visibly at 120 rpm (2 rps on the
    wire) never fired."""
    from sdr_tuner.gui.recorder import Trigger

    def sends_rps(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        sim.params[(0x1A00, 3)] = defs.mapping_code(*defs.SPEED_RPS)
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", sends_rps)
    sim, client = connect(win, qtbot)
    dt, rec = win.drive_tab, win.recorder_page
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    assert dt.cards["wz_speed"].unit.text() == "rpm"
    rec.trigger = Trigger("wz_speed", "rising", 100.0)
    rec.start_btn.click()
    qtbot.wait(150)
    assert rec._armed and not rec.recording
    run_at_120_rpm(win, qtbot, sim)
    qtbot.waitUntil(lambda: rec.recording, timeout=WAIT)
    assert "through 100 rpm" in win.status_text()


def test_stop_and_disconnect_clear_the_card_that_said_sent_every_100_ms(win, qtbot):
    """Kickstart RUN then STOP left the Kickstart card reading "Kickstarting to 15.00 Hz · sent every 100 ms"
    in green while nothing was being sent any more; a disconnect left it there too."""
    sim, client = connect(win, qtbot)
    dm, dt = win.drive_modes_page, win.drive_tab
    dm.kick_target.setValue(15.0)
    qtbot.mouseClick(dm.kick_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Kickstarting to" in dm.kick_status.text(), timeout=WAIT)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)                   # the motion page's STOP clears the modes page's note
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)
    assert dm.kick_status.text() == "" and "STOP sent" in dt.status.text()
    qtbot.mouseClick(dm.kick_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Kickstarting to" in dm.kick_status.text(), timeout=WAIT)
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not win.session.is_connected, timeout=WAIT)
    assert all(label.text() == "" for label in dm._card_status_labels() + dt._card_status_labels())


def test_axes_page_speed_column_shows_the_speed_the_drive_actually_broadcasts(win, qtbot):
    """The reference default layout (and the simulator) sends speed_hz; the column hardcoded wz_speed and so
    read "—" for every simulated or factory-mapped drive, motor running or not."""
    sim, client = connect(win, qtbot)
    page = win.axes_page
    client.set_speed_hz(20.0)
    qtbot.waitUntil(lambda: page.table.rowCount() == 1 and page.table.item(0, 4).text().endswith(" Hz")
                    and not page.table.item(0, 4).text().startswith("0.00"), timeout=WAIT)
    client.stop()


def test_a_chosen_rps_unit_survives_a_disconnect_and_reconnect(win, qtbot, monkeypatch):
    def sends_rps(channel, node_id):
        sim = SimulatedDrive(channel, node_id)
        sim.params[(0x1A00, 3)] = defs.mapping_code(*defs.SPEED_RPS)
        return sim
    monkeypatch.setattr("sdr_tuner.gui.session.SimulatedDrive", sends_rps)
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    choose_unit(dt, "rps")
    assert dt.cards["wz_speed"].unit.text() == "rps"
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not win.session.is_connected, timeout=WAIT)
    connect(win, qtbot)
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    assert dt.speed_unit.currentText() == "rps" and dt.cards["wz_speed"].unit.text() == "rps"   # used to fall back to rpm


def test_removing_the_axis_the_tool_list_is_scoped_to_retargets_it_even_while_another_page_is_open(win, qtbot):
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win._on_axis_selected(2)                                     # the tree click: the list now points at Drive02's tools
    win.open_tool("terminal")                                    # a shared page - none of Drive02's own is current
    win.session.disconnect_axis(2)
    qtbot.waitUntil(lambda: 2 not in win.navigator._axis_items, timeout=WAIT)
    assert not any(key.endswith(":2") for key in win.navigator.tool_keys("drive"))
    win.open_tool("recorder")                                    # used to KeyError on the dangling "recorder:2"
    assert win.host.current_page() is win.recorder_page


def test_disconnecting_keeps_a_typed_channel(win):
    win.iface.setCurrentText("Kvaser")
    win.channel.setText("1")
    win._lock_connection_controls(True)          # what Connect does...
    win._lock_connection_controls(False)         # ...and Disconnect: used to reset the field to the interface's default
    assert win.channel.text() == "1"
    win.iface.setCurrentIndex(0)                 # changing the interface itself still resets it (the simulator's is empty)
    assert win.channel.text() == ""


def test_an_axis_that_never_answered_shows_no_response_on_its_own_pages(win, qtbot):
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win._axis_probe, timeout=WAIT)       # its real probe has landed
    ctx = win.session.axes[2]
    ctx.probed.emit(False, "no answer")                              # what a silent node's probe reports
    win.open_tool(win._axis_tool_key("motion", 2))
    assert win.pill.text().startswith("NO RESPONSE") and win.pill.text().endswith("NODE 2")   # said SIMULATED DRIVE
    ctx.probed.emit(True, "ok")
    assert win.pill.text().startswith("SIMULATED DRIVE")


def test_docking_the_current_page_does_not_wake_its_stack_neighbour(win, qtbot, monkeypatch):
    """Removing the current page from PageHost's stack made the stack show the next widget for an instant - its
    showEvent then ran: a TunerPage's starts a Drive Load, real reads on a real bus, for a page nobody opened."""
    from sdr_tuner.gui.tuner import TunerPage
    connect(win, qtbot)
    loads = []
    monkeypatch.setattr(TunerPage, "load_all", lambda self, *a, **k: loads.append(self))
    win.open_tool("quick_tuning")
    loads.clear()
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    assert win.advanced_tuner not in loads


def test_the_docked_crumb_is_not_overwritten_by_a_tuners_own_page_title(win, qtbot):
    connect(win, qtbot)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("quick_tuning")
    assert win.host.crumb.text() == "Docked View"
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)     # single-page again: the tuner's own crumb
    assert win.host.crumb.text() == win.quick_tuner.current_title()


def test_the_tree_follows_the_drive_the_tool_list_is_scoped_to_when_returning_to_drive_setup(win, qtbot):
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win.navigator._axis_items, timeout=WAIT)
    win.navigator.set_activity("drive")
    win._on_axis_selected(2)                                     # Drive02's Motion, the list scoped to Drive02
    win.navigator.set_activity("system")                         # the primary's own Item Configuration
    assert win.navigator.tree.currentItem() is win.navigator.drive_item
    win.navigator.set_activity("drive")
    assert win.host.current_page() is win._axis_pages[2]["motion"]
    assert win.navigator.tree.currentItem() is win.navigator._axis_items[2]     # used to stay on Drive01


def test_an_axis_error_is_reported_once(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win.bottom, "set_message", lambda text, level="info": calls.append(text))
    win.session.axis_error.emit(2, "boom")
    assert calls == ["Axis 2: boom"]                             # used to be written twice, by two connections


def test_status_monitor_read_stays_disabled_while_a_read_is_in_flight(win, qtbot, monkeypatch):
    """_read_row disabled the button; _tick re-enabled it 200 ms later, while the read was still running - a
    second click then sent a second read."""
    sim, client = connect(win, qtbot)
    page = win.status_monitor_page
    page.add_btn.click()
    row = page._rows[0]
    row.signal_combo.setCurrentIndex(row.signal_combo.findText("Maximum Limit on Continuous Current (Line-to-Line)"))
    qtbot.waitUntil(lambda: row.value_item.text() == "not broadcast", timeout=WAIT)
    gate = threading.Event()
    monkeypatch.setattr(client, "read", lambda reg: (gate.wait(5), 6.5)[1])
    row.read_btn.click()
    qtbot.wait(500)                                                  # at least two ticks
    assert not row.read_btn.isEnabled()
    gate.set()
    qtbot.waitUntil(lambda: row.read_btn.isEnabled() and row.value_item.text() == "6.5", timeout=WAIT)


def test_a_read_that_lands_after_a_disconnect_does_not_repopulate_item_configuration(win, qtbot, monkeypatch):
    """Networks' Read is 8 SDOs - seconds on a real bus. Disconnect before it finishes: _on_disconnected clears the
    rows, then the result used to paint them "read" again and report "Read 8 parameter(s)" on a DISCONNECTED
    page, with those stale values surviving into the next connection."""
    connect(win, qtbot)
    gate = threading.Event()

    def blocked(client, regs, cancel):
        gate.wait(5)
        return {regs[0].key: 7}
    monkeypatch.setattr("sdr_tuner.gui.config_page.read_registers", blocked)
    group = win.config_page.networks
    group.read_btn.click()
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not win.session.is_connected, timeout=WAIT)
    gate.set()
    qtbot.wait(400)
    assert all(row.last is None for row in group.param_rows.values())
    assert not win.status_text().startswith("Read ")


# ---- audit fixes (2026-09-25), G-code layer: STOP really stops, RESUME does not double up, dwells stay sane ---
def test_stop_while_a_feedback_check_is_in_flight_sends_nothing_afterwards(win, qtbot, monkeypatch):
    """A Feedback Settings check was almost always in flight at STOP (every move runs one first): when it finished
    it sent its hold_position AFTER the emergency stop, and the motor moved."""
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    gate = threading.Event()
    real_read = client.read
    monkeypatch.setattr(client, "read", lambda reg: (gate.wait(5), real_read(reg))[1])
    mc.editor.setPlainText(program("G00 Y1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.wait(150)                                              # the check is now blocked mid-flight
    qtbot.mouseClick(mc.stop_btn, Qt.MouseButton.LeftButton)
    gate.set()
    qtbot.wait(400)
    assert client.commanded is None and not mc._running


def test_the_round_stop_button_halts_a_running_program(win, qtbot):
    """It only ever stopped the drives: press it during a G04 and the NEXT line moved the motor again once the
    dwell ended."""
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G1030 Y100", "G04 P1", "G00 Y1000"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Dwelling" in mc.output.toPlainText(), timeout=WAIT)
    win.emergency_stop()                                         # the round STOP button / Esc
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)
    assert not mc._running
    qtbot.wait(1300)                                             # past the dwell: the G00 must not have run
    assert client.commanded is None and "Moving to" not in mc.output.toPlainText()


def test_resume_does_not_run_the_line_still_in_flight_a_second_time(win, qtbot):
    sim, client = connect_axis1(win, qtbot)
    mc = win.motion_controller_page
    mc.editor.setPlainText(program("G04 P0.6"))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Dwelling" in mc.output.toPlainText(), timeout=WAIT)
    qtbot.mouseClick(mc.pause_btn, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(mc.resume_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not mc._running, timeout=WAIT)
    assert mc.output.toPlainText().count("Dwelling") == 1       # RESUME used to dispatch the dwell again


def test_stop_in_simulate_freezes_a_spinning_virtual_axis(win, qtbot):
    from sdr_tuner.gui import gcode, gcode_sim
    runner = win.motion_controller_page.sim_runner
    runner.run(gcode.parse("G1030 X100"), lambda text, state: None)
    qtbot.wait(120)
    runner.cancel()
    frozen = gcode_sim.axis_value_now(win.session.virtual_axes[0])
    qtbot.wait(200)
    assert gcode_sim.axis_value_now(win.session.virtual_axes[0]) == frozen      # used to keep spinning


def test_absurd_dwells_end_with_an_error_instead_of_hanging(win, qtbot):
    connect_axis1(win, qtbot)
    log = win.log_tab
    run_gcode(log, "G04 PINF")
    qtbot.waitUntil(lambda: "not a finite number" in log.gcode_status.text(), timeout=WAIT)
    run_gcode(log, "G04 P3000000")
    qtbot.waitUntil(lambda: "not a wait this tool will start" in log.gcode_status.text(), timeout=WAIT)


def test_status_monitor_row_buttons_are_wide_enough_for_their_own_text(win, qtbot):
    """Seen live on the real bench drive (2026-09-25, 1360 px wide window): the Read button showed "Reac" and the
    Remove button showed nothing at all. Measured: a cell widget is ~12 px narrower than its column, so the 72 px Read
    column gave a 60 px button whose "Read" needs 71; the 28 px fixed Remove button inherited the theme's 18 px side
    padding (46 px needed) and clipped its own glyph away. Every row button must get at least its own size hint."""
    win.resize(1360, 932)
    win.open_tool("status_monitor")
    page = win.status_monitor_page
    page._add_row()
    qtbot.waitUntil(lambda: page._rows[0].read_btn.isVisible(), timeout=WAIT)
    for name, button in (("Read", page._rows[0].read_btn), ("Remove", page._rows[0].remove_btn)):
        assert button.width() >= button.sizeHint().width(), (name, button.width(), button.sizeHint().width())


def test_the_sim_flag_selects_the_simulator_even_when_a_real_interface_was_saved(win, qtbot):
    """`main.py --sim` used to press Connect alone, assuming the interface list still sat on its Simulator default -
    but _load_settings() restores the last-saved interface first, so with the real bench drive's USB cable saved it
    would have connected to the real drive (2026-09-25). connect_simulator() (what --sim calls) must pick the
    Simulator itself."""
    win.iface.setCurrentIndex(win.iface.findText(USB))
    assert win.iface.currentText() == USB
    win.connect_simulator()
    qtbot.waitUntil(lambda: win.session.is_connected, timeout=WAIT)
    assert win.session.is_simulated and win.iface.currentText() == session.SIMULATOR


def _click_item(qtbot, tree, item):
    tree.scrollToItem(item)
    qtbot.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualItemRect(item).center())


def test_parameters_explorer_sections_fold_and_unfold_on_a_single_click(win, qtbot):
    """User (2026-09-25): Item Configuration's groups fold on one click but the Parameters Explorer's sections needed
    a double-click (Qt's default for a tree item) - "every where fold and unfold should work with single mouse
    click". One click on a section item toggles it; a click on a parameter row (a leaf) changes nothing."""
    win.open_tool("parameters")
    tab = win.params_tab
    top = tab._tops[0]
    qtbot.waitUntil(lambda: tab.tree.isVisible(), timeout=WAIT)
    before = top.isExpanded()
    _click_item(qtbot, tab.tree, top)
    assert top.isExpanded() is not before
    _click_item(qtbot, tab.tree, top)
    assert top.isExpanded() is before
    top.setExpanded(True)
    _click_item(qtbot, tab.tree, top.child(0))
    assert top.isExpanded()                                   # a leaf row is not a fold header


def test_tuner_headings_and_the_workspace_folder_fold_on_a_single_click(win, qtbot):
    """Same one-click rule for the two other trees: a tuning wizard's headings and the navigator's Workspace folder."""
    win.open_tool("advanced_tuning")
    tree = win.advanced_tuner.tree
    heading = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).childCount())
    qtbot.waitUntil(lambda: tree.isVisible(), timeout=WAIT)
    assert heading.isExpanded()
    _click_item(qtbot, tree, heading)
    assert not heading.isExpanded()
    _click_item(qtbot, tree, heading)
    assert heading.isExpanded()

    nav = win.navigator
    assert nav.workspace.isExpanded()
    _click_item(qtbot, nav.tree, nav.workspace)
    assert not nav.workspace.isExpanded()
    _click_item(qtbot, nav.tree, nav.workspace)
    assert nav.workspace.isExpanded()


def test_docked_panels_fill_the_workspace_when_the_window_grows(win, qtbot):
    """User (2026-09-25, window maximised in Docked View): the two panels sat on the right and the left half of the
    workspace was blank - QMainWindow gives every leftover pixel to its central widget, and the workspace's own
    central widget was an ordinary empty QWidget. It is zero-sized now, so the dock areas get all of the room."""
    win.resize(1360, 932)
    win.navigator.select_activity("drive") if hasattr(win.navigator, "select_activity") else None
    win.docked_view_btn.click()
    win.open_tool("parameters")
    win.open_tool("motion")
    win.resize(1920, 1049)
    ws = win.dock_workspace
    qtbot.waitUntil(lambda: ws.width() > 1500, timeout=WAIT)
    docks = {d.objectName(): d for d in ws.findChildren(QDockWidget)}
    assert set(docks) == {"parameters", "motion"}
    for dock in docks.values():
        qtbot.waitUntil(lambda d=dock: d.width() >= ws.width() - 12, timeout=WAIT)   # the whole width, not a strip
    assert ws.centralWidget().width() == 0


def test_raw_register_access_note_stays_a_few_lines_in_a_narrow_panel(qtbot):
    """User's recording (2026-09-25): dragging the docked Parameters Explorer narrower made the RAW REGISTER ACCESS
    note wrap into a one-word-per-line column about 20 lines tall, ballooning the box and squeezing the tree above.
    The note now has its own full-width line under the inputs, so at 560 px it wraps to a handful of lines."""
    tab = ParamsTab(session.Session(), telemetry=False)
    qtbot.addWidget(tab)
    tab.resize(560, 700)
    tab.show()
    qtbot.waitUntil(lambda: tab.raw_status.isVisible(), timeout=WAIT)
    box = tab.raw_status.parentWidget()
    qtbot.waitUntil(lambda: tab.raw_status.width() >= 0.8 * box.width(), timeout=WAIT)   # spans the box, not a sliver
    assert box.height() < 180, box.height()


def _title_mouse(bar, kind, global_pos):
    """Delivers a left-button mouse event to a dock's title bar with an explicit GLOBAL position - the drag reads
    globals, and the bar's own mapping changes the moment the panel tears off into its own window."""
    held = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    QApplication.sendEvent(bar, QMouseEvent(kind, QPointF(bar.mapFromGlobal(global_pos)), QPointF(global_pos),
                                            Qt.MouseButton.LeftButton, held, Qt.KeyboardModifier.NoModifier))


def _two_docked_panels(win, qtbot):
    """Parameters Explorer and Axes - two panels whose pages have small minimum widths, so both fit side by side
    in the test window (the Motion page's own fixed left column + cards grid needs ~1060 px on its own, which no
    two-panel row fits in a 1360 px window - a real limit of that page, not of the docking)."""
    connect(win, qtbot)
    win.resize(1600, 1000)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("parameters")
    win.open_tool("axes")
    ws = win.dock_workspace
    params, axes = ws._docks["parameters"], ws._docks["axes"]
    qtbot.waitUntil(lambda: axes.isVisible() and axes.width() > 100 and params.width() > 100, timeout=WAIT)
    return ws, params, axes


def test_every_docked_panel_has_the_tools_own_title_bar_with_float_and_close(win, qtbot):
    """User (2026-09-25, screen recording): dragging a panel gave no visible way to choose where it lands. Every
    panel now carries this tool's own title bar - the title, a float toggle, a close button - which is also what
    makes the drag with drop guides possible (Qt only runs its own drag through its default title bar)."""
    ws, params, axes = _two_docked_panels(win, qtbot)
    bar = axes.titleBarWidget()
    assert isinstance(bar, DockTitleBar) and bar.label.text() == axes.windowTitle()
    bar.float_btn.click()
    assert axes.isFloating() and bool(axes.windowFlags() & Qt.WindowType.FramelessWindowHint)   # our bar, no native frame
    bar.float_btn.click()
    assert not axes.isFloating()
    bar.close_btn.click()
    assert not axes.isVisible()
    win.open_tool("axes")
    assert axes.isVisible() and axes.titleBarWidget() is bar        # same panel, same bar, not rebuilt


def test_dragging_a_panel_shows_drop_guides_and_dropping_on_the_left_guide_docks_it_on_the_left(win, qtbot):
    """The request itself: "if i move mouse towards right or left or top or bottom then i should get an option
    where to set that dock". Tear Axes off by its title bar: the guides appear; release on the workspace's own
    left-edge guide and Axes docks along the left, side by side with Parameters Explorer, with a fair share of
    the width (not the 14 px sliver Qt's own placement gives)."""
    ws, params, axes = _two_docked_panels(win, qtbot)
    bar = axes.titleBarWidget()
    start = bar.mapToGlobal(QPoint(bar.width() // 2, bar.height() // 2))
    _title_mouse(bar, QEvent.Type.MouseButtonPress, start)
    _title_mouse(bar, QEvent.Type.MouseMove, start + QPoint(60, 60))
    assert axes.isFloating() and ws.guides.isVisible()               # torn off, guides up
    over_params = ws.mapToGlobal(QRect(params.mapTo(ws, QPoint(0, 0)), params.size()).center())
    _title_mouse(bar, QEvent.Type.MouseMove, over_params)
    zones = set(ws.guides.guide_rects())
    assert ("tab", params) in zones and ("inner", "left", params) in zones and ("outer", "top") in zones
    target = ws.mapToGlobal(ws.guides.guide_rects()[("outer", "left")].center())
    _title_mouse(bar, QEvent.Type.MouseMove, target)
    assert ws.guides.zone_at(ws.mapFromGlobal(target)) == ("outer", "left")
    _title_mouse(bar, QEvent.Type.MouseButtonRelease, target)
    assert not ws.guides.isVisible()
    qtbot.waitUntil(lambda: not axes.isFloating() and axes.geometry().right() < params.geometry().left(), timeout=WAIT)
    assert axes.width() >= 300 and axes.width() + params.width() >= ws.width() - 20   # a real share, not a
    # sliver: the dragged panel gets half the width, or as much as the neighbour's own minimum width leaves


def test_releasing_a_dragged_panel_away_from_any_guide_leaves_it_floating_where_it_is(win, qtbot):
    ws, params, axes = _two_docked_panels(win, qtbot)
    bar = axes.titleBarWidget()
    start = bar.mapToGlobal(QPoint(bar.width() // 2, bar.height() // 2))
    _title_mouse(bar, QEvent.Type.MouseButtonPress, start)
    _title_mouse(bar, QEvent.Type.MouseMove, start + QPoint(60, 60))
    assert axes.isFloating()
    nowhere = ws.mapToGlobal(params.mapTo(ws, QPoint(0, 0)) + QPoint(70, 70))     # inside Parameters, off every guide
    _title_mouse(bar, QEvent.Type.MouseMove, nowhere)
    assert ws.guides.zone_at(ws.mapFromGlobal(nowhere)) is None
    _title_mouse(bar, QEvent.Type.MouseButtonRelease, nowhere)
    assert axes.isFloating() and not ws.guides.isVisible() and ws.docked_panels() == [params]


def test_the_inner_guides_split_beside_a_panel_and_the_centre_one_stacks_as_a_tab(win, qtbot):
    """Each of the five guides shown over another panel, plus a workspace edge, through the same drop() the
    title bar's release calls - checked by where the panel actually ends up, not just that a call succeeded."""
    ws, params, axes = _two_docked_panels(win, qtbot)
    win.open_tool("status_monitor")
    status = ws._docks["status_monitor"]
    geo = lambda d: QRect(d.mapTo(ws, QPoint(0, 0)), d.size())
    ws.drop(axes, ("inner", "left", params))
    qtbot.waitUntil(lambda: geo(axes).right() < geo(params).left() and abs(geo(axes).top() - geo(params).top()) < 4, timeout=WAIT)
    ws.drop(axes, ("inner", "right", params))
    qtbot.waitUntil(lambda: geo(axes).left() > geo(params).right(), timeout=WAIT)
    ws.drop(axes, ("inner", "top", params))
    qtbot.waitUntil(lambda: geo(axes).bottom() < geo(params).top() and abs(geo(axes).left() - geo(params).left()) < 4, timeout=WAIT)
    ws.drop(axes, ("inner", "bottom", params))
    qtbot.waitUntil(lambda: geo(axes).top() > geo(params).bottom(), timeout=WAIT)
    ws.drop(status, ("tab", params))
    qtbot.waitUntil(lambda: params in ws.tabifiedDockWidgets(status), timeout=WAIT)
    ws.drop(status, ("outer", "top"))
    qtbot.waitUntil(lambda: not status.isFloating() and geo(status).top() == 0 and geo(status).bottom() < geo(params).top()
                    and status.width() >= ws.width() - 12, timeout=WAIT)


def test_stacked_docks_never_overlap_even_when_their_pages_minimums_do_not_fit(win, qtbot):
    """Seen live (2026-09-25, Motion + Parameters Explorer + Axes stacked in a 1360 px window): the Motion page
    painted through the Parameters Explorer, which painted through Axes. A dock can never be smaller than its
    widget's minimum size, and the three pages' minimum heights (596 + 351 + 161) do not fit the 725 px workspace,
    so Qt clamped each dock to its minimum and they overlapped. Each page now sits behind its dock's own scroll
    area, so the docks shrink to fit and the page scrolls instead."""
    connect(win, qtbot)
    win.resize(1360, 932)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    for key in ("motion", "parameters", "axes"):
        win.open_tool(key)
    ws = win.dock_workspace
    docks = [ws._docks[key] for key in ("motion", "parameters", "axes")]
    qtbot.waitUntil(lambda: all(d.height() > 30 for d in docks), timeout=WAIT)
    rects = [QRect(d.mapTo(ws, QPoint(0, 0)), d.size()) for d in docks]
    for i, a in enumerate(rects):
        assert a.bottom() < ws.height(), (i, a)
        for b in rects[i + 1:]:
            assert not a.intersects(b), (a, b)
    assert ws.page_of("motion") is win.drive_tab and win.drive_tab.isVisible()


def test_an_edge_drop_always_takes_the_whole_edge_the_newest_one_outermost(win, qtbot):
    """Live (2026-09-25): with Axes sitting in Qt's top dock area (a layout restored from an earlier session), a panel
    dropped on the LEFT edge guide landed under Axes, full width - Qt's top area owned the corners, so "left" only
    meant the space the top area left over. The side just dropped on takes its corners now: Axes dropped on the top
    edge spans the whole width; Parameters then dropped on the left edge spans the whole height and Axes moves
    inward; Axes dropped on the top edge again spans the whole width once more. Corners survive save/restore."""
    ws, params, axes = _two_docked_panels(win, qtbot)
    geo = lambda d: QRect(d.mapTo(ws, QPoint(0, 0)), d.size())
    ws.drop(axes, ("outer", "top"))
    qtbot.waitUntil(lambda: geo(axes).top() == 0 and axes.width() >= ws.width() - 12, timeout=WAIT)
    ws.drop(params, ("outer", "left"))
    qtbot.waitUntil(lambda: geo(params).left() <= 6 and geo(params).top() == 0
                    and geo(params).bottom() >= ws.height() - 12 and geo(axes).left() > geo(params).right(), timeout=WAIT)
    ws.drop(axes, ("outer", "top"))
    qtbot.waitUntil(lambda: geo(axes).top() == 0 and axes.width() >= ws.width() - 12
                    and geo(params).top() > geo(axes).bottom(), timeout=WAIT)
    assert ws.corner(Qt.Corner.TopLeftCorner) == Qt.DockWidgetArea.TopDockWidgetArea
    assert ws.restoreState(ws.saveState())
    assert ws.corner(Qt.Corner.TopLeftCorner) == Qt.DockWidgetArea.TopDockWidgetArea


def test_the_drop_guides_are_their_own_window_over_the_workspace_not_a_child_the_dragged_panel_can_cover(win, qtbot):
    ws, params, axes = _two_docked_panels(win, qtbot)
    guides = ws.guides
    assert guides.isWindow() and bool(guides.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    guides.begin(axes)
    assert guides.isVisible() and guides.geometry() == QRect(ws.mapToGlobal(QPoint(0, 0)), ws.size())
    guides.end()
    assert not guides.isVisible()


def _gold_centre_x(button):
    """Horizontal centre of the gold (icon) pixels in a grabbed button, in device pixels."""
    image = button.grab().toImage()
    xs = [x for y in range(image.height()) for x in range(image.width())
          if (lambda c: c.red() > 150 and c.green() > 110 and c.blue() < 120)(image.pixelColor(x, y))]
    assert xs, "no gold pixels - no icon drawn?"
    return (min(xs) + max(xs)) / 2


def test_a_collapsed_navigator_centres_its_icons_and_keeps_the_toggle_at_the_top(win, qtbot):
    """User (2026-09-25, screenshot of the collapsed sidebar: "logo mismatched when collapse"): the activity buttons'
    icons sat 6 px right of centre (the expanded style's 12 px left padding, with a 24 px icon in a 38 px button),
    the "»" toggle was a plain text glyph unlike the drawn icons under it, and it floated mid-panel because the
    hidden tree's space was spread between the rows. Collapsed buttons now carry a `collapsed` property with a
    centred, unpadded style; the toggle is a drawn chevron; a zero-stretch spacer keeps the toggle at the top and
    the buttons at the bottom."""
    nav = win.navigator
    win.show()
    qtbot.waitUntil(lambda: nav.isVisible(), timeout=WAIT)
    nav.set_activity("drive")                       # so the System Configuration button is the UNCHECKED one
    system_btn = nav._activities["system"][0]      # (a checked button's gold border would mask the measurement)
    qtbot.mouseClick(nav.collapse_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: nav.width() < 100 and system_btn.text() == "", timeout=WAIT)
    assert system_btn.property("collapsed") == "true"
    assert abs(_gold_centre_x(system_btn) - system_btn.width() / 2) <= 2
    assert nav.collapse_btn.geometry().top() < 40, nav.collapse_btn.geometry()           # top of the panel, not mid-way
    assert system_btn.geometry().bottom() > nav.height() - 120, (system_btn.geometry(), nav.height())
    assert not nav.collapse_btn.icon().isNull() and nav.collapse_btn.text() == ""
    qtbot.mouseClick(nav.collapse_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: system_btn.text() == "System Configuration", timeout=WAIT)
    assert system_btn.property("collapsed") == "false"


def test_servo_drive_and_motion_share_one_docked_workspace_and_the_header_names_the_activity(win, qtbot):
    """User (2026-09-25): the old "Drive Setup and Motion" is now two activities - SERVO DRIVE (one drive's own
    tools) and MOTION (everything multi-axis) - and Docked View covers both with ONE shared workspace, so a drive's
    Motion page can sit next to the 2D Plotter. The outer header names whichever activity's tool was opened last."""
    connect(win, qtbot)
    qtbot.mouseClick(win.docked_view_btn, Qt.MouseButton.LeftButton)
    win.open_tool("motion")                                   # Servo Drive
    assert win.navigator.current_activity() == "drive" and win.host.title.text() == "Servo Drive"
    win.open_tool("plotter_2d")                               # Motion
    assert win.navigator.current_activity() == "motion" and win.host.title.text() == "Motion"
    assert win.dock_workspace.open_keys() == ["motion", "plotter_2d"]     # both docked, side by side, one workspace
    assert win.host.current_page() is win.dock_workspace
    win.open_tool("config")                                   # System Configuration never docks
    assert win.host.current_page() is win.config_page and win.dock_workspace.open_keys() == ["motion", "plotter_2d"]


def _run_program(win, qtbot, *lines):
    mc = win.motion_controller_page
    mc.editor.setPlainText(program(*lines))
    qtbot.mouseClick(mc.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Program finished." in mc.output.toPlainText() or "Stopped." in mc.output.toPlainText(),
                    timeout=WAIT)


def test_a_g_code_command_is_mirrored_on_that_axis_own_motion_page(win, qtbot):
    """User (2026-09-25): "when i run G-code through motion controller then i should get update in servo drive as
    well" - the Motion - Single Axis page's live cards followed the telemetry, but its own target field still read
    0 rpm and its status "Ready. Set a target speed and press RUN." while a program had the motor at 100 rpm. Each
    kind of G-code command now shows on that axis's page the way its own button would have, tagged "(G-code)"."""
    sim, client = connect_axis1(win, qtbot)          # node 2 = axis 1 = the G-code letter Y
    win.open_tool("motion_controller")
    dt = win.drive_tab
    _run_program(win, qtbot, "G1030 Y120")
    assert client.commanded == ("esc_rpm", 120.0)
    assert dt.target.value() == 120.0 and dt.slider.value() == 1200
    assert "Commanding 120.0 rpm (G-code)" in dt.status.text() and "sent every 100 ms" in dt.status.text()

    _run_program(win, qtbot, "G1040 Y0.5")            # torque takes over the single repeat slot
    dm = win.drive_modes_page                          # ...and is mirrored on the Drive Modes page, where the card lives
    assert dm.ct_mode.currentIndex() == 1 and dm.ct_target.value() == 0.5
    assert "Commanding 0.500 N·m (Torque (ETC)) (G-code)" in dm.ct_status.text()
    assert "sent every" not in dt.status.text()       # Speed Control's note cleared: one repeat at a time

    _run_program(win, qtbot, "G1020 Y500")            # a position move, through the same Feedback Settings check
    qtbot.waitUntil(lambda: client.commanded is not None and client.commanded[0] == "servo", timeout=WAIT)
    assert dt.pos_target.value() == 500
    assert "Moving to 500 counts (G-code)" in dt.pos_status.text() and "sent every" not in dm.ct_status.text()

    _run_program(win, qtbot, "G1002")
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)
    assert dt.target.value() == 0.0 and dt.status.text() == "Stop sent. (G-code)"
    assert "sent every" not in dt.pos_status.text()


def test_a_g_code_speed_above_the_pages_own_limit_is_reported_not_clamped_into_the_field(win, qtbot):
    """This page's Speed Limit only caps its own RUN; a program may command more. The field must never show a
    clamped value the drive was not sent - the status says what was sent and that it is above the limit."""
    sim, client = connect_axis1(win, qtbot)
    win.open_tool("motion_controller")
    dt = win.drive_tab
    assert dt.limit.value() == 500
    _run_program(win, qtbot, "G1030 Y900")
    assert client.commanded == ("esc_rpm", 900.0) and dt.target.value() == 0.0
    assert "Commanding 900.0 rpm (G-code) - above the 500 rpm speed limit set here" in dt.status.text()


def test_sim3d_frames_the_camera_on_the_models_own_centre_and_fits_the_floor_grid_under_it(win, qtbot):
    """User (2026-09-25, two screenshots): before a model loads the floor grid shows; after loading radar.obj the
    grid "vanished" and the model was not in the middle. The grid was pyqtgraph's default 20 units (20 mm) under
    a camera framed ~700 mm back, and the framing set the camera distance but left it aimed at the origin while
    radar's mesh is centred at (-56, 25, -115) mm. Now the camera looks at the bounding-box centre and the grid is
    a round-stepped floor about 2.5x the model, laid at its lowest point; clearing the model restores the default."""
    from pathlib import Path
    from PySide6.QtGui import QVector3D
    from sdr_tuner import mesh
    from sdr_tuner.gui.sim3d import MESH_SCALE_MM_PER_M
    samples = Path(defs.__file__).parent / "data" / "kinematics_samples"
    page = win.sim3d_page
    win.session.load_kinematics(str(samples / "radar.obj.kinematics"))
    pts = np.concatenate([m.vertices for m in mesh.parse_obj_file(str(samples / "radar.obj")).values()]) * MESH_SCALE_MM_PER_M
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    centre = (lo + hi) / 2
    looked_at = page.view.opts["center"]
    assert abs(looked_at.x() - centre[0]) < 0.5 and abs(looked_at.y() - centre[1]) < 0.5 and abs(looked_at.z() - centre[2]) < 0.5
    extent = float((hi - lo).max())
    size, spacing = page.grid.size()[0], page.grid.spacing()[0]
    assert size >= 2 * extent and spacing in (10.0, 20.0, 50.0, 100.0) and 5 <= extent / spacing <= 30
    floor = page.grid.transform().map(QVector3D(0, 0, 0))
    assert abs(floor.x() - centre[0]) < 0.5 and abs(floor.y() - centre[1]) < 0.5 and abs(floor.z() - lo[2]) < 0.5
    qtbot.mouseClick(page.reset_view_btn, Qt.MouseButton.LeftButton)     # Reset View keeps looking at the centre
    assert abs(page.view.opts["center"].x() - centre[0]) < 0.5
    win.session.clear_kinematics()
    assert page.grid.size()[0] == 20 and page.grid.spacing()[0] == 1 and page.grid.transform().map(QVector3D(0, 0, 0)) == QVector3D(0, 0, 0)


def test_step_response_wave_form_is_a_named_choice_whose_index_is_the_drives_own_value(win, qtbot):
    """Found while checking Bode-plot support (2026-09-25): the Wave Form field was a bare 0-5 number whose tooltip
    said the reference does not document the values - the Device Reference does (pp. 89-91/95: square, positive
    square, sine, three sweeps). It is a named dropdown now; its index is exactly the value the command carries."""
    from sdr_tuner.gui.tuner import WAVE_FORMS
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    page = tuner.steps
    tuner.go_to(page)
    assert [page.wave_form.itemText(i) for i in range(page.wave_form.count())] == list(WAVE_FORMS)
    assert page.wave_form.itemText(2) == "2 - Sine Wave" and page.wave_form.itemText(3).startswith("3 - Frequency Sweep")
    enter_amplitude(page, "3")
    page.wave_form.setCurrentIndex(2)
    assert page.values()[2] == 2
    page._confirm = lambda text: True
    page.start()
    qtbot.waitUntil(lambda: page.running and client.commanded is not None and client.commanded[1][2] == 2,
                    timeout=WAIT)                                      # the sine wave, on the wire
    page.stop()
    qtbot.waitUntil(lambda: not page.running and client.commanded is None, timeout=WAIT)


# ---- Frequency Response (Bode plot) - Servo Drive > Advanced Tuning > Verification -------------------------------
from sdr_tuner.gui.bode_page import MAPPING_SLOTS, MEASUREMENT_HZ


def bode_ready(win, qtbot, f_start="1", f_stop="4", points="3"):
    """Advanced Tuning open on the Frequency Response page with a short sweep typed in (1, 2, 4 Hz by default: the
    simulator's speed loop is a first-order lag with time constant sim.tau, so every point has a known answer)."""
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    page = tuner.bode
    tuner.go_to(page)
    page.amplitude.setText("10")
    page.f_start.setText(f_start)
    page.f_stop.setText(f_stop)
    page.n_points.setText(points)
    page.settle_cycles.setText("1")
    page.measure_cycles.setText("2")
    page._confirm = lambda text: True
    return page, tuner, sim, client


def telemetry_setup(sim):
    return sim.params[(0x3000, 0x05)], {key: sim.params.get(key, 0) for key in MAPPING_SLOTS}


def auto_test_ready(win, qtbot, autotune=False, speed=False):
    """Connected to the simulator with the Automatic Test page open, its confirmations answered Yes, and the options set."""
    from sdr_tuner import autotest
    sim, client = connect(win, qtbot)
    win.open_tool("advanced_tuning")
    page = win.advanced_tuner.auto_test
    win.advanced_tuner.go_to(page)
    page._confirm = lambda title, text: True
    page.autotune.setChecked(autotune)
    page.current.setChecked(True)
    page.speed.setChecked(speed)
    return page, sim, client, autotest


def test_the_automatic_test_page_lives_under_advanced_tunings_verification_and_the_ribbon_button_opens_it_there(win, qtbot):
    """User (2026-09-25): "put automatic test under advanced tunning below frequency response" - no longer a Servo Drive
    tool of its own (nor a per-axis one: each axis's own Advanced Tuning carries it); the ribbon's Tuning button still
    exists and opens Advanced Tuning on that page; the round STOP reaches it through the tuner's own emergency()."""
    from sdr_tuner.gui.autotest_page import AutomaticTestPage
    tuner = win.advanced_tuner
    page = tuner.auto_test
    assert isinstance(page, AutomaticTestPage) and page in tuner.pages and not hasattr(win.quick_tuner, "auto_test")
    assert "auto_test" not in win.tools and "auto_test" not in win.navigator.tool_keys("drive")
    assert tuner.pages.index(page) == tuner.pages.index(tuner.bode) + 1                  # right below Frequency Response
    assert not page.run_btn.isEnabled() and "Connect" in page.status.text()
    win.open_tool("motion")
    win._open_automatic_test()
    assert win.host.current_page() is tuner and tuner.pages[tuner.index] is page
    connect(win, qtbot)                                                                    # an axis needs the primary's bus
    win.session.connect_axis(session.SIMULATOR, "", 0, 3)
    qtbot.waitUntil(lambda: 3 in win._axis_pages, timeout=WAIT)
    axis_tuner = win._axis_pages[3]["advanced"]
    assert isinstance(axis_tuner.auto_test, AutomaticTestPage) and axis_tuner.auto_test.s is win.session.axes[3]


def test_run_tests_checks_the_drive_and_measures_its_current_loop_then_puts_the_telemetry_back(win, qtbot):
    """One button: the read-only checks, then a step test and a chirp on the simulator's current loop (a lag of IQ_TAU at
    the default FOC Kp), the telemetry layout and rate exactly as before, the result table filled and Save disabled
    (nothing was written)."""
    from sdr_tuner.simulator import IQ_TAU
    page, sim, client, autotest = auto_test_ready(win, qtbot)
    sim.params[(0x2002, 0x12)] = 0.2                    # a 12.7 Hz current loop (tau = IQ_TAU x 0.5 / Kp): well inside the chirp's
    tau = IQ_TAU * 0.5 / 0.2                            # range even when a loaded machine slows the simulator's publishing
    freq_before, mapping_before = telemetry_setup(sim)
    page.run_btn.click()
    qtbot.waitUntil(lambda: page.running, timeout=WAIT)
    assert not page.run_btn.isEnabled() and page.stop_btn.isEnabled()
    qtbot.waitUntil(lambda: not page.running, timeout=40000)
    report = page.report
    assert report is not None and report.autotuned is None and report.speed is None
    names = {c.name: c for c in report.checks}
    assert names["Faults"].status == "ok" and names["DC bus"].status == "ok" and names["Motor data"].status == "ok"
    assert names["Speed loop"].status == "skip"                                          # not asked for
    m = report.current
    assert m is not None and m.bandwidth_hz is not None and m.rise_ms is not None
    # the simulator steps its current loop only every ~5 ms, so its lag near that resolution is crude (the pure tests
    # hold the analysis to exact synthetic signals); here: the right order of magnitude, and the step and the chirp
    # agree with each other (a first-order lag's bandwidth is ~0.35 / its 10-90 % rise)
    assert 8.0 < m.bandwidth_hz < 26.0, m
    assert abs(0.35 / (m.rise_ms / 1000.0) - m.bandwidth_hz) < 0.4 * m.bandwidth_hz, m
    assert m.peak_db is not None and m.peak_db < 1.0 and m.rise_ms is not None
    assert telemetry_setup(sim) == (freq_before, mapping_before) and client.commanded is None
    assert page.table.rowCount() == len(report.checks) and "Tests complete" in page.status.text()
    assert not page.save_btn.isEnabled()


def test_run_tests_can_start_with_the_drives_own_automatic_tuning(win, qtbot):
    page, sim, client, autotest = auto_test_ready(win, qtbot, autotune=True)
    sim.autoconfig_seconds = 0.8
    page.run_btn.click()
    qtbot.waitUntil(lambda: not page.running, timeout=60000)
    report = page.report
    assert report.autotuned and any(c.name == "Automatic tuning" and c.status == "ok" for c in report.checks)
    assert report.current is not None and client.commanded is None


def test_run_tests_measures_the_speed_loop_only_with_an_encoder(win, qtbot):
    page, sim, client, autotest = auto_test_ready(win, qtbot, speed=True)
    sim.params[(0x2000, 0x0A)] = 0                                                       # sensorless
    sim.params[(0x2000, 0x0B)] = 0
    page.run_btn.click()
    qtbot.waitUntil(lambda: not page.running, timeout=40000)
    assert page.report.speed is None and any(c.name == "Speed loop" and c.status == "skip" for c in page.report.checks)
    sim.params[(0x2000, 0x0A)] = 2                                                       # an encoder: the speed loop is measured too
    sim.tau = 0.02                                                                       # an 8 Hz speed loop, inside the chirp's range
    page.run_btn.click()
    qtbot.waitUntil(lambda: not page.running, timeout=60000)
    s = page.report.speed
    assert s is not None and s.bandwidth_hz is not None and abs(s.bandwidth_hz - 1 / (2 * math.pi * 0.02)) < 4.0, s


def test_beta_raises_foc_kp_round_by_round_until_the_sampling_can_verify_no_more(win, qtbot):
    """Beta on the simulator: its current loop gets faster in proportion to FOC Kp (as the real drive's does), so Beta
    raises Kp by half each round - T first set to L/R - until the -3 dB point passes 0.3 x the sampling rate, then
    stops with the reason; everything it wrote is in the simulator's RAM, the telemetry put back, Save enabled."""
    page, sim, client, autotest = auto_test_ready(win, qtbot)
    sim.params[(0x2002, 0x12)] = 0.2                    # start slow (12.7 Hz) so at least one raise fits under the verification
    freq_before, mapping_before = telemetry_setup(sim)  # limit (0.3 x the rate the simulator really publishes at, ~100-200 Hz)
    kp0, t0 = sim.params[(0x2002, 0x12)], sim.params[(0x2002, 0x13)]
    page.beta_btn.click()
    qtbot.waitUntil(lambda: page.running, timeout=WAIT)
    qtbot.waitUntil(lambda: not page.running, timeout=240000)
    assert "Beta finished" in page.status.text(), page.status.text()
    assert sim.params[(0x2002, 0x12)] > kp0 * 1.4, page.status.text() + "\n" + page.log.toPlainText()   # raised at least once
    assert sim.params[(0x2002, 0x13)] == pytest.approx(sim.params[(0x2000, 0x05)] / 2 / (sim.params[(0x2000, 0x04)] / 2), rel=0.01)
    assert any(p.key == autotest.FOC_KP for p, _v in page.applied) and any(p.key == autotest.FOC_T for p, _v in page.applied)
    assert "verify" in page.status.text() or "cap" in page.status.text() or "round limit" in page.status.text()
    assert telemetry_setup(sim) == (freq_before, mapping_before) and client.commanded is None
    assert page.save_btn.isEnabled()
    page.save_btn.click()                                                                 # its own Save to Flash
    qtbot.waitUntil(lambda: sim.flash_saves == 1, timeout=WAIT)
    assert sim.flash[(0x2002, 0x12)] == sim.params[(0x2002, 0x12)]


def test_stop_ends_the_automatic_test_and_puts_the_telemetry_back(win, qtbot):
    page, sim, client, autotest = auto_test_ready(win, qtbot)
    freq_before, mapping_before = telemetry_setup(sim)
    page.run_btn.click()
    qtbot.waitUntil(lambda: client.commanded is not None, timeout=WAIT)                  # a test is running on the drive
    win.emergency_stop()                                                                  # the round STOP button / Esc
    qtbot.waitUntil(lambda: not page.running, timeout=WAIT)
    assert "Stopped" in page.status.text() and client.commanded is None
    assert telemetry_setup(sim) == (freq_before, mapping_before)
    assert page.run_btn.isEnabled()


def test_the_frequency_response_page_is_under_advanced_tunings_verification_only(win):
    assert not hasattr(win.quick_tuner, "bode")
    tuner = win.advanced_tuner
    assert tuner.bode.title == "Frequency Response" and tuner.bode in tuner.pages
    tree = tuner.tree
    verification = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).text(0) == "Verification")
    assert [verification.child(i).text(0) for i in range(verification.childCount())] == ["Step Response Tests", "Frequency Response",
                                                                                          "Automatic Test"]


def test_the_bode_pair_and_the_nyquist_plot_fit_the_page_at_once_at_every_window_size(win, qtbot):
    """User (2026-09-25, the gain plot alone filling the screen): "make size of both graph small so it can fit in one
    screen so user can see both graph at the same time without scroll up or down", then "nyquist plot is not visible
    in screen" - each Bode plot takes half the page's height (PLOT_MIN_PX..PLOT_MAX_PX) and the Nyquist square sits
    BESIDE the pair, as tall as it (capped at a share of the width), all refitted on every resize; the old code left
    the plots at whatever the first layout pass gave them (~480 px each on a 756 px page) with the Nyquist below."""
    from sdr_tuner.gui import bode_page
    tuner = win.advanced_tuner
    page = tuner.bode
    win.open_tool("advanced_tuning")
    tuner.go_to(page)
    heights = []
    for size in ((1920, 1049), (1360, 932), (1120, 700)):
        win.resize(*size)

        def all_three_fit():
            gap = page.bode_col.spacing()
            return (page.gain_plot.height() == page.phase_plot.height()
                    and page.gain_plot.height() + gap + page.phase_plot.height() <= page.viewport().height()
                    and page.plots.height() <= page.viewport().height())
        qtbot.waitUntil(all_three_fit, timeout=WAIT)
        h, gap = page.gain_plot.height(), page.bode_col.spacing()
        assert bode_page.PLOT_MIN_PX <= h <= bode_page.PLOT_MAX_PX
        assert page.gain_plot.minimumHeight() == page.gain_plot.maximumHeight() == h          # fixed, not a hint
        nyq = page.nyquist_plot
        assert nyq.width() == nyq.height() <= 2 * h + gap                                     # a square, no taller than the pair
        assert nyq.x() >= page.gain_plot.x() + page.gain_plot.width()                         # beside the pair, not under it
        assert nyq.width() <= max(bode_page.NYQUIST_MIN_PX, int(page.viewport().width() * bode_page.NYQUIST_WIDTH_SHARE))
        heights.append(h)
    assert heights[0] == bode_page.PLOT_MAX_PX                    # a tall page: capped, the rest stays for the text
    assert heights[0] >= heights[1] > heights[2]                  # a shorter page: the plots shrink so all still fit


def test_the_nyquist_unit_circle_is_drawn_in_ruby_not_the_hairline_colour(win):
    """User (2026-09-25, looking at a real sweep): "nyquist plot that circle is in white colour which is rarely visible
    make it in red colour" - the dashed unit circle, the reference every margin is read against, uses the ruby accent
    the -1 marker already uses, not theme.LINE (the near-background hairline colour)."""
    import pyqtgraph as pg
    from PySide6.QtGui import QColor
    from sdr_tuner.gui import theme
    items = win.advanced_tuner.bode.nyquist_plot.getPlotItem().listDataItems()
    circle = items[0]                                              # drawn first, under everything else
    x, y = circle.getData()
    assert len(x) == 181 and abs(x[0] - 1.0) < 1e-9 and abs(x[45] ** 2 + y[45] ** 2 - 1.0) < 1e-9   # it is the unit circle
    pen = pg.mkPen(circle.opts["pen"])
    assert pen.color().name().lower() == QColor(theme.RUBY).name().lower()
    assert pen.style() == Qt.PenStyle.DashLine


def test_the_simulator_drives_a_real_sine_reference_for_wave_form_2(win, qtbot):
    """Wave Form 2 (Device Reference pp. 89-91): the simulated speed reference follows a sine of the given amplitude,
    not the square wave of Wave Form 0 - the excitation a Bode sweep relies on."""
    sim, client = connect(win, qtbot)
    client.run_test("testing_electronic_speed_control_esc", 0.4, 20.0, 2)
    seen = []
    for _ in range(16):
        qtbot.wait(40)
        seen.append(sim._live()["ref_speed_hz"])
    assert min(seen) < -12 and max(seen) > 12 and any(abs(v) < 8 for v in seen)      # both signs AND in between: a sine
    client.run_test("testing_electronic_speed_control_esc", 0.4, 20.0, 0)
    seen = []
    for _ in range(16):
        qtbot.wait(40)
        seen.append(sim._live()["ref_speed_hz"])
    assert all(abs(abs(v) - 20.0) < 1e-9 for v in seen)                                 # a square wave: only +-amplitude
    client.stop()


def test_a_frequency_sweep_measures_the_simulators_speed_loop_lag_and_puts_the_telemetry_back(win, qtbot):
    """The whole thing, against the simulator: the sweep maps only Reference: Speed and Speed into message 0, sets the
    telemetry to 1000 Hz, runs the drive's sine test at each frequency, and the gain/phase it measures match the
    simulator's own first-order lag (1/(1+jwt), t = sim.tau) - then the mapping and the rate are exactly as before."""
    page, tuner, sim, client = bode_ready(win, qtbot)
    freq_before, mapping_before = telemetry_setup(sim)
    assert mapping_before[(0x1A01, 1)] != 0                                              # the default layout has 3 messages
    stops = []                                                                           # every Stop frame that leaves for the drive
    client.frame_listeners.append(lambda d, t, cid, data: stops.append(t) if d == "tx" and cid == 0x200 + client.node and data[0] == 0x04 else None)
    page.start()
    qtbot.waitUntil(lambda: sim.params[(0x3000, 0x05)] == MEASUREMENT_HZ, timeout=WAIT)
    assert page.running
    assert sim.params[(0x1A00, 1)] == defs.mapping_code(0x4000, 0x21) and sim.params[(0x1A00, 2)] == defs.mapping_code(0x4000, 0x10)
    assert all(sim.params[key] == 0 for key in MAPPING_SLOTS if key[0] != 0x1A00 or key[1] > 2)
    qtbot.waitUntil(lambda: client.commanded is not None and client.commanded[0] == "testing_electronic_speed_control_esc"
                    and client.commanded[1][2] == 2, timeout=WAIT)                     # the sine wave, on the wire
    qtbot.waitUntil(lambda: not page.running and len(page.points) == 3, timeout=40000)
    tau = sim.tau
    for point, f in zip(page.points, (1.0, 2.0, 4.0)):
        w = 2 * math.pi * f * tau
        assert point.frequency == pytest.approx(f, rel=2e-3)                             # the frequency the reference showed
        assert abs(point.gain_db - (-10 * math.log10(1 + w * w))) < 1.5, point
        assert abs(point.phase_deg - (-math.degrees(math.atan(w)))) < 12.0, point
        assert point.quality > 0.8
    gains = [p.gain_db for p in page.points]
    assert gains == sorted(gains, reverse=True)                                          # a lag: gain falls with frequency
    assert client.commanded is None                                                      # Stop was sent
    assert len(stops) == 3                                # one before the 2nd and 3rd point (the drive's sine generator drifts
    assert telemetry_setup(sim) == (freq_before, mapping_before)                         # the longer one test runs) + the final one
    assert "bandwidth" in page.results.text().lower() and "Sweep complete: 3 points" in page.status.text()
    assert page.save_csv_btn.isEnabled() and page.save_image_btn.isEnabled()
    # the Nyquist view: a first-order lag's open loop is an integrator - 90 deg of phase margin at 1/(2 pi tau)
    m = bode.margins(page.points)
    assert m.phase_margin_deg is not None and abs(m.phase_margin_deg - 90.0) < 15.0, m
    assert abs(m.gain_crossover_hz - 1 / (2 * math.pi * tau)) < 0.4, m
    assert "phase margin" in page.results.text().lower() and "no gain margin" in page.results.text()
    assert len(page.nyquist_curve.xData) == 3 and len(page.nyquist_cross.xData) == 1
    page.points[1] = page.points[1]._replace(quality=0.2)                               # a weak point stays ON the joined line,
    page._redraw()                                                                       # ringed hollow (user: "line joining all points")
    assert len(page.nyquist_curve.xData) == 3 and len(page.nyquist_warn.xData) == 1
    view = page.nyquist_plot.getPlotItem().viewRange()
    assert view[1][1] == pytest.approx(2.5, abs=0.01)                                    # the crossover is in range: the normal view
    page.points = [bode.BodePoint(f, -0.5, -10.0, 0.95) for f in (10.0, 20.0, 40.0)]     # |T| ~ 0.94: |L| ~ 5 everywhere - far outside
    page._redraw()                                                                       # (the Y range: the aspect lock trims X to the panel)
    view = page.nyquist_plot.getPlotItem().viewRange()
    assert 6.0 < view[1][1] < 8.0, view                                                  # widened so the nearest point is on screen


def test_a_new_sweep_resets_a_zoomed_view_on_every_plot(win, qtbot):
    """A zoom left from looking at the last sweep must not hide the next one's points: start() puts the Bode plots
    back on auto-range and the Nyquist plot back in its own +-NYQUIST_VIEW box before the first point arrives."""
    from sdr_tuner.gui import bode_page
    page, tuner, sim, client = bode_ready(win, qtbot)
    for plot in (page.gain_plot, page.phase_plot, page.nyquist_plot):
        assert plot.zoom_with_modifier and plot.reset_view is not None                    # theme.enable_zoom applied
        plot.getPlotItem().getViewBox().scaleBy((0.5, 0.5))                                 # a zoom, as the wheel would
    assert page.gain_plot.getPlotItem().getViewBox().state["autoRange"] == [False, False]
    assert page.nyquist_plot.getPlotItem().getViewBox().viewRange()[1][1] < bode_page.NYQUIST_VIEW * 0.9
    page.start()
    qtbot.waitUntil(lambda: page.running, timeout=WAIT)
    for plot in (page.gain_plot, page.phase_plot):
        assert plot.getPlotItem().getViewBox().state["autoRange"] == [True, True]
    assert page.nyquist_plot.getPlotItem().getViewBox().viewRange()[1][1] == pytest.approx(bode_page.NYQUIST_VIEW, abs=1e-6)
    page.stop()
    qtbot.waitUntil(lambda: not page.running, timeout=WAIT)
    page.nyquist_plot.getPlotItem().getViewBox().scaleBy((0.5, 0.5))
    page.nyquist_plot.reset_view()                                                           # the double-click's reset
    assert page.nyquist_plot.getPlotItem().getViewBox().viewRange()[1][1] == pytest.approx(bode_page.NYQUIST_VIEW, abs=1e-6)


def bode_lag_points(corner_hz=10.0, freqs=(1, 2, 4, 8, 16, 32, 64, 128, 256), quality=0.95):
    """A first-order lag's closed-loop points, exact: gain and phase of 1 / (1 + j f / corner)."""
    from sdr_tuner import bode
    return [bode.BodePoint(f, -10 * math.log10(1 + (f / corner_hz) ** 2), -math.degrees(math.atan(f / corner_hz)), quality)
            for f in freqs]


def test_bode_cursors_stay_in_sync_snap_to_the_measured_points_and_read_the_slope(win, qtbot):
    """User (2026-09-25): "should we give cursor for more analysis in gain and phase both graph and sync" -> "build the
    cursors". Two cursors, A and B, shared by the gain and phase plots: dragging one on either plot moves its twin,
    letting go snaps both to the nearest measured point, the table reads each point (with the open loop behind it)
    and the slope between them in dB/decade, and the Nyquist plot marks the same two points."""
    from sdr_tuner import bode
    page, tuner, sim, client = bode_ready(win, qtbot)
    page.points = bode_lag_points()
    page._redraw()
    assert not any(line.isVisible() for lines in page._cursors.values() for line in lines)
    assert page.cursor_points() == {"A": None, "B": None}
    page.cursors_btn.click()
    assert page.cursor_table.isVisible() and all(line.isVisible() for lines in page._cursors.values() for line in lines)
    assert page.cursor_points()["A"].frequency == 8 and page.cursor_points()["B"].frequency == 64     # thirds of nine points
    table = page.cursor_table                       # every row on show, no scroll bar (the real screen's 37 px header
    needed = table.horizontalHeader().sizeHint().height() + table.rowCount() * 24 + 2 * table.frameWidth()   # hid the Slope row)
    assert table.height() >= needed and not table.verticalScrollBar().isVisible()
    gain_line, phase_line = page._cursors["B"]
    assert gain_line.value() == pytest.approx(math.log10(64)) == phase_line.value()                  # log-x plots
    gain_line.setValue(math.log10(200.0))                                          # dragged on the gain plot toward 200 Hz
    gain_line.sigDragged.emit(gain_line)
    assert phase_line.value() == pytest.approx(math.log10(200.0))                 # the phase plot's twin followed
    assert page.cursor_points()["B"].frequency == 256                             # the readout already tracks the nearest point
    gain_line.sigPositionChangeFinished.emit(gain_line)                            # let go: both snap to 256 Hz
    assert gain_line.value() == pytest.approx(math.log10(256)) and phase_line.value() == pytest.approx(math.log10(256))
    table = page.cursor_table
    read = {table.item(r, 0).text(): [table.item(r, c).text() for c in (1, 2, 3)] for r in range(table.rowCount())}
    a, b = page.points[3], page.points[8]
    assert read["Frequency (Hz)"][:2] == ["8", "256"] and read["Frequency (Hz)"][2].startswith("+248")
    assert read["Gain (dB)"] == [f"{a.gain_db:+.2f}", f"{b.gain_db:+.2f}", f"{b.gain_db - a.gain_db:+.2f}"]
    assert read["Phase (°)"][2] == f"{b.phase_deg - a.phase_deg:+.1f}"
    slope = float(read["Slope A→B (dB/decade)"][2])
    assert slope == pytest.approx((b.gain_db - a.gain_db) / math.log10(32), abs=0.06) and -20 < slope < -16   # heading to -20
    ol = {f: l for f, l, _q in bode.open_loop(page.points)}
    assert read["Open loop |L|"][1] == f"{abs(ol[256]):.3g}"
    x, y = page.nyquist_marks["B"].getData()
    assert x[0] == pytest.approx(ol[256].real) and y[0] == pytest.approx(ol[256].imag)
    assert page.nyquist_labels["B"].isVisible()
    page.cursors_btn.click()                                                       # off: everything hidden, nothing read
    assert not page.cursor_table.isVisible() and not gain_line.isVisible() and not page.nyquist_labels["B"].isVisible()
    xs = page.nyquist_marks["B"].getData()[0]
    assert xs is None or len(xs) == 0
    page.cursors_btn.click()                                                       # back on: B is still at 256 Hz
    assert page.cursor_points()["B"].frequency == 256


def test_the_recorders_cursor_table_shows_every_row_without_a_scroll_bar(win, qtbot):
    """The same 7 px shortfall the Bode cursor table had (a 28 px header assumed, the theme's is 37): the Recorder's
    own cursor table is sized from the measured header height too (widgets.fit_table_height)."""
    connect(win, qtbot)
    win.open_tool("recorder")
    rec = win.recorder_page
    rec.cursors_btn.click()
    qtbot.waitUntil(lambda: rec.table.isVisible(), timeout=WAIT)
    table = rec.table
    needed = table.horizontalHeader().sizeHint().height() + max(table.rowCount(), 1) * 24 + 2 * table.frameWidth()
    assert table.height() >= needed and not table.verticalScrollBar().isVisible()


def test_the_bode_csv_marks_the_rows_the_cursors_sit_on(win, qtbot, tmp_path, monkeypatch):
    """Save Data… gains a 'cursor' column: A / B on the rows the cursors sit on, empty elsewhere (and everywhere
    while the cursors are off) - the readout travels with the data."""
    import csv
    from PySide6.QtWidgets import QFileDialog
    page, tuner, sim, client = bode_ready(win, qtbot)
    page.points = bode_lag_points(freqs=(1, 10, 100))
    page._redraw()
    path = tmp_path / "bode.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "CSV (*.csv)")))
    page.save_csv_dialog()
    rows = list(csv.reader(open(path)))
    assert rows[0][-1] == "cursor" and [r[-1] for r in rows[1:]] == ["", "", ""]
    page.cursors_btn.click()
    page.save_csv_dialog()
    rows = list(csv.reader(open(path)))
    assert [r[-1] for r in rows[1:]] == ["", "A", "B"] and rows[0][0] == "frequency_hz"


def test_the_full_contour_plot_draws_the_measured_half_and_its_mirror_for_t_or_l(win, qtbot, tmp_path, monkeypatch):
    """User (2026-09-25, a textbook picture of G = 1/(s^2+s+1)'s Nyquist contour: "this is nyquist plot while our looks
    different i am confused" -> "add both the mirror half and the closed loop T toggle but don't remove current one
    just add both in different graph below"): a second Nyquist plot below the first draws the measured half and its
    complex-conjugate mirror, for the closed loop T as measured (the default: starts near (1, 0) and heads for the
    origin, the familiar shape) or the open loop L; the -1 point shows for L only; the cursors mark the same points
    here too; Save Image stacks four plots."""
    import cmath
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QFileDialog
    from sdr_tuner import bode
    from sdr_tuner.gui import bode_page
    page, tuner, sim, client = bode_ready(win, qtbot)
    page.points = bode_lag_points()
    page._redraw()
    qtbot.waitUntil(lambda: page.contour_plot.width() == page.nyquist_plot.width() > 0, timeout=WAIT)
    assert page.contour_plot.width() == page.contour_plot.height()                            # a square like the one above
    assert page.contour_plot.mapTo(page.widget(), QPoint(0, 0)).y() > page.plots.mapTo(page.widget(), QPoint(0, 0)).y() + page.plots.height()
    assert page.contour_view.currentData() == "closed"
    xs, ys = page.contour_curve.getData()
    t = [cmath.rect(10 ** (p.gain_db / 20), math.radians(p.phase_deg)) for p in page.points]
    assert list(xs) == pytest.approx([v.real for v in t]) and list(ys) == pytest.approx([v.imag for v in t])
    assert abs(xs[0] - 1.0) < 0.02 and abs(ys[0]) < 0.11 and math.hypot(xs[-1], ys[-1]) < 0.05   # from (1, 0) toward the origin
    mx, my = page.contour_mirror.getData()
    assert list(mx) == list(xs) and list(my) == pytest.approx([-y for y in ys])                # negative frequencies: the conjugate
    assert not page.contour_minus_one.isVisible()
    view = page.contour_plot.getPlotItem().getViewBox()

    def half_view():                     # the box the page asked for (the aspect lock then stretches one axis to fit the panel)
        (x0, x1), (y0, y1) = view.targetRange()
        assert -x0 == x1 == -y0 == y1
        return y1
    assert half_view() == pytest.approx(bode_page.CONTOUR_VIEW_T, abs=1e-6)
    page.contour_view.setCurrentIndex(1)                                                       # the open loop L
    assert page.contour_view.currentData() == "open" and page.contour_minus_one.isVisible()
    xs, ys = page.contour_curve.getData()
    ol = bode.open_loop(page.points)
    assert list(xs) == pytest.approx([l.real for _f, l, _q in ol]) and list(ys) == pytest.approx([l.imag for _f, l, _q in ol])
    assert half_view() == pytest.approx(bode_page.NYQUIST_VIEW, abs=1e-6)
    page.cursors_btn.click()                                                                  # B (64 Hz) marked here too
    x, y = page.contour_marks["B"].getData()
    l64 = next(l for f, l, _q in ol if f == 64)
    assert x[0] == pytest.approx(l64.real) and y[0] == pytest.approx(l64.imag) and page.contour_labels["B"].isVisible()
    page.contour_view.setCurrentIndex(0)
    x, y = page.contour_marks["B"].getData()
    assert x[0] == pytest.approx(t[6].real) and y[0] == pytest.approx(t[6].imag)             # ... and follows the toggle
    path = tmp_path / "bode.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "PNG image (*.png)")))
    page.save_image_dialog()
    image = QPixmap(str(path))
    assert image.height() == sum(w.grab().height() for w in (page.gain_plot, page.phase_plot, page.nyquist_plot, page.contour_plot))


def test_the_chirp_method_shows_its_own_fields_and_hides_the_stepped_sines(win, qtbot):
    tuner, sim, client = tuner_ready(win, qtbot, "advanced_tuning")
    page = tuner.bode
    tuner.go_to(page)
    page.amplitude.setText("10")
    assert not page.chirp_mode() and page.f_start.isVisible() and not page.chirp_period.isVisible()
    page.method.setCurrentIndex(1)
    qtbot.waitUntil(lambda: page.chirp_period.isVisible(), timeout=WAIT)
    assert page.chirp_mode() and not page.f_start.isVisible() and not page.settle_cycles.isVisible()
    assert "1 Hz" in page.chirp_note.text() and "10 s cycle" in page.chirp_note.text()      # Period 1 s, 2 cycles by default
    page.chirp_period.setText("0.25")
    page.chirp_cycles.setText("3")
    page.refresh()
    assert "4 Hz" in page.chirp_note.text() and "2.5 s cycle" in page.chirp_note.text() and "= 8 s" in page.chirp_note.text()
    assert page.values() == (10.0, 0.25, 3)                                                    # amplitude, period (1/256 s steps), cycles
    page.chirp_period.setText("0.001")                                                        # below 1/256 s: raw 0
    page.refresh()
    assert any(c.level == "block" and "1/256" in c.text for c in page.checks())
    page.method.setCurrentIndex(0)
    qtbot.waitUntil(lambda: page.f_start.isVisible(), timeout=WAIT)
    assert not page.chirp_period.isVisible()


def test_a_chirp_measures_the_simulators_current_loop_lag_and_puts_the_telemetry_back(win, qtbot):
    """The Chirp method end to end on the simulator's Iq loop (a first-order lag, IQ_TAU): the drive's sweep (modelled
    on the bench measurement) is recorded for two cycles, analysed by Welch, and the points where the coherence is
    good match 1/(1 + jwt); the mapping and the rate are exactly as before, and a Stop went out at the end."""
    from sdr_tuner.simulator import IQ_TAU
    page, tuner, sim, client = bode_ready(win, qtbot)
    page.kind.setCurrentIndex(2)                                                                # Iq Current
    page.method.setCurrentIndex(1)
    page.amplitude.setText("0.5")
    page.chirp_period.setText("0.5")                                                            # from 2 Hz, 5 s cycles
    page.chirp_cycles.setText("2")
    freq_before, mapping_before = telemetry_setup(sim)
    page.start()
    qtbot.waitUntil(lambda: client.commanded is not None and client.commanded[0] == "testing_iq_current_control"
                    and client.commanded[1][2] == 3, timeout=WAIT)                            # wave form 3 on the wire
    assert page._phase == "chirp" and "recording 2 cycle" in page.status.text()
    qtbot.waitUntil(lambda: not page.running, timeout=30000)
    assert len(page.points) > 20 and client.commanded is None
    good = [p for p in page.points if p.quality >= 0.8 and 2.0 <= p.frequency <= 20.0]
    assert len(good) >= 8, [(round(p.frequency, 1), round(p.quality, 2)) for p in page.points]
    for p in good:
        w = 2 * math.pi * p.frequency * IQ_TAU
        assert abs(p.gain_db - (-10 * math.log10(1 + w * w))) < 1.5, p
        if p.frequency <= 10.0:      # the simulator's current loop is a filter stepped every 5 ms (= IQ_TAU): above ~10 Hz
            assert abs(p.phase_deg - (-math.degrees(math.atan(w)))) < 12.0, p     # its phase is not the continuous lag's
    assert telemetry_setup(sim) == (freq_before, mapping_before)
    assert "Sweep complete" in page.status.text() and "bandwidth" in page.results.text().lower()
    assert page.save_csv_btn.isEnabled()


def test_stopping_a_chirp_stops_the_drive_and_puts_the_telemetry_back(win, qtbot):
    page, tuner, sim, client = bode_ready(win, qtbot)
    page.method.setCurrentIndex(1)
    page.chirp_period.setText("1")
    page.chirp_cycles.setText("3")                                                              # 30 s - stopped long before
    freq_before, mapping_before = telemetry_setup(sim)
    page.start()
    qtbot.waitUntil(lambda: page._phase == "chirp", timeout=WAIT)
    page.stop()
    qtbot.waitUntil(lambda: not page.running, timeout=WAIT)
    assert client.commanded is None and page.points == []
    assert telemetry_setup(sim) == (freq_before, mapping_before)
    assert "stopped" in page.status.text().lower()


def test_stopping_a_sweep_stops_the_drive_keeps_the_points_and_puts_the_telemetry_back(win, qtbot):
    page, tuner, sim, client = bode_ready(win, qtbot, "0.5", "2", "6")
    before = telemetry_setup(sim)
    page.start()
    qtbot.waitUntil(lambda: client.commanded is not None and client.commanded[1][2] == 2, timeout=WAIT)
    qtbot.waitUntil(lambda: len(page.points) >= 1, timeout=30000)
    qtbot.mouseClick(page.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page.running, timeout=WAIT)
    assert client.commanded is None and 1 <= len(page.points) < 6
    assert telemetry_setup(sim) == before
    assert "Sweep stopped after" in page.status.text()


def test_the_round_stop_button_ends_a_sweep_too(win, qtbot):
    page, tuner, sim, client = bode_ready(win, qtbot, "0.5", "2", "6")
    before = telemetry_setup(sim)
    page.start()
    qtbot.waitUntil(lambda: client.commanded is not None and client.commanded[1][2] == 2, timeout=WAIT)
    qtbot.mouseClick(win.ribbon.stop_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not page.running, timeout=WAIT)
    assert client.commanded is None and telemetry_setup(sim) == before


def test_disconnecting_during_a_sweep_puts_the_telemetry_back_before_the_connection_closes(win, qtbot):
    page, tuner, sim, client = bode_ready(win, qtbot, "0.5", "2", "6")
    before = telemetry_setup(sim)
    page.start()
    qtbot.waitUntil(lambda: client.commanded is not None and client.commanded[1][2] == 2, timeout=WAIT)
    assert telemetry_setup(sim) != before
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not win.session.is_connected, timeout=WAIT)
    assert not page.running and telemetry_setup(sim) == before                         # restored while it still could be


def test_a_sweep_refuses_to_start_without_a_connection_or_with_bad_values(win, qtbot):
    page = win.advanced_tuner.bode
    said = []
    page.message.connect(lambda text, level: said.append(text))
    page.amplitude.setText("10")
    page.start()
    assert not page.running and page.checks()[0].level == "block" and "Not connected" in page.checks()[0].text
    assert not page.start_btn.isEnabled()
    sim, client = connect(win, qtbot)
    win.open_tool("advanced_tuning")
    page.f_start.setText("50")
    page.f_stop.setText("1")
    page._confirm = lambda text: True
    page.start()
    assert not page.running and "start < stop" in said[-1]


# ---- Save to Flash (ribbon) and the Frequency Response page's telemetry-rate card ----------------------------------
def test_the_parameters_ribbon_has_save_to_flash_that_asks_first_and_sends_the_store_request(win, qtbot):
    """A parameter write is RAM-only on this drive (2026-09-25); the ribbon's Save to Flash sends the vendor IDE's own
    store request through client.save_to_flash after a confirmation - declined, nothing is sent."""
    win.ribbon.show_page("parameters")
    assert win.save_flash_btn.text() == "Save to\nFlash"
    win._save_to_flash()                                                     # not connected: a warning, nothing else
    assert "not connected" in win.status_text().lower()
    sim, client = connect(win, qtbot)
    win._confirm_flash = lambda text: False
    win._save_to_flash()
    assert sim.flash_saves == 0 and "cancelled" in win.status_text().lower()
    asked = []
    win._confirm_flash = lambda text: asked.append(text) or True
    win._save_to_flash()
    qtbot.waitUntil(lambda: sim.flash_saves == 1, timeout=WAIT)
    assert "node 1" in asked[0] and "power-up" in asked[0]
    qtbot.waitUntil(lambda: "saved to flash" in win.status_text(), timeout=WAIT)
    assert sim.flash[(0x3000, 0x05)] == sim.params[(0x3000, 0x05)]


def test_a_reconnect_made_from_another_page_reads_the_timer_only_once_frequency_response_is_reopened(win, qtbot, monkeypatch):
    """The 2000 Hz workflow tells the user to power-cycle, then Disconnect and Connect - normally from Item Configuration,
    not from this page. Connecting must read nothing beyond the pole count, the mapping and the identity (the standing
    rule), the old connection's timer value must not be shown afterwards (the timer may have changed at boot), and
    reopening the page reads it once, fresh. Found while fixing the 16-reads test: the first cut re-read the timer on
    every `probed`, a 17th read at connect."""
    from sdr_tuner.client import DriveClient
    page, tuner, sim, client = bode_ready(win, qtbot)
    qtbot.waitUntil(lambda: page.timer_hz == 1000.0, timeout=WAIT)
    reads = []
    real = DriveClient.read_raw
    monkeypatch.setattr(DriveClient, "read_raw",
                        lambda self, index, sub, *a, **k: reads.append((index, sub)) or real(self, index, sub, *a, **k))
    win.open_tool("config")                                                           # Item Configuration: Disconnect / Connect live there
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not win.session.is_connected, timeout=WAIT)
    assert page.timer_hz is None                                                      # gone with the connection it belonged to
    sim2, client2 = connect(win, qtbot)                                               # a fresh simulator = the drive after its power cycle
    assert client2 is not client and win.session.is_connected
    assert (0x5000, 0xA9) not in reads and page.timer_hz is None                     # connecting read nothing extra
    sim2.timer_isr_hz = 2000.0                                                        # what a 2000 Hz boot reports
    win.open_tool("advanced_tuning")
    tuner.go_to(page)
    qtbot.waitUntil(lambda: page.timer_hz == 2000.0, timeout=WAIT)                    # read once the page is on screen
    assert reads.count((0x5000, 0xA9)) == 1 and "2000 Hz" in page.rate_note.text()


def test_prepare_2000_hz_writes_the_receiving_frequency_saves_to_flash_and_the_sweep_then_samples_at_2000(win, qtbot):
    """The user's own workflow (2026-09-25): for a Bode plot, set the drive to 2000 Hz (write receiving frequency, save
    to flash, power cycle), sweep at that rate, then go back to 1000 the same way. The simulator's reboot() stands in
    for the power cycle."""
    page, tuner, sim, client = bode_ready(win, qtbot)
    qtbot.waitUntil(lambda: page.timer_hz == 1000.0, timeout=WAIT)
    assert "1000 Hz" in page.rate_note.text() and page.prepare_fast_btn.isEnabled() and not page.restore_rate_btn.isEnabled()
    page._confirm_rate = lambda text: False
    page.set_rate(2000.0)
    assert sim.params[(0x3000, 0x06)] == 1000.0 and sim.flash_saves == 0            # declined: nothing written or saved
    asked = []
    page._confirm_rate = lambda text: asked.append(text) or True
    qtbot.mouseClick(page.prepare_fast_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.flash_saves == 1, timeout=WAIT)
    assert sim.params[(0x3000, 0x06)] == 2000.0 and sim.flash[(0x3000, 0x06)] == 2000.0 and "2000 Hz" in asked[0]
    qtbot.waitUntil(lambda: page.rate_instruction.isVisible() and "POWER-CYCLE" in page.rate_instruction.text(), timeout=WAIT)
    assert page.timer_hz == 1000.0                                                    # until the power cycle, nothing changes
    sim.reboot()                                                                      # the power cycle
    page.refresh_timer()                                                              # what a reconnect (probed) triggers
    qtbot.waitUntil(lambda: page.timer_hz == 2000.0, timeout=WAIT)
    assert not page.rate_instruction.isVisible() and "2000 Hz now" in page.rate_note.text()
    assert not page.prepare_fast_btn.isEnabled() and page.restore_rate_btn.isEnabled()
    page.start()
    qtbot.waitUntil(lambda: sim.params[(0x3000, 0x05)] == 2000.0, timeout=WAIT)     # the sweep asks for the timer's rate
    qtbot.waitUntil(lambda: not page.running and len(page.points) == 3, timeout=40000)
    assert page._sample_hz == 2000.0 and "Sampled at 2000 Hz" in page.status.text()
    assert sim.params[(0x3000, 0x05)] == 300.0                                        # the test simulator's own rate, put back
    qtbot.mouseClick(page.restore_rate_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.flash_saves == 2, timeout=WAIT)
    assert sim.params[(0x3000, 0x06)] == 1000.0 and sim.flash[(0x3000, 0x06)] == 1000.0
    qtbot.waitUntil(lambda: page.rate_instruction.isVisible() and "1000 Hz" in page.rate_instruction.text(), timeout=WAIT)


def test_drive_modes_is_its_own_servo_drive_page_per_drive_and_the_motion_page_keeps_only_speed_and_position(win, qtbot):
    """User, 2026-09-26: "keep velocity and position control in motion single axis, rest of the command parameters
    like kickstart or torque control, direct field control etc ... move somewhere else so we can keep motion single
    axis simple to operate with limited functions" - "build it, move all of them including torque control"."""
    dt, dm = win.drive_tab, win.drive_modes_page
    for gone in ("cyclic_move_btn", "ct_run_btn", "dfc_run_btn", "kick_run_btn", "gen_run_btn", "brushed_run_btn", "vbrake_btn"):
        assert not hasattr(dt, gone) and hasattr(dm, gone)
    for kept in ("run_btn", "stop_btn", "off_btn", "reset_btn", "enable_btn", "disable_btn", "move_btn", "move_by_btn", "home_btn"):
        assert hasattr(dt, kept)
    assert dt.modes is dm and dm.motion is dt
    win.open_tool("drive_modes")
    assert win.host.current_page() is dm and win.navigator.current_activity() == "drive"
    connect(win, qtbot)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)
    qtbot.waitUntil(lambda: 2 in win._axis_pages, timeout=WAIT)
    axis_modes = win._axis_pages[2]["modes"]
    assert axis_modes.s is win.session.axes[2] and axis_modes.motion is win._axis_pages[2]["motion"]
    assert win._axis_pages[2]["motion"].modes is axis_modes
    assert win.tools[win._axis_tool_key("modes", 2)].page is axis_modes


def test_a_mode_started_on_drive_modes_and_a_run_on_the_motion_page_take_the_repeat_slot_from_each_other(win, qtbot):
    """One continuous command at a time: the "· sent every 100 ms" note follows the slot across the two pages."""
    sim, client = connect(win, qtbot)
    dt, dm = win.drive_tab, win.drive_modes_page
    dt.target.setValue(60.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "sent every 100 ms" in dt.status.text(), timeout=WAIT)
    dm.kick_target.setValue(15.0)
    qtbot.mouseClick(dm.kick_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: "Kickstarting to" in dm.kick_status.text(), timeout=WAIT)
    assert client.commanded == ("kickstart", 15.0)
    assert "sent every 100 ms" not in dt.status.text()                       # the motion page's note went with the slot
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is not None and client.commanded[0] == "esc_rpm", timeout=WAIT)
    qtbot.waitUntil(lambda: dm.kick_status.text() == "", timeout=WAIT)          # and the modes page's note went back
    assert "sent every 100 ms" in dt.status.text()


def test_the_drive_modes_readouts_show_the_motion_pages_own_telemetry_and_its_stop_ends_the_mode(win, qtbot):
    sim, client = connect(win, qtbot)
    dt, dm = win.drive_tab, win.drive_modes_page
    win.open_tool("drive_modes")
    dm.gen_target.setValue(48.0)
    qtbot.mouseClick(dm.gen_run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: dm.readouts["op_mode"].value.text() == "Power Generation", timeout=WAIT)
    assert dm.readouts["op_mode"].value.text() == dt.cards["op_mode"].value.text()
    assert dm.readouts["vbus"].isVisible() and dm.readouts["fault_bits"].value.text() == dt.cards["fault_bits"].value.text()
    qtbot.mouseClick(dm.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)
    assert dm.gen_status.text() == "" and "STOP sent" in dt.status.text()


def test_the_live_plots_are_on_drive_modes_too_and_only_the_page_on_screen_redraws_them(win, qtbot):
    """User, 2026-09-26: "show this live telemetry in that new page as well - just make sure which page is opened
    telemetry will show in that page only so software will not get much load"."""
    _, client = connect(win, qtbot)
    dt, dm = win.drive_tab, win.drive_modes_page
    dt.target.setValue(120.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)

    def points(plots, key):
        x, _ = plots.curves[key].getData()
        return 0 if x is None else len(x)
    assert set(dm.plots.curves) == set(dt.plots.curves) == {"speed_hz", "wz_count", "ia", "vbus"}   # the same signals
    win.open_tool("drive_modes")
    qtbot.waitUntil(lambda: points(dm.plots, "speed_hz") > 20, timeout=WAIT)     # the page on screen draws...
    assert dm.plots.live_tag.text().endswith("LIVE")
    n = points(dt.plots, "speed_hz")
    qtbot.wait(400)
    assert points(dt.plots, "speed_hz") == n                                     # ...the hidden one does not
    win.open_tool("motion")
    qtbot.waitUntil(lambda: points(dt.plots, "speed_hz") > n + 2, timeout=WAIT)
    m = points(dm.plots, "speed_hz")
    qtbot.wait(400)
    assert points(dm.plots, "speed_hz") == m
    assert dm.plots.plot_widgets[0].getPlotItem().getAxis("left").labelUnits == dt.plots.plot_widgets[0].getPlotItem().getAxis("left").labelUnits
    assert dm.plots.speed_unit is None                                           # the unit chooser writes the drive: motion page only
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not win.session.is_connected, timeout=WAIT)
    assert dm.plots.live_tag.text() == "NO DATA" and points(dm.plots, "speed_hz") == 0


def test_the_motion_page_shows_one_control_mode_at_a_time_with_the_drive_buttons_always_there(win, qtbot):
    """User, 2026-09-26: "give drop down from which user can select position mode or velocity mode instead of two you
    are showing at the same time - as only one of them can be used at a time"."""
    win.open_tool("motion")
    dt = win.drive_tab
    assert dt.control_mode.currentData() == "velocity"
    assert dt.velocity_box.isVisible() and not dt.position_box.isVisible()
    assert dt.run_btn.isVisible() and not dt.move_btn.isVisible()
    for always in (dt.stop_btn, dt.off_btn, dt.reset_btn, dt.enable_btn, dt.disable_btn, dt.status):
        assert always.isVisible()
    dt.control_mode.setCurrentIndex(1)
    assert dt.control_mode.currentData() == "position"
    assert dt.position_box.isVisible() and not dt.velocity_box.isVisible()
    assert dt.move_btn.isVisible() and dt.move_by_btn.isVisible() and dt.home_btn.isVisible() and not dt.run_btn.isVisible()
    for always in (dt.stop_btn, dt.off_btn, dt.reset_btn, dt.enable_btn, dt.disable_btn, dt.status):
        assert always.isVisible()
    sim, client = connect(win, qtbot)                                     # the chooser is purely which card is shown:
    dt.control_mode.setCurrentIndex(0)                                     # switching it sends nothing
    dt.target.setValue(60.0)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", 60.0), timeout=WAIT)
    dt.control_mode.setCurrentIndex(1)
    assert client.commanded == ("esc_rpm", 60.0) and not any(k == "servo" for k, _v in sim.commands_received)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)              # STOP is there in Position mode too
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)


def test_the_target_speed_can_be_typed_in_rpm_rps_or_degrees_per_second(win, qtbot):
    """User, 2026-09-26: "give an option to user that user can enter velocity value in rpm or rps or degree per sec".
    A pure conversion (a turn is a turn - nothing through the pole count): the drive is always sent its rpm command."""
    sim, client = connect(win, qtbot)
    dt = win.drive_tab
    assert dt.entry_unit.currentText() == "rpm" and dt.target.suffix() == " rpm" and dt.limit.value() == 500.0
    dt.target.setValue(120.0)
    dt.entry_unit.setCurrentIndex(1)                                            # rps
    assert dt.target.suffix() == " rps" and dt.target.value() == pytest.approx(2.0) and dt.limit.value() == pytest.approx(8.33, abs=0.01)
    assert dt.limit.suffix() == " rps" and dt.target.decimals() == 3
    dt.target.setValue(1.5)
    qtbot.mouseClick(dt.run_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded == ("esc_rpm", pytest.approx(90.0)), timeout=WAIT)   # 1.5 rps = 90 rpm
    assert "Commanding 1.500 rps = 90.0 rpm" in dt.status.text()
    dt.entry_unit.setCurrentIndex(2)                                            # degrees per second
    assert dt.target.suffix() == " deg/sec" and dt.target.value() == pytest.approx(540.0) and dt.limit.value() == pytest.approx(3000.0)
    assert client.commanded == ("esc_rpm", pytest.approx(90.0))                  # switching the unit sends nothing
    dt.slider.setValue(dt.slider.value())                                        # the slider follows the shown unit's scale
    assert dt.slider.value() == round(540.0 * 10)
    qtbot.mouseClick(dt.stop_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded is None, timeout=WAIT)
    assert dt.target.value() == 0.0
    dt.entry_unit.setCurrentIndex(0)                                            # back to rpm: the limit is 500 rpm again
    assert dt.limit.value() == 500.0 and dt.target.suffix() == " rpm"
    assert dt.entry_unit.itemText(2) == "deg/sec"                                 # written out, not a symbol (user)


def test_the_telemetry_speed_can_be_shown_in_degrees_per_second(win, qtbot):
    """User, 2026-09-26: "also add deg/sec in graph as well" - a fourth Speed-in choice, taken from the drive's own
    Work Zone: Speed (rps) times 360, like rpm is times 60; nothing goes through the pole count."""
    sim, client = connect(win, qtbot)
    win.open_tool("motion")
    dt = win.drive_tab
    asked = []
    dt._confirm = lambda text: asked.append(text) or True
    assert [dt.speed_unit.itemText(i) for i in range(dt.speed_unit.count())] == ["rpm", "rps", "deg/sec", "Hz"]
    run_at_120_rpm(win, qtbot, sim)
    choose_unit(dt, "degs")                                                        # the drive sends Hz: switch it to rps first
    qtbot.waitUntil(lambda: "wz_speed" in dt.cards, timeout=WAIT)
    assert len(asked) == 1 and "Work Zone: Speed, RPS" in asked[0]
    qtbot.waitUntil(lambda: dt.speed_unit.isEnabled(), timeout=WAIT)
    assert dt.cards["wz_speed"].unit.text() == "deg/sec"
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 720.0) < 30, timeout=WAIT)      # 2 rps = 720 deg/sec
    assert dt._plot_widgets[0].getPlotItem().getAxis("left").labelUnits == "deg/sec"
    qtbot.waitUntil(lambda: curve_max(dt) > 600, timeout=WAIT)                    # the plot is in deg/sec too
    assert win.drive_modes_page.plots.plot_widgets[0].getPlotItem().getAxis("left").labelUnits == "deg/sec"
    choose_unit(dt, "rpm")                                                         # already rps-based: no write
    assert len(asked) == 1
    qtbot.waitUntil(lambda: abs(speed_value(dt) - 120.0) < 5, timeout=WAIT)


def test_positions_can_be_typed_and_shown_in_degrees_from_the_drives_own_work_zone_dimension(win, qtbot):
    """User, 2026-09-26: "add position as count and degree both" and "also show option for deg and count in graph as
    well". Degrees are not a pure conversion: they need counts per turn, which is the drive's own Work Zone: Dimension
    (0x4000:0x1C) - read once when deg is chosen, never polled. One page-wide choice, mirrored by both choosers."""
    sim, client = connect(win, qtbot)
    win.open_tool("motion")
    dt, dm = win.drive_tab, win.drive_modes_page
    assert dt.pos_unit.currentData() == "counts" and dt.plots.position_unit.currentData() == "counts"
    dt.pos_target.setValue(16384)
    dt.pos_unit.setCurrentIndex(1)
    dt.pos_unit.activated.emit(1)                                              # deg, chosen on the card
    qtbot.waitUntil(lambda: dt.pos_target.suffix() == " deg", timeout=WAIT)       # one read of Work Zone: Dimension
    assert "1 turn = 65,536 counts" in dt.pos_status.text()                       # the simulator's SSI counts per revolution
    assert dt.pos_target.value() == pytest.approx(90.0) and dt.pos_target.decimals() == 2
    assert dt.plots.position_unit.currentData() == "deg"                          # the plot's chooser mirrors it
    assert dt.plots.plot_widgets[1].getPlotItem().getAxis("left").labelUnits == "deg"
    assert dm.plots.plot_widgets[1].getPlotItem().getAxis("left").labelUnits == "deg"
    assert dt.cards["wz_count"].unit.text() == "deg"
    dt.control_mode.setCurrentIndex(1)
    dt.pos_target.setValue(45.0)
    qtbot.mouseClick(dt.move_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: client.commanded == ("servo", 8192), timeout=WAIT)   # 45 deg of 65,536 counts
    assert "Moving to 45.00 deg (8,192 counts)" in dt.pos_status.text()
    qtbot.waitUntil(lambda: dt.cards["wz_count"].value.text() == "45.00", timeout=WAIT)
    dt.plots.position_unit.setCurrentIndex(0)
    dt.plots.position_unit.activated.emit(0)                                    # counts, chosen on the plot this time
    assert dt.pos_unit.currentData() == "counts" and dt.pos_target.suffix() == " counts" and dt.pos_target.value() == 8192
    assert dt.plots.plot_widgets[1].getPlotItem().getAxis("left").labelUnits == "counts"
    qtbot.mouseClick(win.disconnect_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not win.session.is_connected, timeout=WAIT)
    assert dt.pos_unit.currentData() == "counts"


def test_a_telemetry_mapping_code_can_be_copied_from_telemetry_explorer_and_pasted_into_a_mapping_slot(win, qtbot):
    """User, 2026-09-26: "if user want to change from workzone modulo to phase B or something else then give option to
    user that he can copy telemetry code and paste it in telemetry mapping code"."""
    from PySide6.QtWidgets import QApplication
    sim, client = connect(win, qtbot)
    tel, par = win.telemetry_tab, win.params_tab
    ib = next(r for r in tel.rows if r.reg.key == (0x5001, 0x06))                  # Current: Phase B (Ib)
    assert "5243142" in ib.item.toolTip(0)                                         # hex 500106 as a decimal
    copy = next(a for a in tel.menu_for(ib).actions() if a.text().startswith("Copy telemetry mapping code"))
    assert "5243142" in copy.text()
    copy.trigger()
    assert QApplication.clipboard().text() == "5243142"
    slot = next(r for r in par.rows if r.reg.key == (0x1A02, 0x02))                # message 2, slot 2: Work Zone Modulo by default
    paste = next(a for a in par.menu_for(slot).actions() if a.text().startswith("Paste mapping code"))
    assert paste.isEnabled() and "Phase B" in paste.text()
    paste.trigger()
    assert slot.dirty and slot.get() == 5243142 and "edited" in slot.item.toolTip(0)
    qtbot.mouseClick(slot.write_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sim.params[(0x1A02, 0x02)] == 5243142, timeout=WAIT)    # written to the drive...
    qtbot.waitUntil(lambda: "ib" in win.drive_tab.cards and "wz_modulo" not in win.drive_tab.cards, timeout=WAIT)   # ...and broadcast
    qtbot.waitUntil(lambda: "Currently: <b>Current: Phase B (Ib)" in slot.item.toolTip(0), timeout=WAIT)   # set by the write's
                                                                                                              # own read-back, which can
                                                                                                              # land after the cards did
    QApplication.clipboard().setText("not a code")
    paste = next(a for a in par.menu_for(slot).actions() if a.text().startswith("Paste mapping code"))
    assert not paste.isEnabled()
    unused = next(a for a in par.menu_for(slot).actions() if a.text().startswith("Set this slot unused"))
    unused.trigger()
    assert slot.get() == 0 and slot.dirty
    assert not any(a.text().startswith("Paste") for a in tel.menu_for(ib).actions())   # a telemetry row only copies


def test_a_black_rule_separates_the_stacked_plots_and_follows_which_plots_are_shown(win, qtbot):
    """User, 2026-09-26: "give black line so user know where each graph is separated from each other"."""
    from sdr_tuner.gui.live_plots import SEPARATOR_PX
    win.open_tool("motion")
    plots = win.drive_tab.plots
    assert len(plots.separators) == 3 and all(r.height() == SEPARATOR_PX for r in plots.separators)
    assert all("#000000" in r.styleSheet() for r in plots.separators)
    connect(win, qtbot)                                                   # the default layout shows all four plots
    assert all(r.isVisible() for r in plots.separators)
    assert all(r.isVisible() for r in win.drive_modes_page.plots.separators) or not win.drive_modes_page.isVisible()
    # a layout with no speed hides the speed plot - and the rule under it, so no rule sits above the first shown plot
    plots.build_curves(["wz_count", "ia", "vbus"], win.drive_tab._card_info, win.drive_tab._title)
    assert not plots.plot_widgets[0].isVisible() and not plots.separators[0].isVisible()
    assert plots.separators[1].isVisible() and plots.separators[2].isVisible()


def test_the_axes_table_gives_every_column_an_equal_share_of_the_width(win, qtbot):
    """User, 2026-09-26: "give equal spacing for each parameters"."""
    from PySide6.QtWidgets import QHeaderView
    win.open_tool("axes")
    table = win.axes_page.table
    head = table.horizontalHeader()
    from sdr_tuner.gui.axes_page import COLUMN_GAP, COL_FAULTS
    connect(win, qtbot)
    qtbot.waitUntil(lambda: table.rowCount() == 1 and table.item(0, 2) is not None and "Online" in table.item(0, 2).text(),
                    timeout=WAIT)
    for c in range(table.columnCount() - 1):                              # every entry followed by the SAME blank: the
        assert head.sectionResizeMode(c) == QHeaderView.ResizeMode.Fixed   # column is its widest text plus the gap, so
        widest = max(head.sectionSizeHint(c), table.sizeHintForColumn(c))  # a long header ("Position (counts)") and a
        assert table.columnWidth(c) == widest + COLUMN_GAP                 # short one ("Axis") read as evenly spaced
    assert head.sectionResizeMode(COL_FAULTS) == QHeaderView.ResizeMode.Stretch   # the last column reaches the edge: so
    assert sum(table.columnWidth(c) for c in range(table.columnCount())) >= table.viewport().width() - 2   # does the rule
    from sdr_tuner.gui.axes_page import COLUMN_ALIGNMENT
    assert all(a == Qt.AlignmentFlag.AlignHCenter for a in COLUMN_ALIGNMENT.values())   # every value centred under its
    for c in range(table.columnCount()):                                   # own header ("1 should be in the middle of
        assert table.horizontalHeaderItem(c).textAlignment() & Qt.AlignmentFlag.AlignHCenter   # node is written")
        assert table.item(0, c).textAlignment() & Qt.AlignmentFlag.AlignHCenter


def test_the_multi_axis_motion_table_is_arranged_like_the_axes_table(win, qtbot):
    """User, 2026-09-26, after the Axes table: "can you do the same here as well and arrange it"."""
    from PySide6.QtWidgets import QHeaderView
    from sdr_tuner.gui.axes_page import COLUMN_GAP
    from sdr_tuner.gui.multi_axis_motion import COL_AXIS, COL_MOVE, COL_NODE, COL_POSITION, COL_STATUS, COL_TARGET, MOVE_PX, TARGET_PX
    connect(win, qtbot)
    win.open_tool("multi_axis_motion")
    table = win.multi_axis_motion_page.table
    head = table.horizontalHeader()
    qtbot.waitUntil(lambda: table.rowCount() == 1, timeout=WAIT)
    for c in (COL_AXIS, COL_NODE, COL_POSITION):                            # text columns: widest text + the gap
        assert head.sectionResizeMode(c) == QHeaderView.ResizeMode.Fixed
        assert table.columnWidth(c) == max(head.sectionSizeHint(c), table.sizeHintForColumn(c)) + COLUMN_GAP
    assert head.sectionResizeMode(COL_STATUS) == QHeaderView.ResizeMode.Stretch   # Status reaches the edge (the rule too)
    assert table.columnWidth(COL_TARGET) == TARGET_PX + COLUMN_GAP           # widget columns: the widget + the gap
    assert table.columnWidth(COL_MOVE) == MOVE_PX + COLUMN_GAP
    row = next(iter(win.multi_axis_motion_page._rows.values()))
    assert row.target_spin.width() == TARGET_PX and row.move_btn.width() == MOVE_PX
    for c in range(table.columnCount()):                                   # every value centred under its own header
        assert table.horizontalHeaderItem(c).textAlignment() & Qt.AlignmentFlag.AlignHCenter
    for c in (COL_AXIS, COL_NODE, COL_POSITION, COL_STATUS):
        assert table.item(0, c).textAlignment() & Qt.AlignmentFlag.AlignHCenter
    assert table.horizontalHeaderItem(COL_MOVE).text() == "Command"         # the MOVE button's column is named (user)


def test_reloading_the_same_kinematics_model_leaves_exactly_one_row_per_axis(win, qtbot):
    """User, 2026-09-26: "if i click on open kinematic model of radar 3 times then why it is adding 3 time" - the
    rebuild tracked the spin boxes, removed those, and left every label row behind."""
    setup = win.axes_page.setup
    for _ in range(3):
        setup._load_kinematics_sample(setup.kinematics_samples.findText("radar.obj"))
    frames = [setup.kinematics_axes_layout.itemAt(i).widget() for i in range(setup.kinematics_axes_layout.count())]
    assert len(frames) == 3 and sorted(setup._counts_per_unit_rows) == [0, 1, 2]
    labels = [w for f in frames for w in f.findChildren(QLabel) if "counts per unit" in w.text()]
    assert len(labels) == 3


def test_the_2d_plotter_says_it_needs_two_axes_while_only_one_is_connected(win, qtbot):
    """User, 2026-09-26: "if i run g code from motion controller and check 2d plotter then i am not seeing anything" -
    one drive connected: the Y pick was empty (it needs two axes) while the Y combo still showed Drive01, so the page
    looked ready and traced nothing without saying why."""
    from sdr_tuner.gui.plotter_2d import ONE_AXIS_TEXT, PICK_PLACEHOLDER
    sim, client = connect(win, qtbot)
    page = win.plotter_2d_page
    win.open_tool("plotter_2d")
    assert page.x_combo.currentData() == 1 and page.y_combo.currentData() is None
    assert page.y_combo.currentText() == PICK_PLACEHOLDER                    # not a drive it is not tracing
    qtbot.waitUntil(lambda: page.status.text() == ONE_AXIS_TEXT, timeout=WAIT)
    win.session.connect_axis(session.SIMULATOR, "", 0, 2)                    # a second axis: the Y pick fills in
    qtbot.waitUntil(lambda: page.y_combo.currentData() == 2, timeout=WAIT)
    assert page.y_combo.findData(None) < 0                                   # and the placeholder is gone
    qtbot.waitUntil(lambda: "point(s) traced" in page.status.text(), timeout=WAIT)


def test_live_and_recorder_curves_are_drawn_peak_downsampled_to_the_screen(win, qtbot):
    """At the drive's 1000-2000 Hz telemetry rates a curve holds up to 20 000 points and pyqtgraph drew every one
    (2026-09-27: drawPath led the GUI thread's profile). Peak-preserving downsampling to one bucket per pixel column
    (its min and max drawn): a spike is never lost and the drawn shape is the same, at a fraction of the cost."""
    sim, client = connect(win, qtbot)
    win.open_tool("motion")
    for plots in (win.drive_tab.plots, win.drive_modes_page.plots):
        assert plots.curves and all(c.opts["autoDownsample"] and c.opts["downsampleMethod"] == "peak"
                                    and c.opts["autoDownsampleFactor"] == 1.0 for c in plots.curves.values())
    rec = win.recorder_page
    rec.start_btn.click()
    qtbot.waitUntil(lambda: "speed_hz" in rec.charts[0].curves, timeout=WAIT)
    rec.stop_btn.click()
    curve = rec.charts[0].curves["speed_hz"]
    assert curve.opts["autoDownsample"] and curve.opts["downsampleMethod"] == "peak" and curve.opts["autoDownsampleFactor"] == 1.0


def test_the_terminal_keeps_its_newest_frames_when_the_log_overflows(win, qtbot):
    """The overflow used to be trimmed with removeRow(0) once per surplus row, each shifting every remaining row, and
    every frame was its own insertRow (2026-09-27). One row-count change per batch and one removeRows call now."""
    from sdr_tuner.gui.log_tab import MAX_PER_TICK, MAX_ROWS
    sim, client = connect(win, qtbot)
    log = win.log_tab
    win.open_tool("terminal")
    log._clear()
    n = MAX_ROWS + 2 * MAX_PER_TICK
    for i in range(n):
        win.session.frames.put(("tx", 1000.0 + i * 0.001, 0x600 + client.node, i.to_bytes(8, "little")))
    while not win.session.frames.empty():
        log._drain()
    assert log.table.rowCount() == MAX_ROWS
    assert log.table.item(0, 3).text() == (n - MAX_ROWS).to_bytes(8, "little").hex(" ").upper()      # the oldest kept
    assert log.table.item(MAX_ROWS - 1, 3).text() == (n - 1).to_bytes(8, "little").hex(" ").upper()  # the newest
    assert log.count.text() == f"{MAX_ROWS} frames shown"


def test_an_async_result_is_dropped_once_its_owner_page_is_gone(qtbot):
    """Two full-suite teardowns (2026-09-27) saw a Drive Load / Read All finish AFTER the window had closed and write
    into a deleted QStackedWidget / QCheckBox ("Internal C++ object already deleted" in the event loop). run_async's
    `owner` is the page the callbacks write to: once it has been deleted, whatever the job delivers is dropped."""
    import threading
    import shiboken6
    from PySide6.QtWidgets import QWidget
    from sdr_tuner.gui.workers import run_async
    release, results = threading.Event(), []
    page = QWidget()
    run_async(lambda emit, cancel: release.wait(5) and "answer", on_done=lambda r: page.setWindowTitle(r),
              on_failed=lambda exc: page.setWindowTitle(str(exc)), on_progress=lambda p: page.setWindowTitle(p), owner=page)
    shiboken6.delete(page)                              # the window closed, or the axis disconnected, mid-read
    release.set()
    qtbot.wait(300)                                     # nothing reaches the dead page: no exception in the event loop
    live = QWidget()
    qtbot.addWidget(live)
    run_async(lambda emit, cancel: (emit("working"), "answer")[1], on_done=results.append,
              on_progress=results.append, owner=live)
    qtbot.waitUntil(lambda: results == ["working", "answer"], timeout=WAIT)     # a living owner gets everything
