"""Shared pytest fixtures for KokertechAI tests."""

import gc
import os
import sys
import tempfile
import warnings
import pytest
from unittest.mock import patch

# Suppress ResourceWarning from Python internals during pytest teardown.
# Python's capture mechanism replaces sys.stdout/stderr with temporary wrappers.
# During teardown, these wrappers are restored, but the old temporary objects
# may not be properly closed (Python 3.14 stricter FD lifecycle). The warning
# originates from <sys>:0 with cp1252 encoding (Windows internals) and is not
# a project bug. Suppressing it prevents exit code 1 on test files that would
# otherwise pass cleanly.
warnings.filterwarnings("ignore", category=ResourceWarning, module="sys")

# ---------------------------------------------------------------------------
# Pytest 9 / Python 3.14 stability: stdout flush after stream lifecycle
# ---------------------------------------------------------------------------
#
# Reference: https://github.com/pytest-dev/pytest/issues/14528
#
# Root cause: Python 3.14 enforces stricter file descriptor lifecycle
# management. When pytest's capture mechanism replaces sys.stdout/stderr,
# the original stream objects' underlying FDs may be closed by the time
# capture teardown restores them. pytest.console_main() then calls
# sys.stdout.flush() on the restored (but broken) stream object,
# raising: ValueError: I/O operation on closed file
#
# Mitigations in this file:
#   1. _safe_flush: wraps flush() to catch ValueError/OSError
#   2. _reapply_safe_flush: re-applies wrappers AFTER capture teardown,
#      called from pytest_unconfigure (before console_main)
#   3. FDCapture method wrapping in pytest_configure: catches crashes
#      in FDCapture.snap(), .start(), and .resume()
#   4. _reset_all_shared_state(aggressive_qt_cleanup=False): avoids the
#      crash during cross-file teardown by skipping aggressive Qt event
#      processing (only when capture is active)
#
# See also: pytest.ini comments and the pytest_unconfigure docstring.
# ---------------------------------------------------------------------------
# Test hang prevention: timer patches, closeEvent patch, and timeout config
# ---------------------------------------------------------------------------
#
# Problem: Tests that create a KokertechDashboard would hang indefinitely
# due to background QTimers and blocking I/O in closeEvent.
#
# Mitigations in the shared_dashboard fixture:
#   5. Timer setup patches: setup_vram_monitor, setup_cleanup_timer,
#      setup_consolidation_timer, setup_daily_summary_timer are replaced
#      with MagicMock no-ops before Dashboard creation. This prevents
#      background QTimers (vram 2s, cleanup 30s, consolidation 5min,
#      daily summary) from starting and keeping the process alive.
#   6. Non-blocking closeEvent patch: _test_close_event replaces the real
#      closeEvent which performs heavy synchronous cleanup that can hang
#      indefinitely on Windows due to file/SQLite locks. See the KNOWN
#      LIMITATION block in shared_dashboard for full details.
#   7. WHY app.quit() CANNOT BE USED IN TEARDOWN: Calling app.quit() sets
#      the internal closingDown flag on the session-scoped QApplication.
#      Once closingDown is True, Qt's QWidget::create() silently returns
#      early, leaving subsequent widgets with no native handle. The
#      _suppress_quit flag prevents this during closeEvent.
#
# Mitigation in pytest.ini:
#   8. pytest-timeout: timeout=60, timeout_method=thread (Windows-compatible).
#      Catches any remaining hangs that the patches don't cover.
#
# See also: pytest.ini comments and the KNOWN LIMITATION block in
# shared_dashboard.
# ---------------------------------------------------------------------------
_original_stdout_flush = getattr(sys.stdout, "flush", None)
_original_stderr_flush = getattr(sys.stderr, "flush", None)

def _safe_flush(stream_flush):
    def _inner(*args, **kwargs):
        try:
            return stream_flush(*args, **kwargs)
        except (ValueError, OSError):
            return None
    return _inner

if callable(_original_stdout_flush):
    sys.stdout.flush = _safe_flush(_original_stdout_flush)
if callable(_original_stderr_flush):
    sys.stderr.flush = _safe_flush(_original_stderr_flush)


def _reapply_safe_flush():
    """Re-apply safe flush wrappers AFTER pytest capture teardown.

    pytest's capture mechanism replaces sys.stdout/stderr during the session.
    When it restores them, the original stream objects may have closed FDs
    (Python 3.14 stricter FD lifecycle). This re-applies the safe wrappers
    so console_main's final sys.stdout.flush() does not crash.
    """
    if callable(getattr(sys.stdout, "flush", None)):
        sys.stdout.flush = _safe_flush(sys.stdout.flush)
    if callable(getattr(sys.stderr, "flush", None)):
        sys.stderr.flush = _safe_flush(sys.stderr.flush)


# ---------------------------------------------------------------------------
# Native dependency guard (Windows stability)
# ---------------------------------------------------------------------------
#
# The test runner has shown Windows fatal access violations during import of:
#   pyarrow -> pandas -> sklearn
#
# If these imports cannot be performed safely in the current environment,
# skip tests that rely on that native stack so the suite can still run.
#
# Notes:
# - If the interpreter AVs (process crash), Python cannot execute this guard.
# - In many cases these failures manifest as ImportError/Exception, which
#   we can catch and convert into skips.
# ---------------------------------------------------------------------------

NATIVE_STACK_AVAILABLE = True
NATIVE_STACK_IMPORT_ERROR = None


def _probe_native_stack():
    global NATIVE_STACK_AVAILABLE, NATIVE_STACK_IMPORT_ERROR
    try:
        import pyarrow  # noqa: F401
        import pandas  # noqa: F401
        import sklearn  # noqa: F401
    except Exception as e:
        NATIVE_STACK_AVAILABLE = False
        NATIVE_STACK_IMPORT_ERROR = repr(e)


