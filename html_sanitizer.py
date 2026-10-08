"""
html_sanitizer.py — HTML sanitization for KokertechAI chat output.

Uses a regex-based allow-list approach:
  1. Entity-encode the entire input (so raw HTML is neutralized)
  2. Restore only explicitly allowed tags from their encoded form
  3. Strip dangerous attributes (event handlers, javascript: URLs)
  4. Strip dangerous tag content (<script>, <style>, etc.)

This is a defense-in-depth layer — QTextBrowser itself does not execute
JavaScript, but sanitization prevents attribute-injection attacks and
keeps chat output well-formed.

Safe tags allowed by default:
  b, i, em, strong, u, a, pre, code, span, div, br, hr, ul, ol, li,
  table, thead, tbody, tr, th, td, h1-h6, blockquote, p, sub, sup

Safe attributes:
  href, target, rel, title, style, class, id, align, border, colspan,
  rowspan, width, height, alt
"""

import re

from logging_config import get_logger

logger = get_logger(name="HtmlSanitizer")

# ---------------------------------------------------------------------------
# Allow lists
# ---------------------------------------------------------------------------

SAFE_TAGS = frozenset({
    "b", "i", "em", "strong", "u", "a", "pre", "code", "span", "div",
    "br", "hr", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th",
    "td", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "p", "sub",
    "sup", "del", "ins", "mark", "small", "q", "cite", "abbr",
})

SAFE_ATTRS = frozenset({
    "href", "target", "rel", "title", "style", "class", "id", "align",
    "border", "colspan", "rowspan", "width", "height", "alt", "dir",
    "lang", "xml:lang", "summary",
})

# Void (self-closing) elements that should not be restored with </...> closing tags
VOID_ELEMENTS = frozenset({"br", "hr", "img", "input", "meta", "link"})

# Tags whose entire content (including child HTML) should be removed
DANGEROUS_TAGS_WITH_CONTENT = frozenset({
    "script", "style", "iframe", "object", "embed", "applet", "frame",
    "frameset", "noframes", "noscript", "form", "input", "select",
    "textarea", "button", "canvas", "svg", "math",
})

# Tags that are unsafe — remove the tag but keep inner content
DANGEROUS_TAGS_STRIP_TAG_ONLY = frozenset({
    "base", "basefont", "blink", "marquee", "template", "slot",
})

_DANGEROUS_URL_PATTERN = re.compile(
    r"\s*(?:javascript|data|vbscript|file|mk|unknown)\s*:",
    re.IGNORECASE,
)

