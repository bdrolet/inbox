import sys
import types
from datetime import UTC, datetime

# Stub the heavy DB module the handler chain imports at load time
# (handlers.actions._shared -> services.calendar_invite -> clients.db -> psycopg,
# which needs libpq and isn't available on this machine).
_fake_db = types.ModuleType("clients.db")
_fake_db.get_conn = lambda: None
sys.modules.setdefault("clients.db", _fake_db)

import handlers.actions.dispatch as dispatch_mod  # noqa: E402
import handlers.actions.respond as respond  # noqa: E402
import handlers.actions.review as review  # noqa: E402
import handlers.actions.urgent as urgent  # noqa: E402
from models.types import CalendarInvite, Category, Classification, Importance  # noqa: E402
from services import email_events  # noqa: E402


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


def test_dispatch_publishes_for_categories_without_handlers(monkeypatch):
    monkeypatch.delenv("REDIRECTOR_BASE_URL", raising=False)
    monkeypatch.setattr(dispatch_mod.archiving, "apply_tags", lambda m, c: None)
    published = []
    monkeypatch.setattr(email_events, "publish", lambda e: published.append(e))

    dispatch_mod.dispatch(_classification(Category.REFERENCE), _msg())
    dispatch_mod.dispatch(_classification(Category.IGNORE), _msg())

    assert [e["category"] for e in published] == ["reference", "ignore"]


def test_dispatch_publishes_even_when_handler_raises(monkeypatch):
    monkeypatch.delenv("REDIRECTOR_BASE_URL", raising=False)
    monkeypatch.setattr(dispatch_mod.archiving, "apply_tags", lambda m, c: None)
    monkeypatch.setattr(review, "prepare", lambda msg: 1 / 0)
    published = []
    monkeypatch.setattr(email_events, "publish", lambda e: published.append(e))

    dispatch_mod.dispatch(_classification(Category.REVIEW), _msg())

    assert len(published) == 1
    assert published[0]["category"] == "review"
    assert published[0]["seed_key_points"] is None


def test_review_returns_invite_seeds(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://inbox-webhook.example")
    monkeypatch.setenv("WEBHOOK_LABEL_TOKEN", "tok")
    monkeypatch.setattr(review, "prepare", lambda msg: _invite())

    extras = review.handle(_classification(), _msg())

    assert any(p.startswith("Calendar invite: Standup") for p in extras["seed_key_points"])
    labels = [label for _, label in extras["seed_links"]]
    assert "Join Zoom" in labels and "RSVP: Accept" in labels
    accept_url = next(url for url, label in extras["seed_links"] if label == "RSVP: Accept")
    assert accept_url.startswith("https://inbox-webhook.example/calendar?id=m1&action=accept")


def test_respond_returns_draft_link(monkeypatch):
    monkeypatch.setattr(respond, "prepare", lambda msg: None)
    monkeypatch.setattr(respond.draft_svc, "generate", lambda msg: "draft text")

    class FakeGraph:
        def create_reply_draft(self, external_id, text):
            return "https://outlook.example/draft-1"

    monkeypatch.setattr(respond, "get_graph_client", lambda: FakeGraph())

    extras = respond.handle(_classification(Category.RESPOND), _msg())
    assert extras["draft_link"] == "https://outlook.example/draft-1"


def test_urgent_push_clicks_through_to_the_email(monkeypatch):
    monkeypatch.delenv("REDIRECTOR_BASE_URL", raising=False)
    monkeypatch.setattr(urgent, "prepare", lambda msg: None)
    notified = {}
    monkeypatch.setattr(urgent.ntfy, "notify", lambda **kw: notified.update(kw))

    msg = _msg() | {"web_link": "https://outlook.example/m1"}
    urgent.handle(_classification(Category.URGENT), msg)
    # task is created async by the tasks service — the push opens the email
    assert notified["click_url"] == "https://outlook.example/m1"
