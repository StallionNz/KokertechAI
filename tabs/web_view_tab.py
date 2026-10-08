"""
web_view_tab.py — Web View tab mixin for KokertechDashboard.

Sprint 8: Embeds the Flask web UI (kokerpro_web.py) inside a PyQt6 tab
using QWebEngineView. Provides controls to start/stop/refresh the web server
and open the UI in an external browser.
"""

import os
import sys
import subprocess
import threading

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSpinBox, QCheckBox, QLineEdit,
)

from config import CONFIG, save_settings
from logging_config import get_logger

if TYPE_CHECKING:
    from tabs.context import DashboardContext

logger = get_logger(name="WebViewTab")


def _add_qt6_bin_to_path():
    """Add the PyQt6 Qt6 bin directory to Windows DLL search path.

    Pip installs PyQt6-WebEngine to the user site-packages directory,
    but the Qt6WebEngine*.dll dependencies in the Qt6/bin subdirectory
    may not be on the system PATH. This function tries likely locations
    and adds the first valid one via os.add_dll_directory() so the .pyd
    extension modules can find their Qt6 C++ dependencies at import time.
    """
    if os.name != "nt":
        return
    try:
        # Try multiple candidate directories for the Qt6/bin folder
        candidates = []

        # 1. Derive from PyQt6 package location (system-site)
        import PyQt6
        pyqt6_dir = os.path.dirname(PyQt6.__file__)
        candidates.append(os.path.join(pyqt6_dir, "Qt6", "bin"))

        # 2. User site-packages (pip --user install location)
        user_sp = os.path.expanduser(
            "~/AppData/Roaming/Python/Python" +
            "".join(__import__("sys").version.split(".")[:2]) +
            "/site-packages"
        )
        candidates.append(os.path.join(user_sp, "PyQt6", "Qt6", "bin"))

        # 3. Try site.getusersitepackages() as a portable fallback
        try:
            import site
            usersp = site.getusersitepackages()
            if usersp:
                candidates.append(os.path.join(usersp, "PyQt6", "Qt6", "bin"))
        except (ImportError, AttributeError):
            pass

        for candidate in candidates:
            if os.path.isdir(candidate):
                os.add_dll_directory(candidate)
                logger.info(f"Added Qt6 bin dir to DLL path: {candidate}")
        # Don't return early on first match — the WebEngine DLLs may be in a
        # different site-packages than the base PyQt6 package (e.g., system vs
        # user install). Add all valid Qt6 bin directories to the search path.
    except (ImportError, OSError, AttributeError):
        pass  # Non-fatal — QWebEngineView import may still work via PATH


