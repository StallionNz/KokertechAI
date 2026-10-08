"""cli.formatting — Rich console output helpers for KokertechAI CLI (50+ functions)."""

import logging
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Ensure UTF-8 output on Windows consoles to prevent UnicodeEncodeError
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as e:
        # Cosmetic console setup — never block import/startup over it.
        logger.debug(f"Console UTF-8 reconfigure skipped: {e}")

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.tree import Tree

# ── Theme management ──

_PRESET_THEMES = {
    "dark": Theme({
        "system": "dim blue", "thinking": "dim italic", "final": "bold white",
        "error": "bold red", "success": "green", "user": "cyan",
        "highlight": "magenta", "model": "yellow", "timestamp": "dim",
    }),
    "light": Theme({
        "system": "dim blue", "thinking": "dim italic", "final": "bold black",
        "error": "bold red", "success": "green", "user": "cyan",
        "highlight": "magenta", "model": "dark_orange", "timestamp": "dim",
    }),
    "monokai": Theme({
        "system": "#a6e22e", "thinking": "#75715e italic", "final": "#f8f8f2",
        "error": "#f92672 bold", "success": "#a6e22e", "user": "#66d9ef",
        "highlight": "#ae81ff", "model": "#e6db74", "timestamp": "#75715e",
    }),
    "one-dark": Theme({
        "system": "#61afef", "thinking": "#5c6370 italic", "final": "#abb2bf",
        "error": "#e06c75 bold", "success": "#98c379", "user": "#56b6c2",
        "highlight": "#c678dd", "model": "#e5c07b", "timestamp": "#5c6370",
    }),
    "dracula": Theme({
        "system": "#6272a4", "thinking": "#44475a italic", "final": "#f8f8f2",
        "error": "#ff5555 bold", "success": "#50fa7b", "user": "#8be9fd",
        "highlight": "#bd93f9", "model": "#f1fa8c", "timestamp": "#6272a4",
    }),
}

_current_theme = "dark"
console = Console(theme=_PRESET_THEMES["dark"], highlight=False)


def switch_theme(name: str) -> None:
    global console, _current_theme
    theme = _PRESET_THEMES.get(name, _PRESET_THEMES["dark"])
    console = Console(theme=theme, highlight=False)
    _current_theme = name


# ── Basic output ──

def print_system(msg: str) -> None:
    console.print(f"[system]{msg}[/system]")


def print_error(msg: str) -> None:
    console.print(f"[error]ERROR: {msg}[/error]")


def print_success(msg: str) -> None:
    console.print(f"[success]✓ {msg}[/success]")


def print_user(msg: str, turn_num: int = 0) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    num_part = f"[timestamp]{turn_num}[/timestamp] " if turn_num else ""
    console.print(f"\n{num_part}[timestamp][{ts}][/timestamp] [user]You:[/user] {msg}")


def print_thinking(text: str, animated: bool = False) -> None:
    if not text.strip():
        return
    if animated:
        with console.status("[thinking]Thinking...[/thinking]", spinner="dots"):
            pass  # spinner only, text displayed after in print_response
    panel = Panel(
        Text(text, style="thinking"),
        title="Thinking",
        border_style="dim",
        padding=(0, 1),
    )
    console.print(panel)


def print_response(text: str) -> None:
    if not text.strip():
        return
    # Detect code blocks and syntax-highlight them
    code_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    parts = code_pattern.split(text)
    if len(parts) > 1:
        for i, part in enumerate(parts):
            if i == 0:
                _print_md_or_text(part)
            elif i % 3 == 1:  # language
                lang = part or "text"
            elif i % 3 == 2:  # code
                print_code_block(part, lang)
    else:
        _print_md_or_text(text)


def _print_md_or_text(text: str) -> None:
    try:
        md = Markdown(text, code_theme="monokai")
        console.print(md)
    except Exception:
        console.print(Text(text, style="final"))


