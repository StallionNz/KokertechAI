#!/usr/bin/env python3
"""Pre-flight CI gate: verify Markdown anchor links resolve to real headings.

EXTENDED 2026-07-22 (Sprint 19.6 follow-up): scans inline anchor links in
KNOWLEDGE.md AND in docs/*.md files (cross-file drift detection). Adds two
PASS scans to ``main()``:

- Pass 1 (intra-file drift, backward-compatible with prior Sprint 18 R3):
  scan ``DOC_PATH`` (single file) for inline anchors that resolve against
  that same file's H1-H6 slug namespace. Exits 1 on HARD-BROKEN.
- Pass 2 (cross-file drift, NEW): scan every file in ``DOC_PATHS`` for
  inline anchors whose TARGET path lives in another file's namespace, and
  validate the anchor against that target file's slug set. Exits 1 on
  HARD-BROKEN. External URLs (``http://``, ``https://``, ``mailto:`` etc.)
  bypass validation.

Uses the proven GitHub html-pipeline slugify algorithm (per html-pipeline gem):
    1. Strip leading '#' chars
    2. Lowercase
    3. Strip non-{alphanumeric, hyphen, space}
    4. Replace spaces with hyphens (DOES NOT collapse consecutive hyphens)

Patterns scanned (per the project's actual Markdown dialect):
    - Markdown inline links ``[text](path#anchor)`` (intra + cross-file).
    - Bare ``§N`` / ``§N.M`` prose mentions (Tier 2, KNOWN DKNOWLEDGE.md only;
      0 such prose exists in docs/*.md today).

Exit codes:
    0 -> clean (no hard-broken anchors; drift warnings allowed)
    1 -> at least one HARD-BROKEN anchor (fails CI gate)

Drift-resistance: cite ``gh_anchor()`` by *function name* -- it mirrors GitHub's
algorithm verbatim, so future contributors using the same function will
compute the same slug. Future-proofness depends on GitHub keeping the
algorithm stable. If GitHub changes the algorithm, the test suite pinned
against this function will catch drift regressions.
"""
import re
import sys
import urllib.parse
from collections import OrderedDict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Legacy single-file scope -- preserved for test back-compat (tests/
# test_check_knowledge_anchors.py monkey-patches ``gate.DOC_PATH`` to a
# temp file). The first element of ``DOC_PATHS`` MUST equal ``DOC_PATH``
# so both intra-file and cross-file scans pick up the same baseline.
DOC_PATH = PROJECT_ROOT / "KNOWLEDGE.md"

# Pass 2 cross-file scope -- KNOWLEDGE.md plus every *.md under docs/.
# The glob is evaluated once at module load so a contributor adding a
# new docs/*.md file lands in the next CI run without a code edit.
# Drift-resistance: glob capture is the same pattern as
# ``_scratch/extract_all.py::_DOCS_GLOB``.
DOC_PATHS = [DOC_PATH] + sorted((PROJECT_ROOT / "docs").glob("*.md"))


def gh_anchor(heading_text):
    """Return the GitHub-style anchor slug for a Markdown heading."""
    s = heading_text.strip()
    if s.startswith('#'):
        s = s.lstrip('#').strip()
    s = s.lower()
    s = re.sub(r'[^a-z0-9\- ]', '', s)
    s = s.replace(' ', '-')
    return s


def load_real_slugs(doc_path):
    """Read ``doc_path`` and return {slug -> (line_no, heading_text)} for
    every H1-H6 heading. Mirrors the Sprint 18 R3 algorithm verbatim.
    """
    text = doc_path.read_text(encoding='utf-8')
    slug_to_heading = {}
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        m = re.match(r'^(#{1,6})\s+(.*)', s)
        if m:
            slug_to_heading[gh_anchor(m.group(2))] = (i, s)
    return slug_to_heading


def find_markdown_inline_links(text):
    """Yield (line_no, link_text, link_target_full) for every Markdown inline
    link OUTSIDE of backtick-fenced code spans.

    ``link_target_full`` is the raw string the link's ``(...)`` contains,
    e.g. ``"#nope"``, ``"docs/CHANGELOG.md#slug"``, ``"./KNOWLEDGE.md"``,
    ``"https://example.com#x"``.

    The backtick-parity tracker mirrors the Sprint 18 R3 defense in
    case INSIDE of code is referenced -- preserved verbatim.
    """
    refs = []
    for i, line in enumerate(text.splitlines(), 1):
        inside_code = False
        for m in re.finditer(r'`|\[([^\]]+)\]\(([^\)]+)\)', line):
            if m.group(0) == '`':
                inside_code = not inside_code
                continue
            if inside_code:
                continue
            refs.append((i, m.group(1), m.group(2)))
    return refs


def split_target(raw_target):
    """Split a raw Markdown link target into ``(path_or_url, anchor_or_None)``.

    Examples:
        ``"#nope"``              -> ``("", "nope")``   (intra-file anchor)
        ``"docs/CHANGELOG.md#v0"``-> ``("docs/CHANGELOG.md", "v0")``
        ``"./CHANGELOG.md"``     -> ``("./CHANGELOG.md", None)``
        ``"https://example.com"``-> ``("https://example.com", None)``
    """
    if '#' not in raw_target:
        return (raw_target, None)
    head, _, tail = raw_target.partition('#')
    return (head, tail)


def is_external_url(target_path):
    """Return True if the path resolves to an external URL scheme.

    Markdown inline links can be:
        - Absolute internal path  ``/path/foo.md``
        - Relative internal path  ``./foo.md`` / ``../foo.md`` / ``foo.md``
        - External URL            ``http://``, ``https://``, ``mailto:``,
                                    ``ftp://``, etc.

    External URLs bypass local anchor validation -- the gate cannot
    introspect remote targets.
    """
    parsed = urllib.parse.urlparse(target_path)
    return bool(parsed.scheme) and parsed.scheme.lower() not in ('', 'file')


def resolve_target_filename(target_path, source_path):
    """Resolve a relative target path against the source file and return the
    matching registry key (a Path's name that exists in ``DOC_PATHS``).

    Resolution order:
        1. If ``target_path`` is empty -> returns ``source_path`` (intra-file).
        2. If ``target_path`` is an absolute path (starts with ``/``) -> try
           to match by basename against the DOC_PATHS registry.
        3. Else (relative) -> resolve against the source file's parent
           directory and match by basename.
    """
    if not target_path:
        return source_path
    # Strip query / fragment bits that survived ``split_target`` (defensive).
    cleaned = target_path.split('?', 1)[0].split('\\', 1)[0]
    # Basename-only match against DOC_PATHS registry keys (Path.name).
    basename = Path(cleaned).name
    return basename or source_path


def find_bare_section_mentions(text):
    """Yield (line_no, section_number_int) for every bare mention.

    Tier 2 detection -- only meaningful for KNOWLEDE.md which carries the
    ``§N`` cross-reference convention; docs/*.md never use bare §N.
    """
    refs = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(r'\u00a7(\d+)(?:\.(\d+))?', line):
            refs.append((i, int(m.group(1))))
    return refs


def scan_intra_file_anchors(doc_path, real_slugs):
    """Pass 1: intra-file scan, backward-compatible with Sprint 18 R3 contract.

    Returns ``OrderedDict[broken_target -> (line_no_first, link_text_first)]``
    where ``broken_target`` is the raw anchor string (without leading ``#``).
    Excludes links whose target_path points at a different file (those are
    handled by Pass 2).
    """
    text = doc_path.read_text(encoding='utf-8')
    broken = OrderedDict()
    for ln, link_text, raw_target in find_markdown_inline_links(text):
        target_path, anchor = split_target(raw_target)
        if is_external_url(target_path):
            continue
        # Cross-file target -- handled by Pass 2.
        if target_path and not target_path.startswith('#'):
            continue
        if anchor is None or anchor == '':
            continue  # no anchor to validate (file-only link)
        if anchor in real_slugs:
            continue
        if anchor not in broken:
            broken[anchor] = (ln, link_text)
    return broken


def scan_cross_file_anchors(doc_paths):
    """Pass 2: cross-file scan.

    Builds the slug registry from every ``doc_paths`` entry, then re-scans
    each file for inline links whose target_path resolves to a different
    file's registry. Returns an OrderedDict keyed on
    ``(source_filename, target_filename, anchor) -> (line_no, link_text)``
    so duplicates dedup across files naturally.
    """
    # Build registry: filename -> {slug -> (line, heading)}
    # Same basename can exist in root AND docs/ (historical duplicates). Union
    # the slug sets: the last copy encountered previously OVERWROTE earlier
    # ones, silently shadowing the root knowledge base whenever a docs/ fork
    # shared its name (exposed by the 2026-09-24 docs/KNOWLEDGE.md collapse
    # -- the slug-less stub zeroed the whole key). With the fork collapsed,
    # the root copy is the only substantial one; union keeps the gate
    # correct even if same-named files ever reappear.
    registry = {}
    for p in doc_paths:
        if not p.exists():
            continue
        registry.setdefault(p.name, {}).update(load_real_slugs(p))

    broken = OrderedDict()
    for source_p in doc_paths:
        if not source_p.exists():
            continue
        text = source_p.read_text(encoding='utf-8')
        for ln, link_text, raw_target in find_markdown_inline_links(text):
            target_path, anchor = split_target(raw_target)
            if is_external_url(target_path):
                continue
            if anchor is None or anchor == '':
                continue  # no anchor to validate
            target_filename = resolve_target_filename(target_path, source_p.name)
            # Intra-file: skip (Pass 1 handles these).
            if target_filename == source_p.name:
                continue
            # External/local unknown file: skip silently -- the gate does
            # not flag broken FILE references, only broken ANCHORS within
            # a known file. (Adding file-existence checks would be
            # out-of-scope for the anchor-drift gate.)
            if target_filename not in registry:
                continue
            if anchor in registry[target_filename]:
                continue
            key = (source_p.name, target_filename, anchor)
            if key not in broken:
                broken[key] = (ln, link_text)
    return broken


def main():
    # PASS 1 -- intra-file scan of DOC_PATH (backward-compatible).
    real_slugs = {}
    if DOC_PATH.exists():
        real_slugs = load_real_slugs(DOC_PATH)

    intra_broken = scan_intra_file_anchors(DOC_PATH, real_slugs) if DOC_PATH.exists() else OrderedDict()

    # PASS 2 -- cross-file scan across DOC_PATHS.
    cross_broken = scan_cross_file_anchors(DOC_PATHS)

    # Summary line: total scanned vs total real headings (intra + cross).
    total_refs = 0
    total_slugs = 0
    for p in DOC_PATHS:
        if p.exists():
            total_refs += len(find_markdown_inline_links(p.read_text(encoding='utf-8')))
            total_slugs += len(load_real_slugs(p))

    print('[anchors] ' + str(total_refs) + ' Markdown inline links scanned across ' + str(len([p for p in DOC_PATHS if p.exists()])) + ' files vs ' + str(total_slugs) + ' real headings total')
    if intra_broken or cross_broken:
        if intra_broken:
            print('[anchors] HARD-BROKEN (intra-file): ' + str(len(intra_broken)) + ' unique broken target(s) in ' + DOC_PATH.name)
            for tgt, (ln, txt) in intra_broken.items():
                print('  ' + DOC_PATH.name + ':' + str(ln) + ': [' + txt + '](#' + tgt + ')')
        if cross_broken:
            print('[anchors] HARD-BROKEN (cross-file): ' + str(len(cross_broken)) + ' unique broken target(s) across files')
            for (src, tgt_file, anchor), (ln, txt) in cross_broken.items():
                # ``tgt_file`` is already the full filename (basename-with-extension)
                # returned by ``resolve_target_filename`` -- DON'T append ``.md``
                # redundantly (regression: ``CHANGELOG.md.md#...`` instead of
                # ``CHANGELOG.md#...``).
                print('  ' + src + ':' + str(ln) + ': link [' + txt + '] -> ' + tgt_file + '#' + anchor)
        return 1
    print('[anchors] CLEAN: all inline anchors resolve OK (intra + cross-file)')

    # Tier 2: bare §-mentions -- DRIFT WARNING (informational, exit 0).
    # Only meaningful for KNOWLEDGE.md.
    section_nums = set()
    for _, heading in real_slugs.values():
        m = re.match(r'^#+\s+(\d+)\.', heading)
        if m:
            section_nums.add(int(m.group(1)))

    if DOC_PATH.exists():
        bare = find_bare_section_mentions(DOC_PATH.read_text(encoding='utf-8'))
        drift = [(ln, sec) for ln, sec in bare if sec not in section_nums]
        if drift:
            print('[anchors] DRIFT WARNING: ' + str(len(drift)) + ' bare §N mentions without a real ## N. heading')
            for ln, sec in drift[:20]:
                print('  line ' + str(ln) + ': \u00a7' + str(sec))
        else:
            print('[anchors] CLEAN: all ' + str(len(bare)) + ' bare \u00a7-menteions reference real sections OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
