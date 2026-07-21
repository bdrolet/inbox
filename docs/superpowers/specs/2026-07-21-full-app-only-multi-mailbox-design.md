# Full app-only migration + multi-mailbox monitoring — design

**Date:** 2026-07-21
**Status:** Approved design, ready for implementation plan

## Problem

Today only the primary mailbox (`ben@drolet.cloud`) is monitored in real time. Ben has
several shared mailboxes that receive mail nobody triages. He wants those monitored by the
same pipeline.

The blocker is authentication. The system runs on a **delegated** MSAL token (device-code
flow, refresh token cached in the `msal-token-cache` Secret Manager secret, refreshed silently
on each run). Microsoft explicitly does **not** allow delegated permissions — including
`Mail.Read.Shared` — to create change-notification subscriptions on shared or delegated
folders. Per the Graph docs:

> The sharing permissions (`Mail.Read.Shared`…) don't support subscribing to change
> notifications on items in shared or delegated folders. To set up change notification
> subscriptions on messages in a shared, delegated, or any other user's mail folder, use the
> **application** permission `Mail.Read`.

So real-time monitoring of shared mailboxes requires an **app-only (client-credentials)**
credential. Rather than run two auth mechanisms side by side, this design converts the
**entire** system to app-only and retires the delegated token — which also eliminates the
recurring refresh-token expiry that periodically breaks the pipeline (the `refreshing-msal-token`
fire drill).

Verified during design: the currently-consented delegated scopes are only
`Mail.Read Mail.ReadWrite User.Read` — no `.Shared` scopes at all. The delegated token cannot
today read or write any mailbox but the primary; `.env.example` lists broader scopes
aspirationally, but they were never consented.

## Goals

- **Monitor N mailboxes**, not one: the primary plus a configured set of shared mailboxes,
  through the same webhook → processor → sweep pipeline.
- **Full app-only.** Replace delegated auth everywhere; retire the `msal-token-cache` secret
  and the `refreshing-msal-token` flow. One credential (`client-id` + `client-secret`),
  in-memory token, re-minted on ~1h expiry.
- **Full action parity** across all monitored mailboxes: classify, tag, move, urgent ntfy
  push, reply drafts, and calendar RSVP — one uniform code path, no per-mailbox branching.
- **Whole system in scope**, including `inbox-api` (search, outbound send, redirector), so the
  delegated token is fully retired.
- **Security-fenced.** App-only access is bounded to exactly the monitored mailboxes by an
  Exchange Application Access Policy, not granted tenant-wide in practice.

## Non-goals (explicitly out of scope)

- **Per-mailbox action policy.** Ben chose full parity; there is no per-mailbox opt-in/opt-out
  of drafts or RSVP. If that's wanted later it's a follow-up.
- **Dynamic discovery of "mailboxes Ben can access."** App-only has no delegate identity to
  inherit access from. The monitored set is an **explicit configured list**, seeded once from
  Ben's current delegate access via Exchange admin.
- **Changing the classification model, categories, or feedback-loop invariants.**
  `message_embeddings.current_label` remains human-feedback-only.
- **Backfilling shared-mailbox history.** Only mail arriving after a mailbox's subscription is
  registered is processed. The existing untagged-republish grooming will pick up recent
  stragglers within a day.

## Accepted trade-offs

- **Broader application grant.** `Mail.ReadWrite` + `Mail.Send` + `Calendars.ReadWrite` +
  `Group.Read.All` as *application* roles is a larger, admin-consent-required grant than the
  delegated scopes. Mitigated by the Application Access Policy fence and by Ben holding tenant
  Global Admin.
- **Group search behavior change.** `/me/memberOf` has no app-only equivalent, so M365 group
  search moves from "groups Ben belongs to" to an explicit configured group list. This is a
  deliberate, minor behavior change to an existing API feature.
- **Higher cutover risk to the working primary.** Replacing the primary's auth is riskier than
  leaving it alone. Mitigated by the phased rollout (verify primary on app-only before adding
  shared mailboxes) and revert-the-deploy rollback.

