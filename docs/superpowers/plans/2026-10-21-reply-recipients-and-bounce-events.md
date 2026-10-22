# Reply Recipients and Bounce Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let inbox-api replies name their own To and Cc, return the sent message id from a draft send, and publish an `email_bounced` event when a non-delivery report arrives.

**Architecture:** Two additive changes to the Graph client and the emails router (recipient overrides on `reply`, immutable ids on drafts so `send_draft` can return the id), plus a new `services/bounces.py` that recognises a non-delivery report and extracts the failed recipients, wired into `handlers/pipeline.py` between the duplicate check and embedding. The event rides the existing `email-events` topic; every consumer already ignores unknown event names.

**Tech Stack:** Python 3.13, FastAPI, `requests` against Microsoft Graph, pytest with the repo's monkeypatch and `sys.modules` stub conventions.

**Spec:** `~/src/consulting/docs/superpowers/specs/2026-10-21-funded-founder-campaign-design.md`, §10 (the people-side consumer is a separate plan in `~/src/people`).

## Global Constraints

- Layer rules in `CLAUDE.md`: `clients/` is I/O only, `services/` owns one concern, `handlers/` orchestrates, `models/` has no logic.
- Omitting the new `to`/`cc` fields keeps Graph's own recipient choice, so every existing caller of `POST /emails/{id}/reply` behaves exactly as today.
- `reply_all=true` together with an explicit `to` is a 400.
- Detection runs after the duplicate check so a repeated Graph notification never publishes a second `email_bounced`.
- An NDR still goes through embedding, classification and dispatch unchanged; the bounce step only adds a publish.
- Work on a feature branch and open the PR with `/pr-open` (repo rule).
- Run `ruff check . && ruff format --check . && pytest -q` before every commit.

## Review Focus

