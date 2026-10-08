"""REGRESSION GUARD for KokertechController._reject_with_error (Sprint 19.4).

Three Decision-8 safety guards in ``process_input`` share this helper:
budget overflow, Layer-A sentinel (raw text), Layer-C sentinel (parsed
final). It consolidates the pop-and-reject blocks that previously lived
inline at each site, so a drift here silently changes history state on
ALL three reject paths.

Locks (per KNOWLEDGE.md §3 Decision 8 error-shape contract):
- default ``final`` == ``"\\u274c " + err`` (matches the original
  ``f"\\u274c {err}"`` at Layer A / Layer C reject sites)
- explicit ``final`` override is honored verbatim (budget-overflow site)
- default pops ONLY the last user message
- ``pop_assistant=True`` pops assistant FIRST, then user (Layer C order)
- empty history is safe (no crash, dict still returned)

ANTI-FRAGILITY: helper is exercised via a real ``KokertechController``
(provider mocked) so ``self.history`` is the production list -- no stub
drift. The e2e sentinel suites (``test_call_chain_e2e.py``) still pin the
full pipeline behavior; this file isolates the shared helper itself.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kokertechController import KokertechController


def _make_controller():
    """Return a KokertechController with empty history (provider mocked)."""
    with patch("kokertechController.get_provider"):
        ctrl = KokertechController()
    ctrl.history = []
    return ctrl


class TestRejectWithError(unittest.TestCase):
    """Pop semantics + Decision-8 error-shape for the shared reject helper."""

    def setUp(self):
        self.ctrl = _make_controller()
        self.ctrl.history = []

    def test_default_pops_user_only(self):
        """User-role last message is popped; error dict returned."""
        self.ctrl.history.append({"role": "user", "content": "hello"})
        result = self.ctrl._reject_with_error("engine exploded")
        self.assertEqual(self.ctrl.history, [])
        self.assertEqual(result["error"], "engine exploded")

    def test_default_final_is_prefix_plus_err(self):
        """Default final == '\\u274c ' + err (Layer A / Layer C contract)."""
        self.ctrl.history.append({"role": "user", "content": "x"})
        result = self.ctrl._reject_with_error("bad sentinel")
        self.assertEqual(result["final"], "\u274c bad sentinel")
        self.assertEqual(result["thinking"], "")
        self.assertIsNone(result["command"])
        self.assertIn("ts", result)

    def test_explicit_final_override_honored(self):
        """Budget-overflow site passes a custom final string verbatim."""
        self.ctrl.history.append({"role": "user", "content": "x"})
        result = self.ctrl._reject_with_error(
            "Context overflow: 9999 chars exceeds safe budget 5000",
            final="\u274c Context overflow (9999 > 5000). Try shorter input.",
        )
        self.assertEqual(
            result["final"],
            "\u274c Context overflow (9999 > 5000). Try shorter input.",
        )
        self.assertNotIn("Context overflow: 9999 chars", result["final"])

    def test_pop_assistant_then_user(self):
        """Layer C order: assistant popped first, then user."""
        self.ctrl.history.append({"role": "user", "content": "q"})
        self.ctrl.history.append({"role": "assistant", "content": "a"})
        result = self.ctrl._reject_with_error(
            "Engine returned sentinel in parsed final",
            thinking="inner thought",
            pop_assistant=True,
        )
        self.assertEqual(self.ctrl.history, [])
        self.assertEqual(result["thinking"], "inner thought")
        self.assertEqual(result["final"], "\u274c Engine returned sentinel in parsed final")

    def test_pop_assistant_false_leaves_assistant(self):
        """Default (pop_assistant=False) does NOT pop a trailing assistant msg.

        With history = [user, assistant] and pop_assistant=False, the helper
        pops NOTHING: the assistant-pop is skipped, and the user-pop guard
        checks history[-1]["role"] == "user" -- false, since the tail is
        the assistant. History stays fully intact.
        """
        self.ctrl.history.append({"role": "user", "content": "q"})
        self.ctrl.history.append({"role": "assistant", "content": "a"})
        self.ctrl._reject_with_error("engine exploded")
        self.assertEqual(
            self.ctrl.history,
            [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
        )

    def test_empty_history_is_safe(self):
        """No history: no crash, error dict still returned."""
        result = self.ctrl._reject_with_error("nothing here")
        self.assertEqual(result["error"], "nothing here")
        self.assertEqual(self.ctrl.history, [])

    def test_non_user_tail_not_popped_by_default(self):
        """Guard: only a trailing USER message is popped by default."""
        self.ctrl.history.append({"role": "assistant", "content": "a"})
        self.ctrl._reject_with_error("err")
        self.assertEqual(self.ctrl.history, [{"role": "assistant", "content": "a"}])


if __name__ == "__main__":
    unittest.main()
