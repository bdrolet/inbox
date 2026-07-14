# Deferred folder moves — design

**Date:** 2026-07-14
**Status:** Approved design, ready for implementation plan

## Problem

When an email arrives, the pipeline classifies it and immediately moves it out of the
Inbox into an action folder (`Archive`, `reply_required`, `review`). This happens before
Ben has a chance to see the message sitting in his inbox. He wants classified mail to
**stay visible in the Inbox during the day** and only be filed away in a single batch
early each morning.

## Goals

- Emails are **tagged** (Outlook color categories) as soon as they arrive — unchanged.
- **Urgent notifications** (ntfy), **Asana tasks**, and **reply drafts** all still fire
  immediately as they do today. Only the *physical folder move* is deferred.
- Classified mail stays in the Inbox and is moved to its destination folder by a **cron
  sweep at 5:00 AM America/New_York**.
- Asana task links keep working **permanently**, before and after the move, without ever
  patching the task.

## Non-goals (explicitly out of scope)

- **Moving urgent mail.** Urgent messages stay in the Inbox indefinitely, exactly as today.
  A future feature may give urgent its own deferred move; not now.
- **Deferring anything other than the move** — tags, tasks, drafts, and notifications stay
  immediate.
- **Backfilling historical messages** into the new immutable-ID format. The switch is
  forward-only.

## Accepted trade-offs

These were reviewed and accepted during design:

- **DB-vs-mailbox inconsistency during the day.** Once classified, the DB records the
  destination (e.g. `REFERENCE`), but the email physically remains in the Inbox until the
  5 AM sweep. `inbox-api`'s live mailbox search will therefore surface reference/ignore mail
  in the Inbox rather than its destination folder until the sweep runs. Self-heals every
  morning. The DB remains the source of truth for category, so DB-backed search stays correct.
- **Inbox accumulation + uneven review windows.** Classified mail piles up in the Inbox
  until morning; unread counts run higher. An email arriving at 11 PM gets a short review
  window; one from early the previous morning gets ~24 h. Inherent to a fixed-time sweep.

## Architecture overview

Three coordinated changes:

1. **Stop the inline move; record intent instead.** The classify pipeline records the
   *intended* destination folder on the classification row and does everything else
   (tags, tasks, drafts, ntfy) immediately. No folder move at classify time.
2. **A morning sweep** (new scheduled Cloud Function) executes the pending moves in a batch
   at 5 AM ET, skipping anything the user already handled manually.
3. **Immutable IDs + a redirector** so Asana task links resolve to the message's *current*
   location for the life of the message, across the sweep move and any manual moves.

### Data flow

```
Email arrives
  → webhook CF → Pub/Sub → processor CF (pipeline)
      → classify
      → record pending_folder on classification   (NEW: no move here)
      → apply tags (immediate)
      → dispatch: create Asana task / draft / ntfy (immediate)
           Asana task "Open in Outlook" link = redirector URL, not raw webLink

... message sits in Inbox all day, tagged, with a live task ...

5:00 AM ET
  → Cloud Scheduler → Pub/Sub (inbox-sweep) → sweep CF
      → for each classification with pending_folder set and moved_at NULL:
           GET message by immutable id, $select=parentFolderId
             - not found (404)        → mark moved_at, skip (deleted/gone)
             - not in Inbox           → mark moved_at, skip (user already filed it)
             - in Inbox               → move to pending_folder, mark moved_at
```

## Components

### 1. Immutable IDs (`clients/azure/graph_email_client.py`, `clients/graph_subscriptions.py`)

Adopt Microsoft Graph immutable IDs so a message's `id` survives folder moves within the
mailbox. Without this, the stored `external_id` dies the moment the message moves (sweep or
manual), which would break both the sweep and the redirector.

- Add `Prefer: IdType="ImmutableId"` to the headers used for **message reads, moves, and
  the redirector's live lookup**. Scope it to a dedicated header variant (e.g.
  `get_headers(immutable=True)`) used on the message paths — **not** globally on
  `get_headers()`, to avoid unintended effects on group / shared-mailbox / thread-post reads
  that use different ID types.
- Add the same header to **subscription creation** (`graph_subscriptions.register`) so change
  notifications deliver immutable IDs in `resourceData.id`. Existing subscriptions keep the
  old format, so the live subscription
  (`f58b30e4-4090-433a-87cc-fbe1f87f574a`) must be **deleted and recreated**; the renew CF
  self-heals the new ID into the `graph-subscription-id` secret, and
  `terraform.tfvars` is updated.
- **Forward-only.** Historical `external_id` values are mutable-format and won't match new
  immutable-format IDs. Dedup keys on `(source, external_id)`; this only matters for a
  message notified under both formats, which won't happen in practice (notifications fire once,
  on `created`). No schema change or backfill.

**Bonus (free, note only):** adopting immutable IDs also makes the calendar-invite
`graph_message_id` stored in `services/calendar_invite.py` durable across the move, fixing a
latent "RSVP button 404s after move" bug. No code change required for that benefit.

### 2. Pending-move state (`repo/schema.sql`, `repo/classifications.py`)

Add two columns to `classifications` (via `ADD COLUMN IF NOT EXISTS`, matching the existing
`importance` migration pattern):

- `pending_folder TEXT` — destination folder name. `NULL` = no move intended (e.g. urgent).
- `moved_at TIMESTAMPTZ` — set by the sweep when the move is executed **or** determined
  terminal (deleted / already filed). `NULL` + non-null `pending_folder` = still pending.

A partial index on `(pending_folder) WHERE moved_at IS NULL` keeps the sweep query cheap.

