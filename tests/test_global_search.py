"""Tests for tabs/global_search.py."""
import unittest
from unittest.mock import patch, MagicMock


class TestSearchSessionHistory(unittest.TestCase):
    def setUp(self):
        import importlib, tabs.global_search as gs
        importlib.reload(gs)
        self._p = patch.object(gs.GlobalSearchDialog, "restore_requested", MagicMock())
        self._p.start()
        self.dialog = gs.GlobalSearchDialog(parent=None, controller=None)
    def tearDown(self):
        self._p.stop()
    def _set_history(self, msgs):
        c = MagicMock(); c.history = msgs; self.dialog._controller = c

    def test_empty_history(self):
        self._set_history([])
        self.assertEqual(self.dialog._search_session_history("hello"), [])
    def test_no_controller(self):
        self.dialog._controller = None
        self.assertEqual(self.dialog._search_session_history("hello"), [])
    def test_exact_match_high_score(self):
        self._set_history([{"role":"user","content":"hello world","ts":"10:00"}])
        r = self.dialog._search_session_history("hello")
        self.assertEqual(len(r), 1)
        self.assertAlmostEqual(r[0]["score"], 0.9)
    def test_word_overlap_lower_score(self):
        self._set_history([{"role":"assistant","content":"python is great for AI","ts":"10:00"}])
        r = self.dialog._search_session_history("python machine learning")
        self.assertEqual(len(r), 1)
        self.assertLess(r[0]["score"], 0.9)
    def test_no_match(self):
        self._set_history([{"role":"user","content":"hello"}])
        self.assertEqual(self.dialog._search_session_history("zzz"), [])
    def test_sorted_by_score(self):
        self._set_history([{"role":"user","content":"hello world"},{"role":"assistant","content":"say hello again"}])
        r = self.dialog._search_session_history("hello")
        self.assertEqual(len(r), 2)
        self.assertGreaterEqual(r[0]["score"], r[1]["score"])
    def test_limit_15(self):
        self._set_history([{"role":"user","content":"hello %d" % i} for i in range(20)])
        self.assertLessEqual(len(self.dialog._search_session_history("hello")), 15)
    def test_skips_empty_content(self):
        self._set_history([{"role":"user","content":""},{"role":"assistant","content":"hello"}])
        r = self.dialog._search_session_history("hello")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["role"], "assistant")
    def test_preserves_ts(self):
        self._set_history([{"role":"user","content":"hello","ts":"14:30"}])
        self.assertEqual(self.dialog._search_session_history("hello")[0]["timestamp"], "14:30")
    def test_source_is_session(self):
        self._set_history([{"role":"user","content":"hello"}])
        self.assertEqual(self.dialog._search_session_history("hello")[0]["source"], "session")


class TestOnTextChanged(unittest.TestCase):
    def setUp(self):
        import importlib, tabs.global_search as gs
        importlib.reload(gs)
        self._p = patch.object(gs.GlobalSearchDialog, "restore_requested", MagicMock())
        self._p.start()
        self.dialog = gs.GlobalSearchDialog(parent=None, controller=None)
    def tearDown(self):
        self._p.stop()
    def test_empty_stops_timer(self):
        self.dialog._on_text_changed("")
        self.assertFalse(self.dialog._search_timer.isActive())
    def test_whitespace_stops_timer(self):
        self.dialog._on_text_changed("   ")
        self.assertFalse(self.dialog._search_timer.isActive())
    def test_text_starts_timer(self):
        self.dialog._on_text_changed("hello")
        self.assertTrue(self.dialog._search_timer.isActive())


class TestRestoreAndCopy(unittest.TestCase):
    def setUp(self):
        import importlib, tabs.global_search as gs
        importlib.reload(gs)
        self._p = patch.object(gs.GlobalSearchDialog, "restore_requested", MagicMock())
        self._p.start()
        self.dialog = gs.GlobalSearchDialog(parent=None, controller=None)
    def tearDown(self):
        self._p.stop()
    def test_restore_emits_content(self):
        item = MagicMock()
        item.data.return_value = {"content":"hi","source":"session"}
        self.dialog.result_tree.currentItem = MagicMock(return_value=item)
        self.dialog._restore_selected()
        self.dialog.restore_requested.emit.assert_called_once_with("hi")
    def test_restore_no_item(self):
        self.dialog.result_tree.currentItem = MagicMock(return_value=None)
        self.dialog._restore_selected()
        self.dialog.restore_requested.emit.assert_not_called()
    def test_restore_no_data(self):
        item = MagicMock(); item.data.return_value = None
        self.dialog.result_tree.currentItem = MagicMock(return_value=item)
        self.dialog._restore_selected()
        self.dialog.restore_requested.emit.assert_not_called()
    def test_restore_empty_content(self):
        item = MagicMock(); item.data.return_value = {"content":""}
        self.dialog.result_tree.currentItem = MagicMock(return_value=item)
        self.dialog._restore_selected()
        self.dialog.restore_requested.emit.assert_not_called()