def print_code_block(code: str, language: str = "") -> None:
    """Print a fenced code block with syntax highlighting and copy hint."""
    try:
        syntax = Syntax(code, language or "text", theme="monokai",
                       line_numbers=False, word_wrap=True)
        panel = Panel(syntax, title=f"[timestamp][📋 {language or 'code'}][/timestamp]",
                     border_style="dim", padding=(0, 1))
        console.print(panel)
    except Exception:
        console.print(Text(code, style="final"))


def print_json_pretty(data) -> None:
    """Pretty-print JSON with syntax highlighting."""
    import json as _json
    text = _json.dumps(data, indent=2, ensure_ascii=False) if not isinstance(data, str) else data
    try:
        syntax = Syntax(text, "json", theme="monokai", line_numbers=False)
        console.print(Panel(syntax, title="JSON", border_style="dim"))
    except Exception:
        console.print(Text(text, style="final"))


# ── Streaming ──

def print_streaming_header(turn_num: int = 0) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    num_part = f"[timestamp]{turn_num}[/timestamp] " if turn_num else ""
    console.print(f"\n{num_part}[timestamp][{ts}][/timestamp] [model]AI:[/model] ", end="")


def print_stream_token(token: str) -> None:
    console.print(token, end="", style="final")


def print_typing_indicator(duration_ms: int = 800) -> None:
    """Show a brief typing indicator before streaming starts."""
    frames = ["·  ", "·· ", "···"]
    import time
    for frame in frames:
        console.print(f"\r[thinking]{frame}[/thinking]", end="")
        time.sleep(duration_ms / 1000 / len(frames))
    console.print("\r", end="")


# ── Banner ──

ASCII_LOGO = r"""
 _  __     _             _     _     ___    ___   _
| |/ /___ | | _____ _ __| |_  | |_  / _ \  |_ _| | |
| ' // _ \| |/ / _ \ '__| __| | __|| | | |  | |  | |
| . \ (_) |   <  __/ |  | |_  | |_ | |_| |  | |  |_|
|_|\_\___/|_|\_\___|_|   \__|  \__| \___/  |___| (_)
"""


def print_banner() -> None:
    console.print(Text(ASCII_LOGO, style="bold highlight", justify="center"))
    banner = Panel(
        Text("KokertechAI CLI v2 — Sprint 15", style="bold white", justify="center"),
        subtitle=f"[timestamp]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/timestamp]",
        border_style="highlight", padding=(0, 2),
    )
    console.print(banner)
    console.print("[system]Type /help for commands, /exit to quit.[/system]\n")


# ── Help ──

