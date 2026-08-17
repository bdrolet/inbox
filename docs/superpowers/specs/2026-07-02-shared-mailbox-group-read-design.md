# Shared-Mailbox & M365 Group Read Support — Design

**Date:** 2026-07-02
**Status:** Approved

## Problem

The read API (`GET /emails/{id}`, `GET /emails/{id}/attachments`) is hardcoded to
`/me/messages/...`, so it can only fetch messages from the primary mailbox. But
`POST /search` already fans out across the primary mailbox, `SHARED_MAILBOXES`,
and M365 group conversations — returning results the read API cannot open:

- A shared-mailbox `message_id` 404s (wrong mailbox path).
- A group result's `message_id` is a **conversation ID**, which needs a completely
  different Graph shape (`/groups/{gid}/conversations/{cid}/threads/{tid}/posts`).

Search results already carry the routing information in their `mailbox` field:
`"me"`, a shared-mailbox address, or `"group:{mail-or-id}"`.

## Decisions (approved)

1. **Caller passes the mailbox.** The read endpoints gain an optional
   `?mailbox=` query param taking exactly the label search returns. Default
   `"me"` — existing callers unchanged. No server-side fallback search.
2. **Group fetch returns the full thread.** All posts of the conversation, not
   just the latest.
3. **Service layer.** A new `services/fetching.py` owns the mailbox-label
   policy, mirroring the `services/sending.py` planned in
   `docs/superpowers/plans/2026-06-26-dumb-from-shared-mailbox-config.md`.
   The router stays HTTP-only; the Graph client stays I/O-only.

## Architecture

```
search result (message_id, mailbox)
        │
        ▼
api/routers/emails.py        HTTP only: query param, error mapping, serialization
        │
        ▼
services/fetching.py         Policy: parse mailbox label, resolve group mail→id,
        │                    assemble conversation, aggregate attachments
        ▼
clients/azure/graph_email_client.py    I/O: /me vs /users/{addr} vs /groups/... paths
```

### Graph client (`clients/azure/graph_email_client.py`) — I/O only

- `get_email_details(email_id, mailbox="me")` — new `mailbox` param; builds
  `/me/messages/{id}` or `/users/{addr}/messages/{id}`, mirroring
  `search_emails`.
- `get_attachments(message_id, mailbox="me")` — same pattern.
- New `get_group_conversation(group_id, conversation_id)` — GET
  `/groups/{gid}/conversations/{cid}` (`$select=topic,lastDeliveredDateTime`),
  then `/threads` (`$select=id`), then each thread's `/posts`
  (`$select=id,body,from,sender,receivedDateTime,hasAttachments`). Returns
  `{"topic": ..., "lastDeliveredDateTime": ..., "posts": [...]}` with each raw
  post dict annotated with its `threadId`; `None` on 404.
- New `get_group_post_attachments(group_id, thread_id, post_id)` — GET
  `/groups/{gid}/threads/{tid}/posts/{pid}/attachments`, raw dicts.

**Error-behavior change (targeted fix):** today `get_email_details` swallows
every `RequestException` and returns `None`, and `get_attachments` returns `[]`
— so a Graph 403 (e.g. missing shared-mailbox permission, the most likely
failure when wiring a new mailbox) masquerades as "not found" / "no
attachments". New behavior for these read methods and the two new group
methods: return `None` (or raise `LookupError` where `None` isn't in the
signature) **only on 404**; raise `requests.HTTPError` otherwise.

Caller impact (audited):
- `services/calendar_invite.py:24-28` — already wraps in `try/except Exception`. Safe.
- `scripts/backfill_embeddings.py:96` — already in a `try`. Safe.
- `services/ingestion.py:10` (`fetch`, used by the pipeline) — would newly
  propagate exceptions. **Preserve pipeline behavior**: catch
  `RequestException` there, log, return `None`.

### Fetching service (`services/fetching.py`) — new

Owns the mailbox-label policy. Acquires its client via the existing
`clients.graph.get_graph_client()` (raises `RuntimeError` on auth failure).

- Label parsing: `"me"` → primary; `"group:{value}"` → group; anything else →
  shared/user mailbox address. Private helper.
- Group resolution: match `{value}` against `client.get_member_groups()` by
  mail (case-insensitive) or id. No match → `LookupError("unknown group: ...")`.
  One extra Graph call, group fetches only.