class TestNavigation(unittest.TestCase):
    def setUp(self):
        import importlib, tabs.global_search as gs
        importlib.reload(gs)
        self._p = patch.object(gs.GlobalSearchDialog, "restore_requested", MagicMock())
        self._p.start()
        self.dialog = gs.GlobalSearchDialog(parent=None, controller=None)
        self.dialog.result_tree.setCurrentItem = MagicMock()
        self.dialog._on_item_clicked = MagicMock()
    def tearDown(self):
        self._p.stop()
    def test_up_prev_sibling(self):
        c0, c1 = MagicMock(), MagicMock()
        p = MagicMock(); p.indexOfChild.return_value = 1; p.child.return_value = c0
        c1.parent.return_value = p
        self.dialog.result_tree.currentItem = MagicMock(return_value=c1)
        self.dialog._navigate_up()
        self.dialog.result_tree.setCurrentItem.assert_called_once_with(c0)
    def test_up_header_ignored(self):
        c = MagicMock(); c.parent.return_value = None
        self.dialog.result_tree.currentItem = MagicMock(return_value=c)
        self.dialog._navigate_up()
        self.dialog.result_tree.setCurrentItem.assert_not_called()
    def test_down_no_sel_selects_first(self):
        self.dialog.result_tree.currentItem = MagicMock(return_value=None)
        r, s, fst = MagicMock(), MagicMock(), MagicMock()
        s.childCount.return_value = 2; s.child.return_value = fst
        r.child.return_value = s; r.childCount.return_value = 1
        self.dialog.result_tree.invisibleRootItem = MagicMock(return_value=r)
        self.dialog._navigate_down()
        self.dialog.result_tree.setCurrentItem.assert_called_with(fst)
    def test_down_next_sibling(self):
        c0, c1 = MagicMock(), MagicMock()
        p = MagicMock(); p.indexOfChild.return_value = 0; p.childCount.return_value = 3; p.child.return_value = c1
        c0.parent.return_value = p
        self.dialog.result_tree.currentItem = MagicMock(return_value=c0)
        self.dialog._navigate_down()
        self.dialog.result_tree.setCurrentItem.assert_called_once_with(c1)
    def test_down_header_ignored(self):
        c = MagicMock(); c.parent.return_value = None
        self.dialog.result_tree.currentItem = MagicMock(return_value=c)
        self.dialog._navigate_down()
        self.dialog.result_tree.setCurrentItem.assert_not_called()


class TestShowEvent(unittest.TestCase):
    def setUp(self):
        import importlib, tabs.global_search as gs
        importlib.reload(gs)
        self._p = patch.object(gs.GlobalSearchDialog, "restore_requested", MagicMock())
        self._p.start()
        self.dialog = gs.GlobalSearchDialog(parent=None, controller=None)
        self.dialog.move = MagicMock()
    def tearDown(self):
        self._p.stop()
    def test_clears_search_input(self):
        self.dialog.search_input.setText("old")
        mock_event = MagicMock()
        mock_event.ignore = MagicMock()
        mock_event.accept = MagicMock()
        with patch('PyQt6.QtWidgets.QDialog.showEvent', return_value=None):
            with patch.object(self.dialog, 'move'):
                self.dialog.showEvent(mock_event)
        self.assertEqual(self.dialog.search_input.text(), "")
    def test_clears_counter(self):
        self.dialog.result_count_label.setText("5 results")
        mock_event = MagicMock()
        mock_event.ignore = MagicMock()
        mock_event.accept = MagicMock()
        with patch('PyQt6.QtWidgets.QDialog.showEvent', return_value=None):
            with patch.object(self.dialog, 'move'):
                self.dialog.showEvent(mock_event)
        self.assertEqual(self.dialog.result_count_label.text(), "")


