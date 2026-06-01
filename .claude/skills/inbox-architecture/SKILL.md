---
name: inbox-architecture
description: Use when the user asks how the inbox is deployed, how the infrastructure works, how messages flow through the system, what GCP resources exist, how the processor Cloud Function is set up, or how any component of the inbox pipeline fits together.
---

# Inbox: Architecture & Deployment Reference

## High-level flow

```
Outlook inbox
    │  new email → Graph change notification (POST)
    ▼
Cloud Function: inbox-webhook               (always-on, responds in <100ms)
https://inbox-webhook-aizbgjlava-uc.a.run.app
    │  publishes notification JSON to Pub/Sub
    ▼
Pub/Sub topic: inbox-messages
    │  event trigger (push) to processor CF
    ▼
Cloud Function: inbox-process  (Pub/Sub event trigger, scale-to-zero)
main.py — process(cloud_event)
    │  fetches full email from Graph API by message ID
    │  writes to Cloud SQL (messages + senders tables)
    ▼
Cloud SQL Postgres 16 + pgvector
bens-project-462804:us-central1:inbox  db: app
```

Renewal: Cloud Scheduler → `inbox-renew` CF every 2 days, renews Graph subscription before it expires.

---

## GCP resources

| Resource | Name | Notes |
|----------|------|-------|
| GCP project | `bens-project-462804` | `us-central1` |
| Cloud Function (gen2) | `inbox-webhook` | HTTP trigger, unauthenticated; Graph notifications → Pub/Sub |
| Cloud Function (gen2) | `inbox-process` | Pub/Sub event trigger; processes each email notification |
| Cloud Function (gen2) | `inbox-renew` | HTTP trigger; renews Graph subscription; invoked by Cloud Scheduler |
| Pub/Sub topic | `inbox-messages` | Graph change notifications |
| Cloud SQL | `inbox` (db-f1-micro) | Postgres 16 + pgvector; connection name `bens-project-462804:us-central1:inbox` |
| Cloud Scheduler | `inbox-subscription-renew` | Runs every 2 days (`0 23 */2 * *`) |
| Cloud Scheduler | `email-analysis-daily` | Existing daily Cloud Run Job (kept until Phase 5) |
| Artifact Registry | `email-analysis` | Docker repo; holds `analyze-emails` image |
| Secret Manager | `client-id`, `client-secret`, `tenant-id`, `openai-api-key`, `msal-token-cache`, `inbox-db-password` | Azure/OpenAI creds; MSAL refresh token; DB password |

All GCP resources managed by Terraform in `terraform/`.

---

## Service accounts

| SA | Used by | Key permissions |
|----|---------|----------------|
| `inbox-process-cf@` | Processor CF | `cloudsql.client`, Secret Manager accessor on all CF secrets + `secretVersionManager` on `msal-token-cache` |
| `inbox-webhook-cf@` | Webhook CF | `pubsub.publisher` on `inbox-messages` |
| `inbox-renew-cf@` | Renew CF | Secret Manager accessor + version manager on `msal-token-cache` and Azure creds |
| `email-analysis-job@` | Cloud Run Job | Secret Manager accessor on all secrets |
| `inbox-scheduler@` | Cloud Scheduler | `cloudfunctions.invoker` on `inbox-renew` |

---

## Microsoft Graph subscription

- **Subscription ID**: `f4255e93-97d1-4087-b738-26c66c40a051`
- **Webhook URL**: `https://inbox-webhook-aizbgjlava-uc.a.run.app`
- **Renewal**: automatic via `inbox-renew` CF + Cloud Scheduler (every 2 days)
- **Manual re-registration**:
  ```python
  from clients.azure import GraphEmailClient
  from clients.graph_subscriptions import register
  c = GraphEmailClient()
  c.authenticate_headless()  # or authenticate_interactive() locally
  result = register(c, "https://inbox-webhook-aizbgjlava-uc.a.run.app")
  print(result["id"])  # update graph_subscription_id in terraform.tfvars
  ```

