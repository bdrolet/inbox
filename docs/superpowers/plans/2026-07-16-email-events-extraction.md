# Inbox Email-Events Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inbox stops creating Asana tasks directly; instead it publishes `email_classified` and `label_applied` domain events to a new inbox-owned `email-events` Pub/Sub topic, which the tasks repo (`github.com/bdrolet/tasks`) consumes for all task policy, enrichment, and Asana work.

**Architecture:** Action handlers change contract from `(classification, msg) -> None` (side-effecting Asana calls) to `-> dict | None` (event extras: `draft_link`, `seed_key_points`, `seed_links`). `dispatch()` publishes exactly one `email_classified` event per processed email, for **all five categories** — the tasks service owns the policy for which become tasks. Asana client, tag cache, summary and deadline enrichment are deleted (migrated to the tasks repo). Urgent keeps its synchronous ntfy push, but the tap target becomes the email's redirector link instead of a task URL.

**Tech Stack:** Python 3.13, google-cloud-pubsub, OTel trace-context injection, pytest, Terraform (GCP), Graph API.

**Source spec:** Task 16 + Task 20 in `/Users/ben/src/tasks/docs/superpowers/plans/2026-07-15-tasks-repo-setup.md`. Event payloads must match tasks' `models/events.py` **exactly**.

**Deviation from the spec (deliberate):** the spec inlines the Pub/Sub publisher inside `services/email_events.py`; this plan splits the raw I/O into a new `clients/pubsub.py` per this repo's layer rules (`clients/` = I/O only, `services/` = business logic). Payloads, topic, and tests are identical — `email_events.publish(event)` remains the domain entry point.

## Global Constraints

- **Two phases, hard stop between them.** Phase A (Tasks 1–9) ends with a committed branch and NO deploy. Phase B (Tasks 10–13) runs only after Ben confirms the tasks CFs are live — events published before the tasks subscription exists are silently dropped.
- **NEVER touch `terraform/secrets.tf`.** The `asana-api-key` secret must keep existing (tasks repo consumes it); inbox's `anthropic-api-key` stays (classification + drafts still use it; tasks has its own `tasks-anthropic-api-key`).
- Inbox keeps: `ANTHROPIC_API_KEY`, all Graph/MSAL, ntfy, HubSpot, calendar RSVP flow, the redirector.
- Event payload caps: `body` at 10 000 chars, `body_html` at 200 000.
- Repo workflow: feature branch `tasks-service-extraction`, never commit to `main`, PR via `/pr-open` (Phase B only).
- **Stage files by explicit path only** — the working tree carries an unrelated untracked file (`scripts/get_google_calendar_token.py`); never `git add .` / `git add -A`.
- Terraform changes preview/apply via the `/terraform-plan` and `/terraform-apply` skills only.
- Local CI = `.venv/bin/pytest tests/ -q` + `.venv/bin/ruff check . && .venv/bin/ruff format --check .` + `.venv/bin/mypy clients/ services/ handlers/ models/ repo/ main.py` + `terraform validate`.

## Skills used by this plan

| Skill | Where |
|---|---|
| `/terraform-plan` + `/terraform-apply` | Task 2 (topic, Phase A), Task 12 (code deploy, Phase B) — never hand-rolled |
| `/verifying-pr-locally` | Task 9 (pre-stop confidence), Task 11 (post-PR, posts results to the PR) |
| `/pr-open` | Task 11 — required by inbox CLAUDE.md; never hand-roll `git push` + `gh pr create` |
| `/deploy-inbox` (post-deploy checks) / `/fetch-inbox-logs` | Task 13 Step 1 |
| `/testing-inbox-pipeline` + `/tracing-inbox-email` | Task 13 Step 2 (live happy path) |
| `/monitoring-inbox-deploy` | Task 13 Step 4 (watch the GH Actions deploy after merge) |
| `/querying-grafana-metrics` | Task 13 Step 2 (optional: pipeline metrics still emitting) |

## Progress marker

Steps already executed (2026-07-16 session): Task 1 complete; Task 2 Steps 1–2 complete (terraform edits committed as `7f6b73d` on `tasks-service-extraction`). **Resume at Task 2 Step 3 (GATE A1).**

---

### Task 1: Branch off up-to-date main ✅

**Files:** none (git only)

- [x] **Step 1: Create the branch**

```bash
cd /Users/ben/src/inbox
git checkout main && git pull
git checkout -b tasks-service-extraction
```

Expected: branch created from origin/main HEAD. `git status --short` shows only the pre-existing untracked `scripts/get_google_calendar_token.py` — leave it alone.

---

### Task 2: Terraform — email-events topic, applied BEFORE any code edits

