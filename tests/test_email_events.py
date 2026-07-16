from datetime import UTC, datetime

from models.types import CalendarInvite, Category, Classification, Importance
from services import email_events


def _classification(category=Category.REVIEW) -> Classification:
    return Classification(
        category=category,
        confidence=0.9,
        alternatives={},
        tags=["finance"],
        reasoning="needs review",
        importance=Importance.P1,
    )


def _msg() -> dict:
    return {
        "id": "m1",
        "external_id": "ext-1",
        "subject": "Quarterly report",
        "sender": "a@b.com",
        "sender_display": "Alice",
        "to": ["ben@drolet.cloud"],
        "cc": ["team@example.com"],
        "received_at": "2026-07-15T12:00:00Z",
        "body": "hello " * 3000,  # 18k chars — must truncate to 10k
        "body_html": '<p>See <a href="https://docs.example/q2">the report</a></p>',
    }


def _invite() -> CalendarInvite:
    return CalendarInvite(
        message_id="m1",
        graph_message_id="g1",
        ical_uid="u1",
        title="Standup",
        start=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        end=datetime(2026, 7, 20, 14, 30, tzinfo=UTC),
        timezone="UTC",
        organizer="alice@b.com",
        zoom_link="https://zoom.us/j/1",
        location=None,
    )


def test_build_event_maps_and_truncates(monkeypatch):
    monkeypatch.delenv("REDIRECTOR_BASE_URL", raising=False)
    event = email_events.build_event(_msg(), _classification(), {"draft_link": "https://d"})
    assert event["event"] == "email_classified"
    assert event["category"] == "review"
    assert event["importance"] == "P1"
    assert event["confidence"] == 0.9
    assert event["message_id"] == "m1"
    assert event["draft_link"] == "https://d"
    assert event["to"] == ["ben@drolet.cloud"]
    assert event["cc"] == ["team@example.com"]
    assert len(event["body"]) == 10_000
    assert event["web_link"] is None  # no redirector base, no web_link on msg


def test_build_event_prefers_redirector_link(monkeypatch):
    monkeypatch.setenv("REDIRECTOR_BASE_URL", "https://api.example")
    event = email_events.build_event(_msg(), _classification(), None)
    assert event["web_link"] == "https://api.example/r/m1"


def test_invite_extras_builds_points_and_rsvp_links(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://inbox-webhook.example")
    monkeypatch.setenv("WEBHOOK_LABEL_TOKEN", "tok")
    points, links = email_events.invite_extras("m1", _invite())
    assert points[0].startswith("Calendar invite: Standup — 2026-07-20 14:00 UTC–14:30 UTC")
    assert [label for _, label in links] == [
        "Join Zoom",
        "Open in Google Calendar",
        "RSVP: Accept",
        "RSVP: Decline",
        "RSVP: Maybe",
    ]
    accept = next(url for url, label in links if label == "RSVP: Accept")
    assert accept == "https://inbox-webhook.example/calendar?id=m1&action=accept&token=tok"


def test_invite_extras_none_invite():
    assert email_events.invite_extras("m1", None) == ([], [])
