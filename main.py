"""
main.py - Primary entry point for KokertechAI.
Launches the PyQt6 desktop GUI dashboard (app_core.py).
"""
import sys
import os

# Ensure workspace root is in sys.path
WORKSPACE = os.path.dirname(os.path.abspath(__file__))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon, QGuiApplication
from app_core import KokertechDashboard


def main():
    # Set explicit AppUserModelID on Windows for taskbar icon separation
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kokertech.ai.dashboard.v2")
    except (AttributeError, OSError):  # noqa: S110
        pass

    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, TypeError):
        pass

    app = QApplication(sys.argv)
    icon_path = os.path.join(WORKSPACE, "kokertech.ico")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)

    window = KokertechDashboard()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