`classifications.insert` gains a `pending_folder` parameter.

### 3. Handlers record intent instead of moving (`handlers/actions/*`, `handlers/actions/_shared.py`)

- `_shared.prepare()` no longer calls `archiving.move_to_folder()`. Its `folder` argument
  becomes the value written to `pending_folder` on the classification.
- Category → pending_folder mapping is unchanged from today's inline behavior:
  `IGNORE`/`REFERENCE` → `Archive`, `RESPOND` → `reply_required`, `REVIEW` → `review`,
  `URGENT` → `NULL` (no move).
- Calendar-invite detection and draft creation continue to run at classify time against the
  still-in-Inbox message — now strictly more robust, since the message hasn't moved.
- The Asana "Open in Outlook" link becomes the **redirector URL** (below), not the raw
  webLink.

### 4. Redirector endpoint (`api/routers/` on `inbox-api`)

A new **unauthenticated** GET endpoint on the existing `inbox-api` Cloud Run service:

```
GET /r/{message_uuid}  →  302 to the message's current Outlook webLink
```

- Looks up the message's immutable `external_id` by DB UUID, does a live
  `GET /me/messages/{external_id}?$select=webLink` (immutable header), and returns a
  **302 Found** redirect to the resolved `webLink`.
- Because it resolves live from the immutable ID, it always points at wherever the message
  currently lives — Inbox during the day, the action folder after the sweep, or wherever the
  user manually moved it. (The redirect is a 302 — temporary — precisely because the target
  webLink changes as the message moves.)
- **Auth model:** unauthenticated capability URL. The endpoint reveals only a `webLink`
  (a navigation URL); opening the actual message still requires the user's Outlook session,
  so no message content is exposed. The `{message_uuid}` is an unguessable random UUID. The
  other `inbox-api` endpoints keep their Bearer-token auth; the redirector is a separate,
  auth-free route because it is clicked from a browser via Asana.
- Failure modes: unknown UUID → 404; Graph lookup fails → 502/404 with a short message.

### 5. Morning sweep (new CF `functions/sweep/`, Terraform)

A new scheduled Cloud Function, mirroring the existing `inbox-renew` scheduler→CF pattern:

- **Cloud Scheduler** cron at `0 5 * * *`, timezone `America/New_York` → publishes to a new
  Pub/Sub topic `inbox-sweep` → sweep CF.
- Sweep logic:
  1. Query classifications with `pending_folder` set and `moved_at IS NULL`.
  2. For each, `GET` the message by immutable id with `$select=parentFolderId`:
     - **404 / not found** → user deleted it (or it's gone). Mark `moved_at`, skip.
     - **not in Inbox** → user already filed it manually; their action wins. Mark `moved_at`,
       skip. (This explicit check is required *because* immutable IDs no longer go stale on a
       manual move — we can't rely on a 404 to detect it.)
     - **in Inbox** → move to `pending_folder`, mark `moved_at`.
  3. Marking `moved_at` on every terminal outcome guarantees idempotency: a crash between the
     Graph move and the DB write is safe to retry, and rows never get stuck pending.
- Emits OTel metrics (count moved / skipped / errored) consistent with the rest of the
  pipeline.

### 6. Terraform

- New Pub/Sub topic `inbox-sweep`, Cloud Scheduler job (`0 5 * * *`, `America/New_York`),
  sweep Cloud Function + its service account and IAM (Cloud SQL, Secret Manager for Graph +
  DB creds).
- Update `graph_subscription_id` in `terraform.tfvars` after the subscription is recreated
  with the immutable-ID header.

## Error handling

- **Sweep partial failure:** idempotent by design — `moved_at` is set on every terminal
  outcome (moved, deleted, already-filed); the pending query naturally excludes completed
  rows on retry.
- **Redirector resolution failure:** returns 404/502 rather than crashing; the Asana task
  still carries subject, summary, sender, and (for RESPOND) the draft link.
- **Immutable-ID switchover:** forward-only; no attempt to reconcile old mutable IDs.
- Existing per-stage `try/except` + OTel error counters in the pipeline are unchanged.

## Testing

- **Unit:** category → `pending_folder` mapping; `classifications.insert` persists
  `pending_folder`; sweep decision logic for the three cases (in-Inbox / moved-away /
  deleted) with a mocked Graph client; redirector resolves UUID → 302 and handles unknown
  UUID / Graph failure.
- **Integration / local E2E:** run the pipeline against a real test email — assert it is
  classified, tagged, task created with a redirector link, and **not** moved. Then invoke the
  sweep locally and assert the message moves to the destination folder and `moved_at` is set.
  Manually move a second test message out of the Inbox first and assert the sweep skips it.
- **Redirector:** hit `/r/{uuid}` before and after a sweep move; both resolve to a working
  Outlook link.
- Immutable-ID subscription: verify a freshly-arrived notification's `resourceData.id` is in
  immutable format and that fetch/move round-trip on it.

## Rollout

1. Land code (immutable-ID headers, schema columns, handler changes, redirector, sweep CF)
   behind the normal PR flow.
2. Apply Terraform (new topic, scheduler, sweep CF).
3. Recreate the Graph subscription with the immutable-ID header; update
   `graph_subscription_id`.
4. Deploy the processor CF. From this point new mail is recorded with `pending_folder` and
   left in the Inbox.
5. First 5 AM sweep files the accumulated day's mail.

## Open questions

None blocking. Immutable-ID adoption is scoped to message read/move/subscription paths; the
urgent-move feature and any redirector reuse for ntfy deep-links are deliberately deferred.
