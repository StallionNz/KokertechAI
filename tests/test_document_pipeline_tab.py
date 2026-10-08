"""Tests for tabs/document_pipeline_tab.py."""
import unittest
from unittest.mock import patch, MagicMock
import os
import sys


class TestClassifyFilePreview(unittest.TestCase):
    """Test _classify_file_preview function.

    Production delegates to doc_pipeline._classify_file, so these tests
    exercise the REAL doc_pipeline module (fidelity over mocks) and assert
    the extension→type contract at both ends of the delegation.
    """
    def setUp(self):
        import importlib, tabs.document_pipeline_tab as dp
        importlib.reload(dp)
        self.dp = dp

    def test_pdf_returns_pdf(self):
        self.assertEqual(self.dp._classify_file_preview("test.pdf"), "pdf")

    def test_unknown_returns_skip(self):
        self.assertEqual(self.dp._classify_file_preview("test.xyz"), "skip")

    def test_image_returns_image(self):
        self.assertEqual(self.dp._classify_file_preview("photo.png"), "image")

    def test_delegates_to_doc_pipeline_classify_file(self):
        """The tab helper must delegate to doc_pipeline._classify_file so
        the two classifiers can never drift apart (regression guard for
        the FILE_TYPE_HANDLERS → delegation migration)."""
        import doc_pipeline
        with patch.object(doc_pipeline, "_classify_file", return_value="text") as m:
            result = self.dp._classify_file_preview("notes.txt")
        m.assert_called_once_with("notes.txt")
        self.assertEqual(result, "text")

    def test_fallback_when_doc_pipeline_lacks_classify(self):
        """If the import target ever loses _classify_file, the helper must
        degrade to 'skip', not raise."""
        import types
        fake_dp = types.ModuleType("doc_pipeline")  # no _classify_file attr
        with patch.dict("sys.modules", {"doc_pipeline": fake_dp}):
            self.assertEqual(self.dp._classify_file_preview("test.xyz"), "skip")


class TestGetFileSizeStr(unittest.TestCase):
    """Test _get_file_size_str function."""
    def setUp(self):
        import importlib, tabs.document_pipeline_tab as dp
        importlib.reload(dp)
        self.fn = dp._get_file_size_str

    def test_bytes(self):
        with patch('os.path.getsize', return_value=500):
            self.assertEqual(self.fn("test.txt"), "500B")

    def test_kb(self):
        with patch('os.path.getsize', return_value=2048):
            self.assertEqual(self.fn("test.txt"), "2KB")

    def test_mb(self):
        with patch('os.path.getsize', return_value=2_500_000):
            self.assertEqual(self.fn("test.txt"), "2.4MB")

    def test_oserror_returns_question(self):
        with patch('os.path.getsize', side_effect=OSError):
            self.assertEqual(self.fn("test.txt"), "?")


class TestClassifyAndLabel(unittest.TestCase):
    """Test _classify_and_label method."""
    def setUp(self):
        import importlib, tabs.document_pipeline_tab as dp
        importlib.reload(dp)
        self.mixin = dp.DocumentPipelineTabMixin()

    def test_returns_label_and_type(self):
        with patch('tabs.document_pipeline_tab._classify_file_preview', return_value='pdf') as m_cf:
            with patch('tabs.document_pipeline_tab._get_file_size_str', return_value='1MB') as m_fs:
                with patch('os.path.basename', return_value='doc.pdf'):
                    label, ftype = self.mixin._classify_and_label("/path/doc.pdf")
        self.assertIn("doc.pdf", label)
        self.assertIn("1MB", label)
        self.assertEqual(ftype, "pdf")


