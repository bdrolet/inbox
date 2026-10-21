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


def test_postmaster_outside_local_part_is_not_an_ndr():
    # is_ndr takes the address only; callers must pass msg["sender"], never
    # sender_display.
    assert not bounces.is_ndr("sue@acme.com", "Re: postmaster question", [])
    assert not bounces.is_ndr("bob@postmaster.com", "Hello", [])
    assert not bounces.is_ndr("postmaster.bob@acme.com", "Hello", [])


def _dsn_msg_atts(dsn: bytes):
    import base64

    msg = {
        "sender": "postmaster@x.com",
        "to": ["ben@drolet.ai"],
        "cc": [],
        "subject": "",
        "body": "",
    }
    att = {"contentType": "message/delivery-status", "contentBytes": base64.b64encode(dsn).decode()}
    return msg, [att]


def test_non_ascii_dsn_headers_do_not_crash():
    dsn = "Reporting-MTA: dns;x\n\nFinal-Recipient: rfc822;a@x.com\nStatus: 5.1.1\nDiagnostic-Code: smtp; 550 Empf\u00e4nger unbekannt\n"
    b = bounces.extract(*_dsn_msg_atts(dsn.encode()))
    assert b.failed_recipients == ["a@x.com"] and b.status == "5.1.1"
    assert "Empf\u00e4nger" in b.diagnostic


def test_invalid_bytes_in_dsn_do_not_crash():
    dsn = b"Reporting-MTA: dns;x\n\nFinal-Recipient: rfc822;a@x.com\nStatus: 5.1.1\nDiagnostic-Code: smtp; 550 \xff\xfe bad\n"
    b = bounces.extract(*_dsn_msg_atts(dsn))
    assert b.failed_recipients == ["a@x.com"] and b.status == "5.1.1"


def test_body_without_any_foreign_address_returns_none():
    msg = {
        "sender": "postmaster@x.com",
        "to": ["ben@drolet.ai"],
        "cc": [],
        "subject": "Undeliverable: hi",
        "body": "Your message could not be delivered.",
    }
    assert bounces.extract(msg, []) is None


def test_exchange_delivery_delayed_is_not_a_bounce():
    msg, atts = _load("ndr_exchange_delayed.json")
    assert bounces.extract(msg, atts) is None


def test_gmail_delay_notification_is_not_a_bounce():
    msg, atts = _load("ndr_gmail_delayed.json")
    assert bounces.extract(msg, atts) is None


def test_body_only_delay_notice_is_not_a_bounce():
    msg = {
        "sender": "postmaster@drolet.ai",
        "to": ["ben@drolet.ai"],
        "cc": [],
        "subject": "Undeliverable: Delivery delayed: hi",
        "body": "Delivery is delayed to these recipients or groups: cto@acme.com",
    }
    assert bounces.extract(msg, []) is None


def test_dsn_keeps_only_failed_recipient_groups():
    dsn = (
        b"Reporting-MTA: dns;x\n\n"
        b"Final-Recipient: rfc822;slow@x.com\nAction: delayed\nStatus: 4.4.7\n\n"
        b"Final-Recipient: rfc822;gone@x.com\nAction: FAILED\nStatus: 5.1.1\n"
        b"Diagnostic-Code: smtp; 550 5.1.1 User unknown\n"
    )
    b = bounces.extract(*_dsn_msg_atts(dsn))
    assert b.failed_recipients == ["gone@x.com"]
    assert b.status == "5.1.1"


def test_dsn_without_action_keeps_only_permanent_status():
    dsn = (
        b"Reporting-MTA: dns;x\n\n"
        b"Final-Recipient: rfc822;slow@x.com\nStatus: 4.2.2\n\n"
        b"Final-Recipient: rfc822;gone@x.com\nStatus: 5.1.1\n"
    )
    b = bounces.extract(*_dsn_msg_atts(dsn))
    assert b.failed_recipients == ["gone@x.com"]


def test_body_fallback_skips_system_addresses():
    msg = {
        "sender": "mailer-daemon@googlemail.com",
        "to": ["ben@drolet.ai"],
        "cc": [],
        "subject": "Delivery Status Notification (Failure)",
        "body": "From mailer-daemon@google.com: delivery to founder@startup.io failed",
    }
    assert bounces.extract(msg, []).failed_recipients == ["founder@startup.io"]


def test_exchange_online_ndr_from_fixture():
    from clients.azure.email import Email
    from services.ingestion import normalize

    data = json.loads((_FIXTURES / "ndr_exchange_online.json").read_text())
    msg = normalize(Email(data))
    atts = data["attachments"]
    assert bounces.is_ndr(msg["sender"], msg["subject"], atts)
    b = bounces.extract(msg, atts)
    assert b.failed_recipients == ["cto@acme.com"]
    assert b.status == "5.1.10"
    assert b.diagnostic.startswith("This error occurs because")
    assert b.diagnostic.endswith("at the destination")
    assert b.original_subject == "Production-readiness audit for Acme"


def test_microsoftexchange_sender_is_ndr():
    assert bounces.is_ndr(
        "MicrosoftExchange329e71ec88ae4615bbc36ab6ce41109e@example.com", "anything", []
    )


