# People Extraction — Inbox Side (Phase B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inbox stops owning people: delete the HubSpot client and pipeline call, delete the `senders` table and repo module, read sender context from `people-api` (fail-open), and publish a new `email_sent` event from a second Graph subscription on Sent Items.

**Architecture:** One PR. The pipeline swaps `senders.get()` for `clients/people_api.get_person()` behind a 2-second timeout that returns `None` on any failure, so `build_prompt` is untouched. The renew Cloud Function manages two Graph subscriptions (Inbox and Sent Items) with distinct `clientState` values and secrets; the webhook accepts both and stamps a `folder` Pub/Sub attribute; `main.py::process` routes `folder=sentitems` to a new `handlers/sent.py` that fetches the message and publishes `email_sent` to `email-events` — no store, no classify.

**Tech Stack:** Python 3.11/3.13, functions-framework, requests, google-cloud-pubsub, Terraform, pytest.

**Spec:** `docs/superpowers/specs/2026-09-03-people-service-extraction-design.md` (§10, §11 Phase B).

**Prerequisite:** the people plan (`~/src/people/docs/superpowers/plans/2026-09-03-people-service.md`) Tasks 1–17 are complete and Gate A passed: `people-api` is live, the `people-api-token` secret exists and `inbox-process-cf@…` has `secretAccessor` on it, and `hubspot-token` is in people's Terraform state.

## Global Constraints

- **Never destroy `hubspot-token`.** People's state owns it; inbox removes it from its own state with `terraform state rm` (Task 9) — the same procedure as `docs/superpowers/plans/2026-08-17-remove-google-calendar.md`.
- `build_prompt(msg, aggregates, top_examples, sender_ctx)` keeps its signature and its four-key contract (`message_count`, `my_response_count`, `relationship_label`, `notes`, or `None`) — `tests/test_classification.py` must stay green untouched.
- People lookup: `GET {PEOPLE_API_URL}/people/{email}`, bearer `PEOPLE_API_TOKEN`, **timeout 2 s**, `None` on 404 / exception / unset URL (spec §10.2).
- Webhook: existing `folder=inbox` behaviour byte-identical apart from the added attribute; unknown `clientState` still rejected; the processor treats a **missing** `folder` attribute as `inbox` (in-flight messages during deploy).
- `email_sent` payload exactly as spec §10.3: `event, graph_message_id, conversation_id, sent_at, from, to, cc, subject`. No body. Bcc excluded.
- Sent path: no DB write, no embedding, no classification, no tagging; `[LOCAL-TEST]` subjects skipped in GCP like the pipeline.
- Subscriptions: Inbox keeps `me/mailFolders/inbox/messages` / `inbox-webhook` / `graph-subscription-id`; Sent is `me/mailFolders/sentitems/messages` / `inbox-webhook-sent` / `graph-sent-subscription-id` (`lifecycle.ignore_changes` on the version like the first).
- The `senders` table is dropped in the live DB **only after Gate B** (Task 11).
- Repo workflow: feature branch `people-extraction`, never commit to `main`, PR via `/pr-open`, Terraform via `/terraform-plan` / `/terraform-apply` only. Stage files by explicit path.
- Local CI = `.venv/bin/pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy clients/ services/ handlers/ models/ repo/ main.py && (cd terraform && terraform validate)`.

## Skills used by this plan

| Skill | Where |
|---|---|
| `/terraform-plan`, `/terraform-apply` | Task 9 |
| `/pr-open` | Task 11 |
| `/verifying-pr-locally` | Task 11 |
| `/testing-inbox-pipeline`, `/tracing-inbox-email`, `/fetch-inbox-logs` | Task 11 Gate B |
| `/monitoring-inbox-deploy` | Task 11 after merge |
| `/querying-inbox-db` | Task 11 (drop `senders`) |
| `/adding-observability` | Tasks 3, 5 (metric names) |

## File structure

```
clients/people_api.py           NEW  get_person(email) -> dict | None, fail-open
clients/hubspot.py              DELETE
clients/azure/email.py          + conversation_id
clients/azure/graph_email_client.py  $select += conversationId
clients/graph_subscriptions.py  register(client, url, *, resource, client_state)
clients/otel.py                 + people_lookup, people_lookup_duration, emails_sent_published
repo/senders.py                 DELETE
repo/schema.sql                 - senders DDL
services/email_events.py        + email_sent_payload(email) -> dict
handlers/pipeline.py            - hubspot block, - senders; + people_api lookup
handlers/sent.py                NEW  run(notification, context) -> None
main.py                         process: route on attrs["folder"]
functions/webhook/main.py       two client states, folder attribute
functions/renew/main.py         list of subscriptions
scripts/import_contacts.py      DELETE
scripts/bootstrap_labels.py     - senders
scripts/backfill_embeddings.py  - senders
.claude/skills/importing-hubspot-contacts/  DELETE
terraform/{secrets,variables,iam,cloud_functions}.tf, .github/workflows/deploy.yml
CLAUDE.md, docs/inbox-architecture.md
~/.claude/skills/adding-referral-contact/SKILL.md  (repointed by the people plan Task 16 — verify only)
tests/test_people_api.py, tests/test_sent_handler.py, tests/test_webhook_folder.py, tests/test_renew.py (+cases), tests/test_email_events.py (+case)
```

---

### Task 1: Branch

- [ ] **Step 1**

```bash
cd ~/src/inbox && git checkout main && git pull && git checkout -b people-extraction
git status --short
```

Expected: clean tree, on `people-extraction`.

---

### Task 2: Remove HubSpot code

**Files:**
- Delete: `clients/hubspot.py`, `scripts/import_contacts.py`, `.claude/skills/importing-hubspot-contacts/SKILL.md`
- Modify: `handlers/pipeline.py:11` (import) and `handlers/pipeline.py:144-156` (call block), `requirements.txt:29-30`

- [ ] **Step 1: Delete files**

```bash
git rm clients/hubspot.py scripts/import_contacts.py .claude/skills/importing-hubspot-contacts/SKILL.md
```

- [ ] **Step 2: Edit `handlers/pipeline.py`**

Remove line 11 `import clients.hubspot as hubspot`. Remove the whole block:

