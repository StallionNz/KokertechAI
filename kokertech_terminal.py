"""
kokertech_terminal.py — Sprint 15 CLI Tool V2.

Rich interactive terminal with streaming tokens, 50 slash commands,
session persistence, history navigation, tabs, and 90+ features.
"""

import os
import sys
import time
from datetime import datetime

from ai_base import get_provider
from logging_config import get_logger

logger = get_logger(name="KokertechCLI")


def _setup_readline():
    """Enable readline for history navigation (up/down arrows)."""
    try:
        import readline
    except ImportError:
        try:
            import pyreadline3 as readline
        except ImportError:
            readline = None
    if readline is not None:
        try:
            histfile = os.path.expanduser("~/.kokertech_cli_history")
            if os.path.exists(histfile):
                readline.read_history_file(histfile)
            import atexit
            atexit.register(readline.write_history_file, histfile)
        except OSError as e:
            # History is an optional enhancement; a corrupt/unwritable file
            # must never block startup. OSError = the realistic failure set
            # for file IO here (readline.read_history_file / write at exit).
            logger.debug(f"CLI history unavailable: {e}")


def _get_input(context: dict = None) -> str:
    """Get user input with styled prompt based on context."""
    if context is None:
        context = {}
    style = context.get("prompt_style", "arrow")
    if style == "minimal":
        prompt_str = "· "
    elif style == "verbose":
        session = context.get("session_name", "default")
        prompt_str = f"[{session}] Jacques > "
    elif style == "none":
        prompt_str = ""
    else:
        prompt_str = "> "
    try:
        return input(f"\n{prompt_str}").strip()
    except (EOFError, KeyboardInterrupt):
        return "/exit"


def _auto_resume(context: dict):
    """Offer to resume the last saved session on startup."""
    try:
        from cli.session import get_last_session
        last = get_last_session()
        if last and last.get("chat_history"):
            from cli.formatting import console
            console.print(f"[system]Found previous session '{last.get('name')}' ({len(last['chat_history'])} messages). Resuming...[/system]")
            context["session_name"] = last.get("name", "default")
            context["chat_history"] = list(last.get("chat_history", []))
    except Exception as e:
        logger.debug(f"Auto-resume skipped: {e}")


def _do_exit(context: dict):
    """Save session on exit if there's history."""
    try:
        history = context.get("chat_history", [])
        if history:
            from cli.session import save_session
            s_name = context.get("session_name") or f"session_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}"
            save_session(s_name, history)
            from cli.formatting import print_system
            print_system(f"Session saved as '{s_name}'.")
    except Exception as e:
        # REGRESSION GUARD: silent data loss is worse than a failed save.
        # The user must KNOW their session did not persist.
        logger.warning(f"Session save FAILED on exit (session may be lost): {e}")


def _init_raw_provider():
    """Initialize raw provider + chat history for fallback mode."""
    try:
        provider = get_provider()
        return provider
    except Exception as e:
        logger.warning(f"Could not initialize raw provider: {e}")
        return None


def _process_with_controller(controller, text: str, context: dict):
    """Process input through KokertechController with streaming + token stats."""
    from cli.formatting import print_thinking, print_response, print_error, print_token_footer
    t0 = time.time()
    try:
        context.setdefault("chat_history", []).append({"role": "user", "content": text})

        def log_cb(msg: str):
            if context.get("show_thinking", True):
                print_thinking(msg)

        res = controller.process_input(text, agent_type="Executive", log_callback=log_cb)
        reply = res.get("final", "")
        context["chat_history"].append({"role": "assistant", "content": reply})
        print_response(reply)

        elapsed = max(time.time() - t0, 0.001)
        tok_count = len(reply.split())
        tps = tok_count / elapsed
        print_token_footer(tok_count, elapsed, tps)
    except Exception as e:
        print_error(f"Inference error: {e}")


def _process_raw(text: str, context: dict):
    """Fallback: process input through raw provider (no controller)."""
    from cli.formatting import print_response, print_error
    try:
        provider = _init_raw_provider()
        if not provider:
            print_error("No AI provider available.")
            return
        context.setdefault("chat_history", []).append({"role": "user", "content": text})
        resp = provider.generate(text)
        reply = resp if isinstance(resp, str) else str(resp)
        context["chat_history"].append({"role": "assistant", "content": reply})
        print_response(reply)
    except Exception as e:
        print_error(f"Raw provider error: {e}")


