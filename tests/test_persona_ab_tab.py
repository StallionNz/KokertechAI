"""Tests for tabs/persona_ab_tab.py -- Persona A/B Testing tab mixin."""
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

class TestPersonaABTestingTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.persona_ab_tab.QTimer", return_value=MagicMock())
        self._p.start()
    def tearDown(self):
        self._p.stop()
    def _mk(self, obj):
        with patch.object(obj, "_refresh_ab_leaderboard") as _ph:
            obj._tab = obj.create_persona_ab_testing_tab()
        # Regression guard: the `_mk` helper shadows `_refresh_ab_leaderboard` on the
        # instance for the lifetime of the `with` block (which is the rest of the test
        # method). Patch stop/start between tests is NOT sufficient here because the
        # shadow survives `patch.stop()` — only re-binding the attribute on the *same*
        # instance (patch.object target) clears it.

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.persona_ab_tab import PersonaABTestingTabMixin
        obj = PersonaABTestingTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "ab_leaderboard_table"))
        self.assertTrue(hasattr(obj, "ab_history_text"))
        self.assertTrue(hasattr(obj, "ab_history_count"))

    def test_leaderboard_has_6_columns(self):
        from tabs.persona_ab_tab import PersonaABTestingTabMixin
        obj = PersonaABTestingTabMixin()
        self._mk(obj)
        self.assertEqual(obj.ab_leaderboard_table.columnCount(), 6)
        labels = [obj.ab_leaderboard_table.horizontalHeaderItem(i).text() for i in range(6)]
        self.assertEqual(labels, ["Persona", "Wins", "Losses", "Ties", "Total", "Win Rate"])

    def test_history_text_readonly(self):
        from tabs.persona_ab_tab import PersonaABTestingTabMixin
        obj = PersonaABTestingTabMixin()
        self._mk(obj)
        self.assertTrue(obj.ab_history_text.isReadOnly())

    def test_refresh_loads_leaderboard(self):
        from tabs.persona_ab_tab import PersonaABTestingTabMixin
        from unittest.mock import patch
        obj = PersonaABTestingTabMixin()
        self._mk(obj)
        # `self._mk` patched `_refresh_ab_leaderboard` for the test body — this test
        # needs the real impl, so restore it on the SAME instance that `_mk` already
        # shadowed. Re-binding on the instance clears the `patch.object` shadow.
        import tabs.persona_ab_tab as _pat
        obj._refresh_ab_leaderboard = _pat.PersonaABTestingTabMixin._refresh_ab_leaderboard.__get__(obj, _pat.PersonaABTestingTabMixin)
        leaderboard = [
            {"persona": "Helper", "display": "Friendly Helper",
             "wins": 5, "losses": 2, "ties": 1, "total": 8, "win_rate": 0.625},
            {"persona": "Coder", "display": "Expert Coder",
             "wins": 3, "losses": 4, "ties": 0, "total": 7, "win_rate": 0.429},
        ]
        votes = [
            {"timestamp": "10:00", "prompt": "Hello", "winner": "A",
             "persona_a": "You are Helper", "persona_b": "You are Coder"},
        ]
        pl = patch("memory_vault.get_persona_leaderboard", return_value=leaderboard)
        pv = patch("memory_vault.get_recent_persona_votes", return_value=votes)
        pl.start(); pv.start()
        try:
            obj._refresh_ab_leaderboard()
        finally: pl.stop(); pv.stop()
        self.assertEqual(obj.ab_leaderboard_table.rowCount(), 2)
        self.assertEqual(obj.ab_leaderboard_table.item(0, 0).text(), "Friendly Helper")
        self.assertEqual(obj.ab_leaderboard_table.item(0, 1).text(), "5")
        self.assertIn("62%", obj.ab_leaderboard_table.item(0, 5).text())

    def test_refresh_leaderboard_error(self):
        from tabs.persona_ab_tab import PersonaABTestingTabMixin
        obj = PersonaABTestingTabMixin()
        self._mk(obj)
        # `_mk` patched the method for the body; restore the real impl on the same
        # instance so the `patch.object` shadow is cleared before we exercise it.
        import tabs.persona_ab_tab as _pat
        obj._refresh_ab_leaderboard = _pat.PersonaABTestingTabMixin._refresh_ab_leaderboard.__get__(obj, _pat.PersonaABTestingTabMixin)
        pl = patch("memory_vault.get_persona_leaderboard", side_effect=sqlite3.Error("fail"))
        pv = patch("memory_vault.get_recent_persona_votes", return_value=[])
        pl.start(); pv.start()
        try:
            obj._refresh_ab_leaderboard()
        finally:
            pl.stop(); pv.stop()
        self.assertIn("Error", obj.ab_leaderboard_table.item(0, 0).text())

    def test_refresh_no_votes_shows_placeholder(self):
        from tabs.persona_ab_tab import PersonaABTestingTabMixin
        obj = PersonaABTestingTabMixin()
        self._mk(obj)
        # `_mk` patched the method for the body — restore the real impl for this test.
        import tabs.persona_ab_tab as _pat
        obj._refresh_ab_leaderboard = _pat.PersonaABTestingTabMixin._refresh_ab_leaderboard.__get__(obj, _pat.PersonaABTestingTabMixin)
        pl = patch("memory_vault.get_persona_leaderboard", return_value=[])
        pv = patch("memory_vault.get_recent_persona_votes", return_value=[])
        pl.start(); pv.start()
        try:
            obj._refresh_ab_leaderboard()
        finally:
            pl.stop(); pv.stop()
        self.assertIn("No A/B tests", obj.ab_history_text.toPlainText())
        self.assertEqual(obj.ab_history_count.text(), "")

    def test_refresh_with_winner_a(self):
        from tabs.persona_ab_tab import PersonaABTestingTabMixin
        obj = PersonaABTestingTabMixin()
        self._mk(obj)
        # `_mk` shadowed the method for the body — restore the real impl for this test.
        import tabs.persona_ab_tab as _pat
        obj._refresh_ab_leaderboard = _pat.PersonaABTestingTabMixin._refresh_ab_leaderboard.__get__(obj, _pat.PersonaABTestingTabMixin)
        votes = [
            {"timestamp": "10:00", "prompt": "Test", "winner": "A",
             "persona_a": "You are Helper.", "persona_b": "You are Coder."},
        ]
        pl = patch("memory_vault.get_persona_leaderboard", return_value=[])
        pv = patch("memory_vault.get_recent_persona_votes", return_value=votes)
        pl.start(); pv.start()
        try:
            obj._refresh_ab_leaderboard()
        finally:
            pl.stop(); pv.stop()
        text = obj.ab_history_text.toPlainText()
        self.assertIn("Helper", text)
        self.assertIn("Coder", text)
        self.assertIn("1 tests", obj.ab_history_count.text())

    def test_refresh_with_tie(self):
        from tabs.persona_ab_tab import PersonaABTestingTabMixin
        obj = PersonaABTestingTabMixin()
        self._mk(obj)
        # `_mk` shadowed the method for the body — restore the real impl for this test.
        import tabs.persona_ab_tab as _pat
        obj._refresh_ab_leaderboard = _pat.PersonaABTestingTabMixin._refresh_ab_leaderboard.__get__(obj, _pat.PersonaABTestingTabMixin)
        votes = [{"timestamp": "10:00", "prompt": "Test", "winner": "tie",
                   "persona_a": "HelperA", "persona_b": "CoderB"}]
        pl = patch("memory_vault.get_persona_leaderboard", return_value=[])
        pv = patch("memory_vault.get_recent_persona_votes", return_value=votes)
        pl.start(); pv.start()
        try:
            obj._refresh_ab_leaderboard()
        finally:
            pl.stop(); pv.stop()
        text = obj.ab_history_text.toPlainText()
        self.assertIn("HelperA", text)
        self.assertIn("CoderB", text)

if __name__ == "__main__":
    unittest.main()