```python
try:
    contact_id = hubspot.upsert_contact(msg["sender"], msg["sender_display"])
    if contact_id:
        hubspot.log_email(
            contact_id,
            msg["subject"],
            msg["sender"],
            msg["body"],
            msg["received_at"],
            body_html=msg.get("body_html"),
        )
except Exception:
    logger.warning("HubSpot logging failed", exc_info=True)
```

- [ ] **Step 3: Edit `requirements.txt`** — delete the two lines `# HubSpot CRM` and `hubspot-api-client>=10.0`.

- [ ] **Step 4: Verify**

```bash
grep -rn -i hubspot --include="*.py" . | grep -v .venv    # expect: no output
.venv/bin/pytest tests/ -q && .venv/bin/ruff check .
```

Expected: no matches; tests pass.

- [ ] **Step 5: Commit**

```bash
git add -u handlers/pipeline.py requirements.txt
git commit -m "refactor: remove HubSpot client, pipeline call, and import script — people owns contacts"
```

---

### Task 3: `clients/people_api.py` (fail-open lookup)

**Files:**
- Create: `clients/people_api.py`
- Modify: `clients/otel.py` (globals block near line 24-33, and `setup_telemetry` after `events_published`)
- Test: `tests/test_people_api.py`

**Interfaces:**
- Produces: `people_api.get_person(email: str) -> dict | None`; metrics `otel.people_lookup` (counter, label `outcome=hit|miss|error|disabled`), `otel.people_lookup_duration` (histogram ms).

- [ ] **Step 1: Failing tests**

```python
# tests/test_people_api.py
import pytest
import requests

from clients import people_api


class Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._p = payload or {}

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PEOPLE_API_URL", "https://people.example")
    monkeypatch.setenv("PEOPLE_API_TOKEN", "t0k")


def test_hit_returns_the_four_keys(monkeypatch, env):
    seen = {}

    def fake_get(url, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return Resp(
            200,
            {
                "email": "a@x.com",
                "message_count": 3,
                "my_response_count": 1,
                "relationship_label": "family",
                "notes": None,
                "eligible": True,
            },
        )

    monkeypatch.setattr(people_api.requests, "get", fake_get)
    out = people_api.get_person("A@X.com")
    assert out == {
        "message_count": 3,
        "my_response_count": 1,
        "relationship_label": "family",
        "notes": None,
    }
    assert seen["url"] == "https://people.example/people/a@x.com"
    assert seen["headers"] == {"Authorization": "Bearer t0k"} and seen["timeout"] == 2


def test_404_is_none(monkeypatch, env):
    monkeypatch.setattr(people_api.requests, "get", lambda *a, **k: Resp(404))
    assert people_api.get_person("a@x.com") is None


def test_timeout_is_none(monkeypatch, env):
    def boom(*a, **k):
        raise requests.Timeout()

    monkeypatch.setattr(people_api.requests, "get", boom)
    assert people_api.get_person("a@x.com") is None


def test_500_is_none(monkeypatch, env):
    monkeypatch.setattr(people_api.requests, "get", lambda *a, **k: Resp(500))
    assert people_api.get_person("a@x.com") is None


def test_unset_url_is_none_without_calling(monkeypatch):
    monkeypatch.delenv("PEOPLE_API_URL", raising=False)

    def fail(*a, **k):
        raise AssertionError("must not call")

    monkeypatch.setattr(people_api.requests, "get", fail)
    assert people_api.get_person("a@x.com") is None
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_people_api.py -q` — Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# clients/people_api.py
"""Client for the people-api Cloud Run service (github.com/bdrolet/people).
Sender context for the classification prompt: message_count,
my_response_count, relationship_label, notes. Fail-open — any problem
returns None and the pipeline classifies without sender context. Hot path,
so a hard 2 s timeout."""

import logging
import os
import time

import requests

import clients.otel as otel

logger = logging.getLogger(__name__)

TIMEOUT_S = 2
_KEYS = ("message_count", "my_response_count", "relationship_label", "notes")


def get_person(email: str) -> dict | None:
    base = os.environ.get("PEOPLE_API_URL", "").rstrip("/")
    if not base:
        otel.people_lookup.add(1, {"outcome": "disabled"})
        return None
    addr = (email or "").strip().lower()
    t0 = time.monotonic()
    outcome = "error"
    try:
        resp = requests.get(
            f"{base}/people/{addr}",
            headers={"Authorization": f"Bearer {os.environ.get('PEOPLE_API_TOKEN', '')}"},
            timeout=TIMEOUT_S,
        )
        if resp.status_code == 404:
            outcome = "miss"
            return None
        resp.raise_for_status()
        data = resp.json()
        outcome = "hit"
        return {k: data.get(k) for k in _KEYS}
    except Exception:
        logger.warning("people-api lookup failed for %s", addr, exc_info=True)
        return None
    finally:
        otel.people_lookup.add(1, {"outcome": outcome})
        otel.people_lookup_duration.record((time.monotonic() - t0) * 1000)
```

`clients/otel.py` — add to the globals block:

```python
people_lookup: metrics.Counter = metrics.NoOpMeter("noop").create_counter("noop")
people_lookup_duration: metrics.Histogram = metrics.NoOpMeter("noop").create_histogram("noop")
emails_sent_published: metrics.Counter = metrics.NoOpMeter("noop").create_counter("noop")
```

and in `setup_telemetry` (extend the existing `global` statement with the three names, then after `events_published = ...`):

```python
    people_lookup = meter.create_counter(
        "inbox.people.lookup", description="people-api sender-context lookups by outcome"
    )
    people_lookup_duration = meter.create_histogram(
        "inbox.people.lookup.duration", unit="ms", description="people-api lookup latency"
    )
    emails_sent_published = meter.create_counter(
        "inbox.emails.sent.published", description="email_sent events published"
    )
```

- [ ] **Step 4: Run tests** — pass.

- [ ] **Step 5: Commit**

```bash
git add clients/people_api.py clients/otel.py tests/test_people_api.py
git commit -m "feat: fail-open people-api client for sender context"
```

---

### Task 4: Replace `senders` with the people-api lookup

**Files:**
- Delete: `repo/senders.py`
- Modify: `handlers/pipeline.py` (import line 18; lines 75-76), `scripts/bootstrap_labels.py:86` (+ its import), `scripts/backfill_embeddings.py:115` (+ its import), `repo/schema.sql:50-59`
- Test: `tests/test_pipeline_people_ctx.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_pipeline_people_ctx.py
"""The pipeline asks people-api for sender context and tolerates None."""

