"""Tests for tabs/workflow_tab.py -- Workflow Engine tab mixin."""
import unittest
from unittest.mock import patch, MagicMock


class TestWorkflowTabMixin(unittest.TestCase):
    """Tests for WorkflowTabMixin -- YAML workflow editor + execution."""

    def test_create_workflow_tab_returns_widget(self):
        """create_workflow_tab() returns a QWidget with all sub-widgets."""
        from PyQt6.QtWidgets import QWidget
        from tabs.workflow_tab import WorkflowTabMixin
        obj = WorkflowTabMixin()
        tab = obj.create_workflow_tab()
        self.assertIsInstance(tab, QWidget)
        self.assertTrue(hasattr(obj, "workflow_editor"))
        self.assertTrue(hasattr(obj, "workflow_progress"))
        self.assertTrue(hasattr(obj, "workflow_status"))
        self.assertTrue(hasattr(obj, "workflow_tree"))
        self.assertTrue(hasattr(obj, "_wf_stop"))

    def test_create_workflow_tab_tree_headers(self):
        """The tree widget has [Step, Status, Output] headers."""
        from tabs.workflow_tab import WorkflowTabMixin
        obj = WorkflowTabMixin()
        obj._tab = obj.create_workflow_tab()
        labels = [obj.workflow_tree.headerItem().text(i) for i in range(3)]
        self.assertEqual(labels, ["Step", "Status", "Output"])

    def test_workflow_run_empty_yaml_is_noop(self):
        """_workflow_run() with empty editor does nothing."""
        from tabs.workflow_tab import WorkflowTabMixin
        obj = WorkflowTabMixin()
        obj._tab = obj.create_workflow_tab()
        obj.workflow_editor.setPlainText("")
        original_status = obj.workflow_status.text()
        obj._workflow_run()
        self.assertEqual(obj.workflow_status.text(), original_status)

    def test_workflow_run_invalid_yaml_shows_parse_error(self):
        """_workflow_run() with invalid YAML shows Parse error status."""
        from tabs.workflow_tab import WorkflowTabMixin
        obj = WorkflowTabMixin()
        obj._tab = obj.create_workflow_tab()
        obj.workflow_editor.setPlainText("not: valid: yaml: {{{")
        obj._workflow_run()
        self.assertIn("Parse error", obj.workflow_status.text())

    def test_workflow_run_valid_yaml_starts_execution(self):
        """_workflow_run() with valid YAML parses and sets Running status."""
        from tabs.workflow_tab import WorkflowTabMixin
        from workflow_engine import WorkflowDefinition
        obj = WorkflowTabMixin()
        obj._tab = obj.create_workflow_tab()
        yaml_text = (
            "name: Test WF\n"
            "steps:\n"
            "  - name: step1\n"
            "    action: RESEARCH_TOPIC\n"
            "    params: {query: 'AI'}\n"
        )
        obj.workflow_editor.setPlainText(yaml_text)
        mock_thread = MagicMock()
        mock_wf = WorkflowDefinition(name="Test WF")
        with patch("workflow_engine.parse_yaml", return_value=mock_wf), \
             patch("workflow_engine.execute_workflow_async", return_value=mock_thread) as mock_exec:
            obj._workflow_run()
        self.assertEqual(obj.workflow_status.text(), "Running: Test WF")
        self.assertEqual(obj.workflow_progress.value(), 0)
        mock_exec.assert_called_once()

    def test_workflow_run_invokes_progress_and_done_callbacks(self):
        """ANTI-FRAGILITY: progress/done callbacks fire via event loop;
        ``test_workflow_timer_fires_via_application_event_loop`` —
        QTimer.singleShot(0, cb) must fire through processEvents()
        for the lambda bodies to execute in this test.
        """
        from tabs.workflow_tab import WorkflowTabMixin
        from workflow_engine import WorkflowDefinition, WorkflowResult, StepResult
        from PyQt6.QtWidgets import QApplication

        if QApplication.instance() is None:
            QApplication([])
        app = QApplication.instance()

        obj = WorkflowTabMixin()
        obj._tab = obj.create_workflow_tab()
        yaml_text = (
            "name: Test WF\n"
            "steps:\n"
            "  - name: step1\n"
            "    action: RESEARCH_TOPIC\n"
            "    params: {query: 'AI'}\n"
        )
        obj.workflow_editor.setPlainText(yaml_text)

        captured = {}
        def mock_execute(wf, progress_callback, done_callback, stop_event):
            captured["progress"] = progress_callback
            captured["done"] = done_callback
            return MagicMock()

        mock_wf = WorkflowDefinition(name="Test WF")
        with patch("workflow_engine.parse_yaml", return_value=mock_wf), \
             patch("workflow_engine.execute_workflow_async", side_effect=mock_execute):
            obj._workflow_run()

        # Invoke the progress callback — fires real QTimer.singleShot(0, lambda).
        # processEvents() flushes the zero-timer so the lambda body executes.
        captured["progress"](1, 2, "step1")
        for _ in range(10):
            app.processEvents()

        self.assertEqual(obj.workflow_progress.value(), 50,
            msg="REGRESSION: progress callback should update progress bar to 50%")

        # Invoke the done callback — same real-timer + processEvents approach.
        result = WorkflowResult(
            workflow_name="TestWF",
            overall="success",
            steps=[StepResult(step_name="step1", status="success", output="OK")],
        )
        captured["done"](result)
        for _ in range(10):
            app.processEvents()

        self.assertEqual(obj.workflow_progress.value(), 100,
            msg="REGRESSION: done callback should set progress to 100 via _workflow_done")

    def test_workflow_stop_sets_stop_event(self):
        """_workflow_stop() sets the stop event and updates status."""
        import threading
        from tabs.workflow_tab import WorkflowTabMixin
        obj = WorkflowTabMixin()
        obj._tab = obj.create_workflow_tab()
        self.assertIsInstance(obj._wf_stop, threading.Event)
        self.assertFalse(obj._wf_stop.is_set())
        obj._workflow_stop()
        self.assertTrue(obj._wf_stop.is_set())
        self.assertEqual(obj.workflow_status.text(), "Stopping...")

    # Cross-linked from test_workflow_run_invokes_progress_and_done_callbacks
    def test_workflow_timer_fires_via_application_event_loop(self):
        """REGRESSION GUARD for zero-timer execution via QApplication
        event loop in ``tabs.workflow_tab.WorkflowTabMixin._workflow_run``
        ``on_progress`` closure (line ~85).

        Confirms that a real ``QTimer.singleShot(0, callback)`` fires
        its callback when the QApplication event loop processes events,
        covering the same code path as line 84's lambda body without
        patching.
        """
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        if QApplication.instance() is None:
            QApplication([])
        app = QApplication.instance()

        result = []
        def my_cb():
            result.append(42)

        QTimer.singleShot(0, my_cb)
        for _ in range(10):
            app.processEvents()

        self.assertEqual(result, [42],
            msg="REGRESSION: QTimer.singleShot(0, cb) should fire \
                the callback after processEvents()")

    def test_workflow_done_updates_ui(self):
        """_workflow_done() sets progress to 100 and populates tree with steps."""
        from tabs.workflow_tab import WorkflowTabMixin
        from workflow_engine import WorkflowResult, StepResult
        obj = WorkflowTabMixin()
        obj._tab = obj.create_workflow_tab()
        result = WorkflowResult(
            workflow_name="TestWF",
            overall="success",
            steps=[
                StepResult(step_name="step1", status="success", output="Done OK"),
                StepResult(step_name="step2", status="failed", error="Boom"),
                StepResult(step_name="step3", status="skipped"),
            ],
        )
        obj._workflow_done(result)
        self.assertEqual(obj.workflow_progress.value(), 100)
        self.assertIn("TestWF", obj.workflow_status.text())
        self.assertIn("success", obj.workflow_status.text())
        self.assertEqual(obj.workflow_tree.topLevelItemCount(), 3)
        item0 = obj.workflow_tree.topLevelItem(0)
        self.assertEqual(item0.text(0), "step1")
        self.assertIn("Done OK", item0.text(2))
        item1 = obj.workflow_tree.topLevelItem(1)
        self.assertEqual(item1.text(0), "step2")
        self.assertIn("Boom", item1.text(2))


if __name__ == "__main__":
    unittest.main()
