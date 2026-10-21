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
# Delay / success notices come from the same senders and may carry a DSN, but
# the address is still live: never report these as bounces.
NON_FAILURE_SUBJECT_MARKERS: tuple[str, ...] = (
    "Delivery delayed",
    "Delivery Status Notification (Delay)",
    "Delivered:",
    "Relayed:",
    "Undeliverable: Delivery delayed",
)
_NDR_LOCAL_PARTS = {"postmaster", "mailer-daemon"}
# Exchange Online NDRs come from MicrosoftExchange<hex>@<tenant domain>.
_NDR_LOCAL_PART_PREFIXES: tuple[str, ...] = ("microsoftexchange",)
_DSN_TYPE = "message/delivery-status"
# Domain labels are lowercase only and the match may not continue into a
# lowercase letter: HTML-stripped bodies glue words onto addresses
# ("ben@example.comRecipient Address:"). Match against the body as-is, never
# lower-cased, or the glue comes back.
_ADDR = r"(?-i:[\w.+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+)(?![a-z])"
_ADDR_RE = re.compile(_ADDR)
_EXCHANGE_PHRASE = re.compile(rf"Your message to\s*({_ADDR})\s*couldn.t be delivered", re.I)
# Tried in order against the body; a phrase match beats the first-address fallback.
_PHRASE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _EXCHANGE_PHRASE,
    re.compile(rf"wasn.t delivered to\s*({_ADDR})", re.I),
    re.compile(rf"message to\s*({_ADDR})\s*(?:was|has been) (?:blocked|rejected)", re.I),
    re.compile(
        rf"(?:delivery to the following recipients? failed|failed permanently)[^@]{{0,80}}?({_ADDR})",
        re.I,
    ),
)
_EXCHANGE_STATUS_RE = re.compile(r"Status code:\s*\d{3}\s+(\d\.\d+\.\d+)\s*([^.]{0,160})", re.I)
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


def _is_non_failure_notice(subject: str) -> bool:
    s = (subject or "").strip().lower()
    return any(m.lower() in s for m in NON_FAILURE_SUBJECT_MARKERS)


def _local_part(addr: str) -> str:
    return addr.rpartition("@")[0].strip().lower() if "@" in addr else ""


def _is_permanent_failure(hdrs) -> bool:
    """True for a per-recipient DSN group that reports a permanent failure."""
    action = str(hdrs.get("Action") or "").strip().lower()
    if action:
        return action == "failed"
    return str(hdrs.get("Status") or "").strip().startswith("5.")


def is_ndr(sender: str, subject: str, attachments: list[dict]) -> bool:
    local = _local_part(sender or "")
    if local in _NDR_LOCAL_PARTS or local.startswith(_NDR_LOCAL_PART_PREFIXES):
        return True
    if any(a.get("contentType") == _DSN_TYPE for a in attachments or []):
        return True
    return _starts_with_prefix(subject or "") is not None


def _original_subject(subject: str) -> str:
    s = (subject or "").strip()
    prefix = _starts_with_prefix(s)
    return s[len(prefix) :].strip() if prefix else s


def _parse_dsn(raw: bytes) -> tuple[list[str], str | None, str | None]:
    """Return (recipients, status, diagnostic) from RFC 3464 text.

    Only per-recipient groups reporting a permanent failure count (Action
    `failed`, or no Action and a 5.x.x status); delayed/delivered/relayed/
    expanded groups are dropped.
    """
    recipients: list[str] = []
    status: str | None = None
    diagnostic: str | None = None
    # Per-message group first, then one group per recipient, blank-line separated.
    for group in re.split(r"\r?\n\s*\r?\n", raw.decode("utf-8", errors="replace").strip()):
        hdrs = message_from_string(group.strip(), policy=policy.default)
        field = str(hdrs.get("Final-Recipient") or hdrs.get("Original-Recipient") or "")
        m = _RFC822_RE.search(field or "")
        if not m or not _is_permanent_failure(hdrs):
            continue
        addr = m.group(1).strip().strip("<>;").lower()
        if addr not in recipients:
            recipients.append(addr)
        status = status or str(hdrs.get("Status") or "").strip() or None
        diagnostic = diagnostic or " ".join(str(hdrs.get("Diagnostic-Code") or "").split()) or None
    return recipients, status, diagnostic


def extract(msg: Message, attachments: list[dict]) -> Bounce | None:
    if _is_non_failure_notice(msg.get("subject") or ""):
        return None
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

    body = msg.get("body") or ""
    sender = (msg.get("sender") or "").lower()
    for pattern in _PHRASE_PATTERNS:
        m = pattern.search(body)
        if not m or m.group(1).lower() == sender:
            continue
        status = diagnostic = None
        if pattern is _EXCHANGE_PHRASE and (s := _EXCHANGE_STATUS_RE.search(body)):
            status = s.group(1)
            diagnostic = " ".join(s.group(2).split()) or None
        return Bounce([m.group(1).lower()], status, diagnostic, original_subject)

    # Generic fallback: the first address that is neither the NDR's sender nor
    # a system mailbox. To/Cc are NOT excluded: Exchange addresses the NDR to
    # the failed recipient.
    for m in _ADDR_RE.finditer(body):
        addr = m.group(0).lower()
        if addr != sender and _local_part(addr) not in _NDR_LOCAL_PARTS:
            return Bounce([addr], None, None, original_subject)
    return None
