"""tests/test_cli_agent.py — Sprint 18: CLI Coding Agent & Diff Viewer tests.

Covers:
- cli/diff_viewer.py (diff computation, summarization, rendering, confirmation)
- cli/agent.py (CodingAgent, undo/redo checkpoints, fuzzy file picker, AST, tasks)
- cli/commands.py (@register("/code") and subcommands: help, context, files, ast, search, diff, status, undo, redo, task)
- kokertech_terminal.py (CLI argument entrypoint)
"""

from __future__ import annotations

import io
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

import cli.commands as _cmd
import cli.formatting as _fmt
from cli.agent import CodingAgent
from cli.diff_viewer import compute_unified_diff, confirm_diff, render_diff, summarize_diff
from cli.project_context import ProjectContext
from kokertech_terminal import main as terminal_main, run_cli_command


@pytest.fixture(autouse=True)
def _reset_rich_console():
    """Route Rich output through a per-test StringIO buffer to dodge closed stdout."""
    original_fmt_console = _fmt.console
    original_cmd_console = _cmd.console

    buf = io.StringIO()
    fresh = _fmt.Console(file=buf, theme=_fmt._PRESET_THEMES["dark"], highlight=False)
    _fmt.console = fresh
    _cmd.console = fresh

    try:
        yield
    finally:
        _fmt.console = original_fmt_console
        _cmd.console = original_cmd_console


class TestDiffViewer:
    """Test unified diff calculation, summary metrics, and rendering."""

    def test_compute_unified_diff(self):
        old = "def hello():\n    return 'old'\n"
        new = "def hello():\n    return 'new'\n"
        diff = compute_unified_diff(old, new, from_file="test.py", to_file="test.py")
        assert "--- a/test.py" in diff
        assert "+++ b/test.py" in diff
        assert "-    return 'old'" in diff
        assert "+    return 'new'" in diff

    def test_summarize_diff(self):
        diff = (
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def foo():\n"
            "-    return 1\n"
            "+    return 2\n"
            "+    # extra\n"
        )
        summary = summarize_diff(diff)
        assert summary["additions"] == 2
        assert summary["deletions"] == 1
        assert "foo.py" in summary["files_changed"]
        assert summary["total_hunks"] == 1

    def test_summarize_diff_empty(self):
        summary = summarize_diff("")
        assert summary["additions"] == 0
        assert summary["deletions"] == 0
        assert summary["files_changed"] == []

    def test_render_diff(self):
        diff = (
            "--- a/bar.py\n"
            "+++ b/bar.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-a = 1\n"
            "+a = 2\n"
        )
        # Should not raise exception
        render_diff(diff, title="Test Diff")
        render_diff("", title="Empty Diff")

    def test_confirm_diff(self):
        diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-1\n+2\n"
        # auto_confirm=True should immediately return True
        assert confirm_diff(diff, auto_confirm=True) is True
        # non-tty should return default_yes
        with patch("sys.stdin.isatty", return_value=False):
            assert confirm_diff(diff, default_yes=True) is True
            assert confirm_diff(diff, default_yes=False) is False


