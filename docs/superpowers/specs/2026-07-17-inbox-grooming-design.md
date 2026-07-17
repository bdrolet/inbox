# Inbox grooming: stale-urgent re-triage + untagged reclassification — design

**Date:** 2026-07-17
**Status:** Approved design, ready for implementation plan

## Problem

After each 5 AM sweep, the Inbox is left holding exactly three kinds of mail: `urgent`-tagged
messages (deliberately never moved), messages under an unexpired `keep_until:` hold, and
**untagged** messages the classifier has never seen — mail predating the pipeline, or missed
while it was broken. The first and third populations accumulate forever:

- An `urgent` message that Ben has already dealt with (or whose deadline has passed) sits in
  the Inbox indefinitely, because nothing ever re-examines it.
- Untagged legacy mail is skipped by every sweep, because the sweep only files by tag.

Ben wants a daily process that clears both — but **intelligently**: a blanket "demote urgent
after N days" rule would file away things that are still genuinely urgent, just not yet done.

## Goals

- **Stale-urgent re-triage.** An `urgent` message untouched for 3 days is re-evaluated by
  Claude using real signals (message content, its age, today's date, and whether Ben has
  replied in the thread). The verdict decides its fate: stay urgent, demote to
  needs-response, or archive as resolved/expired. Still-urgent messages are re-checked every
  3 days, not nightly.
- **Untagged reclassification.** Untagged Inbox mail older than 24 hours is fed through the
  existing classification pipeline (embed → classify → tag); the next morning's sweep then
  files it normally. The system becomes **self-healing**: any message that slips past the
  webhook is picked up within a day or two.
- Both behaviors run inside the **existing `inbox-sweep`** morning pass — no new function, no
  new scheduler.
- Re-triage needs **no new state store**: the recheck schedule is carried on the message
  itself via the existing `keep_until:` tag.

## Non-goals (explicitly out of scope)

- **Feedback-loop writes.** Re-triage verdicts are time-based *policy*, not human judgment.
  They never set `message_embeddings.current_label` (project invariant: human feedback only)
  and never emit `label_applied` events.
- **Asana task reconciliation.** If re-triage archives an expired urgent email, any task the
  tasks service created for it is untouched. Task lifecycle stays the tasks repo's problem.
- **Grooming other folders.** Old mail in `reply_required` / `review` is not touched; this
  design is Inbox-only.
- **A synchronous backfill of the legacy backlog.** The 50/night cap drains it over weeks; a
  one-off manual run (invoking the same publish path with a higher cap) can accelerate it if
  Ben wants, but is not part of this design.

## Accepted trade-offs

- **LLM verdicts can be wrong.** A still-pending message could be judged expired and
  archived. Mitigations: the reply-signal is factual (did Ben reply, yes/no), the verdict
  prompt is deliberately conservative (uncertain → `still_urgent`), and archived mail is
  never deleted — it is findable in `Archive` and via search. Accepted.
- **Two-morning latency for untagged mail.** A republished message is classified minutes
  after the sweep but not filed until the *next* sweep. Accepted — consistent with the
  deferred-move design's "tags now, moves at 5 AM" philosophy.
- **Re-triage cost.** A handful of Claude calls per night, plus one Graph conversation
  lookup per evaluated message. Negligible at this mailbox's volume.
- **`keep_until:` is now dual-use.** A manual hold and a re-triage "check again in 3 days"
  marker are the same tag. Consequence: a manual `keep_until:` on an urgent message also
  defers its re-triage — which is the intuitive behavior ("I said keep it") — and a
  re-triage-applied hold is visible and editable in Outlook like any other. Accepted as a
  feature, not a collision.

## Architecture overview

Two new per-message behaviors inside the existing sweep pass, plus one repair in the
processor:

```
5:00 AM ET — sweep CF (existing)
  → list Inbox messages (now $select=id,categories,receivedDateTime,conversationId)
  → for each message:
       - keep_until present, not elapsed        → hold (existing; also defers re-triage)
       - category tag maps to a folder           → move (existing behavior, unchanged)
       - URGENT tag, received > 3 days ago       → RE-TRIAGE (new):
           gather: subject/body, received date, today, has-Ben-replied (conversationId)
           Claude verdict:
             still_urgent        → apply keep_until:<now+3d>  (stays; re-checked in 3 days)
             needs_response      → retag urgent→respond, move to reply_required
             resolved_or_expired → move to Archive, strip urgent tag
       - no recognized tag, received > 24h ago   → REPUBLISH (new, capped at 50/night):
           publish {"resourceData": {"id": <immutable-id>}} to the processor Pub/Sub topic
       - otherwise                               → skip (existing)

minutes later — processor CF (existing pipeline)
  → classifies republished messages exactly like fresh mail
  → NEW repair: if the message already exists in the DB with a stored classification,
    re-apply its category tag instead of silently skipping the duplicate
  → next morning's sweep files the now-tagged message normally
```

## Components

### 1. Graph client listing (`clients/azure/graph_email_client.py`)

`list_inbox_categories()` widens its `$select` to
`id,categories,receivedDateTime,conversationId` and returns those fields per message.
Callers that only read `id`/`categories` (the existing sweep rules) are unaffected. One new
helper, `has_reply_from_me(conversation_id, after: datetime) -> bool`, checks whether any
message in the conversation was **sent by the mailbox owner** after the given instant
(query the conversation, filter `from` = own address, `sentDateTime > after`). Failure to
determine → return `False` (absence of evidence, not evidence of absence; the verdict prompt
treats it accordingly).

### 2. Sweep rules (`services/sweep_rules.py` — pure, no I/O)

`decide(categories, now)` gains `received_at: datetime | None` and two new decision
outcomes, keeping the function pure and CI-testable:

- Existing `hold` / `move` / `skip` behavior is unchanged and evaluated in the same order
  (`keep_until` holds still win over everything).
- **New `retriage` action:** `urgent` tag present, no unelapsed `keep_until:`, and
  `received_at` more than 3 days before `now`.
- **New `republish` action:** no recognized category tag, no `keep_until:`, and
  `received_at` more than 24 hours before `now`. Messages with a missing `received_at`
  are skipped (fail safe).
- A second pure function, `apply_verdict(verdict, categories) -> SweepDecision`-style
  mapping, converts a re-triage verdict into the concrete outcome (which folder, which tags
  to strip/add), so the verdict→action table is unit-testable without any I/O:
  - `still_urgent` → add `keep_until:<(now+3 days).date()>`; no move.
  - `needs_response` → categories rewritten with `urgent` replaced by `respond`; move to
    `reply_required`.
  - `resolved_or_expired` → move to `Archive`; strip `urgent`.
  - Unparseable / unexpected verdict → treated as `still_urgent` (fail safe: nothing moves).

### 3. Re-triage service (`services/retriage.py` — new)

Owns the one concern of evaluating a stale urgent message:

- Fetches the message body (existing fetch path), calls `has_reply_from_me`, and builds a
  compact prompt: subject, trimmed body, received date, today's date, and the reply signal.
- Calls Claude (same client as classification, `claude-sonnet-4-6`) requesting a structured
  verdict: `still_urgent | needs_response | resolved_or_expired`, with a one-line reason
  (logged, not stored).
- The prompt instructs: *when uncertain, answer `still_urgent`* — the safe verdict is the
  one that changes nothing.
- Any exception (fetch, Graph, Claude) → log, count as errored, leave the message untouched.
  Re-triage failure must never abort the rest of the sweep.

### 4. Sweep orchestration (`services/sweep.py`, `main.py`)