def print_help() -> None:
    table = Table(title="CLI Commands", border_style="dim", show_header=True)
    table.add_column("Command", style="highlight", no_wrap=True)
    table.add_column("Description", style="system")

    commands = [
        ("----- Conversation -----", ""),
        ("/undo", "Remove last user+assistant turn"), ("/retry [N]", "Re-submit last prompt"),
        ("/repeat", "Replay last AI response"), ("/system <msg>", "Inject one-shot system message"),
        ("/pin <N>", "Pin message N to context"), ("/unpin", "Remove all pinned messages"),
        ("/clear", "Clear chat history"),
        ("----- Model & Provider -----", ""),
        ("/model [list|swap]", "Model info, list, or hot-swap"), ("/health", "Full provider health check"),
        ("/vram", "Quick VRAM usage"), ("/temp <0-2>", "Set temperature"),
        ("/ctx <tokens>", "Resize context window"), ("/reset", "Full reset: clear + reload"),
        ("----- Search & Diagnostics -----", ""),
        ("/search <pat>", "Search codebase with ripgrep"), ("/diff [--staged]", "Git diff viewer"),
        ("/log [tail|search]", "Browse execution log"), ("/audit", "Cognitive auditor on last response"),
        ("/eval <expr>", "Evaluate Python expression"), ("/test <pat>", "Run pytest tests"),
        ("/sql <query>", "Read-only SQL on vault.db"), ("/graph [query]", "Knowledge graph query"),
        ("----- External Context -----", ""),
        ("/rag <query>", "Deep research via Agentic RAG"), ("/web <url>", "Fetch URL as context"),
        ("/scan <dir>", "Scan directory tree as context"), ("/pipe <cmd>", "Run shell, pipe to AI"),
        ("/multi <a> <b> <p>", "A/B model comparison"),
        ("----- Sessions -----", ""),
        ("/save [--tag x] [name]", "Save session"), ("/load [--merge] <name>", "Load or merge session"),
        ("/sessions [search|delete|rename|peek|archive|copy]", "Manage sessions"),
        ("/fork <name>", "Fork to new tab"), ("/tab [list|switch|close]", "Session tab management"),
        ("----- Customization -----", ""),
        ("/persona [name]", "Switch or list personas"), ("/alias <n> <cmd>", "Create command alias"),
        ("/theme <name>", "Switch theme"), ("/prompt <style>", "Change prompt style"),
        ("/thinking on|off", "Toggle thinking display"), ("/compact", "Toggle compact mode"),
        ("/silent on|off", "Toggle silent mode"),
        ("----- Coding Agent -----", ""),
        ("/code [task|search|test|diff|ast|files|undo]", "Autonomous CLI Coding Agent"),
        ("----- Utilities -----", ""),
        ("/timer <s> <msg>", "Set countdown timer"), ("/notify <msg>", "Windows toast notification"),
        ("/tts on|off|speak", "Text-to-speech control"), ("/snapshot [name]", "Save memory snapshot"),
        ("/restore <name>", "Restore memory snapshot"), ("/export [md|json]", "Export conversation"),
        ("/workflow [name]", "Run YAML workflow"), ("/changelog", "Show recent changelog"),
        ("/stats", "Session statistics"), ("/config <k> [v]", "Read/write CONFIG"),
        ("/plugin list", "List plugins"), ("/help", "This message"),
        ("/exit, /quit", "Exit the CLI"),
    ]
    for cmd, desc in commands:
        if cmd.startswith("-----"):
            table.add_section()
            table.add_row(Text(cmd, style="bold highlight"), "")
        else:
            table.add_row(cmd, desc)
    console.print(table)


# ── Model info ──

def print_model_info(result: dict) -> None:
    table = Table(title="Model Info", border_style="dim")
    table.add_column("Property", style="highlight")
    table.add_column("Value", style="system")
    table.add_row("Provider", result.get("provider", "?"))
    table.add_row("Model", result.get("model_name", "?"))
    table.add_row("Loaded", "✓" if result.get("model_loaded") else "✗")
    if result.get("model_loaded"):
        table.add_row("Context window", str(result.get("n_ctx", "?")))
        table.add_row("GPU layers", str(result.get("n_gpu_layers", "?")))
        table.add_row("VRAM used", f"{result.get('vram_used_mb', 0)} MB")
        table.add_row("Latency (1 token)", f"{result.get('latency_ms', 0)} ms")
    if result.get("error"):
        table.add_row("Error", f"[error]{result['error']}[/error]")
    console.print(table)


def print_vram_only() -> None:
    try:
        cmd = ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"]
        cf = 0x08000000 if os.name == "nt" else 0
        output = subprocess.check_output(cmd, timeout=5, text=True, creationflags=cf)
        parts = output.strip().split(",")
        if len(parts) >= 2:
            used, total = int(parts[0].strip()), int(parts[1].strip())
            pct = used / total * 100 if total else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            console.print(f"[system]VRAM: [{bar}] {used}/{total} MB ({pct:.0f}%)[/system]")
            return
    except Exception as e:
        # Read fallback (hot path) — nvidia-smi probe failure degrades to the
        # "unavailable" line below; debug-level per §13 convention.
        logger.debug(f"nvidia-smi VRAM probe failed: {e}")
    console.print("[system]VRAM: unavailable (nvidia-smi not found)[/system]")