**Why first:** an inbox terraform apply re-zips and redeploys the processor CF source. Applying while the code tree is identical to `main` ships nothing new; applying mid-edit would ship half-finished code. The topic must also exist before the tasks repo's own apply can reference it.

**Files:**
- Modify: `terraform/pubsub.tf` (append topic)
- Modify: `terraform/iam.tf` (append publisher binding after `process_cf_google_calendar`)

- [x] **Step 1: Add the topic and binding**

`terraform/pubsub.tf` — append:

```hcl
# Domain events: one email_classified per processed email + label_applied
# feedback. The tasks repo's CF subscribes (its terraform references this
# topic by name — this apply must run before the tasks repo's apply).
resource "google_pubsub_topic" "email_events" {
  name       = "email-events"
  depends_on = [google_project_service.apis]
}
```

`terraform/iam.tf` — append to the processor-SA section:

```hcl
# Publish email_classified / label_applied domain events (consumed by the
# tasks repo — github.com/bdrolet/tasks)
resource "google_pubsub_topic_iam_member" "process_cf_email_events_publisher" {
  topic  = google_pubsub_topic.email_events.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.process_cf.email}"
}
```

- [x] **Step 2: Commit just these two files**

```bash
git add terraform/pubsub.tf terraform/iam.tf
git commit -m "feat: email-events topic for the tasks service"
```

- [ ] **Step 3: 🚧 GATE A1 — terraform plan review (human-verifiable expected output)**

Run the `/terraform-plan` skill. The plan MUST match:

- **Add (2):** `google_pubsub_topic.email_events`, `google_pubsub_topic_iam_member.process_cf_email_events_publisher`
- **Change (0–2):** only CF source re-zip noise (`google_storage_bucket_object.*` / `google_cloudfunctions2_function.*` source hash) — the zipped code is identical to `main`, so this is harmless
- **Destroy (0):** anything to destroy → **STOP, do not apply, report to Ben**
- Any add/change beyond the above → **STOP and report** (state drift; must be understood first)

- [ ] **Step 4: Apply**

Run the `/terraform-apply` skill.

- [ ] **Step 5: Verify the topic exists**

```bash
gcloud pubsub topics describe email-events --project=bens-project-462804 --format='value(name)'
```

Expected: `projects/bens-project-462804/topics/email-events`. The tasks repo apply (its Task 18) is now unblocked.

---

### Task 3: Carry `to`/`cc` recipients through `Message`

The Graph fetch already `$select`s `toRecipients`/`ccRecipients` and `clients/azure/email.py:15-16` parses them into `Email.to_recipients`/`cc_recipients`, but `normalize()` drops them. The event payload needs them.

**Files:**
- Modify: `models/message.py` (two fields)
- Modify: `services/ingestion.py:33-46` (`normalize()` return)
- Test: `tests/test_ingestion_fetch.py` (append)

**Interfaces:**
- Produces: `Message["to"]: list[str]`, `Message["cc"]: list[str]` — plain address lists, consumed by `email_events.build_event` (Task 4).
- `repo/messages.py` inserts named columns, so the extra dict keys are inert — no schema change.

- [ ] **Step 1: Write the failing test** — append to `tests/test_ingestion_fetch.py`:

```python
def test_normalize_carries_to_and_cc():
    from clients.azure.email import Email

    email = Email(
        {
            "id": "g1",
            "subject": "s",
            "from": {"emailAddress": {"name": "Alice", "address": "a@b.com"}},
            "toRecipients": [
                {"emailAddress": {"name": "Ben", "address": "ben@drolet.cloud"}},
                {"emailAddress": {"name": "NoAddr"}},
            ],
            "ccRecipients": [{"emailAddress": {"address": "team@example.com"}}],
            "body": {"contentType": "text", "content": "hi"},
            "receivedDateTime": "2026-07-15T12:00:00Z",
        }
    )
    msg = ingestion.normalize(email)
    assert msg["to"] == ["ben@drolet.cloud"]
    assert msg["cc"] == ["team@example.com"]
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `.venv/bin/pytest tests/test_ingestion_fetch.py -v`
Expected: FAIL with `KeyError: 'to'` (TypedDict missing keys).

- [ ] **Step 3: Implement**

`models/message.py` — add below `subject`:

```python
    to: list[str]  # recipient addresses
    cc: list[str]