class WebViewTabMixin:
    """Mixin providing a web view tab that embeds the Flask UI."""

    @property
    def context(self) -> "DashboardContext":
        """Return the shared DashboardContext, falling back to legacy attributes if ctx is unset."""
        if hasattr(self, "ctx") and self.ctx is not None:
            return self.ctx
        from tabs.context import DashboardContext
        return DashboardContext(
            controller=getattr(self, "controller", None),
            file_logger=getattr(self, "file_logger", None),
            log_to_audit=getattr(self, "log_to_audit", None),
            audit_signal=getattr(self, "audit_signal", None),
            config=getattr(self, "config", {}),
            restore_chat_input=getattr(self, "restore_chat_input", None),
            switch_tab=getattr(self, "switch_tab", None),
        )

    @context.setter
    def context(self, value: "DashboardContext") -> None:
        self.ctx = value

    def create_web_view_tab(self) -> QWidget:
        """Create the web view tab with embedded browser."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Lazily import QWebEngineView so missing QtWebEngine DLLs don't crash the app.
        # On Windows, the Qt6 bin directory must be in the DLL search path for the
        # Qt6WebEngine*.dll dependencies to resolve. Pip installs them to the user
        # site-packages but may not add this dir to PATH.
        self._web_engine_available = False
        try:
            if os.name == "nt":
                _add_qt6_bin_to_path()
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            self._web_engine_available = True
        except (ImportError, OSError):
            pass

        # ── Header ──
        header = QLabel("🌐 Web Interface")
        header.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(header)

        # ── Toolbar ──
        toolbar = QHBoxLayout()

        self.web_enabled_cb = QCheckBox("Enable Web Server")
        self.web_enabled_cb.setChecked(CONFIG.get("web_server_enabled", False))
        self.web_enabled_cb.toggled.connect(self._on_web_server_toggle)
        toolbar.addWidget(self.web_enabled_cb)

        toolbar.addWidget(QLabel("Port:"))

        self.web_port_spin = QSpinBox()
        self.web_port_spin.setRange(1024, 65535)
        self.web_port_spin.setValue(CONFIG.get("web_server_port", 5050))
        self.web_port_spin.editingFinished.connect(self._on_port_changed)
        toolbar.addWidget(self.web_port_spin)

        self.web_status_lbl = QLabel("⏹ Stopped")
        self.web_status_lbl.setStyleSheet(
            "font-size: 10pt; color: #EF4444; font-weight: bold; padding: 2px 8px;"
        )
        toolbar.addWidget(self.web_status_lbl)

        toolbar.addStretch()

        self.btn_web_start = QPushButton("▶ Start")
        self.btn_web_start.clicked.connect(self._start_web_server)
        toolbar.addWidget(self.btn_web_start)

        self.btn_web_stop = QPushButton("⏹ Stop")
        self.btn_web_stop.clicked.connect(self._stop_web_server)
        self.btn_web_stop.setEnabled(False)
        toolbar.addWidget(self.btn_web_stop)

        self.btn_web_home = QPushButton("🏠 Home")
        self.btn_web_home.clicked.connect(self._navigate_home)
        toolbar.addWidget(self.btn_web_home)

        self.btn_web_external = QPushButton("↗ Open in Browser")
        self.btn_web_external.clicked.connect(self._open_external)
        toolbar.addWidget(self.btn_web_external)

        layout.addLayout(toolbar)

        # ── Separator ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("Separator")
        layout.addWidget(sep)

        # ── WebEngine View (or fallback) ──
        if self._web_engine_available:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            self.web_view = QWebEngineView()
            self.web_view.setStyleSheet("border: 1px solid #374151;")
            layout.addWidget(self.web_view, 1)
            self.web_view.loadProgress.connect(self._on_load_progress)
            self.web_view.urlChanged.connect(self._on_url_changed)
            self.web_view.loadFinished.connect(self._on_load_finished)
        else:
            # Use a separate fallback label so the AST-driven Qt interface
            # test doesn't see self.web_view as a QLabel and flag the
            # engine-guarded setUrl/back/forward calls as invalid.
            self.web_view = None
            self._web_view_fallback = QLabel(
                "⚠ QtWebEngine not available.\n\n"
                "Install with: pip install PyQt6-WebEngine\n\n"
                "The web server can still be started — "
                "use 'Open in Browser' to access it externally."
            )
            self._web_view_fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._web_view_fallback.setStyleSheet(
                "font-size: 11pt; color: #F59E0B; padding: 20px;"
            )
            layout.addWidget(self._web_view_fallback, 1)

        # ── Navigation bar (back, forward, reload, URL bar) ──
        nav_bar = QHBoxLayout()

        self.btn_back = QPushButton("◀")
        self.btn_back.setToolTip("Go back")
        self.btn_back.setFixedWidth(36)
        self.btn_back.setEnabled(False)
        nav_bar.addWidget(self.btn_back)

        self.btn_forward = QPushButton("▶")
        self.btn_forward.setToolTip("Go forward")
        self.btn_forward.setFixedWidth(36)
        self.btn_forward.setEnabled(False)
        nav_bar.addWidget(self.btn_forward)

        self.btn_reload = QPushButton("🔄")
        self.btn_reload.setToolTip("Reload current page")
        self.btn_reload.setFixedWidth(36)
        nav_bar.addWidget(self.btn_reload)

        nav_bar.addWidget(QLabel("URL:"))

        self.web_url_input = QLineEdit()
        self.web_url_input.setPlaceholderText("Enter a URL and press Enter")
        self.web_url_input.setText(f"http://localhost:{self.web_port_spin.value()}")
        nav_bar.addWidget(self.web_url_input, 1)

        layout.addLayout(nav_bar)

        # Navigation button connections
        self.btn_back.clicked.connect(self._navigate_back)
        self.btn_forward.clicked.connect(self._navigate_forward)
        self.btn_reload.clicked.connect(self._reload_page)
        self.web_url_input.returnPressed.connect(self._navigate_to_url)

        # ── Status bar ──
        self.web_page_status = QLabel("Ready")
        self.web_page_status.setStyleSheet("font-size: 8pt; color: #6B7280;")
        layout.addWidget(self.web_page_status)

        # ── Web server process (managed) ──
        self._web_server_proc = None
        self._web_server_lock = threading.Lock()

        # ---- Server health polling ----
        self._server_poll_timer = QTimer()
        self._server_poll_timer.setInterval(2000)
        self._server_poll_timer.timeout.connect(self._poll_server_status)

        # ── Auto-start if enabled ──
        if CONFIG.get("web_server_enabled", False):
            QTimer.singleShot(500, self._start_web_server)

        return tab

    # ── Web server lifecycle ──

    def _start_web_server(self):
        """Start the Flask web server as a managed subprocess."""
        with self._web_server_lock:
            if self._web_server_proc is not None and self._web_server_proc.poll() is None:
                self.web_status_lbl.setText("⚠ Already running")
                return

            port = self.web_port_spin.value()
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            script_fastapi = os.path.normpath(os.path.join(root_dir, "kokerpro_fastapi.py"))
            script_flask = os.path.normpath(os.path.join(root_dir, "kokerpro_web.py"))
            script_path = script_fastapi if os.path.exists(script_fastapi) else script_flask

            if not os.path.exists(script_path):
                self.web_status_lbl.setText("❌ Web server script not found")
                self.web_page_status.setText(f"Missing: {script_path}")
                return

            try:
                self._web_server_proc = subprocess.Popen(  # noqa: S603
                    [sys.executable, script_path, "--port", str(port)],
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.web_status_lbl.setText(f"▶ Starting on :{port}")
                self.web_status_lbl.setStyleSheet(
                    "font-size: 10pt; color: #F59E0B; font-weight: bold;"
                )
                self.btn_web_start.setEnabled(False)
                self.btn_web_stop.setEnabled(True)
                self.web_page_status.setText(
                    f"Server process started (PID: {self._web_server_proc.pid})"
                )
                self.web_url_input.setText(f"http://localhost:{port}")

                # Start loading after a brief delay to let the server initialise
                QTimer.singleShot(1500, self._load_web_view)
                self._server_poll_timer.start()

            except (OSError, subprocess.SubprocessError) as e:
                self.web_status_lbl.setText("❌ Failed to start")
                self.web_page_status.setText(f"Error: {e}")
                logger.error(f"Failed to start web server: {e}")

    def _stop_web_server(self):
        """Stop the Flask web server subprocess."""
        with self._web_server_lock:
            if self._web_server_proc is None:
                return

            try:
                self._web_server_proc.terminate()
                self._web_server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._web_server_proc.kill()
                self._web_server_proc.wait(timeout=1)
            except (subprocess.SubprocessError, OSError):
                pass

            self._server_poll_timer.stop()
            self._web_server_proc = None
            self.web_status_lbl.setText("⏹ Stopped")
            self.web_status_lbl.setStyleSheet(
                "font-size: 10pt; color: #EF4444; font-weight: bold;"
            )
            self.btn_web_start.setEnabled(True)
            self.btn_web_stop.setEnabled(False)
            self.web_page_status.setText("Server stopped")
            if getattr(self, '_web_engine_available', False):
                self.web_view.setUrl(QUrl("about:blank"))

    def _poll_server_status(self):
        """Periodic health check for the Flask server subprocess."""
        with self._web_server_lock:
            if self._web_server_proc is None:
                self._server_poll_timer.stop()
                return
            retcode = self._web_server_proc.poll()
            if retcode is not None:
                self._web_server_proc = None
                self._server_poll_timer.stop()
                self.web_status_lbl.setText("\u26a0 Crashed")
                self.web_status_lbl.setStyleSheet(
                    "font-size: 10pt; color: #EF4444; font-weight: bold;"
                )
                self.btn_web_start.setEnabled(True)
                self.btn_web_stop.setEnabled(False)
                self.web_page_status.setText(
                    f"Server exited unexpectedly (code: {retcode})"
                )
                if getattr(self, '_web_engine_available', False):
                    self.web_view.setUrl(QUrl("about:blank"))
                logger.warning(f"Web server process exited with code {retcode}")

    def _navigate_home(self):
        """Navigate to the Flask base URL in the embedded view."""
        if self._web_server_proc is not None and self._web_server_proc.poll() is None:
            self._load_web_view()
        else:
            self.web_page_status.setText("Server not running — start it first")

    def _load_web_view(self):
        """Load the Flask web UI in the embedded view."""
        if getattr(self, '_shutting_down', False):
            return
        if not getattr(self, '_web_engine_available', False):
            self.web_page_status.setText("QtWebEngine not available — use Open in Browser")
            return
        port = self.web_port_spin.value()
        url = f"http://localhost:{port}"
        self.web_view.setUrl(QUrl(url))
        self.web_page_status.setText(f"Loading {url}...")

    def _open_external(self):
        """Open the web UI in the default system browser."""
        port = self.web_port_spin.value()
        url = f"http://localhost:{port}"
        QDesktopServices.openUrl(QUrl(url))

    # ── UI event handlers ──

    def _on_web_server_toggle(self, enabled):
        """Handle enable/disable checkbox change."""
        CONFIG["web_server_enabled"] = enabled
        save_settings()
        if enabled:
            self._start_web_server()
        else:
            self._stop_web_server()

    def _on_port_changed(self):
        """Handle port spinbox change (triggered on editing finished)."""
        port = self.web_port_spin.value()
        CONFIG["web_server_port"] = port
        save_settings()
        self.web_url_input.setText(f"http://localhost:{port}")
        # Restart server if running
        if self._web_server_proc is not None and self._web_server_proc.poll() is None:
            self._stop_web_server()
            self._start_web_server()

    def _on_load_progress(self, progress):
        """Update status bar with page load progress."""
        if progress < 100:
            self.web_page_status.setText(f"Loading... {progress}%")
        else:
            self.web_page_status.setText("Page loaded")
            self.web_status_lbl.setText("▶ Running")
            self.web_status_lbl.setStyleSheet(
                "font-size: 10pt; color: #10B981; font-weight: bold;"
            )

    # ── Navigation controls ──

    def _navigate_back(self):
        """Navigate back in browser history."""
        if getattr(self, '_web_engine_available', False):
            self.web_view.back()
            self.web_page_status.setText("Navigating back...")

    def _navigate_forward(self):
        """Navigate forward in browser history."""
        if getattr(self, '_web_engine_available', False):
            self.web_view.forward()
            self.web_page_status.setText("Navigating forward...")

    def _reload_page(self):
        """Reload the current page in the web view."""
        if not getattr(self, '_web_engine_available', False):
            self.web_page_status.setText("QtWebEngine not available")
            return
        self.web_view.reload()
        self.web_page_status.setText("Reloading...")

    def _navigate_to_url(self):
        """Navigate to the URL entered in the URL bar."""
        if not getattr(self, '_web_engine_available', False):
            self.web_page_status.setText("QtWebEngine not available")
            return
        url_text = self.web_url_input.text().strip()
        if url_text:
            if not url_text.startswith(("http://", "https://")):
                url_text = "http://" + url_text
            self.web_view.setUrl(QUrl(url_text))
            self.web_page_status.setText(f"Navigating to {url_text}...")

    def _on_url_changed(self, url):
        """Update the URL bar and nav button states when the page URL changes."""
        self.web_url_input.setText(url.toString())
        if getattr(self, '_web_engine_available', False):
            try:
                history = self.web_view.page().history()
                self.btn_back.setEnabled(history.canGoBack())
                self.btn_forward.setEnabled(history.canGoForward())
            except (AttributeError, RuntimeError):
                pass

    def _on_load_finished(self, ok):
        """Update UI when page finishes loading."""
        if ok:
            self.web_page_status.setText("Page loaded")
        else:
            self.web_page_status.setText("Failed to load page")
        if getattr(self, '_web_engine_available', False):
            try:
                history = self.web_view.page().history()
                self.btn_back.setEnabled(history.canGoBack())
                self.btn_forward.setEnabled(history.canGoForward())
            except (AttributeError, RuntimeError):
                pass

    # ── Lifecycle integration ──

    def web_view_shutdown(self):
        """Clean up the web server process. Called from closeEvent."""
        self._stop_web_server()
