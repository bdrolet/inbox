# Shared-Mailbox & M365 Group Read Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use superpowers:test-driven-development — write the failing test before the implementation in every task.

**Goal:** Let the read API (`GET /emails/{id}`, `GET /emails/{id}/attachments`) fetch messages from shared mailboxes and full M365 group conversations, using the `mailbox` label that `/search` already returns.

**Architecture:** The caller passes `?mailbox=` (default `"me"`) taking exactly the label from search results (`"me"`, a shared address, or `"group:{mail-or-id}"`). A new `services/fetching.py` owns the label policy (parse, resolve group mail→ID, normalize conversations); the Graph client gains mailbox-aware read paths and two group-conversation methods; the router stays HTTP-only. Spec: `docs/superpowers/specs/2026-07-02-shared-mailbox-group-read-design.md`.

**Tech Stack:** Python 3.13, FastAPI, requests, pytest (fakes via monkeypatch — no new runtime deps; `httpx` added to dev deps for FastAPI's TestClient).

## Global Constraints

- **Branch:** all work on `feat/shared-group-read` off `main`. Never commit to `main` (CLAUDE.md).
- **Do NOT build on or depend on** the unimplemented `docs/superpowers/plans/2026-06-26-dumb-from-shared-mailbox-config.md` (no `config.py`, no `services/sending.py`). This plan is independent.
- **Layer rules (CLAUDE.md):** `clients/` I/O only — no label parsing or group resolution there. Policy lives in `services/fetching.py`. Router does HTTP concerns only.
- **Error contract (spec):** client read methods return `None` / raise `LookupError` **only on Graph 404**; raise `requests.HTTPError` otherwise. Router maps: `LookupError→404`, `RuntimeError→503`, `ValueError→400`, `HTTPError 403→403`, other `HTTPError→502`, `None→404 "message not found"`.
- **Backward compatibility:** `?mailbox=` defaults to `"me"`; `posts` is `None` for non-group messages; `post_id` is `None` for non-group attachments. Existing callers see identical responses.
- **No new Graph scopes** — search already uses `Mail.Read.Shared` and `Group.Read.All`.
- **Environment gotcha:** the repo venv is currently **broken** (Homebrew removed `python@3.13`; `.venv/bin/python3.13` is a dangling symlink). Task 0 rebuilds it — do not skip.
- Run all commands from the repo root `/Users/ben/src/inbox` with `.venv` activated.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `clients/azure/graph_email_client.py` | `mailbox` param on `get_email_details`/`get_attachments`; new `get_group_conversation` + `get_group_post_attachments`; 404-vs-raise error contract | Modify |
| `services/ingestion.py` | Preserve pipeline behavior: catch new client exceptions in `fetch()` | Modify |
| `services/fetching.py` | Mailbox-label policy: parse label, resolve group, assemble conversation, aggregate attachments | Create |
| `api/routers/emails.py` | `?mailbox=` param, `Post` model, `posts`/`post_id` fields, error mapping, delegate to service | Modify |
| `tests/test_graph_read.py` | Client read methods: paths + error contract | Create |
| `tests/test_graph_groups.py` | Client group-conversation methods | Create |
| `tests/test_ingestion_fetch.py` | `ingestion.fetch` swallows client errors | Create |
| `tests/test_fetching.py` | Fetching service (fake client) | Create |
| `tests/test_emails_router.py` | Router param/serialization/error mapping (service mocked) | Create |
| `requirements-dev.txt` | Add `httpx` for TestClient | Modify |
| `~/.claude/skills/fetching-inbox-email/SKILL.md` | Document `?mailbox=` usage (outside repo — not committed) | Modify |

---

### Task 0: Rebuild the broken venv + feature branch

**Files:** none in-repo (environment only).

- [ ] **Step 1: Reinstall Python 3.13 and rebuild the venv**

```bash
cd /Users/ben/src/inbox
brew list python@3.13 >/dev/null 2>&1 || brew install python@3.13
rm -rf .venv
/opt/homebrew/opt/python@3.13/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt -r api/requirements.txt
```

- [ ] **Step 2: Verify the existing suite runs**

Run: `python -m pytest tests/ -q`
Expected: existing tests (test_classification, test_renew) pass. If collection errors mention missing packages, `pip install` the named package and re-run.

- [ ] **Step 3: Create the feature branch**

```bash
git checkout main && git pull
git checkout -b feat/shared-group-read
```

---

### Task 1: Graph client — mailbox-aware reads + 404-vs-raise contract

**Files:**
- Modify: `clients/azure/graph_email_client.py:354-383` (`get_email_details`), `clients/azure/graph_email_client.py:717-729` (`get_attachments`)
- Modify: `services/ingestion.py:1-10`
- Test: `tests/test_graph_read.py`, `tests/test_ingestion_fetch.py`

**Interfaces:**
- Produces: `GraphEmailClient.get_email_details(email_id: str, mailbox: str = "me") -> Email | None` — `None` only on 404, raises `requests.HTTPError` otherwise.
- Produces: `GraphEmailClient.get_attachments(message_id: str, mailbox: str = "me") -> list[dict]` — raises `LookupError` on 404, `requests.HTTPError` otherwise.
- Both build `/me/...` when `mailbox == "me"`, else `/users/{mailbox}/...` (same convention as `search_emails`, `graph_email_client.py:812-815`).

Callers of the old swallow-everything behavior (audited): `services/calendar_invite.py:24-28` and `scripts/backfill_embeddings.py:96` already wrap in `try` — safe. `services/ingestion.py:10` must newly catch (this task).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph_read.py`:

```python
import pytest
import requests

from clients.azure.graph_email_client import GraphEmailClient


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(str(self.status_code))
            err.response = self
            raise err


def _client() -> GraphEmailClient:
    """Bare client: skip __init__ (env vars + MSAL); set only what reads use."""
    c = GraphEmailClient.__new__(GraphEmailClient)
    c.graph_endpoint = "https://graph.microsoft.com/v1.0"
    c.access_token = "token"
    return c


def test_get_email_details_default_hits_me(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(json_data={"id": "m1", "subject": "s"})

    monkeypatch.setattr(requests, "get", fake_get)
    email = _client().get_email_details("m1")
    assert seen["url"] == "https://graph.microsoft.com/v1.0/me/messages/m1"
    assert email.id == "m1"


def test_get_email_details_shared_hits_users_path(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(json_data={"id": "m1"})

    monkeypatch.setattr(requests, "get", fake_get)
    _client().get_email_details("m1", mailbox="team@x.com")
    assert seen["url"] == "https://graph.microsoft.com/v1.0/users/team@x.com/messages/m1"


def test_get_email_details_404_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=404))
    assert _client().get_email_details("gone") is None


def test_get_email_details_403_raises(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=403, text="denied"))
    with pytest.raises(requests.exceptions.HTTPError):
        _client().get_email_details("m1", mailbox="team@x.com")


