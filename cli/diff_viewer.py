"""cli.diff_viewer — Diff display, summarization, and confirmation before applying code changes.

Sprint 18: CLI Coding Agent Interface (Task 18.4).
"""

from __future__ import annotations

import difflib
import re
import sys
from typing import Any, Dict, List, Optional


def compute_unified_diff(
    old_content: str,
    new_content: str,
    from_file: str = "original",
    to_file: str = "modified",
    context_lines: int = 3,
) -> str:
    """Compute a standard unified diff string between two text strings."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{from_file}",
        tofile=f"b/{to_file}",
        n=context_lines,
    )
    return "".join(diff)


def summarize_diff(diff_text: str) -> Dict[str, Any]:
    """Parse unified diff text and summarize line changes and modified files."""
    if not diff_text or not diff_text.strip():
        return {
            "additions": 0,
            "deletions": 0,
            "files_changed": [],
            "total_hunks": 0,
        }

    additions = 0
    deletions = 0
    files_changed: List[str] = []
    hunks = 0

    file_header_re = re.compile(r"^\+\+\+\s+(?:b/)?([^\s\t\n]+)")
    hunk_header_re = re.compile(r"^@@\s+-\d+.*?\+\d+.*?@@")

    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            m = file_header_re.match(line)
            if m:
                path = m.group(1).strip()
                if path and path != "/dev/null" and path not in files_changed:
                    files_changed.append(path)
        elif line.startswith("--- "):
            continue
        elif hunk_header_re.match(line):
            hunks += 1
        elif line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1

    return {
        "additions": additions,
        "deletions": deletions,
        "files_changed": files_changed,
        "total_hunks": hunks,
    }


def render_diff(
    diff_text: str,
    title: str = "Diff Preview",
    console_override: Optional[Any] = None,
) -> None:
    """Render unified diff with syntax highlighting and summary badge."""
    from cli.formatting import console as default_console
    out_console = console_override or default_console

    if not diff_text or not diff_text.strip():
        out_console.print("[system]No changes to display.[/system]")
        return

    summary = summarize_diff(diff_text)
    add_count = summary["additions"]
    del_count = summary["deletions"]
    files = summary["files_changed"]
    files_str = f" ({len(files)} file{'s' if len(files) != 1 else ''})" if files else ""

    badge = f" [green]+{add_count}[/green] [red]-{del_count}[/red]{files_str}"
    full_title = f"{title}{badge}"

    try:
        from rich.panel import Panel
        from rich.syntax import Syntax
        syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
        panel = Panel(syntax, title=full_title, border_style="dim")
        out_console.print(panel)
    except (ImportError, AttributeError, ValueError, OSError):
        # Fallback to plain print
        out_console.print(f"=== {full_title} ===")
        out_console.print(diff_text[:4000])


def confirm_diff(
    diff_text: str,
    prompt_text: str = "Apply these changes? [y/N]: ",
    default_yes: bool = False,
    auto_confirm: bool = False,
) -> bool:
    """Display diff preview and prompt user for confirmation."""
    render_diff(diff_text)
    if auto_confirm:
        return True

    # If stdin is not an interactive tty, use default
    if not sys.stdin.isatty():
        return default_yes

    try:
        choice = input(f"\n{prompt_text}").strip().lower()
        if not choice:
            return default_yes
        return choice in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False
