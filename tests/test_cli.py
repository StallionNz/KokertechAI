"""Integration tests for Sprint 15 CLI Tool V2."""
import pytest, os, tempfile, json, sys, io
from unittest.mock import patch, MagicMock

# Fix Windows CP1252 encoding issue when Rich prints UTF-8 chars
try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


# ── ANTI-FRAGILITY re-bind Rich console per test ──────────────────────────
# Sprint 15 audit (2026-07-19): pytest's stdout handling closes the file
# descriptor that ``cli/formatting.py``'s module-level ``Console()`` captured
# at import time. Every ``console.print(...)`` then raises
# ``ValueError: I/O operation on closed file`` for ~65 formatting + dispatch
# tests. Round-1 (rebinding to ``sys.stdout``) failed because pytest closes
# ``sys.stdout`` itself, so any freshly-defaulted Console inherits the same
# closed FD. This iteration routes output through a throwaway
# ``io.StringIO`` buffer (tests don't inspect stdout) and also wraps
# ``switch_theme`` (which re-instantiates Console) so a mid-test theme
# switch never re-binds to closed stdout. Production runtime untouched.
@pytest.fixture(autouse=True)
def _reset_rich_console():
    """Route Rich output through a per-test buffer to dodge the closed-sys.stdout FD.

    ANTI-FRAGILITY (Sprint 15 audit, 2026-07-19): pytest's capture boundaries
    close ``sys.stdout``, and module-level ``cli.formatting.console = Console()``
    captures that FD at import. Every ``console.print(...)`` then raises
    ``ValueError: I/O operation on closed file``. Round-1 (rebind to fresh
    Console bound to ``sys.stdout``) failed because pytest closes
    ``sys.stdout`` itself. Round-2 routes through an ``io.StringIO`` buffer
    (tests don't inspect stdout content) AND wraps ``switch_theme`` so a
    mid-test theme swap can't accidentally rebind to closed stdout. The
    ``finally`` block restores all four module globals (console in two
    modules + switch_theme callable + _current_theme value) so a test that
    triggers a theme change doesn't leak ``_current_theme`` to the next test.

    Companion stricter REGRESSION GUARD:
    ``test_reset_console_relinks_to_fresh_buffer_regression`` in
    ``tests/test_reset_console_relinks_to_fresh_buffer_regression.py``
    mirrors this fixture's StringIO rebinding inline and asserts
    ``cli.formatting.console.file is not closed`` after rebind — a
    contract lock that fires if the fixture pattern regresses to a
    non-StringIO file target that pytest's FDCapture could close mid-test.
    """
    import cli.formatting as _fmt
    import cli.commands as _cmd
    original_fmt_console = _fmt.console
    original_cmd_console = _cmd.console
    original_switch_theme = _fmt.switch_theme
    original_current_theme = _fmt._current_theme

    buf = io.StringIO()
    safe_theme = _fmt._PRESET_THEMES[_fmt._current_theme]

    def _safe_switch_theme(name: str) -> None:
        # Mirror switch_theme()'s contract but pin file=buf so a mid-test
        # theme swap doesn't accidentally rebind to closed sys.stdout.
        theme = _fmt._PRESET_THEMES.get(name, _fmt._PRESET_THEMES["dark"])
        new_console = _fmt.Console(file=buf, theme=theme, highlight=False)
        _fmt.console = new_console
        _fmt._current_theme = name
        _cmd.console = new_console

    fresh = _fmt.Console(file=buf, theme=safe_theme, highlight=False)
    _fmt.console = fresh
    _cmd.console = fresh
    _fmt.switch_theme = _safe_switch_theme

    try:
        yield
    finally:
        _fmt.console = original_fmt_console
        _cmd.console = original_cmd_console
        _fmt.switch_theme = original_switch_theme
        # Restore the theme tag so a test that calls cmd_theme("monokai", ctx)
        # doesn't leak the new tag to subsequent tests within this file or to
        # downstream test files sharing the imported cli.* modules.
        _fmt._current_theme = original_current_theme


