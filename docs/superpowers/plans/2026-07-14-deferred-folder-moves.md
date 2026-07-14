# Deferred Folder Moves Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep classified email in the Inbox during the day and file it into action folders in one batch at 5:00 AM ET via a stateless, tag-driven sweep — while tags, tasks, drafts, and notifications still fire immediately, and Asana task links stay permanently valid via an immutable-ID-backed redirector.

**Architecture:** The classify pipeline stops moving messages; it only tags them (the Outlook category carries the destination category) and runs its side effects. A new HTTP-triggered `inbox-sweep` Cloud Function, invoked by Cloud Scheduler at 5 AM ET, lists the Inbox and moves each message into the folder implied by its current category tag, honoring `keep_until:<when>` holds. Asana "Open in Outlook" links become `inbox-api` redirector URLs (`/r/{uuid}`) that resolve the message's current webLink live via its immutable Graph ID, so they survive the sweep move.

**Tech Stack:** Python 3.13, FastAPI (inbox-api on Cloud Run), Google Cloud Functions Gen2 + Cloud Scheduler, Microsoft Graph REST, psycopg3 (local) / pg8000 (prod) via `clients/db.py`, pytest + ruff + mypy.

## Global Constraints

- Python 3.13; all code must pass `ruff check .` and `mypy .`.
- CI installs **only** `requirements-dev.txt` (ruff, mypy, pytest, httpx, requests, msal, python-dotenv, fastapi). Tests must not import heavy/uninstalled libs (torch, psycopg, google-cloud-*) at collection time; monkeypatch or stub them (see `tests/test_renew.py` for the stub pattern).
- pytest `testpaths = ["tests"]`.
- Layer rules: `clients/` I/O only; `repo/` takes an open `psycopg.Connection`, never opens its own; `services/` calls clients+repo, one concern; `handlers/` orchestrate; `models/` pure types.
- **`message_embeddings.current_label` is only set by human feedback** — do not touch it.
- Timezone for all `keep_until` and sweep-time logic: `America/New_York` (use stdlib `zoneinfo.ZoneInfo("America/New_York")`).
- PRs only (never commit code to `main`): open with the `pr-open` skill; verify with the `verifying-pr-locally` skill; Terraform via `terraform-plan`/`terraform-apply` skills.
- Category tag values (Outlook categories written by `apply_tags`): `urgent`, `respond`, `review`, `reference`, `ignore` (from `models/types.Category`).

---

## File Structure

- Create `services/sweep_rules.py` — pure decision logic (category→folder, `keep_until` parsing, per-message decision). No I/O.
- Create `services/links.py` — builds the redirector URL from a DB message UUID. Pure.
- Create `api/routers/redirect.py` — unauthenticated `GET /r/{uuid}` → 302 to live webLink.
- Modify `api/main.py` — register the redirect router.
- Modify `handlers/actions/_shared.py` — stop moving; keep summary/deadline/invite.
- Modify `handlers/actions/respond.py`, `review.py`, `urgent.py` — pass redirector URL as `web_link`.
- Modify `handlers/actions/ignore.py`, `reference.py` — become no-ops (tagging alone; sweep files them).
- Modify `handlers/actions/dispatch.py` — drop `IGNORE`/`REFERENCE` from `_HANDLERS`.
- Modify `clients/azure/graph_email_client.py` — `get_headers(immutable=False)`; immutable header on message read/move; add `list_inbox_categories()`, `get_web_link()`, `set_categories()`.
- Modify `clients/graph_subscriptions.py` — immutable header on `register`.
- Modify `main.py` — add `sweep` HTTP entry point.
- Modify `terraform/cloud_functions.tf`, `terraform/scheduler.tf`, `terraform/api.tf` — sweep CF, scheduler job, `REDIRECTOR_BASE_URL` on processor.
- Tests: `tests/test_sweep_rules.py`, `tests/test_links.py`, `tests/test_redirect_router.py`, `tests/test_sweep.py`, plus additions to graph-client header tests.

---

## Task 1: Sweep decision rules (pure logic)

**Files:**
- Create: `services/sweep_rules.py`
- Test: `tests/test_sweep_rules.py`

**Interfaces:**
- Produces:
  - `folder_for_category(category: str) -> str | None` — `"reference"`/`"ignore"`→`"Archive"`, `"respond"`→`"reply_required"`, `"review"`→`"review"`, `"urgent"`/unknown→`None`.
  - `KEEP_UNTIL_PREFIX = "keep_until:"`
  - `decide(categories: list[str], now: datetime) -> SweepDecision` where `SweepDecision` is a dataclass `{action: Literal["move","hold","skip"], folder: str | None, strip_categories: list[str]}`. `now` is timezone-aware ET.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sweep_rules.py