def handle_slash_command(text: str):
    """
    Handle slash commands for the CLI.

    Returns:
        (should_skip, result): should_skip=True means don't send to AI.
            result is the transformed text (or empty string if skipped).
    """
    if not text:
        return False, ""
    stripped = text.strip()
    if not stripped.startswith("/"):
        # Normal text passes through unchanged
        return False, text
    # Extract command and rest
    parts = stripped.split(None, 1)
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if cmd == "/help":
        _print_help_text()
        return True, ""

    if cmd == "/rag":
        if not rest:
            _print_help_text()
            return True, ""
        print(f"Deep research mode activated — searching vault for: {rest}")
        return False, f"<<RAG:{rest}>>"

    # Unknown slash command — pass through unchanged
    return False, text


def _print_help_text():
    """Print available slash commands to stdout."""
    print("Available slash commands:")
    print("  /rag <query>  — Deep research mode (RAG search)")
    print("  /help          — Show this help text")


def run_cli_command(command_str: str, context: dict) -> None:
    """Execute a single one-shot CLI command without entering interactive loop."""
    from cli.commands import dispatch

    if command_str.startswith("/") or command_str.startswith("<<RAG:"):
        dispatch(command_str, context)
        return

    should_skip, transformed = handle_slash_command(command_str)
    if should_skip:
        return
    actual_input = transformed or command_str

    ctrl = context.get("_controller")
    if ctrl is not None:
        _process_with_controller(ctrl, actual_input, context)
    else:
        _process_raw(actual_input, context)


def main():
    """Main CLI loop with rich formatting, streaming, and CLI subcommands."""
    # Ensure UTF-8 output on Windows consoles
    if sys.platform == "win32":
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError) as e:
            # Cosmetic console setup — never block startup over it.
            logger.debug(f"Console UTF-8 reconfigure skipped: {e}")

    from cli.formatting import print_banner, print_keybinding_bar, print_system
    from cli.commands import dispatch

    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    context = {
        "chat_history": [],
        "should_exit": False,
        "workspace": workspace_dir,
        "session_name": "default",
        "silent": False,
        "show_thinking": True,
        "prompt_style": "arrow",
        "_controller": None,
    }

    # Initialize controller
    try:
        from kokertechController import KokertechController
        context["_controller"] = KokertechController()
    except (ImportError, OSError, RuntimeError, TypeError, KeyError, ValueError) as e:
        logger.debug(f"Controller init deferred: {e}")

    # Check for CLI arguments / one-shot execution
    raw_args = sys.argv[1:]
    is_interactive = "-i" in raw_args or "--interactive" in raw_args
    filtered_args = [a for a in raw_args if a not in ("-i", "--interactive")]

    if filtered_args:
        first = filtered_args[0].lower()
        if first == "code":
            rest = filtered_args[1:]
            cmd_to_run = f"/code {' '.join(rest)}".strip()
        elif first in ("-c", "--command"):
            cmd_to_run = " ".join(filtered_args[1:]).strip()
        elif first.startswith("/"):
            cmd_to_run = " ".join(filtered_args).strip()
        else:
            cmd_to_run = " ".join(filtered_args).strip()

        if cmd_to_run:
            run_cli_command(cmd_to_run, context)
            if not is_interactive:
                _do_exit(context)
                return

    # Interactive mode startup
    _setup_readline()
    print_banner()
    print_keybinding_bar()
    _auto_resume(context)

    if context.get("_controller") is not None:
        print_system("Connected to KokertechAI Executive Core.")
    else:
        print_system("Starting in CLI mode (Controller offline or standalone).")

    while not context.get("should_exit", False):
        try:
            user_input = _get_input(context)
            if not user_input:
                continue

            # First check if it's a CLI slash command
            if user_input.startswith("/") or user_input.startswith("<<RAG:"):
                should_skip = dispatch(user_input, context)
                if should_skip or context.get("should_exit", False):
                    continue

            # Also check fallback handle_slash_command if needed
            should_skip, transformed = handle_slash_command(user_input)
            if should_skip:
                continue
            actual_input = transformed or user_input

            # Send to controller
            ctrl = context.get("_controller")
            if ctrl is not None:
                _process_with_controller(ctrl, actual_input, context)
            else:
                _process_raw(actual_input, context)

        except (KeyboardInterrupt, EOFError):
            print("\n")
            break
        except Exception as e:
            from cli.formatting import print_error
            print_error(f"Unexpected error: {e}")

    _do_exit(context)
    print_system("Goodbye.")


if __name__ == "__main__":
    main()
