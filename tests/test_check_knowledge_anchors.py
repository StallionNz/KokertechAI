"""Regression tests for scripts/check_knowledge_anchors.py.

Pins the gate's behavior so future refactors that break dedup, H1-H6 scope,
cross-file anchor resolution, or the gh_anchor() GitHub-spec algorithm
correctness surface as test failures rather than silent false-negatives
(broken anchors slipping through the gate during CI).

Test budget: 8 cases (Sprint 18 R3's 6 + 2 cross-file anchors added 2026-07-22).
The first 6 use direct-import + redirect_stdout so pytest can drive the gate
without subprocess-escape pitfalls on Windows paths. The e2e test uses a real
subprocess call against the actual KNOWLEDGE.md as the daily-authoring
canary -- if a future contributor accidentally introduces a broken inline
link, that test fails before the docs ship. The two cross-file tests use
the same fimport-then-monkey-patch pattern with a richer DOC_PATHS swap.
"""
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parent.parent / "scripts"
GATE_PATH = GATE_DIR / "check_knowledge_anchors.py"
PROJECT_ROOT = GATE_DIR.parent

# Make the gate module importable.
sys.path.insert(0, str(GATE_DIR))
import check_knowledge_anchors as gate  # noqa: E402


def _run_gate(doc_text):
    """Run gate against an in-memory stub. Backward-compatible single-file."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(doc_text)
        tmp_path = tmp.name
    saved_path = gate.DOC_PATH
    saved_paths = list(gate.DOC_PATHS)
    gate.DOC_PATH = Path(tmp_path)
    gate.DOC_PATHS = [Path(tmp_path)]  # single-file scope for legacy tests
    buf = StringIO()
    try:
        with redirect_stdout(buf):
            rc = gate.main()
    finally:
        gate.DOC_PATH = saved_path
        gate.DOC_PATHS = saved_paths
        Path(tmp_path).unlink(missing_ok=True)
    return rc, buf.getvalue()


def _run_gate_multi(src_text, target_text):
    """Run gate against 2-file cross-file scope."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        src = tmpdir_path / "src.md"
        tgt = tmpdir_path / "target.md"
        src.write_text(src_text, encoding="utf-8")
        tgt.write_text(target_text, encoding="utf-8")
        saved_doc = gate.DOC_PATH
        saved_paths = list(gate.DOC_PATHS)
        # DOC_PATH is replaced with src for the scan; target is reachable
        # via DOC_PATHS for cross-file anchor resolution.
        gate.DOC_PATH = src
        gate.DOC_PATHS = [src, tgt]
        buf = StringIO()
        try:
            with redirect_stdout(buf):
                rc = gate.main()
        finally:
            gate.DOC_PATH = saved_doc
            gate.DOC_PATHS = saved_paths
    return rc, buf.getvalue()


