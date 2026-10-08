"""Tests for tabs/chat_history_tab.py."""
import unittest
from unittest.mock import patch, MagicMock


class TestFilterSection(unittest.TestCase):
    """Test _filter_section and _filter_chat_tree."""
    def setUp(self):
        import importlib, tabs.chat_history_tab as ch
        importlib.reload(ch)
        self.mixin = ch.ChatHistoryTabMixin()

    def test_no_text_shows_all(self):
        section = MagicMock()
        section.childCount.return_value = 2
        c0, c1 = MagicMock(), MagicMock()
        section.child.side_effect = lambda i: [c0, c1][i]
        self.mixin._filter_section(section, "")
        c0.setHidden.assert_called_with(False)
        c1.setHidden.assert_called_with(False)
        section.setHidden.assert_called_with(False)

    def test_matching_text_shows_child(self):
        section = MagicMock()
        section.childCount.return_value = 1
        c0 = MagicMock()
        c0.text.return_value = "hello world"
        section.child.return_value = c0
        self.mixin._filter_section(section, "hello")
        c0.setHidden.assert_called_with(False)
        section.setHidden.assert_called_with(False)

    def test_non_matching_text_hides_child(self):
        section = MagicMock()
        section.childCount.return_value = 1
        c0 = MagicMock()
        c0.text.return_value = "goodbye"
        section.child.return_value = c0
        self.mixin._filter_section(section, "hello")
        c0.setHidden.assert_called_with(True)
        section.setHidden.assert_called_with(True)

    def test_role_text_matches(self):
        section = MagicMock()
        section.childCount.return_value = 1
        c0 = MagicMock()
        c0.text.return_value = "some content"
        section.child.return_value = c0
        self.mixin._filter_section(section, "some")
        c0.setHidden.assert_called_with(False)


class TestFilterChatTree(unittest.TestCase):
    """Test _filter_chat_tree delegates to _filter_section."""
    def setUp(self):
        import importlib, tabs.chat_history_tab as ch
        importlib.reload(ch)
        self.mixin = ch.ChatHistoryTabMixin()
        self.mixin.chat_history_tree = MagicMock()

    def test_filters_all_sections(self):
        root = MagicMock()
        root.childCount.return_value = 2
        s0, s1 = MagicMock(), MagicMock()
        s0.childCount.return_value = 1
        s1.childCount.return_value = 0
        root.child.side_effect = lambda i: [s0, s1][i]
        self.mixin.chat_history_tree.invisibleRootItem.return_value = root
        # Mock _filter_section to track calls
        with patch.object(self.mixin, '_filter_section') as mock_fs:
            self.mixin._filter_chat_tree("hello")
        self.assertEqual(mock_fs.call_count, 2)


class TestCollectTreeData(unittest.TestCase):
    """Test _collect_tree_data walks tree and returns structured data."""
    def setUp(self):
        import importlib, tabs.chat_history_tab as ch
        importlib.reload(ch)
        self.mixin = ch.ChatHistoryTabMixin()
        self.mixin.chat_history_tree = MagicMock()

    def _make_section_with_children(self, title, children_data):
        section = MagicMock()
        section.text.return_value = title
        children = []
        for label, role, ts, userdata in children_data:
            child = MagicMock()
            child.text.side_effect = lambda c, l=label, r=role, t=ts: [l, r, t][c]
            child.isHidden.return_value = False
            child.data.return_value = userdata
            children.append(child)
        section.childCount.return_value = len(children)
        section.child.side_effect = lambda i: children[i]
        return section

    def test_collects_messages(self):
        s0 = self._make_section_with_children("Current Session", [
            ("hello world", "user", "10:00", {"type":"message","role":"user","content":"hello world","ts":"10:00"}),
        ])
        root = MagicMock()
        root.childCount.return_value = 1
        root.child.return_value = s0
        self.mixin.chat_history_tree.invisibleRootItem.return_value = root

        data = self.mixin._collect_tree_data()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["items"][0]["type"], "message")

    def test_skips_placeholders(self):
        s0 = self._make_section_with_children("Journal", [
            ("(no past sessions)", "", "", {}),
            ("valid entry", "episodic", "09:00", {"type":"episodic","summary":"summary"}),
        ])
        root = MagicMock()
        root.childCount.return_value = 1
        root.child.return_value = s0
        self.mixin.chat_history_tree.invisibleRootItem.return_value = root

        data = self.mixin._collect_tree_data()
        self.assertEqual(len(data), 1)
        self.assertEqual(len(data[0]["items"]), 1)

    def test_empty_tree_returns_empty(self):
        root = MagicMock()
        root.childCount.return_value = 0
        self.mixin.chat_history_tree.invisibleRootItem.return_value = root
        data = self.mixin._collect_tree_data()
        self.assertEqual(data, [])


