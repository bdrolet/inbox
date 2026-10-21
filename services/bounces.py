"""Detect and parse non-delivery reports (NDRs / bounces).

Pure functions: no I/O. The inbox pipeline uses them to turn a bounce
message into an email_bounced event (see services.email_events).
"""

import base64
import re
from dataclasses import dataclass
from email import message_from_string, policy

from models.message import Message

NDR_SUBJECT_PREFIXES: tuple[str, ...] = (
    "Undeliverable:",
    "Delivery Status Notification (Failure)",
    "Mail delivery failed",
    "Delivery has failed",
)
_NDR_LOCAL_PARTS = {"postmaster", "mailer-daemon"}
_DSN_TYPE = "message/delivery-status"
_ADDR_RE = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_RFC822_RE = re.compile(r"rfc822;\s*(\S+@\S+)", re.IGNORECASE)


@dataclass(frozen=True)
class Bounce:
    failed_recipients: list[str]
    status: str | None
    diagnostic: str | None
    original_subject: str


def _starts_with_prefix(subject: str) -> str | None:
    s = subject.strip().lower()
    return next((p for p in NDR_SUBJECT_PREFIXES if s.startswith(p.lower())), None)


def is_ndr(sender: str, subject: str, attachments: list[dict]) -> bool:
    local = (sender or "").rpartition("@")[0].strip().lower() if "@" in (sender or "") else ""
    if local in _NDR_LOCAL_PARTS:
        return True
    if any(a.get("contentType") == _DSN_TYPE for a in attachments or []):
        return True
    return _starts_with_prefix(subject or "") is not None


def _original_subject(subject: str) -> str:
    s = (subject or "").strip()
    prefix = _starts_with_prefix(s)
    return s[len(prefix) :].strip() if prefix else s


def _parse_dsn(raw: bytes) -> tuple[list[str], str | None, str | None]:
    """Return (recipients, status, diagnostic) from RFC 3464 text."""
    recipients: list[str] = []
    status: str | None = None
    diagnostic: str | None = None
    # Per-message group first, then one group per recipient, blank-line separated.
    for group in re.split(r"\r?\n\s*\r?\n", raw.decode("utf-8", errors="replace").strip()):
        hdrs = message_from_string(group.strip(), policy=policy.default)
        field = str(hdrs.get("Final-Recipient") or hdrs.get("Original-Recipient") or "")
        m = _RFC822_RE.search(field or "")
        if not m:
            continue
        addr = m.group(1).strip().strip("<>;").lower()
        if addr not in recipients:
            recipients.append(addr)
        status = status or str(hdrs.get("Status") or "").strip() or None
        diagnostic = diagnostic or " ".join(str(hdrs.get("Diagnostic-Code") or "").split()) or None
    if not recipients:
        recipients = [
            m.strip("<>;").lower() for m in _RFC822_RE.findall(raw.decode("utf-8", "replace"))
        ]
    return recipients, status, diagnostic


def extract(msg: Message, attachments: list[dict]) -> Bounce | None:
    original_subject = _original_subject(msg.get("subject") or "")
    for att in attachments or []:
        if att.get("contentType") != _DSN_TYPE:
            continue
        try:
            raw = base64.b64decode(att.get("contentBytes") or "")
        except ValueError:
            continue
        recipients, status, diagnostic = _parse_dsn(raw)
        if recipients:
            return Bounce(recipients, status, diagnostic, original_subject)

    known = {(msg.get("sender") or "").lower()}
    known |= {a.lower() for a in (msg.get("to") or []) + (msg.get("cc") or [])}
    for m in _ADDR_RE.finditer(msg.get("body") or ""):
        addr = m.group(0).lower()
        if addr not in known:
            return Bounce([addr], None, None, original_subject)
    return None
