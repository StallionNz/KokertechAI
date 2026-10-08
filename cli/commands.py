"""cli.commands — Slash-command handlers for KokertechAI CLI (50 commands)."""

import ast
import json
import logging
import os
import re
import subprocess
import sqlite3
import threading
from datetime import datetime
from typing import Callable

logger = logging.getLogger(__name__)

from cli.formatting import (
    console, print_audit_report, print_breadcrumb, print_changelog,
    print_code_block, print_context_gauge, print_diff_view, print_error,
    print_export_done, print_help, print_json_pretty, print_keybinding_bar,
    print_log_entries, print_model_info, print_model_list, print_memory_results,
    print_message_separator, print_notification_toast, print_persona_list,
    print_prompt_style, print_response, print_search_results, print_session_stats, print_sessions,
    print_success, print_system, print_test_results, print_tree,
    print_vram_only, print_web_preview,
)
from cli.session import (
    SESSIONS_DIR, archive_session, copy_session, delete_session,
    export_sessions_bulk, get_last_session, get_session_duration,
    get_session_word_count, import_session, integrity_check, list_archived,
    list_sessions, load_session, merge_session, rename_session, save_session,
    search_sessions, session_from_template, session_to_template,
    unarchive_session,
)

# ── Dispatch table ──
_HANDLERS: dict[str, Callable] = {}
_ALIASES: dict[str, str] = {
    "c": "/code",
    "/c": "/code",
}


def register(cmd: str):
    def decorator(fn):
        _HANDLERS[cmd] = fn
        return fn
    return decorator


def dispatch(user_input: str, context: dict) -> bool:
    """Dispatch a slash command. Returns True to skip AI processing."""
    text = user_input.strip()
    if not text.startswith("/"):
        match = re.match(r'^<<RAG:(.+)>>$', text, re.IGNORECASE | re.DOTALL)
        if match:
            query = match.group(1).strip()
            print_system(f"Deep research mode: {query[:80]}{'...' if len(query) > 80 else ''}")
            context["pending_input"] = text
            return False
        return False

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    # Resolve aliases
    alias_lookup = cmd if cmd in _ALIASES else cmd.lstrip("/")
    if alias_lookup in _ALIASES:
        expanded = _ALIASES[alias_lookup]
        print_breadcrumb(f"{cmd} → {expanded}")
        cmd = expanded.split(maxsplit=1)[0].lower()
        alias_args = expanded.split(maxsplit=1)[1] if " " in expanded else ""
        args = f"{alias_args} {args}".strip()

    handler = _HANDLERS.get(cmd)
    if handler:
        try:
            return handler(args, context)
        except Exception as e:
            print_error(f"Command '{cmd}' failed: {e}")
            return True

    # Tab-based dispatch
    if cmd.startswith("/tab"):
        return _handle_tab(text, context)

    print_system(f"Unknown command: {cmd}. Type /help for available commands.")
    return True


# ══════════════════════════════════════════════════════════════════════
# Core commands
# ══════════════════════════════════════════════════════════════════════

@register("/help")
def cmd_help(args: str, context: dict) -> bool:
    print_help()
    print_keybinding_bar()
    return True


