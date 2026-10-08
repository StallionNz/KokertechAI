#!/usr/bin/env python3
"""Unit tests for ``assert_section11_render_clean.py`` pure-function finders.

Exercises the four public-callable symbols directly (no file IO):

    - ``find_pipe_violations``
    - ``find_blockquote_break_violations``
    - ``find_bare_backslash_violations``
    - ``_find_code_spans``

Each test passes fixture markdown slices as ``lines`` with ``sec_start=0`` so
that returned line numbers are 1-based within the fixture.  For
``sec_start != 0`` we assert that the offset is preserved.

Coverage matrix (per the user request):

    - pipe-count == 4 (clean) + 3 / 5 pipes (violations)
    - single-backtick code span (one BT pair)
    - double-backtick code span (two BT-pair)
    - triple-backtick code span (three BT-pair)
    - nested inner-BT-inside-outer (tier closure + outer closure)
    - mid-word BT closure + bare backslash after closed pair
    - bare ``\\X`` injection outside any fence
    - empty-line and offset behaviour
"""
# ``assert_section11_render_clean`` is importable because tests/conftest.py
# adds the ``scripts/`` directory to sys.path.  No local sys.path boilerplate.
import assert_section11_render_clean as S11


# ---------------------------------------------------------------------------
# _find_code_spans (CommonMark BT-run state machine)
# ---------------------------------------------------------------------------

class TestFindCodeSpans:
    """Lock in the BT-run state machine: a run of length N closes the
    most recent unclosed run of equal length.  Mismatched lengths push onto
    the stack and are silently dropped."""

    def test_no_bt_no_spans(self):
        assert S11._find_code_spans("plain text") == []

    def test_single_bt_pair_outer(self):
        # `` `foo` `` -> one (open, close) span covering positions 0..4.
        assert S11._find_code_spans("`foo`") == [(0, 4)]

    def test_double_bt_pair(self):
        # `` ``bar`` `` -> one larger span; the algorithm returns the
        # position where the CLOSING run BEGINS (not the position past
        # the last char).  Closing run starts at index 5.
        assert S11._find_code_spans("``bar``") == [(0, 5)]

    def test_triple_bt_pair(self):
        # `` ````baz```` `` -> closing run begins at index 6.
        assert S11._find_code_spans("```baz```") == [(0, 6)]

    def test_mid_word_close_then_unmatched_double_bt(self):
        # `` `foo`bar`` `` -> inner pair (0,4) closes; trailing 2-BT run is
        # unmatched (state machine left on the stack, dropped from spans).
        assert S11._find_code_spans("`foo`bar``") == [(0, 4)]




    def test_unmatched_single_bt_no_span(self):
        # `` `foo `` (no closing BT) -> no matched span.
        assert S11._find_code_spans("`foo") == []

    def test_unmatched_double_bt_no_span(self):
        # `` ``foo `` (no closing run of equal length) -> no matched span.
        assert S11._find_code_spans("``foo") == []

    def test_nested_inner_bt_in_outer_produces_two_spans(self):
        # `` `` `xyz` `` `` -> outer (0,9) AND inner (3,7) both close.
        # Stack-based algorithm pairs equal-length runs; inner-twin closes
        # first, then outer-twin closes against the now-top twin run.
        assert S11._find_code_spans("`` `xyz` ``") == [(3, 7), (0, 9)]

    def test_inner_bt_mismatched_length_does_not_close_outer(self):
        # `` `` `xyz  `` `` -> the inner 1-BT is unmatched, the outer 2-BT
        # pushes onto stack and is never closed (no 2-BT twin to pair with).
        assert S11._find_code_spans("`` `xyz  ``") == []

    def test_empty_string(self):
        assert S11._find_code_spans("") == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_find(finder, lines):
    """Call a finder with sec_start=0 and return only the violation list."""
    return finder(lines, 0)


# ---------------------------------------------------------------------------
# find_pipe_violations (pipe-count invariant)
# ---------------------------------------------------------------------------

class TestFindPipeViolations:
    """Every contiguous ``|...|`` row is treated as a table; rows whose
    pipe-count is not 4 (the SEC11 pitfall-table contract) are flagged."""

    def test_exactly_4_pipes_clean(self):
        # 4 pipes = 3 columns, matches the section-11 pitfall table contract.
        line = "| a | b | c |"
        assert run_find(S11.find_pipe_violations, [line]) == []

    def test_three_pipes_violated(self):
        line = "| a | b |"
        result = run_find(S11.find_pipe_violations, [line])
        assert result == [(1, 3, line)]

    def test_five_pipes_violated(self):
        line = "| a | b | c | d |"
        result = run_find(S11.find_pipe_violations, [line])
        assert result == [(1, 5, line)]

    def test_two_pipes_violated(self):
        line = "| a |"
        assert run_find(S11.find_pipe_violations, [line]) == [(1, 2, line)]

    def test_skips_non_pipe_lines(self):
        # Only lines whose lstrip() startswith "| """ count.
        lines = [
            "## 11. Common Pitfalls to Avoid",   # non-table
            "Some prose",                         # non-table
            "a |b |c",                             # mid-text pipe, not a row
            "| valid | row | here |",              # 4 pipes: clean
            "",                                   # empty line
            "|bad|row|",                          # 3 pipes: violation
        ]
        result = run_find(S11.find_pipe_violations, lines)
        assert len(result) == 1
        line_no, count, _ = result[0]
        assert line_no == 6
        assert count == 3

    def test_sec_start_offset_absolute(self):
        # When sec_start=10, returned line_no for the first violation is 11.
        line = "| a | b |"
        result = S11.find_pipe_violations([line], 10)
        assert result == [(11, 3, line)]

    def test_multiple_violations_all_reported(self):
        lines = [
            "| a | b |",        # 3 pipes -> viol (line 1)
            "| x | y |",        # 3 pipes -> viol (line 2)
            "| 1 | 2 | 3 |",    # 4 pipes -> clean
            "| a | b | c | d |",  # 5 pipes -> viol (line 4)
        ]
        result = run_find(S11.find_pipe_violations, lines)
        assert [r[0] for r in result] == [1, 2, 4]
        assert [r[1] for r in result] == [3, 3, 5]


