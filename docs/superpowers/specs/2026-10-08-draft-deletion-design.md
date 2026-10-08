# Draft Deletion — Design

**Date:** 2026-10-08
**Status:** Draft spec, awaiting review. Implementation comes later.
**Asana:** [P2] Spec draft deletion for inbox-api (`1219334204676058`)

## 1. Summary

inbox-api can create a draft, attach files to it and send it, but it cannot
delete one. On 2026-10-08 the `email-builder` agent wrote four revisions of
one email to Cheryl and sent the last. Each revision was a new draft, because
the API has no edit endpoint either. The agent tried to clean up the three
superseded drafts with `DELETE /emails/drafts/{id}` and got 404s, since no
such route exists. Ben deleted them by hand in Outlook.

This spec adds:

1. **`DELETE /emails/drafts/{draft_id}`**, which deletes a draft only after it
   has confirmed through Graph that the message is a draft (`isDraft`). The
   endpoint therefore cannot delete a received or sent message.
2. **Agent and skill changes.** When `email-builder` replaces a draft, it
   deletes the old one. `sending-inbox-email` gains a direct "discard this
   draft" path.

It also settles the open question: revisions **do not** move to in-place
`PATCH` updates (§5).

## 2. Goals and non-goals

**Goals**

1. A superseded draft never needs a manual cleanup in Outlook.
2. No input to the endpoint can delete a message that is not a draft.
3. Mailbox targeting matches the other draft endpoints exactly, so a caller
   sends the same `from` block it used to create the draft.

**Non-goals**

- Permanent deletion. Deleted drafts go to Recoverable Items, where they can
  be recovered until retention purges them (§3.4).
- Deleting received or sent mail, or deleting in bulk.
- A draft-edit (`PATCH`) endpoint (§5).
- Removing attachments from a draft.

## 3. API: `DELETE /emails/drafts/{draft_id}`

### 3.1 Request

```
DELETE /emails/drafts/{draft_id}
Content-Type: application/json            (optional body)

{"from": {"address": "shared@drolet.cloud", "shared": true}}
```

- `draft_id` is the id returned by `POST /emails/drafts` or by
  `POST /emails/{id}/reply` with `"send": false`, URL-encoded like every other
  path id. Declare the route as `/drafts/{draft_id:path}`, as `reply` does with
  `{message_id:path}`, because Graph ids can contain `/` and that character
  arrives decoded from `%2F`.
- The body is optional and has the **same shape as `SendDraftRequest`**:
  `req: DeleteDraftRequest | None = None`, carrying only `from`. If the body is
  omitted, the primary mailbox is used.

**Why a body on a DELETE rather than query parameters.** Every other draft
endpoint (`/attachments`, `/send`) identifies the mailbox with the `from`
block, and every caller already holds that block from when it created the
draft. Accepting the identical shape means a caller cannot pick the wrong
mailbox by translating the block into a different convention. FastAPI, curl
(`-XDELETE -d`) and `requests` all handle a DELETE body. The read endpoints'
`?mailbox=` convention was considered and rejected because it would make this
the only draft endpoint that addresses mailboxes differently.

### 3.2 Mailbox resolution

The endpoint calls the existing `GraphEmailClient._mailbox_base(from_address,
from_shared)` without changes:

| `from` | Graph base | Why |
|---|---|---|
| omitted | `/me` | primary mailbox |
| alias or M365 group (`shared: false`) | `/me` | alias drafts live in the primary mailbox's Drafts folder, with the `from` stamped on the message |
| shared mailbox (`shared: true`) | `/users/{address}` | the draft lives in the shared mailbox's own Drafts folder |

When the wrong mailbox is passed, for example a shared-mailbox draft deleted
without `from`, the guard's GET returns 404, so the endpoint returns 404 and
nothing is deleted. That outcome is safe, and the error message points the
caller at the `from` block.

### 3.3 The draft guard

A new client method, `GraphEmailClient.delete_draft(message_id, *,
from_address=None, from_shared=False)`, does the following:

1. `GET {base}/messages/{quote(id, safe='')}?$select=id,isDraft`
   - 404 → `raise LookupError("draft not found")`
2. If `isDraft` is anything other than `true` (false, missing or null) →
   `raise NotADraftError("message is not a draft; refusing to delete")`.
   **This check fails closed.**
3. `DELETE {base}/messages/{quote(id, safe='')}`
   - 404 → `LookupError` (the draft disappeared between the two calls, which
     still leaves no draft, so it is effectively success)
   - other errors → `raise_for_status()`

`isDraft` is the right signal. Graph sets it on every unsent message: both
drafts created with `POST /messages` and drafts created with
`createReply`/`createReplyAll`. It sets it on nothing that was received or
sent. The guard does **not** check the parent folder, so a draft Ben has
filed somewhere other than Drafts can still be deleted. That is the intended
behaviour.

