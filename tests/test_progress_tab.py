"""
Tests for tabs/progress_tab.py - Execution Progress tab mixin.
"""
import unittest
from unittest.mock import MagicMock, patch

class _MockParent:
    pass

# ============================================================
# Helper function tests
# ============================================================

class TestHtmlEscape(unittest.TestCase):
    def test_ampersand(self):
        from tabs.progress_tab import _html_escape
        self.assertEqual(_html_escape("a & b"), "a &amp; b")

    def test_angle_brackets(self):
        from tabs.progress_tab import _html_escape
        self.assertEqual(_html_escape("<div>"), "&lt;div&gt;")

    def test_quotes(self):
        from tabs.progress_tab import _html_escape
        self.assertEqual(_html_escape('"'), "&quot;")
        self.assertEqual(_html_escape("'"), "&#x27;")



class TestBuildAgentCard(unittest.TestCase):
    def test_returns_qframe(self):
        from tabs.progress_tab import _build_agent_card
        from PyQt6.QtWidgets import QFrame
        card = _build_agent_card("Coder", "running", "Working...", "#10B981")
        self.assertIsInstance(card, QFrame)


class TestStatusDot(unittest.TestCase):
    def test_default_is_idle(self):
        from tabs.progress_tab import _StatusDot
        dot = _StatusDot()
        self.assertIn("#6B7280", dot.styleSheet())

    def test_set_active_changes_color(self):
        from tabs.progress_tab import _StatusDot
        dot = _StatusDot()
        dot.set_active(True, "#10B981")
        self.assertIn("#10B981", dot.styleSheet())

    def test_set_inactive_returns_to_idle(self):
        from tabs.progress_tab import _StatusDot
        dot = _StatusDot()
        dot.set_active(True, "#10B981")
        dot.set_active(False)
        self.assertIn("#6B7280", dot.styleSheet())


class TestProgressSection(unittest.TestCase):
    def test_initial_header_text(self):
        from tabs.progress_tab import _ProgressSection
        section = _ProgressSection("Test Title", "#3B82F6")
        self.assertIn("Test Title", section._header.text())

    def test_toggle_collapses_content(self):
        from tabs.progress_tab import _ProgressSection
        section = _ProgressSection("Test", "#3B82F6")
        self.assertFalse(section._content.isHidden())
        section._toggle()
        self.assertTrue(section._content.isHidden())
        self.assertTrue(section._collapsed)

    def test_toggle_expands_content(self):
        from tabs.progress_tab import _ProgressSection
        section = _ProgressSection("Test", "#3B82F6")
        section._toggle()
        section._toggle()
        self.assertFalse(section._content.isHidden())
        self.assertFalse(section._collapsed)

    def test_content_layout(self):
        from tabs.progress_tab import _ProgressSection
        section = _ProgressSection("Test", "#3B82F6")
        layout = section.content_layout()
        self.assertIsNotNone(layout)

# ============================================================
# Mixin method tests (no QApplication needed)
# ============================================================

