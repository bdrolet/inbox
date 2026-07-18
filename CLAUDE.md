# Inbox

Unified message triage system. Classifies incoming email into five action-oriented categories using Claude Sonnet, with a retrieval-augmented feedback loop that improves over time.

See `docs/inbox-architecture.md` for the full design and `docs/v1-implementation.md` for the phase-by-phase build plan.

## Project state

**Phases 1–4 complete** — full classification pipeline live: emails received via Graph webhooks, embedded with bge-small, classified by Claude Sonnet with retrieval-augmented context, tagged with Outlook color categories, and urgent messages push to phone via ntfy with action buttons that feed corrections back to the vector store.

**Deferred folder moves (shipped):** classified mail is tagged immediately but stays in the Inbox during the day. The `inbox-sweep` Cloud Function (Cloud Scheduler, 5 AM America/New_York) files each message into the folder implied by its **current** Outlook category tag — so a correction made during the day is honored at move time. The sweep is stateless ("tagged + in Inbox → file by tag"); a `keep_until:<YYYY-MM-DD[THH:MM]>` Outlook category holds a message in the Inbox past the sweep. "Open in Outlook" links are `inbox-api` redirector URLs (`/r/{uuid}`) that resolve the live webLink via the message's **immutable** Graph ID, so links survive the move. See `docs/superpowers/specs/2026-07-14-deferred-folder-moves-design.md`.

**Tasks-service extraction (shipped):** inbox owns classification only — it no longer talks to Asana. For every processed email (all five categories) the dispatch handler publishes one `email_classified` event, and the labeling service publishes `label_applied` feedback events (human confirmations/corrections), both to the inbox-owned `email-events` Pub/Sub topic. The separate `tasks` repo (github.com/bdrolet/tasks) subscribes to that topic and owns all Asana task policy, enrichment (summaries, deadlines), and creation. Urgent ntfy pushes still click through to the source email via the `/r/{uuid}` redirector; the Asana task itself now shows up moments later, created by the tasks service. See `docs/superpowers/plans/2026-07-16-email-events-extraction.md`.

**Inbox grooming (shipped):** the 5 AM sweep also grooms what it can't file. Urgent messages older than 3 days are re-triaged by Claude using the message content and the text of Ben's latest reply in the thread (if any): `still_urgent` re-holds them via a `keep_until:+3d` tag (re-checked every 3 days), `needs_response` demotes them to `respond`/`reply_required`, `resolved_or_expired` archives them. Verdicts are policy, never human feedback — they don't touch `current_label`. Untagged Inbox mail older than 24 h is republished (≤50/night) to the `inbox-messages` topic for normal classification; the processor repairs missing tags on duplicate notifications, so republishing is a safe universal repair. See `docs/superpowers/specs/2026-07-17-inbox-grooming-design.md`.

**Phase 5 remainder:** the old Cloud Run Job is already decommissioned (`docs/v1-implementation.md`); what's left is seeding the vector store with human-confirmed labels — an interactive `scripts/bootstrap_labels.py` session driven by Ben.

## Development workflow

When implementing new code in this repo, open a pull request rather than committing to `main`:

1. Create a feature branch off `main` before making changes (never commit code changes directly to `main`).
2. Once the change is implemented and verified, open the PR using the `/pr-open` skill — it pulls the base branch, creates the feature branch, commits, pushes, and writes a rich PR description. Don't hand-roll `git push` + `gh pr create` for this repo.
3. Do this proactively when work is complete — don't wait to be asked. (Still hold off if the change is incomplete, exploratory, or the user signalled they're mid-iteration.)

This overrides the default "commit or push only when asked" behavior for code changes in this repo.

## Stack

| | |
|---|---|
| **GCP project** | `bens-project-462804`, `us-central1` |
| **Worker** | Cloud Function `inbox-process` (Pub/Sub event trigger, scale-to-zero) |
| **Sweep** | Cloud Function `inbox-sweep` (HTTP, Cloud Scheduler `0 5 * * *` America/New_York) — files Inbox mail by its current category tag; re-triages stale urgent mail via Claude; republishes untagged mail for classification |
| **API** | Cloud Run service `inbox-api` (FastAPI) — email search + outbound email + `/r/{uuid}` redirector |
| **Database** | Cloud SQL Postgres 16 + pgvector, `bens-project-462804:us-central1:inbox`, db `app` |
| **Email source** | Microsoft Graph API (Outlook/Office 365), MSAL auth |
| **LLM** | Claude Sonnet via Anthropic API |
| **Trigger** | Graph change notifications → webhook CF → Pub/Sub → processor CF |
| **Domain events** | Pub/Sub topic `email-events` (inbox-owned) — `email_classified` + `label_applied` events; consumed by the separate `tasks` repo (github.com/bdrolet/tasks), which owns Asana |
| **Notifications** | Self-hosted ntfy at `ntfy.drolet.ai`, topic `inbox` |
| **GCP infra** | `terraform/` (Cloud Functions, Pub/Sub, Cloud SQL, Scheduler, Secrets, IAM) |

