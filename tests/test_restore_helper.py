"""
tests/test_restore_helper.py

Behavioral regression tests for scripts/restore_session_chats_backup.py.

Mirrors the 7 bash-level smoke tests we ran during the v0.x hardening cycle:

  1. --list parses manifest (no plan, no apply)
  2. dry-run does not write to target_root
  3. path-traversal rels are rejected via is_path_safe (plan.unsafe)
  4. --apply with sha-match copies files
  5. --only filter narrows the plan to the matching set
  6. --force overwrites WITHOUT saving a .pre_restore sibling
  7. --backup-current saves prior target to <name>.pre_restore_<UTC>

Tests run in-process by importing the script as a module and calling
main(argv) directly. No subprocess fork.
"""
import hashlib
import sys
from pathlib import Path

import pytest


# Make scripts/ importable so "from restore_session_chats_backup import main" works.
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from restore_session_chats_backup import (  # noqa: E402  (intentional sys.path insertion above)
    main,
    parse_manifest,
    is_path_safe,
)


# NOTE: conftest.py already provides a session-scoped `qapp` fixture
# (used by the autouse _ensure_qapp). This test file is a pure-script test
# and doesn't need any Qt fixtures, so we deliberately don't define one
# locally to avoid overriding the conftest's qapp.
#
# FIX: Apply `qapp` at module level so all test classes opt in to the
# conftest's session-scoped `qapp`. Without this, pytest-qt's `set_fixture`
# hook (which fires for every test via the session-scoped _ensure_qapp
# autouse fixture) would try to use pytest-qt's built-in function-scoped
# `qapp`, causing `ScopeMismatch: function scoped fixture qapp with
# session scoped request object`. The module-level `pytestmark` ensures
# every test in this file explicitly uses the conftest's session-scoped
# `qapp` instead of pytest-qt's function-scoped one.
pytestmark = pytest.mark.usefixtures("qapp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _stage_snapshot(snapshot_dir, files):
    """Stage a snapshot dir whose layout mirrors Backups/session_chats_<UTC>/."""
    # Each (rel -> content) pair written to snapshot_dir/<rel>; then a
    # manifest.txt in content-based format (parts[-1]=sha, parts[-2]=size, parts[0..-3]=path).
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        (snapshot_dir / rel).write_bytes(content)

    chunks = [
        "# Test snapshot manifest",
        "# Source: " + str(snapshot_dir) + "",
        "",
        "## File inventory (path | size_bytes | sha256)",
        "",
    ]
    for rel, content in files.items():
        size = len(content)
        sha = hashlib.sha256(content).hexdigest()
        chunks.append(rel.ljust(70) + "  " + str(size) + "  " + sha)
    chunks.append("")
    manifest_path = snapshot_dir / "manifest.txt"
    manifest_path.write_text(chr(10).join(chunks) + chr(10), encoding="utf-8")
    return manifest_path


def _argv(*parts):
    return [str(p) for p in parts]


# ---------------------------------------------------------------------------
# Test 1: --list parses manifest and prints summary
# ---------------------------------------------------------------------------
class TestListFlag:
    def test_list_parses_manifest(self, capsys, tmp_path):
        snapshot = tmp_path / "snap"
        manifest = _stage_snapshot(snapshot, {
            "settings_a.json": b"{}",
            "settings_b.json": b"{}",
        })
        target = tmp_path / "tgt"
        rc = main(_argv("--list", manifest, "--target", target))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Parsed 2 of 2 data lines" in out
        assert "settings_a.json" in out
        assert "settings_b.json" in out
        # --list must NOT print plan/apply banners.
        assert "WILL RESTORE" not in out
        assert "DRY-RUN COMPLETE" not in out


# ---------------------------------------------------------------------------
# Test 2: dry-run does NOT write to target
# ---------------------------------------------------------------------------
class TestDryRunNoWrite:
    def test_dry_run_does_not_write(self, capsys, tmp_path):
        snapshot = tmp_path / "snap"
        manifest = _stage_snapshot(snapshot, {"should_not_appear.json": b"{}"})
        target = tmp_path / "tgt"
        target.mkdir()
        rc = main(_argv(manifest, "--target", target))
        out = capsys.readouterr().out
        assert rc == 0
        assert "DRY-RUN COMPLETE" in out
        assert not (target / "should_not_appear.json").exists()
# ---------------------------------------------------------------------------
# Test 3: path-traversal rels refused via is_path_safe
# ---------------------------------------------------------------------------
class TestPathSafety:
    def test_path_traversal_rejected(self, capsys, tmp_path):
        snapshot = tmp_path / "snap"
        snapshot.mkdir()
        # Stage escapes.txt ONE LEVEL ABOVE snapshot at tmp_path/escapes.txt.
        # is_path_safe refuses because target_root/<rel>.resolve() escapes target_dir.
        (tmp_path / "escapes.txt").write_bytes(b"ESCAPES")
        sha = hashlib.sha256(b"ESCAPES").hexdigest()
        manifest_text = (
            "# Test snapshot\n\n## inventory\n\n"
            + "../escapes.txt".ljust(70) + "  " + str(len(b"ESCAPES")) + "  " + sha + "\n"
        )
        manifest = snapshot / "manifest.txt"
        manifest.write_text(manifest_text, encoding="utf-8")

        target = tmp_path / "tgt"
        target.mkdir()
        rc = main(_argv(manifest, "--target", target))
        out = capsys.readouterr().out
        assert rc == 0
        # Plan flags the rel as UNSAFE -> row never makes it into plan.do.
        assert "UNSAFE (path-traversal)" in out
        assert (tmp_path / "escapes.txt").exists()
        assert not any(target.iterdir())
        # Unit-level: is_path_safe denies the traversal, accepts a benign rel.
        assert not is_path_safe("../escapes.txt", target)
        assert not is_path_safe("../../../etc/passwd", target)
        assert is_path_safe("config.json", target)


# ---------------------------------------------------------------------------
# Test 4: --apply with sha-match copies file
# ---------------------------------------------------------------------------
class TestApplyWithHashMatch:
    def test_apply_overwrites_with_hash_match(self, capsys, tmp_path):
        snapshot = tmp_path / "snap"
        manifest = _stage_snapshot(snapshot, {"config.json": b'{"v": 1}'})
        target = tmp_path / "tgt"
        rc = main(_argv("--apply", "--yes", "--target", target, manifest))
        out = capsys.readouterr().out
        assert rc == 0
        assert "RESTORE COMPLETE" in out
        assert "1 files copied" in out
        assert (target / "config.json").read_bytes() == b'{"v": 1}'


# ---------------------------------------------------------------------------
# Test 5: --only filter narrows plan to matching set
# ---------------------------------------------------------------------------
class TestOnlyFilter:
    def test_only_filter_narrows_plan(self, capsys, tmp_path):
        snapshot = tmp_path / "snap"
        manifest = _stage_snapshot(snapshot, {
            "wanted.json": b"WANTED",
            "unwanted_a.json": b"U1",
            "unwanted_b.json": b"U2",
        })
        target = tmp_path / "tgt"
        rc = main(_argv("--apply", "--yes", "--only", "wanted.json",
                        "--target", target, manifest))
        out = capsys.readouterr().out
        assert rc == 0
        assert (target / "wanted.json").exists()
        assert (target / "wanted.json").read_bytes() == b"WANTED"
        assert not (target / "unwanted_a.json").exists()
        assert not (target / "unwanted_b.json").exists()
        # Non-matching files show in the SKIPPED -> FILTER subsection.
        assert "FILTER" in out
        assert "unwanted_a.json" in out
        assert "unwanted_b.json" in out
        # Lock that ONLY the matching row landed in plan.do (catches regression
        # where --only got bypassed while collision handling still leaked others).
        assert "1 files copied" in out


# ---------------------------------------------------------------------------
# Test 6: --force overwrites WITHOUT a .pre_restore sibling
# ---------------------------------------------------------------------------
class TestForceNoPreRestore:
    def test_force_overwrites_without_backup(self, capsys, tmp_path):
        snapshot = tmp_path / "snap"
        manifest = _stage_snapshot(snapshot, {"config.json": b"NEW_CONTENT_FROM_BACKUP"})
        target = tmp_path / "tgt"
        target.mkdir()
        (target / "config.json").write_bytes(b"OLD_CONTENT")

        rc = main(_argv("--apply", "--yes", "--force", "--target", target, manifest))
        out = capsys.readouterr().out
        assert rc == 0
        assert "OVERWRITE (--force)" in out
        assert (target / "config.json").read_bytes() == b"NEW_CONTENT_FROM_BACKUP"
        # Contract: --force must NOT create .pre_restore siblings.
        siblings = list(target.glob("config.json.pre_restore_*"))
        assert siblings == [], "force must not create .pre_restore siblings; got " + str(siblings)
        # Lock the apply-count contract catches the regression where --force
        # accidentally drops the row from plan.do before copy.
        assert "1 files copied" in out


# ---------------------------------------------------------------------------
# Test 7: --backup-current saves prior to .pre_restore_<UTC>
# ---------------------------------------------------------------------------
class TestBackupCurrentSavesPrior:
    def test_backup_current_saves_prior(self, capsys, tmp_path):
        snapshot = tmp_path / "snap"
        manifest = _stage_snapshot(snapshot, {"config.json": b"NEW_CONTENT_FROM_BACKUP"})
        target = tmp_path / "tgt"
        target.mkdir()
        (target / "config.json").write_bytes(b"OLD_CONTENT")

        rc = main(_argv("--apply", "--yes", "--backup-current",
                        "--target", target, manifest))
        out = capsys.readouterr().out
        assert rc == 0
        assert "OVERWRITE (saving current to .pre_restore)" in out
        assert (target / "config.json").read_bytes() == b"NEW_CONTENT_FROM_BACKUP"
        # Contract: exactly one .pre_restore_<UTC> sibling with the OLD content.
        siblings = list(target.glob("config.json.pre_restore_*"))
        assert len(siblings) == 1
        assert siblings[0].read_bytes() == b"OLD_CONTENT"
        # Lock the apply-count contract catches the regression where --backup-current
        # accidentally drops the row from plan.do before copy.
        assert "1 files copied" in out


# ---------------------------------------------------------------------------
# Unit-level: parse_manifest is content-based (not position-based)
# ---------------------------------------------------------------------------
class TestParseManifestContract:
    def test_parse_skips_comments_and_blanks(self):
        text = (
            "# header\n"
            "\n"
            + "settings_a.json" + " " * 50 + "42  " + "a" * 64 + "\n"
        )
        rows, skipped = parse_manifest(text)
        assert rows == [("settings_a.json", 42, "a" * 64)]
        assert skipped == 0

    def test_parse_rejects_bad_sha(self):
        text = "bad.json  10  notasha\n"
        rows, skipped = parse_manifest(text)
        assert rows == []
        assert skipped == 1

    def test_parse_rejects_bad_size(self):
        text = "bad.json  SIZE  " + "a" * 64 + "\n"
        rows, skipped = parse_manifest(text)
        assert rows == []
        assert skipped == 1

    def test_parse_accepts_filenames_with_spaces(self):
        sha = "b" * 64
        text = "Freebuff session 1 2026_06_01 16_58_17.md                 42870  " + sha + "\n"
        rows, skipped = parse_manifest(text)
        assert rows == [("Freebuff session 1 2026_06_01 16_58_17.md", 42870, sha)]
        assert skipped == 0


# ---------------------------------------------------------------------------
# Post-restore accumulation warning (Option 2 of scope-deferred fix).
# ---------------------------------------------------------------------------
class TestPreRestoreAccumulationWarning:
    """Locks the post-restore line that surfaces `.pre_restore_<UTC>` siblings.
    The lighter-weight fix per scope discipline: emit a count + WARN line after a
    successful apply so the operator sees accumulation without an auto-prune
    foot-gun. A future `--prune-restore-backups keep=N` flag is the active
    alternative (currently deferred)."""

    def test_warns_after_backup_current_restore(self, capsys, tmp_path):
        snapshot = tmp_path / "snap"
        manifest = _stage_snapshot(snapshot, {"config.json": b"NEW_CONTENT_FROM_BACKUP"})
        target = tmp_path / "tgt"
        target.mkdir()
        (target / "config.json").write_bytes(b"OLD_CONTENT")

        rc = main(_argv("--apply", "--yes", "--backup-current",
                        "--target", target, manifest))
        out = capsys.readouterr().out
        assert rc == 0
        # WARN line surfaces the new sibling count alongside the target path.
        assert "WARN: 1 accumulated .pre_restore_* sibling(s)" in out
        assert str(target) in out

    def test_no_warn_for_plain_apply_without_backup_current(self, capsys, tmp_path):
        snapshot = tmp_path / "snap"
        manifest = _stage_snapshot(snapshot, {"config.json": b"NONE"})
        target = tmp_path / "tgt"

        rc = main(_argv("--apply", "--yes", "--target", target, manifest))
        out = capsys.readouterr().out
        assert rc == 0
        # No --backup-current and no pre-existing siblings -> no WARN, zero-baseline line.
        assert "WARN:" not in out
        assert "0 .pre_restore_* siblings accumulated" in out

    def test_warn_counts_cumulative_siblings_across_runs(self, capsys, tmp_path):
        # Stage multiple pre-existing stale sibling files (left-behind from prior runs)
        # so the WARN surfaces the cumulative count, not just the new one.
        snapshot = tmp_path / "snap"
        manifest = _stage_snapshot(snapshot, {"config.json": b"NEW"})
        target = tmp_path / "tgt"
        target.mkdir()
        (target / "config.json.pre_restore_20260101_000000").write_bytes(b"OLD1")
        (target / "config.json.pre_restore_20260102_000000").write_bytes(b"OLD2")

        # --force creates no new sibling (force path skips _pre_restore_path).
        rc = main(_argv("--apply", "--yes", "--force", "--target", target, manifest))
        out = capsys.readouterr().out
        assert rc == 0
        # WARN counts ALL cumulative siblings in target_root, including stale ones.
        assert "WARN: 2 accumulated .pre_restore_* sibling(s)" in out
