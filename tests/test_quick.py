"""
test_quick.py — Quick unit tests for config.py utility functions.

Covers: _is_valid_json (validates file content), _write_save_marker,
check_last_save_integrity (returns integrity dict).
"""

import os
import json
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from config import _is_valid_json, _write_save_marker, check_last_save_integrity
from logging_config import get_logger

logger = get_logger(name="TestQuick")

class TestIsValidJson(unittest.TestCase):
    """Tests for _is_valid_json(filepath) — validates that a file exists and contains valid JSON."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.temp_dir, name)

    def test_valid_json_object(self):
        path = self._path("valid.json")
        with open(path, "w") as f:
            json.dump({"a": 1}, f)
        self.assertTrue(_is_valid_json(path))

    def test_valid_json_array(self):
        path = self._path("array.json")
        with open(path, "w") as f:
            json.dump([1, 2, 3], f)
        self.assertTrue(_is_valid_json(path))

    def test_valid_json_string(self):
        path = self._path("str.json")
        with open(path, "w") as f:
            json.dump("hello", f)
        self.assertTrue(_is_valid_json(path))

    def test_valid_json_number(self):
        path = self._path("num.json")
        with open(path, "w") as f:
            json.dump(42, f)
        self.assertTrue(_is_valid_json(path))

    def test_invalid_json_syntax(self):
        path = self._path("bad.json")
        with open(path, "w") as f:
            f.write("{a: 1}")
        self.assertFalse(_is_valid_json(path))

    def test_invalid_json_truncated(self):
        path = self._path("truncated.json")
        with open(path, "w") as f:
            f.write('{"a":')
        self.assertFalse(_is_valid_json(path))

    def test_empty_file(self):
        path = self._path("empty.json")
        with open(path, "w") as f:
            f.write("")
        self.assertFalse(_is_valid_json(path))

    def test_nonexistent_file(self):
        self.assertFalse(_is_valid_json("/nonexistent/path.json"))

    def test_none_path_raises_typeerror(self):
        with self.assertRaises(TypeError):
            _is_valid_json(None)

    def test_random_text_file(self):
        path = self._path("random.txt")
        with open(path, "w") as f:
            f.write("not json at all")
        self.assertFalse(_is_valid_json(path))

class TestWriteSaveMarker(unittest.TestCase):
    """Tests for _write_save_marker — writes a timestamp to SAVE_MARKER_PATH."""

    @patch("config.LOG_DIR", new_callable=lambda: None)
    @patch("config.SAVE_MARKER_PATH", new_callable=lambda: None)
    def test_writes_iso_timestamp(self, mock_marker, mock_logdir):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, temp_dir, ignore_errors=True)
        marker_path = os.path.join(temp_dir, ".save_complete")
        with patch("config.SAVE_MARKER_PATH", marker_path):
            with patch("config.LOG_DIR", temp_dir):
                _write_save_marker()
                self.assertTrue(os.path.exists(marker_path))
                with open(marker_path, "r") as f:
                    content = f.read().strip()
                # Should be an ISO timestamp string, not JSON
                self.assertRegex(content, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    @patch("config.SAVE_MARKER_PATH", "/nonexistent/dir/.save_complete")
    def test_handles_permission_error_gracefully(self):
        # Should not raise despite invalid path
        _write_save_marker()

class TestCheckLastSaveIntegrity(unittest.TestCase):
    """Tests for check_last_save_integrity — returns a dict with validation results."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = os.path.join(self.temp_dir, "settings.json")
        self.identity_path = os.path.join(self.temp_dir, "identity.json")
        self.marker_path = os.path.join(self.temp_dir, ".save_complete")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _patch_paths(self):
        return patch.multiple(
            "config",
            SETTINGS_PATH=self.settings_path,
            IDENTITY_PATH=self.identity_path,
            SAVE_MARKER_PATH=self.marker_path,
        )

    def test_returns_dict(self):
        with self._patch_paths():
            result = check_last_save_integrity()
        self.assertIsInstance(result, dict)

    def test_no_files_returns_ok_true(self):
        """Fresh install — no files at all — ok should be True."""
        with self._patch_paths():
            result = check_last_save_integrity()
        self.assertTrue(result["ok"])
        self.assertTrue(result["settings_valid"])
        self.assertTrue(result["identity_valid"])
        self.assertFalse(result["marker_exists"])

    def test_valid_settings_and_identity(self):
        with self._patch_paths():
            with open(self.settings_path, "w") as f:
                json.dump({"key": "val"}, f)
            with open(self.identity_path, "w") as f:
                json.dump({"name": "test"}, f)
            result = check_last_save_integrity()
        self.assertTrue(result["ok"])
        self.assertTrue(result["settings_valid"])
        self.assertTrue(result["identity_valid"])

    def test_corrupt_settings_returns_ok_false(self):
        with self._patch_paths():
            with open(self.settings_path, "w") as f:
                f.write("corrupt json{{{")
            with open(self.identity_path, "w") as f:
                json.dump({"name": "test"}, f)
            result = check_last_save_integrity()
        self.assertFalse(result["ok"])
        self.assertFalse(result["settings_valid"])
        self.assertTrue(result["identity_valid"])

    def test_corrupt_identity_returns_ok_false(self):
        with self._patch_paths():
            with open(self.settings_path, "w") as f:
                json.dump({"key": "val"}, f)
            with open(self.identity_path, "w") as f:
                f.write("not json")
            result = check_last_save_integrity()
        self.assertFalse(result["ok"])
        self.assertTrue(result["settings_valid"])
        self.assertFalse(result["identity_valid"])

    def test_marker_exists_when_file_present(self):
        # Manually patch SAVE_MARKER_PATH to a tmp path and verify marker detection
        with patch("config.SAVE_MARKER_PATH", self.marker_path):
            with open(self.marker_path, "w") as f:
                f.write("2026-01-01T00:00:00")
            result = check_last_save_integrity()
        self.assertTrue(result["marker_exists"])

    def test_marker_not_exists_when_absent(self):
        with self._patch_paths():
            result = check_last_save_integrity()
        self.assertFalse(result["marker_exists"])

    def test_backups_info_in_result(self):
        with self._patch_paths():
            result = check_last_save_integrity()
        self.assertIn("backups_available", result)
        self.assertIn("latest_backup_ts", result)
        self.assertIn("latest_backup_label", result)

    def test_returns_integrity_structure(self):
        with self._patch_paths():
            result = check_last_save_integrity()
        expected_keys = {
            "ok", "settings_valid", "identity_valid", "marker_exists",
            "settings_path", "identity_path", "backups_available",
            "latest_backup_ts", "latest_backup_label",
        }
        self.assertEqual(set(result.keys()), expected_keys)

if __name__ == "__main__":
    unittest.main()