---

## Database schema

Five tables in `repo/schema.sql`, applied once via `scripts/migrate_db.py`:

| Table | Purpose |
|-------|---------|
| `messages` | One row per email; `(source, external_id)` unique constraint deduplicates |
| `message_embeddings` | bge-small-en-v1.5 vector(384); `current_label` only set by human feedback, never LLM |
| `classifications` | LLM and human labels; full history, newest wins |
| `senders` | Per-sender stats (message count, response rate, relationship label) |
| `tags` | Tag vocabulary |

Key invariant: **`message_embeddings.current_label` is never written by the LLM** — only by human corrections/confirmations.

---

## Code layout

```
main.py        Processor Cloud Function entry point — process(cloud_event)
clients/       I/O only — db, Graph API, Azure auth, Claude, bge, ntfy
  db.py        Cloud SQL Python Connector (prod) / direct psycopg fallback (local)
  azure/       GraphEmailClient — MSAL auth, email fetch, folder moves
models/        Pure types — Message TypedDict, Category enum
repo/          DB read/write — takes an open psycopg.Connection, never opens its own
services/      Business logic — one concern per file
handlers/      Orchestration — pipeline (Phase 3), per-category actions (Phase 4)
functions/
  webhook/     Cloud Function: Graph notifications → Pub/Sub
  renew/       Cloud Function: renew Graph subscription
scripts/
  analyze_emails.py  Existing Cloud Run Job (removed Phase 5)
  migrate_db.py      One-shot schema migration
terraform/     All GCP resources
docs/          inbox-architecture.md (full design), v1-implementation.md (build plan)
```

Layer rules: `clients/` → I/O only. `repo/` → DB only, no connections. `services/` → calls clients + repo. `handlers/` → orchestrates services. `models/` → no imports from other layers.

---

## Migration phase status

| Phase | Status | What it adds |
|-------|--------|-------------|
| 1 | **Complete** | Cloud SQL, processor CF, webhook CF, Pub/Sub, Graph subscription |
| 2 | **Next** | bge-small embeddings + pgvector retrieval (logged, not used in prompt yet) |
| 3 | Pending | Claude Sonnet, 5-category system, retrieval-augmented prompt |
| 4 | Pending | ntfy.sh notifications, Outlook folder moves, human feedback loop |
| 5 | Pending | Bootstrap labels, decommission Cloud Run Job |

---

## Auth

**Graph API / MSAL**: two modes depending on context:
- **Local** (`GCP_PROJECT_ID` not set): device code flow, token cached in `~/.inbox-token-cache.json`
- **Cloud Function** (`GCP_PROJECT_ID` set): token loaded from Secret Manager `msal-token-cache`, refreshed silently, written back via `authenticate_headless()`

**DB connection**:
- **Production**: `CLOUD_SQL_CONNECTION_NAME=bens-project-462804:us-central1:inbox` → Cloud SQL Python Connector
- **Local**: `POSTGRES_HOST=...` (leave `CLOUD_SQL_CONNECTION_NAME` unset) → direct psycopg3

**Anthropic API** (Phase 3+): `anthropic-api-key` in Secret Manager, injected into processor CF.

---

## Terraform operations

```bash
cd /Users/ben/src/inbox/terraform
terraform plan -var="db_password=PLACEHOLDER"  # preview changes
terraform apply                                 # deploy (db_password must be in terraform.tfvars)
```

`terraform.tfvars` is gitignored (contains secrets + `db_password`). `project.auto.tfvars` sets `project_id = "bens-project-462804"`.

## Known gotchas

- **Cloud SQL Python Connector locally**: connector v1.20.3 does not support the `psycopg` driver string. Use `pg8000` with the connector for local scripts, or leave `CLOUD_SQL_CONNECTION_NAME` unset and use direct psycopg3 via `POSTGRES_HOST`.