def pytest_collection_modifyitems(config, items):
    # Lightweight probe once per collection.
    global NATIVE_STACK_AVAILABLE
    if "NATIVE_STACK_PROBED" not in globals():
        globals()["NATIVE_STACK_PROBED"] = True
        _probe_native_stack()

    if NATIVE_STACK_AVAILABLE:
        pass  # native stack available — skip check
    else:
        reason = (
            "Native stack (pyarrow/pandas/sklearn) unavailable or unsafe to import "
            f"in this environment: {NATIVE_STACK_IMPORT_ERROR}"
        )
        skip_native = pytest.mark.skip(reason=reason)
        for item in items:
            if item.get_closest_marker("native_stack") is not None:
                item.add_marker(skip_native)

    # --ignore-known-failures: convert @pytest.mark.known_failure to skip
    if config.getoption("--ignore-known-failures", default=False):
        skip_known = pytest.mark.skip(reason="known_failure marker — suppressed by --ignore-known-failures")
        for item in items:
            if item.get_closest_marker("known_failure") is not None:
                item.add_marker(skip_known)


# ---------------------------------------------------------------------------
# Custom ini options
# ---------------------------------------------------------------------------


def pytest_addoption(parser):
    """Register custom ini options and CLI flags."""
    parser.addini(
        "requires_api",
        type="bool",
        default=False,
        help="If true, run tests that require a live AI provider (e.g. ToolUseAgent E2E tests).",
    )
    parser.addini(
        "test_drop_threshold",
        type="string",
        default="5",
        help="Max test-count drop before the suite is failed (used by the CI gate).",
    )
    parser.addoption(
        "--ignore-known-failures",
        action="store_true",
        default=False,
        help="Skip tests marked @pytest.mark.known_failure. "
             "Off by default; tests with this marker run normally unless flag is passed.",
    )


# ---------------------------------------------------------------------------
# Session-scoped: redirect test logs to a temp file so the production
# execution_log.txt is not polluted with init/shutdown cycles from tests.
# ---------------------------------------------------------------------------


def pytest_configure(config):
    """
    Run before test collection begins — redirect test logs to a separate file
    so the production execution_log.txt is not polluted with init/shutdown
    cycles from tests.

    NOTE: The pytest-qt plugin (which causes hangs between fixture yield and
    test function execution) is blocked via ``-p no:pytest-qt`` in pytest.ini.
    The plugin's entry point name is ``pytest-qt``, NOT ``qt`` — so
    ``get_plugin("qt")`` and ``-p no:qt`` are both silent no-ops.
    """
    # Set Qt to offscreen platform so PyQt6 tests don't pop up visible
    # Python GUI windows on the desktop during test runs.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # Prevent global keyboard hooks (and related background threads) during pytest.
    # This avoids Windows instability observed during full-suite runs.
    os.environ.setdefault("KOKERTECH_DISABLE_KEYBOARD", "1")

    import config as cfg
    import logging_config

    # Point ALL references to LOG_FILE to a test-specific file.
    # logging_config imports LOG_FILE at module level from config, so we must
    # update BOTH refs to ensure get_logger() picks up the test path.
    #
    # KOKERTECH_TEST_LOG_PATH (env var) takes precedence so CI / orchestrated
    # runs can direct logs to a known path. Default is a per-PID unique
    # filename in the system temp dir to avoid OS-level handle-lock
    # collisions when two pytest sessions overlap (the previous fixed-name
    # `execution_log_TEST.txt` would otherwise fight for an exclusive WRITE
    # handle on Windows and cause `Permission denied` errors during
    # long-running batches).
    env_path = os.environ.get("KOKERTECH_TEST_LOG_PATH")
    if env_path:
        test_log = env_path
    else:
        test_log = os.path.join(
            tempfile.gettempdir(),
            f"execution_log_TEST_{os.getpid()}.txt",
        )
    cfg.LOG_FILE = test_log
    logging_config.LOG_FILE = test_log
    # Do not reset logger handlers during the test run; repeated handler
    # teardown/recreation has shown instability on Windows when combined with
    # pytest terminal output capture.

    # Suppress heavy background imports during tests
    os.environ.setdefault("KOKERTECH_SKIP_EMBEDDINGS", "1")

    # ---------------------------------------------------------------------
    # Pytest 9 / Python 3.14 stability: capture tmpfile method wrapping
    # ---------------------------------------------------------------------
    # Large test batches (20+ files) can trigger:
    #   ValueError: I/O operation on closed file
    # when the capture tempfile's fd has been closed by Python 3.14's
    # tighter file lifecycle. The crash manifests at three points:
    #
    #   1. FDCapture.snap()   -> self.tmpfile.seek(0)  [20+ files, teardown]
    #   2. FDCaptureBase.start() -> self.tmpfile.fileno() [40+ files, collection]
    #   3. FDCaptureBase.resume() -> self.tmpfile.fileno() [40+ files, collection]
    #
    # Approach B: wrap each crashing method individually rather than
    # wrapping the tmpfile's methods directly (which isn't possible on
    # C-extension types like ``io.BufferedRandom`` that ``TemporaryFile``
    # returns). The wrappers catch ``(ValueError, OSError)`` and return
    # a safe fallback (empty bytes for snap, do-nothing for start/resume).
    #
    # ``FDCaptureBase.done()`` accepts ("initialized", "started",
    # "suspended", "done") states, so if start() fails and leaves state
    # as "initialized", done() still cleans up correctly.
    #
    # Sibling mitigations:
    #   - ``_safe_flush`` (top of file) catches session-exit flush crash
    #   - ``aggressive_qt_cleanup=False`` catches cross-file teardown crash
    # ---------------------------------------------------------------------
    try:
        import _pytest.capture

        # -- Patch 1: FDCapture.snap() -> tmpfile.seek/read/truncate
        _orig_snap = _pytest.capture.FDCapture.snap
        def _safe_fdcapture_snap(self):
            try:
                return _orig_snap(self)
            except (ValueError, OSError):
                return b""
        _pytest.capture.FDCapture.snap = _safe_fdcapture_snap

        # -- Patch 2: FDCaptureBase.start() -> tmpfile.fileno() during dup2
        _orig_start = _pytest.capture.FDCaptureBase.start
        def _safe_fdcapture_start(self):
            try:
                return _orig_start(self)
            except (ValueError, OSError):
                pass  # Leave state as "initialized" — done() accepts this
        _pytest.capture.FDCaptureBase.start = _safe_fdcapture_start

        # -- Patch 3: FDCaptureBase.resume() -> tmpfile.fileno() during dup2
        _orig_resume = _pytest.capture.FDCaptureBase.resume
        def _safe_fdcapture_resume(self):
            try:
                return _orig_resume(self)
            except (ValueError, OSError):
                pass  # Leave state as "suspended" — snap() accepts this
        _pytest.capture.FDCaptureBase.resume = _safe_fdcapture_resume

    except Exception:
        pass

    # -- Patch 4: TerminalWriter.flush() -> _file.flush() on closed FD
    try:
        import _pytest._io
        _orig_tw_flush = _pytest._io.TerminalWriter.flush
        def _safe_tw_flush(self):
            try:
                return _orig_tw_flush(self)
            except (ValueError, OSError):
                return None
        _pytest._io.TerminalWriter.flush = _safe_tw_flush
    except Exception:
        pass

    # Create the test log file header
    try:
        with open(test_log, "w", encoding="utf-8") as f:
            f.write("# Test log — redirected from execution_log.txt\n")
            f.write(f"# PID: {os.getpid()}\n\n")
    except Exception:
        pass

    # ---------------------------------------------------------------------
    # Bypass FileHandler during tests (external OS-level handle lock)
    # ---------------------------------------------------------------------
    # Some Windows hosts keep a write handle on the redirected test log even
    # after a fresh path is used, producing `Permission denied` on every
    # FileHandler append during test runs. To keep the suite runnable, we
    # monkeypatch logging.FileHandler.__init__ to a no-op for the test
    # session, so logger calls are accepted by Python's logging layer but
    # no longer touch the filesystem. Restored in pytest_unconfigure.
    import logging
    _orig_fh_init = logging.FileHandler.__init__
    def _noop_fh_init(self, *args, **kwargs):
        # Replace self.stream with a write-only in-memory sink.
        try:
            _orig_fh_init(self, *args, **kwargs)
        except Exception:
            pass
        # Always replace stream with a discard buffer to avoid real I/O.
        # Close the original stream first to prevent ResourceWarning from
        # unclosed file handles (triggers -W error::RuntimeWarning in pytest.ini).
        old_stream = getattr(self, 'stream', None)
        try:
            self.stream = open(os.devnull, "w", encoding="utf-8")
        except Exception:
            pass
        if old_stream is not None:
            try:
                old_stream.close()
            except Exception:
                pass
        # Close the devnull stream on FileHandler close to avoid
        # ResourceWarning: unclosed file <_io.TextIOWrapper name='nul'>
        _orig_close = self.close
        def _close_with_devnull_cleanup(*a, **kw):
            try:
                _orig_close(*a, **kw)
            except Exception:
                pass
            try:
                if hasattr(self, 'stream') and self.stream is not None:
                    self.stream.close()
                    self.stream = None
            except Exception:
                pass
        self.close = _close_with_devnull_cleanup
    logging.FileHandler.__init__ = _noop_fh_init