class TestExecuteSearch(unittest.TestCase):
    def setUp(self):
        import importlib, tabs.global_search as gs
        importlib.reload(gs)
        self._p = patch.object(gs.GlobalSearchDialog, "restore_requested", MagicMock())
        self._p.start()
        self.dialog = gs.GlobalSearchDialog(parent=None, controller=None)

    def tearDown(self):
        self._p.stop()

    def test_execute_search_empty_query_returns_early(self):
        self.dialog.search_input.setText("")
        self.dialog._search_session_history = MagicMock()
        self.dialog._execute_search()
        self.dialog._search_session_history.assert_not_called()

    def test_execute_search_with_query(self):
        self.dialog.search_input.setText("hello")
        self.dialog._search_session_history = MagicMock(return_value=[])
        self.dialog._display_results = MagicMock()
        with patch("memory_vault.global_search", return_value=[]):
            self.dialog._execute_search()
        self.dialog._search_session_history.assert_called_once_with("hello")
        self.dialog._display_results.assert_called_once_with([])

    def test_execute_search_maps_core_and_core_keyword_to_vault(self):
        self.dialog.search_input.setText("python")
        self.dialog._search_session_history = MagicMock(return_value=[])
        self.dialog._display_results = MagicMock()
        mock_vault_results = [
            {"id": 1, "source": "episodic", "content": "episodic note", "score": 0.8},
            {"id": 2, "source": "core", "content": "core note", "score": 0.9},
            {"id": 3, "source": "core_keyword", "content": "keyword note", "score": 0.5},
        ]
        with patch("memory_vault.global_search", return_value=mock_vault_results):
            self.dialog._execute_search()
        
        passed_results = self.dialog._display_results.call_args[0][0]
        sources = [r["source"] for r in passed_results]
        self.assertEqual(sources, ["episodic", "vault", "vault"])
        self.assertEqual(passed_results[2]["content"], "keyword note")

    def test_execute_search_populates_result_tree_end_to_end(self):
        self.dialog.search_input.setText("test")
        mock_vault_results = [
            {"id": 10, "source": "core_keyword", "content": "keyword hit content", "score": 0.5}
        ]
        with patch("memory_vault.global_search", return_value=mock_vault_results):
            self.dialog._execute_search()
        self.dialog._search_timer.stop()
        self.assertIn("1 result", self.dialog.result_count_label.text())
        self.assertGreater(self.dialog.result_tree.topLevelItemCount(), 0)


class TestGlobalSearchDashboardContext(unittest.TestCase):
    """Test DashboardContext composition and routing on GlobalSearchDialog."""

    def setUp(self):
        import importlib, tabs.global_search as gs
        importlib.reload(gs)
        self._p = patch.object(gs.GlobalSearchDialog, "restore_requested", MagicMock())
        self._p.start()
        self.dialog = gs.GlobalSearchDialog(parent=None, controller=None)

    def tearDown(self):
        self._p.stop()

    def test_default_context_fallback(self):
        ctx = self.dialog.context
        self.assertIsNotNone(ctx)
        self.assertIsNone(ctx.controller)

    def test_context_setter_and_override(self):
        from tabs.context import DashboardContext
        custom_ctx = DashboardContext()
        self.dialog.context = custom_ctx
        self.assertIs(self.dialog.context, custom_ctx)
        self.assertIs(self.dialog.ctx, custom_ctx)

    def test_search_session_history_routes_via_context_controller(self):
        from tabs.context import DashboardContext
        mock_ctrl = MagicMock()
        mock_ctrl.history = [{"role": "user", "content": "decoupled context query", "ts": "12:00"}]
        self.dialog.context = DashboardContext(controller=mock_ctrl)
        res = self.dialog._search_session_history("decoupled")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["content"], "decoupled context query")

    def test_restore_selected_routes_via_context(self):
        from tabs.context import DashboardContext
        from PyQt6.QtWidgets import QTreeWidgetItem
        restored = []
        logged = []
        self.dialog.context = DashboardContext(
            restore_chat_input=lambda text: restored.append(text),
            log_to_audit=lambda msg: logged.append(msg)
        )
        item = QTreeWidgetItem(["Result text"])
        item.setData(0, 0x0100, {"content": "Text to restore into chat"})
        self.dialog.result_tree.addTopLevelItem(item)
        self.dialog.result_tree.setCurrentItem(item)

        self.dialog._restore_selected()
        self.assertEqual(restored, ["Text to restore into chat"])
        self.assertTrue(any("Global search restored content" in m for m in logged))

    def test_explicit_controller_overrides_parent_context_controller(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.context import DashboardContext
        import tabs.global_search as gs
        parent = QWidget()
        parent.context = DashboardContext(controller="PARENT_CTRL")
        dialog = gs.GlobalSearchDialog(parent=parent, controller="EXPLICIT_CTRL")
        self.assertEqual(dialog.context.controller, "EXPLICIT_CTRL")

    def test_parent_with_none_context_gracefully_falls_back(self):
        from PyQt6.QtWidgets import QWidget
        import tabs.global_search as gs
        parent = QWidget()
        parent.context = None
        parent.ctx = None
        dialog = gs.GlobalSearchDialog(parent=parent)
        self.assertIsNotNone(dialog.context)


if __name__ == "__main__":
    unittest.main()
