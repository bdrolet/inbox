# Inbox

Unified message triage system. Classifies incoming email into five action-oriented categories using Claude Sonnet, with a retrieval-augmented feedback loop that improves over time.

See `docs/inbox-architecture.md` for the full design and `docs/v1-implementation.md` for the phase-by-phase build plan.

## Project state

**Phases 1–4 complete** — full classification pipeline live: emails received via Graph webhooks, embedded with bge-small, classified by Claude Sonnet with retrieval-augmented context, folder-moved in Outlook, tagged with Outlook color categories, and urgent messages push to phone via ntfy with action buttons that feed corrections back to the vector store.

Ready to start **Phase 5** (bootstrap labels, decommission Cloud Run Job).

## Stack

| | |
|---|---|
| **GCP project** | `bens-project-462804`, `us-central1` |
| **Worker** | Cloud Function `inbox-process` (Pub/Sub event trigger, scale-to-zero) |
| **Database** | Cloud SQL Postgres 16 + pgvector, `bens-project-462804:us-central1:inbox`, db `app` |
| **Email source** | Microsoft Graph API (Outlook/Office 365), MSAL auth |
| **LLM** | Claude Sonnet via Anthropic API |
| **Trigger** | Graph change notifications → webhook CF → Pub/Sub → processor CF |
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
main.py           Processor Cloud Function entry point (Pub/Sub event trigger)
scripts/          Entry points and one-off jobs
  analyze_emails.py  Existing Cloud Run Job (kept until Phase 5)
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

The Graph change-notification subscription points at the webhook Cloud Function URL. It expires every ~3 days and is renewed automatically by the `inbox-renew` Cloud Function via Cloud Scheduler.

Webhook CF URL: `https://inbox-webhook-aizbgjlava-uc.a.run.app`

Active subscription ID: `f0443feb-28dd-4d8c-be3c-919b5794fed4` (set in `terraform/terraform.tfvars` as `graph_subscription_id`). Renewal runs automatically every 2 days via `inbox-renew` CF + Cloud Scheduler.

To re-register (e.g. after subscription expires):
```python
from clients.azure import GraphEmailClient
from clients.graph_subscriptions import register
c = GraphEmailClient()
c.authenticate_headless()  # or authenticate_interactive() locally
result = register(c, "https://inbox-webhook-aizbgjlava-uc.a.run.app")
print(result["id"])  # update graph_subscription_id in terraform.tfvars
```

## Local development

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in CLIENT_ID, CLIENT_SECRET, TENANT_ID, OPENAI_API_KEY
python scripts/analyze_emails.py  # interactive mode, no GCP_PROJECT_ID set
```

The existing `analyze_emails.py` runs locally without a DB or Pub/Sub.

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
| `client-id` | Graph API auth |
| `client-secret` | Graph API auth |
| `tenant-id` | Graph API auth |
| `openai-api-key` | Existing Cloud Run Job (removed Phase 5) |
| `anthropic-api-key` | Processor CF |
| `msal-token-cache` | Processor CF + renew CF — MSAL refresh token |
| `inbox-db-password` | Processor CF — Cloud SQL password |
| `ntfy-token` | Processor CF — ntfy server access token |
| `webhook-label-token` | Processor CF + webhook CF — authenticates `/label` action button callbacks |

## Migration phases

| Phase | Status | What it adds |
|-------|--------|-------------|
| 1 | **Complete** | DB schema, processor CF, webhook CF, Cloud SQL, Pub/Sub, Graph subscription |
| 2 | **Complete** | bge-small embeddings + pgvector retrieval |
| 3 | **Complete** | Claude Sonnet, 5-category + P0–P3 importance, retrieval-augmented prompt |
| 4 | **Complete** | ntfy push notifications, Outlook folder moves + color-category tagging, human feedback loop |
| 5 | **Next** | Bootstrap labels, decommission Cloud Run Job |

## Known issues / gotchas

- **Cloud SQL Python Connector**: connector v1.20.3 does not support the `"psycopg"` driver at all (only pg8000/asyncpg/pymysql/pytds). `clients/db.py` uses pg8000 with a `_Pg8000Conn` wrapper that implements psycopg3's `conn.execute()` API and dict-row behaviour. The direct (local) path still uses psycopg3 natively.
- **`clients/db.py` local fallback**: set `POSTGRES_HOST` (not `CLOUD_SQL_CONNECTION_NAME`) for a direct psycopg3 connection to a local Postgres instance.
