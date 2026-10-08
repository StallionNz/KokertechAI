"""Reusable inverted-toggle fixture for meta-regression tests on ``scripts.check_knowledge_anchors``.

Locks the inversion-detection property of the Case C exact-string pin.

Usage:
    from _check_anchors_fixtures import run_with_inverted_toggle
    rc, out = run_with_inverted_toggle(text)
"""
import sys, re, tempfile
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path

GATE_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(GATE_DIR))
import check_knowledge_anchors as gate  # noqa: E402

# Captures group(2) as the FULL target including '#' — matching the
# original find_markdown_inline_links signature where split_target()
# expects the raw parenthesized content (e.g. '#nope', not just 'nope').
_PARITY_RE = re.compile(r"`|\[([^\]]+)\]\(([#][^\)]+)\)")


def _inverted_find(text):
    refs = []
    for i, line in enumerate(text.splitlines(), 1):
        inside_code = False
        for m in re.finditer(_PARITY_RE, line):
            if m.group(0) == "`":
                inside_code = not inside_code
                continue
            if not inside_code:  # INVERTED: was `if inside_code: continue`
                continue
            refs.append((i, m.group(1), m.group(2)))
    return refs


def run_with_inverted_toggle(text):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as t:
        t.write(text)
        path = t.name
    saved_path, saved_func = gate.DOC_PATH, gate.find_markdown_inline_links
    gate.DOC_PATH = Path(path)
    gate.find_markdown_inline_links = _inverted_find
    buf = StringIO()
    rc = None
    try:
        with redirect_stdout(buf):
            rc = gate.main()
    finally:
        gate.find_markdown_inline_links = saved_func
        gate.DOC_PATH = saved_path
        Path(path).unlink(missing_ok=True)
    return rc, buf.getvalue()
