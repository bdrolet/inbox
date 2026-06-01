# Inbox

Unified message triage system. Classifies incoming email into five action-oriented categories using Claude Sonnet, with a retrieval-augmented feedback loop that improves over time.

See `docs/inbox-architecture.md` for the full design and `docs/v1-implementation.md` for the phase-by-phase build plan.

## Project state

**Phase 1 complete** — event-driven GKE worker receiving live emails, writing to DB. KEDA scaling 0→1 on Pub/Sub backlog. Graph subscription active. The existing Cloud Run Job (`scripts/analyze_emails.py`) stays as a daily fallback until Phase 5.

Ready to start **Phase 2** (bge embeddings + pgvector retrieval).

## Stack

| | |
|---|---|
| **GCP project** | `bens-project-462804`, `us-central1` |
| **Worker** | GKE Deployment (`inbox-processor`, namespace `apps`), KEDA scale 0→1 |
| **Database** | Postgres 16 + pgvector at `postgres.apps.svc.cluster.local` (shared GKE instance) |
| **Email source** | Microsoft Graph API (Outlook/Office 365), MSAL auth |
| **LLM** | Claude Sonnet via Anthropic API (Phase 3+); gpt-4o-mini currently |
| **Trigger** | Graph change notifications → Cloud Function → Pub/Sub → GKE worker |
| **Notifications** | ntfy.sh (Phase 4) |
| **K8s manifests** | `~/src/infra/k8s/inbox/` |
| **GCP infra** | `terraform/` (Cloud Functions, Pub/Sub, Scheduler, Secrets, IAM) |

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
scripts/          Entry points and one-off jobs
  worker.py       GKE pod — Pub/Sub pull loop
  analyze_emails.py  Existing Cloud Run Job (kept until Phase 5)
  migrate_db.py   One-shot schema migration
terraform/        GCP resources (Cloud Functions, Pub/Sub, Scheduler, Secrets, IAM)
docs/             Architecture and implementation docs
```

## Layer rules

- `clients/` — I/O only, no business logic
- `repo/` — DB read/write only; takes an open `psycopg.Connection`; never opens its own connection
- `services/` — calls `clients/` and `repo/`; owns one concern
- `handlers/` — orchestrates multiple services; entry points for the pipeline and action dispatch
- `models/` — pure types; no imports from other layers

## Database

Postgres at `postgres.apps.svc.cluster.local:5432`, database `app`. Credentials from the `postgres-credentials` k8s Secret (env vars: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`).

Schema: `repo/schema.sql`. Five tables: `messages`, `message_embeddings`, `classifications`, `senders`, `tags`.

Key invariant: **`message_embeddings.current_label` is only set by human feedback** (`human_confirmation` or `human_correction`). LLM-assigned labels never go into this column. This prevents bad classifications from becoming retrieval examples.

## Graph API auth

`clients/azure/graph_email_client.py` handles auth in two modes:
- **Interactive** (local): device code flow, token cached in `.token_cache.json`
- **Headless** (GKE/Cloud Run): MSAL token loaded from Secret Manager secret `msal-token-cache`, refreshed silently, written back

The GKE worker pod detects headless mode via `GCP_PROJECT_ID` env var and calls `authenticate_headless()`. The pod needs Workload Identity bound to GCP SA `inbox-worker@bens-project-462804.iam.gserviceaccount.com` for Secret Manager access.

## Graph subscription

The Graph change-notification subscription points at the webhook Cloud Function URL. It expires every ~3 days and is renewed automatically by the `inbox-renew` Cloud Function via Cloud Scheduler.

Webhook CF URL: `https://inbox-webhook-aizbgjlava-uc.a.run.app`

Active subscription ID: `f4255e93-97d1-4087-b738-26c66c40a051` (set in `terraform/terraform.tfvars` as `graph_subscription_id`). Renewal runs automatically every 2 days via `inbox-renew` CF + Cloud Scheduler.

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
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in CLIENT_ID, CLIENT_SECRET, TENANT_ID, OPENAI_API_KEY
python scripts/analyze_emails.py  # interactive mode, no GCP_PROJECT_ID set
```

The existing `analyze_emails.py` runs locally without a DB or Pub/Sub. Set `POSTGRES_*` env vars to test the new repo layer locally.

## Terraform

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars  # fill in secrets
terraform init && terraform apply
```

All GCP resources are in `terraform/` and fully applied. K8s manifests live in `~/src/infra/k8s/inbox/` — apply them separately via kubectl.

Existing GCP resources (secrets, Artifact Registry, Cloud Run job, service accounts) were imported into Terraform state manually since they predate Terraform management. `terraform.tfvars` is gitignored; values come from Secret Manager.

## Secrets

| Secret Manager key | Used by |
|---|---|
| `client-id` | Graph API auth |
| `client-secret` | Graph API auth |
| `tenant-id` | Graph API auth |
| `openai-api-key` | Existing Cloud Run Job (removed Phase 5) |
| `anthropic-api-key` | GKE worker (added Phase 3) |
| `msal-token-cache` | Both workers — MSAL refresh token |

Azure credentials are also injected into the GKE pod from the `inbox-azure-credentials` k8s Secret.

## Migration phases

| Phase | Status | What it adds |
|-------|--------|-------------|
| 1 | **Complete** | DB schema, GKE worker, Cloud Function webhook, Pub/Sub, KEDA, Graph subscription |
| 2 | **Next** | bge-small embeddings + pgvector retrieval (logged, not used in prompt) |
| 3 | Pending | Claude Sonnet, 5-category system, retrieval-augmented prompt |
| 4 | Pending | ntfy.sh notifications, Outlook folder moves, human feedback loop |
| 5 | Pending | Bootstrap labels, decommission Cloud Run Job |

## Known issues / gotchas

- **`POSTGRES_PORT` env var**: k8s injects this as `tcp://host:port` for the `postgres` service. `clients/db.py` handles it via `_parse_port()`. Deployment also pins it explicitly to `"5432"`.
- **KEDA GCP auth**: uses `ClusterTriggerAuthentication` with `podIdentity: gcp` backed by `keda-pubsub@bens-project-462804.iam.gserviceaccount.com` (roles: `pubsub.viewer`, `monitoring.viewer`). KEDA operator SA annotated with Workload Identity.
- **Worker logging**: set `GRPC_VERBOSITY=ERROR` (in both deployment.yaml and worker.py) to prevent gRPC's C layer from silencing Python's logging output.