1. An NDR whose `message/delivery-status` attachment lists two failed recipients must publish one event with both addresses, not two events. Pinned in Task 4.
2. A Google Workspace bounce (`mailer-daemon@googlemail.com`, no DSN part, address only in the body) must still yield the failed recipient and `status: null`. Pinned in Task 4.
3. A reply with `to` set and `reply_all` true must be rejected before any Graph call. Pinned in Task 2.
4. A bounce whose conversation has no message in Sent Items (someone else's NDR forwarded to Ben) must publish with `original_graph_message_id: null` and must not raise. Pinned in Task 5.
5. An ordinary email from a sender whose display name contains "postmaster" but whose address does not must not be treated as an NDR. Pinned in Task 4.

---

### Task 1: Recipient overrides on `GraphEmailClient.reply`

**Files:**
- Modify: `clients/azure/graph_email_client.py:816-868`
- Test: `tests/test_graph_reply.py`

**Interfaces:**
- Produces: `GraphEmailClient.reply(self, message_id: str, *, comment: str, reply_all: bool = False, send: bool = True, from_address: str | None = None, from_shared: bool = False, to: list[str] | None = None, cc: list[str] | None = None) -> dict | None`. When `to` is given, `payload["message"]["toRecipients"]` is `[{"emailAddress": {"address": a}} for a in to]`; same for `cc` into `ccRecipients`. Both merge with the existing `from` block under `payload["message"]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_graph_reply.py`, using the existing `calls` fixture:

```python
def test_reply_with_to_sets_to_recipients(calls):
    _client().reply("m1", comment="hi", to=["cto@example.com"])
    _, payload = calls["post"][0]
    assert payload["message"]["toRecipients"] == [{"emailAddress": {"address": "cto@example.com"}}]
    assert "ccRecipients" not in payload["message"]

def test_reply_with_to_and_from_merges_message_block(calls):
    _client().reply("m1", comment="hi", from_address="ben@drolet.ai", to=["a@x.com"], cc=["b@x.com"])
    _, payload = calls["post"][0]
    assert payload["message"]["from"] == {"emailAddress": {"address": "ben@drolet.ai"}}
    assert payload["message"]["toRecipients"][0]["emailAddress"]["address"] == "a@x.com"
    assert payload["message"]["ccRecipients"][0]["emailAddress"]["address"] == "b@x.com"

def test_reply_without_to_leaves_payload_unchanged(calls):
    _client().reply("m1", comment="hi")
    assert calls["post"][0][1] == {"comment": "hi"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_graph_reply.py -v`
Expected: FAIL with `TypeError: reply() got an unexpected keyword argument 'to'`

- [ ] **Step 3: Add `to` and `cc` to `reply()`** per the Interfaces block; update the docstring to say recipients can be overridden and that a reply to a Sent Items message otherwise goes back to the mailbox owner.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_graph_reply.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clients/azure/graph_email_client.py tests/test_graph_reply.py
git commit -m "feat(graph): reply accepts explicit to/cc recipients"
```

### Task 2: `to` and `cc` on the reply endpoint

**Files:**
- Modify: `api/routers/emails.py:122-128` (`ReplyRequest`), `:310-327` (`reply`)
- Test: `tests/test_emails_router.py`

**Interfaces:**
- Consumes: Task 1's `reply(..., to=, cc=)`.
- Produces: `ReplyRequest` gains `to: list[str] | None = None` and `cc: list[str] | None = None`. `POST /emails/{id}/reply` passes them through; `reply_all` with `to` returns 400 `{"detail": "reply_all and to are mutually exclusive"}` without calling Graph.

- [ ] **Step 1: Write the failing tests** in `tests/test_emails_router.py`, following the file's pattern of monkeypatching `emails._get_client` with a fake whose `reply` records kwargs:

```python
def test_reply_passes_to_and_cc(monkeypatch):
    seen = {}
    class Fake:
        def reply(self, mid, **kw):
            seen.update(kw); return None
    monkeypatch.setattr(emails, "_get_client", lambda: Fake())
    resp = client.post("/emails/m1/reply", json={"body": "hi", "to": ["a@x.com"], "cc": ["b@x.com"]})
    assert resp.status_code == 200 and resp.json()["status"] == "sent"
    assert seen["to"] == ["a@x.com"] and seen["cc"] == ["b@x.com"]

def test_reply_omitting_to_sends_none(monkeypatch):
    ...  # assert seen["to"] is None and seen["cc"] is None

def test_reply_all_with_to_is_400(monkeypatch):
    called = []
    class Fake:
        def reply(self, *a, **kw): called.append(1)
    monkeypatch.setattr(emails, "_get_client", lambda: Fake())
    resp = client.post("/emails/m1/reply", json={"body": "hi", "reply_all": True, "to": ["a@x.com"]})
    assert resp.status_code == 400 and called == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_emails_router.py -k reply -v`
Expected: the first fails on `seen["to"]` (KeyError), the third gets 200 instead of 400.

- [ ] **Step 3: Implement** the two fields on `ReplyRequest` and the pass-through plus the 400 guard in `reply()`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_emails_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/routers/emails.py tests/test_emails_router.py
git commit -m "feat(api): reply endpoint takes explicit to and cc"
```

### Task 3: Draft send returns the message id

**Files:**
- Modify: `clients/azure/graph_email_client.py:645-742` (`create_draft`, `send_draft`), `api/routers/emails.py:143-144` (`StatusResponse`), `:292-299` (`send_draft` route)
- Test: `tests/test_graph_drafts.py`, `tests/test_emails_router.py`

**Interfaces:**
- Produces: `create_draft` sends `Prefer: IdType="ImmutableId"` (`self.get_headers(immutable=True)`) so the returned draft id survives the move to Sent Items. `send_draft(...) -> str` returns the id it was given. `StatusResponse` gains `id: str | None = None`; `POST /emails/drafts/{id}/send` responds `{"status": "sent", "id": "<draft id>"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_graph_drafts.py
def test_create_draft_requests_immutable_ids(monkeypatch):
    # capture headers on the POST; assert headers["Prefer"] == 'IdType="ImmutableId"'

def test_send_draft_returns_the_id(monkeypatch):
    # fake requests.post returning 202; assert _client().send_draft("d1") == "d1"

# tests/test_emails_router.py
def test_send_draft_returns_id(monkeypatch):
    # fake client.send_draft returning "d1"; assert resp.json() == {"status": "sent", "id": "d1"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_graph_drafts.py tests/test_emails_router.py -k "send_draft or immutable" -v`
Expected: FAIL (no Prefer header; `send_draft` returns None; response has no `id`).

- [ ] **Step 3: Implement** the immutable header on `create_draft`, the return value on `send_draft`, the optional `id` on `StatusResponse`, and the route returning it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_graph_drafts.py tests/test_emails_router.py -v`
Expected: PASS

- [ ] **Step 5: Verify against Graph by hand** (the spec asks for this): from `~/src/inbox` with `.env` loaded, create a draft to your own address through inbox-api, send it, then `GET /emails/{returned id}` and confirm it resolves to the message now in Sent Items. If it 404s, the immutable id did not survive: make `send_draft` return `None`, drop the `id` from the response, and note it in the PR; the campaign falls back to the `email_sent` event.

- [ ] **Step 6: Commit**

```bash
git add clients/azure/graph_email_client.py api/routers/emails.py tests/test_graph_drafts.py tests/test_emails_router.py
git commit -m "feat(api): draft send returns the sent message id"
```

### Task 4: Non-delivery report detection and extraction

**Files:**
- Create: `services/bounces.py`, `tests/test_bounces.py`, `tests/fixtures/ndr_exchange.json`, `tests/fixtures/ndr_gmail.json`, `tests/fixtures/ndr_plain.json`
- Modify: `services/email_events.py` (add `build_bounced_event`), `tests/test_email_events.py`

**Interfaces:**
- Produces, in `services/bounces.py`:
  - `NDR_SUBJECT_PREFIXES: tuple[str, ...] = ("Undeliverable:", "Delivery Status Notification (Failure)", "Mail delivery failed", "Delivery has failed")`
  - `is_ndr(sender: str, subject: str, attachments: list[dict]) -> bool`: true when the sender's local part is `postmaster` or `mailer-daemon` (case-insensitive, address only, never the display name), or any attachment has `contentType == "message/delivery-status"`, or the subject starts with a prefix.
  - `@dataclass(frozen=True) class Bounce: failed_recipients: list[str]; status: str | None; diagnostic: str | None; original_subject: str`
  - `extract(msg: Message, attachments: list[dict]) -> Bounce | None`: from a `message/delivery-status` attachment (base64 `contentBytes`, RFC 3464 per-recipient groups: `Final-Recipient`/`Original-Recipient` `rfc822;addr`, `Status`, `Diagnostic-Code`), else the first email address in `msg["body"]` that is not `msg["sender"]` and not in `msg["to"]`/`msg["cc"]`, lower-cased, with `status=None`. `original_subject` strips the first matching prefix and surrounding whitespace. Returns None when no recipient can be found.
- Produces, in `services/email_events.py`: `build_bounced_event(msg: Message, bounce: Bounce, original_graph_message_id: str | None) -> dict` with exactly the keys in spec §10.2: `event="email_bounced"`, `message_id`, `graph_message_id`, `conversation_id`, `received_at` (ISO-8601 "T" form like `build_event`), `failed_recipients`, `status`, `diagnostic`, `original_subject`, `original_graph_message_id`. `Message` gains `conversation_id: Optional[str]` in `models/message.py` and `normalize()` fills it from `email.conversation_id`.

- [ ] **Step 1: Write the three fixtures.** Each is a Graph message JSON with `from`, `subject`, `toRecipients`, `body`, `hasAttachments`, `conversationId`, plus a sibling `attachments` list. `ndr_exchange.json`: `postmaster@drolet.ai` ("Microsoft Outlook"), subject `Undeliverable: Production-readiness audit for Acme`, a `message/delivery-status` attachment whose decoded text has two per-recipient groups (`cto@acme.com` status `5.1.1`, `ceo@acme.com` status `5.1.1`). `ndr_gmail.json`: `mailer-daemon@googlemail.com`, subject `Delivery Status Notification (Failure)`, no attachments, body text naming `founder@startup.io`. `ndr_plain.json`: sender `bounce@relay.example`, subject `Mail delivery failed: returning message to sender`, body naming `vp@health.co`.

- [ ] **Step 2: Write the failing tests** in `tests/test_bounces.py`:

```python
def test_exchange_ndr_is_detected_and_lists_both_recipients():
    msg, atts = _load("ndr_exchange.json")
    assert bounces.is_ndr(msg["sender"], msg["subject"], atts)
    b = bounces.extract(msg, atts)
    assert b.failed_recipients == ["cto@acme.com", "ceo@acme.com"]
    assert b.status == "5.1.1"
    assert b.original_subject == "Production-readiness audit for Acme"

def test_gmail_bounce_without_dsn_uses_body_address():
    msg, atts = _load("ndr_gmail.json")
    b = bounces.extract(msg, atts)
    assert b.failed_recipients == ["founder@startup.io"] and b.status is None

def test_plain_bounce_detected_by_subject_prefix():
    msg, atts = _load("ndr_plain.json")
    assert bounces.is_ndr(msg["sender"], msg["subject"], atts)
    assert bounces.extract(msg, atts).failed_recipients == ["vp@health.co"]

def test_display_name_postmaster_is_not_an_ndr():
    assert not bounces.is_ndr("sue@acme.com", "Re: postmaster question", [])

def test_body_without_any_foreign_address_returns_none():
    msg = {...sender "postmaster@x", to ["ben@drolet.ai"], body "Your message could not be delivered."}
    assert bounces.extract(msg, []) is None
```

And in `tests/test_email_events.py`:

```python
def test_build_bounced_event_shape():
    ev = email_events.build_bounced_event(msg, Bounce(["a@x.com"], "5.1.1", "550 no such user", "Hello"), "orig1")
    assert ev == {"event": "email_bounced", "message_id": "u1", "graph_message_id": "g1",
                  "conversation_id": "c1", "received_at": "2026-10-21T12:00:00+00:00",
                  "failed_recipients": ["a@x.com"], "status": "5.1.1",
                  "diagnostic": "550 no such user", "original_subject": "Hello",
                  "original_graph_message_id": "orig1"}
```

- [ ] **Step 3: Run them to verify they fail**

Run: `pytest tests/test_bounces.py tests/test_email_events.py -v`
Expected: FAIL with `ModuleNotFoundError: services.bounces` and `AttributeError: build_bounced_event`.

- [ ] **Step 4: Implement** `services/bounces.py`, `build_bounced_event`, and the `conversation_id` field on `Message`/`normalize`. Parse the DSN with `email.parser` on the decoded bytes (a `multipart/report` or bare `message/delivery-status` body both work); fall back to a regex for `rfc822;\s*(\S+@\S+)` if the parser yields nothing.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_bounces.py tests/test_email_events.py tests/test_ingestion_fetch.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/bounces.py services/email_events.py models/message.py services/ingestion.py tests/test_bounces.py tests/fixtures tests/test_email_events.py
git commit -m "feat(bounces): detect non-delivery reports and build email_bounced events"
```

### Task 5: Publish `email_bounced` from the pipeline

**Files:**
- Create: `handlers/bounce.py`, `tests/test_bounce_handler.py`
- Modify: `handlers/pipeline.py:55-75` (after the duplicate check, before embedding), `clients/azure/graph_email_client.py` (new `sent_message_id_in_conversation`), `clients/otel.py` (counter `emails_bounced_published`)

**Interfaces:**
- Consumes: Task 4's `is_ndr`, `extract`, `build_bounced_event`.
- Produces:
  - `GraphEmailClient.sent_message_id_in_conversation(self, conversation_id: str) -> str | None`: the immutable id of the earliest Sent Items message with that `conversationId` (`GET /me/mailFolders/sentitems/messages?$filter=conversationId eq '…'&$select=id,sentDateTime&$top=25`, immutable headers), None on any failure or no match. Same shape as `latest_reply_from_me`.
  - `handlers.bounce.maybe_publish(msg: Message, email: Email, graph_client) -> bool`: if `is_ndr`, fetch attachments through `services.fetching.fetch_attachments(msg["external_id"])` only when `msg["has_attachments"]`, `extract`, resolve the original id via `sent_message_id_in_conversation(msg["conversation_id"])` when a conversation id exists, publish, increment `otel.emails_bounced_published`, return True. Returns False for a non-NDR. Never raises: any Graph or parse failure logs a warning and returns False.
  - `handlers/pipeline.py` calls `maybe_publish(msg, email, graph_client)` right after `msg["id"] = msg_id` and before `sender_ctx`.

- [ ] **Step 1: Write the failing tests** in `tests/test_bounce_handler.py`, with the same `sys.modules` stubs as `tests/test_sent_handler.py`:

```python
def test_ndr_publishes_one_event_with_original_id(monkeypatch, published):
    # graph fake: sent_message_id_in_conversation returns "orig1"; fetch_attachments returns the exchange DSN
    assert bounce.maybe_publish(msg_exchange, email, graph) is True
    assert len(published) == 1 and published[0]["original_graph_message_id"] == "orig1"

def test_ndr_without_sent_message_publishes_null_original(monkeypatch, published):
    # graph fake returns None
    assert published[0]["original_graph_message_id"] is None

def test_non_ndr_publishes_nothing(monkeypatch, published):
    assert bounce.maybe_publish(msg_ordinary, email, graph) is False and published == []

def test_graph_failure_during_resolution_still_publishes(monkeypatch, published):
    # graph fake raises; event published with original None, no exception
```

Plus a pipeline test in `tests/test_pipeline_repair.py`'s style asserting `pipeline.run` calls `bounce.maybe_publish` once for a fresh message and not for a duplicate.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_bounce_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: handlers.bounce`

- [ ] **Step 3: Implement** the client method, the counter, `handlers/bounce.py`, and the one-line pipeline call.

- [ ] **Step 4: Run the whole suite**

Run: `ruff check . && ruff format --check . && pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add handlers/bounce.py handlers/pipeline.py clients/azure/graph_email_client.py clients/otel.py tests/test_bounce_handler.py tests/test_pipeline_repair.py
git commit -m "feat(pipeline): publish email_bounced for non-delivery reports"
```

### Task 6: Consumers, docs and PR

**Files:**
- Modify: `CLAUDE.md` (Stack table "Domain events" row and the People ownership paragraph), `services/email_events.py` module docstring, `docs/inbox-architecture.md` if it lists events.
- Verify only: `~/src/tasks/main.py:72-77`, `~/src/schedule` event router.

- [ ] **Step 1: Confirm every subscriber ignores unknown events.** `~/src/tasks/main.py` logs and ignores (`case other`). Grep `~/src/schedule` for `email_classified` to find its router and confirm the same; if it raises on unknown names, add an ignore branch there in a separate PR before merging this one.

- [ ] **Step 2: Update the docs** named above: add `email_bounced` to the event list with one line on when it fires and its payload keys, and note the reply endpoint's `to`/`cc` and the draft send's `id`.

- [ ] **Step 3: Run the suite one more time and open the PR**

Run: `ruff check . && ruff format --check . && pytest -q`, then `/pr-open` with a description that lists the three API changes and the new event, and the hand-verification result from Task 3 Step 5.

- [ ] **Step 4: After merge**, the Functions deploy workflow and the API deploy workflow run on `main`. Confirm `inbox-api` is serving the new `ReplyRequest` by posting a reply with `to` to yourself, and confirm one `email_bounced` by sending a campaign-style email to a known-bad address at a domain you control and watching the people logs (`fetch-people-logs` skill) for "Ignoring event type 'email_bounced'" until the people plan's handler ships.
