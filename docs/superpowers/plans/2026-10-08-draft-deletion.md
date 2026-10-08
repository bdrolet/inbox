# Draft Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `DELETE /emails/drafts/{draft_id}` to inbox-api. It deletes only messages that Graph reports as drafts. Update `email-builder` and `sending-inbox-email` so superseded drafts get cleaned up.

**Architecture:** A new `GraphEmailClient.delete_draft` method checks `isDraft` and then issues a soft DELETE. It raises `LookupError` (→ 404) or a new `NotADraftError` (→ 409). A thin FastAPI route in `api/routers/emails.py` resolves the `from` block like the other draft endpoints. The agent and skill markdown change so a revision deletes the draft it replaces.

**Tech Stack:** Python 3.13, FastAPI, `requests`, Microsoft Graph v1.0, pytest with monkeypatch and FastAPI `TestClient`.

**Spec:** `docs/superpowers/specs/2026-10-08-draft-deletion-design.md`

## Global Constraints

- Delete only when Graph returns `isDraft is True`. False, missing or null → `NotADraftError`, and **no DELETE request is sent**.
- Use the soft `DELETE {base}/messages/{id}` and never `permanentDelete`.
- Mailbox resolution goes through the existing `_mailbox_base(from_address, from_shared)` unchanged: alias/group → `/me`, shared → `/users/{addr}`.
- Message ids are URL-quoted with `quote(id, safe='')` in both Graph URLs.
- The route is `DELETE /emails/drafts/{draft_id:path}`, with optional body `{"from": {...}}` (same shape as `SendDraftRequest`). The response is `StatusResponse(status="deleted")`.
- Status mapping: 404 not found or vanished, 409 not a draft, 403 Graph permission, 503 auth, 502 other Graph errors.
- `NotADraftError` subclasses `Exception` directly, not `ValueError` or `LookupError`.
- No new Graph scope, no new metric.
- Do not add a `Co-Authored-By` trailer to commits (Ben's `/pr-open` rule).

## Review Focus

1. **A received or sent message id passed to DELETE.** Expect 409 with no DELETE call to Graph. Pinned in Task 1 (`isDraft: false` asserts no DELETE call) and Task 2 (409 mapping).
2. **`isDraft` absent from the Graph response**, for example a select that is dropped. Expect fail-closed 409. Pinned in Task 1.
3. **A Graph id containing `/`, `+` and `=`.** Expect the router to receive the decoded id and the client to quote it in both URLs. Pinned in Task 1 (quoting) and Task 2 (`%2F` route).
4. **A shared-mailbox draft deleted with or without its `from` block.** Expect `/users/{addr}` for both calls when shared, and `/me` (therefore 404 rather than a wrong delete) when omitted. Pinned in Task 1 (shared base) and Task 2 (body forwarded, no body → primary).
5. **The draft vanishing between the GET and the DELETE.** Expect 404, not 502. Pinned in Task 1 (DELETE 404 → `LookupError`).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `clients/azure/graph_email_client.py` | Graph I/O | add `NotADraftError` and `delete_draft` |
| `tests/test_graph_drafts.py` | client tests for draft deletion | create |
| `api/routers/emails.py` | HTTP transport | add `DeleteDraftRequest`, a 409 branch in `_call_graph`, and the route |
| `tests/test_emails_router.py` | route tests | append |
| `.claude/agents/email-builder.md` | agent instructions | step 5 replace-and-delete, `Replaced:` output line |
| `.claude/skills/sending-inbox-email/SKILL.md` | dispatcher skill | v1.2.0, discard option, "Delete a draft" section |

---

### Task 1: `GraphEmailClient.delete_draft` with the isDraft guard

**Files:**
- Modify: `clients/azure/graph_email_client.py` (add the exception near the top-level imports and classes, and the method after `send_draft`, around line 737)
- Create: `tests/test_graph_drafts.py`

**Interfaces:**
- Produces: `class NotADraftError(Exception)` in `clients.azure.graph_email_client`
- Produces: `GraphEmailClient.delete_draft(self, message_id: str, *, from_address: str | None = None, from_shared: bool = False) -> None`. It raises `LookupError` (missing), `NotADraftError` (not a draft) or `requests.HTTPError` (other Graph errors).

- [ ] **Step 1: Write the failing tests** in `tests/test_graph_drafts.py`:

```python
import pytest

import clients.azure.graph_email_client as graph_email_client
from clients.azure.graph_email_client import GraphEmailClient, NotADraftError

GRAPH = "https://graph.microsoft.com/v1.0"


def _client():
    c = GraphEmailClient.__new__(GraphEmailClient)
    c.access_token = "tok"
    c.graph_endpoint = GRAPH
    return c


class _Resp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


def _fake_graph(monkeypatch, *, get_status=200, get_json=None, delete_status=204):
    seen = {"get": [], "delete": []}

    def fake_get(url, headers=None, params=None):
        seen["get"].append((url, params))
        return _Resp(
            get_status, get_json if get_json is not None else {"id": "d1", "isDraft": True}
        )

    def fake_delete(url, headers=None):
        seen["delete"].append(url)
        return _Resp(delete_status)

    monkeypatch.setattr(graph_email_client.requests, "get", fake_get)
    monkeypatch.setattr(graph_email_client.requests, "delete", fake_delete)
    return seen


def test_delete_draft_checks_is_draft_then_deletes(monkeypatch):
    seen = _fake_graph(monkeypatch)
    assert _client().delete_draft("d1") is None
    assert seen["get"] == [(f"{GRAPH}/me/messages/d1", {"$select": "id,isDraft"})]
    assert seen["delete"] == [f"{GRAPH}/me/messages/d1"]


def test_delete_draft_refuses_non_draft_without_deleting(monkeypatch):
    seen = _fake_graph(monkeypatch, get_json={"id": "m1", "isDraft": False})
    with pytest.raises(NotADraftError):
        _client().delete_draft("m1")
    assert seen["delete"] == []


def test_delete_draft_fails_closed_when_is_draft_missing(monkeypatch):
    seen = _fake_graph(monkeypatch, get_json={"id": "m1"})
    with pytest.raises(NotADraftError):
        _client().delete_draft("m1")
    assert seen["delete"] == []


def test_delete_draft_missing_message_is_lookup_error(monkeypatch):
    seen = _fake_graph(monkeypatch, get_status=404)
    with pytest.raises(LookupError):
        _client().delete_draft("gone")
    assert seen["delete"] == []


def test_delete_draft_vanished_before_delete_is_lookup_error(monkeypatch):
    _fake_graph(monkeypatch, delete_status=404)
    with pytest.raises(LookupError):
        _client().delete_draft("d1")


def test_delete_draft_other_delete_error_raises(monkeypatch):
    _fake_graph(monkeypatch, delete_status=500)
    with pytest.raises(RuntimeError):
        _client().delete_draft("d1")


def test_delete_draft_shared_mailbox_targets_that_mailbox(monkeypatch):
    seen = _fake_graph(monkeypatch)
    _client().delete_draft("d1", from_address="shared@x.com", from_shared=True)
    assert seen["get"][0][0] == f"{GRAPH}/users/shared@x.com/messages/d1"
    assert seen["delete"] == [f"{GRAPH}/users/shared@x.com/messages/d1"]


def test_delete_draft_alias_uses_primary_mailbox(monkeypatch):
    seen = _fake_graph(monkeypatch)
    _client().delete_draft("d1", from_address="ben@drolet.ai")
    assert seen["delete"] == [f"{GRAPH}/me/messages/d1"]


def test_delete_draft_quotes_id_in_both_urls(monkeypatch):
    seen = _fake_graph(monkeypatch)
    _client().delete_draft("AA/B+C=")
    assert seen["get"][0][0] == f"{GRAPH}/me/messages/AA%2FB%2BC%3D"
    assert seen["delete"] == [f"{GRAPH}/me/messages/AA%2FB%2BC%3D"]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `../../../.venv/bin/pytest tests/test_graph_drafts.py -q`
Expected: collection error, `ImportError: cannot import name 'NotADraftError'`.

- [ ] **Step 3: Implement.** Add the exception at module level in `clients/azure/graph_email_client.py`, above `class GraphEmailClient`:

```python
class NotADraftError(Exception):
    """Refused to delete a message whose Graph isDraft is not true."""
```

Add the method after `send_draft`:

```python
    def delete_draft(
        self,
        message_id: str,
        *,
        from_address: str | None = None,
        from_shared: bool = False,
    ) -> None:
        """Delete a draft by id; refuses anything Graph doesn't report as a draft.

        Soft delete: the draft moves to Recoverable Items (not Deleted Items;
        not permanentDelete), restorable in Outlook until retention purges it.
        Requires Mail.ReadWrite.

        Raises:
            LookupError: no such message in the resolved mailbox (Graph 404).
            NotADraftError: the message exists but isDraft is not true.
            requests.HTTPError: any other Graph error.
        """
        base = self._mailbox_base(from_address, from_shared)
        url = f"{self.graph_endpoint}{base}/messages/{quote(message_id, safe='')}"
        check = requests.get(url, headers=self.get_headers(), params={"$select": "id,isDraft"})
        if check.status_code == 404:
            raise LookupError("draft not found")
        check.raise_for_status()
        if check.json().get("isDraft") is not True:
            logger.info("Refused to delete non-draft %s (base=%s)", message_id, base)
            raise NotADraftError("message is not a draft; refusing to delete")

        response = requests.delete(url, headers=self.get_headers())
        if response.status_code == 404:
            raise LookupError("draft not found")
        response.raise_for_status()
        logger.info("Deleted draft %s (base=%s)", message_id, base)
```

(`quote` is already imported in this module because `reply` uses it.)

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `../../../.venv/bin/pytest tests/test_graph_drafts.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add clients/azure/graph_email_client.py tests/test_graph_drafts.py
git commit -m "feat(graph): delete_draft with isDraft guard"
```

---

### Task 2: `DELETE /emails/drafts/{draft_id}` route

**Files:**
- Modify: `api/routers/emails.py`: request model (next to `SendDraftRequest`), `_call_graph` (new branch before `except ValueError`), route (after `send_draft`)
- Modify: `tests/test_emails_router.py` (append)

**Interfaces:**
- Consumes: `GraphEmailClient.delete_draft(message_id, *, from_address, from_shared) -> None` and `NotADraftError` from Task 1
- Produces: HTTP `DELETE /emails/drafts/{draft_id:path}` → `{"status": "deleted"}`

- [ ] **Step 1: Write the failing tests** and append them to `tests/test_emails_router.py`. They reuse the file's `client`, `_http_error` and `_use_client`. Starlette's `TestClient.delete` takes no `json=`, so bodies go through `client.request`.

```python
from clients.azure.graph_email_client import NotADraftError


class _FakeDeleteClient:
    def __init__(self, exc=None):
        self.exc, self.kwargs = exc, None

    def delete_draft(self, message_id, **kwargs):
        self.kwargs = {"message_id": message_id, **kwargs}
        if self.exc:
            raise self.exc


def test_delete_draft_without_body_uses_primary(monkeypatch):
    fake = _use_client(monkeypatch, _FakeDeleteClient())
    resp = client.delete("/emails/drafts/d1")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    assert fake.kwargs == {"message_id": "d1", "from_address": None, "from_shared": False}


def test_delete_draft_forwards_from_block(monkeypatch):
    fake = _use_client(monkeypatch, _FakeDeleteClient())
    resp = client.request(
        "DELETE",
        "/emails/drafts/d1",
        json={"from": {"address": "shared@x.com", "shared": True}},
    )
    assert resp.status_code == 200
    assert fake.kwargs == {"message_id": "d1", "from_address": "shared@x.com", "from_shared": True}


def test_delete_draft_accepts_ids_containing_slash(monkeypatch):
    fake = _use_client(monkeypatch, _FakeDeleteClient())
    assert client.delete("/emails/drafts/AA%2FBB%3D").status_code == 200
    assert fake.kwargs["message_id"] == "AA/BB="


def test_delete_draft_missing_is_404(monkeypatch):
    _use_client(monkeypatch, _FakeDeleteClient(exc=LookupError("draft not found")))
    resp = client.delete("/emails/drafts/gone")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "draft not found"


def test_delete_draft_non_draft_is_409(monkeypatch):
    _use_client(
        monkeypatch,
        _FakeDeleteClient(exc=NotADraftError("message is not a draft; refusing to delete")),
    )
    resp = client.delete("/emails/drafts/m1")
    assert resp.status_code == 409
    assert "not a draft" in resp.json()["detail"]


def test_delete_draft_403_maps_to_403(monkeypatch):
    _use_client(monkeypatch, _FakeDeleteClient(exc=_http_error(403)))
    assert client.delete("/emails/drafts/d1").status_code == 403


def test_delete_draft_other_graph_error_is_502(monkeypatch):
    _use_client(monkeypatch, _FakeDeleteClient(exc=_http_error(500)))
    assert client.delete("/emails/drafts/d1").status_code == 502
```

(Put the `NotADraftError` import with the file's other imports at the top.)

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `../../../.venv/bin/pytest tests/test_emails_router.py -q -k delete_draft`
Expected: 7 failed with a 404 or 405 status. Either is the route-missing symptom.

- [ ] **Step 3: Implement** in `api/routers/emails.py`.

Change the imports:

```python
import services.fetching as fetching
from clients.azure.graph_email_client import NotADraftError
```

Add the request model after `SendDraftRequest`:

```python
class DeleteDraftRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: FromMailbox | None = Field(default=None, alias="from")
```

In `_call_graph`, add `not a draft (NotADraftError) as 409;` to the docstring, and add this branch as the first `except`:

```python
    except NotADraftError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
```

Add the route after `send_draft`:

```python
# `:path` because Graph ids can contain "/", which arrives decoded from %2F
@router.delete("/drafts/{draft_id:path}", response_model=StatusResponse)
def delete_draft(draft_id: str, req: DeleteDraftRequest | None = None) -> StatusResponse:
    client = _get_client()
    addr, shared = _from_parts(req.from_ if req else None)
    _call_graph(client.delete_draft, draft_id, from_address=addr, from_shared=shared)
    return StatusResponse(status="deleted")
```

Check: `api/routers/emails.py` must not import `clients.azure` at module top if that triggers MSAL setup at import time. `_get_client` imports lazily for that reason. If importing `NotADraftError` at the top breaks app import, check with `../../../.venv/bin/python -c "import api.main"`. If it does break, import it inside `_call_graph` instead. `clients/azure/graph_email_client.py` defines no import-time side effects beyond `msal`/`requests` imports, so a top-level import is expected to be fine.

- [ ] **Step 4: Run the full suite and confirm it passes**

Run: `../../../.venv/bin/pytest tests/ -q`
Expected: all pass, including the 7 new route tests and 9 client tests.

- [ ] **Step 5: Commit**

```bash
git add api/routers/emails.py tests/test_emails_router.py
git commit -m "feat(api): DELETE /emails/drafts/{id}, 409 for non-drafts"
```

---

### Task 3: email-builder and sending-inbox-email clean up superseded drafts

**Files:**
- Modify: `.claude/agents/email-builder.md` (the "Flags" bullet for `draft_id`, step 5's `draft_id` paragraph, Output section)
- Modify: `.claude/skills/sending-inbox-email/SKILL.md` (version, Relay bullets, new section, Notes)

**Interfaces:**
- Consumes: `DELETE /emails/drafts/{id}` with optional `{"from": ...}`: 200 `{"status":"deleted"}`, 404, 409 (Task 2)

- [ ] **Step 1: In `email-builder.md`, Inputs → Flags**, replace the `draft_id` bullet with:

```markdown
- `draft_id: <id>` (with the `from` block it was created with, if any) means
  revise that existing draft: build a replacement, then delete the old one
  (see step 5).
```

- [ ] **Step 2: In `email-builder.md` step 5**, replace the paragraph that begins "When the dispatch carries `draft_id`, the API has no draft-edit endpoint…" with:

```markdown
When the dispatch carries `draft_id`, revise by replacement. There is no
draft-edit endpoint, by design, because it would mangle a reply's quoted
history. Order matters:

1. Build the new draft completely: create it, then add every attachment.
2. Only then delete the old one: `DELETE /emails/drafts/{enc(draft_id)}`
   with the `from` block **the old draft was created with**. The dispatch
   passes it alongside `draft_id`, and it is usually the same identity as
   this draft. A body of `{}` means primary.
3. Add a `Replaced:` line to the report, chosen by the delete's status:
   - 200: `Replaced: <old id> (deleted)`
   - 404: `Replaced: <old id> (already gone)`. This is not a failure.
   - 409: `Replaced: <old id> (NOT deleted: not a draft, it may already
     have been sent)`. Put this at the top of the report as well, because
     Ben needs to know.
   - anything else: `Replaced: <old id> (NOT deleted: <status> <body>)`.
     The new draft is still the result, and the run did not fail.

Never delete a draft you weren't handed as `draft_id`. Never delete when the
run ends in `RECIPIENT_UNRESOLVED`, `AMBIGUOUS` or a failure, because nothing
replaced the old draft. A partial draft left by a failure (for example a
missing attachment) is not deleted either. Report it, as below.
```

- [ ] **Step 3: In `email-builder.md` Output**, in the **Drafted** block add a line directly after `Draft id: <id>`:

```
Replaced: <old id> (<deleted | already gone | NOT deleted: reason>)   ← only when revising
```

The **Sent** shape says "same shape", so it inherits the line.

- [ ] **Step 4: In `sending-inbox-email/SKILL.md`**, set `version: 1.2.0`. Then replace the `DRAFTED` relay bullet list with:

```markdown
- **`DRAFTED`**: show from/to/cc/subject/attachments and the full body, plus
  any `Assumptions`, then ask: *send it, change it, leave it in Drafts, or
  discard it?*
  - *send*: send the draft yourself (`POST /emails/drafts/{id}/send`, below,
    with the same `from` block). No re-dispatch.
  - *change*: re-dispatch with `draft_id`, the `from` block it was created
    with, and the changes. The agent builds a new draft and deletes the old
    one. Relay its `Replaced:` line. On `NOT deleted`, give Ben the old id
    and say it is still in Drafts.
  - *leave it*: done; give the `web_link`.
  - *discard*: delete it yourself (**Delete a draft**, below). No
    re-dispatch.
```

- [ ] **Step 5: In `sending-inbox-email/SKILL.md`**, add this section directly after "Draft, then send":

````markdown
## Delete a draft

```bash
ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$DRAFT_ID")
curl -s -XDELETE "$BASE/emails/drafts/$ENC" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{}'     # or the draft's "from" block
# -> {"status":"deleted"}   404 = already gone   409 = not a draft, nothing deleted
```

Pass the `from` block the draft was created with. A shared-mailbox draft
deleted without it returns 404, because the lookup runs in the wrong
mailbox. Deleting a draft Ben said to discard or replace needs no further
confirmation. The endpoint refuses (409) anything that isn't a draft, so it
cannot delete received or sent mail.
````

- [ ] **Step 6: In `sending-inbox-email/SKILL.md` Notes**, add:

```markdown
- Deleted drafts skip Deleted Items. They go to Recoverable Items, which Outlook reaches via Deleted Items → "Recover items deleted from this folder", until retention purges them.
```

- [ ] **Step 7: Verify the edits.** Run `grep -n "no draft-edit endpoint\|until Ben deletes" .claude/agents/email-builder.md`. Expected: no matches, since the stale text is gone. Run `grep -n "1.2.0\|Delete a draft\|discard" .claude/skills/sending-inbox-email/SKILL.md`. Expected: all three present.

- [ ] **Step 8: Commit**

```bash
git add .claude/agents/email-builder.md .claude/skills/sending-inbox-email/SKILL.md
git commit -m "feat(email-builder): delete superseded drafts; skill gains discard"
```

---

### Task 4: Live verification (after deploy, for Ben or with his OK)

This writes to the real mailbox, so it is not part of automated execution. Run it after the inbox-api deploy from `main` and record the results on the PR. Use the shared-mailbox address Ben chooses.

- [ ] **Step 1:** Create a draft with `POST /emails/drafts` (to self, subject `delete-test`). Then call `DELETE /emails/drafts/{enc(id)}` and expect `{"status":"deleted"}`. Confirm the draft is in **Recoverable Items** (`recoverableitemsdeletions`) and not purged (spec §3.4). Done 2026-10-08: confirmed.
- [ ] **Step 2:** Pick a received message id from `searching-inbox-emails` and call DELETE on it. Expect **409**, and confirm the message is still in place.
- [ ] **Step 3:** Repeat step 1's DELETE on the same id. Expect **404**.
- [ ] **Step 4:** Repeat step 1 with a shared-mailbox `from` on both the create and the delete. Expect deletion from that mailbox's Drafts.