import importlib

import pytest


def test_pipeline_imports_people_api_not_senders():
    src = open("handlers/pipeline.py").read()
    assert "people_api" in src and "senders" not in src


def test_repo_has_no_senders_module():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("repo.senders")
```

- [ ] **Step 2: Run to verify failure** — Expected: first assert fails (`senders` present); second fails (module exists).

- [ ] **Step 3: Edit `handlers/pipeline.py`**

Line 18: `from repo import classifications, messages, senders` → `from repo import classifications, messages`. Add `from clients import people_api` to the imports. Replace

```python
                senders.upsert(conn, msg["sender"], msg["source"])
                sender_ctx = senders.get(conn, msg["sender"], msg["source"])
```

with

```python
                sender_ctx = people_api.get_person(msg["sender"])  # None → no sender context
```

- [ ] **Step 4: Scripts and schema**

`scripts/bootstrap_labels.py`: remove `senders` from its `from repo import …` line; replace `sender_ctx = senders.get(conn, msg["sender"], "email")` with `sender_ctx = people_api.get_person(msg["sender"])` and add `from clients import people_api`.
`scripts/backfill_embeddings.py`: remove `senders` from the import and delete the line `senders.upsert(conn, msg["sender"], msg["source"])`.
`repo/schema.sql`: delete the `CREATE TABLE IF NOT EXISTS senders (...)` block (lines 50-59).

```bash
git rm repo/senders.py
```

- [ ] **Step 5: Run tests + lint** — `.venv/bin/pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/mypy clients/ services/ handlers/ models/ repo/ main.py` — Expected: pass (`tests/test_classification.py` untouched and green).

- [ ] **Step 6: Commit**

```bash
git add -u handlers/pipeline.py scripts/bootstrap_labels.py scripts/backfill_embeddings.py repo/schema.sql
git add tests/test_pipeline_people_ctx.py
git commit -m "refactor: sender context from people-api; drop senders table and repo"
```

---

### Task 5: `email_sent` payload and `handlers/sent.py`

**Files:**
- Modify: `clients/azure/email.py` (add `conversation_id`), `clients/azure/graph_email_client.py:436` (`$select`), `services/email_events.py`
- Create: `handlers/sent.py`
- Test: `tests/test_email_events.py` (append), `tests/test_sent_handler.py`

**Interfaces:**
- Produces: `Email.conversation_id: str | None`; `email_events.email_sent_payload(email) -> dict`; `handlers.sent.run(notification: dict, context=None) -> None`.

- [ ] **Step 1: Failing tests**

Append to `tests/test_email_events.py`:

```python
def test_email_sent_payload_shape():
    class E:
        id = "AAMk-immutable"
        conversation_id = "AAQk-conv"
        sent_datetime = datetime(2026, 9, 3, 14, 5, tzinfo=UTC)
        from_email = "ben@drolet.cloud"
        to_recipients = [{"address": "Alice@Example.com", "name": "Alice"}, {"name": "no address"}]
        cc_recipients = [{"address": "carol@example.com"}]
        bcc_recipients = [{"address": "hidden@example.com"}]
        subject = "Re: hello"

    p = email_events.email_sent_payload(E())
    assert p == {
        "event": "email_sent",
        "graph_message_id": "AAMk-immutable",
        "conversation_id": "AAQk-conv",
        "sent_at": "2026-09-03T14:05:00+00:00",
        "from": "ben@drolet.cloud",
        "to": ["alice@example.com"],
        "cc": ["carol@example.com"],
        "subject": "Re: hello",
    }
    assert "body" not in p and "hidden@example.com" not in str(p)
```

```python
# tests/test_sent_handler.py
import sys
import types
from datetime import UTC, datetime

import pytest

# Same libpq-free stubs as tests/test_email_events.py (session-persistent).
_fake_db = types.ModuleType("clients.db")
_fake_db.get_conn = lambda: None
sys.modules.setdefault("clients.db", _fake_db)

import clients.otel as otel  # noqa: E402
import handlers.sent as sent  # noqa: E402
from services import email_events  # noqa: E402


class E:
    def __init__(self, subject="Re: hi"):
        self.id = "g1"
        self.conversation_id = None
        self.sent_datetime = datetime(2026, 9, 3, tzinfo=UTC)
        self.from_email = "ben@drolet.cloud"
        self.to_recipients = [{"address": "a@x.com"}]
        self.cc_recipients = []
        self.bcc_recipients = []
        self.subject = subject


@pytest.fixture
def wired(monkeypatch):
    published = []
    monkeypatch.setattr(sent, "get_graph_client", lambda: object())
    monkeypatch.setattr(email_events, "publish", lambda ev: published.append(ev))
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    return published


def test_publishes_email_sent(monkeypatch, wired):
    monkeypatch.setattr(sent, "fetch", lambda mid, client: E())
    sent.run({"resourceData": {"id": "g1"}})
    assert len(wired) == 1 and wired[0]["event"] == "email_sent" and wired[0]["to"] == ["a@x.com"]


def test_missing_id_is_skipped(monkeypatch, wired):
    sent.run({"resourceData": {}})
    assert wired == []


def test_fetch_none_is_skipped(monkeypatch, wired):
    monkeypatch.setattr(sent, "fetch", lambda mid, client: None)
    sent.run({"resourceData": {"id": "g1"}})
    assert wired == []


def test_local_test_subject_skipped_in_gcp(monkeypatch, wired):
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setattr(sent, "fetch", lambda mid, client: E("[LOCAL-TEST] hi"))
    sent.run({"resourceData": {"id": "g1"}})
    assert wired == []
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_email_events.py tests/test_sent_handler.py -q` — Expected: `AttributeError: email_sent_payload`, `ModuleNotFoundError: handlers.sent`.

- [ ] **Step 3: Implement**

`clients/azure/email.py` — after `self.odata_type = ...` add:

```python
        self.conversation_id = data.get("conversationId")
