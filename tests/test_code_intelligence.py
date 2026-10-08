"""Sprint 16: CodeIntelligence service tests.

6 test classes covering all 9 public methods + path traversal + json parsing
+ subprocess-mocked test/linter/git_status pipelines.

Cross-file hygiene: CodeIntelligence has no module-level mutable state (rg
cache is per-instance; linter_cmd injectable). The shared
conftest._reset_all_shared_state() pattern is sufficient for isolation.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.code_intelligence import CodeIntelligence


def _make_ci(tmpdir=None, linter_cmd="ruff"):
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp(prefix="ci_test_")
    return CodeIntelligence(workspace=str(tmpdir), linter_cmd=linter_cmd)


class TestFileOps(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ci_test_"))
        self.ci = _make_ci(self.tmpdir)
        self.sample = self.tmpdir / "sample.py"
        self.sample.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_read_file_happy(self):
        text = self.ci.read_file("sample.py")
        self.assertEqual(text, "a\nb\nc\nd\ne\n")

    def test_read_file_range(self):
        text = self.ci.read_file("sample.py", start_line=2, end_line=4)
        self.assertEqual(text, "b\nc\nd\n")

    def test_read_file_past_eof_returns_empty(self):
        text = self.ci.read_file("sample.py", start_line=100)
        self.assertEqual(text, "")

    def test_read_file_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.ci.read_file("does_not_exist.py")

    def test_read_file_path_traversal_blocked(self):
        with self.assertRaises(ValueError):
            self.ci.read_file("../etc/passwd")

    def test_write_file_creates_dirs(self):
        target = self.tmpdir / "a" / "b" / "c.py"
        ok = self.ci.write_file("a/b/c.py", "x = 1\n")
        self.assertTrue(ok)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_write_file_traversal_blocked(self):
        with self.assertRaises(ValueError):
            self.ci.write_file("../../../tmp/x.py", "x")

    def test_edit_file_happy(self):
        ok = self.ci.edit_file("sample.py", "b", "B")
        self.assertTrue(ok)
        self.assertEqual(self.sample.read_text(encoding="utf-8"), "a\nB\nc\nd\ne\n")

    def test_edit_file_not_found_raises(self):
        with self.assertRaises(ValueError):
            self.ci.edit_file("sample.py", "ZZZZ", "Z")

    def test_edit_file_multi_match_default_raises(self):
        (self.tmpdir / "multi.py").write_text("foo\nfoo\nbar\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.ci.edit_file("multi.py", "foo", "FOO")

    def test_edit_file_multi_match_allowed(self):
        multi = self.tmpdir / "multi.py"
        multi.write_text("foo\nfoo\nbar\n", encoding="utf-8")
        ok = self.ci.edit_file("multi.py", "foo", "FOO", allow_multiple=True)
        self.assertTrue(ok)
        self.assertEqual(multi.read_text(encoding="utf-8"), "FOO\nFOO\nbar\n")


    def test_path_traversal_blocks_symlink(self):
        """ANTI-FRAGILITY: symlink inside workspace pointing to file outside is rejected.

        Locks the os.path.realpath defense in CodeIntelligence._validate_path
        (services/code_intelligence.py ~line 57). A symlink at /workspace/escape.txt
        pointing to /tmp/sentinel.txt must raise ValueError when read via
        ci.read_file('escape.txt'), even though the relative path appears valid.
        Without realpath, the symlink would let the agent read /tmp files.
        Skips if os.symlink() is not permitted (Windows without elevation).
        """
        out_dir = Path(tempfile.mkdtemp(prefix="ci_test_out_"))
        sentinel = out_dir / "sentinel.txt"
        sentinel.write_text("sensitive content", encoding="utf-8")
        symlink_path = self.tmpdir / "link_to_sentinel"
        try:
            os.symlink(str(sentinel), str(symlink_path))
        except OSError as e:
            shutil.rmtree(out_dir, ignore_errors=True)
            self.skipTest("os.symlink not permitted on this platform/privilege: " + str(e))
        try:
            with self.assertRaisesRegex(ValueError, "escapes workspace"):
                self.ci.read_file("link_to_sentinel")
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    def test_path_traversal_blocks_realpath_escape(self):
        """ANTI-FRAGILITY: explicit absolute path to a file OUTSIDE workspace is rejected.

        Companion to test_path_traversal_blocks_symlink. Same defense
        (_validate_path with os.path.realpath at services/code_intelligence.py ~line 57)
        but bypassed without the symlink: caller passes an absolute path to a
        file in a sibling directory. The commonpath(comparing check_resolved vs
        check_ws) returns the shared ancestor prefix, not check_ws itself,
        so ValueError is raised.
        """
        out_dir = Path(tempfile.mkdtemp(prefix="ci_test_out_"))
        sentinel = out_dir / "sentinel.txt"
        sentinel.write_text("sensitive content", encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "escapes workspace"):
                self.ci.read_file(str(sentinel))
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)


class TestSearchCode(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ci_test_"))
        self.ci = _make_ci(self.tmpdir)
        (self.tmpdir / "a.py").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        (self.tmpdir / "sub").mkdir()
        (self.tmpdir / "sub" / "b.py").write_text("alpha\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_search_empty_pattern_raises(self):
        with self.assertRaises(ValueError):
            self.ci.search_code("")

    def test_search_invalid_regex_raises(self):
        with self.assertRaises(ValueError):
            self.ci.search_code("[unclosed")

    def test_search_python_re_fallback_finds_matches(self):
        self.ci._rg_path = None
        results = self.ci.search_code("alpha")
        paths = {r["path"] for r in results}
        self.assertTrue(any(p.endswith("a.py") for p in paths))
        self.assertTrue(any(p.endswith("b.py") for p in paths))

    def test_search_with_ripgrep_parses_json(self):
        fake_json_line = (
            '{"type":"match","data":{"path":{"text":"a.py"},'
            '"line_number":2,"submatches":[{"match":{"text":"beta"}}]}}'
        )
        mock_proc = MagicMock(returncode=0, stdout=fake_json_line + "\n", stderr="")
        with patch("services.code_intelligence.run_with_limits", return_value=mock_proc):
            self.ci._rg_path = shutil.which("rg") or "/usr/bin/rg"
            results = self.ci.search_code("beta")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "a.py")
        self.assertEqual(results[0]["line"], 2)


    def test_search_skips_binary_files(self):
        """Sprint 16 polish: null-byte probe in first 8KB skips binary files."""
        from pathlib import Path as _P
        binary = self.tmpdir / "data.bin"
        # NUL bytes in first 8KB = binary signature, regardless of extension
        binary.write_bytes(b"\x00\x01\x02\x03\x00alpha\x00\x00\x00")
        text = self.tmpdir / "safe.py"
        text.write_text("alpha appears here\n", encoding="utf-8")
        self.ci._rg_path = None  # force python re fallback
        results = self.ci.search_code("alpha")
        paths = {r["path"] for r in results}
        self.assertTrue(any(p.endswith("safe.py") for p in paths))
        self.assertFalse(any(p.endswith("data.bin") for p in paths))


class TestAnalyzeAst(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ci_test_"))
        self.ci = _make_ci(self.tmpdir)
        self.src = self.tmpdir / "mod.py"
        self.src.write_text(
            '"""Module docstring."""\n'
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "def foo(x: int) -> str:\n"
            '    """Foo docstring."""\n'
            "    return str(x)\n"
            "\n"
            "class Bar:\n"
            '    """Bar docstring."""\n'
            "    def method(self):\n"
            "        return 1\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_analyze_ast_functions(self):
        result = self.ci.analyze_ast("mod.py")
        funcs = {f["name"] for f in result["functions"]}
        self.assertIn("foo", funcs)
        self.assertIn("method", funcs)
        foo = next(f for f in result["functions"] if f["name"] == "foo")
        self.assertEqual(foo["args"], ["x"])
        self.assertEqual(foo["returns"], "str")
        self.assertEqual(foo["docstring"], "Foo docstring.")

    def test_analyze_ast_classes_with_methods(self):
        result = self.ci.analyze_ast("mod.py")
        classes = {c["name"]: c for c in result["classes"]}
        self.assertIn("Bar", classes)
        self.assertEqual(classes["Bar"]["docstring"], "Bar docstring.")
        self.assertTrue(any(m["name"] == "method" for m in classes["Bar"]["methods"]))

    def test_analyze_ast_imports(self):
        result = self.ci.analyze_ast("mod.py")
        modules = {imp["module"] for imp in result["imports"]}
        self.assertIn("os", modules)
        self.assertIn("pathlib", modules)

    def test_analyze_ast_syntax_error_forwards(self):
        bad = self.tmpdir / "bad.py"
        bad.write_text("def foo(:\n", encoding="utf-8")
        with self.assertRaises(SyntaxError):
            self.ci.analyze_ast("bad.py")


    def test_analyze_ast_encoding_fallback(self):
        """Sprint 16 polish: UTF-8 first, latin-1 fallback for legacy .py."""
        legacy = self.tmpdir / "legacy.py"
        # PEP 263 latin-1: 'é' = 0xE9 single byte, would fail under UTF-8 strict
        legacy.write_bytes(
            b'# -*- coding: latin-1 -*-\nname = "caf\xe9"\n\ndef greet():\n    return name\n'
        )
        result = self.ci.analyze_ast("legacy.py")
        funcs = {f["name"] for f in result["functions"]}
        self.assertIn("greet", funcs)


class TestSubprocessIntegrations(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ci_test_"))
        self.ci = _make_ci(self.tmpdir, linter_cmd="ruff")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_run_tests_pytest_when_conftest_present(self):
        (self.tmpdir / "conftest.py").write_text("", encoding="utf-8")
        mock_proc = MagicMock(returncode=0, stdout="passed", stderr="")
        with patch("services.code_intelligence.run_with_limits", return_value=mock_proc) as m:
            out = self.ci.run_tests()
        self.assertEqual(out["runner"], "pytest")
        self.assertEqual(out["returncode"], 0)
        self.assertEqual(out["stdout"], "passed")
        self.assertIn("pytest", m.call_args[0][0])

    def test_run_tests_unittest_when_no_pytest_markers(self):
        mock_proc = MagicMock(returncode=0, stdout="ran 1", stderr="")
        with patch("services.code_intelligence.run_with_limits", return_value=mock_proc) as m:
            out = self.ci.run_tests()
        self.assertEqual(out["runner"], "unittest")


class TestGitStatus(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ci_test_"))
        self.ci = _make_ci(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_git_status_empty_when_no_git(self):
        with patch("services.code_intelligence.shutil.which", return_value=None):
            out = self.ci.git_status()
        self.assertEqual(out["branch"], "")
        self.assertIn("PATH", out["warning"])

    def test_git_status_parses_porcelain(self):
        fake_branch = MagicMock(returncode=0, stdout="main\n", stderr="")
        fake_status = MagicMock(returncode=0, stdout=" M unstaged.py\nA  staged.py\n?? untracked.py\n", stderr="")
        with patch("services.code_intelligence.run_with_limits", side_effect=[fake_branch, fake_status]):
            out = self.ci.git_status()
        self.assertEqual(out["branch"], "main")
        self.assertTrue(out["dirty"])
        self.assertIn("staged.py", out["staged"])
        self.assertIn("unstaged.py", out["unstaged"])
        self.assertIn("untracked.py", out["untracked"])

    def test_git_status_parses_rename(self):
        """Sprint 16 polish: R/C porcelain entries parse 'old -> new' to target only.

        Standard porcelain: 'XY filename' where X=index, Y=workdir.
        ' M filename' = workdir modified (unstaged). 'M  filename' = index modified (staged).
        """
        fake_branch = MagicMock(returncode=0, stdout="main\n", stderr="")
        fake_status = MagicMock(
            returncode=0,
            stdout="R  old.py -> new.py\n M modified.py\n",
            stderr="",
        )
        with patch("services.code_intelligence.run_with_limits", side_effect=[fake_branch, fake_status]):
            out = self.ci.git_status()
        # Rename SOURCE is dropped (by design); target goes into staged
        self.assertIn("new.py", out["staged"])
        self.assertNotIn("old.py", out["staged"])
        # Non-rename entry still works (space-before-M = workdir modified = unstaged)
        self.assertIn("modified.py", out["unstaged"])

    def test_git_diff_parses_unified_format(self):
        """Sprint 16: closes accept criterion #5. Parses a 2-file unified diff."""
        fake_diff_stdout = (
            "diff --git a/foo.py b/foo.py\n"
            "index abc..def 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "-removed\n"
            "+added\n"
            "+another_added\n"
            " line4\n"
            "diff --git a/bar.py b/bar.py\n"
            "new file mode 100644\n"
            "index 000..111\n"
            "--- /dev/null\n"
            "+++ b/bar.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+x\n"
            "+y\n"
        )
        fake_diff = MagicMock(returncode=0, stdout=fake_diff_stdout, stderr="")
        fake_git = MagicMock(return_value="/usr/bin/git")
        with patch("services.code_intelligence.shutil.which", return_value=fake_git), \
             patch("services.code_intelligence.run_with_limits", return_value=fake_diff):
            out = self.ci.git_diff()
        self.assertFalse(out["empty"])
        self.assertFalse(out["staged"])
        self.assertEqual(len(out["files"]), 2)
        foo, bar = out["files"]
        self.assertEqual(foo["file"], "foo.py")
        self.assertEqual(foo["added_count"], 2)
        self.assertEqual(foo["removed_count"], 1)
        self.assertEqual(foo["hunks"][0]["@"], [1, 3, 1, 4])
        self.assertEqual(foo["hunks"][0]["added"], ["added", "another_added"])
        self.assertEqual(foo["hunks"][0]["removed"], ["removed"])
        self.assertEqual(bar["file"], "bar.py")
        self.assertTrue(bar["new_file"])
        self.assertEqual(bar["added_count"], 2)
        self.assertEqual(bar["hunks"][0]["@"], [0, 0, 1, 2])

    def test_git_diff_handles_renames(self):
        """Sprint 16: rename entries populate old_file + set rename=True."""
        fake_diff_stdout = (
            "diff --git a/old_name.py b/new_name.py\n"
            "similarity index 95%\n"
            "rename from old_name.py\n"
            "rename to new_name.py\n"
            "index abc..def 100644\n"
            "--- a/old_name.py\n"
            "+++ b/new_name.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        )
        fake_diff = MagicMock(returncode=0, stdout=fake_diff_stdout, stderr="")
        with patch("services.code_intelligence.shutil.which", return_value="/usr/bin/git"), \
             patch("services.code_intelligence.run_with_limits", return_value=fake_diff):
            out = self.ci.git_diff()
        self.assertEqual(len(out["files"]), 1)
        entry = out["files"][0]
        self.assertEqual(entry["file"], "new_name.py")
        self.assertEqual(entry["old_file"], "old_name.py")
        self.assertTrue(entry["rename"])
        self.assertFalse(entry["new_file"])
        self.assertEqual(entry["added_count"], 1)
        self.assertEqual(entry["removed_count"], 1)

    def test_git_commit_with_message(self):
        """Sprint 16: closes accept criterion #5 + destructive gate parity."""
        fake_commit = MagicMock(returncode=0, stdout="[main abc123] feat: subject\n", stderr="")
        fake_revparse = MagicMock(returncode=0, stdout="abc123def456\n", stderr="")
        with patch("services.code_intelligence.shutil.which", return_value="/usr/bin/git"), \
             patch("services.code_intelligence.run_with_limits", side_effect=[fake_commit, fake_revparse]) as mock_rwl:
            out = self.ci.git_commit("feat: subject", allow_destructive=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["sha"], "abc123def456")
        self.assertFalse(out["dry_run"])
        commit_call = mock_rwl.call_args_list[0]
        self.assertEqual(commit_call.args[0][0], "/usr/bin/git")
        self.assertIn("-m", commit_call.args[0])
        self.assertIn("feat: subject", commit_call.args[0])

    def test_git_commit_multiline_body(self):
        """Sprint 16: multiline message -> multiple -m flags (subject + body lines)."""
        fake_commit = MagicMock(returncode=0, stdout="ok\n", stderr="")
        fake_revparse = MagicMock(returncode=0, stdout="newsha\n", stderr="")
        msg = "feat: subject\n\nThis is a body line.\nAnd another."
        with patch("services.code_intelligence.shutil.which", return_value="/usr/bin/git"), \
             patch("services.code_intelligence.run_with_limits", side_effect=[fake_commit, fake_revparse]) as mock_rwl:
            out = self.ci.git_commit(msg, allow_destructive=True)
        self.assertTrue(out["ok"])
        commit_cmd = mock_rwl.call_args_list[0].args[0]
        m_indices = [i for i, x in enumerate(commit_cmd) if x == "-m"]
        # Three non-blank lines -> three -m flags
        self.assertEqual(len(m_indices), 3)
        messages = [commit_cmd[i + 1] for i in m_indices]
        self.assertEqual(messages, ["feat: subject", "This is a body line.", "And another."])

    def test_git_commit_without_allow_destructive_raises(self):
        """Sprint 16: destructive gate parity with ToolUseAgent._DESTRUCTIVE_ACTIONS."""
        with self.assertRaises(PermissionError):
            self.ci.git_commit("anyone can call this")


class TestAuditLog(unittest.TestCase):
    """Sprint 16 Round 3: audit log + destructive-gate parity with ToolUseAgent._audit_log."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ci_audit_"))
        self.ci = _make_ci(self.tmpdir)
        # Round 5 polish #b: reset the class-level _audit_warned guard so each
        # test starts with a clean "no warning yet" state. Without this reset,
        # test ordering could leave _audit_warned=True from a previous test,
        # which would short-circuit the warn-once contract test below.
        from services.code_intelligence import CodeIntelligence
        CodeIntelligence._audit_warned = False

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_audit_log_disabled_by_default(self):
        """Gate: when CONFIG key is False/None, audit log file is NOT created."""
        from services.code_intelligence import os as _os
        audit_path = _os.path.join(str(self.tmpdir), "data", "code_intelligence_audit.jsonl")
        self.ci.write_file("foo.txt", "bar")
        self.assertFalse(_os.path.exists(audit_path), "audit log must not exist when disabled")

    def test_audit_log_writes_on_write_file_when_enabled(self):
        """Instrumentation: write_file emits a JSONL entry when CONFIG gate is True."""
        from services.code_intelligence import os as _os
        audit_path = _os.path.join(str(self.tmpdir), "data", "code_intelligence_audit.jsonl")
        with patch.dict("config.CONFIG", {"code_intelligence_audit_log": True}, clear=False):
            self.ci.write_file("foo.txt", "hello world")
        self.assertTrue(_os.path.exists(audit_path))
        with open(audit_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        entry = lines[0]
        self.assertEqual(entry["action"], "write_file")
        self.assertTrue(entry["ok"])
        # Round 5 polish #c: field renamed from `bytes` to `chars` -- Python
        # len(str) returns CHARACTER count, not byte count. The old `bytes`
        # name misled bandwidth-monitoring consumers.
        self.assertEqual(entry["chars"], len("hello world"))
        self.assertIn("ts", entry)

    def test_audit_log_writes_on_git_commit_when_enabled(self):
        """Instrumentation: git_commit emits a JSONL entry when CONFIG gate is True (allow_destructive=False still emits?)."""
        # Note: with allow_destructive=False, PermissionError raises BEFORE the audit hook,
        # so this test exercises the allow_destructive=True happy path.
        from services.code_intelligence import os as _os
        audit_path = _os.path.join(str(self.tmpdir), "data", "code_intelligence_audit.jsonl")
        fake_commit = MagicMock(returncode=0, stdout="ok\n", stderr="")
        fake_revparse = MagicMock(returncode=0, stdout="abc12345\n", stderr="")
        with patch.dict("config.CONFIG", {"code_intelligence_audit_log": True}, clear=False), \
             patch("services.code_intelligence.shutil.which", return_value="/usr/bin/git"), \
             patch("services.code_intelligence.run_with_limits", side_effect=[fake_commit, fake_revparse]):
            self.ci.git_commit("feat: test", allow_destructive=True)
        self.assertTrue(_os.path.exists(audit_path))
        with open(audit_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        entry = lines[0]
        self.assertEqual(entry["action"], "git_commit")
        self.assertEqual(entry["sha"], "abc12345")
        self.assertTrue(entry["ok"])

    def test_destructive_actions_set_contains_expected(self):
        """Parity: _DESTRUCTIVE_ACTIONS frozenset mirrors ToolUseAgent contract."""
        from services.code_intelligence import _DESTRUCTIVE_ACTIONS
        self.assertIn("write_file", _DESTRUCTIVE_ACTIONS)
        self.assertIn("edit_file", _DESTRUCTIVE_ACTIONS)
        self.assertIn("git_commit", _DESTRUCTIVE_ACTIONS)

    def test_audit_log_records_failure_on_disk_full(self):
        """REGRESSION GUARD: write_file OSError captured with ok=False audit (Round 4).

        Locks the try/except wrap in code_intelligence.write_file (services/
        code_intelligence.py ~line 164). When the underlying open() raises
        OSError (mimicking ENOSPC disk-full), the audit log still captures
        an ok=False entry so post-mortems see WHY the write failed. Without
        this guard, a write_file that crashes silently produces NO audit
        trail -- defeating the post-mortem use case entirely.

        Setup trick: patch builtins.open with a counter-callable. First call
        (from write_file's with-open block) raises OSError; subsequent calls
        (from _audit_log's internal open for appending the JSONL line) pass
        through to the real open(). Verifies: (a) exactly 2 open() calls were
        attempted (write fail + audit append), (b) the audit JSONL exists,
        (c) the entry has action=write_file, ok=False, error_type=OSError.
        REGRESSION: revert the try/except in write_file and the file will be
        empty (audit never fires on raise) -- this test will fail.
        """
        real_open = open
        calls = []
        def selective_open(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                raise OSError(28, "No space left on device")
            return real_open(*args, **kwargs)

        with patch.dict("config.CONFIG", {"code_intelligence_audit_log": True}, clear=False), \
             patch("builtins.open", side_effect=selective_open):
            with self.assertRaises(OSError):
                self.ci.write_file("foo.txt", "hello world")

        # First open call: write_file's IO (raised).
        # Second open call: _audit_log's append (succeeded).
        self.assertEqual(
            len(calls), 2,
            "expected exactly 2 open() calls (1 from write_file + 1 from _audit_log), got " + str(len(calls)),
        )
        audit_path = os.path.join(str(self.tmpdir), "data", "code_intelligence_audit.jsonl")
        self.assertTrue(
            os.path.exists(audit_path),
            "audit log file must exist despite write_file failure (audit_log is fail-soft but must still write)",
        )
        with open(audit_path, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(entries), 1, "exactly one audit entry expected after write_file failure")
        entry = entries[0]
        self.assertEqual(entry["action"], "write_file")
        self.assertFalse(entry["ok"], "ok must be False when write_file raised OSError")
        self.assertEqual(entry["error_type"], "OSError")
        self.assertIn("No space", entry["error_msg"])

    def test_audit_log_records_failure_on_git_non_zero_returncode(self):
        """REGRESSION GUARD: git_commit non-zero returncode emits ok=False audit (Round 4).

        Locks the existing audit call inside git_commit (services/
        code_intelligence.py ~line 568). When run_with_limits returns a
        non-zero returncode (mimicking commit failures: no HEAD, dirty
        tree, hook rejection), the audit log still fires with ok=False.
        This was already implicit behavior but lacked a dedicated test to
        lock the contract -- a subtle refactor could remove the audit
        emission and the existing happy-path test wouldn't catch it.

        Setup: allow_destructive=True (gate passed); mock run_with_limits
        to a single MagicMock with returncode=128 (one-shot, no rev-parse
        followup since the commit didn't succeed). Verifies the audit log
        JSONL has exactly 1 entry with action=git_commit, ok=False.
        REGRESSION: remove the audit_log call at the bottom of git_commit
        and the file will be empty -- this test will fail.
        """
        fake_commit = MagicMock(returncode=128, stdout="", stderr="nothing to commit, working tree clean\n")
        with patch.dict("config.CONFIG", {"code_intelligence_audit_log": True}, clear=False), \
             patch("services.code_intelligence.shutil.which", return_value="/usr/bin/git"), \
             patch("services.code_intelligence.run_with_limits", return_value=fake_commit):
            out = self.ci.git_commit("feat: test subject", allow_destructive=True)

        self.assertFalse(out["ok"], "ok must be False when run_with_limits returned non-zero returncode")
        self.assertEqual(out["returncode"], 128)
        audit_path = os.path.join(str(self.tmpdir), "data", "code_intelligence_audit.jsonl")
        self.assertTrue(os.path.exists(audit_path))
        with open(audit_path, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(entries), 1, "exactly one audit entry expected after git_commit failure")
        entry = entries[0]
        self.assertEqual(entry["action"], "git_commit")
        self.assertFalse(entry["ok"], "ok must be False when returncode != 0")

    def test_edit_file_success_emits_edit_file_AND_write_file_audit_entries(self):
        """REGRESSION GUARD: edit_file success emits BOTH edit_file + write_file audit entries.

        Locks the Round 4 fix where the edit_file success-path audit was
        RESTORED after the code-reviewer flagged its initial removal as
        semantically wrong (the two entries are layered semantic context,
        NOT duplicates). Post-mortem needs BOTH action='edit_file' AND
        action='write_file' to disambiguate a str_replace operation from
        a bare write_file call. REGRESSION: remove the edit_file success
        audit and the count drops to 1 -- this test fails.

        Setup diverges from TestAuditLog.setUp: we need a sample file for
        edit_file to act on. TestFileOps has a fixture for this but mixing
        inheritance would couple separated concerns -- a direct inline
        write_text is simplest. (TestAuditLog.setUp wasn't extended to
        share the fixture because the other TestAuditLog tests exercise
        write_file / git_commit directly -- no sample.py needed.)
        """
        # Inline fixture: create sample.py with "b" appearing ONCE (single-match).
        sample = self.tmpdir / "sample.py"
        sample.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")

        with patch.dict("config.CONFIG", {"code_intelligence_audit_log": True}, clear=False):
            self.ci.edit_file("sample.py", "b", "B")

        audit_path = os.path.join(str(self.tmpdir), "data", "code_intelligence_audit.jsonl")
        self.assertTrue(os.path.exists(audit_path))
        with open(audit_path, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]

        # Canonical multiset assertion: sorted-list equality locks \"exactly
        # one edit_file + one write_file\" without over-constraining EMISSION
        # ORDER (audit consumers identify entries by ts + action, not by
        # sequence position). Replaces a redundant 3-assert trio
        # (len == 2 + assertIn(\"edit_file\") + assertIn(\"write_file\")) that
        # triangulated the same invariant. Per Round 4 code-reviewer feedback.
        actions = [e["action"] for e in entries]
        self.assertEqual(
            sorted(actions), ["edit_file", "write_file"],
            "expected exactly one edit_file + one write_file audit entry, got: " + str(actions),
        )
        for e in entries:
            self.assertTrue(e["ok"], "both entries must have ok=True on successful edit; entry=" + repr(e))

    def test_audit_warns_once_on_config_import_error(self):
        """REGRESSION GUARD: CONFIG unreachable emits EXACTLY ONE warning per process (Round 5 polish #b).

        Locks the Round 5 polish contract for _audit_log: when `from config
        import CONFIG` raises ImportError (or any other Exception), the
        OUTER except block emits a single `logger.warning(...)` per
        process (class-level `_audit_warned` flag), then silently returns.
        Subsequent calls do NOT re-warn. Without the flag, every audit
        emission would either spam logs with ImportError noise OR silently
        miss all audit entries without any signal that audit is disabled.

        Setup: patch sys.modules["config"] = None to make `from config
        import CONFIG` raise ImportError on each call (Python treats None
        in sys.modules as a halted import). patch.dict auto-restores
        sys.modules on context exit. spy on logger.warning via
        patch.object on the stdlib Logger instance imported from
        services.code_intelligence.

        TestAuditLog.setUp already reset `_audit_warned = False`, so this
        test starts with a clean guard.
        """
        import sys
        from services.code_intelligence import logger
        # Note: patch.dict on sys.modules makes `from config import CONFIG`
        # inside _audit_log fail because Python treats None in sys.modules
        # as a halted import. The outer except branch fires each call.
        with patch.dict(sys.modules, {"config": None}), \
             patch.object(logger, "warning") as mock_warn:
            self.ci._audit_log({"action": "test1", "ok": False})
            self.ci._audit_log({"action": "test2", "ok": False})
            self.ci._audit_log({"action": "test3", "ok": False})

        self.assertEqual(
            mock_warn.call_count, 1,
            "logger.warning must fire exactly once across 3 audit invocations, got " + str(mock_warn.call_count),
        )
        mock_warn.assert_called_once_with(
            "CodeIntelligence audit disabled -- CONFIG unreachable",
        )

    def test_audit_post_polish_write_file_chars_AND_edit_file_canonical_path(self):
        """REGRESSION GUARD: Round 5 polish #c (chars rename) + #e (canonical path).

        Locks post-Round 4 audit field contract:
        (c) write_file audit emits `chars` field (NOT `bytes`). Python's
            len() on a str returns CHARACTER count; the old `bytes` name
            misled bandwidth-monitoring consumers who expected UTF-8 byte
            counts. Rename-only (per user spec) -- no `bytes` field.
        (e) edit_file audit `path` is the CANONICAL resolved path
            (re-calls _validate_path), so post-mortem consumers correlate
            edit_file + write_file entries by exact path equality (instead
            of trying to manually re-resolve raw caller-supplied paths).

        Setup: nested sample in `src/` subdir so canonical resolution
        produces an absolute path that includes the workspace tmpdir root
        (different from a bare relative path like "src/sample.py").
        """
        # Nested sample to make canonical-vs-raw path difference observable.
        nested_dir = self.tmpdir / "src"
        nested_dir.mkdir(parents=True, exist_ok=True)
        sample = nested_dir / "sample.py"
        sample.write_text("a\nINITIAL\nc\n", encoding="utf-8")

        # Distinct content lengths: explicit write_file uses 7 chars
        # ("INITIAL"), edit_file's inner write_file uses 8 chars ("REPLACED").
        # Filtering by chars value uniquely identifies the audit entry from
        # the explicit write_file (without this, both write_file audits could
        # share a chars count when len(old) == len(new)).
        with patch.dict("config.CONFIG", {"code_intelligence_audit_log": True}, clear=False):
            # Trigger write_file success path -- the audit entry must use `chars`.
            self.ci.write_file("src/sample.py", "INITIAL")
            # Trigger edit_file success path -- the audit `path` must be canonical.
            self.ci.edit_file("src/sample.py", "INITIAL", "REPLACED")

        audit_path = os.path.join(str(self.tmpdir), "data", "code_intelligence_audit.jsonl")
        self.assertTrue(os.path.exists(audit_path))
        with open(audit_path, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]

        # (c) write_file entry: has `chars` field, NO legacy `bytes` field.
        # Filter by chars=7 uniquely identifies the EXPLICIT write_file
        # (vs the inner write_file from edit_file's delegation, which has
        # chars=8 for the "REPLACED" content).
        initial_writes = [
            e for e in entries
            if e["action"] == "write_file" and e.get("chars") == len("INITIAL")
        ]
        self.assertEqual(
            len(initial_writes), 1,
            "exactly one explicit write_file audit with chars=7 (len('INITIAL')) expected, got count=" + str(len(initial_writes)),
        )
        initial_write = initial_writes[0]
        self.assertIn("chars", initial_write, "write_file audit must have `chars` field (renamed from `bytes`)")
        self.assertNotIn(
            "bytes", initial_write,
            "write_file audit must NOT have legacy `bytes` field name; downstream consumers using "
            "`bytes` for UTF-8 bandwidth would get misleading char counts",
        )
        self.assertEqual(initial_write["chars"], len("INITIAL"))

        # (e) edit_file entry: `path` is CANONICAL resolved (absolute, includes tmpdir root).
        edit_entries = [
            e for e in entries
            if e["action"] == "edit_file" and e.get("ok") is True
        ]
        self.assertEqual(
            len(edit_entries), 1,
            "exactly one edit_file success audit expected, got count=" + str(len(edit_entries)),
        )
        edit_entry = edit_entries[0]
        self.assertTrue(
            os.path.isabs(edit_entry["path"]),
            "edit_file audit `path` must be canonical resolved (absolute), got: " + repr(edit_entry["path"]),
        )
        self.assertIn(
            str(self.tmpdir), edit_entry["path"],
            "edit_file audit `path` must include workspace root " + str(self.tmpdir) +
            ", got: " + repr(edit_entry["path"]),
        )
        # Cross-class correlation: edit_file audit `path` == write_file audit `path`
        # (both should be canonicalized via _validate_path).
        initial_write_path = initial_write["path"]
        self.assertEqual(
            edit_entry["path"], initial_write_path,
            "edit_file and write_file audit `path` fields must match (both canonical), got edit=" +
            repr(edit_entry["path"]) + " write=" + repr(initial_write_path),
        )


class TestParseError(unittest.TestCase):
    def setUp(self):
        self.ci = _make_ci(Path(tempfile.mkdtemp(prefix="ci_test_")))

    def test_parse_empty_returns_empty(self):
        self.assertEqual(self.ci.parse_error(""), [])

    def test_parse_python_traceback(self):
        tb = (
            'Traceback (most recent call last):\n'
            '  File "foo.py", line 42, in bar\n'
            '    baz()\n'
            "NameError: name 'baz' is not defined\n"
        )
        result = self.ci.parse_error(tb)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["file"], "foo.py")
        self.assertEqual(result[0]["line"], 42)

    def test_parse_pytest_failure(self):
        # Verbose pytest format (file:line: msg) — matches _PYTEST_FAIL_RE
        out = "tests/test_x.py:99: AssertionError\n"
        result = self.ci.parse_error(out)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["file"], "tests/test_x.py")
        self.assertEqual(result[0]["line"], 99)

    def test_parse_ruff_output(self):
        out = "mod.py:10:5: F401 unused import\n"
        result = self.ci.parse_error(out)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["file"], "mod.py")
        self.assertEqual(result[0]["line"], 10)

    def test_parse_dedupes_by_file_line(self):
        tb = 'File "mod.py", line 10, in f\nNameError: x\n'
        out = "mod.py:10:5: NameError: x\n"
        result = self.ci.parse_error(tb + out)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