def test_gate_clean_on_actual_knowledge_md():
    """E2E: real KNOWLEDGE.md exits 0 (daily-authoring canary)."""
    result = subprocess.run(
        [sys.executable, str(GATE_PATH)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert result.returncode == 0, (
        f"Real KNOWLEDGE.md FAILED anchor gate (exit {result.returncode}).\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_gate_exits_one_on_broken_inline_link():
    """A broken [](#does-not-exist) link forces HARD-BROKEN exit 1."""
    rc, out = _run_gate("## 1. Foo\n\n[Broken](#does-not-exist)\n")
    assert rc == 1, f"Expected exit 1 on broken anchor; got {rc}"
    assert "HARD-BROKEN" in out
    assert "1 unique broken target" in out


def test_gate_dedups_broken_targets_on_same_line():
    """ Two links pointing at the same broken target dedup to 1 unique finding. """
    rc, out = _run_gate(
        "## 1. Foo\nFirst [Bad](#nope) and second [Bad](#nope) on same line.\n"
    )
    assert rc == 1
    # dedup keyed on target -- if dedup is broken, count would inflate and
    # the next line would say '2 unique broken targets'.
    assert "1 unique broken target" in out, f"Dedup failed:\n{out}"


def test_gate_h4_headings_in_scope():
    """#### Foo Bar Baz heading slugifies to foo-bar-baz and resolves."""
    rc, _ = _run_gate(
        "## 1. Section\n#### Foo Bar Baz\n\n[bar](#foo-bar-baz) here.\n"
    )
    assert rc == 0, (
        "H4 heading slug 'foo-bar-baz' should resolve -- H1-H6 scan must "
        "include #### headings."
    )


def test_gate_drift_warning_on_unknown_section_mention():
    """Bare section 99 with no real ## 99. heading emits DRIFT WARNING (exit 0)."""
    rc, out = _run_gate(
        "## 1. Real Section\n\nSee " + chr(0xa7) + "99 for missing content.\n"
    )
    # Drift is informational, gate still passes (exit 0).
    assert rc == 0
    assert "DRIFT WARNING" in out


def test_gate_gh_anchor_spec_pins():
    """Pin: gh_anchor() mirrors GitHub html-pipeline spec (no consecutive-dash collapse).

    These 4 fixtures are pinned to the LIVE algorithm output (verified during
    the Sprint 18 R3 anchor pass via direct invocation of `g.gh_anchor(*)`).
    If GitHub changes its slugify algorithm, this test fires and flags the
    drift explicitly via assertion mismatch.
    """
    cases = [
        # '### Rule 1 -- Lifecycle Signals' -> 4-dash literal preserved
        # (1 from '1 ', 2 from '--', 1 from ' lifecycle' = 4 dashes)
        ("### Rule 1 -- Lifecycle Signals", "rule-1----lifecycle-signals"),
        # '## 18. KeepaliveContext -- Context Manager' -> 4-dash literal preserved
        ("## 18. KeepaliveContext -- Context Manager",
         "18-keepalivecontext----context-manager"),
        # '#### Foo Bar Baz' -> each word separated by 1 dash (no consecutive dashes)
        ("#### Foo Bar Baz", "foo-bar-baz"),
        # '## 1. Project Identity & Philosophy' -> & stripped, 2-dash gap
        ("## 1. Project Identity & Philosophy",
         "1-project-identity--philosophy"),
    ]
    for heading, expected in cases:
        actual = gate.gh_anchor(heading)
        assert actual == expected, (
            f"gh_anchor({heading!r}) returned {actual!r}, expected {expected!r}. "
            f"Possible GitHub algorithm drift -- investigate before adjusting."
        )


def test_gate_ignores_anchors_in_code_spans():
    """REGRESSION GUARD for backtick-parity defense in
    ``find_markdown_inline_links()`` of ``scripts/check_knowledge_anchors.py``.

    Sprint 18 R3 sub-section in KNOWLEDGE.md section 17 inserts literal
    `` [text](#anchor) `` prose as self-documentation -- without backtick
    parity tracking, the prior single-regex implementation flagged its
    own prose with HARD-BROKEN exit 1. This test pins the parity toggle
    so a future refactor that drops it surfaces as a regression.

    Uses literal backtick characters inside Python string literals --
    they need no escaping. The first word of the gate's authoritative
    prose was changed from `[text]` to `text` so an inline trigger
    cannot accidentally activate the regex match against the docstring
    itself during pytest's collection phase.
    """
    heading = "## 1. Demo Section"

    # Case A: anchor INSIDE backticks -> gate must NOT trigger HARD-BROKEN.
    rc_a, out_a = _run_gate(
        heading + "\n\nUse the literal ` [broken](#nope) ` span as illustration.\n"
    )
    assert rc_a == 0, (
        "Anchor inside backticks must be skipped; got exit " + str(rc_a) + ":\n" + out_a
    )
    assert "0 Markdown inline links scanned" in out_a, (
        "Backtick-parity defense regression: expected 0 anchors scanned; got:\n" + out_a
    )
    assert "HARD-BROKEN" not in out_a

    # Case B: identical anchor OUTSIDE backticks -> gate MUST still trigger.
    rc_b, out_b = _run_gate(
        heading + "\n\nUse [broken](#nope) as a real link here.\n"
    )
    assert rc_b == 1, "Anchor outside backticks must be flagged; got " + str(rc_b)
    # Gate outputs in ``filename:lineno: [text](#target)`` format.
    assert ":3: [broken](#nope)" in out_b, (
        "Out-of-code anchor must be reported as broken; expected ``line 3: [broken](#nope)`` "
        "in output, got:\n" + out_b
    )
    assert "HARD-BROKEN" in out_b

    # Case C: mid-line backtick toggle -- second anchor is in code span,
    # only the OUT-of-code anchor triggers HARD-BROKEN.
    rc_c, out_c = _run_gate(
        heading + "\n\n[broken](#nope) and literal ` [also-broken](#nope) ` span.\n"
    )
    assert rc_c == 1
    assert ":3: [broken](#nope)" in out_c


def test_inverted_toggle_breaks_case_c():
    """Meta-regression: pins Case C inversion-detection property via the
    ``run_with_inverted_toggle`` fixture in ``_check_anchors_fixtures``."""
    from _check_anchors_fixtures import run_with_inverted_toggle
    heading = "## 1. Demo Section"
    text = heading + "\n\n[broken](#nope) and literal ` [also-broken](#nope) ` span.\n"
    rc_inv, out_inv = run_with_inverted_toggle(text)
    assert rc_inv == 1
    assert "line 3: [broken](#nope)" not in out_inv


# --- Sprint 19.6 follow-up additions (2026-07-22) -----------------------------

def test_cross_file_broken_anchor_exits_one():
    """Cross-file target with broken anchor forces HARD-BROKEN exit 1.

    The Sprint 19.6 drift example: ``(target.md#bogus)`` where target.md's
    real heading slug is ``real-heading``. Validates the new Pass 2 scan.

    Also pins the cross-file OUTPUT format contract: no double ``.md.md``
    extension. The bug fix in commit 2026-07-22 was a real defect (printed
    ``CHANGELOG.md.md#...`` instead of ``CHANGELOG.md#...``) that slipped
    through because no test pinned the exact format. This assertion locks
    the contract so the regression cannot return silently.
    """
    src = "## 1. Source Section\n\n[Link](target.md#bogus) here.\n"
    tgt = "## 6. Real Heading\n"
    rc, out = _run_gate_multi(src, tgt)
    assert rc == 1, "Cross-file broken target must HARD-BROKEN; got " + str(rc)
    assert "HARD-BROKEN" in out
    # The cross-file path is reported with source filename + line + target file + anchor.
    assert "target.md#bogus" in out, (
        "Expected cross-file HARD-BROKEN source:target#anchor in output; got:\n" + out
    )
    # REGRESSION GUARD: cross-file output format must NOT contain the
    # double-extension artifact ``.md.md#`` (regression: tgt_file was
    # already ending in ``.md`` and the print statement appended another
    # ``.md``). Locks the contract for future refactors of the output.
    assert ".md.md" not in out, (
        "REGRESSION: cross-file HARD-BROKEN output must not contain "
        "``.md.md`` (double-extension bug). Got:\n" + out
    )


def test_cross_file_resolved_anchor_exits_zero():
    """Cross-file target whose anchor matches a real heading in target.md
    EXITS 0 -- the link is valid.
    """
    # Compute the actual gh_anchor slug for the target heading, then build
    # the source link with that slug. Validates the cross-file passes
    # anchor resolution end-to-end.
    target_heading_text = "## 6. Real Heading"
    actual_slug = gate.gh_anchor(target_heading_text)
    src = (
        "## 1. Source Section\n\n"
        "[Link](target.md#" + actual_slug + ") here.\n"
    )
    tgt = target_heading_text + "\n"
    rc, out = _run_gate_multi(src, tgt)
    assert rc == 0, (
        "Resolved cross-file anchor must NOT trigger; got exit "
        + str(rc) + ":\n" + out
    )
    assert "HARD-BROKEN" not in out, (
        "Resolved cross-file anchor must NOT trigger HARD-BROKEN; got:\n" + out
    )


def test_external_url_anchor_bypasses_validation():
    """External URL anchors (http://, https://) bypass local validation.

    A link like ``https://github.com/foo#readme`` is external; gate must
    not crash and must not hard-broken on a remote URL.
    """
    src = (
        "## 1. Source Section\n\n"
        "[External](https://example.com/docs#whatever) here.\n"
    )
    tgt = "## 6. Real Heading\n"
    rc, out = _run_gate_multi(src, tgt)
    assert rc == 0, (
        "External URL anchor must bypass validation; got "
        + str(rc) + ":\n" + out
    )
    assert "HARD-BROKEN" not in out


def test_prefix_drift_pin_implementation_roadmap_pattern():
    """PIN: the exact drift risk from the Sprint 19.6 follow-up
    user prompt. ``(target.md#0203---2026-07-22)`` is a PREFIX of the
    actual ``target.md`` heading slug ``0203---2026-07-22--pyc-corruption-
    recovery--no-corruption-gate-5th-gate``. Strict equality must HARD-
    BROKEN; only GitHub's lenient prefix-rendering tolerates it. The
    gate's strict-eq check is intentional -- it's the canary for prefix
    drift as headings evolve.
    """
    # The link target filename must match the temp file's basename
    # (otherwise resolve_target_filename() can't find it in the registry
    # and silently skips the broken anchor -- the test would pass for
    # the wrong reason).
    src = (
        "## Sprint 19: Decomposition\n\n"
        "See [CHANGELOG v0.20.3](target.md#0203---2026-07-22) here.\n"
    )
    # target.md has the FULL heading text, so the actual slug includes
    # the post-`---2026-07-22` text -- the prefix alone is NOT exact match.
    tgt = (
        "## [0.20.3] - 2026-07-22 .pyc Corruption Recovery + NO-CORRUPTION Gate (5th gate)\n"
    )
    rc, out = _run_gate_multi(src, tgt)
    assert rc == 1, (
        "Prefix-only drift anchor must HARD-BROKEN (strict equality); got " + str(rc)
    )
    # Check the exact broken target key appears in the formatted output.
    assert "target.md#0203---2026-07-22" in out, (
        "Expected HARD-BROKEN output to reference the exact broken prefix "
        "target target.md#0203---2026-07-22; got:\n" + out
    )