# ---------------------------------------------------------------------------
# find_blockquote_break_violations (footnote-continuity invariant)
# ---------------------------------------------------------------------------

class TestFindBlockquoteBreakViolations:
    """Blank lines inside the section-11 footnote blockquote split it in a
    CommonMark viewer.  The function looks for the footnote header before
    scanning and returns [] if no footnote is present."""

    def test_no_footnote_header_returns_empty(self):
        # If slice has no ``> **Footnote`` line, function returns [] by design.
        slice_lines = [
            "## 11. Common Pitfalls to Avoid",
            "> A blockquote without footnote header",
            "",
            "> continues",
        ]
        assert run_find(S11.find_blockquote_break_violations, slice_lines) == []

    def test_clean_footnote_no_blank_lines(self):
        slice_lines = [
            "## 11. Common Pitfalls to Avoid",
            "> **Footnote** -- something about backslashes",
            "> Line one of footnote",
            "> Line two of footnote",
        ]
        assert run_find(S11.find_blockquote_break_violations, slice_lines) == []

    def test_blank_line_inside_footnote_flagged(self):
        slice_lines = [
            "> **Footnote** -- header",
            "> line one",
            "",                  # <-- blank line splits the blockquote
            "> line two",
        ]
        result = run_find(S11.find_blockquote_break_violations, slice_lines)
        assert len(result) == 1
        assert result[0][0] == 3     # 1-based line number

    def test_blank_line_outside_blockquote_not_flagged(self):
        # Surrounding non-blank lines are not BOTH blockquote-prefixed -> not flagged.
        slice_lines = [
            "> **Footnote** -- header",
            "> line one",
            "",                  # <- blank
            "non-blockquote line",  # <- after: not blockquote (next_q = False)
        ]
        assert run_find(S11.find_blockquote_break_violations, slice_lines) == []

    def test_blank_line_above_blockquote_no_footnote_no_flag(self):
        # A blank line at the very top, before any blockquote line, must
        # not be flagged because there is no blockquote line before it.
        slice_lines = [
            "",
            "> **Footnote** -- header",
            "> line one",
        ]
        assert run_find(S11.find_blockquote_break_violations, slice_lines) == []

    def test_multiple_blank_lines_inside_footnote_all_flagged(self):
        # Three blank lines interleaved with ``>`` content. ``prev_q`` is
        # True and ``next_q`` is True for lines 2 and 4 (each is bracketed by
        # ``>`` lines on both sides). Line 6 has NO successor non-blank line
        # in this slice, so ``next_q`` stays False and the algorithm
        # cannot confirm the blockquote continues -> line 6 is NOT flagged.
        # This is intentional conservative behaviour.
        slice_lines = [
            "> **Footnote** -- header",
            "",                  # flag (line 2: blockquoted both sides)
            "> between 1",
            "",                  # flag (line 4: blockquoted both sides)
            "> between 2",
            "",                  # NOT flagged (line 6: no successor in slice)
        ]
        result = run_find(S11.find_blockquote_break_violations, slice_lines)
        assert [r[0] for r in result] == [2, 4]


# ---------------------------------------------------------------------------
# find_bare_backslash_violations (backslash-in-backtick invariant)
# ---------------------------------------------------------------------------

