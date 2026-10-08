"""cli.project_context -- Sprint 18 AC #4 closeout (2026-07-19).

Detect project language, frameworks, and conventions for a workspace directory.

Design contract:
  * Pure-function ``detect(root_path)`` (no module-level singleton / mutable state)
  * Returns an IMMUTABLE ProjectContext (frozen dataclass) the caller can
    safely cache downstream
  * Stateless: caller invokes detect() per-task; Sprint 18's ``cli/agent.py``
    (deferred to a later round) calls this for agent-loop context priming

Non-singleton rationale: per Sprint 17 R12 closeout, ``services/``-residing
modules are the canonical scope for module-level singletons captured by the
``scripts/assert_singleton_reset_pattern.py`` pre-flight gate. CLI utilities
under ``cli/`` are stateless helpers that get invoked per-task -- no
module-level cache is needed or desired here, so NO ``_reset_x_for_tests()``
helper is required. The 5-rule Sprint 17 R12 convention applies to
"singleton-bearing" modules only, which this one is not.

Cross-references:
  * Sprint 18 R1 Closeout entry: docs/CHANGELOG.md
  * Sprint 18 plan: docs/IMPLEMENTATION_PLAN.md (lines 251-271)
  * Sprint 17 R12 ancestor: KNOWLEDGE.md Section 10 -- Project-Wide Singleton Convention
  * Kit-bashed from ``scripts/fix_markdownlint._detect_language`` (Sprint ?? LLM-era)
    which operated on content (not files); this module operates at the FILESYSTEM
    walker level instead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import FrozenSet, Mapping, Optional


# Language marker files at workspace root (file basename -> language slug).
# Order matters where multiple files exist: tsconfig.json always wins over
# package.json (TypeScript projects always carry a package.json).
_LANGUAGE_MARKERS: Mapping = MappingProxyType({
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    "Pipfile": "python",
    "package.json": "javascript",
    "tsconfig.json": "typescript",
    "Gemfile": "ruby",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "composer.json": "php",
})

# Convention marker files at workspace root. Files marked True must exist
# as actual files; the ".github" entry is special-cased as a DIRECTORY.
_CONVENTION_MARKERS: Mapping = MappingProxyType({
    ".editorconfig": "editorconfig",
    ".prettierrc": "prettier",
    ".prettierrc.json": "prettier",
    ".prettierrc.js": "prettier",
    ".prettierrc.yaml": "prettier",
    ".eslintrc": "eslint",
    ".eslintrc.json": "eslint",
    ".eslintrc.js": "eslint",
    ".eslintrc.yml": "eslint",
    ".pylintrc": "pylint",
    ".flake8": "flake8",
    "pytest.ini": "pytest",
    "pyrightconfig.json": "pyright",
    "mypy.ini": "mypy",
    "Makefile": "makefile",
    "Dockerfile": "dockerfile",
    ".gitignore": "gitignore",
    ".github": "github-actions",   # special-cased as dir below
    "tox.ini": "tox",
    "poetry.lock": "poetry",
})


@dataclass(frozen=True)
class ProjectContext:
    """Immutable workspace metadata detected by ``detect()``.

    Attributes:
        root_path: Absolute path to the workspace root directory.
        language: Primary language slug detected (None if no markers matched).
        frameworks: Frozen set of framework slugs (e.g. {"react", "next"}).
        conventions: Frozen set of convention slugs (e.g. {"gitignore", "pytest"}).
    """

    root_path: str
    language: Optional[str] = None
    frameworks: FrozenSet[str] = field(default_factory=frozenset)
    conventions: FrozenSet[str] = field(default_factory=frozenset)

    def validate(self) -> None:
        """Enforce invariants. Raises ``ValueError`` on violation.

        Per Coding Conventions (KokertechAI -- see KNOWLEDGE.md Section 10),
        every Schema class exposes a ``validate()`` method that raises
        ``ValueError`` (not ``AssertionError``) on contract violation. The
        sibling tests ``TestProjectContextValidation`` lock this contract.
        """
        if not isinstance(self.root_path, str) or not self.root_path:
            raise ValueError(f"root_path must be a non-empty string, got {self.root_path!r}")
        # ANTI-FRAGILITY: Python stdlib semantics for absolute-path detection
        # differ strictly between platforms -- ``os.path.isabs()`` AND
        # ``pathlib.Path.is_absolute()`` BOTH reject a POSIX-style ``/foo``
        # path on Windows as "not absolute" because it lacks a drive letter.
        # The Round 3 pathlib migration (lines ~75-78 in the prior state)
        # was effectively a no-op: pathlib has the same cross-platform
        # restrictions as os.path here. The `os.path.isabs/is_dir` calls
        # are the canonical stdlib approach and are STABLE across Python
        # versions. The TEST side (``tests/test_project_context.py``)
        # complements this by selecting a platform-correct absolute path
        # (``C:\\foo`` on Windows, ``/foo`` on POSIX). See the sibling
        # REGRESSION GUARD class ``TestProjectContextValidation`` for the
        # contract this enforces.
        if not os.path.isabs(self.root_path):
            raise ValueError(f"root_path must be absolute, got {self.root_path!r}")
        if not os.path.isdir(self.root_path):
            raise ValueError(
                f"root_path must be an existing directory, got {self.root_path!r}"
            )


def detect(root_path: str) -> ProjectContext:
    """Detect project language, frameworks, and conventions at ``root_path``.

    Walks the TOP-LEVEL of ``root_path`` only (no recursion). Marker files
    are matched by basename; framework detection inspects ``package.json``
    dependencies. Malformed ``package.json`` is gracefully tolerated --
    language still detected, framework set stays empty.

    Args:
        root_path: Absolute path to the workspace root directory.

    Returns:
        Immutable ``ProjectContext`` with detected fields populated.

    Raises:
        ValueError: If ``root_path`` is not absolute or does not exist
            (forwarded from ``ProjectContext.validate()``).
    """
    ctx = ProjectContext(root_path=root_path)
    ctx.validate()

    language = None
    frameworks: set = set()
    conventions: set = set()

    try:
        entries = os.listdir(root_path)
    except PermissionError:
        # Permission denied -> return minimal context; do not leak contents.
        return ProjectContext(
            root_path=root_path,
            language=None,
            frameworks=frozenset(),
            conventions=frozenset(),
        )

    # ── Language detection ──
    # tsconfig.json wins over package.json (TypeScript projects always carry a package.json).
    if "tsconfig.json" in entries:
        language = "typescript"
    else:
        for entry in entries:
            full = os.path.join(root_path, entry)
            if os.path.isfile(full) and entry in _LANGUAGE_MARKERS:
                language = _LANGUAGE_MARKERS[entry]
                break  # first marker wins

    # ── Convention detection (all matches accumulate) ──
    for entry in entries:
        full = os.path.join(root_path, entry)
        if entry not in _CONVENTION_MARKERS:
            continue
        if entry == ".github":
            if os.path.isdir(full):
                conventions.add(_CONVENTION_MARKERS[entry])
        else:
            if os.path.isfile(full):
                conventions.add(_CONVENTION_MARKERS[entry])

    # ── Framework detection (only inspect package.json contents if present) ──
    pkg_json_path = os.path.join(root_path, "package.json")
    if os.path.isfile(pkg_json_path):
        try:
            with open(pkg_json_path, "r", encoding="utf-8") as fh:
                pkg = json.load(fh)
            deps = pkg.get("dependencies", {}) or {}
            if not isinstance(deps, dict):
                deps = {}
            dev_deps = pkg.get("devDependencies", {}) or {}
            if not isinstance(dev_deps, dict):
                dev_deps = {}
            peer_deps = pkg.get("peerDependencies", {}) or {}
            if not isinstance(peer_deps, dict):
                peer_deps = {}
            all_deps = {**deps, **dev_deps, **peer_deps}

            if "next" in all_deps or os.path.isfile(
                os.path.join(root_path, "next.config.js")
            ):
                frameworks.add("next")
            if "react" in all_deps:
                frameworks.add("react")
            if "vue" in all_deps or "@vue/core" in all_deps:
                frameworks.add("vue")
            if "@angular/core" in all_deps:
                frameworks.add("angular")
            if "svelte" in all_deps:
                frameworks.add("svelte")
            has_vite_dep = "vite" in all_deps
            has_vite_config = any(
                os.path.isfile(os.path.join(root_path, name))
                for name in ("vite.config.ts", "vite.config.js", "vite.config.mjs")
            )
            if has_vite_dep or has_vite_config:
                frameworks.add("vite")
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError, AttributeError):
            # Malformed package.json -- skip framework detection gracefully.
            # Language detection still wins from the marker pass above.
            pass

    return ProjectContext(
        root_path=root_path,
        language=language,
        frameworks=frozenset(frameworks),
        conventions=frozenset(conventions),
    )