class TestDispatch:
    def test_all_registered(self):
        from cli.commands import _HANDLERS
        assert len(_HANDLERS) >= 40
        for c in ["/help","/save","/sessions","/exit","/search","/config","/alias","/theme","/undo","/retry","/repeat","/temp","/ctx","/reset","/fork","/snapshot","/restore","/changelog","/stats","/code"]:
            assert c in _HANDLERS, f'Missing {c}'
    def test_alias_registry(self):
        from cli.commands import _ALIASES
        assert isinstance(_ALIASES, dict)
        _ALIASES["m"] = "/model"
        assert _ALIASES["m"] == "/model"
        del _ALIASES["m"]
    def test_dispatch_help(self):
        from cli.commands import dispatch
        assert dispatch("/help", {"chat_history":[],"should_exit":False,"workspace":"."}) is True
    def test_dispatch_exit(self):
        from cli.commands import dispatch
        ctx = {"chat_history":[],"should_exit":False,"workspace":"."}
        assert dispatch("/exit", ctx) is True
        assert ctx["should_exit"] is True
    def test_dispatch_clear(self):
        from cli.commands import dispatch
        ctx = {"chat_history":[{"role":"user","content":"hi"}],"compressed_summary":"x","should_exit":False,"workspace":"."}
        dispatch("/clear", ctx)
        assert ctx["chat_history"] == []
        assert ctx["compressed_summary"] == ""
    def test_dispatch_unknown(self):
        from cli.commands import dispatch
        assert dispatch("/xyz_nonexistent", {"chat_history":[],"should_exit":False,"workspace":"."}) is True
    def test_non_command(self):
        from cli.commands import dispatch
        assert dispatch("hello", {"chat_history":[],"should_exit":False,"workspace":"."}) is False
    def test_rag_inline(self):
        from cli.commands import dispatch
        ctx = {"chat_history":[],"should_exit":False,"workspace":".","pending_input":None}
        assert dispatch("<<RAG:test>>", ctx) is False

class TestConversationCmds:
    def test_undo(self):
        from cli.commands import cmd_undo
        ctx = {"chat_history":[{"role":"user","content":"a"},{"role":"assistant","content":"b"}],"workspace":"."}
        cmd_undo("", ctx)
        assert len(ctx["chat_history"]) == 0
    def test_undo_empty(self):
        from cli.commands import cmd_undo
        assert cmd_undo("", {"chat_history":[],"workspace":"."}) is True
    def test_pin_unpin(self):
        from cli.commands import cmd_pin, cmd_unpin
        ctx = {"chat_history":[{"role":"user","content":"pinme"}],"workspace":"."}
        cmd_pin("1", ctx)
        assert ctx["pinned_messages"][0]["content"] == "pinme"
        cmd_unpin("", ctx)
        assert "pinned_messages" not in ctx
    def test_system_msg(self):
        from cli.commands import cmd_system
        ctx = {"chat_history":[],"workspace":"."}
        cmd_system("be helpful", ctx)
        assert ctx["one_shot_system"] == "be helpful"
    def test_retry_empty(self):
        from cli.commands import cmd_retry
        assert cmd_retry("", {"chat_history":[],"workspace":"."}) is True