```

`services/ingestion.py` `normalize()` — add to the returned `Message` (after `subject=...`):

```python
        to=[r.get("address", "") for r in email.to_recipients if r.get("address")],
        cc=[r.get("address", "") for r in email.cc_recipients if r.get("address")],
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/bin/pytest tests/test_ingestion_fetch.py -v` → PASS.
Also run the full suite (`.venv/bin/pytest tests/ -q`) — other tests build `Message` dicts and TypedDict is not runtime-enforced, so no breakage expected; fix any surprise before moving on.

- [ ] **Step 5: Commit**

```bash
git add models/message.py services/ingestion.py tests/test_ingestion_fetch.py
git commit -m "feat: carry to/cc recipients through Message"
```

---

### Task 4: `clients/pubsub.py` + `services/email_events.py` — publisher, `build_event`, `invite_extras`

**Files:**
- Create: `clients/pubsub.py` (raw Pub/Sub I/O — see the deviation note in the header)
- Create: `services/email_events.py` (domain: topic name, payload shape, logging)
- Test: `tests/test_email_events.py` (new)

**Interfaces:**
- Consumes: `services.links.redirector_url(uuid) -> str | None`, `models.types.CalendarInvite`, `models.message.Message` (incl. Task 3's `to`/`cc`).
- Produces:
  - `clients.pubsub.publish(topic: str, event: dict) -> None` — cached publisher, OTel trace-context attrs; reusable by any future publisher.
  - Used by Tasks 5–6 (all in `services.email_events`):
    - `publish(event: dict) -> None` — publishes to `email-events` via `clients.pubsub`.
    - `build_event(msg, classification, extras: dict | None) -> dict` — the `email_classified` payload.
    - `invite_extras(message_id: str, invite: CalendarInvite | None) -> tuple[list[str], list[list[str]]]` — (key-point lines, `[url, label]` links).

- [ ] **Step 1: Write the failing tests** — create `tests/test_email_events.py`:

```python
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
```

- [ ] **Step 2: Run — expect FAIL**

Run: `.venv/bin/pytest tests/test_email_events.py -v`
Expected: FAIL with `ImportError: cannot import name 'email_events'`.

- [ ] **Step 3: Implement** — two files, split per the repo's layer rules (`clients/` = I/O only; the service owns the topic name, payload shape, and logging).

Create `clients/pubsub.py`:

```python
"""Thin Pub/Sub publisher. I/O only — topic choice and payload shape belong
to the calling service.

The publisher client and topic paths are cached (CF instances are reused
across invocations). OTel trace context is injected as message attributes so
the consumer can continue the trace.
"""

import json
import os

from google.cloud import pubsub_v1
from opentelemetry.propagate import inject

_publisher: pubsub_v1.PublisherClient | None = None
_topic_paths: dict[str, str] = {}


def _client(topic: str) -> tuple[pubsub_v1.PublisherClient, str]:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    if topic not in _topic_paths:
        _topic_paths[topic] = _publisher.topic_path(os.environ["GCP_PROJECT_ID"], topic)
    return _publisher, _topic_paths[topic]


def publish(topic: str, event: dict) -> None:
    """Publish a JSON-encoded event to the named topic with trace context."""
    publisher, path = _client(topic)
    carrier: dict = {}
    inject(carrier)
    publisher.publish(path, json.dumps(event).encode(), **carrier)
```

Create `services/email_events.py`:

```python
"""Build and publish email domain events (the email-events topic).

One email_classified event per processed email (every category — the tasks
repo, github.com/bdrolet/tasks, owns the policy for which become Asana tasks)
plus label_applied feedback events. Tasks' models/events.py mirrors these
payloads exactly.

Calendar invites are deliberately NOT a dedicated payload field: invite facts
travel as seed_key_points and the RSVP/calendar links as seed_links
(invite_extras below), so the tasks service renders them with zero
calendar-specific code.
"""

import logging
import os
import urllib.parse

from clients import pubsub
from models.message import Message
from models.types import CalendarInvite, Classification
from services.links import redirector_url

logger = logging.getLogger(__name__)

_TOPIC = "email-events"

# Defensive truncation — Pub/Sub caps messages at 10MB; enrichment in tasks
# reads at most the first 3000 chars of body and parses body_html for links.
_BODY_LIMIT = 10_000
_HTML_LIMIT = 200_000


def publish(event: dict) -> None:
    pubsub.publish(_TOPIC, event)
    logger.info(
        "Published %s event for message_id=%s", event.get("event"), event.get("message_id")
    )


def build_event(msg: Message, classification: Classification, extras: dict | None = None) -> dict:
    """Assemble the email_classified payload from the message, its
    classification, and the action handler's extras (draft_link, invite seeds)."""
    extras = extras or {}
    return {
        "event": "email_classified",
        "message_id": str(msg["id"]),
        "category": classification.category.value,
        "importance": classification.importance.value,
        "confidence": classification.confidence,
        "subject": msg["subject"],
        "sender": msg["sender"],
        "sender_display": msg.get("sender_display") or msg["sender"],
        "to": msg.get("to") or [],
        "cc": msg.get("cc") or [],
        "received_at": str(msg["received_at"]),
        "tags": classification.tags,
        "reasoning": classification.reasoning,
        "body": (msg["body"] or "")[:_BODY_LIMIT],
        "body_html": (msg.get("body_html") or "")[:_HTML_LIMIT] or None,
        "web_link": redirector_url(str(msg.get("id") or "")) or msg.get("web_link"),
        "draft_link": extras.get("draft_link"),
        "seed_key_points": extras.get("seed_key_points"),
        "seed_links": extras.get("seed_links"),
    }


