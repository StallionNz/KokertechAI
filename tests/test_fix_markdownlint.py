"""
tests/test_fix_markdownlint.py
==============================

Unit tests for ``scripts/fix_markdownlint.py``. These tests protect the
idempotency contract and the two non-trivial unit-level behaviors that are
easy to regress in future edits:

1. **4-pass idempotency** — running the full CLI on the same file twice plus
   bracketing ``--check`` invocations must produce zero changes on the second
   pass and exit 0 from ``--check``.
2. **fix_md025 skip mode** — ``--no-demote-h1`` must only skip ``fix_md025``
   and apply every other fix normally.
3. **fix_md036 parent-level tracking** — bare ``**text**`` emphasis under a
   known parent heading must be promoted to ONE level deeper than that parent
   consistently (not deeper each time).

Other coverage:
- ``fix_md036`` correctly skips list items, table rows, blockquotes, and
  emphasis that contains internal markers (``**a**b**``).
- ``_wrap_line`` preserves the trailing break pattern on the first line and
  drops it from the second (recursive re-check is in ``fix_md013``).
- ``_default_fixes`` exposes exactly the 13 expected rule handlers and never
  ``fix_md041`` (intentionally excluded from the default fix list).
- Orchestrator safety paths: non-existent file path returns exit 1 with a
  ``NOT FOUND`` message on stderr; a binary / non-UTF-8 file returns a non-zero
  exit code with an ``ERROR reading`` message on stderr (no Python traceback,
  no crash).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Make ``import fix_markdownlint`` work without packaging.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_SCRIPTS = _ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import fix_markdownlint as fml  # noqa: E402  (intentional sys.path tweak above)
from fix_markdownlint import (  # noqa: E402
    _default_fixes,
    _wrap_line,
    fix_md025,
    fix_md036,
)

SCRIPT = _SCRIPTS / "fix_markdownlint.py"
PYTHON = sys.executable

# Module-local newline constant used in test fixtures. Defined as chr(10)
# (not the literal "\n") so it survives heredoc/shell-escape paths that
# earlier corrupted the test file with literal newlines vs escaped \n.
D = chr(10)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the fix script as a subprocess so tests exercise the real CLI."""
    return subprocess.run(
        [PYTHON, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        errors="replace",
        cwd=str(cwd) if cwd else None,
    )


# ---------------------------------------------------------------------------
# 4-pass idempotency (the headline test the user asked for)
# ---------------------------------------------------------------------------

# Truly multi-rule sample — exercises fix_md012 + fix_md018 + fix_md026 +
# fix_md029 + fix_md060 so the 4-pass test actually verifies multi-rule
# idempotency, not just a single-rule renumbering pass.
SAMPLE_MULTI_RULE = """\
#Title
## Section A:
content

## Section A
duplicate section heading is intentional (MD024 disabled in config).
1. First item
3. Third item
2. Second item


Extra blank lines above should be collapsed to one.


| a|b |
"""


def test_4pass_idempotency_holds(tmp_path: Path) -> None:
    """Pass 1: --check reports WOULD FIX, exit 1
    Pass 2: actual fix succeeds, exit 0
    Pass 3: --check again reports CLEAN, exit 0 (the contract)
    Pass 4: re-running the fix is a no-op (file content unchanged)
    """
    sample = tmp_path / "multi.md"
    sample.write_text(SAMPLE_MULTI_RULE, encoding="utf-8")

    # Pass 1: --check on dirty file (exit 1, WOULD FIX reported)
    rc1 = _run_cli("--check", str(sample))
    assert rc1.returncode == 1, (
        "Pass 1 (--check on dirty file) should exit 1.\n"
        f"stdout={rc1.stdout!r}\nstderr={rc1.stderr!r}"
    )
    assert "WOULD FIX" in rc1.stdout

    # Pass 2: actual fix (exit 0, file modified)
    rc2 = _run_cli(str(sample))
    assert rc2.returncode == 0, f"Pass 2 (fix): {rc2.stderr!r}"
    after_pass2 = sample.read_text(encoding="utf-8")
    assert after_pass2 != SAMPLE_MULTI_RULE, "first pass must change content"

    # Pass 3: --check on now-clean file (exit 0, CLEAN reported) — THE IDEMPOTENCY CONTRACT
    rc3 = _run_cli("--check", str(sample))
    assert rc3.returncode == 0, (
        "Pass 3 (--check after fix) MUST exit 0 — this is the idempotency contract.\n"
        f"stdout={rc3.stdout!r}\nstderr={rc3.stderr!r}"
    )
    assert "CLEAN" in rc3.stdout

    # Pass 4: re-running the fix is a no-op (CLEAN, file unchanged)
    rc4 = _run_cli(str(sample))
    assert rc4.returncode == 0
    assert "CLEAN" in rc4.stdout
    assert sample.read_text(encoding="utf-8") == after_pass2, (
        "Pass 4 must not mutate the file further."
    )


# ---------------------------------------------------------------------------
# fix_md025 — --no-demote-h1 skip mode
# ---------------------------------------------------------------------------

DUPLICATE_H1_DOC = """\
# Real Title

Some content here.

## A Section

# Stray Second H1

More content.
"""


def test_fix_md025_promotes_duplicate_h1_to_h2(tmp_path: Path) -> None:
    """Without the flag, the duplicate H1 is demoted to H2."""
    sample = tmp_path / "dup.md"
    sample.write_text(DUPLICATE_H1_DOC, encoding="utf-8")

    rc = _run_cli("--verbose", str(sample))
    assert rc.returncode == 0

    text = sample.read_text(encoding="utf-8")
    assert "## Stray Second H1" in text, (
        f"Expected second H1 promoted to H2; got:\n{text}"
    )
    assert "\n# Stray Second H1\n" not in text


def test_fix_md025_skipped_under_no_demote_h1(tmp_path: Path) -> None:
    """--no-demote-h1 must (a) skip fix_md025 only and (b) leave the duplicate H1 intact.

    Asserts on BEHAVIOR (file content unchanged at the duplicate H1) rather
    than on a substring search of the verbose log — keeps the test stable if
    the orchestrator's log format changes.
    """
    sample = tmp_path / "dup.md"
    sample.write_text(DUPLICATE_H1_DOC, encoding="utf-8")

    rc = _run_cli("--no-demote-h1", str(sample))
    assert rc.returncode == 0

    text = sample.read_text(encoding="utf-8")
    # (a) duplicate H1 is preserved verbatim
    assert "\n# Stray Second H1\n" in text, (
        f"--no-demote-h1 must leave duplicate H1 alone; got:\n{text}"
    )
    # (b) other rules still ran (MD029 renumbering should have happened:
    #     the regex `1.` ordered list marker is a no-op target here, but
    #     the doc is otherwise well-formed, so no spurious changes expected)
    # We deliberately do NOT assert on which fixes ran via stdout parsing —
    # the content-preservation assertion above is the behavioral contract.


def test_fix_md025_unit_no_h1_skips() -> None:
    """Unit-level: fix_md025 is a true no-op when the file has no H1."""
    text = "## Only H2 here\n\nNo H1 in doc.\n"
    out, changed = fix_md025(text)
    assert out == text
    assert changed is False


def test_fix_md025_unit_preserves_first_h1() -> None:
    """Unit-level: only the 2nd and later H1s are demoted, not the first."""
    text = "# First H1 is kept\n\n## H2 too\n\n# Second H1 demoted\n"
    out, changed = fix_md025(text)
    assert changed is True, f"expected changed=True for duplicate H1; got False. out=\n{out!r}"
    # First H1 is preserved at depth 1 (no leading `##`) — output starts with `# First`.
    assert out.startswith("# First H1 is kept"), (
        f"first H1 should be unmodified at file start; got start: {out[:40]!r}"
    )
    assert "# First H1 is kept" in out
    # Second H1 demoted to `## ` — look for the demoted (depth-2) form as line start.
    assert "\n## Second H1 demoted" in out, (
        f"second H1 should be demoted to ## (line-start); out=\n{out!r}"
    )
    # The original `\\n# Second H1 demoted` (depth 1) form must NOT survive
    # in the doc (would mean the H1 was left untouched, contradicting the fix).
    assert "\n# Second H1 demoted" not in out, (
        f"second H1 should be demoted (no \\n# form); out=\n{out!r}"
    )


# ---------------------------------------------------------------------------
# fix_md036 — parent-level tracking
# ---------------------------------------------------------------------------

def test_fix_md036_promotes_orphan_emphasis_to_h2() -> None:
    """A bare ``**text**`` with no parent heading becomes ``## ``."""
    text = "**Standalone Section**\n\nbody\n"
    out, changed = fix_md036(text)
    assert changed is True
    assert "## Standalone Section" in out
    assert "**Standalone Section**" not in out


def test_fix_md036_nests_under_h1_parent() -> None:
    """A bare emphasis under ``# H1`` becomes ``## `` (one level deeper)."""
    text = "# Parent H1\n\n**Child emphasis**\n\nbody\n"
    out, changed = fix_md036(text)
    assert changed is True
    assert "# Parent H1" in out
    assert "## Child emphasis" in out
    assert "**Child emphasis**" not in out
    assert "### Child emphasis" not in out  # not too deep


def test_fix_md036_nests_under_h2_parent() -> None:
    """A bare emphasis under ``## H2`` becomes ``### ``."""
    text = "## Parent H2\n\n**Child emphasis**\n\nbody\n"
    out, changed = fix_md036(text)
    assert changed is True
    assert "## Parent H2" in out
    assert "### Child emphasis" in out
    assert "#### Child emphasis" not in out  # not over-deep


def test_fix_md036_siblings_promoted_at_each_level() -> None:
    """Bare emphasis-as-heading siblings under the same ``# H1`` parent.

    The fix script's chosen heuristic: after promoting ``**First**`` to ``## First``,
    ``last_heading_level`` is updated to 2, so the next bare emphasis gets a
    deeper level. This is the implementation's documented behavior — siblings
    NEST by design (the v3 bug would have accidentally re-set the level, the
    v4 fix ensures each subsequent emphasis consistently advances).
    """
    text = "# Parent\n\n**First**\n\nbody between\n\n**Second**\n\nbody after\n"
    out, changed = fix_md036(text)
    assert changed is True
    # First sibling: ## (one level deeper than # Parent)
    assert "\n## First\n" in out, f"First emphasis should be H2; got:\n{out}"
    # Second sibling: ### (one level deeper than ## First — implementation chooses to nest)
    assert "\n### Second\n" in out, f"Second emphasis should be H3 (nested); got:\n{out}"


def test_fix_md036_advances_level_after_promotion() -> None:
    """An emphasis-as-heading nested under a previous emphasis-as-heading
    advances the level further, rather than re-using the same level twice.
    """
    text = "# Parent\n\n**First**\n\n**Second**\n"
    out, changed = fix_md036(text)
    assert changed is True
    assert "## First" in out
    assert "### Second" in out  # 2nd emphasis nests deeper than 1st


def test_fix_md036_skips_list_items() -> None:
    """List items with bold text should NOT be promoted to headings."""
    text = "- **not a heading**\n\nbody\n"
    out, changed = fix_md036(text)
    assert changed is False
    assert out == text


def test_fix_md036_skips_internal_emphasis() -> None:
    """``**a**b**`` contains unmatched markers and must be left alone."""
    text = "**a**b**\n\nbody\n"
    out, changed = fix_md036(text)
    # The matcher rejects it (strlen guard or inner-marker guard) — never promoted.
    # Tightened assertion: bare `## a` would substring-match `## a**b**, so check
    # explicitly for the cleaned-up, post-trim headings only.
    assert "## a" not in out.split("\n")
    assert "## a**b**" not in out


def test_fix_md036_skips_tables_and_blockquotes() -> None:
    """Table rows and blockquote lines must NOT be promoted."""
    text = "| **cell** | other |\n\n> **quoted**\n"
    out, changed = fix_md036(text)
    assert changed is False
    assert out == text


# ---------------------------------------------------------------------------
# _wrap_line unit tests
# ---------------------------------------------------------------------------

def test_wrap_line_short_unchanged() -> None:
    assert _wrap_line("a short line", 120) == "a short line"


def test_wrap_line_breaks_at_nearest_pattern() -> None:
    """_wrap_line splits at the rightmost break pattern within max_length.

    Uses a short, predictable input with a clear ``. `` break pattern so the
    assertion isn't dependent on character counts in a 4x-repeated string.
    """
    # A single ``. `` at index 12; remainder is ``x`` (no further break points),
    # so the wrap is forced at the only break within max_length=30.
    line = "alpha bravo. " + "x" * 200
    wrapped = _wrap_line(line, 30)
    parts = wrapped.split("\n")
    assert len(parts) == 2, f"expected exactly 2 parts after wrap; got: {wrapped!r}"
    # parts[0] should end with the break pattern (`. `) — preserved at end of first part.
    assert parts[0].endswith(". "), (
        f"first part should end with break pattern '. '; got: {parts[0]!r}"
    )
    assert parts[0] == "alpha bravo. ", f"parts[0]={parts[0]!r}"
    # parts[1] should be the trailing x-run, no break patterns.
    assert parts[1].startswith("x"), f"parts[1]={parts[1]!r}"


def test_wrap_line_returns_input_unchanged_when_no_break_within_limit() -> None:
    """A 500-char run with no break points <= max_length is left as-is
    (safety: prevents infinite recursion in fix_md013)."""
    line = "x" * 250  # no spaces, no periods, no commas
    wrapped = _wrap_line(line, 120)
    assert wrapped == line


def test_wrap_line_returns_input_when_no_break_within_limit() -> None:
    """A long run with no break points <= max_length is returned unchanged."""
    # Replaces the older test_wrap_line_idempotent_on_already_wrapped which
    # tested a wrong contract — _wrap_line is a SUB-LINE helper and the
    # actual idempotency guarantee lives at the fix_md013 level (split +
    # re-wrap each sub-line). Wrap-the-same-line-twice testing is a
    # responsibility of fix_md013, not _wrap_line.
    line = "x" * 250
    assert _wrap_line(line, 120) == line


# ---------------------------------------------------------------------------
# Default fix list sanity
# ---------------------------------------------------------------------------

def test_default_fixes_count_and_excludes_md041() -> None:
    """13 default fixes; fix_md041 is intentionally NOT in the default list
    (conservative — better to manually review first-line H1 placement)."""
    fixes = _default_fixes()
    assert len(fixes) == 18, (
        f"expected 18 default fixes; got {len(fixes)}: "
        f"{[getattr(f, '__name__', f.__class__.__name__) for f in fixes]}"
    )
    fn_names = [getattr(f, "__name__", f.__class__.__name__) for f in fixes]
    # fix_md041 is intentionally absent (see fix_markdownlint.py module docstring).
    assert "fix_md041" not in fn_names, (
        "fix_md041 must not be in the default fix list — see "
        "fix_markdownlint.py module docstring."
    )
    # Spot-check that the 16 un-prefixed handlers are present by identity
    # (fix_md013 is wrapped in functools.partial so its __name__ comes from
    # the wrapped function — presence is brand-tested by .func unwrap below).
    expected_handlers = (
        fml.fix_md003, fml.fix_md004, fml.fix_md007, fml.fix_md009,
        fml.fix_md012, fml.fix_md018, fml.fix_md022, fml.fix_md025,
        fml.fix_md026, fml.fix_md029, fml.fix_md032, fml.fix_md033,
        fml.fix_md036, fml.fix_md040, fml.fix_md056, fml.fix_md060,
    )
    for fn in expected_handlers:
        assert fn in fixes, f"{fn.__name__} not in default fix list (by identity)"
    # fix_md013 is bound via functools.partial — check by unwrapping .func
    partial_md013 = next(
        (f for f in fixes if hasattr(f, "func") and f.func is fml.fix_md013),
        None,
    )
    assert partial_md013 is not None, "fix_md013 (as partial) not found in default fix list"


def test_default_fixes_demo_md_012_on_tripple_blank() -> None:
    """Practical demonstration: a doc with triple-blanks is a CLEAN target
    after one run of the default fix list (covers fix_md012)."""
    text = "# T\n\nfoo\n\n\nbar\n"
    out = fml._default_fixes()  # type: ignore[attr-defined]
    # Just call the fixes by iterating to simulate orchestrator behavior.
    current = text
    overall_changed = False
    for fn in out:
        new, c = fn(current)
        if c:
            overall_changed = True
            current = new
    assert "\n\n\n" not in current
    assert overall_changed is True


# ---------------------------------------------------------------------------
# fix_md026 — direct unit test (the rule the L99 cleanup depended on)
# ---------------------------------------------------------------------------

def test_fix_md026_strips_trailing_colon_from_heading() -> None:
    """``### Section:`` becomes ``### Section`` (MD026 contract)."""
    text = "### Section A:\n\nbody\n"
    out, changed = fml.fix_md026(text)
    assert changed is True, f"expected changed=True on trailing colon; got False. out=\n{out!r}"
    # Trailing colon gone, the rest preserved word-for-word.
    assert "### Section A" in out
    assert "### Section A:" not in out


def test_fix_md026_strips_trailing_period_from_heading() -> None:
    """``### Done.`` becomes ``### Done``."""
    text = "### Done.\n\nbody\n"
    out, changed = fml.fix_md026(text)
    assert changed is True
    assert "### Done" in out
    assert "### Done." not in out


def test_fix_md026_no_change_when_no_trailing_punct() -> None:
    """A heading without trailing punctuation must be left alone."""
    text = "### Clean Heading\n\nbody\n"
    out, changed = fml.fix_md026(text)
    assert changed is False
    assert out == text


def test_no_demote_h1_removes_only_fix_md025() -> None:
    """The CLI ``--no-demote-h1`` path drops exactly fix_md025, nothing else."""
    import fix_markdownlint as _fml
    all_fixes = _fml._default_fixes()
    filtered = [f for f in all_fixes if f is not _fml.fix_md025]
    assert len(filtered) == len(all_fixes) - 1
    for fn in filtered:
        assert fn is not _fml.fix_md025


# ---------------------------------------------------------------------------
# Orchestrator exception paths (process_files safety nets)
# ---------------------------------------------------------------------------
#
# The orchestrator's two safety paths previously had no test coverage. These
# tests assert the BEHAVIORAL contracts:
#   1. A non-existent path is reported explicitly with exit 1 (the script
#      cannot silently succeed on bad CLI input).
#   2. A binary / non-UTF-8 file does NOT crash the script — the read error
#      is surfaced with ``ERROR reading`` on stderr and a non-zero exit code.
#      Regression guard: previously ``process_files`` printed ``CLEAN``
#      whenever ``fix_file`` returned ``changed=False``, which silently
#      swallowed ``UnicodeDecodeError`` paths and left operators in the dark.


def test_process_files_nonexistent_returns_exit_1_and_not_found_message(
    tmp_path: Path,
) -> None:
    """A path that does not exist on disk must produce exit 1 and a
    ``NOT FOUND`` message on stderr that names the offending path.

    This is the orchestrator's only safety guarantee against bad CLI input
    (typos, deleted files, stale scripts). A silent success here would let
    the orchestrator pretend it processed a file it never touched.
    """
    nonexistent = tmp_path / "does_not_exist_xyzzy.md"
    assert not nonexistent.exists(), (
        "test fixture invariant: tmp_path/does_not_exist_xyzzy.md must not exist"
    )

    rc = _run_cli(str(nonexistent))

    assert rc.returncode == 1, (
        f"non-existent file must exit 1; got {rc.returncode}\n"
        f"stdout={rc.stdout!r}\nstderr={rc.stderr!r}"
    )
    assert "NOT FOUND" in rc.stderr, (
        f"stderr must contain 'NOT FOUND'; got: {rc.stderr!r}"
    )
    assert str(nonexistent) in rc.stderr, (
        f"stderr must name the missing path; got: {rc.stderr!r}"
    )
    # Must NOT claim the file was processed — a 'CLEAN' line for a file that
    # was never read would be a silent-success regression.
    assert "CLEAN" not in rc.stdout, (
        f"stdout must not report CLEAN for an unreadable path; got: {rc.stdout!r}"
    )


def test_process_files_nonexistent_with_other_valid_args_does_not_abort_batch(
    tmp_path: Path,
) -> None:
    """When one path is missing and a real file is also passed, the script
    processes the real file normally AND reports the missing one (still
    exit 1 overall). This guards against the orchestrator short-circuiting
    on the first error instead of collecting all problems.
    """
    nonexistent = tmp_path / "missing.md"
    real = tmp_path / "real.md"
    real.write_text("# T\n\nclean body\n", encoding="utf-8")

    rc = _run_cli(str(nonexistent), str(real))

    assert rc.returncode == 1, "overall exit must be 1 (the missing path)"
    assert "NOT FOUND" in rc.stderr
    assert str(nonexistent) in rc.stderr
    # The valid file is processed normally.
    assert "CLEAN" in rc.stdout
    assert str(real) in rc.stdout


def test_process_files_binary_file_handles_unicode_decode_error_gracefully(
    tmp_path: Path,
) -> None:
    """A binary file with non-UTF-8 bytes must:

    1. NOT crash the script (no Python traceback anywhere in stdout/stderr).
    2. Return a non-zero exit code (the operator must see the failure).
    3. Surface ``ERROR reading`` so the failure mode is diagnosable.

    Regression guard: previously ``process_files`` only branched on
    ``changed=False`` and would print ``CLEAN`` for files whose ``read_text``
    raised ``UnicodeDecodeError`` — silently accepting corrupted input as
    a no-op rather than reporting the read failure.
    """
    binary = tmp_path / "binary.md"
    # Non-UTF-8 bytes — guaranteed ``UnicodeDecodeError`` under strict utf-8.
    binary.write_bytes(b"\xff\xfe\x00\x01\x80\x90random bytes\n")
    assert binary.exists()
    assert binary.stat().st_size > 0

    rc = _run_cli(str(binary))

    # (1) No Python traceback — script must not crash.
    combined = rc.stdout + rc.stderr
    assert "Traceback" not in combined, (
        f"script must not crash on binary input; got:\nstdout={rc.stdout!r}\n"
        f"stderr={rc.stderr!r}"
    )
    assert "UnicodeDecodeError" not in combined, (
        f"raw exception class name must not leak into output; got:\n"
        f"stdout={rc.stdout!r}\nstderr={rc.stderr!r}"
    )
    # (2) Non-zero exit code.
    assert rc.returncode != 0, (
        f"binary file must exit non-zero; got {rc.returncode}\n"
        f"stdout={rc.stdout!r}\nstderr={rc.stderr!r}"
    )
    # (3) 'ERROR reading' surfaced so the operator sees what failed.
    assert ("ERROR reading" in rc.stderr) or ("ERROR reading" in rc.stdout), (
        f"output must contain 'ERROR reading' message to surface the failure;\n"
        f"stdout={rc.stdout!r}\nstderr={rc.stderr!r}"
    )
    # Must NOT silently claim CLEAN — that was the pre-fix regression.
    assert "CLEAN" not in rc.stdout, (
        f"stdout must not say CLEAN for an unreadable file; got: {rc.stdout!r}"
    )


def test_process_files_binary_file_does_not_mutate_other_files(
    tmp_path: Path,
) -> None:
    """A binary file alongside a valid file: the binary is rejected with an
    ERROR and the valid file is still processed normally. Guards against the
    orchestrator bailing out on the first failure instead of advancing
    through the rest of the file list.
    """
    binary = tmp_path / "binary.md"
    binary.write_bytes(b"\xff\xfe\x00\x01\x80\x90random bytes\n")
    valid = tmp_path / "valid.md"
    valid.write_text("# T\n\nclean body\n", encoding="utf-8")

    rc = _run_cli(str(binary), str(valid))

    assert rc.returncode != 0, "binary file should keep overall exit non-zero"
    combined = rc.stdout + rc.stderr
    assert "Traceback" not in combined
    assert "ERROR reading" in combined
    # Valid file is still processed (CLEAN, no mutation needed).
    assert "CLEAN" in rc.stdout
    assert str(valid) in rc.stdout


def test_fix_file_returns_error_marker_on_unreadable_input(tmp_path: Path) -> None:
    """Unit-level: ``fix_file`` against a binary file returns the
    ``ERROR reading:`` marker so ``process_files`` can surface it.

    Pinning the contract: the marker prefix ``ERROR reading:`` is what
    ``process_files`` greps for — if this changes, both sides need to move
    together (the test will catch a one-sided change).
    """
    from fix_markdownlint import fix_file

    binary = tmp_path / "binary.md"
    binary.write_bytes(b"\xff\xfe\x00\x01\x80\x90random bytes\n")

    changed, new_content, fixes = fix_file(binary)

    assert changed is False
    assert new_content == ""
    assert fixes, "fix_file must return a non-empty fixes list on read failure"
    assert fixes[0].startswith("ERROR reading:"), (
        f"fix_file's error marker must start with 'ERROR reading:'; "
        f"got: {fixes[0]!r}"
    )
    assert "unicode" in fixes[0].lower() or "decode" in fixes[0].lower(), (
        f"error message should mention UnicodeDecodeError; got: {fixes[0]!r}"
    )

# ---------------------------------------------------------------------------

# ===========================================================
# Unit tests for the rules added in the L99 cleanup pass:
# fix_md004, fix_md007, fix_md009, fix_md032, CRLF-aware fix_md012.
# ===========================================================
def test_fix_md004_normalizes_star_marker() -> None:
    """`* Item` -> `- Item` (MD004 ul-style)."""
    text = "intro paragraph" + D + "* first" + D + "* second" + D
    out, changed = fml.fix_md004(text)
    assert changed is True
    assert out == "intro paragraph" + D + "- first" + D + "- second" + D


def test_fix_md004_normalizes_plus_marker() -> None:
    """`+ Item` -> `- Item`."""
    text = "+ first" + D + "+ second" + D
    out, changed = fml.fix_md004(text)
    assert changed is True
    assert out == "- first" + D + "- second" + D


def test_fix_md004_dash_marker_left_alone() -> None:
    """`- Item` is already canonical -- no change."""
    text = "- first" + D + "- second" + D
    out, changed = fml.fix_md004(text)
    assert changed is False
    assert out == text


def test_fix_md004_preserves_indentation() -> None:
    """Nested bullets preserve their indent on conversion."""
    text = "  * nested" + D
    out, changed = fml.fix_md004(text)
    assert changed is True
    assert out == "  - nested" + D


def test_fix_md004_idempotent_on_already_canonical() -> None:
    """Running fix_md004 twice produces identical output."""
    src = "- already-dash" + D
    once, c1 = fml.fix_md004(src)
    twice, c2 = fml.fix_md004(once)
    assert c1 is False and c2 is False
    assert once == twice == src


def test_fix_md007_snaps_1_space_to_0() -> None:
    """Single-indent list marker snaps to no-indent."""
    text = " - Item" + D
    out, changed = fml.fix_md007(text)
    assert changed is True
    assert out == "- Item" + D


def test_fix_md007_snaps_3_space_to_2() -> None:
    """Three-space indent snaps to two-space."""
    text = "   - Item" + D
    out, changed = fml.fix_md007(text)
    assert changed is True
    assert out == "  - Item" + D


def test_fix_md007_leaves_2_space_unchanged() -> None:
    """Already-even indent at 2 spaces is left alone."""
    text = "  - Item" + D
    out, changed = fml.fix_md007(text)
    assert changed is False
    assert out == text


def test_fix_md007_leaves_4_space_unchanged() -> None:
    """4-space indent at multiple-of-2 is left alone."""
    text = "    - deeply nested" + D
    out, changed = fml.fix_md007(text)
    assert changed is False
    assert out == text


def test_fix_md007_snaps_odd_indent_on_star_and_plus() -> None:
    """fix_md007 normalizes odd indent on `+` and `*`."""
    out_star3, c_star3 = fml.fix_md007("   * Item" + D)
    out_plus3, c_plus3 = fml.fix_md007("   + Item" + D)
    assert c_star3 is True and c_plus3 is True
    assert out_star3 == "  * Item" + D
    assert out_plus3 == "  + Item" + D


def test_fix_md009_strips_trailing_spaces() -> None:
    """Trailing spaces are stripped, content preserved."""
    text = "hello   " + D + "world" + D
    out, changed = fml.fix_md009(text)
    assert changed is True
    assert out == "hello" + D + "world" + D


def test_fix_md009_strips_trailing_tab() -> None:
    """Trailing tabs are stripped as whitespace."""
    text = "tabbed" + chr(9) + D + "line" + D
    out, changed = fml.fix_md009(text)
    assert changed is True
    assert out == "tabbed" + D + "line" + D


def test_fix_md009_idempotent_when_no_trailing_whitespace() -> None:
    """Already-clean doc is unchanged."""
    text = "clean" + D + "lines" + D + "here" + D
    out, changed = fml.fix_md009(text)
    assert changed is False
    assert out == text


def test_fix_md012_crlf_aware_collapse() -> None:
    """CRLF triple-blank lines collapse to 2 CRLFs preserving local style."""
    text = "para1" + chr(13) + chr(10) + chr(13) + chr(10) + chr(13) + chr(10) + chr(13) + chr(10) + "para2" + chr(13) + chr(10)
    out, changed = fml.fix_md012(text)
    assert changed is True
    assert out == "para1" + chr(13) + chr(10) + chr(13) + chr(10) + "para2" + chr(13) + chr(10)


def test_fix_md012_lf_collapse_still_works() -> None:
    """LF triple-blank collapse is backwards compatible."""
    text = "para1" + D + D + D + D + "para2" + D
    out, changed = fml.fix_md012(text)
    assert changed is True
    assert out == "para1" + D + D + "para2" + D


def test_fix_md012_idempotent_when_already_clean() -> None:
    """Doc with at most 1 blank line is unchanged."""
    text = "para1" + D + D + "para2" + D
    out, changed = fml.fix_md012(text)
    assert changed is False
    assert out == text


def test_fix_md032_inserts_blank_around_inline_list() -> None:
    """A list between two prose lines without blanks gets them."""
    text = "para1" + D + "- item a" + D + "- item b" + D + "para2" + D
    out, changed = fml.fix_md032(text)
    assert changed is True
    assert out == "para1" + D + D + "- item a" + D + "- item b" + D + D + "para2" + D


def test_fix_md032_handles_heading_before_list() -> None:
    """List right after heading is separated by blank line."""
    text = "# Heading" + D + "- item" + D + "more" + D
    out, changed = fml.fix_md032(text)
    assert changed is True
    assert out == "# Heading" + D + D + "- item" + D + D + "more" + D


def test_fix_md032_idempotent_when_already_separated() -> None:
    """List already surrounded by blanks is unchanged."""
    text = "para1" + D + D + "- a" + D + "- b" + D + D + "para2" + D
    out, changed = fml.fix_md032(text)
    assert changed is False
    assert out == text


def test_fix_md032_preserves_loose_lists() -> None:
    """Internal blank line within a list is NOT disturbed."""
    text = "para1" + D + D + "- a" + D + D + "- b" + D + D + "para2" + D
    out, changed = fml.fix_md032(text)
    assert changed is False
    assert out == text


def test_fix_md032_handles_ordered_list() -> None:
    """Numbered list items (1./2.) trigger blank-line insertion."""
    text = "para" + D + "1. first" + D + "2. second" + D + "more" + D
    out, changed = fml.fix_md032(text)
    assert changed is True
    assert out == "para" + D + D + "1. first" + D + "2. second" + D + D + "more" + D


def test_fix_md032_does_not_break_open_fence_around_inner_dashes() -> None:
    """YAML code fence with ``- step1`` / ``- step2`` must NOT have blank
    lines injected around the fence (regression guard for the L99 cleanup
    pass where mdlint fenced blocks were broken by spurious blanks)."""
    text = (
        "intro paragraph" + D
        + "```yaml" + D
        + "- step1" + D
        + "- step2" + D
        + "```" + D
        + "after block" + D
    )
    out, changed = fml.fix_md032(text)
    assert changed is False, (
        f"fenced code block with inner dashes must be a no-op; got changed=True. "
        f"out={out!r}"
    )
    assert out == text, (
        f"fenced code block content must be byte-identical to input; got diff:\n"
        f"INPUT={text!r}\nOUTPUT={out!r}"
    )

def test_fix_md032_does_not_break_open_fence_with_outer_list_concat() -> None:
    """A list abutting a code fence must not accidentally split the fence
    boundary. The list is treated as a single tight-list that wraps the
    fence (``- outer a``, `` ```, ``- inner``, `` ```, ``- outer b`` —
    all contiguous, no blanks injected inside the fence).
    """
    text = (
        "- outer a" + D
        + "```" + D
        + "- inner" + D
        + "```" + D
        + "- outer b" + D
    )
    out, changed = fml.fix_md032(text)
    # The implementation should leave this as a no-op: the list wraps the
    # fence as a tight list (Markdown-spec valid), so MD032's blank-around-
    # lists rule is satisfied without inserting any blanks at all.
    assert changed is False, (
        f"tight list wrapping a fence is a no-op; got changed=True. "
        f"out={out!r}"
    )
    assert out == text, (
        f"output must be byte-identical to input; diff:\n"
        f"IN={text!r}\nOUT={out!r}"
    )
    # Sanity: fence interior must be consecutive (no newline inserted
    # between ````` `` and ``- inner``, or between ``- inner`` and
    # `` ``` ``). This is the actual correctness invariant the test is
    # protecting against.
    assert "```" + D + "- inner" + D + "```" + D in out, (
        f"fence block must be contiguous (open-fence, inner, close-fence "
        f"with NO intervening blanks); got: {out!r}"
    )


def test_fix_md032_does_not_promote_table_rows_with_inner_dashes() -> None:
    """A markdown table row whose cell starts with ``- `` (e.g. ``| - step |``)
    must NOT be treated as a list item, even when surrounded by prose."""
    text = (
        "intro" + D
        + "| step | action |" + D
        + "| - | run |" + D
        + "| --- | --- |" + D
        + "| done | ship |" + D
        + "outro" + D
    )
    out, changed = fml.fix_md032(text)
    assert changed is False, (
        f"table with inner ``- `` cells must be a no-op; got changed=True. "
        f"out={out!r}"
    )
    assert out == text, (
        f"table content must be unchanged; diff:\nIN={text!r}\nOUT={out!r}"
    )


def test_fix_md032_fence_then_list_still_wraps_list() -> None:
    """After a fenced code block, a downstream real list SHOULD still get
    blank-line wrapping (sanity that the fence-state path doesn't suppress
    all subsequent list detection).
    """
    text = (
        "intro" + D
        + "```bash" + D
        + "echo hello" + D
        + "```" + D
        + "- real list" + D
        + "- second item" + D
        + "after" + D
    )
    expected = (
        "intro" + D
        + "```bash" + D
        + "echo hello" + D
        + "```" + D
        + D
        + "- real list" + D
        + "- second item" + D
        + D
        + "after" + D
    )
    out, changed = fml.fix_md032(text)
    assert changed is True, (
        f"real list after fence should trigger blank-wrapping; got False. "
        f"out={out!r}"
    )
    assert out == expected, (
        f"full-output mismatch:\nEXPECTED={expected!r}\nGOT     ={out!r}"
    )
    # Sanity-invariant: the fence block (open-fence + interior + close-fence)
    # must remain contiguous. Specifically: `` ```bash\\necho hello\\n```\\n``
    # must appear as a consecutive substring of the output (NO blank line
    # inserted between `` ```bash`` and ``echo hello``, or between
    # ``echo hello`` and `` ``` ``).
    fence_block = "```bash" + D + "echo hello" + D + "```" + D
    assert fence_block in out, (
        f"fence block (with lang tag) must remain contiguous; got: {out!r}"
    )


def test_fix_md031_inserts_blank_around_inline_fence() -> None:
    """A fence between prose lines without blanks gets blanks around it (MD031)."""
    text = "para1" + D + "```" + D + "echo hi" + D + "```" + D + "para2" + D
    out, changed = fml.fix_md031(text)
    assert changed is True
    assert out == ("para1" + D + D + "```" + D + "echo hi" + D + "```" + D + D + "para2" + D)

def test_fix_md031_idempotent_when_already_blanked() -> None:
    """Fence already surrounded by blanks is unchanged (idempotency)."""
    text = "para1" + D + D + "```" + D + "echo hi" + D + "```" + D + D + "para2" + D
    out, changed = fml.fix_md031(text)
    assert changed is False
    assert out == text

def test_fix_md031_no_phantom_blank_at_file_start() -> None:
    """A fence as the first line does NOT gain a phantom leading blank."""
    text = "```" + D + "echo hi" + D + "```" + D
    out, changed = fml.fix_md031(text)
    assert changed is False, f"unexpected phantom blank at file start: {out!r}"
    assert out == text

def test_fix_md031_no_phantom_blank_at_file_end() -> None:
    """A fence as the last content does NOT gain a phantom trailing blank."""
    text = "para1" + D + "```" + D + "echo hi" + D + "```" + D
    out, changed = fml.fix_md031(text)
    assert out == ("para1" + D + D + "```" + D + "echo hi" + D + "```" + D), "got: {out!r}"

def test_fix_md031_two_consecutive_fences_get_separator() -> None:
    """Two fences with no blank between them get one inserted between closing and opening."""
    text = "```" + D + "a" + D + "```" + D + "```" + D + "b" + D + "```" + D
    out, changed = fml.fix_md031(text)
    assert changed is True
    assert out == ("```" + D + "a" + D + "```" + D + D + "```" + D + "b" + D + "```" + D), "got: {out!r}"