def test_addr_regex_stops_at_glued_text():
    body = "Sender Address: ben@example.comRecipient Address: cto@acme.com"
    # Glue onto a following word character is a miss, never a wrong hit.
    assert [m.group(0) for m in bounces._ADDR_RE.finditer("cto@acme.comwasn't found")] == []
    # Capitalised glue still matches ("ben@example.comRecipient"); the
    # anchored "Sender Address:" label keeps it out of the result.
    msg = {
        "sender": "postmaster@x.com",
        "to": ["ben@example.com"],
        "cc": [],
        "subject": "Undeliverable: hi",
        "body": body,
    }
    assert bounces.extract(msg, []).failed_recipients == ["cto@acme.com"]


def test_gmail_phrase_pattern():
    msg = {
        "sender": "mailer-daemon@googlemail.com",
        "to": ["ben@example.com"],
        "cc": [],
        "subject": "Delivery Status Notification (Failure)",
        "body": "Your message wasn't delivered to founder@startup.io because the address couldn't be found",
    }
    assert bounces.extract(msg, []).failed_recipients == ["founder@startup.io"]


def test_fallback_no_longer_excludes_the_ndr_to_address():
    msg = {
        "sender": "postmaster@x.com",
        "to": ["cto@acme.com"],
        "cc": [],
        "subject": "Undeliverable: hi",
        "body": "Recipient: cto@acme.com",
    }
    assert bounces.extract(msg, []).failed_recipients == ["cto@acme.com"]


def _undeliverable(body: str, sender: str = "postmaster@x.com"):
    return {
        "sender": sender,
        "to": ["ben@example.com"],
        "cc": [],
        "subject": "Undeliverable: hi",
        "body": body,
    }


def test_exchange_phrase_with_capitalised_domain():
    for typed in ("cto@Acme.com", "CTO@ACME.COM"):
        body = f"Your message to {typed} couldn't be delivered. Sender Address: ben@drolet.ai"
        b = bounces.extract(_undeliverable(body), [])
        assert b.failed_recipients == ["cto@acme.com"], typed


def test_fallback_skips_sender_address_label_and_accepts_capitals():
    body = "Sender Address: ben@drolet.ai Recipient Address: cto@Acme.com"
    assert bounces.extract(_undeliverable(body), []).failed_recipients == ["cto@acme.com"]


def test_fallback_skips_from_label():
    body = "From: ben@drolet.ai\nSubject: Production-readiness audit\nTo: cto@acme.com"
    assert bounces.extract(_undeliverable(body), []).failed_recipients == ["cto@acme.com"]


def test_fallback_glued_lowercase_word_is_a_miss():
    assert bounces.extract(_undeliverable("cto@acme.comwasn't found"), []) is None


def test_exchange_diagnostic_trimmed_at_word_boundary():
    body = (
        "Your message to cto@acme.com couldn't be delivered. Status code: 550 5.1.10 "
        + "words " * 50
        + "end."
    )
    b = bounces.extract(_undeliverable(body), [])
    assert b.status == "5.1.10"
    assert len(b.diagnostic) <= 160 and b.diagnostic.endswith("words")


def test_exchange_phrase_with_camel_case_domain():
    body = (
        "Your message to cto@AcmeHealth.com couldn't be delivered. Sender Address: ben@drolet.ai "
        "Status code: 550 5.1.10 This error occurs because the address doesn't exist."
    )
    b = bounces.extract(_undeliverable(body), [])
    assert b.failed_recipients == ["cto@acmehealth.com"]
    assert b.status == "5.1.10"
    assert b.diagnostic == "This error occurs because the address doesn't exist"


def test_exchange_online_fixture_with_camel_case_recipient():
    from clients.azure.email import Email
    from services.ingestion import normalize

    raw = (_FIXTURES / "ndr_exchange_online.json").read_text()
    data = json.loads(raw.replace("cto@acme.com", "cto@AcmeHealth.com"))
    msg = normalize(Email(data))
    b = bounces.extract(msg, data["attachments"])
    assert b.failed_recipients == ["cto@acmehealth.com"]
    assert b.status == "5.1.10"


def test_fallback_ignores_quoted_header_block():
    body = "From: ben@x.com\nTo: cto@acme.com\nMessage-ID: <abc@mail.x.com>"
    assert bounces.extract(_undeliverable(body), []) is None


def test_sender_label_exclusion_is_anchored_to_the_next_address():
    body = "From: ben@drolet.ai\nRecipient: cto@acme.com"
    assert bounces.extract(_undeliverable(body), []).failed_recipients == ["cto@acme.com"]


def test_sender_label_with_display_name_is_skipped():
    body = 'From: "Ben Drolet" <ben@drolet.ai>\nRecipient: cto@acme.com'
    assert bounces.extract(_undeliverable(body), []).failed_recipients == ["cto@acme.com"]


def test_trim_words_never_exceeds_limit():
    assert len(bounces._trim_words("x" * 200, 160)) == 160
    assert bounces._trim_words("ab cd", 4) == "ab"