class TestUtilityCmds:
    def test_thinking(self):
        from cli.commands import cmd_thinking
        ctx = {"chat_history":[],"workspace":".","show_thinking":True}
        cmd_thinking("off", ctx)
        assert ctx["show_thinking"] is False
        cmd_thinking("on", ctx)
        assert ctx["show_thinking"] is True
        assert cmd_thinking("", ctx) is True
    def test_eval(self):
        from cli.commands import cmd_eval
        assert cmd_eval("2+2", {"chat_history":[],"workspace":"."}) is True
        assert cmd_eval("", {"chat_history":[],"workspace":"."}) is True
    def test_snapshot_restore(self):
        from cli.commands import cmd_snapshot, cmd_restore
        ctx = {"chat_history":[{"role":"user","content":"snap"}],"workspace":".","session_name":"t"}
        cmd_snapshot("s1", ctx)
        ctx["chat_history"] = []
        cmd_restore("s1", ctx)
        assert ctx["chat_history"][0]["content"] == "snap"
    def test_silent(self):
        from cli.commands import cmd_silent
        ctx = {"chat_history":[],"workspace":"."}
        cmd_silent("on", ctx)
        assert ctx["silent"] is True
        cmd_silent("off", ctx)
        assert ctx["silent"] is False
    def test_prompt(self):
        from cli.commands import cmd_prompt
        ctx = {"chat_history":[],"workspace":"."}
        cmd_prompt("arrow", ctx)
        assert ctx["prompt_style"] == "arrow"
    def test_changelog(self):
        from cli.commands import cmd_changelog
        assert cmd_changelog("", {"chat_history":[],"workspace":"."}) is True
    def test_sql(self, tmp_path):
        import sqlite3
        from cli.commands import cmd_sql
        # Empty args
        assert cmd_sql("", {"workspace": str(tmp_path)}) is True
        # Missing db
        assert cmd_sql("SELECT 1", {"workspace": str(tmp_path)}) is True
        # Valid db and query
        db_file = tmp_path / "kokertech_vault.db"
        with sqlite3.connect(str(db_file)) as conn:
            conn.execute("CREATE TABLE test (id INT, val TEXT)")
            conn.execute("INSERT INTO test VALUES (1, 'hello')")
        ctx = {"workspace": str(tmp_path)}
        assert cmd_sql("SELECT * FROM test", ctx) is True
        # Query with error
        assert cmd_sql("SELECT * FROM nonexistent", ctx) is True
    def test_theme(self):
        from cli.commands import cmd_theme
        assert cmd_theme("list", {"chat_history":[],"workspace":"."}) is True
    def test_compact(self):
        from cli.commands import cmd_compact
        assert cmd_compact("", {"chat_history":[],"workspace":"."}) is True
    def test_stats(self):
        from cli.commands import cmd_stats
        assert cmd_stats("", {"chat_history":[],"workspace":".","session_name":"t"}) is True
    def test_timer_usage(self):
        from cli.commands import cmd_timer
        assert cmd_timer("", {"chat_history":[],"workspace":"."}) is True
    def test_notify(self):
        from cli.commands import cmd_notify
        assert cmd_notify("hi", {"chat_history":[],"workspace":"."}) is True
    def test_vram(self):
        from cli.commands import cmd_vram
        assert cmd_vram("", {"chat_history":[],"workspace":"."}) is True
    def test_sessions_peek(self, monkeypatch):
        from cli.commands import cmd_sessions
        # Missing session
        monkeypatch.setattr("cli.commands.load_session", lambda name: None)
        assert cmd_sessions("peek missing", {}) is True
        # Short session (<= 6 messages)
        short_hist = [{"role": "user", "content": f"msg {i}"} for i in range(4)]
        monkeypatch.setattr("cli.commands.load_session", lambda name: {"chat_history": short_hist})
        assert cmd_sessions("peek short", {}) is True
        # Long session (> 6 messages)
        long_hist = [{"role": "user", "content": f"msg {i}"} for i in range(10)]
        monkeypatch.setattr("cli.commands.load_session", lambda name: {"chat_history": long_hist})
        assert cmd_sessions("peek long", {}) is True
    def test_pipe(self):
        from cli.commands import cmd_pipe
        # Empty args
        assert cmd_pipe("", {}) is True
        # Simple echo command
        ctx = {"workspace": "."}
        assert cmd_pipe("echo hello", ctx) is False
        assert "pending_input" in ctx
    def test_workflow(self, monkeypatch):
        from cli.commands import cmd_workflow
        class MockEngine:
            def list_workflows(self): return ["test.yaml"]
            def execute(self, sub): return {"ok": True}
        monkeypatch.setattr("workflow_engine.WorkflowEngine", MockEngine)
        assert cmd_workflow("list", {}) is True
        assert cmd_workflow("test", {}) is True
    def test_multi(self):
        from cli.commands import cmd_multi
        # Usage check
        assert cmd_multi("too few", {}) is True
        # Mock controller
        class MockCtrl:
            def process_input_multi(self, prompt, specs):
                return [{"label": "A", "ok": True, "output": "test"}]
        ctx = {"_controller": MockCtrl()}
        assert cmd_multi("model_a model_b test prompt", ctx) is True

class TestMemoryGraphCmd:
    def test_memory_empty(self):
        from cli.commands import cmd_memory
        assert cmd_memory("", {"chat_history":[],"workspace":"."}) is True
    def test_memory_with_query(self):
        with patch("memory_vault.hybrid_search", return_value=[]):
            from cli.commands import cmd_memory
            assert cmd_memory("test", {"chat_history":[],"workspace":"."}) is True
    def test_rag_cmd(self):
        from cli.commands import cmd_rag
        ctx = {"chat_history":[],"workspace":".","pending_input":None}
        assert cmd_rag("query", ctx) is False
        assert "<<RAG:query>>" in ctx["pending_input"]
    def test_graph_stats(self):
        with patch("memory_vault.get_graph_stats", return_value={"nodes":5}):
            from cli.commands import cmd_graph
            assert cmd_graph("stats", {"chat_history":[],"workspace":"."}) is True
    def test_graph_empty(self):
        with patch("memory_vault.get_graph_stats", return_value={}):
            from cli.commands import cmd_graph
            assert cmd_graph("", {"chat_history":[],"workspace":"."}) is True
    def test_audit_no_history(self):
        from cli.commands import cmd_audit
        assert cmd_audit("", {"chat_history":[],"workspace":"."}) is True
    def test_log_tail(self):
        from cli.commands import cmd_log
        assert cmd_log("tail", {"chat_history":[],"workspace":"."}) is True