def pytest_unconfigure(config):
    """Restore streams and log paths after tests.

    Re-apply safe flush wrappers to stdout/stderr AFTER pytest's
    capture teardown restores the original stream objects.
    This prevents the ValueError: I/O operation on closed file crash
    in console_main (Python 3.14 / pytest 9 FD lifecycle issue #14528).

    Also runs the production-log RuntimeWarning watchdog: if any test
    inadvertently inflated the production ``execution_log.txt`` count
    (e.g. a smoke-style invocation that wrote phantom RuntimeWarning
    lines), the new ``reset_runtime_warning_count`` helper archives the
    phantoms to ``<log_path>.archived`` so the count returns to its
    pre-session baseline. Without this, the audit count would climb
    monotonically across test runs until an actual rotation script ran.
    """
    _reapply_safe_flush()  # prevent console_main crash (issue #14528)
    import config as cfg
    import logging_config
    orig = os.path.join(cfg.WORKSPACE_DIR, "execution_log.txt")
    cfg.LOG_FILE = orig
    logging_config.LOG_FILE = orig

    # Production-log RuntimeWarning watchdog: archive any phantom lines the
    # test session pumped into the real log so it does not inflate forever.
    try:
        from utils.log_scanner import reset_runtime_warning_count
        moved = reset_runtime_warning_count(orig)
        if moved and moved > 0:
            sys.stderr.write(
                f"\n[conftest WATCHDOG] archived {moved} phantom "
                f"RuntimeWarning line(s) from {orig} -> {orig}.archived "
                f"(audit count reset to baseline)\n"
            )
            sys.stderr.flush()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_loggers_for_test():
    """No-op: keep loggers stable during the test run."""
    yield


@pytest.fixture(autouse=True)
def _reset_rag_inrun_cache_per_test():
    """Cross-file defense for the agentic_rag in-run cache (Phase D 15.3).

    Within-file isolation is handled by ``test_agentic_rag.py::TestRetrieval``
    + ``TestFullPipeline`` setUp + tearDown (which work under both ``pytest``
    and ``unittest`` runners). This autouse fixture is the **cross-file**
    defense: clears the module-level cache BEFORE every test, so a stale
    entry from any prior test (in any test file) cannot leak into the next.
    Sister resetter: ``_reset_rag_inrun_cache()`` in
    ``_reset_all_shared_state()`` (fires on file transitions only).
    """
    try:
        from agentic_rag import _clear_rag_inrun_cache
        _clear_rag_inrun_cache()
    except Exception:
        pass
    yield