- `fetch_email(message_id, mailbox="me") -> FetchedEmail | None`
  - Mailbox case: `Email` from `get_email_details`, `posts=None`.
  - Group case: synthetic `Email` (same technique as
    `search_group_conversations`) + `posts` list. Posts sorted oldest-first.
    Top-level `subject` = conversation `topic`; `received_at` = conversation
    `lastDeliveredDateTime`; top-level `body`/`from`/`sent_at` mirror the
    **latest** post (parity with search's preview); `has_attachments` = any
    post has one; `web_link` = None (Graph has none for posts).
  - `FetchedEmail` is a small frozen dataclass local to the service:
    `email: Email`, `posts: list[dict] | None`.
- `fetch_attachments(message_id, mailbox="me") -> list[dict]`
  - Mailbox case: passthrough to `get_attachments`.
  - Group case: resolve group → list posts → fetch attachments for each post
    with `hasAttachments`; annotate each dict with `postId`.

### Router (`api/routers/emails.py`) — HTTP only

- `GET /emails/{id}` and `GET /emails/{id}/attachments` gain
  `mailbox: str = "me"` query param.
- Both call the service through `_call_graph`, which gains two mappings:
  `LookupError → 404` (unknown group) and `RuntimeError → 503` (auth failure —
  pre-aligns with the dumb-`from` plan's Task 3). Existing mappings stay:
  `ValueError → 400`, `HTTPError 403 → 403`, other `HTTPError → 502`.
  `None` from the service → 404 "message not found" (existing behavior).
- `_get_client()` stays — the write paths still use it until the dumb-`from`
  plan lands. The read paths simply stop using it (the service acquires its
  own client).

## API contract

### Request

```
GET /emails/{message_id}?mailbox=me                     (default; unchanged)
GET /emails/{message_id}?mailbox=team@drolet.cloud       (shared mailbox)
GET /emails/{conversation_id}?mailbox=group:eng@drolet.cloud   (group conversation)
GET /emails/{id}/attachments?mailbox=...                 (same three forms)
```

### Response models

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
```

- `EmailDetailResponse` gains `posts: list[Post] | None = None` — populated
  only for group conversations; `None` for regular messages (no change for
  existing consumers).
- `AttachmentItem` gains `post_id: str | None = None` — set only for group
  attachments.

### Errors

| Failure | Response |
|---|---|
| Auth failure (`RuntimeError` from `get_graph_client`) | 503 |
| Message/conversation not found (Graph 404) | 404 "message not found" |
| `group:` label not in the user's `memberOf` | 404 "unknown group: ..." |
| Graph 403 (e.g. shared mailbox without permission) | 403 with Graph's detail |
| Other Graph errors | 502 |

## Permissions

No new Graph scopes. Search already reads shared mailboxes
(`Mail.Read.Shared`) and group conversations (`Group.Read.All`) with the
current token.

## Testing

- **Unit — `services/fetching.py`** (the bulk): fake Graph client. Label
  parsing (`me` / address / `group:mail` / `group:id`); group resolution
  (case-insensitive mail, id, unknown → `LookupError`); conversation assembly
  (post order, top-level mirrors latest post); attachment aggregation with
  `post_id`; not-found paths.
- **Unit — router**: FastAPI `TestClient`, service mocked. `?mailbox=`
  defaults to `"me"`; error mapping 404/403/502/503; `posts` absent for
  regular messages.
- **Unit — `services/ingestion.fetch`**: client exception → caught, logged,
  returns `None`.
- **Manual (before PR)**: against the live API, fetch a known shared-mailbox
  message and a group conversation found via `/search`, plus attachments for
  each.

## Scope

- **In:** everything above, plus updating the `fetching-inbox-email` skill
  (`~/.claude/skills/fetching-inbox-email`) to pass `mailbox` from search
  results.
- **Out (YAGNI):** fetching individual group posts by ID; replying to group
  conversations; paginating very long threads (fetch all posts — group threads
  here are small); DB-mode fetch.
- **Ordering vs. the dumb-`from` plan
  (`2026-06-26-dumb-from-shared-mailbox-config.md`):** independent — no
  `config.py` needed (caller supplies the mailbox). Both touch `emails.py` and
  `services/`; whichever lands second rebases trivially.