class TestConfig:
    def test_config_read(self):
        with patch("config.CONFIG", {"key":"val"}):
            from cli.commands import cmd_config
            assert cmd_config("key", {"chat_history":[],"workspace":"."}) is True
    def test_config_list(self):
        with patch("config.CONFIG", {"a":1}):
            from cli.commands import cmd_config
            assert cmd_config("", {"chat_history":[],"workspace":"."}) is True
    def test_config_write(self):
        with patch("config.CONFIG", {"k":"old"}), patch("config.save_settings"):
            from cli.commands import cmd_config
            from config import CONFIG
            assert cmd_config("k new", {"chat_history":[],"workspace":"."}) is True
            assert CONFIG["k"] == "new"
    def test_config_unknown(self):
        with patch("config.CONFIG", {}):
            from cli.commands import cmd_config
            assert cmd_config("bad", {"chat_history":[],"workspace":"."}) is True

class TestRegisterDecorator:
    def test_register(self):
        from cli.commands import register, _HANDLERS
        @register("/__t__")
        def _fn(a, c): return True
        assert "/__t__" in _HANDLERS
        del _HANDLERS["/__t__"]
    def test_multi_register(self):
        from cli.commands import register, _HANDLERS
        @register("/__x__")
        @register("/__y__")
        def _fn(a, c): return True
        assert "/__x__" in _HANDLERS and "/__y__" in _HANDLERS
        del _HANDLERS["/__x__"]; del _HANDLERS["/__y__"]

class TestSessionCmds:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        import cli.session as s, cli.commands as c
        self.td = tempfile.mkdtemp(prefix="clitest_")
        monkeypatch.setattr(s, "SESSIONS_DIR", self.td)
        monkeypatch.setattr(s, "ARCHIVE_DIR", os.path.join(self.td, "archive"))
        monkeypatch.setattr(s, "BACKUP_DIR", os.path.join(self.td, "backups"))
        monkeypatch.setattr(s, "TEMPLATES_DIR", os.path.join(self.td, "templates"))
        monkeypatch.setattr(c, "SESSIONS_DIR", self.td)
        yield
        import shutil; shutil.rmtree(self.td, ignore_errors=True)
    def test_save_list(self):
        from cli.commands import cmd_save, cmd_sessions
        ctx = {"chat_history":[{"role":"user","content":"h"}],"workspace":".","compressed_summary":"","session_name":"s1"}
        cmd_save("s1 --tag=test", ctx)
        assert cmd_sessions("", {"chat_history":[],"workspace":".","should_exit":False}) is True
    def test_save_load(self):
        from cli.commands import cmd_save, cmd_load
        ctx = {"chat_history":[{"role":"user","content":"loadme"}],"workspace":".","compressed_summary":"sum","session_name":"rt"}
        cmd_save("rt", ctx)
        ctx2 = {"chat_history":[],"workspace":".","compressed_summary":"","should_exit":False}
        cmd_load("rt", ctx2)
        assert ctx2["chat_history"][0]["content"] == "loadme"
    def test_load_nonexistent(self):
        from cli.commands import cmd_load
        assert cmd_load("noexist_xyz", {"chat_history":[],"workspace":".","should_exit":False}) is True
    def test_load_merge(self):
        from cli.commands import cmd_save, cmd_load
        cmd_save("base", {"chat_history":[{"role":"user","content":"b"}],"workspace":".","compressed_summary":"","session_name":"base"})
        cmd_save("merge", {"chat_history":[{"role":"user","content":"m"}],"workspace":".","compressed_summary":"","session_name":"merge"})
        ctx = {"chat_history":[{"role":"assistant","content":"e"}],"workspace":".","compressed_summary":"","session_name":"base"}
        cmd_load("--merge merge", ctx)
        assert len(ctx["chat_history"]) >= 2
    def test_sessions_peek(self):
        from cli.commands import cmd_save, cmd_sessions
        cmd_save("peek_test", {"chat_history":[{"role":"user","content":"p"}],"workspace":".","compressed_summary":"","session_name":"peek_test"})
        cmd_sessions("peek peek_test", {"chat_history":[],"workspace":".","should_exit":False})
    def test_sessions_delete(self):
        from cli.commands import cmd_save, cmd_sessions
        cmd_save("del_me", {"chat_history":[{"role":"user","content":"d"}],"workspace":".","compressed_summary":"","session_name":"del_me"})
        with patch("builtins.input", return_value="y"):
            cmd_sessions("delete del_me", {"chat_history":[],"workspace":".","should_exit":False})
    def test_sessions_rename(self):
        from cli.commands import cmd_save, cmd_sessions
        cmd_save("old", {"chat_history":[{"role":"user","content":"r"}],"workspace":".","compressed_summary":"","session_name":"old"})
        cmd_sessions("rename old new", {"chat_history":[],"workspace":".","should_exit":False})
    def test_sessions_archive(self):
        from cli.commands import cmd_save, cmd_sessions
        cmd_save("arch", {"chat_history":[{"role":"user","content":"a"}],"workspace":".","compressed_summary":"","session_name":"arch"})
        cmd_sessions("archive arch", {"chat_history":[],"workspace":".","should_exit":False})
    def test_sessions_copy(self):
        from cli.commands import cmd_save, cmd_sessions
        cmd_save("src", {"chat_history":[{"role":"user","content":"c"}],"workspace":".","compressed_summary":"","session_name":"src"})
        cmd_sessions("copy src dst", {"chat_history":[],"workspace":".","should_exit":False})

