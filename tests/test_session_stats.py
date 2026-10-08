"""Tests for session_stats.py - session log aggregation and badge generation."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from session_stats import (
    _badge_svg, _default_sessions_dir, _generate_badges,
    _parse_duration_hours, _resolve_root, aggregate,
    count_files, find_files, parse_yaml,
)


class TestResolveRoot(unittest.TestCase):
    def test_resolve_root_returns_string(self):
        root = _resolve_root()
        self.assertIsInstance(root, str)
        self.assertTrue(os.path.isabs(root))
    def test_resolve_root_contains_project(self):
        root = _resolve_root()
        self.assertIn("KokertechAI", root)

class TestDefaultSessionsDir(unittest.TestCase):
    def test_default_sessions_dir_ends_with_data_sessions(self):
        d = _default_sessions_dir()
        self.assertTrue(d.replace("\\", "/").endswith("data/sessions"))

class TestFindFiles(unittest.TestCase):
    def test_find_files_nonexistent_dir_returns_empty(self):
        result = find_files(sessions_dir="/nonexistent/path/xyz")
        self.assertEqual(result, [])
    @patch("session_stats.os.path.isdir", return_value=True)
    @patch("session_stats.glob.glob", side_effect=[["/tmp/s1.yaml", "/tmp/s2.yaml"], []])
    def test_find_files_returns_sorted(self, mock_glob, mock_isdir):
        result = find_files(sessions_dir="/tmp")
        self.assertEqual(len(result), 2)

class TestParseYaml(unittest.TestCase):
    def test_parse_yaml_nonexistent_file(self):
        result = parse_yaml("/nonexistent/file.yaml")
        self.assertIsNone(result)
    def test_parse_yaml_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("not valid json\n{broken")
            fpath = f.name
        try:
            result = parse_yaml(fpath)
            self.assertIsNone(result)
        finally:
            os.unlink(fpath)
    def test_parse_yaml_valid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            json.dump({"session": "test", "duration": "2h"}, f)
            fpath = f.name
        try:
            result = parse_yaml(fpath)
            self.assertEqual(result["session"], "test")
        finally:
            os.unlink(fpath)
    def test_parse_yaml_non_dict_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            json.dump(["list", "not", "dict"], f)
            fpath = f.name
        try:
            result = parse_yaml(fpath)
            self.assertIsNone(result)
        finally:
            os.unlink(fpath)

class TestParseDurationHours(unittest.TestCase):
    def test_none_returns_zero(self):
        self.assertEqual(_parse_duration_hours(None), 0.0)
    def test_int_returns_float(self):
        self.assertEqual(_parse_duration_hours(3), 3.0)
    def test_hours_only(self):
        self.assertEqual(_parse_duration_hours("2h"), 2.0)
    def test_minutes_only(self):
        self.assertEqual(_parse_duration_hours("90m"), 1.5)
    def test_hours_and_minutes(self):
        self.assertEqual(_parse_duration_hours("1h30m"), 1.5)
    def test_invalid_string_returns_zero(self):
        self.assertEqual(_parse_duration_hours("not-a-duration"), 0.0)

class TestCountFiles(unittest.TestCase):
    def test_empty_session(self):
        result = count_files({})
        self.assertEqual(result, (0, 0, [], []))
    def test_files_created_key(self):
        result = count_files({"files_created": ["a.py"]})
        self.assertEqual(result[0], 1)
    def test_files_modified_key(self):
        result = count_files({"files_modified": ["c.py"]})
        self.assertEqual(result[1], 1)
    def test_plus_prefix_in_files_list(self):
        result = count_files({"files": ["+ new.py", "~ mod.py"]})
        self.assertEqual(result[0], 1)
        self.assertEqual(result[1], 1)

class TestAggregate(unittest.TestCase):
    def test_empty_list(self):
        result = aggregate([])
        self.assertEqual(result["sessions"], 0)
        self.assertEqual(result["hours"], 0.0)
    def test_single_session(self):
        result = aggregate([{"session": "S1", "date": "2026-07-01", "duration": "2h", "focus": "Work", "files_created": ["fix.py"]}])
        self.assertEqual(result["sessions"], 1)
        self.assertEqual(result["hours"], 2.0)
        self.assertEqual(result["created"], 1)
    def test_date_range(self):
        result = aggregate([{"date": "2026-07-01", "duration": "1h"}, {"date": "2026-07-03", "duration": "1h"}])
        self.assertEqual(result["date_range"][0], "2026-07-01")
        self.assertEqual(result["date_range"][1], "2026-07-03")
    def test_date_range_no_dates(self):
        result = aggregate([{"duration": "1h"}])
        self.assertEqual(result["date_range"], ("N/A", "N/A"))
    def test_sprint_hours(self):
        result = aggregate([{"duration": "2h", "sprint": 1}, {"duration": "3h", "sprint": 1}])
        self.assertEqual(result["sprint_hours"][1], 5.0)
    def test_tag_frequency(self):
        result = aggregate([{"tags": ["bugfix", "urgent"], "duration": "1h"}, {"tags": ["bugfix"], "duration": "1h"}])
        self.assertEqual(result["tag_frequency"]["bugfix"], 2)
    def test_non_dict_entries_skipped(self):
        result = aggregate([{"duration": "1h"}, "not a dict", {"duration": "2h"}])
        self.assertEqual(result["sessions"], 2)

class TestBadgeSvg(unittest.TestCase):
    def test_badge_contains_svg_tag(self):
        svg = _badge_svg("Test", "42")
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)
    def test_badge_contains_label_and_value(self):
        svg = _badge_svg("Sessions", "10")
        self.assertIn("Sessions", svg)
        self.assertIn("10", svg)

class TestGenerateBadges(unittest.TestCase):
    def test_generate_badges_returns_list_of_tuples(self):
        stats = {"sessions": 1, "hours": 1.0, "created": 0, "modified": 0, "changed": 0, "sprint_hours": {}, "tag_frequency": {}}
        with tempfile.TemporaryDirectory() as tmpdir:
            results = _generate_badges(stats, out_dir=tmpdir)
            self.assertGreater(len(results), 0)
    def test_generate_badges_has_expected_default_badges(self):
        stats = {"sessions": 1, "hours": 1.0, "created": 0, "modified": 0, "changed": 0, "sprint_hours": {}, "tag_frequency": {}}
        expected = ["badge_sessions.svg", "badge_hours.svg", "badge_created.svg", "badge_modified.svg", "badge_changed.svg", "badge_sprints.svg"]
        with tempfile.TemporaryDirectory() as tmpdir:
            results = _generate_badges(stats, out_dir=tmpdir)
            filenames = [r[0] for r in results]
            for name in expected:
                self.assertIn(name, filenames)
    def test_generate_badges_includes_top_tag(self):
        stats = {"sessions": 1, "hours": 1.0, "created": 0, "modified": 0, "changed": 0, "sprint_hours": {}, "tag_frequency": {"bugfix": 5}}
        with tempfile.TemporaryDirectory() as tmpdir:
            results = _generate_badges(stats, out_dir=tmpdir)
            filenames = [r[0] for r in results]
            self.assertIn("badge_top_tag.svg", filenames)

if __name__ == "__main__":
    unittest.main()
