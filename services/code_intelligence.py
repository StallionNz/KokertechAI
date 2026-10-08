"""
code_intelligence.py - Sprint 16: Code Intelligence Core service.

Exposes 9 code-aware operations the agent can chain:
  read_file / write_file / edit_file - file ops with str_replace safety
  search_code - ripgrep integration with Python re fallback
  analyze_ast - AST inspection (functions, classes, imports)
  run_tests / run_linter - subprocess via run_with_limits
  git_status - git porcelain parser
  parse_error - Python traceback + pytest + ruff output parser

Design (Sprint 16):
- Class (not module functions) - enables workspace anchor + rg cache without
  module-level mutable state. ServiceRegistry can inject workspace via factory.
- Workspace-relative path validation - rejects ../../etc/passwd traversal.
- run_with_limits (scripts.safe_subprocess) for ALL subprocess calls.
- edit_file mirrors the str_replace anti-pattern: ValueError on not-found
  + multi-match by default; allow_multiple=True is the explicit opt-in.

Cross-file hygiene: no module-level mutable globals (rg cache is per-instance).
"""
from __future__ import annotations

import ast
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

from scripts.safe_subprocess import run_with_limits

# Module-level logger (Round 5 polish #b). Used by _audit_log to emit
# exactly-once-per-process warning when CONFIG is unreachable. Stdlib
# logging to avoid cross-module dependency on logging_config.
logger = logging.getLogger(__name__)


_TRACEBACK_RE = re.compile(
    r"File \"(?P<file>[^\"]+)\", line (?P<line>\d+)(?:, in .+)?\n\s*(?P<msg>.*?)(?:\n|$)"
)
_PYTEST_FAIL_RE = re.compile(
    r"(?P<file>[^\s:]+\.py):(?P<line>\d+):\s*(?P<msg>.*?)(?:\n|$)"
)
_RUFF_RE = re.compile(
    r"(?P<file>[^\s:]+):(?P<line>\d+):(?P<col>\d+):\s*(?P<msg>.*?)(?:\n|$)"
)
_GIT_STATUS_PORCELAIN_RE = re.compile(r"^(?P<status>[ MADRCU?!]{2})\s+(?P<path>.+)$")

# Sprint 16: git diff unified-format parser regexes (closed gap on accept criterion #5)
# Split at the file-header boundary so each element of blocks[] is one file's metadata+hunks.
_DIFF_HEADER_SPLIT_RE = re.compile(r"^diff --git ", re.MULTILINE)
# Extract the two paths from the remainder of a `diff --git a/OLD b/NEW` header line.
_DIFF_PATHS_RE = re.compile(r"^a/(?P<old_path>.+?) b/(?P<new_path>.+)$")
# Hunk header: `@@ -OLD_LINE[,OLD_COUNT] +NEW_LINE[,NEW_COUNT] @@` -- counts default to 1 when omitted.
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_line>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_line>\d+)(?:,(?P<new_count>\d+))? @@"
)
_BINARY_RE = re.compile(r"^Binary files .+ differ$", re.MULTILINE)
_RENAME_FROM_RE = re.compile(r"^rename from (?P<from>.+)$", re.MULTILINE)
_RENAME_TO_RE = re.compile(r"^rename to (?P<to>.+)$", re.MULTILINE)
_NEW_FILE_RE = re.compile(r"^new file mode \d+", re.MULTILINE)
_DEL_FILE_RE = re.compile(r"^deleted file mode \d+", re.MULTILINE)

# Sprint 16 Round 3: _DESTRUCTIVE_ACTIONS mirrors ToolUseAgent._DESTRUCTIVE_ACTIONS
# at services/tool_use_agent.py:56. Methods in this set are gated via
# `allow_destructive=True` from non-interactive callers. Audit-instrumented
# regardless of gate pass/fail so post-mortems can see BLOCKED attempts.
_DESTRUCTIVE_ACTIONS = frozenset({"write_file", "edit_file", "git_commit"})

# Sprint 16 Round 4: error_msg truncation cap for audit entries. Keeps the
# JSONL line bounded when str(e) is huge (a full traceback embed could be
# 10K+ chars). Mirrors ToolUseAgent._audit_log convention (truncates long
# payloads to a bounded cap so one failing call doesn't bloat the trail).
_AUDIT_ERR_MAX_CHARS = 200