class TestForkTab:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        import cli.session as s, cli.commands as c
        self.td = tempfile.mkdtemp(prefix="clifork_")
        monkeypatch.setattr(s, "SESSIONS_DIR", self.td)
        monkeypatch.setattr(c, "SESSIONS_DIR", self.td)
        yield
        import shutil; shutil.rmtree(self.td, ignore_errors=True)
    def test_fork(self):
        from cli.commands import cmd_fork
        ctx = {"chat_history":[{"role":"user","content":"f"}],"workspace":".","compressed_summary":"","session_name":"p","tabs":[],"_active_tab":-1}
        cmd_fork("forked", ctx)
        assert len(ctx["tabs"]) == 1
    def test_tab_list(self):
        from cli.commands import _handle_tab
        assert _handle_tab("/tab list", {"chat_history":[],"workspace":".","tabs":[]}) is True
    def test_tab_switch(self):
        from cli.commands import _handle_tab
        ctx = {"chat_history":[],"workspace":".","tabs":[
            {"session_name":"t0","chat_history":[{"role":"user","content":"m0"}]},
            {"session_name":"t1","chat_history":[{"role":"user","content":"m1"}]}],
            "_active_tab":0}
        _handle_tab("/tab switch 1", ctx)
        assert ctx["chat_history"][0]["content"] == "m1"
    def test_tab_close(self):
        from cli.commands import _handle_tab
        ctx = {"chat_history":[],"workspace":".","tabs":[
            {"session_name":"t0","chat_history":[{"role":"assistant","content":"a"}]}],
            "_active_tab":0}
        _handle_tab("/tab close 0", ctx)
        assert len(ctx["tabs"]) == 0