class TestPipelineFileManagement(unittest.TestCase):
    """Test queue management: add, clear, remove, update counts."""
    def setUp(self):
        import importlib, tabs.document_pipeline_tab as dp
        importlib.reload(dp)
        self.mixin = dp.DocumentPipelineTabMixin()
        self.mixin._pipeline_files = []
        self.mixin.pipeline_file_list = MagicMock()
        self.mixin.pipeline_count_label = MagicMock()
        self.mixin.pipeline_classification_label = MagicMock()
        self.mixin.pipeline_status_label = MagicMock()
        self.mixin.pipeline_drop_zone = MagicMock()
        self.mixin.btn_process = MagicMock()
        self.mixin.btn_retry_failed = MagicMock()
        self.mixin.btn_view_graph = MagicMock()
        self.mixin.btn_export = MagicMock()
        self.mixin.pipeline_stats_success = MagicMock()
        self.mixin.pipeline_stats_skipped = MagicMock()
        self.mixin.pipeline_stats_errors = MagicMock()
        self.mixin.pipeline_stats_nodes = MagicMock()
        self.mixin.pipeline_results_log = MagicMock()
        self.mixin._pipeline_processed = False

    def test_update_counts_no_files(self):
        self.mixin._pipeline_files = []
        self.mixin.pipeline_file_list.count.return_value = 0
        self.mixin._pipeline_update_counts()
        self.mixin.pipeline_count_label.setText.assert_called()
        self.mixin.btn_process.setEnabled.assert_called_with(False)

    def test_update_counts_with_files(self):
        self.mixin._pipeline_files = ["f1.pdf", "f2.txt"]
        self.mixin.pipeline_file_list.count.return_value = 2
        item1 = MagicMock()
        item2 = MagicMock()
        item1.data.return_value = "pdf"
        item2.data.return_value = "text"
        self.mixin.pipeline_file_list.item.side_effect = [item1, item2]
        self.mixin._pipeline_update_counts()
        self.mixin.btn_process.setEnabled.assert_called_with(True)

    def test_clear_files_resets_state(self):
        self.mixin._pipeline_files = ["f1.pdf"]
        self.mixin._pipeline_clear_files()
        self.assertEqual(self.mixin._pipeline_files, [])
        self.mixin.pipeline_file_list.clear.assert_called_once()
        self.mixin.btn_retry_failed.setEnabled.assert_called_with(False)
        self.mixin.btn_view_graph.setEnabled.assert_called_with(False)
        self.assertFalse(self.mixin._pipeline_processed)

    def test_remove_selected_pops_from_list(self):
        self.mixin._pipeline_files = ["f1.pdf", "f2.txt"]
        selected_item = MagicMock()
        self.mixin.pipeline_file_list.selectedItems.return_value = [selected_item]
        self.mixin.pipeline_file_list.row.return_value = 0
        self.mixin._pipeline_remove_selected()
        self.mixin.pipeline_file_list.takeItem.assert_called_once_with(0)
        self.assertEqual(len(self.mixin._pipeline_files), 1)
        self.assertEqual(self.mixin._pipeline_files[0], "f2.txt")

    def test_update_dropzone_hint_empty(self):
        self.mixin._pipeline_files = []
        self.mixin._update_dropzone_hint()
        self.mixin.pipeline_drop_zone.setText.assert_called_once()

    def test_update_dropzone_hint_with_files(self):
        self.mixin._pipeline_files = ["f1.pdf", "f2.pdf", "f3.pdf"]
        self.mixin._update_dropzone_hint()
        arg = self.mixin.pipeline_drop_zone.setText.call_args[0][0]
        self.assertIn("3 files", arg)

    def test_set_group_title_processed(self):
        self.mixin._pipeline_files = ["f1.pdf"]
        self.mixin._pipeline_set_group_title(True)
        self.assertTrue(self.mixin._pipeline_processed)
        self.mixin.btn_process.setEnabled.assert_called_with(False)

    def test_set_group_title_queued(self):
        self.mixin._pipeline_files = ["f1.pdf"]
        self.mixin.pipeline_file_list.count.return_value = 1
        self.mixin._pipeline_set_group_title(False)
        self.assertFalse(self.mixin._pipeline_processed)