```

`clients/azure/graph_email_client.py:436` — append `,conversationId` to the `$select` string.

`services/email_events.py` — append:

```python
def email_sent_payload(email) -> dict:
    """email_sent: recipients + timestamp only (spec §10.3). No body, no inbox
    UUID (sent mail is never stored). Bcc deliberately excluded. Consumed by
    the people repo (github.com/bdrolet/people) for my_response_count."""
    sent = getattr(email, "sent_datetime", None)
    if isinstance(sent, datetime):
        sent_at = sent.isoformat()
    else:
        sent_at = datetime.now(timezone.utc).isoformat()

    def _addrs(recipients) -> list[str]:
        return [r["address"].strip().lower() for r in (recipients or []) if r.get("address")]

    return {
        "event": "email_sent",
        "graph_message_id": email.id or "",
        "conversation_id": getattr(email, "conversation_id", None),
        "sent_at": sent_at,
        "from": (email.from_email or "").lower(),
        "to": _addrs(email.to_recipients),
        "cc": _addrs(email.cc_recipients),
        "subject": email.subject or "",
    }
```

(Add `timezone` to the existing `from datetime import datetime` import.)

```python
# handlers/sent.py
"""Sent Items notification → email_sent event. Nothing is stored, embedded,
classified or tagged (spec §10.3). Duplicates from Graph are acceptable — the
consumer's counters tolerate them."""

import logging
import os
import time

import clients.otel as otel
from clients.graph import get_graph_client
from services import email_events
from services.ingestion import fetch

logger = logging.getLogger(__name__)


def run(notification: dict, context=None) -> None:
    message_id = notification.get("resourceData", {}).get("id")
    if not message_id:
        logger.warning("Sent notification missing resourceData.id — skipping")
        return
    tracer = otel.get_tracer()
    t0 = time.monotonic()
    with tracer.start_as_current_span("inbox.sent", context=context) as span:
        span.set_attribute("message_id", message_id)
        email = fetch(message_id, get_graph_client())
        if email is None:
            logger.warning("Could not fetch sent message %s — skipping", message_id)
            return
        if os.environ.get("GCP_PROJECT_ID") and "[LOCAL-TEST]" in (email.subject or ""):
            logger.info("Skipping local-test sent message %s in GCP", message_id)
            return
        payload = email_events.email_sent_payload(email)
        email_events.publish(payload)
        otel.emails_sent_published.add(1)
        otel.stage_duration.record((time.monotonic() - t0) * 1000, {"stage": "sent"})
        logger.info(
            "Published email_sent %s → %d recipients",
            message_id,
            len(payload["to"]) + len(payload["cc"]),
        )
```

- [ ] **Step 4: Run tests** — pass.

- [ ] **Step 5: Commit**

```bash
git add clients/azure/email.py clients/azure/graph_email_client.py services/email_events.py handlers/sent.py tests/test_email_events.py tests/test_sent_handler.py
git commit -m "feat: email_sent event from Sent Items notifications"
```

---

### Task 6: `main.py` routes on the `folder` attribute

**Files:**
- Modify: `main.py:62-77`
- Test: `tests/test_main_folder.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_main_folder.py
import base64
import json
import sys
import types

import pytest

_fake_db = types.ModuleType("clients.db")
_fake_db.get_conn = lambda: None
sys.modules.setdefault("clients.db", _fake_db)

import main  # noqa: E402


class CE:
    def __init__(self, attrs):
        self.data = {
            "message": {
                "data": base64.b64encode(
                    json.dumps({"resourceData": {"id": "x"}}).encode()
                ).decode(),
                "attributes": attrs,
            }
        }


@pytest.fixture
def routes(monkeypatch):
    log = []
    monkeypatch.setattr(main, "_run_pipeline", lambda n, m, context=None: log.append("pipeline"))
    monkeypatch.setattr(main, "_run_sent", lambda n, context=None: log.append("sent"))
    monkeypatch.setattr(main, "_get_model", lambda: None)
    return log


def test_default_is_pipeline(routes):
    main.process(CE({}))
    assert routes == ["pipeline"]


def test_inbox_is_pipeline(routes):
    main.process(CE({"folder": "inbox"}))
    assert routes == ["pipeline"]


def test_sentitems_is_sent(routes):
    main.process(CE({"folder": "sentitems"}))
    assert routes == ["sent"]
```

- [ ] **Step 2: Run to verify failure** — Expected: `AttributeError: main has no attribute _run_pipeline`.

- [ ] **Step 3: Implement** — replace `process` in `main.py`:

```python
def _run_pipeline(notification, model, context=None):
    from handlers.pipeline import run

    run(notification, model, context=context)


def _run_sent(notification, context=None):
    from handlers.sent import run

    run(notification, context=context)


@functions_framework.cloud_event
def process(cloud_event: CloudEvent) -> None:
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode()
    notification = json.loads(data)
    attrs = cloud_event.data["message"].get("attributes", {})
    ctx = extract(attrs)
    folder = attrs.get("folder", "inbox")  # missing → inbox: in-flight messages during deploy
    otel.flush()
    try:
        if folder == "sentitems":
            _run_sent(notification, context=ctx)
        else:
            _run_pipeline(notification, _get_model(), context=ctx)
    finally:
        otel.flush()
```

(Keep the existing baseline-flush comment above `otel.flush()`.)

- [ ] **Step 4: Run tests** — `.venv/bin/pytest tests/ -q` — pass.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_folder.py
git commit -m "feat: processor routes sentitems notifications to the sent handler"
```

---

### Task 7: Webhook accepts two client states and stamps `folder`

**Files:**
- Modify: `functions/webhook/main.py` (docstring; lines 143-158)
- Test: `tests/test_webhook_folder.py`

- [ ] **Step 1: Failing test** (loads the standalone CF module the way `tests/test_renew.py` does)