class TestSessionAPI:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        import cli.session as s
        self.td = tempfile.mkdtemp(prefix="clsess_")
        monkeypatch.setattr(s, "SESSIONS_DIR", self.td)
        monkeypatch.setattr(s, "ARCHIVE_DIR", os.path.join(self.td, "archive"))
        monkeypatch.setattr(s, "BACKUP_DIR", os.path.join(self.td, "backups"))
        monkeypatch.setattr(s, "TEMPLATES_DIR", os.path.join(self.td, "templates"))
        yield
        import shutil; shutil.rmtree(self.td, ignore_errors=True)
    def test_save_load(self):
        from cli.session import save_session, load_session
        save_session("sl", [{"role":"user","content":"test"}], "summary", ["tag1"])
        d = load_session("sl")
        assert d["message_count"] == 1 and d["tags"] == ["tag1"]
    def test_load_none(self):
        from cli.session import load_session
        assert load_session("noexist") is None
    def test_list(self):
        from cli.session import save_session, list_sessions
        assert list_sessions() == []
        save_session("a", [{"role":"user","content":"x"}])
        save_session("b", [{"role":"user","content":"y"}])
        assert len(list_sessions()) == 2
    def test_tag_filter(self):
        from cli.session import save_session, list_sessions
        save_session("tagged", [{"role":"user","content":"x"}], tags=["urgent"])
        save_session("plain", [{"role":"user","content":"y"}])
        assert len(list_sessions(tag_filter="urgent")) == 1
    def test_favorites(self):
        from cli.session import save_session, toggle_favorite, list_sessions
        save_session("fav", [{"role":"user","content":"f"}])
        toggle_favorite("fav")
        assert list_sessions(favorites_only=True)[0]["favorite"] is True
    def test_search(self):
        from cli.session import save_session, search_sessions
        save_session("alpha", [{"role":"user","content":"hello world"}])
        assert len(search_sessions("hello")) == 1
    def test_delete(self):
        from cli.session import save_session, delete_session, load_session
        save_session("del", [{"role":"user","content":"x"}])
        assert delete_session("del") is True
        assert load_session("del") is None
    def test_rename(self):
        from cli.session import save_session, rename_session, load_session
        save_session("old", [{"role":"user","content":"x"}])
        assert rename_session("old", "new") is True
        assert load_session("new") is not None
    def test_copy(self):
        from cli.session import save_session, copy_session, load_session
        save_session("src", [{"role":"user","content":"c"}])
        assert copy_session("src", "dst") is True
        assert load_session("dst")["chat_history"][0]["content"] == "c"
    def test_merge(self):
        from cli.session import save_session, merge_session
        save_session("base", [{"role":"user","content":"b"}])
        save_session("m", [{"role":"user","content":"m"}])
        merged = merge_session("base", "m")
        assert merged and len(merged["chat_history"]) == 2
    def test_archive(self):
        from cli.session import save_session, archive_session, unarchive_session
        save_session("arch", [{"role":"user","content":"a"}])
        assert archive_session("arch") is True
        assert unarchive_session("arch") is True
    def test_integrity(self):
        from cli.session import save_session, integrity_check
        save_session("ok", [{"role":"user","content":"d"}])
        r = integrity_check()
        assert r["ok"] >= 1 and r["corrupt"] == []
    def test_templates(self):
        from cli.session import save_session, session_to_template, session_from_template, list_templates
        save_session("tmpl", [{"role":"system","content":"sys"},{"role":"user","content":"q"}])
        assert session_to_template("tmpl", "t1") is True
        assert "t1" in list_templates()
        d = session_from_template("t1", "new")
        assert d and d["chat_history"][0]["content"] == "sys"
    def test_notes(self):
        from cli.session import save_session, set_session_notes, get_session_notes
        save_session("noted", [{"role":"user","content":"x"}])
        set_session_notes("noted", "my notes")
        assert get_session_notes("noted") == "my notes"
    def test_wordcount(self):
        from cli.session import save_session, get_session_word_count
        save_session("wc", [{"role":"user","content":"one two three"}])
        assert get_session_word_count("wc") == 3

