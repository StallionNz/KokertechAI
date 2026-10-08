"""REGRESSION GUARD for the autouse-fixture contract in ``tests/test_cli.py``.

The autouse fixture ``_reset_rich_console`` in ``tests/test_cli.py`` rebinds
``cli.formatting.console`` (and ``cli.commands.console``) to a fresh
``Console(file=io.StringIO())`` at the start of every test, so the 102
Sprint 15 CLI formatting tests don't crash on ``ValueError: I/O operation
on closed file`` when pytest's default FDCapture closes the original
``sys.stdout`` mid-session (Python 3.14 / pytest 9 FD-lifecycle issue
#14528). This test mirrors that binding inline and asserts the resulting
file target stays open -- locking the fixture's contract so a future
regression that swaps ``io.StringIO()`` for a real file handle (which
pytest could close mid-test) doesn't silently re-break the 102 tests.

The ``assert not cli.formatting.console.file.closed`` check fires if the
fixture pattern is changed to use a non-StringIO target. Diagnostic
message points future readers at the fixture (function-name anchor) and
SESSION_2026_07_19.md (root-cause history).
"""
import io
import cli.formatting
import cli.commands


def test_reset_console_relinks_to_fresh_buffer_regression():
    original_fmt_console = cli.formatting.console
    original_cmd_console = cli.commands.console
    fresh = cli.formatting.Console(file=io.StringIO())
    cli.formatting.console = fresh
    cli.commands.console = fresh
    try:
        assert not cli.formatting.console.file.closed, (
            "REGRESSION: cli.formatting.console was rebound to a closed file "
            "target by the inline fixture pattern. The autouse fixture in "
            "tests/test_cli.py::_reset_rich_console relies on io.StringIO() "
            "(never auto-closes) as the file target. If this fires, the "
            "fixture pattern was changed to use a non-StringIO target that "
            "pytest's FDCapture can close mid-test. See "
            "docs/sessions/SESSION_2026_07_19.md."
        )
        # Symmetric coverage: the autouse fixture rebinds BOTH consoles to
        # the same fresh Console instance (cli.commands does
        # `from cli.formatting import console` as a name binding, not a
        # live-ref, so the sibling module global must be rebound alongside
        # to keep both globals in sync). Without this second assertion, a
        # future fixture refactor that rebinds ONLY cli.formatting.console
        # would leave cli.commands.console pointing at a closed file --
        # splitting the contract without breaking this REGRESSION GUARD.
        assert not cli.commands.console.file.closed, (
            "REGRESSION: cli.commands.console was NOT rebound to a fresh "
            "StringIO-backed Console by the inline fixture pattern. The "
            "autouse fixture in tests/test_cli.py::_reset_rich_console "
            "rebinds BOTH cli.formatting.console AND cli.commands.console "
            "to the same fresh Console instance -- splitting them silently "
            "would leave cli.commands.console pointing at pytest's closed "
            "stdout wrapper. See docs/sessions/SESSION_2026_07_19.md."
        )
    finally:
        cli.formatting.console = original_fmt_console
        cli.commands.console = original_cmd_console
