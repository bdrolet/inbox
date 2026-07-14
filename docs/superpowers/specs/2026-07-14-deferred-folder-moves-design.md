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
- The folder decision — the business logic — runs **at move time, from the message's current
  tags**, not at classify time. A correction made during the day is honored by that night's
  sweep.
- Asana task links keep working **permanently**, before and after the move, without ever
  patching the task.
- A message can be **held in the Inbox** past the sweep via a `keep_until` tag.

## Non-goals (explicitly out of scope)

- **Moving urgent mail.** Urgent messages stay in the Inbox indefinitely, exactly as today.
  A future feature may give urgent its own deferred move; not now.
- **Deferring anything other than the move** — tags, tasks, drafts, and notifications stay
  immediate.
- **Backfilling historical messages** into the new immutable-ID format. The switch is
  forward-only.
- **A UI for setting `keep_until`.** For v1 the hold tag is added manually in Outlook. A
  convenience affordance (snooze button / API endpoint) is a possible future enhancement.

## Accepted trade-offs

These were reviewed and accepted during design:

- **DB-vs-mailbox inconsistency during the day.** Once classified, the DB records the
  category (e.g. `REFERENCE`), but the email physically remains in the Inbox until the
  5 AM sweep. `inbox-api`'s live mailbox search will therefore surface reference/ignore mail
  in the Inbox rather than its destination folder until the sweep runs. Self-heals every
  morning. The DB remains the source of truth for category, so DB-backed search stays correct.
- **Inbox accumulation + uneven review windows.** Classified mail piles up in the Inbox
  until morning; unread counts run higher. An email arriving at 11 PM gets a short review
  window; one from early the previous morning gets ~24 h. Inherent to a fixed-time sweep.
- **Stateless re-filing.** The sweep's rule is "tagged + in Inbox at 5 AM → file by tag."
  There is no per-message "already moved" memory. If a filed message is dragged back into the
  Inbox with its category tag intact, the next sweep re-files it. To keep something in the
  Inbox, use a `keep_until` tag or remove its category tag. This is the deliberate cost of a
  stateless, tag-driven sweep, and keeps behavior fully predictable from what's visible on the
  message.

## Architecture overview

Three coordinated changes:

1. **Stop the inline move.** The classify pipeline does everything except the move — it
   classifies, applies tags (which encode the category), creates the Asana task / reply draft,
   and sends the ntfy notification. It records **no** pending-move state.
2. **A stateless, tag-driven morning sweep** (new scheduled Cloud Function) enumerates the
   Inbox at 5 AM ET and files each message into the folder implied by its current category
   tag — honoring `keep_until` holds and skipping urgent / untagged mail.
3. **Immutable IDs + a redirector** so Asana task links resolve to the message's *current*
   location for the life of the message, across the sweep move and any manual moves.

### Data flow

```
Email arrives
  → webhook CF → Pub/Sub → processor CF (pipeline)
      → classify
      → apply tags (immediate)  — Outlook categories carry the category value
      → dispatch: create Asana task / draft / ntfy (immediate)
           Asana task "Open in Outlook" link = redirector URL, not raw webLink
      → NO folder move, NO pending-move state written

... message sits in Inbox all day, tagged, with a live task ...

5:00 AM ET
  → Cloud Scheduler → HTTP POST → sweep CF
      → list Inbox messages (GET /me/mailFolders/inbox/messages?$select=id,categories)
      → for each message:
           - keep_until tag present and not elapsed  → skip (held in Inbox)
           - no recognized category tag              → skip (untagged / personal)
           - category maps to no folder (urgent)     → skip
           - otherwise                               → move to folder_for_category(tag);
                                                        strip any elapsed keep_until tag
```

## Components

### 1. Immutable IDs (`clients/azure/graph_email_client.py`, `clients/graph_subscriptions.py`)

Adopt Microsoft Graph immutable IDs so a message's `id` survives folder moves within the
mailbox. This is required by the **redirector**: the stored `external_id` must still resolve
after the message is moved (by the sweep or manually). The sweep itself does *not* depend on
immutable IDs — it moves each message using the fresh ID from its live Inbox listing — but
adopting them repo-wide keeps one ID format everywhere.