class TestFormatting:
    def test_console(self):
        from cli.formatting import console
        assert console is not None
    def test_themes(self):
        from cli.formatting import _PRESET_THEMES, switch_theme
        assert set(_PRESET_THEMES.keys()) == {"dark","light","monokai","one-dark","dracula"}
        for n in _PRESET_THEMES: switch_theme(n)
    def test_print_basic(self):
        from cli.formatting import print_system, print_error, print_success
        print_system("s"); print_error("e"); print_success("ok")
    def test_print_thinking(self):
        from cli.formatting import print_thinking
        print_thinking("think"); print_thinking(""); print_thinking("  ")
    def test_print_response(self):
        from cli.formatting import print_response
        print_response("**bold**"); print_response("```py\nprint(1)\n```")
    def test_print_code_block(self):
        from cli.formatting import print_code_block
        print_code_block("def f(): pass", "python")
    def test_print_json_pretty(self):
        from cli.formatting import print_json_pretty
        print_json_pretty({"a": 1})
    def test_print_banner(self):
        from cli.formatting import print_banner
        print_banner()
    def test_print_help(self):
        from cli.formatting import print_help
        print_help()
    def test_print_model_info(self):
        from cli.formatting import print_model_info
        print_model_info({"provider":"ll","model_name":"m.gguf","model_loaded":True,"n_ctx":4096,"n_gpu_layers":0,"vram_used_mb":100,"latency_ms":50})
        print_model_info({"provider":"ll","model_name":"m.gguf","model_loaded":False})
    def test_print_model_list(self):
        from cli.formatting import print_model_list
        print_model_list([{"name":"a.gguf","size_bytes":10**9,"dir":"m/"}])
        print_model_list([])
    def test_print_sessions(self):
        from cli.formatting import print_sessions
        print_sessions([{"name":"s","message_count":5,"tags":["t"],"favorite":True,"word_count":100,"created":"d","updated":"d"}])
        print_sessions([])
    def test_print_memory_results(self):
        from cli.formatting import print_memory_results
        print_memory_results([("id","src","content",0.9)])
        print_memory_results([])
    def test_print_search_results(self):
        from cli.formatting import print_search_results
        print_search_results([{"file":"f.py","line":1,"text":"def f():"}], "f")
        print_search_results([], "f")
    def test_print_diff_view(self):
        from cli.formatting import print_diff_view
        print_diff_view(""); print_diff_view("+added")
    def test_print_token_footer(self):
        from cli.formatting import print_token_footer
        print_token_footer(tokens_out=100, elapsed_ms=2000, model="m.gguf")
        print_token_footer()
    def test_print_context_gauge(self):
        from cli.formatting import print_context_gauge
        print_context_gauge(1500, 4096)
    def test_print_audit_report(self):
        from cli.formatting import print_audit_report
        print_audit_report({"issues": 0})
    def test_print_log_entries(self):
        from cli.formatting import print_log_entries
        print_log_entries(["2026-07-06 test"], "test")
        print_log_entries([], "e")
    def test_print_test_results(self):
        from cli.formatting import print_test_results
        print_test_results("test_a PASSED\ntest_b FAILED", "p")
    def test_print_web_preview(self):
        from cli.formatting import print_web_preview
        print_web_preview("https://x.com", "content")
    def test_print_tree(self):
        from cli.formatting import print_tree
        print_tree("f1.py\nf2.py", "/tmp")
    def test_print_changelog(self):
        from cli.formatting import print_changelog
        print_changelog(["## v1", "### fix", "- bug"])
    def test_print_notification_toast(self):
        from cli.formatting import print_notification_toast
        print_notification_toast("hello")
    def test_print_status_bar(self):
        from cli.formatting import print_status_bar
        print_status_bar({"chat_history":[{"role":"user","content":"h"}],"session_name":"s","show_thinking":True})
    def test_print_rainbow(self):
        from cli.formatting import print_rainbow_divider
        print_rainbow_divider()
    def test_print_keybinding(self):
        from cli.formatting import print_keybinding_bar
        print_keybinding_bar()
    def test_print_separator(self):
        from cli.formatting import print_message_separator
        print_message_separator()
    def test_print_prompt_style(self):
        from cli.formatting import print_prompt_style
        for s in ("arrow","minimal","verbose","none"):
            print_prompt_style(s)
    def test_print_breadcrumb(self):
        from cli.formatting import print_breadcrumb
        print_breadcrumb("path")
    def test_print_persona_list(self):
        from cli.formatting import print_persona_list
        print_persona_list({"Coder":"writes code"}, "Coder")
        print_persona_list({})
    def test_print_session_stats(self):
        from cli.formatting import print_session_stats
        print_session_stats({"chat_history":[{"role":"user","content":"h"}],"session_name":"s","compressed_summary":"","pinned_messages":[]})
    def test_print_export_done(self):
        from cli.formatting import print_export_done
        print_export_done("/tmp/x.md", "md", 5)
    def test_rag_progress(self):
        from cli.formatting import create_rag_progress
        assert create_rag_progress() is not None
    def test_print_user(self):
        from cli.formatting import print_user
        print_user("hi", turn_num=3)
    def test_streaming(self):
        from cli.formatting import print_streaming_header, print_stream_token
        print_streaming_header(1); print_stream_token("hi")
        # Bare RC().print() defaulted to sys.stdout, which pytest closes at
        # capture boundaries → ValueError. Pin to an in-memory buffer so the
        # test still exercises Console constructor + empty-print path.
        from rich.console import Console as RC
        import io
        RC(file=io.StringIO()).print()
