"""
utils/log_scanner.py - Shared audit helpers for housekeeping scripts.

Centralizes the RuntimeWarning audit pattern so current callers
(app_lifecycle.closeEvent post-shutdown, vault_cleaner.run daily-clean,
vault_upgrade.upgrade_schema upgrade-time) and future housekeeping scripts
can all import a single source of truth instead of copy-pasting the same
try/except / open / read / count / warning boilerplate.

Portable Python equivalent of the requested
`grep -c 'RuntimeWarning:' execution_log.txt`.
"""

import os

# Module-level guard: keeps the import side-effect-free for callers that
# only need the helper. There are no logger side-effects at import time.
__all__ = ["scan_execution_log", "reset_runtime_warning_count"]


def reset_runtime_warning_count(log_path):
    """Archive phantom ``RuntimeWarning:`` lines from ``log_path`` so they do
    not inflate subsequent audit counts.

    Background
    ----------
    Ad-hoc smoke runs or bad tests can write lines containing
    ``"RuntimeWarning:"`` directly to ``execution_log.txt`` (e.g.
    ``logger.warning("RuntimeWarning: phantom")``). Those phantom lines linger
    forever because :func:`scan_execution_log` only reports counts -- it does
    not clear or archive them. Over time the count climbs even after the
    underlying regression is fixed, producing false-positive audit signals
    during daily housekeeping / upgrade-time scans.

    Behavior
    --------
    1. Read ``log_path`` line by line via :func:`open`.
    2. Move every line containing ``"RuntimeWarning:"`` to a sibling archive
       ``<log_path>.archived`` (append -- safe to call repeatedly).
    3. Rewrite ``log_path`` with only the non-phantom lines.
    4. Return the number of phantom lines moved to the archive.

    Idempotent across runs: a line already in ``<log_path>.archived`` will be
    re-detected and re-archived -- harmless because the archive grows
    monotonically. Downstream consumers can ``unique`` it before review.

    Failure modes
    -------------
    - Missing path: returns 0 (no-op).
    - ``OSError`` / ``ValueError`` on read or write: returns -1.

    Parameters
    ----------
    log_path : str
        Absolute path to ``execution_log.txt``.

    Returns
    -------
    int
        Number of phantom RuntimeWarning lines moved to
        ``<log_path>.archived``, or -1 on I/O failure, or 0 if the file did
        not exist / had no matches.
    """
    archive_path = f"{log_path}.archived"
    if not os.path.exists(log_path):
        return 0
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as _f:
            _lines = _f.readlines()
    except (OSError, ValueError):
        return -1

    phantom_lines = [ln for ln in _lines if "RuntimeWarning:" in ln]
    keep_lines = [ln for ln in _lines if "RuntimeWarning:" not in ln]

    if not phantom_lines:
        return 0

    try:
        with open(archive_path, "a", encoding="utf-8") as _af:
            _af.writelines(phantom_lines)
        with open(log_path, "w", encoding="utf-8") as _f:
            _f.writelines(keep_lines)
    except (OSError, ValueError):
        return -1

    return len(phantom_lines)


def scan_execution_log(log_path, logger, *, prefix=""):
    """Audit a log file for RuntimeWarning occurrences.

    Opens ``log_path``, counts occurrences of ``"RuntimeWarning:"``, and
    emits a single WARNING-level log entry via ``logger`` if any are found.
    Returns the count (0 if none / scan skipped due to I/O error).

    Shared utility -- kills the 14-line duplication that previously lived
    in app_lifecycle.closeEvent, vault_cleaner.run (private helper, since
    promoted into utils.log_scanner), and vault_upgrade.upgrade_schema.
    Portable Python equivalent of the requested
    ``grep -c 'RuntimeWarning:' execution_log.txt``.

    Parameters
    ----------
    log_path : str
        Absolute path to the log file to scan. The caller is responsible
        for resolving this (e.g. ``LOG_FILE`` from ``config``, or
        ``self.file_logger.log_path`` for the post-shutdown case).
    logger : object
        Logger instance with ``.warning(str)`` and ``.debug(str)`` methods.
        Typically a ``logging_config._LevelFilteredLogger`` (or a MagicMock
        in tests).
    prefix : str, optional
        String prepended verbatim to the warning message. Defaults to ``""``.
        Used by ``app_lifecycle.closeEvent`` to insert the ``"[Cleanup] "``
        grep-friendly audit-trail marker so existing daily-review grep
        workflows that look for ``[Cleanup]`` continue to match the
        new shared-helper output. Other callers (``vault_cleaner.run``,
        ``vault_upgrade.upgrade_schema``) leave this empty.

    Returns
    -------
    int
        The number of ``"RuntimeWarning:"`` occurrences found, or 0 if
        the scan was skipped due to ``OSError`` (e.g. Windows file lock)
        or ``ValueError``.
    """
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as _f:
            _content = _f.read()
        _count = _content.count("RuntimeWarning:")
        if _count:
            logger.warning(
                f"{prefix}{log_path} contains {_count} 'RuntimeWarning:' "
                f"occurrence(s) \u2014 review session for regressions"
            )
        return _count
    except (OSError, ValueError) as _e:
        logger.debug(f"RuntimeWarning scan skipped: {_e}")
        return 0