- Add `Prefer: IdType="ImmutableId"` to the headers used for **message reads, moves, and the
  redirector's live lookup**. Scope it to a dedicated header variant (e.g.
  `get_headers(immutable=True)`) used on the message paths — **not** globally on
  `get_headers()`, to avoid unintended effects on group / shared-mailbox / thread-post reads
  that use different ID types.
- Add the same header to **subscription creation** (`graph_subscriptions.register`) so change
  notifications deliver immutable IDs in `resourceData.id`. Existing subscriptions keep the
  old format, so the live subscription (`f58b30e4-4090-433a-87cc-fbe1f87f574a`) must be
  **deleted and recreated**; the renew CF self-heals the new ID into the
  `graph-subscription-id` secret, and `terraform.tfvars` is updated.
- **Forward-only.** Historical `external_id` values are mutable-format and won't match new
  immutable-format IDs. Dedup keys on `(source, external_id)`; this only matters for a
  message notified under both formats, which won't happen in practice (notifications fire once,
  on `created`). No schema change or backfill.

**Bonus (free, note only):** adopting immutable IDs also makes the calendar-invite
`graph_message_id` stored in `services/calendar_invite.py` durable across the move, fixing a
latent "RSVP button 404s after move" bug. No code change required for that benefit.

### 2. No pending-move DB state