class TestCodingAgent:
    """Test CodingAgent operations, undo/redo stack, and fuzzy finder."""

    def test_agent_initialization(self, tmp_path):
        agent = CodingAgent(workspace=str(tmp_path))
        assert agent.workspace == os.path.abspath(str(tmp_path))
        assert isinstance(agent.project_context, ProjectContext)
        assert agent.can_undo is False
        assert agent.can_redo is False

    def test_system_prompt_contains_context(self, tmp_path):
        # Create marker to detect Python
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'", encoding="utf-8")
        agent = CodingAgent(workspace=str(tmp_path))
        prompt = agent.build_system_prompt()
        assert "python" in prompt.lower()
        assert str(tmp_path) in prompt

    def test_fuzzy_find_files(self, tmp_path):
        (tmp_path / "app.py").write_text("# app", encoding="utf-8")
        (tmp_path / "models.py").write_text("# models", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "util_helper.py").write_text("# util", encoding="utf-8")

        # Ignored dir
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("# git config", encoding="utf-8")

        agent = CodingAgent(workspace=str(tmp_path))
        all_files = agent.fuzzy_find_files("")
        assert len(all_files) == 3
        assert not any(".git" in f for f in all_files)

        helper_matches = agent.fuzzy_find_files("helper")
        assert len(helper_matches) >= 1
        assert "sub/util_helper.py" in helper_matches[0]

    def test_write_and_edit_with_checkpoint(self, tmp_path):
        agent = CodingAgent(workspace=str(tmp_path))
        rel_file = "module.py"

        # 1. Write file
        ok, msg = agent.write_file_with_checkpoint(rel_file, "val = 10\n", description="init")
        assert ok is True
        assert (tmp_path / rel_file).read_text(encoding="utf-8") == "val = 10\n"
        assert agent.can_undo is True

        # 2. Edit file
        ok, msg = agent.edit_file_with_checkpoint(rel_file, "10", "20", description="update val")
        assert ok is True
        assert (tmp_path / rel_file).read_text(encoding="utf-8") == "val = 20\n"

    def test_undo_and_redo_lifecycle(self, tmp_path):
        agent = CodingAgent(workspace=str(tmp_path))
        rel_file = "service.py"

        # Step 1: Create file
        agent.write_file_with_checkpoint(rel_file, "x = 1\n")
        assert (tmp_path / rel_file).exists()

        # Step 2: Edit file
        agent.edit_file_with_checkpoint(rel_file, "1", "2")
        assert (tmp_path / rel_file).read_text(encoding="utf-8") == "x = 2\n"

        # Undo edit -> should revert to x = 1
        ok, msg = agent.undo()
        assert ok is True
        assert (tmp_path / rel_file).read_text(encoding="utf-8") == "x = 1\n"
        assert agent.can_redo is True

        # Redo edit -> should reapply x = 2
        ok, msg = agent.redo()
        assert ok is True
        assert (tmp_path / rel_file).read_text(encoding="utf-8") == "x = 2\n"

        # Undo edit again
        agent.undo()
        # Undo creation -> file should be deleted
        ok, msg = agent.undo()
        assert ok is True
        assert not (tmp_path / rel_file).exists()

        # Redo creation -> file restored
        ok, msg = agent.redo()
        assert ok is True
        assert (tmp_path / rel_file).exists()
        assert (tmp_path / rel_file).read_text(encoding="utf-8") == "x = 1\n"

    def test_analyze_ast(self, tmp_path):
        code = "class Calculator:\n    def add(self, a, b):\n        return a + b\n"
        (tmp_path / "calc.py").write_text(code, encoding="utf-8")
        agent = CodingAgent(workspace=str(tmp_path))
        ast_info = agent.analyze_ast("calc.py")
        assert ast_info["syntax_valid"] is True
        assert any(c["name"] == "Calculator" for c in ast_info["classes"])
        assert any(f["name"] == "add" for f in ast_info["functions"])

    def test_run_task_offline_and_controller(self, tmp_path):
        agent = CodingAgent(workspace=str(tmp_path))
        res = agent.run_task("Build greeting module")
        assert res["status"] == "success"
        assert "greeting" in res["task"].lower()

        # Test with mock controller
        mock_ctrl = MagicMock()
        mock_ctrl.process_input.return_value = {"final": "Done!"}
        agent_with_ctrl = CodingAgent(workspace=str(tmp_path), controller=mock_ctrl)
        res_ctrl = agent_with_ctrl.run_task("Build greeting module")
        assert res_ctrl["status"] == "success"
        assert res_ctrl["response"] == "Done!"
        mock_ctrl.process_input.assert_called_once()


class TestCodeCommandDispatch:
    """Test slash command /code and all its subcommands."""

    def test_code_help(self):
        ctx = {"workspace": "."}
        assert _cmd.dispatch("/code help", ctx) is True
        assert _cmd.dispatch("/code", ctx) is True

    def test_code_alias_c(self):
        ctx = {"workspace": "."}
        assert _cmd.dispatch("/c help", ctx) is True

    def test_code_context(self, tmp_path):
        ctx = {"workspace": str(tmp_path)}
        assert _cmd.dispatch("/code context", ctx) is True

    def test_code_files(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
        ctx = {"workspace": str(tmp_path)}
        assert _cmd.dispatch("/code files main", ctx) is True

    def test_code_ast(self, tmp_path):
        (tmp_path / "foo.py").write_text("def test_fn(): pass", encoding="utf-8")
        ctx = {"workspace": str(tmp_path)}
        assert _cmd.dispatch("/code ast foo.py", ctx) is True

    def test_code_search(self, tmp_path):
        (tmp_path / "sample.py").write_text("TARGET_PHRASE = 42", encoding="utf-8")
        ctx = {"workspace": str(tmp_path)}
        assert _cmd.dispatch("/code search TARGET_PHRASE", ctx) is True

    def test_code_diff_and_status(self, tmp_path):
        ctx = {"workspace": str(tmp_path)}
        assert _cmd.dispatch("/code diff", ctx) is True
        assert _cmd.dispatch("/code status", ctx) is True

    def test_code_undo_redo(self, tmp_path):
        ctx = {"workspace": str(tmp_path)}
        # Empty stack should handle gracefully
        assert _cmd.dispatch("/code undo", ctx) is True
        assert _cmd.dispatch("/code redo", ctx) is True

        agent = ctx["_coding_agent"]
        agent.write_file_with_checkpoint("demo.txt", "abc")
        assert _cmd.dispatch("/code undo", ctx) is True
        assert _cmd.dispatch("/code redo", ctx) is True

    def test_code_task_subcommand(self, tmp_path):
        ctx = {"workspace": str(tmp_path)}
        assert _cmd.dispatch("/code task Add logging helper", ctx) is True
        assert _cmd.dispatch("/code Add logging helper", ctx) is True


class TestTerminalCLIInvocation:
    """Test CLI argument entrypoint in kokertech_terminal.py."""

    def test_run_cli_command_direct(self, tmp_path):
        ctx = {"workspace": str(tmp_path), "chat_history": []}
        run_cli_command("/code help", ctx)
        run_cli_command("hello world", ctx)

    def test_terminal_main_code_argument(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "argv", ["kokertech_terminal.py", "code", "help"])
        with patch("kokertech_terminal._do_exit") as mock_exit:
            terminal_main()
            mock_exit.assert_called_once()
