---
name: testing-inbox-pipeline
description: Use when locally testing the full inbox pipeline (fetch → embed → classify → dispatch) against a real email, without deploying. Use run-pipeline-local.py for end-to-end pipeline testing including classification, tagging, and publishing the email_classified event.
metadata:
  type: project
---

# Testing Inbox Handlers Locally

## Full pipeline runner (`run-pipeline-local.py`)

Runs the complete pipeline against a real email: fetch → normalize → embed → classify → store → dispatch. This is the closest local equivalent to what the Cloud Function does in production. Use this to test end-to-end changes including classification, tagging, and publishing the `email_classified` event. (Asana task creation now happens downstream in the tasks repo, which consumes that event — verify it there.)

```bash
.venv/bin/python .claude/skills/testing-inbox-pipeline/scripts/run-pipeline-local.py
```

Without `--message-id`, fetches the most recent unprocessed email from your Outlook inbox. To target a specific email:

```bash
.venv/bin/python .claude/skills/testing-inbox-pipeline/scripts/run-pipeline-local.py --message-id <graph_message_id>
```

Run from the repo root (`/Users/ben/src/inbox`). All required env vars are read from `.env`.

**Required `.env` vars:**

| Var | Notes |
|-----|-------|
| `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID` | Azure app credentials for Graph auth |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `CLOUD_SQL_CONNECTION_NAME` | `bens-project-462804:us-central1:inbox` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Cloud SQL credentials |
| `WEBHOOK_URL`, `WEBHOOK_LABEL_TOKEN` | For ntfy action button URLs and calendar RSVP links |

**Gotchas:**
- Graph auth uses interactive device-code flow locally (token cached at `~/.inbox-token-cache.json`). Re-auth only needed if the cache expires.
- The pipeline inserts a real DB record and publishes a real `email_classified` event — use a test email you send to yourself.
- If the email was already processed, the pipeline skips it (duplicate check). Use `--message-id` with a fresh email ID or send a new test email.
- ntfy notifications fire if `NTFY_TOPIC` and `NTFY_TOKEN` are set in `.env` — omit them to skip.
