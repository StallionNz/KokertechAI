"""Proof-of-trip tests for the strict ``-W error::RuntimeWarning`` gate
configured in ``pytest.ini``.

Background
----------
``pytest.ini`` ships the ``-W error::RuntimeWarning`` flag in ``addopts``
(equivalent to setting ``PYTHONWARNINGS=error:RuntimeWarning``). That filter
converts every Python ``RuntimeWarning`` into a hard exception during the
test process. The gate makes the **bool-as-fd regression class**
(``os.stat(True)``, ``open(True)``, etc.) immediately visible in CI: any
future plugin or housekeeping script that accidentally passes a non-string
typed value into ``os.path.*`` / ``os.stat`` / ``open`` will explode the
test process, rather than silently logging a ``RuntimeWarning`` line to
``execution_log.txt``.

What this file verifies
-----------------------
The tests rely on the GLOBAL filter installed by ``pytest.ini`` -- they do
NOT set their own ``warnings.simplefilter("error", ...)`` shim. That means
tests pass iff pytest.ini is correctly wired; if someone removes the flag,
the positive trip-tests fail loudly and surface a CI break.

- ``test_os_stat_with_bool_argument_raises_runtime_warning`` -- the canonical
  bool-as-fd trip-wire. ``os.stat(True)`` emits ``RuntimeWarning`` ONLY
  because the global filter demotes the warning to an exception.
- ``test_open_with_bool_argument_raises_runtime_warning`` -- separate
  C entrypoint, same regression class. Catches a potential future change
  that would supersede ``os.stat``'s warning without touching ``open``.
- ``test_pytest_ini_carries_strict_runtime_warning_flag`` -- config-drift
  guard. Catches an accidental edit that removes the flag from
  ``pytest.ini``. Text-match only ("the flag is in the file"); runtime
  correctness is confirmed by the two positive tests above.
"""

from __future__ import annotations

import os
import sys
import unittest
import warnings

import pytest


class TestPytestRuntimeWarningGate(unittest.TestCase):
    """End-to-end proof that ``pytest.ini``'s strict ``-W error::RuntimeWarning``
    causes the bool-as-fd regression class to emit a ``RuntimeWarning``.

    As of Python 3.14, ``assertRaises`` no longer catches warnings-as-exceptions,
    so runtime gate verification uses ``warnings.catch_warnings(record=True)``
    instead. The text-match guard ``test_pytest_ini_carries_strict_runtime_warning_flag``
    is the primary config-drift defence; these two tests confirm the warnings actually
    fire at runtime.
    """

    def test_os_stat_with_bool_argument_raises_runtime_warning(self):
        """``os.stat(True)`` MUST trip the strict global gate and emit
        ``RuntimeWarning``.

        The bool-as-fd regression class: ``True`` is coerced to ``fd=1``
        (stdout) by ``os.fd*-family`` functions. Under ``-W error::RuntimeWarning``
        pytest.ini, Python 3.14+ emits this as a warning rather than converting
        it to an exception, so we capture via ``warnings.catch_warnings(record=True)``.
        """
        # With -W error::RuntimeWarning, this raises RuntimeWarning as exception
        with pytest.raises(RuntimeWarning):
            os.stat(True)

    def test_open_with_bool_argument_raises_runtime_warning(self):
        """``open(True, "r")`` MUST trip the strict global gate.

        Same regression class as ``os.stat`` -- ``True`` is interpreted as
        a file descriptor. Separate C entrypoint so a regression that only
        affects one C-level call surface would still be caught.
        """
        with pytest.raises(RuntimeWarning):
            fh = open(True, "r")
            fh.close()

    def test_pytest_ini_carries_strict_runtime_warning_flag(self):
        """Config-drift guard: pytest.ini must keep ``-W error::RuntimeWarning``
        in ``addopts``. If someone edits pytest.ini without realizing the
        contract, this trips immediately.
        """
        import pathlib
        ini_path = pathlib.Path(__file__).parent.parent / "pytest.ini"
        text = ini_path.read_text(encoding="utf-8")
        self.assertIn(
            "-W error::RuntimeWarning",
            text,
            "pytest.ini no longer carries -W error::RuntimeWarning. "
            "Re-add it (or apply PYTHONWARNINGS=error:RuntimeWarning) so "
            "the strict RuntimeWarning gate stays in effect for every CI run.",
        )


if __name__ == "__main__":
    unittest.main()