## Design

### 1. Authentication

Replace `msal.PublicClientApplication` (delegated) with `msal.ConfidentialClientApplication`
and acquire tokens via client credentials:

```python
result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
```

The token lives in memory and is re-minted on expiry (~1h). No refresh token, no Secret
Manager token cache, no device-code re-consent. `authenticate_headless()` /
`authenticate_interactive()` are replaced by a single `authenticate_app_only()`.

**Retired:** the `msal-token-cache` secret (and its Terraform/GitHub Actions wiring) and the
`refreshing-msal-token` skill.

### 2. Graph client re-path

All ~26 `/me/` sites live in one file, `clients/azure/graph_email_client.py`. Introduce a
single helper `_base(mailbox) -> f"{graph_endpoint}/users/{quote(mailbox)}"`; there is no more
`me`. Every method that touches a mailbox takes an explicit `mailbox: str` (SMTP address),
defaulting to the configured primary address. The three methods that currently use the
`mailbox="me"` convention (`get_email_details`, `get_attachments`, `search_emails`) switch to
address semantics.

Methods to parametrize: fetch/get_email_details, tag_message, set_categories,
move_message_to_action_folder, get_or_create_mail_folder + _find_top_level_mail_folder_id,
create_reply_draft, mark_as_read, get_web_link, latest_reply_from_me, list_inbox_categories,
send_mail/send_message/send_draft/create_draft/add_attachment, and the calendar
event methods (_find_event_id_by_ical_uid, accept/decline/tentativelyAccept_event).

### 3. Mailbox identity flow (notification → pipeline)

Each per-mailbox subscription encodes its address in `clientState`:
`f"{WEBHOOK_CLIENT_STATE}:{address}"`. The webhook validates the **secret prefix** (preserving
the existing shared-secret authenticity check) and forwards the notification unchanged — a
change notification already carries its subscription's `clientState`. `pipeline.run()` parses
the address out of `clientState` and threads `mailbox` through fetch → normalize → classify →
dispatch → action handlers. No GUID→SMTP lookup is needed.

The webhook's current exact-match check (`clientState != "inbox-webhook"`) becomes a
prefix/secret check that also extracts the address.

### 4. Subscriptions + renew (reconcile model)

Drop the single-ID `graph-subscription-id` secret. `graph_subscriptions.register(client, url,
mailbox)` registers `users/{mailbox}/mailFolders/inbox/messages` with the address-stamped
`clientState`. The `inbox-renew` CF becomes a **reconciler**:

1. `GET /subscriptions` (app-only lists all subscriptions the app owns).
2. Renew each subscription whose `notificationUrl` is our webhook.
3. Self-heal: for any configured mailbox with no live subscription, register one.

The monitored mailbox set is a config list (Terraform var → env var), read by renew and sweep.
This is strictly more robust than tracking IDs in a secret and naturally handles adding/removing
mailboxes.

### 5. Data model

Add a `mailbox` column to `messages` (owning SMTP address), backfilled to the primary address
for existing rows. The dedup/unique key becomes `(mailbox, external_id)` — immutable IDs are
mailbox-scoped, and the same message delivered to two mailboxes is legitimately two rows. The
`/r/{uuid}` redirector and any post-hoc tag/move read `mailbox` from the row to target the
correct `/users/{addr}/`.

### 6. Sweep

