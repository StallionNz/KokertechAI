#!/usr/bin/env python3
"""
fix_markdownlint.py
===================

Idempotent, re-run-safe cleanup script for KokertechAI's .md files under the
strict `.markdownlint.json` config.

Handles 18 mechanical fix categories that the `markdownlint-cli --fix`
auto-fixer does NOT handle (MD003, MD004, MD007, MD009, MD012, MD018, MD022,
MD025, MD026, MD029, MD031, MD032, MD033, MD036, MD040, MD056, MD060, MD013).
Pairs with that auto-fixer as a second pass:

    npx markdownlint-cli --fix --config .markdownlint.json <file>  # first pass
    python scripts/fix_markdownlint.py <file>                      # second pass

Re-run safety:
  - Each fix function is a no-op when the content is already in the target state
  - File is only written if the final content differs from the original (hash check)
  - `--check` mode reports what WOULD change without modifying anything
  - `--dry-run` is an alias for `--check`
  - Idempotency: running the script twice produces identical output

Usage:
    python scripts/fix_markdownlint.py file1.md file2.md ...
    python scripts/fix_markdownlint.py --check file.md
    python scripts/fix_markdownlint.py --dry-run file.md
    python scripts/fix_markdownlint.py --verbose file.md
    python scripts/fix_markdownlint.py --no-demote-h1 file.md   # skip fix_md025
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from functools import partial
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NL = "\n"
BT = "```"  # code fence

# Conservative natural-break point order (longest to shortest)
BREAK_PATTERNS = [". ", ", ", " "]

# ---------------------------------------------------------------------------
# Fix functions — each takes a string, returns (new_string, changed: bool)
# ---------------------------------------------------------------------------


def fix_md003(text: str) -> tuple[str, bool]:
    """Convert setext headings (===, ---) to ATX style (MD003 heading-style)."""
    lines = text.split(NL)
    out = []
    changed = False
    i = 0
    while i < len(lines):
        if i + 1 < len(lines):
            n = lines[i + 1].strip()
            if n and len(n) >= 3 and (all(c == "=" for c in n) or all(c == "-" for c in n)):
                if lines[i].strip() and not lines[i].lstrip().startswith("#"):
                    if all(c == "=" for c in n):
                        out.append(f"# {lines[i].strip()}")
                    else:
                        out.append(f"## {lines[i].strip()}")
                    changed = True
                    i += 2
                    continue
        out.append(lines[i])
        i += 1
    return NL.join(out), changed


def fix_md004(text: str) -> tuple[str, bool]:
    """Normalize unordered list markers (* / +) to - (MD004 ul-style)."""
    lines = text.split(NL)
    out: list[str] = []
    changed = False
    for line in lines:
        s = line.lstrip()
        marker = ""
        if s.startswith("* "):
            marker = "* "
        elif s.startswith("+ "):
            marker = "+ "
        if marker:
            indent = line[: len(line) - len(s)]
            new_line = f"{indent}- {s[len(marker):]}"
            if new_line != line:
                changed = True
            out.append(new_line)
        else:
            out.append(line)
    return NL.join(out), changed


def fix_md007(text: str) -> tuple[str, bool]:
    """Snap odd list-marker indentation to the nearest even indent (MD007 ul-indent).

    Conservative: only snaps lines whose leading-whitespace count is odd.
    Lines with an even count already are left alone (might be correctly nested
    under a 2-space parent). 1 → 0, 3 → 2, 5 → 4, etc.
    """
    lines = text.split(NL)
    out: list[str] = []
    changed = False
    marker_starts = ("- ", "* ", "+ ")
    for line in lines:
        s = line.lstrip()
        if s.startswith(marker_starts):
            leading = line[: len(line) - len(s)]
            n = len(leading)
            if n % 2 == 1:
                new_n = n - 1 if n > 0 else 0
                new_line = " " * new_n + s
                if new_line != line:
                    changed = True
                out.append(new_line)
                continue
        out.append(line)
    return NL.join(out), changed


def fix_md009(text: str) -> tuple[str, bool]:
    """Strip trailing whitespace from each line (MD009 no-trailing-spaces).

    Splits on \\n (LF); any trailing \\r from CRLF input is rstrip'd away.
    Idempotent: already-clean documents pass through unchanged.
    """
    lines = text.split(NL)
    out: list[str] = []
    changed = False
    for line in lines:
        stripped = line.rstrip()
        if stripped != line:
            changed = True
        out.append(stripped)
    return NL.join(out), changed


def fix_md012(text: str) -> tuple[str, bool]:
    """Collapse 3+ consecutive line breaks (LF or CRLF) to exactly 2 (MD012).

    CRLF-aware: handles ``(?:\r?\n){3,}`` so Windows-source files
    (CRLF line endings) get fixed, not silently ignored. Preserves the local
    line-ending style — CRLF input still produces CRLF output, LF input
    produces LF output.
    """
    pat = re.compile(r"(?:\r?\n){3,}")
    if not pat.search(text):
        return text, False
    matched = pat.search(text)
    style = "\r\n" if matched.group().startswith("\r") else "\n"
    new = pat.sub(style + style, text)
    return new, new != text


def fix_md018(text: str) -> tuple[str, bool]:
    """Add space after '#' in ATX headings (MD018 no-missing-space-atx)."""
    lines = text.split(NL)
    out = []
    changed = False
    for line in lines:
        s = line.lstrip()
        if s.startswith("#") and not s.startswith("#!"):
            i = 0
            while i < len(s) and s[i] == "#":
                i += 1
            if 1 <= i <= 6 and i < len(s) and s[i] != " ":
                indent = line[: len(line) - len(s)]
                out.append(f"{indent}{'#' * i} {s[i:].lstrip()}")
                changed = True
                continue
        out.append(line)
    return NL.join(out), changed


def fix_md022(text: str) -> tuple[str, bool]:
    """Add blank lines around headings (MD022 blanks-around-headings)."""
    lines = text.split(NL)
    out: list[str] = []
    changed = False
    for i, line in enumerate(lines):
        s = line.strip()
        is_heading = bool(s) and s[0] == "#" and not s.startswith("#!") and (
            len(s) == 1 or s[1] == " "
        )
        if is_heading:
            if out and out[-1].strip() != "":
                out.append("")
                changed = True
            out.append(line)
            if i + 1 < len(lines) and lines[i + 1].strip() != "":
                out.append("")
                changed = True
        else:
            out.append(line)
    return NL.join(out), changed


def fix_md025(text: str) -> tuple[str, bool]:
    """Promote 2nd+ H1 to H2 (MD025 single-title).

    Conservative: only acts if the first heading is already an H1, and the
    duplicate H1 is not the file's first heading.
    """
    lines = text.split(NL)
    out = []
    first_h1_seen = False
    file_has_h1 = False
    changed = False
    # First pass: check if file has an H1
    for line in lines:
        s = line.lstrip()
        if s.startswith("# ") and not s.startswith("## "):
            file_has_h1 = True
            break
    if not file_has_h1:
        return text, False
    # Second pass: promote 2nd+ H1 to H2
    for line in lines:
        s = line.lstrip()
        if s.startswith("# ") and not s.startswith("## "):
            if first_h1_seen:
                indent = line[: len(line) - len(s)]
                out.append(f"{indent}## {s[2:]}")
                changed = True
                continue
            first_h1_seen = True
        out.append(line)
    return NL.join(out), changed


def fix_md026(text: str) -> tuple[str, bool]:
    """Remove trailing punctuation from headings (MD026 no-trailing-punctuation).

    Strips a single trailing `.` `,` `;` or `:` from the heading body only.
    Preserves all other whitespace and content.
    """
    lines = text.split(NL)
    out = []
    changed = False
    for line in lines:
        s = line.rstrip()
        stripped = s.lstrip()
        if not stripped or stripped[0] != "#":
            out.append(line)
            continue
        i = 0
        while i < len(stripped) and stripped[i] == "#":
            i += 1
        if i == 0 or i > 6 or (i < len(stripped) and stripped[i] != " "):
            out.append(line)
            continue
        body = stripped[i + 1 :].rstrip()
        if not body or body[-1] not in ".,;:":
            out.append(line)
            continue
        indent = line[: len(line) - len(s)]
        new_heading = f"{indent}{'#' * i} {body[:-1].rstrip()}"
        out.append(new_heading)
        changed = True
    return NL.join(out), changed


def fix_md029(text: str) -> tuple[str, bool]:
    """Renumber ordered list items sequentially (MD029 ol-prefix)."""
    lines = text.split(NL)
    out = []
    i = 0
    changed = False
    while i < len(lines):
        s = lines[i].lstrip()
        j = 0
        while j < len(s) and s[j].isdigit():
            j += 1
        if 0 < j < len(s) and s[j] == "." and j + 1 < len(s) and s[j + 1] == " ":
            items = []
            k = i
            while k < len(lines):
                ss = lines[k].lstrip()
                jj = 0
                while jj < len(ss) and ss[jj].isdigit():
                    jj += 1
                if 0 < jj < len(ss) and ss[jj] == "." and jj + 1 < len(ss) and ss[jj + 1] == " ":
                    items.append(lines[k])
                    k += 1
                elif ss == "":
                    m = k + 1
                    while m < len(lines) and lines[m].strip() == "":
                        m += 1
                    if m < len(lines):
                        sss = lines[m].lstrip()
                        mm = 0
                        while mm < len(sss) and sss[mm].isdigit():
                            mm += 1
                        if 0 < mm < len(sss) and sss[mm] == "." and mm + 1 < len(sss) and sss[mm + 1] == " ":
                            items.append(lines[k])
                            k += 1
                            continue
                    break
                else:
                    break
            indent = lines[i][: len(lines[i]) - len(s)]
            for n, item in enumerate(items, start=1):
                ss = item.lstrip()
                jj = 0
                while jj < len(ss) and ss[jj].isdigit():
                    jj += 1
                new_line = f"{indent}{n}.{ss[jj:]}"
                if new_line != item:
                    changed = True
                out.append(new_line)
            i = k
        else:
            out.append(lines[i])
            i += 1
    return NL.join(out), changed


def fix_md031(text: str) -> tuple[str, bool]:
    """Insert blank lines around fenced code blocks (MD031 blanks-around-fences).

    Tracks ``in_fence`` state via per-line BT (`` ``` ``) toggling. For each
    OPEN-fence line, ensures a blank line precedes it (unless ``out`` is
    empty — no phantom blank line at file start). For each CLOSE-fence line,
    ensures a blank line follows it (unless this fence is the last
    non-empty content — no phantom blank line at file end). Fence interiors
    are emitted verbatim and untouched (this rule does not modify code-block
    contents).

    Idempotent: fences already surrounded by blank lines pass through
    unchanged. Pairs with the fence-tracking guards in
    ``fix_md032`` / ``fix_md013`` / ``fix_md040``.
    """
    lines = text.split(NL)

    # Pre-pass: identify fence open/close indices by toggling ``in_fence``
    # on every ``BT``-prefixed line. Nested fences are not supported by
    # Markdown and not specially handled.
    fence_starts: set[int] = set()
    fence_ends: set[int] = set()
    in_fence = False
    for i, line in enumerate(lines):
        if line.strip().startswith(BT):
            if not in_fence:
                fence_starts.add(i)
            else:
                fence_ends.add(i)
            in_fence = not in_fence

    if not fence_starts and not fence_ends:
        return text, False

    out: list[str] = []
    changed = False
    for i, line in enumerate(lines):
        # Before an opening fence line: insert blank iff the most recent
        # emitted line is non-blank AND we already have content. This
        # avoids inserting a phantom blank at the very start of the file.
        if i in fence_starts and out and out[-1].strip() != "":
            out.append("")
            changed = True
        out.append(line)
        # After a closing fence line: insert blank iff the next input line
        # is non-blank (meaning content resumes right after the fence).
        # The boundary at end-of-file is naturally handled by the index
        # check (no phantom blank at end).
        if (
            i in fence_ends
            and i + 1 < len(lines)
            and lines[i + 1].strip() != ""
        ):
            out.append("")
            changed = True
    return NL.join(out), changed


def fix_md032(text: str) -> tuple[str, bool]:
    """Insert blank lines around list blocks (MD032 blanks-around-lists).

    Ensures that a contiguous run of list items is preceded and followed by
    at least one blank line, separating it from preceding/following prose.
    Idempotent: already-separated documents pass through unchanged.

    Tracks ``in_fence`` (`` ``` ``) and ``in_table`` (`` | ... | ``) state so
    code blocks and tables that happen to contain list-shaped lines are NOT
    falsely promoted into list blocks with surrounding blank lines. This
    mirrors the ``fix_md013`` / ``fix_md040`` fence-tracking pattern. Without
    this guard, YAML or shell code fences whose contents are ``- step1`` /
    ``- step2`` lines would have break-the-fence blank lines injected
    around them (a real regression that previously broke `````fenced code
    blocks in the L99 cleanup pass).
    """
    lines = text.split(NL)
    out: list[str] = []
    changed = False
    in_list = False
    in_fence = False
    in_table = False

    def _is_list_item(s: str) -> bool:
        if not s:
            return False
        if s.startswith(("- ", "* ", "+ ")):
            return True
        if len(s) >= 3 and s[0].isdigit() and s[1] in ".)" and s[2] == " ":
            return True
        return False

    def _is_table_row(s: str) -> bool:
        """A markdown table row (``| ... |`` with >= 2 pipes)."""
        return bool(s) and s.startswith("|") and s.endswith("|") and s.count("|") >= 2

    for line in lines:
        s = line.strip()
        # Fence marker lines (``BT`` open/close) are always handled as
        # fence-only — they toggle ``in_fence`` and are emitted verbatim.
        # Critically, the BT line itself must not fall through to list
        # detection, because that would let ``in_list=True`` falsely insert
        # ``out.append(\"\")`` BEFORE the closing fence and break the fence
        # rendering (the regression the L99 cleanup pass needed this guard
        # for). Fence markers also break table continuity.
        if s.startswith(BT):
            in_fence = not in_fence
            in_table = False
            out.append(line)
            continue

        # Inside a fence (post-marker), do not interpret lines as belonging
        # to a list block. A YAML/shell code fence containing ``- step1``
        # / ``- step2`` lines must NOT be wrapped in blank lines around
        # the fence. Emit the line verbatim and continue.
        if in_fence:
            out.append(line)
            continue

        if _is_table_row(s):
            in_table = True
        elif in_table:
            # Any non-table-row line ends the current table-row span.
            in_table = False

        if in_table:
            # Inside a table, list detection is suppressed — a cell whose
            # content starts with ``- step`` should not be promoted into
            # a list item. Emit the line verbatim and continue.
            out.append(line)
            continue

        is_list = _is_list_item(s)
        if is_list and not in_list:
            # Entering a list block from prose/heading. Ensure blank before.
            if out and out[-1].strip() != "":
                out.append("")
                changed = True
            in_list = True
        elif not is_list and in_list and s != "":
            # Leaving list block into prose. Ensure blank between list and prose.
            if out and out[-1].strip() != "":
                out.append("")
                changed = True
            in_list = False
        elif not is_list and in_list and s == "":
            # Blank line that ends the current list block (next non-blank may
            # either resume list for a loose-list, or be prose).
            in_list = False
        out.append(line)
    return NL.join(out), changed


def fix_md033(text: str) -> tuple[str, bool]:
    """Replace inline HTML with markdown equivalents (MD033 no-inline-html)."""
    changed = False
    new = text
    if "<p>" in new and "</p>" in new:
        new = new.replace("<p>", "").replace("</p>", "")
        changed = True
    for tag in ("<br>", "<br/>", "<br />"):
        if tag in new:
            new = new.replace(tag, "  " + NL)
            changed = True
    return new, changed


def fix_md036(text: str) -> tuple[str, bool]:
    """Convert emphasis-as-heading to proper ATX heading (MD036 no-emphasis-as-heading).

    Tracks the most recent heading level so the promoted heading is one level
    deeper than its parent. A bare `**text**` with no parent becomes `## `.
    """
    lines = text.split(NL)
    out = []
    changed = False
    last_heading_level = 0  # 0 = before any heading (treat as H1 parent)
    for line in lines:
        s = line.strip()
        # Skip list items, tables, blockquotes
        if not s or s.startswith(("- ", "* ", "+ ", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "0.", "|", ">")):
            out.append(line)
            continue
        # Track actual heading levels
        if s.startswith("#") and not s.startswith("#!"):
            i = 0
            while i < len(s) and s[i] == "#":
                i += 1
            if 1 <= i <= 6 and (i == len(s) or s[i] == " "):
                last_heading_level = i
                out.append(line)
                continue
        # Check for emphasis-as-heading
        is_emphasis = False
        body = ""
        if s.startswith("**") and s.endswith("**") and len(s) > 4:
            inner = s[2:-2]
            if "**" not in inner:
                is_emphasis = True
                body = inner.strip()
        elif s.startswith("__") and s.endswith("__") and len(s) > 4:
            inner = s[2:-2]
            if "__" not in inner:
                is_emphasis = True
                body = inner.strip()
        if is_emphasis and body:
            indent = line[: len(line) - len(s)]
            target_level = min(last_heading_level + 1, 6) if last_heading_level >= 1 else 2
            if target_level == 0:
                target_level = 2
            out.append(f"{indent}{'#' * target_level} {body}")
            last_heading_level = target_level  # Update so subsequent emphasis nests
            changed = True
            continue
        out.append(line)
    return NL.join(out), changed


def fix_md040(text: str) -> tuple[str, bool]:
    """Add language tag to un-languaged opening fences (MD040 fenced-code-language)."""
    lines = text.split(NL)
    out = []
    in_fence = False
    changed = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(BT):
            fence = s[3:].strip()
            if not in_fence:
                if not fence:
                    j = i + 1
                    content_lines = []
                    while j < len(lines) and not lines[j].strip().startswith(BT):
                        content_lines.append(lines[j])
                        j += 1
                    content = NL.join(content_lines)
                    lang = _detect_language(content)
                    indent = line[: len(line) - len(s)]
                    out.append(f"{indent}{BT}{lang}")
                    changed = True
                else:
                    out.append(line)
                in_fence = True
            else:
                out.append(line)
                in_fence = False
        else:
            out.append(line)
    return NL.join(out), changed


def _detect_language(content: str) -> str:
    """Best-effort language detection for a fenced code block."""
    first = content.strip().split(NL, 1)[0] if content.strip() else ""
    if first.startswith(("import ", "from ", "def ", "class ")) or "def " in first or "import " in first:
        return "python"
    if first.startswith("$") or first.startswith(("npx ", "pytest", "git ", "pip ", "python ", "cd ", "mkdir ")):
        return "bash"
    if first.startswith("```"):
        return "text"
    if any(kw in content for kw in ("def ", "import ", "class ")):
        return "python"
    if any(kw in content for kw in ("$ ", "npx ", "pytest")):
        return "bash"
    return "text"


def fix_md056(text: str) -> tuple[str, bool]:
    """Fix table rows with more cells than header (MD056 table-column-count)."""
    lines = text.split(NL)
    out = []
    changed = False
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 3:
            header = s
            if i + 1 < len(lines):
                sep = lines[i + 1].strip()
                if sep.startswith("|") and sep.endswith("|") and "---" in sep:
                    header_count = s.count("|") - 1
                    sep_count = sep.count("|") - 1
                    if header_count == sep_count:
                        out.append(lines[i])
                        out.append(lines[i + 1])
                        j = i + 2
                        while j < len(lines):
                            row = lines[j]
                            rs = row.strip()
                            if rs.startswith("|") and rs.endswith("|") and rs.count("|") >= 3:
                                row_count = rs.count("|") - 1
                                if row_count > header_count:
                                    indent = row[: len(row) - len(rs)]
                                    cells = [c.strip() for c in rs[1:-1].split("|")]
                                    merged = cells[: header_count - 1] + [" / ".join(cells[header_count - 1 :])]
                                    out.append(indent + "| " + " | ".join(merged) + " |")
                                    changed = True
                                else:
                                    out.append(row)
                                j += 1
                            elif rs == "":
                                out.append(row)
                                j += 1
                            else:
                                break
                        i = j
                        continue
        out.append(line)
        i += 1
    return NL.join(out), changed


def fix_md060(text: str) -> tuple[str, bool]:
    """Add spaces around pipes in compact tables (MD060 table-column-style)."""
    lines = text.split(NL)
    out = []
    changed = False
    for line in lines:
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            indent = line[: len(line) - len(s)]
            cells = s[1:-1].split("|")
            new_cells = []
            for c in cells:
                c_stripped = c.strip()
                if not c_stripped:
                    new_cells.append("")  # Empty cell (handles `||`)
                else:
                    new_cells.append(" " + c_stripped + " ")
            new_line = indent + "|" + "|".join(new_cells) + "|"
            if new_line != line:
                changed = True
            out.append(new_line)
        else:
            out.append(line)
    return NL.join(out), changed


def fix_md013(text: str, max_length: int = 120) -> tuple[str, bool]:
    """Wrap long lines at natural break points (MD013 line-length)."""
    lines = text.split(NL)
    out = []
    changed = False
    in_fence = False
    in_table = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(BT):
            in_fence = not in_fence
            in_table = False
        elif s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            in_table = True
        elif in_table and not s.startswith("|"):
            in_table = False
        if in_fence or in_table or len(line) <= max_length or not s:
            out.append(line)
            continue
        if s.startswith(("#", ">", "-", "*", "+", "|")) or (s[0].isdigit() and len(s) > 1 and s[1] == "."):
            out.append(line)
            continue
        wrapped = _wrap_line(line, max_length)
        if wrapped != line:
            changed = True
        # Re-split wrapped result so continuation lines are re-checked by the
        # outer loop's length guard (idempotency: prevents >2x max_length lines)
        for sub in wrapped.split(NL):
            if len(sub) > max_length:
                # Still too long: recursive wrap on the over-length sub-line
                sub_wrapped = _wrap_line(sub, max_length)
                if sub_wrapped != sub:
                    changed = True
                out.extend(sub_wrapped.split(NL))
            else:
                out.append(sub)
    return NL.join(out), changed


def _wrap_line(line: str, max_length: int) -> str:
    """Wrap a single line at the nearest natural break point <= max_length.

    Keeps the break pattern at the end of the first line and drops it from the
    start of the second line. Inserts NL between.
    """
    if len(line) <= max_length:
        return line
    best_break = -1
    best_pattern = ""
    for pat in BREAK_PATTERNS:
        idx = line.rfind(pat, 0, max_length)
        if idx > best_break:
            best_break = idx
            best_pattern = pat
    if best_break <= 0:
        return line  # No good break point; leave as-is
    return line[: best_break + len(best_pattern)] + NL + line[best_break + len(best_pattern) :]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# Order matters: run mechanical fixes first, then structural ones.
# fix_md041 is intentionally NOT included (conservative no-op by design;
# markdownlint will report MD041 for manual review).
def _default_fixes() -> list[Callable[[str], tuple[str, bool]]]:
    """Return the default list of fix functions, in execution order.

    Execution-order rationale:
    1. ``fix_md009`` (line.rstrip) cleans trailing whitespace first.
    2. ``fix_md012`` (CRLF-aware blank-line collapse) cleans line stream.
    3. ``fix_md018`` (heading space) precedes ``fix_md022`` (heading
       blanks) so heading detection sees consistent ``# `` syntax.
    4. ``fix_md004`` (bullet-marker normalization) precedes ``fix_md007``
       (list indent snap) so the latter sees only ``- `` markers.
    5. ``fix_md032`` (blank-around-lists) runs after the list-related
       passes plus fix_md012 so list-block boundaries are unambiguous.
    """
    return [
        fix_md003,
        fix_md009,
        fix_md012,
        fix_md018,
        fix_md004,
        fix_md007,
        fix_md022,
        fix_md025,
        fix_md026,
        fix_md029,
        fix_md031,
        fix_md032,
        fix_md033,
        fix_md036,
        fix_md040,
        fix_md056,
        fix_md060,
        partial(fix_md013, max_length=120),
    ]


def fix_file(
    path: Path,
    fix_fns: list[Callable[[str], tuple[str, bool]]] | None = None,
    verbose: bool = False,
) -> tuple[bool, str, list[str]]:
    """Apply all fixes to a file. Returns (changed, new_content, fixes_applied)."""
    if fix_fns is None:
        fix_fns = _default_fixes()
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, "", [f"ERROR reading: {e}"]

    current = original
    applied: list[str] = []

    for fn in fix_fns:
        new, changed = fn(current)
        if changed:
            applied.append(fn.__name__ if hasattr(fn, "__name__") else str(fn))
            current = new

    changed = current != original
    if verbose and applied:
        original_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()[:8]
        new_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()[:8]
        print(f"  [{path.name}] {len(applied)} fixes: {', '.join(applied)}")
        print(f"  [{path.name}] hash: {original_hash} -> {new_hash}")

    return changed, current, applied


def process_files(
    files: list[Path],
    check: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
    no_demote_h1: bool = False,
) -> int:
    """Process a list of files. Returns exit code (0 = success, 1 = changes needed/in errors)."""
    fix_fns = _default_fixes()
    if no_demote_h1:
        fix_fns = [f for f in fix_fns if f is not fix_md025]
    exit_code = 0
    for path in files:
        if not path.exists():
            print(f"  NOT FOUND: {path}", file=sys.stderr)
            exit_code = 1
            continue
        if not path.is_file():
            print(f"  SKIP (not a file): {path}", file=sys.stderr)
            continue

        changed, new_content, fixes = fix_file(path, fix_fns=fix_fns, verbose=verbose)
        # fix_file signals read failures via fixes=["ERROR reading: <reason>"]
        # combined with changed=False. Surface those to stderr (instead of
        # silently printing "CLEAN") and bump the exit code so operators can
        # see what went wrong. This guards against binary or non-UTF-8 inputs
        # crashing the script or hiding the failure.
        if fixes and fixes[0].startswith("ERROR reading:"):
            print(f"  {fixes[0]}: {path}", file=sys.stderr)
            exit_code = 1
            continue
        if not changed:
            print(f"  CLEAN: {path}")
            continue

        if check or dry_run:
            print(f"  WOULD FIX ({len(fixes)}): {path} — {', '.join(fixes)}")
            exit_code = 1
        else:
            try:
                path.write_text(new_content, encoding="utf-8")
                print(f"  FIXED ({len(fixes)}): {path}")
            except (OSError, UnicodeDecodeError) as e:
                print(f"  ERROR writing {path}: {e}", file=sys.stderr)
                exit_code = 1
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Idempotent markdownlint fix script for KokertechAI docs.",
    )
    parser.add_argument("files", nargs="+", type=Path, help="Markdown files to fix")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what would change, don't modify. Alias for --dry-run. Returns exit 1 if changes needed.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Alias for --check.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--no-demote-h1",
        action="store_true",
        help="Skip fix_md025 (don't promote 2nd+ H1 to H2). Use for files with intentional multi-H1 structure.",
    )
    args = parser.parse_args()

    return process_files(
        args.files,
        check=args.check or args.dry_run,
        dry_run=args.dry_run,
        verbose=args.verbose,
        no_demote_h1=args.no_demote_h1,
    )


if __name__ == "__main__":
    sys.exit(main())
