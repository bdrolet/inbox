---
name: testing-inbox-pipeline
description: Use when locally testing the full inbox pipeline (fetch → embed → classify → dispatch) or just the Asana task creation step against a real email, without deploying. Use run-pipeline-local.py for end-to-end pipeline testing including email summary, classification, and task creation. Use create-task-local.py to smoke-test only task creation or draft generation.
metadata:
  type: project
---

# Testing Inbox Handlers Locally

Two scripts are available depending on how much of the pipeline you want to exercise.

---

## Full pipeline runner (`run-pipeline-local.py`)

Runs the complete pipeline against a real email: fetch → normalize → embed → classify → store → dispatch. This is the closest local equivalent to what the Cloud Function does in production. Use this to test end-to-end changes including classification, email summary, Asana task creation, and folder moves.

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
| `ASANA_API_KEY`, `ASANA_PROJECT_ID` | Asana credentials |
| `WEBHOOK_URL`, `WEBHOOK_LABEL_TOKEN` | For Asana action button URLs |

**Gotchas:**
- Graph auth uses interactive device-code flow locally (token cached at `~/.inbox-token-cache.json`). Re-auth only needed if the cache expires.
- The pipeline inserts a real DB record and creates a real Asana task — use a test email you send to yourself.
- If the email was already processed, the pipeline skips it (duplicate check). Use `--message-id` with a fresh email ID or send a new test email.
- ntfy notifications fire if `NTFY_TOPIC` and `NTFY_TOKEN` are set in `.env` — omit them to skip.

---

## Task creation only (`create-task-local.py`)

Runs only the Asana task creation step (and optionally Outlook draft generation) against a real classified message from the DB, or the latest live email from Graph. Does **not** run classification or embedding. Use this to smoke-test changes to `clients/asana.py`, `services/draft_reply.py`, or `services/asana_tag_cache.py`.

```bash
.venv/bin/python .claude/skills/testing-inbox-pipeline/scripts/create-task-local.py --category review
```

Run from the repo root. Swap `--category respond` for the respond flow.

**Source selection:**

| DB env vars set? | Email source |
|-----------------|--------------|
| Yes | Cloud SQL — queries the latest message classified as `--category` |
| No | Graph API — fetches the latest non-`RE:` email live from Outlook |

**Gotchas:**
- Always uses a fresh UUID for `external.gid` — avoids Asana's "already assigned" 400 on re-runs.
- This script bypasses `email_summary.generate()` — task notes will show the plain-text preview fallback, not key points or links. Use the pipeline runner to test those.
- Bodies from Cloud SQL are HTML — the script strips tags before passing to Asana.
- Empty `WEBHOOK_URL` causes Asana 400 (relative hrefs rejected by Asana's HTML validator).