@pytest.fixture
def mock_env_api_key():
    """Set KOKERTECH_API_KEY env var."""
    old = os.environ.get("KOKERTECH_API_KEY")
    os.environ["KOKERTECH_API_KEY"] = "test-key-from-env"
    yield
    if old:
        os.environ["KOKERTECH_API_KEY"] = old
    else:
        os.environ.pop("KOKERTECH_API_KEY", None)


@pytest.fixture
def mock_env_clear():
    """Clear KOKERTECH_API_KEY so fallback is tested."""
    old = os.environ.pop("KOKERTECH_API_KEY", None)
    yield
    if old:
        os.environ["KOKERTECH_API_KEY"] = old


@pytest.fixture
def temp_vault_db():
    """Create a temporary SQLite database for memory_vault tests."""
    import memory_vault
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    original_path = memory_vault.DB_PATH
    original_init = memory_vault._db_initialized
    memory_vault.DB_PATH = path
    memory_vault._db_initialized = False
    memory_vault.ensure_tables_exist()
    yield path
    memory_vault.DB_PATH = original_path
    memory_vault._db_initialized = original_init
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def temp_workspace_dir():
    """Create a temporary workspace directory."""
    import shutil
    temp_dir = tempfile.mkdtemp(prefix="kokertech_test_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)



# ---------------------------------------------------------------------------
# Test model isolation: discard workstation model paths inherited when
# config.py loads app_settings.json, without modifying the live settings file.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _isolate_persisted_model_config():
    """Keep the developer's settings file and local model out of test runs.

    config.py loads app_settings.json before pytest fixtures run. Redirect
    later config reloads/saves to a disposable session directory and clear
    the active model selectors; tests may still set these keys explicitly.
    """
    import config

    original_settings_path = config.SETTINGS_PATH
    model_config = {
        key: config.CONFIG.get(key, "")
        for key in ("model_file", "model_name")
    }
    with tempfile.TemporaryDirectory(prefix="kokertech_test_settings_") as settings_dir:
        config.SETTINGS_PATH = os.path.join(settings_dir, "app_settings.json")
        config.CONFIG["model_file"] = ""
        config.CONFIG["model_name"] = ""
        try:
            yield
        finally:
            config.SETTINGS_PATH = original_settings_path
            config.CONFIG.update(model_config)


# ---------------------------------------------------------------------------
# Per-test CONFIG reset: snapshot CONFIG + IDENTITY_CONFIG before each test
# and restore after. Opt-out via @pytest.mark.no_config_reset marker.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_onboarding_modal(request, monkeypatch):
    """Neuter BOTH onboarding/startup modals for every test (Group 5 companion).

    ROOT CAUSE (2026-09-22): onboarding_wizard.is_first_run() returns True
    whenever app_settings.json lacks ``onboarding_complete`` -- true on fresh
    clones, corrupted settings, or whenever the live app churns the file.
    Any test whose dashboard reaches _deferred_init_dashboard then opened a
    REAL modal wizard (wizard.exec() blocks the thread); under pytest-timeout
    the worker thread is killed mid-exec() -> Qt native unwind -> xdist
    "node down: Not properly terminated" worker deaths in parallel runs.

    TWO choke points are neutered (2026-09-24 audit):
    1. KokertechDashboard._run_onboarding_or_show (wizard modal via
       run_onboarding -> OnboardingWizard.exec, scheduled from the
       is_first_run() branch of _init_startup_checks).
    2. KokertechDashboard._check_save_integrity (corrupt-settings
       QMessageBox.question via check_last_save_integrity, scheduled from
       the else-branch -- a second modal on fresh clones / corrupted
       app_settings.json).

    Patched at the call site (Group 4 precedent: fix the caller, not the
    module) so tests/test_onboarding_wizard.py still exercises the real
    wizard via direct run_onboarding() calls.

    Opt-out via ``@pytest.mark.real_onboarding`` for tests that exercise the
    real _run_onboarding_or_show flow (currently only
    tests/test_app_core.py::TestRunOnboardingOrShow).
    """
    if request.node.get_closest_marker("real_onboarding") is not None:
        return
    try:
        import app_core
        monkeypatch.setattr(
            app_core.KokertechDashboard,
            "_run_onboarding_or_show",
            lambda self: None,
            raising=True,
        )
        monkeypatch.setattr(
            app_core.KokertechDashboard,
            "_check_save_integrity",
            lambda self: None,
            raising=True,
        )
    except (ImportError, AttributeError):
        # app_core unavailable (non-dashboard test subset) or API drift --
        # nothing to guard in either case.
        pass


@pytest.fixture(autouse=True)
def _reset_config_per_test(request):
    """Snapshot CONFIG and IDENTITY_CONFIG before each test and restore after.

    Opt-out via ``@pytest.mark.no_config_reset`` marker (class-level,
    function-level, or module-level).
    """
    from config import CONFIG, IDENTITY_CONFIG
    saved_config = dict(CONFIG)
    saved_identity = dict(IDENTITY_CONFIG)
    yield
    if request.node.get_closest_marker("no_config_reset") is not None:
        return
    CONFIG.clear()
    CONFIG.update(saved_config)
    IDENTITY_CONFIG.clear()
    IDENTITY_CONFIG.update(saved_identity)


# ---------------------------------------------------------------------------
# Default-deny vault isolation (Sprint 19.8.1, 2026-09-21).
#
# FORENSIC ROOT CAUSE: production core_memories held 742,941 rows — ALL of
# them test artifacts (237 distinct contents; 705k carrying [MOCK]/test-query
# signatures written Jul 27–Aug 3, plus a fresh 2-row bleed on 2026-09-21
# from tests/test_kokertechController.py). Vault isolation was OPT-IN
# (temp_vault_db fixture), so any test exercising memory_vault without opting
# in wrote straight into kokertech_vault.db. Worse, _reset_memory_vault()'s
# importlib.reload() restores DB_PATH to production, silently defeating
# per-file redirects at every cross-file teardown.
#
# This fixture makes isolation DEFAULT-DENY: every test runs against its own
# temp vault unless explicitly marked otherwise. Escape hatch for tests that
# genuinely need the production DB: @pytest.mark.real_vault (registered in
# pytest.ini). First (and only) user: tests/test_vault_size_guard.py
# (Sprint 19.8.3) — the production row-count watchdog, which must SEE the
# real vault to guard it, and opens it mode=ro so it can never write here.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _default_deny_vault_isolation(request):
    """Point memory_vault.DB_PATH at a per-test temp DB for every test.

    REGRESSION GUARD invariants:
    - Runs for EVERY test (autouse), so opt-in isolation is no longer
      required to protect production data.
    - Per-test temp file (no cross-test sharing) — immune to the
      _reset_memory_vault() importlib.reload() DB_PATH reset, connection
      pool staleness (pool validates against DB_PATH per §20.6), and
      fixture ordering hazards.
    - WAL sidecars (-wal/-shm) are removed with the DB on teardown.
    """
    if request.node.get_closest_marker("real_vault") is not None:
        yield
        return
    if os.environ.get("KOKERTECH_TEST_REAL_VAULT") == "1":
        # Debug escape hatch: run the whole session against the real vault
        # (e.g. when reproducing a production-data issue). Default OFF.
        yield
        return
    import sqlite3
    import memory_vault as mv

    fd, path = tempfile.mkstemp(prefix="kokertech_test_vault_", suffix=".db")
    os.close(fd)
    mv.DB_PATH = path
    mv._db_initialized = False
    try:
        mv.ensure_tables_exist()
    except sqlite3.Error:
        pass  # tests that need schema set it up themselves
    yield
    try:
        mv._clear_connection_pool()
    except (OSError, sqlite3.Error):
        pass
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Cross-file test isolation: reset shared module-level state when pytest
# moves to a new test_*.py file.
#
# Prevents failures caused by:
#   - QApplication top-level widget C++ objects accumulating across files
#   - PluginRegistry singleton accumulating plugins/schemas/disabled sets
#   - MemoryVault _db_initialized / _model flags persisting
#   - AI provider cache returning stale instances
#   - MCP server _http_server* state left from prior test files
# ---------------------------------------------------------------------------

_last_test_file = None


def _get_test_file_path(item):
    """Get the file path of a pytest item, supporting both fspath (legacy)
    and path (pytest 7+) attributes."""
    if hasattr(item, "path"):
        return str(item.path)
    if hasattr(item, "fspath"):
        return str(item.fspath)
    return None


@pytest.fixture(autouse=True)
def _cross_file_isolation(request):
    """
    Automatically runs before every test. Tracks the current test file
    so the teardown hook can detect file transitions reliably.
    """
    global _last_test_file
    _last_test_file = _get_test_file_path(request.node)
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """
    After the last test of each file completes, reset shared module-level
    state. This runs AFTER tearDownClass, which is the correct time to
    clean up -- preventing access violations when the next file's setUpClass
    creates new QApplication objects atop stale C++ widgets.
    """
    yield
    if nextitem is not None:
        item_path = _get_test_file_path(item)
        next_path = _get_test_file_path(nextitem)
        if item_path and next_path and item_path != next_path:
            try:
                _reset_all_shared_state(
                    aggressive_qt_cleanup=(item.config.option.capture == "no")
                )
            except Exception:
                pass


def _reset_all_shared_state(aggressive_qt_cleanup: bool = True):
    """Reset every known source of cross-file test pollution."""
    patch.stopall()
    gc.collect()
    if aggressive_qt_cleanup:
        _aggressive_cleanup_qt()
    else:
        # Minimal cleanup during pytest's active capture teardown to avoid
        # `ValueError: I/O operation on closed file.` from pytest capture.
        #
        # Sibling mitigation: ``_safe_flush`` wrapper at the top of this file
        # catches the same crash at session exit (pytest.console_main() ->
        # sys.stdout.flush()). Between them, both the cross-file teardown and
        # session-exit pathways are covered.
        try:
            gc.collect()
        except Exception:
            pass
    _reset_config_state()
    _reset_plugin_registry()
    _reset_memory_vault()
    _reset_ai_cache()
    _reset_rag_inrun_cache()
    _reset_mcp_state()
    _reset_services_registry()
    gc.collect()


def _aggressive_cleanup_qt():
    """
    Destroy all QApplication top-level widgets and flush the Qt event queue.

    Uses ``deleteLater()`` instead of ``close()`` to avoid triggering
    ``closeEvent`` handlers (which call ``QApplication.instance().quit()``,
    setting the internal ``closingDown`` flag on the session-scoped QApp).
    Once ``closingDown`` is True, Qt's ``QWidget::create()`` silently returns
    early, leaving widgets with no native handle — causing hangs in the next
    test file's ``shared_dashboard`` fixture when it creates a new
    ``KokertechDashboard``.
    """
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QEventLoop

        app = QApplication.instance()
        if app is None:
            return

        # Phase 1: Delete all top-level widgets WITHOUT calling close().
        # ``close()`` triggers ``closeEvent`` which calls ``app.quit()``,
        # setting the ``closingDown`` flag. Use ``deleteLater()`` instead
        # to schedule deferred C++ destruction without running event handlers.
        for w in list(app.topLevelWidgets()):
            try:
                w.deleteLater()
            except Exception:
                pass

        # Phase 2: Process events to flush all pending
        # deferred deletion events (deleteLater() schedules DeferredDelete).
        # Reduced from 20 to 5 cycles — empirical testing shows 5 cycles
        # is sufficient to drain all DeferredDelete events on both 2s
        # and 2s+ timer scenarios. The previous 20-cycle loop was a
        # defensive value that added ~15ms of unnecessary overhead per
        # cross-file teardown (116 test files × 15ms ≈ 1.7s saved).
        for _ in range(5):
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)

        # Phase 3: Flush any remaining posted events
        try:
            app.sendPostedEvents()
        except Exception:
            pass
    except Exception:
        pass