```python
# tests/test_webhook_folder.py
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _ensure(name, **attrs):
    try:
        importlib.import_module(name)
    except ImportError:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


_ensure("functions_framework", http=lambda fn: fn)
try:
    import google.cloud.pubsub_v1  # noqa: F401
except ImportError:
    g = sys.modules.setdefault("google", types.ModuleType("google"))
    c = sys.modules.setdefault("google.cloud", types.ModuleType("google.cloud"))
    g.cloud = c
    pv1 = types.ModuleType("google.cloud.pubsub_v1")
    pv1.PublisherClient = object
    c.pubsub_v1 = pv1
    sys.modules["google.cloud.pubsub_v1"] = pv1

_PATH = Path(__file__).resolve().parents[1] / "functions" / "webhook" / "main.py"
_spec = importlib.util.spec_from_file_location("webhook_main", _PATH)
webhook_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(webhook_main)


class Fut:
    def result(self, timeout=None):
        return "id"


class Pub:
    def __init__(self):
        self.calls = []

    def publish(self, topic, data, **attrs):
        self.calls.append((topic, attrs))
        return Fut()


class Req:
    path = "/"
    method = "POST"
    args = {}
    headers = {}

    def __init__(self, body):
        self._b = body

    def get_json(self, silent=True):
        return self._b


@pytest.fixture
def pub(monkeypatch):
    p = Pub()
    monkeypatch.setattr(webhook_main, "_publisher_client", lambda: (p, "t-messages", "t-labels"))
    monkeypatch.setattr(webhook_main, "_flush", lambda: None)
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE", "inbox-webhook")
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE_SENT", "inbox-webhook-sent")
    return p


def _n(state):
    return {"value": [{"changeType": "created", "clientState": state, "resourceData": {"id": "x"}}]}


def test_inbox_state_gets_folder_inbox(pub):
    webhook_main.webhook(Req(_n("inbox-webhook")))
    assert pub.calls[0][1]["folder"] == "inbox"


def test_sent_state_gets_folder_sentitems(pub):
    webhook_main.webhook(Req(_n("inbox-webhook-sent")))
    assert pub.calls[0][1]["folder"] == "sentitems"


def test_unknown_state_rejected(pub):
    webhook_main.webhook(Req(_n("nope")))
    assert pub.calls == []
```

- [ ] **Step 2: Run to verify failure** — Expected: `KeyError: 'folder'` on the first test.

- [ ] **Step 3: Implement** — in `functions/webhook/main.py` replace

```python
        client_state = os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook")
```

with

```python
        # Two Graph subscriptions share this endpoint (Inbox and Sent Items);
        # clientState is the routing key. The folder rides along as a Pub/Sub
        # attribute so the processor can branch without re-reading Graph.
        folder_by_state = {
            os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook"): "inbox",
            os.environ.get("WEBHOOK_CLIENT_STATE_SENT", "inbox-webhook-sent"): "sentitems",
        }
```

and replace

```python
if notification.get("clientState") != client_state:
    logger.warning("Unexpected clientState: %s", notification.get("clientState"))
    continue

carrier = {}
inject(carrier)
futures.append(publisher.publish(messages_topic, json.dumps(notification).encode(), **carrier))
```

with

```python
folder = folder_by_state.get(notification.get("clientState", ""))
if folder is None:
    logger.warning("Unexpected clientState: %s", notification.get("clientState"))
    continue

carrier = {"folder": folder}
inject(carrier)
futures.append(publisher.publish(messages_topic, json.dumps(notification).encode(), **carrier))
```

Update the module docstring's `POST /` line to: `— change notification (Inbox or Sent Items subscription, by clientState); publishes each created message to Pub/Sub with a folder attribute`.

- [ ] **Step 4: Run tests** — pass.

- [ ] **Step 5: Commit**

```bash
git add functions/webhook/main.py tests/test_webhook_folder.py
git commit -m "feat: webhook accepts the Sent Items subscription and stamps a folder attribute"
```

---

### Task 8: Renew CF manages both subscriptions; `graph_subscriptions.register` takes resource + client state

**Files:**
- Modify: `functions/renew/main.py` (docstring; `_load_subscription_id`, `_save_subscription_id`, `_create_subscription`, `_register_subscription`, `_renew_or_register`, `renew`), `clients/graph_subscriptions.py::register`
- Test: `tests/test_renew.py` (adjust + add), `tests/test_graph_subscriptions_register.py`

**Interfaces:**
- Produces: in renew: `SUBSCRIPTIONS: list[dict]` built from env (`resource`, `client_state`, `secret_name`); every helper takes a `sub: dict`. `register(client, notification_url, *, resource="me/mailFolders/inbox/messages", client_state=None) -> dict`.

- [ ] **Step 1: Update the existing renew tests to the new signatures and add two**

In `tests/test_renew.py`, define at the top (after module load):

```python
INBOX = {
    "resource": "me/mailFolders/inbox/messages",
    "client_state": "inbox-webhook",
    "secret_name": "graph-subscription-id",
}
SENT = {
    "resource": "me/mailFolders/sentitems/messages",
    "client_state": "inbox-webhook-sent",
    "secret_name": "graph-sent-subscription-id",
}
```

Then: every `_patch_subscription` lambda keeps `(sid, tok)`; `_register_subscription` lambdas become `lambda sub, tok: ...`; `_save_subscription_id` lambdas become `lambda sub, sid: ...`; every `_renew_or_register("...", "tok")` call becomes `_renew_or_register(INBOX, "...", "tok")`; `_register_subscription("tok")` becomes `_register_subscription(INBOX, "tok")`; `_create_subscription` monkeypatch becomes `lambda sub, tok: ...`. Add:

```python
def test_register_matches_on_resource_not_just_url(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://webhook.example.com")
    inbox_sub = {
        "id": "sub-inbox",
        "notificationUrl": "https://webhook.example.com",
        "resource": INBOX["resource"],
    }
    monkeypatch.setattr(renew_main, "_list_subscriptions", lambda tok: [inbox_sub])
    monkeypatch.setattr(
        renew_main, "_create_subscription", lambda sub, tok: {"id": "sub-created", **sub}
    )
    assert renew_main._register_subscription(SENT, "tok")["id"] == "sub-created"


def test_subscriptions_from_env(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_SECRET_NAME", "graph-subscription-id")
    monkeypatch.setenv("SENT_SUBSCRIPTION_SECRET_NAME", "graph-sent-subscription-id")
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE", "inbox-webhook")
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE_SENT", "inbox-webhook-sent")
    subs = renew_main._subscriptions()
    assert [s["resource"] for s in subs] == [INBOX["resource"], SENT["resource"]]
    assert (
        subs[1]["client_state"] == "inbox-webhook-sent"
        and subs[1]["secret_name"] == "graph-sent-subscription-id"
    )


def test_renew_handles_each_subscription(monkeypatch):
    seen = []
    monkeypatch.setattr(renew_main, "_get_access_token", lambda: "tok")
    monkeypatch.setattr(renew_main, "_subscriptions", lambda: [INBOX, SENT])
    monkeypatch.setattr(
        renew_main, "_load_subscription_id", lambda sub: "id-" + sub["client_state"]
    )
    monkeypatch.setattr(
        renew_main,
        "_renew_or_register",
        lambda sub, sid, tok: seen.append((sub["resource"], sid)) or {"id": sid},
    )
    body, status, _ = renew_main.renew(None)
    assert status == 200 and len(seen) == 2
```

