"""test_assert_known_anchors.py - Unit tests for scripts/assert_known_anchors.py.

Covers pure-function regex logic of the core finders via the new
``text=`` parameter, which lets tests inject markdown content directly
without requiring KNOWLEDGE.md on disk. CI behavior (FileNotFoundError
when the file is missing) is preserved; only the test path is exercised
here.
"""
import unittest
from unittest.mock import patch

from assert_known_anchors import (  # noqa: E402
    KNOWLEDGE_MD,
    find_anchor_references,
    find_drift_anchors,
    find_wide_table_cells,
)


# ``assert_known_anchors`` is importable because tests/conftest.py adds
# the ``scripts/`` directory to sys.path.  No local sys.path boilerplate here.


class TestFindDriftAnchors(unittest.TestCase):
    """``(line N)``, ``(lines N-M)``, and ``<module>.py:N`` are stale."""

    def test_detects_parenthesized_line_citation(self):
        text = "See (line 42) for details."
        out = find_drift_anchors(text=text)
        self.assertEqual(len(out), 1)
        _, desc, snippet = out[0]
        self.assertIn("parenthesized (line N)", desc)
        self.assertEqual(snippet, "(line 42)")

    def test_detects_parenthesized_lines_range_citation(self):
        text = "Per (lines 10-25) above."
        out = find_drift_anchors(text=text)
        snippets = [s for _, _, s in out]
        self.assertIn("(lines 10-25)", snippets)

    def test_backticked_module_py_cite_is_stripped(self):
        # The prose stripper in _load_prose_lines removes inline backticks AND
        # their content via ``re.sub(r"`[^`]+`", "", line)``.  A backticked
        # `` `config.py:99` `` therefore disappears from the prose before the
        # patterns run -- so a backticked cite is NOT flagged.  Only BARE
        # cites (not in backticks) are detected by the surviving pattern #4.
        # Pattern #3 (`` `[a-zA-Z]...` ``) is effectively dead code given
        # the stripper's behavior; this test locks in the contract so any
        # future change to the stripper is caught explicitly.
        text = "See `config.py:99` for context."
        out = find_drift_anchors(text=text)
        self.assertEqual(out, [])

    def test_detects_bare_module_py_cite(self):
        text = "Look at config.py:99 next."
        out = find_drift_anchors(text=text)
        self.assertEqual(len(out), 1)
        _, desc, _snippet = out[0]
        self.assertIn("bare", desc)

    def test_drift_immune_anchor_not_flagged(self):
        text = "See `config.load_settings` and `app_core.KokertechDashboard.__init__`."
        out = find_drift_anchors(text=text)
        self.assertEqual(out, [])

    def test_code_block_contents_are_ignored(self):
        text = (
            "Prose line.\n"
            "```python\n"
            "# (line 99) and config.py:99 are examples.\n"
            "```\n"
            "End prose line.\n"
        )
        out = find_drift_anchors(text=text)
        self.assertEqual(
            out, [], "citations inside fenced code blocks must not be flagged"
        )


class TestFindWideTableCells(unittest.TestCase):
    """Cells over WIDE_CELL_THRESHOLD are flagged; header/separator rows are not."""

    def test_flags_cell_over_threshold(self):
        long_cell = "a" * 260
        text = (
            "| H1 | H2 | H3 |\n"
            "| --- | --- | --- |\n"
            f"| a | {long_cell} | c |\n"
        )
        out = find_wide_table_cells(text=text)
        self.assertEqual(len(out), 1)
        line, cell_index, length, snippet = out[0]
        self.assertEqual(line, 3)
        self.assertEqual(cell_index, 2)
        self.assertEqual(length, 260)
        self.assertEqual(snippet, "a" * 120)

    def test_returns_empty_when_all_cells_short(self):
        text = (
            "| H1 | H2 | H3 |\n"
            "| --- | --- | --- |\n"
            "| short | also short | tiny |\n"
        )
        out = find_wide_table_cells(text=text)
        self.assertEqual(out, [])

    def test_separator_row_is_ignored(self):
        text = (
            "| H1 | H2 | H3 |\n"
            "| --- | " + ("-" * 260) + " | --- |\n"
            "| a | b | c |\n"
        )
        out = find_wide_table_cells(text=text)
        self.assertEqual(out, [])

    def test_short_header_with_one_long_data_cell(self):
        text = (
            "| A | B | C |\n"
            "| --- | --- | --- |\n"
            f"| a | {('b' * 260)} | c |\n"
        )
        out = find_wide_table_cells(text=text)
        self.assertEqual(len(out), 1)


class TestFindAnchorReferences(unittest.TestCase):
    """Drift-immune ``module.class.method`` anchors are extracted, deduplicated."""

    def test_extracts_unique_backticked_anchors(self):
        # ALLOWED_ANCHOR is `` `[a-zA-Z]...` `` so backticks are PART of the
        # matched span.  Tests must account for that.
        text = (
            "See `config.load_settings` and `app_core.KokertechDashboard`.\n"
            "Also `config.load_settings` (repeated, must dedupe).\n"
        )
        out = find_anchor_references(text=text)
        self.assertEqual(
            out,
            [
                "`app_core.KokertechDashboard`",
                "`config.load_settings`",
            ],
        )

    def test_returns_empty_when_no_anchors(self):
        text = "Just prose with no backticked module references."
        out = find_anchor_references(text=text)
        self.assertEqual(out, [])


class TestEmptyAndCliContract(unittest.TestCase):
    """Empty input is graceful; no-arg call fails loudly when KNOWLEDGE.md is absent."""

    def test_empty_text_returns_empty_for_drift_anchors(self):
        out = find_drift_anchors(text="")
        self.assertEqual(out, [])

    def test_empty_text_returns_empty_for_anchor_references(self):
        out = find_anchor_references(text="")
        self.assertEqual(out, [])

    def test_empty_text_returns_empty_for_wide_table_cells(self):
        out = find_wide_table_cells(text="")
        self.assertEqual(out, [])

    def test_no_arg_call_raises_when_knowledge_md_missing(self):
        # Patch KNOWLEDGE_MD to a path that is GUARANTEED not to exist so the
        # contract is explicit and not coupled to what happens to be on disk.
        # No-arg invocations of any file-reading finder must raise
        # FileNotFoundError so the CLI surfaces a clear exit-1.
        fake_path = type(KNOWLEDGE_MD)(r"C:\nonexistent\path\KNOWLEDGE.md")
        self.assertFalse(fake_path.exists())
        with patch("assert_known_anchors.KNOWLEDGE_MD", fake_path):
            with self.assertRaises(FileNotFoundError):
                find_drift_anchors()


if __name__ == "__main__":
    unittest.main()