class TestOnPipelineUpdate(unittest.TestCase):
    def test_updates_progress_bar(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._pipe_progress = MagicMock()
        p._pipe_dot = MagicMock()
        p._pipe_stage = MagicMock()
        p._pipe_detail = MagicMock()
        p._overall_status = MagicMock()
        p._overall_label = MagicMock()
        p._set_nonidle = MagicMock()
        p._pipeline_step = 0
        p._pipeline_total = 5
        ProgressTabMixin._on_pipeline_update(p, 3, 5, "Processing STEP 3/5")
        p._pipe_progress.setValue.assert_called_with(60)
        p._pipe_dot.set_active.assert_called_with(True, "#3B82F6")
        p._pipe_stage.setText.assert_called_with("Pipeline Running")


class TestOnRagUpdate(unittest.TestCase):
    def setUp(self):
        from tabs.progress_tab import ProgressTabMixin
        self.p = _MockParent()
        self.p._rag_dot = MagicMock()
        self.p._rag_stage = MagicMock()
        self.p._rag_hops_label = MagicMock()
        self.p._rag_questions_label = MagicMock()
        self.p._rag_sources_label = MagicMock()
        self.p._rag_sub_list = MagicMock()
        self.p._rag_sub_questions = []
        self.p._set_nonidle = MagicMock()
        self.p._update_rag_sub_list = MagicMock()
        self.p._overall_status = MagicMock()
        self.p._overall_label = MagicMock()

    def test_structured_dict_phase(self):
        from tabs.progress_tab import ProgressTabMixin
        data = {"phase": "retrieve", "hop": 2, "total_hops": 3,
                "sub_questions": ["Q1?"]}
        ProgressTabMixin._on_rag_update(self.p, data)
        self.p._rag_hops_label.setText.assert_called_with("Hops: 2/3")
        self.p._rag_questions_label.setText.assert_called_with("Sub-Questions: 1")

    def test_complete_phase(self):
        from tabs.progress_tab import ProgressTabMixin
        data = {"phase": "complete", "hop": 3, "total_hops": 3}
        ProgressTabMixin._on_rag_update(self.p, data)
        self.p._rag_stage.setText.assert_called_with("Complete")

    def test_legacy_message_with_hop(self):
        from tabs.progress_tab import ProgressTabMixin
        data = {"phase": "message", "message": "RAG: Hop 2/3 completed"}
        ProgressTabMixin._on_rag_update(self.p, data)
        self.p._rag_hops_label.setText.assert_called_with("Hops: 2/3")


class TestOnWorkflowUpdate(unittest.TestCase):
    def test_updates_widgets(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._wf_progress = MagicMock()
        p._wf_dot = MagicMock()
        p._wf_stage = MagicMock()
        p._wf_step_name = MagicMock()
        p._set_nonidle = MagicMock()
        p._overall_status = MagicMock()
        p._overall_label = MagicMock()
        p._workflow_done = 0
        p._workflow_total = 0
        ProgressTabMixin._on_workflow_update(p, 2, 5, "Extract data")
        p._wf_progress.setValue.assert_called_with(40)
        p._wf_stage.setText.assert_called_with("Running: Extract data")


class TestOnSubagentUpdate(unittest.TestCase):
    def setUp(self):
        from tabs.progress_tab import ProgressTabMixin
        self.T = ProgressTabMixin
        self.p = _MockParent()
        self.p._subagents = {}
        self.p._sa_dot = MagicMock()
        self.p._sa_stage = MagicMock()
        self.p._sa_cards = MagicMock()
        self.p._set_nonidle = MagicMock()
        self.p._overall_status = MagicMock()
        self.p._overall_label = MagicMock()
        self.p._rebuild_agent_cards = MagicMock()

    def test_running_adds_agent(self):
        self.T._on_subagent_update(self.p, "Coder", "running", "Working...")
        self.assertIn("Coder", self.p._subagents)
        self.assertEqual(self.p._subagents["Coder"]["status"], "running")

    def test_complete_marks_idle(self):
        self.p._subagents["Coder"] = {"status": "running", "message": "", "color": "#10B981"}
        self.T._on_subagent_update(self.p, "Coder", "complete", "Done")
        self.assertEqual(self.p._subagents["Coder"]["status"], "complete")


class TestParseToolMessage(unittest.TestCase):
    def setUp(self):
        from tabs.progress_tab import ProgressTabMixin
        self.T = ProgressTabMixin
        self.p = _MockParent()
        self.p._tool_call_signal = MagicMock()

    def test_start_pattern(self):
        self.T._parse_tool_message(self.p, "[TOOL] START SEARCH_WEB query=hello")
        self.p._tool_call_signal.emit.assert_called_with("SEARCH_WEB", "running", "query=hello")

    def test_done_pattern(self):
        self.T._parse_tool_message(self.p, "[TOOL] DONE SEARCH_WEB: Found 3 results")
        self.p._tool_call_signal.emit.assert_called_with("SEARCH_WEB", "complete", "Found 3 results")

    def test_fail_pattern(self):
        self.T._parse_tool_message(self.p, "[TOOL] FAIL SEARCH_WEB: timeout after 30s")
        self.p._tool_call_signal.emit.assert_called_with("SEARCH_WEB", "failed", "timeout after 30s")


class TestParseSubagentMessage(unittest.TestCase):
    def test_starting_agent(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._on_subagent_update = MagicMock()
        ProgressTabMixin._parse_subagent_message(
            p, "[SUB-AGENT] Starting Coder agent on: write tests")
        p._on_subagent_update.assert_called()
        args = p._on_subagent_update.call_args[0]
        self.assertEqual(args[0], "Coder")
        self.assertEqual(args[1], "running")

    def test_complete_agent(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._on_subagent_update = MagicMock()
        ProgressTabMixin._parse_subagent_message(
            p, "[SUB-AGENT] Coder complete (123 chars)")
        args = p._on_subagent_update.call_args[0]
        self.assertEqual(args[0], "Coder")
        self.assertEqual(args[1], "complete")

    def test_crew_start(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._on_subagent_update = MagicMock()
        ProgressTabMixin._parse_subagent_message(
            p, "[CREW] Starting SubAgent: Researcher")
        args = p._on_subagent_update.call_args[0]
        self.assertEqual(args[0], "Researcher")
        self.assertEqual(args[1], "running")

    def test_async_start(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._on_subagent_update = MagicMock()
        ProgressTabMixin._parse_subagent_message(
            p, "[SUB-AGENT] Async start: Planner")
        args = p._on_subagent_update.call_args[0]
        self.assertEqual(args[0], "Planner")
        self.assertEqual(args[1], "running")

    def test_failed_agent(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._on_subagent_update = MagicMock()
        ProgressTabMixin._parse_subagent_message(
            p, "[SUB-AGENT] Coder failed: timeout after 60s")
        args = p._on_subagent_update.call_args[0]
        self.assertEqual(args[0], "Coder")
        self.assertEqual(args[1], "failed")


class TestSetNonIdle(unittest.TestCase):
    def test_updates_overall_status(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._overall_status = MagicMock()
        p._overall_label = MagicMock()
        ProgressTabMixin._set_nonidle(p, "Pipeline: Step 1/5", "#3B82F6")
        p._overall_status.set_active.assert_called_with(True, "#3B82F6")
        p._overall_label.setText.assert_called_with("Pipeline: Step 1/5")


class TestOnToolCall(unittest.TestCase):
    def test_running_adds_entry(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._tool_calls = []
        p._tc_dot = MagicMock()
        p._tc_stage = MagicMock()
        p._tc_list = MagicMock()
        p._set_nonidle = MagicMock()
        p._rebuild_tool_list = MagicMock()
        p._overall_status = MagicMock()
        p._overall_label = MagicMock()
        ProgressTabMixin._on_tool_call(p, "SEARCH_WEB", "running", "q=hello")
        self.assertEqual(len(p._tool_calls), 1)
        self.assertEqual(p._tool_calls[0]["command"], "SEARCH_WEB")

    def test_limits_to_10(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._tool_calls = []
        p._tc_dot = MagicMock()
        p._tc_stage = MagicMock()
        p._tc_list = MagicMock()
        p._set_nonidle = MagicMock()
        p._rebuild_tool_list = MagicMock()
        p._overall_status = MagicMock()
        p._overall_label = MagicMock()
        for i in range(15):
            ProgressTabMixin._on_tool_call(p, "CMD" + str(i), "running", "")
        self.assertEqual(len(p._tool_calls), 10)

    def test_failed_sets_error_color(self):
        from tabs.progress_tab import ProgressTabMixin
        p = _MockParent()
        p._tool_calls = []
        p._tc_dot = MagicMock()
        p._tc_stage = MagicMock()
        p._tc_list = MagicMock()
        p._set_nonidle = MagicMock()
        p._rebuild_tool_list = MagicMock()
        p._overall_status = MagicMock()
        p._overall_label = MagicMock()
        ProgressTabMixin._on_tool_call(p, "SEARCH_WEB", "failed", "timeout")
        p._tc_dot.set_active.assert_called_with(True, "#EF4444")


class TestQtLogHandler(unittest.TestCase):
    def test_forwards_watched_loggers(self):
        from tabs.progress_tab import _QtLogHandler
        import logging
        cb = MagicMock()
        handler = _QtLogHandler(cb)
        record = logging.LogRecord("SubAgents", logging.INFO, "", 0, "test message", (), None)
        handler.emit(record)
        cb.assert_called_with("test message")

    def test_ignores_unwatched_loggers(self):
        from tabs.progress_tab import _QtLogHandler
        import logging
        cb = MagicMock()
        handler = _QtLogHandler(cb)
        record = logging.LogRecord("SomeOther", logging.INFO, "", 0, "ignored", (), None)
        handler.emit(record)
        cb.assert_not_called()


class TestProgressModule(unittest.TestCase):
    def test_module_imports(self):
        import tabs.progress_tab
        for name in ["_html_escape", "_build_agent_card", "_StatusDot",
                     "_ProgressSection", "ProgressTabMixin", "_QtLogHandler"]:
            self.assertTrue(hasattr(tabs.progress_tab, name), f"Missing {name}")


if __name__ == "__main__":
    unittest.main()