class TestFindBareBackslashViolations:
    """Every ``\\letter`` token OUTSIDE a code span is unsafe (v0.18 fragile
    substring family).  The state machine correctly identifies single,
    double, and triple-backtick code spans, nested closures, and mid-word
    close-then-unmatched-trailing topologies; it emits no spans for
    unmatched runs (CommonMark behaviour).  Also locks the **40-char
    pre-match prologue window**: snippets are truncated to the rightmost
    40 chars before the match (``line[:pos][-40:]``)."""

    def test_bare_outside_any_fence_flagged(self):
        line = r"Some prose \X and more"
        result = run_find(S11.find_bare_backslash_violations, [line])
        assert len(result) == 1
        line_no, prologue, ctx = result[0]
        assert line_no == 1
        assert r"\X" in ctx
        assert "Some prose " in prologue

    def test_single_bt_wraps_letter_skipped(self):
        line = r"Some prose `\X` and more"
        assert run_find(S11.find_bare_backslash_violations, [line]) == []

    def test_double_bt_wraps_letter_skipped(self):
        line = r"Use ``\X`` carefully"
        assert run_find(S11.find_bare_backslash_violations, [line]) == []

    def test_triple_bt_wraps_letter_skipped(self):
        line = r"```\X```"
        assert run_find(S11.find_bare_backslash_violations, [line]) == []

    def test_double_bt_outer_wraps_inner_bt_skipped(self):
        # `` `` `\X` `` `` -> outer (0,9) AND inner (3,7) both close;
        # the ``\\X`` substring at positions 4-5 falls inside the inner span.
        line = r"`` `\X` ``"
        assert run_find(S11.find_bare_backslash_violations, [line]) == []

    def test_mid_word_close_then_bare_following_flagged(self):
        # `` `foo`b\Xar`` `` -> inner pair at (0,4) closes; the ``\\X``
        # substring at position 6 is OUTSIDE any closed span (trailing
        # 2-BT run is unmatched, dropped from spans list) -> flagged bare.
        line = r"`foo`b\Xar``"
        result = run_find(S11.find_bare_backslash_violations, [line])
        assert len(result) == 1
        line_no, prologue, ctx = result[0]
        assert line_no == 1
        assert r"\X" in ctx
        # ``line[:6][-40:]`` = `` `foo`b `` (the whole 6-char slice, which is
        # shorter than the 40-char window so no trimming).  Prologue is the
        # full ``pre-match slice trimmed to 40 chars``, NOT just the
        # character immediately before ``\X``.
        assert prologue == r"`foo`b"

    def test_prologue_truncates_at_40_chars(self):
        # Input with 45 chars before ``\X``: ``line[:pos][-40:]`` keeps
        # only the right-most 40 chars of the pre-match slice.  This locks
        # in the 40-character window cap from the OPPOSITE end of the
        # contract (the previous test pins the no-trim case).
        line = "x" * 45 + r"\X"
        result = run_find(S11.find_bare_backslash_violations, [line])
        assert len(result) == 1
        line_no, prologue, ctx = result[0]
        assert line_no == 1
        assert r"\X" in ctx
        assert prologue == "x" * 40   # exactly the 40-char window tail

    def test_empty_line_skipped(self):
        # An empty line produces no matches (skipped by .strip() check).
        assert run_find(S11.find_bare_backslash_violations, [""]) == []

    def test_multiple_offenders_in_single_line(self):
        # Both ``\\A`` and ``\\B`` are bare -> two offenders.
        line = r"x \A y \B z"
        result = run_find(S11.find_bare_backslash_violations, [line])
        assert len(result) == 2

    def test_mixed_clean_and_bare_same_line_only_bare_flagged(self):
        # ``\\K`` inside a single BT (clean). ``\\X`` is bare.
        line = r"clean `\K` then bare \X end"
        result = run_find(S11.find_bare_backslash_violations, [line])
        assert len(result) == 1
        assert r"\X" in result[0][2]

    def test_sec_start_offset_applied(self):
        # sec_start=5, violation on the first slice line -> line_no=6.
        line = r"prose \X tail"
        result = S11.find_bare_backslash_violations([line], 5)
        assert len(result) == 1
        assert result[0][0] == 6


# ---------------------------------------------------------------------------
# Integration: combined finders against a synthetic section-11 fixture
# ---------------------------------------------------------------------------

class TestIntegrationFixture:
    """One synthetic ``## 11.``-shaped fixture that exercises all three
    finders at once.  Each finder targets a disjoint line (6 / 8 / 10)
    so the assertions can lock in *which* line tripwires each invariant
    without interference from the other two finders."""

    FIXTURE = [
        "## 11. Common Pitfalls to Avoid",      # line 1
        "| pitfall | why | fix |",               # line 2: 4 pipes (clean)
        "|---|---|---|",                          # line 3: 4 pipes (clean)
        "| a | b | c |",                          # line 4: 4 pipes (clean)
        "| x | y | z |",                          # line 5: 4 pipes (clean)
        "| one | two |",                          # line 6: 3 pipes (VIOL)
        r"Use `\K` here",                         # line 7: BT-clean
        r"Then \X happens",                       # line 8: bare (VIOL)
        "> **Footnote** -- header",               # line 9: footnote header
        "",                                       # line 10: blank splits (VIOL)
        "> footnote body",                        # line 11
    ]

    def test_pipe_violation_only_on_line_6(self):
        result = S11.find_pipe_violations(self.FIXTURE, 0)
        assert [r[0] for r in result] == [6]

    def test_bare_backslash_violation_only_on_line_8(self):
        result = S11.find_bare_backslash_violations(self.FIXTURE, 0)
        assert [r[0] for r in result] == [8]

    def test_blockquote_break_violation_only_on_line_10(self):
        result = S11.find_blockquote_break_violations(self.FIXTURE, 0)
        assert [b[0] for b in result] == [10]
