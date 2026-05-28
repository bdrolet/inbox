# Inbox

Unified message triage system. Classifies incoming email (with SMS and voicemail coming later) into five action-oriented categories using an LLM, with a retrieval-augmented feedback loop that improves over time as you confirm and correct classifications.

See [docs/inbox-architecture.md](docs/inbox-architecture.md) for the full design and migration plan.

## How it works

New emails trigger a Microsoft Graph change notification → Cloud Function → Pub/Sub → GKE worker. The worker normalizes the message, embeds it, retrieves similar past messages with human-confirmed labels, builds a prompt with that context, and calls Claude Sonnet to classify it. The result drives a folder move in Outlook and (for urgent messages) a push notification via ntfy.sh.

**Categories**: `urgent` · `respond` · `review` · `reference` · `ignore`

## Current state

The repo currently runs a Cloud Run Job that fetches all inbox emails daily and classifies them with GPT-4o-mini. The architecture above is being built out in phases — see [docs/inbox-architecture.md](docs/inbox-architecture.md) for the migration plan.

## Project structure

```
clients/          # External service connections (Graph API, DB, Claude, bge model, ntfy)
models/           # Shared type definitions (Message, Category, Classification)
repo/             # Database read/write (messages, classifications, embeddings, senders, tags)
services/         # Business logic (ingestion, embedding, classification, labeling, archiving)
handlers/         # Multi-service orchestration (webhook, pipeline, per-category actions)
scripts/          # Entry points and one-off jobs
terraform/        # GCP infrastructure (Cloud Functions, Pub/Sub, Scheduler, Secrets)
docs/             # Architecture and design docs
```

K8s manifests for the GKE worker live in `~/src/infra/k8s/inbox/`.

## Local development

### Prerequisites

- Python 3.11+
- An Azure app registration with `Mail.Read` and `Mail.ReadWrite` permissions on a Microsoft 365 mailbox
- A `.env` file with the required variables (see below)

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment variables

```bash
# Azure / Microsoft Graph
CLIENT_ID=
CLIENT_SECRET=
TENANT_ID=
REDIRECT_URI=http://localhost:8080/callback   # optional, this is the default
SCOPES=https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read

# LLM
OPENAI_API_KEY=      # current (Cloud Run Job)
ANTHROPIC_API_KEY=   # target (GKE worker, Phase 3+)

# Leave unset for local interactive mode; set to your GCP project ID for headless mode
# GCP_PROJECT_ID=
```

### Authenticate (first time)

The first run requires an interactive device code login to get an MSAL token:

```bash
python scripts/seed_token_cache.py > /tmp/msal_cache.json
# Follow the device code prompt in your browser
```

For local development, the token is cached in `.token_cache.json`. For Cloud Run, seed it into Secret Manager:

```bash
gcloud secrets versions add msal-token-cache --data-file=/tmp/msal_cache.json
rm /tmp/msal_cache.json
```

### Run locally

```bash
python scripts/analyze_emails.py
```

Runs in interactive mode: fetches the latest 10 emails, unread emails, and emails from the last 24 hours. Prints classifications to stdout. Does not move any emails unless `EMAIL_ANALYSIS_MOVE_TO_ACTION_FOLDERS=true` is set.

## Deployment

Infrastructure is managed with Terraform. Secrets are stored in GCP Secret Manager and injected at runtime.

### First-time setup

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Fill in terraform.tfvars with real values
terraform init
terraform apply
```

### Deploy a new image

```bash
# Build and push to Artifact Registry
IMAGE=$(terraform -chdir=terraform output -raw artifact_registry_url)/analyze-emails:latest
docker build -f Dockerfile.analyze-emails -t $IMAGE .
docker push $IMAGE
```

The Cloud Scheduler triggers the job daily at 8 AM EST. To run immediately:

```bash
gcloud run jobs execute email-analysis --region us-central1
```

### Terraform variables

| Variable | Description |
|----------|-------------|
| `project_id` | GCP project ID (set in `project.auto.tfvars`) |
| `client_id` | Azure app registration client ID |
| `client_secret` | Azure app registration client secret |
| `tenant_id` | Azure tenant ID |
| `openai_api_key` | OpenAI API key |
| `msal_token_cache` | Serialized MSAL token cache JSON |
| `region` | GCP region (default: `us-central1`) |
| `schedule_cron` | Cron expression (default: `0 8 * * *`) |
| `schedule_timezone` | Timezone (default: `America/New_York`) |

## Architecture

See [docs/inbox-architecture.md](docs/inbox-architecture.md).