class CodeIntelligence:
    """9-method code-aware operations surface for the AI agent."""

    # Round 5 polish #b: per-process guard for the "CONFIG unreachable" warning.
    # Class-level (NOT instance) so a single flag tracks across ALL instances
    # in a process -- matches the user's spec for "exactly one warning per
    # process". Tests reset this in TestAuditLog.setUp so each test starts
    # with a clean guard. THREAD-SAFETY: assumes single-threaded usage --
    # two concurrent _audit_log calls could TOCTOU the flag-check + mutate
    # and emit the warning twice (check, set, check, set race). The
    # current agent-loop usage is single-threaded, so this is defensive
    # documentation rather than a current bug. If parallelism arrives,
    # wrap the check-mutate sequence in a class-level threading.Lock.
    _audit_warned: bool = False

    def __init__(self, workspace: str, linter_cmd=None):
        if not workspace:
            raise ValueError("workspace must be a non-empty path")
        self.workspace = os.path.abspath(workspace)
        self._rg_path = shutil.which("rg")
        self._linter_cmd = linter_cmd
        self._audit_log_name = "data/code_intelligence_audit.jsonl"

    def _audit_log(self, entry: dict) -> None:
        """Append a JSONL audit entry to data/<workspace>/code_intelligence_audit.jsonl.

        Mirrors ToolUseAgent._audit_log (services/tool_use_agent.py:83). Toggled
        via CONFIG['code_intelligence_audit_log'] (defaults to False). Fail-soft:
        silently no-ops if CONFIG is unavailable or the file cannot be written
        (disk full / permission denied) -- audit-trail problems must NEVER break
        the calling operation. ANTI-FRAGILITY: gate + fail-soft verified by
        TestAuditLog in tests/test_code_intelligence.py.
        """
        try:
            from config import CONFIG
            if not CONFIG.get("code_intelligence_audit_log", False):
                return
        except (ImportError, AttributeError, KeyError, OSError, RuntimeError, ValueError):
            # ANTI-FRAGILITY (Round 5 polish #b): if CONFIG is unreachable,
            # emit ONE logger.warning per process so post-mortems notice
            # audit disabled. Without the flag, every audit emission would
            # either spam logs with ImportError noise OR silently miss all
            # audit entries without any signal that something's wrong.
            # The flag is class-level so it persists across all instances
            # of CodeIntelligence in the process -- matches user spec for
            # "exactly once per process".
            if not CodeIntelligence._audit_warned:
                CodeIntelligence._audit_warned = True
                try:
                    logger.warning("CodeIntelligence audit disabled -- CONFIG unreachable")
                except (RuntimeError, OSError, ValueError):
                    # Logging itself failed — nothing else we can emit; this is the
                    # documented degenerate fallback (deliberate, not a silent catch).
                    pass
            return
        try:
            import json
            import time
            entry_with_ts = {"ts": round(time.time(), 3), **entry}
            audit_path = os.path.join(self.workspace, self._audit_log_name)
            os.makedirs(os.path.dirname(audit_path), exist_ok=True)
            with open(audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry_with_ts, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            return

    def _validate_path(self, path: str) -> Path:
        """Resolve and protect against directory traversal and symlink escapes.

        ANTI-FRAGILITY (per KNOWLEDGE.md §12): the symlink + realpath defense
        is locked by TestFileOps::test_path_traversal_blocks_symlink and
        test_path_traversal_blocks_realpath_escape in tests/test_code_intelligence.py.
        Uses os.path.realpath (cross-platform NTFS/POSIX symlink eval) and
        os.path.normcase (Windows case-insensitive commonpath compare).
        """
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string, got " + type(path).__name__)

        # 1. Expand relative pathways via standard abspath
        if os.path.isabs(path):
            resolved = Path(os.path.abspath(path))
        else:
            resolved = Path(self.workspace) / path

        # 2. Belt-and-suspenders: trace symlinks + NTFS junctions
        resolved_abs = os.path.realpath(str(resolved))
        ws_abs = os.path.realpath(self.workspace)

        # 3. Case-insensitive comparison (crucial for Windows commonpath)
        check_resolved = os.path.normcase(resolved_abs)
        check_ws = os.path.normcase(ws_abs)

        try:
            common = os.path.commonpath([check_resolved, check_ws])
        except ValueError:
            raise ValueError(
                "path escapes workspace: " + repr(path) + " resolves to " + resolved_abs + ", workspace=" + ws_abs
            )
        if common != check_ws:
            raise ValueError(
                "path escapes workspace: " + repr(path) + " resolves to " + resolved_abs + ", workspace=" + ws_abs
            )
        return resolved

    def read_file(self, path: str, start_line=None, end_line=None) -> str:
        """Read a file as UTF-8 with optional 1-indexed line range."""
        resolved = self._validate_path(path)
        if not resolved.exists():
            raise FileNotFoundError("file not found: " + str(resolved))
        if not resolved.is_file():
            raise IsADirectoryError("not a file: " + str(resolved))
        if start_line is not None and end_line is not None and start_line > end_line:
            raise ValueError("start_line (" + str(start_line) + ") > end_line (" + str(end_line) + ")")
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        if start_line is None and end_line is None:
            return text
        lines = text.splitlines(keepends=True)
        start_idx = max(0, (start_line or 1) - 1)
        end_idx = end_line if end_line is not None else len(lines)
        if start_idx >= len(lines):
            return ""
        return "".join(lines[start_idx:end_idx])

    def write_file(self, path: str, content: str, create_dirs: bool = True) -> bool:
        """Write file content; create parent dirs if create_dirs=True.

        ANTI-FRAGILITY (Round 4): audit-log fires on BOTH success and failure.
        On exception (disk full, mkdir permission, validate-path reject,
        open() PermissionError) the audit captures {ok=False, error_type,
        error_msg} so post-mortems see WHY the write failed. Exception is
        re-raised so callers still see the original error.
        """
        resolved = self._validate_path(path)
        try:
            if create_dirs:
                parent = resolved.parent
                if not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)
            self._audit_log({"action": "write_file", "path": str(resolved), "ok": True, "chars": len(content)})
            return True
        except Exception as e:
            self._audit_log({
                "action": "write_file",
                "path": str(resolved),
                "ok": False,
                "error_type": type(e).__name__,
                "error_msg": str(e)[:_AUDIT_ERR_MAX_CHARS],
            })
            raise

    def edit_file(self, path: str, old_text: str, new_text: str, allow_multiple: bool = False) -> bool:
        """Replace old_text with new_text in path. Anti-pattern protection.

        ANTI-FRAGILITY (Round 4+5): edit_file audit-fires ONLY on FAILURE
        (validation errors before delegation). The success path delegates
        to inner write_file, which has its OWN write_file audit entry --
        a second ok=True entry on the outer edit_file would be a duplicate
        of the same logical event. On exception, the audit captures
        {ok=False, error_type, error_msg} for post-mortem analysis;
        exception is re-raised. ANTI-FRAGILITY (Round 5 polish #e): audit
        `path` field uses the CANONICAL resolved path -- edit_file re-calls
        _validate_path at the top of try so audits always show the
        post-resolution absolute path (not the caller-supplied raw path),
        so post-mortem consumers can correlate edit_file + write_file
        entries by exact path equality. FALLBACK (resolution-failure case):
        if _validate_path itself raises (path traversal / workspace-escape),
        `resolved` retains the raw caller-supplied `path` and the audit
        emits `str(path)` -- there is no canonical path to record when
        the input couldn't validate, so we record what the agent actually
        passed (better signal for debugging than a fake canonical).
        """
        # Round 5 polish #e: resolved defaults to caller-supplied path so the
        # audit is safe even when _validate_path itself raises (future-proof
        # fallback -- if _validate_path succeeds below, this is replaced with
        # the canonical Path object).
        resolved = path
        try:
            resolved = self._validate_path(path)
            if not old_text:
                raise ValueError("old_text must be non-empty")
            text = self.read_file(path)
            if old_text not in text:
                raise ValueError("old_text not found in " + repr(path))
            occurrences = text.count(old_text)
            if occurrences > 1 and not allow_multiple:
                raise ValueError(
                    "old_text matches " + str(occurrences) + " times in " + repr(path)
                    + "; pass allow_multiple=True (DANGEROUS)"
                )
            # ANTI-FRAGILITY (Round 4 fix to code-reviewer feedback): emit
            # edit_file success audit BEFORE delegating to inner write_file.
            # The two entries are layered semantic context (caller intent +
            # IO operation), NOT duplicates. Post-mortem needs both to
            # disambiguate edit_file from a bare write_file call. Round 5
            # polish #e: `path` is the CANONICAL resolved path so post-
            # mortem correlates edit_file + write_file by exact path.
            self._audit_log({"action": "edit_file", "path": str(resolved), "ok": True})
            new_content = text.replace(old_text, new_text, 1 if not allow_multiple else -1)
            return self.write_file(path, new_content)
        except Exception as e:
            self._audit_log({
                "action": "edit_file",
                "path": str(resolved),
                "ok": False,
                "error_type": type(e).__name__,
                "error_msg": str(e)[:_AUDIT_ERR_MAX_CHARS],
            })
            raise

    def search_code(self, pattern: str, paths=None):
        """Search code for pattern (regex). Returns list of {path, line, text, match} dicts."""
        if not pattern:
            raise ValueError("pattern must be non-empty")
        try:
            regex = re.compile(pattern)
        except re.error as e:
            raise ValueError("invalid regex: " + str(e))
        if not paths:
            paths = ["."]
        scope_paths = []
        for p in paths:
            resolved = self._validate_path(p)
            if resolved.exists():
                scope_paths.append(resolved)
        if not scope_paths:
            return []
        if self._rg_path:
            try:
                results = self._search_with_ripgrep(pattern, scope_paths)
                if results is not None:
                    return results
            except (OSError, RuntimeError, ValueError, KeyError) as e:
                # §13 read-fallback convention: ripgrep is an optional external
                # binary; on failure we degrade to the built-in Python re walker.
                # Log at debug (hot path — never warning).
                logger.debug(f"ripgrep search failed, falling back to Python re walker: {e}")
        return self._search_with_python_re(regex, scope_paths)

    def _search_with_ripgrep(self, pattern, scope_paths):
        try:
            cmd = [self._rg_path, "--json", "--line-number", pattern] + [str(p) for p in scope_paths]
            result = run_with_limits(
                cmd, timeout=30, memory_mb=256, cwd=self.workspace,
                capture_output=True, text=True,
            )
            if result.returncode not in (0, 1):
                return None
            import json as _json
            out = []
            for line in result.stdout.splitlines():
                try:
                    obj = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if obj.get("type") != "match":
                    continue
                data = obj.get("data", {})
                sub = data.get("submatches", [{}])[0]
                out.append({
                    "path": data.get("path", {}).get("text", ""),
                    "line": data.get("line_number", 0),
                    "text": sub.get("match", {}).get("text", ""),
                })
            return out
        except (OSError, RuntimeError, ValueError, KeyError, IndexError, AttributeError):
            return None

    def _search_with_python_re(self, regex, scope_paths):
        skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv"}
        out = []
        for root in scope_paths:
            if root.is_file():
                files = [root]
            else:
                files = []
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if d not in skip_dirs]
                    for fn in filenames:
                        if fn.endswith((".pyc", ".png", ".jpg", ".gif", ".ico")):
                            continue
                        files.append(Path(dirpath) / fn)
            for fpath in files:
                # Sprint 16 polish (reviewer-flagged): probe for binary signature
                # (null bytes in first 8KB) before opening as text. Extension-only
                # skip misses extensionless blobs / .bin / .dat files that decode
                # as garbage or hang unicode codecs.
                try:
                    with open(fpath, "rb") as fb:
                        if b"\0" in fb.read(8192):
                            continue
                except OSError:
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            m = regex.search(line)
                            if m:
                                out.append({
                                    "path": str(fpath),
                                    "line": lineno,
                                    "text": line.rstrip("\n"),
                                    "match": m.group(0),
                                })
                except (OSError, UnicodeError):
                    continue
        return out

    def analyze_ast(self, path: str):
        """Parse a Python file and extract structural summary.

        Returns dict with keys: functions, classes, imports.
        Each function has: name, args, returns, docstring, line, decorators.
        Each class has: name, bases, docstring, line, methods.
        Each import has: module, names, line.
        """
        resolved = self._validate_path(path)
        if not resolved.exists():
            raise FileNotFoundError("file not found: " + str(resolved))
        # Sprint 16 polish (reviewer-flagged): try UTF-8 first, fall back to
        # latin-1 for legacy non-ascii .py files. errors="replace" would mask
        # the issue silently; explicit fallback surfaces the encoding contract.
        try:
            with open(resolved, "r", encoding="utf-8") as f:
                src = f.read()
        except UnicodeDecodeError:
            with open(resolved, "r", encoding="latin-1") as f:
                src = f.read()
        tree = ast.parse(src, filename=str(resolved))

        funcs = []
        classes = []
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append(self._extract_function(node))
            elif isinstance(node, ast.ClassDef):
                classes.append(self._extract_class(node))
            elif isinstance(node, ast.Import):
                imports.append({
                    "module": node.names[0].name,
                    "names": [node.names[0].asname or node.names[0].name],
                    "line": node.lineno,
                })
            elif isinstance(node, ast.ImportFrom):
                imports.append({
                    "module": node.module or "",
                    "names": [a.asname or a.name for a in node.names],
                    "line": node.lineno,
                })

        return {"functions": funcs, "classes": classes, "imports": imports}

    @staticmethod
    def _extract_function(node) -> Dict:
        args = [a.arg for a in node.args.args]
        returns = ast.unparse(node.returns) if node.returns else None
        decorators = [ast.unparse(d) for d in node.decorator_list]
        return {
            "name": node.name,
            "args": args,
            "returns": returns,
            "docstring": ast.get_docstring(node),
            "line": node.lineno,
            "decorators": decorators,
        }

    @staticmethod
    def _extract_class(node) -> Dict:
        bases = [ast.unparse(b) for b in node.bases]
        methods = [
            CodeIntelligence._extract_function(n)
            for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        return {
            "name": node.name,
            "bases": bases,
            "docstring": ast.get_docstring(node),
            "line": node.lineno,
            "methods": methods,
        }

    def run_tests(self, path=None):
        """Run pytest (default) or unittest on path (or workspace).

        Returns {runner, returncode, stdout, stderr, errors}.
        """
        scope = self._validate_path(path) if path else Path(self.workspace)
        runner = self._detect_test_runner(scope)
        if runner == "pytest":
            cmd = [sys.executable, "-m", "pytest", str(scope), "-q", "--no-header"]
        else:
            cmd = [sys.executable, "-m", "unittest", "discover", "-s", str(scope)]
        result = run_with_limits(
            cmd, timeout=300, memory_mb=2048, cwd=self.workspace,
            capture_output=True, text=True,
        )
        stdout, stderr = result.stdout or "", result.stderr or ""
        return {
            "runner": runner,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "errors": self.parse_error(stdout + stderr),
        }

    @staticmethod
    def _detect_test_runner(scope: Path) -> str:
        scope = Path(scope)
        if scope.is_file():
            scope = scope.parent
        for marker in ("pytest.ini", "pyproject.toml", "conftest.py"):
            if (scope / marker).exists():
                return "pytest"
        tests_dir = scope / "tests"
        if tests_dir.exists() and any(tests_dir.glob("test_*.py")):
            return "pytest"
        return "unittest"

    def run_linter(self, path=None):
        """Run ruff (default linter) on path. Returns {linter, returncode, stdout, stderr, errors}."""
        if self._linter_cmd is None:
            return {
                "linter": None,
                "returncode": 0,
                "stdout": "",
                "stderr": "",
                "errors": [],
                "warning": "linter disabled (linter_cmd=None)",
            }
        scope = str(self._validate_path(path)) if path else self.workspace
        linter_path = shutil.which(self._linter_cmd)
        if not linter_path:
            raise FileNotFoundError("linter " + repr(self._linter_cmd) + " not on PATH")
        result = run_with_limits(
            [linter_path, "check", scope, "--output-format=concise"],
            timeout=60, memory_mb=512, cwd=self.workspace,
            capture_output=True, text=True,
        )
        stdout, stderr = result.stdout or "", result.stderr or ""
        return {
            "linter": self._linter_cmd,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "errors": self.parse_error(stdout + stderr),
        }

    def git_status(self):
        """Return git porcelain status for self.workspace.

        Returns {branch, dirty, staged, unstaged, untracked} or empty+warning on failure.
        """
        git_path = shutil.which("git")
        if not git_path:
            return self._empty_git_status(reason="git not on PATH")
        result = run_with_limits(
            [git_path, "-C", self.workspace, "rev-parse", "--abbrev-ref", "HEAD"],
            timeout=10, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return self._empty_git_status(reason="not a git repository")
        branch = (result.stdout or "").strip()
        result = run_with_limits(
            [git_path, "-C", self.workspace, "status", "--porcelain"],
            timeout=10, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return self._empty_git_status(reason="git status failed")
        staged = []
        unstaged = []
        untracked = []
        for line in (result.stdout or "").splitlines():
            m = _GIT_STATUS_PORCELAIN_RE.match(line.rstrip())
            if not m:
                continue
            code, p = m.group("status"), m.group("path")
            if "??" in code:
                untracked.append(p)
            elif code.startswith(("R", "C")) and " -> " in p:
                # Sprint 16 polish (reviewer-flagged): rename/copy entries have
                # "old -> new" syntax. Record target only; source path is dropped
                # by design (would need a richer dict{'source','target'} if the
                # agent ever needs to narrate renames explicitly).
                target = p.split(" -> ", 1)[1]
                if code[0] != " ":
                    staged.append(target)
                else:
                    unstaged.append(target)
            elif code[0] != " ":
                staged.append(p)
            elif code[1] != " ":
                unstaged.append(p)
        dirty = bool(staged or unstaged or untracked)
        return {
            "branch": branch,
            "dirty": dirty,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        }

    @staticmethod
    def _empty_git_status(reason: str):
        return {
            "branch": "",
            "dirty": False,
            "staged": [],
            "unstaged": [],
            "untracked": [],
            "warning": reason,
        }

    def git_diff(self, staged: bool = False):
        """Parse `git diff [--staged]` unified output into a structured dict.

        Returns {"files": [...], "staged": bool, "empty": bool, "raw": str}.
        Each file entry has: file, old_file (None unless rename), hunks (list
        of {"@": [old_line, old_count, new_line, new_count], "added": [...],
        "removed": [...]}), added_count, removed_count, binary/new_file/
        deleted/rename flags. ANTI-FRAGILITY: parse logic verified by
        test_git_diff_parses_unified_format + test_git_diff_handles_renames
        + test_git_diff_handles_binary in tests/test_code_intelligence.py.
        """
        git_path = shutil.which("git")
        if not git_path:
            return {"files": [], "staged": staged, "empty": True, "raw": "",
                    "warning": "git not on PATH"}
        cmd = [git_path, "-C", self.workspace, "diff", "--no-color"]
        if staged:
            cmd.append("--staged")
        result = run_with_limits(cmd, timeout=15, capture_output=True, text=True)
        raw = result.stdout or ""
        if not raw.strip():
            return {"files": [], "staged": staged, "empty": True, "raw": ""}
        files = []
        # Split into per-file blocks; index 0 is the empty string before the
        # first "diff --git" boundary line.
        blocks = _DIFF_HEADER_SPLIT_RE.split(raw)[1:]
        for block in blocks:
            header_line, _, body = block.partition("\n")
            m = _DIFF_PATHS_RE.match(header_line.strip())
            if not m:
                continue
            entry = {
                "file": m.group("new_path"),
                "old_file": None,
                "hunks": [],
                "added_count": 0,
                "removed_count": 0,
                "binary": bool(_BINARY_RE.search(body)),
                "new_file": bool(_NEW_FILE_RE.search(body)),
                "deleted": bool(_DEL_FILE_RE.search(body)),
                "rename": bool(_RENAME_FROM_RE.search(body)) and bool(_RENAME_TO_RE.search(body)),
            }
            if entry["rename"]:
                entry["old_file"] = m.group("old_path")
            # Split body into hunk chunks at each `@@ -` boundary (lookahead via ^).
            hunk_chunks = re.split(r"(?=^@@ -)", body, flags=re.MULTILINE)
            for chunk in hunk_chunks:
                h = _HUNK_HEADER_RE.match(chunk)
                if not h:
                    continue
                old_line = int(h.group("old_line"))
                old_count = int(h.group("old_count") or 1)
                new_line = int(h.group("new_line"))
                new_count = int(h.group("new_count") or 1)
                added, removed = [], []
                for ln in chunk.split("\n")[1:]:
                    if ln.startswith("+++ ") or ln.startswith("--- "):
                        continue
                    if ln.startswith("+"):
                        added.append(ln[1:])
                        entry["added_count"] += 1
                    elif ln.startswith("-"):
                        removed.append(ln[1:])
                        entry["removed_count"] += 1
                entry["hunks"].append({
                    "@": [old_line, old_count, new_line, new_count],
                    "added": added,
                    "removed": removed,
                })
            files.append(entry)
        return {"files": files, "staged": staged, "empty": len(files) == 0, "raw": raw}

    def git_commit(self, message: str, dry_run: bool = False, allow_destructive: bool = False):
        """Commit staged changes with the given message.

        ANTI-FRAGILITY: gated by allow_destructive to match the
        ToolUseAgent._DESTRUCTIVE_ACTIONS pattern at services/tool_use_agent.py:73.
        Multiline message: each non-blank line becomes a separate -m flag so
        git uses the first line as the subject and the rest as the body.
        """
        try:
            if not allow_destructive:
                raise PermissionError(
                    "git_commit is destructive -- pass allow_destructive=True to confirm. "
                    "Matches ToolUseAgent._DESTRUCTIVE_ACTIONS parity."
                )
            if not message or not message.strip():
                raise ValueError("commit message must be non-empty")
            # Parity with git_status / git_diff: fail-soft when git is missing.
            git_path = shutil.which("git")
            if not git_path:
                self._audit_log({
                    "action": "git_commit",
                    "ok": False,
                    "warning": "git not on PATH",
                    "allow_destructive": allow_destructive,
                })
                return {
                    "ok": False, "sha": "", "dry_run": dry_run, "returncode": -1,
                    "stdout": "", "stderr": "", "warning": "git not on PATH",
                }
            msg_args = []
            for line in message.splitlines():
                stripped = line.strip()
                if stripped:
                    msg_args.extend(["-m", stripped])
            if not msg_args:
                raise ValueError("commit message contains only blank lines")
            cmd = [git_path, "-C", self.workspace, "commit"] + msg_args
            if dry_run:
                cmd.append("--dry-run")
            result = run_with_limits(cmd, timeout=30, cwd=self.workspace, capture_output=True, text=True)
            sha = ""
            if result.returncode == 0 and not dry_run:
                sha_res = run_with_limits(
                    ["git", "-C", self.workspace, "rev-parse", "HEAD"],
                    timeout=10, cwd=self.workspace, capture_output=True, text=True,
                )
                sha = (sha_res.stdout or "").strip() if sha_res.returncode == 0 else ""
            action = "git_commit_dry_run" if dry_run else "git_commit"
            self._audit_log({
                "action": action,
                "ok": result.returncode == 0,
                "sha": sha[:8] if sha else "",
                "msg_len": len(message),
            })
            return {
                "ok": result.returncode == 0,
                "sha": sha,
                "dry_run": dry_run,
                "returncode": result.returncode,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
            }
        except Exception as e:
            # success path inside try audits on its own; this catches VALIDATION
            # errors raised BEFORE the subprocess call (PermissionError, ValueError).
            self._audit_log({
                "action": "git_commit",
                "ok": False,
                "error_type": type(e).__name__,
                "error_msg": str(e)[:_AUDIT_ERR_MAX_CHARS],
                "allow_destructive": allow_destructive,
            })
            raise

    def parse_error(self, output: str):
        """Parse Python / pytest / ruff output into structured records.

        Returns list of {file, line, message}, deduplicated by (file, line).
        """
        if not output:
            return []
        seen = {}
        for pattern in (_RUFF_RE, _PYTEST_FAIL_RE, _TRACEBACK_RE):
            for m in pattern.finditer(output):
                key = (m.group("file"), int(m.group("line")))
                if key in seen:
                    continue
                seen[key] = {
                    "file": m.group("file").strip(),
                    "line": int(m.group("line")),
                    "message": m.group("msg").strip(),
                }
        return list(seen.values())