def print_model_list(models: list) -> None:
    if not models:
        console.print("[system]No .gguf models found.[/system]")
        return
    table = Table(title="Available Models", border_style="dim")
    table.add_column("Name", style="highlight")
    table.add_column("Size", style="system")
    table.add_column("Directory", style="dim")
    for m in models:
        size_mb = m.get("size_bytes", 0) / (1024 * 1024)
        table.add_row(m.get("name", "?"), f"{size_mb:.0f} MB", m.get("dir", ""))
    console.print(table)


# ── Sessions ──

def print_sessions(sessions: list) -> None:
    if not sessions:
        console.print("[system]No saved sessions found.[/system]")
        return
    table = Table(title="Saved Sessions", border_style="dim")
    table.add_column("", style="success", width=1)
    table.add_column("Name", style="highlight")
    table.add_column("Msgs", style="system")
    table.add_column("Words", style="dim")
    table.add_column("Tags", style="dim")
    table.add_column("Updated", style="dim")
    for s in sessions:
        fav = "★" if s.get("favorite") else ""
        tags = ", ".join(s.get("tags", [])) if s.get("tags") else "-"
        table.add_row(
            fav, s.get("name", "?"), str(s.get("message_count", 0)),
            str(s.get("word_count", 0)), tags, s.get("updated", "?"),
        )
    console.print(table)


# ── Memory / Search results ──

def print_memory_results(results: list) -> None:
    if not results:
        console.print("[system]No matching memories found.[/system]")
        return
    console.print(f"[system]🔍 Found {len(results)} memories:[/system]")
    for i, r in enumerate(results[:10], 1):
        content = r[2] if len(r) > 2 else str(r)
        preview = content[:200] + "..." if len(content) > 200 else content
        console.print(f"  [highlight]{i}.[/highlight] [system]{preview}[/system]")


def print_search_results(results: list, pattern: str) -> None:
    if not results:
        console.print(f"[system]No matches for '{pattern}'.[/system]")
        return
    console.print(f"[system]🔎 Found {len(results)} matches for '{pattern}':[/system]")
    for r in results[:20]:
        file_path = r.get("file", "?")
        line_no = r.get("line", 0)
        text = r.get("text", "").strip()
        # File-path hyperlinking
        console.print(
            f"  [highlight]{file_path}[/highlight]:"
            f"[timestamp]{line_no}[/timestamp] [system]{text[:120]}[/system]")


def print_diff_view(diff_text: str) -> None:
    if not diff_text.strip():
        console.print("[system]No changes (working tree clean).[/system]")
        return
    try:
        syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
        panel = Panel(syntax, title="📋 Git Diff", border_style="dim")
        console.print(panel)
    except Exception:
        console.print(Text(diff_text[:2000], style="system"))


# ── Token footer ──

def print_token_footer(tokens_in: int = 0, tokens_out: int = 0,
                       elapsed_ms: int = 0, model: str = "") -> None:
    parts = []
    if model:
        parts.append(f"model: {model}")
    if tokens_out:
        parts.append(f"{tokens_out} words out")
        if elapsed_ms > 0 and tokens_out > 0:
            wps = tokens_out / (elapsed_ms / 1000)
            parts.append(f"{elapsed_ms / 1000:.1f}s")
            parts.append(f"{wps:.0f} w/s")
    if tokens_in:
        parts.append(f"{tokens_in} words in")
    if parts:
        console.print(f"[timestamp]── {' · '.join(parts)}[/timestamp]")


# ── Context gauge ──

def print_context_gauge(used_tokens: int, max_tokens: int) -> None:
    if max_tokens <= 0:
        return
    pct = min(used_tokens / max_tokens * 100, 100)
    bar_len = 20
    filled = int(pct / 5)
    bar = "█" * filled + "░" * (bar_len - filled)
    console.print(
        f"[timestamp]ctx: [{bar}] {used_tokens}/{max_tokens} tokens ({pct:.0f}%)[/timestamp]")