`NotADraftError(Exception)` is defined in `clients/azure/graph_email_client.py`.
It deliberately does not subclass `ValueError` or `LookupError`, so the generic
mapping cannot misclassify it as a 400 or a 404. `_call_graph` in
`api/routers/emails.py` gains a branch that runs before the `ValueError` branch:

```python
except NotADraftError as e:
    raise HTTPException(status_code=409, detail=str(e)) from e
```

**Race between check and delete.** Nothing makes the GET and the DELETE
atomic. The only way a draft stops being a draft is to be sent, and sending
moves it to Sent Items. The router does not request immutable ids, so a moved
message gets a new id and the DELETE returns 404. The residual risk is
therefore a draft that is sent by some other path in the milliseconds between
the two calls *and* keeps its id. That is accepted. Graph's `If-Match`/
`changeKey` support on message DELETE is not documented well enough to rely
on here.

### 3.4 Delete semantics

`DELETE /messages/{id}` is Graph's normal delete. Verified live on
2026-10-08: the item does **not** go to Deleted Items. It moves to
**Recoverable Items → Deletions** (well-known folder
`recoverableitemsdeletions`), the same place Shift+Delete sends mail in
Outlook. It is out of sight, but Outlook can restore it through Deleted
Items → "Recover items deleted from this folder" until the mailbox's
deleted-item retention purges it. `permanentDelete` is deliberately **not**
used, because it skips straight to Purges.

Ben chose this over moving drafts into Deleted Items (Graph's `move` action
with `destinationId: "deleteditems"`). The trade is that superseded drafts
don't clutter Deleted Items, but restoring the wrong one is less obvious. An agent that deletes the wrong
draft costs Ben a recovery from Recoverable Items within the retention
window, not lost work.

One consequence: a deleted draft still has `isDraft = true` in
Recoverable Items. Its id changed when it moved, though, so the id the
caller holds now returns 404, which was also verified live. A second DELETE
with the same id cannot purge it.

### 3.5 Responses

| Status | When | Body |
|---|---|---|
| 200 | deleted | `{"status": "deleted"}` (`StatusResponse`, like `/send` and `/attachments`) |
| 404 | no such message in the resolved mailbox, or it vanished mid-call | `{"detail": "draft not found"}` |
| 409 | the message exists but is not a draft | `{"detail": "message is not a draft; refusing to delete"}` |
| 403 | the account lacks rights on the `from` mailbox | Graph's detail, as for other endpoints |
| 503 | Graph authentication failed | `{"detail": "authentication failed"}` |
| 502 | any other Graph error | Graph's detail |

The endpoint returns 200 with a status body rather than 204 so that every
outbound endpoint keeps one response shape.

Callers should treat a 404 on a delete as "not in that mailbox" (already gone, or the wrong `from` block) and not as an error
worth surfacing loudly (§4).

### 3.6 Permissions

No new Graph scope is needed. Deleting a message requires `Mail.ReadWrite`
(and `Mail.ReadWrite.Shared` for a shared mailbox), which is the same
permission `create_draft` and `add_attachment` already depend on in
production.

### 3.7 Logging and tests

- Log `Deleted draft %s (base=%s)` on success and `Refused to delete
  non-draft %s (base=%s)` on 409, matching the existing `logger.info` lines
  in the client. No new metric is added, because the outbound endpoints emit
  none today.
- `tests/test_graph_reply.py`, or a new `tests/test_graph_drafts.py`, covers
  the client:
  - `isDraft: true` → DELETE issued
  - `isDraft: false` → `NotADraftError`, and **no DELETE request is sent**
    (assert on the mock)
  - `isDraft` missing → `NotADraftError`
  - GET 404 → `LookupError`
  - DELETE 404 → `LookupError`
  - shared `from` → both calls hit `/users/{addr}`, while alias `from` hits
    `/me`
  - an id containing `/`, `+` and `=` is quoted in both URLs
- `tests/test_emails_router.py` covers the route: 200, 404, 409 and 403
  mapping, with a body and without one, and a `%2F`-encoded id reaching the
  client intact.

## 4. Agent and skill changes

### 4.1 `email-builder` (`.claude/agents/email-builder.md`)

**Step 5, the `draft_id` paragraph,** is replaced. The current text says "the
API has no draft-edit endpoint… the old draft stays in Drafts until Ben
deletes it". The new behaviour:

1. Build the replacement draft completely: create it and add every
   attachment.
2. **Only after the replacement exists**, delete the old draft:
   `DELETE /emails/drafts/{enc(draft_id)}` with the **same `from` block the
   old draft was created with**. That is normally the identity in this
   dispatch. If the dispatch changes the identity, the old draft's `from`
   must be passed through as well (see §4.2).
3. Report the old id on a new line: `Replaced: <old id> (deleted)`.

Order matters. Creating before deleting means a failure partway through
leaves two drafts rather than none. Two drafts is the problem this spec
solves; zero drafts loses Ben's review copy.

Results of the delete:

- **200** → `Replaced: <id> (deleted)`
- **404** → `Replaced: <id> (not found in <mailbox>)`. This is not a failure, but it is not reported as "gone" either, because a wrong `from` block also produces it. The `From:` line reports `(shared)` so the dispatcher can pass the block back exactly.
- **409** → `Replaced: <id> (NOT deleted: not a draft, it may already have
  been sent)`. Surface this prominently, because it means the earlier version
  may have gone out.
- **anything else** → `Replaced: <id> (NOT deleted: <status> <detail>)`. The
  new draft is still the result, and the run does not fail.

The agent never deletes a draft it was not handed as `draft_id`, and never
deletes during a run that ends in `RECIPIENT_UNRESOLVED`, `AMBIGUOUS` or a
failure. In those cases nothing new replaced the old draft.

**Step 6 (send).** No change. Sending the replacement leaves no stray drafts,
because the old one was already deleted in step 5.

**Output.** Add an optional `Replaced:` line to the `DRAFTED` and `SENT`
shapes, directly after `Draft id:`.

**Partial drafts left by a failure** (for example the draft was created but an
attachment failed) are **not** deleted automatically. The current rule stays:
report what exists. Ben, or the dispatcher on Ben's instruction, discards it
through §4.2.

### 4.2 `sending-inbox-email` (`.claude/skills/sending-inbox-email/SKILL.md`)

Bump `version` to `1.2.0`.

1. **Relay → `change`.** Re-dispatch with `draft_id` **and the `from` block
   that draft was created with**. The agent creates the new draft and deletes
   the old one. Relay the `Replaced:` line, and if it says `NOT deleted`, give
   Ben the old id or link.
2. **Relay → new option `discard`.** The prompt becomes *send it, change it,
   leave it in Drafts, or discard it?* On *discard*, delete the draft directly
   (below) with no re-dispatch.
3. **New section "Delete a draft"**, placed after "Draft, then send":

   ```bash
   ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$DRAFT_ID")
   curl -s -XDELETE "$BASE/emails/drafts/$ENC" -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{}'          # or the draft's "from" block
   # -> {"status":"deleted"}   404 = not in that mailbox   409 = not a draft, nothing deleted
   ```

   Add one line of policy: deleting a draft does not need confirmation when
   Ben said to discard or replace it. Deleting anything else is impossible,
   because the endpoint returns 409.
4. **Notes.** Add one bullet: deleted drafts skip Deleted Items. They go
   to Recoverable Items, and Outlook restores them via "Recover items
   deleted from this folder".

### 4.3 Out of scope

Other consumers (`tasks`, `schedule`) do not create drafts through inbox-api
and need no changes. Inbox's own reply-draft creation in the pipeline
(`draft_link` on `email_classified`) is unaffected. Nothing deletes those
drafts automatically.

## 5. Decision: replace-and-delete, not `PATCH`

**The question.** Should an `email-builder` revision update the existing
draft in place (`PATCH /messages/{id}`) instead of creating a new draft and
deleting the old one?

**The decision.** No, at least not now. Revisions remain create-new +
delete-old.

**Reasons.**

1. **Reply drafts.** A `createReply` draft's body holds Graph's quoted
   history beneath the comment. A `PATCH` of `body` replaces the whole body.
   The agent would have to fetch the draft, split the quote back out of the
   HTML and splice in the new text, which is fragile. Replace-and-delete
   simply calls `createReply` again and gets a fresh, correct quote.
2. **Identity changes.** A revision that moves from the primary mailbox to a
   shared mailbox, or the other way, cannot be expressed as a `PATCH`, because
   the draft has to live in a different mailbox. Replace-and-delete handles it
   with the same code path.
3. **Attachments.** A `PATCH` leaves attachments untouched, so a revision
   that drops a file would also need an attachment-delete endpoint.
   Replace-and-delete rebuilds the attachment list from the dispatch.
4. **One path.** New messages and replies, with or without an identity
   change, all revise the same way. `PATCH` would only cover the easy case
   (a new message, same identity, same attachments), and the agent would
   still need replace-and-delete for every other case.

**What this costs.** The draft's id and `web_link` change on each revision, so
a link Ben opened for v1 goes stale. That is acceptable, because each
`DRAFTED` report carries the current link. Graph allows a `PATCH` of
`subject`, `body`, `toRecipients`, `ccRecipients` and `bccRecipients` only
while `isDraft = true`. If revision churn becomes a real cost, a later spec
can add `PATCH /emails/drafts/{id}` for the new-message, same-identity case
and reuse this spec's guard.

## 6. Rollout

1. Implement §3 in `clients/azure/graph_email_client.py` and
   `api/routers/emails.py`, with tests. Deploy inbox-api.
2. Verify against the real mailbox by hand: create a draft, delete it, and
   confirm it is in Recoverable Items (§3.4). Then point DELETE at a received
   message id and confirm the response is 409 and the message is untouched.
   Repeat once with a shared-mailbox `from`.
3. Make the agent and skill edits in §4 in the **same PR**. The skill and the
   endpoint ship together, because before the deploy the agent's DELETE gets
   404, which it reports as "not found". That would be misleading.