class TestResetStats(unittest.TestCase):
    """Test _reset_stats resets all counters."""
    def setUp(self):
        import importlib, tabs.document_pipeline_tab as dp
        importlib.reload(dp)
        self.mixin = dp.DocumentPipelineTabMixin()
        self.mixin.pipeline_stats_success = MagicMock()
        self.mixin.pipeline_stats_skipped = MagicMock()
        self.mixin.pipeline_stats_errors = MagicMock()
        self.mixin.pipeline_stats_nodes = MagicMock()
        self.mixin.btn_export = MagicMock()

    def test_resets_all_labels_to_zero(self):
        self.mixin._reset_stats()
        self.mixin.pipeline_stats_success.setText.assert_called_with("\u2705 0")
        self.mixin.pipeline_stats_errors.setText.assert_called_with("\u274c 0")
        self.mixin.btn_export.setEnabled.assert_called_with(False)


class TestSyncFilesFromList(unittest.TestCase):
    """Test _pipeline_sync_files_from_list rebuilds _pipeline_files after drag-reorder."""
    def setUp(self):
        import importlib, tabs.document_pipeline_tab as dp
        importlib.reload(dp)
        self.mixin = dp.DocumentPipelineTabMixin()
        self.mixin._pipeline_files = []
        self.mixin.pipeline_file_list = MagicMock()

    def test_rebuilds_from_list_order(self):
        self.mixin.pipeline_file_list.count.return_value = 2
        item1 = MagicMock()
        item2 = MagicMock()
        item1.data.return_value = "/path/b.pdf"
        item2.data.return_value = "/path/a.pdf"
        self.mixin.pipeline_file_list.item.side_effect = [item1, item2]
        with patch('os.path.isfile', return_value=True):
            self.mixin._pipeline_sync_files_from_list()
        self.assertEqual(self.mixin._pipeline_files, ["/path/b.pdf", "/path/a.pdf"])


class TestDocPipelineDashboardContext(unittest.TestCase):
    """Test DashboardContext composition and routing on DocumentPipelineTabMixin."""

    def setUp(self):
        import importlib, tabs.document_pipeline_tab as dp
        importlib.reload(dp)
        self.mixin = dp.DocumentPipelineTabMixin()

    def test_default_context_fallback(self):
        ctx = self.mixin.context
        self.assertIsNotNone(ctx)

    def test_context_setter_and_override(self):
        from tabs.context import DashboardContext
        custom_ctx = DashboardContext()
        self.mixin.context = custom_ctx
        self.assertIs(self.mixin.context, custom_ctx)
        self.assertIs(self.mixin.ctx, custom_ctx)

    def test_context_log_routing(self):
        from tabs.context import DashboardContext
        logged = []
        self.mixin.context = DashboardContext(log_to_audit=lambda msg: logged.append(msg))
        self.mixin.context.log("Pipeline test audit event")
        self.assertEqual(logged, ["Pipeline test audit event"])

    def test_pipeline_export_routes_audit_to_context_log(self):
        from tabs.context import DashboardContext
        logged = []
        self.mixin.context = DashboardContext(log_to_audit=lambda msg: logged.append(msg))
        self.mixin._last_progress = MagicMock()
        self.mixin.pipeline_results_log = MagicMock()
        self.mixin.pipeline_results_log.toPlainText.return_value = "Sample log"
        self.mixin.pipeline_status_label = MagicMock()

        with patch("tabs.document_pipeline_tab.QFileDialog.getSaveFileName", return_value=("test_export.txt", "Text Files (*.txt)")), \
             patch("builtins.open", unittest.mock.mock_open()):
            self.mixin._pipeline_export()

        self.assertEqual(logged, ["💾 Results exported to test_export.txt"])


if __name__ == "__main__":
    unittest.main()