# ── Audit report ──

def print_audit_report(report) -> None:
    """Print cognitive auditor results."""
    panel = Panel(
        Text(str(report)[:2000], style="system"),
        title="🧠 Cognitive Audit",
        border_style="highlight",
        padding=(0, 1),
    )
    console.print(panel)


# ── Log entries ──

def print_log_entries(lines: list, label: str) -> None:
    if not lines:
        console.print(f"[system]No log entries for '{label}'.[/system]")
        return
    console.print(f"[system]📋 Log ({label}, {len(lines)} lines):[/system]")
    for line in lines[:30]:
        console.print(f"  [timestamp]{line.rstrip()[:150]}[/timestamp]")
    if len(lines) > 30:
        console.print(f"  [timestamp]... and {len(lines) - 30} more lines[/timestamp]")


# ── Test results ──

def print_test_results(output: str, pattern: str) -> None:
    # Verbose pytest (-v, used by /test) prints per-test PASSED/FAILED/ERROR lines.
    # Quiet pytest (-q, used by /code test) prints only a summary line instead,
    # e.g. "23 passed in 5.71s" — so prefer summary counts when they are present.
    summary_passed = re.findall(r"(\d+) passed", output)
    summary_failed = re.findall(r"(\d+) failed", output)
    summary_errors = re.findall(r"(\d+) error", output)
    if summary_passed or summary_failed or summary_errors:
        passed = sum(int(n) for n in summary_passed)
        failed = sum(int(n) for n in summary_failed)
        errors = sum(int(n) for n in summary_errors)
    else:
        passed = len(re.findall(r"PASSED", output))
        failed = len(re.findall(r"FAILED", output))
        errors = len(re.findall(r"ERROR", output))
    console.print(f"[system]🧪 Test run for '{pattern}':[/system]")
    console.print(f"  [success]{passed} passed[/success]  "
                  f"[error]{failed} failed[/error]  "
                  f"[highlight]{errors} errors[/highlight]")
    if output.strip():
        console.print(Text(output[:1000], style="dim"))


# ── Web preview ──

def print_web_preview(url: str, text: str) -> None:
    preview = text[:300] + "..." if len(text) > 300 else text
    console.print(f"[system]🌐 {url} ({len(text)} chars):[/system]")
    console.print(f"  [system]{preview}[/system]")


# ── Tree ──

def print_tree(tree_text: str, root: str) -> None:
    console.print(f"[system]📁 {root}:[/system]")
    console.print(f"  [system]{tree_text[:2000]}[/system]")


# ── Progress / RAG ──

def create_rag_progress() -> Progress:
    return Progress(SpinnerColumn(), TextColumn("[system]{task.description}[/system]"),
                    console=console, transient=False)


# ── Persona list ──

def print_persona_list(personas: dict, active: str = "") -> None:
    if not personas:
        console.print("[system]No personas configured.[/system]")
        return
    table = Table(title="Personas", border_style="dim")
    table.add_column("Name", style="highlight")
    table.add_column("Description", style="system")
    table.add_column("", style="success", width=1)
    for name, desc in personas.items():
        marker = "★" if name == active else ""
        desc_text = desc[:80] + "..." if len(desc) > 80 else desc
        table.add_row(name, desc_text, marker)
    console.print(table)


# ── Session stats ──