def _reset_plugin_registry():
    """Recreate the plugin registry singleton to clear loaded plugins and state."""
    try:
        import plugin_registry
        plugin_registry.registry = plugin_registry.PluginRegistry()
    except Exception:
        pass


def _reset_memory_vault():
    """Reset memory vault module-level flags and force-restore real functions.

    setUpClass-level patch.multiple('memory_vault', ...) from prior test files
    (e.g. test_hotkey_controller_plugin_integration.py) can leave MagicMock
    instances attached to memory_vault functions even after patch.stopall().

    Using importlib.reload() atomically replaces every module attribute
    (including lingering MagicMocks) with fresh copies from source.

    Sprint 19.8.1: DB_PATH is NOT restored to production here. Reload used to
    reset DB_PATH to the real vault mid-session — the mechanism that let
    per-file redirects silently die at cross-file teardowns while tests kept
    writing production data (see _default_deny_vault_isolation above).
    """
    try:
        import importlib
        import memory_vault
        real_db_path = memory_vault.DB_PATH  # preserve current target (temp under isolation)
        importlib.reload(memory_vault)
        memory_vault.DB_PATH = real_db_path
        memory_vault._db_initialized = False
        memory_vault._model = None
        memory_vault._model_state["model"] = None
        memory_vault._model_state["load_attempted"] = False
        memory_vault._session_state["session_id"] = None
        memory_vault._current_session_id = None
    except Exception:
        pass