## Code layout

```
clients/          External connections (Graph API, DB, Claude, bge model, ntfy)
models/           Shared types — Message TypedDict, Category enum (no logic)
repo/             Database read/write (messages, classifications, embeddings, senders, tags)
services/         Business logic — one concern per file
handlers/         Multi-service orchestration (pipeline, per-category actions)
functions/        Cloud Function entry points (standalone, minimal deps)
  webhook/        Receives Graph notifications → publishes to Pub/Sub
  renew/          Renews Graph subscription every 2 days
api/              FastAPI app (Cloud Run service inbox-api)
  routers/        search.py (mailbox/group/DB search), emails.py (draft, attach, send), redirect.py (/r/{uuid} → live webLink)
main.py           Processor + sweep Cloud Function entry points (Pub/Sub event trigger; sweep is HTTP)
scripts/          Entry points and one-off jobs
  bootstrap_labels.py  Interactive human-labeling session (seeds current_label)
  migrate_db.py   One-shot schema migration
terraform/        GCP resources (Cloud Functions, Pub/Sub, Cloud SQL, Scheduler, Secrets, IAM)
docs/             Architecture and implementation docs
```

## Layer rules

- `clients/` — I/O only, no business logic
- `repo/` — DB read/write only; takes an open `psycopg.Connection`; never opens its own connection
- `services/` — calls `clients/` and `repo/`; owns one concern
- `handlers/` — orchestrates multiple services; entry points for the pipeline and action dispatch
- `models/` — pure types; no imports from other layers

## Database

Cloud SQL Postgres 16 + pgvector. Connection name: `bens-project-462804:us-central1:inbox`, database `app`.

**In production** (Cloud Function): `clients/db.py` uses the Cloud SQL Python Connector via `CLOUD_SQL_CONNECTION_NAME` env var. Credentials (`POSTGRES_USER`, `POSTGRES_PASSWORD`) injected from Secret Manager.

**Locally**: set `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` and leave `CLOUD_SQL_CONNECTION_NAME` unset — `clients/db.py` falls back to direct psycopg connect.

Schema: `repo/schema.sql`. Five tables: `messages`, `message_embeddings`, `classifications`, `senders`, `tags`.

Key invariant: **`message_embeddings.current_label` is only set by human feedback** (`human_confirmation` or `human_correction`). LLM-assigned labels never go into this column.

## Graph API auth

`clients/azure/graph_email_client.py` handles auth in two modes:
- **Interactive** (local): device code flow, token cached in `~/.inbox-token-cache.json`
- **Headless** (Cloud Function): MSAL token loaded from Secret Manager secret `msal-token-cache`, refreshed silently, written back

Headless mode is triggered by the presence of `GCP_PROJECT_ID` env var. The processor CF SA `inbox-process-cf@bens-project-462804.iam.gserviceaccount.com` has Secret Manager accessor + version manager roles on `msal-token-cache`.

## Graph subscription

The Graph change-notification subscription points at the webhook Cloud Function URL. It expires every ~3 days and is renewed automatically by the `inbox-renew` Cloud Function via Cloud Scheduler. It is registered with `Prefer: IdType="ImmutableId"`, so `resourceData.id` in notifications is an **immutable** Graph ID (stable across folder moves).

Webhook CF URL: `https://inbox-webhook-aizbgjlava-uc.a.run.app`

Active subscription ID: `a250d513-059e-4624-9121-7dca3954c4c9`. **The authoritative value is the `graph-subscription-id` Secret Manager secret**, which the `inbox-renew` CF reads, renews every 2 days, and rewrites on self-heal — Terraform seeds it once but never overwrites it (`lifecycle.ignore_changes` in `terraform/secrets.tf`). The `terraform.tfvars` / GitHub Actions `GRAPH_SUBSCRIPTION_ID` values only matter for the initial seed of a fresh secret; keep them roughly in sync for disaster recovery.

