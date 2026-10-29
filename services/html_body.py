"""Plain text → HTML for outbound email bodies.

Graph renders a reply's `comment` as HTML whatever the caller intended, so a
plain-text body loses every newline and arrives as one run-on paragraph. This
is the one place that conversion happens.
"""

import html
import re

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")


def text_to_html(text: str) -> str:
    """Blank-line-separated blocks become <p>; single newlines become <br>."""
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return ""
    paragraphs = []
    for block in _PARAGRAPH_BREAK.split(normalized):
        lines = [html.escape(line.strip(), quote=False) for line in block.split("\n")]
        paragraphs.append("<p>" + "<br>".join(lines) + "</p>")
    return "".join(paragraphs)