def print_session_stats(context: dict, controller=None) -> None:
    table = Table(title="Session Stats", border_style="dim")
    table.add_column("Metric", style="highlight")
    table.add_column("Value", style="system")
    history = context.get("chat_history", [])
    table.add_row("Messages", str(len(history)))
    table.add_row("Session name", context.get("session_name", "(unnamed)"))
    user_msgs = sum(1 for m in history if m.get("role") == "user")
    ai_msgs = sum(1 for m in history if m.get("role") == "assistant")
    table.add_row("User messages", str(user_msgs))
    table.add_row("AI responses", str(ai_msgs))
    summary = context.get("compressed_summary", "")
    table.add_row("Context summary", f"{len(summary)} chars" if summary else "(empty)")
    table.add_row("Pinned messages", str(len(context.get("pinned_messages", []))))
    tabs = context.get("tabs", [])
    if tabs:
        table.add_row("Open tabs", str(len(tabs)))
    if controller is not None:
        try:
            model = controller._load_target_model()
            table.add_row("Active model", model or "(unknown)")
        except Exception as e:
            # Read fallback — model-info row is best-effort enrichment; the table
            # still renders with the other rows.
            logger.debug(f"Failed to resolve active model for table: {e}")
    console.print(table)


# ── Export done ──

def print_export_done(path: str, fmt: str, count: int) -> None:
    console.print(f"[success]✓ Exported {count} messages as {fmt} → "
                  f"[highlight]{path}[/highlight][/success]")


# ── Notification toast ──

def print_notification_toast(msg: str, duration: int = 3) -> None:
    panel = Panel(Text(msg, style="bold white"), border_style="success",
                 padding=(0, 2), title="🔔 Notification")
    console.print(panel)


# ── Changelog ──

def print_changelog(lines: list) -> None:
    console.print("[system]📋 Recent Changelog:[/system]")
    for line in lines[:40]:
        line = line.rstrip()
        if line.startswith("## "):
            console.print(f"\n[highlight]{line}[/highlight]")
        elif line.startswith("### "):
            console.print(f"  [model]{line}[/model]")
        elif line.startswith("- "):
            console.print(f"    [system]{line[:140]}[/system]")
    if len(lines) > 40:
        console.print(f"  [timestamp]... ({len(lines) - 40} more lines)[/timestamp]")


# ── Breadcrumb trail ──

def print_breadcrumb(trail: str) -> None:
    console.print(f"[timestamp]{trail}[/timestamp]")


# ── Keybinding bar ──

def print_keybinding_bar() -> None:
    console.print(
        "\n[timestamp]Ctrl+C cancel · ↑↓ history · Tab complete · "
        "/help | /exit | /clear | /undo | /retry[/timestamp]")


# ── Message separator ──

def print_message_separator() -> None:
    console.print(Rule(style="dim"))


# ── Prompt style ──

def print_prompt_style(style: str) -> None:
    examples = {
        "arrow": "[dim]>[/dim] ",
        "minimal": "[dim]·[/dim] ",
        "verbose": "[timestamp]You:[/timestamp] ",
        "none": "",
    }
    preview = examples.get(style, "[dim]>[/dim] ")
    console.print(f"[success]Prompt style: {style} → {preview}[/success]")


# ── Status bar ──

def print_status_bar(context: dict) -> None:
    """Print a persistent status bar at the bottom."""
    model_name = "?"
    try:
        from config import CONFIG
        model_name = os.path.basename(CONFIG.get("model_file", "?"))
    except (ImportError, OSError, ValueError) as e:
        # Read fallback — model_name stays "?" in the status line; non-fatal.
        # Narrowed (v0.22.23): ImportError (config module/name), OSError
        # (settings file IO at config import), ValueError (bad JSON —
        # JSONDecodeError subclasses ValueError). Anything else is a bug.
        logger.debug(f"Failed to read model_file from CONFIG: {e}")
    history = context.get("chat_history", [])
    parts = [
        f"model: {model_name}",
        f"msgs: {len(history)}",
        f"session: {context.get('session_name', '-')}",
        f"thinking: {'on' if context.get('show_thinking', True) else 'off'}",
    ]
    console.print(f"[timestamp]── {' · '.join(parts)}[/timestamp]")


# ── Gradient divider ──

def print_rainbow_divider() -> None:
    colors = ["red", "yellow", "green", "cyan", "blue", "magenta"]
    bar = ""
    for i in range(30):
        bar += f"[{colors[i % len(colors)]}]─[/{colors[i % len(colors)]}]"
    console.print(bar)
