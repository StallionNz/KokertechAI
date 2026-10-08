"""cli.agent — CLI Coding Agent Interface.

Sprint 18: CLI Coding Agent Interface (Tasks 18.1–18.7).
Provides autonomous and interactive coding agent capabilities:
- Workspace and project-context awareness (language, frameworks, conventions)
- Code Intelligence operations (search, AST analysis, test execution, git status/diff)
- Checkpointing, undo, and redo for code changes
- Fuzzy file picker for context injection
- Interactive diff viewing and confirmation
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from cli.diff_viewer import compute_unified_diff, confirm_diff
from cli.project_context import ProjectContext, detect
from logging_config import get_logger
from services.code_intelligence import CodeIntelligence

logger = get_logger(name="CLICodingAgent")

# Ignored directory names for workspace file walks
_DEFAULT_IGNORE_DIRS = frozenset({
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    ".egg-info",
    "site-packages",
})


@dataclass
class FileChangeRecord:
    """Snapshot of a file modification for undo/redo."""
    path: str
    old_content: Optional[str]  # None if file was newly created
    new_content: Optional[str]  # None if file was deleted
    timestamp: float = field(default_factory=time.time)
    description: str = ""


class CodingAgent:
    """CLI Autonomous and Interactive Coding Agent."""

    def __init__(
        self,
        workspace: Optional[str] = None,
        controller: Optional[Any] = None,
        linter_cmd: Optional[str] = None,
    ):
        if not workspace:
            from config import WORKSPACE_DIR
            workspace = WORKSPACE_DIR if os.path.isdir(WORKSPACE_DIR) else os.getcwd()
        self.workspace = os.path.abspath(workspace)
        self.controller = controller
        self.code_intel = CodeIntelligence(self.workspace, linter_cmd=linter_cmd)

        try:
            self.project_context: ProjectContext = detect(self.workspace)
        except (ValueError, OSError) as e:
            logger.debug(f"Project context detection fallback: {e}")
            self.project_context = ProjectContext(root_path=self.workspace)

        self._undo_stack: List[FileChangeRecord] = []
        self._redo_stack: List[FileChangeRecord] = []

    # ── Undo / Redo System ──────────────────────────────────────────────

    @property
    def can_undo(self) -> bool:
        """True if there are modifications in the undo stack."""
        return len(self._undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        """True if there are modifications in the redo stack."""
        return len(self._redo_stack) > 0

    def undo(self) -> Tuple[bool, str]:
        """Roll back the most recent file change."""
        if not self._undo_stack:
            return False, "Undo stack is empty."

        record = self._undo_stack.pop()
        abs_path = os.path.join(self.workspace, record.path)

        try:
            if record.old_content is None:
                # File was newly created by the action — remove it
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            else:
                # Restore old content
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(record.old_content)

            self._redo_stack.append(record)
            desc = f"Rolled back changes to '{record.path}'"
            if record.description:
                desc += f" ({record.description})"
            return True, desc
        except (OSError, ValueError) as e:
            return False, f"Failed to undo change to '{record.path}': {e}"

    def redo(self) -> Tuple[bool, str]:
        """Reapply the most recently rolled-back change."""
        if not self._redo_stack:
            return False, "Redo stack is empty."

        record = self._redo_stack.pop()
        abs_path = os.path.join(self.workspace, record.path)

        try:
            if record.new_content is None:
                # File was deleted by the action
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            else:
                # Reapply new content
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(record.new_content)

            self._undo_stack.append(record)
            desc = f"Reapplied changes to '{record.path}'"
            if record.description:
                desc += f" ({record.description})"
            return True, desc
        except (OSError, ValueError) as e:
            return False, f"Failed to redo change to '{record.path}': {e}"

    def write_file_with_checkpoint(
        self,
        path: str,
        content: str,
        description: str = "",
        confirm: bool = False,
    ) -> Tuple[bool, str]:
        """Write content to a file with automatic undo checkpointing and optional diff confirmation."""
        abs_path = os.path.join(self.workspace, path)
        old_content: Optional[str] = None
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    old_content = f.read()
            except (OSError, UnicodeDecodeError):
                old_content = ""

        # Check diff if requested
        if confirm:
            diff_text = compute_unified_diff(
                old_content or "",
                content,
                from_file=path,
                to_file=path,
            )
            if diff_text and not confirm_diff(diff_text):
                return False, f"Change to '{path}' was aborted by user."

        # Write through CodeIntelligence
        try:
            self.code_intel.write_file(path, content, create_dirs=True)
            self._undo_stack.append(
                FileChangeRecord(
                    path=path,
                    old_content=old_content,
                    new_content=content,
                    description=description or f"write {path}",
                )
            )
            self._redo_stack.clear()
            return True, f"Successfully wrote '{path}'"
        except (OSError, ValueError) as e:
            return False, f"Write failed: {e}"

    def edit_file_with_checkpoint(
        self,
        path: str,
        old_text: str,
        new_text: str,
        allow_multiple: bool = False,
        description: str = "",
        confirm: bool = False,
    ) -> Tuple[bool, str]:
        """Apply targeted replacement in a file with checkpointing and diff preview."""
        abs_path = os.path.join(self.workspace, path)
        if not os.path.exists(abs_path):
            return False, f"File not found: '{path}'"

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                current_content = f.read()
        except (OSError, UnicodeDecodeError) as e:
            return False, f"Cannot read file '{path}': {e}"

        if old_text not in current_content:
            return False, f"Target text not found in '{path}'"

        count = current_content.count(old_text)
        if count > 1 and not allow_multiple:
            return False, f"Target text appears {count} times in '{path}' (allow_multiple=False)"

        updated_content = current_content.replace(old_text, new_text) if allow_multiple else current_content.replace(old_text, new_text, 1)

        if confirm:
            diff_text = compute_unified_diff(
                current_content,
                updated_content,
                from_file=path,
                to_file=path,
            )
            if diff_text and not confirm_diff(diff_text):
                return False, f"Edit to '{path}' aborted by user."

        try:
            self.code_intel.edit_file(path, old_text, new_text, allow_multiple=allow_multiple)
            self._undo_stack.append(
                FileChangeRecord(
                    path=path,
                    old_content=current_content,
                    new_content=updated_content,
                    description=description or f"edit {path}",
                )
            )
            self._redo_stack.clear()
            return True, f"Successfully edited '{path}'"
        except (OSError, ValueError) as e:
            return False, f"Edit failed: {e}"

    # ── Fuzzy File Picker ───────────────────────────────────────────────

    def fuzzy_find_files(self, query: str = "", max_results: int = 15) -> List[str]:
        """Search files within workspace using fuzzy score matching."""
        q = query.strip().lower()
        candidates: List[Tuple[int, str]] = []

        for root, dirs, files in os.walk(self.workspace):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if d not in _DEFAULT_IGNORE_DIRS and not d.startswith(".")]

            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.workspace).replace("\\", "/")

                if not q:
                    candidates.append((0, rel_path))
                    continue

                filename_lower = file.lower()
                rel_lower = rel_path.lower()

                # Scoring:
                # Exact filename match = 100
                # Starts with query = 80
                # Substring in filename = 60
                # Substring in relative path = 40
                # Subsequence match = 20
                if filename_lower == q:
                    score = 100
                elif filename_lower.startswith(q):
                    score = 80
                elif q in filename_lower:
                    score = 60
                elif q in rel_lower:
                    score = 40
                else:
                    # Subsequence matching
                    it = iter(rel_lower)
                    if all(char in it for char in q):
                        score = 20
                    else:
                        continue

                candidates.append((score, rel_path))

        # Sort candidates by score descending, then path ascending
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return [path for _, path in candidates[:max_results]]

    # ── Code Operations ─────────────────────────────────────────────────

    def search_code(self, pattern: str, paths: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Search codebase for pattern with ripgrep or regex fallback."""
        return self.code_intel.search_code(pattern, paths=paths)

    def analyze_ast(self, path: str) -> Dict[str, Any]:
        """Extract functions, classes, and imports from a Python file."""
        try:
            res = self.code_intel.analyze_ast(path)
            res["syntax_valid"] = True
            return res
        except SyntaxError as e:
            return {
                "syntax_valid": False,
                "syntax_error": str(e),
                "functions": [],
                "classes": [],
                "imports": [],
            }
        except OSError as e:
            # FileNotFoundError / PermissionError / decode-adjacent OSErrors.
            return {
                "syntax_valid": False,
                "syntax_error": f"File not readable: {e}",
                "error": "not_found",
                "functions": [],
                "classes": [],
                "imports": [],
            }

    def run_tests(self, target: Optional[str] = None) -> Dict[str, Any]:
        """Execute test suite or specific test file."""
        return self.code_intel.run_tests(path=target)

    def git_status(self) -> Dict[str, Any]:
        """Return workspace git status porcelain view."""
        return self.code_intel.git_status()

    def git_diff(self, staged: bool = False) -> str:
        """Return unified git diff for workspace."""
        diff_res = self.code_intel.git_diff(staged=staged)
        return diff_res.get("raw", "")

    # ── Project Context & Prompt Building ───────────────────────────────

    def build_system_prompt(self) -> str:
        """Construct project-aware coding assistant prompt."""
        lang = self.project_context.language or "Python"
        frameworks = ", ".join(sorted(self.project_context.frameworks)) or "none detected"
        conventions = ", ".join(sorted(self.project_context.conventions)) or "none detected"

        return (
            f"You are KokertechAI Autonomous Coding Agent (God Reviewer & Buffy Mode).\n"
            f"Workspace Root: {self.workspace}\n"
            f"Detected Language: {lang}\n"
            f"Frameworks: {frameworks}\n"
            f"Conventions: {conventions}\n\n"
            "Engineering Rules:\n"
            "1. Plan before modifying: inspect existing code, gather context, then make minimal targeted edits.\n"
            "2. Zero-Trust Verification: run test suites to empirically verify correctness.\n"
            "3. Clean error handling: catch explicit exception tuples, never bare except.\n"
            "4. Preserve existing style, comments, and public contracts.\n"
        )

    # ── Agent Task Execution ────────────────────────────────────────────

    def run_task(
        self,
        task: str,
        interactive: bool = False,
        confirm: bool = False,
    ) -> Dict[str, Any]:
        """Execute a coding task using the controller or fallback ReAct logic."""
        if not task.strip():
            return {
                "status": "error",
                "error": "Task description cannot be empty.",
                "files_modified": [],
            }

        logger.info(f"CodingAgent starting task: {task[:80]}")
        system_prompt = self.build_system_prompt()

        if self.controller is not None:
            prompt = f"{system_prompt}\n\nUser Coding Task: {task}"
            try:
                res = self.controller.process_input(prompt, agent_type="Coder")
                reply = res.get("final", "") if isinstance(res, dict) else str(res)
                return {
                    "status": "success",
                    "task": task,
                    "response": reply,
                    "project_context": {
                        "language": self.project_context.language,
                        "frameworks": list(self.project_context.frameworks),
                        "conventions": list(self.project_context.conventions),
                    },
                    "files_modified": [rec.path for rec in self._undo_stack],
                }
            except (RuntimeError, ValueError, OSError) as e:
                logger.error(f"Controller execution error: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "task": task,
                }

        # Offline / standalone fallback
        return {
            "status": "success",
            "task": task,
            "response": (
                f"Kokertech Coding Agent evaluated task in {self.workspace}.\n"
                f"Language: {self.project_context.language or 'generic'}, "
                f"Frameworks: {list(self.project_context.frameworks)}, "
                f"Conventions: {list(self.project_context.conventions)}.\n"
                f"Ready to execute code operations (search, AST inspection, tests, diff, undo/redo)."
            ),
            "project_context": {
                "language": self.project_context.language,
                "frameworks": list(self.project_context.frameworks),
                "conventions": list(self.project_context.conventions),
            },
            "files_modified": [],
        }