def invite_extras(
    message_id: str, invite: CalendarInvite | None
) -> tuple[list[str], list[list[str]]]:
    """Fold a calendar invite into generic task_create fields.

    Returns (key_points lines, [url, label] links) to append to the event's
    key_points and relevant_links. RSVP links hit inbox's webhook /calendar
    endpoint (GET, token-authenticated) — same URLs the old Asana calendar
    block used.
    """
    if invite is None:
        return [], []

    start = invite.start.strftime("%Y-%m-%d %H:%M %Z") if invite.start else ""
    end = invite.end.strftime("%H:%M %Z") if invite.end else ""
    points = [
        f"Calendar invite: {invite.title or '(untitled)'} — {start}–{end}, "
        f"organizer {invite.organizer or 'unknown'}"
    ]
    if invite.location:
        points.append(f"Location: {invite.location}")

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    label_token = os.environ.get("WEBHOOK_LABEL_TOKEN", "")

    def cal_url(action: str) -> str:
        params = f"id={message_id}&action={action}"
        if label_token:
            params += f"&token={urllib.parse.quote(label_token, safe='')}"
        return f"{webhook_url}/calendar?{params}"

    links: list[list[str]] = []
    if invite.zoom_link:
        links.append([invite.zoom_link, "Join Zoom"])
    gcal = (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={urllib.parse.quote(invite.title or '')}"
        f"&dates={invite.start.strftime('%Y%m%dT%H%M%SZ') if invite.start else ''}"
        f"/{invite.end.strftime('%Y%m%dT%H%M%SZ') if invite.end else ''}"
        f"&location={urllib.parse.quote(invite.location or invite.zoom_link or '')}"
    )
    links.append([gcal, "Open in Google Calendar"])
    links.append([cal_url("accept"), "RSVP: Accept"])
    links.append([cal_url("decline"), "RSVP: Decline"])
    links.append([cal_url("maybe"), "RSVP: Maybe"])
    return points, links
```

Note: `clients/pubsub.py` and the service's `publish()` are thin I/O glue over the Pub/Sub client (same pattern as `functions/webhook/main.py:67-75`, which stays hand-rolled — `functions/` entry points are standalone and can't import `clients/`) — they get no unit test; Phase B's live check covers them.

- [ ] **Step 4: Run — expect PASS**

Run: `.venv/bin/pytest tests/test_email_events.py -v` → 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add clients/pubsub.py services/email_events.py tests/test_email_events.py
git commit -m "feat: pubsub client + email_events service — build_event, invite_extras, publisher"
```

---

### Task 5: New handler contract — dispatch publishes, handlers return extras

**Files:**
- Modify: `handlers/actions/dispatch.py` (full rewrite)
- Modify: `handlers/actions/_shared.py` (full rewrite — trim to invite detection)
- Modify: `handlers/actions/review.py`, `handlers/actions/respond.py`, `handlers/actions/urgent.py` (full rewrites)
- Modify: `clients/ntfy.py:11-56` (rename `task_url` → `click_url`)
- Test: `tests/test_email_events.py` (append)

**Interfaces:**
- Consumes: Task 4's `email_events.publish/build_event/invite_extras`; existing `services.calendar_invite.detect/store`, `services.draft_reply.generate`, `clients.graph.get_graph_client().create_reply_draft`, `services.links.redirector_url`, `services.archiving.apply_tags`, `clients.ntfy.notify`.
- Produces: `review/respond/urgent.handle(classification, msg) -> dict` returning keys from `{draft_link, seed_key_points, seed_links}`; `_shared.prepare(msg) -> CalendarInvite | None`; `ntfy.notify(..., click_url: str | None = None)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_email_events.py`. The imports go into the **top import block** of the file (ruff enforces E402 + I001 on tests — mid-file imports fail lint):

```python
import handlers.actions.dispatch as dispatch_mod
import handlers.actions.respond as respond
import handlers.actions.review as review
import handlers.actions.urgent as urgent
```

New tests:

```python
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
```

- [ ] **Step 2: Run — expect FAIL**