def test_get_attachments_shared_hits_users_path(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(json_data={"value": [{"id": "a1"}]})

    monkeypatch.setattr(requests, "get", fake_get)
    atts = _client().get_attachments("m1", mailbox="team@x.com")
    assert seen["url"] == "https://graph.microsoft.com/v1.0/users/team@x.com/messages/m1/attachments"
    assert atts == [{"id": "a1"}]


def test_get_attachments_404_raises_lookup(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=404))
    with pytest.raises(LookupError):
        _client().get_attachments("gone")


def test_get_attachments_403_raises_http(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=403))
    with pytest.raises(requests.exceptions.HTTPError):
        _client().get_attachments("m1", mailbox="team@x.com")
```

Create `tests/test_ingestion_fetch.py`:

```python
import requests

from services import ingestion


class _RaisingClient:
    def get_email_details(self, message_id):
        raise requests.exceptions.HTTPError("boom")


class _OkClient:
    def get_email_details(self, message_id):
        return "the-email"


def test_fetch_returns_none_on_graph_error():
    assert ingestion.fetch("m1", _RaisingClient()) is None


def test_fetch_passes_through_success():
    assert ingestion.fetch("m1", _OkClient()) == "the-email"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_graph_read.py tests/test_ingestion_fetch.py -v`
Expected: FAIL — `get_email_details() got an unexpected keyword argument 'mailbox'`, 404 test fails (current code returns `None` for *all* errors so 403 test fails too), `test_fetch_returns_none_on_graph_error` raises.

- [ ] **Step 3: Implement the client changes**

In `clients/azure/graph_email_client.py`, replace `get_email_details` (lines 354–383) with:

```python
    def get_email_details(self, email_id: str, mailbox: str = "me") -> Optional[Email]:
        """Get detailed information about a specific email.

        Args:
            email_id: The ID of the email to retrieve.
            mailbox: 'me' for the primary mailbox or an email address for a
                shared mailbox (same convention as search_emails).

        Returns:
            Email object, or None if the message does not exist (Graph 404).

        Raises:
            requests.HTTPError: any Graph error other than 404 (e.g. 403 when
                the token lacks rights to a shared mailbox).
        """
        if not self.access_token:
            raise ValueError("Not authenticated. Call authenticate() first.")

        base = "/me" if mailbox == "me" else f"/users/{mailbox}"
        endpoint = f"{self.graph_endpoint}{base}/messages/{email_id}"
        params = {
            "$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,sentDateTime,body,bodyPreview,isRead,hasAttachments,attachments,webLink"
        }
        response = requests.get(endpoint, headers=self.get_headers(), params=params)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return Email(response.json())
```

Replace `get_attachments` (lines 717–729) with:

```python
    def get_attachments(self, message_id: str, mailbox: str = "me") -> list[dict]:
        """GET {mailbox}/messages/{id}/attachments — returns raw attachment dicts.

        Raises:
            LookupError: the message does not exist (Graph 404).
            requests.HTTPError: any other Graph error.
        """
        base = "/me" if mailbox == "me" else f"/users/{mailbox}"
        response = requests.get(
            f"{self.graph_endpoint}{base}/messages/{message_id}/attachments",
            headers=self.get_headers(),
        )
        if response.status_code == 404:
            raise LookupError("message not found")
        response.raise_for_status()
        return response.json().get("value", [])
```

In `services/ingestion.py`, replace lines 1–10 with:

```python
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from clients.azure.email import Email
from models.message import Message

logger = logging.getLogger(__name__)


def fetch(message_id: str, client) -> Optional[Email]:
    """Fetch a single email by ID from the Graph API.

    Returns None on any Graph failure — the pipeline treats fetch failures
    as skips (pre-existing behavior, preserved when the client started
    raising instead of swallowing errors).
    """
    try:
        return client.get_email_details(message_id)
    except requests.RequestException:
        logger.exception("Failed to fetch message %s", message_id)
        return None
