import json
from pathlib import Path

from services import bounces

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    d = json.loads((_FIXTURES / name).read_text())
    msg = {
        "sender": d["from"]["emailAddress"]["address"],
        "subject": d["subject"],
        "to": [r["emailAddress"]["address"] for r in d["toRecipients"]],
        "cc": [],
        "body": d["body"]["content"],
    }
    return msg, d["attachments"]


def test_exchange_ndr_is_detected_and_lists_both_recipients():
    msg, atts = _load("ndr_exchange.json")
    assert bounces.is_ndr(msg["sender"], msg["subject"], atts)
    b = bounces.extract(msg, atts)
    assert b.failed_recipients == ["cto@acme.com", "ceo@acme.com"]
    assert b.status == "5.1.1"
    assert b.diagnostic == "smtp;550 5.1.1 User unknown"
    assert b.original_subject == "Production-readiness audit for Acme"


def test_gmail_bounce_without_dsn_uses_body_address():
    msg, atts = _load("ndr_gmail.json")
    assert bounces.is_ndr(msg["sender"], msg["subject"], atts)
    b = bounces.extract(msg, atts)
    assert b.failed_recipients == ["founder@startup.io"] and b.status is None


def test_plain_bounce_detected_by_subject_prefix():
    msg, atts = _load("ndr_plain.json")
    assert bounces.is_ndr(msg["sender"], msg["subject"], atts)
    assert bounces.extract(msg, atts).failed_recipients == ["vp@health.co"]


def test_display_name_postmaster_is_not_an_ndr():
    assert not bounces.is_ndr("sue@acme.com", "Re: postmaster question", [])


def test_body_without_any_foreign_address_returns_none():
    msg = {
        "sender": "postmaster@x.com",
        "to": ["ben@drolet.ai"],
        "cc": [],
        "subject": "Undeliverable: hi",
        "body": "Your message could not be delivered.",
    }
    assert bounces.extract(msg, []) is None