Run: `.venv/bin/pytest tests/test_email_events.py -v`
Expected: the five new tests FAIL (handlers still have the old `-> None` Asana-calling contract; `review.prepare` doesn't exist as a 1-arg function; ntfy has no `click_url`).

- [ ] **Step 3: Rewrite `handlers/actions/dispatch.py`** (full file):

```python
import logging

from handlers.actions import respond, review, urgent
from models.message import Message
from models.types import Category, Classification
from services import archiving, email_events

logger = logging.getLogger(__name__)

_HANDLERS = {
    Category.URGENT: urgent.handle,
    Category.RESPOND: respond.handle,
    Category.REVIEW: review.handle,
}


def dispatch(classification: Classification, msg: Message) -> None:
    logger.info(
        "Dispatching %s (importance=%s) for message_id=%s",
        classification.category.value,
        classification.importance.value,
        msg.get("id"),
    )
    try:
        archiving.apply_tags(msg, classification)
    except Exception:
        logger.exception("apply_tags failed for %s", msg.get("id"))

    extras: dict = {}
    handler = _HANDLERS.get(classification.category)
    if handler:
        try:
            extras = handler(classification, msg) or {}
        except Exception:
            logger.exception(
                "Action handler failed for %s/%s", classification.category.value, msg.get("id")
            )

    try:
        email_events.publish(email_events.build_event(msg, classification, extras))
    except Exception:
        logger.exception("email_classified publish failed for %s", msg.get("id"))
```

- [ ] **Step 4: Rewrite `handlers/actions/_shared.py`** (full file — summary/deadline enrichment moved to tasks; the redirector link moved into `email_events.build_event`):

```python
from clients.graph import get_graph_client
from models.message import Message
from models.types import CalendarInvite
from services import calendar_invite as calendar_invite_svc


def prepare(msg: Message) -> CalendarInvite | None:
    """Detect and store a calendar invite (RSVP flow stays in inbox). Summary
    and deadline enrichment now live in the tasks repo."""
    invite = calendar_invite_svc.detect(msg, get_graph_client())
    if invite:
        calendar_invite_svc.store(invite)
    return invite
```

- [ ] **Step 5: Rewrite the three action handlers**

`handlers/actions/review.py` (full file):

```python
from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification
from services import email_events


def handle(classification: Classification, msg: Message) -> dict:
    invite = prepare(msg)
    points, links = email_events.invite_extras(str(msg["id"]), invite)
    return {"seed_key_points": points or None, "seed_links": links or None}
```

`handlers/actions/respond.py` (full file):

```python
import logging

from clients.graph import get_graph_client
from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification
from services import draft_reply as draft_svc
from services import email_events

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> dict:
    invite = prepare(msg)
    points, links = email_events.invite_extras(str(msg["id"]), invite)
    extras: dict = {"seed_key_points": points or None, "seed_links": links or None}

    try:
        draft_text = draft_svc.generate(msg)
        extras["draft_link"] = get_graph_client().create_reply_draft(
            msg["external_id"], draft_text
        )
    except Exception:
        logger.exception("Draft generation failed for message_id=%s", msg["id"])
    return extras
```

`handlers/actions/urgent.py` (full file — ntfy keeps firing synchronously; the Asana task is created async by the tasks service, so the push's click target becomes the **email** redirector link):

```python
import logging

import clients.ntfy as ntfy
from handlers.actions._shared import prepare
from models.message import Message
from models.types import Classification
from services import email_events
from services.links import redirector_url

logger = logging.getLogger(__name__)


def handle(classification: Classification, msg: Message) -> dict:
    invite = prepare(msg)
    points, links = email_events.invite_extras(str(msg["id"]), invite)

    ntfy.notify(
        message_id=str(msg["id"] or ""),
        subject=msg["subject"],
        sender=msg["sender"],
        reasoning=classification.reasoning,
        importance=classification.importance.value,
        # Task is created asynchronously by the tasks service — tapping the
        # push opens the email itself (stable redirector link) instead.
        click_url=redirector_url(str(msg.get("id") or "")) or msg.get("web_link"),
    )
    logger.info("ntfy notification sent for message_id=%s", msg["id"])
    return {"seed_key_points": points or None, "seed_links": links or None}
```

- [ ] **Step 6: Rename `task_url` → `click_url` in `clients/ntfy.py`**

In `notify()`: parameter `task_url: str | None = None` becomes `click_url: str | None = None`, and the tail becomes:

```python
    if click_url:
        payload["click"] = click_url
```

Then confirm no other caller:

```bash
grep -rn "task_url" --include="*.py" . | grep -v .venv
```

Expected: no hits.

- [ ] **Step 7: Run — expect PASS**

Run: `.venv/bin/pytest tests/test_email_events.py -v` → all PASS (Task 4's four + these five).

- [ ] **Step 8: Commit**

```bash
git add handlers/actions/dispatch.py handlers/actions/_shared.py \
        handlers/actions/review.py handlers/actions/respond.py \
        handlers/actions/urgent.py clients/ntfy.py tests/test_email_events.py
git commit -m "feat: dispatch publishes email_classified; handlers return event extras"
```

---

### Task 6: Publish `label_applied` from `services/labeling.py`

**Files:**
- Modify: `services/labeling.py`
- Test: `tests/test_email_events.py` (append)

**Interfaces:**
- Produces: a `label_applied` event `{event, message_id, task_gid: None, label, source}` on the same topic. The `inbox-label` CF runs as the same `process_cf` SA, so Task 2's publisher binding covers it; it already has `GCP_PROJECT_ID` in its env. `task_gid` is always `None` — inbox doesn't track task GIDs; tasks resolves via DB / `external:{message_id}`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_email_events.py`; `from services import labeling` goes into the top import block (ruff E402/I001):

```python
def test_apply_label_publishes_label_applied(monkeypatch):
    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def commit(self):
            pass

    monkeypatch.setattr(labeling, "get_conn", lambda: FakeConn())
    monkeypatch.setattr(labeling.classifications, "insert", lambda conn, **kw: None)
    monkeypatch.setattr(labeling, "set_current_label", lambda conn, mid, label: None)
    published = []
    monkeypatch.setattr(email_events, "publish", lambda e: published.append(e))

    labeling.apply_label("m1", "respond", "human_correction")

    assert published == [
        {
            "event": "label_applied",
            "message_id": "m1",
            "task_gid": None,
            "label": "respond",
            "source": "human_correction",
        }
    ]
```

- [ ] **Step 2: Run — expect FAIL** (`published == []`).

- [ ] **Step 3: Implement**

`services/labeling.py` — add to the imports (no circular-import risk; `email_events` only imports `clients.pubsub`, `models`, and `services.links`):

```python
from services import email_events
```

At the end of `apply_label(...)`, after the `otel.human_feedback.add(...)` line (the success path — everything above it either wrote or raised):

```python
    try:
        email_events.publish(
            {
                "event": "label_applied",
                "message_id": message_id,
                "task_gid": None,  # inbox doesn't track task GIDs; tasks resolves via DB / external:{message_id}
                "label": label,
                "source": source,
            }
        )
    except Exception:
        logger.exception("label_applied publish failed for message_id=%s", message_id)
```

Note: `scripts/bootstrap_labels.py` also calls `apply_label` — the try/except means a local run without Pub/Sub access degrades to a logged error, which is correct (tasks will pick labels up from the DB).

- [ ] **Step 4: Run — expect PASS**

Run: `.venv/bin/pytest tests/test_email_events.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/labeling.py tests/test_email_events.py
git commit -m "feat: publish label_applied events from labeling service"
```

---

### Task 7: Delete the migrated modules and fix stragglers

**Files:**
- Delete: `clients/asana.py`, `services/asana_tag_cache.py`, `services/email_summary.py`, `services/deadline.py`, `repo/asana_tags.py`, `.claude/skills/testing-inbox-pipeline/scripts/create-task-local.py`
- Modify: `handlers/pipeline.py`, `models/types.py`, `clients/claude.py`, `.claude/skills/testing-inbox-pipeline/SKILL.md`

- [ ] **Step 1: Delete**

```bash
git rm clients/asana.py services/asana_tag_cache.py repo/asana_tags.py \
       services/email_summary.py services/deadline.py \
       .claude/skills/testing-inbox-pipeline/scripts/create-task-local.py
```

(`create-task-local.py` only smoke-tested Asana task creation — that whole concern now lives in the tasks repo.)

- [ ] **Step 2: Find every straggler**

```bash
grep -rn "asana\|email_summary\|extract_deadline\|summarize\|tag_gids\|EmailSummary\|CreatedTask" \
     --include="*.py" . | grep -v ".venv"
```

Fix each (Steps 3–6 below are the known ones; anything else the grep surfaces gets the same treatment — delete the dead reference). Note `main.py`'s `extract(attrs)` is OTel trace-context extraction, NOT `claude.extract` — leave it.

- [ ] **Step 3: `handlers/pipeline.py`** — remove the import (line 19):

```python
from services import asana_tag_cache as tag_cache_svc
```

and the tag-GID block after classification (lines 116–119):

```python
                try:
                    classification.tag_gids = tag_cache_svc.resolve_gids(classification.tags)
                except Exception:
                    logger.exception("Tag GID resolution failed for message_id=%s", msg_id)
```

(Tag names travel in the event; the tasks service resolves GIDs.)

- [ ] **Step 4: `models/types.py`** — after Step 3 nothing reads `Classification.tag_gids`, and `EmailSummary`/`CreatedTask` lose all consumers. Remove the `tag_gids` field and both dataclasses. `Classification` becomes:

```python
@dataclass
class Classification:
    category: Category
    confidence: float
    alternatives: dict[str, float]
    tags: list[str]
    reasoning: str
    importance: Importance = Importance.P2
```

Drop `field` from the `dataclasses` import if nothing else in the file uses it.

- [ ] **Step 5: `clients/claude.py`** — delete `extract()` and `summarize()` (migrated to tasks); keep `classify()` and `draft()`.

- [ ] **Step 6: `.claude/skills/testing-inbox-pipeline/SKILL.md`** — remove the "Task creation only (`create-task-local.py`)" section and strip Asana/task-creation/summary claims from the frontmatter description and the pipeline description (the pipeline now ends at "publish `email_classified` event"; Asana verification lives in the tasks repo's skills).

- [ ] **Step 7: Verify clean**

```bash
grep -rn "clients.asana\|asana_tag_cache\|repo.asana_tags\|email_summary\|extract_deadline\|tag_gids\|EmailSummary\|CreatedTask" \
     --include="*.py" . | grep -v ".venv"
.venv/bin/pytest tests/ -q
```

Expected: no grep hits; full suite PASS.

- [ ] **Step 8: Commit**

```bash
git add -u
git add .claude/skills/testing-inbox-pipeline/
git commit -m "refactor: remove Asana client, tag cache, summary + deadline enrichment (migrated to tasks repo)"
```

(`git add -u` stages deletions + tracked modifications only — the untracked `scripts/get_google_calendar_token.py` stays untouched.)

---

### Task 8: Inbox terraform — Asana env cleanup (NO apply in this task)

**Files:**
- Modify: `terraform/cloud_functions.tf` (process function)
- Modify: `terraform/iam.tf`
- **Forbidden:** `terraform/secrets.tf` — do not open it for editing at all.

- [ ] **Step 1: Remove inbox's Asana access**

`terraform/cloud_functions.tf`, `process` function: delete the `ASANA_PROJECT_ID = var.asana_project_id` line from `environment_variables` (line 222) and the whole `ASANA_API_KEY` `secret_environment_variables` block (lines 282–287).

`terraform/iam.tf`: delete the `google_secret_manager_secret_iam_member.process_cf_asana` resource (the "Read the Asana API key" block).

Leave `variables.tf` / `terraform.tfvars`'s `asana_project_id` in place — the tasks repo's setup docs grep it as the reference value.

- [ ] **Step 2: 🚧 GATE A3 — secrets.tf untouched + validate only**

```bash
git diff main -- terraform/secrets.tf   # MUST print nothing
cd terraform && terraform init -backend=false && terraform validate && cd ..
```

Expected: empty diff; `Success! The configuration is valid.`
**Do NOT run `/terraform-plan` or `/terraform-apply` here** — this change deploys in Phase B only.

- [ ] **Step 3: Commit**

```bash
git add terraform/cloud_functions.tf terraform/iam.tf
git commit -m "chore(terraform): drop Asana env + secret access from the process CF"
```

---

### Task 9: Full local CI, then 🛑 HARD STOP (end of Phase A)

- [ ] **Step 1: Run the full local CI**

```bash
.venv/bin/pytest tests/ -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy clients/ services/ handlers/ models/ repo/ main.py
cd terraform && terraform init -backend=false && terraform validate && cd ..
```

Expected: all pass. Fix and re-run until green (fixes get amended into the responsible task's commit or a small `fix:` commit).

- [ ] **Step 2: Runtime verification (recommended)** — run the `/verifying-pr-locally` skill (it works without a PR; results are reported in-session). Focus on the services/handlers surface. Caveat: a full pipeline run publishes real events to the live `email-events` topic — harmless in Phase A (no subscription yet, events are dropped), but it means there's no consumer effect to assert on; the consumer-side check happens in Task 13.

- [ ] **Step 3: Confirm branch state**

```bash
git log --oneline main..tasks-service-extraction
git status --short   # only the untracked scripts/get_google_calendar_token.py may remain
```

- [ ] **Step 4: 🛑 GATE A4 — HARD STOP. Report and wait for Ben.**

Report: topic applied (Task 2), branch committed, CI green.
**Do NOT open the PR. Do NOT run another terraform apply. Do NOT deploy.**
The tasks repo's CFs (`tasks-events`, `tasks-webhook`) and their subscription must go live first (tasks plan Tasks 17–19) — every event inbox publishes before then is silently dropped.

Phase B starts only on Ben's explicit confirmation of the Task 10 preconditions.

---

### Task 10: 🚧 GATE B0 — Phase B preconditions (Ben confirms; verify anyway)

Ben will say the tasks side is deployed. Trust but verify:

- [ ] **Step 1: Verify both tasks CFs are ACTIVE**

```bash
gcloud functions describe tasks-events  --project=bens-project-462804 --region=us-central1 --format='value(state)'
gcloud functions describe tasks-webhook --project=bens-project-462804 --region=us-central1 --format='value(state)'
```

Expected: `ACTIVE` twice.

- [ ] **Step 2: Verify the email-events subscription exists**

```bash
gcloud pubsub topics list-subscriptions email-events --project=bens-project-462804
```

Expected: one subscription (the `tasks-events` CF's push subscription). Empty output → **STOP**: events would be dropped; tasks plan Task 18 isn't done.

- [ ] **Step 3: Confirm with Ben** that the tasks DB is migrated and the Asana webhook is registered (tasks plan Task 19). Any "no" → STOP.

---

### Task 11: Open the inbox PR

- [ ] **Step 1:** Rebase check — `git fetch origin && git log --oneline tasks-service-extraction..origin/main`; if main moved, rebase and re-run Task 9 Step 1.
- [ ] **Step 2:** Use the `/pr-open` skill (inbox CLAUDE.md requires it). In the description, cross-link the tasks PR (`bdrolet/tasks`, branch `tasks-service`) and note the two-repo deploy ordering.
- [ ] **Step 3: 🚧 GATE B1 — runtime verification** — run the `/verifying-pr-locally` skill: it exercises the branch against real Graph/DB and posts results to the open PR. PASS required before Task 12; on FAIL fix on the branch, push, re-verify. Never proceed past a FAIL or an ambiguous result.

---

### Task 12: Deploy — terraform plan + apply (this apply ships the new code)

- [ ] **Step 1: 🚧 GATE B2 — `/terraform-plan` review (expected output)**

- **Change (~1–2):** the `process` CF (source re-zip + `ASANA_PROJECT_ID`/`ASANA_API_KEY` env removal), possibly the source bucket object
- **Destroy (1):** `google_secret_manager_secret_iam_member.process_cf_asana` — expected and correct
- **Topic + publisher binding:** no changes (applied in Task 2)
- **🛑 If the plan wants to destroy the `asana-api-key` or `anthropic-api-key` secrets themselves, STOP** — `secrets.tf` was touched by mistake; fix the diff before anything else.

- [ ] **Step 2:** `/terraform-apply`.

---

### Task 13: Post-deploy verification, then merge

- [ ] **Step 1: Deploy checks** — use the `/deploy-inbox` skill's post-deploy verification (function version + log tail) or `/fetch-inbox-logs`. No import errors, no references to deleted modules.

- [ ] **Step 2: Live happy path** — send a test email through (`/testing-inbox-pipeline` run-pipeline-local, or `/tracing-inbox-email` to live-monitor a real message end-to-end) and confirm, per tasks plan Task 21:
  - inbox logs show `Published email_classified event for message_id=...`
  - `tasks-events` logs show the task created (or the policy skip for ignore/reference)
  - an urgent email's ntfy push opens the **email** (redirector link), and the Asana task appears moments later
  - optional: `/querying-grafana-metrics` — `inbox_*` pipeline metrics still emitting after the deploy

- [ ] **Step 3: Regression grep**

```bash
cd /Users/ben/src/inbox && grep -rn "asana_tag_cache\|clients.asana\|repo.asana_tags\|email_summary\|extract_deadline" --include="*.py" . | grep -v .venv
```

Expected: no hits.

- [ ] **Step 4: Merge** the inbox PR (and coordinate merging the tasks PR — its CI apply should be a no-op since infra is already applied). Then use `/monitoring-inbox-deploy` to watch the GitHub Actions deploy workflow triggered by the merge (it redeploys the same code — confirm it stays green). Clean up the local branch.

- [ ] **Step 5: Report** — PR links, verification evidence (log lines), anything discovered beyond the plan.

## Rollback (if tasks stop being created after the Phase B deploy)

If the tasks side can't be fixed quickly: `git revert` the inbox PR merge and `/terraform-apply` — the revert restores the direct Asana path (deleted files, `ASANA_*` env, and the IAM binding all come back with it). Pub/Sub retries (`RETRY_POLICY_RETRY`, at-least-once) plus the tasks side's `external.gid` dedupe make the overlap safe — drained events become no-op skips, not duplicates. Never roll back by destroying tasks infrastructure (topic/DB/secrets carry state). Full procedure: "Rollback procedures" in `/Users/ben/src/tasks/docs/superpowers/plans/2026-07-15-tasks-repo-setup.md`.