```

(Keep `normalize` and everything below line 10 unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_graph_read.py tests/test_ingestion_fetch.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite (regression) and commit**

Run: `python -m pytest tests/ -q` — Expected: all pass.

```bash
git add clients/azure/graph_email_client.py services/ingestion.py tests/test_graph_read.py tests/test_ingestion_fetch.py
git commit -m "feat: mailbox-aware Graph read methods with 404-vs-raise contract"
```

---

### Task 2: Graph client — group conversation methods

**Files:**
- Modify: `clients/azure/graph_email_client.py` (add two methods after `search_group_conversations`, ~line 913)
- Test: `tests/test_graph_groups.py`

**Interfaces:**
- Produces: `GraphEmailClient.get_group_conversation(group_id: str, conversation_id: str) -> dict | None` — returns `{"topic": str, "lastDeliveredDateTime": str | None, "posts": list[dict]}`; each post is the raw Graph post dict annotated with `"threadId"`. `None` only on conversation 404; raises `requests.HTTPError` otherwise.
- Produces: `GraphEmailClient.get_group_post_attachments(group_id: str, thread_id: str, post_id: str) -> list[dict]` — raw attachment dicts; raises `LookupError` on 404, `requests.HTTPError` otherwise.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph_groups.py`:

```python
import pytest
import requests

from clients.azure.graph_email_client import GraphEmailClient


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(str(self.status_code))
            err.response = self
            raise err


def _client() -> GraphEmailClient:
    c = GraphEmailClient.__new__(GraphEmailClient)
    c.graph_endpoint = "https://graph.microsoft.com/v1.0"
    c.access_token = "token"
    return c


def _graph_fake(routes):
    """Return a fake requests.get dispatching on URL substring."""

    def fake_get(url, headers=None, params=None):
        for fragment, resp in routes.items():
            if fragment in url:
                return resp
        raise AssertionError(f"unexpected URL: {url}")

    return fake_get


def test_get_group_conversation_assembles_posts(monkeypatch):
    routes = {
        "/threads/t1/posts": _Resp(
            json_data={"value": [{"id": "p1", "receivedDateTime": "2026-07-01T10:00:00Z"}]}
        ),
        "/conversations/c1/threads": _Resp(json_data={"value": [{"id": "t1"}]}),
        "/conversations/c1": _Resp(
            json_data={"topic": "Lunch", "lastDeliveredDateTime": "2026-07-01T10:00:00Z"}
        ),
    }
    monkeypatch.setattr(requests, "get", _graph_fake(routes))
    convo = _client().get_group_conversation("g1", "c1")
    assert convo["topic"] == "Lunch"
    assert convo["posts"] == [
        {"id": "p1", "receivedDateTime": "2026-07-01T10:00:00Z", "threadId": "t1"}
    ]


def test_get_group_conversation_404_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=404))
    assert _client().get_group_conversation("g1", "gone") is None


def test_get_group_conversation_403_raises(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=403))
    with pytest.raises(requests.exceptions.HTTPError):
        _client().get_group_conversation("g1", "c1")


def test_get_group_post_attachments(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None):
        seen["url"] = url
        return _Resp(json_data={"value": [{"id": "a1", "name": "f.pdf"}]})

    monkeypatch.setattr(requests, "get", fake_get)
    atts = _client().get_group_post_attachments("g1", "t1", "p1")
    assert seen["url"] == "https://graph.microsoft.com/v1.0/groups/g1/threads/t1/posts/p1/attachments"
    assert atts == [{"id": "a1", "name": "f.pdf"}]


def test_get_group_post_attachments_404_raises_lookup(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(status_code=404))
    with pytest.raises(LookupError):
        _client().get_group_post_attachments("g1", "t1", "gone")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_graph_groups.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'get_group_conversation'`.

- [ ] **Step 3: Implement the two methods**

In `clients/azure/graph_email_client.py`, add directly after `search_group_conversations` (after ~line 912):

```python
    def get_group_conversation(self, group_id: str, conversation_id: str) -> Optional[dict]:
        """Fetch an M365 group conversation with all its posts.

        Returns:
            {"topic": str, "lastDeliveredDateTime": str | None, "posts": [dict]}
            where each post is the raw Graph post dict annotated with its
            "threadId". None if the conversation does not exist (Graph 404).

        Raises:
            requests.HTTPError: any Graph error other than 404.
        """
        conv_url = f"{self.graph_endpoint}/groups/{group_id}/conversations/{conversation_id}"
        response = requests.get(
            conv_url,
            headers=self.get_headers(),
            params={"$select": "id,topic,lastDeliveredDateTime"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        convo = response.json()

        threads_resp = requests.get(
            f"{conv_url}/threads", headers=self.get_headers(), params={"$select": "id"}
        )
        threads_resp.raise_for_status()

        posts: list[dict] = []
        for thread in threads_resp.json().get("value", []):
            posts_resp = requests.get(
                f"{self.graph_endpoint}/groups/{group_id}/threads/{thread['id']}/posts",
                headers=self.get_headers(),
                params={"$select": "id,body,from,sender,receivedDateTime,hasAttachments"},
            )
            posts_resp.raise_for_status()
            for post in posts_resp.json().get("value", []):
                post["threadId"] = thread["id"]
                posts.append(post)

        return {
            "topic": convo.get("topic", ""),
            "lastDeliveredDateTime": convo.get("lastDeliveredDateTime"),
            "posts": posts,
        }

    def get_group_post_attachments(self, group_id: str, thread_id: str, post_id: str) -> list[dict]:
        """GET /groups/{gid}/threads/{tid}/posts/{pid}/attachments — raw dicts.

        Raises:
            LookupError: the post does not exist (Graph 404).
            requests.HTTPError: any other Graph error.
        """
        response = requests.get(
            f"{self.graph_endpoint}/groups/{group_id}/threads/{thread_id}/posts/{post_id}/attachments",
            headers=self.get_headers(),
        )
        if response.status_code == 404:
            raise LookupError("post not found")
        response.raise_for_status()
        return response.json().get("value", [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_graph_groups.py -v`