```python
# tests/test_graph_subscriptions_register.py
import clients.graph_subscriptions as gs


class C:
    def get_headers(self, immutable=False):
        return {"h": "1"}


def test_register_default_is_inbox(monkeypatch):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "s"}

    monkeypatch.setattr(gs.requests, "post", lambda url, json, headers: seen.update(json) or R())
    monkeypatch.setenv("WEBHOOK_CLIENT_STATE", "inbox-webhook")
    gs.register(C(), "https://w")
    assert (
        seen["resource"] == "me/mailFolders/inbox/messages"
        and seen["clientState"] == "inbox-webhook"
    )


def test_register_sent(monkeypatch):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "s"}

    monkeypatch.setattr(gs.requests, "post", lambda url, json, headers: seen.update(json) or R())
    gs.register(
        C(),
        "https://w",
        resource="me/mailFolders/sentitems/messages",
        client_state="inbox-webhook-sent",
    )
    assert (
        seen["resource"] == "me/mailFolders/sentitems/messages"
        and seen["clientState"] == "inbox-webhook-sent"
    )
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_renew.py tests/test_graph_subscriptions_register.py -q` — Expected: `TypeError`s / `AttributeError: _subscriptions`.

- [ ] **Step 3: Implement `functions/renew/main.py`**

Replace the four secret/registration helpers and the entry point with:

```python
def _subscriptions() -> list[dict]:
    """The Graph subscriptions this function keeps alive. Order matters only
    for logging. Env names match the webhook CF's."""
    return [
        {
            "resource": "me/mailFolders/inbox/messages",
            "client_state": os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook"),
            "secret_name": os.environ.get("SUBSCRIPTION_SECRET_NAME", "graph-subscription-id"),
        },
        {
            "resource": "me/mailFolders/sentitems/messages",
            "client_state": os.environ.get("WEBHOOK_CLIENT_STATE_SENT", "inbox-webhook-sent"),
            "secret_name": os.environ.get(
                "SENT_SUBSCRIPTION_SECRET_NAME", "graph-sent-subscription-id"
            ),
        },
    ]


def _load_subscription_id(sub: dict) -> str:
    project_id = os.environ["GCP_PROJECT_ID"]
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{sub['secret_name']}/versions/latest"
    try:
        return client.access_secret_version(request={"name": name}).payload.data.decode().strip()
    except gcp_exceptions.NotFound:
        return ""


def _save_subscription_id(sub: dict, subscription_id: str) -> None:
    project_id = os.environ["GCP_PROJECT_ID"]
    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project_id}/secrets/{sub['secret_name']}"
    client.add_secret_version(
        request={"parent": parent, "payload": {"data": subscription_id.encode()}}
    )


def _create_subscription(sub: dict, token: str) -> dict:
    resp = requests.post(
        "https://graph.microsoft.com/v1.0/subscriptions",
        json={
            "changeType": "created",
            "notificationUrl": os.environ["WEBHOOK_URL"],
            "resource": sub["resource"],
            "expirationDateTime": _expiry(),
            "clientState": sub["client_state"],
        },
        headers={"Authorization": f"Bearer {token}", "Prefer": 'IdType="ImmutableId"'},
    )
    if not resp.ok:
        logger.error("Graph POST /subscriptions returned %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()
    return resp.json()


def _register_subscription(sub: dict, token: str) -> dict:
    webhook_url = os.environ["WEBHOOK_URL"]
    for existing in _list_subscriptions(token):
        if (
            existing.get("notificationUrl") == webhook_url
            and existing.get("resource") == sub["resource"]
        ):
            logger.info("Reusing existing subscription %s for %s", existing["id"], sub["resource"])
            return existing
    return _create_subscription(sub, token)


def _renew_or_register(sub: dict, subscription_id: str, token: str) -> dict:
    if not subscription_id:
        logger.warning("No subscription ID on file for %s -- registering", sub["resource"])
        created = _register_subscription(sub, token)
        _save_subscription_id(sub, created["id"])
        return created
    resp = _patch_subscription(subscription_id, token)
    if resp.status_code == 404:
        logger.warning("Subscription %s not found -- registering a replacement", subscription_id)
        created = _register_subscription(sub, token)
        _save_subscription_id(sub, created["id"])
        return created
    if not resp.ok:
        logger.error("Graph PATCH %s returned %d: %s", subscription_id, resp.status_code, resp.text)
        resp.raise_for_status()
    body = resp.json()
    logger.info(
        "Renewed %s (%s) -- expiry %s",
        subscription_id,
        sub["resource"],
        body.get("expirationDateTime"),
    )
    return body


@functions_framework.http
def renew(request):
    token = _get_access_token()
    results = [
        _renew_or_register(sub, _load_subscription_id(sub), token) for sub in _subscriptions()
    ]
    return json.dumps(results), 200, {"Content-Type": "application/json"}
```

**Latent bug fixed here:** the existing `_create_subscription` sends only the `Authorization` header — no `Prefer: IdType="ImmutableId"` — even though `clients/graph_subscriptions.register` does (`get_headers(immutable=True)`). A subscription the renew CF self-healed would therefore have delivered **mutable** IDs, breaking `/r/{uuid}` links after the 5 AM folder move. The snippet above adds the header for both subscriptions. Mention this in the PR description. Update the docstring env list with `WEBHOOK_CLIENT_STATE_SENT` and `SENT_SUBSCRIPTION_SECRET_NAME`.

- [ ] **Step 4: Implement `clients/graph_subscriptions.register`**