`run_sweep` handles the two new actions: for `retriage` it calls `services/retriage.py` and
then applies the pure verdict mapping (move / retag / add hold via existing client methods);
for `republish` it publishes the synthetic notification to the processor topic (topic name
from a new env var) and increments a counter, stopping at the 50-message nightly cap.
Counts grow: `retriaged_kept`, `retriaged_demoted`, `retriaged_archived`, `republished`
alongside the existing `moved/held/skipped/errored`. OTel metrics follow the existing
sweep-counter pattern (per the adding-observability conventions).

### 5. Processor duplicate repair (`handlers/pipeline.py`)

Today the pipeline returns silently when `messages.exists(...)` is true. That makes
republishing useless for a message that got *stored* but never *tagged* (e.g. a dispatch
crash after the classification insert). Change: on duplicate, look up the stored
classification; if one exists, **re-apply the category tag** to the message (idempotent —
tagging an already-tagged message is a no-op PATCH) and return. If the message exists but
has no stored classification, delete-and-reprocess is *not* attempted; it is logged and
skipped (rare; can be handled manually). This makes "publish a notification for message X"
a safe universal repair action.

### 6. Terraform

- Sweep CF service account gains `roles/pubsub.publisher` on the processor topic.
- Sweep CF env gains the processor topic name and the `anthropic-api-key` secret (it now
  calls Claude).
- No new functions, schedulers, or topics.

## Error handling

- **Per-message isolation:** every new action (re-triage, republish) is wrapped so a failure
  logs + counts `errored` and continues the batch — same contract as the existing move loop.
- **Fail-safe defaults everywhere:** missing `received_at` → skip; reply-lookup failure →
  "no reply found"; Claude error or malformed verdict → `still_urgent` (nothing moves);
  publish failure → message stays untagged and is retried next night.
- **Cap as circuit breaker:** the 50/night republish cap bounds Claude cost and Pub/Sub
  volume even if the Inbox suddenly contains thousands of untagged messages.
- **Idempotency:** re-runs are safe. Re-triaged messages either left the Inbox or carry a
  fresh `keep_until:` (so they hold); republished messages that got tagged are filed by the
  normal rules; ones that didn't are republished again (processor dedupe + tag repair make
  this harmless).

## Testing

- **Unit (pure, CI):**
  - `decide()`: urgent + 3 days → `retriage`; urgent + 2 days → skip; urgent + old +
    unelapsed `keep_until` → hold; untagged + 25 h → `republish`; untagged + 2 h → skip;
    untagged + missing `received_at` → skip; tagged messages unchanged from today.
  - Verdict mapping: all three verdicts + an unknown verdict → correct folder/tag/hold
    outcomes; `keep_until` date arithmetic at ET day boundaries.
  - `run_sweep` with a fake client + fake retriage/publish: counts, cap enforcement at 50,
    error isolation (one failing message doesn't stop the batch).
  - Pipeline duplicate repair: exists + stored classification → tag re-applied, no
    reclassification; exists + no classification → logged skip.
- **Local E2E — via the `verifying-pr-locally` skill** after the PR is open:
  - Plant an urgent-tagged test message dated 4+ days back; run the sweep locally; verify
    the verdict path taken (and for `still_urgent`, the applied `keep_until:` tag).
  - Plant an untagged message > 24 h old; run the sweep; verify the Pub/Sub publish (or, in
    local mode, the direct pipeline invocation) results in tags; run the sweep again and
    verify it files.
  - Verify a duplicate republish of an already-classified-but-untagged DB row re-applies
    its tag.

## Rollout

1. Land code behind the normal PR flow: `pr-open` skill, then `verifying-pr-locally`, then
   merge (deploy runs from main via GitHub Actions).
2. Apply Terraform (publisher IAM, sweep env vars + Anthropic secret).
3. First morning: untagged backlog starts draining at ≤ 50/night; stale urgent messages get
   their first verdicts. Watch sweep logs/metrics for verdict distribution the first few
   days and tune the prompt if it's over-eager.

## Open questions

None blocking. Deliberately deferred: a one-off accelerated backfill run for the legacy
backlog, grooming of `reply_required`/`review` folders, and any ntfy notification
summarizing what the sweep did each morning.