Expected: all PASS. (Note the `_graph_fake` route order matters: `/conversations/c1/threads` is checked before `/conversations/c1` because dicts preserve insertion order and the threads fragment is listed first.)

- [ ] **Step 5: Commit**

```bash
git add clients/azure/graph_email_client.py tests/test_graph_groups.py
git commit -m "feat: Graph client methods for group conversation posts and attachments"
```

---

### Task 3: `services/fetching.py` — mailbox-label policy

**Files:**
- Create: `services/fetching.py`
- Test: `tests/test_fetching.py`

**Interfaces:**
- Consumes: `clients.graph.get_graph_client()` (raises `RuntimeError` on auth failure); Task 1's `get_email_details(id, mailbox=)` / `get_attachments(id, mailbox=)`; Task 2's `get_group_conversation` / `get_group_post_attachments`; existing `get_member_groups()` (returns `[{"id", "display_name", "mail"}]`).
- Produces: `FetchedEmail` frozen dataclass — `email: Email`, `posts: list[dict] | None` (None for non-group).
- Produces: `fetch_email(message_id: str, mailbox: str = "me") -> FetchedEmail | None` — `None` when not found; raises `LookupError` for unknown group.
- Produces: `fetch_attachments(message_id: str, mailbox: str = "me") -> list[dict]` — group attachments annotated with `"postId"`; raises `LookupError` when group/conversation unknown.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetching.py`:

```python
import pytest

import services.fetching as fetching
from services.fetching import FetchedEmail, fetch_attachments, fetch_email


class FakeClient:
    def __init__(self):
        self.groups = [{"id": "g1", "display_name": "Eng", "mail": "eng@x.com"}]
        self.emails = {}          # (message_id, mailbox) -> Email-constructor dict
        self.attachments = {}     # (message_id, mailbox) -> list[dict]
        self.conversations = {}   # (group_id, conversation_id) -> convo dict
        self.post_attachments = {}  # (group_id, thread_id, post_id) -> list[dict]

    def get_member_groups(self):
        return self.groups

    def get_email_details(self, email_id, mailbox="me"):
        from clients.azure.email import Email

        data = self.emails.get((email_id, mailbox))
        return Email(data) if data else None

    def get_attachments(self, message_id, mailbox="me"):
        if (message_id, mailbox) not in self.attachments:
            raise LookupError("message not found")
        return self.attachments[(message_id, mailbox)]

    def get_group_conversation(self, group_id, conversation_id):
        return self.conversations.get((group_id, conversation_id))

    def get_group_post_attachments(self, group_id, thread_id, post_id):
        return self.post_attachments.get((group_id, thread_id, post_id), [])


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(fetching, "get_graph_client", lambda: fake)
    return fake


def _post(pid, when, body="hi", has_attachments=False):
    return {
        "id": pid,
        "threadId": "t1",
        "receivedDateTime": when,
        "body": {"contentType": "html", "content": body},
        "from": {"emailAddress": {"name": "Ann", "address": "ann@x.com"}},
        "hasAttachments": has_attachments,
    }


def test_fetch_email_default_me(client):
    client.emails[("m1", "me")] = {"id": "m1", "subject": "hello"}
    fetched = fetch_email("m1")
    assert isinstance(fetched, FetchedEmail)
    assert fetched.email.subject == "hello"
    assert fetched.posts is None


def test_fetch_email_shared_mailbox(client):
    client.emails[("m1", "team@x.com")] = {"id": "m1", "subject": "shared"}
    assert fetch_email("m1", mailbox="team@x.com").email.subject == "shared"


def test_fetch_email_not_found_returns_none(client):
    assert fetch_email("gone") is None


def test_fetch_email_group_by_mail_case_insensitive(client):
    client.conversations[("g1", "c1")] = {
        "topic": "Lunch",
        "lastDeliveredDateTime": "2026-07-01T12:00:00Z",
        "posts": [_post("p1", "2026-07-01T10:00:00Z"), _post("p2", "2026-07-01T12:00:00Z", body="latest")],
    }
    fetched = fetch_email("c1", mailbox="group:ENG@x.com")
    assert fetched.email.subject == "Lunch"
    assert fetched.email.body_content == "latest"          # mirrors latest post
    assert [p["id"] for p in fetched.posts] == ["p1", "p2"]  # oldest first
    assert fetched.email.web_link is None


