"""Unit tests for doc_pipeline.py (Sprint 4 Stream F)."""
import unittest
import sys
import os
import tempfile
from unittest.mock import MagicMock, patch


def _make_tmp_file(suffix=".txt", content="Hello world"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, content.encode("utf-8"))
    os.close(fd)
    return path

class TestClassifyFile(unittest.TestCase):

    def test_pdf(self):
        from doc_pipeline import _classify_file
        self.assertEqual(_classify_file("/x/doc.pdf"), "pdf")

    def test_png_image(self):
        from doc_pipeline import _classify_file
        self.assertEqual(_classify_file("/x/img.png"), "image")

    def test_txt_text(self):
        from doc_pipeline import _classify_file
        self.assertEqual(_classify_file("/x/readme.txt"), "text")

    def test_md_text(self):
        from doc_pipeline import _classify_file
        self.assertEqual(_classify_file("/x/readme.md"), "text")

    def test_exe_skipped(self):
        from doc_pipeline import _classify_file
        self.assertEqual(_classify_file("/x/app.exe"), "skip")

    def test_db_skipped(self):
        from doc_pipeline import _classify_file
        self.assertEqual(_classify_file("/x/vault.db"), "skip")

    def test_unknown_skipped(self):
        from doc_pipeline import _classify_file
        self.assertEqual(_classify_file("/x/file.xyz"), "skip")

    def test_case_insensitive(self):
        from doc_pipeline import _classify_file
        self.assertEqual(_classify_file("/x/DOC.PDF"), "pdf")

class TestExtractText(unittest.TestCase):

    def setUp(self):
        self.tmp = _make_tmp_file(".txt", "Alpha beta gamma")

    def tearDown(self):
        try:
            os.unlink(self.tmp)
        except OSError:
            pass

    def test_extracts_text_file(self):
        from doc_pipeline import _extract_text
        text = _extract_text(self.tmp, "text")
        self.assertEqual(text, "Alpha beta gamma")

    def test_missing_file_returns_empty(self):
        from doc_pipeline import _extract_text
        text = _extract_text("/nonexistent/file.txt", "text")
        self.assertEqual(text, "")

    def test_unknown_type_returns_empty(self):
        from doc_pipeline import _extract_text
        text = _extract_text(self.tmp, "unknown_type")
        self.assertEqual(text, "")

class TestProcessFile(unittest.TestCase):

    def setUp(self):
        self.tmp = _make_tmp_file(".txt", "Some content here")

    def tearDown(self):
        try:
            os.unlink(self.tmp)
        except OSError:
            pass

    def test_text_file_success(self):
        from doc_pipeline import process_file
        result = process_file(self.tmp)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.file_type, "text")
        self.assertEqual(result.extracted_text, "Some content here")
        self.assertEqual(result.node_ids, [])

    def test_skipped_file(self):
        from doc_pipeline import process_file
        result = process_file("/x/app.exe")
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.file_type, "skip")

    def test_empty_file_is_error(self):
        tmp = _make_tmp_file(".txt", "   ")
        try:
            from doc_pipeline import process_file
            result = process_file(tmp)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.error, "No extractable text")
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

class TestProcessBatch(unittest.TestCase):

    def setUp(self):
        self.files = []
        for i, content in enumerate(["Alpha", "Beta", "Gamma"]):
            p = _make_tmp_file(".txt", content)
            self.files.append(p)

    def tearDown(self):
        for p in self.files:
            try:
                os.unlink(p)
            except OSError:
                pass

    def test_processes_all_files(self):
        from doc_pipeline import process_batch
        result = process_batch(self.files, index_to_vault=False)
        self.assertEqual(result.total, 3)
        self.assertEqual(result.completed, 3)
        self.assertEqual(result.percent, 100)
        self.assertEqual(
            sum(1 for r in result.results if r.status == "success"), 3)

    def test_progress_callback_called(self):
        from doc_pipeline import process_batch
        cb = MagicMock()
        process_batch(self.files, progress_callback=cb, index_to_vault=False)
        self.assertTrue(cb.call_count >= 3)

    def test_index_to_vault(self):
        from doc_pipeline import process_batch
        with patch("memory_vault.store_memory", return_value=42) as mock_store:
            result = process_batch(self.files, index_to_vault=True)
            self.assertEqual(result.total, 3)
            self.assertGreaterEqual(mock_store.call_count, 3)

    def test_indexing_failure_sets_error(self):
        from doc_pipeline import process_batch
        with patch("memory_vault.store_memory", side_effect=RuntimeError("vault down")):
            result = process_batch(self.files[:1], index_to_vault=True)
            self.assertEqual(result.results[0].status, "error")
            self.assertIn("vault down", result.results[0].error)

class TestProcessBatchAsync(unittest.TestCase):

    def test_returns_thread(self):
        import threading
        from doc_pipeline import process_batch_async
        t = process_batch_async([])
        self.assertIsInstance(t, threading.Thread)
        self.assertTrue(t.daemon)

    def test_done_callback_called(self):
        from doc_pipeline import process_batch_async
        done = MagicMock()
        t = process_batch_async([], done_callback=done)
        t.join(timeout=5)
        done.assert_called_once()

class TestPipelineProgress(unittest.TestCase):

    def test_percent_zero_total(self):
        from doc_pipeline import PipelineProgress
        pp = PipelineProgress(total=0)
        self.assertEqual(pp.percent, 0)

    def test_percent_half(self):
        from doc_pipeline import PipelineProgress
        pp = PipelineProgress(total=4, completed=2)
        self.assertEqual(pp.percent, 50)

    def test_percent_complete(self):
        from doc_pipeline import PipelineProgress
        pp = PipelineProgress(total=1, completed=1)
        self.assertEqual(pp.percent, 100)

class TestPipelineResult(unittest.TestCase):

    def test_defaults(self):
        from doc_pipeline import PipelineResult
        r = PipelineResult(
            filepath="/tmp/test.txt", status="skipped", file_type="unknown")
        self.assertEqual(r.status, "skipped")
        self.assertEqual(r.file_type, "unknown")
        self.assertEqual(r.extracted_text, "")
        self.assertEqual(r.node_ids, [])
        self.assertEqual(r.error, "")

if __name__ == "__main__":
    unittest.main()
