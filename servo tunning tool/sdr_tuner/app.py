"""Application entry point."""
import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from . import APP_TITLE, __version__

APP_ICON_PATH = Path(__file__).parent / "data" / "app_icon.png"


def _loading_splash(progress=0.08, status="Starting the control workspace…"):
    pixmap = QPixmap(620, 270)
    pixmap.fill(QColor("#0C0D10"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    background = QLinearGradient(0, 0, 620, 270)
    background.setColorAt(0, QColor("#17140D"))
    background.setColorAt(0.48, QColor("#101114"))
    background.setColorAt(1, QColor("#0C0D10"))
    painter.setBrush(background)
    painter.setPen(QPen(QColor("#8F7420"), 1))
    painter.drawRoundedRect(1, 1, 618, 268, 16, 16)
    painter.setPen(QPen(QColor("#D4AF37"), 3))
    painter.drawLine(28, 24, 76, 24)
    icon = QPixmap(str(APP_ICON_PATH)).scaled(124, 124, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation)
    painter.setPen(QPen(QColor("#4C3E19"), 1))
    painter.setBrush(QColor("#121318"))
    painter.drawEllipse(30, 66, 132, 132)
    painter.drawPixmap(38, 74, icon)
    painter.setPen(QColor("#D4AF37"))
    painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
    painter.drawText(194, 82, "STIMITA MOTION CONTROL")
    painter.setPen(QColor("#EDE7D9"))
    painter.setFont(QFont("Segoe UI", 21, QFont.Weight.DemiBold))
    painter.drawText(192, 121, "SDR Servo Tuning Tool")
    painter.setPen(QColor("#77736A"))
    painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
    painter.drawText(194, 146, "SERVO DRIVE  ·  TUNING  ·  MONITORING")
    painter.setPen(QColor("#B8B2A6"))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(194, 181, status)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#27282C"))
    painter.drawRoundedRect(194, 199, 386, 5, 2.5, 2.5)
    painter.setBrush(QColor("#D4AF37"))
    painter.drawRoundedRect(194, 199, int(386 * max(0.0, min(1.0, progress))), 5, 2.5, 2.5)
    painter.setPen(QColor("#77736A"))
    painter.setFont(QFont("Segoe UI", 8))
    painter.drawText(194, 232, f"VERSION {__version__}")
    painter.drawText(476, 232, "INITIALIZING")
    painter.end()
    return pixmap


class LoadingSplash(QSplashScreen):
    def set_stage(self, progress, status):
        self.setPixmap(_loading_splash(progress, status))
        self.repaint()


def main(argv=None):
    sys.setswitchinterval(0.001)     # GUI painting must not starve the CAN threads
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--sim", action="store_true", help="connect to the built-in simulated drive on start-up")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_TITLE)
    icon = QIcon(str(APP_ICON_PATH))
    app.setWindowIcon(icon)
    splash = LoadingSplash(_loading_splash())
    splash.show()
    app.processEvents()
    splash.set_stage(0.12, "Loading control modules…")
    from .gui.theme import apply_theme
    from .gui.main_window import MainWindow
    apply_theme(app)
    window = MainWindow(splash.set_stage)
    window.setWindowIcon(icon)
    window.showMaximized()
    splash.finish(window)
    if args.sim:
        window.connect_simulator()      # selects the Simulator explicitly - the saved interface may be a real one
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
