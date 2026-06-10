import json
import logging
import re
from html.parser import HTMLParser

import clients.claude as claude
from models.message import Message
from models.types import EmailSummary

logger = logging.getLogger(__name__)

_NOISE = re.compile(
    r"unsubscribe|tracking|pixel|open.?in.?browser|view.?online|manage.?preferences",
    re.IGNORECASE,
)
_GENERIC_LABELS = {"click here", "here", "link", "this link", "more", "read more"}


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href.startswith("http"):
                self._href = href
                self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            label = " ".join("".join(self._text).split())
            if (
                label
                and label.lower() not in _GENERIC_LABELS
                and not _NOISE.search(label)
                and not _NOISE.search(self._href)
            ):
                self.links.append((self._href, label))
            self._href = None
            self._text = []


def _extract_links(html: str) -> list[tuple[str, str]]:
    extractor = _LinkExtractor()
    try:
        extractor.feed(html)
    except Exception:
        return []
    seen: set[str] = set()
    result = []
    for url, label in extractor.links:
        if url not in seen:
            seen.add(url)
            result.append((url, label))
    return result


def generate(msg: Message, html_body: str | None = None) -> EmailSummary:
    """Return key points and relevant links for the email."""
    links = _extract_links(html_body) if html_body else []

    body_text = (msg["body"] or "")[:3000]
    prompt = (
        "Summarize this email in 2-3 concise bullet points. Be specific about what action "
        "is requested or what information is conveyed. No preamble.\n"
        'Return JSON only: {"key_points": ["point 1", "point 2"]}\n\n'
        f"Subject: {msg['subject']}\n"
        f"From: {msg.get('sender_display') or msg['sender']}\n\n"
        f"{body_text}"
    )
    key_points: list[str] = []
    try:
        raw = claude.summarize(prompt)
        data = json.loads(raw)
        key_points = data.get("key_points", [])
    except Exception:
        logger.warning("Email summary generation failed for message_id=%s", msg["id"])

    return EmailSummary(key_points=key_points, relevant_links=links)