def test_fetch_email_group_by_id(client):
    client.conversations[("g1", "c1")] = {"topic": "T", "lastDeliveredDateTime": None, "posts": []}
    assert fetch_email("c1", mailbox="group:g1").email.subject == "T"


def test_fetch_email_unknown_group_raises(client):
    with pytest.raises(LookupError, match="unknown group"):
        fetch_email("c1", mailbox="group:nobody@x.com")


def test_fetch_email_group_conversation_not_found(client):
    assert fetch_email("gone", mailbox="group:eng@x.com") is None


def test_fetch_attachments_mailbox_passthrough(client):
    client.attachments[("m1", "team@x.com")] = [{"id": "a1"}]
    assert fetch_attachments("m1", mailbox="team@x.com") == [{"id": "a1"}]


def test_fetch_attachments_group_aggregates_with_post_id(client):
    client.conversations[("g1", "c1")] = {
        "topic": "T",
        "lastDeliveredDateTime": None,
        "posts": [_post("p1", "2026-07-01T10:00:00Z", has_attachments=True), _post("p2", "2026-07-01T11:00:00Z")],
    }
    client.post_attachments[("g1", "t1", "p1")] = [{"id": "a1", "name": "f.pdf"}]
    atts = fetch_attachments("c1", mailbox="group:eng@x.com")
    assert atts == [{"id": "a1", "name": "f.pdf", "postId": "p1"}]


def test_fetch_attachments_group_conversation_not_found(client):
    with pytest.raises(LookupError):
        fetch_attachments("gone", mailbox="group:eng@x.com")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fetching.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.fetching'`.

- [ ] **Step 3: Implement `services/fetching.py`**

```python
"""Read a message or group conversation given a search-result mailbox label.

Owns the mailbox-label policy: parses the `mailbox` label that /search
returns ("me", a shared-mailbox address, or "group:{mail-or-id}"), resolves
group labels to a group ID via the user's memberships, and normalizes group
conversations into the same Email shape as single messages.
"""
import logging
from dataclasses import dataclass

from clients.azure.email import Email
from clients.graph import get_graph_client

logger = logging.getLogger(__name__)

_GROUP_PREFIX = "group:"


@dataclass(frozen=True)
class FetchedEmail:
    email: Email
    posts: list[dict] | None = None  # group conversations only, oldest first


def _resolve_group_id(client, value: str) -> str:
    """Match a group mail address or ID against the user's groups."""
    needle = value.strip().lower()
    for group in client.get_member_groups():
        if group["id"].lower() == needle or (group.get("mail") or "").lower() == needle:
            return group["id"]
    raise LookupError(f"unknown group: {value}")


def _conversation_to_fetched(conversation_id: str, convo: dict) -> FetchedEmail:
    posts = sorted(convo["posts"], key=lambda p: p.get("receivedDateTime") or "")
    latest = posts[-1] if posts else {}
    # Same synthetic-Email technique as search_group_conversations: top-level
    # fields mirror the latest post (parity with search's preview).
    synthetic = {
        "id": conversation_id,
        "subject": convo.get("topic", ""),
        "from": latest.get("from") or latest.get("sender") or {},
        "toRecipients": [],
        "receivedDateTime": convo.get("lastDeliveredDateTime"),
        "sentDateTime": latest.get("receivedDateTime"),
        "body": latest.get("body", {}),
        "hasAttachments": any(p.get("hasAttachments") for p in posts),
        "webLink": None,  # Graph has no webLink for group posts
    }
    return FetchedEmail(email=Email(synthetic), posts=posts)


def fetch_email(message_id: str, mailbox: str = "me") -> FetchedEmail | None:
    """Fetch one message (primary/shared mailbox) or group conversation."""
    client = get_graph_client()
    if mailbox.startswith(_GROUP_PREFIX):
        group_id = _resolve_group_id(client, mailbox[len(_GROUP_PREFIX):])
        convo = client.get_group_conversation(group_id, message_id)
        if convo is None:
            return None
        return _conversation_to_fetched(message_id, convo)

    email = client.get_email_details(message_id, mailbox=mailbox)
    if email is None:
        return None
    return FetchedEmail(email=email)


def fetch_attachments(message_id: str, mailbox: str = "me") -> list[dict]:
    """Fetch attachments; for groups, aggregate across the conversation's posts."""
    client = get_graph_client()
    if not mailbox.startswith(_GROUP_PREFIX):
        return client.get_attachments(message_id, mailbox=mailbox)

    group_id = _resolve_group_id(client, mailbox[len(_GROUP_PREFIX):])
    convo = client.get_group_conversation(group_id, message_id)
    if convo is None:
        raise LookupError("message not found")
    attachments: list[dict] = []
    for post in convo["posts"]:
        if not post.get("hasAttachments"):
            continue
        for att in client.get_group_post_attachments(group_id, post["threadId"], post["id"]):
            att["postId"] = post["id"]
            attachments.append(att)
    return attachments
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fetching.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/fetching.py tests/test_fetching.py
git commit -m "feat: fetching service — mailbox-label policy for shared and group reads"
```

---

### Task 4: Router — `?mailbox=` param, posts, error mapping

**Files:**
- Modify: `api/routers/emails.py`
- Modify: `requirements-dev.txt` (add `httpx>=0.27` — FastAPI TestClient dependency)
- Test: `tests/test_emails_router.py`