```python
def register(
    client,
    notification_url: str,
    *,
    resource: str = "me/mailFolders/inbox/messages",
    client_state: str | None = None,
) -> dict:
    """Create a subscription (immutable IDs). resource/client_state default to
    the Inbox subscription; pass the Sent Items pair for the second one."""
    resp = requests.post(
        "https://graph.microsoft.com/v1.0/subscriptions",
        json={
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": _expiry(),
            "clientState": client_state or os.environ.get("WEBHOOK_CLIENT_STATE", "inbox-webhook"),
        },
        headers=client.get_headers(immutable=True),
    )
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 5: Run tests** — `.venv/bin/pytest tests/ -q` — pass.

- [ ] **Step 6: Commit**

```bash
git add functions/renew/main.py clients/graph_subscriptions.py tests/test_renew.py tests/test_graph_subscriptions_register.py
git commit -m "feat: renew CF keeps the Inbox and Sent Items subscriptions alive"
```

---

### Task 9: Terraform + deploy workflow

**Files:**
- Modify: `terraform/secrets.tf`, `terraform/variables.tf`, `terraform/iam.tf`, `terraform/cloud_functions.tf`, `.github/workflows/deploy.yml`

- [ ] **Step 1: Move `hubspot-token` out of this state (pre-edit, live)**

Confirm people owns it first:

```bash
cd ~/src/people/terraform && terraform state list | grep hubspot_token   # expect: google_secret_manager_secret.hubspot_token
cd ~/src/inbox/terraform && terraform state rm 'google_secret_manager_secret.secrets["hubspot-token"]' 'google_secret_manager_secret_version.secrets["hubspot-token"]'
```

Expected: `Removed …` ×2. The live secret is untouched.

- [ ] **Step 2: `secrets.tf`**

- Delete the line `"hubspot-token"         = var.hubspot_token`.
- Add `"graph-sent-subscription-id" = var.graph_sent_subscription_id` to `local.secrets`, and add `"graph-sent-subscription-id"` to `self_managed_secrets`.
- Add, next to the existing `graph_subscription_id` version resource:

```hcl
resource "google_secret_manager_secret_version" "graph_sent_subscription_id" {
  secret      = google_secret_manager_secret.secrets["graph-sent-subscription-id"].id
  secret_data = var.graph_sent_subscription_id

  lifecycle {
    ignore_changes = [secret_data]
  }
}
```

- Add the people-owned token as a data source:

```hcl
# Owned by the people repo's terraform (github.com/bdrolet/people); inbox-process
# reads it to call people-api for sender context. People grants our SA accessor.
data "google_secret_manager_secret" "people_api_token" {
  secret_id = "people-api-token"
  project   = var.project_id
}
```

- [ ] **Step 3: `variables.tf`**

Delete the `hubspot_token` variable (lines 85-90). Add:

```hcl
variable "graph_sent_subscription_id" {
  description = "Seed for the Sent Items Graph subscription id secret; the renew CF owns the live value (self-heal). Empty seed = renew CF registers on first run."
  type        = string
  default     = ""
}

variable "people_api_url" {
  description = "people-api Cloud Run URL — gcloud run services describe people-api --region us-central1 --format='value(status.url)'"
  type        = string
  default     = ""
}
```

Note: the `graph-subscription-id` version resource uses `var.graph_subscription_id`; an empty seed for the sent secret creates a version with empty data, which `_load_subscription_id` treats as "register fresh". Good.

- [ ] **Step 4: `iam.tf`** — delete the `process_cf_hubspot` resource (lines 74-78). No new binding here: people's terraform grants `inbox-process-cf` on `people-api-token`.

- [ ] **Step 4b: project APIs** — inbox's terraform owns the project API list (`google_project_service.apis`). Add `"people.googleapis.com"` to it: the people service's Google Contacts client needs the People API, which was enabled by hand with `gcloud services enable people.googleapis.com` during the Phase A deploy and must be codified here so a fresh project gets it. The apply is a no-op for the already-enabled API.

- [ ] **Step 5: `cloud_functions.tf`**

Process CF: delete the `HUBSPOT_TOKEN` `secret_environment_variables` block (lines 281-286). Add to `environment_variables`: `PEOPLE_API_URL = var.people_api_url`. Add:

```hcl
    secret_environment_variables {
      key        = "PEOPLE_API_TOKEN"
      project_id = var.project_id
      secret     = data.google_secret_manager_secret.people_api_token.secret_id
      version    = "latest"
    }
```

Webhook CF `environment_variables`: add `WEBHOOK_CLIENT_STATE_SENT = "inbox-webhook-sent"`.
Renew CF `environment_variables`: add `SENT_SUBSCRIPTION_SECRET_NAME = "graph-sent-subscription-id"` and `WEBHOOK_CLIENT_STATE_SENT = "inbox-webhook-sent"`. Check the renew SA's secret IAM: it currently has accessor + versionManager on `graph-subscription-id` — grep `iam.tf` for `graph-subscription-id` and duplicate each binding for `graph-sent-subscription-id`.

- [ ] **Step 6: `deploy.yml`** — delete `TF_VAR_hubspot_token: ${{ secrets.TF_VAR_HUBSPOT_TOKEN }}`; add `TF_VAR_people_api_url: ${{ vars.PEOPLE_API_URL }}`. Then:

```bash
gh variable set PEOPLE_API_URL --repo bdrolet/inbox --body "$(gcloud run services describe people-api --region us-central1 --format='value(status.url)')"
gh secret delete TF_VAR_HUBSPOT_TOKEN --repo bdrolet/inbox
```

Also set `people_api_url` in the local `terraform/terraform.tfvars` and remove `hubspot_token` from it.

- [ ] **Step 7: Validate and plan**

```bash
cd terraform && terraform fmt -check && terraform validate
```

Then `/terraform-plan`. Expected: **no destroy of any secret**; changes = new `graph-sent-subscription-id` secret + version, env/secret-env updates on the three CFs, removed IAM binding `process_cf_hubspot`, updated source zips. If the plan shows `hubspot-token` being destroyed, stop — Step 1 did not take.

- [ ] **Step 8: Commit** (do not apply yet — apply happens with the PR in Task 11)

```bash
git add terraform/secrets.tf terraform/variables.tf terraform/iam.tf terraform/cloud_functions.tf .github/workflows/deploy.yml
git commit -m "infra: drop hubspot-token wiring; add people-api token + Sent Items subscription secret"
```

---

### Task 10: Docs

**Files:**
- Modify: `CLAUDE.md`, `docs/inbox-architecture.md`
- Verify: `~/.claude/skills/adding-referral-contact/SKILL.md` already repointed (people plan Task 16)

- [ ] **Step 1: CLAUDE.md**

- Project state: add after the calendar paragraph:

  > **People ownership (shipped):** inbox owns mail only. The separate `people` repo (github.com/bdrolet/people) owns everyone Ben corresponds with — Google Contacts (source of truth), a derived `people` index, a HubSpot mirror capped at 1000 most-recent contacts, and `people-api`. Inbox publishes `email_classified` (unchanged) and a new `email_sent` event from a second Graph subscription on Sent Items (`clientState` `inbox-webhook-sent`, secret `graph-sent-subscription-id`); the webhook stamps a `folder` Pub/Sub attribute and `main.py::process` routes `sentitems` to `handlers/sent.py`, which publishes and stores nothing. At classify time the pipeline fetches sender context from `people-api` (`clients/people_api.py`, 2 s timeout, fail-open). The `senders` table and HubSpot client are gone. Do not add contact or HubSpot logic here again. See `docs/superpowers/specs/2026-09-03-people-service-extraction-design.md`.

- Stack table: Domain events row → `email_classified` + `label_applied` + `email_sent`; add the people repo to the consumer list.
- Code layout: remove `senders` from the `repo/` comment; add `handlers/sent.py`.
- Database: "Five tables" → "Four tables: `messages`, `message_embeddings`, `classifications`, `tags`".
- Graph subscription section: mention the second subscription and secret; the re-register snippet gains a second call: `register(c, url, resource="me/mailFolders/sentitems/messages", client_state="inbox-webhook-sent")`.
- Secrets table: delete the `hubspot-token` row; add `graph-sent-subscription-id` (Renew CF) and `people-api-token` (Processor CF — **owned by the people repo**, read via data source).

- [ ] **Step 2: `docs/inbox-architecture.md`** — remove `repo/senders.py` from the layer diagram (line 104), the `senders` entries (135, 198, 222, 448, 498), and the "Sender context" pipeline stage text (replace with one sentence: sender context comes from `people-api`). Add the Sent Items subscription to the trigger section (~line 166).

- [ ] **Step 2b: Point the people skills at the custom domain** — `people-api.drolet.cloud` is mapped (people PR #2, infra PR #8). In `~/src/people/.claude/skills/{searching-people,fetching-person,editing-person}/SKILL.md` (the global copies under `~/.claude/skills/` are symlinks to these) replace the `terraform output -raw people_api_url` base-URL step with `BASE=https://people-api.drolet.cloud`, keeping the token step. Commit in the people repo via its own `/pr-open` (one PR, three files).