To re-register (e.g. after subscription expires — `register()` sends the immutable-ID header):
```python
from clients.azure import GraphEmailClient
from clients.graph_subscriptions import register
c = GraphEmailClient()
c.authenticate_headless()  # or authenticate_interactive() locally
result = register(c, "https://inbox-webhook-aizbgjlava-uc.a.run.app")
print(result["id"])  # write to the graph-subscription-id secret (authoritative); update GRAPH_SUBSCRIPTION_ID for DR
```

## Local development

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in CLIENT_ID, CLIENT_SECRET, TENANT_ID, ANTHROPIC_API_KEY, ...
python scripts/analyze_emails.py  # interactive mode, no GCP_PROJECT_ID set
```


## Terraform

Use the `/terraform-plan` and `/terraform-apply` skills when making changes to Terraform files. These handle credential checks, run the command, and post results as a PR comment automatically.

First-time setup only — copy and fill in `terraform.tfvars`:
```bash
cd terraform && cp terraform.tfvars.example terraform.tfvars  # fill in secrets + db_password
terraform init
```

All GCP resources are in `terraform/` and fully applied. `terraform.tfvars` is gitignored; contains secrets and `db_password`.

After a successful apply that creates Cloud SQL, run the schema migration:
```bash
CLOUD_SQL_CONNECTION_NAME=bens-project-462804:us-central1:inbox \
  POSTGRES_USER=inbox POSTGRES_PASSWORD=<db_password> POSTGRES_DB=app \
  python scripts/migrate_db.py
```

## Secrets

| Secret Manager key | Used by |
|---|---|
| `client-id`, `client-secret`, `tenant-id` | Graph API auth (processor/webhook/renew CFs + `inbox-api`) |
| `anthropic-api-key` | Processor CF |
| `msal-token-cache` | Processor CF + renew CF + `inbox-api` — MSAL refresh token |
| `inbox-db-password` | Processor CF + `inbox-api` — Cloud SQL password |
| `ntfy-token` | Processor CF — ntfy server access token |
| `webhook-label-token` | Processor CF + webhook CF — authenticates `/label` action button callbacks. **Owned by the platform state (`~/src/infra`)**; read here via data source |
| `grafana-otlp-endpoint`, `grafana-otlp-token` | Processor + webhook CFs — OTel metrics/traces export to Grafana Cloud. **Owned by the platform state (`~/src/infra`)**; read here via data source |
| `asana-api-key` | **Owned by the platform state (`~/src/infra`)** for the tasks repo (github.com/bdrolet/tasks) — inbox no longer references it |
| `hubspot-token` | Processor CF — HubSpot contact upsert + email logging |
| `google-calendar-client-id`, `google-calendar-client-secret`, `google-calendar-refresh-token` | Processor CF — Google Calendar responses |
| `hf-token` | Processor CF — Hugging Face auth for bge model download |
| `search-token` | `inbox-api` — authenticates API requests |
| `graph-subscription-id` | Renew CF — subscription to renew/self-heal |

## Migration phases

| Phase | Status | What it adds |
|-------|--------|-------------|
| 1 | **Complete** | DB schema, processor CF, webhook CF, Cloud SQL, Pub/Sub, Graph subscription |
| 2 | **Complete** | bge-small embeddings + pgvector retrieval |
| 3 | **Complete** | Claude Sonnet, 5-category + P0–P3 importance, retrieval-augmented prompt |
| 4 | **Complete** | ntfy push notifications, Outlook color-category tagging, human feedback loop |
| — | **Complete** | Deferred folder moves: `inbox-sweep` CF (5 AM ET) files by tag, `keep_until` holds, immutable IDs + `/r/{uuid}` redirector |
| — | **Complete** | Tasks-service extraction: `email_classified`/`label_applied` events on the `email-events` topic; Asana task creation + enrichment moved to the tasks repo |
| 5 | **Partial** | Cloud Run Job decommissioned (see `docs/v1-implementation.md` Phase 5); remaining: bootstrap labels via `scripts/bootstrap_labels.py` (interactive) |

## Known issues / gotchas

- **Cloud SQL Python Connector**: connector v1.20.3 does not support the `"psycopg"` driver at all (only pg8000/asyncpg/pymysql/pytds). `clients/db.py` uses pg8000 with a `_Pg8000Conn` wrapper that implements psycopg3's `conn.execute()` API and dict-row behaviour. The direct (local) path still uses psycopg3 natively.
- **`clients/db.py` local fallback**: set `POSTGRES_HOST` (not `CLOUD_SQL_CONNECTION_NAME`) for a direct psycopg3 connection to a local Postgres instance.