class TestOnTreeItemClicked(unittest.TestCase):
    """Test _on_tree_item_clicked shows preview for different message types."""
    def setUp(self):
        import importlib, tabs.chat_history_tab as ch
        importlib.reload(ch)
        self.mixin = ch.ChatHistoryTabMixin()
        self.mixin.chat_preview = MagicMock()

    def test_message_type_shows_content(self):
        item = MagicMock()
        item.data.return_value = {"type":"message","role":"user","content":"hello","ts":"10:00"}
        self.mixin._on_tree_item_clicked(item, 0)
        self.mixin.chat_preview.setPlainText.assert_called_once()
        call_arg = self.mixin.chat_preview.setPlainText.call_args[0][0]
        self.assertIn("USER", call_arg)
        self.assertIn("hello", call_arg)

    def test_episodic_shows_summary(self):
        item = MagicMock()
        item.data.return_value = {"type":"episodic","summary":"past session","ts":"09:00","importance":7,"tags":["topic1"]}
        self.mixin._on_tree_item_clicked(item, 0)
        call_arg = self.mixin.chat_preview.setPlainText.call_args[0][0]
        self.assertIn("EPISODIC", call_arg)
        self.assertIn("past session", call_arg)
        self.assertIn("topic1", call_arg)

    def test_vault_shows_content(self):
        item = MagicMock()
        item.data.return_value = {"type":"vault","content":"long-term memory content"}
        self.mixin._on_tree_item_clicked(item, 0)
        call_arg = self.mixin.chat_preview.setPlainText.call_args[0][0]
        self.assertIn("LONG-TERM", call_arg)
        self.assertIn("long-term memory content", call_arg)

    def test_no_data_skips(self):
        item = MagicMock()
        item.data.return_value = None
        self.mixin._on_tree_item_clicked(item, 0)
        self.mixin.chat_preview.setPlainText.assert_not_called()


class TestCopyPreviewToClipboard(unittest.TestCase):
    """Test _copy_preview_to_clipboard."""
    def setUp(self):
        import importlib, tabs.chat_history_tab as ch
        importlib.reload(ch)
        self.mixin = ch.ChatHistoryTabMixin()
        self.mixin.chat_preview = MagicMock()

    def test_copies_text_to_clipboard(self):
        self.mixin.chat_preview.toPlainText.return_value = "some text"
        with patch("PyQt6.QtWidgets.QApplication.clipboard") as clip:
            mock_cb = MagicMock()
            clip.return_value = mock_cb
            self.mixin._copy_preview_to_clipboard()
        mock_cb.setText.assert_called_once_with("some text")

    def test_empty_text_skips(self):
        self.mixin.chat_preview.toPlainText.return_value = ""
        with patch("PyQt6.QtWidgets.QApplication.clipboard") as clip:
            mock_cb = MagicMock()
            clip.return_value = mock_cb
            self.mixin._copy_preview_to_clipboard()
        mock_cb.setText.assert_not_called()


class TestRefreshChatHistory(unittest.TestCase):
    """Test _refresh_chat_history populates tree from controller + vault."""
    def setUp(self):
        import importlib, tabs.chat_history_tab as ch
        importlib.reload(ch)
        self.mixin = ch.ChatHistoryTabMixin()
        self.mixin.chat_history_tree = MagicMock()
        self.mixin.chat_stats_label = MagicMock()
        self.mixin.controller = MagicMock()

    def test_refresh_empty_history(self):
        self.mixin.controller.history = []
        with patch("tabs.chat_history_tab.QTreeWidgetItem"), \
             patch("memory_vault.get_recent_episodic", return_value=[]), \
             patch("memory_vault.retrieve_all_memories", return_value=""):
            self.mixin._refresh_chat_history()
        self.mixin.chat_history_tree.clear.assert_called_once()

    def test_refresh_with_context_controller(self):
        from tabs.context import DashboardContext
        mock_ctrl = MagicMock()
        mock_ctrl.history = [{"role": "user", "content": "hi", "ts": "10:00"}]
        self.mixin.ctx = DashboardContext(controller=mock_ctrl)
        with patch("tabs.chat_history_tab.QTreeWidgetItem"), \
             patch("memory_vault.get_recent_episodic", return_value=[]), \
             patch("memory_vault.retrieve_all_memories", return_value=""):
            self.mixin._refresh_chat_history()
        self.mixin.chat_history_tree.clear.assert_called_once()


class TestDecoupledContextComposition(unittest.TestCase):
    """Test composition-based decoupling on ChatHistoryTabMixin."""
    def setUp(self):
        import importlib, tabs.chat_history_tab as ch
        importlib.reload(ch)
        self.mixin = ch.ChatHistoryTabMixin()

    def test_restore_chat_input_via_context_bridge(self):
        from tabs.context import DashboardContext
        restored = []
        self.mixin.ctx = DashboardContext(restore_chat_input=lambda text: restored.append(text))
        self.mixin.chat_history_tree = MagicMock()
        mock_item = MagicMock()
        mock_item.data.return_value = {"content": "restored prompt"}
        self.mixin.chat_history_tree.currentItem.return_value = mock_item

        self.mixin._restore_to_chat_input()
        self.assertEqual(restored, ["restored prompt"])

    def test_clear_session_via_context_bridge(self):
        from tabs.context import DashboardContext
        mock_ctrl = MagicMock()
        mock_ctrl.wipe_memory.return_value = "cleared"
        logged = []
        self.mixin.ctx = DashboardContext(
            controller=mock_ctrl,
            log_to_audit=lambda msg: logged.append(msg)
        )
        self.mixin._refresh_chat_history = MagicMock()

        self.mixin._clear_current_session()
        mock_ctrl.wipe_memory.assert_called_once()
        self.mixin._refresh_chat_history.assert_called_once()
        self.assertTrue(any("Current session chat cleared" in m for m in logged))