@register("/rag")
def cmd_rag(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /rag <your research question>")
        return True
    print_system(f"Deep research mode: {args[:80]}{'...' if len(args) > 80 else ''}")
    context["pending_input"] = f"<<RAG:{args}>>"
    return False


@register("/memory")
def cmd_memory(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /memory <search query>")
        return True
    try:
        import memory_vault
        results = memory_vault.hybrid_search(args, top_k=10)
        print_memory_results(results)
    except Exception as e:
        print_error(f"Memory search failed: {e}")
    return True


@register("/model")
def cmd_model(args: str, context: dict) -> bool:
    from ai_base import get_provider
    sub = args.strip().lower()

    if sub == "list":
        try:
            models = get_provider().list_available_models()
            print_model_list(models)
        except Exception as e:
            print_error(f"Failed to list models: {e}")
        return True

    if sub.startswith("swap"):
        parts = args.strip().split(maxsplit=1)
        if len(parts) < 2 or parts[0].lower() != "swap":
            print_error("Usage: /model swap <model_filename>")
            return True
        model_name = parts[1].strip()
        if not model_name:
            print_error("Usage: /model swap <model_filename>")
            return True
        try:
            from config import CONFIG
            result = get_provider().swap_model(model_name)
            if result["ok"]:
                CONFIG["model_file"] = model_name
                try:
                    from config import save_settings
                    save_settings()
                except Exception as e:
                    # Config write failure = settings may not survive restart (data loss).
                    logger.warning(f"Failed to persist model_file after swap: {e}")
                print_success(
                    f"Swapped to {result['new_model']} ({result['load_time_ms']}ms, "
                    f"n_ctx={result['n_ctx']}, n_gpu_layers={result['n_gpu_layers']})")
            else:
                print_error(f"Swap failed: {result.get('error', 'unknown error')}")
                if result.get("rolled_back"):
                    print_system(f"Rolled back to {result['old_model']}")
        except Exception as e:
            print_error(f"Model swap failed: {e}")
        return True

    try:
        print_model_info(get_provider().health_check())
    except Exception as e:
        print_error(f"Health check failed: {e}")
    return True


@register("/health")
def cmd_health(args: str, context: dict) -> bool:
    from ai_base import get_provider
    try:
        print_model_info(get_provider().health_check())
    except Exception as e:
        print_error(f"Health check failed: {e}")
    return True


@register("/vram")
def cmd_vram(args: str, context: dict) -> bool:
    print_vram_only()
    return True


@register("/plugin")
def cmd_plugin(args: str, context: dict) -> bool:
    sub = args.strip().lower()
    try:
        import plugin_registry
        if sub == "list" or not sub:
            console.print(plugin_registry.registry.list_plugins())
        else:
            print_error("Usage: /plugin list")
    except ImportError:
        print_error("Plugin registry not available")
    return True


@register("/clear")
def cmd_clear(args: str, context: dict) -> bool:
    context["chat_history"] = []
    context["compressed_summary"] = ""
    context.pop("pinned_messages", None)
    print_success("Chat history and context cleared.")
    return True


# ══════════════════════════════════════════════════════════════════════
# Conversation control
# ══════════════════════════════════════════════════════════════════════

@register("/undo")
def cmd_undo(args: str, context: dict) -> bool:
    history = context.get("chat_history", [])
    if not history:
        print_error("Nothing to undo.")
        return True
    # Remove last assistant + user turn
    removed = 0
    if history and history[-1].get("role") == "assistant":
        history.pop()
        removed += 1
    if history and history[-1].get("role") == "user":
        history.pop()
        removed += 1
    print_success(f"Undid {removed} message(s). {len(history)} remaining.")
    return True


@register("/retry")
def cmd_retry(args: str, context: dict) -> bool:
    history = context.get("chat_history", [])
    if not history:
        print_error("Nothing to retry.")
        return True

    # Parse optional N (which turn to retry, 0 = last)
    n = 0
    temp_override = None
    for part in args.split():
        if part.startswith("--temp="):
            try:
                temp_override = float(part[7:])
            except ValueError:
                print_error(f"Invalid temperature: {part[7:]}")
                return True
        else:
            try:
                n = int(part)
            except ValueError:
                pass

    # Remove the last assistant response so the last user prompt re-fires
    if history and history[-1].get("role") == "assistant":
        history.pop()
    if history and history[-1].get("role") == "user":
        last_user = history.pop()
        if temp_override is not None:
            from config import CONFIG
            CONFIG["llm_temperature"] = temp_override
            print_system(f"Temperature overridden to {temp_override}")
        # Set pending_input to replay
        context["pending_input"] = last_user.get("content", "")
        print_success("Retrying last prompt...")
        return False  # let AI process
    print_error("No user prompt found to retry.")
    return True


@register("/repeat")
def cmd_repeat(args: str, context: dict) -> bool:
    history = context.get("chat_history", [])
    for m in reversed(history):
        if m.get("role") == "assistant":
            from cli.formatting import print_response
            print_response(m.get("content", ""))
            return True
    print_error("No assistant response found to repeat.")
    return True


@register("/system")
def cmd_system(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /system <one-shot system message>")
        return True
    context["one_shot_system"] = args.strip()
    print_success(f"One-shot system message set ({len(args)} chars). Will be injected next turn.")
    return True


@register("/pin")
def cmd_pin(args: str, context: dict) -> bool:
    history = context.get("chat_history", [])
    try:
        n = int(args.strip()) if args.strip() else 0
    except ValueError:
        print_error("Usage: /pin <message number>")
        return True

    if n < 1 or n > len(history):
        print_error(f"Invalid message number: {n} (1-{len(history)})")
        return True

    pinned = context.setdefault("pinned_messages", [])
    msg = history[n - 1]
    if msg in pinned:
        print_system(f"Message {n} already pinned.")
        return True
    pinned.append(msg)
    preview = msg.get("content", "")[:60]
    print_success(f"Pinned message {n}: {preview}...")
    return True


@register("/unpin")
def cmd_unpin(args: str, context: dict) -> bool:
    if "pinned_messages" in context:
        context.pop("pinned_messages")
        print_success("All pinned messages cleared.")
    else:
        print_system("No pinned messages.")
    return True


# ══════════════════════════════════════════════════════════════════════
# Provider tuning
# ══════════════════════════════════════════════════════════════════════

@register("/temp")
def cmd_temp(args: str, context: dict) -> bool:
    from config import CONFIG
    if not args.strip():
        current = CONFIG.get("llm_temperature", 0.1)
        print_system(f"Temperature: {current}")
        return True
    try:
        t = float(args.strip())
        if t < 0 or t > 2:
            print_error("Temperature must be between 0 and 2")
            return True
        CONFIG["llm_temperature"] = t
        print_success(f"Temperature set to {t}")
    except ValueError:
        print_error("Usage: /temp <0.0-2.0>")
    return True


@register("/ctx")
def cmd_ctx(args: str, context: dict) -> bool:
    from config import CONFIG
    from ai_base import get_provider
    if not args.strip():
        current = CONFIG.get("llm_n_ctx", 4096)
        print_system(f"Context window: {current} tokens")
        return True
    try:
        n = int(args.strip())
        if n < 512:
            print_error("Minimum context window is 512 tokens")
            return True
        CONFIG["llm_n_ctx"] = n
        print_success(f"Context window set to {n} tokens. Reload model to apply (/reset).")
    except ValueError:
        print_error("Usage: /ctx <tokens> (e.g. /ctx 8192)")
    return True


@register("/reset")
def cmd_reset(args: str, context: dict) -> bool:
    """Full hard reset: clear everything and reload provider."""
    context["chat_history"] = []
    context["compressed_summary"] = ""
    context.pop("pinned_messages", None)
    context.pop("_controller", None)
    try:
        from ai_base import reset_providers, get_provider
        reset_providers()
        provider = get_provider()
        provider.unload_model()
        provider._load_model(provider.model_file)
        print_success("Full reset: history cleared, provider reloaded.")
    except Exception as e:
        print_error(f"Reset failed: {e}")
    return True


# ══════════════════════════════════════════════════════════════════════
# Debugging & diagnostics
# ══════════════════════════════════════════════════════════════════════

@register("/log")
def cmd_log(args: str, context: dict) -> bool:
    sub = args.strip()
    log_path = os.path.join(context.get("workspace", "."), "execution_log.txt")

    if sub.startswith("tail"):
        try:
            n = int(sub[4:].strip() or "20")
        except ValueError:
            n = 20
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            print_log_entries(lines[-n:], "tail")
        except FileNotFoundError:
            print_error("execution_log.txt not found")
        return True

    if sub.startswith("search "):
        term = sub[7:].strip()
        if not term:
            print_error("Usage: /log search <term>")
            return True
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = [l for l in f if term.lower() in l.lower()]
            print_log_entries(lines[:30], f"search '{term}'")
        except FileNotFoundError:
            print_error("execution_log.txt not found")
        return True

    if sub == "clear":
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("")
            print_success("Execution log cleared.")
        except OSError as e:
            print_error(f"Failed to clear log: {e}")
        return True

    # Default: tail 20
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        print_log_entries(lines[-20:], "tail 20")
    except FileNotFoundError:
        print_error("execution_log.txt not found")
    return True


@register("/audit")
def cmd_audit(args: str, context: dict) -> bool:
    history = context.get("chat_history", [])
    last_user = ""
    last_ai = ""
    for m in reversed(history):
        if m.get("role") == "assistant" and not last_ai:
            last_ai = m.get("content", "")
        if m.get("role") == "user" and not last_user:
            last_user = m.get("content", "")
        if last_user and last_ai:
            break

    if not last_ai:
        print_error("No AI response to audit.")
        return True

    try:
        from cognitive_auditor import audit_interaction
        report = audit_interaction(last_user, last_ai)
        print_audit_report(report)
    except Exception as e:
        print_error(f"Audit failed: {e}")
    return True


@register("/eval")
def cmd_eval(args: str, context: dict) -> bool:
    """Evaluate a python LITERAL expression (zero-trust fix, Sprint 19.8).

    REGRESSION GUARD for the no-arbitrary-code-execution invariant:
    the previous implementation called ``eval(args, {"os": os, ...})``
    which handed arbitrary code the full ``os`` module (os.system,
    os.remove, ...). ``ast.literal_eval`` parses literals/collections
    only — names, calls and attribute access raise ValueError, so the
    /eval surface can no longer execute code or touch the filesystem.
    If reverted, ``cmd_eval("os.getcwd()", ctx)`` would succeed instead
    of printing an error. Requires-python-literal note printed on
    failure points power users at /sandbox for full expressions.
    """
    if not args.strip():
        print_error("Usage: /eval <python literal expression>")
        return True
    try:
        result = ast.literal_eval(args)
        console.print(f"[success]→ {result!r}[/success]")
    except (ValueError, SyntaxError, MemoryError, RecursionError) as e:
        print_error(f"Eval failed (literals only — names/calls blocked): {e}")
    return True


@register("/test")
def cmd_test(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /test <pytest-pattern> (e.g. /test test_ai_base.py)")
        return True
    workspace = context.get("workspace", ".")
    try:
        import shlex
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()

    cmd = ["python", "-m", "pytest", "-v", "--no-header"] + tokens
    try:
        output = subprocess.check_output(
            cmd, cwd=workspace, text=True, timeout=120,
            stderr=subprocess.STDOUT)
        print_test_results(output, " ".join(tokens))
    except subprocess.TimeoutExpired:
        print_error("Tests timed out (120s)")
    except subprocess.CalledProcessError as e:
        print_test_results(e.output or str(e), " ".join(tokens))
    except FileNotFoundError:
        print_error("pytest not found")
    return True


# ══════════════════════════════════════════════════════════════════════
# External context injection
# ══════════════════════════════════════════════════════════════════════

@register("/web")
def cmd_web(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /web <url>")
        return True

    url = args.strip()
    if not url.startswith("http"):
        url = "https://" + url

    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, headers={"User-Agent": "KokertechAI-CLI/2.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        print_error(f"Failed to fetch URL: {e}")
        return True
    except Exception as e:
        print_error(f"Web fetch failed: {e}")
        return True

    # Crude text extraction
    import html as _html
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    print_web_preview(url, text)
    context["pending_input"] = (
        f"Here is the readable text from {url}:\n\n{text[:4000]}\n\n"
        f"Please summarize or answer based on this content.")
    return False


@register("/scan")
def cmd_scan(args: str, context: dict) -> bool:
    target = args.strip() or context.get("workspace", ".")

    def _tree(dirpath: str, prefix: str = "", max_depth: int = 3, depth: int = 0) -> list:
        if depth > max_depth:
            return [f"{prefix}..."]
        try:
            entries = sorted(os.listdir(dirpath))
        except PermissionError:
            return [f"{prefix}[denied]"]
        lines = []
        for i, entry in enumerate(entries):
            if entry.startswith(".") or entry == "__pycache__":
                continue
            full = os.path.join(dirpath, entry)
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            if os.path.isdir(full):
                lines.append(f"{prefix}{connector}{entry}/")
                sub_prefix = prefix + ("    " if is_last else "│   ")
                lines.extend(_tree(full, sub_prefix, max_depth, depth + 1))
            else:
                lines.append(f"{prefix}{connector}{entry}")
        return lines

    tree_lines = _tree(target)
    tree_text = "\n".join(tree_lines[:60])
    print_tree(tree_text, target)
    context["pending_input"] = (
        f"Here is the directory tree for {target}:\n\n{tree_text}\n\n"
        f"Please analyze this structure.")
    return False


@register("/sql")
def cmd_sql(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /sql <read-only SQL query>")
        return True

    db_path = os.path.join(context.get("workspace", "."), "kokertech_vault.db")
    if not os.path.isfile(db_path):
        print_error("kokertech_vault.db not found")
        return True

    # SQLite URI requires forward slashes on Windows
    uri = "file:{}?mode=ro".format(db_path.replace("\\", "/"))

    try:
        conn = sqlite3.connect(uri, uri=True, timeout=15.0)
        try:
            cursor = conn.cursor()
            cursor.execute(args)
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description] if cursor.description else []
        finally:
            conn.close()

        if not rows:
            print_system("Query returned no rows.")
            return True

        from rich.table import Table
        table = Table(title=f"SQL Results ({len(rows)} rows)", border_style="dim")
        for col in cols:
            table.add_column(col, style="system")
        for row in rows[:50]:
            table.add_row(*[str(v)[:80] for v in row])
        console.print(table)
        if len(rows) > 50:
            print_system(f"... and {len(rows) - 50} more rows.")
    except Exception as e:
        print_error(f"SQL query failed: {e}")
    return True


@register("/graph")
def cmd_graph(args: str, context: dict) -> bool:
    try:
        import memory_vault
    except ImportError:
        print_error("memory_vault not available")
        return True

    sub = args.strip().lower()

    if sub == "stats":
        try:
            stats = memory_vault.get_graph_stats()
            from rich.table import Table
            table = Table(title="Knowledge Graph Stats", border_style="dim")
            table.add_column("Metric", style="highlight")
            table.add_column("Value", style="system")
            for k, v in (stats or {}).items():
                table.add_row(str(k), str(v))
            console.print(table)
        except Exception as e:
            print_error(f"Graph stats failed: {e}")
        return True

    if sub:
        try:
            results = memory_vault.graph_rag_search(sub)
            if results:
                console.print(f"[system]{len(results)} graph results for '{sub}':[/system]")
                for i, r in enumerate(results[:10], 1):
                    preview = str(r)[:200]
                    console.print(f"  [highlight]{i}.[/highlight] [system]{preview}[/system]")
            else:
                print_system("No graph nodes found.")
        except Exception as e:
            print_error(f"Graph search failed: {e}")
        return True

    try:
        stats = memory_vault.get_graph_stats()
        console.print(f"[system]Graph: {stats or 'no stats available'}[/system]")
        console.print("[system]Use /graph <query> to search, /graph stats for details.[/system]")
    except Exception:
        print_error("Graph unavailable")
    return True


# ══════════════════════════════════════════════════════════════════════
# Session forking & tabs
# ══════════════════════════════════════════════════════════════════════

@register("/fork")
def cmd_fork(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /fork <session_name>")
        return True
    name = args.strip()

    fork_data = {
        "chat_history": list(context.get("chat_history", [])),
        "compressed_summary": context.get("compressed_summary", ""),
        "session_name": name,
        "show_thinking": context.get("show_thinking", True),
        "should_exit": False,
        "pending_input": None,
        "workspace": context.get("workspace", "."),
    }
    save_session(name, fork_data["chat_history"],
                 fork_data["compressed_summary"])
    tabs = context.setdefault("tabs", [])
    tabs.append(fork_data)
    context.setdefault("_active_tab", len(tabs) - 1)
    print_success(f"Forked to new tab: {name} (tab {len(tabs) - 1})")
    return True


def _handle_tab(text: str, context: dict) -> bool:
    """Handle /tab subcommands."""
    tabs = context.get("tabs", [])
    parts = text.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else ""

    if sub == "list" or not sub:
        if not tabs:
            print_system("No tabs open. Use /fork <name> to create one.")
            return True
        from rich.table import Table
        table = Table(title="Tabs", border_style="dim")
        table.add_column("ID", style="highlight")
        table.add_column("Name", style="system")
        table.add_column("Msgs", style="dim")
        active = context.get("_active_tab", -1)
        for i, t in enumerate(tabs):
            marker = "★" if i == active else ""
            name = t.get("session_name", f"tab-{i}")
            msgs = str(len(t.get("chat_history", [])))
            table.add_row(f"{marker} {i}", name, msgs)
        console.print(table)
        return True

    if sub == "switch":
        try:
            n = int(parts[2]) if len(parts) > 2 else -1
        except (ValueError, IndexError):
            print_error("Usage: /tab switch <N>")
            return True
        if n < 0 or n >= len(tabs):
            print_error(f"Invalid tab: {n} (0-{len(tabs)-1})")
            return True
        # Save current tab state
        active = context.get("_active_tab", -1)
        if 0 <= active < len(tabs):
            tabs[active]["chat_history"] = context["chat_history"]
            tabs[active]["compressed_summary"] = context.get("compressed_summary", "")
        # Switch
        context["chat_history"] = list(tabs[n].get("chat_history", []))
        context["compressed_summary"] = tabs[n].get("compressed_summary", "")
        context["session_name"] = tabs[n].get("session_name")
        context["_active_tab"] = n
        print_success(f"Switched to tab {n}: {tabs[n].get('session_name', '?')}")
        return True

    if sub == "close":
        try:
            n = int(parts[2]) if len(parts) > 2 else -1
        except (ValueError, IndexError):
            print_error("Usage: /tab close <N>")
            return True
        if n < 0 or n >= len(tabs):
            print_error(f"Invalid tab: {n}")
            return True
        name = tabs[n].get("session_name", f"tab-{n}")
        # Auto-save before closing
        try:
            save_session(name, tabs[n].get("chat_history", []),
                        tabs[n].get("compressed_summary", ""))
        except Exception as e:
            # Session-save failure = the tab's chat history is lost on close (data loss).
            logger.warning(f"Failed to auto-save tab {n} before close: {e}")
        tabs.pop(n)
        active = context.get("_active_tab", 0)
        if n == active:
            context["_active_tab"] = max(0, len(tabs) - 1)
            if tabs:
                context["chat_history"] = list(tabs[-1].get("chat_history", []))
        print_success(f"Closed tab {n}: {name}")
        return True

    print_error("Usage: /tab [list|switch <N>|close <N>]")
    return True


# ══════════════════════════════════════════════════════════════════════
# Customization & aliases
# ══════════════════════════════════════════════════════════════════════

@register("/alias")
def cmd_alias(args: str, context: dict) -> bool:
    sub = args.strip()

    if sub == "list" or not sub:
        if not _ALIASES:
            print_system("No aliases defined. Create one with: /alias m /model")
            return True
        from rich.table import Table
        table = Table(title="Aliases", border_style="dim")
        table.add_column("Alias", style="highlight")
        table.add_column("Expands to", style="system")
        for k, v in sorted(_ALIASES.items()):
            table.add_row(f"/{k}", v)
        console.print(table)
        return True

    if sub.startswith("rm "):
        name = sub[3:].strip().lstrip("/")
        if name in _ALIASES:
            del _ALIASES[name]
            print_success(f"Removed alias: /{name}")
        else:
            print_error(f"Alias '/{name}' not found.")
        return True

    parts = sub.split(maxsplit=1)
    if len(parts) < 2:
        print_error("Usage: /alias <name> <command> (e.g. /alias m /model)")
        return True

    alias_name = parts[0].lstrip("/").lower()
    alias_cmd = parts[1]
    _ALIASES[alias_name] = alias_cmd
    print_success(f"Alias created: /{alias_name} → {alias_cmd}")
    return True


@register("/theme")
def cmd_theme(args: str, context: dict) -> bool:
    themes = ["dark", "light", "monokai", "one-dark", "dracula", "github", "native"]
    sub = args.strip().lower()
    if not sub or sub == "list":
        console.print(f"[system]Available themes: {', '.join(themes)}[/system]")
        print_system("Usage: /theme <name>")
        return True
    if sub not in themes:
        print_error(f"Unknown theme: {sub}. Available: {', '.join(themes)}")
        return True
    try:
        from cli.formatting import switch_theme
        switch_theme(sub)
        context["theme"] = sub
        print_success(f"Theme: {sub}")
    except Exception as e:
        print_error(f"Theme switch failed: {e}")
    return True


@register("/prompt")
def cmd_prompt(args: str, context: dict) -> bool:
    styles = ["arrow", "minimal", "verbose", "none"]
    sub = args.strip().lower()
    if not sub or sub == "list":
        console.print(f"[system]Available prompt styles: {', '.join(styles)}[/system]")
        return True
    if sub not in styles:
        print_error(f"Unknown style: {sub}")
        return True
    context["prompt_style"] = sub
    print_prompt_style(sub)
    return True


@register("/compact")
def cmd_compact(args: str, context: dict) -> bool:
    current = context.get("compact", False)
    context["compact"] = not current
    state = "ON" if context["compact"] else "OFF"
    print_success(f"Compact mode: {state}")
    return True


@register("/silent")
def cmd_silent(args: str, context: dict) -> bool:
    sub = args.strip().lower()
    if sub in ("on", "true", "1", "yes"):
        context["silent"] = True
        print_success("Silent mode: ON (only AI responses and errors shown)")
    elif sub in ("off", "false", "0", "no"):
        context["silent"] = False
        print_success("Silent mode: OFF")
    else:
        current = context.get("silent", False)
        state = "ON" if current else "OFF"
        print_system(f"Silent mode: {state}. Use /silent on|off to toggle.")
    return True


# ══════════════════════════════════════════════════════════════════════
# Timers, notifications & TTS
# ══════════════════════════════════════════════════════════════════════

@register("/timer")
def cmd_timer(args: str, context: dict) -> bool:
    parts = args.strip().split(maxsplit=1)
    try:
        seconds = int(parts[0])
    except (ValueError, IndexError):
        print_error("Usage: /timer <seconds> <message>")
        return True
    msg = parts[1] if len(parts) > 1 else "Timer done!"

    def _fire():
        import time as _time
        _time.sleep(seconds)
        print_notification_toast(f"⏰ {msg} ({seconds}s)", duration=5)

    t = threading.Thread(target=_fire, daemon=True)
    t.start()
    print_success(f"Timer set for {seconds}s: {msg}")
    return True


@register("/notify")
def cmd_notify(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /notify <message>")
        return True
    print_notification_toast(args.strip(), duration=4)
    return True


@register("/tts")
def cmd_tts(args: str, context: dict) -> bool:
    sub = args.strip().lower()
    if sub in ("on", "true", "1", "yes"):
        from config import CONFIG
        CONFIG["tts_enabled"] = True
        print_success("TTS: ON")
    elif sub in ("off", "false", "0", "no"):
        from config import CONFIG
        CONFIG["tts_enabled"] = False
        print_success("TTS: OFF")
    elif sub and sub not in ("on", "off"):
        # Speak
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.say(args.strip())
            engine.runAndWait()
            print_success("Spoke.")
        except ImportError:
            print_error("pyttsx3 not installed. Install with: pip install pyttsx3")
        except Exception as e:
            print_error(f"TTS failed: {e}")
    else:
        from config import CONFIG
        state = "ON" if CONFIG.get("tts_enabled", True) else "OFF"
        print_system(f"TTS: {state}. Use /tts on|off|speak <text>.")
    return True


# ══════════════════════════════════════════════════════════════════════
# Snapshots
# ══════════════════════════════════════════════════════════════════════

@register("/snapshot")
def cmd_snapshot(args: str, context: dict) -> bool:
    name = args.strip() or f"snap_{datetime.now().strftime('%H%M%S')}"
    snapshots = context.setdefault("_snapshots", {})
    snapshots[name] = {
        "chat_history": list(context.get("chat_history", [])),
        "compressed_summary": context.get("compressed_summary", ""),
        "session_name": context.get("session_name"),
    }
    print_success(f"Snapshot saved: {name} ({len(snapshots[name]['chat_history'])} msgs)")
    return True


@register("/restore")
def cmd_restore(args: str, context: dict) -> bool:
    snapshots = context.get("_snapshots", {})
    if not args.strip():
        names = list(snapshots.keys())
        if not names:
            print_system("No snapshots. Use /snapshot [name] to create one.")
            return True
        console.print(f"[system]Snapshots: {', '.join(names)}[/system]")
        return True

    name = args.strip()
    snap = snapshots.get(name)
    if snap is None:
        print_error(f"Snapshot '{name}' not found. Available: {', '.join(snapshots.keys())}")
        return True
    context["chat_history"] = list(snap["chat_history"])
    context["compressed_summary"] = snap.get("compressed_summary", "")
    context["session_name"] = snap.get("session_name")
    print_success(f"Restored snapshot: {name} ({len(snap['chat_history'])} msgs)")
    return True


# ══════════════════════════════════════════════════════════════════════
# Search, diff, config, persona, pipe, multi, export (existing v2)
# ══════════════════════════════════════════════════════════════════════

@register("/search")
def cmd_search(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /search <pattern> [--max N] [--type py]")
        return True
    import shlex
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    pattern = tokens[0]
    extra_flags = tokens[1:] if len(tokens) > 1 else []
    max_results = 20
    type_filter = None
    i = 0
    while i < len(extra_flags):
        if extra_flags[i] == "--max" and i + 1 < len(extra_flags):
            try:
                max_results = int(extra_flags[i + 1])
                i += 2
                continue
            except ValueError:
                pass
        elif extra_flags[i] == "--type" and i + 1 < len(extra_flags):
            type_filter = extra_flags[i + 1]
            i += 2
            continue
        i += 1
    cmd = ["rg", "--no-heading", "--line-number", "--color", "never",
           "--max-count", str(max_results), pattern]
    if type_filter:
        cmd.extend(["--type", type_filter])
    workspace = context.get("workspace", os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    try:
        output = subprocess.check_output(cmd, cwd=workspace, text=True, timeout=30,
                                         stderr=subprocess.STDOUT)
        lines = output.strip().split("\n") if output.strip() else []
        results = []
        for line in lines:
            parts = line.split(":", 2)
            if len(parts) >= 3:
                results.append({"file": parts[0], "line": int(parts[1]), "text": parts[2]})
        print_search_results(results, pattern)
    except subprocess.CalledProcessError as e:
        if e.returncode == 1:
            print_search_results([], pattern)
        else:
            print_error(f"Search failed: {e.output or e}")
    except FileNotFoundError:
        print_error("ripgrep (rg) not found.")
    except Exception as e:
        print_error(f"Search failed: {e}")
    return True


@register("/code")
def cmd_code(args: str, context: dict) -> bool:
    """CLI Coding Agent interface (Sprint 18).

    Subcommands:
      /code help                — Show coding agent subcommands
      /code task <prompt>       — Run autonomous coding task
      /code search <pattern>    — Search workspace code (ripgrep / re)
      /code test [target]       — Run tests via pytest / unittest
      /code diff [--staged]     — Show diff before applying or git diff
      /code status              — Git status porcelain summary
      /code ast <file>          — Inspect AST functions, classes, and imports
      /code files [query]       — Fuzzy search files in workspace
      /code context             — Show detected project language, frameworks, conventions
      /code undo                — Rollback last file modification
      /code redo                — Reapply rolled-back file modification
    """
    from cli.agent import CodingAgent
    from cli.diff_viewer import render_diff
    from rich.table import Table

    agent = context.get("_coding_agent")
    if agent is None or not isinstance(agent, CodingAgent):
        workspace = context.get("workspace", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        controller = context.get("_controller")
        agent = CodingAgent(workspace=workspace, controller=controller)
        context["_coding_agent"] = agent

    sub = args.strip()
    if not sub or sub.lower() in ("help", "-h", "--help"):
        table = Table(title="CLI Coding Agent Subcommands (/code)", border_style="dim", show_header=True)
        table.add_column("Subcommand", style="highlight", no_wrap=True)
        table.add_column("Description", style="system")
        table.add_row("/code task <prompt>", "Run autonomous coding task")
        table.add_row("/code search <pattern>", "Search codebase with ripgrep/regex")
        table.add_row("/code test [target]", "Run test suite (pytest/unittest)")
        table.add_row("/code diff [--staged]", "Preview git diff with syntax highlighting")
        table.add_row("/code status", "Show workspace git porcelain status")
        table.add_row("/code ast <filepath>", "Inspect AST functions, classes, and imports")
        table.add_row("/code files [query]", "Fuzzy find files in workspace")
        table.add_row("/code context", "Inspect detected language, frameworks, conventions")
        table.add_row("/code undo", "Rollback last code change")
        table.add_row("/code redo", "Reapply rolled-back change")
        console.print(table)
        return True

    parts = sub.split(maxsplit=1)
    subcmd = parts[0].lower()
    subargs = parts[1].strip() if len(parts) > 1 else ""

    if subcmd in ("task", "run"):
        task_query = subargs
        if not task_query:
            print_error("Usage: /code task <description of task>")
            return True
        print_system(f"Running Coding Agent on task: '{task_query[:80]}'")
        result = agent.run_task(task_query)
        if result.get("status") == "success":
            resp = result.get("response", "")
            if resp:
                print_response(resp)
            mods = result.get("files_modified", [])
            if mods:
                print_success(f"Files modified: {', '.join(mods)}")
        else:
            print_error(f"Coding task failed: {result.get('error', 'unknown error')}")
        return True

    elif subcmd == "search":
        if not subargs:
            print_error("Usage: /code search <pattern>")
            return True
        try:
            results = agent.search_code(subargs)
        except ValueError as e:
            print_error(f"Invalid search pattern: {e}")
            return True
        if not results:
            print_system(f"No occurrences of '{subargs}' found.")
        else:
            table = Table(title=f"Code Search: '{subargs}' ({len(results)} matches)", border_style="dim")
            table.add_column("File", style="highlight")
            table.add_column("Line", justify="right", style="cyan")
            table.add_column("Content", style="system")
            for item in results[:50]:
                table.add_row(
                    str(item.get("path", "")),
                    str(item.get("line", "")),
                    str(item.get("text", item.get("content", "")))[:120],
                )
            console.print(table)
        return True

    elif subcmd == "test":
        target = subargs if subargs else None
        print_system(f"Running tests{' for ' + target if target else ''}...")
        test_res = agent.run_tests(target)
        returncode = test_res.get("returncode")
        stdout = test_res.get("stdout", "")
        stderr = test_res.get("stderr", "")
        runner = test_res.get("runner", "pytest")
        errors = test_res.get("errors", [])
        if returncode == 0:
            verdict = f"Tests passed ({runner})."
        elif returncode is None:
            verdict = f"Test run status unknown (runner: {runner})."
        else:
            err_note = f" — {len(errors)} error(s) parsed" if errors else ""
            verdict = f"Tests failed (runner: {runner}, exit {returncode}){err_note}."
        output = stdout or stderr or verdict
        print_test_results(output, target or "all")
        return True

    elif subcmd == "diff":
        staged = "--staged" in subargs.lower()
        diff_text = agent.git_diff(staged=staged)
        render_diff(diff_text, title=f"Git Diff{' (Staged)' if staged else ''}")
        return True

    elif subcmd == "status":
        status_res = agent.git_status()
        warning = status_res.get("warning")
        branch = status_res.get("branch") or "(detached)"
        entries = (
            [("staged", p) for p in status_res.get("staged", [])]
            + [("modified", p) for p in status_res.get("unstaged", [])]
            + [("untracked", p) for p in status_res.get("untracked", [])]
        )
        if not entries:
            if warning:
                print_system(f"Git status unavailable: {warning}")
            else:
                print_success(f"Working tree clean on '{branch}' (no modified or untracked files).")
        else:
            table = Table(title=f"Git Status — {branch} ({len(entries)} changed)", border_style="dim")
            table.add_column("Status", style="highlight", justify="center")
            table.add_column("Path", style="system")
            for status_label, path in entries:
                table.add_row(status_label, path)
            console.print(table)
        return True

    elif subcmd == "ast":
        if not subargs:
            print_error("Usage: /code ast <filepath>")
            return True
        try:
            ast_res = agent.analyze_ast(subargs)
        except (OSError, ValueError) as e:
            print_error(f"AST analysis failed for {subargs}: {e}")
            return True
        if ast_res.get("error") == "not_found":
            print_error(f"File not found: {subargs}")
            return True
        if ast_res.get("syntax_valid") is False:
            print_error(f"Syntax error in {subargs}: {ast_res.get('syntax_error')}")
            return True
        funcs = ast_res.get("functions", [])
        classes = ast_res.get("classes", [])
        imports = ast_res.get("imports", [])
        table = Table(title=f"AST Analysis: {subargs}", border_style="dim")
        table.add_column("Kind", style="highlight")
        table.add_column("Name", style="bold")
        table.add_column("Details", style="system")
        for c in classes:
            bases = ", ".join(c.get("bases", []))
            table.add_row("Class", c.get("name", ""), f"line {c.get('line')} (bases: {bases or 'none'})")
        for fn in funcs:
            args_list = ", ".join(fn.get("args", []))
            table.add_row("Function", fn.get("name", ""), f"line {fn.get('line')}({args_list})")
        for imp in imports[:15]:
            if isinstance(imp, dict):
                mod = imp.get("module", "")
                names = ", ".join(imp.get("names", []))
                table.add_row("Import", mod, f"imported: {names}" if names else "")
            else:
                table.add_row("Import", str(imp), "")
        console.print(table)
        return True

    elif subcmd == "files":
        matches = agent.fuzzy_find_files(subargs)
        if not matches:
            print_system(f"No files matching '{subargs}'.")
        else:
            table = Table(title=f"Workspace Files ({len(matches)} matches)", border_style="dim")
            table.add_column("#", justify="right", style="dim")
            table.add_column("File Path", style="highlight")
            for idx, p in enumerate(matches, 1):
                table.add_row(str(idx), p)
            console.print(table)
        return True

    elif subcmd in ("context", "info"):
        ctx = agent.project_context
        table = Table(title="Project Context", border_style="dim")
        table.add_column("Attribute", style="highlight")
        table.add_column("Value", style="system")
        table.add_row("Workspace", ctx.root_path)
        table.add_row("Language", ctx.language or "Unknown")
        table.add_row("Frameworks", ", ".join(sorted(ctx.frameworks)) or "None")
        table.add_row("Conventions", ", ".join(sorted(ctx.conventions)) or "None")
        console.print(table)
        return True

    elif subcmd == "undo":
        ok, msg = agent.undo()
        if ok:
            print_success(msg)
        else:
            print_error(msg)
        return True

    elif subcmd == "redo":
        ok, msg = agent.redo()
        if ok:
            print_success(msg)
        else:
            print_error(msg)
        return True

    else:
        # Treat unknown subcommand as task description
        print_system(f"Running Coding Agent on task: '{sub[:80]}'")
        result = agent.run_task(sub)
        if result.get("status") == "success":
            resp = result.get("response", "")
            if resp:
                print_response(resp)
            mods = result.get("files_modified", [])
            if mods:
                print_success(f"Files modified: {', '.join(mods)}")
        else:
            print_error(f"Coding task failed: {result.get('error', 'unknown error')}")
        return True


@register("/diff")
def cmd_diff(args: str, context: dict) -> bool:
    workspace = context.get("workspace", os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    staged = "--staged" in args.lower()
    try:
        cmd = ["git", "-C", workspace, "diff"]
        if staged:
            cmd.append("--staged")
        output = subprocess.check_output(cmd, text=True, timeout=10,
                                         stderr=subprocess.STDOUT)
        print_diff_view(output)
    except subprocess.CalledProcessError as e:
        print_error(f"Git diff failed: {e.output or e}")
    except FileNotFoundError:
        print_error("git not found.")
    except Exception as e:
        print_error(f"Diff failed: {e}")
    return True


@register("/config")
def cmd_config(args: str, context: dict) -> bool:
    from config import CONFIG
    if not args.strip():
        keys = sorted(CONFIG.keys())
        console.print(f"[system]{len(keys)} config keys available.[/system]")
        return True
    parts = args.strip().split(maxsplit=1)
    key = parts[0]
    if key not in CONFIG:
        print_error(f"Unknown config key: {key}")
        return True
    if len(parts) == 1:
        console.print(f"[highlight]{key}[/highlight] = [system]{CONFIG[key]!r}[/system]")
    else:
        raw = parts[1].strip()
        old = CONFIG[key]
        try:
            if isinstance(old, bool):
                val = raw.lower() in ("true", "1", "yes", "on")
            elif isinstance(old, int):
                val = int(raw)
            elif isinstance(old, float):
                val = float(raw)
            else:
                val = raw
        except (ValueError, TypeError):
            print_error(f"Cannot convert '{raw}' to {type(old).__name__}")
            return True
        CONFIG[key] = val
        try:
            from config import save_settings
            save_settings()
            print_success(f"Set {key} = {val!r} (was {old!r})")
        except Exception as e:
            CONFIG[key] = old
            print_error(f"Failed to save: {e}")
    return True


@register("/persona")
def cmd_persona(args: str, context: dict) -> bool:
    from config import CONFIG
    sub = args.strip()
    personas_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "personas.json")
    try:
        with open(personas_path, "r", encoding="utf-8") as f:
            personas = json.load(f)
    except (OSError, json.JSONDecodeError):
        print_error("personas.json not found or invalid")
        return True
    if not sub or sub.lower() == "list":
        active = CONFIG.get("active_persona", "")
        if isinstance(personas, dict):
            print_persona_list(personas, active)
        elif isinstance(personas, list):
            console.print("[system]Personas:[/system]")
            for p in personas:
                console.print(f"  [highlight]{p}{' ★' if p == active else ''}[/highlight]")
        return True
    if isinstance(personas, dict):
        if sub not in personas and sub.lower() not in (k.lower() for k in personas):
            print_error(f"Persona '{sub}' not found.")
            return True
        for k in personas:
            if k.lower() == sub.lower():
                sub = k
                break
    CONFIG["active_persona"] = sub
    try:
        from config import save_settings
        save_settings()
    except Exception as e:
        # Config write failure = persona choice may not survive restart (data loss).
        logger.warning(f"Failed to persist active_persona: {e}")
    print_success(f"Persona set to: {sub}")
    return True


@register("/pipe")
def cmd_pipe(args: str, context: dict) -> bool:
    if not args.strip():
        print_error("Usage: /pipe <shell command>")
        return True
    # shell=True here is INTENTIONAL (audit 2026-09-21): /pipe is an explicit
    # shell-passthrough command — the user's literal string must support
    # pipes (|), redirection (>), and env expansion, which list-args cannot
    # provide. Trust level == the user's own terminal (single-user offline
    # system); there is no untrusted input path. Do NOT "fix" to list-args:
    # that silently breaks /pipe ls | grep x. Ruff S602 remains baselined.
    try:
        output = subprocess.check_output(
            args, shell=True, text=True, timeout=30, stderr=subprocess.STDOUT,
            cwd=context.get("workspace", "."))
    except subprocess.TimeoutExpired:
        print_error("Command timed out (30s)")
        return True
    except Exception as e:
        print_error(f"Command failed: {e}")
        return True
    output = output.strip()
    if not output:
        print_system("Command produced no output.")
        return True
    preview = output[:500].replace("\n", "\n  ")
    console.print(f"[system]Command output ({len(output)} chars):[/system]")
    console.print(f"  [system]{preview}[/system]")
    if len(output) > 500:
        console.print(f"  [timestamp]... ({len(output) - 500} more chars)[/timestamp]")
    context["pending_input"] = (
        f"Here is the output of `{args}`:\n\n{output[:4000]}\n\n"
        f"Please analyze this output.")
    return False


@register("/multi")
def cmd_multi(args: str, context: dict) -> bool:
    parts = args.strip().split(maxsplit=2)
    if len(parts) < 3:
        print_error("Usage: /multi <model_a> <model_b> <prompt>")
        return True
    model_a, model_b, prompt = parts
    print_system(f"A/B comparing '{model_a}' vs '{model_b}'...")
    try:
        from kokertechController import KokertechController
        ctrl = context.get("_controller")
        if ctrl is None:
            ctrl = KokertechController()
            context["_controller"] = ctrl
        specs = [
            {"provider": "local_llm", "model": model_a, "label": f"A: {model_a}"},
            {"provider": "local_llm", "model": model_b, "label": f"B: {model_b}"},
        ]
        results = ctrl.process_input_multi(prompt, specs)
        for r in results:
            status = "[success]OK[/success]" if r["ok"] else f"[error]{r['error']}[/error]"
            console.print(f"\n[highlight]{r['label']}[/highlight] {status}")
            if r["ok"] and r["content"]:
                console.print(f"  [system]{r['content'][:500]}[/system]")
    except Exception as e:
        print_error(f"Multi-model comparison failed: {e}")
    return True


@register("/export")
def cmd_export(args: str, context: dict) -> bool:
    parts = args.strip().split()
    fmt = "md"
    path = None
    if parts and parts[0] in ("md", "json"):
        fmt = parts[0]
        parts = parts[1:]
    if parts:
        path = parts[0]
    history = context.get("chat_history", [])
    if not history:
        print_error("Nothing to export.")
        return True
    if fmt == "json":
        content = json.dumps(history, indent=2, ensure_ascii=False)
        ext = ".json"
    else:
        lines = ["# KokertechAI Session Export", "",
                 f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
        for m in history:
            lines.append(f"### {m.get('role', 'unknown').capitalize()}")
            lines.append("")
            lines.append(m.get("content", ""))
            lines.append("")
        content = "\n".join(lines)
        ext = ".md"
    if not path:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print_export_done(path, fmt, len(history))
    except OSError as e:
        print_error(f"Export failed: {e}")
    return True


@register("/changelog")
def cmd_changelog(args: str, context: dict) -> bool:
    cl_path = os.path.join(context.get("workspace", "."), "docs", "CHANGELOG.md")
    try:
        with open(cl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        print_changelog(lines[:100])
    except FileNotFoundError:
        print_error("docs/CHANGELOG.md not found")
    return True


# ══════════════════════════════════════════════════════════════════════
# Stats, thinking, session commands
# ══════════════════════════════════════════════════════════════════════

@register("/stats")
def cmd_stats(args: str, context: dict) -> bool:
    ctrl = context.get("_controller")
    print_session_stats(context, ctrl)
    return True


@register("/thinking")
def cmd_thinking(args: str, context: dict) -> bool:
    sub = args.strip().lower()
    if sub in ("on", "true", "1", "yes"):
        context["show_thinking"] = True
        print_success("Thinking blocks: ON")
    elif sub in ("off", "false", "0", "no"):
        context["show_thinking"] = False
        print_success("Thinking blocks: OFF")
    else:
        current = context.get("show_thinking", True)
        state = "ON" if current else "OFF"
        print_system(f"Thinking blocks: {state}. Use /thinking on|off to toggle.")
    return True


@register("/save")
def cmd_save(args: str, context: dict) -> bool:
    tags = []
    name_parts = []
    parts = args.split()
    i = 0
    while i < len(parts):
        part = parts[i]
        if part.startswith("--tag="):
            tags = [t.strip() for t in part[6:].split(",")]
        elif part == "--tag":
            i += 1
            if i < len(parts):
                tags = [t.strip() for t in parts[i].split(",")]
        else:
            name_parts.append(part)
        i += 1
    name = " ".join(name_parts).strip()
    if not name:
        name = datetime.now().strftime("session_%Y%m%d_%H%M%S")
    try:
        path = save_session(name=name, chat_history=context.get("chat_history", []),
                           context_summary=context.get("compressed_summary", ""),
                           tags=tags or None)
        tag_str = f" [tags: {','.join(tags)}]" if tags else ""
        print_success(f"Session saved: {name}{tag_str} ({os.path.basename(path)})")
        context["session_name"] = name
    except Exception as e:
        print_error(f"Failed to save session: {e}")
    return True


@register("/load")
def cmd_load(args: str, context: dict) -> bool:
    merge = "--merge" in args
    name = args.replace("--merge", "").strip()
    if not name:
        print_error("Usage: /load [--merge] <session_name>")
        return True
    if merge:
        merged = merge_session(context.get("session_name", "current"), name)
        if merged is None:
            print_error(f"Cannot merge: session '{name}' or current not found.")
            return True
        context["chat_history"] = merged.get("chat_history", [])
        context["compressed_summary"] = merged.get("context_summary", "")
        print_success(f"Merged '{name}' ({merged.get('message_count', 0)} total messages)")
        return True
    data = load_session(name)
    if data is None:
        print_error(f"Session not found: {name}")
        sessions = list_sessions()
        if sessions:
            console.print("[system]Available sessions:[/system]")
            for s in sessions[:5]:
                console.print(f"  [highlight]{s['name']}[/highlight]")
        return True
    context["chat_history"] = data.get("chat_history", [])
    context["compressed_summary"] = data.get("context_summary", "")
    context["session_name"] = data.get("name", name)
    print_success(f"Loaded: {name} ({data.get('message_count', 0)} messages)")
    return True


@register("/workflow")
def cmd_workflow(args: str, context: dict) -> bool:
    try:
        from workflow_engine import WorkflowEngine
    except ImportError:
        print_error("Workflow engine not available")
        return True
    sub = args.strip()
    engine = WorkflowEngine()
    if not sub or sub.lower() == "list":
        try:
            workflows = engine.list_workflows()
            if not workflows:
                print_system("No workflows found.")
            else:
                console.print(f"[system]{len(workflows)} workflow(s):[/system]")
                for w in workflows:
                    console.print(f"  [highlight]{w}[/highlight]")
        except Exception as e:
            print_error(f"Failed to list workflows: {e}")
        return True
    if not sub.endswith(".yaml") and not sub.endswith(".yml"):
        sub += ".yaml"
    try:
        result = engine.execute(sub)
        if result.get("ok"):
            print_success(f"Workflow '{sub}' completed.")
        else:
            print_error(f"Workflow failed: {result.get('error', 'unknown')}")
    except Exception as e:
        print_error(f"Workflow execution failed: {e}")
    return True


@register("/sessions")
def cmd_sessions(args: str, context: dict) -> bool:
    sub = args.strip()

    if sub.startswith("search "):
        term = sub[7:].strip()
        if not term:
            print_error("Usage: /sessions search <term>")
            return True
        results = search_sessions(term)
        print_sessions(results)
        return True

    if sub.startswith("delete "):
        name = sub[7:].strip()
        if not name:
            print_error("Usage: /sessions delete <name>")
            return True
        confirm = input(f"Delete session '{name}'? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print_system("Delete cancelled.")
            return True
        if delete_session(name):
            print_success(f"Deleted session: {name}")
        else:
            print_error(f"Session not found: {name}")
        return True

    if sub.startswith("rename "):
        rest = sub[7:].strip()
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            print_error("Usage: /sessions rename <old_name> <new_name>")
            return True
        if rename_session(parts[0], parts[1]):
            print_success(f"Renamed: {parts[0]} → {parts[1]}")
        else:
            print_error("Cannot rename: source not found or target exists")
        return True

    if sub.startswith("peek "):
        name = sub[5:].strip()
        data = load_session(name)
        if data is None:
            print_error(f"Session not found: {name}")
            return True
        history = data.get("chat_history", [])
        console.print(f"[system]Session '{name}' ({len(history)} msgs):[/system]")
        if len(history) <= 6:
            for i, m in enumerate(history):
                console.print(f"  [timestamp]{i+1}. {m.get('role', '?')}:[/timestamp] "
                              f"{m.get('content', '')[:100]}")
        else:
            for i, m in enumerate(history[:3]):
                console.print(f"  [timestamp]{i+1}. {m.get('role', '?')}:[/timestamp] "
                              f"{m.get('content', '')[:100]}")
            console.print(f"  [timestamp]... ({len(history) - 6} messages)[/timestamp]")
            for i, m in enumerate(history[-3:], len(history) - 2):
                console.print(f"  [timestamp]{i+1}. {m.get('role', '?')}:[/timestamp] "
                              f"{m.get('content', '')[:100]}")
        return True

    if sub.startswith("archive "):
        name = sub[8:].strip()
        if archive_session(name):
            print_success(f"Archived: {name}")
        else:
            print_error(f"Cannot archive: {name}")
        return True

    if sub == "archived":
        archived = list_archived()
        if not archived:
            print_system("No archived sessions.")
        else:
            console.print(f"[system]{len(archived)} archived sessions:[/system]")
            for a in archived:
                console.print(f"  [highlight]{a}[/highlight]")
        return True

    if sub.startswith("unarchive "):
        name = sub[10:].strip()
        if unarchive_session(name):
            print_success(f"Restored: {name}")
        else:
            print_error(f"Cannot restore: {name}")
        return True

    if sub.startswith("copy "):
        rest = sub[5:].strip()
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            print_error("Usage: /sessions copy <source> <new_name>")
            return True
        if copy_session(parts[0], parts[1]):
            print_success(f"Copied: {parts[0]} → {parts[1]}")
        else:
            print_error("Copy failed")
        return True

    if sub.startswith("export-all"):
        tag = sub[len("export-all"):].strip()
        path = export_sessions_bulk(tag or None)
        if path:
            print_success(f"Exported to: {path}")
        else:
            print_error("Export failed or no sessions matched")
        return True

    sessions = list_sessions()
    print_sessions(sessions)
    return True


@register("/exit")
@register("/quit")
def cmd_exit(args: str, context: dict) -> bool:
    context["should_exit"] = True
    return True