**Interfaces:**
- Consumes: Task 3's `services.fetching` — `fetch_email(message_id, mailbox=)`, `fetch_attachments(message_id, mailbox=)`, `FetchedEmail`.
- Produces (HTTP): `GET /emails/{id}?mailbox=...` → `EmailDetailResponse` with new `posts: list[Post] | None`; `GET /emails/{id}/attachments?mailbox=...` → `AttachmentItem` gains `post_id: str | None`. Errors: `LookupError→404`, `RuntimeError→503`, `None→404`, `HTTPError 403→403`, other `HTTPError→502` (existing `ValueError→400` kept).

- [ ] **Step 1: Add httpx to dev requirements and install**

Append to `requirements-dev.txt`:

```
httpx>=0.27
```

Run: `pip install "httpx>=0.27"` (quotes required — `>` is a shell redirect otherwise)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_emails_router.py`:

```python
import pytest
import requests
from fastapi.testclient import TestClient

import services.fetching as fetching
from api.main import app
from clients.azure.email import Email

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch):
    """_verify_token reads SEARCH_TOKEN per request — unset it so tests skip auth."""
    monkeypatch.delenv("SEARCH_TOKEN", raising=False)


def _http_error(status: int) -> requests.exceptions.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"graph says no"
    return requests.exceptions.HTTPError(str(status), response=resp)


def _single() -> fetching.FetchedEmail:
    return fetching.FetchedEmail(email=Email({"id": "m1", "subject": "hello"}))


def _group() -> fetching.FetchedEmail:
    posts = [
        {
            "id": "p1",
            "threadId": "t1",
            "receivedDateTime": "2026-07-01T10:00:00Z",
            "body": {"contentType": "html", "content": "<p>hi</p>"},
            "from": {"emailAddress": {"name": "Ann", "address": "ann@x.com"}},
            "hasAttachments": False,
        }
    ]
    return fetching.FetchedEmail(
        email=Email({"id": "c1", "subject": "Lunch"}), posts=posts
    )


def test_get_email_defaults_to_me(monkeypatch):
    seen = {}

    def fake(message_id, mailbox="me"):
        seen["args"] = (message_id, mailbox)
        return _single()

    monkeypatch.setattr(fetching, "fetch_email", fake)
    resp = client.get("/emails/m1")
    assert resp.status_code == 200
    assert seen["args"] == ("m1", "me")
    body = resp.json()
    assert body["subject"] == "hello"
    assert body["posts"] is None


def test_get_email_passes_mailbox(monkeypatch):
    seen = {}

    def fake(message_id, mailbox="me"):
        seen["mailbox"] = mailbox
        return _single()

    monkeypatch.setattr(fetching, "fetch_email", fake)
    resp = client.get("/emails/m1", params={"mailbox": "team@x.com"})
    assert resp.status_code == 200
    assert seen["mailbox"] == "team@x.com"


def test_get_email_group_returns_posts(monkeypatch):
    monkeypatch.setattr(fetching, "fetch_email", lambda mid, mailbox="me": _group())
    resp = client.get("/emails/c1", params={"mailbox": "group:eng@x.com"})
    assert resp.status_code == 200
    posts = resp.json()["posts"]
    assert len(posts) == 1
    assert posts[0]["id"] == "p1"
    assert posts[0]["thread_id"] == "t1"
    assert posts[0]["sender_email"] == "ann@x.com"
    assert posts[0]["body"] == "<p>hi</p>"
    assert posts[0]["body_type"] == "html"


def test_get_email_none_is_404(monkeypatch):
    monkeypatch.setattr(fetching, "fetch_email", lambda mid, mailbox="me": None)
    assert client.get("/emails/gone").status_code == 404


def test_get_email_unknown_group_is_404(monkeypatch):
    def fake(mid, mailbox="me"):
        raise LookupError("unknown group: nope@x.com")

    monkeypatch.setattr(fetching, "fetch_email", fake)
    resp = client.get("/emails/c1", params={"mailbox": "group:nope@x.com"})
    assert resp.status_code == 404
    assert "unknown group" in resp.json()["detail"]


def test_get_email_403_maps_to_403(monkeypatch):
    def fake(mid, mailbox="me"):
        raise _http_error(403)

    monkeypatch.setattr(fetching, "fetch_email", fake)
    assert client.get("/emails/m1", params={"mailbox": "team@x.com"}).status_code == 403


def test_get_email_500_maps_to_502(monkeypatch):
    def fake(mid, mailbox="me"):
        raise _http_error(500)

    monkeypatch.setattr(fetching, "fetch_email", fake)
    assert client.get("/emails/m1").status_code == 502


def test_get_email_auth_failure_maps_to_503(monkeypatch):
    def fake(mid, mailbox="me"):
        raise RuntimeError("Graph API headless authentication failed")

    monkeypatch.setattr(fetching, "fetch_email", fake)
    assert client.get("/emails/m1").status_code == 503


def test_get_attachments_passes_mailbox_and_post_id(monkeypatch):
    def fake(message_id, mailbox="me"):
        return [{"id": "a1", "name": "f.pdf", "postId": "p1"}]

    monkeypatch.setattr(fetching, "fetch_attachments", fake)
    resp = client.get("/emails/c1/attachments", params={"mailbox": "group:eng@x.com"})
    assert resp.status_code == 200
    att = resp.json()["attachments"][0]
    assert att["post_id"] == "p1"