def _reset_ai_cache():
    """Clear the AI provider cache to prevent stale provider instances."""
    try:
        import ai_base
        ai_base._PROVIDER_CACHE = {}
    except Exception:
        pass


def _reset_rag_inrun_cache():
    """Clear the agentic_rag module-level in-run retrieval cache (Phase D 15.3).

    Without this reset, the cache persists across tests in the same process,
    causing cross-test bleed: a second test that calls ``_retrieve_for()``
    with the same question text as an earlier test returns the earlier
    test's cached (mock-driven) result instead of its own fixture's mocks.
    Mirrors the ``_reset_ai_cache()`` pattern above.
    """
    try:
        from agentic_rag import _clear_rag_inrun_cache
        _clear_rag_inrun_cache()
    except Exception:
        pass


def _reset_config_state():
    """
    Remove any non-JSON-serializable values from CONFIG that may have been
    introduced by prior test files (e.g. MagicMock instances, lambdas, etc.).
    Also resets _KEYRING_AVAILABLE to its true module-level value.

    This prevents cross-file contamination where earlier test files leave
    non-serializable CONFIG values that cause ``json.dump()`` to raise
    ``TypeError`` inside ``save_settings()`` / ``save_identity()``, leaving
    truncated/ corrupted files on disk.
    """
    try:
        import config
        import json
        for k in list(config.CONFIG.keys()):
            try:
                json.dumps(config.CONFIG[k])
            except (TypeError, ValueError):
                del config.CONFIG[k]
        # Re-probe keyring availability (may have been patched by prior files)
        try:
            import keyring  # noqa: F401
            config._KEYRING_AVAILABLE = True
        except ImportError:
            config._KEYRING_AVAILABLE = False
    except Exception:
        pass


def _reset_mcp_state():
    """Reset MCP HTTP server state that could be left running from prior test files."""

    try:
        import mcp_server
        mcp_server._http_server = None
        mcp_server._http_server_thread = None
        mcp_server._http_server_running = False
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Test-count CI gate: baseline comparison at collection finish
# ---------------------------------------------------------------------------
#
# After every full test collection, compare the total test count against
# a stored baseline (_baseline_test_count.json). If the count drops below
# the threshold (test_drop_threshold, default 5), the suite fails.
#
# Set KOKERTECH_UPDATE_TEST_BASELINE=1 to refresh the baseline to the
# current count (e.g. after intentionally adding/removing tests).
# ---------------------------------------------------------------------------


def _is_full_suite_run(session) -> bool:
    """Check if this is a full test suite run (not a subset).

    A full-suite run means pytest was invoked on the ``tests/``
    directory as a whole (e.g. ``pytest tests/`` or just ``pytest``
    with discovery).  Single-file runs (``pytest tests/test_foo.py``),
    ``-k`` filtered runs, and module-path runs are NOT full-suite
    and should skip the gate.
    """
    args = [str(a).replace("\\", "/").rstrip("/") for a in session.config.args]
    if not args:
        return True  # Empty args = pytest discovers tests/ by default
    # Full suite: the ONLY arg is "tests" (discovered directory target)
    # Single-file or multi-file runs have paths with "tests/test_" etc.
    return len(args) == 1 and args[0] == "tests"


