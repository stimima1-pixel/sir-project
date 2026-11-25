"""Black & gold theme: warm near-black surfaces, ivory text, champagne-gold accents."""
import tempfile
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QFrame

BG = "#0C0D10"          # window
PANEL = "#14161B"       # cards
RAISED = "#1B1E25"      # buttons
INPUT = "#0F1114"       # text inputs
LINE = "#2A2D36"        # hairlines
TEXT = "#EDE7D9"        # warm ivory
MUTED = "#9C978B"
DIM = "#6A665D"
GOLD = "#D4AF37"        # metallic gold
GOLD_HI = "#EBCB6B"
GOLD_LO = "#8F7420"
EMERALD = "#38C48F"
RUBY = "#DA4A56"
RUBY_LO = "#8E2432"
SAPPHIRE = "#6EA8F0"
AMBER = "#E8AA3E"
ROSE = "#E77F8A"
AMETHYST = "#B592EC"

BRAND_FONTS = ["Didot", "Bodoni 72", "Playfair Display", "Georgia", "Times New Roman"]
MONO_FONTS = ["SF Mono", "Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"]

_QSS = """
QWidget { color: @TEXT@; font-size: 13px; }
QMainWindow, QDialog, QWidget#root { background: @BG@; }
QToolTip { background: @RAISED@; color: @TEXT@; border: 1px solid @GOLD_LO@; padding: 5px 8px; }
QLabel { background: transparent; }
QLabel[muted="true"] { color: @MUTED@; }

QFrame#header { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1A150A, stop:0.55 #101013, stop:1 @BG@);
                border-bottom: 1px solid @GOLD_LO@; }
QLabel#brand { color: @GOLD@; }
QLabel#product { color: @MUTED@; font-size: 10px; font-weight: 700; }
QLabel#pill { border-radius: 11px; padding: 4px 14px; font-size: 11px; font-weight: 800;
              background: #16181D; border: 1px solid @LINE@; color: @MUTED@; }
QLabel#pill[state="ok"] { color: #06231A; background: @EMERALD@; border-color: @EMERALD@; }
QLabel#pill[state="warn"] { color: #2A1B02; background: @AMBER@; border-color: @AMBER@; }
QLabel#pill[state="error"] { color: #FFFFFF; background: @RUBY@; border-color: @RUBY@; }

QTabWidget::pane { border: 0; border-top: 1px solid @LINE@; background: @BG@; }
QTabBar { background: @BG@; }
QTabBar::tab { background: transparent; color: @MUTED@; padding: 11px 26px; margin: 0 2px; border: 0;
               border-bottom: 2px solid transparent; font-size: 12px; font-weight: 700; }
QTabBar::tab:selected { color: @GOLD@; border-bottom: 2px solid @GOLD@; }
QTabBar::tab:hover:!selected { color: @TEXT@; }

QGroupBox { background: @PANEL@; border: 1px solid @LINE@; border-radius: 10px; margin-top: 16px; padding: 18px 14px 14px 14px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 14px; padding: 0 8px;
                   color: @GOLD@; font-size: 11px; font-weight: bold; }

QPushButton { background: @RAISED@; border: 1px solid @LINE@; border-radius: 7px; padding: 8px 18px; color: @TEXT@; font-weight: 600; }
QPushButton:hover { border-color: @GOLD_LO@; color: @GOLD_HI@; }
QPushButton:pressed { background: #121418; }
QPushButton:disabled { color: @DIM@; background: #111317; border-color: #1C1F25; }
QPushButton[size="small"] { padding: 3px 10px; font-size: 11px; border-radius: 5px; }
QPushButton#removeRow { padding: 3px 4px; font-size: 12px; border-radius: 5px; }
QPushButton[role="primary"] { color: #17110A; border: 1px solid @GOLD@; font-weight: 800;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 @GOLD_HI@, stop:1 @GOLD_LO@); }
QPushButton[role="primary"]:hover { color: #000000;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #F6DC8C, stop:1 #A8862A); }
QPushButton[role="primary"]:disabled { color: #6E6446; border-color: #2E2818; background: #1F1B10; }
QPushButton[role="danger"] { color: #FFFFFF; border: 1px solid @RUBY@; font-weight: 800; font-size: 15px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #E5606B, stop:1 @RUBY_LO@); }
QPushButton[role="danger"]:hover { color: #FFFFFF; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #F0727C, stop:1 #A02A39); }
QPushButton[role="danger"]:disabled { color: #7A5A5E; border-color: #3A1C21; background: #221316; }

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: @INPUT@; border: 1px solid @LINE@; border-radius: 6px; padding: 5px 9px;
    selection-background-color: @GOLD@; selection-color: #17110A; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: @GOLD@; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled { color: @DIM@; background: #0D0F12; border-color: #1B1D23; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox::down-arrow { image: url("@ICON_DOWN@"); width: 12px; height: 8px; margin-right: 8px; }
QComboBox QAbstractItemView { background: @RAISED@; border: 1px solid @GOLD_LO@; outline: 0;
                              selection-background-color: #2C2410; selection-color: @GOLD_HI@; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button { width: 0; border: 0; }

QTreeWidget, QTableWidget { background: @PANEL@; alternate-background-color: #171A20; border: 1px solid @LINE@;
    border-radius: 10px; gridline-color: #20232A; outline: 0; }
QTreeWidget::item { padding: 2px 4px; border-bottom: 1px solid #1B1E24; }
QTableWidget::item { padding: 2px 6px; }
QTreeWidget::item:selected, QTableWidget::item:selected { background: #2A2412; color: @GOLD_HI@; }
QHeaderView::section { background: #101216; color: @GOLD@; border: 0; border-bottom: 1px solid @GOLD_LO@;
                       padding: 8px 8px; font-size: 11px; font-weight: 800; }
QHeaderView { background: #101216; }
QLabel#readOnlyBadge { color: #06202F; background: @SAPPHIRE@; border-radius: 9px; padding: 3px 10px; font-size: 10px; font-weight: 800; }

QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: #30343D; border-radius: 4px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: @GOLD_LO@; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:horizontal { background: #30343D; border-radius: 4px; min-width: 36px; }
QScrollBar::handle:horizontal:hover { background: @GOLD_LO@; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSlider::groove:horizontal { height: 4px; background: #252830; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @GOLD_LO@; border-radius: 2px; }
QSlider::handle:horizontal { background: @GOLD@; width: 16px; height: 16px; margin: -7px 0; border-radius: 8px; border: 2px solid #17110A; }
QSlider::handle:horizontal:hover { background: @GOLD_HI@; }
QSlider::handle:horizontal:disabled { background: #4A4432; }

QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid @DIM@; background: @INPUT@; }
QCheckBox::indicator:hover { border-color: @GOLD@; }
QCheckBox::indicator:checked { image: url("@ICON_CHECK@"); background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 @GOLD_HI@, stop:1 @GOLD_LO@); border-color: @GOLD@; }

QProgressBar { background: @INPUT@; border: 1px solid @LINE@; border-radius: 4px; max-height: 8px; color: transparent; }
QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 @GOLD_LO@, stop:1 @GOLD_HI@); border-radius: 3px; }

QStatusBar { background: #090A0C; color: @MUTED@; border-top: 1px solid @LINE@; }
QStatusBar::item { border: 0; }

QFrame#readout { background: @PANEL@; border: 1px solid @LINE@; border-top: 2px solid @GOLD_LO@; border-radius: 10px; }
QLabel#readoutTitle { color: @MUTED@; font-size: 10px; font-weight: 800; }
QLabel#readoutValue { color: @TEXT@; font-size: 26px; font-weight: 600; }
QLabel#readoutValue[state="fault"] { color: @RUBY@; }
QLabel#readoutUnit { color: @GOLD@; font-size: 12px; font-weight: 700; }
QFrame#plotcard { background: @PANEL@; border: 1px solid @LINE@; border-radius: 10px; }
QLabel#led { background: #22252C; border: 1px solid #343843; border-radius: 7px;
             min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; }
QLabel#led[on="true"] { border: 1px solid #FF9CA5;
    background: qradialgradient(cx:0.4, cy:0.35, radius:0.75, fx:0.4, fy:0.35, stop:0 #FFB0B7, stop:1 @RUBY@); }
QLabel#faultSummary { font-size: 12px; font-weight: 800; color: @EMERALD@; }
QLabel#faultSummary[state="fault"] { color: @RUBY@; }
QLabel#ctlStatus { font-size: 12px; color: @MUTED@; }
QLabel#ctlStatus[state="ok"] { color: @EMERALD@; }
QLabel#ctlStatus[state="error"] { color: @RUBY@; }
QLabel#ctlStatus[state="warn"] { color: @AMBER@; }
QLabel#liveTag { font-size: 10px; font-weight: 800; color: @MUTED@; }
QLabel#liveTag[state="ok"] { color: @EMERALD@; }
QLabel#liveTag[state="warn"] { color: @AMBER@; }

/* ---- studio window: ribbon, navigator, page header, status bar ---- */
QFrame#ribbon { background: #0A0B0D; }
QFrame#ribbonBar { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1A150A, stop:0.45 #0E0F12, stop:1 #08090B);
                   min-height: 36px; max-height: 36px; }
QTabBar#ribbonTabs { background: transparent; }
QTabBar#ribbonTabs::tab { background: transparent; color: @MUTED@; padding: 8px 20px; margin: 0; border: 0;
                          border-top: 2px solid transparent; font-size: 13px; font-weight: 600; }
QTabBar#ribbonTabs::tab:hover:!selected { color: @TEXT@; }
QTabBar#ribbonTabs::tab:selected { color: @GOLD_HI@; background: #15171C; border-top: 2px solid @GOLD@; }
QFrame#ribbonBody { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #15171C, stop:1 #101216);
                    border-bottom: 1px solid @GOLD_LO@; }
QFrame#ribbonGroup { background: transparent; border: 0; border-right: 1px solid #262931; }
QLabel#ribbonCaption { color: @DIM@; font-size: 11px; }
QToolButton#ribbonButton { background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 5px 6px 3px 6px;
                           color: @TEXT@; font-size: 12px; }
QToolButton#ribbonButton:hover { background: #1F2229; border-color: @GOLD_LO@; }
QToolButton#ribbonButton:pressed { background: #14161A; }
QToolButton#ribbonButton:disabled { color: @DIM@; }
QToolButton#ribbonButton:checked { background: #2A2412; border-color: @GOLD@; color: @GOLD_HI@; }
QToolButton#ribbonSmall { background: transparent; border: 0; color: @MUTED@; font-size: 14px; padding: 4px 8px; }
QToolButton#ribbonSmall:hover { color: @GOLD_HI@; }
QToolButton#ribbonCollapse { background: transparent; border: 0; padding: 4px 8px; }
QPushButton#stopButton { border-radius: 39px; border: 3px solid #4A1119; color: #FFFFFF; font-size: 17px; font-weight: 900; padding: 0;
    background: qradialgradient(cx:0.4, cy:0.3, radius:0.95, fx:0.35, fy:0.25, stop:0 #F27A84, stop:0.55 @RUBY@, stop:1 @RUBY_LO@); }
QPushButton#stopButton:hover { border-color: #FF9CA5;
    background: qradialgradient(cx:0.4, cy:0.3, radius:0.95, fx:0.35, fy:0.25, stop:0 #FF929A, stop:0.55 #EA5A66, stop:1 #A02A39); }
QPushButton#stopButton:pressed { background: @RUBY_LO@; }

QFrame#navigator { background: #08090B; border-right: 1px solid @LINE@; }
QLabel#navTitle { color: @GOLD@; font-size: 16px; font-weight: 800; padding: 2px 4px; }
QToolButton#navCollapse { background: transparent; border: 0; padding: 2px 4px; border-radius: 4px; }
QToolButton#navCollapse:hover { background: #1F2229; }
QTreeWidget#navTree { background: #0F1114; border: 1px solid @LINE@; border-radius: 6px; padding: 4px; }
QTreeWidget#navTree::item { padding: 4px 4px; border: 0; }
QTreeWidget#navTree::item:selected { background: #2A2412; color: @GOLD_HI@; }
QListWidget#toolList { background: #0F1114; border: 1px solid @LINE@; border-radius: 6px; padding: 3px; outline: 0; }
QListWidget#toolList::item { padding: 6px 8px; border-radius: 4px; color: @TEXT@; }
QListWidget#toolList::item:hover { background: #1B1E25; }
QListWidget#toolList::item:selected { background: #2A2412; color: @GOLD_HI@; }
QPushButton#activityButton { text-align: left; padding: 8px 12px; border: 1px solid @LINE@; border-radius: 6px; background: @RAISED@;
                             color: @TEXT@; font-size: 13px; font-weight: 600; }
QPushButton#activityButton:hover { border-color: @GOLD_LO@; color: @GOLD_HI@; }
QPushButton#activityButton:checked { background: #2A2412; border-color: @GOLD@; color: @GOLD_HI@; }
QPushButton#activityButton[collapsed="true"] { text-align: center; padding: 8px 0; }   /* icon only: centred - with the
                                          expanded 12px left padding the 24px icon sat 6px right of centre */

QFrame#pageHeader { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1B1D23, stop:1 #111318); border-bottom: 1px solid @GOLD_LO@; }
QLabel#pageTitle { color: @GOLD@; font-size: 19px; font-weight: 700; }
QLabel#pageCrumb { color: @MUTED@; font-size: 13px; padding-left: 12px; border-left: 1px solid @LINE@; }
QLabel#pageTarget { color: @TEXT@; font-size: 13px; padding-left: 12px; border-left: 1px solid @LINE@; }

/* ---- dockable multi-pane workspace (Drive Setup and Motion only, "Docked View") ---- */
QDockWidget { background: @PANEL@; border: 1px solid @LINE@; }
QDockWidget::title { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1B1D23, stop:1 #111318);
                     border-bottom: 1px solid @GOLD_LO@; color: @GOLD@; padding: 6px 8px; font-weight: 700; font-size: 12px; }
QDockWidget::close-button, QDockWidget::float-button { background: transparent; border: 0; padding: 2px; }
QDockWidget::close-button:hover, QDockWidget::float-button:hover { background: #1F2229; border-radius: 3px; }
QWidget#dockTitle { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1B1D23, stop:1 #111318);
                    border-bottom: 1px solid @GOLD_LO@; }
QLabel#dockTitleLabel { color: @GOLD@; font-weight: 700; font-size: 12px; background: transparent; }
QToolButton#dockTitleButton { color: @MUTED@; background: transparent; border: 0; border-radius: 3px; font-size: 12px; }
QToolButton#dockTitleButton:hover { background: #1F2229; color: @GOLD_HI@; }
QScrollArea#dockScroll, QScrollArea#dockScroll > QWidget > QWidget { background: @BG@; }

QFrame#bottomBar { background: #090A0C; border-top: 1px solid @LINE@; min-height: 26px; }
QLabel#bottomMessage { color: @MUTED@; font-size: 12px; }
QLabel#bottomMessage[level="warn"] { color: @AMBER@; }
QLabel#bottomMessage[level="error"] { color: @RUBY@; }

QMenu { background: @RAISED@; border: 1px solid @GOLD_LO@; padding: 4px; }
QMenu::item { padding: 6px 26px 6px 8px; border-radius: 4px; color: @TEXT@; }
QMenu::item:selected { background: #2C2410; color: @GOLD_HI@; }
QMenu::indicator { width: 14px; height: 14px; margin-left: 6px; border: 1px solid @DIM@; border-radius: 3px; background: @INPUT@; }
QMenu::indicator:checked { image: url("@ICON_CHECK@"); border-color: @GOLD@;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 @GOLD_HI@, stop:1 @GOLD_LO@); }
QToolButton#chartMenu { background: @RAISED@; border: 1px solid @LINE@; border-radius: 6px; padding: 3px 10px; color: @TEXT@;
                        font-size: 11px; font-weight: 600; }
QToolButton#chartMenu:hover { border-color: @GOLD_LO@; color: @GOLD_HI@; }
QToolButton#chartMenu::menu-indicator { image: none; }

QFrame#gridGroup { background: transparent; }
QPushButton#gridHeader { text-align: left; background: #1B1E25; color: @GOLD@; font-weight: 800; border: 0; border-radius: 0;
                         border-bottom: 1px solid @LINE@; padding: 7px 14px; }
QPushButton#gridHeader:hover { color: @GOLD_HI@; border-color: @LINE@; }
QFrame#gridRow { background: transparent; border: 0; border-bottom: 1px solid #1B1E24; }
QFrame#gridDivider { background: #1B1E24; }
QLabel#gridKey { color: @TEXT@; font-size: 13px; padding: 5px 0; }
QLabel#gridValue { color: @TEXT@; font-size: 13px; padding: 5px 9px 5px 0; }

/* ---- tuning wizards ---- */
QLabel#groupTitle { color: @GOLD@; font-size: 11px; font-weight: 800; padding: 14px 0 4px 0; border-bottom: 1px solid @LINE@; }
QLabel#formName { color: @TEXT@; font-size: 13px; padding: 3px 0; }
QLabel#formName[state="warn"] { color: @AMBER@; }
QLabel#formName[state="error"] { color: @RUBY@; }
QLabel#formUnits { color: @MUTED@; font-size: 12px; }
QLabel#formNote { color: @MUTED@; font-size: 12px; }
QFrame#tunerCard { background: @PANEL@; border: 1px solid @LINE@; border-radius: 10px; }
QLabel#cardTitle { color: @GOLD@; font-size: 11px; font-weight: 800; }
QLabel#checkList { font-size: 13px; }
QLabel#bigStatus { font-size: 15px; font-weight: 700; color: @TEXT@; }
QLabel#bigStatus[state="ok"] { color: @EMERALD@; }
QLabel#bigStatus[state="warn"] { color: @AMBER@; }
QLabel#bigStatus[state="error"] { color: @RUBY@; }
QTreeWidget#pageTree { background: #0F1114; border: 1px solid @LINE@; border-radius: 6px; padding: 4px; }
QTreeWidget#pageTree::item { padding: 6px 4px; border: 0; }
QTreeWidget#pageTree::item:selected { background: #2A2412; color: @GOLD_HI@; }
QFrame#tunerBar { background: #0E1013; border-top: 1px solid @LINE@; }
"""