def test_get_attachments_no_post_id_for_plain_messages(monkeypatch):
    monkeypatch.setattr(
        fetching, "fetch_attachments", lambda mid, mailbox="me": [{"id": "a1", "name": "f.pdf"}]
    )
    att = client.get("/emails/m1/attachments").json()["attachments"][0]
    assert att["post_id"] is None


def test_get_attachments_lookup_error_is_404(monkeypatch):
    def fake(mid, mailbox="me"):
        raise LookupError("message not found")

    monkeypatch.setattr(fetching, "fetch_attachments", fake)
    assert client.get("/emails/gone/attachments").status_code == 404
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_emails_router.py -v`
Expected: FAIL — `posts` missing from response / 404s where 200 expected (router still calls the Graph client directly, ignoring the mocked service).

- [ ] **Step 4: Implement the router changes**

In `api/routers/emails.py`:

**(a)** Add the import (after the existing imports, line ~10):

```python
import services.fetching as fetching
```

**(b)** Add the `Post` model and extend `EmailDetailResponse` / `AttachmentItem` (replace the current `EmailDetailResponse` at lines 31–43 and `AttachmentItem` at lines 46–52):

```python
class Post(BaseModel):
    id: str | None = None
    thread_id: str | None = None  # needed to trace a post's attachments
    sender_name: str | None = None
    sender_email: str | None = None
    body: str | None = None
    body_type: str | None = None
    sent_at: datetime | str | None = None
    has_attachments: bool = False


class EmailDetailResponse(BaseModel):
    id: str | None = None
    subject: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    to: list[Recipient] = []
    cc: list[Recipient] = []
    received_at: datetime | str | None = None
    sent_at: datetime | str | None = None
    body: str | None = None
    body_type: str | None = None
    has_attachments: bool = False
    web_link: str | None = None
    posts: list[Post] | None = None  # group conversations only


class AttachmentItem(BaseModel):
    id: str | None = None
    name: str | None = None
    content_type: str | None = None
    size: int | None = None
    is_inline: bool = False
    content_bytes: str | None = None
    post_id: str | None = None  # group conversations only
```

**(c)** Extend `_call_graph` (lines 118–134) with two new mappings — the full function becomes:

```python
def _call_graph(fn, *args, **kwargs):
    """Invoke a Graph client write method, mapping failures to HTTPExceptions.

    Graph 403 (permission) surfaces as 403 with Graph's detail; other Graph errors
    as 502; client-side validation errors (e.g. attachment too large) as 400;
    not-found (LookupError) as 404; auth failure (RuntimeError) as 503.
    """
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail="authentication failed") from e
    except requests.exceptions.HTTPError as e:
        resp = e.response
        detail = resp.text[:1000] if resp is not None else str(e)
        status = resp.status_code if resp is not None else 502
        if status == 403:
            raise HTTPException(status_code=403, detail=detail) from e
        raise HTTPException(status_code=502, detail=detail) from e
```

**(d)** Add a `Post`-mapping helper and rewrite the two read endpoints (replace `get_email` at lines 147–167 and `get_attachments` at lines 170–187). `_get_client()` stays — the write endpoints still use it.

```python
def _post_model(p: dict) -> Post:
    addr = (p.get("from") or p.get("sender") or {}).get("emailAddress", {})
    body = p.get("body") or {}
    return Post(
        id=p.get("id"),
        thread_id=p.get("threadId"),
        sender_name=addr.get("name"),
        sender_email=addr.get("address"),
        body=body.get("content"),
        body_type=body.get("contentType"),
        sent_at=p.get("receivedDateTime"),
        has_attachments=p.get("hasAttachments", False),
    )


@router.get("/{message_id}", response_model=EmailDetailResponse)
def get_email(
    message_id: str, mailbox: str = "me", _: None = Depends(_verify_token)
) -> EmailDetailResponse:
    fetched = _call_graph(fetching.fetch_email, message_id, mailbox=mailbox)
    if fetched is None:
        raise HTTPException(status_code=404, detail="message not found")

    email = fetched.email
    return EmailDetailResponse(
        id=email.id,
        subject=email.subject,
        from_email=email.from_email,
        from_name=email.from_name,
        to=[Recipient(name=r.get("name"), address=r.get("address")) for r in email.to_recipients],
        cc=[Recipient(name=r.get("name"), address=r.get("address")) for r in email.cc_recipients],
        received_at=email.received_datetime,
        sent_at=email.sent_datetime,
        body=email.body_content,
        body_type=email.body_type,
        has_attachments=email.has_attachments,
        web_link=email.web_link,
        posts=[_post_model(p) for p in fetched.posts] if fetched.posts is not None else None,
    )