`inbox-sweep` iterates the configured mailbox set. Per mailbox it performs the existing three
behaviors unchanged in logic: file-by-tag, stale-urgent re-triage (using that mailbox's Sent
Items for `latest_reply_from_me`), and untagged republish (stamping the mailbox into the
republished notification's `clientState` so the processor routes it correctly). Per-mailbox
failures are isolated — a 403/404 on one mailbox logs and emits a metric but does not abort the
others.

### 7. inbox-api (search / send / redirect)

- **Redirect:** `get_web_link` becomes mailbox-aware, using the `mailbox` stored on the row.
- **Send:** app-only `/users/{addr}/sendMail` and draft operations; requires the `Mail.Send`
  application role. Existing alias/shared/group `from` routing is preserved (alias sends target
  the owning mailbox with a `from`; shared/group sends path-target `/users/{addr}`).
- **Group search redesign:** replace `/me/memberOf` with a **configured list of group
  addresses** resolved to IDs via `/groups?$filter=mail eq '…'` and read with `Group.Read.All`.
  This is the one behavior change to an existing feature.

### 8. Azure / Exchange setup (one-time, non-code)

- App registration: add application roles `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`,
  `Group.Read.All`; grant admin consent.
- Create an **Application Access Policy** (`New-ApplicationAccessPolicy … -AccessRight
  RestrictAccess`) scoping the app to a mail-enabled security group whose members are the
  primary + shared mailboxes (+ any M365 groups the API sends/reads as). This fence keeps
  app-only from reading the whole tenant.
- Enumerate the shared mailboxes Ben currently has delegate access to (Exchange admin) to seed
  that group and the config list.
- Ensure the five color categories + `keep_until` exist in each shared mailbox's master
  category list, otherwise tags apply but render uncolored (PATCH still succeeds).

## Error handling & rollout

- **Access-policy propagation** can lag (minutes, historically up to ~24h). Verify app-only can
  actually read each mailbox before cutover.
- **Phased rollout, one implementation:**
  - **Phase A** — Azure roles + admin consent + Application Access Policy + config lists.
  - **Phase B** — ship the mailbox-aware app-only code + schema migration; re-register the
    **primary** subscription under app-only; verify the primary pipeline end-to-end is
    unchanged.
  - **Phase C** — register **shared** subscriptions; verify each end-to-end; enable the
    multi-mailbox sweep.
  - **Rollback** — revert the deploy; the delegated code path remains in git history.
- **Per-mailbox isolation:** sweep and renew loop over mailboxes independently; one mailbox's
  failure never blocks the rest.

## Testing

- **Unit:** `_base(mailbox)` routing; `clientState` build/parse/validate (secret prefix +
  address extraction); renew reconciler against synthetic `GET /subscriptions` payloads
  (missing, extra, stale, foreign-URL subscriptions); dedup on `(mailbox, external_id)`.
- **Local E2E** (`run-pipeline-local`, `testing-inbox-pipeline`): retarget to app-only; drive a
  real message through a **shared** mailbox and confirm classify → tag → move → reply draft →
  RSVP all land in the *correct* mailbox.
- **Reconciler** verified against live `GET /subscriptions`.
- **API:** search across primary + a shared mailbox + a configured group; a send from a shared
  `from`; a redirect resolving a shared-mailbox message's live webLink.

## Affected components

| Component | Change |
|---|---|
| `clients/azure/graph_email_client.py` | Confidential-client auth; all `/me/` → `/users/{addr}`; `mailbox` param throughout |
| `clients/graph.py` | `get_graph_client()` uses `authenticate_app_only()` |
| `clients/graph_subscriptions.py` | Per-mailbox `register(…, mailbox)`; address-stamped `clientState` |
| `functions/webhook/main.py` | Secret-prefix `clientState` validation; forward unchanged |
| `functions/renew/main.py` | Reconciler over configured mailbox set |
| `handlers/pipeline.py` + `handlers/actions/*` | Thread `mailbox` through fetch/classify/dispatch/actions |
| `main.py` (sweep) | Loop over configured mailboxes; per-mailbox isolation |
| `repo/schema.sql`, `repo/messages.py`, `scripts/migrate_db.py` | `mailbox` column; `(mailbox, external_id)` key; backfill |
| `api/routers/{search,emails,redirect}.py` | App-only; mailbox-aware redirect; configured-group search |
| `terraform/` | App roles + consent; Access Policy note; config lists as env; drop `msal-token-cache` wiring |
| Skills/docs | Retire `refreshing-msal-token`; update `CLAUDE.md` auth + secrets sections |