from datetime import datetime
from zoneinfo import ZoneInfo

from services.sweep_rules import decide, folder_for_category

ET = ZoneInfo("America/New_York")


def _now(y, m, d, hh=5, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_folder_for_category():
    assert folder_for_category("reference") == "Archive"
    assert folder_for_category("ignore") == "Archive"
    assert folder_for_category("respond") == "reply_required"
    assert folder_for_category("review") == "review"
    assert folder_for_category("urgent") is None
    assert folder_for_category("nonsense") is None


def test_decide_moves_tagged_message():
    d = decide(["reference", "P3"], _now(2026, 7, 14))
    assert d.action == "move"
    assert d.folder == "Archive"
    assert d.strip_categories == []


def test_decide_skips_urgent_and_untagged():
    assert decide(["urgent", "P0"], _now(2026, 7, 14)).action == "skip"
    assert decide(["P2", "newsletter"], _now(2026, 7, 14)).action == "skip"
    assert decide([], _now(2026, 7, 14)).action == "skip"


def test_decide_holds_future_keep_until_bare_date():
    # held through end of 2026-07-20 -> at 5 AM on 07-14 it is a hold
    d = decide(["respond", "keep_until:2026-07-20"], _now(2026, 7, 14))
    assert d.action == "hold"


def test_decide_files_after_bare_date_elapses():
    # first sweep strictly after 2026-07-20 -> file on 07-21
    d = decide(["respond", "keep_until:2026-07-20"], _now(2026, 7, 21))
    assert d.action == "move"
    assert d.folder == "reply_required"
    assert d.strip_categories == ["keep_until:2026-07-20"]


def test_decide_datetime_keep_until_boundary():
    tag = "keep_until:2026-07-14T09:00"
    assert decide(["review", tag], _now(2026, 7, 14, 5, 0)).action == "hold"
    assert decide(["review", tag], _now(2026, 7, 14, 9, 0)).action == "move"


def test_decide_unparseable_keep_until_holds():
    assert decide(["respond", "keep_until:not-a-date"], _now(2026, 7, 14)).action == "hold"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sweep_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.sweep_rules'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/sweep_rules.py
"""Pure decision logic for the morning sweep. No I/O — safe to import in CI."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KEEP_UNTIL_PREFIX = "keep_until:"

_CATEGORY_FOLDER = {
    "reference": "Archive",
    "ignore": "Archive",
    "respond": "reply_required",
    "review": "review",
    # "urgent" intentionally absent -> no move
}


def folder_for_category(category: str) -> str | None:
    return _CATEGORY_FOLDER.get(category)


@dataclass
class SweepDecision:
    action: str  # "move" | "hold" | "skip"
    folder: str | None = None
    strip_categories: list[str] = field(default_factory=list)


def _parse_keep_until(value: str) -> datetime | None:
    """Parse the value after 'keep_until:'. Returns the ET instant at which the
    hold elapses, or None if unparseable. A bare date holds through the end of
    that day (elapses at 00:00 ET the next day); a datetime elapses at that
    exact ET instant."""
    raw = value.strip()
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw)
            return dt.replace(tzinfo=ET) if dt.tzinfo is None else dt.astimezone(ET)
        d = datetime.fromisoformat(raw).date()
        return datetime.combine(d, time(0, 0), tzinfo=ET) + timedelta(days=1)
    except ValueError:
        return None


def decide(categories: list[str], now: datetime) -> SweepDecision:
    keep_tags = [c for c in categories if c.startswith(KEEP_UNTIL_PREFIX)]
    for tag in keep_tags:
        elapses = _parse_keep_until(tag[len(KEEP_UNTIL_PREFIX) :])
        if elapses is None or now < elapses:
            return SweepDecision(action="hold")

    for c in categories:
        folder = folder_for_category(c)
        if folder is not None:
            return SweepDecision(action="move", folder=folder, strip_categories=keep_tags)
    return SweepDecision(action="skip")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sweep_rules.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Lint + commit**

```bash
ruff check services/sweep_rules.py tests/test_sweep_rules.py && mypy services/sweep_rules.py
git add services/sweep_rules.py tests/test_sweep_rules.py
git commit -m "feat: pure sweep decision rules (folder mapping + keep_until)"
```

---

## Task 2: Redirector URL builder

**Files:**
- Create: `services/links.py`
- Test: `tests/test_links.py`

**Interfaces:**
- Produces: `redirector_url(message_uuid: str) -> str | None` — returns `f"{base}/r/{message_uuid}"` where `base = os.environ["REDIRECTOR_BASE_URL"]` (trailing slash trimmed). Returns `None` if the env var is unset or `message_uuid` is falsy, so callers fall back to the raw webLink.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_links.py
import services.links as links


def test_redirector_url_builds(monkeypatch):
    monkeypatch.setenv("REDIRECTOR_BASE_URL", "https://inbox-api.example.com/")
    assert links.redirector_url("abc-123") == "https://inbox-api.example.com/r/abc-123"


def test_redirector_url_none_when_unset(monkeypatch):
    monkeypatch.delenv("REDIRECTOR_BASE_URL", raising=False)
    assert links.redirector_url("abc-123") is None


def test_redirector_url_none_when_no_uuid(monkeypatch):
    monkeypatch.setenv("REDIRECTOR_BASE_URL", "https://x")
    assert links.redirector_url("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_links.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.links'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/links.py
"""Build stable redirector URLs for the inbox-api /r/{uuid} endpoint."""

import os


def redirector_url(message_uuid: str) -> str | None:
    base = os.environ.get("REDIRECTOR_BASE_URL")
    if not base or not message_uuid:
        return None
    return f"{base.rstrip('/')}/r/{message_uuid}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_links.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint + commit**

```bash
ruff check services/links.py tests/test_links.py && mypy services/links.py
git add services/links.py tests/test_links.py
git commit -m "feat: redirector URL builder"
```

---

## Task 3: Stop the inline move; wire redirector link into handlers

**Files:**
- Modify: `handlers/actions/_shared.py`
- Modify: `handlers/actions/respond.py`, `handlers/actions/review.py`, `handlers/actions/urgent.py`
- Modify: `handlers/actions/ignore.py`, `handlers/actions/reference.py`
- Modify: `handlers/actions/dispatch.py`

**Interfaces:**
- Consumes: `services.links.redirector_url` (Task 2).
- Produces: `_shared.prepare(msg, classification) -> tuple[str | None, EmailSummary, str | None, CalendarInvite | None]` — same return shape, **no `folder` parameter**, **no folder move**. `web_link` returned is `redirector_url(msg["id"]) or msg.get("web_link")`.

- [ ] **Step 1: Rewrite `_shared.prepare` to not move and to return a redirector link**

```python
# handlers/actions/_shared.py
from clients.graph import get_graph_client
from models.message import Message
from models.types import CalendarInvite, Classification, EmailSummary, Importance
from services import calendar_invite as calendar_invite_svc
from services import deadline as deadline_svc
from services import email_summary as summary_svc
from services.links import redirector_url


def prepare(
    msg: Message,
    classification: Classification,
) -> tuple[str | None, EmailSummary, str | None, CalendarInvite | None]:
    """Detect calendar invite, generate summary + deadline, and resolve the link.

    The folder move is deferred to the morning sweep, so nothing is moved here.
    The returned link is a stable redirector URL (falls back to the raw webLink
    if REDIRECTOR_BASE_URL is unset).
    """
    invite = calendar_invite_svc.detect(msg, get_graph_client())
    if invite:
        calendar_invite_svc.store(invite)

    web_link = redirector_url(str(msg.get("id") or "")) or msg.get("web_link")

    summary = summary_svc.generate(msg, html_body=msg.get("body_html"))
    due_date = (
        deadline_svc.extract_deadline(msg)
        if classification.importance in (Importance.P0, Importance.P1)
        else None
    )
    return web_link, summary, due_date, invite
```

- [ ] **Step 2: Update `respond.py` / `review.py` / `urgent.py` to call `prepare` without a folder**

In `handlers/actions/respond.py` change:
```python
    web_link, summary, due_date, invite = prepare(msg, classification, folder="reply_required")
```
to:
```python
    web_link, summary, due_date, invite = prepare(msg, classification)
```

In `handlers/actions/review.py` change:
```python
    web_link, summary, due_date, invite = prepare(msg, classification, folder="review")
```
to:
```python
    web_link, summary, due_date, invite = prepare(msg, classification)
```

`handlers/actions/urgent.py` already calls `prepare(msg, classification)` — no change to that line.

- [ ] **Step 3: Make `ignore`/`reference` no-ops and drop them from dispatch**

Replace `handlers/actions/ignore.py` body:
```python
# handlers/actions/ignore.py
"""IGNORE: no immediate action. Tagging happens in dispatch; the morning sweep
files the message to Archive based on its category tag."""
```

Replace `handlers/actions/reference.py` body:
```python
# handlers/actions/reference.py
"""REFERENCE: no immediate action. Tagging happens in dispatch; the morning
sweep files the message to Archive based on its category tag."""
```

Edit `handlers/actions/dispatch.py`:
```python
from handlers.actions import respond, review, urgent
...
_HANDLERS = {
    Category.URGENT: urgent.handle,
    Category.RESPOND: respond.handle,
    Category.REVIEW: review.handle,
}
```
(Remove the `ignore`/`reference` imports and their `_HANDLERS` entries. Tagging via `archiving.apply_tags(msg, classification)` at the top of `dispatch` is unchanged.)

- [ ] **Step 4: Remove the now-unused `move_to_folder`**

`services/archiving.py` — delete `move_to_folder` (no remaining callers after this task; `apply_tags` stays). Verify no references remain:

Run: `grep -rn "move_to_folder" handlers/ services/ main.py | grep -v __pycache__`
Expected: no output.

- [ ] **Step 5: Typecheck + commit**

Run: `mypy handlers/ services/archiving.py && ruff check handlers/ services/archiving.py`
Expected: clean.

```bash
git add handlers/ services/archiving.py
git commit -m "feat: defer folder move; emit redirector link from action handlers"
```

Note: handler modules import heavy clients, so behavior here is covered by the local E2E verification (`verifying-pr-locally`), not a CI unit test — see the Rollout section.

---

## Task 4: Immutable-ID Graph headers + read/move/list helpers

**Files:**
- Modify: `clients/azure/graph_email_client.py`
- Modify: `clients/graph_subscriptions.py`
- Test: `tests/test_graph_immutable.py`

**Interfaces:**
- Produces on `GraphEmailClient`:
  - `get_headers(immutable: bool = False) -> dict` — adds `Prefer: IdType="ImmutableId"` when `immutable=True`.
  - `list_inbox_categories() -> list[dict]` — returns `[{"id": str, "categories": list[str]}, ...]` for all Inbox messages (immutable IDs, paged).
  - `get_web_link(external_id: str) -> str | None` — `GET /me/messages/{id}?$select=webLink` (immutable header).
  - `set_categories(external_id: str, categories: list[str]) -> bool` — PATCH categories (immutable header).
- `clients.graph_subscriptions.register(client, notification_url)` sends the immutable header.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_immutable.py
from clients.azure.graph_email_client import GraphEmailClient


def _client():
    c = GraphEmailClient.__new__(GraphEmailClient)
    c.access_token = "tok"
    return c


def test_get_headers_default_has_no_prefer():
    h = _client().get_headers()
    assert "Prefer" not in h
    assert h["Authorization"] == "Bearer tok"


def test_get_headers_immutable_sets_prefer():
    h = _client().get_headers(immutable=True)
    assert h["Prefer"] == 'IdType="ImmutableId"'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_graph_immutable.py -v`
Expected: FAIL — `get_headers() got an unexpected keyword argument 'immutable'`.

- [ ] **Step 3: Update `get_headers` and add helpers**

In `clients/azure/graph_email_client.py`, replace `get_headers`:
```python
    def get_headers(self, immutable: bool = False) -> Dict[str, str]:
        """Headers for Graph API requests. When immutable=True, request/accept
        immutable IDs (stable across folder moves within the mailbox)."""
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        if immutable:
            headers["Prefer"] = 'IdType="ImmutableId"'
        return headers
```

Add helper methods (place near `move_message_to_action_folder`):
```python
    def list_inbox_categories(self) -> list[dict]:
        """Return [{'id', 'categories'}] for all Inbox messages (immutable IDs)."""
        results: list[dict] = []
        url = (
            f"{self.graph_endpoint}/me/mailFolders/inbox/messages"
            "?$select=id,categories&$top=100"
        )
        while url:
            resp = requests.get(url, headers=self.get_headers(immutable=True))
            resp.raise_for_status()
            data = resp.json()
            for m in data.get("value", []):
                results.append({"id": m["id"], "categories": m.get("categories", [])})
            url = data.get("@odata.nextLink")
        return results

    def get_web_link(self, external_id: str) -> str | None:
        """Resolve a message's current webLink via its immutable ID."""
        resp = requests.get(
            f"{self.graph_endpoint}/me/messages/{external_id}",
            headers=self.get_headers(immutable=True),
            params={"$select": "webLink"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("webLink")

    def set_categories(self, external_id: str, categories: list[str]) -> bool:
        """Overwrite a message's Outlook categories (immutable ID)."""
        try:
            resp = requests.patch(
                f"{self.graph_endpoint}/me/messages/{external_id}",
                headers=self.get_headers(immutable=True),
                json={"categories": categories},
            )
            resp.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error("set_categories failed for %s: %s", external_id, e)
            return False
```

Also add the immutable header to the existing message read/move calls so IDs round-trip: in `move_message_to_action_folder` change `headers=self.get_headers()` to `headers=self.get_headers(immutable=True)` (both the `get_or_create_mail_folder` call it makes internally may stay default — folders don't support immutable IDs — but the move POST uses immutable). In `fetch`-path message GET (the `$select=...webLink` fetch used by `services/ingestion`) pass `immutable=True` as well so the stored `external_id` is immutable.

- [ ] **Step 4: Add immutable header to subscription creation**

In `clients/graph_subscriptions.py`, `register`, change:
```python
        headers=client.get_headers(),
```
to:
```python
        headers=client.get_headers(immutable=True),
```

- [ ] **Step 5: Run test to verify it passes + lint**

Run: `pytest tests/test_graph_immutable.py -v`
Expected: PASS (2 tests)
Run: `ruff check clients/ tests/test_graph_immutable.py && mypy clients/azure/graph_email_client.py clients/graph_subscriptions.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add clients/azure/graph_email_client.py clients/graph_subscriptions.py tests/test_graph_immutable.py
git commit -m "feat: immutable Graph IDs + inbox listing/webLink/category helpers"
```

---

## Task 5: Redirector endpoint on inbox-api

**Files:**
- Create: `api/routers/redirect.py`
- Modify: `api/main.py`
- Test: `tests/test_redirect_router.py`

**Interfaces:**
- Consumes: `GraphEmailClient.get_web_link` (Task 4); `repo.messages.get` (existing: `get(conn, message_id) -> dict | None` with an `external_id` key); `clients.db.get_conn`; `clients.graph.get_graph_client`.
- Produces: `GET /r/{message_uuid}` → 302 to webLink; 404 unknown UUID / no webLink; 502 on Graph error. **No auth dependency.**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_redirect_router.py
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    # Stub the heavy modules the router imports at load time.
    fake_db = types.ModuleType("clients.db")
    fake_db.get_conn = lambda: _FakeConnCtx()
    monkeypatch.setitem(sys.modules, "clients.db", fake_db)

    fake_graph = types.ModuleType("clients.graph")
    fake_graph.get_graph_client = lambda: _FakeGraph()
    monkeypatch.setitem(sys.modules, "clients.graph", fake_graph)

    fake_repo = types.ModuleType("repo.messages")
    fake_repo.get = lambda conn, mid: (
        {"external_id": "IMMUT-1"} if mid == "known" else None
    )
    monkeypatch.setitem(sys.modules, "repo.messages", fake_repo)

    import importlib

    import api.routers.redirect as redirect

    importlib.reload(redirect)
    app = FastAPI()
    app.include_router(redirect.router)
    return TestClient(app, follow_redirects=False)


class _FakeConnCtx:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


class _FakeGraph:
    def get_web_link(self, external_id):
        return "https://outlook.example/msg" if external_id == "IMMUT-1" else None


def test_known_uuid_redirects(client):
    r = client.get("/r/known")
    assert r.status_code == 302
    assert r.headers["location"] == "https://outlook.example/msg"


def test_unknown_uuid_404(client):
    r = client.get("/r/missing")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_redirect_router.py -v`
Expected: FAIL — `No module named 'api.routers.redirect'`.

- [ ] **Step 3: Write the router**

```python
# api/routers/redirect.py
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from clients.db import get_conn
from clients.graph import get_graph_client
from repo import messages

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/r/{message_uuid}")
def redirect(message_uuid: str) -> RedirectResponse:
    with get_conn() as conn:
        row = messages.get(conn, message_uuid)
    if not row or not row.get("external_id"):
        raise HTTPException(status_code=404, detail="unknown message")
    try:
        web_link = get_graph_client().get_web_link(row["external_id"])
    except Exception as e:
        logger.warning("redirect: webLink resolution failed for %s: %s", message_uuid, e)
        raise HTTPException(status_code=502, detail="resolution failed") from e
    if not web_link:
        raise HTTPException(status_code=404, detail="message not found")
    return RedirectResponse(url=web_link, status_code=302)
```

- [ ] **Step 4: Register the router**

Edit `api/main.py`:
```python
from api.routers import emails, redirect, search

app = FastAPI(title="inbox-api")
app.include_router(search.router)
app.include_router(emails.router)
app.include_router(redirect.router)
```

- [ ] **Step 5: Run test + lint**

Run: `pytest tests/test_redirect_router.py -v`
Expected: PASS (2 tests)
Run: `ruff check api/routers/redirect.py api/main.py tests/test_redirect_router.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add api/routers/redirect.py api/main.py tests/test_redirect_router.py
git commit -m "feat: /r/{uuid} redirector resolving live webLink via immutable id"
```

---

## Task 6: Sweep entry point

**Files:**
- Modify: `main.py`
- Test: `tests/test_sweep.py`

**Interfaces:**
- Consumes: `services.sweep_rules.decide` (Task 1); `GraphEmailClient.list_inbox_categories`, `move_message_to_action_folder`, `set_categories` (Task 4); `clients.graph.get_graph_client`.
- Produces: `run_sweep(client, now: datetime) -> dict` — returns counts `{"moved", "held", "skipped", "errored"}`. Plus a `sweep` HTTP CF entry point in `main.py` that calls `run_sweep`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sweep.py
from datetime import datetime
from zoneinfo import ZoneInfo

from services.sweep import run_sweep  # pure orchestrator, importable in CI

ET = ZoneInfo("America/New_York")


class FakeClient:
    def __init__(self, messages):
        self._messages = messages
        self.moved = []
        self.stripped = []

    def list_inbox_categories(self):
        return self._messages

    def move_message_to_action_folder(self, msg_id, folder):
        self.moved.append((msg_id, folder))
        return {"id": msg_id}

    def set_categories(self, msg_id, categories):
        self.stripped.append((msg_id, categories))
        return True


def test_run_sweep_moves_holds_skips():
    now = datetime(2026, 7, 21, 5, 0, tzinfo=ET)
    client = FakeClient(
        [
            {"id": "a", "categories": ["reference", "P3"]},
            {"id": "b", "categories": ["urgent", "P0"]},
            {"id": "c", "categories": ["respond", "keep_until:2027-01-01"]},
            {"id": "d", "categories": ["respond", "keep_until:2026-07-20"]},
        ]
    )
    counts = run_sweep(client, now)
    assert ("a", "Archive") in client.moved
    assert ("d", "reply_required") in client.moved
    assert counts["moved"] == 2
    assert counts["held"] == 1  # c
    assert counts["skipped"] == 1  # b (urgent)
    # d had an elapsed keep_until -> stripped to just its category tag
    assert ("d", ["respond"]) in client.stripped


def test_run_sweep_counts_move_errors():
    now = datetime(2026, 7, 21, 5, 0, tzinfo=ET)

    class Boom(FakeClient):
        def move_message_to_action_folder(self, msg_id, folder):
            return None  # signals failure

    client = Boom([{"id": "a", "categories": ["reference"]}])
    counts = run_sweep(client, now)
    assert counts["errored"] == 1
    assert counts["moved"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sweep.py -v`
Expected: FAIL — `No module named 'services.sweep'`.

- [ ] **Step 3: Write the orchestrator**

```python
# services/sweep.py
"""Morning sweep orchestration: list Inbox, decide per message, move/strip.

Pure of GCP/functions-framework deps so it is unit-testable in CI. The Cloud
Function entry point in main.py builds the Graph client and calls run_sweep.
"""

import logging
from datetime import datetime

from services.sweep_rules import decide

logger = logging.getLogger(__name__)


def run_sweep(client, now: datetime) -> dict:
    counts = {"moved": 0, "held": 0, "skipped": 0, "errored": 0}
    for msg in client.list_inbox_categories():
        d = decide(msg.get("categories", []), now)
        if d.action == "hold":
            counts["held"] += 1
            continue
        if d.action == "skip":
            counts["skipped"] += 1
            continue
        # action == "move"
        moved = client.move_message_to_action_folder(msg["id"], d.folder)
        if moved is None:
            counts["errored"] += 1
            logger.warning("sweep: move failed for %s -> %s", msg["id"], d.folder)
            continue
        counts["moved"] += 1
        if d.strip_categories:
            remaining = [c for c in msg.get("categories", []) if c not in d.strip_categories]
            client.set_categories(moved.get("id", msg["id"]), remaining)
    logger.info("sweep complete: %s", counts)
    return counts
```

Fix the test import: it imports `from services.sweep import run_sweep`. Update Step 1's import line accordingly if you named the file differently — keep it `services/sweep.py`.

- [ ] **Step 4: Add the Cloud Function entry point**

In `main.py`, add near the other entry points:
```python
@functions_framework.http
def sweep(request):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from clients.graph import get_graph_client
    from services.sweep import run_sweep

    otel.flush()
    try:
        counts = run_sweep(get_graph_client(), datetime.now(ZoneInfo("America/New_York")))
        return (counts, 200)
    finally:
        otel.flush()
```
(`functions_framework` and `otel` are already imported at the top of `main.py`.)

- [ ] **Step 5: Run test + lint**

Run: `pytest tests/test_sweep.py -v`
Expected: PASS (2 tests)
Run: `ruff check services/sweep.py main.py tests/test_sweep.py && mypy services/sweep.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add services/sweep.py main.py tests/test_sweep.py
git commit -m "feat: morning sweep orchestration + inbox-sweep HTTP entry point"
```

---

## Task 7: Terraform — sweep CF, scheduler, redirector env

**Files:**
- Modify: `terraform/cloud_functions.tf` (new `inbox-sweep` function, mirroring `calendar_action`/`renew`)
- Modify: `terraform/scheduler.tf` (invoker IAM + scheduler job at 5 AM ET)
- Modify: `terraform/api.tf` (add `REDIRECTOR_BASE_URL` to inbox-api's own URL if self-referencing is possible, else a var) and the processor env in `terraform/cloud_functions.tf` (`REDIRECTOR_BASE_URL = google_cloud_run_v2_service.inbox_api.uri`).

**Interfaces:**
- Consumes: existing `google_cloudfunctions2_function.process` bundle (repo root), `google_service_account.scheduler_sa`, `google_cloud_run_v2_service.inbox_api`.

- [ ] **Step 1: Add the sweep Cloud Function** (mirror the `calendar_action` block: same source bundle as processor from repo root, `entry_point = "sweep"`, `512Mi`, HTTP-triggered — no `event_trigger`). Give it the same Secret-Manager env the processor uses for Graph auth (`GCP_PROJECT_ID`, `MSAL_SECRET_NAME`, `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID`, plus `GRAFANA_OTLP_*`). It needs **no** DB env.

```hcl
resource "google_cloudfunctions2_function" "sweep" {
  name     = "inbox-sweep"
  location = var.region

  build_config {
    runtime     = "python313"
    entry_point = "sweep"
    source {
      storage_source {
        bucket = google_storage_bucket.cf_source.name
        object = google_storage_bucket_object.process_source.name
      }
    }
  }

  service_config {
    available_memory      = "512Mi"
    timeout_seconds       = 540
    service_account_email = google_service_account.process_cf.email
    # env: copy the Graph-auth + OTLP env blocks from the process function
    # (GCP_PROJECT_ID, MSAL_SECRET_NAME, CLIENT_ID, CLIENT_SECRET, TENANT_ID,
    #  GRAFANA_OTLP_ENDPOINT, GRAFANA_OTLP_TOKEN). No DB env needed.
  }

  depends_on = [google_project_service.apis]
}
```

- [ ] **Step 2: Add `REDIRECTOR_BASE_URL` to the processor function env** (in the `process` function's `service_config`, plain env):

```hcl
      env {
        name  = "REDIRECTOR_BASE_URL"
        value = google_cloud_run_v2_service.inbox_api.uri
      }
```

- [ ] **Step 3: Add scheduler invoker IAM + job** in `terraform/scheduler.tf` (mirror `renew_invoker` + `inbox_renew`):

```hcl
resource "google_cloudfunctions2_function_iam_member" "sweep_invoker" {
  project        = var.project_id
  location       = var.region
  cloud_function = google_cloudfunctions2_function.sweep.name
  role           = "roles/cloudfunctions.invoker"
  member         = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

resource "google_cloud_run_v2_service_iam_member" "sweep_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloudfunctions2_function.sweep.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

resource "google_cloud_scheduler_job" "inbox_sweep" {
  name      = "inbox-morning-sweep"
  schedule  = "0 5 * * *"
  time_zone = "America/New_York"

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.sweep.service_config[0].uri
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }
    oidc_token {
      service_account_email = google_service_account.scheduler_sa.email
    }
  }

  depends_on = [google_project_service.apis]
}
```

- [ ] **Step 4: Validate formatting**

Run: `cd terraform && terraform fmt && terraform validate`
Expected: `Success! The configuration is valid.` (needs `terraform init` already done.)

- [ ] **Step 5: Commit** (do NOT apply yet — apply is a gated rollout step)

```bash
git add terraform/cloud_functions.tf terraform/scheduler.tf terraform/api.tf
git commit -m "feat: terraform for inbox-sweep CF + 5am ET scheduler + redirector env"
```

---

## Rollout & Production Gates

These steps are operational, not TDD. Run them in order; the ⛔ steps require Ben's explicit go-ahead because they touch production / live mail.

- [ ] **R1 — CI green.** Run the `running-ci-checks` skill (or `pytest && ruff check . && mypy .`). Expected: all pass.
- [ ] **R2 — Open the PR.** Use the `pr-open` skill (branch `deferred-folder-moves`). It commits, pushes, and writes the PR description.
- [ ] **R3 — Local verification.** Use the `verifying-pr-locally` skill. Exercise:
  - Pipeline against a real test email → classified, tagged, task created with a `/r/{uuid}` link, **not moved**.
  - Hit the `/r/{uuid}` link → 302 to the Inbox copy.
  - Invoke `sweep` locally against the test mailbox → tagged message moves; `keep_until:<future>` stays; `urgent` stays; untagged stays; elapsed `keep_until` files and is stripped.
  - Hit the same `/r/{uuid}` after the move → 302 to the new folder copy.
- [ ] **R4 ⛔ — Merge to main.** Confirm with Ben, then merge the PR. Triggers the GitHub Actions deploy of `inbox-process`, `inbox-api`, and (once Terraform is applied) `inbox-sweep`.
- [ ] **R5 — Monitor deploy.** Use the `monitoring-inbox-deploy` skill to watch the Actions workflow to green.
- [ ] **R6 ⛔ — Terraform apply.** Use `terraform-plan` then `terraform-apply`. Confirm the plan adds `inbox-sweep`, `inbox-morning-sweep` scheduler, the two invoker bindings, and `REDIRECTOR_BASE_URL`; no destroys. Requires Ben's go-ahead.
- [ ] **R7 ⛔ — Immutable-ID subscription cutover.** Recreate the live Graph subscription so notifications carry immutable IDs. With local Graph auth:
  ```python
  from clients.azure import GraphEmailClient
  from clients.graph_subscriptions import register, delete
  c = GraphEmailClient(); c.authenticate_interactive()
  delete(c, "f58b30e4-4090-433a-87cc-fbe1f87f574a")  # current sub
  print(register(c, "https://inbox-webhook-aizbgjlava-uc.a.run.app")["id"])
  ```
  Update `graph_subscription_id` in `terraform/terraform.tfvars` and the CLAUDE.md note to the new ID; the renew CF self-heals it into the `graph-subscription-id` secret. Production cutover — requires Ben's go-ahead. Verify a freshly-arrived notification's `resourceData.id` is immutable-format.
- [ ] **R8 — Post-cutover smoke check.** Send a test email; confirm it is tagged, left in the Inbox, its Asana `/r/{uuid}` link opens it, and the next (or a manually-invoked) 5 AM sweep files it.

---

## Self-Review

**Spec coverage:**
- Tag immediately, notify immediately, defer move → Task 3 (handlers stop moving; dispatch still tags; urgent still notifies). ✓
- Business logic at move time from tags → Tasks 1 + 6 (`decide` from live categories in the sweep). ✓
- 5 AM ET cron sweep → Tasks 6 + 7. ✓
- `keep_until` hold → Task 1 (`decide`) + Task 6 (strip on file). ✓
- Immutable IDs (reads/moves/subscription) → Task 4 + R7. ✓
- Redirector, live-resolving, unauthenticated, 302 → Task 5; link emitted → Tasks 2 + 3. ✓
- Urgent not moved → `folder_for_category("urgent") is None` (Task 1); sweep skips (Task 6). ✓
- No pending_folder/moved_at columns → no schema task; confirmed none added. ✓
- Verify via pr-open + verifying-pr-locally → R2/R3. ✓
- Calendar-invite bonus fix → free via immutable IDs (R7); no dedicated task, as designed. ✓

**Placeholder scan:** No TODO/TBD; Task 1 shows complete, correct code. Terraform Task 7 Step 1 leaves the repeated Graph-auth/OTLP env blocks as a copy instruction rather than re-listing ~40 lines already shown verbatim in `cloud_functions.tf`'s `process` function — acceptable as it points at the exact existing source to mirror.

**Type consistency:** `decide` → `SweepDecision{action, folder, strip_categories}` used identically in Tasks 1 and 6. `get_web_link`/`list_inbox_categories`/`set_categories`/`move_message_to_action_folder` signatures match between Task 4 (def) and Tasks 5/6 (use). `redirector_url(str)` matches Task 2 def and Task 3 use. `messages.get(conn, id)->dict|None` matches existing repo signature used in Task 5.