def pytest_collection_finish(session):
    """Compare collected test count against baseline after collection.

    Only fires for full test suite runs (``tests/`` directory targeted).
    If ``KOKERTECH_UPDATE_TEST_BASELINE=1`` is set, update the baseline
    file with the current count instead of comparing.

    Subset runs (single file, ``-k`` filter, module path) are silently
    skipped to avoid false positives during development.
    """
    if not _is_full_suite_run(session):
        return  # Skip gate for subset runs

    import json
    from pathlib import Path

    baseline_path = Path(__file__).resolve().parent / "tests" / "_baseline_test_count.json"
    # Use len(session.items) directly instead of session.testscollected
    # which may be 0 in some environments despite successful collection.
    current = len(session.items) or session.testscollected

    # Update mode
    if os.environ.get("KOKERTECH_UPDATE_TEST_BASELINE") == "1":
        baseline_path.write_text(json.dumps({"count": current, "updated": "auto"}, indent=2), encoding="utf-8")
        sys.stderr.write(f"[test-count gate] Baseline UPDATED to {current} ({baseline_path})\n")
        return

    # Comparison mode
    if not baseline_path.exists():
        sys.stderr.write(
            f"[test-count gate] Baseline missing at {baseline_path}. "
            f"Run KOKERTECH_UPDATE_TEST_BASELINE=1 pytest to seed it.\n"
        )
        return

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        expected = int(baseline.get("count", 0))
    except (json.JSONDecodeError, ValueError, KeyError):
        sys.stderr.write(f"[test-count gate] Baseline corrupt at {baseline_path}. Re-seed with KOKERTECH_UPDATE_TEST_BASELINE=1.\n")
        return

    threshold = int(session.config.getini("test_drop_threshold"))
    drop = expected - current
    if drop > threshold:
        raise RuntimeError(
            f"Test-count CI gate FAILED: expected ~{expected} tests, got {current} "
            f"(drop of {drop} exceeds threshold of {threshold}).\n"
            f"  - If this is intentional, re-seed the baseline:\n"
            f"      KOKERTECH_UPDATE_TEST_BASELINE=1 python -m pytest tests/ --collect-only\n"
            f"  - Baseline file: {baseline_path}\n"
        )
    elif drop > 0:
        sys.stderr.write(
            f"[test-count gate] Test count dropped from {expected} to {current} "
            f"({drop} fewer — within threshold of {threshold}).\n"
        )


def _reset_services_registry():
    """Reset services registry + singleton factory helpers.

    Wipes per-test state from registered service singletons and
    re-initializes lazy factory helpers so the next test file
    starts with fresh instances (Sprint 17 R12 pattern).
    """
    try:
        from services import get_services
        get_services().reset()
    except Exception:
        pass
    # Repair the canonical wiring: a destructive reset(clear_factories=True)
    # from any test file (e.g. test_services.TestGlobalSingleton) wipes the
    # 4 controller-split service factories from the GLOBAL registry; without
    # re-registration, every later KokertechController() in the same pytest
    # process raises KeyError("service 'history_service' is not registered")
    # (cross-file isolation invariant, KNOWLEDGE.md §12). Idempotent.
    try:
        from services.registry import register_canonical_services
        register_canonical_services()
    except Exception:
        pass
    try:
        from services.code_intelligence_factory import _reset_factory_for_tests
        _reset_factory_for_tests()
    except Exception:
        pass
    try:
        from services.skill_service import _reset_skill_service_for_tests
        _reset_skill_service_for_tests()
    except Exception:
        pass
    try:
        from services.settings_cache_service import _reset_settings_cache_service_for_tests
        _reset_settings_cache_service_for_tests()
    except Exception:
        pass
    try:
        from services.provider_stream_service import _reset_provider_stream_service_for_tests
        _reset_provider_stream_service_for_tests()
    except Exception:
        pass
    try:
        from services.tool_execution_service import _reset_tool_execution_service_for_tests
        _reset_tool_execution_service_for_tests()
    except (ImportError, AttributeError):
        pass


# Note: function-scoped qapp fixture
# so QApplication is created once and shared across the entire test session.
# This eliminates the ~360s overhead of destroy/recreate QApplication
# between every test.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp():
    """
    Session-scoped QApplication fixture.

    Overrides pytest-qt's built-in (function-scoped) qapp fixture so that
    a single QApplication is created at session start and reused for all
    tests.  This eliminates the ~3x runtime overhead of destroying and
    recreating QApplication for every test.

    Cross-file isolation is handled by ``_aggressive_cleanup_qt()`` which
    destroys all top-level widgets via ``deleteLater()`` and flushes pending
    events — without calling ``app.quit()`` (which would set the closingDown
    flag and prevent widget creation in subsequent test files).
    """
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True, scope="session")
def _ensure_qapp(qapp):
    """
    Pre-initialize QApplication at session start so that even
    unittest.TestCase-based test files that import PyQt6 classes
    (but don't request the ``qapp`` fixture explicitly) find an
    active QApplication instance.
    """


@pytest.fixture
def plugin_reg():
    """Return the shared plugin registry singleton."""
    import plugin_registry
    return plugin_registry.registry