The tag-driven sweep is stateless, so **no schema change is needed** — no `pending_folder`,
no `moved_at`. The "which folder" decision is derived from the message's Outlook categories at
sweep time; the "has it been moved" question is answered by the message's folder location
(it's not in the Inbox anymore). This is a deliberate simplification over an earlier
classify-time-recorded-intent approach.

### 3. Handlers stop moving; folder mapping centralized (`handlers/actions/*`, `handlers/actions/_shared.py`, `services/archiving.py`)

- `_shared.prepare()` no longer calls `archiving.move_to_folder()`, and the handlers no longer
  pass a `folder`. They classify-time work is unchanged otherwise: calendar-invite detection,
  summary, deadline, Asana task, reply draft, ntfy.
- The category → folder mapping moves out of the individual handlers into a single shared
  function, `services/sweep_rules.folder_for_category(category) -> str | None`:
  `IGNORE`/`REFERENCE` → `Archive`, `RESPOND` → `reply_required`, `REVIEW` → `review`,
  `URGENT` → `None` (no move). This is the one source of truth, used only by the sweep.
- Calendar-invite detection and draft creation now run against the still-in-Inbox message —
  strictly more robust than today, since the message hasn't moved.
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
- Failure modes: unknown UUID → 404; Graph lookup fails → 502.

### 5. Morning sweep (new CF `functions/sweep/`, Terraform)

A new HTTP-triggered Cloud Function, mirroring the existing `inbox-renew` scheduler→CF pattern
(a new `sweep` entry point in `main.py`, deployed as its own `inbox-sweep` CF from the repo
bundle, the same way `calendar_action` and `label` are separate CFs):

- **Cloud Scheduler** cron `0 5 * * *`, timezone `America/New_York`, POSTs directly to the
  sweep CF's URL (no Pub/Sub topic needed — the sweep carries no message payload).
- Sweep logic:
  1. List Inbox messages: `GET /me/mailFolders/inbox/messages?$select=id,categories` (paged).
  2. For each message, inspect its `categories`:
     - **`keep_until` present and not elapsed** → skip (see the `keep_until` contract below).
     - **no entry matching a `Category` enum value** → skip (untagged / personal mail).
     - **category maps to `None`** (urgent) → skip.
     - **otherwise** → move to `folder_for_category(category)`; if an *elapsed* `keep_until`
       tag is present, strip it in the same PATCH/move cleanup.
  3. Idempotency is inherent: moving removes the message from the Inbox, so a re-run simply
     doesn't see it. A crash mid-sweep is safe to retry.
- Emits OTel metrics (moved / held / skipped / errored counts) consistent with the rest of
  the pipeline.

### `keep_until` contract

A message is held in the Inbox past the sweep by adding an Outlook category of the form
`keep_until:<when>`, where `<when>` is either `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`, interpreted
in `America/New_York`.

- **Bare date `D`** — the message is held through the end of day `D`; it is filed on the first
  sweep whose date is strictly after `D`.
- **Datetime `D T HH:MM`** — the message is held until `now_ET >= D T HH:MM`; the next sweep
  at or after that moment files it.
- When the hold elapses (or is absent), the message is filed normally by its category tag, and
  the elapsed `keep_until:` category is stripped as cleanup.
- **Unparseable `keep_until:*`** → fail safe: treat as not-yet-elapsed (keep in Inbox) and log
  a warning, so a mistyped hold never causes premature filing.
- A `keep_until` on an otherwise-untagged message simply keeps it in the Inbox; once elapsed
  there is no category to file by, so it stays. That is acceptable.
- Setting the tag is a manual Outlook action for v1; a snooze affordance is a future
  enhancement (out of scope).

### 6. Terraform

- New HTTP-triggered `inbox-sweep` Cloud Function, Cloud Scheduler job (`0 5 * * *`,
  `America/New_York`) POSTing to it, and scheduler invoker IAM (Secret Manager for Graph
  creds; the sweep needs no DB access).
- Update `graph_subscription_id` in `terraform.tfvars` after the subscription is recreated
  with the immutable-ID header.

## Error handling

- **Sweep partial failure:** idempotent by design — the Inbox folder location *is* the state;
  completed moves drop out of the Inbox and aren't reconsidered on retry. A per-message move
  failure is logged, counted, and skipped without aborting the batch.
- **Redirector resolution failure:** returns 404/502 rather than crashing; the Asana task
  still carries subject, summary, sender, and (for RESPOND) the draft link.
- **Malformed `keep_until`:** fail safe — keep the message in the Inbox and warn.
- **Immutable-ID switchover:** forward-only; no attempt to reconcile old mutable IDs.
- Existing per-stage `try/except` + OTel error counters in the pipeline are unchanged.

## Testing

- **Unit:**
  - `folder_for_category` mapping for all five categories (urgent → `None`).
  - Sweep per-message decision from a `categories` list: files by category tag; skips urgent,
    untagged, and held (`keep_until` in future); files and strips an elapsed `keep_until`.
  - `keep_until` parsing: bare date vs datetime, ET boundary behavior, unparseable → held.
  - Redirector: UUID → 302 to resolved webLink; unknown UUID → 404; Graph failure → 502.
- **Integration / local E2E — via the `verifying-pr-locally` skill.** After the change is
  implemented, open the PR with the `pr-open` skill, then run `verifying-pr-locally` against
  the branch. That skill drives the API + real Graph and posts results to the open PR. The
  E2E checks it should exercise:
  - Run the pipeline against a real test email — assert it is classified, tagged, task created
    with a redirector link, and **not** moved.
  - Invoke the sweep locally against the test mailbox: a tagged message moves to the mapped
    folder; a `keep_until:<future>`-tagged message stays; an urgent-tagged message stays; an
    untagged message stays.
  - Redirector: hit `/r/{uuid}` before and after a sweep move; both resolve to a working
    Outlook link.
  - Verify a freshly-arrived notification's `resourceData.id` is in immutable format and that
    a fetch/move round-trip on it succeeds.

## Rollout

1. Land code (immutable-ID headers, `folder_for_category`, handler changes, redirector, sweep
   CF) behind the normal PR flow: open the PR with the `pr-open` skill, verify with the
   `verifying-pr-locally` skill, then merge.
2. Apply Terraform (new topic, scheduler, sweep CF).
3. Recreate the Graph subscription with the immutable-ID header; update
   `graph_subscription_id`.
4. Deploy the processor CF. From this point new mail is tagged and left in the Inbox.
5. First 5 AM sweep files the accumulated day's mail by tag.

## Open questions

None blocking. Immutable-ID adoption is scoped to message read/move/subscription paths; the
urgent-move feature, a `keep_until` snooze UI, and any redirector reuse for ntfy deep-links
are deliberately deferred.