def _icons():
    """Draw the small arrow / tick images used by the style sheet (needs a QGuiApplication)."""
    folder = Path(tempfile.gettempdir()) / "sdr_tuner_icons"
    folder.mkdir(exist_ok=True)
    paths = {}
    for name, size in (("down", (12, 8)), ("check", (12, 12))):
        for scale in (1, 2):
            pix = QPixmap(size[0] * scale, size[1] * scale)
            pix.fill(Qt.GlobalColor.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.scale(scale, scale)
            if name == "down":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(GOLD))
                p.drawPolygon(QPolygonF([QPointF(1, 1), QPointF(11, 1), QPointF(6, 7)]))
            else:
                p.setPen(QPen(QColor("#17110A"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                p.drawPolyline(QPolygonF([QPointF(2.5, 6.5), QPointF(5, 9), QPointF(9.5, 3.5)]))
            p.end()
            file = folder / (f"{name}.png" if scale == 1 else f"{name}@2x.png")
            pix.save(str(file))
            if scale == 1:
                paths[name] = file.as_posix()
    return paths


def stylesheet():
    qss = _QSS
    for name, path in _icons().items():
        qss = qss.replace(f"@ICON_{name.upper()}@", path)
    for name, value in globals().items():
        if name.isupper() and isinstance(value, str) and value.startswith("#"):
            qss = qss.replace(f"@{name}@", value)
    return qss


def brand_font(size, spacing=0.0, weight=QFont.Weight.DemiBold):
    font = QFont()
    font.setFamilies(BRAND_FONTS)
    font.setPixelSize(size)
    font.setWeight(weight)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def mono_font(size):
    font = QFont()
    font.setFamilies(MONO_FONTS)
    font.setPixelSize(size)
    return font


def apply_theme(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: INPUT, QPalette.ColorRole.AlternateBase: PANEL,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: RAISED, QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.ToolTipBase: RAISED, QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.Highlight: GOLD, QPalette.ColorRole.HighlightedText: "#17110A",
        QPalette.ColorRole.PlaceholderText: DIM, QPalette.ColorRole.Link: GOLD_HI,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet())
    pg.setConfigOptions(antialias=True, background=PANEL, foreground=MUTED)


class _PlotMouse(QObject):
    """Wheel, pinch and double-click over a styled plot. (1) Lets the page scroll when the wheel turns over a plot
    whose own mouse zoom is off: pyqtgraph's axes and view box accept every wheel event even with the mouse disabled,
    so a plot inside a scrolling page stopped the page dead wherever the pointer sat over it - worst along the
    frequency axis, where there is nothing to zoom (user, 2026-09-25, a screenshot of the Bode plots' axis rows: "if
    you keep mouse pointer then scroll up and down will not work"). Ignoring the event and reporting it filtered
    makes Qt hand it to the parent, the page's scroll area. A plot that turns its mouse back on (Recorder, 2D
    Plotter) keeps the wheel for zooming. (2) A plot set up with enable_zoom() (the Bode and Nyquist plots, small
    enough to fit one screen - user, the same day: "make sure we can zoom the graph as we make graph size small")
    zooms only with Ctrl (Cmd on a Mac) held, or with a trackpad pinch (a native zoom gesture, which pyqtgraph does
    not handle), and a double-click calls its reset; its plain wheel still goes to the page, like (1)."""

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.Wheel, QEvent.Type.NativeGesture, QEvent.Type.MouseButtonDblClick):
            return False
        widget = watched if hasattr(watched, "getPlotItem") else watched.parent()
        try:
            view = widget.getPlotItem().getViewBox()
        except AttributeError:
            return False
        modifier_zoom = getattr(widget, "zoom_with_modifier", False)
        if kind == QEvent.Type.Wheel:
            if not any(view.state["mouseEnabled"]) or (
                    modifier_zoom and not event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                event.ignore()
                return True
            return False
        if not modifier_zoom:
            return False
        if kind == QEvent.Type.NativeGesture:
            if event.gestureType() != Qt.NativeGestureType.ZoomNativeGesture:
                return False
            scale = 1.0 / max(0.05, 1.0 + event.value())                 # fingers apart: value > 0, a narrower view
            centre = view.mapSceneToView(widget.mapToScene(event.position().toPoint()))
            view.scaleBy((scale, scale), centre)
            return True
        reset = getattr(widget, "reset_view", None)                      # a double-click
        if reset is None:
            return False
        reset()
        return True


_plot_mouse = _PlotMouse()


def enable_zoom(plot_widget, reset):
    """A styled plot inside a scrolling page that can be looked into: drag to pan, Ctrl/Cmd + wheel or a trackpad
    pinch to zoom, a double-click calls `reset` (the plot's own way back to its full view). The PLAIN wheel keeps
    scrolling the page (_PlotMouse) - the reason the zoom needs the modifier at all."""
    plot_widget.getPlotItem().setMouseEnabled(x=True, y=True)
    plot_widget.zoom_with_modifier = True
    plot_widget.reset_view = reset


def style_plot(plot_widget, label, units="", min_span=None):
    """Dark card look for a pyqtgraph PlotWidget. min_span: smallest y-range auto-scaling may zoom into."""
    plot_widget.setFrameShape(QFrame.Shape.NoFrame)
    plot_widget.installEventFilter(_plot_mouse)
    plot_widget.viewport().installEventFilter(_plot_mouse)
    plot_widget.setBackground(PANEL)
    item = plot_widget.getPlotItem()
    item.showGrid(x=True, y=True, alpha=0.13)
    item.hideButtons()
    item.setMenuEnabled(False)
    item.setMouseEnabled(x=False, y=False)
    for side in ("left", "bottom"):
        axis = item.getAxis(side)
        axis.setPen(pg.mkPen(LINE))
        axis.setTextPen(pg.mkPen(MUTED))
    item.getAxis("left").setWidth(86)
    if min_span:
        item.vb.setLimits(minYRange=min_span)
    item.getAxis("left").enableAutoSIPrefix(False)
    item.setLabel("left", label, units=units, **{"color": MUTED, "font-size": "9pt"})