- [ ] **Step 3: Check the global skill**

```bash
grep -n "src/people\|src/inbox" ~/.claude/skills/adding-referral-contact/SKILL.md
```

Expected: only `src/people`. If `src/inbox` still appears, the people plan's Task 16 Step 3 was skipped — do it now.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/inbox-architecture.md
git commit -m "docs: people ownership, email_sent, Sent Items subscription"
```

---

### Task 11: PR, apply, Gate B, drop `senders`

- [ ] **Step 1: Local CI** — run the Global Constraints command. Expected: all green.

- [ ] **Step 2: Open the PR** — `/pr-open`. Title `feat: extract contacts to the people service; publish email_sent`. Body: link the spec and both plans; list the state-rm performed in Task 9 Step 1; note that apply happens from this PR.

- [ ] **Step 3: Verify locally** — `/verifying-pr-locally` (pipeline with `PEOPLE_API_URL` set in `.env` pointing at the live people-api; confirm the log line shows sender context or a clean miss).

- [ ] **Step 4: Apply** — `/terraform-apply` from the branch (it ships this branch's code, as the tasks extraction did). Then trigger the renew CF once so the Sent subscription registers:

```bash
curl -s -X POST "$(cd terraform && terraform output -raw renew_url 2>/dev/null || gcloud functions describe inbox-renew --region us-central1 --format='value(serviceConfig.uri)')"
gcloud secrets versions access latest --secret graph-sent-subscription-id
```

Expected: a JSON array of two subscriptions; the second secret now holds a subscription id.

- [ ] **Step 5: Gate B**

1. Send an email from your mailbox to a non-automated address you control.
2. `/fetch-inbox-logs` for `inbox-process`: expect `Published email_sent <id> → 1 recipients` and **no** `Processed …` classification line for it.
3. In people: `gcloud functions logs read people-process --region us-central1 --limit 20` shows `email_sent <id> → 1 recipients`; `curl "$PEOPLE_API/people/<recipient>"` shows `my_response_count` incremented.
4. Receive an email; `/tracing-inbox-email` shows the pipeline log; Grafana (`/querying-grafana-metrics`) shows `inbox_people_lookup_total{outcome="hit"|"miss"}` incrementing and zero `error` beyond transient.
5. `grep -c hubspot` on the processor's recent logs → 0.

Record the message ids in the PR.

- [ ] **Step 6: Merge** — after CI green and Gate B recorded. `/monitoring-inbox-deploy` until the deploy workflow succeeds (expected: a no-op-ish apply on `main`).

- [ ] **Step 7: Drop the dead table** — via `/querying-inbox-db` (read-only guardrails must be lifted for this one statement; do it deliberately):

```sql
DROP TABLE IF EXISTS senders;
```

Expected: `DROP TABLE`. Confirm `\dt` shows four tables.

- [ ] **Step 8: Hand off** — tell the people plan's Task 18 (Phase C) it can run.

---

## Self-review

**Spec coverage.** §10.1 → Tasks 2, 9 (secret state-rm, IAM, env, deploy.yml), 10; §10.2 → Tasks 3, 4, 9 (env + data source); §10.3 → Tasks 5, 6, 7, 8, 9 (secret, env), 10; §10.4 → Task 10; §11 Phase B and Gate B → Task 11 (including the deferred `DROP TABLE senders`). Metrics named in spec §14 for inbox (`inbox_people_lookup{outcome}`, duration histogram, `inbox_emails_sent_published`) → Task 3 (`clients/otel.py`) and Task 5 (`handlers/sent.py`).

**Type consistency.** `people_api.get_person(email) -> dict | None` returns exactly the four keys `build_prompt` reads (Task 3 test asserts the dict). `handlers.sent.run(notification, context=None)` matches `main._run_sent(n, context=ctx)` in Task 6. `email_events.email_sent_payload(email)` keys match the spec and the people plan's `EmailSentEvent` (`from` is the JSON key). Renew helpers all take `sub: dict` with keys `resource`, `client_state`, `secret_name`, matching `_subscriptions()` and the tests. `register(client, url, *, resource, client_state)` matches the CLAUDE.md snippet in Task 10.

**Placeholders.** None. The one conditional ("check whether `_create_subscription` already sends the Prefer header") is resolved by the snippet, which sends it regardless.