@router.get("/{message_id}/attachments", response_model=AttachmentsResponse)
def get_attachments(
    message_id: str, mailbox: str = "me", _: None = Depends(_verify_token)
) -> AttachmentsResponse:
    raw = _call_graph(fetching.fetch_attachments, message_id, mailbox=mailbox)

    return AttachmentsResponse(
        attachments=[
            AttachmentItem(
                id=a.get("id"),
                name=a.get("name"),
                content_type=a.get("contentType"),
                size=a.get("size"),
                is_inline=a.get("isInline", False),
                content_bytes=a.get("contentBytes"),
                post_id=a.get("postId"),
            )
            for a in raw
        ]
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_emails_router.py -v`
Expected: all PASS.

- [ ] **Step 6: Full suite + lint, then commit**

Run: `python -m pytest tests/ -q` — Expected: all pass.
Run: `ruff check api/ services/ clients/ tests/` — Expected: clean (fix anything it flags).

```bash
git add api/routers/emails.py requirements-dev.txt tests/test_emails_router.py
git commit -m "feat: read API accepts ?mailbox= for shared mailboxes and group conversations"
```

---

### Task 5: Update the `fetching-inbox-email` skill

**Files:**
- Modify: `/Users/ben/.claude/skills/fetching-inbox-email/SKILL.md` (outside the repo — no git commit for this file)

- [ ] **Step 1: Add mailbox usage to the skill**

In `/Users/ben/.claude/skills/fetching-inbox-email/SKILL.md`:

**(a)** Bump `version: 1.0.0` → `version: 1.1.0`.

**(b)** In the **Prerequisites** section, replace the existing paragraph with:

```markdown
You need a Graph message ID **and its mailbox label**. If you don't have them yet, use the **searching-inbox-emails** skill — each search result has a `message_id` and a `mailbox` field (`"me"`, a shared-mailbox address, or `"group:..."`). Pass both: fetching a shared-mailbox or group result without its `mailbox` label returns 404.
```

**(c)** Replace the "Fetch full email detail" curl with the `-G`/`--data-urlencode` form so the mailbox label is URL-encoded:

```bash
curl -s -G "https://inbox-api-aizbgjlava-uc.a.run.app/emails/<message_id>" \
  --data-urlencode "mailbox=<mailbox label from search result>" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Omit `--data-urlencode "mailbox=..."` (or pass `mailbox=me`) for primary-mailbox messages.

**(d)** Add to the response-fields table:

```markdown
| `posts` | Group conversations only: full thread as a list of posts (`id`, `thread_id`, `sender_name`, `sender_email`, `body`, `body_type`, `sent_at`, `has_attachments`), oldest first. `null` for regular messages. For groups, the top-level `body`/`from_*` mirror the latest post and `web_link` is `null`. |
```

**(e)** Apply the same `-G --data-urlencode "mailbox=..."` form to the attachments curl, and note that group attachment items include a `post_id` tracing the attachment to its post. Also note the error additions: `403` means the token lacks rights to that shared mailbox; `404 "unknown group: ..."` means the group label isn't among the account's memberships.

- [ ] **Step 2: Verify**

Read the edited SKILL.md end-to-end once: every curl example must include the auth header, and the mailbox label flows from search result → fetch command. No repo commit — the file lives outside the repo.

---

### Task 6: End-to-end verification + PR

**Files:** none new.

- [ ] **Step 1: Full suite green**

Run: `python -m pytest tests/ -q` — Expected: all pass, no warnings that indicate broken imports.

- [ ] **Step 2: Live verification against local API**

Requires the local interactive token cache (`~/.inbox-token-cache.json`). Do not set `GCP_PROJECT_ID`. `SEARCH_TOKEN` unset → API auth disabled locally.

```bash
source .venv/bin/activate
uvicorn api.main:app --port 8123 &   # run in background
sleep 3
# 1) find a shared-mailbox and a group result
curl -s -X POST localhost:8123/search -H 'Content-Type: application/json' \
  -d '{"query": "invoice"}' | python3 -m json.tool
```

Pick from the output one result with `mailbox` = a shared address and one with `mailbox` = `group:...` (adjust the query if needed to get hits in each), then:

```bash
# 2) shared-mailbox fetch — expect 200 with full body, posts=null
curl -s -G "localhost:8123/emails/<shared_message_id>" \
  --data-urlencode "mailbox=<shared address>" | python3 -m json.tool
# 3) group conversation fetch — expect 200 with posts[] populated
curl -s -G "localhost:8123/emails/<group_conversation_id>" \
  --data-urlencode "mailbox=<group:label>" | python3 -m json.tool
# 4) attachments for each (group one should show post_id when present)
curl -s -G "localhost:8123/emails/<id>/attachments" \
  --data-urlencode "mailbox=<label>" | python3 -m json.tool
# 5) regression: a primary-mailbox fetch with no mailbox param still works
curl -s "localhost:8123/emails/<primary_message_id>" | python3 -m json.tool
kill %1
```

Expected: all four fetches return 200 with correct content; the no-param fetch is byte-compatible with the old response apart from the new `posts: null` field.

- [ ] **Step 3: Open the PR**

Use the `/pr-open` skill (per CLAUDE.md — do not hand-roll `git push` + `gh pr create`). The branch `feat/shared-group-read` already carries the commits; include the spec (`docs/superpowers/specs/2026-07-02-shared-mailbox-group-read-design.md`) and this plan in the PR if not already committed:

```bash
git add docs/superpowers/specs/2026-07-02-shared-mailbox-group-read-design.md \
        docs/superpowers/plans/2026-07-02-shared-mailbox-group-read.md
git commit -m "docs: spec + plan for shared-mailbox and group read support"
```

PR description should call out: the `?mailbox=` contract, the group `posts[]` shape, the client error-contract change (404-vs-raise) and its audited callers, and that no new Graph scopes are needed.
