"""
scripts/capture_ui_preview.py - Capture offscreen UI screenshot for documentation.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from app_core import KokertechDashboard


def capture_preview(output_path: str = "assets/dashboard_preview.png") -> bool:
    """Instantiate the main dashboard and grab a high-fidelity screenshot."""
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, TypeError):
        pass

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    window = KokertechDashboard()
    window.resize(1280, 820)
    window.show()

    # Allow event loop to process layouts, fonts, and stylesheets
    for _ in range(10):
        app.processEvents()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    def _do_grab():
        pixmap = window.grab()
        success = pixmap.save(output_path)
        if success:
            print(f"Screenshot successfully saved to {output_path}")
        else:
            print(f"Failed to save screenshot to {output_path}")
        window.close()
        app.quit()

    QTimer.singleShot(600, _do_grab)
    app.exec()
    return True


if __name__ == "__main__":
    capture_preview()