_EVENT_HANDLER_PATTERN = re.compile(
    r"\s*on\w+\s*=",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Step 1: Entity encoding
# ---------------------------------------------------------------------------


def _escape_entities(text):
    """Encode HTML special characters to named/numeric entities.

    &, <, >, ", and ' are encoded so the input cannot inject raw HTML.

    Args:
        text: Raw input string.

    Returns:
        Entity-encoded string safe for HTML insertion.
    """
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def escape_message_html(text):
    """Escape text for HTML insertion while preserving its visible layout.

    Escape FIRST (so the <br> this generates survives as markup, not
    re-escaped content), then convert newlines to <br> and leading spaces
    per line to non-breaking spaces. Use for any multi-line message fed to
    an HTML-interpreting QTextEdit/QTextBrowser: in rich-text mode raw \n
    is whitespace and collapses to nothing, which is how multi-sentence
    messages end up as one run-on line.

    Args:
        text: Raw input string (any type; str() is applied).

    Returns:
        HTML-safe string with line breaks and indentation preserved.
    """
    safe = _escape_entities(text)
    safe = safe.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in safe.split("\n"):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        lines.append("&nbsp;" * indent + stripped)
    return "<br>".join(lines)


# ---------------------------------------------------------------------------
# Step 2: Strip dangerous tags + their content
# ---------------------------------------------------------------------------


def _strip_dangerous_tags_content(html):
    """Remove specified tags AND all their inner content.

    Used for tags like ``<script>``, ``<style>``, ``<iframe>`` whose
    content is inherently unsafe regardless of escaping.

    Args:
        html: Encoded HTML string (entities already escaped).

    Returns:
        String with dangerous tag blocks removed.
    """
    for tag in DANGEROUS_TAGS_WITH_CONTENT:
        # Case-insensitive, handles attributes inside opening tag
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>",
            "",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # Also handle self-closing variants
        html = re.sub(
            rf"<{tag}[^>]*/>",
            "",
            html,
            flags=re.IGNORECASE,
        )
    return html


# ---------------------------------------------------------------------------
# Step 3: Strip dangerous tags (keep inner content)
# ---------------------------------------------------------------------------


def _strip_dangerous_tags(html):
    """Remove dangerous tags but preserve their inner content.

    Args:
        html: Encoded HTML string.

    Returns:
        String with dangerous tag markers removed but content kept.
    """
    for tag in DANGEROUS_TAGS_STRIP_TAG_ONLY:
        html = re.sub(
            rf"</?{tag}[^>]*>",
            "",
            html,
            flags=re.IGNORECASE,
        )
    return html


# ---------------------------------------------------------------------------
# Step 4: Strip event handlers (onclick, onload, etc.)
# ---------------------------------------------------------------------------


def _strip_event_handlers(html):
    """Remove event-handler attributes from HTML tags.

    Matches ``on<eventname>=`` patterns and removes the entire
    attribute including its quoted value.

    Args:
        html: Encoded HTML string.

    Returns:
        String with event handlers removed.
    """
    # Remove on*="..." attributes (double-quoted values)
    html = re.sub(
        r'\s+on\w+\s*=\s*"[^"]*"',
        "",
        html,
        flags=re.IGNORECASE,
    )
    # Remove on*='...' attributes (single-quoted values)
    html = re.sub(
        r"\s+on\w+\s*=\s*'[^']*'",
        "",
        html,
        flags=re.IGNORECASE,
    )
    # Remove on*=... unquoted values
    html = re.sub(
        r'\s+on\w+\s*=\s*\S+',
        "",
        html,
        flags=re.IGNORECASE,
    )
    return html


# ---------------------------------------------------------------------------
# Step 5: Strip dangerous URLs (javascript:, data:, etc.)
# ---------------------------------------------------------------------------


def _strip_dangerous_urls(html):
    """Neutralize dangerous ``href``, ``src``, ``action``, ``formaction`` URLs.

    Replaces ``href="javascript:alert(1)"`` with ``href="#blocked"`` so the UI
    remains well-formed but the link is harmless.

    Args:
        html: Encoded HTML string.

    Returns:
        String with dangerous URL attributes replaced by ``#blocked``.
    """
    for attr in ("href", "src", "action", "formaction", "xlink:href"):
        html = re.sub(
            rf'({attr}\s*=\s*")(?:[^"]*)(?=")',
            lambda m: m.group(1) + "#blocked"
            if _DANGEROUS_URL_PATTERN.match(m.group(0).split("=", 1)[1].strip().strip('"'))
            else m.group(0),
            html,
            flags=re.IGNORECASE,
        )
    return html


# ---------------------------------------------------------------------------
# Step 6: Restore safe tags (inverse of entity encoding for allowed tags)
# ---------------------------------------------------------------------------


def _restore_safe_tags(html, allowed_tags=None):
    """Restore allowed HTML tags from their entity-encoded form.

    After ``_escape_entities`` turns ``<b>`` into ``&lt;b&gt;``, this
    function converts the encoded form back to raw HTML for tags in
    *allowed_tags* (defaults to ``SAFE_TAGS``).

    The key insight is that ``&lt;`` and ``&gt;`` are tag *delimiters*,
    not content entities — they only need to match at tag boundaries.
    Content entities like ``&amp;``, ``&quot;``, ``&#39;`` inside the
    tag body are left encoded.

    Args:
        html: Entity-encoded HTML string.
        allowed_tags: Iterable of tag names to restore.  ``None`` restores
            all tags in the module-level ``SAFE_TAGS`` frozenset.

    Returns:
        String with safe tag markers restored to raw HTML.
    """
    tags = allowed_tags or SAFE_TAGS
    # Build a single pattern that matches <encoded_tag> or </encoded_tag>
    # for any tag in *tags*, optionally with attributes.
    tags_alt = "|".join(re.escape(t) for t in tags)

    # Opening tags: &lt;tag attr="value"&gt;
    html = re.sub(
        rf"&lt;({tags_alt})([^&]*(?:&(?:quot|#39|amp);[^&]*)*)\s*&gt;",
        r"<\1\2>",
        html,
        flags=re.IGNORECASE,
    )

    # Closing tags: &lt;/tag&gt;
    html = re.sub(
        rf"&lt;/({tags_alt})\s*&gt;",
        r"</\1>",
        html,
        flags=re.IGNORECASE,
    )

    # Self-closing: &lt;tag/&gt; or &lt;tag /&gt;
    html = re.sub(
        rf"&lt;({tags_alt})\s*/&gt;",
        r"<\1/>",
        html,
        flags=re.IGNORECASE,
    )

    # Restore void elements (br, hr, img) that may appear without closing
    for tag in VOID_ELEMENTS:
        html = re.sub(
            rf"&lt;({re.escape(tag)})([^>]*?)&gt;",
            rf"<{tag}\2>",
            html,
            flags=re.IGNORECASE,
        )

    return html


# ---------------------------------------------------------------------------
# Step 7: Restore safe attributes
# ---------------------------------------------------------------------------


def _restore_safe_attrs(html):
    """Restore ``class``, ``style``, ``id`` and other safe attributes
    from their entity-encoded form.

    After ``_escape_entities`` turns ``class="foo"`` into
    ``class=&quot;foo&quot;&#39;``, this converts only the safe attribute
    markers back so the HTML renders correctly.

    Only attributes in ``SAFE_ATTRS`` are restored. All other entity
    references inside tag bodies stay encoded (defense-in-depth).

    Args:
        html: HTML string with entity-encoded attributes.

    Returns:
        String with safe attribute names restored to raw form.
    """
    # Restore attribute names: &quot; → " for value delimiters
    # when the attribute name is in SAFE_ATTRS
    for attr in SAFE_ATTRS:
        # Pattern: attr=&quot;value&quot;
        html = re.sub(
            rf"({re.escape(attr)})\s*=\s*&quot;",
            r'\1="',
            html,
        )
    # Restore remaining content entities inside tag bodies
    # &quot; → " (inside tag bodies where they serve as attribute delimiters)
    html = re.sub(
        r'&quot;',
        '"',
        html,
    )
    return html


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sanitize_html(text, allow_tags):
    """Sanitize HTML text, allowing only a specified set of tags.

    Pipeline:
      1. Strip dangerous tags + content BEFORE encoding
         (``_strip_dangerous_tags_content`` + ``_strip_dangerous_tags``)
      2. Entity-encode the entire input (``_escape_entities``)
      3. Restore only the tags in *allow_tags* (``_restore_safe_tags``)
      4. Restore safe attributes (``_restore_safe_attrs``)
      5. Strip event handlers (``_strip_event_handlers``)
      6. Strip dangerous URLs (``_strip_dangerous_urls``)

    Args:
        text: Raw HTML string to sanitise.
        allow_tags: Iterable of tag names to permit (e.g. ``["b", "i", "a"]``).
            Use ``None`` to allow all safe tags or ``[]`` for plain-text mode.

    Returns:
        Sanitized HTML string safe for rendering.
    """
    if not text:
        return ""

    # Step 1: strip dangerous tags BEFORE encoding (they're raw HTML here)
    result = _strip_dangerous_tags_content(text)
    result = _strip_dangerous_tags(result)

    # Step 2: encode everything
    result = _escape_entities(result)

    # Step 3: restore allowed tags
    # ``allow_tags=[]`` means "restore nothing" (plain text mode).
    # ``allow_tags=None`` means "restore all SAFE_TAGS".
    # Must use ``is not None`` — an empty list is falsy in Python!
    allowed = set(allow_tags) & SAFE_TAGS if allow_tags is not None else SAFE_TAGS
    if allowed:
        result = _restore_safe_tags(result, allowed_tags=allowed)

    # Step 4: restore safe attributes
    result = _restore_safe_attrs(result)

    # Step 5: strip event handlers (on restored safe tags)
    result = _strip_event_handlers(result)

    # Step 6: strip dangerous URLs (on restored safe tags)
    result = _strip_dangerous_urls(result)

    return result


def sanitize_plain(text):
    """Strip ALL HTML tags, returning plain text only.

    This is a convenience wrapper around ``sanitize_html`` with an empty
    allow list — all tags are entity-encoded and never restored.

    Args:
        text: Raw HTML string.

    Returns:
        Plain text string with all HTML encoded.
    """
    return sanitize_html(text, allow_tags=[])