# ---------------------------------------------------------------------------
# Module-scoped: share one KokertechDashboard across all tests in a file.
# Only the first test of each file pays the ~15s creation cost.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shared_dashboard(qapp):
    """
    Module-scoped KokertechDashboard shared across all tests in a single file.

    The first test in each file pays the ~15s Dashboard creation cost;
    subsequent tests reuse the same instance. State is reset between tests
    by a function-scoped ``dashboard`` fixture.

    Cross-file cleanup is handled by ``_reset_all_shared_state()`` which runs
    between files via ``pytest_runtest_teardown`` — it calls ``patch.stopall()``
    and ``_aggressive_cleanup_qt()``, so the Dashboard is properly torn down
    before the next file starts.
    """
    import tempfile
    import app_core
    from unittest.mock import patch, MagicMock

    # Baseline patches (applied before Dashboard creation, persist for the file)
    _p = patch("config.SETTINGS_PATH", tempfile.mktemp(suffix=".json"))
    _p.start()

    _p = patch.multiple(
        "memory_vault",
        semantic_search=MagicMock(return_value=[]),
        store_memory=MagicMock(),
        get_recent_bias=MagicMock(return_value=[]),
        get_growth_arc=MagicMock(return_value=[]),
        ensure_tables_exist=MagicMock(),
        consolidate_episodic=MagicMock(return_value=0),
        start_new_session=MagicMock(),
    )
    _p.start()

    _p = patch("app_core.get_logger", return_value=MagicMock())
    _p.start()

    _p = patch("app_core.VoiceOutput", return_value=MagicMock())
    _p.start()

    _p = patch("workers.VoiceRecorderWorker", return_value=MagicMock())
    _p.start()

    mock_post = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        raise_for_status=lambda: None,
    )
    _p = patch("requests.post", return_value=mock_post)
    _p.start()

    # Patch _init_safety to prevent slow pyautogui → pyscreeze → PIL.ImageFont
    # import chain during Dashboard creation. The import happens lazily in
    # computer_use_tab._init_safety() and hangs under pytest on this Windows
    # machine (likely PIL font enumeration). Patched method is safe because
    # tests in test_hotkey_system.py don't exercise computer_use methods.
    _p = patch("tabs.computer_use_tab.ComputerUseTabMixin._init_safety", MagicMock())
    _p.start()

    # Patch _update_health to prevent slow nvidia-smi/GPUtil VRAM queries
    # during Dashboard creation. The health tab calls _update_health() in
    # create_health_tab(), which triggers _get_vram() → nvidia-smi (5s timeout)
    # → GPUtil.getGPUs(). Under pytest, these subprocess/import calls can
    # hang or take excessive time. Tests don't assert health tab state.
    _p = patch("tabs.health_tab.HealthTabMixin._update_health", MagicMock())
    _p.start()

    # Patch refresh_agency_data to prevent SQLite lock contention during
    # Dashboard creation. The agency tab calls refresh_agency_data() at end of
    # create_agency_tab(), which connects to kokertech_vault.db. The
    # GraphLayoutWorker (started by neural_graph_tab) may hold an open
    # SQLite connection, causing lock contention on the main thread.
    _p = patch("tabs.cognitive_agency_tab.CognitiveAgencyTabMixin.refresh_agency_data", MagicMock())
    _p.start()

    # Patch render_knowledge_graph (missing sibling of the patches above):
    # create_neural_graph_tab() starts ui_components.GraphLayoutWorker — a
    # QThread that opens the REAL vault DB and paints NodeGraphicsItem
    # scenes via layout_ready_signal inside app.processEvents(), hanging
    # fixture setup for shared_dashboard users (same SQLite-lock rationale
    # as the refresh_agency_data comment above; see that test file's own
    # docstring). Tests exercising graph rendering use test_neural_graph.py,
    # which does NOT use shared_dashboard.
    _p = patch("tabs.neural_graph_tab.NeuralGraphTabMixin.render_knowledge_graph", MagicMock())
    _p.start()

    # NOTE: plugin_registry.registry is NOT patched here — tests that
    # dispatch plugin commands (e.g. SYSTEM_STATUS, LIST_FILES) need the
    # real registry with real plugins. The _enable_all_plugins() call in
    # each test's _setup fixture ensures all plugins are enabled.

    # Patch timer setup methods to prevent background QTimers from starting.
    # These timers (vram 2s, cleanup 30s, consolidation 5min, daily summary)
    # keep firing after the test and prevent the process from exiting.
    # Tests that exercise timer behavior should mock these individually.
    _p = patch.multiple(
        "app_core.KokertechDashboard",
        setup_vram_monitor=MagicMock(),
        setup_cleanup_timer=MagicMock(),
        setup_consolidation_timer=MagicMock(),
        setup_daily_summary_timer=MagicMock(),
    )
    _p.start()

    # Patch _prewarm_model and _update_provider_badge to prevent hang when
    # init_ui is patched but the daemon thread's QTimer fires during
    # app.processEvents(). See test_app_core.py for detailed explanation.
    _p = patch("app_core.KokertechDashboard._prewarm_model", MagicMock())
    _p.start()
    _p = patch("app_core.KokertechDashboard._update_provider_badge", MagicMock())
    _p.start()

    # ---------------------------------------------------------------------
    # Non-blocking closeEvent patch for tests
    # ---------------------------------------------------------------------
    # The real closeEvent in app_lifecycle.py performs heavy synchronous cleanup
    # that can hang indefinitely on Windows due to file/SQLite locks.
    # Production code now wraps consolidate_episodic() and scan_execution_log()
    # with _call_with_timeout(5s), but this test patch provides additional
    # defense-in-depth by replacing closeEvent entirely with a lightweight
    # version that preserves _shutting_down flag and _save_window_geometry()
    # while skipping ALL blocking I/O (including worker.terminate()+wait(2000),
    # thread.join(2s), api_proc.wait(2)).
    #
    # NOTE: app.quit() CANNOT be called in teardown because it sets the
    # internal closingDown flag on the session-scoped QApplication, which
    # prevents QWidget::create() in subsequent test files.
    #
    # NOTE: test_close_event_unhooks_hotkeys bypasses this patch by calling
    # AppLifecycleMixin.closeEvent directly (mixin version is unpatched since
    # patch.object only shadows KokertechDashboard.__dict__). Sets
    # _suppress_quit=True to prevent QApplication.quit(), restores state
    # in try/finally.
    # ---------------------------------------------------------------------
    def _test_close_event(self, event):
        self._shutting_down = True
        try:
            self._save_window_geometry()
        except Exception:
            pass
        event.accept()

    _p = patch.object(
        app_core.KokertechDashboard, "closeEvent", _test_close_event
    )
    _p.start()

    window = app_core.KokertechDashboard()

    yield window

    # Teardown: call close() to trigger closeEvent, then deleteLater().
    # closeEvent runs cleanup (threads, memory consolidation, etc.).
    # NOTE: Background QTimers (vram, cleanup, consolidation, daily summary)
    # are prevented from starting by the setup_*_timer no-op patches above,
    # so no explicit timer.stop() calls are needed here.
    # The ``_suppress_quit`` flag prevents QApp.quit() (keeps closingDown
    # flag False for the session-scoped QApplication).
    try:
        window._suppress_quit = True
        window.close()
        window.deleteLater()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for _ in range(5):
                app.processEvents()
    except Exception:
        pass
    # NOTE: Patches are not explicitly stopped here — ``_reset_all_shared_state``
    # calls ``patch.stopall()`` between test files, which handles cleanup.
